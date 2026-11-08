"""Owner-thread wrapper for one service client and its registrations.

The factory is trusted application wiring and is invoked only by the worker.
A timeout after dispatch is an unknown outcome; callers must use the original
request identity to resolve it and must not automatically resend it.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from bt_api_base.service_protocol import WireIdentity, WireScope

from bt_api_py.service_runtime import ServiceRuntimeError


class ThreadedRuntimeError(RuntimeError):
    """Finite wrapper diagnostic; no transport or remote error text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class OwnerOperationError(RuntimeError):
    """Finite diagnostic deliberately emitted by a trusted owner extension."""

    def __init__(self, code: str):
        if type(code) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code) is None:
            raise ValueError("INVALID_OWNER_ERROR_CODE")
        self.code = code
        super().__init__(code)


class OwnerRuntimeExtension(Protocol):
    """Trusted extension whose entire lifecycle runs on the runtime owner."""

    def start(self, runtime: Any) -> None: ...
    def invoke(self, operation: str, arguments: Mapping[str, Any]) -> Any: ...
    def close(self) -> None: ...

    @property
    def cleanup_uncertainties(self) -> int: ...


@dataclass(frozen=True)
class BindingSnapshot:
    registration_id: str
    service_epoch: str
    active: bool
    mount: str | None = None
    service_id: str | None = None
    instance_id: str | None = None
    scope_ref: str | None = None
    scope: WireScope | None = None
    operations: frozenset[str] | None = None
    required: bool | None = None


@dataclass(frozen=True)
class RuntimeSnapshot:
    state: str
    thread_alive: bool
    healthy: bool
    error_code: str | None
    bindings: tuple[BindingSnapshot, ...]
    cleanup_failures: int
    unregister_uncertainties: int
    identity: WireIdentity | None = None
    run_id: str | None = None
    owner_cleanup_uncertainties: int = 0


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


class _OwnerCall:
    def __init__(self, operation: str, arguments: dict[str, Any]):
        self.operation = operation
        self.arguments = arguments
        self.state = "queued"
        self.result: Any = None
        self.error: Exception | None = None


def _copy_owner_json(value: Any, *, error_code: str) -> Any:
    """Copy exact JSON types while bounding nesting, nodes and canonical bytes."""
    remaining = [100_000]
    active: set[int] = set()
    max_bytes = 8 * 1024 * 1024
    used_bytes = [0]

    def consume(size: int) -> None:
        used_bytes[0] += size
        if used_bytes[0] > max_bytes:
            raise ValueError

    def copy(item: Any, depth: int) -> Any:
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 32:
            raise ValueError
        if item is None or type(item) in (bool, int):
            consume(len(json.dumps(item, allow_nan=False)))
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError
            consume(len(json.dumps(item, allow_nan=False)))
            return item
        if type(item) is str:
            if len(item) > max_bytes:
                raise ValueError
            consume(2)  # quotation marks
            for index in range(0, len(item), 4096):
                chunk = item[index : index + 4096]
                chunk.encode("utf-8", errors="strict")
                consume(len(json.dumps(chunk, ensure_ascii=True)) - 2)
            return item
        if type(item) not in (list, dict) or id(item) in active:
            raise ValueError
        active.add(id(item))
        try:
            consume(2 + max(0, len(item) - 1))  # brackets and commas
            if type(item) is list:
                return [copy(child, depth + 1) for child in item]
            if any(type(key) is not str for key in item):
                raise ValueError
            consume(len(item))  # colons
            return {copy(key, depth + 1): copy(child, depth + 1) for key, child in item.items()}
        finally:
            active.remove(id(item))

    try:
        result = copy(value, 0)
        return result
    except Exception:
        raise ThreadedRuntimeError(error_code) from None


class OwnerCallHandle:
    """One owner call retained by its caller across wait timeouts."""

    def __init__(self, runtime: ThreadedServiceClientRuntime, call: _OwnerCall):
        self._runtime, self._call = runtime, call

    def result(self, *, timeout_seconds: float = 5.0) -> Any:
        runtime, call = self._runtime, self._call
        runtime._check_reentry()
        timeout = _timeout(timeout_seconds)
        if timeout > threading.TIMEOUT_MAX:
            raise ValueError("timeout_seconds exceeds platform limit")
        deadline = time.monotonic() + timeout
        with runtime._condition:
            while call.state in ("queued", "running"):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ThreadedRuntimeError(
                        "OWNER_TIMEOUT_QUEUED" if call.state == "queued" else "OWNER_TIMEOUT_RUNNING"
                    )
                runtime._condition.wait(remaining)
            if call.error is not None:
                if type(call.error) is OwnerOperationError:
                    raise OwnerOperationError(call.error.code)
                raise ThreadedRuntimeError(call.error.code)
            value = call.result
        return _copy_owner_json(value, error_code="INVALID_OWNER_RESULT")

    def cancel(self) -> bool:
        runtime, call = self._runtime, self._call
        runtime._check_reentry()
        with runtime._condition:
            if call.state != "queued":
                return False
            runtime._queue.remove(call)
            call.state = "done"
            call.error = ThreadedRuntimeError("OWNER_CANCELLED")
            runtime._condition.notify_all()
            return True


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
        owner_extension_factory: Callable[[], OwnerRuntimeExtension] | None = None,
    ):
        if not callable(factory):
            raise TypeError("factory must be callable")
        if type(queue_size) is not int or not 1 <= queue_size <= 1024:
            raise ValueError("invalid queue_size")
        if not callable(monotonic_clock):
            raise TypeError("monotonic_clock must be callable")
        if owner_extension_factory is not None and not callable(owner_extension_factory):
            raise TypeError("owner_extension_factory must be callable")
        self._factory = factory
        self._owner_extension_factory = owner_extension_factory
        # Trusted clock must share the runtime's monotonic clock domain.
        self._monotonic_clock = monotonic_clock
        self._creator_pid = os.getpid()
        self._queue_size = queue_size
        self._condition = threading.Condition()
        self._queue: deque[_Call | _OwnerCall] = deque()
        self._thread: threading.Thread | None = None
        self._state = "new"
        self._stop = False
        self._error: str | None = None
        self._healthy = False
        self._lease_deadline: float | None = None
        self._bindings: tuple[BindingSnapshot, ...] = ()
        self._identity: WireIdentity | None = None
        self._run_id: str | None = None
        self._cleanup_failures = 0
        self._unregister_uncertainties = 0
        self._owner_cleanup_uncertainties = 0
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
                self._identity,
                self._run_id,
                self._owner_cleanup_uncertainties,
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

    def submit_owner(self, operation: str, arguments: Mapping[str, Any]) -> OwnerCallHandle:
        self._check_reentry()
        if self._owner_extension_factory is None:
            raise ThreadedRuntimeError("OWNER_UNAVAILABLE")
        if type(operation) is not str or not operation:
            raise ThreadedRuntimeError("INVALID_OWNER_ARGUMENTS")
        try:
            if len(operation.encode("utf-8", errors="strict")) > 256:
                raise ValueError
        except (ValueError, UnicodeError):
            raise ThreadedRuntimeError("INVALID_OWNER_ARGUMENTS") from None
        copied = _copy_owner_json(arguments, error_code="INVALID_OWNER_ARGUMENTS")
        if type(copied) is not dict:
            raise ThreadedRuntimeError("INVALID_OWNER_ARGUMENTS")
        call = _OwnerCall(operation, copied)
        with self._condition:
            if (
                self._state != "running" or self._stop or not self._healthy
                or not self._lease_current()
            ):
                raise ThreadedRuntimeError("NOT_READY")
            if len(self._queue) >= self._queue_size:
                raise ThreadedRuntimeError("QUEUE_FULL")
            self._queue.append(call)
            self._condition.notify_all()
        return OwnerCallHandle(self, call)

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
            BindingSnapshot(
                b.registration_id,
                b.service_epoch,
                b.active,
                getattr(b, "mount", None),
                getattr(b, "service_id", None),
                getattr(b, "instance_id", None),
                getattr(b, "scope_ref", None),
                getattr(b, "scope", None) if type(getattr(b, "scope", None)) is WireScope else None,
                getattr(b, "operations", None)
                if type(getattr(b, "operations", None)) is frozenset
                else None,
                getattr(b, "required", None)
                if type(getattr(b, "required", None)) is bool
                else None,
            )
            for b in runtime.bindings
        )
        identity = getattr(runtime, "identity", None)
        run_id = getattr(runtime, "run_id", None)
        identity = identity if type(identity) is WireIdentity else None
        run_id = run_id if type(run_id) is str else None
        deadlines = [b.lease_deadline for b in runtime.bindings if b.active and b.required]
        with self._condition:
            self._bindings = bindings
            self._identity = identity
            self._run_id = run_id
            self._lease_deadline = min(deadlines) if deadlines else None
            self._healthy = bool(runtime.healthy) and not self._stop
            self._cleanup_failures = runtime.cleanup_failures
            self._unregister_uncertainties = runtime.unregister_uncertainties

    def _capture_owner_uncertainties(self, extension: OwnerRuntimeExtension) -> None:
        count = extension.cleanup_uncertainties
        if type(count) is not int or not 0 <= count < 2**63:
            raise ValueError("INVALID_OWNER_CLEANUP_COUNT")
        with self._condition:
            self._owner_cleanup_uncertainties = max(self._owner_cleanup_uncertainties, count)

    def _run(self) -> None:
        runtime = None
        extension = None
        creating_extension = False
        failed = False
        try:
            runtime = self._factory()
            with self._condition:
                if self._stop:
                    return
            if self._owner_extension_factory is not None:
                creating_extension = True
                extension = self._owner_extension_factory()
                if extension is None:
                    raise ValueError("INVALID_OWNER_EXTENSION")
                creating_extension = False
            with self._condition:
                if self._stop:
                    return
            runtime.start()
            if extension is not None:
                extension.start(runtime)
                self._capture_owner_uncertainties(extension)
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
                    if type(call) is _OwnerCall:
                        try:
                            result = _copy_owner_json(
                                extension.invoke(call.operation, call.arguments),
                                error_code="INVALID_OWNER_RESULT",
                            )
                        finally:
                            self._capture_owner_uncertainties(extension)
                    else:
                        result = runtime.call(
                            call.mount, call.operation, call.arguments, **call.kwargs
                        )
                except Exception as error:
                    result = None
                    # Runtime errors have finite public codes; arbitrary errors
                    # must not expose application or transport details.
                    if type(call) is _OwnerCall:
                        if type(error) is OwnerOperationError:
                            try:
                                failure = OwnerOperationError(error.code)
                            except Exception:
                                failure = ThreadedRuntimeError("OWNER_OUTCOME_UNKNOWN")
                        elif type(error) is ThreadedRuntimeError and error.code == "INVALID_OWNER_RESULT":
                            failure = ThreadedRuntimeError("INVALID_OWNER_RESULT")
                        else:
                            failure = ThreadedRuntimeError("OWNER_OUTCOME_UNKNOWN")
                    else:
                        failure = (
                            error if isinstance(error, ServiceRuntimeError)
                            else ThreadedRuntimeError("CALL_OUTCOME_UNKNOWN")
                        )
                except BaseException:
                    with self._condition:
                        call.error, call.state = (
                            ThreadedRuntimeError(
                                "OWNER_OUTCOME_UNKNOWN" if type(call) is _OwnerCall
                                else "CALL_OUTCOME_UNKNOWN"
                            ),
                            "done",
                        )
                        self._condition.notify_all()
                    raise
                else:
                    failure = None
                with self._condition:
                    call.result, call.error, call.state = result, failure, "done"
                    self._condition.notify_all()
                del call, result, failure
        except BaseException:
            failed = True
            with self._condition:
                if runtime is None or creating_extension:
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
            if runtime is not None or extension is not None:
                extension_closed = extension is None
                runtime_closed = runtime is None
                while True:
                    with self._condition:
                        attempt = self._cleanup_retry
                    close_failed = False
                    if not extension_closed:
                        try:
                            extension.close()
                            extension_closed = True
                        except BaseException:
                            close_failed = True
                    if extension is not None:
                        try:
                            self._capture_owner_uncertainties(extension)
                        except BaseException:
                            close_failed = True
                    if not runtime_closed:
                        try:
                            runtime.close()
                            runtime_closed = True
                        except BaseException:
                            close_failed = True
                    if runtime is not None:
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
