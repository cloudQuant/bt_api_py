"""Regression tests for additional market data container base classes."""

from __future__ import annotations

from bt_api_py.containers.fundingrates.funding_rate import FundingRateData
from bt_api_py.containers.incomes.income import IncomeData
from bt_api_py.containers.liquidations.liquidation import LiquidationData
from bt_api_py.containers.markprices.mark_price import MarkPriceData
from bt_api_py.containers.orderbooks.orderbook import OrderBookData


class _SampleFundingRate(FundingRateData):
    def init_data(self) -> _SampleFundingRate:
        self.exchange_name = "BINANCE"
        self.symbol_name = "BTCUSDT"
        self.current_funding_rate = 0.001
        return self

    def get_exchange_name(self) -> str:
        return "BINANCE"

    def get_server_time(self) -> float | None:
        return None

    def get_local_update_time(self) -> float | None:
        return None

    def get_asset_type(self) -> str | None:
        return "SWAP"

    def get_symbol_name(self) -> str | None:
        return "BTCUSDT"

    def get_pre_funding_rate(self) -> float | None:
        return None

    def get_pre_funding_time(self) -> float | None:
        return None

    def get_next_funding_rate(self) -> float | None:
        return None

    def get_next_funding_time(self) -> float | None:
        return None

    def get_max_funding_rate(self) -> float | None:
        return None

    def get_min_funding_rate(self) -> float | None:
        return None

    def get_current_funding_rate(self) -> float | None:
        return 0.001

    def get_current_funding_time(self) -> float | None:
        return None

    def get_settlement_funding_rate(self) -> float | None:
        return None

    def get_settlement_status(self) -> str | None:
        return None

    def get_method(self) -> str | None:
        return None

    def __str__(self) -> str:
        return "funding-rate"

    def __repr__(self) -> str:
        return "funding-rate"


class _SampleMarkPrice(MarkPriceData):
    def init_data(self) -> _SampleMarkPrice:
        self.exchange_name = "BINANCE"
        self.symbol_name = "BTCUSDT"
        self.mark_price = 1.0
        return self

    def get_exchange_name(self) -> str:
        return "BINANCE"

    def get_server_time(self) -> float | None:
        return None

    def get_local_update_time(self) -> float | None:
        return None

    def get_symbol_name(self) -> str | None:
        return "BTCUSDT"

    def get_mark_price(self) -> float | None:
        return 1.0

    def __str__(self) -> str:
        return "mark-price"

    def __repr__(self) -> str:
        return "mark-price"


class _SampleOrderBook(OrderBookData):
    def init_data(self) -> _SampleOrderBook:
        self.exchange_name = "BINANCE"
        self.symbol_name = "BTCUSDT"
        self.bid_price_list = [1.0]
        return self

    def get_exchange_name(self) -> str:
        return "BINANCE"

    def get_local_update_time(self) -> float | None:
        return None

    def get_symbol_name(self) -> str | None:
        return "BTCUSDT"

    def get_asset_type(self) -> str | None:
        return "SPOT"

    def get_server_time(self) -> float | None:
        return None

    def get_bid_price_list(self) -> list[float] | None:
        return [1.0]

    def get_ask_price_list(self) -> list[float] | None:
        return [1.1]

    def get_bid_volume_list(self) -> list[float] | None:
        return [2.0]

    def get_ask_volume_list(self) -> list[float] | None:
        return [3.0]

    def get_bid_trade_nums(self) -> list[int] | None:
        return None

    def get_ask_trade_nums(self) -> list[int] | None:
        return None

    def __str__(self) -> str:
        return "orderbook"

    def __repr__(self) -> str:
        return "orderbook"


class _SampleLiquidation(LiquidationData):
    def init_data(self) -> _SampleLiquidation:
        self.exchange_name = "OKX"
        self.symbol_name = "BTC-USDT-SWAP"
        return self

    def get_exchange_name(self) -> str:
        return "OKX"

    def get_asset_type(self) -> str | None:
        return "SWAP"

    def get_symbol_name(self) -> str | None:
        return "BTC-USDT-SWAP"

    def get_server_time(self) -> float | None:
        return None

    def get_local_update_time(self) -> float | None:
        return None

    def __str__(self) -> str:
        return "liquidation"


class _SampleIncome(IncomeData):
    def init_data(self) -> _SampleIncome:
        self.exchange_name = "BINANCE"
        self.symbol_name = "BTCUSDT"
        self.income_value = 1.0
        return self

    def get_exchange_name(self) -> str:
        return "BINANCE"

    def get_server_time(self) -> float | None:
        return None

    def get_local_update_time(self) -> float | None:
        return None

    def get_symbol_name(self) -> str | None:
        return "BTCUSDT"

    def get_income_type(self) -> str | None:
        return "FUNDING_FEE"

    def get_income_value(self) -> float | None:
        return 1.0

    def get_income_asset(self) -> str | None:
        return "USDT"

    def __str__(self) -> str:
        return "income"

    def __repr__(self) -> str:
        return "income"


def test_funding_rate_data_base_event_and_payload() -> None:
    funding_rate = _SampleFundingRate({"symbol": "BTCUSDT"}, True)
    funding_rate.init_data()
    assert funding_rate.get_event() == "FundingEvent"
    assert funding_rate.get_event_type() == "FundingEvent"
    assert funding_rate.get_all_data()["current_funding_rate"] == 0.001


def test_mark_price_data_base_event_and_payload() -> None:
    mark_price = _SampleMarkPrice({"symbol": "BTCUSDT"}, True)
    mark_price.init_data()
    assert mark_price.get_event() == "MarkPriceEvent"
    assert mark_price.get_all_data()["mark_price"] == 1.0


def test_orderbook_data_base_event_and_payload() -> None:
    orderbook = _SampleOrderBook({"symbol": "BTCUSDT"}, True)
    orderbook.init_data()
    assert orderbook.get_event() == "OrderBookEvent"
    assert orderbook.get_all_data()["bid_price_list"] == [1.0]


def test_liquidation_data_base_event_and_payload() -> None:
    liquidation = _SampleLiquidation({"instId": "BTC-USDT-SWAP"}, True)
    liquidation.init_data()
    assert liquidation.get_event() == "LiquidationWarningEvent"
    assert liquidation.get_all_data()["symbol_name"] == "BTC-USDT-SWAP"


def test_income_data_base_event_and_payload() -> None:
    income = _SampleIncome({"symbol": "BTCUSDT"}, True)
    income.init_data()
    assert income.get_event() == "IncomeEvent"
    assert income.get_event_type() == "IncomeEvent"
    assert income.get_all_data()["income_value"] == 1.0
