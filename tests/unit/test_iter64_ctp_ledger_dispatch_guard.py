"""Synthetic CTP dispatch guards for a ledger without a venue-bound cut."""

from contextlib import nullcontext
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest
from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
from bt_api_base.direct_write_protocol import (
    DirectSubmitResult,
    NativeOrderIdentity,
    PreDispatchRecord,
    WriteOperation,
)
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_delegation import GatewayDelegateGrant, authorize_delegated_command
from bt_api_gateway.direct_dispatch import _CommitPort, _identity

from bt_api_py.ctp_gateway_trading import ROUTE, CtpGatewayReturnSettings
from bt_api_py.gateway_direct_owner import GatewayCancelCommand, GatewayDispatchCommand
from bt_api_py.gateway_direct_service import CANCEL_OPERATIONS
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant
from tests.test_iter63_ctp_gateway_trading import (
    CLIENT_A,
    FakeProvider,
    FakeSession,
    _resources,
    _wait,
    make,
)
from tests.unit.test_iter63_gateway_direct_service import _order
from tests.unit.test_iter64_ctp_gateway_v2_reopen import _delegated, _fence, _profile, _start


@pytest.fixture
def profile_with_ledger(tmp_path, monkeypatch):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch, write_enabled=True)
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["options"]["cancel_enabled"] = True
    config = parse_service_config(raw)
    principal = authority.grants[CLIENT_A.principal_ref][0]
    resource = principal.resources[0]
    authority = replace(authority, grants={CLIENT_A.principal_ref: (PrincipalGrant(
        CLIENT_A, (replace(resource, operations=resource.operations | CANCEL_OPERATIONS),),
    ),)})
    monkeypatch.setattr(FakeProvider, "assert_cancel_ready", lambda *_a, **_kw: None, raising=False)
    instrument = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612")
    profile, options, authority = _profile(
        tmp_path, settings, config, authority, grants,
        return_settings=CtpGatewayReturnSettings((instrument,)),
        upgrade_controls_from_v9=True,
        upgrade_strategy_ledger_from_v10=True,
        upgrade_account_extensions_from_v11=True,
    )
    resources = _start(profile, options, authority)
    try:
        yield settings, resources.values["binding"], monkeypatch
    finally:
        resources.close()


def _existing(journal, monkeypatch, key, offset, *, delegation=None):
    """Seed a same-generation prior reservation using a synthetic old capacity decision."""
    if delegation is None:
        principal = CLIENT_A.principal_ref
        payload = dict(_order(key=key, account=journal.account_scope), offset=offset)
        strategy = CLIENT_A.actor_ref
        kwargs = {}
    else:
        principal, payload, permit = delegation
        strategy = "strategy"
        kwargs = {"reservation_fence": _fence, "delegation_permit": permit}
    # Establish the state that could have existed before the venue-cut fix.
    # No test monkeypatch is active during any attempted dispatch.
    with monkeypatch.context() as temporary:
        temporary.setattr(journal, "_check_strategy_capacity", lambda *_a, **_kw: None)
        return journal.reserve(strategy, key, principal, payload, ttl_seconds=30, **kwargs)


def _enqueue(owner, row, *, delegation_permit=None):
    done = Event()
    command = GatewayDispatchCommand(row.strategy_scope, row.request_key, row.principal,
                                     row.reservation_id, row.digest, row.version)
    result = owner.enqueue(command, request_fence=lambda _: nullcontext(), finalize=done.set,
                           delegation_permit=delegation_permit)
    assert result == "QUEUED"
    assert done.wait(5)


@pytest.mark.parametrize("offset", ["open", "close_today"])
def test_existing_same_generation_direct_reservation_is_not_dispatched(profile_with_ledger, offset):
    _, binding, monkeypatch = profile_with_ledger
    journal, owner = binding._journal, binding._owner
    row = _existing(journal, monkeypatch, "direct-" + offset, offset)
    gate = owner._dispatcher._admission
    seen = []
    monkeypatch.setattr(owner._dispatcher, "claims_available", lambda: True)
    monkeypatch.setattr(owner._dispatcher, "_admission", lambda record: seen.append(gate(record)) or seen[-1])
    _enqueue(owner, row)
    assert seen == [False]
    after = journal.get(row.strategy_scope, row.request_key, row.principal)
    assert after.status == "RESERVED" and after.native_identity_json is None
    assert FakeSession.instances[-1].provider.sends == []


@pytest.mark.parametrize("kind", ["risk", "execution"])
@pytest.mark.parametrize("offset", ["open", "close_today"])
def test_existing_delegated_reservation_is_not_dispatched(profile_with_ledger, kind, offset):
    settings, binding, monkeypatch = profile_with_ledger
    journal, owner = binding._journal, binding._owner
    key = "delegated-" + kind
    principal, payload, reserve_permit = _delegated(settings, kind, key)
    payload["offset"] = offset
    command = replace(reserve_permit.command, payload_digest=gateway_payload_digest(payload))
    identity = WireIdentity(WireRole.SERVICE, principal, command.actor_ref)
    scope = WireScope("domain", account_ref=settings.account_scope, strategy_ref="strategy", run_id="run")
    grant = GatewayDelegateGrant(identity, settings.account_scope, "strategy", "domain", kind,
                                 "grant-v1", frozenset({command.instrument}),
                                 frozenset({"direct.reserve", "direct.dispatch"}))
    context = SimpleNamespace(identity=identity, scope=scope, principal_ref=principal,
                              registration_id="registration")
    def permit(operation):
        return authorize_delegated_command(
            context, grant, command, operation, payload, registry_recheck=lambda _: True,
            current_grant=lambda: grant, instrument_recheck=lambda _: True,
            parent_recheck=(lambda *_: True) if kind == "execution" else None,
        )

    row = _existing(journal, monkeypatch, key, offset, delegation=(principal, payload, permit("direct.reserve")))
    gate = owner._dispatcher._admission
    seen = []
    monkeypatch.setattr(owner._dispatcher, "claims_available", lambda: True)
    monkeypatch.setattr(owner._dispatcher, "_admission", lambda record: seen.append(gate(record)) or seen[-1])
    _enqueue(owner, row, delegation_permit=permit("direct.dispatch"))
    assert seen == [False]
    after = journal.get(row.strategy_scope, row.request_key, row.principal)
    assert after.status == "RESERVED" and after.native_identity_json is None
    assert FakeSession.instances[-1].provider.sends == []


def _mark_existing(owner, journal, row, status):
    identity = NativeOrderIdentity(ROUTE, "client-a", {"trading_day": "20300101", "front_id": 1,
                                                     "session_id": 2}, 1)
    native = _identity(identity, ROUTE)
    keys = ("synthetic-order-key",)

    def write():
        started = journal.begin_dispatch(row.strategy_scope, row.request_key, row.reservation_id,
                                         row.digest, row.principal, row.version, native, keys).record
        if status == "DISPATCHING":
            return started
        return journal.finish(row.strategy_scope, row.request_key, row.reservation_id, row.digest,
                              row.principal, started.version, native, keys, status)

    return owner.submit_service_call(write).result(timeout_seconds=3), identity


@pytest.mark.parametrize("status", ["DISPATCHING", "SENT", "UNKNOWN"])
def test_existing_nonreserved_state_is_not_rewritten(profile_with_ledger, status):
    _, binding, monkeypatch = profile_with_ledger
    journal, owner = binding._journal, binding._owner
    row = _existing(journal, monkeypatch, "prior-" + status.lower(), "open")
    before, _ = _mark_existing(owner, journal, row, status)
    _enqueue(owner, before)
    after = journal.get(before.strategy_scope, before.request_key, before.principal)
    assert after == before
    assert FakeSession.instances[-1].provider.sends == []


def test_cancel_and_status_remain_available(profile_with_ledger, monkeypatch):
    _, binding, _ = profile_with_ledger
    journal, owner = binding._journal, binding._owner
    row = _existing(journal, monkeypatch, "sent-target", "open")
    sent, identity = _mark_existing(owner, journal, row, "SENT")
    provider = FakeSession.instances[-1].provider
    cancels = []

    def prepare(_provider, target, symbol):
        return PreparedCancelRequest("cancel-reservation", target, symbol, "20300101", 1,
                                     {"request_id": 1}, ("cancel-key-1",))

    def submit(_provider, prepared, grant, verifier):
        verifier.consume(grant, exchange_name=ROUTE, operation=WriteOperation.CANCEL,
                         session_generation=1)
        cancels.append(prepared.target_identity)
        return DirectSubmitResult(True, prepared.target_identity)

    monkeypatch.setattr(FakeProvider, "prepare_cancel_order", prepare, raising=False)
    monkeypatch.setattr(FakeProvider, "submit_prepared_cancel", submit, raising=False)
    monkeypatch.setattr(FakeProvider, "release_cancel_reservation", lambda *_: None, raising=False)
    cancel_journal = GatewayDirectCancelJournal(journal)
    cancel = owner.submit_service_call(lambda: cancel_journal.request(
        sent.strategy_scope, "cancel-op", sent.principal, sent.request_key, sent.reservation_id,
        sent.digest, target_identity=identity, symbol="rb",
    )).result(timeout_seconds=3)
    done = Event()
    assert owner.enqueue_cancel(
        GatewayCancelCommand(sent.strategy_scope, "cancel-op", sent.principal, cancel.version),
        request_fence=lambda _: nullcontext(), finalize=done.set,
    ) == "QUEUED"
    assert done.wait(5)
    observed = cancel_journal.get(sent.strategy_scope, "cancel-op", sent.principal)
    assert observed.status == "SENT"
    assert cancels == [identity]
    assert journal.get(sent.strategy_scope, sent.request_key, sent.principal) == sent
    assert provider.sends == []


def test_existing_reservation_can_still_be_released(profile_with_ledger):
    _, binding, monkeypatch = profile_with_ledger
    journal, owner = binding._journal, binding._owner
    row = _existing(journal, monkeypatch, "release-target", "open")
    released = owner.submit_service_call(lambda: journal.release(
        row.strategy_scope, row.request_key, row.reservation_id, row.digest,
        row.principal, "release-op", row.version,
    )).result(timeout_seconds=3)
    assert released.status == "RELEASED"
    assert journal.get(row.strategy_scope, row.request_key, row.principal).status == "RELEASED"


def test_queued_reservation_checks_gate_when_owner_resumes(profile_with_ledger):
    _, binding, monkeypatch = profile_with_ledger
    journal, owner = binding._journal, binding._owner
    row = _existing(journal, monkeypatch, "queued", "open")
    gate, seen = owner._dispatcher._admission, []
    monkeypatch.setattr(owner._dispatcher, "claims_available", lambda: True)
    monkeypatch.setattr(owner._dispatcher, "_admission", lambda record: seen.append(gate(record)) or seen[-1])
    entered, resume, done = Event(), Event(), Event()

    def hold():
        entered.set()
        assert resume.wait(3)

    pending = owner.submit_service_call(hold)
    try:
        assert entered.wait(3)
        command = GatewayDispatchCommand(row.strategy_scope, row.request_key, row.principal,
                                         row.reservation_id, row.digest, row.version)
        assert owner.enqueue(command, request_fence=lambda _: nullcontext(), finalize=done.set) == "QUEUED"
    finally:
        resume.set()
    pending.result(timeout_seconds=3)
    assert done.wait(3) and seen == [False]
    assert journal.get(row.strategy_scope, row.request_key, row.principal).status == "RESERVED"
    assert FakeSession.instances[-1].provider.sends == []


def test_ledger_disabled_preserves_existing_place_gate(tmp_path, monkeypatch):
    profile, settings, _, authority, _, _ = make(tmp_path, monkeypatch, write_enabled=True)
    resources = _resources(profile, authority)
    try:
        binding = resources.values["binding"]
        journal, owner = binding._journal, binding._owner
        row = journal.reserve(CLIENT_A.actor_ref, "normal", CLIENT_A.principal_ref,
                              _order(key="normal", account=settings.account_scope))
        _wait(lambda: FakeSession.instances[-1].provider.counter >= 1)
        assert owner.submit_service_call(lambda: owner._dispatcher._admission(row)).result(timeout_seconds=3) is True
    finally:
        resources.close()


@pytest.mark.parametrize("deny_on", [2, 3])
def test_gate_is_rechecked_inside_marker_transaction(profile_with_ledger, deny_on):
    settings, binding, monkeypatch = profile_with_ledger
    journal, owner, dispatcher = binding._journal, binding._owner, binding._owner._dispatcher
    row = _existing(journal, monkeypatch, "marker-race", "open")
    original_gate = dispatcher._admission
    seen = []

    def simulated_change(record):
        # A test-only allow reaches the marker; real CTP ledger authority is
        # absent throughout, so the next recheck uses the production gate.
        seen.append(record.request_key)
        return True if len(seen) < deny_on else original_gate(record)

    def attempt():
        monkeypatch.setattr(dispatcher, "_admission", simulated_change)
        monkeypatch.setattr(dispatcher, "_identity_keys", lambda _: ("synthetic-marker-key",))
        identity = NativeOrderIdentity(ROUTE, "synthetic-ref", {"trading_day": "20300101"}, 1)
        record = PreDispatchRecord(
            WriteOperation.PLACE, settings.account_scope, ROUTE, "rb", identity, None,
            dict(_order(key="marker-race", account=settings.account_scope), offset="open"),
            "20300101", 1, "synthetic",
        )
        dispatcher._active = True
        try:
            port = _CommitPort(dispatcher, row, lambda _: nullcontext(), None, None, None)
            try:
                port.commit(record)
            except Exception as error:
                return type(error).__name__, str(error)
        finally:
            dispatcher._active = False

    assert owner.submit_service_call(attempt).result(timeout_seconds=3) == (
        "DirectDispatchError", "ADMISSION_DENIED"
    )
    assert len(seen) == deny_on
    after = journal.get(row.strategy_scope, row.request_key, row.principal)
    assert after.status == "RESERVED" and after.native_identity_json is None
    with journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM direct_journal_strong_keys").fetchone()[0] == 0
    assert FakeSession.instances[-1].provider.sends == []
