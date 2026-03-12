"""Coinone Ticker Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class CoinoneRequestTickerData(TickerData):
    """Coinone ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Coinone ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "COINONE"
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
        self.has_been_init_data = False

    def init_data(self) -> "CoinoneRequestTickerData":
        """Parse Coinone ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data or {}
        if data:
            # Coinone returns ticker data at root level
            self.ticker_symbol_name = self.symbol_name
            self.last_price = parse_float(data.get("last"))
            # Coinone API has best_bids/best_asks arrays
            best_bids = data.get("best_bids")
            if best_bids and isinstance(best_bids, list) and len(best_bids) > 0:
                first_bid = best_bids[0]
                self.bid_price = parse_float(
                    first_bid.get("price") if isinstance(first_bid, dict) else None
                )
            else:
                self.bid_price = parse_float(data.get("bid"))
            best_asks = data.get("best_asks")
            if best_asks and isinstance(best_asks, list) and len(best_asks) > 0:
                first_ask = best_asks[0]
                self.ask_price = parse_float(
                    first_ask.get("price") if isinstance(first_ask, dict) else None
                )
            else:
                self.ask_price = parse_float(data.get("ask"))
            # Use quote_volume for volume
            self.volume_24h = parse_float(data.get("quote_volume")) or parse_float(
                data.get("volume")
            )
            self.high_24h = parse_float(data.get("high"))
            self.low_24h = parse_float(data.get("low"))

        self.has_been_init_data = True
        return self

    def get_exchange_name(self) -> str:
        return self.exchange_name

    def get_local_update_time(self) -> float:
        return self.local_update_time

    def get_symbol_name(self) -> str:
        return self.symbol_name

    def get_ticker_symbol_name(self) -> str | None:
        return self.ticker_symbol_name

    def get_asset_type(self) -> str:
        return self.asset_type

    def get_server_time(self) -> float | None:
        return getattr(self, "timestamp", None)

    def get_bid_price(self) -> float | None:
        return self.bid_price

    def get_ask_price(self) -> float | None:
        return self.ask_price

    def get_last_price(self) -> float | None:
        return self.last_price
