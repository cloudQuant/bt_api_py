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


def compute_futures_linear(
    trades: Sequence[StrategyTrade],
    working: Sequence[WorkingOpenOrder],
    rates: StrategyRates,
    prices: Mapping[str, Decimal],
) -> StrategyFinancialAmounts:
    with localcontext() as context:
        context.prec = 60
        book: dict[tuple[str, str], list[Decimal]] = {}   # (symbol, direction) -> [quantity, average]
        realized = fees = Decimal(0)
        for trade in trades:
            if (type(trade) is not StrategyTrade or trade.side not in ("buy", "sell")
                    or trade.offset not in _OFFSETS):
                _unavailable("TRADE_INVALID")
            quantity = _positive(trade.quantity, "TRADE_INVALID")
            price = _positive(trade.price, "TRADE_INVALID")
            rate = rates.for_instrument(trade.symbol)
            if rate is None:
                _unavailable("RATES_UNAVAILABLE")
            fees += rate.commission(trade.offset).amount(
                price=price, quantity=quantity, multiplier=rate.multiplier)
            if trade.offset == "open":
                held = book.setdefault((trade.symbol, "long" if trade.side == "buy" else "short"),
                                       [Decimal(0), Decimal(0)])
                held[1] = (held[1] * held[0] + price * quantity) / (held[0] + quantity)
                held[0] += quantity
                continue
            direction = "long" if trade.side == "sell" else "short"
            held = book.get((trade.symbol, direction))
            if held is None or held[0] < quantity:
                # More was closed than this strategy's own trades opened.
                _unavailable("POSITION_UNDERFLOW")
            move = price - held[1] if direction == "long" else held[1] - price
            realized += move * quantity * rate.multiplier
            held[0] -= quantity
            if held[0] == 0:
                held[1] = Decimal(0)
        unrealized = margin = Decimal(0)
        holdings = []
        for (symbol, direction), (quantity, average) in sorted(book.items()):
            if quantity == 0:
                continue
            rate = rates.for_instrument(symbol)
            last = prices.get(symbol)
            if type(last) is not Decimal or not last.is_finite() or last <= 0:
                _unavailable("VALUATION_PRICE_UNAVAILABLE")
            move = last - average if direction == "long" else average - last
            unrealized += move * quantity * rate.multiplier
            margin += quantity * last * rate.multiplier * rate.margin_ratio(direction)
            holdings.append(StrategyHolding(symbol, direction, quantity, average))
        reservations = Decimal(0)
        for order in working:
            if type(order) is not WorkingOpenOrder or order.side not in ("buy", "sell"):
                _unavailable("WORKING_ORDER_INVALID")
            remaining = _positive(order.remaining, "WORKING_ORDER_INVALID")
            price = _positive(order.price, "WORKING_ORDER_INVALID")
            rate = rates.for_instrument(order.symbol)
            if rate is None:
                _unavailable("RATES_UNAVAILABLE")
            notional = remaining * price * rate.multiplier
            reservations += notional * rate.margin_ratio("long" if order.side == "buy" else "short")
            reservations += rate.open.amount(price=price, quantity=remaining, multiplier=rate.multiplier)

        def cent(value: Decimal) -> Decimal:
            return value.quantize(_CENT, rounding=ROUND_HALF_EVEN)

        return StrategyFinancialAmounts(cent(realized), cent(unrealized), cent(fees), cent(margin),
                                        cent(reservations), tuple(holdings))


__all__ = (
    "StrategyFinancialAmounts",
    "StrategyFinancialError",
    "StrategyHolding",
    "StrategyTrade",
    "WorkingOpenOrder",
    "compute_futures_linear",
)
