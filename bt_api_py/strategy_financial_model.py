"""Futures model ``futures_linear_v1`` for one strategy's cash and value (D63-44, D63-55).

Pure arithmetic, no I/O. The caller supplies the strategy's own trades in the
order they were applied, its working opening orders, the shared rates and one
valuation price per instrument it still holds. The result is the five amounts
of a strategy financial snapshot besides the budget:

* realized P&L before fees: every close against the average open price of its
  side (long and short are kept apart, as CTP does);
* unrealized P&L: what is still held, at the valuation price;
* fees: every trade at the rate of its offset, estimated from the rates file
  (a CTP trade return carries no fee);
* margin: what is still held, at the valuation price, each side on its own;
* reservations: margin and opening fee of working opening orders at their
  limit price. Closing orders reserve nothing.

Everything that cannot be computed from known inputs raises
``StrategyFinancialError``; nothing is filled with zero.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from bt_api_py.strategy_rates import StrategyRates

_CENT = Decimal("0.01")
_OFFSETS = frozenset(("open", "close", "close_today", "close_yesterday"))


class StrategyFinancialError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _unavailable(code: str):
    raise StrategyFinancialError(code)


@dataclass(frozen=True)
class StrategyTrade:
    symbol: str
    side: str          # "buy" or "sell"
    offset: str        # "open", "close", "close_today", "close_yesterday"
    quantity: Decimal
    price: Decimal


@dataclass(frozen=True)
class WorkingOpenOrder:
    symbol: str
    side: str          # "buy" opens long, "sell" opens short
    remaining: Decimal
    price: Decimal


@dataclass(frozen=True)
class StrategyHolding:
    symbol: str
    direction: str     # "long" or "short"
    quantity: Decimal
    average_price: Decimal


@dataclass(frozen=True)
class StrategyFinancialAmounts:
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    fees: Decimal
    margin: Decimal
    reservations: Decimal
    holdings: tuple[StrategyHolding, ...]


def _positive(value: object, code: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        _unavailable(code)
    return value


class FuturesLinearBook:
    """Running state of one strategy: apply each trade once, in order, then ask for the amounts.

    A trade that cannot be applied makes the book unusable for good: every later
    call raises the same code, because the history it stands for is not known.
    """

    def __init__(self, rates: StrategyRates) -> None:
        if type(rates) is not StrategyRates:
            raise ValueError("INVALID_RATES")
        self._rates = rates
        self._book: dict[tuple[str, str], list[Decimal]] = {}   # (symbol, direction) -> [quantity, average]
        self._realized = self._fees = Decimal(0)
        self._symbols: set[str] = set()
        self._failed: str | None = None

    @property
    def symbols(self) -> frozenset[str]:
        """Every instrument this strategy has traded."""
        return frozenset(self._symbols)

    def apply(self, trade: StrategyTrade) -> None:
        if self._failed is not None:
            _unavailable(self._failed)
        try:
            with localcontext() as context:
                context.prec = 60
                self._apply(trade)
        except StrategyFinancialError as error:
            self._failed = error.code
            raise

    def _apply(self, trade: StrategyTrade) -> None:
        if (type(trade) is not StrategyTrade or trade.side not in ("buy", "sell")
                or trade.offset not in _OFFSETS):
            _unavailable("TRADE_INVALID")
        quantity = _positive(trade.quantity, "TRADE_INVALID")
        price = _positive(trade.price, "TRADE_INVALID")
        rate = self._rates.for_instrument(trade.symbol)
        if rate is None:
            _unavailable("RATES_UNAVAILABLE")
        fee = rate.commission(trade.offset).amount(price=price, quantity=quantity, multiplier=rate.multiplier)
        if trade.offset == "open":
            held = self._book.setdefault((trade.symbol, "long" if trade.side == "buy" else "short"),
                                         [Decimal(0), Decimal(0)])
            held[1] = (held[1] * held[0] + price * quantity) / (held[0] + quantity)
            held[0] += quantity
        else:
            direction = "long" if trade.side == "sell" else "short"
            held = self._book.get((trade.symbol, direction))
            if held is None or held[0] < quantity:
                # More was closed than this strategy's own trades opened.
                _unavailable("POSITION_UNDERFLOW")
            move = price - held[1] if direction == "long" else held[1] - price
            self._realized += move * quantity * rate.multiplier
            held[0] -= quantity
            if held[0] == 0:
                held[1] = Decimal(0)
        self._fees += fee
        self._symbols.add(trade.symbol)

    def holdings(self) -> tuple[StrategyHolding, ...]:
        if self._failed is not None:
            _unavailable(self._failed)
        return tuple(StrategyHolding(symbol, direction, quantity, average)
                     for (symbol, direction), (quantity, average) in sorted(self._book.items()) if quantity != 0)

    def amounts(self, working: Sequence[WorkingOpenOrder],
                prices: Mapping[str, Decimal]) -> StrategyFinancialAmounts:
        holdings = self.holdings()
        with localcontext() as context:
            context.prec = 60
            unrealized = margin = reservations = Decimal(0)
            for held in holdings:
                rate = self._rates.for_instrument(held.symbol)
                last = prices.get(held.symbol)
                if type(last) is not Decimal or not last.is_finite() or last <= 0:
                    _unavailable("VALUATION_PRICE_UNAVAILABLE")
                move = last - held.average_price if held.direction == "long" else held.average_price - last
                unrealized += move * held.quantity * rate.multiplier
                margin += held.quantity * last * rate.multiplier * rate.margin_ratio(held.direction)
            for order in working:
                if type(order) is not WorkingOpenOrder or order.side not in ("buy", "sell"):
                    _unavailable("WORKING_ORDER_INVALID")
                remaining = _positive(order.remaining, "WORKING_ORDER_INVALID")
                price = _positive(order.price, "WORKING_ORDER_INVALID")
                rate = self._rates.for_instrument(order.symbol)
                if rate is None:
                    _unavailable("RATES_UNAVAILABLE")
                notional = remaining * price * rate.multiplier
                reservations += notional * rate.margin_ratio("long" if order.side == "buy" else "short")
                reservations += rate.open.amount(price=price, quantity=remaining, multiplier=rate.multiplier)

            def cent(value: Decimal) -> Decimal:
                return value.quantize(_CENT, rounding=ROUND_HALF_EVEN)

            return StrategyFinancialAmounts(cent(self._realized), cent(unrealized), cent(self._fees),
                                            cent(margin), cent(reservations), holdings)


def compute_futures_linear(
    trades: Sequence[StrategyTrade],
    working: Sequence[WorkingOpenOrder],
    rates: StrategyRates,
    prices: Mapping[str, Decimal],
) -> StrategyFinancialAmounts:
    """The amounts after applying ``trades`` in order to an empty book."""
    book = FuturesLinearBook(rates)
    for trade in trades:
        book.apply(trade)
    return book.amounts(working, prices)


__all__ = (
    "FuturesLinearBook",
    "StrategyFinancialAmounts",
    "StrategyFinancialError",
    "StrategyHolding",
    "StrategyTrade",
    "WorkingOpenOrder",
    "compute_futures_linear",
)
