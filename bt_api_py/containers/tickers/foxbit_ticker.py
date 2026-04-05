"""Foxbit Ticker Data Container."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class FoxbitRequestTickerData(TickerData):
    """Foxbit ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Foxbit ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "FOXBIT"
        self.local_update_time = time.time()
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name: str | None = None
        self.has_been_init_data = False

    def init_data(self) -> FoxbitRequestTickerData:
        """Parse Foxbit ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Foxbit ticker format
        ticker = self.ticker_data or {}
        data = ticker.get("data", ticker)
        # Handle both list (API response) and dict (test format)
        if isinstance(data, list) and len(data) > 0:
            data = data[0]

        if data:
            self.ticker_symbol_name = (
                data.get("marketSymbol") or data.get("market_symbol") or data.get("symbol")
            )

            # Handle nested structure from actual API response
            last_trade = data.get("last_trade", {})
            best = data.get("best", {})
            rolling_24h = data.get("rolling_24h", {})

            self.last_price = parse_float(last_trade.get("price") or data.get("lastPrice"))
            self.bid_price = parse_float(best.get("bid", {}).get("price") or data.get("bidPrice"))
            self.ask_price = parse_float(best.get("ask", {}).get("price") or data.get("askPrice"))
            self.volume_24h = parse_float(rolling_24h.get("volume") or data.get("vol"))
            self.high_24h = parse_float(rolling_24h.get("high") or data.get("highPrice"))
            self.low_24h = parse_float(rolling_24h.get("low") or data.get("lowPrice"))
            self.quote_volume_24h = parse_float(
                rolling_24h.get("quote_volume") or data.get("volQuote")
            )

        self.has_been_init_data = True
        return self
