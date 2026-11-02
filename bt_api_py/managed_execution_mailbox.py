"""Bounded synchronous mailbox for an existing managed owner thread.

The using runtime supplies and owns the thread, readiness, lease and mailbox
state. The queued/running transition controls cancellation; the mailbox does
not grant account or execution authority and starts no threads.
"""
from __future__ import annotations

import math
import queue
import threading
from collections.abc import Callable
from typing import Any


_OWNER_THREAD_CALL_QUEUE_SIZE = 32
_OWNER_THREAD_CALL_DRAIN_LIMIT = 8
_OWNER_THREAD_CALL_DEFAULT_TIMEOUT_S = 5.0
_OWNER_THREAD_CALL_MAX_TIMEOUT_S = 30.0


def _owner_thread_call_error(code: str) -> Exception:
    """Create the shared finite mailbox error without an eager base import."""

    from bt_api_base.managed_execution_runtime_protocol import OwnerThreadCallError

    return OwnerThreadCallError(code)


class _OwnerThreadCall:
    """One bounded, single-use callback queued for the CTP owner thread."""

    __slots__ = (
        "callback",
        "event",
        "lock",
        "state",
        "result",
        "error",
    )

    def __init__(self, callback: Callable[[], Any]) -> None:
        self.callback: Callable[[], Any] | None = callback
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.state = "queued"
        self.result: Any = None
        self.error: Exception | None = None


def _finish_owner_thread_call(
    call: _OwnerThreadCall,
    *,
    result: Any = None,
    error: Exception | None = None,
) -> None:
    """Publish one finite result and release the callback reference."""

    with call.lock:
        if call.state not in {"queued", "running"}:
            return
        call.callback = None
        call.result = result
        call.error = error
        call.state = "done"
        call.event.set()


class ManagedOwnerThreadMailboxMixin:
    """Use runtime-owned state for bounded owner-thread handoff."""

    def call_on_owner_thread(
        self,
        callback: Callable[[], Any],
        *,
        timeout_seconds: float = _OWNER_THREAD_CALL_DEFAULT_TIMEOUT_S,
    ) -> Any:
        """Run one synchronous callback on the existing CTP owner thread.

        Calls from other threads enter a bounded mailbox drained by
        :meth:`maintain`. A timeout cancels a callback only while it is still
        queued. Once the owner thread marks it running, timeout is explicitly
        uncertain; the caller must preserve its durable UNKNOWN latch and must
        not retry the operation.
        """

        if not callable(callback):
            raise TypeError("callback must be callable")
        if (
            type(timeout_seconds) not in (int, float)
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 0.01 <= timeout_seconds <= _OWNER_THREAD_CALL_MAX_TIMEOUT_S
        ):
            raise ValueError("timeout_seconds is outside the supported range")
        if self.owner_thread_id is None or self._owner_thread is None:
            raise _owner_thread_call_error("OWNER_THREAD_CALL_NOT_READY")
        if threading.current_thread() is self._owner_thread:
            self._require_owner_thread_calls_open()
            if self._owner_thread_call_active:
                raise _owner_thread_call_error("OWNER_THREAD_CALL_CLOSED")
            self._owner_thread_call_active = True
            try:
                return callback()
            finally:
                self._owner_thread_call_active = False

        call = _OwnerThreadCall(callback)
        with self._owner_thread_calls_lock:
            if (
                not self._owner_thread_calls_accepting
                or not self._ready
                or not self._lease_healthy
                or self._closed
            ):
                raise _owner_thread_call_error("OWNER_THREAD_CALL_NOT_READY")
            try:
                self._owner_thread_calls.put_nowait(call)
            except queue.Full:
                raise _owner_thread_call_error("OWNER_THREAD_CALL_QUEUE_FULL") from None

        if call.event.wait(float(timeout_seconds)):
            with call.lock:
                if call.state == "done":
                    if call.error is not None:
                        raise call.error
                    return call.result

        with call.lock:
            if call.state == "queued":
                call.callback = None
                call.state = "cancelled"
                raise _owner_thread_call_error(
                    "OWNER_THREAD_CALL_TIMEOUT_BEFORE_START"
                )
            if call.state == "running":
                raise _owner_thread_call_error(
                    "OWNER_THREAD_CALL_TIMEOUT_AFTER_START"
                )
            if call.state == "done":
                if call.error is not None:
                    raise call.error
                return call.result
            raise _owner_thread_call_error("OWNER_THREAD_CALL_CLOSED")


    def _require_owner_thread_calls_open(self) -> None:
        self._assert_owner_thread()
        if (
            not self._owner_thread_calls_accepting
            or not self.is_ready()
            or self._closed
        ):
            raise _owner_thread_call_error("OWNER_THREAD_CALL_NOT_READY")


    def _drain_owner_thread_calls(self) -> None:
        """Run a small, bounded batch after lease renewal on the owner thread."""

        self._assert_owner_thread()
        if not self._owner_thread_calls_accepting or not self.is_ready():
            self._reject_pending_owner_thread_calls("OWNER_THREAD_CALL_NOT_READY")
            return
        for _ in range(_OWNER_THREAD_CALL_DRAIN_LIMIT):
            try:
                call = self._owner_thread_calls.get_nowait()
            except queue.Empty:
                return
            callback: Callable[[], Any] | None = None
            with call.lock:
                if call.state == "queued":
                    callback = call.callback
                    call.state = "running"
                elif call.state == "cancelled":
                    call.callback = None
            if callback is None:
                self._owner_thread_calls.task_done()
                continue
            try:
                self._assert_owner_thread()
                if self._owner_thread_call_active:
                    _finish_owner_thread_call(
                        call,
                        error=_owner_thread_call_error("OWNER_THREAD_CALL_CLOSED"),
                    )
                    continue
                if not self.is_ready():
                    _finish_owner_thread_call(
                        call,
                        error=_owner_thread_call_error("OWNER_THREAD_CALL_NOT_READY"),
                    )
                else:
                    self._owner_thread_call_active = True
                    try:
                        result = callback()
                        _finish_owner_thread_call(call, result=result)
                    finally:
                        self._owner_thread_call_active = False
            except Exception as error:
                _finish_owner_thread_call(call, error=error)
            finally:
                self._owner_thread_calls.task_done()


    def _reject_pending_owner_thread_calls(self, reason: str) -> None:
        """Cancel queued calls without retaining their callback closures."""

        self._assert_owner_thread()
        for _ in range(_OWNER_THREAD_CALL_QUEUE_SIZE):
            try:
                call = self._owner_thread_calls.get_nowait()
            except queue.Empty:
                return
            with call.lock:
                if call.state == "queued":
                    call.callback = None
                    call.state = "done"
                    call.error = _owner_thread_call_error(reason)
                    call.event.set()
                elif call.state == "cancelled":
                    call.callback = None
            self._owner_thread_calls.task_done()

