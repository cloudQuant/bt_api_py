"""CoinSwitch Ticker Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData


class CoinSwitchRequestTickerData(TickerData):
    """CoinSwitch ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize CoinSwitch ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.ticker_symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "COINSWITCH"
        self.local_update_time = time.time()
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name = None
        self.has_been_init_data = False

    def init_data(self) -> "CoinSwitchRequestTickerData":
        """Parse CoinSwitch ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # CoinSwitch API response structure may vary
        # Common fields: last, bid, ask, volume, high, low
        data = self.ticker_data
        if isinstance(data, dict):
            self.last_price = self._parse_float(data.get("last") or data.get("price"))
            self.bid_price = self._parse_float(data.get("bid"))
            self.ask_price = self._parse_float(data.get("ask"))
            self.volume_24h = self._parse_float(data.get("volume"))
            self.high_24h = self._parse_float(data.get("high"))
            self.low_24h = self._parse_float(data.get("low"))

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
