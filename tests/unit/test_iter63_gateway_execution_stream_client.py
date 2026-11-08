"""Fake runtime tests; no native session, transport or account is created."""

from __future__ import annotations

import copy
import time
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest
from bt_api_base.direct_write_protocol import DirectExecutionEventKind
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.gateway_execution_stream_protocol import (
    GatewayExecutionObservation,
    GatewayExecutionStreamEvent,
    GatewayExecutionStreamPage,
    GatewayExecutionStreamProgress,
    gateway_execution_consumer_ref,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import (
    MAX_WIRE_MESSAGE_BYTES,
    WireIdentity,
    WireRole,
    WireScope,
    _freeze_payload,
)

from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_execution_stream_client import (
    GatewayExecutionReadContext,
    GatewayExecutionStreamClient,
)
from bt_api_py.service_runtime import ServiceRuntimeError, ServiceRuntimeRequestNotSentError
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime

EPOCH = "e" * 64

OPERATIONS = frozenset({"direct.events.read_after", "direct.events.progress", "direct.events.ack"})


class Owner:
    def __init__(self, route):
        self.identity, self.run_id = route.identity, route.scope.run_id
        self.bindings = (
            SimpleNamespace(
                registration_id="reg",
                service_epoch="service-epoch",
                active=True,
                mount=route.mount,
                service_id=route.service_id,
                instance_id=route.instance_id,
                scope_ref=route.scope_ref,
                scope=route.scope,
                operations=OPERATIONS,
                lease_deadline=time.monotonic() + 1000,
                required=True,
            ),
        )
        self.healthy = True
        self.cleanup_failures = self.unregister_uncertainties = 0
        self.calls = []
        self.reply = None
        self.raise_error = None
        self.frozen_reply = False

    def start(self):
        pass

    def maintain(self):
        pass

    def next_maintenance_delay(self):
        return 1.0

    def call(self, mount, operation, arguments, **kwargs):
        self.calls.append((mount, operation, arguments, kwargs))
        if self.raise_error is not None:
            raise self.raise_error
        reply = copy.deepcopy(self.reply)
        return _freeze_payload(reply) if self.frozen_reply else reply

    def close(self):
        self.healthy = False


@pytest.fixture
def system():
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-a", "strategy-a")
    scope = WireScope(
        "paper:demo", account_ref="account-a", strategy_ref="strategy-a", run_id="run-a"
    )
    route = GatewayClientRoute(
        "broker.direct_write", "service", "instance", "scope", scope, identity
    )
    binding = GatewayCommandBinding("journal", "account-a", "strategy-a", "principal-a")
    owner = Owner(route)
    runtime = ThreadedServiceClientRuntime(lambda: owner)
    runtime.start(timeout_seconds=2)
    client = GatewayExecutionStreamClient(runtime, binding=binding, route=route)
    try:
        yield client, owner, runtime, route, binding
    finally:
        runtime.close(timeout_seconds=2)


def consumer(route, binding):
    return gateway_execution_consumer_ref(
        principal_ref=route.identity.principal_ref,
        actor_ref=route.identity.actor_ref,
        execution_domain_ref=route.scope.execution_domain_ref,
        account_scope=binding.account_scope,
        strategy_scope=binding.strategy_scope,
    )


def event(sequence):
    observation = GatewayExecutionObservation(
        "account-a",
        "CTP___FUTURE",
        MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb"),
        "20261108",
        DirectExecutionEventKind.ORDER,
    )
    return GatewayExecutionStreamEvent(
        sequence, "request", "reservation", "a" * 64, "original-principal", "b" * 64, observation
    )


def page(route, binding, *, events=(1, 3), offered=3, acked=0, epoch=EPOCH):
    return GatewayExecutionStreamPage(
        binding,
        consumer(route, binding),
        epoch,
        offered,
        acked,
        False,
        tuple(event(i) for i in events),
    ).to_mapping()


def progress(route, binding, *, offered=3, acked=0, epoch=EPOCH):
    return GatewayExecutionStreamProgress(
        binding, consumer(route, binding), epoch, offered, acked
    ).to_mapping()


def test_sparse_page_no_automatic_ack_and_explicit_calls(system):
    client, owner, _, route, binding = system
    owner.reply = {"status": "OK", "page": page(route, binding)}
    result = client.read_after()
    assert result.outcome == "OBSERVED"
    assert [e.sequence for e in result.page.events] == [1, 3]
    assert [call[1] for call in owner.calls] == ["direct.events.read_after"]
    assert owner.calls[0][2] == {"after_sequence": 0, "expected_epoch": None, "limit": 256}
    assert result.request_context == GatewayExecutionReadContext(
        binding, consumer(route, binding), 0, None, 256
    )
    assert owner.calls[0][3]["max_reply_message_bytes"] == MAX_WIRE_MESSAGE_BYTES
    owner.reply = {"status": "OK", "progress": progress(route, binding, acked=3)}
    result = client.ack(3, EPOCH)
    assert result.outcome == "OBSERVED" and result.progress.acknowledged_sequence == 3
    assert result.request_context is None
    assert owner.calls[-1][1:3] == ("direct.events.ack", {"sequence": 3, "expected_epoch": EPOCH})
    result = client.progress()
    assert result.outcome == "OBSERVED" and result.request_context is None
    assert owner.calls[-1][2] == {}


def test_historical_replay_and_explicit_epoch(system):
    client, owner, _, route, binding = system
    owner.reply = {"status": "OK", "page": page(route, binding, events=(3,), offered=9)}
    first = client.read_after(1, EPOCH, 1)
    second = client.read_after(2, EPOCH, 2)
    assert first.outcome == second.outcome == "OBSERVED"
    for result, call in zip((first, second), owner.calls, strict=True):
        context = result.request_context
        assert context.binding is binding
        assert context.consumer_ref == consumer(route, binding)
        assert call[2] == {
            "after_sequence": context.after_sequence,
            "expected_epoch": context.expected_epoch,
            "limit": context.limit,
        }
    assert (first.request_context.after_sequence, second.request_context.after_sequence) == (1, 2)
    assert client.read_after(1).code == "INVALID_ARGUMENTS"
    assert len(owner.calls) == 2


def test_read_context_validates_exact_types_and_is_immutable(system):
    _, _, _, route, binding = system
    context = GatewayExecutionReadContext(binding, consumer(route, binding), 0, None, 256)
    with pytest.raises(FrozenInstanceError):
        context.after_sequence = 1
    assert not hasattr(context, "__dict__")
    invalid_binding = replace(binding)
    object.__setattr__(invalid_binding, "account_scope", "")
    for changes in (
        {"binding": object()},
        {"binding": invalid_binding},
        {"consumer_ref": "gexec:" + "E" * 64},
        {"consumer_ref": "gexec:" + "a" * 63},
        {"consumer_ref": True},
        {"after_sequence": True},
        {"after_sequence": -1},
        {"after_sequence": 2**63},
        {"after_sequence": 1},
        {"limit": True},
        {"limit": 0},
        {"limit": 257},
        {"expected_epoch": "E" * 64},
        {"expected_epoch": False},
    ):
        with pytest.raises(ValueError):
            replace(context, **changes)


def test_read_context_absent_on_all_failure_categories(system):
    client, owner, _, _, _ = system
    assert client.read_after(after_sequence=True).request_context is None
    owner.reply = {"status": "REQUEST_REJECTED", "code": "EPOCH_MISMATCH"}
    denied = client.read_after()
    assert denied.outcome == "REQUEST_DENIED" and denied.request_context is None
    owner.reply = {"status": "OK", "page": {}}
    malformed = client.read_after()
    assert malformed.outcome == "UNKNOWN" and malformed.request_context is None
    owner.raise_error = RuntimeError("lost reply")
    lost = client.read_after()
    assert lost.outcome == "UNKNOWN" and lost.request_context is None


def test_real_wire_freeze_shape_decodes_through_threaded_runtime(system):
    client, owner, _, route, binding = system
    owner.reply = {"status": "OK", "page": page(route, binding)}
    owner.frozen_reply = True
    result = client.read_after()
    assert result.outcome == "OBSERVED"
    assert [item.sequence for item in result.page.events] == [1, 3]


def test_untrusted_containers_and_cycles_are_rejected(system, monkeypatch):
    client, _, runtime, _, _ = system
    cycle = {}
    cycle["self"] = cycle

    class UntrustedDict(dict):
        def items(self):
            raise AssertionError("custom mapping code must not run")

    samples = [
        cycle,
        UntrustedDict(status="OK"),
        {"status": "OK", "page": [None] * 257},
        {"status": "OK", "page": "x" * (MAX_WIRE_MESSAGE_BYTES + 1)},
        {"status": "OK", "page": float("inf")},
    ]
    for sample in samples:
        monkeypatch.setattr(runtime, "call", lambda *args, sample=sample, **kwargs: sample)
        assert client.read_after().outcome == "UNKNOWN"


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, 10**100])
def test_timeout_rejected_before_request(system, timeout):
    client, owner, _, _, _ = system
    assert client.read_after(timeout_seconds=timeout).code == "INVALID_ARGUMENTS"
    assert client.progress(timeout_seconds=timeout).code == "INVALID_ARGUMENTS"
    assert client.ack(1, EPOCH, timeout_seconds=timeout).code == "INVALID_ARGUMENTS"
    assert not owner.calls


@pytest.mark.parametrize("epoch", ["short", "E" * 64, "g" * 64, True])
def test_epoch_rejected_before_request(system, epoch):
    client, owner, _, _, _ = system
    assert client.read_after(expected_epoch=epoch).code == "INVALID_ARGUMENTS"
    assert client.ack(1, epoch).code == "INVALID_ARGUMENTS"
    assert not owner.calls


def test_consumer_stays_stable_across_run_registration(system):
    _, _, runtime, route, binding = system
    new_route = replace(route, scope=replace(route.scope, run_id="run-b"))
    old = GatewayExecutionStreamClient(runtime, binding=binding, route=route)
    new = GatewayExecutionStreamClient(runtime, binding=binding, route=new_route)
    assert old._consumer_ref == new._consumer_ref


def test_wrong_process_never_calls_runtime(system):
    client, owner, _, _, _ = system
    client._pid = -1
    assert client.read_after().code == "WRONG_PROCESS"
    assert client.progress().code == "WRONG_PROCESS"
    assert client.ack(1, EPOCH).code == "WRONG_PROCESS"
    assert not owner.calls


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["binding"].update(account_scope="other"),
        lambda p: p["binding"].update(principal="other"),
        lambda p: p.update(consumer_ref="gexec:" + "f" * 64),
        lambda p: p.update(epoch="different"),
        lambda p: p["events"][0].update(sequence=1),
        lambda p: p["events"][0].update(sequence=4),
        lambda p: p["events"][0]["observation"].update(account_scope="other"),
        lambda p: p.update(offered_sequence=2),
        lambda p: p.update(unknown="unexpected"),
    ],
)
def test_untrusted_page_never_observed(system, mutate):
    client, owner, _, route, binding = system
    raw = page(route, binding, events=(3,), offered=3)
    mutate(raw)
    owner.reply = {"status": "OK", "page": raw}
    result = client.read_after(1, EPOCH)
    assert result.outcome == "UNKNOWN" and result.page is None


def test_sequence_order_limit_and_response_shapes(system):
    client, owner, _, route, binding = system
    raw = page(route, binding)
    raw["events"].reverse()
    owner.reply = {"status": "OK", "page": raw}
    assert client.read_after().outcome == "UNKNOWN"
    owner.reply = {"status": "OK", "page": page(route, binding)}
    assert client.read_after(limit=1).outcome == "UNKNOWN"
    owner.reply["extra"] = True
    assert client.read_after().outcome == "UNKNOWN"
    for kwargs in (
        {"limit": 0},
        {"limit": 257},
        {"limit": True},
        {"after_sequence": True},
        {"expected_epoch": ""},
    ):
        assert client.read_after(**kwargs).code == "INVALID_ARGUMENTS"


@pytest.mark.parametrize("offered,acked,epoch", [(3, 2, EPOCH), (3, 3, "f" * 64), (2, 3, EPOCH)])
def test_ack_progress_cannot_forge_success(system, offered, acked, epoch):
    client, owner, _, route, binding = system
    raw = progress(route, binding, offered=3, acked=0)
    raw.update(offered_sequence=offered, acknowledged_sequence=acked, epoch=epoch)
    owner.reply = {"status": "OK", "progress": raw}
    assert client.ack(3, EPOCH).outcome == "UNKNOWN"


def test_lost_ack_reply_is_unknown_no_retry(system):
    client, owner, _, _, _ = system
    owner.raise_error = RuntimeError("lost after commit")
    result = client.ack(3, EPOCH)
    assert result.outcome == "UNKNOWN" and result.progress is None
    assert [call[1] for call in owner.calls] == ["direct.events.ack"]


def test_reply_too_large_is_specific_denial_and_other_errors_remain_unknown(system):
    client, owner, _, _, _ = system
    owner.raise_error = ServiceRuntimeError("REPLY_TOO_LARGE")
    result = client.read_after()
    assert (result.outcome, result.code) == ("REQUEST_DENIED", "REPLY_TOO_LARGE")
    owner.raise_error = ServiceRuntimeError("HANDLER_FAILED")
    assert client.ack(1, EPOCH).outcome == "UNKNOWN"


def test_denials_are_finite_and_bad_replies_unknown(system):
    client, owner, _, _, _ = system
    owner.reply = {"status": "REQUEST_REJECTED", "code": "EPOCH_MISMATCH"}
    assert client.progress().outcome == "REQUEST_DENIED"
    owner.reply["code"] = "raw untrusted error"
    assert client.progress().outcome == "UNKNOWN"
    owner.reply = {"status": "OK", "progress": {}}
    assert client.progress().outcome == "UNKNOWN"


def test_runtime_failure_classification(system, monkeypatch):
    client, _, runtime, _, _ = system

    def fail(code):
        def call(*args, **kwargs):
            raise ThreadedRuntimeError(code)

        return call

    monkeypatch.setattr(runtime, "call", fail("TIMEOUT_BEFORE_START"))
    assert client.progress().outcome == "TRANSPORT_NOT_STARTED"
    monkeypatch.setattr(runtime, "call", fail("TIMEOUT_AFTER_START"))
    assert client.progress().outcome == "UNKNOWN"

    def not_sent(*args, **kwargs):
        raise ServiceRuntimeRequestNotSentError()

    monkeypatch.setattr(runtime, "call", not_sent)
    assert client.progress().outcome == "TRANSPORT_NOT_STARTED"


def test_route_registration_and_identity_checked(system, monkeypatch):
    client, owner, runtime, route, binding = system
    snapshot = runtime.status()
    binding_state = snapshot.bindings[0]
    bad_states = (
        replace(snapshot, identity=WireIdentity(WireRole.STRATEGY_CLIENT, "other", "strategy-a")),
        replace(snapshot, run_id="other"),
        replace(snapshot, bindings=(replace(binding_state, service_id="other"),)),
        replace(snapshot, bindings=(replace(binding_state, operations=frozenset()),)),
        replace(snapshot, bindings=(binding_state, binding_state)),
    )
    for state in bad_states:
        monkeypatch.setattr(runtime, "status", lambda state=state: state)
        assert client.progress().code == "ROUTE_UNAVAILABLE"
    assert not owner.calls
    delegated = replace(route, identity=replace(route.identity, delegate_ref="delegated"))
    with pytest.raises(ValueError, match="INVALID_CLIENT_ROUTE"):
        GatewayExecutionStreamClient(runtime, binding=binding, route=delegated)
