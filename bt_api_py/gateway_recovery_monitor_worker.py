"""Independent bounded relay worker for a borrowed Gateway recovery journal.

It never owns the source journal, starts a provider, or proves venue continuity.
Borrowing is limited to ``read_after``: the journal's SQLite connections are
still opened in ``mode=rw``, not as filesystem read-only handles.
"""

from __future__ import annotations

import math
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_gateway.recovery_observation_journal import RecoveryObservationJournal

from bt_api_py.gateway_recovery_monitor_relay import (
    GatewayRecoveryMonitorRelay,
    GatewayRecoveryRelayError,
    GatewayRecoveryRelayStatus,
)
from bt_api_py.gateway_recovery_observation import GatewayRecoveryObservationRecorder
from bt_api_py.service_monitor_authenticated import (
    AuthenticatedMonitorSinkError,
    AuthenticatedMonitorV2Sink,
)
from bt_api_py.service_runtime import ServiceClientRuntime

_RELAY_FAILURE_CODES = frozenset(
    {
        "EVENT_TOO_LARGE",
        "CHECKPOINT_UNCERTAIN",
        "CHECKPOINT_INVALID",
        "SOURCE_HISTORY_CHANGED",
        "BINDING_CHANGED",
    }
)


def _finite_relay_failure(error):
    if type(error) is GatewayRecoveryRelayError:
        code = getattr(error, "code", None)
        if type(code) is str and code in _RELAY_FAILURE_CODES:
            return code
    if type(error) is AuthenticatedMonitorSinkError and error.code == "INGRESS_BINDING_CHANGED":
        return "BINDING_CHANGED"
    return "RELAY_OR_BINDING_UNAVAILABLE"


def _text(value):
    try:
        return (
            type(value) is str
            and bool(value)
            and value == value.strip()
            and value.isprintable()
            and len(value.encode("utf-8", "strict")) <= 256
        )
    except UnicodeError:
        return False


def _path(value):
    try:
        value = os.fspath(value)
        return (
            type(value) is str
            and bool(value)
            and value == value.strip()
            and os.path.isabs(value)
            and "\x00" not in value
        )
    except (TypeError, ValueError):
        return False


def _duration(value, maximum):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= maximum


@dataclass(frozen=True, slots=True)
class GatewayRecoveryMonitorSettings:
    """Explicit destination and source; references do not authorize trading.

    The trusted factory must only construct an unstarted ServiceClientRuntime;
    it must not connect or acquire external resources before returning. It is
    called on the worker thread with ``require_unregister_ack=True``.
    """

    runtime_factory: Callable[..., ServiceClientRuntime] = field(repr=False)
    expected_peer: WireIdentity
    scope_ref: str
    outbox_scope: str
    binding_ref: str
    source_ref: str
    journal_path: str | Path
    journal_scope_ref: str
    checkpoint_path: str | Path
    poll_interval_seconds: float = 1.0
    start_timeout_seconds: float = 5.0
    close_timeout_seconds: float = 5.0
    max_records: int = 16
    max_bytes: int = 65536

    def __post_init__(self):
        try:
            if (
                not callable(self.runtime_factory)
                or type(self.expected_peer) is not WireIdentity
                or set(vars(self.expected_peer))
                != {"role", "principal_ref", "actor_ref", "delegate_ref"}
            ):
                raise ValueError
            peer = WireIdentity(**vars(self.expected_peer))
            if (
                peer.role is not WireRole.SERVICE
                or peer.delegate_ref is not None
                or any(
                    not _text(value)
                    for value in (
                        self.scope_ref,
                        self.outbox_scope,
                        self.binding_ref,
                        self.source_ref,
                        self.journal_scope_ref,
                    )
                )
                or not _path(self.journal_path)
                or not _path(self.checkpoint_path)
                or not _duration(self.poll_interval_seconds, 60)
                or not _duration(self.start_timeout_seconds, 30)
                or not _duration(self.close_timeout_seconds, 30)
                or type(self.max_records) is not int
                or not 1 <= self.max_records <= 256
                or type(self.max_bytes) is not int
                or not 1 <= self.max_bytes <= 16 * 1024 * 1024
            ):
                raise ValueError
        except Exception:
            raise ValueError("INVALID_RECOVERY_MONITOR_SETTINGS") from None
        object.__setattr__(self, "expected_peer", peer)
        object.__setattr__(self, "journal_path", os.fspath(self.journal_path))
        object.__setattr__(self, "checkpoint_path", os.fspath(self.checkpoint_path))


class GatewayRecoveryMonitorWorker:
    """One owner thread, one runtime and one relay; no automatic replacement."""

    def __init__(self, recorder, settings):
        if (
            type(recorder) is not GatewayRecoveryObservationRecorder
            or type(settings) is not GatewayRecoveryMonitorSettings
        ):
            raise ValueError("INVALID_RECOVERY_MONITOR_WORKER")
        try:
            settings = GatewayRecoveryMonitorSettings(
                **{name: getattr(settings, name) for name in settings.__dataclass_fields__}
            )
        except Exception:
            raise ValueError("INVALID_RECOVERY_MONITOR_WORKER") from None
        self._recorder, self._settings, self._pid = recorder, settings, os.getpid()
        self._lock = threading.Lock()
        self._started = threading.Event()
        self._stop = threading.Event()
        self._retry = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="gateway-recovery-monitor", daemon=False
        )
        self._thread_started = False
        self._state, self._resource_state, self._failure = "new", "NOT_STARTED", None
        self._cursor, self._pending_count, self._relay_state = None, None, None
        self._runtime = self._sink = self._relay = self._journal = None
        self._relay_closed = False

    def _check_pid(self):
        if os.getpid() != self._pid:
            raise RuntimeError("WORKER_WRONG_PROCESS")

    def status(self):
        self._check_pid()
        with self._lock:
            return {
                "state": self._state,
                "resource_state": self._resource_state,
                "last_failure_code": self._failure,
                "cursor": self._cursor,
                "pending_count": self._pending_count,
                "last_relay_state": self._relay_state,
            }

    def _set(self, state, resource_state, failure=None):
        with self._lock:
            if state == "running" and self._stop.is_set():
                return
            self._state, self._resource_state, self._failure = state, resource_state, failure

    def start(self):
        self._check_pid()
        with self._lock:
            if self._state != "new":
                raise RuntimeError("WORKER_ALREADY_STARTED")
            self._state = "starting"
            try:
                self._thread.start()
                self._thread_started = True
            except BaseException:
                self._state, self._failure = "failed", "WORKER_START_FAILED"
                raise RuntimeError("WORKER_START_FAILED") from None
        if not self._started.wait(self._settings.start_timeout_seconds):
            raise RuntimeError("WORKER_START_UNCONFIRMED")
        outcome = self.status()
        if outcome["state"] != "running":
            raise RuntimeError(outcome["last_failure_code"] or "WORKER_START_FAILED")

    def close(self, timeout_seconds=None):
        self._check_pid()
        if threading.current_thread() is self._thread:
            raise RuntimeError("WORKER_WRONG_THREAD")
        timeout = (
            self._settings.close_timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        try:
            valid_timeout = (
                type(timeout) in (int, float) and math.isfinite(timeout) and 0 <= timeout <= 30
            )
        except (OverflowError, ValueError):
            valid_timeout = False
        if not valid_timeout:
            raise ValueError("INVALID_WORKER_CLOSE_TIMEOUT")
        with self._lock:
            if self._state == "new":
                self._state, self._resource_state = "closed", "NOT_STARTED"
                return True
            if not self._thread_started:
                return self._resource_state == "NOT_STARTED"
            if self._state == "closed":
                return not self._thread.is_alive()
            if self._state == "failed" and self._resource_state == "NOT_STARTED":
                return not self._thread.is_alive()
            self._state, self._resource_state = "stopping", "UNKNOWN"
        self._stop.set()
        self._retry.set()
        self._thread.join(timeout)
        with self._lock:
            return (
                not self._thread.is_alive()
                and self._state == "closed"
                and self._resource_state == "COMPLETE"
            )

    def _publish_relay(self, result):
        if (
            type(result) is not GatewayRecoveryRelayStatus
            or result.state
            not in ("IDLE", "BUDGET_EXHAUSTED", "BUDGET_EXCEEDED", "SINK_UNAVAILABLE", "READY")
            or type(result.cursor) is not int
            or not 0 <= result.cursor < 2**63
            or type(result.pending_count) is not int
            or result.pending_count not in (0, 1)
            or type(result.delivered) is not int
            or not 0 <= result.delivered <= self._settings.max_records
        ):
            raise ValueError("INVALID_RELAY_STATUS")
        with self._lock:
            self._cursor, self._pending_count, self._relay_state = (
                result.cursor,
                result.pending_count,
                result.state,
            )

    def _source(self):
        journal = self._recorder.borrow_journal()
        if (
            type(journal) is not RecoveryObservationJournal
            or journal.schema_version != 2
            or os.path.realpath(journal._path) != os.path.realpath(self._settings.journal_path)
            or journal._scope != self._settings.journal_scope_ref
            or self._recorder._settings.source_ref != self._settings.source_ref
        ):
            raise ValueError("INVALID_SOURCE")
        self._journal = journal

    def _run(self):
        try:
            self._work()
        except BaseException:
            if self._runtime is None:
                self._set("failed", "NOT_STARTED", "WORKER_INTERNAL_FAILURE")
                self._started.set()
            else:
                self._set("retained", "UNKNOWN", "WORKER_INTERNAL_FAILURE")
                self._started.set()
                self._stop.wait()
                self._cleanup_loop()

    def _work(self):
        try:
            self._source()
        except Exception:
            self._set("failed", "NOT_STARTED", "SOURCE_JOURNAL_UNAVAILABLE")
            self._started.set()
            return
        if self._stop.is_set():
            self._set("closed", "COMPLETE")
            self._started.set()
            return
        try:
            runtime = self._settings.runtime_factory(require_unregister_ack=True)
        except Exception:
            self._set("failed", "NOT_STARTED", "FACTORY_FAILED")
            self._started.set()
            return
        self._runtime = runtime
        try:
            if (
                type(runtime) is not ServiceClientRuntime
                or runtime._require_unregister_ack is not True
                or runtime.state != "new"
                or runtime._owner_pid != os.getpid()
                or runtime._owner_thread_id != threading.get_ident()
            ):
                raise ValueError
            if not self._stop.is_set():
                runtime.start()
            if not self._stop.is_set():
                self._sink = AuthenticatedMonitorV2Sink.__new__(AuthenticatedMonitorV2Sink)
                AuthenticatedMonitorV2Sink.__init__(
                    self._sink,
                    runtime,
                    scope_ref=self._settings.scope_ref,
                    outbox_scope=self._settings.outbox_scope,
                    binding_ref=self._settings.binding_ref,
                    source_ref=self._settings.source_ref,
                    expected_peer=self._settings.expected_peer,
                )
            if not self._stop.is_set():
                self._relay = GatewayRecoveryMonitorRelay.__new__(GatewayRecoveryMonitorRelay)
                GatewayRecoveryMonitorRelay.__init__(
                    self._relay,
                    self._journal,
                    journal_path=self._settings.journal_path,
                    scope_ref=self._settings.journal_scope_ref,
                    source_ref=self._settings.source_ref,
                    checkpoint_path=self._settings.checkpoint_path,
                    sink=self._sink,
                )
                self._publish_relay(self._relay.status())
        except Exception:
            self._set("retained", "UNKNOWN", "WORKER_SETUP_FAILED")
            self._started.set()
            self._stop.wait()
            self._cleanup_loop()
            return
        if self._stop.is_set():
            self._started.set()
            self._cleanup_loop()
            return
        self._set("running", "ACTIVE")
        self._started.set()
        while not self._stop.is_set():
            try:
                runtime.maintain()
                if self._stop.is_set():
                    break
                result = self._relay.pump(
                    max_records=self._settings.max_records, max_bytes=self._settings.max_bytes
                )
                self._publish_relay(result)
                if result.state == "SINK_UNAVAILABLE":
                    self._sink.assert_current()
            except Exception as exc:
                with self._lock:
                    self._pending_count = None
                self._set("degraded", "ACTIVE", _finite_relay_failure(exc))
                # No re-registration, recreation or automatic send after an
                # owner/binding/lease failure. Only explicit shutdown remains.
                self._stop.wait()
                break
            self._stop.wait(self._settings.poll_interval_seconds)
        self._cleanup_loop()

    def _cleanup_once(self):
        relay, runtime = self._relay, self._runtime
        if relay is not None and not self._relay_closed:
            try:
                try:
                    self._publish_relay(relay.status())
                except Exception:
                    with self._lock:
                        self._pending_count = None
                relay.close()
                if (
                    relay._closed is not True
                    or relay._db is not None
                    or relay._fd is not None
                    or relay._lock_fd is not None
                ):
                    return False
                self._relay_closed = True
            except BaseException:
                return False
        if type(runtime) is not ServiceClientRuntime:
            return False
        try:
            if runtime._require_unregister_ack is not True:
                return False
            runtime.close()
            counts = (
                runtime.cleanup_failures,
                runtime.unregister_uncertainties,
                runtime.unknown_registration_count,
                runtime.open_transport_count,
            )
            state = runtime.state
            return (
                type(state) is str
                and state == "closed"
                and runtime._require_unregister_ack is True
                and runtime._owner_pid == os.getpid()
                and runtime._owner_thread_id == threading.get_ident()
                and all(type(value) is int and value == 0 for value in counts)
            )
        except BaseException:
            return False

    def _cleanup_loop(self):
        while True:
            self._retry.clear()
            if self._cleanup_once():
                self._set("closed", "COMPLETE")
                return
            self._set("retained", "UNKNOWN", "CLEANUP_UNCERTAIN")
            self._retry.wait()
