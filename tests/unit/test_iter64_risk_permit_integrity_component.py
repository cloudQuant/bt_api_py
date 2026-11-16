"""SDK preflight tests, using a local Risk ledger and synthetic ports only."""

import pytest

from tests.unit.test_iter64_risk_service_component import (
    V2_POLICY_BLOCKED,
    ctx,
    payload,
    reserve,
)
from tests.unit.test_iter64_risk_service_component import service as _service_fixture

# Every test here needs the shared fixture's published policy; that is blocked
# by RULE_R04_V2_UNAVAILABLE until the iteration-64 R-04/R-11 activation
# decision.  Strict xfail keeps the cases collected and flags an XPASS when the
# activation (or a v2 fixture) lands.
pytestmark = V2_POLICY_BLOCKED


@pytest.fixture
def service(tmp_path):
    # Explicitly reuse the existing synthetic ledger fixture in this module;
    # this works whether or not its source test module was collected first.
    yield from _service_fixture.__wrapped__(tmp_path)


def _historical_reserve(service, context, key="k1"):
    """Build a synthetic pre-quarantine row; never unguard the tested RPC."""
    from bt_api_risk.pretrade import engine as pretrade_engine

    caller, code = service.caller(context, "risk.order.reserve")
    assert code is None and caller is not None
    view, canonical, refusal = service.build_view(caller, key, payload(key), None, None)
    assert refusal is None and view is not None and canonical is not None
    with pytest.MonkeyPatch.context() as legacy:
        legacy.setattr(pretrade_engine, "_v1_source_quarantined", lambda: False)
        result = service._engine.reserve(caller, view, canonical, caller.run_id)
    assert pretrade_engine._v1_source_quarantined() is True
    assert result.outcome == "RESERVED"
    assert result.permit_id and result.reservation_id and result.view_digest
    return result.to_mapping()


def _args(reply, operation):
    assert reply["outcome"] == "RESERVED" and reply["permit_id"]
    assert reply["reservation_id"] and reply["view_digest"]
    result = {"request_key": "k1", "permit_id": reply["permit_id"]}
    if operation == "risk.order.dispatch":
        result.update(reservation_id=reply["reservation_id"], view_digest=reply["view_digest"])
    return result


def _snapshot(service, permit_id):
    assert permit_id
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
    reply = _historical_reserve(service, ctx("principal-a"))
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
    reply = _historical_reserve(service, ctx("principal-a"))
    original = list(service.gateway.calls), list(service.runs)
    args = _args(reply, operation)
    context = ctx("principal-b", strategy="strategy-b")
    foreign = service.handle(operation, context=context, arguments=args)
    absent = service.handle(operation, context=context, arguments={**args, "permit_id": "not-present"})
    assert foreign == absent
    assert foreign["outcome"] == "UNKNOWN" and foreign["code"] == "risk_permit_mismatch"
    assert (service.gateway.calls, service.runs) == original


def test_quarantine_keeps_bad_request_digest_unknown(service):
    reply = _historical_reserve(service, ctx("principal-a"))
    before = list(service.gateway.calls), list(service.runs)
    result = service.handle(
        "risk.order.dispatch", context=ctx("principal-a"),
        arguments={**_args(reply, "risk.order.dispatch"), "view_digest": "a" * 64},
    )
    assert result["outcome"] == "UNKNOWN" and result["code"] == "risk_permit_mismatch"
    assert (service.gateway.calls, service.runs) == before


@pytest.mark.parametrize("value", [None, "UNKNOWN", "ready", True, object()])
def test_quarantine_stops_before_preflight_result(service, monkeypatch, value):
    reply = _historical_reserve(service, ctx("principal-a"))
    before = list(service.gateway.calls), list(service.runs)
    calls = []

    def preflight(*args, **kwargs):
        calls.append(True)
        return value

    monkeypatch.setattr(service._engine, "permit_integrity_status", preflight)
    result = service.handle("risk.order.release", context=ctx("principal-a"),
                            arguments=_args(reply, "risk.order.release"))
    assert result["outcome"] == "UNKNOWN"
    assert calls == []
    assert (service.gateway.calls, service.runs) == before
    # Exercise the private, non-authorizing mapper independently: the public
    # write path above intentionally does not reach it while quarantined.
    caller, code = service.caller(ctx("principal-a"), "risk.order.release")
    assert code is None and caller is not None
    mapped = service._permit_integrity_preflight(
        caller, "risk.order.release", "k1", _args(reply, "risk.order.release")
    )
    assert mapped is not None and mapped.outcome == "UNKNOWN" and calls == [True]
    assert (service.gateway.calls, service.runs) == before


def test_quarantine_stops_before_preflight_exception(service, monkeypatch):
    reply = _historical_reserve(service, ctx("principal-a"))
    before = list(service.gateway.calls), list(service.runs)

    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("local failure")

    monkeypatch.setattr(service._engine, "permit_integrity_status", fail)
    result = service.handle("risk.order.release", context=ctx("principal-a"),
                            arguments=_args(reply, "risk.order.release"))
    assert result["outcome"] == "UNKNOWN"
    assert calls == []
    assert (service.gateway.calls, service.runs) == before
    caller, code = service.caller(ctx("principal-a"), "risk.order.release")
    assert code is None and caller is not None
    mapped = service._permit_integrity_preflight(
        caller, "risk.order.release", "k1", _args(reply, "risk.order.release")
    )
    assert mapped is not None and mapped.outcome == "UNKNOWN" and calls == [True]
    assert (service.gateway.calls, service.runs) == before


def test_quarantine_stops_before_preflight_corruption(service, monkeypatch):
    reply = _historical_reserve(service, ctx("principal-a"))
    original = service._engine.permit_integrity_status
    calls, runs = list(service.gateway.calls), list(service.runs)

    # The former corruption-after-preflight race remains GAP/NOT_RUN: this
    # quarantined public path never invokes the injected corruption callback.
    preflight_calls = []

    def corrupt_after_read(*args, **kwargs):
        preflight_calls.append(True)
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
    assert preflight_calls == []
    assert service.gateway.calls == calls
    assert service.runs == runs


@pytest.mark.parametrize("wrong_digest", [False, True])
def test_v2_remains_unknown_without_note_run(service, wrong_digest):
    reply = _historical_reserve(service, ctx("principal-a"))
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
    reply = _historical_reserve(service, ctx("principal-a"))
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


def test_existing_order_status_is_unknown_to_owning_strategy_across_runs(service):
    old = _historical_reserve(service, ctx("principal-a", run="old-run"))
    before = list(service.gateway.calls), list(service.runs)
    result = service.handle("risk.order.status", context=ctx("principal-a", run="new-run"),
                            arguments={"request_key": "k1"})
    assert old["permit_id"] and old["reservation_id"]
    assert result["outcome"] == "UNKNOWN" and result["permit_id"] is None
    assert (service.gateway.calls, service.runs) == before


def test_existing_key_invalid_payload_is_not_final_rejection(service):
    reply = _historical_reserve(service, ctx("principal-a"))
    before = _snapshot(service, reply["permit_id"]), list(service.gateway.calls), list(service.runs)
    result = service.handle("risk.order.reserve", context=ctx("principal-a"), arguments={
        "request_key": "k1", "payload": {}, "parent_ref": None, "child_id": None,
    })
    assert result["outcome"] == "UNKNOWN"
    assert (_snapshot(service, reply["permit_id"]), service.gateway.calls, service.runs) == before


def test_existing_key_changed_payload_remains_unknown_without_rebinding(service):
    old = _historical_reserve(service, ctx("principal-a"))
    before = _snapshot(service, old["permit_id"]), list(service.gateway.calls), list(service.runs)
    result = reserve(service, ctx("principal-a"), quantity="2")
    assert result["outcome"] == "UNKNOWN" and result["code"] == "risk_permit_mismatch"
    assert result["permit_id"] is None and result["reservation_id"] is None
    assert (_snapshot(service, old["permit_id"]), service.gateway.calls, service.runs) == before


@pytest.mark.parametrize("operation", ["risk.order.reserve", "risk.order.status"])
def test_lookup_exception_or_quarantine_returns_unknown_without_side_effects(service, monkeypatch, operation):
    old = _historical_reserve(service, ctx("principal-a"))
    before = _snapshot(service, old["permit_id"]), list(service.gateway.calls), list(service.runs)
    lookup_calls = []

    def fail(*args, **kwargs):
        lookup_calls.append(True)
        raise RuntimeError("local failure")

    monkeypatch.setattr(service._engine, "lookup_order", fail)
    args = {"request_key": "k1"}
    if operation == "risk.order.reserve":
        args.update(payload=payload("k1"), parent_ref=None, child_id=None)
    result = service.handle(operation, context=ctx("principal-a"), arguments=args)
    assert result["outcome"] == "UNKNOWN" and result["code"] == "risk_permit_mismatch"
    # Reserve is stopped before lookup; status actually observes the exception.
    assert lookup_calls == ([] if operation == "risk.order.reserve" else [True])
    assert (_snapshot(service, old["permit_id"]), service.gateway.calls, service.runs) == before


def test_quarantine_stops_before_existing_reserve_lookup(service, monkeypatch):
    old = _historical_reserve(service, ctx("principal-a"))
    original, calls, runs = service._engine.lookup_order, list(service.gateway.calls), list(service.runs)

    # The former corruption-after-lookup race remains GAP/NOT_RUN: this
    # quarantined public path does not enter the injected callback.
    lookup_calls = []

    def corrupt_after_read(*args, **kwargs):
        lookup_calls.append(True)
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
    assert lookup_calls == []
    assert service.gateway.calls == calls
    assert service.runs == runs


@pytest.mark.parametrize("other_writer", ["foreign", "old_run"])
def test_quarantine_stops_before_new_lookup_race(service, monkeypatch, other_writer):
    from dataclasses import replace

    original = service._engine.lookup_order
    # The former insert-after-lookup identity race remains GAP/NOT_RUN: the
    # quarantined public path does not perform the lookup or synthetic insert.
    inserted_calls, lookup_calls = [], []

    def insert_after_lookup(*args, **kwargs):
        lookup_calls.append(True)
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
    assert lookup_calls == [] and inserted_calls == [] and service.gateway.calls == []


@pytest.mark.parametrize("column,value", [
    ("view_digest", "bad"), ("view_json", "{}"), ("quantity", "not-a-decimal"),
])
def test_execution_reserve_corruption_is_unknown_without_side_effects(service, monkeypatch, column, value):
    from decimal import Decimal

    from bt_api_base.risk_service_protocol import RISK_ORDER_OPERATIONS, RiskParentView
    from bt_api_base.service_protocol import WireRole
    from bt_api_risk.pretrade import CallerIdentity
    from bt_api_risk.pretrade import engine as pretrade_engine
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
        legacy.setattr(pretrade_engine, "_v1_source_quarantined", lambda: False)
        legacy.setattr(PretradeEngine, "_parent_contract_verified", staticmethod(lambda: True))
        assert service._engine.parent_reserve(caller, parent).outcome == "RESERVED"
        view, canonical, refusal = service.build_view(caller, "k1", payload("k1"), "P1", "child")
        assert refusal is None
        old = service._engine.reserve(caller, view, canonical, caller.run_id)
    assert pretrade_engine._v1_source_quarantined() is True
    assert old.outcome == "RESERVED"
    assert old.permit_id and old.reservation_id and old.view_digest
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
