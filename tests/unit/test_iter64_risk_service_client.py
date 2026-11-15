"""Iteration 64: strategy Risk client (wire shapes, transport mapping) and PLACE routing.

UNIT/FAKE only: a real ThreadedServiceClientRuntime that is never started, with
its status/call replaced; no Risk host, Gateway or socket exists.
"""
from __future__ import annotations

import json
import threading
from dataclasses import replace
from types import SimpleNamespace

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
    bindings = (binding,) if state == "running" else ()
    return RuntimeSnapshot(state, state == "running", healthy, None, bindings, 0, 0,
                           route.identity, route.scope.run_id, unknown_registration_count=0,
                           strict_unregister_ack=True)


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
    client = RiskServiceClient(ThreadedServiceClientRuntime(lambda: None, require_unregister_ack=True), route=risk_route())
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
        self._risk_tickets = {}
        self._risk_ticket_lock = threading.Lock()

    def risk_first_send_host_contract(self):
        """Declare fake local ticket callbacks; no real durability is implied."""
        from types import MethodType

        def save(host, intent, **_kwargs):
            return host.create_risk_reserve_intent(intent)

        def create(host, intent):
            return host.create_risk_reserve_intent(intent)

        def consume(host, intent, ticket):
            return host.consume_risk_reserve_ticket(intent, ticket)

        return ("local-first-send-host-v1", MethodType(save, self),
                MethodType(create, self), MethodType(consume, self))

    def create_risk_reserve_intent(self, intent):
        with self._risk_ticket_lock:
            if intent.request_key in self.intents:
                return None
            self.intents[intent.request_key] = intent
            ticket = object()
            self._risk_tickets[intent.request_key] = (intent, ticket)
            return ticket

    def consume_risk_reserve_ticket(self, intent, ticket):
        with self._risk_ticket_lock:
            entry = self._risk_tickets.get(intent.request_key)
            if entry is None or entry[0] != intent or entry[1] is not ticket:
                return False
            del self._risk_tickets[intent.request_key]
            return True

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


def test_v1_reserve_keeps_the_key_unknown_without_any_wire_write(monkeypatch):
    api, host, wire, gateway = facade(monkeypatch, replies=[answer("RESERVED")])
    consumed = []
    consume_ticket = host.consume_risk_reserve_ticket

    def consume_once(intent, ticket):
        result = consume_ticket(intent, ticket)
        consumed.append((intent.request_key, result))
        return result

    monkeypatch.setattr(host, "consume_risk_reserve_ticket", consume_once)
    result = api.reserve_order("CTP___FUTURE", order(), timeout_seconds=2.0)
    assert (result.outcome, result.code, result.record) == ("UNKNOWN", "RISK_OUTCOME_UNKNOWN", None)
    assert gateway == [] and wire.calls == [] and len(wire.replies) == 1
    assert host.load_intent("same-key").request_key == "same-key"
    assert consumed == [("same-key", True)] and host._risk_tickets == {}
    assert "same-key" in api._risk_keys and "same-key" in api._risk_uncertain_keys
    assert "same-key" not in api._risk_permits
    assert api.dispatch_order("CTP___FUTURE", "same-key").outcome == "UNKNOWN"
    assert api.release_order("CTP___FUTURE", "same-key", operation_id="release-1").outcome == "UNKNOWN"
    assert gateway == [] and wire.calls == []


def test_v1_duplicate_keeps_the_original_intent_and_never_resends(monkeypatch):
    api, host, wire, gateway = facade(monkeypatch, replies=[answer("RESERVED")])
    assert api.reserve_order("CTP___FUTURE", order()).outcome == "UNKNOWN"
    original = host.load_intent("same-key")
    assert original is not None
    # A different retry payload with the same key must not replace the old intent.
    result = api.reserve_order("CTP___FUTURE", replace(order(), quantity=order().quantity * 2))
    assert (result.outcome, result.code) == ("UNKNOWN", "RISK_OUTCOME_UNKNOWN")
    assert host.load_intent("same-key") is original
    assert "same-key" in api._risk_uncertain_keys and "same-key" not in api._risk_permits
    assert wire.calls == [] and len(wire.replies) == 1 and gateway == []


@pytest.mark.parametrize("make_down", ["not_registered", "not_sent", "not_ready"])
def test_v1_gate_does_not_consume_unavailable_risk_replies(monkeypatch, make_down):
    api, _, wire, gateway = facade(monkeypatch)
    if make_down == "not_registered":
        wire.state["snapshot"] = snapshot(wire.route, state="failed", healthy=False)
    elif make_down == "not_sent":
        wire.replies.append(ServiceRuntimeRequestNotSentError())
    else:
        wire.replies.append(ThreadedRuntimeError("NOT_READY"))
    result = api.reserve_order("CTP___FUTURE", order())
    assert (result.outcome, result.code) == ("UNKNOWN", "RISK_OUTCOME_UNKNOWN")
    assert result.risk_denied is None
    assert gateway == [] and wire.calls == []
    assert len(wire.replies) == (0 if make_down == "not_registered" else 1)
    assert api.dispatch_order("CTP___FUTURE", "same-key").outcome == "UNKNOWN"
    assert gateway == [] and wire.calls == []


def test_synthetic_client_refusal_projection_preserves_risk_code(monkeypatch):
    # Direct client plus private projector: v1 facade reserve never reaches this branch.
    api, _, wire, gateway = facade(monkeypatch, replies=[
        answer("DENIED", code="risk_order_quantity_exceeds_cap", state="DENIED")])
    call = api._risk.reserve(order(), request_key="same-key")
    assert call.outcome == "ANSWERED" and call.answer.outcome == "DENIED"
    result = api._risk_refusal(call.answer)
    assert (result.outcome, result.code) == ("REQUEST_DENIED", "RISK_ORDER_QUANTITY_EXCEEDS_CAP")
    assert result.risk_denied == "risk_order_quantity_exceeds_cap" and gateway == []
    assert [entry[1] for entry in wire.calls] == ["risk.order.reserve"]


def test_synthetic_client_gateway_refusal_projection_keeps_gateway_code(monkeypatch):
    # This exercises the projector, not a reachable facade reserve path.
    api, _, wire, gateway = facade(monkeypatch, replies=[
        answer("DENIED", state="DENIED", code="risk_gateway_refused", gateway_code="CONTROL_STOP_NEW")])
    call = api._risk.reserve(order(), request_key="same-key")
    assert call.outcome == "ANSWERED" and call.answer.outcome == "DENIED"
    result = api._risk_refusal(call.answer)
    assert (result.outcome, result.code) == ("REQUEST_DENIED", "CONTROL_STOP_NEW")
    assert (result.risk_code, result.risk_denied) == ("risk_gateway_refused", None)
    assert [entry[1] for entry in wire.calls] == ["risk.order.reserve"] and gateway == []


@pytest.mark.parametrize("reply", [ThreadedRuntimeError("TIMEOUT_AFTER_START"),
                                   {"not": "a result"}, answer("UNKNOWN")])
def test_v1_gate_does_not_consume_unknown_risk_outcome(monkeypatch, reply):
    api, _, wire, gateway = facade(monkeypatch, replies=[reply])
    result = api.reserve_order("CTP___FUTURE", order())
    assert result.outcome == "UNKNOWN" and result.risk_denied is None and gateway == []
    assert wire.calls == [] and wire.replies == [reply]
    assert "same-key" in api._risk_keys  # later status/cancel use the delegated path


def test_synthetic_held_permit_dispatches_through_risk_not_gateway(monkeypatch):
    api, _, wire, gateway = facade(monkeypatch, replies=[answer("DISPATCHED")])
    # Injected only to test downstream projection; no v1 facade can acquire it.
    api._risk_keys.add("same-key")
    api._risk_permits["same-key"] = RiskOrderResult.from_mapping(answer("RESERVED"))
    ref = {"key": {"source_ref": "s", "entitlement_ref": "e",
                   "instrument": {"provider": "ctp", "venue": "SHFE", "environment": "paper",
                                  "product_type": "future", "instrument_id": "rb",
                                  "native_symbol": "rb"},
                   "channel": "tick", "depth": None, "timeframe": None},
           "epoch": "e1", "sequence": 3, "message_id": "m3"}
    result = api.dispatch_order("CTP___FUTURE", "same-key", market_ref=ref)
    assert result.outcome == "QUEUED" and result.record.state == "DISPATCHED"
    assert gateway == []
    assert len(wire.calls) == 1
    _, operation, arguments, _ = wire.calls[0]
    assert operation == "risk.order.dispatch"
    assert {k: arguments[k] for k in ("permit_id", "reservation_id", "view_digest")} == {
        "permit_id": "permit-1", "reservation_id": "res-1", "view_digest": DIGEST}


def test_synthetic_held_permit_release_uses_risk_not_gateway(monkeypatch):
    api, _, wire, gateway = facade(monkeypatch, replies=[answer("RELEASED")])
    # Artificial in-process state is not proof of a real not-sent reservation.
    api._risk_keys.add("same-key")
    api._risk_permits["same-key"] = RiskOrderResult.from_mapping(answer("RESERVED"))
    result = api.release_order("CTP___FUTURE", "same-key", operation_id="release-1")
    assert result.outcome == "OBSERVED" and result.record.state == "RELEASED"
    assert gateway == [] and len(wire.calls) == 1 and wire.calls[0][1:3] == (
        "risk.order.release", {"request_key": "same-key", "permit_id": "permit-1"})


def test_unknown_risk_key_status_and_cancel_use_delegated_gateway_even_with_risk_down(
        monkeypatch):
    api, _, wire, gateway = facade(monkeypatch)
    api.reserve_order("CTP___FUTURE", order())
    assert wire.calls == []  # v1 gate left the key UNKNOWN; no reservation was sent.
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
    assert wire.calls == []         # cancel and status never touched Risk


def test_unavailable_delegated_observation_never_cancels(monkeypatch):
    api, _, wire, gateway = facade(monkeypatch, replies=[answer("RESERVED")])
    api.reserve_order("CTP___FUTURE", order())
    monkeypatch.setattr(api._delegated, "status", lambda key, **kw: GatewayDelegatedCommandResult(
        "UNAVAILABLE", code="DELEGATED_COMMAND_UNAVAILABLE"))
    monkeypatch.setattr(api._delegated, "cancel", lambda *a, **kw: pytest.fail("cancelled blind"))
    result = api.cancel_order("CTP___FUTURE", "same-key", operation_id="cancel-1")
    assert result.outcome == "TRANSPORT_NOT_STARTED" and gateway == []
    assert wire.calls == [] and len(wire.replies) == 1


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
    assert api.reserve_order("CTP___FUTURE", order()).code == "RISK_OUTCOME_UNKNOWN"


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
        runtime = ThreadedServiceClientRuntime(lambda: None, require_unregister_ack=True)
        box = {"snapshot": snapshot(route, state=state, healthy=state == "running")}
        monkeypatch.setattr(runtime, "status", lambda: box["snapshot"])
        monkeypatch.setattr(runtime, "bounded_status", lambda **_: runtime.status())
        monkeypatch.setattr(runtime, "cleanup_observation", lambda **_: (runtime.status(),
                                                                        runtime._thread is not None))

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
        monkeypatch.setattr(runtime, "start_bounded_once", lambda **kw: runtime.start(
            timeout_seconds=kw["timeout_seconds"]))
        monkeypatch.setattr(runtime, "close", close)
        monkeypatch.setattr(runtime, "close_once_if_state", lambda **kw: runtime.close(
            timeout_seconds=kw["timeout_seconds"]))
        monkeypatch.setattr(runtime, "call", call)
        return runtime

    def factory():
        runtime = fake("new")
        made.append(runtime)
        return runtime

    clock = Clock()
    client = RiskServiceClient(fake("failed"), route=route, runtime_factory=factory,
                               monotonic_clock=clock)
    # Exercise the internal recovery state machine with an explicitly controlled fake.
    client._factory = factory
    return client, made, calls, clock


def test_a_failed_runtime_is_replaced_and_nothing_is_resent(monkeypatch):
    client, made, calls, _ = lost_runtime_client(monkeypatch)
    assert client.available is False
    assert client.reconnect(timeout_seconds=1.0) is True
    assert len(made) == 1 and client._runtime is made[0] and client.available is True
    assert calls == []  # reconnecting sends no order request


def test_factory_exception_is_bounded_and_blocks_further_attempts(monkeypatch):
    client, made, calls, clock = lost_runtime_client(monkeypatch)
    old, original_factory = client._runtime, client._factory
    attempts = []
    def factory():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("synthetic factory failure")
        return original_factory()
    client._factory = factory
    assert client.reconnect(timeout_seconds=1.0) is False
    assert client._runtime is old and made == [] and calls == []
    assert client._pending_candidate_unknown is True
    assert client.reconnect(timeout_seconds=1.0) is False and attempts == [1]
    for _ in range(3):
        clock.now += 60.0
        assert client.reconnect(timeout_seconds=1.0) is False
    assert len(made) == 0 and calls == [] and len(attempts) == 1
    assert client.status("prior", timeout_seconds=1).outcome == "NOT_SENT"


def test_v1_gate_does_not_invoke_unavailable_runtime_factory(monkeypatch):
    data = parts()
    session = GatewayStrategyClientSession(**{**data, "host": Host(), "cancel_host": Host()})
    client, _made, calls, _clock = lost_runtime_client(monkeypatch)
    old = client._runtime
    factory_calls = []
    def fail():
        factory_calls.append(True)
        raise RuntimeError("synthetic factory failure")
    client._factory = fail
    api = GatewayBtApi(session, exchange_name="CTP___FUTURE", risk_client=client)
    monkeypatch.setattr(api._command, "reserve", lambda *a, **kw: pytest.fail("Gateway fallback"))
    api._risk_keys.add("unknown-old-key")
    refused = api.reserve_order("CTP___FUTURE", order())
    assert (refused.outcome, refused.code) == ("UNKNOWN", "RISK_OUTCOME_UNKNOWN")
    assert client._runtime is old and calls == [] and factory_calls == []
    assert "unknown-old-key" in api._risk_keys
    session.close()


@pytest.mark.parametrize("problem", ["unregister", "owner", "factory", "alive", "error"])
def test_reconnect_keeps_original_when_cleanup_is_not_proven(monkeypatch, problem):
    client, made, calls, clock = lost_runtime_client(monkeypatch)
    old = client._runtime
    cleanup_calls = []
    # The owner has not furnished a stopped, clean proof. Do not prod its
    # close path: the owner may already be waiting to retry an uncertain close.
    monkeypatch.setattr(old, "status", lambda: replace(
        snapshot(client.route, state="close_failed", healthy=False),
        error_code="CLEANUP_FAILED"))
    def close(**_kwargs):
        cleanup_calls.append(True)
        if problem == "error":
            raise ThreadedRuntimeError("CLEANUP_FAILED")
        return replace(snapshot(client.route, state="failed", healthy=False),
            thread_alive=problem == "alive",
            error_code="FACTORY_FAILED_CLEANUP_UNPROVEN" if problem == "factory" else "MAINTENANCE_FAILED",
            unregister_uncertainties=int(problem == "unregister"),
            owner_cleanup_uncertainties=int(problem == "owner"))
    monkeypatch.setattr(old, "close", close)
    assert client.reconnect(timeout_seconds=1.0) is False
    assert client._runtime is old and made == [] and calls == []
    # A failed or ambiguous close is not retried, even if a later fake would
    # report success: the original owner may still hold retained resources.
    def later_close(**_kwargs):
        cleanup_calls.append(True)
        return snapshot(client.route, state="closed", healthy=False)
    monkeypatch.setattr(old, "close", later_close)
    clock.now += 1.0
    assert client.reconnect(timeout_seconds=1.0) is False
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1.0)
    assert client._runtime is old and cleanup_calls == [] and made == [] and calls == []


@pytest.mark.parametrize("proof", [None, False, 1])
def test_client_rejects_missing_or_invalid_strict_cleanup_proof(monkeypatch, proof):
    client, wire = make_client(monkeypatch, [answer("RESERVED")])
    wire.state["snapshot"] = replace(wire.state["snapshot"], strict_unregister_ack=proof)
    assert not client.available
    assert client.reserve(order(), request_key="same-key", timeout_seconds=1).outcome == "NOT_SENT"
    assert wire.calls == []
    client, made, calls, _ = lost_runtime_client(monkeypatch)
    old = client._runtime
    monkeypatch.setattr(old, "status", lambda: replace(
        snapshot(client.route, state="failed", healthy=False), strict_unregister_ack=proof))
    assert client.reconnect(timeout_seconds=1) is False
    assert client._runtime is old and made == [] and calls == []


def test_factory_without_strict_contract_never_starts_and_keeps_old(monkeypatch):
    client, _made, calls, _clock = lost_runtime_client(monkeypatch)
    old = client._runtime
    unsafe = ThreadedServiceClientRuntime(lambda: pytest.fail("unsafe factory started"))
    client._factory = lambda: unsafe
    assert client.reconnect(timeout_seconds=1) is False
    assert client._runtime is old and unsafe.status().state == "new" and calls == []
    assert client._pending_runtime is unsafe


@pytest.mark.parametrize("kind", ["status_raises", "fake_object", "none"])
def test_uninspectable_factory_candidate_is_retained_and_blocks_next_factory(monkeypatch, kind):
    client, made, calls, clock = lost_runtime_client(monkeypatch)
    old = client._runtime
    if kind == "status_raises":
        candidate = ThreadedServiceClientRuntime(lambda: None)
        monkeypatch.setattr(candidate, "status", lambda: (_ for _ in ()).throw(RuntimeError("status")))
        monkeypatch.setattr(candidate, "close", lambda **_: (_ for _ in ()).throw(RuntimeError("close")))
    elif kind == "fake_object":
        candidate = object()
    else:
        candidate = None
    issued = []
    client._factory = lambda: issued.append(candidate) or candidate
    assert client.reconnect(timeout_seconds=1) is False
    assert client._runtime is old and client._pending_runtime is candidate
    assert client._pending_candidate_unknown is (candidate is None)
    assert client.reserve(order(), request_key="same-key", timeout_seconds=1).outcome == "NOT_SENT"
    clock.now += 1
    assert client.reconnect(timeout_seconds=1) is False
    assert issued == [candidate] and client._pending_runtime is candidate
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)
    assert made == [] and calls == []


def test_failed_candidate_with_uncertain_cleanup_is_retained_and_never_parallelized(monkeypatch):
    client, made, calls, clock = lost_runtime_client(monkeypatch)
    old, normal_factory = client._runtime, client._factory
    candidate = ThreadedServiceClientRuntime(lambda: None, require_unregister_ack=True)
    monkeypatch.setattr(candidate, "cleanup_observation", lambda **_: (candidate.status(),
                                                                         candidate._thread is not None))
    closed = replace(snapshot(client.route, state="failed", healthy=False), unregister_uncertainties=1)
    monkeypatch.setattr(candidate, "start", lambda **_: (_ for _ in ()).throw(
        ThreadedRuntimeError("START_FAILED")))
    monkeypatch.setattr(candidate, "start_bounded_once", lambda **kw: candidate.start(
        timeout_seconds=kw["timeout_seconds"]))
    monkeypatch.setattr(candidate, "close", lambda **_: closed)
    monkeypatch.setattr(candidate, "status", lambda: closed)
    factory_calls = []
    client._factory = lambda: factory_calls.append(True) or candidate
    assert client.reconnect(timeout_seconds=1) is False
    assert client._runtime is old and client._pending_runtime is candidate and factory_calls == [True]
    clock.now += 1
    assert client.reconnect(timeout_seconds=1) is False
    assert client._runtime is old and client._pending_runtime is candidate and factory_calls == [True]
    # A later synthetic success cannot justify repeating an uncertain cleanup.
    cleanup_calls = []
    monkeypatch.setattr(candidate, "close", lambda **_: cleanup_calls.append(True) or replace(
        closed, unregister_uncertainties=0))
    monkeypatch.setattr(candidate, "status", lambda: replace(closed, unregister_uncertainties=0))
    client._factory = normal_factory
    clock.now += 2
    assert client.reconnect(timeout_seconds=1) is False
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)
    assert client._pending_runtime is candidate and cleanup_calls == [] and made == [] and calls == []


@pytest.mark.parametrize("problem", ["status_raises", "status_mismatch", "thread_alive"])
def test_candidate_close_needs_its_own_consistent_stopped_snapshot(monkeypatch, problem):
    client, made, calls, clock = lost_runtime_client(monkeypatch)
    old = client._runtime
    candidate = ThreadedServiceClientRuntime(lambda: None, require_unregister_ack=True)
    monkeypatch.setattr(candidate, "cleanup_observation", lambda **_: (candidate.status(),
                                                                         candidate._thread is not None))
    clean = snapshot(client.route, state="closed", healthy=False)
    monkeypatch.setattr(candidate, "start", lambda **_: (_ for _ in ()).throw(
        ThreadedRuntimeError("START_FAILED")))
    monkeypatch.setattr(candidate, "start_bounded_once", lambda **kw: candidate.start(
        timeout_seconds=kw["timeout_seconds"]))
    monkeypatch.setattr(candidate, "close", lambda **_: clean)
    if problem == "status_raises":
        monkeypatch.setattr(candidate, "status", lambda: (_ for _ in ()).throw(RuntimeError("status")))
    elif problem == "status_mismatch":
        monkeypatch.setattr(candidate, "status", lambda: replace(clean, unregister_uncertainties=1))
    else:
        monkeypatch.setattr(candidate, "status", lambda: replace(clean, thread_alive=True))
        monkeypatch.setattr(candidate, "_thread", SimpleNamespace(is_alive=lambda: True))
    issued = []
    client._factory = lambda: issued.append(candidate) or candidate
    assert client.reconnect(timeout_seconds=1) is False
    clock.now += 1
    assert client.reconnect(timeout_seconds=1) is False
    assert client._runtime is old and client._pending_runtime is candidate
    assert issued == [candidate] and made == [] and calls == []


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


def test_explicit_client_reconnect_keeps_v1_facade_keys_unknown_and_unsent(monkeypatch):
    data = parts()
    session = GatewayStrategyClientSession(**{**data, "host": Host(), "cancel_host": Host()})
    client, made, calls, clock = lost_runtime_client(monkeypatch, start_ok=False)
    api = GatewayBtApi(session, exchange_name="CTP___FUTURE", risk_client=client)
    monkeypatch.setattr(api._command, "reserve", lambda *a, **kw: pytest.fail("Gateway reserve"))
    api._risk_keys.add("in-flight-key")  # an earlier reserve whose outcome was not learned
    initial = api.reserve_order("CTP___FUTURE", order())
    assert (initial.outcome, initial.code) == ("UNKNOWN", "RISK_OUTCOME_UNKNOWN")
    assert calls == [] and made == []  # facade gate does not try to reconnect or send
    # Explicitly exercise client reconnection, independently of the gated facade.
    assert client.reconnect(timeout_seconds=1) is False and len(made) == 1
    clock.now += 60.0
    monkeypatch.setattr(made[-1], "start", lambda **kw: (_ for _ in ()).throw(
        ThreadedRuntimeError("START_FAILED")))
    # Risk is back: explicitly reconnect the synthetic client, not PLACE.
    healthy = {"value": False}

    def factory_ok():
        runtime = ThreadedServiceClientRuntime(lambda: None, require_unregister_ack=True)
        box = {"snapshot": snapshot(client.route, state="new", healthy=False)}
        monkeypatch.setattr(runtime, "status", lambda: box["snapshot"])
        monkeypatch.setattr(runtime, "bounded_status", lambda **_: runtime.status())
        monkeypatch.setattr(runtime, "start", lambda **kw: box.update(
            snapshot=snapshot(client.route, registration="reg-ok")) or box["snapshot"])
        monkeypatch.setattr(runtime, "start_bounded_once", lambda **kw: runtime.start(
            timeout_seconds=kw["timeout_seconds"]))
        monkeypatch.setattr(runtime, "close", lambda **kw: box["snapshot"])

        def call(mount, operation, arguments, **kwargs):
            calls.append((operation, arguments.get("request_key")))
            return answer("RESERVED", key=arguments["request_key"])

        monkeypatch.setattr(runtime, "call", call)
        healthy["value"] = True
        return runtime

    client._factory = factory_ok
    assert client.reconnect(timeout_seconds=1) is True and healthy["value"] is True
    # Neither the prior uncertain key nor a fresh key gains first-send proof.
    prior = api.reserve_order("CTP___FUTURE", replace(order(), idempotency_key="in-flight-key"))
    assert (prior.outcome, prior.code) == ("UNKNOWN", "RISK_OUTCOME_UNKNOWN")
    assert calls == []
    assert api.reserve_order("CTP___FUTURE", order()).outcome == "UNKNOWN"
    fresh = api.reserve_order(
        "CTP___FUTURE", replace(order(), idempotency_key="new-key")
    )
    assert (fresh.outcome, fresh.code) == ("UNKNOWN", "RISK_OUTCOME_UNKNOWN")
    assert calls == []
    assert {"in-flight-key", "same-key", "new-key"} <= api._risk_keys
    assert api._risk_permits == {}
    session.close()


def test_risk_order_status_never_makes_a_read_back_permit_dispatchable(monkeypatch):
    api, _, wire, gateway = facade(monkeypatch, replies=[answer("RESERVED", key="restored-key")])
    call = api.risk_order_status("CTP___FUTURE", "restored-key")
    assert call.outcome == "ANSWERED" and call.answer.outcome == "RESERVED"
    assert "restored-key" in api._risk_keys and "restored-key" not in api._risk_permits
    result = api.dispatch_order("CTP___FUTURE", "restored-key")
    assert (result.outcome, result.code) == ("UNKNOWN", "RISK_OUTCOME_UNKNOWN")
    result = api.release_order("CTP___FUTURE", "restored-key", operation_id="release-restored")
    assert (result.outcome, result.code) == ("UNKNOWN", "RISK_OUTCOME_UNKNOWN")
    assert [c[1] for c in wire.calls] == ["risk.order.status"] and gateway == []
