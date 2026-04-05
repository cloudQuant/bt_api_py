"""Independent Reserve Ticker Data Container."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class IndependentReserveRequestTickerData(TickerData):
    """Independent Reserve ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize IndependentReserve ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "INDEPENDENT_RESERVE"
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
        self.vwap: float | None = None
        self.timestamp: Any = None
        self.has_been_init_data = False

    def init_data(self) -> IndependentReserveRequestTickerData:
        """Parse Independent Reserve ticker response.

        Independent Reserve ticker response format:
        {
            "LastPrice": 50000.00,
            "CurrentHighestBidPrice": 49950.00,
            "CurrentLowestOfferPrice": 50050.00,
            "DayVolumeXbt": 123.45,
            "DayVolumeAud": 6172500.00,
            "DayHighestPrice": 51000.00,
            "DayLowestPrice": 49000.00,
            "DayAvgPrice": 50000.00,
            "CreatedTimestamp": "2023-01-01T00:00:00Z"
        }
        """
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data if isinstance(self.ticker_data, dict) else {}

        if data:
            self.ticker_symbol_name = self.symbol_name
            self.last_price = parse_float(data.get("LastPrice"))
            self.bid_price = parse_float(data.get("CurrentHighestBidPrice"))
            self.ask_price = parse_float(data.get("CurrentLowestOfferPrice"))
            self.volume_24h = parse_float(data.get("DayVolumeXbt") or data.get("DayVolumeAud"))
            self.high_24h = parse_float(data.get("DayHighestPrice"))
            self.low_24h = parse_float(data.get("DayLowestPrice"))
            self.vwap = parse_float(data.get("DayAvgPrice"))
            self.timestamp = data.get("CreatedTimestamp")

        self.has_been_init_data = True
        return self
