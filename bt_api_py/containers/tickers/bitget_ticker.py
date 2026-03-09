"""Bitget Ticker Data Container."""

from typing import Any, Self
import json
import time

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class BitgetTickerData(TickerData):
    """保存Bitget ticker信息."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False) -> None:
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "BITGET"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.server_time = None
        self.last_price = None
        self.last_volume = None
        self.bid_price = None
        self.ask_price = None
        self.bid_volume = None
        self.ask_volume = None
        self.price_24h_high = None
        self.price_24h_low = None
        self.volume_24h = None
        self.turnover_24h = None
        self.count_24h = None
        self.has_been_init_data = False

    def init_data(self) -> "Self":
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.ticker_symbol_name = from_dict_get_string(self.ticker_data, "symbol")
        self.server_time = from_dict_get_float(self.ticker_data, "ts") or from_dict_get_float(
            self.ticker_data, "time"
        )
        self.last_price = from_dict_get_float(self.ticker_data, "last")
        self.last_volume = from_dict_get_float(self.ticker_data, "volume")
        self.bid_price = from_dict_get_float(self.ticker_data, "bidPx")
        self.ask_price = from_dict_get_float(self.ticker_data, "askPx")
        self.bid_volume = from_dict_get_float(self.ticker_data, "bidSz")
        self.ask_volume = from_dict_get_float(self.ticker_data, "askSz")
        self.price_24h_high = from_dict_get_float(self.ticker_data, "high24h")
        self.price_24h_low = from_dict_get_float(self.ticker_data, "low24h")
        self.volume_24h = from_dict_get_float(self.ticker_data, "volume24h")
        self.turnover_24h = from_dict_get_float(self.ticker_data, "turnover24h")
        self.count_24h = from_dict_get_float(self.ticker_data, "count24h")
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
                "last_price": self.last_price,
                "last_volume": self.last_volume,
                "bid_price": self.bid_price,
                "ask_price": self.ask_price,
                "bid_volume": self.bid_volume,
                "ask_volume": self.ask_volume,
                "price_24h_high": self.price_24h_high,
                "price_24h_low": self.price_24h_low,
                "volume_24h": self.volume_24h,
                "turnover_24h": self.turnover_24h,
                "count_24h": self.count_24h,
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

    def get_last_price(self) -> float | None:
        return self.last_price

    def get_last_volume(self) -> float | None:
        return self.last_volume

    def get_bid_price(self) -> float | None:
        return self.bid_price

    def get_ask_price(self) -> float | None:
        return self.ask_price

    def get_bid_volume(self) -> float | None:
        return self.bid_volume

    def get_ask_volume(self) -> float | None:
        return self.ask_volume

    def get_price_24h_high(self) -> float | None:
        return self.price_24h_high

    def get_price_24h_low(self) -> float | None:
        return self.price_24h_low

    def get_volume_24h(self) -> float | None:
        return self.volume_24h

    def get_turnover_24h(self) -> float | None:
        return self.turnover_24h

    def get_count_24h(self) -> float | None:
        return self.count_24h


class BitgetWssTickerData(BitgetTickerData):
    """Bitget WebSocket Ticker Data."""

    def init_data(self) -> "Self":
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.ticker_symbol_name = from_dict_get_string(self.ticker_data, "s")
        self.server_time = from_dict_get_float(self.ticker_data, "E")
        self.last_price = from_dict_get_float(self.ticker_data, "c")
        self.last_volume = from_dict_get_float(self.ticker_data, "v")
        self.bid_price = from_dict_get_float(self.ticker_data, "b")
        self.ask_price = from_dict_get_float(self.ticker_data, "a")
        self.bid_volume = from_dict_get_float(self.ticker_data, "B")
        self.ask_volume = from_dict_get_float(self.ticker_data, "A")
        self.price_24h_high = from_dict_get_float(self.ticker_data, "h")
        self.price_24h_low = from_dict_get_float(self.ticker_data, "l")
        self.volume_24h = from_dict_get_float(self.ticker_data, "q")
        self.turnover_24h = from_dict_get_float(self.ticker_data, "Q")
        self.count_24h = from_dict_get_float(self.ticker_data, "n")
        self.has_been_init_data = True
        return self


class BitgetRequestTickerData(BitgetTickerData):
    """Bitget REST API Ticker Data."""

    def init_data(self) -> "Self":
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.ticker_symbol_name = from_dict_get_string(self.ticker_data, "symbol")
        self.server_time = from_dict_get_float(self.ticker_data, "ts") or from_dict_get_float(
            self.ticker_data, "time"
        )
        self.last_price = from_dict_get_float(self.ticker_data, "last")
        self.last_volume = from_dict_get_float(self.ticker_data, "volume")
        self.bid_price = from_dict_get_float(self.ticker_data, "bidPx")
        self.ask_price = from_dict_get_float(self.ticker_data, "askPx")
        self.bid_volume = from_dict_get_float(self.ticker_data, "bidSz")
        self.ask_volume = from_dict_get_float(self.ticker_data, "askSz")
        self.price_24h_high = from_dict_get_float(self.ticker_data, "high24h")
        self.price_24h_low = from_dict_get_float(self.ticker_data, "low24h")
        self.volume_24h = from_dict_get_float(self.ticker_data, "volume24h")
        self.turnover_24h = from_dict_get_float(self.ticker_data, "usdVolume24h")
        self.count_24h = from_dict_get_float(self.ticker_data, "count24h")
        self.has_been_init_data = True
        return self
