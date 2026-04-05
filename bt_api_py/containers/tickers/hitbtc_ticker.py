from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class HitBtcRequestTickerData(TickerData):
    """保存HitBTC ticker信息."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize HitBtc ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "HITBTC"  # 交易所名称
        self.local_update_time = time.time()  # 本地时间戳
        self.symbol_name = symbol_name
        self.asset_type = asset_type  # ticker的类型
        # Always store ticker_info for init_data() to parse
        self.ticker_data: str | dict[str, Any] = ticker_info
        self.ticker_symbol_name: str | None = None
        self.server_time: float | None = None
        self.bid_price: float | None = None
        self.ask_price: float | None = None
        self.bid_volume: float | None = None
        self.ask_volume: float | None = None
        self.last_price: float | None = None
        self.last_volume: float | None = None
        self.high_price: float | None = None
        self.low_price: float | None = None
        self.open_price: float | None = None
        self.price_change: float | None = None
        self.price_change_percent: float | None = None
        self.count: float | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> HitBtcRequestTickerData:
        if self.has_been_init_data:
            return self

        raw = self.ticker_data
        if isinstance(raw, str):
            ticker_data: dict[str, Any] | None = json.loads(raw)
        else:
            ticker_data = raw
        if ticker_data is None:
            return self
        self.ticker_data = ticker_data

        # 提取数据
        self.ticker_symbol_name = from_dict_get_string(ticker_data, "symbol")
        self.server_time = from_dict_get_float(ticker_data, "timestamp")
        self.last_price = from_dict_get_float(ticker_data, "last")
        self.bid_price = from_dict_get_float(ticker_data, "bid")
        self.ask_price = from_dict_get_float(ticker_data, "ask")
        self.last_volume = from_dict_get_float(ticker_data, "volume")
        self.high_price = from_dict_get_float(ticker_data, "high")
        self.low_price = from_dict_get_float(ticker_data, "low")
        self.open_price = from_dict_get_float(ticker_data, "open")
        self.price_change = from_dict_get_float(ticker_data, "volume")
        self.price_change_percent = from_dict_get_float(ticker_data, "volumeQuote")
        self.count = from_dict_get_float(ticker_data, "count")
        self.ask_volume = from_dict_get_float(ticker_data, "askVolume")
        self.bid_volume = from_dict_get_float(ticker_data, "bidVolume")

        self.has_been_init_data = True
        return self

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
                "high_price": self.high_price,
                "low_price": self.low_price,
                "open_price": self.open_price,
                "price_change": self.price_change,
                "price_change_percent": self.price_change_percent,
                "count": self.count,
            }
        return self.all_data or {}

    def get_exchange_name(self) -> str:
        """Get exchange name."""
        return self.exchange_name

    def get_local_update_time(self) -> float:
        """Get local update time."""
        return self.local_update_time

    def get_symbol_name(self) -> str:
        """Get symbol name."""
        return self.symbol_name

    def get_ticker_symbol_name(self) -> str | None:
        """Get ticker symbol name."""
        return self.ticker_symbol_name

    def get_asset_type(self) -> str:
        """Get asset type."""
        return self.asset_type

    def get_server_time(self) -> float | None:
        """Get server time."""
        return self.server_time

    def get_bid_price(self) -> float | None:
        """Get bid price."""
        return self.bid_price

    def get_ask_price(self) -> float | None:
        """Get ask price."""
        return self.ask_price

    def get_bid_volume(self) -> float | None:
        """Get bid volume."""
        return self.bid_volume

    def get_ask_volume(self) -> float | None:
        """Get ask volume."""
        return self.ask_volume

    def get_last_price(self) -> float | None:
        """Get last price."""
        return self.last_price

    def get_last_volume(self) -> float | None:
        """Get last volume."""
        return self.last_volume

    def __str__(self) -> str:
        return f"HITBTC Ticker {self.symbol_name}: Last={self.last_price}, Bid={self.bid_price}, Ask={self.ask_price}"

    def __repr__(self) -> str:
        return f"<HitBtcTickerData {self.symbol_name} last={self.last_price}>"
