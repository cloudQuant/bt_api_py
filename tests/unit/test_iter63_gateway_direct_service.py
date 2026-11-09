from dataclasses import replace
from decimal import Decimal

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.gateway_direct_service import (
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
    recovery_requested_operations,
)
from bt_api_py.service_registry import ServiceCallContext


def _context(
    principal="principal-a",
    strategy="strategy-a",
    account="account-a",
    role=WireRole.STRATEGY_CLIENT,
):
    identity = WireIdentity(role, principal, strategy)
    return ServiceCallContext(
        identity,
        WireScope("paper:demo", account_ref=account, strategy_ref=strategy, run_id="run-a"),
        "registration",
        principal,
    )


def _binding(tmp_path):
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "direct.sqlite", "account-a", generation="g1"
    )
    return journal, GatewayDirectServiceBinding(
        journal,
        {
            "principal-a": GatewayDirectGrant("strategy-a", frozenset({"rb"})),
            "principal-b": GatewayDirectGrant("strategy-b", frozenset({"sa"})),
        },
    )


def _order(key="key-1", symbol="rb", quantity="1", account="account-a"):
    return OrderRequest(
        symbol=symbol,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal(quantity),
        account_id=account,
        client_order_id="client-a",
        price=Decimal("10"),
        idempotency_key=key,
    ).to_dict()


def _reserve(binding, context, key="key-1", payload=None):
    return binding.handle(
        "direct.reserve",
        context=context,
        arguments={
            "request_key": key,
            "payload": _order(key) if payload is None else payload,
            "ttl_seconds": 10,
        },
    )


def _same_reservation(later, earlier):
    assert later["status"] == earlier["status"] == "OK"
    left, right = dict(later["record"]), dict(earlier["record"])
    left_ttl = left.pop("remaining_ttl_seconds")
    right_ttl = right.pop("remaining_ttl_seconds")
    assert left == right
    if right_ttl is None:
        assert left_ttl is None
    else:
        assert 0 <= left_ttl <= right_ttl


def test_idempotency_scope_and_payload_rejection(tmp_path):
    journal, binding = _binding(tmp_path)
    context = _context()
    initial = _reserve(binding, context)
    assert initial["status"] == "OK"
    _same_reservation(_reserve(binding, context), initial)
    assert _reserve(binding, context, payload=_order(quantity="2")) == {
        "status": "REQUEST_REJECTED",
        "code": "REQUEST_CONFLICT",
    }
    assert (
        _reserve(binding, context, payload=dict(_order(), principal="other"))["code"]
        == "INVALID_REQUEST"
    )
    assert _reserve(binding, context, payload=_order(symbol="sa"))["code"] == "SCOPE_DENIED"
    assert (
        _reserve(binding, context, payload=_order(account="account-b"))["code"] == "INVALID_REQUEST"
    )
    assert _reserve(binding, context, payload=_order(key="other-key"))["code"] == "INVALID_REQUEST"
    assert _reserve(binding, _context(account="account-b"))["code"] == "SCOPE_DENIED"
    assert _reserve(binding, _context(role=WireRole.SERVICE))["code"] == "SCOPE_DENIED"
    assert (
        binding.handle(
            "direct.command_status",
            context=_context("principal-b", "strategy-b"),
            arguments={"request_key": "key-1"},
        )["code"]
        == "NOT_FOUND"
    )
    _same_reservation(
        binding.handle(
            "direct.command_status", context=context, arguments={"request_key": "key-1"}
        ),
        initial,
    )
    _same_reservation(
        binding.handle(
            "direct.command_status",
            context=replace(context, scope=replace(context.scope, run_id="later-run")),
            arguments={"request_key": "key-1"},
        ),
        initial,
    )
    assert journal.get("strategy-a", "key-1", "principal-a").digest == initial["record"]["digest"]


def test_release_exact_identity_idempotency(tmp_path):
    _, binding = _binding(tmp_path)
    context = _context()
    record = _reserve(binding, context)["record"]
    arguments = {
        "request_key": record["request_key"],
        "reservation_id": record["reservation_id"],
        "digest": record["digest"],
        "operation_id": "release-1",
        "expected_version": record["version"],
    }
    bad = dict(arguments, digest="other")
    assert (
        binding.handle("direct.release", context=context, arguments=bad)["code"]
        == "REQUEST_CONFLICT"
    )
    released = binding.handle("direct.release", context=context, arguments=arguments)
    assert released["record"]["state"] == "RELEASED"
    assert released["record"]["remaining_ttl_seconds"] is None
    assert released["record"]["expiry_pending"] is False
    assert binding.handle("direct.release", context=context, arguments=arguments) == released
    assert (
        binding.handle("direct.command_status", context=context, arguments={"request_key": "key-1"})
        == released
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda order: order.pop("hedge_class"),
        lambda order: order.update(quantity=1),
        lambda order: order.update(instrument={"native_symbol": "rb"}),
        lambda order: order.update(strategy_identity_sha256="a" * 64),
        lambda order: order.update(unknown_field="extra"),
    ],
)
def test_noncanonical_order_is_rejected_before_storage(tmp_path, mutation):
    _, binding = _binding(tmp_path)
    payload = _order()
    mutation(payload)
    assert _reserve(binding, _context(), payload=payload) == {
        "status": "REQUEST_REJECTED",
        "code": "INVALID_REQUEST",
    }
    assert binding.handle(
        "direct.command_status", context=_context(), arguments={"request_key": "key-1"}
    ) == {"status": "REQUEST_REJECTED", "code": "NOT_FOUND"}


def test_commit_then_error_does_not_become_rejected(tmp_path, monkeypatch):
    journal, binding = _binding(tmp_path)
    original = journal._commit
    # The missing-row preflight rolls back; reserve performs the first commit.
    count = 0

    def fail_after_commit(db):
        nonlocal count
        original(db)
        count += 1
        if count == 1:
            raise RuntimeError("lost acknowledgement")

    monkeypatch.setattr(journal, "_commit", fail_after_commit)
    with pytest.raises(RuntimeError):
        _reserve(binding, _context())
    monkeypatch.setattr(journal, "_commit", original)
    assert (
        binding.handle(
            "direct.command_status", context=_context(), arguments={"request_key": "key-1"}
        )["record"]["state"]
        == "RESERVED"
    )


def test_corrupt_stored_payload_does_not_become_scope_denial(tmp_path, monkeypatch):
    journal, binding = _binding(tmp_path)
    _reserve(binding, _context())
    original = journal.observe

    def broken_record(*args):
        observed = original(*args)
        return replace(observed, record=replace(observed.record, payload_json='{"symbol":{}}'))

    monkeypatch.setattr(journal, "observe", broken_record)
    with pytest.raises(RuntimeError, match="INVALID_STORED_COMMAND"):
        binding.handle(
            "direct.command_status", context=_context(), arguments={"request_key": "key-1"}
        )


def test_post_commit_observation_failure_is_unknown(tmp_path, monkeypatch):
    journal, binding = _binding(tmp_path)
    original = journal.observe
    count = 0

    def failing_observe(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("synthetic observation failure")
        return original(*args)

    monkeypatch.setattr(journal, "observe", failing_observe)
    with pytest.raises(RuntimeError, match="observation failure"):
        _reserve(binding, _context())
    monkeypatch.setattr(journal, "observe", original)
    assert (
        binding.handle(
            "direct.command_status", context=_context(), arguments={"request_key": "key-1"}
        )["record"]["state"]
        == "RESERVED"
    )


def test_recovery_configuration_is_explicit_and_immutable():
    operations = recovery_requested_operations()
    assert operations == {
        "broker.direct_write": frozenset({"direct.command_status", "direct.release"})
    }
    with pytest.raises(TypeError):
        operations["broker.direct_write"] = frozenset({"direct.reserve"})
    with pytest.raises(ValueError, match="INVALID_RECOVERY_MOUNT"):
        recovery_requested_operations("upstream.direct_write")


def test_missing_strategy_baseline_denies_new_reservation_without_insert(tmp_path):
    from bt_api_gateway.session_guard import LocalAccountSessionGuard

    directory = tmp_path / "guards"
    directory.mkdir()
    guard = LocalAccountSessionGuard(directory, "account-a", "owner")
    generation = guard.acquire()
    try:
        journal = GatewayDirectCommandJournal.create(tmp_path / "ledger.sqlite", "account-a", generation=generation)
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        binding = GatewayDirectServiceBinding(journal,
            {"principal-a": GatewayDirectGrant("strategy-a", frozenset({"rb"}))})
        payload = {**_order(), "side": "sell", "offset": "close_today", "quantity_unit": "lots", "exchange_id": "SHFE"}
        assert _reserve(binding, _context(), payload=payload) == {
            "status": "REQUEST_REJECTED", "code": "STRATEGY_LEDGER_UNAVAILABLE"}
        with journal._transaction() as db:
            assert db.execute("SELECT count(*) FROM direct_journal_commands").fetchone()[0] == 0
    finally:
        guard.release()


def test_remaining_ttl_uses_first_reservation_and_never_implies_durable_expiry(tmp_path):
    now = [100.0]
    path = tmp_path / "ttl.sqlite"
    journal = GatewayDirectCommandJournal.create(
        path, "account-a", generation="g1", clock=lambda: now[0]
    )
    grant = {"principal-a": GatewayDirectGrant("strategy-a", frozenset({"rb"}))}
    binding = GatewayDirectServiceBinding(journal, grant)
    args = {"request_key": "key-1", "payload": _order(), "ttl_seconds": 10}
    first = binding.handle("direct.reserve", context=_context(), arguments=args)["record"]
    assert first["journal_id"] == journal.journal_id
    assert first["owner_generation"] == "g1" and first["generation_current"] is True
    assert first["reservation_generation"] == "g1"
    assert first["remaining_ttl_seconds"] == 10 and not first["expiry_pending"]
    now[0] = 105.0
    repeated = binding.handle(
        "direct.reserve", context=_context(), arguments=dict(args, ttl_seconds=30)
    )["record"]
    assert repeated["remaining_ttl_seconds"] == 5
    assert repeated["reservation_id"] == first["reservation_id"]
    replacement = GatewayDirectCommandJournal(
        path,
        "account-a",
        generation="g2",
        expected_journal_id=journal.journal_id,
        minimum_revision=0,
        clock=lambda: now[0],
    )
    new_binding = GatewayDirectServiceBinding(replacement, grant)
    old_generation = new_binding.handle(
        "direct.command_status", context=_context(), arguments={"request_key": "key-1"}
    )["record"]
    assert (
        old_generation["owner_generation"] == "g2" and old_generation["generation_current"] is False
    )
    assert old_generation["reservation_generation"] == "g1"
    assert old_generation["state"] == "RESERVED" and old_generation["expiry_pending"]
    assert old_generation["remaining_ttl_seconds"] == 0
    now[0] = 110.0
    expired = binding.handle(
        "direct.command_status", context=_context(), arguments={"request_key": "key-1"}
    )["record"]
    assert expired["state"] == "RESERVED" and expired["version"] == 1
    assert expired["expiry_pending"] and expired["remaining_ttl_seconds"] == 0
    assert journal.get("strategy-a", "key-1", "principal-a").status == "RESERVED"


@pytest.mark.parametrize("preexpire", [False, True])
def test_receipt_carries_durable_scope_and_expired_release_fields(tmp_path, preexpire):
    from bt_api_base.gateway_direct_protocol import GatewayCommandBinding, GatewayCommandRecord

    now = [100.0]
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "expired.sqlite", "account-a", generation="g1", clock=lambda: now[0]
    )
    binding = GatewayDirectServiceBinding(
        journal, {"principal-a": GatewayDirectGrant("strategy-a", frozenset({"rb"}))}
    )
    first = _reserve(binding, _context())["record"]
    assert (
        first["schema_version"],
        first["account_scope"],
        first["strategy_scope"],
        first["principal"],
    ) == (1, "account-a", "strategy-a", "principal-a")
    assert "run_id" not in first
    expected = GatewayCommandBinding(journal.journal_id, "account-a", "strategy-a", "principal-a")
    assert GatewayCommandRecord.from_mapping(first, expected_binding=expected).to_mapping() == first
    now[0] = 111.0
    expected_version = 1
    if preexpire:
        journal.expire("strategy-a", "key-1", "principal-a")
        expected_version = 2
    result = binding.handle(
        "direct.release",
        context=_context(),
        arguments={
            "request_key": "key-1",
            "reservation_id": first["reservation_id"],
            "digest": first["digest"],
            "operation_id": "expired-release",
            "expected_version": expected_version,
        },
    )["record"]
    assert result["state"] == "EXPIRED" and result["version"] == 2
    assert result["release_operation_id"] == "expired-release"
    assert result["release_expected_version"] == expected_version
    assert (
        GatewayCommandRecord.from_mapping(result, expected_binding=expected).to_mapping() == result
    )


def test_overlong_canonical_ascii_payload_is_finite_denial_without_journal_mutation(tmp_path):
    from bt_api_base.gateway_direct_protocol import (
        GatewayDirectProtocolError,
        gateway_payload_digest,
    )
    from bt_api_base.service_protocol import encode_wire_value

    from bt_api_py.gateway_order_contract import decode_gateway_order_request

    journal, binding = _binding(tmp_path)
    payload = _order()
    payload["client_order_id"] = "中" * 12000
    encode_wire_value(payload)  # valid wire value; ASCII-escaped canonical form is too large
    assert decode_gateway_order_request(payload, "account-a").idempotency_key == "key-1"
    with pytest.raises(GatewayDirectProtocolError, match="INVALID_PAYLOAD"):
        gateway_payload_digest(payload)
    result = _reserve(binding, _context(), payload=payload)
    assert result == {"status": "REQUEST_REJECTED", "code": "INVALID_REQUEST"}
    assert binding.handle(
        "direct.command_status", context=_context(), arguments={"request_key": "key-1"}
    ) == {"status": "REQUEST_REJECTED", "code": "NOT_FOUND"}
    import sqlite3

    with sqlite3.connect(tmp_path / "direct.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM direct_journal_commands").fetchone()[0] == 0
