import json
import time
from typing import Any, Self

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float


class LocalBitcoinsTickerData(TickerData):
    """保存 LocalBitcoins ticker 信息.

    Note: LocalBitcoins is a P2P exchange, ticker data represents
    aggregated P2P advertisements, not order book data.
    """

    def __init__(
        self, ticker_info, symbol_name, asset_type, has_been_json_encoded: bool = False
    ) -> None:
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "LOCALBITCOINS"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.server_time = None
        self.bid_price = None
        self.ask_price = None
        self.bid_volume = None
        self.ask_volume = None
        self.last_price = None
        self.daily_change = None
        self.daily_change_percentage = None
        self.volume = None
        self.high = None
        self.low = None
        self.has_been_init_data = False

    def init_data(self) -> "Self":
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # LocalBitcoins ticker format (dict):
        # {
        #   "btc_usd": {
        #     "avg": 45000.50,
        #     "bid": 44900.00,
        #     "ask": 45100.00,
        #     "volume_btc": 1234.56
        #   }
        # }
        if isinstance(self.ticker_data, dict):
            # Extract the data for our symbol if nested
            data = self.ticker_data
            if self.symbol_name:
                key = self.symbol_name.lower().replace("-", "_")
                if key in data:
                    data = data[key]

            self.ticker_symbol_name = self.symbol_name
            self.last_price = from_dict_get_float(data, "avg", 0.0)  # Average price
            self.bid_price = from_dict_get_float(data, "bid", 0.0)
            self.ask_price = from_dict_get_float(data, "ask", 0.0)
            self.volume = from_dict_get_float(data, "volume_btc", 0.0)

            # High/low not typically available in P2P
            self.high = None
            self.low = None

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
        return self.all_data

    def __str__(self) -> str:
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        return self.__str__()

    def get_exchange_name(self) -> str:
        return self.exchange_name

    def get_local_update_time(self) -> float:
        return self.local_update_time

    def get_symbol_name(self) -> str:
        return self.symbol_name

    def get_ticker_symbol_name(self) -> str | None:
        return self.ticker_symbol_name

    def get_asset_type(self) -> str:
        return self.asset_type

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


class LocalBitcoinsWssTickerData(LocalBitcoinsTickerData):
    """保存 LocalBitcoins WebSocket ticker 信息.

    Note: LocalBitcoins does not provide WebSocket API.
    This is a placeholder for future compatibility.
    """

    def init_data(self) -> "Self":
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        if isinstance(self.ticker_data, dict):
            data = self.ticker_data
            # Extract the data for our symbol if nested
            if self.symbol_name:
                key = self.symbol_name.lower().replace("-", "_")
                if key in data:
                    data = data[key]

            self.ticker_symbol_name = self.symbol_name
            self.last_price = from_dict_get_float(data, "avg", 0.0)
            self.bid_price = from_dict_get_float(data, "bid", 0.0)
            self.ask_price = from_dict_get_float(data, "ask", 0.0)
            self.volume = from_dict_get_float(data, "volume_btc", 0.0)

            self.server_time = time.time()

        self.has_been_init_data = True
        return self


class LocalBitcoinsRequestTickerData(LocalBitcoinsTickerData):
    """保存 LocalBitcoins REST API ticker 信息."""

    def init_data(self) -> "Self":
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        if isinstance(self.ticker_data, dict):
            data = self.ticker_data
            # Extract the data for our symbol if nested
            if self.symbol_name:
                key = self.symbol_name.lower().replace("-", "_")
                if key in data:
                    data = data[key]

            self.ticker_symbol_name = self.symbol_name
            self.last_price = from_dict_get_float(data, "avg", 0.0)
            self.bid_price = from_dict_get_float(data, "bid", 0.0)
            self.ask_price = from_dict_get_float(data, "ask", 0.0)
            self.volume = from_dict_get_float(data, "volume_btc", 0.0)

            self.server_time = time.time()

        self.has_been_init_data = True
        return self
