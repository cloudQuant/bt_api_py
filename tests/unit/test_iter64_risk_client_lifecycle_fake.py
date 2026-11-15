"""Bounded synthetic runtimes only: no owner thread, transport, or account."""

import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

import bt_api_py.risk_service_client as risk_client_module
from bt_api_py.risk_service_client import RiskServiceClient
from bt_api_py.service_runtime import ServiceRuntimeError, ServiceRuntimeRequestNotSentError
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime
from tests.unit.test_iter64_risk_service_client import answer, risk_route, snapshot


def make_runtime(monkeypatch, route, state="new"):
    runtime = ThreadedServiceClientRuntime(lambda: None, require_unregister_ack=True)
    box = {"value": snapshot(route, state=state, healthy=state == "running")}
    calls = []
    monkeypatch.setattr(runtime, "status", lambda: box["value"])
    monkeypatch.setattr(runtime, "bounded_status", lambda **_: runtime.status())
    monkeypatch.setattr(runtime, "cleanup_observation", lambda **_: (runtime.status(),
                                                                    runtime._thread is not None))

    def start(**_kwargs):
        calls.append("start")
        box["value"] = snapshot(route)
        return box["value"]

    def close(**_kwargs):
        calls.append("close")
        box["value"] = snapshot(route, state="closed", healthy=False)
        return box["value"]

    monkeypatch.setattr(runtime, "start", start)
    monkeypatch.setattr(runtime, "start_bounded_once", lambda **kw: runtime.start(
        timeout_seconds=kw["timeout_seconds"]))
    monkeypatch.setattr(runtime, "close", close)
    monkeypatch.setattr(runtime, "close_once_if_state", lambda **kw: runtime.close(
        timeout_seconds=kw["timeout_seconds"]))
    return runtime, box, calls


def replacement(monkeypatch):
    route = risk_route()
    old, old_box, old_calls = make_runtime(monkeypatch, route, "failed")
    candidate, candidate_box, candidate_calls = make_runtime(monkeypatch, route)
    client = RiskServiceClient(old, route=route, monotonic_clock=lambda: 100.0)
    client._factory = lambda: candidate
    return client, old, old_box, old_calls, candidate, candidate_box, candidate_calls


def test_reentrant_close_during_candidate_start_cannot_install_it(monkeypatch):
    client, old, _, _, candidate, box, calls = replacement(monkeypatch)

    def start(**_kwargs):
        calls.append("start")
        with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
            client.close(timeout_seconds=1)
        box["value"] = snapshot(client.route)
        return box["value"]

    monkeypatch.setattr(candidate, "start", start)
    assert client.reconnect(timeout_seconds=1) is False
    assert client._runtime is old and client._pending_runtime is candidate
    assert not client.available and calls == ["start"]
    assert client.reconnect(timeout_seconds=1) is False
    assert calls == ["start"]
    assert client.close(timeout_seconds=1).state in ("closed", "failed")
    assert client._pending_runtime is None and calls == ["start", "close"]
    assert not client.available


def test_close_during_blocked_start_is_bounded_and_candidate_retained(monkeypatch):
    client, old, _, _, candidate, box, calls = replacement(monkeypatch)
    entered, resume = threading.Event(), threading.Event()

    def start(**_kwargs):
        calls.append("start")
        entered.set()
        assert resume.wait(2)
        box["value"] = snapshot(client.route)
        return box["value"]

    monkeypatch.setattr(candidate, "start", start)
    results = []
    thread = threading.Thread(target=lambda: results.append(client.reconnect(timeout_seconds=1)))
    thread.start()
    assert entered.wait(2)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)
    assert not client.available and calls == ["start"]
    resume.set()
    thread.join(timeout=2)
    assert not thread.is_alive() and results == [False]
    assert client._runtime is old and client._pending_runtime is candidate
    assert client.close(timeout_seconds=1).state in ("closed", "failed")
    assert calls == ["start", "close"] and not client.available


def test_factory_reentrant_close_keeps_returned_candidate(monkeypatch):
    client, old, _, _, candidate, box, calls = replacement(monkeypatch)
    box["value"] = replace(snapshot(client.route, state="new", healthy=False),
                           bindings=(), identity=None, run_id=None, strict_unregister_ack=None)

    def factory():
        with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
            client.close(timeout_seconds=1)
        return candidate

    client._factory = factory
    assert not client.reconnect(timeout_seconds=1)
    assert client._runtime is old and client._pending_runtime is candidate
    assert calls == [] and not client.available
    assert client.close(timeout_seconds=1).state in ("closed", "failed")
    assert calls == ["close"]


def test_start_reentrant_close_requires_explicit_cleanup(monkeypatch):
    route = risk_route()
    runtime, box, calls = make_runtime(monkeypatch, route)
    client = RiskServiceClient(runtime, route=route)

    def start(**_kwargs):
        calls.append("start")
        with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
            client.close(timeout_seconds=1)
        box["value"] = snapshot(route)
        return box["value"]

    monkeypatch.setattr(runtime, "start", start)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.start(timeout_seconds=1)
    assert client._runtime is runtime and not client.available
    assert client.close(timeout_seconds=1).state == "closed"
    assert calls == ["start", "close"]


def test_uncertain_candidate_cleanup_is_retained_without_repeat(monkeypatch):
    client, old, _, _, candidate, box, calls = replacement(monkeypatch)

    def start(**_kwargs):
        box["value"] = snapshot(client.route)
        raise RuntimeError()

    monkeypatch.setattr(candidate, "start", start)

    def close(**_kwargs):
        calls.append("close")
        box["value"] = replace(snapshot(client.route, state="closed", healthy=False),
                               unregister_uncertainties=1)
        return box["value"]

    monkeypatch.setattr(candidate, "close", close)
    assert not client.reconnect(timeout_seconds=1)
    assert client._runtime is old and client._pending_runtime is candidate
    assert calls == ["close"] and not client.available
    assert not client.reconnect(timeout_seconds=1)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)
    assert calls == ["close"] and client._pending_runtime is candidate


def test_uncertain_original_cleanup_is_retained_without_repeat(monkeypatch):
    client, old, _, old_calls, candidate, _, calls = replacement(monkeypatch)
    monkeypatch.setattr(old, "status", lambda: snapshot(client.route))

    def close(**_kwargs):
        old_calls.append("close")
        raise RuntimeError("uncertain")

    monkeypatch.setattr(old, "close", close)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)
    assert client._runtime is old and client._runtime_cleanup_uncertain
    assert not client.reconnect(timeout_seconds=1)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)
    assert old_calls == ["close"] and calls == [] and client._runtime is old


def test_successful_close_allows_later_explicit_reconnect(monkeypatch):
    client, old, old_box, _, candidate, _, calls = replacement(monkeypatch)
    old_box["value"] = snapshot(client.route)
    assert client.close(timeout_seconds=1).state == "closed"
    assert not client.available
    assert client.reconnect(timeout_seconds=1)
    assert client._runtime is candidate and calls == ["start"] and client.available


def test_status_reentry_cannot_return_available_after_successful_close(monkeypatch):
    route = risk_route()
    runtime, box, _calls = make_runtime(monkeypatch, route, "running")
    client = RiskServiceClient(runtime, route=route)
    entered = []

    def status():
        if not entered:
            entered.append(True)
            assert client.close(timeout_seconds=1).state == "closed"
            return snapshot(route)
        return box["value"]

    monkeypatch.setattr(runtime, "status", status)
    assert not client.available
    assert not client.available


def test_failed_start_must_be_explicitly_closed_before_reconnect(monkeypatch):
    route = risk_route()
    runtime, box, calls = make_runtime(monkeypatch, route)
    client = RiskServiceClient(runtime, route=route)

    def start(**_kwargs):
        calls.append("start")
        box["value"] = snapshot(route)
        raise ThreadedRuntimeError("START_TIMEOUT_CLEANUP_PENDING")

    monkeypatch.setattr(runtime, "start", start)
    with pytest.raises(ThreadedRuntimeError, match="START_TIMEOUT_CLEANUP_PENDING"):
        client.start(timeout_seconds=1)
    assert client._runtime is runtime and not client.available
    assert not client.reconnect(timeout_seconds=1)
    assert calls == ["start"]
    assert client.close(timeout_seconds=1).state == "closed"
    assert calls == ["start", "close"]


def test_start_failure_with_owner_cleanup_unknown_is_never_retried(monkeypatch):
    route = risk_route()
    runtime, box, calls = make_runtime(monkeypatch, route)
    client = RiskServiceClient(runtime, route=route)

    def start(**_kwargs):
        calls.append("start")
        # The synthetic owner already tried and failed cleanup; close() would
        # increment the real wrapper's retry token and repeat that operation.
        box["value"] = replace(snapshot(route, state="close_failed", healthy=False),
                               thread_alive=True, error_code="CLEANUP_FAILED")
        raise ThreadedRuntimeError("START_FAILED_CLEANUP_PENDING")

    monkeypatch.setattr(runtime, "start", start)
    with pytest.raises(ThreadedRuntimeError, match="START_FAILED_CLEANUP_PENDING"):
        client.start(timeout_seconds=1)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)
    assert calls == ["start"] and client._runtime is runtime
    assert client._runtime_cleanup_uncertain and not client.available


def test_start_failure_already_clean_is_confirmed_without_second_close(monkeypatch):
    route = risk_route()
    runtime, box, calls = make_runtime(monkeypatch, route)
    client = RiskServiceClient(runtime, route=route)

    def start(**_kwargs):
        calls.append("start")
        box["value"] = snapshot(route, state="failed", healthy=False)
        raise ThreadedRuntimeError("START_FAILED")

    monkeypatch.setattr(runtime, "start", start)
    with pytest.raises(ThreadedRuntimeError, match="START_FAILED"):
        client.start(timeout_seconds=1)
    assert client.close(timeout_seconds=1).state == "failed"
    assert calls == ["start"] and not client._runtime_cleanup_uncertain


def test_failed_snapshot_with_active_binding_is_not_clean(monkeypatch):
    route = risk_route()
    runtime, box, calls = make_runtime(monkeypatch, route)
    client = RiskServiceClient(runtime, route=route)
    active_bindings = snapshot(route).bindings
    box["value"] = replace(snapshot(route, state="failed", healthy=False), bindings=active_bindings)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)
    assert calls == [] and client._runtime is runtime and client._runtime_cleanup_uncertain
    assert not client.available


def test_owner_failure_between_snapshot_and_close_does_not_trigger_retry(monkeypatch):
    route = risk_route()
    runtime = ThreadedServiceClientRuntime(lambda: pytest.fail("no owner"),
                                           require_unregister_ack=True)
    runtime._state = "running"  # Synthetic state; no owner thread was started.
    client = RiskServiceClient(runtime, route=route)

    def status():
        # Simulate the owner entering uncertain automatic cleanup immediately
        # after the observed running snapshot and before atomic close.
        runtime._state = "close_failed"
        return snapshot(route)

    monkeypatch.setattr(runtime, "cleanup_observation", lambda **_: (status(), False))
    monkeypatch.setattr(runtime, "close", lambda **_: pytest.fail("must not retry"))
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)
    assert runtime._cleanup_retry == 0 and client._runtime is runtime
    assert client._runtime_cleanup_uncertain and not client.available


def test_atomic_close_start_can_never_increment_retry_twice():
    runtime = ThreadedServiceClientRuntime(lambda: pytest.fail("no owner"),
                                           require_unregister_ack=True)
    runtime._state = "running"  # No thread, socket or factory is started.
    assert runtime.close_once_if_state(expected_state="running", timeout_seconds=1).state == "stopping"
    assert runtime._cleanup_retry == 1
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_STATE_UNCERTAIN"):
        runtime.close_once_if_state(expected_state="running", timeout_seconds=1)
    assert runtime._cleanup_retry == 1


def test_atomic_close_lock_timeout_has_no_cleanup_side_effect():
    runtime = ThreadedServiceClientRuntime(lambda: pytest.fail("no owner"),
                                           require_unregister_ack=True)
    runtime._state = "running"
    entered, resume = threading.Event(), threading.Event()

    def hold_condition():
        with runtime._condition:
            entered.set()
            assert resume.wait(2)

    thread = threading.Thread(target=hold_condition)
    thread.start()
    assert entered.wait(2)
    try:
        with pytest.raises(ThreadedRuntimeError, match="CLOSE_TIMEOUT_BEFORE_START"):
            runtime.close_once_if_state(expected_state="running", timeout_seconds=0.02)
        assert not runtime._stop and runtime._cleanup_retry == 0
    finally:
        resume.set()
        thread.join(timeout=2)
    assert not thread.is_alive()


@pytest.mark.parametrize("timeout", [0, -1, False, float("inf"), float("nan"), 10**10000, 5e-324],
                         ids=["zero", "negative", "bool", "infinity", "nan", "huge", "tiny"])
def test_cleanup_deadlines_reject_invalid_values_before_touching_runtime(timeout):
    runtime = ThreadedServiceClientRuntime(lambda: pytest.fail("no owner"),
                                           require_unregister_ack=True)
    client = RiskServiceClient(runtime, route=risk_route())
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_NOT_STARTED"):
        client.close(timeout_seconds=timeout)
    assert runtime._state == "new" and runtime._thread is None
    assert not runtime._stop and runtime._cleanup_retry == 0
    with pytest.raises(ValueError):
        runtime.cleanup_observation(timeout_seconds=timeout)
    with pytest.raises(ValueError):
        runtime.close_once_if_state(expected_state="new", timeout_seconds=timeout)


def test_reconnect_rejects_inconsistent_old_cleanup_without_retry(monkeypatch):
    client, old, _, calls, candidate, _, candidate_calls = replacement(monkeypatch)
    # The close claim disagrees with the owner's still-failed snapshot.
    def close(**_kwargs):
        calls.append("close")
        return snapshot(client.route, state="closed", healthy=False)

    monkeypatch.setattr(old, "close", close)
    count = []

    def status():
        count.append(True)
        value = snapshot(client.route, state="failed", healthy=False)
        # Registration, reconnect's snapshot and cleanup preflight precede
        # the second cleanup snapshot.
        return value if len(count) < 4 else replace(value, unregister_uncertainties=1)

    monkeypatch.setattr(old, "status", status)
    assert not client.reconnect(timeout_seconds=1)
    assert client._runtime is old and client._runtime_cleanup_uncertain
    assert not client.reconnect(timeout_seconds=1)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)
    assert calls == [] and candidate_calls == []


def test_nested_close_does_not_poison_confirmed_outer_cleanup(monkeypatch):
    route = risk_route()
    runtime, box, calls = make_runtime(monkeypatch, route, "running")
    client = RiskServiceClient(runtime, route=route)

    def close(**_kwargs):
        calls.append("close")
        with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
            client.close(timeout_seconds=1)
        box["value"] = snapshot(route, state="closed", healthy=False)
        return box["value"]

    monkeypatch.setattr(runtime, "close", close)
    assert client.close(timeout_seconds=1).state == "closed"
    assert client._runtime is runtime and not client._runtime_cleanup_uncertain
    assert client.close(timeout_seconds=1).state == "closed"
    assert calls == ["close"] and not client.available


def test_proven_not_started_close_may_be_retried(monkeypatch):
    client, old, box, calls, candidate, _, candidate_calls = replacement(monkeypatch)
    box["value"] = snapshot(client.route)

    def close(**_kwargs):
        calls.append("close")
        if len(calls) == 1:
            raise ThreadedRuntimeError("CLOSE_TIMEOUT_BEFORE_START")
        box["value"] = snapshot(client.route, state="closed", healthy=False)
        return box["value"]

    monkeypatch.setattr(old, "close", close)
    with pytest.raises(ThreadedRuntimeError, match="CLOSE_TIMEOUT_BEFORE_START"):
        client.close(timeout_seconds=1)
    assert not client._runtime_cleanup_uncertain and calls == ["close"]
    assert client.close(timeout_seconds=1).state == "closed"
    assert client.reconnect(timeout_seconds=1)
    assert client._runtime is candidate and calls == ["close", "close"]
    assert candidate_calls == ["start"]


def test_not_started_error_from_status_does_not_prove_close_not_started(monkeypatch):
    client, old, box, calls, candidate, _, candidate_calls = replacement(monkeypatch)
    box["value"] = snapshot(client.route)

    def close(**_kwargs):
        calls.append("close")
        box["value"] = snapshot(client.route, state="closed", healthy=False)
        return box["value"]

    def status():
        if calls:
            raise ThreadedRuntimeError("CLOSE_TIMEOUT_BEFORE_START")
        return box["value"]

    monkeypatch.setattr(old, "close", close)
    monkeypatch.setattr(old, "status", status)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)
    assert client._runtime_cleanup_uncertain and client._runtime is old
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)
    assert calls == ["close"] and candidate_calls == []


def test_close_unstarted_real_wrapper_needs_no_unregister_ack():
    # This wrapper is never started: its factory, owner thread and transport
    # are never entered, and there was no registration to unregister.
    runtime = ThreadedServiceClientRuntime(
        lambda: pytest.fail("unstarted factory must not run"),
        require_unregister_ack=True,
    )
    client = RiskServiceClient(runtime, route=risk_route())
    closed = client.close(timeout_seconds=1)
    assert closed.state == "closed" and closed.strict_unregister_ack is None
    assert client.close(timeout_seconds=1) == closed
    assert not client.available and runtime._thread is None


def test_never_started_close_can_explicitly_reconnect(monkeypatch):
    route = risk_route()
    runtime = ThreadedServiceClientRuntime(lambda: pytest.fail("old factory must not run"),
                                           require_unregister_ack=True)
    candidate, _, calls = make_runtime(monkeypatch, route)
    client = RiskServiceClient(runtime, route=route, monotonic_clock=lambda: 100.0)
    client._factory = lambda: candidate
    assert client.close(timeout_seconds=1).strict_unregister_ack is None
    assert client.reconnect(timeout_seconds=1)
    assert client._runtime is candidate and calls == ["start"] and client.available


def test_client_close_snapshot_lock_timeout_is_bounded_and_retryable():
    runtime = ThreadedServiceClientRuntime(lambda: pytest.fail("no owner"),
                                           require_unregister_ack=True)
    client = RiskServiceClient(runtime, route=risk_route())
    entered, resume = threading.Event(), threading.Event()

    def hold_condition():
        with runtime._condition:
            entered.set()
            assert resume.wait(2)

    thread = threading.Thread(target=hold_condition)
    thread.start()
    assert entered.wait(2)
    try:
        with pytest.raises(ThreadedRuntimeError, match="CLEANUP_NOT_STARTED"):
            client.close(timeout_seconds=0.02)
        assert runtime._state == "new" and runtime._cleanup_retry == 0
        assert not client._runtime_cleanup_uncertain
    finally:
        resume.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert client.close(timeout_seconds=1).state == "closed"


def test_reconnect_health_snapshot_lock_timeout_never_starts_owner():
    runtime = ThreadedServiceClientRuntime(lambda: pytest.fail("no owner"),
                                           require_unregister_ack=True)
    client = RiskServiceClient(runtime, route=risk_route())
    entered, resume = threading.Event(), threading.Event()

    def hold_condition():
        with runtime._condition:
            entered.set()
            assert resume.wait(2)

    thread = threading.Thread(target=hold_condition)
    thread.start()
    assert entered.wait(2)
    try:
        assert client.reconnect(timeout_seconds=0.02) is False
        assert runtime._state == "new" and runtime._thread is None
        assert not runtime._stop and runtime._cleanup_retry == 0
    finally:
        resume.set()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_bounded_start_lock_timeout_does_not_start_owner():
    runtime = ThreadedServiceClientRuntime(lambda: pytest.fail("no owner"),
                                           require_unregister_ack=True)
    entered, resume = threading.Event(), threading.Event()

    def hold_condition():
        with runtime._condition:
            entered.set()
            assert resume.wait(2)

    thread = threading.Thread(target=hold_condition)
    thread.start()
    assert entered.wait(2)
    try:
        with pytest.raises(ThreadedRuntimeError, match="START_TIMEOUT"):
            runtime.start_bounded_once(timeout_seconds=0.02)
        assert runtime._state == "new" and runtime._thread is None
        assert not runtime._stop and runtime._cleanup_retry == 0
    finally:
        resume.set()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_bounded_health_does_not_call_unbounded_injected_clock():
    calls = []

    def injected_clock():
        calls.append(True)
        raise AssertionError("must not execute arbitrary callback")

    runtime = ThreadedServiceClientRuntime(lambda: pytest.fail("no owner"),
                                           monotonic_clock=injected_clock,
                                           require_unregister_ack=True)
    runtime._state, runtime._healthy = "running", True
    result = runtime.bounded_status(timeout_seconds=1)
    assert result.state == "running" and not result.healthy and calls == []


def test_reconnect_deadline_spans_observations_and_factory(monkeypatch):
    client, old, _, old_calls, candidate, _, candidate_calls = replacement(monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(risk_client_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original_status = old.bounded_status
    original_observation = old.cleanup_observation

    def status(**kwargs):
        clock[0] += 0.1
        return original_status(**kwargs)

    def observation(**kwargs):
        clock[0] += 0.2
        return original_observation(**kwargs)

    def factory():
        clock[0] += 0.4
        return candidate

    monkeypatch.setattr(old, "bounded_status", status)
    monkeypatch.setattr(old, "cleanup_observation", observation)
    client._factory = factory
    assert not client.reconnect(timeout_seconds=1)
    assert client._runtime is old and client._pending_runtime is candidate
    assert not client.available and candidate_calls == [] and old_calls == []


def test_reconnect_expired_during_candidate_start_keeps_candidate(monkeypatch):
    client, old, _, _, candidate, box, calls = replacement(monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(risk_client_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def start(**_kwargs):
        calls.append("start")
        box["value"] = snapshot(client.route)
        clock[0] += 2.0
        return box["value"]

    monkeypatch.setattr(candidate, "start", start)
    assert not client.reconnect(timeout_seconds=1)
    assert client._runtime is old and client._pending_runtime is candidate
    assert calls == ["start"] and not client.available
    assert client._close_required and not client._pending_cleanup_uncertain


def test_reconnect_deadline_does_not_continue_after_candidate_cleanup(monkeypatch):
    route = risk_route()
    old, _, old_calls = make_runtime(monkeypatch, route, "failed")
    candidate, _, candidate_calls = make_runtime(monkeypatch, route, "running")
    client = RiskServiceClient(old, route=route, monotonic_clock=lambda: 100.0)
    client._pending_runtime = candidate
    clock = [0.0]
    monkeypatch.setattr(risk_client_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    for target, duration in ((candidate, 0.3), (old, 0.5)):
        original = target.cleanup_observation

        def observation(*, timeout_seconds, _original=original, _duration=duration):
            clock[0] += _duration
            return _original(timeout_seconds=timeout_seconds)

        monkeypatch.setattr(target, "cleanup_observation", observation)

    assert not client.reconnect(timeout_seconds=1)
    assert candidate_calls == ["close"] and old_calls == []
    assert client._pending_runtime is None and client._runtime is old
    assert not client._pending_cleanup_uncertain and not client._runtime_cleanup_uncertain


def test_timeout_after_close_does_not_retry_unconfirmed_cleanup(monkeypatch):
    runtime = ThreadedServiceClientRuntime(lambda: pytest.fail("no owner"),
                                           require_unregister_ack=True)
    client = RiskServiceClient(runtime, route=risk_route())
    original = runtime.cleanup_observation
    entered, resume = threading.Event(), threading.Event()
    observations, threads = [], []

    def hold_condition():
        with runtime._condition:
            entered.set()
            assert resume.wait(2)

    def observation(**kwargs):
        observations.append(True)
        if len(observations) == 2:
            thread = threading.Thread(target=hold_condition)
            threads.append(thread)
            thread.start()
            assert entered.wait(2)
        return original(**kwargs)

    monkeypatch.setattr(runtime, "cleanup_observation", observation)
    try:
        with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
            client.close(timeout_seconds=0.05)
        assert runtime._state == "closed" and client._runtime_cleanup_uncertain
    finally:
        resume.set()
        for thread in threads:
            thread.join(timeout=2)
            assert not thread.is_alive()
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)
    assert observations == [True, True] and client._runtime is runtime


def test_close_uses_one_timeout_budget_for_candidate_and_original(monkeypatch):
    route = risk_route()
    runtime, _, runtime_calls = make_runtime(monkeypatch, route, "running")
    candidate, _, candidate_calls = make_runtime(monkeypatch, route, "running")
    client = RiskServiceClient(runtime, route=route)
    client._pending_runtime = candidate
    clock = [0.0]
    monkeypatch.setattr(risk_client_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    for target, duration in ((candidate, 0.3), (runtime, 0.5)):
        original = target.cleanup_observation

        def observation(*, timeout_seconds, _original=original, _duration=duration):
            clock[0] += _duration
            return _original(timeout_seconds=timeout_seconds)

        monkeypatch.setattr(target, "cleanup_observation", observation)

    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_NOT_STARTED"):
        client.close(timeout_seconds=1)
    assert candidate_calls == ["close"] and runtime_calls == []
    assert client._pending_runtime is None and client._runtime is runtime
    assert not client._runtime_cleanup_uncertain


@pytest.mark.parametrize("state", ["failed", "running"])
def test_late_final_cleanup_observation_does_not_report_success(monkeypatch, state):
    route = risk_route()
    runtime, _, calls = make_runtime(monkeypatch, route, state)
    client = RiskServiceClient(runtime, route=route)
    clock = [0.0]
    monkeypatch.setattr(risk_client_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original = runtime.cleanup_observation
    observations = []

    def observation(*, timeout_seconds):
        observations.append(True)
        clock[0] += 0.2 if len(observations) == 1 else 0.9
        return original(timeout_seconds=timeout_seconds)

    monkeypatch.setattr(runtime, "cleanup_observation", observation)
    expected = "CLEANUP_NOT_STARTED" if state == "failed" else "CLEANUP_FAILED"
    with pytest.raises(ThreadedRuntimeError, match=expected):
        client.close(timeout_seconds=1)
    assert len(observations) == 2 and client._runtime is runtime
    assert calls == ([] if state == "failed" else ["close"])
    assert client._runtime_cleanup_uncertain is (state == "running")


def test_concurrent_close_does_not_poison_confirmed_cleanup(monkeypatch):
    route = risk_route()
    runtime, box, calls = make_runtime(monkeypatch, route, "running")
    client = RiskServiceClient(runtime, route=route)
    entered, resume = threading.Event(), threading.Event()

    def close(**_kwargs):
        calls.append("close")
        entered.set()
        assert resume.wait(2)
        box["value"] = snapshot(route, state="closed", healthy=False)
        return box["value"]

    monkeypatch.setattr(runtime, "close", close)
    result = []
    thread = threading.Thread(target=lambda: result.append(client.close(timeout_seconds=1)))
    thread.start()
    assert entered.wait(2)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)
    resume.set()
    thread.join(timeout=2)
    assert not thread.is_alive() and len(result) == 1 and result[0].state == "closed"
    assert calls == ["close"] and not client._runtime_cleanup_uncertain


def test_call_does_not_switch_to_reconnected_runtime_with_same_registration(monkeypatch):
    route = risk_route()
    old, _, _ = make_runtime(monkeypatch, route, "running")
    new, _, _ = make_runtime(monkeypatch, route)
    client = RiskServiceClient(old, route=route, monotonic_clock=lambda: 100.0)
    client._factory = lambda: new
    calls = []

    def call(*_args, **_kwargs):
        calls.append("old")
        assert client.close(timeout_seconds=1).state == "closed"
        assert client.reconnect(timeout_seconds=1)
        return answer("NOT_FOUND", key="prior")

    monkeypatch.setattr(old, "call", call)
    monkeypatch.setattr(new, "call", lambda *_args, **_kwargs: calls.append("new"))
    result = client.status("prior", timeout_seconds=1)
    assert result.outcome == "UNKNOWN" and calls == ["old"]


def test_runtime_change_before_call_is_unknown_even_if_registration_matches(monkeypatch):
    route = risk_route()
    old, _, _ = make_runtime(monkeypatch, route, "running")
    new, _, _ = make_runtime(monkeypatch, route)
    client = RiskServiceClient(old, route=route, monotonic_clock=lambda: 100.0)
    client._factory = lambda: new
    calls = []
    monkeypatch.setattr(old, "call", lambda *_args, **_kwargs: calls.append("old"))
    monkeypatch.setattr(new, "call", lambda *_args, **_kwargs: calls.append("new"))
    original = client._registration_binding
    switched = []

    def binding(operation, **kwargs):
        result = original(operation, **kwargs)
        if not switched:
            switched.append(True)
            assert client.close(timeout_seconds=1).state == "closed"
            assert client.reconnect(timeout_seconds=1)
        return result

    monkeypatch.setattr(client, "_registration_binding", binding)
    result = client.status("prior", timeout_seconds=1)
    assert result.outcome == "UNKNOWN" and calls == []


@pytest.mark.parametrize("kind", ["not_ready", "not_sent", "refusal"])
def test_not_started_error_after_generation_change_stays_unknown(monkeypatch, kind):
    route = risk_route()
    old, _, _ = make_runtime(monkeypatch, route, "running")
    new, _, _ = make_runtime(monkeypatch, route)
    client = RiskServiceClient(old, route=route, monotonic_clock=lambda: 100.0)
    client._factory = lambda: new

    def call(*_args, **_kwargs):
        assert client.close(timeout_seconds=1).state == "closed"
        assert client.reconnect(timeout_seconds=1)
        if kind == "not_ready":
            raise ThreadedRuntimeError("NOT_READY")
        if kind == "not_sent":
            raise ServiceRuntimeRequestNotSentError()
        error = ServiceRuntimeError("DENIED")
        error.service_reply_code = "DENIED"
        raise error

    monkeypatch.setattr(old, "call", call)
    assert client.status("prior", timeout_seconds=1).outcome == "UNKNOWN"


def test_generation_change_while_detaching_reply_stays_unknown(monkeypatch):
    route = risk_route()
    old, _, _ = make_runtime(monkeypatch, route, "running")
    new, _, _ = make_runtime(monkeypatch, route)
    client = RiskServiceClient(old, route=route, monotonic_clock=lambda: 100.0)
    client._factory = lambda: new
    monkeypatch.setattr(old, "call", lambda *_args, **_kwargs: answer("NOT_FOUND", key="prior"))
    original = risk_client_module._detach_wire

    def detach(raw):
        assert client.close(timeout_seconds=1).state == "closed"
        assert client.reconnect(timeout_seconds=1)
        return original(raw)

    monkeypatch.setattr(risk_client_module, "_detach_wire", detach)
    assert client.status("prior", timeout_seconds=1).outcome == "UNKNOWN"
