"""Explicit owner-local coordination of one CTP order query and receipt cut.

A candidate is a local observation only. It does not prove pre-begin history,
venue atomicity, source continuity, or authorization. No production profile
selects this coordinator. The caller schedules both this object and the same
return maintenance on the same owner thread.
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
from dataclasses import dataclass, replace

from bt_api_ctp.gateway_session import CtpGatewaySession, CtpGatewaySessionError
from bt_api_ctp.registered_sim_session import CtpQuerySessionBinding
from bt_api_gateway.observed_order_source import ObservedOrderQueryCut, ObservedOrderSourceProgress

from bt_api_py.ctp_gateway_observed_order_source import CtpGatewayObservedOrderSource
from bt_api_py.ctp_gateway_return_maintenance import CtpGatewayReturnMaintenance

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MAX_ROWS = 10_000


class CtpGatewayOrderCutError(RuntimeError):
    """A finite, value-free local coordination failure."""


class _CutBusyError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CtpGatewayOrderCutStatus:
    state: str
    reason: str | None
    completed_rows: int
    total_rows: int | None
    pin_retained: bool | None

    @property
    def authorizes_trading(self) -> bool:
        return False

    @property
    def venue_completeness_proven(self) -> bool:
        return False


class CtpGatewayOrderCutCoordinator:
    """One explicitly selected query bound to one session, tracker and ledger.

    ``advance`` performs at most one query poll or one row ingestion. Native
    submission and journal writes can themselves block; no thread or wait loop
    is added here. Normal maintenance polling is scheduled separately.
    """

    def __init__(self, session, maintenance, *, timeout: float):
        if (
            type(session) is not CtpGatewaySession
            or type(maintenance) is not CtpGatewayReturnMaintenance
            or maintenance._session is not session
            or maintenance._observed_order_source_enabled is not True
            or type(maintenance._observed_order_source) is not CtpGatewayObservedOrderSource
            or type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 0.05 <= timeout <= 300
        ):
            raise CtpGatewayOrderCutError("ORDER_CUT_CONFIGURATION_INVALID")
        tracker = maintenance._observed_order_source
        if (
            maintenance._ingestor is None
            or maintenance._reader is None
            or not tracker.matches(maintenance._reader, maintenance._ledger)
            or tracker._session is not session
            or type(tracker._binding) is not CtpQuerySessionBinding
            or maintenance._reader._binding is not tracker._binding
            or maintenance._ingestor._observed_order_source is not tracker
        ):
            raise CtpGatewayOrderCutError("ORDER_CUT_CONFIGURATION_INVALID")
        self._session, self._maintenance, self._tracker = session, maintenance, tracker
        self._reader, self._ingestor, self._ledger = (
            maintenance._reader,
            maintenance._ingestor,
            maintenance._ledger,
        )
        self._binding = tracker._binding
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._timeout = float(timeout)
        self._state = "IDLE"
        self._reason = None
        self._handle = None
        self._result = None
        self._cut = None
        self._digests: list[str] = []
        self._deadline = None
        self._gap_committed = False
        self._candidate = None
        self._custody_unknown = False

    def _check_owner(self):
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise CtpGatewayOrderCutError("ORDER_CUT_WRONG_OWNER")

    def _same_components(self, *, allow_maintenance_query=False):
        m = self._maintenance
        return bool(
            m._session is self._session
            and m._observed_order_source is self._tracker
            and m._reader is self._reader
            and m._ingestor is self._ingestor
            and m._ledger is self._ledger
            and (allow_maintenance_query or m._request is None)
            and not m._closing.is_set()
            and self._tracker._binding is self._binding
            and self._tracker.matches(self._reader, self._ledger)
            and self._ingestor._observed_order_source is self._tracker
            and self._tracker._session is self._session
        )

    def status(self) -> CtpGatewayOrderCutStatus:
        """Read local state only; no query, database access or retry is performed."""
        self._check_owner()
        total = None if self._result is None else len(self._result.records)
        return CtpGatewayOrderCutStatus(
            self._state,
            self._reason,
            len(self._digests),
            total,
            None if self._custody_unknown else self._handle is not None,
        )

    def begin(self) -> bool:
        """Reserve the native query; False on busy or a failed attempt."""
        self._check_owner()
        if self._state != "IDLE":
            raise CtpGatewayOrderCutError("ORDER_CUT_ALREADY_STARTED")
        attempted = False
        try:
            if (
                not self._same_components()
                or not callable(getattr(self._tracker, "ingest_query_order_row", None))
                or not callable(getattr(self._tracker, "verify_next_query_receipt", None))
                or not callable(getattr(self._tracker, "record_query_cut", None))
                or self._session._order_query_cut is not None
            ):
                raise ValueError
            now = time.monotonic()
            if type(now) is not float or not math.isfinite(now) or now < 0:
                raise ValueError
            self._deadline = now + self._timeout
            if not math.isfinite(self._deadline):
                raise ValueError
            attempted = True
            self._handle = self._session.begin_order_query_cut(timeout=self._timeout)
            self._state = "QUERYING"
            self._reason = None
            return True
        except Exception as error:
            if type(error) is CtpGatewaySessionError and error.code == "ORDER_CUT_BUSY":
                self._reason = "BUSY"
                return False
            if attempted and self._handle is None:
                try:
                    pending = self._session._order_query_cut
                    if pending is not None and pending.session is self._session:
                        self._handle = pending.handle
                except Exception:
                    # Custody cannot be determined: never present this as closed.
                    self._state = "UNKNOWN"
                    self._reason = "BEGIN_CUSTODY_UNKNOWN"
                    self._custody_unknown = True
                    return False
            self._fail("BEGIN_FAILED")
            return False

    def _fail(self, reason):
        self._reason = reason
        try:
            # Invalidate the original source, even if maintenance has already
            # replaced it. Failure to persist is UNKNOWN and retains the pin.
            self._tracker.invalidate()
            if self._current_progress().status != "GAP":
                raise ValueError("gap unconfirmed")
            self._gap_committed = True
        except Exception:
            self._gap_committed = False
        self._state = "GAP" if self._gap_committed else "UNKNOWN"

    def _current_cut(self):
        if not self._same_components() or self._handle is None or self._result is None:
            return None
        owner = self._session._connection_owner
        if not self._session._connection_owner_matches(owner):
            return None
        if not owner.query_lock.acquire(blocking=False):
            raise _CutBusyError
        try:
            return self._session.verify_order_query_cut(self._handle, self._result)
        finally:
            owner.query_lock.release()

    def _current_progress(self):
        progress = self._tracker.status()
        journal = self._ledger.journal
        if (
            type(progress) is not ObservedOrderSourceProgress
            or progress.account != self._ledger.account_scope
            or progress.journal_id != journal.journal_id
            or progress.owner_generation != journal.generation
            or progress.source_ref != self._binding.source_ref
            or progress.session_ref != self._binding.generation
            or progress.trading_day != self._binding.trading_day
        ):
            raise ValueError("source progress changed")
        return progress

    def advance(self) -> bool:
        """Advance one bounded step; True means more owner work remains."""
        self._check_owner()
        if self._state in {"IDLE", "CANDIDATE", "GAP", "UNKNOWN", "CLOSED"}:
            return False
        if self._state == "CLOSE_PENDING":
            try:
                now = time.monotonic()
                if (
                    type(now) is not float
                    or not math.isfinite(now)
                    or self._deadline is None
                    or now >= self._deadline
                    or not self._same_components()
                ):
                    raise ValueError
            except Exception:
                self._fail("SOURCE_OR_QUERY_FAILED")
                return False
            return self._finish_close()
        try:
            now = time.monotonic()
            if (
                type(now) is not float
                or not math.isfinite(now)
                or self._deadline is None
                or now >= self._deadline
                or not self._same_components()
            ):
                raise ValueError("source or deadline")
            if self._state == "QUERYING":
                result = self._session.poll_order_query_cut(self._handle)
                if result is None:
                    return True
                self._result = result
                if len(result.records) > _MAX_ROWS:
                    raise ValueError("row capacity")
                cut = self._current_cut()
                if cut is None:
                    raise ValueError("unverified native cut")
                self._cut = cut
                self._state = "PROJECTING" if result.records else "WAITING_RECEIPTS"
                return True
            cut = self._current_cut()
            if (
                cut is None
                or self._cut is None
                or cut.begin_sequence != self._cut.begin_sequence
                or cut.end_sequence != self._cut.end_sequence
                or len(cut.records) != len(self._cut.records)
                or any(
                    left is not right
                    for left, right in zip(cut.records, self._cut.records, strict=True)
                )
            ):
                raise ValueError("native cut changed")
            if self._state == "PROJECTING":
                index = len(self._digests)
                digest = self._tracker.ingest_query_order_row(
                    self._ingestor, handle=self._handle, result=self._result, row_index=index
                )
                if digest is None:
                    # Tracker returns None only before any write, e.g. a
                    # nonblocking native-lock miss; uncertain commits raise.
                    return True
                if type(digest) is not str or _HASH.fullmatch(digest) is None:
                    raise ValueError("invalid row receipt")
                try:
                    post_write_cut = self._current_cut()
                except _CutBusyError:
                    raise ValueError("post-write verification unavailable") from None
                if post_write_cut is None:
                    raise ValueError("native cut changed")
                self._digests.append(digest)
                if len(self._digests) == len(self._result.records):
                    self._state = "WAITING_RECEIPTS"
                return True
            if self._state in {"WAITING_RECEIPTS", "VERIFYING_RECEIPTS", "COMMITTING"}:
                progress = self._current_progress()
                if progress.status == "GAP" or progress.base_sequence > cut.begin_sequence:
                    raise ValueError("source gap")
                if progress.source_prefix < cut.end_sequence:
                    return True
                if self._state != "COMMITTING":
                    verified = self._tracker.verify_next_query_receipt(
                        handle=self._handle, result=self._result
                    )
                    if verified is None:
                        return True
                    if type(verified) is not bool:
                        raise ValueError("invalid receipt verification")
                    self._state = "COMMITTING" if verified else "VERIFYING_RECEIPTS"
                    return True
                candidate = self._tracker.record_query_cut(
                    handle=self._handle, result=self._result, fact_digests=tuple(self._digests)
                )
                if candidate is None:
                    return True
                try:
                    post_write_cut = self._current_cut()
                except _CutBusyError:
                    raise ValueError("post-write verification unavailable") from None
                if (
                    type(candidate) is not ObservedOrderQueryCut
                    or candidate.status != "CANDIDATE"
                    or candidate.begin_sequence != cut.begin_sequence
                    or candidate.end_sequence != cut.end_sequence
                    or candidate.query_scope != "ctp.futures.account_orders.v1"
                    or candidate.query_digest != self._result.query_source.records_sha256
                    or candidate.query_fact_digests != tuple(self._digests)
                    or candidate.fact_epoch != progress.fact_epoch
                    or candidate.source_prefix < cut.end_sequence
                    or candidate.base_sequence > cut.begin_sequence
                    or post_write_cut is None
                ):
                    raise ValueError("candidate invalid")
                self._candidate = candidate
                self._state = "CLOSE_PENDING"
                return self._finish_close()
            raise ValueError("invalid state")
        except _CutBusyError:
            return True
        except Exception:
            self._fail("SOURCE_OR_QUERY_FAILED")
            return False

    def _finish_close(self) -> bool:
        try:
            if self._current_cut() is None:
                raise ValueError
            if not self._session.close_order_query_cut(self._handle):
                # The committed candidate is not exposed as current if its
                # original pin cannot be released and its state is unknown.
                self._fail("CLOSE_UNCONFIRMED")
                return False
            self._handle = None
            self._state = "CANDIDATE"
            return False
        except _CutBusyError:
            return True
        except Exception:
            self._fail("CLOSE_UNCONFIRMED")
            return False

    def candidate(self) -> ObservedOrderQueryCut | None:
        self._check_owner()
        if self._state != "CANDIDATE":
            return None
        try:
            now = time.monotonic()
            source = self._result.query_source
            if (
                type(now) is not float
                or not math.isfinite(now)
                or self._deadline is None
                or now >= self._deadline
                or type(source.completed_monotonic) not in (int, float)
                or not math.isfinite(source.completed_monotonic)
                or now < source.completed_monotonic
                or type(source.trusted_expires_monotonic) not in (int, float)
                or not math.isfinite(source.trusted_expires_monotonic)
                or now > source.trusted_expires_monotonic
                or not self._same_components(allow_maintenance_query=True)
            ):
                raise ValueError
            binding = self._binding

            def check_binding():
                current = self._session.capture_calendar_session()
                if (
                    current._issuer is not binding._issuer
                    or current._source_tags != binding._source_tags
                    or current.source_ref != binding.source_ref
                    or current.generation != binding.generation
                    or current.trading_day != binding.trading_day
                    or current.connection_generation != binding.connection_generation
                    or current.account_fingerprint != binding.account_fingerprint
                ):
                    raise ValueError

            check_binding()
            progress = self._current_progress()
            if (
                progress.status == "GAP"
                or self._candidate is None
                or type(progress.source_history_has_gap) is not bool
                or type(self._candidate.source_history_has_gap) is not bool
                or progress.fact_epoch != self._candidate.fact_epoch
                or progress.source_prefix < self._candidate.end_sequence
                or progress.base_sequence != self._candidate.base_sequence
            ):
                raise ValueError
            check_binding()
            now = time.monotonic()
            if (
                type(now) is not float
                or not math.isfinite(now)
                or now < source.completed_monotonic
                or now >= self._deadline
                or now > source.trusted_expires_monotonic
                or not self._same_components(allow_maintenance_query=True)
            ):
                raise ValueError
            if progress.source_history_has_gap and not self._candidate.source_history_has_gap:
                return replace(self._candidate, source_history_has_gap=True)
            return self._candidate
        except CtpGatewaySessionError as error:
            if error.code == "CALENDAR_SESSION_BUSY":
                return None
            self._fail("SOURCE_OR_QUERY_FAILED")
            return None
        except Exception:
            self._fail("SOURCE_OR_QUERY_FAILED")
            return None

    def close(self) -> bool:
        """Fail closed on cancellation; retain original custody on uncertainty."""
        self._check_owner()
        if self._state == "CLOSED":
            return True
        if self._custody_unknown:
            return False
        if self._state not in {"IDLE", "CANDIDATE"} and not self._gap_committed:
            self._fail(self._reason or "CANCELLED")
            if not self._gap_committed:
                return False
        if self._handle is not None:
            try:
                if not self._session.close_order_query_cut(self._handle):
                    return False
            except Exception:
                return False
            self._handle = None
        self._state = "CLOSED"
        return True
