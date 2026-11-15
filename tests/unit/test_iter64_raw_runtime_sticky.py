"""Pure in-memory cleanup observations; these do not establish owner safety."""
from dataclasses import replace
import gc
import inspect
import sys
import weakref

import pytest

from bt_api_py.service_runtime import RegisteredBinding, ServiceRuntimeError
from bt_api_py import service_runtime
from tests.unit.test_service_runtime import PEER, SCOPE, _config, _runtime


class Transport:
    def __init__(self, calls, name, error=None):
        self.calls, self.name, self.error = calls, name, error

    def close(self):
        self.calls.append(("close", self.name))
        if self.error:
            raise self.error


def make_binding(name, **kwargs):
    return RegisteredBinding(
        mount="store.account", service_id=name, instance_id="instance", scope_ref="scope",
        scope=SCOPE, registration_id="reg-" + name, service_epoch="epoch-" + name,
        operations=frozenset({"account.snapshot"}), expires_at=100, policy_version="p",
        requirements_version="r", transport_ref="profile", timeout=1, **kwargs,
    )


def setup_runtime(*, strict=True, guard=lambda: False, notify=lambda: None, ack=False):
    options = {"cleanup_unknown": guard, "on_cleanup_sticky": notify} if strict else {}
    return _runtime(_config(), lambda *_: None, require_unregister_ack=ack, **options)


def populate(runtime, calls):
    first, second = make_binding("one"), make_binding("two")
    t1, t2 = Transport(calls, "one"), Transport(calls, "two")
    runtime._bindings = [first, second]
    runtime._transports = {("one", "profile"): t1, ("two", "profile"): t2}
    runtime._registration_lineage = {id(first): (first,), id(second): (second,)}
    runtime._state = "registered"
    return first, second, t1, t2


def test_failure_retains_exact_resources_and_reentry_is_inert():
    calls, notices, reentries = [], [], []
    runtime = None
    def notify():
        notices.append(True)
        for method in (runtime.close, runtime.recover, runtime.start):
            with pytest.raises(ServiceRuntimeError):
                method()
            reentries.append(True)
    runtime = setup_runtime(notify=notify)
    first, second, t1, t2 = populate(runtime, calls)
    ref = weakref.ref(first)
    def unregister(binding):
        calls.append(("unregister", binding.registration_id))
        raise TimeoutError("unknown")
    runtime._unregister_binding = unregister
    with pytest.raises(TimeoutError):
        runtime.close()
    assert calls == [("unregister", "reg-one")]
    assert notices == [True] and len(reentries) == 3 and runtime.state == "close_failed"
    assert runtime.bindings == (first, second) and runtime._transports[("one", "profile")] is t1
    assert runtime._transports[("two", "profile")] is t2 and id(first) in runtime._registration_lineage
    del first
    assert ref() is runtime.bindings[0]
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert calls == [("unregister", "reg-one")]


@pytest.mark.parametrize("error", [KeyboardInterrupt(), RuntimeError("close failed")])
def test_transport_failure_stops_and_preserves_original(error):
    calls = []
    runtime = setup_runtime()
    first, second, t1, t2 = populate(runtime, calls)
    runtime._bindings = []
    t1.error = error
    with pytest.raises(type(error)) as caught:
        runtime.close()
    assert caught.value is error and calls == [("close", "one")]
    assert runtime._transports[("one", "profile")] is t1
    assert runtime._transports[("two", "profile")] is t2


def test_guard_before_after_and_success():
    calls, checks = [], []
    def guard():
        checks.append(True)
        return len(checks) == 3
    runtime = setup_runtime(guard=guard)
    first, second, t1, t2 = populate(runtime, calls)
    runtime._unregister_binding = lambda b: calls.append(("unregister", b.registration_id)) or True
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert calls == [("unregister", "reg-one")] and runtime.bindings == (first, second)
    calls.clear()
    runtime = setup_runtime()
    populate(runtime, calls)
    runtime._unregister_binding = lambda b: calls.append(("unregister", b.registration_id)) or True
    runtime.close()
    assert calls == [("unregister", "reg-one"), ("unregister", "reg-two"),
                     ("close", "one"), ("close", "two")]
    assert runtime.state == "closed" and not runtime.bindings


def test_failed_start_does_not_continue_rollback():
    calls = []
    runtime = setup_runtime()
    first, second, t1, t2 = populate(runtime, calls)
    runtime._state = "new"
    def register(*args, **kwargs):
        raise ValueError("startup failure")
    runtime._register = register
    runtime._unregister_binding = lambda b: calls.append(("unregister", b.registration_id)) or False
    with pytest.raises(ServiceRuntimeError):
        runtime.start()
    assert calls == [("unregister", "reg-one")] and runtime.bindings == (first, second)
    assert runtime.state == "close_failed" and len(runtime._transports) == 2


def test_default_policy_preserves_best_effort_and_ack_alone_is_not_opt_in():
    calls = []
    runtime = setup_runtime(strict=False, ack=True)
    assert runtime.sticky_cleanup_enabled is False
    populate(runtime, calls)
    runtime._unregister_binding = lambda b: calls.append(("unregister", b.registration_id)) or False
    runtime.close()
    assert calls == [("unregister", "reg-one"), ("unregister", "reg-two"),
                     ("close", "one"), ("close", "two")]


def test_missing_transport_keeps_binding_and_fails_closed():
    calls = []
    runtime = setup_runtime()
    first, second, t1, t2 = populate(runtime, calls)
    del runtime._transports[("one", "profile")]
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert runtime.state == "close_failed" and runtime.bindings == (first, second)
    assert calls == [] and runtime._transports[("two", "profile")] is t2


def test_optional_start_cleanup_failure_does_not_retry_in_outer_rollback():
    calls = []
    runtime = _runtime(_config(extra=False), lambda *_: None,
                       cleanup_unknown=lambda: False, on_cleanup_sticky=lambda: None)
    first, second = make_binding("one"), make_binding("two")
    t1, t2 = Transport(calls, "one"), Transport(calls, "two")
    def register(binding, **kwargs):
        if binding.get("required", True):
            runtime._bindings.append(first)
            runtime._transports[("one", "profile")] = t1
        else:
            runtime._bindings.append(second)
            runtime._transports[("two", "profile")] = t2
            raise ServiceRuntimeError("INVALID_SERVICE_REPLY")
    runtime._register = register
    runtime._startup_maintain_if_due = lambda: None
    runtime._unregister_binding = lambda b: calls.append(("unregister", b.registration_id)) or False
    with pytest.raises(ServiceRuntimeError):
        runtime.start()
    assert calls == [("unregister", "reg-two")]
    assert runtime.bindings == (first, second) and len(runtime._transports) == 2
    assert runtime.state == "close_failed"


@pytest.mark.parametrize("case", ["unknown", "missing_handle", "invalid_receipt"])
def test_register_failure_retains_transport_and_stops_rollback(case):
    calls = []
    runtime = setup_runtime()
    transport = Transport(calls, "one")
    runtime._transports[("account-service", "account-zmq")] = transport
    runtime._state = "starting"
    binding = runtime._config["clients"]["gateway"]["settings"]["bindings"][0]
    capabilities = [{"instance_id": "gateway-a", "port": "service.account_read",
                     "operations": ["account.snapshot"]}]
    def rpc(service, transport_ref, scope, operation, payload, **kwargs):
        calls.append(operation)
        if operation == "service.register":
            kwargs["timing"]["request_started"] = 1
            if case == "unknown":
                raise TimeoutError("unobserved")
            if case == "missing_handle":
                return ({}, 1)
            return ({"registration_id": "exact", "service_epoch": "epoch"}, 1)
        assert operation == "service.unregister"
        return {"unregistered": True, "registration_id": "wrong"}
    runtime._rpc = rpc
    with pytest.raises((TimeoutError, ServiceRuntimeError)):
        runtime._register_port(binding, SCOPE, "service.account_read", capabilities,
                               timeout=1, required=False, heartbeat_interval_seconds=None)
    assert runtime.state == "close_failed" and runtime._transports[("account-service", "account-zmq")] is transport
    if case == "invalid_receipt":
        assert calls == ["service.register", "service.unregister"]
        assert runtime.bindings[0].registration_id == "exact"
    else:
        assert calls == ["service.register"]
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert calls[-1] != ("close", "one")


def test_optional_degrade_failed_unregister_is_sticky():
    calls = []
    runtime = setup_runtime()
    first, second, t1, t2 = populate(runtime, calls)
    runtime._unregister_binding = lambda b: calls.append(("unregister", b.registration_id)) or False
    with pytest.raises(ServiceRuntimeError):
        runtime._degrade_binding(first, "LEASE_EXPIRED")
    assert calls == [("unregister", "reg-one")]
    assert runtime.state == "close_failed" and runtime.bindings == (first, second)


def test_synchronous_guard_reentry_is_blocked_without_resource_action():
    calls = []
    runtime = None
    def guard():
        with pytest.raises(ServiceRuntimeError):
            runtime.close()
        return False
    runtime = setup_runtime(guard=guard)
    populate(runtime, calls)
    runtime._unregister_binding = lambda b: calls.append(("unregister", b.registration_id)) or True
    runtime.close()
    assert calls == [("unregister", "reg-one"), ("unregister", "reg-two"),
                     ("close", "one"), ("close", "two")]


def test_opt_in_requires_both_callbacks_and_strict_boolean_guard():
    with pytest.raises(ServiceRuntimeError):
        _runtime(_config(), lambda *_: None, cleanup_unknown=lambda: False)
    calls = []
    runtime = setup_runtime(guard=lambda: 0)
    first, second, t1, t2 = populate(runtime, calls)
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert calls == [] and runtime.bindings == (first, second)
    assert runtime.sticky_cleanup_enabled is True


def test_guard_interrupt_is_preserved_and_sticky():
    calls = []
    interruption = KeyboardInterrupt()
    def guard():
        raise interruption
    runtime = setup_runtime(guard=guard)
    first, second, t1, t2 = populate(runtime, calls)
    with pytest.raises(KeyboardInterrupt) as caught:
        runtime.close()
    assert caught.value is interruption and runtime.state == "close_failed"
    assert calls == [] and runtime.bindings == (first, second)
    assert runtime._cleanup_evidence[0][-1] is interruption


def test_start_interrupt_is_not_replaced_by_failed_cleanup():
    calls = []
    interruption = KeyboardInterrupt()
    runtime = setup_runtime()
    first, second, t1, t2 = populate(runtime, calls)
    runtime._state = "new"
    def register(*args, **kwargs):
        raise interruption
    runtime._register = register
    runtime._unregister_binding = lambda b: calls.append(("unregister", b.registration_id)) or False
    with pytest.raises(KeyboardInterrupt) as caught:
        runtime.start()
    assert caught.value is interruption and runtime.state == "close_failed"
    assert calls == [("unregister", "reg-one")] and runtime.bindings == (first, second)


@pytest.mark.parametrize("guard_results", [[True], [False, True]])
def test_empty_cleanup_checks_guard_at_entry_and_completion(guard_results):
    notices = []
    results = iter(guard_results)
    runtime = setup_runtime(guard=lambda: next(results), notify=lambda: notices.append(True))
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert runtime.state == "close_failed" and notices == [True]
    with pytest.raises(ServiceRuntimeError):
        runtime.close()


def invoke_entry(runtime, entry):
    if entry == "call":
        return runtime.call("store.account", "account.snapshot", {})
    if entry == "market_binding_view":
        return runtime.market_binding_view("store.account", required_operations=frozenset())
    if entry == "account_binding_view":
        return runtime.account_binding_view("store.account", "account-a", required_operations=frozenset())
    if entry == "authenticated_peer_for_binding":
        return runtime.authenticated_peer_for_binding(runtime.bindings[0])
    return getattr(runtime, entry)()


@pytest.mark.parametrize("entry", ["call", "maintain", "renew", "next_maintenance_delay",
                                  "market_binding_view", "account_binding_view",
                                  "authenticated_peer_for_binding"])
def test_reentrant_business_during_cleanup_sets_sticky_before_rpc(entry):
    calls, notices = [], []
    runtime = None
    def guard():
        with pytest.raises(ServiceRuntimeError):
            invoke_entry(runtime, entry)
        return False
    runtime = setup_runtime(guard=guard, notify=lambda: notices.append(True))
    first, second, t1, t2 = populate(runtime, calls)
    runtime._rpc = lambda *args, **kwargs: calls.append("rpc")
    runtime._unregister_binding = lambda b: calls.append("unregister") or True
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert runtime.state == "close_failed" and notices == [True]
    assert calls == [] and runtime.bindings == (first, second)
    assert not runtime.healthy


@pytest.mark.parametrize("entry", ["call", "maintain", "renew", "next_maintenance_delay",
                                  "market_binding_view", "account_binding_view",
                                  "authenticated_peer_for_binding"])
def test_business_entry_observes_external_unknown(entry):
    calls = []
    runtime = setup_runtime(guard=lambda: True)
    populate(runtime, calls)
    runtime._rpc = lambda *args, **kwargs: calls.append("rpc")
    with pytest.raises(ServiceRuntimeError):
        invoke_entry(runtime, entry)
    assert runtime.state == "close_failed" and calls == []


@pytest.mark.parametrize("phase", ["guard", "unregister"])
@pytest.mark.parametrize("mutation", ["replace_binding", "append_binding", "replace_transport", "generation"])
def test_mutated_cleanup_resources_stop_before_transport_close(phase, mutation):
    calls = []
    runtime = None
    fired = False
    def mutate():
        nonlocal fired
        if fired:
            return
        fired = True
        if mutation == "replace_binding":
            runtime._bindings[0] = replace(runtime._bindings[0], active=False)
        elif mutation == "append_binding":
            runtime._bindings.append(make_binding("late"))
        elif mutation == "replace_transport":
            runtime._transports[("one", "profile")] = Transport(calls, "replacement")
        else:
            runtime._registration_failure_generation[("one", "profile")] = 1
    def guard():
        if phase == "guard":
            mutate()
        return False
    runtime = setup_runtime(guard=guard)
    first, second, t1, t2 = populate(runtime, calls)
    def unregister(binding):
        calls.append(("unregister", binding.registration_id))
        if phase == "unregister":
            mutate()
        return True
    runtime._unregister_binding = unregister
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert runtime.state == "close_failed" and not any(c[0] == "close" for c in calls)
    assert runtime._transports[("two", "profile")] is t2
    assert runtime._cleanup_evidence is not None
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert not any(c[0] == "close" for c in calls)


def test_binding_created_during_transport_close_stops_later_transports():
    calls = []
    runtime = setup_runtime()
    first, second, t1, t2 = populate(runtime, calls)
    runtime._bindings = []
    def close_first():
        calls.append(("close", "one"))
        runtime._bindings.append(first)
    t1.close = close_first
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert runtime.state == "close_failed" and calls == [("close", "one")]
    assert runtime.bindings == (first,) and runtime._transports[("two", "profile")] is t2


@pytest.mark.parametrize("notification_error", [None, RuntimeError("notification failed"), KeyboardInterrupt()])
def test_unknown_cleanup_remains_rooted_when_caller_drops_runtime(notification_error):
    calls, notifications, holder = [], [], {}

    def notify():
        notifications.append(any(root is holder["runtime"]()
                                 for root in service_runtime._RAW_STICKY_CLEANUP_ROOTS.values()))
        if notification_error is not None:
            raise notification_error

    def exercise():
        runtime = setup_runtime(notify=notify)
        holder["runtime"] = weakref.ref(runtime)
        first, second, transport, other = populate(runtime, calls)
        runtime._unregister_binding = lambda binding: calls.append(binding.registration_id) or False
        with pytest.raises(ServiceRuntimeError):
            runtime.close()
        assert runtime.state == "close_failed"
        assert sum(root is runtime for root in service_runtime._RAW_STICKY_CLEANUP_ROOTS.values()) == 1
        with pytest.raises(ServiceRuntimeError):
            runtime.close()
        assert sum(root is runtime for root in service_runtime._RAW_STICKY_CLEANUP_ROOTS.values()) == 1
        assert runtime.bindings == (first, second)
        assert runtime._transports[("two", "profile")] is other
        return weakref.ref(runtime), weakref.ref(transport)

    runtime_ref, transport_ref = exercise()
    gc.collect()
    assert runtime_ref() is not None and transport_ref() is not None
    assert runtime_ref()._transports[("one", "profile")] is transport_ref()
    assert notifications == [True] and calls == ["reg-one"]


def test_confirmed_cleanup_does_not_create_a_process_root():
    calls = []

    def exercise():
        runtime = setup_runtime()
        _, _, transport, _ = populate(runtime, calls)
        runtime._unregister_binding = lambda binding: True
        runtime.close()
        assert runtime.state == "closed"
        assert not any(root is runtime for root in service_runtime._RAW_STICKY_CLEANUP_ROOTS.values())
        return weakref.ref(runtime), weakref.ref(transport)

    runtime_ref, transport_ref = exercise()
    gc.collect()
    assert runtime_ref() is None and transport_ref() is None
    assert calls == [("close", "one"), ("close", "two")]


def test_default_startup_preserves_cleanup_exception_precedence():
    runtime = setup_runtime(strict=False)
    startup_error = KeyboardInterrupt()
    cleanup_error = RuntimeError("cleanup failed")

    def fail_start(*args, **kwargs):
        raise startup_error

    def fail_cleanup(**kwargs):
        raise cleanup_error

    runtime._register = fail_start
    runtime._release = fail_cleanup
    with pytest.raises(RuntimeError) as caught:
        runtime.start()
    assert caught.value is cleanup_error


def interrupt_before_line(method, fragment, action, *, occurrence=0):
    lines, first = inspect.getsourcelines(method)
    matches = [first + index for index, line in enumerate(lines) if fragment in line]
    assert len(matches) > occurrence
    previous_trace, triggered = sys.gettrace(), []

    def trace(frame, event, arg):
        if frame.f_code is method.__func__.__code__ and event == "line" and frame.f_lineno == matches[occurrence]:
            triggered.append(True)
            sys.settrace(None)
            raise KeyboardInterrupt()
        return trace

    try:
        sys.settrace(trace)
        with pytest.raises(KeyboardInterrupt):
            action()
    finally:
        sys.settrace(previous_trace)
    assert triggered == [True]


def reenter_before_line(method, fragment, action, reenter):
    lines, first = inspect.getsourcelines(method)
    matches = [first + index for index, line in enumerate(lines) if fragment in line]
    assert len(matches) == 1
    previous_trace, triggered = sys.gettrace(), []

    def trace(frame, event, arg):
        if frame.f_code is method.__func__.__code__ and event == "line" and frame.f_lineno == matches[0]:
            triggered.append(True)
            sys.settrace(None)
            reenter()
        return trace

    try:
        sys.settrace(trace)
        result = action()
    finally:
        sys.settrace(previous_trace)
    assert triggered == [True]
    return result


def test_sticky_root_insertion_allows_same_thread_signal_reentry_without_duplicates():
    notices = []
    runtime = setup_runtime(notify=lambda: notices.append(True))
    reenter_before_line(runtime._set_cleanup_sticky, "_RAW_STICKY_CLEANUP_ROOTS.setdefault",
                        lambda: runtime._set_cleanup_sticky("outer"),
                        lambda: runtime._set_cleanup_sticky("inner"))
    assert runtime._has_sticky_cleanup() and runtime.state == "close_failed"
    assert sum(root is runtime for root in service_runtime._RAW_STICKY_CLEANUP_ROOTS.values()) == 1
    assert notices == [True] and runtime.cleanup_failures == 1
    assert {runtime._cleanup_evidence[0], *runtime._cleanup_reentrant_evidence} == {("outer",), ("inner",)}
    with pytest.raises(ServiceRuntimeError):
        runtime.close()


@pytest.mark.parametrize("fragment", [
    'elected = self._cleanup_latch.setdefault("owner", candidate)',
    'self._state = "close_failed"',
    'self._cleanup_failures = max(',
    'self._cleanup_evidence = elected[2]',
    'if self._cleanup_latch.setdefault("notification", notification) is notification:',
])
def test_sticky_evidence_and_notification_are_not_duplicated_by_same_thread_reentry(fragment):
    notices = []
    runtime = setup_runtime(notify=lambda: notices.append(True))
    reenter_before_line(runtime._set_cleanup_sticky, fragment,
                        lambda: runtime._set_cleanup_sticky("outer"),
                        lambda: runtime._set_cleanup_sticky("inner"))
    assert runtime._has_sticky_cleanup() and runtime.state == "close_failed"
    assert runtime.cleanup_failures == 1 and notices == [True]
    assert sum(root is runtime for root in service_runtime._RAW_STICKY_CLEANUP_ROOTS.values()) == 1
    assert {runtime._cleanup_evidence[0], *runtime._cleanup_reentrant_evidence} == {("outer",), ("inner",)}


@pytest.mark.parametrize("fragment", [
    'self._state = "close_failed"',
    'self._cleanup_failures = max(',
    'self._cleanup_evidence = elected[2]',
    'if self._cleanup_latch.setdefault("notification", notification) is notification:',
])
def test_interrupted_sticky_initializer_keeps_first_evidence_and_can_finish(fragment):
    notices = []
    runtime = setup_runtime(notify=lambda: notices.append(True))
    interrupt_before_line(runtime._set_cleanup_sticky, fragment,
                          lambda: runtime._set_cleanup_sticky("first"))
    assert runtime._has_sticky_cleanup()
    assert service_runtime._RAW_STICKY_CLEANUP_ROOTS.get(id(runtime)) is runtime
    runtime._set_cleanup_sticky("later")
    assert runtime._cleanup_evidence[0] == ("first",)
    assert ("later",) in runtime._cleanup_reentrant_evidence
    assert runtime.cleanup_failures == 1 and notices == [True]


def test_sticky_root_lookup_allows_same_thread_signal_reentry():
    notices = []
    runtime = setup_runtime(notify=lambda: notices.append(True))
    interrupt_before_line(runtime._set_cleanup_sticky, "self._cleanup_sticky = True",
                          lambda: runtime._set_cleanup_sticky("unknown"))
    assert not runtime._cleanup_sticky
    assert reenter_before_line(runtime._has_sticky_cleanup, "return _RAW_STICKY_CLEANUP_ROOTS.get",
                               runtime._has_sticky_cleanup,
                               lambda: runtime._set_cleanup_sticky("inner"))
    assert sum(root is runtime for root in service_runtime._RAW_STICKY_CLEANUP_ROOTS.values()) == 1
    assert notices == [True]
    with pytest.raises(ServiceRuntimeError):
        runtime.close()


@pytest.mark.parametrize("fragment,occurrence", [
    ("self._strict_verify_resources(snapshot)", 0),
    ("self._strict_guard(binding, transport)", 1),
    ("self._bindings = [item for item in self._bindings if item is not binding]", 0),
    ("self._registration_lineage.pop(id(binding), None)", 0),
])
def test_interrupt_after_unregister_never_repeats_cleanup(fragment, occurrence):
    calls = []
    runtime = setup_runtime()
    first, second, transport, other = populate(runtime, calls)
    runtime._unregister_binding = lambda binding: calls.append(("unregister", binding.registration_id)) or True
    interrupt_before_line(runtime._strict_unregister_binding, fragment, runtime.close, occurrence=occurrence)
    assert runtime._has_sticky_cleanup()
    assert any(root is runtime for root in service_runtime._RAW_STICKY_CLEANUP_ROOTS.values())
    assert calls == [("unregister", "reg-one")]
    assert runtime._transports[("one", "profile")] is transport
    assert runtime._transports[("two", "profile")] is other
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert calls == [("unregister", "reg-one")]


@pytest.mark.parametrize("fragment,occurrence", [
    ("self._strict_verify_resources(snapshot)", 0),
    ("self._strict_guard(transport)", 1),
    ("del self._transports[key]", 0),
])
def test_interrupt_after_transport_close_never_repeats_cleanup(fragment, occurrence):
    calls = []
    runtime = setup_runtime()
    first, second, transport, other = populate(runtime, calls)
    runtime._bindings = []
    runtime._registration_lineage = {}
    interrupt_before_line(runtime._release, fragment, runtime.close, occurrence=occurrence)
    assert runtime._has_sticky_cleanup()
    assert any(root is runtime for root in service_runtime._RAW_STICKY_CLEANUP_ROOTS.values())
    assert calls == [("close", "one")]
    assert runtime._transports[("one", "profile")] is transport
    assert runtime._transports[("two", "profile")] is other
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert calls == [("close", "one")]


def test_interrupted_sticky_flag_write_still_roots_and_blocks_cleanup():
    calls = []
    runtime = setup_runtime()
    populate(runtime, calls)
    interrupt_before_line(runtime._set_cleanup_sticky, "self._cleanup_sticky = True",
                          lambda: runtime._set_cleanup_sticky("unknown"))
    assert runtime._has_sticky_cleanup()
    assert any(root is runtime for root in service_runtime._RAW_STICKY_CLEANUP_ROOTS.values())
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert calls == []


def _register_args(runtime):
    binding = runtime._config["clients"]["gateway"]["settings"]["bindings"][0]
    capabilities = [{"instance_id": "gateway-a", "port": "service.account_read",
                     "operations": ["account.snapshot"]}]
    return binding, SCOPE, "service.account_read", capabilities


@pytest.mark.parametrize("fragment", ["scope = attempt_scope", 'self._registration_in_progress["handle_recorded"] = True'])
def test_interrupted_successful_register_retains_unknown_transport(fragment):
    calls = []
    runtime = setup_runtime()
    transport = Transport(calls, "one")
    runtime._transports[("account-service", "account-zmq")] = transport
    runtime._state = "starting"

    def rpc(*args, **kwargs):
        calls.append("register")
        kwargs["timing"]["request_started"] = 1
        return {"registration_id": "reg", "service_epoch": "epoch"}, 1

    runtime._rpc = rpc
    args = _register_args(runtime)
    interrupt_before_line(runtime._register_port_impl, fragment,
                          lambda: runtime._register_port(*args, timeout=1, required=False,
                                                         heartbeat_interval_seconds=None))
    assert runtime._has_sticky_cleanup()
    assert runtime._transports[("account-service", "account-zmq")] is transport
    assert runtime._unknown_registration_count == 1
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert calls == ["register"]


@pytest.mark.parametrize("entry", ["close", "recover", "start"])
def test_reentrant_cleanup_during_register_cannot_close_transport(entry):
    calls = []
    runtime = setup_runtime()
    transport = Transport(calls, "one")
    runtime._transports[("account-service", "account-zmq")] = transport
    runtime._state = "starting"

    def rpc(*args, **kwargs):
        calls.append("register")
        kwargs["timing"]["request_started"] = 1
        with pytest.raises(ServiceRuntimeError):
            getattr(runtime, entry)()
        raise TimeoutError("unknown")

    runtime._rpc = rpc
    with pytest.raises(TimeoutError):
        runtime._register_port(*_register_args(runtime), timeout=1, required=False,
                               heartbeat_interval_seconds=None)
    assert runtime._has_sticky_cleanup() and runtime._transports[("account-service", "account-zmq")] is transport
    assert calls == ["register"] and runtime._unknown_registration_count == 1


def test_startup_rollback_does_not_close_after_interrupted_register():
    calls = []
    runtime = setup_runtime()
    transport = Transport(calls, "one")
    runtime._transports[("account-service", "account-zmq")] = transport

    def rpc(*args, **kwargs):
        calls.append("register")
        kwargs["timing"]["request_started"] = 1
        return {"registration_id": "reg", "service_epoch": "epoch"}, 1

    runtime._rpc = rpc
    runtime._register = lambda binding, **kwargs: runtime._register_port(
        *_register_args(runtime), timeout=1, required=False, heartbeat_interval_seconds=None
    )
    interrupt_before_line(runtime._register_port_impl, "scope = attempt_scope", runtime.start)
    assert runtime._has_sticky_cleanup()
    assert runtime._transports[("account-service", "account-zmq")] is transport
    assert runtime._unknown_registration_count == 1
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert calls == ["register"]


def test_interrupted_placeholder_replacement_without_handle_is_unknown():
    calls = []
    runtime = setup_runtime()
    transport = Transport(calls, "one")
    runtime._transports[("account-service", "account-zmq")] = transport
    runtime._state = "starting"

    def rpc(*args, **kwargs):
        calls.append("register")
        kwargs["timing"]["request_started"] = 1
        return {"registration_id": "reg", "service_epoch": "epoch",
                "policy_version": "p", "requirements_version": "r",
                "operations": ["account.snapshot"], "expires_at": 100,
                "lease_remaining_seconds": 30}, 1

    def clock():
        # Simulate in-process loss of the saved placeholder after registration.
        runtime._bindings.clear()
        return 2

    runtime._rpc = rpc
    runtime._monotonic_now = clock
    interrupt_before_line(runtime._register_port_impl, "self._bindings.append(final_binding)",
                          lambda: runtime._register_port(*_register_args(runtime), timeout=1,
                                                         required=False, heartbeat_interval_seconds=None))
    assert runtime._has_sticky_cleanup() and runtime._unknown_registration_count == 1
    assert runtime._transports[("account-service", "account-zmq")] is transport
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert calls == ["register"]


@pytest.mark.parametrize("proof", ["request_not_sent", "server_rejected"])
def test_explicit_non_registration_does_not_create_unknown_root(proof):
    calls = []
    runtime = setup_runtime()
    transport = Transport(calls, "one")
    runtime._transports[("account-service", "account-zmq")] = transport
    runtime._state = "starting"

    def rpc(*args, **kwargs):
        kwargs["timing"][proof] = True
        raise ServiceRuntimeError("REGISTRATION_DENIED")

    runtime._rpc = rpc
    with pytest.raises(ServiceRuntimeError):
        runtime._register_port(*_register_args(runtime), timeout=1, required=False,
                               heartbeat_interval_seconds=None)
    assert not runtime._has_sticky_cleanup() and runtime._unknown_registration_count == 0
    runtime.close()
    assert calls == [("close", "one")]


@pytest.mark.parametrize("phase", ["scope", "peer", "factory", "discover"])
@pytest.mark.parametrize("entry", ["start", "close", "recover"])
def test_startup_callback_reentry_cannot_close_or_continue(phase, entry):
    calls = []
    runtime = setup_runtime()
    transport = Transport(calls, "one")

    def attempt_reentry():
        with pytest.raises(ServiceRuntimeError):
            getattr(runtime, entry)()

    def scope_resolver(_scope_ref):
        calls.append("scope")
        if phase == "scope":
            attempt_reentry()
        return SCOPE

    def peer_resolver(_service):
        calls.append("peer")
        if phase == "peer":
            attempt_reentry()
        return PEER

    def factory(*_):
        calls.append("factory")
        if phase == "factory":
            attempt_reentry()
        return transport

    def rpc(_service, _transport, _scope, operation, *_args, **_kwargs):
        calls.append(operation)
        assert operation == "service.discover"
        if phase == "discover":
            attempt_reentry()
        return {}

    runtime._scope_resolver = scope_resolver
    runtime._peer_resolver = peer_resolver
    runtime._factory = factory
    runtime._rpc = rpc
    with pytest.raises(ServiceRuntimeError):
        runtime.start()
    assert runtime._has_sticky_cleanup() and runtime.state == "close_failed"
    assert not any(isinstance(call, tuple) and call[0] == "close" for call in calls)
    assert "service.register" not in calls
    if phase in {"factory", "discover"}:
        assert runtime._transports[("account-service", "account-zmq")] is transport
    else:
        assert not runtime._transports
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert not any(isinstance(call, tuple) and call[0] == "close" for call in calls)


def test_unknown_guard_result_then_interrupt_is_rooted():
    calls = []
    runtime = setup_runtime(guard=lambda: True)
    first, second, transport, other = populate(runtime, calls)
    interrupt_before_line(runtime._strict_guard, "self._strict_verify_resources(snapshot)", runtime.close)
    assert runtime._has_sticky_cleanup()
    assert any(root is runtime for root in service_runtime._RAW_STICKY_CLEANUP_ROOTS.values())
    assert runtime.bindings == (first, second)
    assert runtime._transports[("one", "profile")] is transport
    assert runtime._transports[("two", "profile")] is other
    with pytest.raises(ServiceRuntimeError):
        runtime.close()
    assert calls == []
