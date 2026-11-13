"""SDK preflight tests, using a local Risk ledger and synthetic ports only."""

import pytest

from tests.unit.test_iter64_risk_service_component import ctx, payload, reserve
from tests.unit.test_iter64_risk_service_component import service as _service_fixture


@pytest.fixture
def service(tmp_path):
    # Explicitly reuse the existing synthetic ledger fixture in this module;
    # this works whether or not its source test module was collected first.
    yield from _service_fixture.__wrapped__(tmp_path)


def _args(reply, operation):
    result = {"request_key": "k1", "permit_id": reply["permit_id"]}
    if operation == "risk.order.dispatch":
        result.update(reservation_id=reply["reservation_id"], view_digest=reply["view_digest"])
    return result


def _snapshot(service, permit_id):
    with service._ledger.transaction() as txn:
        return tuple(txn._c.execute(
            "SELECT state,row_version,quantity,view_json,view_digest FROM risk_pretrade_permits "
            "WHERE scope_key=? AND permit_id=?", (txn._scope, permit_id)
        ).fetchone())


@pytest.mark.parametrize("operation", ["risk.order.dispatch", "risk.order.release"])
@pytest.mark.parametrize(
    "column,value",
    [("view_json", "{}"), ("view_digest", "bad"), ("view_digest", "a" * 64),
     ("instrument_json", "{}"), ("quantity", "not-a-decimal")],
)
def test_corrupt_permit_is_unknown_without_side_effects(service, operation, column, value):
    reply = reserve(service, ctx("principal-a"))
    assert reply["outcome"] == "RESERVED"
    with service._ledger.transaction() as txn:
        # Column names are test constants, not user input.
        txn._c.execute(
            f"UPDATE risk_pretrade_permits SET {column}=? WHERE scope_key=? AND permit_id=?",  # noqa: S608
            (value, txn._scope, reply["permit_id"]),
        )
    before, calls, runs = _snapshot(service, reply["permit_id"]), list(service.gateway.calls), list(service.runs)
    result = service.handle(operation, context=ctx("principal-a"), arguments=_args(reply, operation))
    assert result["outcome"] == "UNKNOWN" and result["code"] == "risk_permit_mismatch"
    assert all(result[name] is None for name in ("state", "permit_id", "reservation_id", "view_digest"))
    assert _snapshot(service, reply["permit_id"]) == before
    assert service.gateway.calls == calls and service.runs == runs


@pytest.mark.parametrize("operation", ["risk.order.dispatch", "risk.order.release"])
def test_foreign_and_missing_permits_have_same_unknown_reply(service, operation):
    reply = reserve(service, ctx("principal-a"))
    original = list(service.gateway.calls), list(service.runs)
    args = _args(reply, operation)
    context = ctx("principal-b", strategy="strategy-b")
    foreign = service.handle(operation, context=context, arguments=args)
    absent = service.handle(operation, context=context, arguments={**args, "permit_id": "not-present"})
    assert foreign == absent
    assert foreign["outcome"] == "UNKNOWN" and foreign["code"] == "risk_permit_mismatch"
    assert (service.gateway.calls, service.runs) == original


def test_valid_legacy_permit_with_bad_request_digest_is_refused(service):
    reply = reserve(service, ctx("principal-a"))
    before = list(service.gateway.calls), list(service.runs)
    result = service.handle(
        "risk.order.dispatch", context=ctx("principal-a"),
        arguments={**_args(reply, "risk.order.dispatch"), "view_digest": "a" * 64},
    )
    assert result["outcome"] == "DENIED" and result["code"] == "risk_permit_mismatch"
    assert (service.gateway.calls, service.runs) == before


@pytest.mark.parametrize("value", [None, "UNKNOWN", "ready", True, object()])
def test_preflight_result_must_be_exact_ready(service, monkeypatch, value):
    reply = reserve(service, ctx("principal-a"))
    before = list(service.gateway.calls), list(service.runs)
    monkeypatch.setattr(service._engine, "permit_integrity_status", lambda *args, **kwargs: value)
    result = service.handle("risk.order.release", context=ctx("principal-a"),
                            arguments=_args(reply, "risk.order.release"))
    assert result["outcome"] == "UNKNOWN"
    assert (service.gateway.calls, service.runs) == before


def test_preflight_exception_is_unknown_without_side_effects(service, monkeypatch):
    reply = reserve(service, ctx("principal-a"))
    before = list(service.gateway.calls), list(service.runs)

    def fail(*args, **kwargs):
        raise RuntimeError("local failure")

    monkeypatch.setattr(service._engine, "permit_integrity_status", fail)
    result = service.handle("risk.order.release", context=ctx("principal-a"),
                            arguments=_args(reply, "risk.order.release"))
    assert result["outcome"] == "UNKNOWN"
    assert (service.gateway.calls, service.runs) == before


def test_corruption_after_preflight_is_rechecked_by_engine(service, monkeypatch):
    reply = reserve(service, ctx("principal-a"))
    original = service._engine.permit_integrity_status
    calls, runs = list(service.gateway.calls), list(service.runs)

    def corrupt_after_read(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result == "READY"
        with service._ledger.transaction() as txn:
            txn._c.execute(
                "UPDATE risk_pretrade_permits SET view_digest=? WHERE scope_key=? AND permit_id=?",
                ("a" * 64, txn._scope, reply["permit_id"]),
            )
        return result

    monkeypatch.setattr(service._engine, "permit_integrity_status", corrupt_after_read)
    result = service.handle("risk.order.dispatch", context=ctx("principal-a"),
                            arguments=_args(reply, "risk.order.dispatch"))
    assert result["outcome"] == "UNKNOWN" and result["code"] == "risk_permit_mismatch"
    assert result["permit_id"] is None and result["view_digest"] is None
    assert service.gateway.calls == calls
    # Preflight and delegate preparation are not an atomic engine claim.
    assert service.runs == runs + [("strategy-a", "run-1")]


@pytest.mark.parametrize("wrong_digest", [False, True])
def test_v2_remains_unknown_without_note_run(service, wrong_digest):
    reply = reserve(service, ctx("principal-a"))
    fence = service._ledger.fence
    with service._ledger.transaction() as txn:
        permit = txn.permit(reply["permit_id"])
        txn._c.execute(
            "INSERT INTO risk_pretrade_dispatch_v2 "
            "(scope_key,permit_id,account_scope,route,strategy_ref,request_key,principal_ref,"
            "run_id,payload_digest,view_digest,registration_id,service_epoch,writer_owner_id,"
            "writer_fencing_token,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (txn._scope, permit.permit_id, "account-a", "CTP___FUTURE", permit.strategy_ref,
             permit.request_key, permit.principal_ref, permit.run_id, permit.payload_digest,
             permit.view_digest, "registration", "epoch", fence.owner_id, fence.fencing_token,
             "PENDING"),
        )
    before = list(service.gateway.calls), list(service.runs)
    arguments = _args(reply, "risk.order.dispatch")
    if wrong_digest:
        arguments["view_digest"] = "a" * 64
    result = service.handle(
        "risk.order.dispatch", context=ctx("principal-a"),
        arguments=arguments,
    )
    assert result["outcome"] == "UNKNOWN" and result["code"] == "risk_permit_mismatch"
    assert result["permit_id"] is None and result["view_digest"] is None
    assert (service.gateway.calls, service.runs) == before


@pytest.mark.parametrize("operation", ["risk.order.reserve", "risk.order.status"])
@pytest.mark.parametrize(
    "column,value",
    [("view_json", "{}"), ("view_digest", "bad"), ("view_digest", "a" * 64),
     ("instrument_json", "{}"), ("quantity", "not-a-decimal")],
)
def test_corrupt_permit_lookup_is_unknown_without_side_effects(service, operation, column, value):
    reply = reserve(service, ctx("principal-a"))
    assert reply["outcome"] == "RESERVED"
    with service._ledger.transaction() as txn:
        txn._c.execute(
            f"UPDATE risk_pretrade_permits SET {column}=? WHERE scope_key=? AND permit_id=?",  # noqa: S608
            (value, txn._scope, reply["permit_id"]),
        )
    before, calls, runs = _snapshot(service, reply["permit_id"]), list(service.gateway.calls), list(service.runs)
    args = {"request_key": "k1"}
    if operation == "risk.order.reserve":
        args.update(payload=payload("k1"), parent_ref=None, child_id=None)
    result = service.handle(operation, context=ctx("principal-a"), arguments=args)
    assert result["outcome"] == "UNKNOWN" and result["code"] == "risk_permit_mismatch"
    assert all(result[name] is None for name in ("state", "permit_id", "reservation_id", "view_digest"))
    assert _snapshot(service, reply["permit_id"]) == before
    assert service.gateway.calls == calls and service.runs == runs


def test_existing_order_status_remains_visible_to_owning_strategy_across_runs(service):
    old = reserve(service, ctx("principal-a", run="old-run"))
    before = list(service.gateway.calls), list(service.runs)
    result = service.handle("risk.order.status", context=ctx("principal-a", run="new-run"),
                            arguments={"request_key": "k1"})
    assert result["outcome"] == "RESERVED" and result["permit_id"] == old["permit_id"]
    assert (service.gateway.calls, service.runs) == before


def test_existing_key_invalid_payload_is_not_final_rejection(service):
    reply = reserve(service, ctx("principal-a"))
    before = _snapshot(service, reply["permit_id"]), list(service.gateway.calls), list(service.runs)
    result = service.handle("risk.order.reserve", context=ctx("principal-a"), arguments={
        "request_key": "k1", "payload": {}, "parent_ref": None, "child_id": None,
    })
    assert result["outcome"] == "UNKNOWN"
    assert (_snapshot(service, reply["permit_id"]), service.gateway.calls, service.runs) == before


def test_existing_key_changed_payload_remains_unknown_without_rebinding(service):
    old = reserve(service, ctx("principal-a"))
    before = _snapshot(service, old["permit_id"]), list(service.gateway.calls), list(service.runs)
    result = reserve(service, ctx("principal-a"), quantity="2")
    assert result["outcome"] == "UNKNOWN" and result["code"] == "risk_request_conflict"
    assert result["permit_id"] is None and result["reservation_id"] is None
    assert (_snapshot(service, old["permit_id"]), service.gateway.calls, service.runs) == before


@pytest.mark.parametrize("operation", ["risk.order.reserve", "risk.order.status"])
def test_lookup_exception_returns_unknown_without_side_effects(service, monkeypatch, operation):
    old = reserve(service, ctx("principal-a"))
    before = _snapshot(service, old["permit_id"]), list(service.gateway.calls), list(service.runs)

    def fail(*args, **kwargs):
        raise RuntimeError("local failure")

    monkeypatch.setattr(service._engine, "lookup_order", fail)
    args = {"request_key": "k1"}
    if operation == "risk.order.reserve":
        args.update(payload=payload("k1"), parent_ref=None, child_id=None)
    result = service.handle(operation, context=ctx("principal-a"), arguments=args)
    assert result["outcome"] == "UNKNOWN" and result["code"] == "risk_permit_mismatch"
    assert (_snapshot(service, old["permit_id"]), service.gateway.calls, service.runs) == before


def test_existing_reserve_snapshot_never_retries_or_rebinds(service, monkeypatch):
    old = reserve(service, ctx("principal-a"))
    original, calls, runs = service._engine.lookup_order, list(service.gateway.calls), list(service.runs)

    def corrupt_after_read(*args, **kwargs):
        lookup = original(*args, **kwargs)
        assert lookup.kind.value == "RESULT"
        with service._ledger.transaction() as txn:
            txn._c.execute(
                "UPDATE risk_pretrade_permits SET view_digest=? WHERE scope_key=? AND permit_id=?",
                ("a" * 64, txn._scope, old["permit_id"]),
            )
        return lookup

    monkeypatch.setattr(service._engine, "lookup_order", corrupt_after_read)
    result = reserve(service, ctx("principal-a"))
    assert result["outcome"] == "UNKNOWN"
    assert service.gateway.calls == calls
    assert service.runs == runs


@pytest.mark.parametrize("other_writer", ["foreign", "old_run"])
def test_new_lookup_race_preserves_inserted_order_as_unknown(service, monkeypatch, other_writer):
    from dataclasses import replace

    original = service._engine.lookup_order
    inserted_calls = []

    def insert_after_lookup(*args, **kwargs):
        lookup = original(*args, **kwargs)
        assert lookup.kind.value == "NEW"
        caller = args[0]
        caller = (replace(caller, principal_ref="other-principal") if other_writer == "foreign"
                  else replace(caller, run_id="old-run"))
        created = service._engine.reserve(caller, kwargs["view"], kwargs["canonical_payload"], caller.run_id)
        assert created.outcome == "RESERVED"
        inserted_calls.extend(service.gateway.calls)
        return lookup

    monkeypatch.setattr(service._engine, "lookup_order", insert_after_lookup)
    result = reserve(service, ctx("principal-a"))
    assert result["outcome"] == "UNKNOWN"
    assert result["permit_id"] is None and result["reservation_id"] is None
    assert service.gateway.calls == inserted_calls and len(inserted_calls) == 1


@pytest.mark.parametrize("column,value", [
    ("view_digest", "bad"), ("view_json", "{}"), ("quantity", "not-a-decimal"),
])
def test_execution_reserve_corruption_is_unknown_without_side_effects(service, monkeypatch, column, value):
    from decimal import Decimal

    from bt_api_base.risk_service_protocol import RISK_ORDER_OPERATIONS, RiskParentView
    from bt_api_base.service_protocol import WireRole
    from bt_api_risk.pretrade import CallerIdentity
    from bt_api_risk.pretrade.engine import PretradeEngine

    from tests.unit.test_iter64_risk_service_component import ACCOUNT, DAY, RB, ROUTE

    context = ctx("exec-1", role=WireRole.SERVICE, actor="exec-actor", run="exec-run")
    caller = CallerIdentity("exec-1", "exec-actor", "execution", frozenset({"strategy-a"}),
                            "exec-run", frozenset(RISK_ORDER_OPERATIONS))
    parent = RiskParentView(
        account_scope=ACCOUNT, route=ROUTE, strategy_ref="strategy-a", parent_ref="P1",
        instrument=RB, side="BUY", offset="OPEN", position_side="NET", total_quantity=Decimal("2"),
        quantity_unit="lots", limit_price=Decimal("10"), created_trading_day=DAY,
        valid_until_trading_day=DAY,
    )
    with monkeypatch.context() as legacy:
        legacy.setattr(PretradeEngine, "_parent_contract_verified", staticmethod(lambda: True))
        assert service._engine.parent_reserve(caller, parent).outcome == "RESERVED"
        view, canonical, refusal = service.build_view(caller, "k1", payload("k1"), "P1", "child")
        assert refusal is None
        old = service._engine.reserve(caller, view, canonical, caller.run_id)
    assert old.outcome == "RESERVED"
    with service._ledger.transaction() as txn:
        txn._c.execute(
            f"UPDATE risk_pretrade_permits SET {column}=? WHERE scope_key=? AND permit_id=?",  # noqa: S608
            (value, txn._scope, old.permit_id),
        )
    before = _snapshot(service, old.permit_id), list(service.gateway.calls), list(service.runs)
    result = service.handle("risk.order.reserve", context=context, arguments={
        "request_key": "k1", "payload": payload("k1"), "parent_ref": "P1", "child_id": "child",
    })
    assert result["outcome"] == "UNKNOWN" and result["permit_id"] is None
    assert (_snapshot(service, old.permit_id), service.gateway.calls, service.runs) == before


def test_status_never_interprets_new_lookup_as_write_or_not_found(service, monkeypatch):
    from bt_api_risk.pretrade import RiskOrderLookup, RiskOrderLookupKind

    monkeypatch.setattr(service._engine, "lookup_order", lambda *a, **kw: RiskOrderLookup(
        RiskOrderLookupKind.NEW
    ))
    result = service.handle("risk.order.status", context=ctx("principal-a"),
                            arguments={"request_key": "k1"})
    assert result["outcome"] == "UNKNOWN" and result["code"] == "risk_permit_mismatch"
    assert service.gateway.calls == [] and service.runs == []


def test_execution_new_lookup_cannot_open_write(service, monkeypatch):
    from bt_api_base.service_protocol import WireRole
    from bt_api_risk.pretrade import RiskOrderLookup, RiskOrderLookupKind

    monkeypatch.setattr(service._engine, "lookup_order", lambda *a, **kw: RiskOrderLookup(
        RiskOrderLookupKind.NEW
    ))
    context = ctx("exec-1", role=WireRole.SERVICE, actor="exec-actor", run="exec-run")
    result = service.handle("risk.order.reserve", context=context, arguments={
        "request_key": "k1", "payload": payload("k1"), "parent_ref": "P1", "child_id": "child",
    })
    assert result["outcome"] == "UNKNOWN" and result["code"] == "risk_permit_mismatch"
    assert service.gateway.calls == [] and service.runs == []
