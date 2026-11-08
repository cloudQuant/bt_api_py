"""Synthetic trusted CTP Gateway profile composition and lifecycle tests."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
import zmq
from bt_api_base.account_protocol import AccountKey
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
from bt_api_py.gateway_direct_service import GatewayDirectGrant
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

    def close(self):
        self.events.append(("close", threading.get_ident()))
        return self.close_allowed


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
    )
    return profile, settings, config, authority, grants, factories


def _resources(profile, authority):
    deployment = profile.deployment_factory()
    return deployment._provide_resources(
        "gateway", "gateway-a", {"role": "trading_owner", "dispatch_enabled": True}, authority.scope
    )


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
        _resources(profile, authority)
    error.value.resources.close()
    assert FakeSession.instances == []


def test_failed_connect_retains_guard_until_session_close_succeeds(tmp_path, monkeypatch):
    profile, settings, _config, authority, _grants, _calls = make(tmp_path, monkeypatch)
    FakeSession.connect_fails = True
    FakeSession.close_ok = False
    with pytest.raises(ServiceResourceConstructionError) as error:
        _resources(profile, authority)
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
        (True, True, "SENT"),
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
        assert journal.get(CLIENT_A.actor_ref, "key", CLIENT_A.principal_ref).status == expected
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
            _resources(profile, authority)
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
        _resources(profile, authority)
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
        _resources(profile, authority)
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
        (True, True, True, "SENT", False),
        (True, True, False, "RESERVED", False),
        (True, True, True, "SENT", True),
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

    from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
    from bt_api_ctp.direct_write_provider import CtpDirectWriteProvider
    from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
    from bt_api_ctp.gateway_session import CtpGatewaySession
    from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

    from tests.unit.test_iter63_gateway_direct_service import _order

    ctp_tests = Path(__file__).resolve().parents[1] / "bt_api" / "bt_api_ctp" / "tests"
    monkeypatch.syspath_prepend(str(ctp_tests))
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    real_loader = module._load_ctp_types
    key = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    _, settings, config, authority, grants, _ = make(
        tmp_path, monkeypatch, write_enabled=enabled, gate=lambda _: admitted, account_key=key
    )
    settings = replace(settings, connect_timeout_seconds=0.5, close_timeout_seconds=0.5)
    grants = {CLIENT_A.principal_ref: GatewayDirectGrant(CLIENT_A.actor_ref, frozenset({"rb2612"}))}
    monkeypatch.setattr(module, "_load_ctp_types", real_loader)
    feeds, natives, events, marker, dispatch_errors = [], [], [], [], []
    allow_stop = threading.Event()
    queries = threading.Event()

    class Native(helper._NativeApi):
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
    submit = CtpDirectWriteProvider.submit_order

    def traced_submit(self, *args, **kwargs):
        try:
            result = submit(self, *args, **kwargs)
            dispatch_errors.append(result.error_code)
            return result
        except Exception as exc:
            dispatch_errors.append(repr(exc))
            raise

    monkeypatch.setattr(CtpDirectWriteProvider, "submit_order", traced_submit)
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
        journal, owner = resources.values["binding"]._journal, resources.values["binding"]._owner
        if enabled:
            _wait(lambda: len(marker) == 1)
            _wait(lambda: journal.settlement_get(module.ROUTE, helper.TODAY).status != "ATTEMPTING")
        else:
            _wait(lambda: len([e for e in events if e[0] == "query"]) >= 3)
            assert marker == []
        _wait(lambda: len([e for e in events if e[0] == "query"]) >= 3)
        payload = _order(key="key", symbol="rb2612", account=settings.account_scope)
        payload.update(exchange_id="SHFE", offset="open", time_in_force="GFD")
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
        assert journal.get(CLIENT_A.actor_ref, "key", CLIENT_A.principal_ref).status == expected, (
            dispatch_errors
        )
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
            feeds[0].trader_client._on_front_disconnected(123)
            _wait(lambda: owner.binding() is None)
            _wait(lambda: any(e[0] == "stop" for e in events))
            assert any(e[0] == "refresh_failed" for e in events)
            duplicate_done = threading.Event()
            assert (
                owner.enqueue(
                    command, request_fence=lambda _: nullcontext(), finalize=duplicate_done.set
                )
                == "CLOSING"
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
