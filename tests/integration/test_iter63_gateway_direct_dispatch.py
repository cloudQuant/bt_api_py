"""Offline SDK/Gateway binding with SQLite, local guard and typed order requests."""

import json
import sqlite3
from decimal import Decimal

import pytest
from bt_api_base.direct_write_protocol import (
    DirectExecutionEvent,
    DirectExecutionEventKind,
    DirectOrderReservation,
    DirectOrderStatus,
    DirectSubmitResult,
    DirectWriteError,
    DirectWriteErrorPolicy,
    NativeOrderIdentity,
    WriteOperation,
)
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import DirectDispatchError
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.gateway_direct_dispatch import (
    GatewayDirectCompositionError,
    compose_gateway_direct_dispatcher,
    decode_gateway_order_request,
)
from bt_api_py.write_admission import WriteAdmission

ROUTE = "SYNTHETIC"
ACCOUNT = "synthetic-account"


def request(key="one"):
    return OrderRequest(
        symbol="X",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1.25"),
        account_id=ACCOUNT,
        client_order_id=key,
        price=Decimal("120.50"),
        time_in_force="IOC",
        reduce_only=True,
        idempotency_key="idem",
        quantity_unit="contracts",
        position_side="long",
        offset="close",
        position_id="pos",
        position_mode="dual_side",
        exchange_id="SHFE",
        hedge_flag="1",
        hedge_class="speculation",
    )


class Provider:
    execution_ids_trading_day_scoped = True
    direct_write_error_policy = DirectWriteErrorPolicy(frozenset({"outcome_unknown"}), frozenset())

    def __init__(self, db):
        self.db = db
        self.refs, self.sends, self.releases, self.events, self.requests = [], [], [], [], []
        self.changed_session = self.unknown = self.mutate_request = False

    def reserve_order(self, order):
        assert type(order) is OrderRequest
        ref = f"{len(self.refs) + 1:012d}"
        self.refs.append(ref)
        if self.mutate_request:
            object.__setattr__(order, "quantity", Decimal("999"))
        native = NativeOrderIdentity(
            ROUTE, ref, {"trading_day": "20300101", "front_id": 1, "session_id": 7}, 1
        )
        return DirectOrderReservation(native, order.symbol, ref)

    def release_reservation(self, reservation):
        self.releases.append(reservation.reservation_id)

    def session_generation(self):
        return 2 if self.changed_session else 1

    def submit_order(self, reservation, order, grant, verifier):
        with sqlite3.connect(self.db) as db:
            status, identity, payload_json = db.execute(
                "SELECT status, native_identity_json, payload_json FROM direct_journal_commands WHERE request_key=?",
                (order.client_order_id,),
            ).fetchone()
        assert status == "DISPATCHING"
        assert json.loads(identity)["client_order_id"] == reservation.identity.client_order_id
        assert type(order) is OrderRequest and order.to_dict() == json.loads(payload_json)
        verifier.consume(
            grant, exchange_name=ROUTE, operation=WriteOperation.PLACE, session_generation=1
        )
        self.requests.append(order)
        self.sends.append(reservation.identity)
        if self.unknown:
            raise DirectWriteError("outcome_unknown")
        return DirectSubmitResult(True, reservation.identity)

    def drain_execution_events(self, max_events):
        events, self.events = self.events[:max_events], self.events[max_events:]
        return tuple(events)


@pytest.fixture
def context(tmp_path):
    directory = tmp_path / "guards"
    directory.mkdir()
    guard = LocalAccountSessionGuard(directory, ACCOUNT, "synthetic-owner")
    generation = guard.acquire()
    db = tmp_path / "journal.sqlite"
    journal = GatewayDirectCommandJournal.create(
        db, ACCOUNT, generation=generation, clock=lambda: 100.0
    )
    provider = Provider(db)
    coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: True}), lambda _: provider)
    yield journal, guard, provider, coordinator
    guard.release()


def build(context, gate=lambda row: True, expected=None):
    journal, guard, provider, coord = context
    return compose_gateway_direct_dispatcher(
        coord,
        ROUTE,
        journal,
        guard,
        gate,
        expected_local_provider=provider if expected is None else expected,
    )


def reserve(journal, key, payload=None):
    return journal.reserve(
        "strategy", key, "principal", request(key).to_dict() if payload is None else payload
    )


def dispatch(dispatcher, row, **kwargs):
    return dispatcher.dispatch(
        row.strategy_scope,
        row.request_key,
        row.principal,
        row.reservation_id,
        row.digest,
        row.version,
        **kwargs,
    )


def current(journal, row):
    return journal.get(row.strategy_scope, row.request_key, row.principal)


def test_reverse_dispatch_binds_full_order_and_correlates(context):
    journal, _, provider, coordinator = context
    first, second = reserve(journal, "first"), reserve(journal, "second")
    assert (
        not provider.refs
        and first.native_identity_json is None
        and second.native_identity_json is None
    )
    dispatcher = build(context)
    second_result, first_result = dispatch(dispatcher, second), dispatch(dispatcher, first)
    assert second_result.status == first_result.status == "SENT"
    assert provider.refs == ["000000000001", "000000000002"]
    assert [obj.to_dict() for obj in provider.requests] == [
        request("second").to_dict(),
        request("first").to_dict(),
    ]
    assert dispatch(dispatcher, second) == second_result and len(provider.sends) == 2
    for result, native in zip((second_result, first_result), provider.sends, strict=True):
        assert result.strong_identity_keys == coordinator.strong_identity_keys(ROUTE, native)
        provider.events.append(
            DirectExecutionEvent(
                DirectExecutionEventKind.ORDER,
                ROUTE,
                "X",
                native,
                status=DirectOrderStatus.ACCEPTED,
            )
        )
    events = coordinator.poll_events(ROUTE)
    assert [event.marker_id for event in events] == [second.reservation_id, first.reservation_id]
    assert all(event.matched and event.host_scope == ACCOUNT for event in events)


def test_session_change_after_commit_is_not_sent(context):
    journal, _, provider, _ = context
    row = reserve(journal, "one")
    provider.changed_session = True
    dispatcher = build(context)
    result = dispatch(dispatcher, row)
    assert result.status == "NOT_SENT" and provider.refs == provider.releases
    assert not provider.sends and dispatch(dispatcher, row) == result


def test_disabled_sdk_admission_prevents_reserve_and_commit(context):
    journal, _, provider, coordinator = context
    row = reserve(journal, "one")
    coordinator.admission._enabled[ROUTE] = False
    with pytest.raises(DirectDispatchError, match="PRE_DISPATCH_FAILED"):
        dispatch(build(context), row)
    assert current(journal, row).status == "RESERVED" and not provider.refs and not provider.sends


def test_failed_precommit_does_not_send(context, monkeypatch):
    journal, _, provider, _ = context
    row = reserve(journal, "one")
    monkeypatch.setattr(
        journal, "begin_dispatch", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("storage"))
    )
    with pytest.raises(DirectDispatchError, match="PRE_DISPATCH_FAILED"):
        dispatch(build(context), row)
    assert current(journal, row).status == "RESERVED" and not provider.sends
    assert provider.refs == provider.releases


def test_commit_reply_loss_remains_unknown_without_send(context, monkeypatch):
    journal, _, provider, _ = context
    row = reserve(journal, "one")
    original = journal.begin_dispatch

    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("lost commit reply")

    monkeypatch.setattr(journal, "begin_dispatch", lost)
    dispatcher = build(context)
    result = dispatch(dispatcher, row)
    assert result.status == "UNKNOWN" and not provider.sends and provider.refs == provider.releases
    assert dispatch(dispatcher, row) == result and len(provider.refs) == 1


def test_native_unknown_keeps_marker_and_does_not_retry(context):
    journal, _, provider, _ = context
    row = reserve(journal, "one")
    provider.unknown = True
    dispatcher = build(context)
    result = dispatch(dispatcher, row)
    assert result.status == "UNKNOWN" and len(provider.sends) == 1
    assert dispatch(dispatcher, row) == result and len(provider.refs) == len(provider.sends) == 1


def test_final_persistence_failure_leaves_dispatching_and_does_not_resend(context, monkeypatch):
    journal, _, provider, _ = context
    row = reserve(journal, "one")
    monkeypatch.setattr(journal, "finish", lambda *args: (_ for _ in ()).throw(OSError("storage")))
    dispatcher = build(context)
    with pytest.raises(DirectDispatchError, match="FINISH_FAILED"):
        dispatch(dispatcher, row)
    assert current(journal, row).status == "DISPATCHING" and len(provider.sends) == 1
    assert (
        dispatch(dispatcher, row).status == "DISPATCHING"
        and len(provider.refs) == len(provider.sends) == 1
    )


def test_composition_requires_matching_provider(context):
    with pytest.raises(ValueError, match="provider does not match"):
        build(context, expected=object())


def test_provider_replacement_blocks_before_reserve(context):
    journal, _, provider, coord = context
    row = reserve(journal, "one")
    dispatcher = build(context)
    coord._providers[ROUTE] = object()
    with pytest.raises(DirectDispatchError, match="PRE_DISPATCH_FAILED"):
        dispatch(dispatcher, row)
    assert current(journal, row).status == "RESERVED" and not provider.refs and not provider.sends


@pytest.mark.parametrize("result", [None, False])
def test_gateway_admission_denial_never_reserves(context, result):
    journal, _, provider, _ = context
    row = reserve(journal, "one")
    with pytest.raises(DirectDispatchError, match="ADMISSION_DENIED"):
        dispatch(build(context, gate=lambda _: result), row)
    assert current(journal, row).status == "RESERVED" and not provider.refs and not provider.sends


def test_reserve_mutation_cannot_change_order_or_commit(context):
    journal, _, provider, _ = context
    row = reserve(journal, "one")
    provider.mutate_request = True
    with pytest.raises(DirectDispatchError, match="PRE_DISPATCH_FAILED"):
        dispatch(build(context), row)
    assert current(journal, row).status == "RESERVED" and not provider.sends
    assert provider.refs == provider.releases
    assert json.loads(current(journal, row).payload_json)["quantity"] == "1.25"


def test_sdk_module_import_does_not_import_optional_gateway(monkeypatch):
    import builtins
    import importlib.util

    import bt_api_py.gateway_direct_dispatch as adapter

    original = builtins.__import__

    def without_gateway(name, *args, **kwargs):
        if name == "bt_api_gateway" or name.startswith("bt_api_gateway."):
            raise AssertionError("Gateway imported without explicit composition")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_gateway)
    spec = importlib.util.spec_from_file_location(
        "bt_api_py._gateway_test_import", adapter.__file__
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def test_decoder_rebuilds_all_exact_order_fields():
    original = request()
    actual = decode_gateway_order_request(original.to_dict(), ACCOUNT)
    assert type(actual) is OrderRequest and actual.to_dict() == original.to_dict()


@pytest.mark.parametrize("field", list(request().to_dict()))
def test_every_field_is_required(field):
    payload = request().to_dict()
    del payload[field]
    with pytest.raises(GatewayDirectCompositionError, match="ORDER_REQUEST_INVALID"):
        decode_gateway_order_request(payload, ACCOUNT)


@pytest.mark.parametrize(
    "field",
    [
        "execution_cycle_id",
        "execution_role",
        "strategy_identity_sha256",
        "ctp_order_identity",
        "managed_intent_id",
        "runtime_order_id",
        "instrument",
    ],
)
def test_managed_or_nested_fields_are_rejected(field):
    payload = request().to_dict()
    payload[field] = "forged"
    with pytest.raises(GatewayDirectCompositionError, match="ORDER_REQUEST_INVALID"):
        decode_gateway_order_request(payload, ACCOUNT)


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", "1e3"),
        ("price", "1E2"),
        ("quantity", 1),
        ("price", 2),
        ("reduce_only", 1),
        ("symbol", 99),
        ("client_order_id", 99),
        ("time_in_force", 1),
        ("hedge_flag", 1),
        ("offset", 1),
        ("account_id", "other"),
        ("instrument", {}),
    ],
)
def test_noncanonical_or_wrong_type_is_rejected(field, value):
    payload = request().to_dict()
    payload[field] = value
    with pytest.raises(GatewayDirectCompositionError, match="ORDER_REQUEST_INVALID"):
        decode_gateway_order_request(payload, ACCOUNT)


def test_extra_field_and_nonmapping_rejected():
    payload = request().to_dict()
    payload["extra"] = "injected"
    for value in (payload, None, [], "value"):
        with pytest.raises(GatewayDirectCompositionError, match="ORDER_REQUEST_INVALID") as exc:
            decode_gateway_order_request(value, ACCOUNT)
        assert exc.value.code == "ORDER_REQUEST_INVALID"


def test_invalid_payload_rejected_before_reserve(context):
    journal, _, provider, _ = context
    payload = request().to_dict()
    payload["quantity"] = "1e3"
    row = reserve(journal, "one", payload)
    with pytest.raises(DirectDispatchError, match="PRE_DISPATCH_FAILED"):
        dispatch(build(context), row)
    assert current(journal, row).status == "RESERVED" and not provider.refs and not provider.sends


def test_sdk_gateway_request_fence_revokes_after_identity_reservation(context):
    from contextlib import contextmanager

    journal, _, provider, _ = context
    row = reserve(journal, "one")
    entries = []

    @contextmanager
    def fence(record):
        assert record.reservation_id == row.reservation_id
        entries.append(record.reservation_id)
        if len(entries) == 2:
            raise PermissionError("synthetic revocation")
        yield

    with pytest.raises(DirectDispatchError, match="PRE_DISPATCH_FAILED"):
        dispatch(build(context), row, request_fence=fence)
    assert entries == [row.reservation_id, row.reservation_id]
    assert provider.refs == provider.releases and len(provider.refs) == 1
    assert provider.sends == [] and current(journal, row).status == "RESERVED"


def test_sdk_native_submit_outside_request_fence(context, monkeypatch):
    from contextlib import contextmanager

    journal, _, provider, _ = context
    row = reserve(journal, "one")
    active, calls = [], []
    original_begin = journal.begin_dispatch
    original_submit = provider.submit_order

    @contextmanager
    def fence(record):
        assert not active and record.reservation_id == row.reservation_id
        active.append(True)
        calls.append("enter")
        try:
            yield
        finally:
            calls.append("exit")
            active.pop()

    def begin(*args, **kwargs):
        assert active == [True]
        calls.append("begin")
        return original_begin(*args, **kwargs)

    def submit(*args, **kwargs):
        assert not active
        calls.append("submit")
        return original_submit(*args, **kwargs)

    monkeypatch.setattr(journal, "begin_dispatch", begin)
    monkeypatch.setattr(provider, "submit_order", submit)
    result = dispatch(build(context), row, request_fence=fence)
    assert result.status == "SENT"
    assert calls == ["enter", "exit", "enter", "begin", "exit", "submit"]
    assert len(provider.sends) == 1
