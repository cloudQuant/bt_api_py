"""GMX Ticker Data Container.

GMX is a decentralized perpetual exchange.
"""

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class GmxRequestTickerData(TickerData):
    """GMX ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Gmx ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.ticker_symbol_name: str | None = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "GMX"
        self.local_update_time = time.time()
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.last_price: float | None = None
        self.bid_price: float | None = None
        self.ask_price: float | None = None
        self.high_24h: float | None = None
        self.low_24h: float | None = None
        self.has_been_init_data = False

    def init_data(self) -> "GmxRequestTickerData":
        """Parse GMX ticker response.

        GMX ticker response format:
        {
            "BTC": {
                "minPrice": "50000.00",
                "maxPrice": "51000.00",
                ...
            },
            ...
        }
        """
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data if isinstance(self.ticker_data, dict) else {}

        # GMX returns a dict of tokens with their price data
        if data and isinstance(data, dict):
            # If symbol_name is provided, try to get that specific token's data
            if self.ticker_symbol_name and self.ticker_symbol_name in data:
                ticker = data[self.ticker_symbol_name]
            else:
                # Use the first token's data if no symbol match
                ticker = next(iter(data.values())) if data else {}

            if isinstance(ticker, dict):
                self.ticker_symbol_name = self.ticker_symbol_name or (ticker.get("symbol") or None)
                self.last_price = parse_float(
                    ticker.get("minPrice") or ticker.get("maxPrice") or ticker.get("price")
                )
                self.bid_price = parse_float(ticker.get("minPrice"))
                self.ask_price = parse_float(ticker.get("maxPrice"))
                self.high_24h = parse_float(ticker.get("maxPrice"))
                self.low_24h = parse_float(ticker.get("minPrice"))

        self.has_been_init_data = True
        return self

    # ── Standard getters ────────────────────────────────────────

    def get_symbol_name(self) -> str:
        return str(self.ticker_symbol_name) if self.ticker_symbol_name else ""

    def get_ticker_symbol_name(self) -> str | None:
        return self.ticker_symbol_name

    def get_last_price(self) -> float | None:
        return getattr(self, "last_price", None)

    def get_exchange_name(self) -> str:
        return self.exchange_name

    def get_local_update_time(self) -> float:
        return self.local_update_time

    def get_asset_type(self) -> str:
        return self.asset_type

    def get_all_data(self) -> dict[str, Any]:
        return self.ticker_data or {}

    def get_server_time(self) -> float | None:
        return self.local_update_time

    def get_bid_price(self) -> float | None:
        return getattr(self, "bid_price", None)

    def get_ask_price(self) -> float | None:
        return getattr(self, "ask_price", None)
