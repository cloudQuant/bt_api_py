"""Owner-local candidate cuts for a Gateway account comparison.

The installed source is trusted application code, not an RPC payload.  It must
prove the provider query coverage, session/day continuity, each trade's
inclusion in the position snapshot, and the absence of a later relevant event.
This module does not install a source, query CTP itself, or ingest trades.  In
particular, a collection of these value objects is not a provider proof.

Queries run before a journal transaction. The local preview is explicitly
``NOT_COMPARABLE`` and grants no authority. The Gateway comparison callbacks
always return unavailable: the current journal interface does not provide an
unforgeable owner-issued transaction capability or prove the database file
actually open on a SQLite connection. Path and anchor checks alone do not
establish either fact, even when called on the owner thread. Do not install
these callbacks as an operational comparison source until that boundary has
been independently implemented and verified. Missing trades must be ingested
through the existing authenticated fact pipeline, never via this preview.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_gateway.strategy_position_ledger import StrategyInstrumentBinding

_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,41})(?:\.[0-9]{1,42})?\Z")
_MAX_TRADES = 100_000


def _ref(value: object) -> bool:
    return type(value) is str and 0 < len(value) <= 256 and value == value.strip() and value.isprintable()


def _amount(value: object, *, positive: bool = False) -> Decimal:
    if type(value) is not str or _DECIMAL.fullmatch(value) is None:
        raise ValueError("INVALID_RECONCILIATION_AMOUNT")
    result = Decimal(value)
    if positive and result <= 0:
        raise ValueError("INVALID_RECONCILIATION_AMOUNT")
    return result


@dataclass(frozen=True, slots=True)
class ReconciliationScope:
    account: str
    owner_generation: str
    session_generation: str
    trading_day: str
    binding: StrategyInstrumentBinding

    def __post_init__(self):
        if (not all(_ref(x) for x in (self.account, self.owner_generation, self.session_generation))
                or type(self.trading_day) is not str
                or re.fullmatch(r"[0-9]{8}", self.trading_day) is None
                or type(self.binding) is not StrategyInstrumentBinding
                or type(self.binding.instrument) is not MarketInstrumentKey):
            raise ValueError("INVALID_RECONCILIATION_SCOPE")
        try:
            datetime.strptime(self.trading_day, "%Y%m%d")
        except ValueError:
            raise ValueError("INVALID_RECONCILIATION_SCOPE") from None


@dataclass(frozen=True, slots=True)
class PositionObservation:
    scope: ReconciliationScope
    snapshot_ref: str
    coverage_ref: str
    net: str
    complete: bool

    def __post_init__(self):
        if (type(self.scope) is not ReconciliationScope or not _ref(self.snapshot_ref)
                or not _ref(self.coverage_ref) or self.complete is not True):
            raise ValueError("INVALID_POSITION_OBSERVATION")
        _amount(self.net)


@dataclass(frozen=True, slots=True)
class TradeObservation:
    """One provider fact and its verified relationship to the position query."""

    scope: ReconciliationScope
    trade_key: str
    fact_ref: str
    direction: str
    quantity: str
    position_relation: str

    def __post_init__(self):
        if (type(self.scope) is not ReconciliationScope or not _ref(self.trade_key)
                or not _ref(self.fact_ref) or self.direction not in {"buy", "sell"}
                or self.position_relation not in {"IN_POSITION", "AFTER_POSITION"}):
            raise ValueError("INVALID_TRADE_OBSERVATION")
        _amount(self.quantity, positive=True)


@dataclass(frozen=True, slots=True)
class TradeObservationBatch:
    scope: ReconciliationScope
    snapshot_ref: str
    coverage_ref: str
    complete: bool
    trades: tuple[TradeObservation, ...]

    def __post_init__(self):
        if (type(self.scope) is not ReconciliationScope or not _ref(self.snapshot_ref)
                or not _ref(self.coverage_ref) or self.complete is not True
                or type(self.trades) is not tuple or len(self.trades) > _MAX_TRADES
                or any(type(row) is not TradeObservation or row.scope != self.scope for row in self.trades)):
            raise ValueError("INVALID_TRADE_BATCH")


@dataclass(frozen=True, slots=True)
class ActivityObservation:
    scope: ReconciliationScope
    snapshot_ref: str
    coverage_ref: str
    complete: bool
    active_orders: int
    pending_dispatches: int
    pending_cancels: int

    def __post_init__(self):
        if (type(self.scope) is not ReconciliationScope or not _ref(self.snapshot_ref)
                or not _ref(self.coverage_ref) or self.complete is not True
                or any(type(value) is not int or value < 0
                       for value in (self.active_orders, self.pending_dispatches, self.pending_cancels))):
            raise ValueError("INVALID_ACTIVITY_OBSERVATION")


@dataclass(frozen=True, slots=True)
class _Candidate:
    position: PositionObservation
    batch: TradeObservationBatch
    activity: ActivityObservation
    trades: tuple[TradeObservation, ...]
    adjusted_net: str
    cut_ref: str
    expires_ns: int
    seal: object


@dataclass(frozen=True, slots=True)
class CandidatePreview:
    """Diagnostic calculation only; neither the digest nor value is authority."""

    status: str
    reason: str
    adjusted_net: str
    cut_ref: str


class GatewayReconciliationCandidate:
    """Build and revalidate an installed source's account cut, or fail closed.

    ``source`` is an explicitly installed, owner-trusted provider adapter.  It
    implements ``hold(scope)`` (a context manager yielding a no-argument
    recheck), ``read_position(scope)``, ``read_trades(scope)``,
    ``read_activity(scope)``, and ``verify(position, batch, activity)``.  Its
    verifier must check the actual provider evidence and inclusion relation,
    not merely the completeness booleans in these DTOs.  In particular it must
    invalidate a cut if a late fact, session/day transition, or coverage gap
    makes it incomparable.  The hold/recheck/verify calls used by Gateway's
    comparison transaction must not perform network or native I/O. These
    checks do not authenticate the SQLite connection: all methods are for
    trusted local owner code only and must not be exposed as an RPC capability.

    ``is_applied(db, trade)`` is a trusted journal reader which must match the
    exact source fact and trade key, not just a user-supplied label. It returns
    False only if the key is absent; conflicts or incomplete evidence must
    return neither True nor False. It runs inside the existing journal
    transaction and must never modify the DB.
    """

    def __init__(self, scope, source, *, journal, guard, is_applied, max_age_seconds=30.0):
        if (type(scope) is not ReconciliationScope or source is None or not callable(is_applied)
                or type(journal) is not GatewayDirectCommandJournal
                or type(guard) is not LocalAccountSessionGuard
                or scope.account != journal.account_scope
                or scope.owner_generation != journal.generation
                or any(not callable(getattr(source, name, None)) for name in
                       ("hold", "read_position", "read_trades", "read_activity", "verify"))
                or type(max_age_seconds) not in (int, float) or not 0 < max_age_seconds <= 300):
            raise ValueError("INVALID_RECONCILIATION_SOURCE")
        try:
            journal._assert_account_guard(guard)
            journal._identity.verify()
        except Exception:
            raise ValueError("INVALID_RECONCILIATION_OWNER") from None
        self._scope, self._source, self._is_applied = scope, source, is_applied
        self._journal, self._guard = journal, guard
        self._original_source, self._original_reader = source, is_applied
        self._original_journal, self._original_guard = journal, guard
        self._journal_id, self._journal_path = journal.journal_id, journal._identity.path
        self._max_age_ns = int(max_age_seconds * 1_000_000_000)
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._seal, self._candidate, self._depth = object(), None, 0

    def _owned(self):
        if os.getpid() != self._pid or threading.get_ident() != self._thread:
            raise ValueError("RECONCILIATION_OWNER_CHANGED")
        try:
            if (self._source is not self._original_source or self._is_applied is not self._original_reader
                    or self._journal is not self._original_journal or self._guard is not self._original_guard
                    or self._journal.journal_id != self._journal_id
                    or self._journal.account_scope != self._scope.account
                    or self._journal.generation != self._scope.owner_generation
                    or self._journal._identity.path != self._journal_path):
                raise ValueError
            self._journal._assert_account_guard(self._guard)
            self._journal._identity.verify()
        except Exception:
            raise ValueError("RECONCILIATION_OWNER_CHANGED") from None

    def _check_transaction(self, db):
        """Reject obvious mismatch; this cannot attest the connection's file."""
        rows = db.execute("PRAGMA database_list").fetchall()
        main = [row[2] for row in rows if row[1] == "main"]
        if len(main) != 1 or not main[0] or not os.path.samefile(main[0], self._journal_path):
            raise ValueError("RECONCILIATION_JOURNAL_CHANGED")
        self._journal._assert_anchor(db)

    @contextmanager
    def source_fence(self):
        """Hold the trusted source fence around a Gateway comparison transaction."""
        self._owned()
        if self._depth:
            raise ValueError("RECONCILIATION_FENCE_REENTRANT")
        with self._source.hold(self._scope) as recheck:
            if not callable(recheck) or recheck() is not True:
                raise ValueError("RECONCILIATION_SOURCE_UNAVAILABLE")
            self._depth = 1
            try:
                yield
                if recheck() is not True:
                    raise ValueError("RECONCILIATION_SOURCE_CHANGED")
                self._owned()
            finally:
                self._depth = 0

    def refresh(self) -> str:
        """Collect position first, then trades; do not open a journal transaction."""
        self._owned()
        self._candidate = None
        started_ns = time.monotonic_ns()
        try:
            with self.source_fence():
                position = self._source.read_position(self._scope)
                batch = self._source.read_trades(self._scope)
                activity = self._source.read_activity(self._scope)
                if (type(position) is not PositionObservation or type(batch) is not TradeObservationBatch
                        or type(activity) is not ActivityObservation
                        or any(item.scope != self._scope for item in (position, batch, activity))
                        or any((activity.active_orders, activity.pending_dispatches, activity.pending_cancels))
                        or self._source.verify(position, batch, activity) is not True):
                    return "NOT_COMPARABLE"
                unique = {}
                for trade in batch.trades:
                    existing = unique.get(trade.trade_key)
                    if existing is not None and existing != trade:
                        return "NOT_COMPARABLE"
                    unique[trade.trade_key] = trade
                trades = tuple(unique[key] for key in sorted(unique))
                with localcontext() as context:
                    context.prec = 160
                    net = _amount(position.net)
                    for trade in trades:
                        if trade.position_relation == "AFTER_POSITION":
                            quantity = _amount(trade.quantity, positive=True)
                            net += quantity if trade.direction == "buy" else -quantity
                    adjusted = format(net, "f")
                # The digest binds all inputs without relying on ambiguous
                # concatenation of provider-supplied reference strings. It is
                # an identifier, not a proof of the provider's assertions.
                content = [self._scope.account, self._scope.owner_generation,
                           self._scope.session_generation, self._scope.trading_day,
                           self._scope.binding.instrument.to_dict(), self._scope.binding.route,
                           self._scope.binding.quantity_unit, self._scope.binding.split_close_required,
                           [position.snapshot_ref, position.coverage_ref, position.net],
                           [batch.snapshot_ref, batch.coverage_ref],
                           [[t.trade_key, t.fact_ref, t.direction, t.quantity, t.position_relation]
                            for t in trades],
                           [activity.snapshot_ref, activity.coverage_ref, activity.active_orders,
                            activity.pending_dispatches, activity.pending_cancels], adjusted]
                encoded = json.dumps(content, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":"), allow_nan=False).encode("ascii")
                cut = "candidate:" + hashlib.sha256(encoded).hexdigest()
                expires_ns = started_ns + self._max_age_ns
                if time.monotonic_ns() >= expires_ns:
                    return "NOT_COMPARABLE"
                candidate = _Candidate(position, batch, activity, trades, adjusted, cut,
                                       expires_ns, self._seal)
                if self._source.verify(position, batch, activity) is not True:
                    return "NOT_COMPARABLE"
                self._candidate = candidate
            return "CANDIDATE"
        except Exception:
            self._candidate = None
            return "NOT_COMPARABLE"

    def _current(self, db, account, instrument):
        self._owned()
        candidate = self._candidate
        if (self._depth != 1 or type(db) is not sqlite3.Connection or not db.in_transaction
                or type(candidate) is not _Candidate or candidate.seal is not self._seal
                or account != self._scope.account or instrument != self._scope.binding.instrument
                or time.monotonic_ns() >= candidate.expires_ns
                or self._source.verify(candidate.position, candidate.batch, candidate.activity) is not True):
            raise ValueError("NOT_COMPARABLE")
        self._check_transaction(db)
        self._owned()
        return candidate

    def pending_trades(self, db, account, instrument):
        """Return facts not yet ingested, without creating or authorizing facts."""
        try:
            candidate = self._current(db, account, instrument)
            pending = []
            for trade in candidate.trades:
                state = self._is_applied(db, trade)
                if state is False:
                    pending.append(trade)
                elif state is not True:
                    raise ValueError("NOT_COMPARABLE")
            self._current(db, account, instrument)
            return tuple(pending)
        except Exception:
            return None

    def preview(self, db, account, instrument):
        """Return a diagnostic result that is never a comparable account cut.

        The caller and transaction are trusted local owner inputs. SQLite's
        reported database path may refer to a different file from the one
        already open on the connection following replacement and restoration;
        the current checks cannot establish otherwise.
        """
        pending = self.pending_trades(db, account, instrument)
        if pending is None or pending:
            return None
        try:
            candidate = self._current(db, account, instrument)
            return CandidatePreview("NOT_COMPARABLE", "OWNER_TRANSACTION_UNVERIFIED",
                                    candidate.adjusted_net, candidate.cut_ref)
        except Exception:
            return None

    def read_account_cut(self, db, account, instrument):
        """Fail closed until Gateway can attest the actual transaction origin."""
        return None

    def verify_account_cut(self, db, account, binding, trading_day, cut, net):
        """A source verifier or path match cannot enable comparison authority."""
        return False
