import json
import time
from typing import Any, Self

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class LatokenTickerData(TickerData):
    """保存 Latoken ticker 信息."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False) -> None:
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "LATOKEN"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name: str | None = None
        self.server_time: float | None = None
        self.bid_price: float | None = None
        self.ask_price: float | None = None
        self.bid_volume: float | None = None
        self.ask_volume: float | None = None
        self.last_price: float | None = None
        self.daily_change: float | None = None
        self.daily_change_percentage: float | None = None
        self.volume: float | None = None
        self.high: float | None = None
        self.low: float | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "Self":
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Latoken ticker format (dict):
        # {
        #   "symbol": "BTCUSDT",
        #   "lastPrice": "45000.50",
        #   "bidPrice": "45000.00",
        #   "askPrice": "45000.50",
        #   "high": "46000.00",
        #   "low": "44000.00",
        #   "volume": "1234.56",
        #   "timestamp": 1678901234000
        # }
        if isinstance(self.ticker_data, dict):
            data = self.ticker_data
            self.ticker_symbol_name = from_dict_get_string(data, "symbol", self.symbol_name)
            self.last_price = from_dict_get_float(data, "lastPrice", 0.0)
            self.bid_price = from_dict_get_float(data, "bidPrice", 0.0)
            self.ask_price = from_dict_get_float(data, "askPrice", 0.0)
            self.high = from_dict_get_float(data, "high", 0.0)
            self.low = from_dict_get_float(data, "low", 0.0)
            self.volume = from_dict_get_float(data, "volume", 0.0)

            # Calculate daily change if not provided
            if self.high and self.low:
                self.daily_change = self.last_price - self.low if self.last_price else None

            # Timestamp (milliseconds)
            timestamp = data.get("timestamp")
            if timestamp:
                self.server_time = float(timestamp) / 1000
            else:
                self.server_time = time.time()

        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "ticker_symbol_name": self.ticker_symbol_name,
                "server_time": self.server_time,
                "bid_price": self.bid_price,
                "ask_price": self.ask_price,
                "bid_volume": self.bid_volume,
                "ask_volume": self.ask_volume,
                "last_price": self.last_price,
                "daily_change": self.daily_change,
                "daily_change_percentage": self.daily_change_percentage,
                "volume": self.volume,
                "high": self.high,
                "low": self.low,
            }
        return self.all_data or {}

    def __str__(self) -> str:
        self.init_data()
        return str(json.dumps(self.get_all_data()))

    def __repr__(self) -> str:
        return str(self.__str__())

    def get_exchange_name(self) -> str:
        return str(self.exchange_name)

    def get_local_update_time(self) -> float:
        return float(self.local_update_time)

    def get_symbol_name(self) -> str:
        return str(self.symbol_name)

    def get_ticker_symbol_name(self) -> str | None:
        val = self.ticker_symbol_name
        return None if val is None else str(val)

    def get_asset_type(self) -> str:
        return str(self.asset_type)

    def get_server_time(self) -> float | None:
        return self.server_time

    def get_bid_price(self) -> float | None:
        return self.bid_price

    def get_ask_price(self) -> float | None:
        return self.ask_price

    def get_bid_volume(self) -> float | None:
        return self.bid_volume

    def get_ask_volume(self) -> float | None:
        return self.ask_volume

    def get_last_price(self) -> float | None:
        return self.last_price

    def get_daily_change(self) -> float | None:
        return self.daily_change

    def get_daily_change_percentage(self) -> float | None:
        return self.daily_change_percentage

    def get_volume(self) -> float | None:
        return self.volume

    def get_high(self) -> float | None:
        return self.high

    def get_low(self) -> float | None:
        return self.low


class LatokenWssTickerData(LatokenTickerData):
    """保存 Latoken WebSocket ticker 信息."""

    def init_data(self) -> "Self":
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # WebSocket ticker format (similar to REST)
        if isinstance(self.ticker_data, dict):
            data = self.ticker_data
            self.ticker_symbol_name = from_dict_get_string(data, "symbol", self.symbol_name)
            self.last_price = from_dict_get_float(data, "lastPrice", 0.0)
            self.bid_price = from_dict_get_float(data, "bidPrice", 0.0)
            self.ask_price = from_dict_get_float(data, "askPrice", 0.0)
            self.high = from_dict_get_float(data, "high", 0.0)
            self.low = from_dict_get_float(data, "low", 0.0)
            self.volume = from_dict_get_float(data, "volume", 0.0)

            timestamp = data.get("timestamp")
            if timestamp:
                self.server_time = float(timestamp) / 1000
            else:
                self.server_time = time.time()

        self.has_been_init_data = True
        return self


class LatokenRequestTickerData(LatokenTickerData):
    """保存 Latoken REST API ticker 信息."""

    def init_data(self) -> "Self":
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # REST API ticker format
        if isinstance(self.ticker_data, dict):
            data = self.ticker_data
            self.ticker_symbol_name = from_dict_get_string(data, "symbol", self.symbol_name)
            self.last_price = from_dict_get_float(data, "lastPrice", 0.0)
            self.bid_price = from_dict_get_float(data, "bidPrice", 0.0)
            self.ask_price = from_dict_get_float(data, "askPrice", 0.0)
            self.high = from_dict_get_float(data, "high", 0.0)
            self.low = from_dict_get_float(data, "low", 0.0)
            self.volume = from_dict_get_float(data, "volume", 0.0)

            timestamp = data.get("timestamp")
            if timestamp:
                self.server_time = float(timestamp) / 1000
            else:
                self.server_time = time.time()

        self.has_been_init_data = True
        return self
