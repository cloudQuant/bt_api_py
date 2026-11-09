"""Synthetic trusted CTP Gateway profile composition and lifecycle tests."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import UTC
from pathlib import Path

import pytest
import zmq
from bt_api_base.account_protocol import AccountKey
from bt_api_base.direct_place_protocol import PreparedPlaceRequest
from bt_api_base.direct_write_protocol import (
    DirectOrderReservation,
    DirectSubmitResult,
    DirectWriteErrorPolicy,
    NativeOrderIdentity,
    SettlementObservation,
    SettlementStatus,
    WriteOperation,
)
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

import bt_api_py.ctp_gateway_trading as module
from bt_api_py.ctp_gateway_trading import (
    CtpGatewayTradingError,
    CtpGatewayTradingSettings,
    build_ctp_gateway_trading_profile,
)
from bt_api_py.gateway_direct_owner import GatewayDispatchCommand
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayReservationAdmissionDenied
from bt_api_py.service_components import ServiceResourceConstructionError
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.integration.test_iter63_gateway_direct_service import (
    CLIENT_A,
    DOMAIN,
    OPS,
    SERVICE,
    _service_config,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint


class FakeProvider:
    settlement_required = True
    execution_ids_trading_day_scoped = True
    direct_write_error_policy = DirectWriteErrorPolicy(frozenset(), frozenset())

    def __init__(self):
        self.state = SettlementStatus.CONFIRMED
        self.session = 1
        self.counter = 0
        self.confirms = 0
        self.sends = []
        self.threads = []
        self._prepared_places = {}

    def session_generation(self):
        return self.session

    def observe_settlement(self):
        self.threads.append(threading.get_ident())
        self.counter += 1
        return SettlementObservation(
            module.ROUTE,
            "native-binding",
            "20300101",
            self.session,
            self.state,
            f"obs-{self.counter}",
        )

    def validate_settlement_observation(self, obs):
        return (
            obs.account_binding == "native-binding"
            and obs.session_generation == self.session
            and obs.status is self.state
        )

    def reserve_order(self, order):
        self.threads.append(threading.get_ident())
        ref = order.client_order_id
        identity = NativeOrderIdentity(
            module.ROUTE,
            ref,
            {"trading_day": "20300101", "front_id": 1, "session_id": 2},
            self.session,
        )
        return DirectOrderReservation(identity, order.symbol, ref)

    def release_reservation(self, reservation):
        pass

    def trading_day(self):
        return "20300101"

    def prepare_order(self, order):
        reservation = self.reserve_order(order)
        prepared = PreparedPlaceRequest(
            reservation.reservation_id, reservation.identity, reservation.symbol,
            "20300101", self.session,
            {"request_key": order.idempotency_key},
            ("synthetic-place:" + order.idempotency_key,),
            {"schema": "synthetic-place-v1"},
        )
        self._prepared_places[id(prepared)] = (prepared, reservation, order)
        return prepared

    def release_prepared_order(self, prepared):
        entry = self._prepared_places.pop(id(prepared), None)
        if entry is not None and entry[0] is prepared:
            self.release_reservation(entry[1])

    def submit_prepared_order(self, prepared, grant, verifier):
        entry = self._prepared_places.pop(id(prepared), None)
        if entry is None or entry[0] is not prepared:
            raise ValueError("synthetic prepared ticket mismatch")
        return self.submit_order(entry[1], entry[2], grant, verifier)

    def submit_order(self, reservation, order, grant, verifier):
        verifier.consume(
            grant,
            exchange_name=module.ROUTE,
            operation=WriteOperation.PLACE,
            session_generation=self.session,
        )
        self.threads.append(threading.get_ident())
        self.sends.append(order.idempotency_key)
        return DirectSubmitResult(True, reservation.identity)

    def confirm_settlement(self, grant, verifier, *, observation):
        verifier.consume(
            grant,
            exchange_name=module.ROUTE,
            operation=WriteOperation.SETTLEMENT_CONFIRM,
            session_generation=self.session,
            settlement_binding=grant.settlement_binding,
        )
        self.confirms += 1
        return SettlementStatus.UNKNOWN


class FakeSession:
    instances = []
    close_ok = True
    connect_fails = False
    initial_status = SettlementStatus.CONFIRMED
    connect_hook = None

    def __init__(self, key, feed, stream, **kwargs):
        self.key, self.kwargs = key, kwargs
        self.owner = threading.get_ident()
        self.provider = FakeProvider()
        self.provider.state = self.initial_status
        self.events = [("construct", self.owner)]
        self.close_allowed = self.close_ok
        self.instances.append(self)

    def connect(self, *, generation, timeout):
        self.events.append(("connect", threading.get_ident()))
        if type(self).connect_hook is not None:
            type(self).connect_hook()
        if self.connect_fails:
            raise ValueError("sensitive synthetic failure")

    @property
    def direct_provider(self):
        assert threading.get_ident() == self.owner
        return self.provider

    @property
    def account_binding(self):
        return "native-binding"

    def refresh_session(self):
        assert threading.get_ident() == self.owner
        self.events.append(("refresh", threading.get_ident()))

    def connection_status(self):
        assert threading.get_ident() == self.owner
        return "ready"

    def close(self):
        self.events.append(("close", threading.get_ident()))
        return self.close_allowed


class FakeReturnMaintenance:
    """Isolate pre-existing settlement/dispatch tests from return ingestion."""

    def __init__(self, session, provider, journal, guard, account_key, settings):
        from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger

        self.ledger = GatewayDirectExecutionLedger(journal, guard)

    def create_execution_stream(self, journal, grants, *, execution_domain_ref):
        from bt_api_py.gateway_execution_stream_service import GatewayExecutionStreamService

        return GatewayExecutionStreamService(
            self.ledger, journal, grants, execution_domain_ref=execution_domain_ref
        )

    def poll(self):
        pass

    def begin_close(self):
        pass

    def pause_for_reconnect(self):
        pass

    def close(self, timeout_seconds=5):
        self.begin_close()
        return True

    def due(self, now):
        return False

    def dispatch_ready(self):
        return True

    def status(self):
        return {"status": "HEALTHY", "reservation_open": True}

    def reservation_fence(self, context, order):
        from contextlib import nullcontext

        return nullcontext()


def _authenticated_reserve(binding, account):
    """Route each test request through a real Registry-issued admission."""
    from bt_api_py.service_registry import ServiceCapability, ServiceRegistry
    from tests.unit.test_iter63_gateway_direct_service import _order
    from tests.unit.test_service_registry import _next_request, _register

    ops = frozenset({"direct.reserve"})
    scope = WireScope(DOMAIN, account_ref=account, strategy_ref=CLIENT_A.actor_ref, run_id="test-run")
    resource = ResourceGrant("gateway-a", "service.direct_write", "scope-a",
                             replace(scope, run_id=None), ops)
    registry = ServiceRegistry(":memory:", service_id="gateway-service",
        capabilities=(ServiceCapability("gateway-a", "service.direct_write", ops, ops),),
        grants={CLIENT_A.principal_ref: (PrincipalGrant(CLIENT_A, (resource,)),)},
        policy_version="p", requirements_version="r")
    registry.register_handler("gateway-a", "direct.reserve",
        lambda ctx, args: binding.handle("direct.reserve", context=ctx, arguments=args))
    response = _register(registry, principal=CLIENT_A.principal_ref, identity=CLIENT_A,
        scope=scope, instance_id="gateway-a", port="service.direct_write", scope_ref="scope-a",
        operations=tuple(ops))
    assert response["ok"]
    registration = response["result"]

    def reserve(key):
        arguments = {"request_key": key, "payload": _order(key=key, account=account), "ttl_seconds": 10}
        response = registry.handle(CLIENT_A.principal_ref, _next_request("direct.reserve", {
            "registration_id": registration["registration_id"], "service_epoch": registration["service_epoch"],
            "instance_id": "gateway-a", "arguments": arguments}, identity=CLIENT_A, scope=scope))
        assert response["ok"], response
        return response["result"]
    return registry, reserve


@pytest.mark.parametrize("failure_point", ["refresh", "settlement"])
def test_maintenance_failure_closes_reservation_before_unconfirmed_cleanup(
    tmp_path, monkeypatch, failure_point
):
    from contextlib import contextmanager

    import bt_api_py.ctp_gateway_return_maintenance as return_module
    from bt_api_py.gateway_direct_service import GatewayReservationAdmissionDenied

    failure = threading.Event()
    original_refresh = FakeSession.refresh_session
    original_tick = module.GatewaySettlementController.tick

    def refresh(session):
        if failure_point == "refresh" and failure.is_set():
            raise RuntimeError
        return original_refresh(session)

    def tick(controller):
        if failure_point == "settlement" and failure.is_set():
            raise RuntimeError
        return original_tick(controller)

    original_ready = module.GatewaySettlementController.ready

    def ready(controller):
        # Maintenance now observes settlement only while not ready; an injected
        # settlement failure therefore also drops the live ticket.
        if failure_point == "settlement" and failure.is_set():
            return False
        return original_ready.fget(controller)

    monkeypatch.setattr(module.GatewaySettlementController, "ready", property(ready))

    class Gate(FakeReturnMaintenance):
        def __init__(self, *args):
            super().__init__(*args)
            self.open = True

        def begin_close(self):
            self.open = False

        @contextmanager
        def reservation_fence(self, context, order):
            if not self.open:
                raise GatewayReservationAdmissionDenied()
            yield

    profile, settings, _, authority, _, _ = make(tmp_path, monkeypatch, write_enabled=True)
    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", Gate)
    monkeypatch.setattr(FakeSession, "refresh_session", refresh)
    monkeypatch.setattr(module.GatewaySettlementController, "tick", tick)
    resources = _resources(profile, authority)
    try:
        binding = resources.values["binding"]
        registry, reserve = _authenticated_reserve(binding, settings.account_scope)

        assert reserve("old")["status"] == "OK"
        FakeSession.instances[0].close_allowed = False
        failure.set()
        _wait(lambda: binding._owner.status().failure_category == "cleanup_failed")
        denied = reserve("new")
        assert denied["status"] == "UNKNOWN", denied
        from bt_api_gateway.direct_command_journal import DirectCommandJournalError
        with pytest.raises(DirectCommandJournalError, match="NOT_FOUND"):
            binding._journal.get(CLIENT_A.actor_ref, "new", CLIENT_A.principal_ref)
        assert reserve("old")["status"] == "OK"
    finally:
        FakeSession.instances[0].close_allowed = True
        registry.close()
        resources.close()


def test_stream_close_timeout_retains_guard_until_read_drains(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

    import bt_api_py.ctp_gateway_return_maintenance as return_module
    from bt_api_py.gateway_direct_service import GatewayReservationAdmissionDenied
    from tests.unit.test_iter63_gateway_direct_service import _context, _order

    class Gate(FakeReturnMaintenance):
        def __init__(self, *args):
            super().__init__(*args)
            self.open = True

        def begin_close(self):
            self.open = False

        @contextmanager
        def reservation_fence(self, context, order):
            if not self.open:
                raise GatewayReservationAdmissionDenied()
            yield

    profile, settings, _, authority, _, _ = make(tmp_path, monkeypatch, write_enabled=True)
    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", Gate)
    resources = _resources(profile, authority)
    stream = resources.values["binding"]._execution_stream
    entered, release = threading.Event(), threading.Event()
    responses = []
    context = _context(CLIENT_A.principal_ref, CLIENT_A.actor_ref, settings.account_scope)

    def fits(_):
        entered.set()
        assert release.wait(5)
        return True

    context = replace(context, result_fits=fits)
    reader = threading.Thread(target=lambda: responses.append(stream.handle(
        "direct.events.read_after", context=context,
        arguments={"after_sequence": 0, "expected_epoch": None, "limit": 1},
    )))
    reader.start()
    try:
        assert entered.wait(5)
        with pytest.raises(CtpGatewayTradingError, match="TRADING_OWNER_CLOSE_UNCONFIRMED"):
            resources.close()
        contender = LocalAccountSessionGuard(Path(settings.guard_root), settings.account_scope, "other")
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
        assert stream.handle("direct.events.progress", context=context, arguments={})["code"] == "EVENTS_UNAVAILABLE"
        assert resources.values["binding"]._owner.binding() is not None
        release.set()
        reader.join(5)
        assert not reader.is_alive()
        reserve = resources.values["binding"].handle("direct.reserve", context=context, arguments={
            "request_key": "late", "payload": _order(key="late", account=settings.account_scope),
            "ttl_seconds": 10,
        })
        assert reserve["code"] == "ADMISSION_DENIED"
    finally:
        release.set()
        reader.join(5)
        resources.close()
    assert responses[0]["status"] == "OK"
    contender.acquire()
    contender.release()


def test_reservation_commit_timeout_retains_guard_and_permanently_closes_gate(tmp_path, monkeypatch):
    import os

    from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

    import bt_api_py.ctp_gateway_return_maintenance as return_module

    actual_gate_type = return_module.CtpGatewayReturnMaintenance

    class Gate(FakeReturnMaintenance):
        def __init__(self, *args):
            super().__init__(*args)
            self.gate = actual_gate_type.__new__(actual_gate_type)
            self.gate._pid = os.getpid()
            self.gate._lock = threading.Lock()
            self.gate._closing = threading.Event()
            self.gate._open = True
            self.gate._status = "HEALTHY"

        def begin_close(self):
            self.gate.begin_close()

        def close(self, timeout_seconds=5):
            return self.gate.close(timeout_seconds)

        def reservation_fence(self, context, order):
            return self.gate.reservation_fence(context, order)

    profile, settings, _, authority, _, _ = make(tmp_path, monkeypatch, write_enabled=True)
    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", Gate)
    resources = _resources(profile, authority)
    binding = resources.values["binding"]
    registry, reserve = _authenticated_reserve(binding, settings.account_scope)
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    outcomes = []
    original_commit = binding._journal._commit

    def commit(db):
        if threading.current_thread() is binding._owner._thread and db.execute(
            "SELECT 1 FROM direct_journal_commands WHERE request_key='held'"
        ).fetchone():
            entered.set()
            assert release.wait(5)
        return original_commit(db)

    monkeypatch.setattr(binding._journal, "_commit", commit)

    worker = threading.Thread(target=lambda: (outcomes.append(reserve("held")), done.set()),
                              name="blocked-reservation")
    worker.start()
    try:
        assert entered.wait(5)
        start = time.monotonic()
        with pytest.raises(CtpGatewayTradingError, match="TRADING_OWNER_CLOSE_UNCONFIRMED"):
            resources.close()
        assert time.monotonic() - start < 1.0
        contender = LocalAccountSessionGuard(Path(settings.guard_root), settings.account_scope, "other")
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
        assert binding._owner.binding() is not None
        release.set()
        assert done.wait(5)
        assert outcomes[0]["status"] == "OK"
        denied = reserve("new")
        assert denied["code"] == "ADMISSION_DENIED", denied
        assert reserve("held")["status"] == "OK"
    finally:
        release.set()
        worker.join(5)
        registry.close()
        resources.close()
    contender.acquire()
    contender.release()


def make(
    tmp_path,
    monkeypatch,
    *,
    write_enabled=False,
    gate=lambda _: True,
    with_journal=True,
    account_key=None,
):
    FakeSession.instances = []
    FakeSession.close_ok = True
    FakeSession.connect_fails = False
    FakeSession.initial_status = SettlementStatus.CONFIRMED
    FakeSession.connect_hook = None
    from bt_api_base.market_protocol import MarketInstrumentKey

    import bt_api_py.ctp_gateway_return_maintenance as return_module

    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", FakeReturnMaintenance)
    monkeypatch.setattr(module, "_load_ctp_types", lambda: (FakeSession, FakeProvider))
    key = account_key or AccountKey("ctp", "simnow", "simnow", "00001", "09999")
    scope = key.account_key_id
    guard_root = tmp_path / "guard"
    guard_root.mkdir()
    flow_root = tmp_path / "flow"
    flow_root.mkdir(mode=0o700)
    journal_path = tmp_path / "journal.sqlite"
    if with_journal:
        journal = GatewayDirectCommandJournal.create(journal_path, scope, generation="old")
        journal_id = journal.journal_id
    else:
        journal_id = "missing-journal"
    settings = CtpGatewayTradingSettings(
        key,
        scope,
        guard_root,
        "owner",
        journal_path,
        journal_id,
        0,
        flow_root,
        "source",
        "entitlement",
        write_enabled=write_enabled,
        maintenance_interval_seconds=0.01,
        close_timeout_seconds=0.1,
    )
    endpoint = _free_endpoint()
    cfg = _service_config(endpoint).to_public_dict()
    cfg["components"]["gateway"]["settings"]["options"]["dispatch_enabled"] = True
    config = parse_service_config(cfg)
    server_public, server_secret = zmq.curve_keypair()
    client_public, _ = zmq.curve_keypair()
    resource = ResourceGrant(
        "gateway-a",
        "service.direct_write",
        "scope-a",
        WireScope(DOMAIN, account_ref=scope, strategy_ref=CLIENT_A.actor_ref),
        OPS | {"direct.dispatch"},
    )
    authority = TrustedServiceAuthority(
        identity=SERVICE,
        scope=WireScope(DOMAIN),
        scope_ref="scope-a",
        scope_version="scope-v1",
        security_ref="security-a",
        security_version="security-v1",
        registration_policy_ref="direct-policy-v1",
        registration_policy_version="direct-policy-v1",
        service_requirements_ref="direct-requirements-v1",
        service_requirements_version="direct-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: CLIENT_A.principal_ref},
        grants={CLIENT_A.principal_ref: (PrincipalGrant(CLIENT_A, (resource,)),)},
    )
    grants = {CLIENT_A.principal_ref: GatewayDirectGrant(CLIENT_A.actor_ref, frozenset({"rb"}))}
    factories = []

    def feed():
        factories.append("feed")

    def market(*args):
        factories.append("market")

    profile = build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "registry.sqlite",
        profile_ref="ctp-test",
        settings=settings,
        grants=grants,
        request_feed_factory=feed,
        market_stream_factory=market,
        assert_admitted=gate,
        return_settings=(
            module.CtpGatewayReturnSettings(
                (MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612"),)
            )
            if write_enabled
            else None
        ),
    )
    return profile, settings, config, authority, grants, factories


def _resources(profile, authority, *, cleanup_on_error=True):
    deployment = profile.deployment_factory()
    try:
        return deployment._provide_resources(
            "gateway", "gateway-a", {"role": "trading_owner", "dispatch_enabled": True}, authority.scope
        )
    except ServiceResourceConstructionError as error:
        # Failure-path tests deliberately retain the partial resource to check
        # retryable cleanup. Success-path callers opt in to cleanup on failure.
        if cleanup_on_error:
            error.resources.close()
        raise


def _wait(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_build_is_pure_and_owner_has_single_thread_and_guard(tmp_path, monkeypatch):
    profile, settings, config, authority, grants, calls = make(tmp_path, monkeypatch)
    assert FakeSession.instances == [] and calls == []
    deployment = profile.deployment_factory()
    assert FakeSession.instances == [] and calls == []
    resources = deployment._provide_resources(
        "gateway", "gateway-a", {"role": "trading_owner", "dispatch_enabled": True}, authority.scope
    )
    try:
        _wait(lambda: any(item[0] == "refresh" for item in FakeSession.instances[0].events))
        session = FakeSession.instances[0]
        assert calls == [] and len(FakeSession.instances) == 1
        assert session.kwargs["flow_account_scope"] == settings.account_scope
        assert session.owner != threading.get_ident()
        assert {thread for _, thread in session.events} == {session.owner}
        assert resources.ready() is True
        from bt_api_gateway.session_guard import (
            LocalAccountSessionGuard,
            LocalAccountSessionGuardError,
        )

        duplicate = LocalAccountSessionGuard(
            Path(settings.guard_root), settings.account_scope, "other"
        )
        with pytest.raises(LocalAccountSessionGuardError):
            duplicate.acquire()
    finally:
        resources.close()
    assert FakeSession.instances[0].events[-1] == ("close", FakeSession.instances[0].owner)


def test_settings_and_static_authority_fail_before_session(tmp_path, monkeypatch):
    profile, settings, config, authority, grants, calls = make(tmp_path, monkeypatch)
    with pytest.raises(CtpGatewayTradingError, match="INVALID_TRADING_SETTINGS"):
        replace(settings, account_scope="alias")
    with pytest.raises(CtpGatewayTradingError, match="INVALID_TRADING_SETTINGS"):
        replace(settings, account_key=AccountKey("ctp", "other", "simnow", "a", "b"))
    with pytest.raises(CtpGatewayTradingError, match="INVALID_TRADING_CONFIGURATION"):
        build_ctp_gateway_trading_profile(
            config,
            authority,
            tmp_path / "r2",
            profile_ref="p",
            settings=settings,
            grants={},
            request_feed_factory=lambda: None,
            market_stream_factory=lambda *_: None,
            assert_admitted=lambda _: True,
        )
    assert calls == [] and FakeSession.instances == []


def test_missing_journal_hands_off_cleanup_without_constructing_session(tmp_path, monkeypatch):
    profile, _settings, _config, authority, _grants, _calls = make(
        tmp_path, monkeypatch, with_journal=False
    )
    with pytest.raises(ServiceResourceConstructionError) as error:
        _resources(profile, authority, cleanup_on_error=False)
    error.value.resources.close()
    assert FakeSession.instances == []


def test_failed_connect_retains_guard_until_session_close_succeeds(tmp_path, monkeypatch):
    profile, settings, _config, authority, _grants, _calls = make(tmp_path, monkeypatch)
    FakeSession.connect_fails = True
    FakeSession.close_ok = False
    with pytest.raises(ServiceResourceConstructionError) as error:
        _resources(profile, authority, cleanup_on_error=False)
    session = FakeSession.instances[0]
    from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

    duplicate = LocalAccountSessionGuard(Path(settings.guard_root), settings.account_scope, "other")
    with pytest.raises(LocalAccountSessionGuardError):
        duplicate.acquire()
    with pytest.raises(CtpGatewayTradingError, match="TRADING_OWNER_CLOSE_UNCONFIRMED"):
        error.value.resources.close()
    session.close_allowed = True
    error.value.resources.close()
    duplicate.acquire()
    duplicate.release()


@pytest.mark.parametrize(
    "enabled,admitted,expected",
    [
        (False, True, "RESERVED"),
        (True, False, "RESERVED"),
        (True, None, "RESERVED"),
        (True, True, "RESERVED"),
        pytest.param(True, True, "SENT", marks=pytest.mark.xfail(strict=True,
            reason="Iter63 B: full trusted writer coverage is not yet proven; Gateway native dispatch is unavailable")),
    ],
)
def test_dispatch_requires_enabled_settlement_and_literal_business_admission(
    tmp_path, monkeypatch, enabled, admitted, expected
):
    from contextlib import nullcontext

    from tests.unit.test_iter63_gateway_direct_service import _order

    profile, settings, _config, authority, _grants, _calls = make(
        tmp_path, monkeypatch, write_enabled=enabled, gate=lambda _: admitted
    )
    resources = _resources(profile, authority)
    try:
        binding = resources.values["binding"]
        owner, journal = binding._owner, binding._journal
        _wait(lambda: FakeSession.instances[0].provider.counter >= 1)
        row = journal.reserve(
            CLIENT_A.actor_ref,
            "key",
            CLIENT_A.principal_ref,
            _order(key="key", account=settings.account_scope),
        )
        finished = threading.Event()
        command = GatewayDispatchCommand(
            row.strategy_scope,
            row.request_key,
            row.principal,
            row.reservation_id,
            row.digest,
            row.version,
        )
        assert (
            owner.enqueue(command, request_fence=lambda _: nullcontext(), finalize=finished.set)
            == "QUEUED"
        )
        assert finished.wait(5)
        stored = journal.get(CLIENT_A.actor_ref, "key", CLIENT_A.principal_ref)
        assert stored.status == expected
        if expected == "RESERVED":
            assert stored.version == row.version and stored.native_identity_json is None
        provider = FakeSession.instances[0].provider
        assert provider.sends == (["key"] if expected == "SENT" else [])
        assert set(provider.threads) == {FakeSession.instances[0].owner}
    finally:
        resources.close()


@pytest.mark.parametrize("enabled,expected_calls", [(False, 0), (True, 1)])
def test_unconfirmed_settlement_at_most_one_attempt(tmp_path, monkeypatch, enabled, expected_calls):
    profile, settings, _config, authority, _grants, _calls = make(
        tmp_path, monkeypatch, write_enabled=enabled
    )
    FakeSession.initial_status = SettlementStatus.UNCONFIRMED
    resources = _resources(profile, authority)
    try:
        provider = FakeSession.instances[0].provider
        _wait(lambda: provider.counter >= 4)
        assert provider.confirms == expected_calls
        row = resources.values["binding"]._journal.settlement_get(module.ROUTE, "20300101")
        assert (row is None) if not enabled else (row.status == "UNKNOWN" and row.marker_id)
    finally:
        resources.close()


def test_host_false_close_retains_owner_and_guard_for_retry(tmp_path, monkeypatch):
    from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

    from bt_api_py.service_host import ServiceHostError

    profile, settings, _config, _authority, _grants, _calls = make(tmp_path, monkeypatch)
    deployment = profile.deployment_factory()
    deployment.start()
    session = FakeSession.instances[0]
    session.close_allowed = False
    with pytest.raises(ServiceHostError):
        deployment.close()
    assert deployment.state == "close_failed"
    duplicate = LocalAccountSessionGuard(Path(settings.guard_root), settings.account_scope, "other")
    with pytest.raises(LocalAccountSessionGuardError):
        duplicate.acquire()
    session.close_allowed = True
    deployment.close()
    assert deployment.state == "closed"
    duplicate.acquire()
    duplicate.release()


def test_timed_out_start_keeps_partial_owner_until_session_stops(tmp_path, monkeypatch):
    from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

    profile, settings, config, authority, grants, calls = make(tmp_path, monkeypatch)
    entered, resume = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert resume.wait(5)

    FakeSession.connect_hook = block
    FakeSession.close_ok = False
    settings = replace(settings, startup_timeout_seconds=0.05)
    profile = build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "registry2.sqlite",
        profile_ref="p",
        settings=settings,
        grants=grants,
        request_feed_factory=lambda: None,
        market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True,
    )
    resources = None
    try:
        with pytest.raises(ServiceResourceConstructionError) as error:
            _resources(profile, authority, cleanup_on_error=False)
        resources = error.value.resources
        assert entered.is_set()
        duplicate = LocalAccountSessionGuard(
            Path(settings.guard_root), settings.account_scope, "other"
        )
        with pytest.raises(LocalAccountSessionGuardError):
            duplicate.acquire()
        with pytest.raises(CtpGatewayTradingError, match="TRADING_OWNER_CLOSE_UNCONFIRMED"):
            resources.close()
        resume.set()
        _wait(
            lambda: (
                bool(FakeSession.instances[0].events)
                and any(x[0] == "close" for x in FakeSession.instances[0].events)
            )
        )
        with pytest.raises(LocalAccountSessionGuardError):
            duplicate.acquire()
        FakeSession.instances[0].close_allowed = True
        resources.close()
        duplicate.acquire()
        duplicate.release()
    finally:
        resume.set()
        if resources is not None:
            FakeSession.instances[0].close_allowed = True
            resources.close()


def test_journal_identity_mismatch_rejected_before_native(tmp_path, monkeypatch):
    profile, settings, config, authority, grants, calls = make(tmp_path, monkeypatch)
    settings = replace(settings, expected_journal_id="wrong-journal")
    profile = build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "registry2.sqlite",
        profile_ref="p",
        settings=settings,
        grants=grants,
        request_feed_factory=lambda: None,
        market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True,
    )
    with pytest.raises(ServiceResourceConstructionError) as error:
        _resources(profile, authority, cleanup_on_error=False)
    error.value.resources.close()
    assert FakeSession.instances == [] and calls == []


def test_invalid_config_and_authority_fail_without_native(tmp_path, monkeypatch):
    profile, settings, config, authority, grants, calls = make(tmp_path, monkeypatch)
    bad_config = config.to_public_dict()
    bad_config["components"]["gateway"]["settings"]["options"]["dispatch_enabled"] = False
    bad_config = parse_service_config(bad_config)
    params = {
        "profile_ref": "p",
        "settings": settings,
        "grants": grants,
        "request_feed_factory": lambda: None,
        "market_stream_factory": lambda *_: None,
        "assert_admitted": lambda _: True,
    }
    with pytest.raises(CtpGatewayTradingError, match="INVALID_TRADING_CONFIGURATION"):
        build_ctp_gateway_trading_profile(bad_config, authority, tmp_path / "r2", **params)
    entry = authority.grants[CLIENT_A.principal_ref][0]
    wrong_scope = replace(entry.resources[0].scope, account_ref="wrong-account")
    wrong_resource = replace(entry.resources[0], scope=wrong_scope)
    wrong_entry = replace(entry, resources=(wrong_resource,))
    wrong_authority = replace(authority, grants={CLIENT_A.principal_ref: (wrong_entry,)})
    with pytest.raises(CtpGatewayTradingError, match="INVALID_TRADING_CONFIGURATION"):
        build_ctp_gateway_trading_profile(config, wrong_authority, tmp_path / "r3", **params)
    assert settings.account_key.account_id not in repr(settings)
    assert settings.account_key.subaccount_id not in repr(settings)
    assert calls == [] and FakeSession.instances == []


def test_existing_v2_journal_is_not_silently_upgraded(tmp_path, monkeypatch):
    import sqlite3

    profile, settings, _config, authority, _grants, calls = make(tmp_path, monkeypatch)
    with sqlite3.connect(settings.journal_path) as db:
        db.execute("DROP TABLE direct_journal_settlement")
        db.execute("UPDATE direct_journal_meta SET schema_version=2")
        before = db.execute(
            "SELECT schema_version,revision,journal_id FROM direct_journal_meta"
        ).fetchone()
    with pytest.raises(ServiceResourceConstructionError) as error:
        _resources(profile, authority, cleanup_on_error=False)
    error.value.resources.close()
    with sqlite3.connect(settings.journal_path) as db:
        assert (
            db.execute(
                "SELECT schema_version,revision,journal_id FROM direct_journal_meta"
            ).fetchone()
            == before
        )
        assert (
            db.execute(
                "SELECT count(*) FROM sqlite_master WHERE name='direct_journal_settlement'"
            ).fetchone()[0]
            == 0
        )
    assert FakeSession.instances == [] and calls == []


@pytest.mark.parametrize(
    "enabled,admitted,confirm_ok,expected,disconnect",
    [
        (False, True, True, "RESERVED", False),
        (True, False, True, "RESERVED", False),
        (True, True, True, "RESERVED", False),
        pytest.param(True, True, True, "SENT", False, marks=pytest.mark.xfail(strict=True,
            reason="Iter63 B: full trusted writer coverage is not yet proven; Gateway native dispatch is unavailable")),
        (True, True, False, "RESERVED", False),
        pytest.param(True, True, True, "SENT", True, marks=pytest.mark.xfail(strict=True,
            reason="Iter63 B: reconnect-safe Gateway native dispatch is not yet available")),
    ],
)
def test_real_ctp_session_trader_with_synthetic_native(
    tmp_path, monkeypatch, enabled, admitted, confirm_ok, expected, disconnect
):
    """Exercise the real Python CTP path; the native boundary is fully synthetic."""
    import importlib
    import sqlite3
    from contextlib import nullcontext
    from types import SimpleNamespace

    from bt_api_base.market_protocol import MarketInstrumentKey
    from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
    from bt_api_ctp.direct_write_provider import CtpDirectWriteProvider
    from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
    from bt_api_ctp.gateway_session import CtpGatewaySession
    from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

    import bt_api_py.ctp_gateway_return_maintenance as return_module
    from tests.unit.test_iter63_gateway_direct_service import _order

    ctp_tests = Path(__file__).resolve().parents[1] / "bt_api" / "bt_api_ctp" / "tests"
    monkeypatch.syspath_prepend(str(ctp_tests))
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    real_loader = module._load_ctp_types
    real_maintenance = return_module.CtpGatewayReturnMaintenance
    key = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    _, settings, config, authority, grants, _ = make(
        tmp_path, monkeypatch, write_enabled=enabled, gate=lambda _: admitted, account_key=key
    )
    settings = replace(settings, connect_timeout_seconds=0.5, close_timeout_seconds=0.5)
    grants = {CLIENT_A.principal_ref: GatewayDirectGrant(CLIENT_A.actor_ref, frozenset({"rb2612"}))}
    monkeypatch.setattr(module, "_load_ctp_types", real_loader)
    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", real_maintenance)
    feeds, natives, events, marker, dispatch_errors = [], [], [], [], []
    allow_stop = threading.Event()
    queries = threading.Event()

    class Native(helper._NativeApi):
        def ReqQryOrder(self, field, request_id):
            events.append(("return_orders", threading.get_ident()))
            self.client._handle_query_callback("orders", None, None, request_id, True)
            return 0

        def ReqQryTrade(self, field, request_id):
            events.append(("return_trades", threading.get_ident()))
            self.client._handle_query_callback("trades", None, None, request_id, True)
            return 0

        def ReqQrySettlementInfoConfirm(self, field, request_id):
            events.append(("query", threading.get_ident()))
            queries.set()
            return super().ReqQrySettlementInfoConfirm(field, request_id)

        def ReqSettlementInfoConfirm(self, field, request_id):
            events.append(("confirm", threading.get_ident()))
            with sqlite3.connect(settings.journal_path) as db:
                row = db.execute(
                    "SELECT status,marker_id,account_binding,trading_day,initial_observation_id "
                    "FROM direct_journal_settlement"
                ).fetchone()
            marker.append(row)
            self.calls.append("confirm_settlement")
            if not confirm_ok:
                return 1
            self.confirmation_day = helper.TODAY
            self.client._spi.OnRspSettlementInfoConfirm(
                SimpleNamespace(BrokerID="9999", InvestorID="account", ConfirmDate=helper.TODAY),
                SimpleNamespace(ErrorID=0, ErrorMsg=""),
                request_id,
                True,
            )
            return 0

        def ReqOrderInsert(self, field, request_id):
            events.append(("insert", threading.get_ident()))
            return super().ReqOrderInsert(field, request_id)

    def start(trader, **_kwargs):
        events.append(("start", threading.get_ident()))
        native = Native()
        native.client = trader
        native.confirmation_day = "20261105"
        trader._api = native
        helper._login(trader, native, session_id=19)
        natives.append(native)

    def stop(trader, *, timeout):
        events.append(("stop", threading.get_ident()))
        return CtpNativeStopReceipt(
            trader.connection_generation, False, True, allow_stop.is_set(), False, False
        )

    monkeypatch.setattr(TraderClient, "start", start)
    monkeypatch.setattr(TraderClient, "stop_and_wait", stop)
    submit = CtpDirectWriteProvider.submit_prepared_order

    def traced_submit(self, *args, **kwargs):
        try:
            result = submit(self, *args, **kwargs)
            dispatch_errors.append(result.error_code)
            return result
        except Exception as exc:
            dispatch_errors.append(repr(exc))
            raise

    monkeypatch.setattr(CtpDirectWriteProvider, "submit_prepared_order", traced_submit)
    refresh = CtpGatewaySession.refresh_session

    def traced_refresh(self):
        try:
            return refresh(self)
        except Exception:
            events.append(("refresh_failed", threading.get_ident()))
            raise

    monkeypatch.setattr(CtpGatewaySession, "refresh_session", traced_refresh)

    def feed():
        result = CtpRequestDataFuture(
            None,
            broker_id="9999",
            user_id="account",
            password="synthetic",
            td_front="tcp://offline-neutral-td.invalid:30001",
            md_front="tcp://offline-neutral-md.invalid:30011",
            auto_settlement_confirm=False,
            ctp_env_profile="config_front_pair",
            ctp_environment="simnow",
            trusted_flow_root=str(settings.trusted_flow_root),
            flow_account_scope=key.account_key_id,
        )
        result._connect_timeout = 0.05
        feeds.append(result)
        return result

    profile = build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "real-registry.sqlite",
        profile_ref="real-ctp-test",
        settings=settings,
        grants=grants,
        request_feed_factory=feed,
        market_stream_factory=lambda *_: pytest.fail("market must remain lazy"),
        assert_admitted=lambda _: admitted,
        return_settings=(
            module.CtpGatewayReturnSettings(
                (MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612"),)
            )
            if enabled
            else None
        ),
    )
    assert feeds == [] and events == []
    try:
        resources = _resources(profile, authority)
    except ServiceResourceConstructionError as exc:
        allow_stop.set()
        exc.resources.close()
        raise
    try:
        assert queries.wait(5)
        if enabled:
            _wait(lambda: any(e[0] == "return_trades" for e in events))
        journal, owner = resources.values["binding"]._journal, resources.values["binding"]._owner
        if enabled:
            _wait(lambda: len(marker) == 1)
            _wait(lambda: journal.settlement_get(module.ROUTE, helper.TODAY).status != "ATTEMPTING")
        else:
            _wait(lambda: len([e for e in events if e[0] == "query"]) >= 3)
            assert marker == []
        _wait(lambda: len([e for e in events if e[0] == "query"]) >= 3)
        payload = _order(key="key", symbol="rb2612", account=settings.account_scope)
        payload.update(exchange_id="SHFE", offset="open", time_in_force="GFD", quantity_unit="contracts")
        row = journal.reserve(
            CLIENT_A.actor_ref,
            "key",
            CLIENT_A.principal_ref,
            payload,
        )
        done = threading.Event()
        command = GatewayDispatchCommand(
            row.strategy_scope,
            row.request_key,
            row.principal,
            row.reservation_id,
            row.digest,
            row.version,
        )
        assert (
            owner.enqueue(command, request_fence=lambda _: nullcontext(), finalize=done.set)
            == "QUEUED"
        )
        assert done.wait(5)
        stored = journal.get(CLIENT_A.actor_ref, "key", CLIENT_A.principal_ref)
        assert stored.status == expected, dispatch_errors
        if expected == "RESERVED":
            assert stored.version == row.version and stored.native_identity_json is None
        _wait(lambda: len([e for e in events if e[0] == "query"]) >= 3)
        assert len(feeds) == len(natives) == 1
        assert len([e for e in events if e[0] == "confirm"]) == int(enabled)
        assert len([event for event in events if event[0] == "insert"]) == int(expected == "SENT")
        if expected == "SENT":
            assert dispatch_errors == [None]
            replay_done = threading.Event()
            assert (
                owner.enqueue(
                    command, request_fence=lambda _: nullcontext(), finalize=replay_done.set
                )
                == "QUEUED"
            )
            assert replay_done.wait(5)
            assert journal.get(CLIENT_A.actor_ref, "key", CLIENT_A.principal_ref).status == "SENT"
            assert len([event for event in events if event[0] == "insert"]) == 1
        if enabled:
            state, marker_id, account, day, observation_id = marker[0]
            assert state == "ATTEMPTING" and marker_id and observation_id
            assert account == feeds[0].trader_client._account_fingerprint and day == helper.TODAY
            settlement = journal.settlement_get(module.ROUTE, helper.TODAY)
            assert settlement.status == ("CONFIRMED" if confirm_ok else "UNKNOWN")
            assert settlement.marker_id == marker_id
            assert settlement.initial_observation_id == observation_id
        assert len({thread for _, thread in events}) == 1
        assert events[0][1] != threading.get_ident()
        duplicate = LocalAccountSessionGuard(
            Path(settings.guard_root), settings.account_scope, "other"
        )
        if disconnect:
            original_binding = owner.binding()
            trader = feeds[0].trader_client
            returns = resources.values["binding"]._reservation_fence.__self__
            feeds[0].trader_client._on_front_disconnected(123)
            _wait(lambda: returns.status()["status"] == "SESSION_CHANGED")
            assert owner.binding() == original_binding and owner.status().thread_alive
            assert feeds[0].trader_client is trader and len(feeds) == len(natives) == 1
            assert not any(e[0] == "stop" for e in events)
            assert returns.status()["reservation_open"] is False
            with pytest.raises(GatewayReservationAdmissionDenied):
                with returns.reservation_fence(None, None):
                    pass
            with pytest.raises(LocalAccountSessionGuardError):
                duplicate.acquire()
            duplicate_done = threading.Event()
            assert (
                owner.enqueue(
                    command, request_fence=lambda _: nullcontext(), finalize=duplicate_done.set
                )
                == "QUEUED"
            )
            assert duplicate_done.wait(1)
            assert journal.get(CLIENT_A.actor_ref, "key", CLIENT_A.principal_ref).status == expected
            assert len([event for event in events if event[0] == "insert"]) == 1
        with pytest.raises(CtpGatewayTradingError, match="TRADING_OWNER_CLOSE_UNCONFIRMED"):
            resources.close()
        with pytest.raises(LocalAccountSessionGuardError):
            duplicate.acquire()
        assert feeds[0].trader_client is not None
    finally:
        allow_stop.set()
        resources.close()
    duplicate.acquire()
    duplicate.release()


def _read_profile(tmp_path, monkeypatch, *, read_principals=None, account_key=None):
    from bt_api_base.account_protocol import AccountResourceKey
    from bt_api_base.market_protocol import MarketInstrumentKey

    from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer
    import bt_api_py.gateway_instrument_service as instrument_module

    real_instrument_service = instrument_module.GatewayInstrumentService

    class LegacyInstrumentStub:
        """Only isolates old fake-session lifecycle tests; not R26 evidence."""
        def begin_close(self):
            pass

        def maintain_registrations(self, _contexts):
            pass

        def cleanup_on_owner(self):
            return True

    def instrument_service(owner, session, *args):
        if type(session) is FakeSession:
            return LegacyInstrumentStub()
        return real_instrument_service(owner, session, *args)

    monkeypatch.setattr(instrument_module, "GatewayInstrumentService", instrument_service)

    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch, account_key=account_key)
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.account_read")
    config = parse_service_config(raw)
    direct_entry = authority.grants[CLIENT_A.principal_ref][0]
    read_resource = ResourceGrant(
        "gateway-a",
        "service.account_read",
        "scope-a",
        WireScope(DOMAIN, account_ref=settings.account_scope, strategy_ref=CLIENT_A.actor_ref),
        module.ACCOUNT_READ_OPERATIONS,
    )
    read_entry = replace(direct_entry, resources=(*direct_entry.resources, read_resource))
    authority = replace(authority, grants={CLIENT_A.principal_ref: (read_entry,)})
    issuer = DurableOrderCorrelationIssuer(tmp_path / "identity.sqlite")
    issuer_id = issuer.issuer_id
    issuer.close()
    read_settings = module.CtpGatewayAccountReadSettings(
        tmp_path / "account.sqlite",
        "account-state",
        (AccountResourceKey("future", "cross"),),
        tmp_path / "identity.sqlite",
        issuer_id,
        (MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612"),),
        refresh_interval_seconds=0.05,
        max_staleness_seconds=0.5,
        request_timeout_seconds=0.5,
    )
    read_principals = (
        frozenset({CLIENT_A.principal_ref}) if read_principals is None else read_principals
    )

    def build(
        *,
        cfg=config,
        auth=authority,
        read=read_settings,
        principals=read_principals,
        feed_factory=lambda: None,
    ):
        return build_ctp_gateway_trading_profile(
            cfg,
            auth,
            tmp_path / "read-registry.sqlite",
            profile_ref="read-test",
            settings=settings,
            grants=grants,
            request_feed_factory=feed_factory,
            market_stream_factory=lambda *_: None,
            assert_admitted=lambda _: True,
            account_read_settings=read,
            account_read_principals=principals,
        )

    return build, settings, config, authority, grants, read_settings


def test_account_read_settings_require_explicit_scope_and_distinct_journal(tmp_path, monkeypatch):
    build, settings, config, authority, _grants, read = _read_profile(tmp_path, monkeypatch)
    build()
    with pytest.raises(CtpGatewayTradingError):
        build(principals=frozenset())
    with pytest.raises(CtpGatewayTradingError):
        build(read=replace(read, journal_path=settings.journal_path))
    alias = tmp_path / "hardlink.sqlite"
    import os

    os.link(settings.journal_path, alias)
    with pytest.raises(CtpGatewayTradingError):
        build(read=replace(read, journal_path=alias))
    with pytest.raises(CtpGatewayTradingError):
        build(read=replace(read, observation_identity_path=settings.journal_path))
    issuer_alias = tmp_path / "issuer-hardlink.sqlite"
    os.link(read.observation_identity_path, issuer_alias)
    with pytest.raises(CtpGatewayTradingError):
        build(read=replace(read, journal_path=issuer_alias))
    issuer_symlink = tmp_path / "issuer-symlink.sqlite"
    issuer_symlink.symlink_to(read.observation_identity_path)
    with pytest.raises(CtpGatewayTradingError):
        build(read=replace(read, journal_path=issuer_symlink))
    instrument = read.observation_instruments[0]
    with pytest.raises(CtpGatewayTradingError):
        replace(read, observation_instruments=(instrument, instrument))
    with pytest.raises(CtpGatewayTradingError):
        build(
            read=replace(read, observation_instruments=(replace(instrument, environment="live"),))
        )
    with pytest.raises(CtpGatewayTradingError):
        build(read=replace(read, observation_instruments=(replace(instrument, venue="other"),)))
    entry = authority.grants[CLIENT_A.principal_ref][0]
    wrong = replace(
        entry.resources[1], scope=replace(entry.resources[1].scope, account_ref="other")
    )
    with pytest.raises(CtpGatewayTradingError):
        build(
            auth=replace(
                authority,
                grants={
                    CLIENT_A.principal_ref: (replace(entry, resources=(entry.resources[0], wrong)),)
                },
            )
        )
    assert FakeSession.instances == []


def test_read_generation_restarts_after_validated_return_reader_rebuild(tmp_path, monkeypatch):
    from datetime import datetime

    from bt_api_base.account_protocol import AccountObservation
    from bt_api_gateway.account_read_model import GatewayAccountReadModel

    import bt_api_py.ctp_gateway_return_maintenance as return_module

    _, settings, config, authority, grants, read = _read_profile(tmp_path, monkeypatch)
    settings = replace(settings, write_enabled=True)
    epochs, reads, rebuilds = [], [], []
    original = GatewayAccountReadModel.start_generation

    def start(model, generation):
        result = original(model, generation)
        epochs.append(generation)
        return result

    def read_account(session, *, generation, timeout):
        reads.append((generation, session.provider.session))
        stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return AccountObservation(
            session.key,
            f"obs-{len(reads)}",
            read.required_coverage,
            (),
            (),
            None,
            stamp,
            stamp,
            "multi_query",
            True,
            True,
            (),
            True,
        )

    class ReturnStub(FakeReturnMaintenance):
        def refresh_generation(self):
            rebuilds.append(FakeSession.instances[0].provider.session)

    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", ReturnStub)
    monkeypatch.setattr(GatewayAccountReadModel, "start_generation", start)
    monkeypatch.setattr(FakeSession, "read_account", read_account, raising=False)
    profile = build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "relogin-registry.sqlite",
        profile_ref="relogin",
        settings=settings,
        grants=grants,
        request_feed_factory=lambda: None,
        market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True,
        account_read_settings=read,
        account_read_principals=frozenset({CLIENT_A.principal_ref}),
        return_settings=module.CtpGatewayReturnSettings(read.observation_instruments),
    )
    resources = _resources(profile, authority, cleanup_on_error=True)
    try:
        model = resources.values["read_service"].state_source
        _wait(lambda: model.ready)
        FakeSession.instances[0].provider.session = 2
        _wait(lambda: len(epochs) == 2 or not resources.ready())
        assert len(epochs) == 2 and epochs[0] != epochs[1] and rebuilds == [2]
        _wait(lambda: model.ready and any(native == 2 for _, native in reads))
        assert all(guard == epochs[0] for guard, _ in reads)
        assert resources.ready()
    finally:
        resources.close()


def test_due_account_and_return_reads_share_one_phase_per_maintenance_tick(tmp_path, monkeypatch):
    from datetime import datetime

    from bt_api_base.account_protocol import AccountObservation

    import bt_api_py.ctp_gateway_return_maintenance as return_module

    _, settings, config, authority, grants, read = _read_profile(tmp_path, monkeypatch)
    settings = replace(settings, write_enabled=True)
    phases, ticks = [], []

    def read_account(session, *, generation, timeout):
        phases.append((len(ticks), "account"))
        stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return AccountObservation(
            session.key,
            f"obs-{len(phases)}",
            read.required_coverage,
            (),
            (),
            None,
            stamp,
            stamp,
            "multi_query",
            True,
            True,
            (),
            True,
        )

    class AlwaysDueReturns(FakeReturnMaintenance):
        def due(self, now):
            return True

        def poll(self):
            # Called once per maintenance tick before any read phase.
            ticks.append(1)
            return super().poll()

        def query(self):
            phases.append((len(ticks), "return"))

    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", AlwaysDueReturns)
    monkeypatch.setattr(FakeSession, "read_account", read_account, raising=False)
    profile = build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "schedule-registry.sqlite",
        profile_ref="schedule",
        settings=settings,
        grants=grants,
        request_feed_factory=lambda: None,
        market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True,
        account_read_settings=read,
        account_read_principals=frozenset({CLIENT_A.principal_ref}),
        return_settings=module.CtpGatewayReturnSettings(read.observation_instruments),
    )
    resources = _resources(profile, authority)
    try:
        _wait(lambda: sum(kind == "account" for _, kind in phases) >= 3)
        assert sum(kind == "return" for _, kind in phases) >= 3
        assert len({tick for tick, _ in phases}) == len(phases)
        assert phases[:2] == [(1, "return"), (2, "account")]
    finally:
        resources.close()


def test_real_profile_account_read_recovers_after_same_native_relogin(tmp_path, monkeypatch):
    import importlib

    from bt_api_base.account_protocol import AccountResourceKey
    from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
    from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
    from bt_api_gateway.account_read_model import GatewayAccountReadModel

    import bt_api_py.ctp_gateway_return_maintenance as return_module
    from tests.integration.test_iter63_ctp_gateway_return_maintenance import _relogin_same_spi

    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "bt_api/bt_api_ctp/tests")
    )
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    real_loader = module._load_ctp_types
    real_maintenance = return_module.CtpGatewayReturnMaintenance
    key = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    _, settings, config, authority, grants, read = _read_profile(
        tmp_path, monkeypatch, account_key=key
    )
    settings = replace(settings, write_enabled=True, maintenance_interval_seconds=0.01)
    read = replace(read, required_coverage=(AccountResourceKey("future", "futures"),))
    monkeypatch.setattr(module, "_load_ctp_types", real_loader)
    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", real_maintenance)
    natives, queries, epochs = [], [], []
    original_start = GatewayAccountReadModel.start_generation

    def start_generation(model, generation):
        result = original_start(model, generation)
        epochs.append(generation)
        return result

    monkeypatch.setattr(GatewayAccountReadModel, "start_generation", start_generation)

    def start(trader, **kwargs):
        native = helper._NativeApi()
        native.client = trader

        def response(kind, rows):
            def call(field, request_id):
                queries.append((kind, trader.connection_generation))
                for row in rows:
                    trader._handle_query_callback(kind, row, None, request_id, False)
                trader._handle_query_callback(kind, None, None, request_id, True)
                return 0

            return call

        native.ReqQryTradingAccount = response(
            "account",
            [
                {
                    "AccountID": "account",
                    "BrokerID": "9999",
                    "CurrencyID": "CNY",
                    "Balance": 1000,
                    "Available": 800,
                }
            ],
        )
        native.ReqQryInvestorPosition = response("positions", [])
        native.ReqQryOrder = response("orders", [])
        native.ReqQryTrade = response("trades", [])
        trader._api = native
        helper._login(trader, native, session_id=19)
        natives.append(native)

    monkeypatch.setattr(TraderClient, "start", start)
    monkeypatch.setattr(
        TraderClient,
        "stop_and_wait",
        lambda trader, **kwargs: CtpNativeStopReceipt(
            trader.connection_generation, False, True, True, False, False
        ),
    )

    def feed():
        return CtpRequestDataFuture(
            None,
            broker_id="9999",
            user_id="account",
            password="synthetic",
            td_front="tcp://offline-neutral-td.invalid:30001",
            md_front="tcp://offline-neutral-md.invalid:30011",
            auto_settlement_confirm=False,
            ctp_env_profile="config_front_pair",
            ctp_environment="simnow",
            trusted_flow_root=str(settings.trusted_flow_root),
            flow_account_scope=key.account_key_id,
        )

    profile = build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "real-relogin.sqlite",
        profile_ref="real-relogin",
        settings=settings,
        grants=grants,
        request_feed_factory=feed,
        market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True,
        account_read_settings=read,
        account_read_principals=frozenset({CLIENT_A.principal_ref}),
        return_settings=module.CtpGatewayReturnSettings(read.observation_instruments),
    )
    resources = _resources(profile, authority)
    try:
        model = resources.values["read_service"].state_source
        _wait(lambda: model.ready and any(kind == "trades" for kind, _ in queries))
        before = natives[0].client.connection_generation
        _relogin_same_spi(natives[0], helper.TODAY, 20)
        _wait(lambda: len(epochs) == 2 or not resources.ready())
        assert len(epochs) == 2 and epochs[0] != epochs[1]
        _wait(
            lambda: model.ready and any(kind == "trades" and gen != before for kind, gen in queries)
        )
        assert len(natives) == 1 and resources.ready()
        assert any(kind == "account" and gen != before for kind, gen in queries)
    finally:
        resources.close()


@pytest.mark.parametrize("failure", ["missing", "wrong_id"])
def test_observation_issuer_refusal_precedes_session_creation(tmp_path, monkeypatch, failure):
    from bt_api_gateway.session_guard import LocalAccountSessionGuard

    build, settings, _config, authority, _grants, read = _read_profile(tmp_path, monkeypatch)
    selected = (
        replace(read, observation_identity_path=tmp_path / "missing.sqlite")
        if failure == "missing"
        else replace(read, expected_observation_issuer_id="0" * 64)
    )
    with pytest.raises(ServiceResourceConstructionError) as error:
        _resources(build(read=selected), authority, cleanup_on_error=False)
    error.value.resources.close()
    assert FakeSession.instances == []
    contender = LocalAccountSessionGuard(Path(settings.guard_root), settings.account_scope, "other")
    contender.acquire()
    contender.release()
    if failure == "missing":
        assert not Path(selected.observation_identity_path).exists()


def test_account_read_shared_owner_invalidates_and_recovers(tmp_path, monkeypatch):
    from datetime import datetime

    from bt_api_base.account_protocol import AccountObservation
    from bt_api_gateway.account_read_model import GatewayAccountReadModel

    build, settings, _config, authority, _grants, read = _read_profile(tmp_path, monkeypatch)
    calls, errors = [], []
    finish = GatewayAccountReadModel.finish_refresh

    def traced_finish(model, *args):
        try:
            return finish(model, *args)
        except Exception as exc:
            errors.append((type(exc).__name__, getattr(exc, "code", None)))
            raise

    monkeypatch.setattr(GatewayAccountReadModel, "finish_refresh", traced_finish)
    blocked, release = threading.Event(), threading.Event()
    failure = threading.Event()

    def read_account(session, *, generation, timeout):
        assert threading.get_ident() == session.owner
        calls.append((generation, timeout, threading.get_ident()))
        if failure.is_set():
            blocked.set()
            assert release.wait(5)
            raise ValueError("synthetic read unavailable")
        stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            return AccountObservation(
                session.key,
                f"obs-{len(calls)}",
                read.required_coverage,
                (),
                (),
                None,
                stamp,
                stamp,
                "multi_query",
                True,
                True,
                (),
                True,
            )
        except Exception as exc:
            errors.append((type(exc).__name__, str(exc)))
            raise

    monkeypatch.setattr(FakeSession, "read_account", read_account, raising=False)
    resources = _resources(build(), authority)
    try:
        service = resources.values["read_service"]
        model = service.state_source
        _wait(lambda: model.ready or errors)
        assert model.ready, errors
        assert len(FakeSession.instances) == 1
        assert {call[2] for call in calls} == {FakeSession.instances[0].owner}
        assert resources.ready() is True
        failure.set()
        assert blocked.wait(5)
        assert not model.ready
        assert resources.ready() is True
        with pytest.raises(CtpGatewayTradingError):
            resources.close()
        release.set()
        _wait(lambda: not model.ready)
    finally:
        release.set()
        resources.close()
    assert not model.ready


def test_real_ctp_session_read_failure_keeps_direct_owner_available(tmp_path, monkeypatch):
    import importlib

    from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
    from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture

    tests = Path(__file__).resolve().parents[1] / "bt_api" / "bt_api_ctp" / "tests"
    monkeypatch.syspath_prepend(str(tests))
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    real_loader = module._load_ctp_types
    key = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    build, settings, _config, authority, _grants, _read = _read_profile(
        tmp_path, monkeypatch, account_key=key
    )
    monkeypatch.setattr(module, "_load_ctp_types", real_loader)
    feeds, natives, reads = [], [], []

    def start(trader, **_kwargs):
        native = helper._NativeApi()
        native.client = trader

        def reject_query(field, request_id):
            reads.append(threading.get_ident())
            return 1

        native.ReqQryTradingAccount = reject_query
        native.ReqQryInvestorPosition = reject_query
        native.ReqQryOrder = reject_query
        trader._api = native
        helper._login(trader, native, session_id=19)
        natives.append(native)

    def stop(trader, *, timeout):
        return CtpNativeStopReceipt(trader.connection_generation, False, True, True, False, False)

    monkeypatch.setattr(TraderClient, "start", start)
    monkeypatch.setattr(TraderClient, "stop_and_wait", stop)

    def feed():
        value = CtpRequestDataFuture(
            None,
            broker_id="9999",
            user_id="account",
            password="synthetic",
            td_front="tcp://offline-neutral-td.invalid:30001",
            md_front="tcp://offline-neutral-md.invalid:30011",
            auto_settlement_confirm=False,
            ctp_env_profile="config_front_pair",
            ctp_environment="simnow",
            trusted_flow_root=str(settings.trusted_flow_root),
            flow_account_scope=key.account_key_id,
        )
        feeds.append(value)
        return value

    resources = _resources(build(feed_factory=feed), authority)
    try:
        _wait(lambda: bool(reads))
        assert len(feeds) == len(natives) == 1
        assert resources.ready() and not resources.values["read_service"].state_source.ready
        assert len(set(reads)) == 1 and reads[0] != threading.get_ident()
    finally:
        resources.close()


def test_account_read_application_scope_does_not_authorize_direct_principal(tmp_path, monkeypatch):
    from bt_api_base.service_protocol import WireIdentity, WireRole
    from bt_api_gateway.service_component import GatewayServiceError

    build, _settings, _config, authority, _grants, _read = _read_profile(tmp_path, monkeypatch)
    direct = authority.grants[CLIENT_A.principal_ref][0]
    app = WireIdentity(WireRole.APPLICATION_CLIENT, "read-only-principal", "read-only-app")
    read_resource = replace(
        direct.resources[1], scope=replace(direct.resources[1].scope, strategy_ref=None)
    )
    app_entry = PrincipalGrant(app, (read_resource,))
    pub, _secret = zmq.curve_keypair()
    authority = replace(
        authority,
        principals={**authority.principals, pub: app.principal_ref},
        grants={
            CLIENT_A.principal_ref: (replace(direct, resources=(direct.resources[0],)),),
            app.principal_ref: (app_entry,),
        },
    )
    resources = _resources(
        build(auth=authority, principals=frozenset({app.principal_ref})), authority
    )
    try:
        service = resources.values["read_service"]
        account_scope = read_resource.scope
        result = service.handle(
            "account.head",
            principal_ref=app.principal_ref,
            identity=app,
            scope=account_scope,
            arguments={},
        )
        assert result["latest_cursor"] == 0
        with pytest.raises(GatewayServiceError):
            service.handle(
                "account.head",
                principal_ref=CLIENT_A.principal_ref,
                identity=CLIENT_A,
                scope=direct.resources[1].scope,
                arguments={},
            )
    finally:
        resources.close()


@pytest.mark.parametrize("fault", ["refresh", "generation_regressed"])
def test_read_model_closes_on_session_identity_loss_and_retains_guard(tmp_path, monkeypatch, fault):
    from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

    build, settings, _config, authority, _grants, read = _read_profile(tmp_path, monkeypatch)
    FakeSession.close_ok = False
    revoked = threading.Event()
    invalid_generation_seen = threading.Event()
    after_fault_reads = []
    release_owner = threading.Event()
    previous = FakeSession.refresh_session

    def refresh(session):
        if revoked.is_set():
            raise ValueError("synthetic changed session")
        return previous(session)

    monkeypatch.setattr(FakeSession, "refresh_session", refresh)
    if fault == "generation_regressed":
        from datetime import datetime
        from bt_api_base.account_protocol import AccountObservation

        previous_generation = FakeProvider.session_generation

        def observe_generation(provider):
            value = previous_generation(provider)
            if value == 0:
                invalid_generation_seen.set()
            return value

        def read_account(session, *, generation, timeout):
            if revoked.is_set():
                after_fault_reads.append(True)
            stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            return AccountObservation(
                session.key, f"obs-{time.monotonic_ns()}", read.required_coverage,
                (), (), None, stamp, stamp, "multi_query", True, True, (), True,
            )

        monkeypatch.setattr(FakeProvider, "session_generation", observe_generation)
        monkeypatch.setattr(FakeSession, "read_account", read_account, raising=False)
    resources = _resources(build(), authority)
    try:
        owner = resources.values["binding"]._owner
        model = resources.values["read_service"].state_source
        if fault == "refresh":
            revoked.set()
        else:
            _wait(lambda: model.ready)
            owner_held = threading.Event()

            def hold_owner():
                owner_held.set()
                assert release_owner.wait(5)

            owner.submit_service_call(hold_owner)
            assert owner_held.wait(5)
            revoked.set()
            FakeSession.instances[0].provider.session = 0
            release_owner.set()
        _wait(lambda: owner.binding() is None)
        assert model.read_status()["ready"] is False
        if fault == "generation_regressed":
            assert invalid_generation_seen.is_set() and after_fault_reads == []
        contender = LocalAccountSessionGuard(
            Path(settings.guard_root), settings.account_scope, "other"
        )
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
        with pytest.raises(CtpGatewayTradingError):
            resources.close()
        FakeSession.instances[0].close_allowed = True
    finally:
        release_owner.set()
        FakeSession.instances[0].close_allowed = True
        resources.close()
    contender.acquire()
    contender.release()


def test_observation_issuer_held_until_session_close_confirmed(tmp_path, monkeypatch):
    from bt_api_py.order_correlation_issuer import OrderCorrelationIssuerError

    build, _settings, _config, authority, _grants, read = _read_profile(tmp_path, monkeypatch)
    FakeSession.close_ok = False
    resources = _resources(build(), authority)
    issuer = FakeSession.instances[0].kwargs["observation_issuer"]
    try:
        with pytest.raises(CtpGatewayTradingError):
            resources.close()
        assert issuer.issuer_id == read.expected_observation_issuer_id
    finally:
        FakeSession.instances[0].close_allowed = True
        resources.close()
    with pytest.raises(OrderCorrelationIssuerError):
        _ = issuer.issuer_id


def test_real_ctp_profile_reopens_same_active_order_observation(tmp_path, monkeypatch):
    import importlib

    from bt_api_base.account_protocol import AccountResourceKey
    from bt_api_base.market_protocol import MarketInstrumentKey
    from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
    from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
    from bt_api_ctp.gateway_session import CtpGatewaySession

    tests = Path(__file__).resolve().parents[1] / "bt_api" / "bt_api_ctp" / "tests"
    monkeypatch.syspath_prepend(str(tests))
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    real_loader = module._load_ctp_types
    key = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    build, settings, _config, authority, _grants, read = _read_profile(
        tmp_path, monkeypatch, account_key=key
    )
    monkeypatch.setattr(module, "_load_ctp_types", real_loader)
    instrument = MarketInstrumentKey("ctp", "CFFEX", "simnow", "future", "IF2612", "IF2612")
    read = replace(
        read,
        required_coverage=(AccountResourceKey("future", "futures"),),
        observation_instruments=(instrument,),
    )
    feeds, natives, queries, startup_errors = [], [], [], []
    session_init = CtpGatewaySession.__init__
    session_connect = CtpGatewaySession.connect
    session_refresh = CtpGatewaySession.refresh_session

    def traced_init(self, *args, **kwargs):
        try:
            return session_init(self, *args, **kwargs)
        except Exception as exc:
            startup_errors.append((type(exc).__name__, str(exc)))
            raise

    def traced_connect(self, *args, **kwargs):
        try:
            return session_connect(self, *args, **kwargs)
        except Exception as exc:
            startup_errors.append((type(exc).__name__, str(exc)))
            raise

    monkeypatch.setattr(CtpGatewaySession, "__init__", traced_init)
    monkeypatch.setattr(CtpGatewaySession, "connect", traced_connect)

    def traced_refresh(self):
        try:
            return session_refresh(self)
        except Exception as exc:
            startup_errors.append((type(exc).__name__, str(exc)))
            raise

    monkeypatch.setattr(CtpGatewaySession, "refresh_session", traced_refresh)

    def start(trader, **_kwargs):
        native = helper._NativeApi()
        native.client = trader
        trader._api = native
        helper._login(trader, native, session_id=19)
        account = {
            "AccountID": "account",
            "BrokerID": "9999",
            "CurrencyID": "CNY",
            "Balance": 1000,
            "Available": 800,
        }
        order = {
            "BrokerID": "9999",
            "InvestorID": "account",
            "InstrumentID": "IF2612",
            "ExchangeID": "CFFEX",
            "TradingDay": helper.TODAY,
            "OrderSysID": "SYS1",
            "OrderStatus": "1",
            "Direction": "0",
            "CombOffsetFlag": "0",
            "VolumeTotalOriginal": 3,
            "VolumeTraded": 1,
            "OrderPriceType": "2",
            "ContingentCondition": "1",
            "LimitPrice": 4001,
            "InsertDate": helper.TODAY,
            "InsertTime": "00:00:00",
        }

        def responder(kind, rows):
            def submit(_field, request_id):
                queries.append((kind, threading.get_ident()))
                for row in rows:
                    trader._handle_query_callback(kind, row, None, request_id, False)
                trader._handle_query_callback(kind, None, None, request_id, True)
                return 0

            return submit

        native.ReqQryTradingAccount = responder("account", [account])
        native.ReqQryInvestorPosition = responder("positions", [])
        native.ReqQryOrder = responder("orders", [order])
        natives.append(native)

    def stop(trader, *, timeout):
        return CtpNativeStopReceipt(trader.connection_generation, False, True, True, False, False)

    monkeypatch.setattr(TraderClient, "start", start)
    monkeypatch.setattr(TraderClient, "stop_and_wait", stop)

    def feed():
        value = CtpRequestDataFuture(
            None,
            broker_id="9999",
            user_id="account",
            password="synthetic",
            td_front="tcp://offline-neutral-td.invalid:30001",
            md_front="tcp://offline-neutral-md.invalid:30011",
            auto_settlement_confirm=False,
            ctp_env_profile="config_front_pair",
            ctp_environment="simnow",
            trusted_flow_root=str(settings.trusted_flow_root),
            flow_account_scope=key.account_key_id,
        )
        feeds.append(value)
        return value

    ids = []
    for _ in range(2):
        try:
            resources = _resources(build(read=read, feed_factory=feed), authority)
        except ServiceResourceConstructionError as exc:
            exc.resources.close()
            pytest.fail(f"synthetic session startup failed: {startup_errors}")
        try:
            model = resources.values["read_service"].state_source
            _wait(lambda model=model: model.ready)
            orders = model.latest_snapshot.payload["observation"]["open_orders"]
            assert len(orders) == 1 and orders[0]["client_order_id"] is None
            assert orders[0]["order_id"].startswith("sdk-order-observation-v1-")
            assert "SYS1" not in repr(model.latest_snapshot.payload)
            ids.append(orders[0]["order_id"])
        finally:
            resources.close()
    assert ids[0] == ids[1]
    assert len(feeds) == len(natives) == 2
    assert {kind for kind, _ in queries} == {"account", "positions", "orders"}
    assert all(owner_thread != threading.get_ident() for _, owner_thread in queries)


def test_observation_issuer_close_error_keeps_guard_until_retry(tmp_path, monkeypatch):
    from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

    from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer

    build, settings, _config, authority, _grants, _read = _read_profile(tmp_path, monkeypatch)
    real_close = DurableOrderCorrelationIssuer.close
    allow_close = threading.Event()

    def closing(issuer):
        if not allow_close.is_set():
            raise OSError("synthetic issuer close failure")
        return real_close(issuer)

    monkeypatch.setattr(DurableOrderCorrelationIssuer, "close", closing)
    resources = _resources(build(), authority)
    try:
        with pytest.raises(CtpGatewayTradingError):
            resources.close()
        assert FakeSession.instances[0].events[-1][0] == "close"
        contender = LocalAccountSessionGuard(
            Path(settings.guard_root), settings.account_scope, "other"
        )
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
    finally:
        allow_close.set()
        resources.close()
    contender.acquire()
    contender.release()


def test_failed_issuer_initialization_keeps_guard_until_exact_close(tmp_path, monkeypatch):
    from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

    import bt_api_py.order_correlation_issuer as issuer_module

    build, settings, _config, authority, _grants, read = _read_profile(tmp_path, monkeypatch)
    original_connect = issuer_module.sqlite3.connect
    allow_close = threading.Event()
    connections = []

    class Connection:
        def __init__(self, wrapped):
            object.__setattr__(self, "wrapped", wrapped)
            self.close_attempts = 0

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def __setattr__(self, name, value):
            if name in {"wrapped", "close_attempts"}:
                object.__setattr__(self, name, value)
            else:
                setattr(self.wrapped, name, value)

        def close(self):
            self.close_attempts += 1
            if not allow_close.is_set():
                raise OSError("synthetic close failure")
            return self.wrapped.close()

    def connect(path, *args, **kwargs):
        result = original_connect(path, *args, **kwargs)
        if "identity.sqlite" in str(path):
            result = Connection(result)
            connections.append(result)
        return result

    monkeypatch.setattr(issuer_module.sqlite3, "connect", connect)
    wrong = replace(read, expected_observation_issuer_id="0" * 64)
    with pytest.raises(ServiceResourceConstructionError) as caught:
        _resources(build(read=wrong), authority, cleanup_on_error=False)
    assert FakeSession.instances == [] and len(connections) == 1
    contender = LocalAccountSessionGuard(Path(settings.guard_root), settings.account_scope, "other")
    try:
        with pytest.raises(CtpGatewayTradingError):
            caught.value.resources.close()
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
        assert connections[0].close_attempts >= 2
    finally:
        allow_close.set()
        caught.value.resources.close()
    assert connections[0].close_attempts >= 3
    with pytest.raises(issuer_module.sqlite3.ProgrammingError):
        connections[0].wrapped.execute("SELECT 1")
    contender.acquire()
    contender.release()


def test_direct_only_does_not_open_observation_issuer(tmp_path, monkeypatch):
    from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer

    profile, _settings, _config, authority, _grants, _calls = make(tmp_path, monkeypatch)

    def forbidden(*_args, **_kwargs):
        pytest.fail("direct-only must not open an observation issuer")

    monkeypatch.setattr(DurableOrderCorrelationIssuer, "open_existing", forbidden)
    resources = _resources(profile, authority)
    try:
        assert resources.ready()
    finally:
        resources.close()


@pytest.mark.parametrize(
    ("live_ticket", "refresh", "per_cycle"),
    [(True, 10.0, False), (False, 10.0, False), (True, 0, True)],
)
def test_settlement_observation_is_rate_limited_on_owner_thread(
    tmp_path, monkeypatch, live_ticket, refresh, per_cycle
):
    # SimNow 63.09: observing settlement every maintenance cycle starved queued
    # owner work. A live ticket is refreshed every settlement_refresh_seconds;
    # without one, retries are at most once per second; 0 restores per-cycle.
    import bt_api_py.ctp_gateway_return_maintenance as return_module

    _, settings, config, authority, grants, read = _read_profile(tmp_path, monkeypatch)
    settings = replace(settings, write_enabled=True, maintenance_interval_seconds=0.01,
                       settlement_refresh_seconds=refresh)
    polls, observations = [], []

    def tick(controller):
        observations.append(1)
        return controller.status

    monkeypatch.setattr(module.GatewaySettlementController, "tick", tick)
    monkeypatch.setattr(module.GatewaySettlementController, "ready",
                        property(lambda controller: live_ticket and bool(observations)))

    class CountingReturns(FakeReturnMaintenance):
        def poll(self):
            polls.append(1)
            return super().poll()

    def read_account(session, *, generation, timeout):
        from datetime import datetime

        from bt_api_base.account_protocol import AccountObservation

        stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return AccountObservation(session.key, f"obs-{len(polls)}", read.required_coverage,
                                  (), (), None, stamp, stamp, "multi_query", True, True, (), True)

    monkeypatch.setattr(FakeSession, "read_account", read_account, raising=False)

    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", CountingReturns)
    profile = build_ctp_gateway_trading_profile(
        config, authority, tmp_path / "settlement-rate-registry.sqlite",
        profile_ref="settlement-rate", settings=settings, grants=grants,
        request_feed_factory=lambda: None, market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True, account_read_settings=read,
        account_read_principals=frozenset({CLIENT_A.principal_ref}),
        return_settings=module.CtpGatewayReturnSettings(read.observation_instruments),
    )
    resources = _resources(profile, authority)
    try:
        _wait(lambda: len(polls) >= 20)
        if per_cycle:
            assert len(observations) >= 10
        else:
            # Twenty cycles (well under one second): only the first observation.
            assert len(observations) == 1
    finally:
        resources.close()


@pytest.mark.parametrize("value", [-1, 25.5, float("nan"), float("inf"), True, "10"])
def test_settlement_refresh_setting_is_validated(tmp_path, monkeypatch, value):
    _, settings, *_ = _read_profile(tmp_path, monkeypatch)
    with pytest.raises(CtpGatewayTradingError, match="INVALID_TRADING_SETTINGS"):
        replace(settings, settlement_refresh_seconds=value)


def test_deployment_refuses_storage_another_user_could_write(tmp_path, monkeypatch):
    # User decision 2026-11-09 (OS-enforced isolation): startup refuses before
    # the Registry, guard or journal open when another user could write them.
    import os

    from bt_api_py.gateway_storage_trust import GatewayStorageTrustError

    _, settings, config, authority, grants, read = _read_profile(tmp_path, monkeypatch)
    profile = build_ctp_gateway_trading_profile(
        config, authority, tmp_path / "trust-registry.sqlite", profile_ref="trust",
        settings=settings, grants=grants, request_feed_factory=lambda: None,
        market_stream_factory=lambda *_: None, assert_admitted=lambda _: True,
        account_read_settings=read, account_read_principals=frozenset({CLIENT_A.principal_ref}),
        return_settings=module.CtpGatewayReturnSettings(read.observation_instruments),
    )
    journal_dir = Path(settings.journal_path).parent
    mode = journal_dir.stat().st_mode & 0o777
    os.chmod(journal_dir, mode | 0o020)
    try:
        # The journal directory may also be an ancestor of other storage.
        with pytest.raises(GatewayStorageTrustError, match="WRITABLE_BY_OTHERS"):
            profile.deployment_factory()
        assert not (tmp_path / "trust-registry.sqlite").exists()
    finally:
        os.chmod(journal_dir, mode)
