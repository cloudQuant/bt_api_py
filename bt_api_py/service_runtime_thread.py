"""Owner-thread wrapper for one service client and its registrations.

The factory is trusted application wiring and is invoked only by the worker.
A timeout after dispatch is an unknown outcome; callers must use the original
request identity to resolve it and must not automatically resend it.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from bt_api_py.service_runtime import ServiceRuntimeError


class ThreadedRuntimeError(RuntimeError):
    """Finite wrapper diagnostic; no transport or remote error text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BindingSnapshot:
    registration_id: str
    service_epoch: str
    active: bool


@dataclass(frozen=True)
class RuntimeSnapshot:
    state: str
    thread_alive: bool
    healthy: bool
    error_code: str | None
    bindings: tuple[BindingSnapshot, ...]
    cleanup_failures: int
    unregister_uncertainties: int


class _Call:
    def __init__(self, mount: str, operation: str, arguments: Any, kwargs: dict[str, Any]):
        self.mount, self.operation, self.arguments, self.kwargs = (
            mount,
            operation,
            arguments,
            kwargs,
        )
        self.state = "queued"
        self.result: Any = None
        self.error: Exception | None = None


def _copy_wire(value: Any) -> Any:
    # Restrict to by-value wire data: deepcopy alone permits custom objects to
    # return themselves and thus allows mutation after submission.
    if value is None or type(value) in (str, bool, int, float):
        return value
    if type(value) in (list, tuple):
        return [_copy_wire(v) for v in value]
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ThreadedRuntimeError("INVALID_ARGUMENTS")
            result[key] = _copy_wire(item)
        return result
    raise ThreadedRuntimeError("INVALID_ARGUMENTS")


def _timeout(value: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("timeout_seconds must be a finite positive number")
    return float(value)


class ThreadedServiceClientRuntime:
    """Own and maintain a runtime on a single dedicated thread.

    ``start`` timeout requests cleanup, but a blocked factory/start may still
    be running. Inspect ``status`` and call ``close`` again to wait for it.
    ``close`` cannot cancel an already-running transport request.
    The trusted ``monotonic_clock`` must use the same domain as the runtime's
    clock so caller-side health cannot outlive the actual required lease.
    """

    def __init__(
        self,
        factory: Callable[[], Any],
        *,
        queue_size: int = 32,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ):
        if not callable(factory):
            raise TypeError("factory must be callable")
        if type(queue_size) is not int or not 1 <= queue_size <= 1024:
            raise ValueError("invalid queue_size")
        if not callable(monotonic_clock):
            raise TypeError("monotonic_clock must be callable")
        self._factory = factory
        # Trusted clock must share the runtime's monotonic clock domain.
        self._monotonic_clock = monotonic_clock
        self._creator_pid = os.getpid()
        self._queue_size = queue_size
        self._condition = threading.Condition()
        self._queue: deque[_Call] = deque()
        self._thread: threading.Thread | None = None
        self._state = "new"
        self._stop = False
        self._error: str | None = None
        self._healthy = False
        self._lease_deadline: float | None = None
        self._bindings: tuple[BindingSnapshot, ...] = ()
        self._cleanup_failures = 0
        self._unregister_uncertainties = 0
        self._cleanup_retry = 0

    def status(self) -> RuntimeSnapshot:
        if os.getpid() != self._creator_pid:
            return RuntimeSnapshot("wrong_process", False, False, "WRONG_PROCESS", (), 0, 0)
        with self._condition:
            return RuntimeSnapshot(
                self._state,
                bool(self._thread and self._thread.is_alive()),
                self._healthy and self._lease_current(),
                self._error,
                self._bindings,
                self._cleanup_failures,
                self._unregister_uncertainties,
            )

    def _lease_current(self) -> bool:
        try:
            now = self._monotonic_clock()
            return (
                type(now) in (int, float)
                and math.isfinite(now)
                and (self._lease_deadline is None or now < self._lease_deadline)
            )
        except Exception:
            return False

    def _check_reentry(self) -> None:
        if os.getpid() != self._creator_pid:
            raise ThreadedRuntimeError("WRONG_PROCESS")
        if threading.current_thread() is self._thread:
            raise ThreadedRuntimeError("OWNER_THREAD_REENTRY")

    def start(self, *, timeout_seconds: float = 5.0) -> RuntimeSnapshot:
        self._check_reentry()
        timeout = _timeout(timeout_seconds)
        deadline = time.monotonic() + timeout
        with self._condition:
            if self._state != "new":
                raise ThreadedRuntimeError("NOT_NEW")
            self._state = "starting"
            self._thread = threading.Thread(
                target=self._run, name="service-client-owner", daemon=True
            )
            self._thread.start()
            while self._state == "starting":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._stop = True
                    self._healthy = False
                    self._state = "stopping"
                    self._error = "START_TIMEOUT_CLEANUP_PENDING"
                    self._condition.notify_all()
                    raise ThreadedRuntimeError(self._error)
                self._condition.wait(remaining)
            if self._state != "running":
                raise ThreadedRuntimeError(self._error or "START_FAILED")
        return self.status()

    def call(
        self,
        mount: str,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        account_ref: str | None = None,
        portfolio_ref: str | None = None,
        scope_ref: str | None = None,
        max_reply_message_bytes: int | None = None,
        timeout_seconds: float = 5.0,
    ) -> Any:
        self._check_reentry()
        timeout = _timeout(timeout_seconds)
        if not isinstance(arguments, Mapping):
            raise ThreadedRuntimeError("INVALID_ARGUMENTS")
        call = _Call(
            _copy_wire(mount),
            _copy_wire(operation),
            _copy_wire(arguments),
            _copy_wire(
                {
                    "account_ref": account_ref,
                    "portfolio_ref": portfolio_ref,
                    "scope_ref": scope_ref,
                    "max_reply_message_bytes": max_reply_message_bytes,
                }
            ),
        )
        deadline = time.monotonic() + timeout
        with self._condition:
            if (
                self._state != "running"
                or self._stop
                or not self._healthy
                or not self._lease_current()
            ):
                raise ThreadedRuntimeError("NOT_READY")
            if len(self._queue) >= self._queue_size:
                raise ThreadedRuntimeError("QUEUE_FULL")
            self._queue.append(call)
            self._condition.notify_all()
            while call.state in ("queued", "running"):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if call.state == "queued":
                        self._queue.remove(call)
                        call.state = "cancelled"
                        raise ThreadedRuntimeError("TIMEOUT_BEFORE_START")
                    raise ThreadedRuntimeError("TIMEOUT_AFTER_START")
                self._condition.wait(remaining)
            if call.error is not None:
                raise call.error
            return call.result

    def close(self, *, timeout_seconds: float = 5.0) -> RuntimeSnapshot:
        self._check_reentry()
        timeout = _timeout(timeout_seconds)
        with self._condition:
            if self._state == "new":
                self._state = "closed"
                return self.status()
            self._stop = True
            self._healthy = False
            self._cleanup_retry += 1
            if self._state in ("starting", "running") or self._state == "close_failed":
                self._state = "stopping"
            self._reject_pending("CLOSED_BEFORE_START")
            self._condition.notify_all()
            thread = self._thread
        deadline = time.monotonic() + timeout
        with self._condition:
            while thread is not None and thread.is_alive() and self._state != "close_failed":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ThreadedRuntimeError("CLOSE_TIMEOUT_WORKER_ALIVE")
                self._condition.wait(min(remaining, 0.05))
            if self._state == "close_failed":
                raise ThreadedRuntimeError("CLEANUP_FAILED")
        snapshot = self.status()
        if snapshot.state == "close_failed":
            raise ThreadedRuntimeError("CLEANUP_FAILED")
        return snapshot

    def _reject_pending(self, code: str) -> None:
        while self._queue:
            call = self._queue.popleft()
            call.error = ThreadedRuntimeError(code)
            call.state = "done"
        self._condition.notify_all()

    def _capture(self, runtime: Any) -> None:
        bindings = tuple(
            BindingSnapshot(b.registration_id, b.service_epoch, b.active) for b in runtime.bindings
        )
        deadlines = [b.lease_deadline for b in runtime.bindings if b.active and b.required]
        with self._condition:
            self._bindings = bindings
            self._lease_deadline = min(deadlines) if deadlines else None
            self._healthy = bool(runtime.healthy) and not self._stop
            self._cleanup_failures = runtime.cleanup_failures
            self._unregister_uncertainties = runtime.unregister_uncertainties

    def _run(self) -> None:
        runtime = None
        failed = False
        try:
            runtime = self._factory()
            with self._condition:
                if self._stop:
                    return
            runtime.start()
            self._capture(runtime)
            if not runtime.healthy:
                raise ThreadedRuntimeError("NOT_READY")
            with self._condition:
                if not self._stop:
                    self._state = "running"
                self._condition.notify_all()
            while True:
                with self._condition:
                    if self._stop:
                        break
                runtime.maintain()
                self._capture(runtime)
                if not runtime.healthy:
                    raise ThreadedRuntimeError("NOT_READY")
                with self._condition:
                    if self._stop:
                        break
                    if self._queue:
                        call = self._queue.popleft()
                        call.state = "running"
                    else:
                        delay = runtime.next_maintenance_delay()
                        if delay is None:
                            wait = 1.0
                        elif delay > 0:
                            wait = min(1.0, delay)
                            if self._lease_deadline is not None:
                                wait = min(
                                    wait, max(0.0, self._lease_deadline - self._monotonic_clock())
                                )
                        else:
                            # Renewal retry may remain due after a timeout. Keep
                            # its backoff strictly within the required lease.
                            remaining = (
                                None
                                if self._lease_deadline is None
                                else self._lease_deadline - self._monotonic_clock()
                            )
                            wait = (
                                0.05 if remaining is None else min(0.05, max(0.0, remaining) * 0.25)
                            )
                        self._condition.wait(wait)
                        continue
                try:
                    result = runtime.call(call.mount, call.operation, call.arguments, **call.kwargs)
                except Exception as error:
                    result = None
                    # Runtime errors have finite public codes; arbitrary errors
                    # must not expose application or transport details.
                    failure = (
                        error
                        if isinstance(error, ServiceRuntimeError)
                        else ThreadedRuntimeError("CALL_OUTCOME_UNKNOWN")
                    )
                except BaseException:
                    with self._condition:
                        call.error, call.state = (
                            ThreadedRuntimeError("CALL_OUTCOME_UNKNOWN"),
                            "done",
                        )
                        self._condition.notify_all()
                    raise
                else:
                    failure = None
                with self._condition:
                    call.result, call.error, call.state = result, failure, "done"
                    self._condition.notify_all()
        except BaseException:
            failed = True
            with self._condition:
                if runtime is None:
                    self._error = "FACTORY_FAILED_CLEANUP_UNPROVEN"
                elif self._error != "START_TIMEOUT_CLEANUP_PENDING":
                    self._error = (
                        "START_FAILED_CLEANUP_PENDING"
                        if self._state == "starting"
                        else "MAINTENANCE_FAILED"
                    )
                self._state = "failed"
                self._healthy = False
                self._stop = True
                self._reject_pending("NOT_READY")
                self._condition.notify_all()
        finally:
            with self._condition:
                original_error = self._error
            if runtime is not None:
                while True:
                    with self._condition:
                        attempt = self._cleanup_retry
                    close_failed = False
                    try:
                        runtime.close()
                    except BaseException:
                        close_failed = True
                    try:
                        self._capture(runtime)
                    except BaseException:
                        close_failed = True
                    if not close_failed:
                        break
                    with self._condition:
                        self._state, self._error = "close_failed", "CLEANUP_FAILED"
                        self._condition.notify_all()
                        while attempt == self._cleanup_retry:
                            self._condition.wait()
            with self._condition:
                self._healthy = False
                self._error = original_error
                if not failed:
                    self._state = "closed"
                    if self._error == "START_TIMEOUT_CLEANUP_PENDING":
                        self._error = "START_TIMEOUT"
                else:
                    self._state = "failed"
                    if self._error == "START_FAILED_CLEANUP_PENDING":
                        self._error = "START_FAILED"
                    elif self._error == "START_TIMEOUT_CLEANUP_PENDING":
                        self._error = "START_TIMEOUT"
                self._condition.notify_all()
