"""bitFlyer Ticker Data Container."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class BitflyerRequestTickerData(TickerData):
    """bitFlyer ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Bitflyer ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BITFLYER"
        self.local_update_time = time.time()
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name: str | None = None
        self.last_price: float | None = None
        self.bid_price: float | None = None
        self.ask_price: float | None = None
        self.volume_24h: float | None = None
        self.volume_quote_24h: float | None = None
        self.bid_size: float | None = None
        self.ask_size: float | None = None
        self.total_bid_depth: float | None = None
        self.total_ask_depth: float | None = None
        self.timestamp: int | None = None
        self.has_been_init_data = False

    def init_data(self) -> BitflyerRequestTickerData:
        """Parse bitFlyer ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # bitFlyer ticker response format
        if isinstance(self.ticker_data, dict):
            data = self.ticker_data
            if "product_code" in data:
                self.ticker_symbol_name = data.get("product_code")
                self.last_price = parse_float(data.get("ltp"))  # Last traded price
                self.bid_price = parse_float(data.get("best_bid"))
                self.ask_price = parse_float(data.get("best_ask"))
                self.volume_24h = parse_float(data.get("volume"))
                self.volume_quote_24h = parse_float(data.get("volume_by_product"))
                self.bid_size = parse_float(data.get("best_bid_size"))
                self.ask_size = parse_float(data.get("best_ask_size"))
                self.total_bid_depth = parse_float(data.get("total_bid_depth"))
                self.total_ask_depth = parse_float(data.get("total_ask_depth"))

                # Parse timestamp
                timestamp_str = data.get("timestamp")
                if timestamp_str:
                    self.timestamp = self._parse_timestamp(timestamp_str)

        self.has_been_init_data = True
        return self

    @staticmethod
    def _parse_timestamp(timestamp_str: str) -> int | None:
        """Parse ISO 8601 timestamp to unix timestamp in milliseconds.

        Args:
            timestamp_str: ISO 8601 timestamp string.

        Returns:
            Unix timestamp in milliseconds or None if parsing fails.

        """
        if not timestamp_str:
            return None
        try:
            from datetime import datetime

            # bitFlyer returns ISO format like "2024-01-01T00:00:00.000"
            dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            return None

    def get_exchange_name(self) -> str:
        """Get exchange name.

        Returns:
            Exchange name string.

        """
        return self.exchange_name

    def get_symbol_name(self) -> str:
        """Get symbol name.

        Returns:
            Symbol name string.

        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """Get asset type.

        Returns:
            Asset type string.

        """
        return self.asset_type

    def get_last_price(self) -> float | None:
        """Get last price.

        Returns:
            Last price or None.

        """
        self.init_data()
        return self.last_price

    def get_bid_price(self) -> float | None:
        """Get bid price.

        Returns:
            Bid price or None.

        """
        self.init_data()
        return self.bid_price

    def get_ask_price(self) -> float | None:
        """Get ask price.

        Returns:
            Ask price or None.

        """
        self.init_data()
        return self.ask_price
