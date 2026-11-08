"""Bounded in-memory handoff to a single trusted Gateway native owner thread.

Queue responses are process-local observations, not durable acceptance or
order outcomes. The factory and cleanup run on the owner thread. An active
native call is never cancelled by this queue. The caller's finalizer must be a
fast, nonblocking local permit revocation: it may execute on the enqueue/close
caller when work is rejected or discarded. No external calls belong there.
Expiry is checked when the owner takes a queued task; no separate sweeper runs
while a native call is blocked. Explicit close drops queued work immediately.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class GatewayDirectOwnerError(RuntimeError):
    """A finite local owner/queue failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class GatewayOwnerStartupCleanupError(RuntimeError):
    """Factory failure that hands partial-resource cleanup to the owner thread."""

    def __init__(self, cleanup: Callable[[], None]) -> None:
        if not callable(cleanup):
            raise ValueError("cleanup must be callable")
        self.cleanup = cleanup
        super().__init__("STARTUP_CLEANUP_REQUIRED")


@dataclass(frozen=True)
class GatewayDispatchCommand:
    strategy: str
    request_key: str
    principal: str
    reservation_id: str
    digest: str
    expected_version: int

    def __post_init__(self) -> None:
        for value in (
            self.strategy,
            self.request_key,
            self.principal,
            self.reservation_id,
        ):
            if type(value) is not str or not value or value != value.strip() or len(value) > 256:
                raise GatewayDirectOwnerError("INVALID_COMMAND")
        if (
            type(self.digest) is not str
            or len(self.digest) != 64
            or any(c not in "0123456789abcdef" for c in self.digest)
            or type(self.expected_version) is not int
            or self.expected_version < 1
        ):
            raise GatewayDirectOwnerError("INVALID_COMMAND")


@dataclass(frozen=True)
class GatewayCancelCommand:
    strategy: str
    operation_id: str
    principal: str
    expected_version: int

    def __post_init__(self) -> None:
        for value in (self.strategy, self.operation_id, self.principal):
            if type(value) is not str or not value or value != value.strip() or len(value) > 256:
                raise GatewayDirectOwnerError("INVALID_COMMAND")
        if type(self.expected_version) is not int or self.expected_version < 1:
            raise GatewayDirectOwnerError("INVALID_COMMAND")


@dataclass(frozen=True)
class GatewayDirectOwnerResources:
    dispatcher: Any
    journal: Any
    close: Callable[[], None]
    maintain: Callable[[], object] | None = None
    maintenance_interval_seconds: float | None = None
    cancel_dispatcher: Any = None


@dataclass(frozen=True)
class GatewayDirectOwnerBinding:
    account_scope: str
    journal_id: str
    generation: str


@dataclass(frozen=True)
class GatewayDirectOwnerStatus:
    """Queue depth covers both queues; accepting reflects place capacity only.

    Cancellation callers must use enqueue_cancel's result, not this flag, to
    determine whether reserved cancel capacity accepted their request.
    """

    queue_depth: int
    running: bool
    accepting: bool
    thread_alive: bool
    failure_category: str | None


@dataclass(frozen=True)
class _Task:
    command: GatewayDispatchCommand | GatewayCancelCommand
    request_fence: Callable[..., Any]
    finalize: Callable[[], None]
    deadline: float


def _command_key(
    command: GatewayDispatchCommand | GatewayCancelCommand,
) -> tuple[str, str, str]:
    if type(command) is GatewayCancelCommand:
        return ("cancel", command.strategy, command.operation_id)
    return ("place", command.strategy, command.request_key)


def _duration(value: Any, *, maximum: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
        raise GatewayDirectOwnerError("INVALID_DURATION")
    return float(value)


class GatewayDirectOwner:
    """One thread owns native resources until a trusted cleanup succeeds.

    Factory must create and return a :class:`GatewayDirectOwnerResources` on
    the owner thread. Its ``close`` must close the native session before it
    releases the guard; failed cleanup leaves the thread alive for close retry.
    This owner is single-use and never starts a replacement thread.
    """

    def __init__(
        self,
        factory: Callable[[], GatewayDirectOwnerResources],
        *,
        queue_size: int,
        max_queue_wait_seconds: float,
        busy_timeout_seconds: float,
        cancel_queue_size: int = 16,
    ) -> None:
        if (
            not callable(factory)
            or type(queue_size) is not int
            or not 1 <= queue_size <= 10000
            or type(cancel_queue_size) is not int
            or not 1 <= cancel_queue_size <= 10000
        ):
            raise GatewayDirectOwnerError("INVALID_CONFIGURATION")
        self._max_wait = _duration(max_queue_wait_seconds, maximum=3600)
        self._busy_timeout = _duration(busy_timeout_seconds, maximum=3600)
        self._factory = factory
        self._queue_size = queue_size
        self._cancel_queue_size = cancel_queue_size
        self._pid = os.getpid()
        self._condition = threading.Condition()
        self._startup = threading.Event()
        self._done = threading.Event()
        self._queue: deque[_Task] = deque()
        self._cancel_queue: deque[_Task] = deque()
        self._pending: dict[tuple[str, str, str], GatewayDispatchCommand | GatewayCancelCommand] = (
            {}
        )
        self._thread: threading.Thread | None = None
        self._closing = False
        self._active_since: float | None = None
        self._failure: str | None = None
        self._binding: GatewayDirectOwnerBinding | None = None
        self._journal: Any = None
        self._dispatcher: Any = None
        self._cancel_dispatcher: Any = None
        self._cleanup_retry = 0
        self._startup_cleanup: Callable[[], None] | None = None

    def _check_pid(self) -> None:
        if os.getpid() != self._pid:
            raise GatewayDirectOwnerError("PROCESS_BOUNDARY")

    def start(self, timeout_seconds: float) -> bool:
        self._check_pid()
        timeout = _duration(timeout_seconds, maximum=3600)
        with self._condition:
            if self._closing:
                return False
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="gateway-direct-owner")
                self._thread.start()
        if not self._startup.wait(timeout):
            with self._condition:
                self._closing = True
                self._condition.notify_all()
            return False
        with self._condition:
            return self._binding is not None and not self._closing and not self._done.is_set()

    def enqueue(
        self,
        command: GatewayDispatchCommand,
        *,
        request_fence: Callable[..., Any],
        finalize: Callable[[], None],
        queue_wait_seconds: float | None = None,
    ) -> str:
        """Try once without waiting for capacity; results are only in memory.

        ``finalize`` is owned by this call even for duplicates and rejections.
        It must be a nonblocking local revocation; it is called exactly once.
        """
        return self._enqueue(
            command, GatewayDispatchCommand, request_fence, finalize, queue_wait_seconds
        )

    def enqueue_cancel(
        self,
        command: GatewayCancelCommand,
        *,
        request_fence: Callable[..., Any],
        finalize: Callable[[], None],
        queue_wait_seconds: float | None = None,
    ) -> str:
        """Queue a cancellation on the same owner, using reserved capacity."""
        return self._enqueue(
            command, GatewayCancelCommand, request_fence, finalize, queue_wait_seconds
        )

    def _enqueue(self, command, command_type, request_fence, finalize, queue_wait_seconds):
        self._check_pid()
        if not callable(finalize):
            raise GatewayDirectOwnerError("INVALID_FINALIZER")
        try:
            if type(command) is not command_type or not callable(request_fence):
                raise GatewayDirectOwnerError("INVALID_COMMAND")
            wait = (
                self._max_wait
                if queue_wait_seconds is None
                else _duration(queue_wait_seconds, maximum=self._max_wait)
            )
        except Exception:
            self._finalize(finalize)
            raise
        key = _command_key(command)
        is_cancel = command_type is GatewayCancelCommand
        now = time.monotonic()
        with self._condition:
            if self._closing or self._binding is None or self._done.is_set():
                result = "CLOSING"
            elif is_cancel and self._cancel_dispatcher is None:
                result = "UNSUPPORTED"
            elif key in self._pending:
                result = "DUPLICATE" if self._pending[key] == command else "CONFLICT"
            elif self._active_since is not None and now - self._active_since >= self._busy_timeout:
                result = "BUSY"
            elif len(self._cancel_queue if is_cancel else self._queue) >= (
                self._cancel_queue_size if is_cancel else self._queue_size
            ):
                result = "FULL"
            else:
                self._pending[key] = command
                (self._cancel_queue if is_cancel else self._queue).append(
                    _Task(command, request_fence, finalize, now + wait)
                )
                self._condition.notify()
                return "QUEUED"
        self._finalize(finalize)
        return result

    def status(self) -> GatewayDirectOwnerStatus:
        self._check_pid()
        with self._condition:
            thread = self._thread
            alive = thread is not None and thread.is_alive()
            busy = (
                self._active_since is not None
                and time.monotonic() - self._active_since >= self._busy_timeout
            )
            return GatewayDirectOwnerStatus(
                len(self._queue) + len(self._cancel_queue),
                self._active_since is not None,
                self._binding is not None
                and not self._closing
                and alive
                and not busy
                and len(self._queue) < self._queue_size,
                alive,
                self._failure,
            )

    def binding(self) -> GatewayDirectOwnerBinding | None:
        self._check_pid()
        with self._condition:
            return self._binding if not self._closing and not self._done.is_set() else None

    def is_bound_to(self, journal: object) -> bool:
        self._check_pid()
        with self._condition:
            return (
                not self._closing
                and self._binding is not None
                and self._journal is journal
                and self._dispatcher is not None
                and self._dispatcher.journal is journal
            )

    def provide_startup_cleanup(self, cleanup: Callable[[], None]) -> None:
        """Transfer a trusted missing startup cleanup handle to the owner thread."""
        self._check_pid()
        if not callable(cleanup):
            raise GatewayDirectOwnerError("INVALID_CLEANUP")
        with self._condition:
            if self._failure != "cleanup_unknown" or self._startup_cleanup is not None:
                raise GatewayDirectOwnerError("CLEANUP_NOT_EXPECTED")
            self._startup_cleanup = cleanup
            self._condition.notify_all()

    def close(self, timeout_seconds: float) -> bool:
        self._check_pid()
        timeout = _duration(timeout_seconds, maximum=3600)
        with self._condition:
            self._closing = True
            dropped = tuple(self._queue) + tuple(self._cancel_queue)
            self._queue.clear()
            self._cancel_queue.clear()
            for task in dropped:
                self._pending.pop(_command_key(task.command), None)
            self._cleanup_retry += 1
            thread = self._thread
            self._condition.notify_all()
        for task in dropped:
            self._finalize(task.finalize)
        if thread is None:
            return True
        return self._done.wait(timeout)

    def _finalize(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except BaseException:
            with self._condition:
                self._failure = "finalizer_failed"

    def _run(self) -> None:
        resources: GatewayDirectOwnerResources | None = None
        try:
            resources = self._factory()
            from bt_api_gateway.direct_command_journal import (
                GatewayDirectCommandJournal,
            )
            from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher

            if (
                type(resources) is not GatewayDirectOwnerResources
                or type(resources.dispatcher) is not GatewayDirectDispatcher
                or type(resources.journal) is not GatewayDirectCommandJournal
                or not callable(resources.close)
                or (resources.maintain is None) != (resources.maintenance_interval_seconds is None)
                or (resources.maintain is not None and not callable(resources.maintain))
                or resources.dispatcher.journal is not resources.journal
            ):
                raise GatewayDirectOwnerError("INVALID_RESOURCES")
            if resources.maintenance_interval_seconds is not None:
                interval = _duration(resources.maintenance_interval_seconds, maximum=3600)
                if interval < 0.001:
                    raise GatewayDirectOwnerError("INVALID_DURATION")
            resources.dispatcher.assert_owned()
            if resources.cancel_dispatcher is not None:
                from bt_api_gateway.direct_cancel_dispatch import (
                    GatewayDirectCancelDispatcher,
                )

                if type(
                    resources.cancel_dispatcher
                ) is not GatewayDirectCancelDispatcher or not resources.cancel_dispatcher.is_bound_to(
                    resources.dispatcher
                ):
                    raise GatewayDirectOwnerError("INVALID_RESOURCES")
            binding = GatewayDirectOwnerBinding(
                resources.journal.account_scope,
                resources.journal.journal_id,
                resources.journal.generation,
            )
            with self._condition:
                self._journal, self._dispatcher, self._binding = (
                    resources.journal,
                    resources.dispatcher,
                    binding,
                )
                self._cancel_dispatcher = resources.cancel_dispatcher
                self._startup.set()
        except BaseException as exc:
            with self._condition:
                self._failure = "factory_failed"
                self._closing = True
                self._startup.set()
            if isinstance(exc, GatewayOwnerStartupCleanupError):
                self._cleanup(exc.cleanup)
            elif type(resources) is GatewayDirectOwnerResources and callable(resources.close):
                self._cleanup(resources.close)
            else:
                # The factory did not hand off a cleanup handle. It may have
                # acquired resources before failing: retain this owner until a
                # trusted caller supplies the missing cleanup responsibility.
                with self._condition:
                    self._failure = "cleanup_unknown"
                    while self._startup_cleanup is None:
                        self._condition.wait()
                    cleanup = self._startup_cleanup
                self._cleanup(cleanup)
            return
        next_maintenance = time.monotonic()
        just_maintained = False
        while True:
            with self._condition:
                while True:
                    if self._closing:
                        break
                    now = time.monotonic()
                    maintenance_due = (
                        resources.maintain is not None
                        and now >= next_maintenance
                        and (not (self._queue or self._cancel_queue) or not just_maintained)
                    )
                    if maintenance_due or self._queue or self._cancel_queue:
                        break
                    timeout = (
                        None if resources.maintain is None else max(0.0, next_maintenance - now)
                    )
                    self._condition.wait(timeout)
                if self._closing:
                    break
                if maintenance_due:
                    self._active_since = now
                    task = None
                    just_maintained = True
                else:
                    task = (self._cancel_queue if self._cancel_queue else self._queue).popleft()
                    just_maintained = False
                    key = _command_key(task.command)
                    expired = now >= task.deadline
                    if not expired:
                        self._active_since = now
            if task is None:
                try:
                    resources.maintain()
                except BaseException:
                    with self._condition:
                        self._failure = "maintenance_failed"
                        self._closing = True
                        dropped = tuple(self._queue) + tuple(self._cancel_queue)
                        self._queue.clear()
                        self._cancel_queue.clear()
                        for item in dropped:
                            self._pending.pop(_command_key(item.command), None)
                    for item in dropped:
                        self._finalize(item.finalize)
                finally:
                    with self._condition:
                        self._active_since = None
                    next_maintenance = time.monotonic() + float(
                        resources.maintenance_interval_seconds
                    )
                continue
            if expired:
                self._finalize(task.finalize)
                with self._condition:
                    self._pending.pop(key, None)
                continue
            command = task.command
            try:
                if type(command) is GatewayCancelCommand:
                    resources.cancel_dispatcher.dispatch(
                        command.strategy,
                        command.operation_id,
                        command.principal,
                        expected_version=command.expected_version,
                        request_fence=task.request_fence,
                    )
                else:
                    resources.dispatcher.dispatch(
                        command.strategy,
                        command.request_key,
                        command.principal,
                        command.reservation_id,
                        command.digest,
                        command.expected_version,
                        request_fence=task.request_fence,
                    )
            except BaseException:
                with self._condition:
                    self._failure = "dispatch_failed"
            finally:
                self._finalize(task.finalize)
                with self._condition:
                    self._active_since = None
                    self._pending.pop(key, None)
        self._cleanup(resources.close)

    def _cleanup(self, cleanup: Callable[[], None]) -> None:
        while True:
            with self._condition:
                retry = self._cleanup_retry
            try:
                cleanup()
            except BaseException:
                with self._condition:
                    self._failure = "cleanup_failed"
                    while self._cleanup_retry == retry:
                        self._condition.wait()
                continue
            with self._condition:
                self._journal = self._dispatcher = self._cancel_dispatcher = self._binding = None
            self._done.set()
            return
