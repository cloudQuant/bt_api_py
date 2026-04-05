"""Regression tests for container base classes."""

from __future__ import annotations

from bt_api_py.containers.accounts.account import AccountData
from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.containers.orders.order import OrderData, OrderStatus
from bt_api_py.containers.positions.position import PositionData
from bt_api_py.containers.symbols.symbol import SymbolData
from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.trades.trade import TradeData


class _SampleTrade(TradeData):
    def init_data(self) -> _SampleTrade:
        self.exchange_name = "BINANCE"
        self.symbol_name = "BTCUSDT"
        self.trade_price = 1.0
        return self

    def get_exchange_name(self) -> str:
        return "BINANCE"

    def get_asset_type(self) -> str:
        return "SPOT"

    def get_symbol_name(self) -> str:
        return "BTCUSDT"

    def get_server_time(self) -> float | None:
        return None

    def get_local_update_time(self) -> float | None:
        return None

    def get_trade_id(self) -> str | None:
        return None

    def get_trade_symbol_name(self) -> str | None:
        return None

    def get_order_id(self) -> str | None:
        return None

    def get_client_order_id(self) -> str | None:
        return None

    def get_trade_side(self) -> str | None:
        return None

    def get_trade_offset(self) -> str | None:
        return None

    def get_trade_price(self) -> float | None:
        return self.trade_price

    def get_trade_volume(self) -> float | None:
        return None

    def get_trade_accumulate_volume(self) -> float | None:
        return None

    def get_trade_type(self) -> str | None:
        return None

    def get_trade_time(self) -> float | None:
        return None

    def get_trade_fee(self) -> float | None:
        return None

    def get_trade_fee_symbol(self) -> str | None:
        return None

    def __str__(self) -> str:
        return "trade"

    def __repr__(self) -> str:
        return "trade"


class _SampleTicker(TickerData):
    def init_data(self) -> _SampleTicker:
        return self

    def get_all_data(self) -> dict[str, object]:
        return {"symbol": "BTCUSDT"}

    def get_exchange_name(self) -> str:
        return "BINANCE"

    def get_local_update_time(self) -> float:
        return 0.0

    def get_symbol_name(self) -> str:
        return "BTCUSDT"

    def get_ticker_symbol_name(self) -> str | None:
        return "BTCUSDT"

    def get_asset_type(self) -> str:
        return "SPOT"

    def get_server_time(self) -> float | None:
        return None

    def get_bid_price(self) -> float | None:
        return None

    def get_ask_price(self) -> float | None:
        return None

    def get_bid_volume(self) -> float | None:
        return None

    def get_ask_volume(self) -> float | None:
        return None

    def get_last_price(self) -> float | None:
        return None

    def get_last_volume(self) -> float | None:
        return None

    def __str__(self) -> str:
        return "ticker"

    def __repr__(self) -> str:
        return "ticker"


class _SampleSymbol(SymbolData):
    def init_data(self) -> _SampleSymbol:
        return self

    def get_all_data(self) -> dict[str, object]:
        return {"symbol": "BTCUSDT"}

    def get_exchange_name(self) -> str:
        return "BINANCE"

    def get_server_time(self) -> float | None:
        return None

    def get_local_update_time(self) -> float | None:
        return None

    def get_symbol_name(self) -> str:
        return "BTCUSDT"

    def get_asset_type(self) -> str:
        return "SPOT"

    def get_maintain_margin_percent(self) -> float | None:
        return None

    def get_required_margin_percent(self) -> float | None:
        return None

    def get_base_asset(self) -> str | None:
        return "BTC"

    def get_quote_asset(self) -> str | None:
        return "USDT"

    def get_contract_multiplier(self) -> float | int | None:
        return None

    def get_price_unit(self) -> float | int | None:
        return None

    def get_price_digital(self) -> int | None:
        return None

    def get_max_price(self) -> float | int | None:
        return None

    def get_min_price(self) -> float | int | None:
        return None

    def get_qty_unit(self) -> float | int | None:
        return None

    def get_qty_digital(self) -> int | None:
        return None

    def get_min_qty(self) -> float | int | None:
        return None

    def get_max_qty(self) -> float | int | None:
        return None

    def get_base_asset_digital(self) -> int | None:
        return None

    def get_quote_asset_digital(self) -> int | None:
        return None

    def get_order_types(self) -> list[str]:
        return []

    def get_time_in_force(self) -> list[str]:
        return []

    def get_fee_digital(self) -> int | None:
        return None

    def get_fee_currency(self) -> str | None:
        return None

    def __str__(self) -> str:
        return "symbol"

    def __repr__(self) -> str:
        return "symbol"


class _SampleBalance(BalanceData):
    def init_data(self) -> _SampleBalance:
        self.exchange_name = "BINANCE"
        self.asset_type = "SPOT"
        self.local_update_time = 0.0
        self.account_type = "SPOT"
        self.available_margin = 10.0
        return self

    def get_all_data(self) -> dict[str, object]:
        return {"exchange_name": "BINANCE", "available_margin": 10.0}

    def get_exchange_name(self) -> str:
        return "BINANCE"

    def get_asset_type(self) -> str:
        return "SPOT"

    def get_server_time(self) -> float | None:
        return None

    def get_local_update_time(self) -> float:
        return 0.0

    def get_account_id(self) -> str | None:
        return None

    def get_account_type(self) -> str | None:
        return "SPOT"

    def get_fee_tier(self) -> int | str | None:
        return None

    def get_max_withdraw_amount(self) -> float | None:
        return None

    def get_margin(self) -> float | None:
        return 10.0

    def get_used_margin(self) -> float | None:
        return 0.0

    def get_maintain_margin(self) -> float | None:
        return None

    def get_available_margin(self) -> float | None:
        return 10.0

    def get_open_order_initial_margin(self) -> float | None:
        return None

    def get_open_order_maintenance_margin(self) -> float | None:
        return None

    def get_unrealized_profit(self) -> float | None:
        return None

    def get_interest(self) -> float | None:
        return None

    def __str__(self) -> str:
        return "balance"

    def __repr__(self) -> str:
        return "balance"


class _SampleAccount(AccountData):
    def init_data(self) -> _SampleAccount:
        self.exchange_name = "BINANCE"
        self.asset_type = "SPOT"
        self.account_type = "SPOT"
        self.balances = [{"asset": "USDT", "free": 1.0}]
        return self

    def get_exchange_name(self) -> str:
        return "BINANCE"

    def get_asset_type(self) -> str:
        return "SPOT"

    def get_server_time(self) -> int | float | None:
        return None

    def get_local_update_time(self) -> int | float | None:
        return None

    def get_account_id(self) -> str | None:
        return None

    def get_account_type(self) -> str | None:
        return "SPOT"

    def get_can_deposit(self) -> bool | None:
        return True

    def get_can_trade(self) -> bool | None:
        return True

    def get_can_withdraw(self) -> bool | None:
        return True

    def get_fee_tier(self) -> int | str | None:
        return 0

    def get_max_withdraw_amount(self) -> float | None:
        return None

    def get_total_margin(self) -> float | None:
        return None

    def get_total_used_margin(self) -> float | None:
        return None

    def get_total_maintain_margin(self) -> float | None:
        return None

    def get_total_available_margin(self) -> float | None:
        return None

    def get_total_open_order_initial_margin(self) -> float | None:
        return None

    def get_total_position_initial_margin(self) -> float | None:
        return None

    def get_total_unrealized_profit(self) -> float | None:
        return None

    def get_total_wallet_balance(self) -> float | None:
        return None

    def get_balances(self) -> list[dict[str, object]]:
        return [{"asset": "USDT", "free": 1.0}]

    def get_positions(self) -> list[dict[str, object]]:
        return []

    def get_spot_maker_commission_rate(self) -> float | None:
        return None

    def get_spot_taker_commission_rate(self) -> float | None:
        return None

    def get_future_maker_commission_rate(self) -> float | None:
        return None

    def get_future_taker_commission_rate(self) -> float | None:
        return None

    def get_option_maker_commission_rate(self) -> float | None:
        return None

    def get_option_taker_commission_rate(self) -> float | None:
        return None

    def __str__(self) -> str:
        return "account"

    def __repr__(self) -> str:
        return "account"


class _SampleOrder(OrderData):
    def init_data(self) -> _SampleOrder:
        self.exchange_name = "BINANCE"
        self.symbol_name = "BTCUSDT"
        self.asset_type = "SPOT"
        self.order_id = "ord-1"
        self.order_status = OrderStatus.ACCEPTED
        return self

    def get_exchange_name(self) -> str:
        return "BINANCE"

    def get_asset_type(self) -> str:
        return "SPOT"

    def get_symbol_name(self) -> str:
        return "BTCUSDT"

    def get_server_time(self) -> float | None:
        return None

    def get_local_update_time(self) -> float | None:
        return None

    def get_trade_id(self) -> str | None:
        return None

    def get_client_order_id(self) -> str | None:
        return None

    def get_cum_quote(self) -> float | None:
        return None

    def get_executed_qty(self) -> float | None:
        return None

    def get_order_id(self) -> str | None:
        return "ord-1"

    def get_order_size(self) -> float | None:
        return None

    def get_order_price(self) -> float | None:
        return None

    def get_reduce_only(self) -> bool | None:
        return None

    def get_order_side(self) -> str | None:
        return None

    def get_order_status(self) -> OrderStatus | str | None:
        return OrderStatus.ACCEPTED

    def get_order_symbol_name(self) -> str | None:
        return "BTCUSDT"

    def get_order_time_in_force(self) -> str | None:
        return None

    def get_order_type(self) -> str | None:
        return None

    def get_order_avg_price(self) -> float | None:
        return None

    def get_origin_order_type(self) -> str | None:
        return None

    def get_position_side(self) -> str | None:
        return None

    def get_trailing_stop_price(self) -> float | None:
        return None

    def get_trailing_stop_trigger_price(self) -> float | None:
        return None

    def get_trailing_stop_callback_rate(self) -> float | None:
        return None

    def get_trailing_stop_trigger_price_type(self) -> str | None:
        return None

    def get_stop_loss_price(self) -> float | None:
        return None

    def get_stop_loss_trigger_price(self) -> float | None:
        return None

    def get_stop_loss_trigger_price_type(self) -> str | None:
        return None

    def get_take_profit_price(self) -> float | None:
        return None

    def get_take_profit_trigger_price(self) -> float | None:
        return None

    def get_take_profit_trigger_price_type(self) -> str | None:
        return None

    def get_close_position(self) -> bool | None:
        return None

    def __str__(self) -> str:
        return "order"

    def __repr__(self) -> str:
        return "order"


class _SamplePosition(PositionData):
    def init_data(self) -> _SamplePosition:
        self.exchange_name = "BINANCE"
        self.symbol_name = "BTCUSDT"
        self.asset_type = "SWAP"
        self.position_symbol_name = "BTCUSDT"
        self.position_volume = 1.0
        return self

    def get_exchange_name(self) -> str:
        return "BINANCE"

    def get_asset_type(self) -> str:
        return "SWAP"

    def get_server_time(self) -> float | None:
        return None

    def get_local_update_time(self) -> float | None:
        return None

    def get_account_id(self) -> str | None:
        return None

    def get_position_id(self) -> str | None:
        return None

    def get_is_isolated(self) -> bool | None:
        return None

    def get_margin_type(self) -> str | None:
        return None

    def get_is_auto_add_margin(self) -> bool | None:
        return None

    def get_leverage(self) -> float | None:
        return None

    def get_max_notional_value(self) -> float | None:
        return None

    def get_position_symbol_name(self) -> str | None:
        return "BTCUSDT"

    def get_position_volume(self) -> float | None:
        return 1.0

    def get_position_side(self) -> str | None:
        return None

    def get_trade_num(self) -> float | None:
        return None

    def get_avg_price(self) -> float | None:
        return None

    def get_mark_price(self) -> float | None:
        return None

    def get_liquidation_price(self) -> float | None:
        return None

    def get_initial_margin(self) -> float | None:
        return None

    def get_maintain_margin(self) -> float | None:
        return None

    def open_order_initial_margin(self) -> float | None:
        return None

    def get_position_initial_margin(self) -> float | None:
        return None

    def get_position_fee(self) -> float | None:
        return None

    def get_position_realized_pnl(self) -> float | None:
        return None

    def get_position_unrealized_pnl(self) -> float | None:
        return None

    def get_position_funding_value(self) -> float | None:
        return None

    def __str__(self) -> str:
        return "position"

    def __repr__(self) -> str:
        return "position"


def test_trade_data_base_event_and_all_data() -> None:
    trade = _SampleTrade({"id": 1}, True)
    assert trade.get_event() == "TradeEvent"
    payload = trade.get_all_data()
    assert payload["exchange_name"] == "BINANCE"
    assert payload["symbol_name"] == "BTCUSDT"


def test_ticker_data_base_event() -> None:
    ticker = _SampleTicker({"last": "1"}, True)
    assert ticker.get_event() == "TickerEvent"
    assert ticker.get_all_data()["symbol"] == "BTCUSDT"


def test_symbol_data_base_event() -> None:
    symbol = _SampleSymbol({"instId": "BTC-USDT"}, True)
    assert symbol.get_event() == "SymbolEvent"
    assert symbol.get_all_data()["symbol"] == "BTCUSDT"


def test_balance_data_base_event() -> None:
    balance = _SampleBalance({"asset": "USDT"}, True)
    assert balance.get_event() == "BalanceEvent"
    assert balance.get_all_data()["exchange_name"] == "BINANCE"


def test_account_data_base_event() -> None:
    account = _SampleAccount({"account": "spot"}, True)
    account.init_data()
    assert account.get_event() == "AccountEvent"
    assert account.get_all_data()["account_type"] == "SPOT"
    assert account.get_all_data()["balances"] == [{"asset": "USDT", "free": 1.0}]


def test_order_data_base_event_and_all_data() -> None:
    order = _SampleOrder({"id": 1}, True)
    order.init_data()
    assert order.get_event() == "OrderEvent"
    assert order.get_all_data()["order_id"] == "ord-1"
    assert order.get_all_data()["order_status"] == "new"


def test_position_data_base_event_and_all_data() -> None:
    position = _SamplePosition({"id": 1}, True)
    position.init_data()
    assert position.get_event() == "PositionEvent"
    assert position.get_all_data()["symbol_name"] == "BTCUSDT"
    assert position.get_all_data()["position_volume"] == 1.0
