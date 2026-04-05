from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py._compat import Self
from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class BinanceTickerData(TickerData):
    """Binance ticker data container (supports REST API and WebSocket)."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False) -> None:
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"  # 交易所名称
        self.local_update_time = time.time()  # 本地时间戳
        self.symbol_name = symbol_name
        self.asset_type = asset_type  # ticker的类型
        self.ticker_data: dict[str, Any] | None = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name: str | None = None
        self.server_time: float | None = None
        self.bid_price: float | None = None
        self.ask_price: float | None = None
        self.bid_volume: float | None = None
        self.ask_volume: float | None = None
        self.last_price: float | None = None
        self.last_volume: float | None = None
        self.open_price: float | None = None
        self.high_price: float | None = None
        self.low_price: float | None = None
        self.prev_close: float | None = None
        self.volume_24h: float | None = None
        self.turnover_24h: float | None = None
        self.price_change: float | None = None
        self.price_change_pct: float | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> Self:
        raise NotImplementedError

    def get_all_data(self) -> dict[str, Any]:
        if self.all_data is None:
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
                "last_volume": self.last_volume,
                "open_price": self.open_price,
                "high_price": self.high_price,
                "low_price": self.low_price,
                "prev_close": self.prev_close,
                "volume_24h": self.volume_24h,
                "turnover_24h": self.turnover_24h,
                "price_change": self.price_change,
                "price_change_pct": self.price_change_pct,
            }
        return self.all_data or {}

    def __str__(self) -> str:
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        return self.__str__()

    def get_exchange_name(self) -> str:
        return str(self.exchange_name)

    def get_local_update_time(self) -> float:
        return float(self.local_update_time)

    def get_symbol_name(self) -> str:
        return str(self.symbol_name)

    def get_ticker_symbol_name(self) -> str | None:
        return self.ticker_symbol_name

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

    def get_last_volume(self) -> float | None:
        return self.last_volume

    def get_open_price(self) -> float | None:
        return self.open_price

    def get_high_price(self) -> float | None:
        return self.high_price

    def get_low_price(self) -> float | None:
        return self.low_price

    def get_prev_close(self) -> float | None:
        return self.prev_close

    def get_volume_24h(self) -> float | None:
        return self.volume_24h

    def get_turnover_24h(self) -> float | None:
        return self.turnover_24h

    def get_price_change(self) -> float | None:
        return self.price_change

    def get_price_change_pct(self) -> float | None:
        return self.price_change_pct


class BinanceWssTickerData(BinanceTickerData):
    """Binance WebSocket ticker data container."""

    def init_data(self) -> Self:
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        data = self.ticker_data or {}
        self.ticker_symbol_name = from_dict_get_string(data, "s")
        self.server_time = from_dict_get_float(data, "E")
        self.bid_price = from_dict_get_float(data, "b")
        self.ask_price = from_dict_get_float(data, "a")
        self.bid_volume = from_dict_get_float(data, "B")
        self.ask_volume = from_dict_get_float(data, "A")
        self.last_price = from_dict_get_float(data, "c")
        self.last_volume = from_dict_get_float(data, "Q")
        self.open_price = from_dict_get_float(data, "o")
        self.high_price = from_dict_get_float(data, "h")
        self.low_price = from_dict_get_float(data, "l")
        self.prev_close = from_dict_get_float(data, "x")
        self.volume_24h = from_dict_get_float(data, "v")
        self.turnover_24h = from_dict_get_float(data, "q")
        self.price_change = from_dict_get_float(data, "p")
        self.price_change_pct = from_dict_get_float(data, "P")
        self.has_been_init_data = True
        return self


class BinanceRequestTickerData(BinanceTickerData):
    """Binance REST API ticker data container."""

    def init_data(self) -> Self:
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        data = self.ticker_data or {}
        self.ticker_symbol_name = from_dict_get_string(data, "symbol")
        self.server_time = from_dict_get_float(data, "time")
        self.bid_price = from_dict_get_float(data, "bidPrice")
        self.ask_price = from_dict_get_float(data, "askPrice")
        self.bid_volume = from_dict_get_float(data, "bidQty")
        self.ask_volume = from_dict_get_float(data, "askQty")
        self.has_been_init_data = True
        return self
