"""SushiSwap Ticker Data Container."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class SushiSwapRequestTickerData(TickerData):
    """SushiSwap ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize SushiSwap ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "SUSHISWAP"
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
        self.liquidity: float | None = None
        self.has_been_init_data = False

    def init_data(self) -> SushiSwapRequestTickerData:
        """Parse SushiSwap ticker response."""
        if not self.has_been_json_encoded:
            if isinstance(self.ticker_info, str):
                self.ticker_data = json.loads(self.ticker_info)
            else:
                self.ticker_data = self.ticker_info
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data if isinstance(self.ticker_data, dict) else {}
        if data:
            # SushiSwap price API returns different formats
            self.ticker_symbol_name = (
                data.get("symbol") or data.get("address") or self.symbol_name
            ) or None
            self.last_price = parse_float(data.get("price") or data.get("value"))
            # SushiSwap may not provide bid/ask for AMM pools
            self.bid_price = parse_float(data.get("bid"))
            self.ask_price = parse_float(data.get("ask"))
            self.volume_24h = parse_float(data.get("volume") or data.get("volumeUSD"))
            self.high_24h = parse_float(data.get("high"))
            self.low_24h = parse_float(data.get("low"))
            # Add liquidity for AMM pools
            self.liquidity = parse_float(data.get("liquidity") or data.get("totalValueLockedUSD"))

        self.has_been_init_data = True
        return self
