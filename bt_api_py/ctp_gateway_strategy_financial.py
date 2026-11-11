"""CTP producer of strategy cash/value snapshots (D63-53, D63-54, D63-55).

The Gateway journal can store one financial snapshot chain per strategy and
serve it over ``direct.strategy_financial``; it needs trusted owner code that
produces the snapshots and vouches for them. This module is that code for the
CTP trading profile. Everything runs on the account owner thread.

Inputs and where they come from:

* Initial budget: a fixed amount per strategy from the operator's resource
  bundle (D63-53). A strategy without one gets no snapshot, so its cash stays
  unavailable. The journal refuses a snapshot whose initial budget differs from
  the one its chain started with: a changed amount in the bundle makes that
  strategy's cash unavailable instead of rewriting history.
* Adjustments: a list in the bundle, each with its own id (D63-54). They take
  effect when the Gateway starts with them; their sum is the snapshot's
  ``adjustments`` and their ids are part of its source reference.
* Trades and working orders: the journal's own attributed returns of the
  strategy. A return on one of the instruments of this snapshot that is still
  unattributed or in conflict makes it unavailable.
* Rates: the shared local rates file (D63-55).
* Valuation price: the last price the Gateway received for the instrument on
  its own market connection. No price, or one older than the allowed age, makes
  a strategy that holds the instrument unavailable; a flat strategy needs none.

A snapshot is produced when the strategy's journal state has changed and, while
it holds a position, at least every ``refresh_seconds``. It is served only
while it is still the latest stored one, the strategy's journal state is the
one it was computed from, the return stream is still complete and, if prices
were used, the oldest of them is not older than ``price_max_age_seconds``.

Returns and commands are read once: each pass takes only what is new since the
previous one, so a pass costs what changed, not the whole history. After a
start the history is loaded in pages; until that is done the strategy is
reported as loading.

Limits:

* The amounts are estimates: fees and margin follow the operator's rates file,
  not the broker's books.
* A price exists only for instruments that some client subscribed to through
  this Gateway. A strategy that holds an instrument nobody subscribes to has
  no cash figure.
* Every price-driven refresh stores one row in the snapshot chain; the chain
  has a fixed capacity, so ``refresh_seconds`` should not be set lower than
  needed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from bt_api_py.strategy_financial_model import (
    FuturesLinearBook,
    StrategyFinancialError,
    StrategyTrade,
    WorkingOpenOrder,
)
from bt_api_py.strategy_rates import StrategyRates

_LOG = logging.getLogger(__name__)
_PAGE = 5000                       # rows of history taken per strategy and pass
_MAX_WATCHED = 10_000              # opening commands of one strategy that are not finished
_MAX_UNRESOLVED = 4096
_MAX_PRICE = Decimal("1e12")       # CTP fills unknown prices with the largest double
_TERMINAL = frozenset(("cancelled", "filled", "rejected"))
_AT_VENUE = frozenset(("DISPATCHING", "SENT", "UNKNOWN"))
_NEVER_SENT = frozenset(("RELEASED", "EXPIRED", "NOT_SENT"))
_STARTING = frozenset(("RETURNS_NOT_CONTINUOUS", "HISTORY_LOADING"))
_MIN_RECOMPUTE_SECONDS = 1.0
_FAILED_RETRY_SECONDS = 5.0


class CtpGatewayStrategyFinancialError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _text(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip() and len(value) <= 256


def _amount(value: object) -> Decimal:
    if type(value) not in (str, int):
        raise CtpGatewayStrategyFinancialError("INVALID_STRATEGY_FINANCIAL_SETTINGS")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise CtpGatewayStrategyFinancialError("INVALID_STRATEGY_FINANCIAL_SETTINGS") from None
    if not result.is_finite() or abs(result) > Decimal("1e15") or result != result.quantize(Decimal("0.01")):
        raise CtpGatewayStrategyFinancialError("INVALID_STRATEGY_FINANCIAL_SETTINGS")
    return result


@dataclass(frozen=True)
class CtpStrategyFinancialSettings:
    """Budgets, adjustments and rates from the operator's bundle; no default budget exists."""

    currency: str
    rates: StrategyRates
    budgets: Mapping[str, Decimal]
    adjustments: tuple[tuple[str, str, Decimal], ...] = ()
    refresh_seconds: float = 60.0
    price_max_age_seconds: float = 180.0

    def __post_init__(self) -> None:
        if (type(self.rates) is not StrategyRates or self.currency != self.rates.currency
                or not isinstance(self.budgets, Mapping) or not self.budgets or len(self.budgets) > 1024
                or any(not _text(name) or type(value) is not Decimal or not value.is_finite() or value < 0
                       for name, value in self.budgets.items())
                or type(self.adjustments) is not tuple or len(self.adjustments) > 4096
                or any(type(item) is not tuple or len(item) != 3 or not _text(item[0])
                       or item[1] not in self.budgets or type(item[2]) is not Decimal
                       or not item[2].is_finite() or item[2] == 0 for item in self.adjustments)
                or len({item[0] for item in self.adjustments}) != len(self.adjustments)
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in (self.refresh_seconds, self.price_max_age_seconds))
                or not 1 <= self.refresh_seconds <= 3600
                or not self.refresh_seconds <= self.price_max_age_seconds <= 7200):
            raise CtpGatewayStrategyFinancialError("INVALID_STRATEGY_FINANCIAL_SETTINGS")
        object.__setattr__(self, "budgets", MappingProxyType(dict(self.budgets)))

    @classmethod
    def from_bundle(cls, raw: object, *, rates: StrategyRates) -> CtpStrategyFinancialSettings:
        """``{"currency", "budgets": {strategy: amount}, "adjustments": [{"id", "strategy", "amount"}], ...}``."""
        required, optional = {"currency", "budgets"}, {"adjustments", "refresh_seconds", "price_max_age_seconds"}
        if type(raw) is not dict or not required <= set(raw) or not set(raw) <= required | optional:
            raise CtpGatewayStrategyFinancialError("INVALID_STRATEGY_FINANCIAL_SETTINGS")
        budgets, adjustments = raw["budgets"], raw.get("adjustments", [])
        if type(budgets) is not dict or type(adjustments) is not list or type(raw["currency"]) is not str:
            raise CtpGatewayStrategyFinancialError("INVALID_STRATEGY_FINANCIAL_SETTINGS")
        parsed = []
        for item in adjustments:
            if type(item) is not dict or set(item) != {"id", "strategy", "amount"}:
                raise CtpGatewayStrategyFinancialError("INVALID_STRATEGY_FINANCIAL_SETTINGS")
            parsed.append((item["id"], item["strategy"], _amount(item["amount"])))
        extra = {name: raw[name] for name in ("refresh_seconds", "price_max_age_seconds") if name in raw}
        return cls(raw["currency"], rates, {name: _amount(value) for name, value in budgets.items()},
                   tuple(parsed), **extra)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
                          .encode("utf-8")).hexdigest()


class _MoreHistoryError(Exception):
    """More history remains to be read; not a fault."""


class _Strategy:
    """What the producer remembers about one strategy between passes."""

    __slots__ = ("book", "sequence", "command_rowid", "command_count", "watched", "current", "failed")

    def __init__(self, rates: StrategyRates) -> None:
        self.book = FuturesLinearBook(rates)
        self.sequence = 0            # last execution event applied
        self.command_rowid = 0       # last command seen
        self.command_count = 0
        # reservation id -> [symbol, side, quantity, price, order status, remaining]
        self.watched: dict[str, list] = {}
        # (snapshot digest, journal state, needs prices, oldest price time, computed at)
        self.current: tuple | None = None
        self.failed: tuple | None = None   # (journal state, retry at)


class CtpGatewayStrategyFinancial:
    """Owner-thread producer and verifier; wire it with ``source()`` into the trading profile."""

    def __init__(self, settings: CtpStrategyFinancialSettings, *, journal: Any, guard: Any,
                 returns_continuous: Callable[[], object], last_price: Callable[[str], object],
                 clock: Callable[[], float] = time.monotonic) -> None:
        if (type(settings) is not CtpStrategyFinancialSettings or not callable(returns_continuous)
                or not callable(last_price) or not callable(clock)):
            raise CtpGatewayStrategyFinancialError("INVALID_STRATEGY_FINANCIAL_SETTINGS")
        self._settings, self._journal, self._guard = settings, journal, guard
        self._continuous, self._last_price, self._clock = returns_continuous, last_price, clock
        self._thread = threading.current_thread()
        self._adjustment_sum: dict[str, Decimal] = {}
        self._adjustment_ref: dict[str, str] = {}
        for name in settings.budgets:
            own = [(item[0], format(item[2], "f")) for item in settings.adjustments if item[1] == name]
            self._adjustment_sum[name] = sum((item[2] for item in settings.adjustments if item[1] == name),
                                             Decimal(0))
            self._adjustment_ref[name] = "bundle-adjustments:" + _digest(own)
        self._strategies = {name: _Strategy(settings.rates) for name in settings.budgets}
        self._reported: dict[str, str | None] = {}
        self._was_available: set[str] = set()
        self._last_attempt = -math.inf

    # -- the source object the journal and the profile use ----------------

    def source(self):
        from bt_api_py.gateway_strategy_financial_source import GatewayStrategyFinancialSource

        return GatewayStrategyFinancialSource(journal=self._journal, guard=self._guard,
                                              source_fence=self._fence, collect=self.collect,
                                              close=self._close)

    def _close(self) -> None:
        for item in self._strategies.values():
            item.current = None

    @contextmanager
    def _fence(self, account, strategy, currency):
        if threading.current_thread() is not self._thread:
            raise CtpGatewayStrategyFinancialError("OWNER_REQUIRED")
        yield lambda db, snapshot: self._verify(db, account, strategy, currency, snapshot)

    def _verify(self, db, account, strategy, currency, snapshot) -> bool:
        try:
            item = self._strategies.get(strategy)
            known = None if item is None else item.current
            if (known is None or currency != self._settings.currency
                    or account != self._journal.account_scope
                    or snapshot.strategy != strategy or snapshot.digest != known[0]
                    or self._continuous() is not True
                    or self._state(db, strategy, item) != known[1]):
                return False
            return not known[2] or self._clock() - known[3] <= self._settings.price_max_age_seconds
        except Exception:
            return False

    # -- journal reads -----------------------------------------------------

    def _state(self, db, strategy: str, item: _Strategy) -> tuple:
        """A cheap mark of everything in the journal that the strategy's amounts depend on."""
        account = self._journal.account_scope
        events = db.execute("SELECT coalesce(max(sequence),0) FROM direct_execution_events "
                            "WHERE account=? AND strategy=?", (account, strategy)).fetchone()[0]
        commands = db.execute("SELECT count(*) FROM direct_journal_commands WHERE account=? AND strategy=?",
                              (account, strategy)).fetchone()[0]
        unresolved = db.execute("SELECT count(*) FROM direct_execution_facts WHERE account=? "
                                "AND disposition IN ('UNATTRIBUTED','CONFLICT')", (account,)).fetchone()[0]
        watched = tuple(
            (reservation, (db.execute("SELECT status FROM direct_journal_commands WHERE reservation_id=?",
                                      (reservation,)).fetchone() or (None,))[0])
            for reservation in sorted(item.watched))
        return events, commands, unresolved, watched

    @staticmethod
    def _watch(item: _Strategy, reservation: str, payload_json: str) -> None:
        payload = json.loads(payload_json)
        if payload.get("offset") != "open":
            return
        price = payload.get("price")
        item.watched[reservation] = [
            payload["symbol"], payload["side"], Decimal(str(payload["quantity"])),
            None if price in (None, "") else Decimal(str(price)), None, None]
        if len(item.watched) > _MAX_WATCHED:
            raise StrategyFinancialError("TOO_MANY_OPEN_COMMANDS")

    def _advance(self, db, strategy: str, item: _Strategy, state: tuple) -> None:
        """Take what is new since the last pass: commands first, then execution events."""
        item.book.holdings()               # a book that failed once stays failed; read nothing more
        account = self._journal.account_scope
        rows = db.execute(
            "SELECT rowid, reservation_id, payload_json FROM direct_journal_commands "
            "WHERE account=? AND strategy=? AND rowid>? ORDER BY rowid LIMIT ?",
            (account, strategy, item.command_rowid, _PAGE)).fetchall()
        for rowid, reservation, payload_json in rows:
            self._watch(item, reservation, payload_json)
            item.command_rowid, item.command_count = rowid, item.command_count + 1
        if len(rows) == _PAGE:
            raise _MoreHistoryError
        if item.command_count != state[1]:
            # Commands were not only appended; what was read before cannot be trusted.
            raise StrategyFinancialError("COMMAND_HISTORY_INCONSISTENT")
        rows = db.execute(
            "SELECT e.sequence, e.fact_json, f.reservation_id, f.disposition, f.applied, f.reason "
            "FROM direct_execution_events e LEFT JOIN direct_execution_facts f "
            "ON f.account=e.account AND f.fact_digest=e.fact_digest "
            "WHERE e.account=? AND e.strategy=? AND e.sequence>? ORDER BY e.sequence LIMIT ?",
            (account, strategy, item.sequence, _PAGE)).fetchall()
        for sequence, fact_json, reservation, disposition, applied, reason in rows:
            fact = json.loads(fact_json)
            kind = fact.get("kind")
            if kind == "trade":
                # The journal counts a trade once (applied); a later, richer report of the
                # same trade is in the stream too and must not be counted again.
                if applied == 1:
                    if disposition != "ATTRIBUTED" or reason != "MATCHED":
                        # The same rule as the strategy position ledger: not a plain match, no figure.
                        raise StrategyFinancialError("TRADE_ATTRIBUTION_NOT_MATCHED")
                    item.book.apply(StrategyTrade(
                        fact["instrument"]["native_symbol"], fact["side"], fact["offset"],
                        Decimal(fact["trade_quantity"]), Decimal(fact["trade_price"])))
            elif kind == "order" and reservation is not None:
                status = fact.get("status")
                if reservation not in item.watched and status not in _TERMINAL:
                    # A return for a command that was dropped as never sent: it is at the venue after all.
                    row = db.execute("SELECT payload_json FROM direct_journal_commands "
                                     "WHERE reservation_id=? AND account=? AND strategy=?",
                                     (reservation, account, strategy)).fetchone()
                    if row is not None:
                        self._watch(item, reservation, row[0])
                entry = item.watched.get(reservation)
                if entry is not None:
                    total, filled = fact.get("order_quantity"), fact.get("filled_quantity")
                    entry[4] = status
                    entry[5] = None if total is None or filled is None else Decimal(total) - Decimal(filled)
            item.sequence = sequence
        if len(rows) == _PAGE:
            raise _MoreHistoryError

    def _working(self, db, item: _Strategy) -> list[WorkingOpenOrder]:
        working = []
        for reservation in sorted(item.watched):
            symbol, side, quantity, price, order_status, remaining = item.watched[reservation]
            if order_status in _TERMINAL:
                del item.watched[reservation]
                continue
            row = db.execute("SELECT status FROM direct_journal_commands WHERE reservation_id=?",
                             (reservation,)).fetchone()
            status = None if row is None else row[0]
            if status not in _AT_VENUE and order_status is None:
                if status in _NEVER_SENT:
                    del item.watched[reservation]
                continue                 # reserved only: nothing is at the venue yet
            left = quantity if remaining is None else remaining
            if left <= 0:
                continue
            if price is None:
                # A working opening order without a limit price cannot be reserved for.
                raise StrategyFinancialError("WORKING_ORDER_PRICE_UNAVAILABLE")
            working.append(WorkingOpenOrder(symbol, side, left, price))
        return working

    def _unresolved_symbols(self, db) -> frozenset[str]:
        rows = db.execute("SELECT fact_json FROM direct_execution_facts WHERE account=? "
                          "AND disposition IN ('UNATTRIBUTED','CONFLICT') LIMIT ?",
                          (self._journal.account_scope, _MAX_UNRESOLVED + 1)).fetchall()
        if len(rows) > _MAX_UNRESOLVED:
            raise StrategyFinancialError("TOO_MANY_UNRESOLVED_RETURNS")
        symbols = set()
        for row in rows:
            fact = json.loads(row[0])
            if fact.get("kind") == "trade":
                symbols.add(fact["instrument"]["native_symbol"])
        return frozenset(symbols)

    # -- production ----------------------------------------------------------

    def _report(self, strategy: str, code: str | None) -> None:
        if self._reported.get(strategy, "") != code:
            self._reported[strategy] = code
            if code is None:
                self._was_available.add(strategy)
                _LOG.info("Strategy cash/value of %s is available", strategy)
            elif code in _STARTING and strategy not in self._was_available:
                # Expected right after a start: returns are not complete yet, history is being read.
                _LOG.info("Strategy cash/value of %s is not available yet: %s", strategy, code)
            else:
                _LOG.warning("Strategy cash/value of %s is unavailable: %s", strategy, code)

    def collect(self) -> tuple:
        """One maintenance pass; returns the new snapshots (possibly none)."""
        from bt_api_gateway.strategy_financial_ledger import StrategyFinancialSnapshot

        if threading.current_thread() is not self._thread:
            raise CtpGatewayStrategyFinancialError("OWNER_REQUIRED")
        now = self._clock()
        if now - self._last_attempt < _MIN_RECOMPUTE_SECONDS:
            return ()
        self._last_attempt = now
        journal, settings = self._journal, self._settings
        if self._continuous() is not True:
            for strategy in settings.budgets:
                self._report(strategy, "RETURNS_NOT_CONTINUOUS")
            return ()
        account, produced, unresolved = journal.account_scope, [], None
        with journal._transaction() as db:
            for strategy in sorted(settings.budgets):
                item = self._strategies[strategy]
                state = self._state(db, strategy, item)
                row = db.execute(
                    "SELECT snapshot_digest, snapshot_json FROM strategy_financial_snapshots "
                    "WHERE account=? AND strategy=? AND currency=? ORDER BY revision DESC LIMIT 1",
                    (account, strategy, settings.currency)).fetchone()
                known = item.current
                if known is not None:
                    stale = known[2] and (now - known[4] >= settings.refresh_seconds
                                          or now - known[3] > settings.price_max_age_seconds)
                    if row is not None and row[0] == known[0] and known[1] == state and not stale:
                        continue
                elif item.failed is not None and item.failed[0] == state and now < item.failed[1]:
                    continue
                item.current = item.failed = None
                try:
                    self._advance(db, strategy, item, state)
                    working = self._working(db, item)
                    symbols = item.book.symbols | {order.symbol for order in working}
                    if symbols:
                        if unresolved is None:
                            unresolved = self._unresolved_symbols(db)
                        if symbols & unresolved:
                            # It may be a trade of this strategy that is not attributed yet.
                            raise StrategyFinancialError("UNATTRIBUTED_TRADE_ON_INSTRUMENT")
                    prices, used, price_time = {}, [], now
                    for held in item.book.holdings():
                        seen = self._last_price(held.symbol)
                        if seen is None or held.symbol in prices or now - seen[1] > settings.price_max_age_seconds:
                            continue
                        prices[held.symbol] = seen[0]
                        used.append((held.symbol, format(seen[0], "f")))
                        price_time = min(price_time, seen[1])
                    amounts = item.book.amounts(working, prices)
                    last = None if row is None else StrategyFinancialSnapshot.from_mapping(json.loads(row[1]))
                    budget = format(settings.budgets[strategy], "f")
                    if last is not None and last.initial_budget != budget:
                        # The chain started with another amount; adjustments are the way to change it.
                        raise StrategyFinancialError("INITIAL_BUDGET_CHANGED")
                except _MoreHistoryError:
                    self._report(strategy, "HISTORY_LOADING")
                    continue             # next pass takes the next page
                except StrategyFinancialError as error:
                    item.failed = (state, now + _FAILED_RETRY_SECONDS)
                    self._report(strategy, error.code)
                    continue
                except Exception as error:  # malformed stored data: unavailable, with the reason logged
                    item.failed = (state, now + _FAILED_RETRY_SECONDS)
                    self._report(strategy, "INPUT_INVALID:" + type(error).__name__)
                    continue
                # Dropping finished commands changes the watched part of the state.
                state = self._state(db, strategy, item)
                inputs_ref = _digest([state, used, settings.rates.digest])
                content = {
                    "cut_ref": "ctp-strategy-financial:" + inputs_ref,
                    "source_refs": tuple(sorted({
                        "budget": "bundle-budget:" + _digest([strategy, budget]),
                        "adjustments": self._adjustment_ref[strategy],
                        "realized_pnl": "journal-trades:" + _digest(state[0]),
                        "unrealized_pnl": "gateway-last-price:" + _digest(used),
                        "fees": "rates-file:" + settings.rates.digest,
                        "margin": "rates-file:" + settings.rates.digest,
                        "reservations": "journal-working-orders:" + _digest(state),
                        "coverage": "ctp-returns-continuous:" + inputs_ref,
                    }.items())),
                    "initial_budget": budget,
                    "adjustments": format(self._adjustment_sum[strategy], "f"),
                    "realized_pnl": format(amounts.realized_pnl, "f"),
                    "unrealized_pnl": format(amounts.unrealized_pnl, "f"),
                    "fees": format(amounts.fees, "f"),
                    "margin": format(amounts.margin, "f"),
                    "reservations": format(amounts.reservations, "f"),
                }
                amounts_only = ("initial_budget", "adjustments", "realized_pnl", "unrealized_pnl", "fees",
                                "margin", "reservations")
                if (last is not None and last.owner_generation == journal.generation
                        and all(getattr(last, name) == content[name] for name in amounts_only)):
                    snapshot = last          # the same amounts: keep serving the stored one
                else:
                    snapshot = StrategyFinancialSnapshot(
                        account, journal.journal_id, strategy, settings.currency,
                        1 if last is None else last.revision + 1,
                        None if last is None else last.digest,
                        owner_generation=journal.generation, **content)
                    produced.append(snapshot)
                item.current = (snapshot.digest, state, bool(amounts.holdings), price_time, now)
                self._report(strategy, None)
        return tuple(produced)


class OwnerLastPrices:
    """Last price per native symbol, from the ticks the owner thread drains for the market hub.

    The events are the Gateway's projected ticks (``sdk.market.tick``): the
    instrument is a mapping with ``native_symbol`` and the price is ``last_price``.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._prices: dict[str, tuple[Decimal, float]] = {}

    def observe(self, events) -> None:
        """Take the provider events of one drained batch; anything unusable is skipped."""
        for event in events:
            data = getattr(event, "data", None)
            if not isinstance(data, Mapping):
                continue
            instrument = data.get("instrument")
            if data.get("schema") != "sdk.market.tick" or not isinstance(instrument, Mapping):
                continue
            symbol, raw = instrument.get("native_symbol"), data.get("last_price")
            if type(symbol) is not str or not symbol or type(raw) not in (int, float, str):
                continue
            try:
                price = Decimal(str(raw))
            except InvalidOperation:
                price = None
            if price is not None and price.is_finite() and 0 < price < _MAX_PRICE:
                self._prices[symbol] = (price, self._clock())

    def last(self, symbol: str):
        return self._prices.get(symbol)


__all__ = (
    "CtpGatewayStrategyFinancial",
    "CtpGatewayStrategyFinancialError",
    "CtpStrategyFinancialSettings",
    "OwnerLastPrices",
)
