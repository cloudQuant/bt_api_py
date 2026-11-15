"""V1 Risk service writes fail closed; all evidence here is local/fake."""

import pytest

from tests.unit.test_iter64_risk_service_component import ctx, payload
from tests.unit.test_iter64_risk_service_component import service as _service_fixture


@pytest.fixture
def service(tmp_path):
    yield from _service_fixture.__wrapped__(tmp_path)


def _legacy_order(service):
    from tests.unit.test_iter64_risk_quarantine_sdk import seed_synthetic_legacy_order

    result = seed_synthetic_legacy_order(service)
    assert result is not None and result.state == "RESERVED"
    return result


def _rows(service):
    with service._ledger.transaction() as txn:
        return {
            name: [tuple(row) for row in txn._c.execute(sql).fetchall()]
            for name, sql in {
                "permits": "SELECT * FROM risk_pretrade_permits ORDER BY rowid",
                "audit": "SELECT * FROM risk_pretrade_audit ORDER BY rowid",
                "counters": "SELECT * FROM risk_pretrade_counters ORDER BY rowid",
            }.items()
        }


def _arguments(operation, old, *, malformed=False):
    if malformed:
        return {"request_key": "k1"}
    if operation == "risk.order.reserve":
        return {"request_key": "k1", "payload": payload("k1"), "parent_ref": None, "child_id": None}
    result = {"request_key": "k1", "permit_id": old.permit_id}
    if operation == "risk.order.dispatch":
        result.update(reservation_id=old.reservation_id, view_digest=old.view_digest)
    return result


@pytest.mark.parametrize("operation", ["risk.order.reserve", "risk.order.dispatch", "risk.order.release"])
@pytest.mark.parametrize("identity", ["owner", "new_run", "foreign"])
@pytest.mark.parametrize("malformed", [False, True])
def test_v1_writes_are_unknown_without_reading_or_mutating_any_state(
    service, monkeypatch, operation, identity, malformed
):
    old = _legacy_order(service)
    rows, gateway, runs = _rows(service), list(service.gateway.calls), list(service.runs)
    context = {"owner": ctx("principal-a", run="old-run"),
               "new_run": ctx("principal-a", run="new-run"),
               "foreign": ctx("principal-b", strategy="strategy-b", run="foreign-run")}[identity]

    def forbidden(*args, **kwargs):
        raise AssertionError("v1 write gate must not inspect or mutate")

    with monkeypatch.context() as blocked:
        blocked.setattr(service._ledger, "transaction", forbidden)
        for name in ("reserve", "dispatch", "release", "lookup_order", "permit_integrity_status"):
            blocked.setattr(service._engine, name, forbidden)
        blocked.setattr(service, "_note_run", forbidden)
        reply = service.handle(operation, context=context, arguments=_arguments(operation, old, malformed=malformed))
    assert (reply["outcome"], reply["code"]) == ("UNKNOWN", "risk_permit_mismatch")
    assert all(reply[name] is None for name in ("state", "permit_id", "reservation_id", "view_digest"))
    assert _rows(service) == rows and service.gateway.calls == gateway and service.runs == runs


@pytest.mark.parametrize("operation", ["risk.order.reserve", "risk.order.dispatch", "risk.order.release"])
def test_absent_and_corrupt_keys_have_same_minimal_write_answer(service, operation):
    old = _legacy_order(service)
    with service._ledger.transaction() as txn:
        txn._c.execute("UPDATE risk_pretrade_permits SET quantity=? WHERE scope_key=? AND permit_id=?",
                       ("not-decimal", txn._scope, old.permit_id))
    before, gateway, runs = _rows(service), list(service.gateway.calls), list(service.runs)
    known = service.handle(operation, context=ctx("principal-a", run="old-run"),
                           arguments=_arguments(operation, old))
    missing_args = {**_arguments(operation, old), "request_key": "missing"}
    missing = service.handle(operation, context=ctx("principal-a", run="old-run"), arguments=missing_args)
    assert {**known, "request_key": "missing"} == missing
    assert known["outcome"] == "UNKNOWN" and known["permit_id"] is None
    assert _rows(service) == before and service.gateway.calls == gateway and service.runs == runs


def test_existing_status_remains_readable_and_absent_status_remains_not_found(service):
    old = _legacy_order(service)
    assert old.state == "RESERVED" and old.reservation_id == "legacy-reservation"
    before, gateway, runs = _rows(service), list(service.gateway.calls), list(service.runs)
    status = service.handle("risk.order.status", context=ctx("principal-a", run="new-run"),
                            arguments={"request_key": "k1"})
    missing = service.handle("risk.order.status", context=ctx("principal-a"),
                             arguments={"request_key": "missing"})
    # Quarantine permits the read but does not expose unproven historical state.
    assert status["outcome"] == "UNKNOWN" and status["state"] is None and status["permit_id"] is None
    assert missing["outcome"] == "NOT_FOUND"
    assert _rows(service) == before and service.gateway.calls == gateway and service.runs == runs


@pytest.mark.parametrize("operation", ["risk.order.reserve", "risk.order.dispatch", "risk.order.release"])
def test_malformed_existing_key_stays_unknown_through_risk_client(service, monkeypatch, operation):
    from tests.unit.test_iter64_risk_service_client import make_client

    old = _legacy_order(service)
    arguments = _arguments(operation, old, malformed=True)
    response = service.handle(operation, context=ctx("principal-a", run="old-run"), arguments=arguments)
    client, _ = make_client(monkeypatch, [response])
    call = client._answer(operation, arguments, timeout_seconds=1.0)
    assert call.outcome == "ANSWERED" and call.answer.outcome == "UNKNOWN"
    assert call.answer.code == "risk_permit_mismatch" and call.answer.state is None


@pytest.mark.parametrize("operation", ["risk.order.reserve", "risk.order.dispatch", "risk.order.release"])
@pytest.mark.parametrize("malformation", ["depth", "item_count", "byte_limit"])
def test_wire_decoder_rejection_does_not_prove_old_order_terminal(
    service, monkeypatch, operation, malformation
):
    from bt_api_base.service_protocol import MAX_WIRE_MESSAGE_BYTES

    from tests.unit.test_iter64_risk_service_client import make_client

    old = _legacy_order(service)
    before, gateway, runs = _rows(service), list(service.gateway.calls), list(service.runs)
    if malformation == "depth":
        value = None
        for _ in range(18):
            value = {"child": value}
    elif malformation == "item_count":
        value = [None] * 257
    else:
        value = "x" * (MAX_WIRE_MESSAGE_BYTES + 1)
    arguments = {**_arguments(operation, old), "untrusted": value}
    response = service.handle(operation, context=ctx("principal-a", run="old-run"), arguments=arguments)
    # This is rejection of this malformed wire request, not a RiskOrderResult
    # or proof about an earlier order sharing the outer request_key.
    assert response == {"status": "REQUEST_REJECTED", "code": "risk_request_invalid"}
    client, _ = make_client(monkeypatch, [response])
    projected = client._answer(operation, arguments, timeout_seconds=1.0)
    assert (projected.outcome, projected.code, projected.answer) == ("UNKNOWN", "INVALID_REPLY", None)
    assert _rows(service) == before and service.gateway.calls == gateway and service.runs == runs


def test_delegated_gateway_cancel_remains_independent(monkeypatch):
    from bt_api_py.gateway_strategy_delegated import GatewayDelegatedCommandResult
    from tests.unit.test_iter64_risk_service_client import delegated_observation, facade

    api, _, wire, gateway = facade(monkeypatch)
    observation = delegated_observation("SENT")
    monkeypatch.setattr(api._delegated, "status", lambda key, **kw: GatewayDelegatedCommandResult(
        "OBSERVED", observation
    ))
    calls = []
    monkeypatch.setattr(api._delegated, "cancel", lambda obs, **kw: calls.append(obs) or "cancelled")
    assert api.cancel_order("CTP___FUTURE", "same-key", operation_id="cancel-1") == "cancelled"
    assert calls == [observation] and wire.calls == [] and gateway == []


def test_unregistered_v2_operation_cannot_fall_back_to_v1(service):
    reply = service.handle("risk.order.reserve_v2", context=ctx("principal-a"),
                           arguments={"request_key": "k1"})
    assert reply == {"status": "REQUEST_REJECTED", "code": "risk_request_invalid"}
    assert service.gateway.calls == [] and service.runs == []
