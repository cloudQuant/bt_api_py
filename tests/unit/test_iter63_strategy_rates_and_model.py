"""Shared rates file (D63-55) and the futures model for strategy cash and value (D63-44)."""
from decimal import Decimal

import pytest

from bt_api_py.strategy_financial_model import (
    FuturesLinearBook,
    StrategyFinancialError,
    StrategyHolding,
    StrategyTrade,
    WorkingOpenOrder,
    compute_futures_linear,
)
from bt_api_py.strategy_rates import StrategyRatesError, load_strategy_rates, parse_strategy_rates

D = Decimal

RATES = b"""
schema_version: 1
currency: CNY
products:
  rb:
    multiplier: 10
    margin_ratio: {long: "0.10", short: "0.12"}
    commission:
      open: {per_lot: "0", ratio: "0.0001"}
      close: {per_lot: "0", ratio: "0.0001"}
      close_today: {per_lot: "0", ratio: "0.0003"}
  SA:
    multiplier: 20
    margin_ratio: {long: 0.09, short: 0.09}
    commission:
      open: {per_lot: 3.5, ratio: 0}
      close: {per_lot: 3.5, ratio: 0}
      close_today: {per_lot: 7, ratio: 0}
instruments:
  rb2705:
    multiplier: 10
    margin_ratio: {long: "0.20", short: "0.20"}
    commission:
      open: {per_lot: "1", ratio: "0"}
      close: {per_lot: "1", ratio: "0"}
      close_today: {per_lot: "1", ratio: "0"}
"""


@pytest.fixture
def rates():
    return parse_strategy_rates(RATES)


def trade(symbol, side, offset, quantity, price):
    return StrategyTrade(symbol, side, offset, D(str(quantity)), D(str(price)))


def test_rates_resolve_instrument_override_then_product_and_never_guess(rates):
    assert rates.currency == "CNY" and len(rates.digest) == 64
    rb = rates.for_instrument("rb2701")
    assert (rb.multiplier, rb.long_margin_ratio, rb.short_margin_ratio) == (D(10), D("0.10"), D("0.12"))
    assert rb.commission("close_today").ratio == D("0.0003")
    assert rb.commission("close").ratio == rb.commission("close_yesterday").ratio == D("0.0001")
    assert rates.for_instrument("rb2705").long_margin_ratio == D("0.20")       # the instrument's own entry
    assert rates.for_instrument("SA701").open.per_lot == D("3.5")
    for unknown in ("hc2701", "IF2612", "", "2701", None, 7):
        assert rates.for_instrument(unknown) is None
    assert rb.margin_ratio("short") == D("0.12")
    with pytest.raises(StrategyRatesError, match="RATES_OFFSET_INVALID"):
        rb.commission("net")
    with pytest.raises(StrategyRatesError, match="RATES_DIRECTION_INVALID"):
        rb.margin_ratio("net")
    assert parse_strategy_rates(RATES + b"\n").digest != rates.digest          # the digest names the bytes


@pytest.mark.parametrize("edit, code", [
    (lambda text: text.replace("schema_version: 1", "schema_version: 2"), "RATES_FILE_INVALID"),
    (lambda text: text.replace("currency: CNY", "currency: cny"), "RATES_CURRENCY_INVALID"),
    (lambda text: text.replace("multiplier: 20", "multiplier: 0"), "RATES_MULTIPLIER_INVALID"),
    (lambda text: text.replace("multiplier: 20", "multiplier: true"), "RATES_MULTIPLIER_INVALID"),
    (lambda text: text.replace('long: "0.10"', 'long: "0"'), "RATES_MARGIN_INVALID"),
    (lambda text: text.replace('long: "0.10"', 'long: "1.5"'), "RATES_MARGIN_INVALID"),
    (lambda text: text.replace('short: "0.12"}', 'short: "0.12", both: 1}'), "RATES_MARGIN_INVALID"),
    (lambda text: text.replace('ratio: "0.0003"', 'ratio: "-0.1"'), "RATES_COMMISSION_INVALID"),
    (lambda text: text.replace("per_lot: 7", "per_lot: nan"), "RATES_COMMISSION_INVALID"),
    (lambda text: text.replace("      close_today: {per_lot: 7, ratio: 0}\n", ""), "RATES_COMMISSION_INVALID"),
    (lambda text: text.replace("  SA:", "  S1:"), "RATES_PRODUCT_INVALID"),
    (lambda text: text.replace("  rb2705:", "  'rb 2705':"), "RATES_INSTRUMENT_INVALID"),
    (lambda text: text.replace("products:", "extra: 1\nproducts:"), "RATES_FILE_INVALID"),
    (lambda text: "products: [", "RATES_FILE_INVALID"),
    (lambda text: "", "RATES_FILE_INVALID"),
])
def test_invalid_rates_files_are_refused(edit, code):
    with pytest.raises(StrategyRatesError, match="^" + code + "$"):
        parse_strategy_rates(edit(RATES.decode()).encode())


def test_rates_file_is_read_from_disk_and_an_unreadable_one_is_an_error(tmp_path):
    path = tmp_path / "rates.yaml"
    path.write_bytes(RATES)
    assert load_strategy_rates(path).digest == parse_strategy_rates(RATES).digest
    for missing in (tmp_path / "absent.yaml", tmp_path):
        with pytest.raises(StrategyRatesError, match="^RATES_FILE_UNREADABLE$"):
            load_strategy_rates(missing)


def test_long_round_trip_with_a_remaining_lot_and_a_working_order(rates):
    result = compute_futures_linear(
        [trade("rb2701", "buy", "open", 2, 3000), trade("rb2701", "sell", "close", 1, 3100)],
        [WorkingOpenOrder("rb2701", "buy", D(1), D(2990))], rates, {"rb2701": D(3050)})
    assert result.realized_pnl == D("1000.00")                 # (3100 - 3000) x 1 x 10
    assert result.unrealized_pnl == D("500.00")                # (3050 - 3000) x 1 x 10
    assert result.fees == D("9.10")                            # 6.00 open + 3.10 close
    assert result.margin == D("3050.00")                       # 1 x 3050 x 10 x 0.10
    assert result.reservations == D("2992.99")                 # 2990 margin + 2.99 opening fee
    assert result.holdings == (StrategyHolding("rb2701", "long", D(1), D(3000)),)


def test_average_cost_short_side_and_both_sides_each_charged(rates):
    averaged = compute_futures_linear(
        [trade("rb2701", "buy", "open", 1, 3000), trade("rb2701", "buy", "open", 1, 3100),
         trade("rb2701", "sell", "close_today", 2, 3060)], [], rates, {})
    assert averaged.realized_pnl == D("200.00") and averaged.holdings == ()   # (3060 - 3050) x 2 x 10
    assert averaged.fees == D("24.46")                         # 3.00 + 3.10 + 18.36 at the close-today rate
    assert (averaged.unrealized_pnl, averaged.margin, averaged.reservations) == (D("0.00"),) * 3
    short = compute_futures_linear([trade("rb2701", "sell", "open", 1, 3000)], [], rates, {"rb2701": D(2950)})
    assert short.unrealized_pnl == D("500.00") and short.margin == D("3540.00")   # 2950 x 10 x 0.12
    both = compute_futures_linear(
        [trade("rb2701", "buy", "open", 1, 3000), trade("rb2701", "sell", "open", 1, 3010)],
        [], rates, {"rb2701": D(3000)})
    assert both.margin == D("6600.00")                         # 3000 + 3600: no netting between sides
    assert both.unrealized_pnl == D("100.00")
    closed_short = compute_futures_linear(
        [trade("rb2701", "sell", "open", 2, 3000), trade("rb2701", "buy", "close_yesterday", 2, 3020)],
        [], rates, {})
    assert closed_short.realized_pnl == D("-400.00")


def test_per_lot_commission_instrument_override_and_a_short_working_order(rates):
    result = compute_futures_linear(
        [trade("SA701", "buy", "open", 2, 970), trade("SA701", "sell", "close_today", 2, 969)],
        [WorkingOpenOrder("rb2705", "sell", D(3), D(3000))], rates, {})
    assert result.realized_pnl == D("-40.00") and result.fees == D("21.00")   # 2 x 3.5 + 2 x 7
    assert result.reservations == D("18003.00")                # 3 x 3000 x 10 x 0.20 + 3 x 1
    short_order = compute_futures_linear([], [WorkingOpenOrder("rb2701", "sell", D(1), D(3000))], rates, {})
    assert short_order.reservations == D("3603.00")            # the short ratio 0.12, plus 3.00 fee


@pytest.mark.parametrize("trades, working, prices, code", [
    ([("rb2701", "sell", "close", 1, 3000)], [], {}, "POSITION_UNDERFLOW"),
    ([("rb2701", "buy", "open", 1, 3000), ("rb2701", "sell", "close", 2, 3000)], [], {}, "POSITION_UNDERFLOW"),
    ([("rb2701", "buy", "open", 1, 3000), ("rb2701", "buy", "close", 1, 3000)], [], {}, "POSITION_UNDERFLOW"),
    ([("rb2701", "buy", "open", 1, 3000)], [], {}, "VALUATION_PRICE_UNAVAILABLE"),
    ([("rb2701", "buy", "open", 1, 3000)], [], {"rb2701": D(0)}, "VALUATION_PRICE_UNAVAILABLE"),
    ([("rb2701", "buy", "open", 1, 3000)], [], {"rb2701": 3000.0}, "VALUATION_PRICE_UNAVAILABLE"),
    ([("hc2701", "buy", "open", 1, 3000)], [], {"hc2701": D(3000)}, "RATES_UNAVAILABLE"),
    ([], [WorkingOpenOrder("hc2701", "buy", D(1), D(3000))], {}, "RATES_UNAVAILABLE"),
    ([("rb2701", "buy", "net", 1, 3000)], [], {}, "TRADE_INVALID"),
    ([("rb2701", "hold", "open", 1, 3000)], [], {}, "TRADE_INVALID"),
    ([("rb2701", "buy", "open", 0, 3000)], [], {}, "TRADE_INVALID"),
    ([("rb2701", "buy", "open", 1, -1)], [], {}, "TRADE_INVALID"),
    ([], [WorkingOpenOrder("rb2701", "hold", D(1), D(3000))], {}, "WORKING_ORDER_INVALID"),
    ([], [WorkingOpenOrder("rb2701", "buy", D(0), D(3000))], {}, "WORKING_ORDER_INVALID"),
    ([], [("rb2701", "buy", 1, 3000)], {}, "WORKING_ORDER_INVALID"),
])
def test_what_cannot_be_computed_is_unavailable_never_zero(rates, trades, working, prices, code):
    with pytest.raises(StrategyFinancialError) as raised:
        compute_futures_linear([trade(*item) for item in trades], working, rates, prices)
    assert raised.value.code == code


def test_a_flat_strategy_needs_no_price_and_amounts_round_to_the_cent(rates):
    flat = compute_futures_linear([], [], rates, {})
    assert (flat.realized_pnl, flat.unrealized_pnl, flat.fees, flat.margin, flat.reservations) == (D("0.00"),) * 5
    odd = compute_futures_linear(
        [trade("rb2701", "buy", "open", 3, "3000.5"), trade("rb2701", "buy", "open", 1, "3001")],
        [], rates, {"rb2701": D("3000.875")})
    assert odd.unrealized_pnl == D("10.00")                    # average 3000.625, +0.25 x 4 x 10
    assert odd.fees == D("12.00") and odd.margin == D("12003.50")


def test_a_running_book_gives_the_same_amounts_and_stays_failed_after_a_bad_trade(rates):
    book = FuturesLinearBook(rates)
    assert book.symbols == frozenset() and book.holdings() == ()
    history = [trade("rb2701", "buy", "open", 2, 3000), trade("SA701", "sell", "open", 1, 970),
               trade("rb2701", "sell", "close", 1, 3100)]
    working, prices = [WorkingOpenOrder("rb2701", "buy", D(1), D(2990))], {"rb2701": D(3050), "SA701": D(960)}
    for index, item in enumerate(history):
        book.apply(item)
        # After every trade the running book equals a replay of the trades so far.
        assert book.amounts(working, prices) == compute_futures_linear(history[:index + 1], working, rates, prices)
    assert book.symbols == {"rb2701", "SA701"}
    assert [(held.symbol, held.direction, held.quantity) for held in book.holdings()] == [
        ("SA701", "short", D(1)), ("rb2701", "long", D(1))]
    before = book.amounts(working, prices)
    with pytest.raises(StrategyFinancialError) as raised:
        book.apply(trade("rb2701", "sell", "close", 5, 3000))
    assert raised.value.code == "POSITION_UNDERFLOW"
    # The refused trade charged no fee and moved nothing, and the book is unusable from now on.
    for call in (lambda: book.apply(trade("rb2701", "buy", "open", 1, 3000)), book.holdings,
                 lambda: book.amounts(working, prices)):
        with pytest.raises(StrategyFinancialError) as again:
            call()
        assert again.value.code == "POSITION_UNDERFLOW"
    assert before.fees == D("12.60")                           # 6.00 + 3.50 + 3.10
    with pytest.raises(ValueError, match="INVALID_RATES"):
        FuturesLinearBook({})
