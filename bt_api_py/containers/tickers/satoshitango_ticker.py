"""SatoshiTango Ticker Data Container."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class SatoshiTangoRequestTickerData(TickerData):
    """SatoshiTango ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize SatoshiTango ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "SATOSHITANGO"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.has_been_init_data = False
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )

    def init_data(self) -> SatoshiTangoRequestTickerData:
        """Parse SatoshiTango ticker response."""
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
            self.ticker_symbol_name = data.get("symbol") or data.get("market")
            self.last_price = parse_float(data.get("last") or data.get("price"))
            self.bid_price = parse_float(data.get("bid"))
            self.ask_price = parse_float(data.get("ask"))
            self.volume_24h = parse_float(data.get("volume") or data.get("volume_24h"))
            self.high_24h = parse_float(data.get("high") or data.get("high_24h"))
            self.low_24h = parse_float(data.get("low") or data.get("low_24h"))

        self.has_been_init_data = True
        return self
