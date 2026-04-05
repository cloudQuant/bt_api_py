"""Crypto.com Ticker Data Container."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py._compat import Self
from bt_api_py.containers.tickers.ticker import TickerData


class CryptoComTicker(TickerData):
    """Crypto.com ticker implementation."""

    def __init__(
        self, ticker_info, symbol_name, asset_type="SPOT", has_been_json_encoded=False
    ) -> None:
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "CRYPTOCOM"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name: str | None = None
        self.server_time: float | None = None
        self.bid_price: float | None = None
        self.ask_price: float | None = None
        self.bid_volume: float | None = None
        self.ask_volume: float | None = None
        self.last_price: float | None = None
        self.high_24h: float | None = None
        self.low_24h: float | None = None
        self.volume_24h: float | None = None
        self.quote_volume_24h: float | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> Self:
        """Initialize ticker data from raw response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data or {}
        self.ticker_symbol_name = self.symbol_name
        t_val = data.get("t")
        self.server_time = float(t_val) / 1000 if t_val is not None else None
        self.last_price = float(data.get("a", 0))
        self.bid_price = float(data.get("b", 0))
        self.ask_price = float(data.get("k", 0))
        self.high_24h = float(data.get("h", 0))
        self.low_24h = float(data.get("l", 0))
        self.volume_24h = float(data.get("v", 0))
        self.quote_volume_24h = float(data.get("vv", 0))
        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """Get all ticker data as dictionary."""
        if not self.has_been_init_data:
            self.init_data()
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
                "high_24h": self.high_24h,
                "low_24h": self.low_24h,
                "volume_24h": self.volume_24h,
                "quote_volume_24h": self.quote_volume_24h,
            }
        return self.all_data

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
        return str(self.symbol_name) if self.symbol_name is not None else ""

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
        return None

    @classmethod
    def from_api_response(cls, data, symbol):
        """Create ticker from API response.

        This is a convenience method for testing.
        For production use, use the standard constructor with json-encoded data.
        """
        import json

        return cls(
            ticker_info=json.dumps(data),
            symbol_name=symbol,
            asset_type="SPOT",
            has_been_json_encoded=False,
        )
