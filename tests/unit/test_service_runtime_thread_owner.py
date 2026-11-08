"""Owner extension queue and cleanup tests without network or accounts."""

import os
import threading
import time

import pytest

from bt_api_py.service_runtime_thread import (
    OwnerOperationError,
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
)


class Raw:
    def __init__(self, log):
        self.log = log
        self.bindings = ()
        self.healthy = True
        self.cleanup_failures = self.unregister_uncertainties = 0
        self.log.append(("raw_create", threading.get_ident()))

    def start(self):
        self.log.append(("raw_start", threading.get_ident()))

    def maintain(self):
        pass

    def next_maintenance_delay(self):
        return 1

    def close(self):
        self.log.append(("raw_close", threading.get_ident()))

    def call(self, _mount, _operation, arguments, **_kwargs):
        return arguments


class Extension:
    def __init__(self, log):
        self.log = log
        self.log.append(("ext_create", threading.get_ident()))
        self.started = threading.Event()
        self.release = threading.Event()
        self.cleanup_uncertainties = 0
        self.invocations = []
        self.result_value = {"nested": [1, {"value": "original"}]}

    def start(self, runtime):
        self.log.append(("ext_start", threading.get_ident()))

    def invoke(self, operation, arguments):
        self.invocations.append((operation, arguments, threading.get_ident()))
        if operation == "block":
            self.started.set()
            assert self.release.wait(3)
        if operation == "error":
            raise OwnerOperationError("TRUSTED_DENIAL")
        if operation == "private":
            raise ValueError("secret detail")
        if operation == "result":
            return self.result_value
        return arguments

    def close(self):
        self.log.append(("ext_close", threading.get_ident()))


def setup_runtime(*, queue_size=3):
    log, holder = [], []

    def factory():
        obj = Extension(log)
        holder.append(obj)
        return obj

    runtime = ThreadedServiceClientRuntime(
        lambda: Raw(log), owner_extension_factory=factory, queue_size=queue_size
    )
    runtime.start()
    return runtime, holder[0], log


def test_owner_queue_timeout_retrieval_cancel_and_snapshot():
    runtime, ext, log = setup_runtime(queue_size=1)
    try:
        running = runtime.submit_owner("block", {"items": [1]})
        assert ext.started.wait(1)
        queued_arguments = {"value": [1]}
        queued = runtime.submit_owner("queued", queued_arguments)
        queued_arguments["value"].append(2)
        with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
            running.result(timeout_seconds=0.01)
        with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_QUEUED"):
            queued.result(timeout_seconds=0.01)
        with pytest.raises(ThreadedRuntimeError, match="QUEUE_FULL"):
            runtime.submit_owner("full", {})
        assert running.cancel() is False
        ext.release.set()
        assert running.result() == {"items": [1]}
        assert queued.result() == {"value": [1]}
        assert queued.result() == {"value": [1]}
        result = runtime.submit_owner("result", {})
        first = result.result()
        first["nested"][1]["value"] = "changed"
        assert result.result()["nested"][1]["value"] == "original"
        assert ext.invocations.count(("queued", {"value": [1]}, ext.invocations[1][2])) == 1
    finally:
        ext.release.set()
        runtime.close()
    assert {thread for _, thread in log} == {log[0][1]}
    assert [entry[0] for entry in log] == [
        "raw_create",
        "ext_create",
        "raw_start",
        "ext_start",
        "ext_close",
        "raw_close",
    ]


def test_cancel_only_queued_and_close_preserves_running_result():
    runtime, ext, _ = setup_runtime()
    running = runtime.submit_owner("block", {})
    assert ext.started.wait(1)
    cancelled = runtime.submit_owner("never", {})
    assert cancelled.cancel() is True
    assert cancelled.cancel() is False
    with pytest.raises(ThreadedRuntimeError, match="OWNER_CANCELLED"):
        cancelled.result()
    with pytest.raises(ThreadedRuntimeError, match="CLOSE_TIMEOUT_WORKER_ALIVE"):
        runtime.close(timeout_seconds=0.01)
    ext.release.set()
    assert running.result() == {}
    assert runtime.close().state == "closed"
    assert [call[0] for call in ext.invocations] == ["block"]


def test_finite_errors_and_result_validation():
    runtime, ext, _ = setup_runtime()
    try:
        with pytest.raises(OwnerOperationError, match="TRUSTED_DENIAL"):
            runtime.submit_owner("error", {}).result()
        with pytest.raises(ThreadedRuntimeError, match="OWNER_OUTCOME_UNKNOWN") as error:
            runtime.submit_owner("private", {}).result()
        assert "secret detail" not in str(error.value)
        ext.result_value = {"bad": float("nan")}
        handle = runtime.submit_owner("result", {})
        for _ in range(2):
            with pytest.raises(ThreadedRuntimeError, match="INVALID_OWNER_RESULT"):
                handle.result()
    finally:
        runtime.close()


def test_uncertainties_are_observed_after_invoke_and_never_decrease():
    runtime, ext, _ = setup_runtime()
    try:
        ext.cleanup_uncertainties = 4
        runtime.submit_owner("echo", {}).result()
        assert runtime.status().owner_cleanup_uncertainties == 4
        ext.cleanup_uncertainties = 1
        runtime.submit_owner("echo", {}).result()
        assert runtime.status().owner_cleanup_uncertainties == 4
    finally:
        assert runtime.close().owner_cleanup_uncertainties == 4


def test_invalid_values_cycles_depth_nodes_and_byte_budget():
    runtime, ext, _ = setup_runtime()
    cyclic = []
    cyclic.append(cyclic)
    nested = value = []
    for _ in range(34):
        child = []
        value.append(child)
        value = child
    bad_values = (
        cyclic,
        nested,
        [1] * 100_000,
        "x" * (8 * 1024 * 1024),
        b"bytes",
        float("inf"),
        float("nan"),
        {1: "key"},
        (1,),
        object(),
    )
    try:
        for value in bad_values:
            with pytest.raises(ThreadedRuntimeError, match="INVALID_OWNER_ARGUMENTS"):
                runtime.submit_owner("bad", {"value": value})
        ext.result_value = cyclic
        with pytest.raises(ThreadedRuntimeError, match="INVALID_OWNER_RESULT"):
            runtime.submit_owner("result", {}).result()
    finally:
        runtime.close()


def test_extension_start_failure_closes_both_and_retries_same_extension():
    log, holder = [], []

    class Partial(Extension):
        def start(self, runtime):
            self.log.append(("ext_start", threading.get_ident()))
            raise RuntimeError("private")

        def close(self):
            super().close()
            self.cleanup_uncertainties = (
                2 if len([x for x in log if x[0] == "ext_close"]) == 1 else 0
            )
            if self.cleanup_uncertainties:
                raise RuntimeError("private cleanup")

    def create():
        value = Partial(log)
        holder.append(value)
        return value

    runtime = ThreadedServiceClientRuntime(lambda: Raw(log), owner_extension_factory=create)
    with pytest.raises(ThreadedRuntimeError):
        runtime.start()
    deadline = time.monotonic() + 2
    while runtime.status().state != "close_failed" and time.monotonic() < deadline:
        time.sleep(0.001)
    assert runtime.status().state == "close_failed"
    assert runtime.status().owner_cleanup_uncertainties == 2
    assert [v[0] for v in log].count("raw_close") == 1
    assert len(holder) == 1
    final = runtime.close()
    assert final.owner_cleanup_uncertainties == 2
    assert [v[0] for v in log].count("ext_close") == 2
    assert [v[0] for v in log].count("raw_close") == 1


def test_owner_handle_reentry_and_wrong_pid(monkeypatch):
    runtime, ext, _ = setup_runtime()
    handle = runtime.submit_owner("result", {})
    handle.result()
    real_pid = os.getpid()
    monkeypatch.setattr("bt_api_py.service_runtime_thread.os.getpid", lambda: real_pid + 1)
    for operation in (handle.result, handle.cancel, lambda: runtime.submit_owner("x", {})):
        with pytest.raises(ThreadedRuntimeError, match="WRONG_PROCESS"):
            operation()
    monkeypatch.undo()
    ext.release.set()
    runtime.close()


def test_extension_factory_failure_closes_raw_and_reports_unproven():
    log = []

    def broken_factory():
        raise RuntimeError("hidden")

    runtime = ThreadedServiceClientRuntime(lambda: Raw(log), owner_extension_factory=broken_factory)
    with pytest.raises(ThreadedRuntimeError, match="FACTORY_FAILED_CLEANUP_UNPROVEN"):
        runtime.start()
    final = runtime.close()
    assert final.error_code == "FACTORY_FAILED_CLEANUP_UNPROVEN"
    assert [event[0] for event in log] == ["raw_create", "raw_close"]


def test_raw_start_failure_closes_created_extension_and_raw():
    log = []

    class BadStart(Raw):
        def start(self):
            super().start()
            raise RuntimeError("hidden")

    runtime = ThreadedServiceClientRuntime(
        lambda: BadStart(log), owner_extension_factory=lambda: Extension(log)
    )
    with pytest.raises(ThreadedRuntimeError):
        runtime.start()
    runtime.close()
    assert [event[0] for event in log] == [
        "raw_create",
        "ext_create",
        "raw_start",
        "ext_close",
        "raw_close",
    ]


def test_owner_cannot_submit_or_wait_reentrantly():
    log, holder = [], []
    runtime = None
    release = threading.Event()

    class Reentrant(Extension):
        def invoke(self, operation, arguments):
            assert release.wait(2)
            errors = []
            for action in (
                lambda: runtime.submit_owner("x", {}),
                holder[0].result,
                holder[0].cancel,
            ):
                try:
                    action()
                except ThreadedRuntimeError as error:
                    errors.append(error.code)
            return errors

    runtime = ThreadedServiceClientRuntime(
        lambda: Raw(log), owner_extension_factory=lambda: Reentrant(log)
    )
    runtime.start()
    try:
        holder.append(runtime.submit_owner("reentry", {}))
        release.set()
        assert holder[0].result() == ["OWNER_THREAD_REENTRY"] * 3
    finally:
        release.set()
        runtime.close()


def test_exact_canonical_encoded_budget_and_unicode_escaping():
    runtime, _, _ = setup_runtime()
    try:
        limit = 8 * 1024 * 1024
        exact = {"v": "x" * (limit - 8)}
        assert len(runtime.submit_owner("echo", exact).result()["v"]) == limit - 8
        for too_large in ({"v": "x" * (limit - 7)}, {"v": "界" * (limit // 6)}):
            with pytest.raises(ThreadedRuntimeError, match="INVALID_OWNER_ARGUMENTS"):
                runtime.submit_owner("echo", too_large)
    finally:
        runtime.close()


def test_both_resources_retry_independently_and_uncertainty_is_monotonic():
    log, holder = [], []

    class FlakyRaw(Raw):
        def close(self):
            super().close()
            if [item[0] for item in log].count("raw_close") == 1:
                raise RuntimeError("raw private")

    class FlakyExtension(Extension):
        def close(self):
            super().close()
            self.cleanup_uncertainties = (
                7 if [item[0] for item in log].count("ext_close") == 1 else 1
            )
            if self.cleanup_uncertainties == 7:
                raise RuntimeError("extension private")

    def ext_factory():
        obj = FlakyExtension(log)
        holder.append(obj)
        return obj

    runtime = ThreadedServiceClientRuntime(
        lambda: FlakyRaw(log), owner_extension_factory=ext_factory
    )
    runtime.start()
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        runtime.close()
    assert runtime.status().owner_cleanup_uncertainties == 7
    assert [item[0] for item in log][-2:] == ["ext_close", "raw_close"]
    assert runtime.close().owner_cleanup_uncertainties == 7
    assert [item[0] for item in log][-2:] == ["ext_close", "raw_close"]
    assert len(holder) == 1
