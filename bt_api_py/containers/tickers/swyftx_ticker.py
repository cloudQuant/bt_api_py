"""Swyftx Ticker Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class SwyftxRequestTickerData(TickerData):
    """Swyftx ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Swyftx ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "SWYFTX"
        self.local_update_time = time.time()
        self.has_been_init_data = False
        self.ticker_symbol_name: str | None = None
        self.last_price: float | None = None
        self.bid_price: float | None = None
        self.ask_price: float | None = None
        self.volume_24h: float | None = None
        self.high_24h: float | None = None
        self.low_24h: float | None = None
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )

    def init_data(self) -> "SwyftxRequestTickerData":
        """Parse Swyftx ticker response."""
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
            self.ticker_symbol_name = (
                data.get("symbol") or data.get("market") or data.get("instrument")
            )
            self.last_price = parse_float(
                data.get("last") or data.get("lastPrice") or data.get("price")
            )
            self.bid_price = parse_float(data.get("bid"))
            self.ask_price = parse_float(data.get("ask"))
            self.volume_24h = parse_float(data.get("volume") or data.get("volume_24h"))
            self.high_24h = parse_float(data.get("high") or data.get("high_24h"))
            self.low_24h = parse_float(data.get("low") or data.get("low_24h"))

        self.has_been_init_data = True
        return self
