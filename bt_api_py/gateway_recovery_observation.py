"""Local, bounded persistence of owner-captured market recovery observations.

The timestamps in these observations are local process values, not a wire protocol
or proof of a continuously healthy source.
"""
from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from bt_api_base.market_recovery_observation import RecoveryObservationPage
from bt_api_gateway.recovery_observation_journal import RecoveryObservationJournal

from bt_api_py.gateway_direct_owner import GatewayOwnerServiceCallError


def _ref(value):
    try:
        return (type(value) is str and bool(value) and value == value.strip()
                and value.isprintable() and len(value.encode('utf-8', 'strict')) <= 256)
    except UnicodeError:
        return False


def _duration(value, maximum):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= maximum


@dataclass(frozen=True, slots=True)
class GatewayRecoveryObservationSettings:
    journal_path: str | Path
    scope_ref: str
    source_ref: str
    source_capacity: int
    page_limit: int
    max_events: int
    max_bytes: int
    sqlite_timeout_seconds: float
    poll_interval_seconds: float
    owner_wait_seconds: float
    close_timeout_seconds: float
    qualification_mode: str = 'disabled'
    clock_max_interval_ns: int | None = None

    def __post_init__(self):
        try:
            path = os.fspath(self.journal_path)
            valid_path = type(path) is str and bool(path) and os.path.isabs(path) and '\x00' not in path
        except (TypeError, ValueError):
            valid_path = False
        if (not valid_path or not _ref(self.scope_ref) or not _ref(self.source_ref)
                or type(self.source_capacity) is not int or not 2 <= self.source_capacity <= 4096
                or type(self.page_limit) is not int or not 1 <= self.page_limit <= 256
                or type(self.max_events) is not int or not 1 <= self.max_events <= 1_000_000
                or type(self.max_bytes) is not int or not 1 <= self.max_bytes <= 1 << 30
                or not _duration(self.sqlite_timeout_seconds, 5)
                or not _duration(self.poll_interval_seconds, 60)
                or not _duration(self.owner_wait_seconds, 5)
                or not _duration(self.close_timeout_seconds, 30)
                or type(self.qualification_mode) is not str
                or self.qualification_mode not in ('disabled', 'local_clock_v1')
                or (self.qualification_mode == 'disabled' and self.clock_max_interval_ns is not None)
                or (self.qualification_mode == 'local_clock_v1' and (
                    type(self.clock_max_interval_ns) is not int
                    or not 1 <= self.clock_max_interval_ns <= (1 << 63) - 1))):
            raise ValueError('INVALID_RECOVERY_OBSERVATION_SETTINGS')


class GatewayRecoveryObservationRecorder:
    """Single worker and at most one outstanding owner call and pending page."""

    def __init__(self, owner, capture, settings):
        if type(settings) is not GatewayRecoveryObservationSettings or not callable(capture):
            raise ValueError('INVALID_RECORDER_CONFIGURATION')
        self._owner, self._capture, self._settings = owner, capture, settings
        self._pid = os.getpid()
        self._journal = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._started = threading.Event()
        self._stop = False
        self._state = 'new'
        self._failure = None
        self._handle = None
        self._page = None
        self._cursor = 0
        self._epoch = None
        self._stop_marker_committed = False
        self._thread = threading.Thread(target=self._run, name='gateway-recovery-recorder', daemon=False)
        self._thread_started = False

    def _set(self, state, failure=None):
        with self._lock:
            self._state, self._failure = state, failure

    def status(self):
        with self._lock:
            return {'state': self._state, 'ready': self._state == 'ready',
                    'degraded': self._state == 'degraded', 'last_failure_code': self._failure,
                    'pending_calls': int(self._handle is not None),
                    'pending_pages': int(self._page is not None),
                    'pending_stop_marker': int(self._stop and self._epoch is not None
                                               and not self._stop_marker_committed)}

    def borrow_journal(self):
        """Borrow the original published journal; callers may only use read_after.

        The underlying journal uses fresh mode=rw SQLite connections. Borrowing
        does not create a read-only filesystem handle, authorize writes or grant
        any source continuity claim. The same object remains available after
        recorder close so committed stop markers can be drained.
        """
        if os.getpid() != self._pid:
            raise RuntimeError('RECORDER_WRONG_PROCESS')
        with self._lock:
            if self._journal is None or not self._started.is_set():
                raise RuntimeError('RECORDER_JOURNAL_UNAVAILABLE')
            return self._journal

    def start(self):
        with self._lock:
            if self._state != 'new':
                raise RuntimeError('RECORDER_ALREADY_STARTED')
            self._owner.claim_service_port('gateway.recovery_observation', self)
            self._state = 'starting'
            try:
                self._thread.start()
                self._thread_started = True
            except BaseException:
                self._state = 'failed'
                self._failure = 'RECORDER_START_FAILED'
                raise
        if not self._started.wait(self._settings.close_timeout_seconds):
            raise RuntimeError('RECORDER_START_UNCONFIRMED')
        if self.status()['state'] == 'failed':
            raise RuntimeError('RECORDER_START_FAILED')

    def close(self):
        with self._lock:
            if self._state == 'new':
                self._state = 'closed'
                return True
            if not self._thread_started:
                return self._state == 'failed'
            self._stop = True
        self._wake.set()
        self._thread.join(self._settings.close_timeout_seconds)
        with self._lock:
            return (not self._thread.is_alive() and self._state in ('closed', 'failed')
                    and self._handle is None and self._page is None
                    and (self._epoch is None or self._stop_marker_committed))

    def _pause(self):
        self._wake.wait(self._settings.poll_interval_seconds)
        self._wake.clear()

    def _run(self):
        try:
            self._work()
        except BaseException:
            self._set('failed', 'RECORDER_INTERNAL_FAILURE')
            self._started.set()

    def _work(self):
        try:
            qualification_options = (
                {'qualification_mode': 'local_clock_v1'}
                if self._settings.qualification_mode == 'local_clock_v1' else {}
            )
            journal = RecoveryObservationJournal(
                self._settings.journal_path, scope_ref=self._settings.scope_ref,
                max_events=self._settings.max_events, max_bytes=self._settings.max_bytes,
                timeout_seconds=self._settings.sqlite_timeout_seconds, **qualification_options)
        except Exception:
            self._set('failed', 'JOURNAL_START_FAILED')
            self._started.set()
            return
        with self._lock:
            self._journal = journal
        self._started.set()
        while True:
            with self._lock:
                stopping = self._stop
                handle, page = self._handle, self._page
            if page is not None:
                try:
                    qualified = self._settings.qualification_mode == 'local_clock_v1'
                    raw = page.page if qualified else page
                    previous = journal.checkpoint(self._settings.source_ref, raw.source_epoch)
                    if previous is not None and previous > raw.last_sequence:
                        raise ValueError
                    if qualified:
                        checkpoint = journal.append_page(
                            self._settings.source_ref, raw, qualifications=page.qualifications)
                    else:
                        checkpoint = journal.append_page(self._settings.source_ref, raw)
                    expected = (raw.observations[-1].sequence if raw.observations else
                                max(self._cursor, raw.first_available_sequence - 1))
                    if (type(checkpoint) is not int or checkpoint != max(previous or 0, expected)
                            or checkpoint > raw.last_sequence):
                        raise ValueError
                    with self._lock:
                        self._cursor = checkpoint
                        self._page = None
                    self._set('ready')
                except Exception:
                    self._set('degraded', 'JOURNAL_WRITE_FAILED')
                    self._pause()
                continue
            if handle is not None:
                try:
                    page = handle.result(timeout_seconds=self._settings.owner_wait_seconds)
                except GatewayOwnerServiceCallError as exc:
                    if exc.code in ('SERVICE_CALL_TIMEOUT_QUEUED', 'SERVICE_CALL_TIMEOUT_RUNNING'):
                        if stopping and handle.cancel():
                            with self._lock:
                                self._handle = None
                        else:
                            self._set('degraded', 'OWNER_CALL_TIMEOUT')
                        continue
                    with self._lock:
                        self._handle = None
                    self._set('degraded', 'OWNER_CALL_FAILED')
                    self._pause()
                    continue
                except Exception:
                    # An unexpected wait failure does not establish whether the
                    # original call ran. Keep its handle, including on close.
                    self._set('degraded', 'OWNER_CALL_UNCONFIRMED')
                    self._pause()
                    continue
                try:
                    if self._settings.qualification_mode == 'local_clock_v1':
                        from bt_api_py.gateway_recovery_qualified_capture import (
                            GatewayQualifiedRecoveryPage,
                        )
                        if type(page) is not GatewayQualifiedRecoveryPage:
                            raise ValueError
                        page.__post_init__()
                        raw = page.page
                    else:
                        raw = page
                    if (type(raw) is not RecoveryObservationPage or raw.owner_pid != os.getpid()
                            or raw.after_sequence != self._cursor
                            or (self._epoch is not None and raw.source_epoch != self._epoch)):
                        raise ValueError
                    raw.__post_init__()
                    with self._lock:
                        self._epoch = raw.source_epoch
                        self._handle = None
                        self._page = page
                except Exception:
                    with self._lock:
                        self._handle = None
                    self._set('degraded', 'INVALID_SOURCE_PAGE')
                    self._pause()
                continue
            if stopping:
                if self._epoch is not None:
                    try:
                        journal.append_capture_stopped(
                            self._settings.source_ref, self._epoch,
                            owner_pid=os.getpid(), checkpoint=self._cursor)
                        with self._lock:
                            self._stop_marker_committed = True
                    except Exception:
                        self._set('degraded', 'STOP_MARKER_WRITE_FAILED')
                        self._pause()
                        continue
                self._set('closed')
                return
            try:
                with self._lock:
                    if self._stop:
                        continue
                    cursor = self._cursor
                    handle = self._owner.submit_service_call(
                        lambda cursor=cursor: self._capture(cursor, self._settings.page_limit))
                    self._handle = handle
            except Exception:
                self._set('degraded', 'OWNER_CALL_FAILED')
                self._pause()
                continue
            self._pause()
