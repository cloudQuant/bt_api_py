"""Bithumb Ticker Data Container."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class BithumbRequestTickerData(TickerData):
    """Bithumb ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Bithumb ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BITHUMB"
        self.local_update_time = time.time()
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name: str | None = None
        self.last_price: float | None = None
        self.high_24h: float | None = None
        self.low_24h: float | None = None
        self.volume_24h: float | None = None
        self.price_change_percent_24h: float | None = None
        self.has_been_init_data = False

    def init_data(self) -> BithumbRequestTickerData:
        """Parse Bithumb ticker response.

        Bithumb ticker format:
        {
          "s": "BTC-USDT",  # symbol
          "c": "50000",      # last price (close)
          "h": "51000",      # 24h high
          "l": "49000",      # 24h low
          "v": "1234.56",    # 24h volume
          "p": "2.5"         # 24h change percent
        }
        """
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data or {}
        if data:
            self.ticker_symbol_name = data.get("s")
            self.last_price = parse_float(data.get("c"))
            self.high_24h = parse_float(data.get("h"))
            self.low_24h = parse_float(data.get("l"))
            self.volume_24h = parse_float(data.get("v"))
            self.price_change_percent_24h = parse_float(data.get("p"))

        self.has_been_init_data = True
        return self

    def get_exchange_name(self) -> str:
        return self.exchange_name
