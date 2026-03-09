"""Bitso Ticker Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData


class BitsoRequestTickerData(TickerData):
    """Bitso ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Bitso ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BITSO"
        self.local_update_time = time.time()
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name = None
        self.has_been_init_data = False

    def init_data(self) -> "BitsoRequestTickerData":
        """Parse Bitso ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Bitso wraps response: {success: true, payload: {...}}
        payload = self.ticker_data.get("payload", {}) if isinstance(self.ticker_data, dict) else {}
        if payload:
            self.ticker_symbol_name = payload.get("book")
            self.last_price = self._parse_float(payload.get("last"))
            self.bid_price = self._parse_float(payload.get("bid"))
            self.ask_price = self._parse_float(payload.get("ask"))
            self.volume_24h = self._parse_float(payload.get("volume"))
            self.high_24h = self._parse_float(payload.get("high"))
            self.low_24h = self._parse_float(payload.get("low"))

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

    # Getter methods required by TickerData base class
    def get_exchange_name(self) -> str:
        return self.exchange_name

    def get_symbol_name(self) -> str:
        return self.symbol_name

    def get_ticker_symbol_name(self) -> str | None:
        return self.ticker_symbol_name

    def get_asset_type(self) -> str:
        return self.asset_type

    def get_local_update_time(self) -> float:
        return self.local_update_time

    def get_server_time(self) -> float | None:
        return None

    def get_bid_price(self) -> float | None:
        return self.bid_price

    def get_ask_price(self) -> float | None:
        return self.ask_price

    def get_bid_volume(self) -> float | None:
        return None

    def get_ask_volume(self) -> float | None:
        return None

    def get_last_price(self) -> float | None:
        return self.last_price

    def get_last_volume(self) -> float | None:
        return None

    def get_all_data(self) -> dict[str, Any]:
        return {
            "exchange_name": self.exchange_name,
            "symbol_name": self.symbol_name,
            "asset_type": self.asset_type,
            "local_update_time": self.local_update_time,
            "ticker_symbol_name": self.ticker_symbol_name,
            "last_price": self.last_price,
            "bid_price": self.bid_price,
            "ask_price": self.ask_price,
            "volume_24h": self.volume_24h,
            "high_24h": self.high_24h,
            "low_24h": self.low_24h,
        }

    def __str__(self) -> str:
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        return self.__str__()
