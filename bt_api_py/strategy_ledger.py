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
* OPEN with a configured budget (policy ``gross_exposure_at_cost.v2``):
  existing positions at cost plus pending opens plus this order, all times
  the configured contract multiplier, must stay within the budget. Unknown
  inputs are refused, never assumed: ``MULTIPLIER_UNKNOWN``, ``PRICE_UNKNOWN``
  and ``COST_UNKNOWN`` (a fill without a price);
* pending quantity (reserved, submitted, UNKNOWN, partially filled) keeps
  counting until the intent is finished, and survives restarts.

Executions are applied by cumulative quantity (``apply_execution``), so
redelivered or reordered reports are idempotent and can never overfill.

Snapshots are the Base ``StrategyAccountBookSnapshot`` (positions and fees;
cash/value stay unavailable). ``reconcile`` compares the sum of strategy
positions with provider account positions; a reader built with an account
position source fails closed unless that comparison is ``BALANCED``.
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

from bt_api_base.account_book_protocol import StrategyAccountBookSnapshot

__all__ = [
    "BUDGET_POLICY",
    "LedgerIntent",
    "LedgerReconciliation",
    "SqliteStrategyLedger",
    "StrategyBudget",
    "StrategyLedgerError",
    "StrategyLedgerReader",
]

BUDGET_POLICY = "gross_exposure_at_cost.v2"
_SCHEMA_VERSION = 1
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
class LedgerReconciliation:
    """Sum of strategy positions against provider account positions."""

    state: str  # BALANCED | RESIDUAL
    residual: Mapping[str, Decimal]
    cursor: int


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
        contract_multipliers: Mapping[str, object] | None = None,
        budgets: Mapping[str, StrategyBudget] | None = None,
        exclusive_owner: bool = True,
    ) -> None:
        self.account_ref = _text(account_ref, "ACCOUNT_REF_INVALID")
        self._multipliers = MappingProxyType(
            {
                _text(name, "MULTIPLIER_INVALID"): _decimal(
                    value, "MULTIPLIER_INVALID", positive=True
                )
                for name, value in (contract_multipliers or {}).items()
            }
        )
        budgets = dict(budgets or {})
        for strategy_ref, budget in budgets.items():
            _text(strategy_ref, "BUDGET_INVALID")
            if type(budget) is not StrategyBudget:
                raise StrategyLedgerError("BUDGET_INVALID")
        self._budgets = MappingProxyType(budgets)
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

    class _Tx:
        def __init__(self, ledger: SqliteStrategyLedger) -> None:
            self._ledger = ledger

        def __enter__(self) -> sqlite3.Connection:
            self._ledger._lock.acquire()
            try:
                self._ledger._db.execute("BEGIN IMMEDIATE")
            except BaseException:
                self._ledger._lock.release()
                raise
            return self._ledger._db

        def __exit__(self, exc_type, exc, tb) -> None:
            try:
                self._ledger._db.execute("COMMIT" if exc_type is None else "ROLLBACK")
            finally:
                self._ledger._lock.release()

    def _transaction(self) -> SqliteStrategyLedger._Tx:
        return SqliteStrategyLedger._Tx(self)

    @staticmethod
    def _event(db: sqlite3.Connection, intent_id: str, action: str, detail: dict) -> int:
        cursor = int(db.execute("SELECT cursor FROM ledger_meta WHERE id = 1").fetchone()[0]) + 1
        db.execute("UPDATE ledger_meta SET cursor = ? WHERE id = 1", (cursor,))
        db.execute(
            "INSERT INTO ledger_events VALUES (?, ?, ?, ?, ?)",
            (cursor, time.time_ns(), intent_id, action, json.dumps(detail, sort_keys=True)),
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

    def _exposure(self, db: sqlite3.Connection, strategy_ref: str) -> Decimal:
        total = Decimal("0")
        for instrument, quantity, cost_basis in db.execute(
            "SELECT instrument, quantity, cost_basis FROM positions WHERE strategy_ref = ?",
            (strategy_ref,),
        ):
            if Decimal(quantity) == 0:
                continue
            if cost_basis is None:
                raise StrategyLedgerError("COST_UNKNOWN")
            total += Decimal(cost_basis) * self._multiplier(instrument)
        for _id, instrument, _effect, quantity, price, filled in self._pending(
            db, strategy_ref, effect="OPEN"
        ):
            if price is None:
                raise StrategyLedgerError("PRICE_UNKNOWN")
            remaining = Decimal(quantity) - Decimal(filled)
            total += remaining * Decimal(price) * self._multiplier(instrument)
        return total

    def _multiplier(self, instrument: str) -> Decimal:
        multiplier = self._multipliers.get(instrument)
        if multiplier is None:
            raise StrategyLedgerError("MULTIPLIER_UNKNOWN")
        return multiplier

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
                if budget is not None:
                    if intent.price is None:
                        raise StrategyLedgerError("PRICE_UNKNOWN")
                    requested = intent.quantity * intent.price * self._multiplier(intent.instrument)
                    if self._exposure(db, intent.strategy_ref) + requested > budget.limit:
                        raise StrategyLedgerError("BUDGET_EXCEEDED")
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
                    time.time_ns(),
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
        ``fee`` is the cumulative fee in ``fee_currency``.
        """

        cumulative = _decimal(cumulative_quantity, "EXECUTION_INVALID")
        average = _decimal(average_price, "EXECUTION_INVALID", positive=True, allow_none=True)
        fee_total = _decimal(fee, "EXECUTION_INVALID", allow_none=True)
        if fee_total is not None:
            _text(fee_currency, "EXECUTION_INVALID")
        with self._transaction() as db:
            strategy_ref, instrument, side, quantity, state, filled = self._intent(db, intent_id)
            quantity, filled = Decimal(quantity), Decimal(filled)
            if cumulative > quantity:
                raise StrategyLedgerError("EXECUTION_OVERFILL")
            delta = cumulative - filled
            if delta <= 0:
                return Decimal("0")
            # A late report for a finished (cancelled) intent is still a real
            # fill and is applied; the state stays final.
            prior_fee = db.execute(
                "SELECT detail FROM ledger_events WHERE intent_id = ? AND action = 'EXECUTION'"
                " ORDER BY cursor DESC LIMIT 1",
                (intent_id,),
            ).fetchone()
            prior = json.loads(prior_fee[0]) if prior_fee is not None else {}
            # Price of this delta from cumulative averages (exact decimals).
            delta_price: Decimal | None
            prior_average = _dec(prior.get("average_price"))
            if average is None or (filled > 0 and prior_average is None):
                delta_price = None
            else:
                delta_price = (cumulative * average - filled * (prior_average or 0)) / delta
                if delta_price <= 0:
                    delta_price = None
            position, cost = self._position(db, strategy_ref, instrument)
            signed = delta if side == "BUY" else -delta
            new_position = position + signed
            if position == 0 or (position > 0) == (signed > 0):
                new_cost = (
                    None if cost is None or delta_price is None else cost + delta * delta_price
                )
            elif abs(signed) <= abs(position):
                new_cost = None if cost is None else cost * abs(new_position) / abs(position)
            else:
                # Crossing zero: the closing part keeps the old cost, the
                # opening part starts a new basis.
                opened = abs(new_position)
                new_cost = None if delta_price is None else opened * delta_price
            if new_position == 0:
                new_cost = Decimal("0")
            db.execute(
                "INSERT INTO positions VALUES (?, ?, ?, ?) ON CONFLICT(strategy_ref, instrument)"
                " DO UPDATE SET quantity = excluded.quantity, cost_basis = excluded.cost_basis",
                (
                    strategy_ref,
                    instrument,
                    str(new_position),
                    None if new_cost is None else str(new_cost),
                ),
            )
            if fee_total is not None:
                prior_total = _dec(prior.get("fee")) or Decimal("0")
                fee_delta = fee_total - prior_total
                if fee_delta < 0:
                    raise StrategyLedgerError("EXECUTION_FEE_REGRESSED")
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
            next_state = state
            if state not in _FINAL:
                next_state = "FILLED" if cumulative == quantity else "PARTIALLY_FILLED"
            db.execute(
                "UPDATE intents SET filled = ?, state = ? WHERE intent_id = ?",
                (str(cumulative), next_state, intent_id),
            )
            self._event(
                db,
                intent_id,
                "EXECUTION",
                {
                    "cumulative": str(cumulative),
                    "average_price": None if average is None else str(average),
                    "fee": None if fee_total is None else str(fee_total),
                },
            )
            return delta

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
        _text(strategy_ref, "STRATEGY_REF_INVALID")
        with self._transaction() as db:
            generation, cursor = db.execute(
                "SELECT generation, cursor FROM ledger_meta WHERE id = 1"
            ).fetchone()
            positions = {
                name: Decimal(quantity)
                for name, quantity in db.execute(
                    "SELECT instrument, quantity FROM positions WHERE strategy_ref = ?",
                    (strategy_ref,),
                )
                if Decimal(quantity) != 0
            }
            fees = {
                currency: Decimal(amount)
                for currency, amount in db.execute(
                    "SELECT currency, amount FROM fees WHERE strategy_ref = ?", (strategy_ref,)
                )
            }
        return StrategyAccountBookSnapshot(
            strategy_ref=strategy_ref,
            account_ref=self.account_ref,
            scope_directory_revision="strategy-ledger.v1",
            journal_generation=str(generation),
            baseline_execution_cursor=0,
            current_execution_cursor=int(cursor),
            positions=positions,
            fees_since_baseline=fees,
        )

    def reconcile(self, account_positions: Mapping[str, object]) -> LedgerReconciliation:
        """Compare strategy positions with the provider's account positions."""

        provider = {
            _text(name, "ACCOUNT_POSITIONS_INVALID"): _signed(value)
            for name, value in account_positions.items()
        }
        with self._transaction() as db:
            cursor = int(db.execute("SELECT cursor FROM ledger_meta WHERE id = 1").fetchone()[0])
            totals: dict[str, Decimal] = {}
            for instrument, quantity in db.execute("SELECT instrument, quantity FROM positions"):
                totals[instrument] = totals.get(instrument, Decimal("0")) + Decimal(quantity)
        residual = {}
        for name in set(totals) | set(provider):
            difference = provider.get(name, Decimal("0")) - totals.get(name, Decimal("0"))
            if difference != 0:
                residual[name] = difference
        return LedgerReconciliation(
            "BALANCED" if not residual else "RESIDUAL", MappingProxyType(residual), cursor
        )

    def reader(
        self,
        strategy_ref: str,
        *,
        account_positions: Callable[[], Mapping[str, object]] | None = None,
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

    With ``account_positions`` the snapshot is only returned when the ledger
    reconciles with the provider account (``STRATEGY_BOOK_RECONCILIATION_RESIDUAL``
    otherwise); the provider read failing fails closed too.
    """

    def __init__(
        self,
        ledger: SqliteStrategyLedger,
        strategy_ref: str,
        *,
        account_positions: Callable[[], Mapping[str, object]] | None = None,
    ) -> None:
        if type(ledger) is not SqliteStrategyLedger:
            raise StrategyLedgerError("LEDGER_INVALID")
        if account_positions is not None and not callable(account_positions):
            raise StrategyLedgerError("ACCOUNT_POSITIONS_INVALID")
        self.ledger = ledger
        self.strategy_ref = _text(strategy_ref, "STRATEGY_REF_INVALID")
        self.account_ref = ledger.account_ref
        self._account_positions = account_positions

    def snapshot(self) -> StrategyAccountBookSnapshot:
        if self._account_positions is not None:
            try:
                provider = self._account_positions()
            except Exception:
                raise StrategyLedgerError("ACCOUNT_POSITIONS_UNAVAILABLE") from None
            if self.ledger.reconcile(provider).state != "BALANCED":
                raise StrategyLedgerError("STRATEGY_BOOK_RECONCILIATION_RESIDUAL")
        return self.ledger.snapshot(self.strategy_ref)
