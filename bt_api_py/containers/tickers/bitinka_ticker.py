"""Bitinka Ticker Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData


class BitinkaRequestTickerData(TickerData):
    """Bitinka ticker data container.

    Note: Bitinka API documentation is limited.
    This implementation provides a generic structure that
    can be adjusted based on actual API responses.
    """

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Bitinka ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BITINKA"
        self.local_update_time = time.time()
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name = None
        self.has_been_init_data = False

    def init_data(self) -> "BitinkaRequestTickerData":
        """Parse Bitinka ticker response.

        Note: Actual response format may vary based on Bitinka's API.
        This is a generic implementation that handles common patterns.
        """
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data
        if data:
            # Try common field names for ticker data
            self.ticker_symbol_name = (
                data.get("symbol") or data.get("market") or data.get("pair") or data.get("s")
            )
            self.last_price = self._parse_float(
                data.get("last")
                or data.get("price")
                or data.get("close")
                or data.get("c")
                or data.get("last_price")
            )
            self.bid_price = self._parse_float(data.get("bid") or data.get("buy"))
            self.ask_price = self._parse_float(data.get("ask") or data.get("sell"))
            self.volume_24h = self._parse_float(
                data.get("volume") or data.get("vol") or data.get("v") or data.get("volume_24h")
            )
            self.high_24h = self._parse_float(
                data.get("high") or data.get("h") or data.get("high_24h")
            )
            self.low_24h = self._parse_float(
                data.get("low") or data.get("l") or data.get("low_24h")
            )

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
