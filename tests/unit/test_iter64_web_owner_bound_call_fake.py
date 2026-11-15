"""Pure owner-call tests: no network, owner thread, account or database."""
from collections.abc import Mapping
from dataclasses import replace
import os
import threading
import time
from types import SimpleNamespace

import pytest

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py import service_runtime_thread as thread
from bt_api_py import service_runtime as raw_module
from bt_api_py.service_runtime import RegisteredBinding, ServiceClientRuntime
from bt_api_py.service_runtime_thread import (
    BoundCallRoute, BoundCallResult, ThreadedRuntimeError, _BoundCall,
    _execute_bound_call, _finish_bound_call,
)
from bt_api_py.risk_web_local_host import build_admin_caller
from bt_api_risk.web.app import RiskAdminCallError


MOUNT = "application.risk_admin"
OPERATION = "risk.admin.status"
IDENTITY = WireIdentity(WireRole.APPLICATION_CLIENT, "operator", "web")
PEER = WireIdentity(WireRole.SERVICE, "risk", "risk-service")
SCOPE = WireScope("domain", account_ref="account")
ROUTE = BoundCallRoute(IDENTITY, SCOPE, "scope", "service", "instance", PEER)
_RAW_RPC = ServiceClientRuntime._rpc
_RAW_MONOTONIC_NOW = ServiceClientRuntime._monotonic_now


def _binding(**fields):
    binding = RegisteredBinding(
        mount=MOUNT, service_id="service", instance_id="instance", scope_ref="scope",
        scope=SCOPE, registration_id="registration", service_epoch="epoch",
        operations=frozenset({OPERATION}), expires_at=99, policy_version="p",
        requirements_version="r", transport_ref="risk", timeout=1,
        lease_deadline=time.monotonic() + 100, required=True,
    )
    return replace(binding, **fields)


@pytest.fixture
def raw(monkeypatch):
    """An uninitialized exact raw object with every external method replaced."""
    runtime = object.__new__(ServiceClientRuntime)
    runtime._owner_thread_id = threading.get_ident()
    runtime._owner_pid = os.getpid()
    runtime._observation_owner_pid = os.getpid()
    runtime._observation_owner_thread = threading.current_thread()
    runtime._monotonic_clock = thread._NATIVE_MONOTONIC
    runtime._state = "registered"
    runtime.identity = IDENTITY
    runtime.run_id = "run"
    runtime._bindings = [_binding()]
    runtime._transports = {("service", "risk"): object()}
    runtime._peers = {"service": PEER}
    runtime._authenticated_registration_peers = {("service", "risk", "registration", "epoch"): (PEER, "a" * 64)}
    runtime._live_parent_capabilities_lock = threading.Lock()
    runtime._live_parent_capabilities = set()
    runtime.sent = []
    runtime.on_rpc = lambda: None
    monkeypatch.setattr(ServiceClientRuntime, "_strict_business_entry", lambda self: None)
    monkeypatch.setattr(ServiceClientRuntime, "_require_healthy", lambda self: None)
    monkeypatch.setattr(ServiceClientRuntime, "_monotonic_now", lambda self: thread._NATIVE_MONOTONIC())
    monkeypatch.setattr(ServiceClientRuntime, "authenticated_peer_for_binding",
                        lambda self, binding: (PEER, "a" * 64))

    def fake_rpc(self, service_id, transport_ref, scope, operation, payload, **kwargs):
        self.sent.append((service_id, transport_ref, scope, operation, payload))
        self.on_rpc()
        return {"status": "OBSERVED"}

    monkeypatch.setattr(ServiceClientRuntime, "_rpc", fake_rpc)
    return runtime


def _call():
    return _BoundCall(MOUNT, OPERATION, {}, {
        "account_ref": "account", "portfolio_ref": None, "scope_ref": "scope",
        "max_reply_message_bytes": None,
    }, ROUTE)


def _execute(raw):
    call = _call()
    return _finish_bound_call(raw, call, _execute_bound_call(raw, call))


class _MemoryTransport:
    def __init__(self):
        self.requests = []

    def request(self, envelope, *, timeout):
        self.requests.append((envelope, timeout))
        return SimpleNamespace(payload={"ok": True, "result": {"status": "OBSERVED"}})


def _real_rpc_with_fake_clock(raw, monkeypatch, samples, on_sample=lambda number: None):
    """Exercise the send path using only an in-memory transport and fake time."""
    times = iter(samples)
    count = 0

    def clock():
        nonlocal count
        count += 1
        on_sample(count)
        return next(times)

    monkeypatch.setattr(thread, "_NATIVE_MONOTONIC", clock)
    monkeypatch.setattr(raw_module, "_BOUND_MONOTONIC", clock)
    monkeypatch.setattr(ServiceClientRuntime, "_monotonic_now", _RAW_MONOTONIC_NOW)
    monkeypatch.setattr(ServiceClientRuntime, "_rpc", _RAW_RPC)
    monkeypatch.setattr(raw_module, "validate_reply", lambda *args, **kwargs: None)
    raw._monotonic_clock = clock
    raw._cleanup_unknown = None
    raw._startup_rollback = False
    raw._registration_failure_generation = {}
    raw._registration_lineage = {}
    raw._config = {"communication": {"profiles": {"risk": {"options": {"request_timeout_ms": 1000}}}}}
    raw.identity = WireIdentity(**vars(IDENTITY))
    raw._bindings = [_binding(scope=WireScope(**vars(SCOPE)), lease_deadline=101.0)]
    transport = _MemoryTransport()
    raw._transports = {("service", "risk"): transport}
    return transport


@pytest.mark.parametrize("boundary_time", [101.0, 102.0, 99.0, float('nan')])
def test_send_boundary_rejects_expired_or_untrusted_clock_without_sending(raw, monkeypatch, boundary_time):
    transport = _real_rpc_with_fake_clock(raw, monkeypatch, [100.0, 100.0, boundary_time])
    with pytest.raises(ThreadedRuntimeError, match="BOUND_OUTCOME_UNKNOWN"):
        _execute_bound_call(raw, _call())
    assert transport.requests == []


def test_send_boundary_caps_timeout_to_remaining_lease(raw, monkeypatch):
    transport = _real_rpc_with_fake_clock(raw, monkeypatch, [100.0, 100.0, 100.7, 100.75, 100.8])
    result = _execute(raw)
    assert result.result == {"status": "OBSERVED"}
    assert len(transport.requests) == 1
    assert transport.requests[0][1] == pytest.approx(0.2)


@pytest.mark.parametrize("change", ["transport", "binding", "scope", "identity", "peer", "proof", "run"])
def test_send_boundary_refuses_changes_after_initial_check(raw, monkeypatch, change):
    replacement = _MemoryTransport()

    def mutate(number):
        if number != 3:
            return
        if change == "transport":
            raw._transports[("service", "risk")] = replacement
        elif change == "binding":
            object.__setattr__(raw._bindings[0], "service_epoch", "changed")
        elif change == "scope":
            object.__setattr__(raw._bindings[0].scope, "account_ref", "other")
        elif change == "identity":
            object.__setattr__(raw.identity, "actor_ref", "other")
        elif change == "peer":
            raw._peers["service"] = WireIdentity(WireRole.SERVICE, "other", "risk-service")
        elif change == "proof":
            raw._authenticated_registration_peers[("service", "risk", "registration", "epoch")] = (PEER, "b" * 64)
        else:
            raw.run_id = "changed"

    transport = _real_rpc_with_fake_clock(raw, monkeypatch, [100.0, 100.0, 100.5], mutate)
    with pytest.raises(ThreadedRuntimeError, match="BOUND_OUTCOME_UNKNOWN"):
        _execute_bound_call(raw, _call())
    assert transport.requests == []
    assert replacement.requests == []


def test_send_uses_captured_transport_even_if_mapping_changes_at_final_clock(raw, monkeypatch):
    replacement = _MemoryTransport()

    def mutate(number):
        if number == 4:
            raw._transports[("service", "risk")] = replacement

    transport = _real_rpc_with_fake_clock(raw, monkeypatch, [100.0, 100.0, 100.5, 100.6, 100.7], mutate)
    call = _call()
    pending = _execute_bound_call(raw, call)
    with pytest.raises(ThreadedRuntimeError, match="BOUND_OUTCOME_UNKNOWN"):
        _finish_bound_call(raw, call, pending)
    assert len(transport.requests) == 1
    assert replacement.requests == []


@pytest.mark.parametrize("change", ["scope", "identity"])
def test_send_header_is_detached_from_live_binding_and_identity(raw, monkeypatch, change):
    def mutate(number):
        if number == 4:
            if change == "scope":
                object.__setattr__(raw._bindings[0].scope, "account_ref", "other")
            else:
                object.__setattr__(raw.identity, "actor_ref", "other")

    transport = _real_rpc_with_fake_clock(raw, monkeypatch, [100.0, 100.0, 100.5, 100.6, 100.7], mutate)
    call = _call()
    pending = _execute_bound_call(raw, call)
    with pytest.raises(ThreadedRuntimeError, match="BOUND_OUTCOME_UNKNOWN"):
        _finish_bound_call(raw, call, pending)
    assert len(transport.requests) == 1
    header = transport.requests[0][0].header
    assert header.scope is not raw._bindings[0].scope
    assert header.identity is not raw.identity
    assert header.scope == SCOPE
    assert header.identity == IDENTITY


def test_same_binding_is_selected_and_receipt_is_by_value(raw):
    result = _execute(raw)
    assert type(result) is BoundCallResult
    assert result.before == result.after
    assert result.before.scope == SCOPE and result.before.scope is not SCOPE
    assert result.before.identity == IDENTITY and result.before.identity is not IDENTITY
    assert len(raw.sent) == 1
    assert raw.sent[0][2] is raw._bindings[0].scope
    assert raw.sent[0][4]["registration_id"] == result.before.registration_id


@pytest.mark.parametrize("changes", [
    {"scope": WireScope("other", account_ref="account")},
    {"required": False}, {"active": False}, {"remote_lease_inactive": True},
    {"service_id": "other"}, {"scope_ref": "other"}, {"registration_id": ""},
    {"lease_deadline": 1.0},
])
def test_precheck_rejects_before_any_send(raw, changes):
    raw._bindings = [_binding(**changes)]
    with pytest.raises(ThreadedRuntimeError, match="BOUND_ROUTE_UNAVAILABLE"):
        _execute_bound_call(raw, _call())
    assert raw.sent == []


def test_ambiguous_raw_selection_rejected_before_send(raw):
    raw._bindings.append(_binding(service_id="other"))
    with pytest.raises(ThreadedRuntimeError, match="BOUND_ROUTE_UNAVAILABLE"):
        _execute_bound_call(raw, _call())
    assert raw.sent == []


@pytest.mark.parametrize("change", ["operation", "scope_ref", "arguments"])
def test_precheck_reentry_cannot_mutate_the_queued_request(raw, monkeypatch, change):
    call = _call()

    def proof(self, binding):
        if change == "operation":
            call.operation = "risk.cancel.request"
        elif change == "scope_ref":
            call.kwargs["scope_ref"] = "other"
        else:
            call.arguments["operation_id"] = "changed"
        return PEER, "a" * 64

    monkeypatch.setattr(ServiceClientRuntime, "authenticated_peer_for_binding", proof)
    with pytest.raises(ThreadedRuntimeError, match="BOUND_ROUTE_UNAVAILABLE"):
        _execute_bound_call(raw, call)
    assert raw.sent == []


@pytest.mark.parametrize("original,changed", [(1, True), (1, 1.0), (0.0, -0.0)])
def test_precheck_rejects_equal_valued_but_different_wire_types(raw, monkeypatch, original, changed):
    call = _BoundCall(MOUNT, OPERATION, {"x": original}, {
        "account_ref": "account", "portfolio_ref": None, "scope_ref": "scope",
        "max_reply_message_bytes": None,
    }, ROUTE)

    def proof(self, binding):
        call.arguments["x"] = changed
        return PEER, "a" * 64

    monkeypatch.setattr(ServiceClientRuntime, "authenticated_peer_for_binding", proof)
    with pytest.raises(ThreadedRuntimeError, match="BOUND_ROUTE_UNAVAILABLE"):
        _execute_bound_call(raw, call)
    assert raw.sent == []


@pytest.mark.parametrize("field,value", [
    ("scope", WireScope("other", account_ref="account")),
    ("registration_id", "other"), ("service_epoch", "other"),
    ("required", False), ("lease_deadline", 1.0),
])
def test_mutated_binding_after_send_is_unknown(raw, field, value):
    raw.on_rpc = lambda: object.__setattr__(raw._bindings[0], field, value)
    pending = _execute_bound_call(raw, _call())
    with pytest.raises(ThreadedRuntimeError, match="BOUND_OUTCOME_UNKNOWN"):
        _finish_bound_call(raw, _call(), pending)
    assert len(raw.sent) == 1


def test_reselected_equal_but_distinct_binding_is_unknown(raw):
    raw.on_rpc = lambda: raw._bindings.__setitem__(0, replace(raw._bindings[0]))
    pending = _execute_bound_call(raw, _call())
    with pytest.raises(ThreadedRuntimeError, match="BOUND_OUTCOME_UNKNOWN"):
        _finish_bound_call(raw, _call(), pending)
    assert len(raw.sent) == 1


@pytest.mark.parametrize("change", ["transport", "peer", "proof", "run"])
def test_changed_runtime_or_transport_after_send_is_unknown(raw, change):
    def mutate():
        if change == "transport":
            raw._transports[("service", "risk")] = object()
        elif change == "peer":
            raw._peers["service"] = WireIdentity(WireRole.SERVICE, "other", "risk-service")
        elif change == "proof":
            raw._authenticated_registration_peers[("service", "risk", "registration", "epoch")] = (PEER, "b" * 64)
        else:
            raw.run_id = "changed"
    raw.on_rpc = mutate
    pending = _execute_bound_call(raw, _call())
    with pytest.raises(ThreadedRuntimeError, match="BOUND_OUTCOME_UNKNOWN"):
        _finish_bound_call(raw, _call(), pending)
    assert len(raw.sent) == 1


def test_send_error_is_unknown_and_not_retried(raw):
    def fail():
        raise TimeoutError()
    raw.on_rpc = fail
    with pytest.raises(ThreadedRuntimeError, match="BOUND_OUTCOME_UNKNOWN"):
        _execute_bound_call(raw, _call())
    assert len(raw.sent) == 1


def test_response_copy_happens_before_last_observation(raw, monkeypatch):
    class MutatingReply(Mapping):
        def __getitem__(self, key):
            return "OBSERVED"

        def __iter__(self):
            return iter(("status",))

        def __len__(self):
            return 1

        def items(self):
            raw._bindings[0] = replace(raw._bindings[0], service_epoch="changed")
            return {"status": "OBSERVED"}.items()

    def fake_rpc(self, *args, **kwargs):
        self.sent.append("sent")
        return MutatingReply()

    monkeypatch.setattr(ServiceClientRuntime, "_rpc", fake_rpc)
    call = _call()
    pending = _execute_bound_call(raw, call)
    with pytest.raises(ThreadedRuntimeError, match="BOUND_OUTCOME_UNKNOWN"):
        _finish_bound_call(raw, call, pending)
    assert raw.sent == ["sent"]


def test_delivered_reply_is_detached_from_raw_result(raw, monkeypatch):
    original = {"status": "OBSERVED", "result": {"value": 1}}
    monkeypatch.setattr(ServiceClientRuntime, "_rpc", lambda *args, **kwargs: original)
    result = _execute(raw)
    original["result"]["value"] = 2
    assert result.result["result"]["value"] == 1


def test_raw_checks_same_object_and_values_before_rpc(raw):
    call = _call()
    binding, _, binding_values, runtime_values, transport = thread._bound_observation(raw, call)
    raw._bindings = [replace(binding)]
    with pytest.raises(Exception, match="BOUND_ROUTE_UNAVAILABLE"):
        raw.call(MOUNT, OPERATION, {}, _expected_binding=binding,
                 _expected_binding_values=binding_values, _expected_runtime_values=runtime_values,
                 _expected_transport=transport, _expected_peer=PEER, _expected_fingerprint="a" * 64,
                 **call.kwargs)
    assert raw.sent == []


@pytest.mark.parametrize("change", ["binding", "duplicate", "transport", "peer", "identity"])
def test_reentry_before_actual_rpc_is_detected(raw, monkeypatch, change):
    samples = 0

    def clock(self):
        nonlocal samples
        samples += 1
        # First sample is owner precheck, second is raw call's own lease check.
        if samples == 2:
            if change == "binding":
                object.__setattr__(self._bindings[0], "service_epoch", "changed")
            elif change == "duplicate":
                self._bindings.append(replace(self._bindings[0]))
            elif change == "transport":
                self._transports[("service", "risk")] = object()
            elif change == "peer":
                self._peers["service"] = WireIdentity(WireRole.SERVICE, "other", "service")
            else:
                self.run_id = "changed"
        return thread._NATIVE_MONOTONIC()

    monkeypatch.setattr(ServiceClientRuntime, "_monotonic_now", clock)
    with pytest.raises(ThreadedRuntimeError, match="BOUND_OUTCOME_UNKNOWN"):
        _execute_bound_call(raw, _call())
    assert raw.sent == []


def test_bound_queue_never_samples_external_lease_clock_under_condition():
    wrapper = thread.ThreadedServiceClientRuntime(lambda: None)
    wrapper._state = "running"
    wrapper._healthy = True

    def sample():
        assert not wrapper._condition._is_owned()
        return thread._NATIVE_MONOTONIC()

    wrapper._sample_lease_clock = sample
    wrapper._lease_current_at = lambda now: False
    with pytest.raises(ThreadedRuntimeError, match="NOT_READY"):
        wrapper.call_bound(MOUNT, OPERATION, {}, route=ROUTE, timeout_seconds=1)


def test_web_uses_bound_call_and_rejects_missing_receipt(raw):
    class FakeBoundRuntime:
        def __init__(self):
            self.count = 0

        def call_bound(self, mount, operation, arguments, *, route, timeout_seconds):
            self.count += 1
            assert route == ROUTE
            return {"status": "OBSERVED"}  # No owner receipt.

    fake = FakeBoundRuntime()
    caller = build_admin_caller(fake, identity=IDENTITY, scope=SCOPE, scope_ref="scope",
                                service_id="service", instance_id="instance", peer=PEER)
    with pytest.raises(RiskAdminCallError) as error:
        caller(OPERATION, {}, 1)
    assert error.value.kind == "UNKNOWN" and fake.count == 1


def test_web_accepts_only_matching_owner_receipt(raw):
    receipt = _execute(raw)

    class FakeBoundRuntime:
        def call_bound(self, mount, operation, arguments, *, route, timeout_seconds):
            return receipt

    caller = build_admin_caller(FakeBoundRuntime(), identity=IDENTITY, scope=SCOPE,
                                scope_ref="scope", service_id="service", instance_id="instance", peer=PEER)
    assert caller(OPERATION, {}, 1) == {"status": "OBSERVED"}


@pytest.mark.parametrize("code,kind", [
    ("BOUND_ROUTE_UNAVAILABLE", "NOT_SENT"), ("TIMEOUT_BEFORE_START", "NOT_SENT"),
    ("TIMEOUT_AFTER_START", "UNKNOWN"), ("CALL_OUTCOME_UNKNOWN", "UNKNOWN"),
])
def test_web_preserves_send_stage_failure_classification(code, kind):
    class FakeBoundRuntime:
        def call_bound(self, *args, **kwargs):
            raise ThreadedRuntimeError(code)

    caller = build_admin_caller(FakeBoundRuntime(), identity=IDENTITY, scope=SCOPE,
                                scope_ref="scope", service_id="service", instance_id="instance", peer=PEER)
    with pytest.raises(RiskAdminCallError) as error:
        caller(OPERATION, {}, 1)
    assert error.value.kind == kind


def test_web_rejects_invalid_receipt_lease(raw):
    receipt = _execute(raw)
    bad = replace(receipt.before, lease_deadline=-1)

    class FakeBoundRuntime:
        def call_bound(self, *args, **kwargs):
            return BoundCallResult(receipt.result, bad, bad)

    caller = build_admin_caller(FakeBoundRuntime(), identity=IDENTITY, scope=SCOPE,
                                scope_ref="scope", service_id="service", instance_id="instance", peer=PEER)
    with pytest.raises(RiskAdminCallError) as error:
        caller(OPERATION, {}, 1)
    assert error.value.kind == "UNKNOWN"


@pytest.mark.parametrize("mutate_after_action", [False, True])
def test_synthetic_owner_publishes_result_and_observation_together(raw, monkeypatch, mutate_after_action):
    """Execute _run on this test thread with a fully replaced fake lifecycle."""
    monkeypatch.setattr(ServiceClientRuntime, "start", lambda self: None)
    monkeypatch.setattr(ServiceClientRuntime, "maintain", lambda self: None)
    monkeypatch.setattr(ServiceClientRuntime, "close", lambda self: None)
    monkeypatch.setattr(ServiceClientRuntime, "healthy", property(lambda self: True))
    wrapper = thread.ThreadedServiceClientRuntime(lambda: raw)
    wrapper._state = "starting"
    wrapper._capture = lambda runtime: None
    call = _call()
    wrapper._queue.append(call)
    notify = wrapper._condition.notify_all

    def stop_after_publication():
        notify()
        if call.state == "done":
            wrapper._stop = True

    wrapper._condition.notify_all = stop_after_publication

    def checked(runtime, extension, action):
        value = action()
        # Simulate a wrapper's post-action evidence callback changing the
        # registration after RPC returns, before _Call is published.
        if mutate_after_action and type(value) is thread._BoundOwnerResult:
            raw._bindings[0] = replace(raw._bindings[0], service_epoch="changed")
        return value

    wrapper._checked_action = checked
    wrapper._run()  # No thread or real host is started.
    assert call.state == "done" and len(raw.sent) == 1
    if mutate_after_action:
        assert call.result is None and call.error.code == "CALL_OUTCOME_UNKNOWN"
    else:
        assert call.error is None and type(call.result) is BoundCallResult
        assert call.result.before == call.result.after


def test_shutdown_during_raw_call_returns_unknown_even_with_valid_reply(raw, monkeypatch):
    monkeypatch.setattr(ServiceClientRuntime, "start", lambda self: None)
    monkeypatch.setattr(ServiceClientRuntime, "maintain", lambda self: None)
    monkeypatch.setattr(ServiceClientRuntime, "close", lambda self: None)
    monkeypatch.setattr(ServiceClientRuntime, "healthy", property(lambda self: True))
    wrapper = thread.ThreadedServiceClientRuntime(lambda: raw)
    wrapper._state = "starting"
    wrapper._capture = lambda runtime: None
    call = _call()
    wrapper._queue.append(call)
    raw.on_rpc = lambda: setattr(wrapper, "_stop", True)
    wrapper._run()
    assert len(raw.sent) == 1 and call.state == "done"
    assert call.result is None and call.error.code == "CALL_OUTCOME_UNKNOWN"
