"""Trusted owner continuations preserve one handle without starving runtime work."""

import threading
import time

import pytest

from bt_api_py.service_runtime_thread import (
    OwnerOperationContinuation,
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
)


class Raw:
    def __init__(self, shared):
        self.shared = shared
        self.bindings = ()
        self.healthy = True
        self.cleanup_failures = self.unregister_uncertainties = 0
        self.shared["owner"] = threading.get_ident()

    def start(self):
        pass

    def maintain(self):
        self.shared["maintain"] += 1
        hook = self.shared.get("maintain_hook")
        if hook is not None:
            hook()
        if self.shared.get("fail_maintain"):
            raise RuntimeError("private maintenance failure")

    def next_maintenance_delay(self):
        return 1

    def call(self, mount, operation, arguments, **kwargs):
        self.shared["calls"].append((operation, threading.get_ident()))
        return arguments

    def close(self):
        self.shared["raw_closed"] += 1


class Extension:
    def __init__(self, shared):
        self.shared = shared
        self.cleanup_uncertainties = 0

    def start(self, runtime):
        pass

    def invoke(self, operation, arguments):
        return self.shared["invoke"](operation, arguments)

    def close(self):
        self.shared["extension_closed"] += 1


def make_runtime(invoke, *, queue_size=4):
    shared = {"invoke": invoke, "maintain": 0, "calls": [], "raw_closed": 0, "extension_closed": 0}
    runtime = ThreadedServiceClientRuntime(
        lambda: Raw(shared),
        owner_extension_factory=lambda: Extension(shared),
        queue_size=queue_size,
    )
    runtime.start()
    return runtime, shared


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("timeout waiting for test state")
        time.sleep(0.001)


def test_multiple_steps_same_handle_other_calls_and_renewal_progress():
    seen = []

    def invoke(operation, args):
        if operation == "short":
            return {"other": True}

        def resume():
            seen.append(threading.get_ident())
            if len(seen) < 3:
                return OwnerOperationContinuation(resume, 0.03)
            return {"steps": len(seen)}

        return OwnerOperationContinuation(resume, 0.03)

    runtime, shared = make_runtime(invoke)
    try:
        handle = runtime.submit_owner("long", {})
        with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
            handle.result(timeout_seconds=0.005)
        assert handle.cancel() is False
        assert runtime.submit_owner("short", {}).result() == {"other": True}
        assert runtime.call("mount", "command", {"ok": True}) == {"ok": True}
        assert handle.result(timeout_seconds=2) == {"steps": 3}
        assert handle.result() == {"steps": 3}
        assert len(seen) == 3 and set(seen) == {shared["owner"]}
        assert shared["maintain"] >= 5
        assert shared["calls"] == [("command", shared["owner"])]
    finally:
        runtime.close()
    assert shared["raw_closed"] == shared["extension_closed"] == 1


def test_continuation_delay_uses_elapsed_time_with_frozen_lease_clock():
    shared = {
        "invoke": lambda operation, args: OwnerOperationContinuation(lambda: "done", 0.01),
        "maintain": 0,
        "calls": [],
        "raw_closed": 0,
        "extension_closed": 0,
    }
    runtime = ThreadedServiceClientRuntime(
        lambda: Raw(shared),
        owner_extension_factory=lambda: Extension(shared),
        monotonic_clock=lambda: 1000,
    )
    runtime.start()
    try:
        assert runtime.submit_owner("pending", {}).result(timeout_seconds=1) == "done"
    finally:
        runtime.close()


def test_pending_counts_capacity_including_one_running_yield(tmp_path):
    entered, release, maintenance_entered, maintenance_release = (
        threading.Event() for _ in range(4)
    )
    shared_holder = {}

    def invoke(operation, args):
        if operation == "first":
            entered.set()
            assert release.wait(2)
            shared_holder["value"]["maintain_hook"] = pause
            return OwnerOperationContinuation(lambda: "done", 30)
        return "second"

    def pause():
        maintenance_entered.set()
        assert maintenance_release.wait(2)
        shared_holder["value"]["maintain_hook"] = None

    runtime, shared = make_runtime(invoke, queue_size=1)
    shared_holder["value"] = shared
    try:
        first = runtime.submit_owner("first", {})
        assert entered.wait(1)
        second = runtime.submit_owner("second", {})
        release.set()
        assert maintenance_entered.wait(1)
        with runtime._condition:
            assert len(runtime._queue) + len(runtime._continuations) == 2
        with pytest.raises(ThreadedRuntimeError, match="QUEUE_FULL"):
            runtime.submit_owner("third", {})
        assert first.cancel() is False
        assert second.cancel() is True
        with pytest.raises(ThreadedRuntimeError, match="OWNER_CANCELLED"):
            second.result()
        with pytest.raises(ThreadedRuntimeError, match="QUEUE_FULL"):
            runtime.submit_owner("fourth", {})
    finally:
        maintenance_release.set()
        release.set()
        runtime.close()
    with pytest.raises(ThreadedRuntimeError, match="OWNER_OUTCOME_UNKNOWN"):
        first.result()


def test_multiple_continuations_and_queued_requests_each_make_progress():
    steps = []

    def invoke(operation, args):
        if operation == "quick":
            steps.append("quick")
            return "ok"

        def resume():
            steps.append(operation)
            if steps.count(operation) < 3:
                return OwnerOperationContinuation(resume, 0.005)
            return operation

        return OwnerOperationContinuation(resume, 0.005)

    runtime, shared = make_runtime(invoke)
    try:
        first = runtime.submit_owner("a", {})
        second = runtime.submit_owner("b", {})
        wait_for(lambda: len(steps) >= 2)
        assert runtime.submit_owner("quick", {}).result() == "ok"
        assert first.result() == "a" and second.result() == "b"
        assert steps.count("a") == steps.count("b") == 3
        assert steps.index("quick") < max(i for i, value in enumerate(steps) if value in {"a", "b"})
        assert shared["maintain"] >= 7
    finally:
        runtime.close()


@pytest.mark.parametrize("delay", [0, -1, 61, 10**1000, float("nan"), float("inf"), True, "1"])
def test_continuation_constructor_rejects_invalid_delay(delay):
    with pytest.raises(ValueError, match="INVALID_OWNER_CONTINUATION"):
        OwnerOperationContinuation(lambda: None, delay)


def test_invalid_mutated_continuation_revalidated_on_return_and_resume():
    first = OwnerOperationContinuation(lambda: "no", 0.01)
    object.__setattr__(first, "delay_seconds", 0)
    second = OwnerOperationContinuation(lambda: "no", 0.05)
    runtime, _ = make_runtime(lambda operation, args: first if operation == "first" else second)
    try:
        with pytest.raises(ThreadedRuntimeError, match="INVALID_OWNER_CONTINUATION"):
            runtime.submit_owner("first", {}).result()
        handle = runtime.submit_owner("second", {})
        wait_for(lambda: bool(runtime._continuations))
        object.__setattr__(second, "resume", None)
        with pytest.raises(ThreadedRuntimeError, match="INVALID_OWNER_CONTINUATION"):
            handle.result(timeout_seconds=2)
    finally:
        runtime.close()


def test_close_and_maintenance_failure_resolve_waiting_continuations_once():
    for should_fail in (False, True):
        called = []
        runtime, shared = make_runtime(
            lambda operation, args, called=called: OwnerOperationContinuation(
                lambda: called.append(True), 30
            )
        )
        handle = runtime.submit_owner("pending", {})
        wait_for(lambda runtime=runtime: bool(runtime._continuations))
        if should_fail:
            shared["fail_maintain"] = True
            with runtime._condition:
                runtime._condition.notify_all()
            wait_for(lambda runtime=runtime: runtime.status().state == "failed")
        else:
            runtime.close()
        for _ in range(2):
            with pytest.raises(ThreadedRuntimeError, match="OWNER_OUTCOME_UNKNOWN"):
                handle.result()
        assert called == []
        runtime.close()
        assert shared["raw_closed"] == shared["extension_closed"] == 1


def test_close_while_resume_running_marks_unknown_but_waits_for_cleanup():
    entered, release = threading.Event(), threading.Event()

    def resume():
        entered.set()
        assert release.wait(2)
        return {"must_not_be_published": True}

    runtime, shared = make_runtime(lambda operation, args: OwnerOperationContinuation(resume, 0.01))
    handle = runtime.submit_owner("pending", {})
    try:
        assert entered.wait(1)
        with pytest.raises(ThreadedRuntimeError, match="CLOSE_TIMEOUT_WORKER_ALIVE"):
            runtime.close(timeout_seconds=0.01)
        with pytest.raises(ThreadedRuntimeError, match="OWNER_OUTCOME_UNKNOWN"):
            handle.result()
        assert shared["raw_closed"] == shared["extension_closed"] == 0
    finally:
        release.set()
        runtime.close()
    with pytest.raises(ThreadedRuntimeError, match="OWNER_OUTCOME_UNKNOWN"):
        handle.result()
    assert shared["raw_closed"] == shared["extension_closed"] == 1


def test_pending_continuation_is_unknown_during_failed_cleanup_and_retry():
    attempts = []

    class FlakyExtension(Extension):
        def close(self):
            attempts.append(threading.get_ident())
            if len(attempts) == 1:
                raise RuntimeError("private")
            super().close()

    shared = {
        "invoke": lambda operation, args: OwnerOperationContinuation(lambda: "late", 30),
        "maintain": 0,
        "calls": [],
        "raw_closed": 0,
        "extension_closed": 0,
    }
    runtime = ThreadedServiceClientRuntime(
        lambda: Raw(shared), owner_extension_factory=lambda: FlakyExtension(shared)
    )
    runtime.start()
    handle = runtime.submit_owner("pending", {})
    wait_for(lambda: bool(runtime._continuations))
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        runtime.close()
    with pytest.raises(ThreadedRuntimeError, match="OWNER_OUTCOME_UNKNOWN"):
        handle.result()
    assert runtime.status().thread_alive
    assert shared["raw_closed"] == 1
    runtime.close()
    assert len(attempts) == 2 and len(set(attempts)) == 1
    assert shared["raw_closed"] == shared["extension_closed"] == 1


def test_base_exception_in_resume_finishes_handle_and_cleans_owner():
    def fatal():
        raise KeyboardInterrupt

    runtime, shared = make_runtime(lambda operation, args: OwnerOperationContinuation(fatal, 0.01))
    handle = runtime.submit_owner("pending", {})
    with pytest.raises(ThreadedRuntimeError, match="OWNER_OUTCOME_UNKNOWN"):
        handle.result(timeout_seconds=2)
    wait_for(lambda: not runtime.status().thread_alive)
    assert runtime.status().state == "failed"
    assert runtime._active_continuation is None
    assert shared["raw_closed"] == shared["extension_closed"] == 1
    runtime.close()


@pytest.mark.parametrize("yields_after_close", [False, True])
def test_initial_invoke_finishing_after_close_distinguishes_result_from_yield(yields_after_close):
    entered, release = threading.Event(), threading.Event()

    def invoke(operation, args):
        entered.set()
        assert release.wait(2)
        if yields_after_close:
            return OwnerOperationContinuation(lambda: pytest.fail("late continuation ran"), 0.01)
        return {"final": True}

    runtime, shared = make_runtime(invoke)
    handle = runtime.submit_owner("initial", {})
    try:
        assert entered.wait(1)
        with pytest.raises(ThreadedRuntimeError, match="CLOSE_TIMEOUT_WORKER_ALIVE"):
            runtime.close(timeout_seconds=0.01)
    finally:
        release.set()
        runtime.close()
    if yields_after_close:
        with pytest.raises(ThreadedRuntimeError, match="OWNER_OUTCOME_UNKNOWN"):
            handle.result()
    else:
        assert handle.result() == {"final": True}
    assert shared["raw_closed"] == shared["extension_closed"] == 1
