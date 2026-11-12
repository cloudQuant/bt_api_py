"""A Risk Registry refusal is a definite not-sent outcome, never UNKNOWN (64.09, defect D4).

Found by the INTEGRATION_SIM scenario matrix: right after a Risk restart the strategy's
client still holds its old registration; the new Risk Registry answers every call
``STALE_EPOCH`` before any handler runs, and the client reported each PLACE as UNKNOWN
for about one lease-renewal period (BT then holds such an order as UNKNOWN). Only the
Registry's exact refusal reply on an unchanged local registration is proof; anything
else stays UNKNOWN. UNIT/FAKE (a never-started runtime with status/call replaced).
"""

from __future__ import annotations

import pytest

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


def test_facade_projects_a_registry_refusal_as_a_plain_retryable_refusal(monkeypatch):
    api, host, wire, gateway = facade(monkeypatch, replies=[refusal("STALE_EPOCH")])
    refused = api.reserve_order("CTP___FUTURE", order())
    assert (refused.outcome, refused.code) == ("REQUEST_DENIED", "RISK_UNAVAILABLE")
    assert (refused.risk_code, refused.risk_denied) == ("risk_unavailable", "risk_unavailable")
    assert gateway == [] and "same-key" not in api._risk_keys  # never placed through Risk
    # The same request key can be placed again once Risk answers.
    wire.replies.append(answer("RESERVED"))
    reserved = api.reserve_order("CTP___FUTURE", order())
    assert reserved.outcome == "OBSERVED" and reserved.record.state == "RESERVED"
    # A refused dispatch keeps the permit for a later dispatch; nothing reaches the Gateway.
    wire.replies.append(refusal("NOT_REGISTERED"))
    denied = api.dispatch_order("CTP___FUTURE", "same-key")
    assert (denied.outcome, denied.risk_denied) == ("REQUEST_DENIED", "risk_unavailable")
    wire.replies.append(answer("DISPATCHED"))
    assert api.dispatch_order("CTP___FUTURE", "same-key").outcome == "QUEUED"
    assert gateway == []
