"""EXMO Ticker Data Container."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class ExmoRequestTickerData(TickerData):
    """EXMO ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Exmo ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "EXMO"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name: str | None = None
        self.server_time: float | None = None
        self.last_price: float | None = None
        self.bid_price: float | None = None
        self.ask_price: float | None = None
        self.bid_volume: float | None = None
        self.ask_volume: float | None = None
        self.last_volume: float | None = None
        self.volume_24h: float | None = None
        self.high_24h: float | None = None
        self.low_24h: float | None = None
        self.quote_volume_24h: float | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> ExmoRequestTickerData:
        """Parse EXMO ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # EXMO ticker format
        ticker = self.ticker_data or {}
        data = ticker.get("data", ticker)
        if data:
            self.ticker_symbol_name = data.get("symbol")
            self.last_price = parse_float(data.get("last_trade"))
            self.bid_price = parse_float(data.get("buy_price"))
            self.ask_price = parse_float(data.get("sell_price"))
            self.volume_24h = parse_float(data.get("vol"))
            self.high_24h = parse_float(data.get("high"))
            self.low_24h = parse_float(data.get("low"))
            self.quote_volume_24h = parse_float(data.get("vol_curr"))
        else:
            # Handle direct ticker format (nested dict)
            for key, value in ticker.items():
                if isinstance(value, dict):
                    self.last_price = parse_float(value.get("last_trade"))
                    self.bid_price = parse_float(value.get("buy_price"))
                    self.ask_price = parse_float(value.get("sell_price"))
                    self.volume_24h = parse_float(value.get("vol"))
                    self.high_24h = parse_float(value.get("high"))
                    self.low_24h = parse_float(value.get("low"))
                    self.quote_volume_24h = parse_float(value.get("vol_curr"))
                    self.ticker_symbol_name = key.replace("_", "/")
                    break

        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """Get all ticker data as a dictionary."""
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
                "volume_24h": self.volume_24h,
                "high_24h": self.high_24h,
                "low_24h": self.low_24h,
                "quote_volume_24h": self.quote_volume_24h,
            }
        return self.all_data

    def __str__(self) -> str:
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        return self.__str__()

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
