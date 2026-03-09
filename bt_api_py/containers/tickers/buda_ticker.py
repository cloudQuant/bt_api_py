"""Buda Ticker Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData


class BudaRequestTickerData(TickerData):
    """Buda ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Buda ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BUDA"
        self.local_update_time = time.time()
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name = None
        self.has_been_init_data = False
        self.last_price = None
        self.bid_price = None
        self.ask_price = None
        self.volume_24h = None
        self.high_24h = None
        self.low_24h = None
        self.timestamp = None

    def init_data(self) -> "BudaRequestTickerData":
        """Parse Buda ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Initialize all attributes to None
        self.ticker_symbol_name = None
        self.last_price = None
        self.bid_price = None
        self.ask_price = None
        self.volume_24h = None
        self.high_24h = None
        self.low_24h = None
        self.timestamp = None

        ticker = self.ticker_data.get("ticker", {})

        if ticker:
            self.ticker_symbol_name = ticker.get("market_id")
            self.last_price = self._parse_float(ticker.get("last_price", [0])[0])
            self.bid_price = self._parse_float(ticker.get("min_ask", [0])[0])
            self.ask_price = self._parse_float(ticker.get("max_bid", [0])[0])
            self.volume_24h = self._parse_float(ticker.get("volume", [0])[0])
            self.high_24h = self._parse_float(ticker.get("max_price", [0])[0])
            self.low_24h = self._parse_float(ticker.get("min_price", [0])[0])
            self.timestamp = self._parse_int(ticker.get("timestamp"))

        self.has_been_init_data = True
        return self

    @staticmethod
    def _parse_float(value: Any) -> float | None:
        """Parse value to float.

        Args:
            value: Value to parse.

        Returns:
            Parsed float value or None if parsing fails.

        """
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_int(value: Any) -> int | None:
        """Parse value to int.

        Args:
            value: Value to parse.

        Returns:
            Parsed int value or None if parsing fails.

        """
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

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
