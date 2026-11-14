"""Optional in-process owner for a local-parent Monitor relay.

The factory is trusted to construct its runtime and relay on the calling owner
thread and to clean up resources if construction raises. An exception does not
prove cleanup; the owner key remains quarantined for this process. It returns an object
with ``maintain()``, ``poll()`` and ``close()`` methods; all three run on that
thread. ``close`` must return LocalParentMonitorCleanup.CONFIRMED only after
all owned resources have actually closed; other results or exceptions leave
cleanup unknown and quarantine the key. This helper does not register or start
itself in ServiceHost.

Stop/start timeouts bound only the caller's wait. A blocked factory, SQLite
operation, RPC or cleanup is not interrupted; its thread and resources remain
owned until it actually returns. The in-process key is cooperative and cannot
fence another process or an incorrectly configured distinct key. Process
restart removes this in-memory quarantine but does not prove system resources
were released. The caller must make the key unique to its source/destination.
The event reader can contend
with local intake storage; moving polling to a thread does not remove that lock.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class LocalParentMonitorOwnerError(RuntimeError):
    """A malformed or duplicate local owner request."""


class LocalParentMonitorCleanup(Enum):
    """Trusted owner's explicit confirmation that all owned resources closed."""
    CONFIRMED = 'CONFIRMED'


class LocalParentMonitorRuntimeResource:
    """Own one already-created relay and runtime on their construction thread.

    A normal ``ServiceClientRuntime.close()`` return does not itself prove its
    registrations were unregistered: the runtime retains uncertainty counters.
    Both close calls are attempted, and any exception or nonzero/unknown
    cleanup, registration, or unregister counter withholds confirmation.
    """
    def __init__(self, *, runtime, relay):
        from bt_api_py.service_local_parent_monitor import LocalParentMonitorRelay
        from bt_api_py.service_local_parent_monitor_sink import LocalParentMonitorServiceSink
        from bt_api_py.service_runtime import ServiceClientRuntime

        if (type(runtime) is not ServiceClientRuntime or type(relay) is not LocalParentMonitorRelay
                or type(relay._sink) is not LocalParentMonitorServiceSink
                or relay._sink._sink._runtime is not runtime):
            raise LocalParentMonitorOwnerError('INVALID_RESOURCE')
        self._runtime, self._relay = runtime, relay

    def maintain(self):
        self._runtime.maintain()

    def poll(self):
        return self._relay.poll()

    def close(self):
        failed = False
        try:
            self._relay.close()
        except BaseException:
            failed = True
        try:
            self._runtime.close()
        except BaseException:
            failed = True
        counters = (self._runtime.cleanup_failures, self._runtime.unregister_uncertainties,
                    self._runtime.unknown_registration_count)
        if (not failed and self._runtime.state == 'closed'
                and all(type(value) is int and value == 0 for value in counters)):
            return LocalParentMonitorCleanup.CONFIRMED
        return None


@dataclass(frozen=True, slots=True)
class LocalParentMonitorOwnerStatus:
    """``UNKNOWN`` cleanup never authorizes another owner to take over."""
    outcome: str
    phase: str
    reason: str | None = None
    cleanup: str = 'UNKNOWN'


@dataclass(slots=True)
class _OwnerSlot:
    thread: threading.Thread
    cleanup_confirmed: bool = False


_registry_lock = threading.Lock()
_owners: dict[str, _OwnerSlot] = {}


def _duration(value, *, positive=False):
    if type(value) not in (int, float):
        raise LocalParentMonitorOwnerError('INVALID_DURATION')
    try:
        seconds = float(value)
    except (OverflowError, ValueError):
        raise LocalParentMonitorOwnerError('INVALID_DURATION') from None
    if (not math.isfinite(seconds) or not 0 <= seconds <= 86400
            or (positive and seconds == 0)):
        raise LocalParentMonitorOwnerError('INVALID_DURATION')
    return seconds


class LocalParentMonitorOwner:
    """Own one factory/relay lifetime; an instance can be started only once."""

    def __init__(self, *, owner_key: str, factory: Callable[[], object], poll_interval: float = 1.0):
        if (type(owner_key) is not str or not owner_key or owner_key != owner_key.strip()
                or len(owner_key.encode('utf-8')) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in owner_key)
                or not callable(factory)):
            raise LocalParentMonitorOwnerError('INVALID_OWNER_CONFIGURATION')
        self._key, self._factory = owner_key, factory
        self._interval = _duration(poll_interval, positive=True)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._startup = threading.Event()
        self._thread: threading.Thread | None = None
        self._phase = 'NEW'
        self._reason: str | None = None
        self._cleanup = 'NOT_STARTED'

    def status(self) -> LocalParentMonitorOwnerStatus:
        with self._lock:
            thread, phase, reason, cleanup = self._thread, self._phase, self._reason, self._cleanup
            stopped_requested = self._stop.is_set()
        if thread is None:
            return LocalParentMonitorOwnerStatus('NOT_STARTED', phase, reason, cleanup)
        if thread.is_alive():
            outcome = 'RUNNING' if phase == 'RUNNING' and not stopped_requested else 'STILL_RUNNING'
            return LocalParentMonitorOwnerStatus(outcome, phase, reason, cleanup)
        return LocalParentMonitorOwnerStatus('FAILED' if reason or cleanup == 'UNKNOWN' else 'STOPPED',
                                             phase, reason, cleanup)

    def start(self, *, timeout: float) -> LocalParentMonitorOwnerStatus:
        timeout = _duration(timeout)
        with _registry_lock:
            with self._lock:
                if self._thread is not None:
                    raise LocalParentMonitorOwnerError('OWNER_ALREADY_STARTED')
                # Hold the actual Thread object until is_alive() is false. A
                # weak owner reference can disappear just before thread exit.
                for key, old_slot in tuple(_owners.items()):
                    if not old_slot.thread.is_alive() and old_slot.cleanup_confirmed:
                        del _owners[key]
                prior = _owners.get(self._key)
                if prior is not None:
                    raise LocalParentMonitorOwnerError(
                        'OWNER_KEY_BUSY' if prior.thread.is_alive() else 'OWNER_KEY_QUARANTINED')
                thread = threading.Thread(target=self._run, name='local-parent-monitor-owner', daemon=True)
                self._thread = thread
                self._phase = 'STARTING'
                self._cleanup = 'UNKNOWN'
                _owners[self._key] = _OwnerSlot(thread)
                try:
                    thread.start()
                except Exception:
                    self._reason, self._phase = 'THREAD_START_FAILED', 'FAILED'
                    self._cleanup = 'NOT_STARTED'
                    del _owners[self._key]
                    raise LocalParentMonitorOwnerError('THREAD_START_FAILED') from None
        self._startup.wait(timeout)
        return self.status()

    def stop(self, *, timeout: float) -> LocalParentMonitorOwnerStatus:
        timeout = _duration(timeout)
        with self._lock:
            thread = self._thread
            if thread is None:
                return LocalParentMonitorOwnerStatus('NOT_STARTED', 'NEW', cleanup='NOT_STARTED')
            self._stop.set()
            if self._phase in ('STARTING', 'RUNNING'):
                self._phase = 'STOPPING'
        if thread is threading.current_thread():
            raise LocalParentMonitorOwnerError('OWNER_THREAD_CANNOT_JOIN')
        thread.join(timeout)
        return self.status()

    def _run(self):
        resource = None
        reason = None
        valid = False
        cleanup_confirmed = False
        try:
            try:
                resource = self._factory()
            except BaseException:
                reason = 'FACTORY_FAILED'
                return
            try:
                valid = all(callable(getattr(resource, name, None)) for name in ('maintain', 'poll', 'close'))
            except BaseException:
                valid = False
            if not valid:
                reason = 'INVALID_RESOURCE'
                return
            with self._lock:
                if not self._stop.is_set():
                    self._phase = 'RUNNING'
            self._startup.set()
            while not self._stop.is_set():
                try:
                    resource.maintain()
                    if not self._stop.is_set():
                        resource.poll()
                except BaseException:
                    reason = 'OWNER_WORK_FAILED'
                    break
                self._stop.wait(self._interval)
        finally:
            with self._lock:
                self._phase = 'CLOSING'
            if resource is not None:
                try:
                    close = getattr(resource, 'close', None)
                    if callable(close):
                        result = close()
                        cleanup_confirmed = valid and result is LocalParentMonitorCleanup.CONFIRMED
                        if valid and not cleanup_confirmed:
                            reason = reason or 'CLOSE_UNCONFIRMED'
                    elif valid:
                        reason = reason or 'CLOSE_UNCONFIRMED'
                except BaseException:
                    reason = reason or 'CLOSE_FAILED'
            with _registry_lock:
                slot = _owners.get(self._key)
                if slot is not None and slot.thread is threading.current_thread():
                    slot.cleanup_confirmed = cleanup_confirmed
                with self._lock:
                    self._reason = reason
                    self._cleanup = 'CONFIRMED' if cleanup_confirmed else 'UNKNOWN'
                    self._phase = 'FAILED' if reason or not cleanup_confirmed else 'STOPPED'
            self._startup.set()


__all__ = ['LocalParentMonitorCleanup', 'LocalParentMonitorOwner', 'LocalParentMonitorOwnerError',
           'LocalParentMonitorOwnerStatus', 'LocalParentMonitorRuntimeResource']
