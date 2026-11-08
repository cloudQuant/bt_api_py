"""Owner-local bounded return ingestion and cached reservation fence."""

import math
import os
import threading
import time
from contextlib import contextmanager

from bt_api_ctp.gateway_return_query import CtpGatewayReturnQueryError
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger

from bt_api_py.ctp_gateway_return_ingestion import CtpGatewayReturnIngestor
from bt_api_py.gateway_direct_service import GatewayReservationAdmissionDenied


class CtpGatewayReturnMaintenance:
    def __init__(self, session, provider, journal, guard, account_key, settings):
        self._session, self._provider, self._settings = session, provider, settings
        self._ledger = GatewayDirectExecutionLedger(journal, guard)
        self._account_key = account_key
        self._lock = threading.Lock()
        self._pid = os.getpid()
        self._owner_thread = threading.current_thread()
        self._closing = threading.Event()
        self._open = False
        self._status = "STARTING"
        self._generation = None
        self._ingestor = None
        self._done = set()
        self._stale_seen = False
        self._next_kind = "orders"
        self._next_query = 0.0
        self._rebuild()

    def _set(self, opened, status):
        with self._lock:
            if self._closing.is_set():
                self._open, self._status = False, "OWNER_CLOSED"
            else:
                self._open, self._status = opened, status

    @contextmanager
    def reservation_fence(self, _context, _order):
        # Called after SQLite transaction begins.  Nothing under this lock may
        # acquire SQLite or call native code (journal -> lock is the only order).
        with self._lock:
            if self._closing.is_set() or not self._open:
                raise GatewayReservationAdmissionDenied()
            yield

    def status(self):
        with self._lock:
            return {
                "status": "OWNER_CLOSED" if self._closing.is_set() else self._status,
                "reservation_open": self._open and not self._closing.is_set(),
            }

    def create_execution_stream(self, journal, grants, *, execution_domain_ref):
        from bt_api_py.gateway_execution_stream_service import GatewayExecutionStreamService

        return GatewayExecutionStreamService(
            self._ledger, journal, grants, execution_domain_ref=execution_domain_ref
        )

    def begin_close(self):
        self._check_pid()
        self._closing.set()

    def pause_for_reconnect(self):
        """Close the write fence and discard this generation's query pair."""
        self._check_pid()
        if threading.current_thread() is not self._owner_thread:
            raise RuntimeError("OWNER_REQUIRED")
        self._set(False, "SESSION_CHANGED")
        self._done.clear()
        self._stale_seen = False
        self._next_kind = "orders"
        self._next_query = 0.0

    def _check_pid(self):
        if os.getpid() != self._pid:
            raise RuntimeError("PROCESS_BOUNDARY")

    def close(self, timeout_seconds=5.0):
        self._check_pid()
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 <= timeout_seconds <= 3600):
            raise ValueError("INVALID_CLOSE_TIMEOUT")
        self.begin_close()
        if not self._lock.acquire(timeout=timeout_seconds):
            return False
        try:
            self._open, self._status = False, "OWNER_CLOSED"
            return True
        finally:
            self._lock.release()

    def _rebuild(self, reader=None):
        self._set(False, "SESSION_CHANGED")
        self._done.clear()
        self._stale_seen = False
        self._next_kind = "orders"
        self._next_query = 0.0
        generation = self._provider.session_generation()
        if reader is None:
            reader = self._session.create_return_query_reader()
        reader.assert_session_current()
        if self._provider.session_generation() != generation:
            raise CtpGatewayReturnQueryError("SESSION_CHANGED")
        ingestor = CtpGatewayReturnIngestor(
            reader,
            self._ledger,
            account_key=self._account_key,
            instruments=self._settings.instruments,
        )
        self._generation, self._ingestor = generation, ingestor

    def refresh_generation(self):
        if self._provider.session_generation() != self._generation:
            self._set(False, "SESSION_CHANGED")
            self._done.clear()
            reader = self._ingestor.rebind_same_native()
            self._rebuild(reader)
        else:
            self._ingestor.assert_session_current()

    def poll(self):
        self._set(False, "MAINTENANCE")
        try:
            self.refresh_generation()
            buffer = self._session.return_buffer
            status = buffer.status()
            batch = buffer.offer(limit=self._settings.callback_batch_limit)
            if batch.records:
                self._ingestor.ingest_callbacks(
                    batch, assert_current=self._session.assert_return_record_current
                )
            buffer.ack(batch)
            self._stale_seen = False
            self._ledger.reconcile_pending(limit=256)
            status = buffer.status()
            if status.gap:
                self._set(False, "SOURCE_GAP")
            elif status.record_count:
                self._set(False, "CALLBACK_PENDING")
            elif len(self._done) != 2:
                self._set(False, "QUERY_INCOMPLETE")
            else:
                self._set(True, "HEALTHY")
        except Exception as error:
            code = getattr(error, "code", "CALLBACK_FAILED")
            if code not in {"UNSUPPORTED_RETURN", "SOURCE_STALE"}:
                code = "CALLBACK_FAILED"
            if code == "SOURCE_STALE" and not self._stale_seen:
                self._done.clear()
                self._stale_seen = True
            self._set(False, code)

    def due(self, now):
        return now >= self._next_query

    def query(self):
        self._set(False, "QUERY_RUNNING")
        kind = self._next_kind
        self._next_kind = "trades" if kind == "orders" else "orders"
        try:
            self.refresh_generation()
            self._ingestor.read_and_ingest(kind, timeout=self._settings.query_timeout_seconds)
            self._done.add(kind)
            self._ledger.reconcile_pending(limit=256)
            self._next_query = (
                time.monotonic() + self._settings.query_interval_seconds
                if len(self._done) == 2
                else 0.0
            )
        except Exception:
            self._done.clear()
            self._next_query = time.monotonic() + self._settings.query_interval_seconds
            self._set(False, "QUERY_FAILED")
            return
        # A concurrent callback can arrive at any time; poll checks live state.
        self.poll()

    def dispatch_ready(self):
        """Owner-thread live check; neither queries nor dispatch share an atomic venue cut."""
        try:
            with self._lock:
                if self._closing.is_set():
                    return False
            if self._provider.session_generation() != self._generation:
                self._set(False, "SESSION_CHANGED")
                return False
            try:
                self._ingestor.assert_session_current()
            except Exception:
                self._done.clear()
                self._set(False, "SOURCE_STALE")
                return False
            status = self._session.return_buffer.status()
            if status.gap or status.record_count or len(self._done) != 2:
                self._set(False, "SOURCE_GAP" if status.gap else "CALLBACK_PENDING")
                return False
            with self._lock:
                return self._open and not self._closing.is_set()
        except Exception:
            self._set(False, "SOURCE_STALE")
            return False
