"""Giottus Ticker Data Container."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class GiottusRequestTickerData(TickerData):
    """Giottus ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Giottus ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "GIOTTUS"
        self.local_update_time = time.time()
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name: str | None = None
        self.last_price: float | None = None
        self.bid_price: float | None = None
        self.ask_price: float | None = None
        self.volume_24h: float | None = None
        self.high_24h: float | None = None
        self.low_24h: float | None = None
        self.price_change_24h: float | None = None
        self.has_been_init_data = False

    def init_data(self) -> GiottusRequestTickerData:
        """Parse Giottus ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data if isinstance(self.ticker_data, dict) else {}
        # Adapt to Giottus API response format when available
        ticker = data.get("data", data) if isinstance(data, dict) else {}

        if ticker:
            self.ticker_symbol_name = str(ticker.get("symbol") or self.symbol_name)
            self.last_price = parse_float(ticker.get("last") or ticker.get("lastPrice"))
            self.bid_price = parse_float(ticker.get("bid") or ticker.get("buy"))
            self.ask_price = parse_float(ticker.get("ask") or ticker.get("sell"))
            self.volume_24h = parse_float(ticker.get("volume") or ticker.get("volume_24h"))
            self.high_24h = parse_float(ticker.get("high") or ticker.get("high_24h"))
            self.low_24h = parse_float(ticker.get("low") or ticker.get("low_24h"))
            self.price_change_24h = parse_float(
                ticker.get("price_change") or ticker.get("priceChange24h") or ticker.get("change")
            )

        self.has_been_init_data = True
        return self

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
        return getattr(self, "timestamp", None)

    def get_bid_price(self) -> float | None:
        return self.bid_price

    def get_ask_price(self) -> float | None:
        return self.ask_price

    def get_last_price(self) -> float | None:
        return self.last_price

    def get_bid_volume(self) -> float | None:
        return None

    def get_ask_volume(self) -> float | None:
        return None

    def get_last_volume(self) -> float | None:
        return None

    def get_all_data(self) -> dict[str, Any]:
        return {
            "exchange_name": self.exchange_name,
            "symbol_name": self.symbol_name,
            "ticker_symbol_name": self.ticker_symbol_name,
            "asset_type": self.asset_type,
            "last_price": self.last_price,
            "bid_price": self.bid_price,
            "ask_price": self.ask_price,
            "volume_24h": self.volume_24h,
            "high_24h": self.high_24h,
            "low_24h": self.low_24h,
            "price_change_24h": self.price_change_24h,
            "local_update_time": self.local_update_time,
        }

    def __str__(self) -> str:
        return f"GiottusTicker({self.symbol_name}: {self.last_price})"

    def __repr__(self) -> str:
        return self.__str__()
