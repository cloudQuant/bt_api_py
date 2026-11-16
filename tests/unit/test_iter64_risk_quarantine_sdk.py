"""Risk quarantine through SDK service/client, with local fakes only."""

import pytest

from tests.unit.test_iter64_risk_service_component import ctx, payload
from tests.unit.test_iter64_risk_service_component import service as _service_fixture
from tests.unit.test_iter64_risk_service_component import V2_POLICY_BLOCKED


@pytest.fixture
def service(tmp_path):
    yield from _service_fixture.__wrapped__(tmp_path)


def _status(service):
    return service.handle("risk.status", context=ctx("principal-a"), arguments={})


@V2_POLICY_BLOCKED
def test_quarantine_status_and_client_preserve_policy_version(service, monkeypatch):
    from tests.unit.test_iter64_risk_service_client import make_client

    summary = service._engine.status_summary()
    assert summary["available"] is False and summary["policy_version"] == 1
    reply = _status(service)
    assert reply["available"] is False and reply["policy_version"] == "1"
    assert reply["refusal_counts"] == {}
    client, wire = make_client(monkeypatch, [reply])
    # Transport registration and Risk's own policy availability are different.
    assert client.available is True
    status = client.service_status()
    assert status.outcome == "ANSWERED" and status.status.available is False
    assert status.status.policy_version == "1" and status.status.refusal_counts == {}
    assert [call[1] for call in wire.calls] == ["risk.status"]
    assert service.gateway.calls == [] and service.runs == []


@V2_POLICY_BLOCKED
def test_quarantined_service_does_not_fallback_and_delegated_reads_and_cancel_remain(
    service, monkeypatch
):
    from bt_api_py.gateway_strategy_delegated import GatewayDelegatedCommandResult
    from tests.unit.test_iter63_gateway_api import order
    from tests.unit.test_iter64_risk_service_client import delegated_observation, facade

    api, _, wire, direct_gateway = facade(monkeypatch, replies=[_status(service)])
    status = api._risk.service_status()
    assert status.outcome == "ANSWERED" and status.status.available is False
    assert api.risk_selected is True and api.risk_available is True  # transport only
    placed = api.reserve_order("CTP___FUTURE", order())
    assert placed.outcome == "UNKNOWN" and placed.risk_denied is None
    assert direct_gateway == [] and [call[1] for call in wire.calls] == ["risk.status"]

    observation = delegated_observation("SENT")
    seen = []

    def read(key, **kwargs):
        seen.append(("status", key))
        return GatewayDelegatedCommandResult("OBSERVED", observation)

    def cancel(observed, *, operation_id, timeout_seconds):
        seen.append(("cancel", observed, operation_id))
        return "cancelled"

    monkeypatch.setattr(api._delegated, "status", read)
    monkeypatch.setattr(api._delegated, "cancel", cancel)
    observed = api.command_status("CTP___FUTURE", "same-key")
    assert observed.outcome == "OBSERVED" and observed.record is observation
    assert api.cancel_order("CTP___FUTURE", "same-key", operation_id="cancel-1") == "cancelled"
    assert seen == [("status", "same-key"), ("status", "same-key"),
                    ("cancel", observation, "cancel-1")]
    assert direct_gateway == [] and [call[1] for call in wire.calls] == ["risk.status"]
    assert service.gateway.calls == [] and service.runs == []


def seed_synthetic_legacy_order(service):
    import hashlib
    import json

    from bt_api_risk.pretrade.controls import derive_effect

    context = ctx("principal-a", run="old-run")
    caller, error = service.caller(context, "risk.order.reserve")
    assert error is None
    view, canonical, refusal = service.build_view(caller, "k1", payload("k1"), None, None)
    assert refusal is None
    # Synthesize a legacy row directly in temporary SQLite: this establishes no
    # source/first-creation fact and never disables the production quarantine.
    effect, position_side, bucket = derive_effect(view.side, view.offset, view.instrument.venue)
    payload_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    now = 1_900_000_000_000_000_000
    with service._ledger.transaction() as txn:
        txn.insert_permit({
            "permit_id": "legacy-permit", "strategy_ref": view.strategy_ref,
            "request_key": view.request_key, "run_id": caller.run_id,
            "principal_ref": caller.principal_ref, "origin": view.origin,
            "view_json": json.dumps(view.to_mapping(), sort_keys=True), "view_digest": view.digest(),
            "payload_json": canonical, "payload_digest": payload_digest,
            "instrument_id": view.instrument.instrument_id,
            "instrument_json": json.dumps(view.instrument.to_dict(), sort_keys=True),
            "side": view.side, "order_offset": view.offset, "effect": effect,
            "position_side": position_side, "bucket": bucket, "quantity": str(view.quantity),
            "limit_price": str(view.limit_price), "parent_ref": None, "child_id": None,
            "trading_day": "20300101", "policy_version": 1, "state": "RESERVED",
            "created_ns": now, "expires_ns": now + 10_000_000_000, "updated_ns": now,
            "reservation_id": "legacy-reservation", "gateway_state": "RESERVED",
            "gw_version": 1, "gw_digest": payload_digest,
            "owner_generation": "g1", "reservation_generation": "g1",
        })
        return txn.permit_by_key("strategy-a", "k1")


@V2_POLICY_BLOCKED
def test_quarantine_preserves_authenticated_order_status_without_claiming_state(service):
    old = seed_synthetic_legacy_order(service)
    assert old is not None and old.state == "RESERVED"
    before, runs = list(service.gateway.calls), list(service.runs)
    status = service.handle("risk.order.status", context=ctx("principal-a", run="new-run"),
                            arguments={"request_key": "k1"})
    assert status["outcome"] == "UNKNOWN"
    assert all(status[name] is None for name in ("state", "permit_id", "reservation_id", "view_digest"))
    absent = service.handle("risk.order.status", context=ctx("principal-a"),
                           arguments={"request_key": "missing"})
    assert absent["outcome"] == "NOT_FOUND"
    assert _status(service)["available"] is False
    assert service.gateway.calls == before and service.runs == runs
    with service._ledger.transaction() as txn:
        original = txn.permit_by_key("strategy-a", "k1")
        assert original is not None and original.state == "RESERVED" and original.occupancy > 0


@V2_POLICY_BLOCKED
@pytest.mark.parametrize("identity", ["strategy", "execution", "operator_without_permission"])
def test_unknown_list_requires_explicit_operator_permission(service, monkeypatch, identity):
    from dataclasses import replace

    from bt_api_base.service_protocol import WireRole

    from tests.unit.test_iter64_risk_service_component import PRINCIPALS

    contexts = {
        "strategy": ctx("principal-a"),
        "execution": ctx("exec-1", role=WireRole.SERVICE, actor="exec-actor", run="exec-run"),
        "operator_without_permission": ctx("operator-1", role=WireRole.APPLICATION_CLIENT, strategy=None),
    }
    service._principals = {**PRINCIPALS, "operator-1": replace(
        PRINCIPALS["operator-1"], operations=frozenset({"risk.admin.status"})
    )}
    inspected = []

    def list_unknown():
        inspected.append(True)
        raise AssertionError("unauthorized caller must not reach account-wide list")

    monkeypatch.setattr(service._engine, "list_unknown", list_unknown)
    before, runs = list(service.gateway.calls), list(service.runs)
    response = service.handle("risk.unknown.list", context=contexts[identity], arguments={})
    assert response == {"status": "REQUEST_REJECTED", "code": "risk_unauthorized"}
    assert inspected == [] and "permits" not in response
    assert service.gateway.calls == before and service.runs == runs
