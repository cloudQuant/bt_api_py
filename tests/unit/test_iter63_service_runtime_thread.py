import threading
import time
from types import SimpleNamespace

import pytest

from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime


class FakeRuntime:
    def __init__(self):
        self.owner = threading.get_ident()
        self.events = [("create", self.owner)]
        self.bindings = (
            SimpleNamespace(
                registration_id="reg",
                service_epoch="epoch",
                active=True,
                required=True,
                lease_deadline=1e12,
            ),
        )
        self.healthy = True
        self.cleanup_failures = 0
        self.unregister_uncertainties = 0
        self.gate = threading.Event()
        self.entered = threading.Event()
        self.fail_maintenance = False

    def start(self):
        self.events.append(("start", threading.get_ident()))

    def maintain(self):
        self.events.append(("maintain", threading.get_ident()))
        if self.fail_maintenance:
            raise RuntimeError("private detail")

    def next_maintenance_delay(self):
        return 0.05

    def call(self, mount, operation, arguments, **kwargs):
        self.events.append((operation, threading.get_ident(), arguments))
        if operation == "block":
            self.entered.set()
            self.gate.wait(2)
        return arguments

    def close(self):
        self.events.append(("close", threading.get_ident()))


def test_owner_and_snapshot():
    holder = []

    def factory():
        obj = FakeRuntime()
        holder.append(obj)
        return obj

    runtime = ThreadedServiceClientRuntime(factory)
    runtime.start(timeout_seconds=1)
    assert runtime.call("mount", "work", {"x": 1}, timeout_seconds=1) == {"x": 1}
    status = runtime.status()
    assert status.healthy and status.bindings[0].registration_id == "reg"
    runtime.close(timeout_seconds=1)
    assert {e[1] for e in holder[0].events} == {holder[0].owner}
    assert not runtime.status().thread_alive


def test_queued_timeout_never_executes_and_running_is_unknown():
    holder = []

    def factory():
        obj = FakeRuntime()
        holder.append(obj)
        return obj

    runtime = ThreadedServiceClientRuntime(factory, queue_size=1)
    runtime.start(timeout_seconds=1)
    with pytest.raises(ThreadedRuntimeError, match="TIMEOUT_AFTER_START"):
        runtime.call("m", "block", {}, timeout_seconds=0.03)
    with pytest.raises(ThreadedRuntimeError, match="TIMEOUT_BEFORE_START"):
        runtime.call("m", "later", {}, timeout_seconds=0.03)
    holder[0].gate.set()
    runtime.close(timeout_seconds=1)
    assert "later" not in [e[0] for e in holder[0].events]


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("condition not met")
        threading.Event().wait(0.005)


def test_queue_full_close_race_and_argument_snapshot():
    holder = []

    def factory():
        obj = FakeRuntime()
        holder.append(obj)
        return obj

    runtime = ThreadedServiceClientRuntime(factory, queue_size=1)
    runtime.start(timeout_seconds=1)
    results = []

    def submit(op, args):
        try:
            results.append((op, runtime.call("m", op, args, timeout_seconds=2)))
        except ThreadedRuntimeError as error:
            results.append((op, error.code))

    first = threading.Thread(target=submit, args=("block", {}))
    first.start()
    assert holder[0].entered.wait(1)
    arguments = {"nested": [1]}
    second = threading.Thread(target=submit, args=("queued", arguments))
    second.start()
    wait_for(lambda: len(runtime._queue) == 1)
    arguments["nested"].append(2)
    with pytest.raises(ThreadedRuntimeError, match="QUEUE_FULL"):
        runtime.call("m", "overflow", {}, timeout_seconds=0.1)
    holder[0].gate.set()
    first.join(1)
    second.join(1)
    assert ("queued", {"nested": [1]}) in results
    runtime.close(timeout_seconds=1)


def test_close_cancels_queued_and_running_timeout_is_not_not_sent():
    holder = []

    def factory():
        obj = FakeRuntime()
        holder.append(obj)
        return obj

    runtime = ThreadedServiceClientRuntime(factory, queue_size=1)
    runtime.start(timeout_seconds=1)
    errors = []

    def submit(op):
        try:
            runtime.call("m", op, {}, timeout_seconds=1)
        except ThreadedRuntimeError as error:
            errors.append(error.code)

    first = threading.Thread(target=submit, args=("block",))
    first.start()
    assert holder[0].entered.wait(1)
    second = threading.Thread(target=submit, args=("queued",))
    second.start()
    wait_for(lambda: len(runtime._queue) == 1)
    with pytest.raises(ThreadedRuntimeError, match="CLOSE_TIMEOUT_WORKER_ALIVE"):
        runtime.close(timeout_seconds=0.03)
    assert runtime.status().thread_alive
    assert not runtime.status().healthy
    second.join(1)
    assert "CLOSED_BEFORE_START" in errors
    holder[0].gate.set()
    first.join(1)
    runtime.close(timeout_seconds=1)
    assert "queued" not in [event[0] for event in holder[0].events]


def test_concurrent_calls_and_maintenance_failure():
    holder = []

    def factory():
        obj = FakeRuntime()
        holder.append(obj)
        return obj

    runtime = ThreadedServiceClientRuntime(factory)
    runtime.start(timeout_seconds=1)
    results = []
    threads = [
        threading.Thread(
            target=lambda n=n: results.append(
                runtime.call("m", "work", {"n": n}, timeout_seconds=2)["n"]
            )
        )
        for n in range(12)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
    assert sorted(results) == list(range(12))
    holder[0].fail_maintenance = True
    wait_for(lambda: runtime.status().state == "failed")
    assert not runtime.status().healthy
    assert runtime.status().error_code == "MAINTENANCE_FAILED"
    with pytest.raises(ThreadedRuntimeError, match="NOT_READY"):
        runtime.call("m", "work", {}, timeout_seconds=1)
    runtime.close(timeout_seconds=1)


def test_start_timeout_is_cleanup_pending_until_owner_returns():
    gate = threading.Event()
    holder = []

    def factory():
        gate.wait(2)
        obj = FakeRuntime()
        holder.append(obj)
        return obj

    runtime = ThreadedServiceClientRuntime(factory)
    with pytest.raises(ThreadedRuntimeError, match="START_TIMEOUT_CLEANUP_PENDING"):
        runtime.start(timeout_seconds=0.03)
    assert runtime.status().thread_alive
    gate.set()
    runtime.close(timeout_seconds=1)
    assert holder[0].events[-1][0] == "close"
    assert not any(event[0] == "start" for event in holder[0].events)
    assert not runtime.status().thread_alive


def test_close_failure_retains_owner_for_retry():
    holder = []

    def factory():
        obj = FakeRuntime()
        original = obj.close

        def close():
            if not getattr(obj, "failed_once", False):
                obj.failed_once = True
                raise RuntimeError("private")
            original()

        obj.close = close
        holder.append(obj)
        return obj

    runtime = ThreadedServiceClientRuntime(factory)
    runtime.start(timeout_seconds=1)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        runtime.close(timeout_seconds=1)
    assert runtime.status().thread_alive
    runtime.close(timeout_seconds=1)
    assert holder[0].events[-1] == ("close", holder[0].owner)


def test_owner_reentry_is_rejected_without_deadlock():
    runtime = None

    def factory():
        obj = FakeRuntime()

        def call(*args, **kwargs):
            try:
                runtime.close(timeout_seconds=1)
            except ThreadedRuntimeError as error:
                return error.code

        obj.call = call
        return obj

    runtime = ThreadedServiceClientRuntime(factory)
    runtime.start(timeout_seconds=1)
    assert runtime.call("m", "work", {}, timeout_seconds=1) == "OWNER_THREAD_REENTRY"
    runtime.close(timeout_seconds=1)


def test_start_failure_reports_cleanup_pending():
    closing = threading.Event()
    release = threading.Event()

    def factory():
        obj = FakeRuntime()

        def start():
            raise RuntimeError("private start error")

        def close():
            closing.set()
            release.wait(2)

        obj.start, obj.close = start, close
        return obj

    runtime = ThreadedServiceClientRuntime(factory)
    with pytest.raises(ThreadedRuntimeError, match="START_FAILED_CLEANUP_PENDING"):
        runtime.start(timeout_seconds=1)
    assert closing.wait(1)
    assert runtime.status().thread_alive
    release.set()
    runtime.close(timeout_seconds=1)
    assert runtime.status().error_code == "START_FAILED"


def test_maintenance_failure_rejects_pending_before_dispatch():
    holder = []

    def factory():
        obj = FakeRuntime()
        holder.append(obj)
        return obj

    runtime = ThreadedServiceClientRuntime(factory)
    runtime.start(timeout_seconds=1)
    outcomes = []

    def submit(op):
        try:
            runtime.call("m", op, {}, timeout_seconds=2)
        except ThreadedRuntimeError as error:
            outcomes.append(error.code)

    first = threading.Thread(target=submit, args=("block",))
    first.start()
    assert holder[0].entered.wait(1)
    second = threading.Thread(target=submit, args=("queued",))
    second.start()
    wait_for(lambda: len(runtime._queue) == 1)
    holder[0].fail_maintenance = True
    holder[0].gate.set()
    first.join(1)
    second.join(1)
    assert outcomes == ["NOT_READY"]
    assert "queued" not in [event[0] for event in holder[0].events]
    runtime.close(timeout_seconds=1)


def test_forked_child_rejects_before_touching_inherited_locks():
    import os
    import signal

    if not hasattr(os, "fork"):
        pytest.skip("fork unavailable")
    runtime = ThreadedServiceClientRuntime(FakeRuntime)
    runtime.start(timeout_seconds=1)
    read_fd, write_fd = os.pipe()
    # Force an inherited locked condition to prove the PID check is first.
    with runtime._condition:
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            signal.alarm(2)
            try:
                status = runtime.status()
                codes = []
                for invoke in (
                    lambda: runtime.start(timeout_seconds=1),
                    lambda: runtime.call("m", "x", {}, timeout_seconds=1),
                    lambda: runtime.close(timeout_seconds=1),
                ):
                    try:
                        invoke()
                    except ThreadedRuntimeError as error:
                        codes.append(error.code)
                ok = (
                    status.state == "wrong_process"
                    and not status.healthy
                    and not status.thread_alive
                    and codes == ["WRONG_PROCESS"] * 3
                )
                os.write(write_fd, b"ok" if ok else b"bad")
            finally:
                os._exit(0)
    os.close(write_fd)
    import select

    try:
        ready, _, _ = select.select([read_fd], [], [], 3)
        response = os.read(read_fd, 16) if ready else b""
        _, exit_status = os.waitpid(pid, 0)
        assert response == b"ok"
        assert os.waitstatus_to_exitcode(exit_status) == 0
    finally:
        os.close(read_fd)
        runtime.close(timeout_seconds=1)


def test_inflight_call_cannot_hide_expired_lease():
    clock = [100.0]
    holder = []

    def factory():
        obj = FakeRuntime()
        obj.bindings[0].lease_deadline = 130.0
        holder.append(obj)
        return obj

    runtime = ThreadedServiceClientRuntime(factory, monotonic_clock=lambda: clock[0])
    runtime.start(timeout_seconds=1)
    with pytest.raises(ThreadedRuntimeError, match="TIMEOUT_AFTER_START"):
        runtime.call("m", "block", {}, timeout_seconds=0.03)
    clock[0] = 131.0
    assert not runtime.status().healthy
    with pytest.raises(ThreadedRuntimeError, match="NOT_READY"):
        runtime.call("m", "later", {}, timeout_seconds=1)
    holder[0].gate.set()
    runtime.close(timeout_seconds=1)


def test_short_lease_is_maintained_before_expiry():
    renewed = threading.Event()

    class ShortLease(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.bindings[0].lease_deadline = time.monotonic() + 0.03
            self.due = time.monotonic() + 0.005

        def maintain(self):
            now = time.monotonic()
            if now >= self.bindings[0].lease_deadline:
                raise RuntimeError("expired")
            if now >= self.due:
                self.bindings[0].lease_deadline = now + 0.03
                self.due = now + 0.005
                renewed.set()

        def next_maintenance_delay(self):
            return max(0, self.due - time.monotonic())

    runtime = ThreadedServiceClientRuntime(ShortLease)
    try:
        runtime.start(timeout_seconds=1)
        assert renewed.wait(0.5)
        assert runtime.status().healthy
    finally:
        runtime.close(timeout_seconds=1)


def test_timed_out_factory_failure_does_not_claim_cleanup():
    gate = threading.Event()

    def factory():
        gate.wait(2)
        raise RuntimeError("private factory error")

    runtime = ThreadedServiceClientRuntime(factory)
    with pytest.raises(ThreadedRuntimeError, match="START_TIMEOUT_CLEANUP_PENDING"):
        runtime.start(timeout_seconds=0.03)
    gate.set()
    runtime.close(timeout_seconds=1)
    assert runtime.status().error_code == "FACTORY_FAILED_CLEANUP_UNPROVEN"


def test_short_lease_retries_transient_renewal_before_deadline():
    renewed = threading.Event()

    class RetryLease(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.bindings[0].lease_deadline = time.monotonic() + 0.03
            self.attempts = 0

        def maintain(self):
            if time.monotonic() >= self.bindings[0].lease_deadline:
                raise RuntimeError("expired")
            self.attempts += 1
            if self.attempts == 1:
                return  # A tolerated transient renewal timeout remains due.
            self.bindings[0].lease_deadline = time.monotonic() + 1
            renewed.set()

        def next_maintenance_delay(self):
            return 0 if self.attempts == 1 else 1

    runtime = ThreadedServiceClientRuntime(RetryLease)
    try:
        runtime.start(timeout_seconds=1)
        assert renewed.wait(0.5)
        assert runtime.status().healthy
    finally:
        runtime.close(timeout_seconds=1)
