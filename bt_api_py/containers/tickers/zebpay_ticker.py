"""Zebpay Ticker Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class ZebpayRequestTickerData(TickerData):
    """Zebpay ticker data container."""

    _parse_float = staticmethod(parse_float)

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Zebpay ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "ZEBPAY"
        self.local_update_time = time.time()
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name = None
        self.has_been_init_data = False

    def init_data(self) -> "ZebpayRequestTickerData":
        """Parse Zebpay ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = (self.ticker_data or {}).get("data", {})
        if data:
            self.ticker_symbol_name = data.get("symbol")
            self.last_price = parse_float(data.get("last"))
            self.bid_price = parse_float(data.get("bid"))
            self.ask_price = parse_float(data.get("ask"))
            self.high_24h = parse_float(data.get("high"))
            self.low_24h = parse_float(data.get("low"))
            self.volume_24h = parse_float(data.get("volume"))
            self.quote_volume = parse_float(data.get("quoteVolume"))

        self.has_been_init_data = True
        return self
