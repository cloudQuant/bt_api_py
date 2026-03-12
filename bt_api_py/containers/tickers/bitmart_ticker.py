"""BitMart Ticker Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class BitmartRequestTickerData(TickerData):
    """BitMart ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Bitmart ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BITMART"
        self.local_update_time = time.time()
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name = None
        self.has_been_init_data = False

    def init_data(self) -> "BitmartRequestTickerData":
        """Parse BitMart ticker response.

        BitMart ticker format:
        {
          "symbol": "BTC_USDT",
          "last_price": "50000",
          "bid_1": "49999",
          "ask_1": "50001",
          "high_24h": "51000",
          "low_24h": "49000",
          "volume_24h": "1234.56",
          "timestamp": 1234567890
        }
        """
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data
        if data:
            self.ticker_symbol_name = data.get("symbol")
            self.last_price = parse_float(data.get("last_price"))
            self.bid_price = parse_float(data.get("bid_1"))
            self.ask_price = parse_float(data.get("ask_1"))
            self.volume_24h = parse_float(data.get("volume_24h"))
            self.high_24h = parse_float(data.get("high_24h"))
            self.low_24h = parse_float(data.get("low_24h"))
            self.quote_volume_24h = parse_float(data.get("quote_volume_24h"))

        self.has_been_init_data = True
        return self

    def get_symbol_name(self) -> str:
        return self.symbol_name
