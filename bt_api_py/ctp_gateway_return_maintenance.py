"""Owner-local bounded return ingestion and cached reservation fence."""

import logging
import math
import os
import re
import threading
import time
from contextlib import contextmanager

from bt_api_ctp.gateway_return_query import CtpGatewayReturnQueryError
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger

from bt_api_py.ctp_gateway_return_ingestion import CtpGatewayReturnIngestor
from bt_api_py.gateway_direct_service import GatewayReservationAdmissionDenied

_LOG = logging.getLogger(__name__)
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
# Polling step of the blocking ``query``; the owner tick never sleeps.
_BLOCKING_QUERY_POLL_SECONDS = 0.005


class CtpGatewayReturnMaintenance:
    def __init__(self, session, provider, journal, guard, account_key, settings, *, external_classifier=None):
        self._session, self._provider, self._settings = session, provider, settings
        self._ledger = GatewayDirectExecutionLedger(journal, guard)
        self._account_key = account_key
        if external_classifier is not None and not callable(external_classifier):
            raise ValueError("INVALID_EXTERNAL_CLASSIFIER")
        self._external_classifier = external_classifier
        self._lock = threading.Lock()
        self._pid = os.getpid()
        self._owner_thread = threading.current_thread()
        self._closing = threading.Event()
        self._open = False
        self._status = "STARTING"
        self._fact_health = {"status": "UNAVAILABLE", "unattributed": None,
                             "conflict": None, "verified_external": None}
        self._generation = None
        self._ingestor = None
        self._reader = None
        # (kind, reader, request) of the one outstanding full-day query (I63-E130).
        self._request = None
        self._done = set()
        self._stale_seen = False
        self._next_kind = "orders"
        self._next_query = 0.0
        self._last_query_failure = None
        self._last_poll_ok = False
        # Both full-day queries completed for the current native session and
        # no gap, stale source or session change has been seen since.
        self._pair_seen = False
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

    def fact_status(self):
        """Observed fact classification counts, not account reconciliation proof."""
        with self._lock:
            if self._closing.is_set():
                return {"status": "UNAVAILABLE", "unattributed": None,
                        "conflict": None, "verified_external": None}
            return dict(self._fact_health)

    def _refresh_fact_status(self):
        try:
            counts = self._ledger.account_fact_health()
            if (type(counts) is not dict or set(counts) != {"unattributed", "conflict", "verified_external"}
                    or any(type(value) is not int or value < 0 for value in counts.values())):
                raise ValueError
            status = ("CONFLICT" if counts["conflict"] else
                      "INCOMPLETE" if counts["unattributed"] else "NO_UNRESOLVED_OBSERVED")
            result = {"status": status, **counts}
        except Exception:
            result = {"status": "UNAVAILABLE", "unattributed": None,
                      "conflict": None, "verified_external": None}
        with self._lock:
            self._fact_health = result

    def _clear_fact_status(self):
        with self._lock:
            self._fact_health = {"status": "UNAVAILABLE", "unattributed": None,
                                 "conflict": None, "verified_external": None}

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
        self._drop_query()
        self._set(False, "SESSION_CHANGED")
        self._clear_fact_status()
        self._done.clear()
        self._pair_seen = False
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
        # An outstanding query of the previous session is never ingested.
        self._drop_query()
        self._set(False, "SESSION_CHANGED")
        self._clear_fact_status()
        self._done.clear()
        self._pair_seen = False
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
            external_classifier=self._external_classifier,
        )
        self._generation, self._ingestor, self._reader = generation, ingestor, reader

    def refresh_generation(self):
        if self._provider.session_generation() != self._generation:
            self._set(False, "SESSION_CHANGED")
            self._done.clear()
            self._pair_seen = False
            reader = self._ingestor.rebind_same_native()
            self._rebuild(reader)
        else:
            self._ingestor.assert_session_current()

    def poll(self):
        self._set(False, "MAINTENANCE")
        self._clear_fact_status()
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
            self._refresh_fact_status()
            self._last_poll_ok = True
            status = buffer.status()
            if status.gap:
                self._pair_seen = False
                self._set(False, "SOURCE_GAP")
            elif status.record_count:
                self._set(False, "CALLBACK_PENDING")
            elif len(self._done) != 2:
                self._set(False, "QUERY_INCOMPLETE")
            else:
                self._set(True, "HEALTHY")
        except Exception as error:
            self._last_poll_ok = False
            self._clear_fact_status()
            self._log_query_failure("callbacks", error)
            code = getattr(error, "code", "CALLBACK_FAILED")
            if code not in {"UNSUPPORTED_RETURN", "SOURCE_STALE"}:
                code = "CALLBACK_FAILED"
            if code == "SOURCE_STALE" and not self._stale_seen:
                self._done.clear()
                self._pair_seen = False
                self._stale_seen = True
            self._set(False, code)

    def due(self, now):
        """A new full-day query may start now (none is outstanding)."""
        return self._request is None and now >= self._next_query

    def query_pending(self):
        """A full-day query was started and has not ended yet (owner state)."""
        return self._request is not None

    def query(self):
        """Run the next full-day query to its end on the owner thread.

        Blocks while the venue answers. The owner's maintenance uses
        ``start_query`` and ``advance_query``, which never wait (I63-E130).
        """
        self.start_query()
        while self.advance_query():
            time.sleep(_BLOCKING_QUERY_POLL_SECONDS)

    def start_query(self):
        """Begin the next full-day query (orders, then trades) without waiting.

        The query is sent when this Trader's query lane allows and answered
        over later ticks; ``advance_query`` ingests it on the owner thread. A
        query that is not answered within its timeout fails as before.
        """
        if self._request is not None:
            return
        kind = self._next_kind
        self._next_kind = "trades" if kind == "orders" else "orders"
        try:
            self.refresh_generation()
            reader = self._reader
            request = reader.begin_read(kind=kind, timeout=self._settings.query_timeout_seconds)
        except Exception as error:
            self._query_failed(kind, error)
            return
        self._request = (kind, reader, request)
        self.advance_query()

    def advance_query(self):
        """Poll the outstanding query without waiting; ingest it once answered.

        Returns whether it is still outstanding. Ingestion, the journal and
        the return buffer stay on this owner thread; while the venue answers,
        reservations and dispatch follow the source's health as between queries.
        """
        if self._request is None:
            return False
        kind, reader, request = self._request
        if self._closing.is_set():
            self._drop_query()
            return False
        try:
            # A changed native session rebuilds the reader and drops the query.
            self.refresh_generation()
            if self._request is None:
                return False
            if not reader.poll_read(request):
                return True
        except Exception as error:
            self._drop_query()
            self._query_failed(kind, error)
            return False
        self._request = None
        self._set(False, "QUERY_RUNNING")
        self._clear_fact_status()
        try:
            # The reader hands over the batch it just sealed; no native call.
            self._ingestor.read_and_ingest(kind, timeout=self._settings.query_timeout_seconds)
            self._query_succeeded(kind)
        except Exception as error:
            self._query_failed(kind, error)
            return False
        # A concurrent callback can arrive at any time; poll checks live state.
        self.poll()
        return False

    def _drop_query(self):
        request, self._request = self._request, None
        if request is not None:
            try:
                request[1].cancel_read(request[2])
            except Exception as error:
                # Only a foreign thread or process fails here; nothing is ingested.
                _LOG.debug("CTP return query could not be sealed locally: %s",
                           type(error).__name__)

    def _query_succeeded(self, kind):
        self._last_query_failure = None
        self._done.add(kind)
        if len(self._done) == 2:
            self._pair_seen = True
        self._ledger.reconcile_pending(limit=256)
        self._next_query = (
            time.monotonic() + self._settings.query_interval_seconds
            if len(self._done) == 2
            else 0.0
        )

    def _query_failed(self, kind, error):
        self._clear_fact_status()
        self._done.clear()
        self._next_query = time.monotonic() + self._settings.query_interval_seconds
        self._set(False, "QUERY_FAILED")
        self._log_query_failure(kind, error)

    def _log_query_failure(self, kind, error):
        """Name a failing return query or callback batch once per distinct cause (I63-E097).

        Repeated failures otherwise surface only as QUERY_INCOMPLETE. Only the
        query kind, exception type and a finite code are logged, never values.
        """
        # Wrappers re-raise "from None"; the original stays on __context__.
        chain, seen, current = [], set(), error
        while current is not None and id(current) not in seen and len(chain) < 4:
            seen.add(id(current))
            code = getattr(current, "code", None)
            chain.append(type(current).__name__ + "("
                         + (code if type(code) is str and _CODE.fullmatch(code) else "-") + ")")
            current = current.__cause__ or current.__context__
        cause = " <- ".join(chain)
        if cause == getattr(self, "_last_query_failure", None):
            _LOG.debug("CTP return query %s failed again: %s", kind, cause)
            return
        self._last_query_failure = cause
        _LOG.warning("CTP return query %s failed: %s", kind, cause)

    def ledger_continuous(self):
        """Owner-thread check that no return of the current native session is lost.

        True once both full-day queries of this native generation completed and
        no gap, stale source or failed callback batch has been seen since.
        Unlike ``dispatch_ready`` it tolerates callbacks that still wait in the
        buffer (a ledger that lags by a callback only under-reports, a ledger
        with a hole does not) and a later periodic query that failed or timed
        out: that query only re-reads what the intact callback stream already
        delivered, so its failure loses nothing (SimNow answers some order
        queries late, which otherwise made every strategy position unreadable
        until the next query round). It is not a venue cut.

        Takes no lock: the journal calls this while the same thread already
        holds ``reservation_fence`` (a plain lock), so acquiring it here would
        block that thread on itself for good.
        """
        return self.ledger_discontinuity() is None

    def ledger_discontinuity(self):
        """``None`` when ``ledger_continuous()`` holds, else the name of the first condition that does not.

        For logs only (I63-E149): a consumer that reports "returns not continuous" could not
        say why. The answer is a finite code and never changes what ``ledger_continuous`` decides.
        """
        try:
            if threading.current_thread() is not self._owner_thread:
                return "OWNER_REQUIRED"
            if self._closing.is_set():
                return "CLOSING"
            if self._provider.session_generation() != self._generation:
                return "SESSION_CHANGED"
            self._ingestor.assert_session_current()
            status = self._session.return_buffer.status()
            if status.gap:
                return "SOURCE_GAP"
            if not self._pair_seen:
                return "QUERY_PAIR_INCOMPLETE"
            if not self._last_poll_ok:
                return "CALLBACK_FAILED"
            if self._stale_seen:
                return "SOURCE_STALE"
            return None
        except Exception as error:
            code = getattr(error, "code", None)
            return code if type(code) is str and code.isupper() and len(code) <= 64 else "CHECK_FAILED"

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
