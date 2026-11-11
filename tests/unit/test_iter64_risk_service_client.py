"""Iteration 64: strategy Risk client (wire shapes, transport mapping) and PLACE routing.

UNIT/FAKE only: a real ThreadedServiceClientRuntime that is never started, with
its status/call replaced; no Risk host, Gateway or socket exists.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest
from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
from bt_api_base.risk_service_protocol import RiskOrderResult
from bt_api_base.service_protocol import WireIdentity, WireRole

from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_direct_client import GatewayClientResult
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.gateway_strategy_delegated import (
    GatewayDelegatedCommandObservation,
    GatewayDelegatedCommandResult,
)
from bt_api_py.risk_service_client import (
    RiskCallResult,
    RiskClientRoute,
    RiskServiceClient,
)
from bt_api_py.service_runtime import ServiceRuntimeRequestNotSentError
from bt_api_py.service_runtime_thread import (
    BindingSnapshot,
    RuntimeSnapshot,
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
)
from tests.unit.test_iter63_gateway_api import order
from tests.unit.test_iter63_gateway_strategy_client import parts

DIGEST = "d" * 64
OPS = frozenset({"risk.order.reserve", "risk.order.dispatch", "risk.order.release",
                 "risk.order.status", "risk.status"})


def risk_route():
    gateway = parts()["route"]
    return RiskClientRoute("broker.risk_order", "risk", "risk-a", "risk-scope",
                           gateway.scope, gateway.identity)


def snapshot(route, *, state="running", healthy=True, registration="reg-1", ops=OPS):
    binding = BindingSnapshot(registration, "epoch-1", True, route.mount, route.service_id,
                              route.instance_id, route.scope_ref, route.scope, frozenset(ops), True)
    return RuntimeSnapshot(state, state == "running", healthy, None, (binding,), 0, 0,
                           route.identity, route.scope.run_id)


class Wire:
    """Replaces runtime.status/call; records every call and never connects."""

    def __init__(self, client, monkeypatch, replies=()):
        self.calls, self.replies, self.route = [], list(replies), client.route
        self.state = {"snapshot": snapshot(client.route)}
        runtime = client._runtime
        monkeypatch.setattr(runtime, "status", lambda: self.state["snapshot"])
        monkeypatch.setattr(runtime, "call", self.call)

    def call(self, mount, operation, arguments, **kwargs):
        self.calls.append((mount, operation, json.loads(json.dumps(arguments)), kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if callable(reply):
            return reply()
        return reply


def make_client(monkeypatch, replies=()):
    client = RiskServiceClient(ThreadedServiceClientRuntime(lambda: None), route=risk_route())
    return client, Wire(client, monkeypatch, replies)


def answer(outcome, key="same-key", **fields):
    defaults = {"RESERVED": {"state": "RESERVED", "permit_id": "permit-1",
                             "reservation_id": "res-1", "view_digest": DIGEST},
                "DISPATCHED": {"state": "DISPATCHED", "permit_id": "permit-1",
                               "reservation_id": "res-1", "view_digest": DIGEST}}.get(outcome, {})
    return RiskOrderResult(key, outcome, **{**defaults, **fields}).to_mapping()


# --- client: wire shapes ---------------------------------------------------------------


def test_reserve_sends_the_canonical_direct_payload_under_the_strategy_request_key(monkeypatch):
    client, wire = make_client(monkeypatch, [answer("RESERVED")])
    result = client.reserve(order(), request_key="same-key", timeout_seconds=2.0)
    assert result.outcome == "ANSWERED" and result.answer.outcome == "RESERVED"
    [(mount, operation, arguments, kwargs)] = wire.calls
    assert (mount, operation) == ("broker.risk_order", "risk.order.reserve")
    assert arguments == {"request_key": "same-key",
                         "payload": json.loads(canonical_gateway_payload(order().to_dict())),
                         "parent_ref": None, "child_id": None}
    assert kwargs == {"account_ref": "account", "scope_ref": "risk-scope", "timeout_seconds": 2.0}


def test_dispatch_release_status_and_service_status_wire_shapes(monkeypatch):
    status_reply = {"schema": "bt_api.risk_service.v1", "schema_version": 1, "available": True,
                    "policy_version": "policy-7", "refusal_counts": {"risk_duplicate_order": 2}}
    client, wire = make_client(monkeypatch, [answer("DISPATCHED"), answer("RELEASED"),
                                             answer("NOT_FOUND"), status_reply])
    assert client.dispatch("same-key", permit_id="permit-1", reservation_id="res-1",
                           view_digest=DIGEST).answer.outcome == "DISPATCHED"
    assert client.release("same-key", permit_id="permit-1").answer.outcome == "RELEASED"
    assert client.status("same-key").answer.outcome == "NOT_FOUND"
    status = client.service_status()
    assert status.outcome == "ANSWERED" and status.status.available is True
    assert status.status.refusal_counts == {"risk_duplicate_order": 2}
    assert [(c[1], c[2]) for c in wire.calls] == [
        ("risk.order.dispatch", {"request_key": "same-key", "permit_id": "permit-1",
                                 "reservation_id": "res-1", "view_digest": DIGEST}),
        ("risk.order.release", {"request_key": "same-key", "permit_id": "permit-1"}),
        ("risk.order.status", {"request_key": "same-key"}),
        ("risk.status", {}),
    ]


@pytest.mark.parametrize("call", [
    lambda c: c.reserve(order(), request_key="other-key"),            # not the Gateway key
    lambda c: c.reserve(order(), request_key="same-key", parent_ref="p"),  # child_id missing
    lambda c: c.dispatch("same-key", permit_id="p", reservation_id="r", view_digest="short"),
    lambda c: c.release("same-key", permit_id=" p"),
    lambda c: c.status("", timeout_seconds=1.0),
])
def test_invalid_local_input_is_not_sent(monkeypatch, call):
    client, wire = make_client(monkeypatch)
    result = call(client)
    assert (result.outcome, result.code) == ("NOT_SENT", "INVALID_REQUEST")
    assert wire.calls == []


# --- client: transport mapping (mirrors gateway_direct_client) ----------------------------


@pytest.mark.parametrize("state,healthy,ops", [
    ("new", False, OPS), ("failed", False, OPS), ("running", False, OPS),
    ("running", True, OPS - {"risk.order.reserve"}),
])
def test_route_not_registered_is_not_sent_and_never_calls(monkeypatch, state, healthy, ops):
    client, wire = make_client(monkeypatch)
    wire.state["snapshot"] = snapshot(client.route, state=state, healthy=healthy, ops=ops)
    result = client.reserve(order(), request_key="same-key")
    assert (result.outcome, result.code) == ("NOT_SENT", "ROUTE_UNAVAILABLE")
    assert wire.calls == [] and client.available is False


@pytest.mark.parametrize("error,outcome,code", [
    (ServiceRuntimeRequestNotSentError(), "NOT_SENT", "REQUEST_NOT_SENT"),
    (ThreadedRuntimeError("NOT_READY"), "NOT_SENT", "NOT_READY"),
    (ThreadedRuntimeError("QUEUE_FULL"), "NOT_SENT", "QUEUE_FULL"),
    (ThreadedRuntimeError("TIMEOUT_BEFORE_START"), "NOT_SENT", "TIMEOUT_BEFORE_START"),
    (ThreadedRuntimeError("TIMEOUT_AFTER_START"), "UNKNOWN", "RPC_OUTCOME_UNKNOWN"),
    (ThreadedRuntimeError("WORKER_FAILED"), "UNKNOWN", "RPC_OUTCOME_UNKNOWN"),
    (ConnectionResetError("reset"), "UNKNOWN", "RPC_OUTCOME_UNKNOWN"),
])
def test_only_provable_not_started_errors_are_not_sent(monkeypatch, error, outcome, code):
    client, wire = make_client(monkeypatch, [error])
    result = client.reserve(order(), request_key="same-key")
    assert (result.outcome, result.code) == (outcome, code)
    assert len(wire.calls) == 1  # never resent


def _malformed(name):
    good = answer("RESERVED")
    if name == "extra_key":
        return {**good, "message": "x"}
    if name == "wrong_schema":
        return {**good, "schema": "other"}
    if name == "other_key":
        return answer("RESERVED", key="other-key")
    if name == "reserved_without_permit":
        return {**good, "permit_id": None}
    if name == "reserved_without_digest":
        return {**good, "view_digest": None}
    if name == "reserved_with_code":
        return {**good, "code": "risk_unavailable"}
    if name == "not_found_on_reserve":
        return answer("NOT_FOUND")
    if name == "denied_without_code":
        return {**answer("UNKNOWN"), "outcome": "DENIED"}
    if name == "unknown_code":
        return {**answer("DENIED", code="risk_unavailable"), "code": "risk_something_new"}
    if name == "gateway_refused_without_gateway_code":
        return answer("DENIED", code="risk_gateway_refused")
    if name == "gateway_refused_lowercase_code":
        return answer("DENIED", code="risk_gateway_refused", gateway_code="control_stop_new")
    if name == "not_a_mapping":
        return ["RESERVED"]
    if name == "float_version":
        return {**good, "schema_version": 1.0}
    raise AssertionError(name)


@pytest.mark.parametrize("name", [
    "extra_key", "wrong_schema", "other_key", "reserved_without_permit", "reserved_without_digest",
    "reserved_with_code", "not_found_on_reserve", "denied_without_code", "unknown_code",
    "gateway_refused_without_gateway_code", "gateway_refused_lowercase_code", "not_a_mapping",
    "float_version",
])
def test_malformed_reply_is_unknown_never_a_refusal(monkeypatch, name):
    client, wire = make_client(monkeypatch, [_malformed(name)])
    result = client.reserve(order(), request_key="same-key")
    assert (result.outcome, result.code, result.answer) == ("UNKNOWN", "INVALID_REPLY", None)
    assert len(wire.calls) == 1


@pytest.mark.parametrize("field,value", [("permit_id", "permit-2"), ("reservation_id", "res-2"),
                                         ("view_digest", "e" * 64)])
def test_dispatch_answer_for_another_permit_is_unknown(monkeypatch, field, value):
    client, _ = make_client(monkeypatch, [answer("DISPATCHED", **{field: value})])
    result = client.dispatch("same-key", permit_id="permit-1", reservation_id="res-1",
                             view_digest=DIGEST)
    assert (result.outcome, result.code) == ("UNKNOWN", "INVALID_REPLY")


def test_reply_across_a_re_registration_is_unknown(monkeypatch):
    client, wire = make_client(monkeypatch)

    def reply_after_reregistration():
        wire.state["snapshot"] = snapshot(client.route, registration="reg-2")
        return answer("RESERVED")

    wire.replies.append(reply_after_reregistration)
    result = client.reserve(order(), request_key="same-key")
    assert (result.outcome, result.code) == ("UNKNOWN", "INVALID_REPLY")


@pytest.mark.parametrize("reply", [
    {"schema": "bt_api.risk_service.v1", "schema_version": 1, "available": 1,
     "policy_version": None, "refusal_counts": {}},
    {"schema": "bt_api.risk_service.v1", "schema_version": 1, "available": True,
     "policy_version": None, "refusal_counts": {"made_up": 1}},
    {"schema": "bt_api.risk_service.v1", "schema_version": 1, "available": True,
     "policy_version": None, "refusal_counts": {}, "extra": 1},
])
def test_malformed_service_status_is_unknown(monkeypatch, reply):
    client, _ = make_client(monkeypatch, [reply])
    assert client.service_status().outcome == "UNKNOWN"


# --- PLACE routing in the strategy facade -------------------------------------------------


class Host:
    """Durable host fake: intents and own records keyed by request key."""

    def __init__(self):
        self.intents, self.records = {}, {}

    def save_intent(self, intent):
        self.intents.setdefault(intent.request_key, intent)

    def load_intent(self, key):
        return self.intents.get(key)

    def save_record(self, key, record):
        self.records[key] = record

    def load_record(self, key):
        return self.records.get(key)

    def commit_pre_dispatch(self, *args):
        raise AssertionError("no direct dispatch in these tests")

    def save_release(self, *args):
        pass

    def load_release(self, *args):
        return None

    def save_cancel_plan(self, plan):
        return plan

    def load_cancel_plan(self, operation_id):
        return None

    def save_cancel_record(self, operation_id, record):
        return record

    def load_cancel_record(self, operation_id):
        return None


class DelegatedCancelHost:
    def save_delegated_cancel_plan(self, plan):
        return plan

    def load_delegated_cancel_plan(self, operation_id):
        return None

    def save_delegated_cancel_observation(self, operation_id, observation):
        return observation

    def load_delegated_cancel_observation(self, operation_id):
        return None


def facade(monkeypatch, *, risk=True, replies=()):
    data = parts()
    host = Host()
    data.update(host=host, cancel_host=host, delegated_cancel_host=DelegatedCancelHost(),
                requested_operations=frozenset({
                    "direct.command_status", "direct.release", "direct.cancel",
                    "direct.cancel_status", "direct.delegated_command_status",
                    "direct.delegated_cancel", "direct.delegated_cancel_status"}))
    session = GatewayStrategyClientSession(**data)
    client = wire = None
    if risk:
        client, wire = make_client(monkeypatch, replies)
    api = GatewayBtApi(session, exchange_name="CTP___FUTURE", owns_session=True, risk_client=client)
    gateway_calls = []
    for name in ("reserve", "dispatch", "status", "release", "cancel", "cancel_status"):
        def spy(*args, _name=name, **kwargs):
            gateway_calls.append((_name, args, kwargs))
            return GatewayClientResult("REQUEST_DENIED", code="NOT_FOUND")
        monkeypatch.setattr(api._command, name, spy)
    return api, host, wire, gateway_calls


def delegated_observation(state="SENT"):
    versions = {"RESERVED": 1, "DISPATCHING": 2, "SENT": 3, "UNKNOWN": 3}
    return GatewayDelegatedCommandObservation("account", "strategy", "risk-principal", "same-key",
                                              "res-1", "f" * 64, state, versions[state], "g1", "g1")


def test_selected_risk_reserve_never_calls_the_gateway(monkeypatch):
    api, host, wire, gateway = facade(monkeypatch, replies=[answer("RESERVED")])
    result = api.reserve_order("CTP___FUTURE", order(), timeout_seconds=2.0)
    assert result.outcome == "OBSERVED" and result.record.state == "RESERVED"
    assert (result.record.permit_id, result.record.reservation_id) == ("permit-1", "res-1")
    assert gateway == [] and [c[1] for c in wire.calls] == ["risk.order.reserve"]
    assert host.load_intent("same-key").request_key == "same-key"  # durable before sending


@pytest.mark.parametrize("make_down", ["not_registered", "not_sent", "not_ready"])
def test_unavailable_risk_refuses_place_and_sends_nothing_to_the_gateway(monkeypatch, make_down):
    api, _, wire, gateway = facade(monkeypatch)
    if make_down == "not_registered":
        wire.state["snapshot"] = snapshot(wire.route, state="failed", healthy=False)
    elif make_down == "not_sent":
        wire.replies.append(ServiceRuntimeRequestNotSentError())
    else:
        wire.replies.append(ThreadedRuntimeError("NOT_READY"))
    result = api.reserve_order("CTP___FUTURE", order())
    assert (result.outcome, result.code) == ("REQUEST_DENIED", "RISK_UNAVAILABLE")
    assert (result.risk_code, result.risk_denied) == ("risk_unavailable", "risk_unavailable")
    assert gateway == []
    assert api.dispatch_order("CTP___FUTURE", "same-key").code == "RISK_PERMIT_UNAVAILABLE"
    assert gateway == []


def test_risk_refusal_projects_upper_case_code_and_lowercase_risk_code(monkeypatch):
    api, _, _, gateway = facade(monkeypatch, replies=[
        answer("DENIED", code="risk_order_quantity_exceeds_cap", state="DENIED")])
    result = api.reserve_order("CTP___FUTURE", order())
    assert (result.outcome, result.code) == ("REQUEST_DENIED", "RISK_ORDER_QUANTITY_EXCEEDS_CAP")
    assert result.risk_denied == "risk_order_quantity_exceeds_cap" and gateway == []


def test_gateway_refusal_through_risk_keeps_the_gateway_code(monkeypatch):
    api, _, _, _ = facade(monkeypatch, replies=[
        answer("DENIED", code="risk_gateway_refused", gateway_code="CONTROL_STOP_NEW")])
    result = api.reserve_order("CTP___FUTURE", order())
    assert (result.outcome, result.code) == ("REQUEST_DENIED", "CONTROL_STOP_NEW")
    assert (result.risk_code, result.risk_denied) == ("risk_gateway_refused", None)


@pytest.mark.parametrize("reply", [ThreadedRuntimeError("TIMEOUT_AFTER_START"),
                                   {"not": "a result"}, answer("UNKNOWN")])
def test_unknown_risk_outcome_stays_unknown(monkeypatch, reply):
    api, _, _, gateway = facade(monkeypatch, replies=[reply])
    result = api.reserve_order("CTP___FUTURE", order())
    assert result.outcome == "UNKNOWN" and result.risk_denied is None and gateway == []
    assert "same-key" in api._risk_keys  # later status/cancel use the delegated path


def test_dispatch_uses_the_kept_permit_and_never_the_gateway(monkeypatch):
    api, _, wire, gateway = facade(monkeypatch, replies=[answer("RESERVED"), answer("DISPATCHED")])
    api.reserve_order("CTP___FUTURE", order())
    ref = {"key": {"source_ref": "s", "entitlement_ref": "e",
                   "instrument": {"provider": "ctp", "venue": "SHFE", "environment": "paper",
                                  "product_type": "future", "instrument_id": "rb",
                                  "native_symbol": "rb"},
                   "channel": "tick", "depth": None, "timeframe": None},
           "epoch": "e1", "sequence": 3, "message_id": "m3"}
    result = api.dispatch_order("CTP___FUTURE", "same-key", market_ref=ref)
    assert result.outcome == "QUEUED" and result.record.state == "DISPATCHED"
    assert gateway == []
    _, operation, arguments, _ = wire.calls[1]
    assert operation == "risk.order.dispatch"
    assert {k: arguments[k] for k in ("permit_id", "reservation_id", "view_digest")} == {
        "permit_id": "permit-1", "reservation_id": "res-1", "view_digest": DIGEST}


def test_release_of_an_unsent_risk_reservation_goes_to_risk(monkeypatch):
    api, _, wire, gateway = facade(monkeypatch, replies=[answer("RESERVED"), answer("RELEASED")])
    api.reserve_order("CTP___FUTURE", order())
    result = api.release_order("CTP___FUTURE", "same-key", operation_id="release-1")
    assert result.outcome == "OBSERVED" and result.record.state == "RELEASED"
    assert gateway == [] and wire.calls[1][1:3] == (
        "risk.order.release", {"request_key": "same-key", "permit_id": "permit-1"})


def test_status_and_cancel_of_a_risk_placed_order_use_delegated_gateway_even_with_risk_down(
        monkeypatch):
    api, _, wire, gateway = facade(monkeypatch, replies=[answer("RESERVED")])
    api.reserve_order("CTP___FUTURE", order())
    wire.state["snapshot"] = snapshot(wire.route, state="failed", healthy=False)  # Risk down
    observation = delegated_observation("SENT")
    delegated = []

    def status(key, *, timeout_seconds):
        delegated.append(("status", key))
        api._delegated._observations[key] = observation
        return GatewayDelegatedCommandResult("OBSERVED", observation)

    def cancel(obs, *, operation_id, timeout_seconds):
        delegated.append(("cancel", obs, operation_id))
        return "delegated-cancel-result"

    monkeypatch.setattr(api._delegated, "status", status)
    monkeypatch.setattr(api._delegated, "cancel", cancel)
    seen = api.command_status("CTP___FUTURE", "same-key")
    assert seen.outcome == "OBSERVED" and seen.record is observation
    assert api.cancel_order("CTP___FUTURE", "same-key", operation_id="cancel-1") == (
        "delegated-cancel-result")
    assert delegated == [("status", "same-key"), ("status", "same-key"),
                         ("cancel", observation, "cancel-1")]
    assert gateway == []            # never direct.cancel / direct.command_status
    assert len(wire.calls) == 1     # cancel and status never touched Risk


def test_unavailable_delegated_observation_never_cancels(monkeypatch):
    api, _, _, gateway = facade(monkeypatch, replies=[answer("RESERVED")])
    api.reserve_order("CTP___FUTURE", order())
    monkeypatch.setattr(api._delegated, "status", lambda key, **kw: GatewayDelegatedCommandResult(
        "UNAVAILABLE", code="DELEGATED_COMMAND_UNAVAILABLE"))
    monkeypatch.setattr(api._delegated, "cancel", lambda *a, **kw: pytest.fail("cancelled blind"))
    result = api.cancel_order("CTP___FUTURE", "same-key", operation_id="cancel-1")
    assert result.outcome == "TRANSPORT_NOT_STARTED" and gateway == []


def test_unknown_provenance_never_passes_not_found_on(monkeypatch):
    # After a restart the facade does not know who placed the key; a missing
    # delegated observation plus the Gateway's NOT_FOUND for an own command is
    # not proof that nothing was placed through Risk.
    api, _, _, gateway = facade(monkeypatch)
    monkeypatch.setattr(api._delegated, "status", lambda key, **kw: GatewayDelegatedCommandResult(
        "UNAVAILABLE", code="DELEGATED_COMMAND_UNAVAILABLE"))
    result = api.command_status("CTP___FUTURE", "restored-key")
    assert (result.outcome, result.code) == ("UNKNOWN", "COMMAND_UNAVAILABLE")
    assert [call[0] for call in gateway] == ["status"]


def test_own_order_keeps_the_direct_gateway_operations_in_risk_mode(monkeypatch):
    api, host, wire, gateway = facade(monkeypatch)
    host.records["own-key"] = object()  # the strategy itself reserved this key earlier
    api.command_status("CTP___FUTURE", "own-key")
    api.cancel_order("CTP___FUTURE", "own-key", operation_id="cancel-own")
    api.release_order("CTP___FUTURE", "own-key", operation_id="release-own")
    assert [call[0] for call in gateway] == ["status", "cancel", "release"]
    assert wire.calls == []


def test_none_path_is_unchanged(monkeypatch):
    api, _, _, gateway = facade(monkeypatch, risk=False)
    request = order()
    results = [
        api.reserve_order("CTP___FUTURE", request, ttl_seconds=3.0, timeout_seconds=2.0),
        api.dispatch_order("CTP___FUTURE", "k", timeout_seconds=2.0),
        api.dispatch_order("CTP___FUTURE", "k", market_ref={"m": 1}, timeout_seconds=2.0),
        api.command_status("CTP___FUTURE", "k", timeout_seconds=2.0),
        api.release_order("CTP___FUTURE", "k", operation_id="o", timeout_seconds=2.0),
        api.cancel_order("CTP___FUTURE", "k", operation_id="o", timeout_seconds=2.0),
        api.cancel_status("CTP___FUTURE", "o", timeout_seconds=2.0),
    ]
    assert gateway == [
        ("reserve", (request,), {"ttl_seconds": 3.0, "timeout_seconds": 2.0}),
        ("dispatch", ("k",), {"timeout_seconds": 2.0}),
        ("dispatch", ("k",), {"market_ref": {"m": 1}, "timeout_seconds": 2.0}),
        ("status", ("k",), {"timeout_seconds": 2.0}),
        ("release", ("k",), {"operation_id": "o", "timeout_seconds": 2.0}),
        ("cancel", ("k",), {"operation_id": "o", "timeout_seconds": 2.0}),
        ("cancel_status", ("o",), {"timeout_seconds": 2.0}),
    ]
    assert all(type(r) is GatewayClientResult for r in results)
    assert api.risk_selected is False and api.risk_available is None


def test_risk_start_failure_does_not_stop_the_gateway_session(monkeypatch):
    api, _, wire, _ = facade(monkeypatch)
    monkeypatch.setattr(api._session, "start", lambda **kw: "gateway-snapshot")

    def refused(**kw):
        raise ThreadedRuntimeError("START_FAILED")

    monkeypatch.setattr(api._risk, "start", refused)
    assert api.start(timeout_seconds=1.0) == "gateway-snapshot"
    assert api._risk_start_code == "START_FAILED"
    wire.state["snapshot"] = snapshot(wire.route, state="failed", healthy=False)
    assert api.risk_available is False
    assert api.reserve_order("CTP___FUTURE", order()).code == "RISK_UNAVAILABLE"


def test_facade_refuses_a_risk_client_for_another_run(monkeypatch):
    data = parts()
    session = GatewayStrategyClientSession(**data)
    route = risk_route()
    other = RiskClientRoute(route.mount, route.service_id, route.instance_id, route.scope_ref,
                            replace(route.scope, run_id="other-run"), route.identity)
    client = RiskServiceClient(ThreadedServiceClientRuntime(lambda: None), route=other)
    with pytest.raises(ValueError, match="RISK_ROUTE_MISMATCH"):
        GatewayBtApi(session, exchange_name="CTP___FUTURE", risk_client=client)
    session.close()


def test_route_requires_strategy_identity_of_the_scope():
    route = risk_route()
    with pytest.raises(ValueError, match="INVALID_RISK_ROUTE"):
        RiskClientRoute(route.mount, route.service_id, route.instance_id, route.scope_ref,
                        route.scope, WireIdentity(WireRole.STRATEGY_CLIENT, "principal", "other"))
    with pytest.raises(ValueError, match="INVALID_RISK_ROUTE"):
        RiskClientRoute("broker.direct_write", route.service_id, route.instance_id,
                        route.scope_ref, route.scope, route.identity)
    assert type(RiskCallResult("NOT_SENT", code="X")) is RiskCallResult


# --- reconnect of a lost Risk runtime (bounded backoff, nothing resent) -----------------


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def lost_runtime_client(monkeypatch, *, start_ok=True):
    """A client whose first runtime failed; the factory hands out fresh fakes."""
    route = risk_route()
    made, calls = [], []

    def fake(state):
        runtime = ThreadedServiceClientRuntime(lambda: None)
        box = {"snapshot": snapshot(route, state=state, healthy=state == "running")}
        monkeypatch.setattr(runtime, "status", lambda: box["snapshot"])

        def start(**kw):
            if not start_ok:
                box["snapshot"] = snapshot(route, state="failed", healthy=False)
                raise ThreadedRuntimeError("START_FAILED")
            box["snapshot"] = snapshot(route, registration="reg-" + str(len(made)))
            return box["snapshot"]

        def close(**kw):
            box["snapshot"] = snapshot(route, state="closed", healthy=False)
            return box["snapshot"]

        def call(mount, operation, arguments, **kwargs):
            calls.append((operation, arguments.get("request_key")))
            return answer("RESERVED", key=arguments["request_key"])

        monkeypatch.setattr(runtime, "start", start)
        monkeypatch.setattr(runtime, "close", close)
        monkeypatch.setattr(runtime, "call", call)
        return runtime

    def factory():
        runtime = fake("new")
        made.append(runtime)
        return runtime

    clock = Clock()
    client = RiskServiceClient(fake("failed"), route=route, runtime_factory=factory,
                               monotonic_clock=clock)
    return client, made, calls, clock


def test_a_failed_runtime_is_replaced_and_nothing_is_resent(monkeypatch):
    client, made, calls, _ = lost_runtime_client(monkeypatch)
    assert client.available is False
    assert client.reconnect(timeout_seconds=1.0) is True
    assert len(made) == 1 and client._runtime is made[0] and client.available is True
    assert calls == []  # reconnecting sends no order request


def test_reconnect_attempts_are_spaced_by_the_backoff(monkeypatch):
    client, made, _, clock = lost_runtime_client(monkeypatch, start_ok=False)
    assert client.reconnect(timeout_seconds=1.0) is False and len(made) == 1
    assert client.reconnect(timeout_seconds=1.0) is False and len(made) == 1  # too early
    clock.now += 0.99
    assert client.reconnect(timeout_seconds=1.0) is False and len(made) == 1  # waits 1 s
    clock.now += 0.01
    assert client.reconnect(timeout_seconds=1.0) is False and len(made) == 2
    clock.now += 1.0
    assert client.reconnect(timeout_seconds=1.0) is False and len(made) == 2  # then 2 s
    clock.now += 1.0
    client.reconnect(timeout_seconds=1.0)
    assert len(made) == 3
    clock.now += 10_000
    for _ in range(10):  # the wait never grows past the last entry (30 s)
        clock.now += 30.0
        client.reconnect(timeout_seconds=1.0)
    assert len(made) == 13


def test_a_running_but_unhealthy_runtime_is_left_to_recover(monkeypatch):
    client, made, _, _ = lost_runtime_client(monkeypatch)
    monkeypatch.setattr(client._runtime, "status",
                        lambda: snapshot(client.route, state="running", healthy=False))
    assert client.reconnect(timeout_seconds=1.0) is False and made == []


def test_place_after_risk_came_back_reconnects_and_in_flight_keys_are_kept(monkeypatch):
    data = parts()
    session = GatewayStrategyClientSession(**{**data, "host": Host(), "cancel_host": Host()})
    client, made, calls, clock = lost_runtime_client(monkeypatch, start_ok=False)
    api = GatewayBtApi(session, exchange_name="CTP___FUTURE", risk_client=client)
    monkeypatch.setattr(api._command, "reserve", lambda *a, **kw: pytest.fail("Gateway reserve"))
    api._risk_keys.add("in-flight-key")  # an earlier reserve whose outcome was not learned
    refused = api.reserve_order("CTP___FUTURE", order())
    assert (refused.outcome, refused.code) == ("REQUEST_DENIED", "RISK_UNAVAILABLE")
    assert calls == []
    clock.now += 60.0
    monkeypatch.setattr(made[-1], "start", lambda **kw: (_ for _ in ()).throw(
        ThreadedRuntimeError("START_FAILED")))
    # Risk is back: the next PLACE re-registers once and is answered.
    healthy = {"value": False}

    def factory_ok():
        runtime = ThreadedServiceClientRuntime(lambda: None)
        box = {"snapshot": snapshot(client.route, state="new", healthy=False)}
        monkeypatch.setattr(runtime, "status", lambda: box["snapshot"])
        monkeypatch.setattr(runtime, "start", lambda **kw: box.update(
            snapshot=snapshot(client.route, registration="reg-ok")) or box["snapshot"])
        monkeypatch.setattr(runtime, "close", lambda **kw: box["snapshot"])

        def call(mount, operation, arguments, **kwargs):
            calls.append((operation, arguments.get("request_key")))
            return answer("RESERVED", key=arguments["request_key"])

        monkeypatch.setattr(runtime, "call", call)
        healthy["value"] = True
        return runtime

    client._factory = factory_ok
    accepted = api.reserve_order("CTP___FUTURE", order())
    assert accepted.outcome == "OBSERVED" and healthy["value"] is True
    assert calls == [("risk.order.reserve", "same-key")]  # the in-flight key was never resent
    assert "in-flight-key" in api._risk_keys and "same-key" in api._risk_permits
    session.close()


def test_risk_order_status_never_makes_a_read_back_permit_dispatchable(monkeypatch):
    api, _, wire, gateway = facade(monkeypatch, replies=[answer("RESERVED", key="restored-key")])
    call = api.risk_order_status("CTP___FUTURE", "restored-key")
    assert call.outcome == "ANSWERED" and call.answer.outcome == "RESERVED"
    assert "restored-key" in api._risk_keys and "restored-key" not in api._risk_permits
    assert api.dispatch_order("CTP___FUTURE", "restored-key").code == "RISK_PERMIT_UNAVAILABLE"
    assert [c[1] for c in wire.calls] == ["risk.order.status"] and gateway == []
