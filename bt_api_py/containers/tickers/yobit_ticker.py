"""YoBit Ticker Data Container."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class YobitRequestTickerData(TickerData):
    """YoBit ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Yobit ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "YOBIT"
        self.local_update_time = time.time()
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name = None
        self.has_been_init_data = False

    def init_data(self) -> YobitRequestTickerData:
        """Parse YoBit ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # YoBit returns data in nested format: {"btc_usdt": {...}}
        data = self.ticker_data
        if isinstance(data, dict):
            # Get the first ticker data
            for value in data.values():
                if isinstance(value, dict) and "last" in value:
                    data = value
                    break

        if data and isinstance(data, dict):
            self.ticker_symbol_name = data.get("symbol")
            self.last_price = parse_float(data.get("last"))
            self.bid_price = parse_float(data.get("buy"))
            self.ask_price = parse_float(data.get("sell"))
            self.volume_24h = parse_float(data.get("vol"))
            self.high_24h = parse_float(data.get("high"))
            self.low_24h = parse_float(data.get("low"))

        self.has_been_init_data = True
        return self
