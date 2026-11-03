"""Durable per-strategy ledger for one shared account, without bt_api_execution.

Iteration 60 (user decision on acceptance report P1-5): a direct process can
run several strategies on one account with strategy-owned positions and
pre-dispatch admission, and it does not need the optional Execution package.

One account has exactly one active ledger owner (an exclusive OS file lock
next to the database; a second owner fails with ``OWNER_ALREADY_ACTIVE``).
Every admission and state change is one SQLite ``BEGIN IMMEDIATE``
transaction, so checks and writes are atomic across ledger instances, threads
and processes that share the file.

Admission (``reserve``) for one intent:

* the side relative to the strategy's own net position decides OPEN or CLOSE;
  a single order that would cross zero is refused (``POSITION_FLIP_REFUSED``);
* CLOSE: quantity must fit the strategy position minus its other pending
  closes (``CLOSE_CAPACITY_EXCEEDED``), so no strategy can close another's
  share of the account position;
* OPEN always needs a budget (policy ``gross_exposure_at_cost.v2``); there is
  no opt-out, a strategy without one is refused (``BUDGET_UNCONFIGURED``).
  CLOSE never needs a budget (only the capacity check above). Existing positions at cost plus pending
  opens plus this order, all times the instrument's contract multiplier and
  all in the budget currency, must stay within the budget. Unknown inputs are
  refused, never assumed: ``INSTRUMENT_TERMS_UNKNOWN``, ``PRICE_UNKNOWN``,
  ``COST_UNKNOWN`` (a fill without a price) and ``BUDGET_CURRENCY_MISMATCH``;
* a strategy with ``capital`` also has its own cash (policy
  ``strategy_cash_spot.v1``): a spot BUY must fit its cash minus its pending
  buys (``STRATEGY_CASH_INSUFFICIENT``). Only long spot positions quoted in the
  capital currency are modelled; anything else is refused before dispatch
  (``STRATEGY_CAPITAL_MODEL_UNSUPPORTED``);
* pending quantity (reserved, submitted, UNKNOWN, partially filled) keeps
  counting until the intent is finished, and survives restarts.

Executions are applied by cumulative quantity and cumulative fee
(``apply_execution``): a fee is identified by (intent, fee currency) and is
the running total the provider reports, so redelivered, reordered and
fee-only (late commission) reports are idempotent and can never overfill or
double count.

Snapshots are the Base ``StrategyAccountBookSnapshot``. Positions come from
the ledger. Strategy cash and position costs (``financial_available``) are
only published when the ledger was reconciled, in the same transaction, with
a complete and fresh ``AccountPositionReport`` for this account; a bare
mapping or a stale, incomplete or foreign report never yields ``BALANCED``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType

from bt_api_base.account_book_protocol import (
    StrategyAccountBookSnapshot,
    StrategyFinancialLedgerCut,
    StrategyFinancialWatermark,
    StrategyPositionCost,
)

__all__ = [
    "AccountPositionReport",
    "BUDGET_POLICY",
    "CASH_POLICY",
    "InstrumentTerms",
    "LedgerEvent",
    "LedgerEventPage",
    "LedgerIntent",
    "LedgerReconciliation",
    "SqliteStrategyLedger",
    "StrategyBudget",
    "StrategyCapital",
    "StrategyLedgerError",
    "StrategyLedgerReader",
]

BUDGET_POLICY = "gross_exposure_at_cost.v2"
CASH_POLICY = "strategy_cash_spot.v1"
_SCHEMA_VERSION = 2
_PRODUCT_MODELS = frozenset({"spot", "futures"})
_FINANCIAL_SOURCE = "strategy-ledger.v2"
_DEFAULT_RECONCILIATION_MAX_AGE_NS = 30_000_000_000
_PENDING = ("RESERVED", "SUBMITTED", "UNKNOWN", "PARTIALLY_FILLED")
_FINAL = frozenset({"FILLED", "CANCELLED", "REJECTED", "EXPIRED"})
_MAX_TEXT = 256


class StrategyLedgerError(RuntimeError):
    """Finite, value-free ledger failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _text(value: object, code: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_TEXT
        or value != value.strip()
        or not value.isprintable()
    ):
        raise StrategyLedgerError(code)
    return value


def _decimal(value: object, code: str, *, positive: bool = False, allow_none: bool = False):
    if value is None and allow_none:
        return None
    if type(value) is Decimal:
        parsed = value
    elif type(value) in (int, str):
        try:
            parsed = Decimal(value)
        except (InvalidOperation, ValueError):
            raise StrategyLedgerError(code) from None
    else:
        raise StrategyLedgerError(code)
    if not parsed.is_finite() or (positive and parsed <= 0) or (not positive and parsed < 0):
        raise StrategyLedgerError(code)
    return parsed


def _dec(text: str | None) -> Decimal | None:
    return None if text is None else Decimal(text)


@dataclass(frozen=True)
class StrategyBudget:
    """Gross exposure limit at cost for one strategy, in one quote currency."""

    limit: Decimal
    currency: str

    def __post_init__(self) -> None:
        _decimal(self.limit, "BUDGET_INVALID", positive=True)
        _text(self.currency, "BUDGET_INVALID")


@dataclass(frozen=True)
class StrategyCapital:
    """Cash allocated to one strategy, in one currency (not a budget).

    The ledger moves this cash with the strategy's own spot fills and fees.
    It is persisted on first use; reopening with a different allocation is
    refused (``LEDGER_CAPITAL_MISMATCH``).
    """

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        _decimal(self.amount, "CAPITAL_INVALID", positive=True)
        _text(self.currency, "CAPITAL_INVALID")


@dataclass(frozen=True)
class InstrumentTerms:
    """Declared trading terms of one instrument, used for every valuation.

    ``product_model`` is ``spot`` (cash changes by price x quantity x
    multiplier) or ``futures`` (margin; no strategy cash model).
    """

    multiplier: Decimal
    quote_currency: str
    product_model: str = "spot"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "multiplier", _decimal(self.multiplier, "INSTRUMENT_TERMS_INVALID", positive=True)
        )
        _text(self.quote_currency, "INSTRUMENT_TERMS_INVALID")
        if self.product_model not in _PRODUCT_MODELS:
            raise StrategyLedgerError("INSTRUMENT_TERMS_INVALID")

    @property
    def version(self) -> str:
        """Published as ``StrategyPositionCost.metadata_version``."""

        return f"instrument-terms.v1:{self.product_model}:{self.quote_currency}:{self.multiplier}"

    @classmethod
    def from_version(cls, text: object) -> InstrumentTerms | None:
        """Parse ``version`` exactly; anything else (another publisher) is ``None``."""

        if type(text) is not str:
            return None
        parts = text.split(":")
        if len(parts) != 4 or parts[0] != "instrument-terms.v1":
            return None
        try:
            terms = cls(Decimal(parts[3]), parts[2], parts[1])
        except (StrategyLedgerError, InvalidOperation, ValueError):
            return None
        return terms if terms.version == text else None


@dataclass(frozen=True)
class AccountPositionReport:
    """Provider account positions, as evidence for reconciliation.

    ``account_ref`` must be the ledger's account; ``source`` names where the
    positions were read (for example the provider route); ``observed_ns`` is
    the wall-clock time the positions were observed (or last changed, for a
    subscription); ``complete`` states that every position of the account is
    included. ``valid_until_ns``, when the source proves liveness (a
    subscription that is current until then), replaces the age check by
    ``now <= valid_until_ns``. Anything less is not evidence.
    """

    account_ref: str
    source: str
    observed_ns: int
    complete: bool
    positions: Mapping[str, object]
    valid_until_ns: int | None = None

    def __post_init__(self) -> None:
        _text(self.account_ref, "ACCOUNT_POSITIONS_INVALID")
        _text(self.source, "ACCOUNT_POSITIONS_INVALID")
        if type(self.observed_ns) is not int or self.observed_ns <= 0:
            raise StrategyLedgerError("ACCOUNT_POSITIONS_INVALID")
        if type(self.complete) is not bool:
            raise StrategyLedgerError("ACCOUNT_POSITIONS_INVALID")
        if self.valid_until_ns is not None and (
            type(self.valid_until_ns) is not int or self.valid_until_ns <= 0
        ):
            raise StrategyLedgerError("ACCOUNT_POSITIONS_INVALID")
        if not isinstance(self.positions, Mapping):
            raise StrategyLedgerError("ACCOUNT_POSITIONS_INVALID")
        object.__setattr__(
            self,
            "positions",
            MappingProxyType(
                {
                    _text(name, "ACCOUNT_POSITIONS_INVALID"): _signed(value)
                    for name, value in self.positions.items()
                }
            ),
        )


@dataclass(frozen=True)
class LedgerIntent:
    """One strategy order before dispatch.

    ``price`` is the limit price, or for orders without one the explicit
    reference price the caller commits to for budgeting; ``None`` means
    unknown and is refused when a budget applies to an OPEN.
    """

    intent_id: str
    strategy_ref: str
    instrument: str
    side: str
    quantity: Decimal
    price: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("intent_id", "strategy_ref", "instrument"):
            _text(getattr(self, name), "INTENT_INVALID")
        if self.side not in ("BUY", "SELL"):
            raise StrategyLedgerError("INTENT_INVALID")
        object.__setattr__(
            self, "quantity", _decimal(self.quantity, "INTENT_INVALID", positive=True)
        )
        object.__setattr__(
            self, "price", _decimal(self.price, "INTENT_INVALID", positive=True, allow_none=True)
        )

    def _fingerprint(self) -> str:
        return json.dumps(
            [
                self.strategy_ref,
                self.instrument,
                self.side,
                str(self.quantity),
                None if self.price is None else str(self.price),
            ]
        )


@dataclass(frozen=True)
class LedgerEvent:
    """One durable ledger state change of one strategy's intent."""

    event_id: str  # "<generation>:<cursor>", stable across restarts
    cursor: int
    strategy_ref: str
    intent_id: str
    action: str  # RESERVE | SUBMITTED | UNKNOWN | EXECUTION | FILLED | CANCELLED | ...
    detail: Mapping[str, object]


@dataclass(frozen=True)
class LedgerEventPage:
    """Events after a cursor for one strategy, and how far the scan reached."""

    generation: str
    events: tuple[LedgerEvent, ...]
    scanned_through: int
    has_more: bool


@dataclass(frozen=True)
class LedgerReconciliation:
    """Sum of strategy positions against one provider account report.

    ``BALANCED``: a complete, fresh report for this account equals the ledger
    at ``cursor``. ``RESIDUAL``: they differ and nothing explains it.
    ``IN_FLIGHT``: they differ only on instruments with pending orders, or
    whose ledger position changed after the report was observed and within
    the settle window (the max report age): the provider may not show those
    fills yet.
    ``UNVERIFIED``: the report is not usable evidence (``reason``:
    ``REPORT_INCOMPLETE``, ``REPORT_STALE`` or ``REPORT_FROM_FUTURE``).
    """

    state: str
    residual: Mapping[str, Decimal]
    cursor: int
    reason: str | None = None
    source: str | None = None
    observed_ns: int | None = None


class _OwnerLock:
    """Exclusive OS lock: one active ledger owner per account database."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._fd)
            raise StrategyLedgerError("OWNER_ALREADY_ACTIVE") from None

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


class SqliteStrategyLedger:
    """The single owner of one shared account's strategy ledger."""

    def __init__(
        self,
        path: str | Path,
        *,
        account_ref: str,
        instruments: Mapping[str, InstrumentTerms] | None = None,
        budgets: Mapping[str, StrategyBudget] | None = None,
        capital: Mapping[str, StrategyCapital] | None = None,
        reconciliation_max_age_ns: int = _DEFAULT_RECONCILIATION_MAX_AGE_NS,
        clock_ns: Callable[[], int] = time.time_ns,
        exclusive_owner: bool = True,
    ) -> None:
        self.account_ref = _text(account_ref, "ACCOUNT_REF_INVALID")
        terms = dict(instruments or {})
        for name, item in terms.items():
            _text(name, "INSTRUMENT_TERMS_INVALID")
            if type(item) is not InstrumentTerms:
                raise StrategyLedgerError("INSTRUMENT_TERMS_INVALID")
        self._instruments = MappingProxyType(terms)
        budgets = dict(budgets or {})
        for strategy_ref, budget in budgets.items():
            _text(strategy_ref, "BUDGET_INVALID")
            if type(budget) is not StrategyBudget:
                raise StrategyLedgerError("BUDGET_INVALID")
        self._budgets = MappingProxyType(budgets)
        allocations = dict(capital or {})
        for strategy_ref, item in allocations.items():
            _text(strategy_ref, "CAPITAL_INVALID")
            if type(item) is not StrategyCapital:
                raise StrategyLedgerError("CAPITAL_INVALID")
        self._capital = MappingProxyType(allocations)
        if type(reconciliation_max_age_ns) is not int or reconciliation_max_age_ns <= 0:
            raise StrategyLedgerError("RECONCILIATION_POLICY_INVALID")
        self._max_age_ns = reconciliation_max_age_ns
        if not callable(clock_ns):
            raise StrategyLedgerError("RECONCILIATION_POLICY_INVALID")
        self._clock_ns = clock_ns
        self._path = Path(path)
        self._owner = (
            _OwnerLock(self._path.with_name(self._path.name + ".owner"))
            if exclusive_owner
            else None
        )
        try:
            self._db = sqlite3.connect(
                str(self._path), isolation_level=None, check_same_thread=False, timeout=5.0
            )
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._lock = threading.RLock()
            self._tx_depth = 0
            self._initialize()
        except BaseException:
            if self._owner is not None:
                self._owner.release()
            raise

    # ------------------------------------------------------------------
    # Schema and transactions
    # ------------------------------------------------------------------

    def _initialize(self) -> None:
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS ledger_meta ("
            " id INTEGER PRIMARY KEY CHECK (id = 1), schema_version INTEGER NOT NULL,"
            " account_ref TEXT NOT NULL, generation TEXT NOT NULL, cursor INTEGER NOT NULL);"
            "CREATE TABLE IF NOT EXISTS intents ("
            " intent_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, strategy_ref TEXT NOT NULL,"
            " instrument TEXT NOT NULL, side TEXT NOT NULL, effect TEXT NOT NULL,"
            " quantity TEXT NOT NULL, price TEXT, state TEXT NOT NULL,"
            " filled TEXT NOT NULL, created_ns INTEGER NOT NULL);"
            "CREATE INDEX IF NOT EXISTS intents_scope ON intents(strategy_ref, instrument, state);"
            "CREATE TABLE IF NOT EXISTS positions ("
            " strategy_ref TEXT NOT NULL, instrument TEXT NOT NULL, quantity TEXT NOT NULL,"
            " cost_basis TEXT, PRIMARY KEY (strategy_ref, instrument));"
            "CREATE TABLE IF NOT EXISTS fees ("
            " strategy_ref TEXT NOT NULL, currency TEXT NOT NULL, amount TEXT NOT NULL,"
            " PRIMARY KEY (strategy_ref, currency));"
            "CREATE TABLE IF NOT EXISTS ledger_events ("
            " cursor INTEGER PRIMARY KEY, at_ns INTEGER NOT NULL, intent_id TEXT NOT NULL,"
            " action TEXT NOT NULL, detail TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS execution_fees ("
            " intent_id TEXT NOT NULL, currency TEXT NOT NULL, total TEXT NOT NULL,"
            " PRIMARY KEY (intent_id, currency));"
            "CREATE TABLE IF NOT EXISTS strategy_capital ("
            " strategy_ref TEXT PRIMARY KEY, currency TEXT NOT NULL, amount TEXT NOT NULL,"
            " cash_error TEXT);"
            "CREATE TABLE IF NOT EXISTS strategy_cash ("
            " strategy_ref TEXT NOT NULL, currency TEXT NOT NULL, amount TEXT NOT NULL,"
            " PRIMARY KEY (strategy_ref, currency));"
            "CREATE TABLE IF NOT EXISTS reconciliation ("
            " id INTEGER PRIMARY KEY CHECK (id = 1), source TEXT NOT NULL,"
            " observed_ns INTEGER NOT NULL, cursor INTEGER NOT NULL);"
            # R2-E: the one execution journal this ledger projects (service
            # hosts) and the last journal sequence applied, written in the
            # same transaction as the ledger effects of that journal event.
            "CREATE TABLE IF NOT EXISTS journal_cursor ("
            " id INTEGER PRIMARY KEY CHECK (id = 1), source TEXT NOT NULL,"
            " sequence INTEGER NOT NULL);"
        )
        with self._transaction() as db:
            row = db.execute(
                "SELECT schema_version, account_ref FROM ledger_meta WHERE id = 1"
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO ledger_meta VALUES (1, ?, ?, ?, 0)",
                    (_SCHEMA_VERSION, self.account_ref, uuid.uuid4().hex),
                )
            elif row[0] != _SCHEMA_VERSION:
                raise StrategyLedgerError("LEDGER_SCHEMA_UNSUPPORTED")
            elif row[1] != self.account_ref:
                raise StrategyLedgerError("LEDGER_ACCOUNT_MISMATCH")
            for strategy_ref, allocation in self._capital.items():
                stored = db.execute(
                    "SELECT currency, amount FROM strategy_capital WHERE strategy_ref = ?",
                    (strategy_ref,),
                ).fetchone()
                if stored is not None:
                    if stored[0] != allocation.currency or Decimal(stored[1]) != Decimal(
                        allocation.amount
                    ):
                        raise StrategyLedgerError("LEDGER_CAPITAL_MISMATCH")
                    continue
                traded = db.execute(
                    "SELECT 1 FROM intents WHERE strategy_ref = ? LIMIT 1", (strategy_ref,)
                ).fetchone()
                if traded is not None:
                    # Cash cannot be derived for history recorded without it.
                    raise StrategyLedgerError("LEDGER_CAPITAL_LATE")
                amount = str(Decimal(allocation.amount))
                db.execute(
                    "INSERT INTO strategy_capital VALUES (?, ?, ?, NULL)",
                    (strategy_ref, allocation.currency, amount),
                )
                db.execute(
                    "INSERT INTO strategy_cash VALUES (?, ?, ?)",
                    (strategy_ref, allocation.currency, amount),
                )

    class _Tx:
        def __init__(self, ledger: SqliteStrategyLedger) -> None:
            self._ledger = ledger

        # Re-entrant (R2-E): a nested block joins the outer transaction, so one
        # journal event can apply several ledger steps atomically. A nested
        # block's exception must propagate (never be caught inside the outer
        # block); the outermost block then rolls everything back.

        def __enter__(self) -> sqlite3.Connection:
            ledger = self._ledger
            ledger._lock.acquire()
            if ledger._tx_depth == 0:
                try:
                    ledger._db.execute("BEGIN IMMEDIATE")
                except BaseException:
                    ledger._lock.release()
                    raise
            ledger._tx_depth += 1
            return ledger._db

        def __exit__(self, exc_type, exc, tb) -> None:
            ledger = self._ledger
            ledger._tx_depth -= 1
            try:
                if ledger._tx_depth == 0:
                    ledger._db.execute("COMMIT" if exc_type is None else "ROLLBACK")
            finally:
                ledger._lock.release()

    def _transaction(self) -> SqliteStrategyLedger._Tx:
        return SqliteStrategyLedger._Tx(self)

    def _event(self, db: sqlite3.Connection, intent_id: str, action: str, detail: dict) -> int:
        cursor = int(db.execute("SELECT cursor FROM ledger_meta WHERE id = 1").fetchone()[0]) + 1
        db.execute("UPDATE ledger_meta SET cursor = ? WHERE id = 1", (cursor,))
        db.execute(
            "INSERT INTO ledger_events VALUES (?, ?, ?, ?, ?)",
            (cursor, int(self._clock_ns()), intent_id, action, json.dumps(detail, sort_keys=True)),
        )
        return cursor

    # ------------------------------------------------------------------
    # Reads inside a transaction
    # ------------------------------------------------------------------

    @staticmethod
    def _position(db: sqlite3.Connection, strategy_ref: str, instrument: str):
        row = db.execute(
            "SELECT quantity, cost_basis FROM positions WHERE strategy_ref = ? AND instrument = ?",
            (strategy_ref, instrument),
        ).fetchone()
        if row is None:
            return Decimal("0"), Decimal("0")
        return Decimal(row[0]), _dec(row[1])

    @staticmethod
    def _pending(db: sqlite3.Connection, strategy_ref: str, *, instrument=None, effect=None):
        query = (
            "SELECT intent_id, instrument, effect, quantity, price, filled FROM intents"
            " WHERE strategy_ref = ? AND state IN (?, ?, ?, ?)"
        )
        args: list[object] = [strategy_ref, *_PENDING]
        if instrument is not None:
            query += " AND instrument = ?"
            args.append(instrument)
        if effect is not None:
            query += " AND effect = ?"
            args.append(effect)
        return db.execute(query, args).fetchall()

    def _exposure(self, db: sqlite3.Connection, strategy_ref: str, currency: str) -> Decimal:
        total = Decimal("0")
        for instrument, quantity, cost_basis in db.execute(
            "SELECT instrument, quantity, cost_basis FROM positions WHERE strategy_ref = ?",
            (strategy_ref,),
        ):
            if Decimal(quantity) == 0:
                continue
            terms = self._terms(instrument)
            if terms.quote_currency != currency:
                raise StrategyLedgerError("BUDGET_CURRENCY_MISMATCH")
            if cost_basis is None:
                raise StrategyLedgerError("COST_UNKNOWN")
            total += Decimal(cost_basis) * terms.multiplier
        for _id, instrument, _effect, quantity, price, filled in self._pending(
            db, strategy_ref, effect="OPEN"
        ):
            terms = self._terms(instrument)
            if terms.quote_currency != currency:
                raise StrategyLedgerError("BUDGET_CURRENCY_MISMATCH")
            if price is None:
                raise StrategyLedgerError("PRICE_UNKNOWN")
            remaining = Decimal(quantity) - Decimal(filled)
            total += remaining * Decimal(price) * terms.multiplier
        return total

    def _terms(self, instrument: str) -> InstrumentTerms:
        terms = self._instruments.get(instrument)
        if terms is None:
            raise StrategyLedgerError("INSTRUMENT_TERMS_UNKNOWN")
        return terms

    @staticmethod
    def _capital_row(db: sqlite3.Connection, strategy_ref: str):
        return db.execute(
            "SELECT currency, cash_error FROM strategy_capital WHERE strategy_ref = ?",
            (strategy_ref,),
        ).fetchone()

    @staticmethod
    def _cash(db: sqlite3.Connection, strategy_ref: str, currency: str) -> Decimal:
        row = db.execute(
            "SELECT amount FROM strategy_cash WHERE strategy_ref = ? AND currency = ?",
            (strategy_ref, currency),
        ).fetchone()
        return Decimal("0") if row is None else Decimal(row[0])

    @staticmethod
    def _add_cash(db: sqlite3.Connection, strategy_ref: str, currency: str, delta: Decimal) -> None:
        current = SqliteStrategyLedger._cash(db, strategy_ref, currency)
        db.execute(
            "INSERT INTO strategy_cash VALUES (?, ?, ?) ON CONFLICT(strategy_ref, currency)"
            " DO UPDATE SET amount = excluded.amount",
            (strategy_ref, currency, str(current + delta)),
        )

    @staticmethod
    def _cash_unknown(db: sqlite3.Connection, strategy_ref: str, code: str) -> None:
        # The first cause is kept; strategy cash stays unavailable from now on.
        db.execute(
            "UPDATE strategy_capital SET cash_error = ? WHERE strategy_ref = ?"
            " AND cash_error IS NULL",
            (code, strategy_ref),
        )

    def _pending_buy_cost(self, db: sqlite3.Connection, strategy_ref: str) -> Decimal:
        total = Decimal("0")
        for _id, instrument, _effect, quantity, price, filled in self._pending(
            db, strategy_ref, effect="OPEN"
        ):
            side = db.execute("SELECT side FROM intents WHERE intent_id = ?", (_id,)).fetchone()[0]
            if side != "BUY":
                continue
            if price is None:
                raise StrategyLedgerError("PRICE_UNKNOWN")
            remaining = Decimal(quantity) - Decimal(filled)
            total += remaining * Decimal(price) * self._terms(instrument).multiplier
        return total

    def _admit_cash(self, db: sqlite3.Connection, intent: LedgerIntent) -> None:
        row = self._capital_row(db, intent.strategy_ref)
        if row is None:
            return
        currency, cash_error = row
        terms = self._terms(intent.instrument)
        if (
            intent.side != "BUY"
            or terms.product_model != "spot"
            or terms.quote_currency != currency
        ):
            raise StrategyLedgerError("STRATEGY_CAPITAL_MODEL_UNSUPPORTED")
        if cash_error is not None:
            raise StrategyLedgerError(str(cash_error))
        if intent.price is None:
            raise StrategyLedgerError("PRICE_UNKNOWN")
        requested = intent.quantity * intent.price * terms.multiplier
        available = self._cash(db, intent.strategy_ref, currency) - self._pending_buy_cost(
            db, intent.strategy_ref
        )
        if requested > available:
            raise StrategyLedgerError("STRATEGY_CASH_INSUFFICIENT")

    # ------------------------------------------------------------------
    # Admission and lifecycle
    # ------------------------------------------------------------------

    def reserve(self, intent: LedgerIntent) -> str:
        """Admit one intent before dispatch; return its effect (OPEN/CLOSE)."""

        if type(intent) is not LedgerIntent:
            raise StrategyLedgerError("INTENT_INVALID")
        fingerprint = intent._fingerprint()
        with self._transaction() as db:
            existing = db.execute(
                "SELECT fingerprint, effect FROM intents WHERE intent_id = ?", (intent.intent_id,)
            ).fetchone()
            if existing is not None:
                if existing[0] != fingerprint:
                    raise StrategyLedgerError("INTENT_ID_CONFLICT")
                return str(existing[1])
            position, _cost = self._position(db, intent.strategy_ref, intent.instrument)
            signed = intent.quantity if intent.side == "BUY" else -intent.quantity
            if position == 0 or (position > 0) == (signed > 0):
                effect = "OPEN"
            else:
                effect = "CLOSE"
                if intent.quantity > abs(position):
                    raise StrategyLedgerError("POSITION_FLIP_REFUSED")
                pending_close = sum(
                    (
                        Decimal(row[3]) - Decimal(row[5])
                        for row in self._pending(
                            db, intent.strategy_ref, instrument=intent.instrument, effect="CLOSE"
                        )
                    ),
                    Decimal("0"),
                )
                if intent.quantity > abs(position) - pending_close:
                    raise StrategyLedgerError("CLOSE_CAPACITY_EXCEEDED")
            if effect == "OPEN":
                budget = self._budgets.get(intent.strategy_ref)
                if budget is None:
                    raise StrategyLedgerError("BUDGET_UNCONFIGURED")
                terms = self._terms(intent.instrument)
                if terms.quote_currency != budget.currency:
                    raise StrategyLedgerError("BUDGET_CURRENCY_MISMATCH")
                if intent.price is None:
                    raise StrategyLedgerError("PRICE_UNKNOWN")
                requested = intent.quantity * intent.price * terms.multiplier
                exposure = self._exposure(db, intent.strategy_ref, budget.currency)
                if exposure + requested > budget.limit:
                    raise StrategyLedgerError("BUDGET_EXCEEDED")
                self._admit_cash(db, intent)
            db.execute(
                "INSERT INTO intents VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED', '0', ?)",
                (
                    intent.intent_id,
                    fingerprint,
                    intent.strategy_ref,
                    intent.instrument,
                    intent.side,
                    effect,
                    str(intent.quantity),
                    None if intent.price is None else str(intent.price),
                    int(self._clock_ns()),
                ),
            )
            self._event(db, intent.intent_id, "RESERVE", {"effect": effect})
            return effect

    def _intent(self, db: sqlite3.Connection, intent_id: str):
        row = db.execute(
            "SELECT strategy_ref, instrument, side, quantity, state, filled FROM intents"
            " WHERE intent_id = ?",
            (intent_id,),
        ).fetchone()
        if row is None:
            raise StrategyLedgerError("INTENT_NOT_FOUND")
        return row

    def _set_state(self, intent_id: str, state: str, allowed: tuple[str, ...]) -> None:
        with self._transaction() as db:
            current = self._intent(db, intent_id)[4]
            if current == state:
                return
            if current not in allowed:
                raise StrategyLedgerError("INTENT_STATE_CONFLICT")
            db.execute("UPDATE intents SET state = ? WHERE intent_id = ?", (state, intent_id))
            self._event(db, intent_id, state, {})

    def mark_submitted(self, intent_id: str) -> None:
        self._set_state(intent_id, "SUBMITTED", ("RESERVED", "UNKNOWN"))

    def mark_unknown(self, intent_id: str) -> None:
        """The dispatch outcome is unknown: keep the reservation counting."""

        self._set_state(intent_id, "UNKNOWN", ("RESERVED", "SUBMITTED"))

    def apply_execution(
        self,
        intent_id: str,
        *,
        cumulative_quantity: object,
        average_price: object = None,
        fee: object = None,
        fee_currency: str | None = None,
    ) -> Decimal:
        """Apply executions up to ``cumulative_quantity``; return the new delta.

        ``average_price`` is the price of the whole cumulative quantity (as
        brokers report it); ``None`` makes the position cost unknown.

        ``fee`` is the provider's *cumulative* fee of this intent in
        ``fee_currency``; one fee total is kept per (intent, currency). A
        report may carry only a higher fee for an unchanged quantity (a late
        commission): that fee is applied and ``0`` is returned. A report
        without a fee leaves the recorded total unchanged. A lower total for
        the same currency is refused (``EXECUTION_FEE_REGRESSED``); a report
        older than what was applied (lower cumulative quantity) is ignored.
        """

        cumulative = _decimal(cumulative_quantity, "EXECUTION_INVALID")
        average = _decimal(average_price, "EXECUTION_INVALID", positive=True, allow_none=True)
        fee_total = _decimal(fee, "EXECUTION_INVALID", allow_none=True)
        if fee_total is not None:
            _text(fee_currency, "EXECUTION_INVALID")
        elif fee_currency is not None:
            raise StrategyLedgerError("EXECUTION_INVALID")
        with self._transaction() as db:
            strategy_ref, instrument, side, quantity, state, filled = self._intent(db, intent_id)
            quantity, filled = Decimal(quantity), Decimal(filled)
            if cumulative > quantity:
                raise StrategyLedgerError("EXECUTION_OVERFILL")
            delta = cumulative - filled
            if delta < 0:
                return Decimal("0")
            fee_delta = Decimal("0")
            if fee_total is not None:
                row = db.execute(
                    "SELECT total FROM execution_fees WHERE intent_id = ? AND currency = ?",
                    (intent_id, fee_currency),
                ).fetchone()
                fee_delta = fee_total - (Decimal("0") if row is None else Decimal(row[0]))
                if fee_delta < 0:
                    raise StrategyLedgerError("EXECUTION_FEE_REGRESSED")
            if delta == 0 and fee_delta == 0:
                return Decimal("0")
            capital = self._capital_row(db, strategy_ref)
            prior = db.execute(
                "SELECT detail FROM ledger_events WHERE intent_id = ? AND action = 'EXECUTION'"
                " ORDER BY cursor DESC LIMIT 1",
                (intent_id,),
            ).fetchone()
            prior_average = _dec(json.loads(prior[0]).get("average_price")) if prior else None
            if delta > 0:
                # A late report for a finished (cancelled) intent is still a
                # real fill and is applied; the state stays final.
                delta_price: Decimal | None
                if average is None or (filled > 0 and prior_average is None):
                    delta_price = None
                else:
                    delta_price = (cumulative * average - filled * (prior_average or 0)) / delta
                    if delta_price <= 0:
                        delta_price = None
                self._apply_position(db, strategy_ref, instrument, side, delta, delta_price)
                if capital is not None:
                    self._apply_cash(db, strategy_ref, instrument, side, delta, delta_price, capital)
                next_state = state
                if state not in _FINAL:
                    next_state = "FILLED" if cumulative == quantity else "PARTIALLY_FILLED"
                db.execute(
                    "UPDATE intents SET filled = ?, state = ? WHERE intent_id = ?",
                    (str(cumulative), next_state, intent_id),
                )
            else:
                # Fee-only report: the cumulative average stays the recorded one.
                average = prior_average
            if fee_delta > 0:
                db.execute(
                    "INSERT INTO execution_fees VALUES (?, ?, ?) ON CONFLICT(intent_id, currency)"
                    " DO UPDATE SET total = excluded.total",
                    (intent_id, fee_currency, str(fee_total)),
                )
                row = db.execute(
                    "SELECT amount FROM fees WHERE strategy_ref = ? AND currency = ?",
                    (strategy_ref, fee_currency),
                ).fetchone()
                current_fee = Decimal("0") if row is None else Decimal(row[0])
                db.execute(
                    "INSERT INTO fees VALUES (?, ?, ?) ON CONFLICT(strategy_ref, currency)"
                    " DO UPDATE SET amount = excluded.amount",
                    (strategy_ref, fee_currency, str(current_fee + fee_delta)),
                )
                if capital is not None:
                    # A fee in another currency is that currency's cash; it is
                    # never converted (no FX in this ledger).
                    self._add_cash(db, strategy_ref, str(fee_currency), -fee_delta)
            self._event(
                db,
                intent_id,
                "EXECUTION",
                {
                    "cumulative": str(cumulative),
                    "average_price": None if average is None else str(average),
                    "fee": None if fee_total is None else str(fee_total),
                    "fee_currency": fee_currency,
                },
            )
            return delta

    def _apply_position(self, db, strategy_ref, instrument, side, delta, delta_price) -> None:
        position, cost = self._position(db, strategy_ref, instrument)
        signed = delta if side == "BUY" else -delta
        new_position = position + signed
        if position == 0 or (position > 0) == (signed > 0):
            new_cost = None if cost is None or delta_price is None else cost + delta * delta_price
        elif abs(signed) <= abs(position):
            new_cost = None if cost is None else cost * abs(new_position) / abs(position)
        else:
            # Crossing zero: the closing part keeps the old cost, the opening
            # part starts a new basis.
            opened = abs(new_position)
            new_cost = None if delta_price is None else opened * delta_price
        if new_position == 0:
            new_cost = Decimal("0")
        db.execute(
            "INSERT INTO positions VALUES (?, ?, ?, ?) ON CONFLICT(strategy_ref, instrument)"
            " DO UPDATE SET quantity = excluded.quantity, cost_basis = excluded.cost_basis",
            (strategy_ref, instrument, str(new_position), None if new_cost is None else str(new_cost)),
        )

    def _apply_cash(self, db, strategy_ref, instrument, side, delta, delta_price, capital) -> None:
        currency, cash_error = capital
        if cash_error is not None:
            return
        terms = self._instruments.get(instrument)
        position, _cost = self._position(db, strategy_ref, instrument)
        if (
            terms is None
            or terms.product_model != "spot"
            or terms.quote_currency != currency
            or position < 0
        ):
            self._cash_unknown(db, strategy_ref, "STRATEGY_CAPITAL_MODEL_UNSUPPORTED")
            return
        if delta_price is None:
            self._cash_unknown(db, strategy_ref, "STRATEGY_CASH_UNKNOWN")
            return
        amount = delta * delta_price * terms.multiplier
        self._add_cash(db, strategy_ref, currency, -amount if side == "BUY" else amount)

    def finish(self, intent_id: str, state: str) -> None:
        """Finish an intent (CANCELLED/REJECTED/EXPIRED/FILLED); release the rest."""

        if state not in _FINAL:
            raise StrategyLedgerError("INTENT_STATE_INVALID")
        with self._transaction() as db:
            current = self._intent(db, intent_id)[4]
            if current == state:
                return
            if current in _FINAL:
                raise StrategyLedgerError("INTENT_STATE_CONFLICT")
            db.execute("UPDATE intents SET state = ? WHERE intent_id = ?", (state, intent_id))
            self._event(db, intent_id, state, {})

    # ------------------------------------------------------------------
    # Execution journal projection (service hosts, R2-E)
    # ------------------------------------------------------------------

    def reserved_intents(self) -> tuple[str, ...]:
        """Intents admitted here but never reported by the journal (``RESERVED``)."""

        with self._transaction() as db:
            rows = db.execute(
                "SELECT intent_id FROM intents WHERE state = 'RESERVED' ORDER BY created_ns"
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def journal_cursor(self, source: str) -> int:
        """Last sequence of journal ``source`` applied here (``0`` before the first).

        A ledger projects exactly one execution journal; any other source is
        refused (``LEDGER_JOURNAL_SOURCE_MISMATCH``), for example a replaced
        journal generation.
        """

        _text(source, "JOURNAL_SOURCE_INVALID")
        with self._transaction() as db:
            return self._journal_cursor(db, source)

    @staticmethod
    def _journal_cursor(db: sqlite3.Connection, source: str) -> int:
        row = db.execute("SELECT source, sequence FROM journal_cursor WHERE id = 1").fetchone()
        if row is None:
            return 0
        if row[0] != source:
            raise StrategyLedgerError("LEDGER_JOURNAL_SOURCE_MISMATCH")
        return int(row[1])

    def apply_journal_event(
        self,
        source: str,
        sequence: int,
        *,
        intent_id: str | None = None,
        step: str = "NONE",
        cumulative_quantity: object = None,
        average_price: object = None,
        fee: object = None,
        fee_currency: str | None = None,
        final_state: str | None = None,
    ) -> bool:
        """Apply one execution-journal event exactly once; ``False`` if already applied.

        The ledger effects of the event and ``sequence`` are written in one
        transaction, so after a crash the projection resumes exactly after
        the last applied event (the journal commits first; the ledger never
        runs ahead of it). Sequences must increase; gaps are allowed (events
        of other scopes). ``step``:

        * ``NONE``: only advances the cursor;
        * ``SUBMITTED`` / ``UNKNOWN``: dispatch accepted / outcome unknown;
        * ``EXECUTION``: cumulative fill and/or cumulative fee
          (``apply_execution`` semantics);
        * ``FINISH``: the optional cumulative fill, then the final state.

        A step that contradicts the ledger raises and applies nothing
        (``INTENT_NOT_FOUND``, ``INTENT_STATE_CONFLICT``, ...).
        """

        _text(source, "JOURNAL_SOURCE_INVALID")
        if type(sequence) is not int or sequence <= 0:
            raise StrategyLedgerError("JOURNAL_SEQUENCE_INVALID")
        if step not in ("NONE", "SUBMITTED", "UNKNOWN", "EXECUTION", "FINISH"):
            raise StrategyLedgerError("JOURNAL_STEP_INVALID")
        if step == "FINISH" and final_state not in _FINAL:
            raise StrategyLedgerError("INTENT_STATE_INVALID")
        if step == "EXECUTION" and cumulative_quantity is None:
            raise StrategyLedgerError("EXECUTION_INVALID")
        with self._transaction() as db:
            if sequence <= self._journal_cursor(db, source):
                return False
            if step != "NONE":
                _text(intent_id, "INTENT_INVALID")
                state = str(self._intent(db, intent_id)[4])
                if step == "SUBMITTED":
                    if state in ("RESERVED", "UNKNOWN"):
                        self.mark_submitted(str(intent_id))
                    elif state != "SUBMITTED":
                        raise StrategyLedgerError("INTENT_STATE_CONFLICT")
                elif step == "UNKNOWN":
                    if state in ("RESERVED", "SUBMITTED"):
                        self.mark_unknown(str(intent_id))
                    elif state in _FINAL:
                        raise StrategyLedgerError("INTENT_STATE_CONFLICT")
                if cumulative_quantity is not None:
                    self.apply_execution(
                        str(intent_id),
                        cumulative_quantity=cumulative_quantity,
                        average_price=average_price,
                        fee=fee,
                        fee_currency=fee_currency,
                    )
                if step == "FINISH":
                    self.finish(str(intent_id), str(final_state))
            db.execute(
                "INSERT INTO journal_cursor VALUES (1, ?, ?) ON CONFLICT(id)"
                " DO UPDATE SET sequence = excluded.sequence",
                (source, sequence),
            )
            return True

    # ------------------------------------------------------------------
    # Projections
    # ------------------------------------------------------------------

    def positions(self, strategy_ref: str) -> Mapping[str, Decimal]:
        with self._transaction() as db:
            rows = db.execute(
                "SELECT instrument, quantity FROM positions WHERE strategy_ref = ?",
                (strategy_ref,),
            ).fetchall()
        return MappingProxyType(
            {name: Decimal(quantity) for name, quantity in rows if Decimal(quantity) != 0}
        )

    def intent_state(self, intent_id: str) -> str:
        with self._transaction() as db:
            return str(self._intent(db, intent_id)[4])

    def snapshot(self, strategy_ref: str) -> StrategyAccountBookSnapshot:
        """This strategy's positions; financials only after a trusted reconcile.

        Without a report there is no reconciliation in this snapshot, so cash
        and costs are unavailable (``STRATEGY_BOOK_UNRECONCILED``) even if an
        earlier reconcile was balanced. Use ``reconciled_snapshot`` for both.
        """

        _text(strategy_ref, "STRATEGY_REF_INVALID")
        with self._transaction() as db:
            return self._snapshot(db, strategy_ref, None)

    def reconciled_snapshot(
        self, strategy_ref: str, report: AccountPositionReport
    ) -> tuple[LedgerReconciliation, StrategyAccountBookSnapshot]:
        """Reconcile with ``report`` and read the strategy in one transaction."""

        _text(strategy_ref, "STRATEGY_REF_INVALID")
        with self._transaction() as db:
            reconciliation = self._reconcile(db, report)
            return reconciliation, self._snapshot(db, strategy_ref, reconciliation)

    def _snapshot(
        self,
        db: sqlite3.Connection,
        strategy_ref: str,
        reconciliation: LedgerReconciliation | None,
    ) -> StrategyAccountBookSnapshot:
        generation, cursor = db.execute(
            "SELECT generation, cursor FROM ledger_meta WHERE id = 1"
        ).fetchone()
        rows = db.execute(
            "SELECT instrument, quantity, cost_basis FROM positions WHERE strategy_ref = ?",
            (strategy_ref,),
        ).fetchall()
        positions = {name: Decimal(q) for name, q, _cost in rows if Decimal(q) != 0}
        fees = {
            currency: Decimal(amount)
            for currency, amount in db.execute(
                "SELECT currency, amount FROM fees WHERE strategy_ref = ?", (strategy_ref,)
            )
        }
        # One identifier for this read transaction (R2-D): positions, cash,
        # costs, instrument terms and the reconciliation verdict below are all
        # read inside it, so every figure of the snapshot declares the same cut.
        cut_id = f"{generation}:{int(cursor)}:{uuid.uuid4().hex}"
        base = dict(
            strategy_ref=strategy_ref,
            account_ref=self.account_ref,
            scope_directory_revision="strategy-ledger.v2",
            journal_generation=str(generation),
            baseline_execution_cursor=0,
            current_execution_cursor=int(cursor),
            positions=positions,
            fees_since_baseline=fees,
            position_cut_id=cut_id,
            reconciliation_state=None if reconciliation is None else reconciliation.state,
        )
        code = self._financial_error(db, strategy_ref, rows, reconciliation, int(cursor))
        if code is not None:
            return StrategyAccountBookSnapshot(**base, financial_error_code=code)
        costs = {}
        for name, quantity, cost_basis in rows:
            amount = Decimal(quantity)
            if amount == 0:
                continue
            terms = self._instruments[name]
            basis = Decimal(cost_basis)
            costs[name] = StrategyPositionCost(
                quantity=amount,
                cost_basis=basis,
                cost_currency=terms.quote_currency,
                product_model=terms.product_model,
                metadata_version=terms.version,
                average_cost=basis / amount,
            )
        cash = {
            currency: Decimal(amount)
            for currency, amount in db.execute(
                "SELECT currency, amount FROM strategy_cash WHERE strategy_ref = ?",
                (strategy_ref,),
            )
        }
        captured = max(int(self._clock_ns()), 1)
        last_event = db.execute(
            "SELECT cursor, at_ns FROM ledger_events ORDER BY cursor DESC LIMIT 1"
        ).fetchone()
        last_fill = db.execute(
            "SELECT cursor, at_ns FROM ledger_events WHERE action = 'EXECUTION'"
            " ORDER BY cursor DESC LIMIT 1"
        ).fetchone()
        if reconciliation is None or reconciliation.observed_ns is None:
            raise StrategyLedgerError("STRATEGY_BOOK_UNRECONCILED")
        watermarks = (
            StrategyFinancialWatermark(
                "execution_outbox",
                self.account_ref,
                str(generation),
                int(cursor),
                min(int(last_event[1]) if last_event else captured, captured),
            ),
            StrategyFinancialWatermark(
                "account_facts",
                str(reconciliation.source),
                str(generation),
                reconciliation.cursor,
                min(reconciliation.observed_ns, captured),
            ),
            StrategyFinancialWatermark(
                "account_activity",
                self.account_ref,
                str(generation),
                int(last_fill[0]) if last_fill else 0,
                min(int(last_fill[1]) if last_fill else captured, captured),
            ),
        )
        return StrategyAccountBookSnapshot(
            **base,
            cash_by_currency=cash,
            position_costs=costs,
            cumulative_fees_by_currency=fees,
            financial_available=True,
            cost_basis_complete=True,
            financial_error_code="",
            financial_source=f"{_FINANCIAL_SOURCE}:{CASH_POLICY}",
            financial_freshness="RECONCILED_IN_SNAPSHOT",
            financial_cut_relationship="SAME_TRANSACTION",
            financial_ledger_cut=StrategyFinancialLedgerCut(
                account_key=self.account_ref,
                owner_directory_revision="strategy-ledger.v2",
                captured_at_ns=captured,
                watermarks=watermarks,
            ),
            account_key=self.account_ref,
            financial_cut_id=cut_id,
        )

    def _financial_error(self, db, strategy_ref, rows, reconciliation, cursor) -> str | None:
        """Why strategy cash/costs are unavailable in this cut, or ``None``."""

        capital = self._capital_row(db, strategy_ref)
        if capital is None:
            return "STRATEGY_CAPITAL_UNCONFIGURED"
        if capital[1] is not None:
            return str(capital[1])
        if reconciliation is None:
            return "STRATEGY_BOOK_UNRECONCILED"
        if reconciliation.state == "IN_FLIGHT":
            return "STRATEGY_BOOK_RECONCILIATION_IN_FLIGHT"
        if reconciliation.state != "BALANCED" or reconciliation.cursor != cursor:
            return "STRATEGY_BOOK_UNRECONCILED"
        for name, quantity, cost_basis in rows:
            amount = Decimal(quantity)
            if amount == 0:
                continue
            terms = self._instruments.get(name)
            if terms is None:
                return "INSTRUMENT_TERMS_UNKNOWN"
            if amount < 0 or terms.product_model != "spot" or terms.quote_currency != capital[0]:
                return "STRATEGY_CAPITAL_MODEL_UNSUPPORTED"
            if cost_basis is None:
                return "COST_UNKNOWN"
        return None

    def reconcile(self, report: AccountPositionReport) -> LedgerReconciliation:
        """Compare strategy positions with one provider account report.

        Only an ``AccountPositionReport`` for this ledger's account is
        accepted; a plain mapping is refused (``ACCOUNT_POSITIONS_INVALID``).
        """

        with self._transaction() as db:
            return self._reconcile(db, report)

    def _reconcile(self, db: sqlite3.Connection, report: object) -> LedgerReconciliation:
        if type(report) is not AccountPositionReport:
            raise StrategyLedgerError("ACCOUNT_POSITIONS_INVALID")
        if report.account_ref != self.account_ref:
            raise StrategyLedgerError("ACCOUNT_POSITIONS_ACCOUNT_MISMATCH")
        cursor = int(db.execute("SELECT cursor FROM ledger_meta WHERE id = 1").fetchone()[0])
        totals: dict[str, Decimal] = {}
        for instrument, quantity in db.execute("SELECT instrument, quantity FROM positions"):
            totals[instrument] = totals.get(instrument, Decimal("0")) + Decimal(quantity)
        residual = {}
        for name in set(totals) | set(report.positions):
            difference = report.positions.get(name, Decimal("0")) - totals.get(name, Decimal("0"))
            if difference != 0:
                residual[name] = difference
        residual_view = MappingProxyType(residual)
        now = int(self._clock_ns())
        reason = None
        if not report.complete:
            reason = "REPORT_INCOMPLETE"
        elif report.observed_ns > now:
            reason = "REPORT_FROM_FUTURE"
        elif report.valid_until_ns is not None:
            if now > report.valid_until_ns:
                reason = "REPORT_STALE"
        elif now - report.observed_ns > self._max_age_ns:
            reason = "REPORT_STALE"
        if reason is not None:
            return LedgerReconciliation(
                "UNVERIFIED", residual_view, cursor, reason, report.source, report.observed_ns
            )
        if not residual:
            return LedgerReconciliation(
                "BALANCED", residual_view, cursor, None, report.source, report.observed_ns
            )
        unexplained = [
            name
            for name in residual
            if db.execute(
                "SELECT 1 FROM intents WHERE instrument = ? AND state IN (?, ?, ?, ?) LIMIT 1",
                (name, *_PENDING),
            ).fetchone()
            is None
            and db.execute(
                "SELECT 1 FROM ledger_events AS e JOIN intents AS i ON i.intent_id = e.intent_id"
                " WHERE i.instrument = ? AND e.action = 'EXECUTION' AND e.at_ns > ?"
                " AND e.at_ns >= ? LIMIT 1",
                # Only recent fills are excused: a difference that outlives the
                # settle window (the max report age) is a residual.
                (name, report.observed_ns, now - self._max_age_ns),
            ).fetchone()
            is None
        ]
        return LedgerReconciliation(
            "RESIDUAL" if unexplained else "IN_FLIGHT",
            residual_view,
            cursor,
            None,
            report.source,
            report.observed_ns,
        )

    def read_events(
        self, strategy_ref: str, *, after: int = 0, limit: int = 100
    ) -> LedgerEventPage:
        """Read this strategy's ledger events with ``cursor > after`` in order.

        The ledger event table is the direct-mode single source of strategy
        order/fill events (D60-16): a consumer keeps its own durable cursor
        bound to ``generation`` (see ``bt_api_py.strategy_event_feed``).
        """

        _text(strategy_ref, "STRATEGY_REF_INVALID")
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise StrategyLedgerError("EVENT_PAGE_INVALID")
        with self._transaction() as db:
            generation, head = db.execute(
                "SELECT generation, cursor FROM ledger_meta WHERE id = 1"
            ).fetchone()
            rows = db.execute(
                "SELECT e.cursor, e.intent_id, e.action, e.detail FROM ledger_events AS e"
                " JOIN intents AS i ON i.intent_id = e.intent_id"
                " WHERE i.strategy_ref = ? AND e.cursor > ? ORDER BY e.cursor LIMIT ?",
                (strategy_ref, after, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        events = tuple(
            LedgerEvent(
                f"{generation}:{cursor}",
                int(cursor),
                strategy_ref,
                intent_id,
                action,
                MappingProxyType(json.loads(detail)),
            )
            for cursor, intent_id, action, detail in rows
        )
        scanned = events[-1].cursor if has_more else int(head)
        return LedgerEventPage(str(generation), events, max(scanned, after), has_more)

    def reader(
        self,
        strategy_ref: str,
        *,
        account_positions: Callable[[], AccountPositionReport] | None = None,
    ) -> StrategyLedgerReader:
        return StrategyLedgerReader(self, strategy_ref, account_positions=account_positions)

    def close(self) -> None:
        with self._lock:
            self._db.close()
            if self._owner is not None:
                self._owner.release()
                self._owner = None


def _signed(value: object) -> Decimal:
    if type(value) is Decimal:
        parsed = value
    else:
        try:
            parsed = Decimal(value) if type(value) in (int, str) else None
        except (InvalidOperation, ValueError):
            parsed = None
    if parsed is None or not parsed.is_finite():
        raise StrategyLedgerError("ACCOUNT_POSITIONS_INVALID")
    return parsed


class StrategyLedgerReader:
    """One strategy's book for ``DirectStrategyAccountBookView``.

    With ``account_positions`` (returning an ``AccountPositionReport``) every
    snapshot is reconciled in the same transaction. ``BALANCED`` returns the
    snapshot with strategy cash; ``IN_FLIGHT`` returns ledger positions without
    financials (fills the provider has not shown yet); ``RESIDUAL`` and
    ``UNVERIFIED`` fail closed (``STRATEGY_BOOK_RECONCILIATION_<STATE>``), as
    does a failing provider read (``ACCOUNT_POSITIONS_UNAVAILABLE``). Without
    ``account_positions`` the snapshot has ledger positions only and no
    financials.
    """

    def __init__(
        self,
        ledger: SqliteStrategyLedger,
        strategy_ref: str,
        *,
        account_positions: Callable[[], AccountPositionReport] | None = None,
    ) -> None:
        if type(ledger) is not SqliteStrategyLedger:
            raise StrategyLedgerError("LEDGER_INVALID")
        if account_positions is not None and not callable(account_positions):
            raise StrategyLedgerError("ACCOUNT_POSITIONS_INVALID")
        self.ledger = ledger
        self.strategy_ref = _text(strategy_ref, "STRATEGY_REF_INVALID")
        self.account_ref = ledger.account_ref
        self._account_positions = account_positions

    @property
    def reconciled(self) -> bool:
        return self._account_positions is not None

    def snapshot(self) -> StrategyAccountBookSnapshot:
        if self._account_positions is None:
            return self.ledger.snapshot(self.strategy_ref)
        try:
            report = self._account_positions()
        except Exception:
            raise StrategyLedgerError("ACCOUNT_POSITIONS_UNAVAILABLE") from None
        reconciliation, snapshot = self.ledger.reconciled_snapshot(self.strategy_ref, report)
        if reconciliation.state not in ("BALANCED", "IN_FLIGHT"):
            raise StrategyLedgerError(f"STRATEGY_BOOK_RECONCILIATION_{reconciliation.state}")
        return snapshot
