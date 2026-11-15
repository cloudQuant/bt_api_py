"""Registry refusal proves only this invocation was not sent (64.09, defect D4).

Found by the INTEGRATION_SIM scenario matrix: right after a Risk restart the strategy's
client still holds its old registration; the new Risk Registry answers every call
``STALE_EPOCH`` before any handler runs. The client may classify that invocation
as NOT_SENT, but the facade cannot infer that an earlier host never used the key.
UNIT/FAKE (a never-started runtime with status/call replaced).
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from bt_api_base.risk_service_protocol import RiskOrderResult

from bt_api_py.risk_service_client import _REGISTRY_REFUSAL_CODES
from bt_api_py.service_runtime import ServiceCallOutcomeUnknownError, ServiceRuntimeError
from tests.unit.test_iter63_gateway_api import order
from tests.unit.test_iter64_risk_service_client import (
    DIGEST,
    answer,
    facade,
    make_client,
    snapshot,
)

CODES = sorted(_REGISTRY_REFUSAL_CODES)


def refusal(code, *, from_service=True):
    error = ServiceRuntimeError(code)
    if from_service:
        error.service_reply_code = code  # what _rpc sets for the service's own rejection
    return error


CALLS = {
    "reserve": lambda client: client.reserve(order(), request_key="same-key"),
    "dispatch": lambda client: client.dispatch("same-key", permit_id="permit-1",
                                               reservation_id="res-1", view_digest=DIGEST),
    "release": lambda client: client.release("same-key", permit_id="permit-1"),
    "status": lambda client: client.status("same-key"),
}


def test_the_refusal_set_is_exactly_the_pre_handler_registry_refusals():
    expected = {"STALE_EPOCH", "NOT_REGISTERED", "DENIED", "CLOSED", "CAPABILITY_UNAVAILABLE",
                "POLICY_REQUIREMENT_UNMET", "SCOPE_DENIED"}
    assert expected == _REGISTRY_REFUSAL_CODES


@pytest.mark.parametrize("operation", sorted(CALLS))
@pytest.mark.parametrize("code", CODES)
def test_registry_refusal_is_not_sent_for_every_order_operation(monkeypatch, operation, code):
    client, wire = make_client(monkeypatch, [refusal(code)])
    result = CALLS[operation](client)
    assert (result.outcome, result.code, result.answer) == ("NOT_SENT", code, None)
    assert len(wire.calls) == 1  # never resent


@pytest.mark.parametrize(
    "error",
    [
        refusal("STALE_EPOCH", from_service=False),  # a local error naming the same code
        refusal("REPLY_TOO_LARGE"),  # the Registry ran the handler before refusing the reply
        refusal("DUPLICATE_ACTIVE_RUN"),  # not in the agreed no-effect set
        ServiceCallOutcomeUnknownError("message-1", "risk.order.reserve"),  # HANDLER_FAILED etc.
        ServiceRuntimeError("LEASE_EXPIRED"),
    ],
)
def test_anything_else_from_the_runtime_stays_unknown(monkeypatch, error):
    client, _ = make_client(monkeypatch, [error])
    result = client.reserve(order(), request_key="same-key")
    assert (result.outcome, result.code) == ("UNKNOWN", "RPC_OUTCOME_UNKNOWN")


def test_refusal_across_a_re_registration_stays_unknown(monkeypatch):
    client, wire = make_client(monkeypatch)

    def refused_after_re_registration():
        wire.state["snapshot"] = snapshot(wire.route, registration="reg-2")
        raise refusal("STALE_EPOCH")

    wire.replies.append(refused_after_re_registration)
    result = client.reserve(order(), request_key="same-key")
    assert (result.outcome, result.code) == ("UNKNOWN", "RPC_OUTCOME_UNKNOWN")


def test_v1_gate_keeps_keys_unknown_without_consuming_registry_reply(monkeypatch):
    """The facade gate runs before the fake Registry reply can be observed."""
    api, host, wire, gateway = facade(monkeypatch, replies=[refusal("STALE_EPOCH")])
    refused = api.reserve_order("CTP___FUTURE", order())
    assert (refused.outcome, refused.code) == ("UNKNOWN", "RISK_OUTCOME_UNKNOWN")
    assert refused.risk_denied is None and gateway == [] and "same-key" in api._risk_keys
    assert "same-key" in api._risk_uncertain_keys and host._risk_tickets == {}
    prior_intent = host.load_intent("same-key")
    assert prior_intent is not None
    assert api.reserve_order("CTP___FUTURE", order()).outcome == "UNKNOWN"
    assert host.load_intent("same-key") is prior_intent
    assert wire.calls == [] and len(wire.replies) == 1
    # A new key is also gated; a queued fake reply is not a creation proof.
    wire.replies.append(answer("RESERVED", key="new-key"))
    reserved = api.reserve_order("CTP___FUTURE", replace(order(), idempotency_key="new-key"))
    assert reserved.outcome == "UNKNOWN" and reserved.record is None
    assert api.dispatch_order("CTP___FUTURE", "new-key").outcome == "UNKNOWN"
    assert api.release_order("CTP___FUTURE", "new-key", operation_id="r1").outcome == "UNKNOWN"
    assert "new-key" in api._risk_uncertain_keys and host._risk_tickets == {}
    assert wire.calls == [] and len(wire.replies) == 2 and gateway == []


def test_synthetic_permit_dispatch_registry_refusal_preserves_local_permit(monkeypatch):
    """A fake pre-existing permit exercises projection, not V1 creation or authority."""
    api, _, wire, gateway = facade(monkeypatch, replies=[
        refusal("NOT_REGISTERED"), answer("DISPATCHED", key="new-key")
    ])
    permit = RiskOrderResult.from_mapping(answer("RESERVED", key="new-key"))
    api._risk_permits["new-key"] = permit
    api._risk_keys.add("new-key")
    denied = api.dispatch_order("CTP___FUTURE", "new-key")
    assert (denied.outcome, denied.risk_denied) == ("REQUEST_DENIED", "risk_unavailable")
    assert api._risk_permits["new-key"] is permit
    assert api.dispatch_order("CTP___FUTURE", "new-key").outcome == "QUEUED"
    assert [call[1] for call in wire.calls] == ["risk.order.dispatch", "risk.order.dispatch"]
    assert gateway == []
