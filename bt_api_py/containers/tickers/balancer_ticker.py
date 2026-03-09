"""Balancer Ticker Data Container.

Handles ticker/price data from Balancer's GraphQL API.
"""

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class BalancerRequestTickerData(TickerData):
    """Balancer ticker data container.

    Parses token price data from Balancer's tokenGetTokenDynamicData query.
    """

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Balancer ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BALANCER"
        self.local_update_time = time.time()
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name = None
        self.has_been_init_data = False
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name = None
        self.server_time = None
        self.bid_price = None
        self.ask_price = None
        self.bid_volume = None
        self.ask_volume = None
        self.last_price = None
        self.last_volume = None
        self.price_change_24h = None
        self.volume_24h = None
        self.market_cap = None
        self.liquidity = None

    def init_data(self) -> "BalancerRequestTickerData":
        """Parse Balancer ticker response from GraphQL API."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Balancer GraphQL response structure:
        # {
        #   "data": {
        #     "tokenGetTokenDynamicData": {
        #       "address": "0x...",
        #       "price": "1234.56",
        #       "priceChange24h": "0.0234",
        #       "marketCap": "1000000",
        #       "volume24h": "50000",
        #       "liquidity": "100000"
        #     }
        #   }
        # }

        # Extract the nested data
        if isinstance(self.ticker_data, dict):
            if "data" in self.ticker_data:
                inner = self.ticker_data.get("data", {}).get("tokenGetTokenDynamicData", {})
                if inner:
                    self.ticker_data = inner

        self.ticker_symbol_name = from_dict_get_string(self.ticker_data, "address")
        self.server_time = time.time()
        self.last_price = from_dict_get_float(self.ticker_data, "price")
        self.price_change_24h = from_dict_get_float(self.ticker_data, "priceChange24h")
        self.volume_24h = from_dict_get_float(self.ticker_data, "volume24h")
        self.market_cap = from_dict_get_float(self.ticker_data, "marketCap")
        self.liquidity = from_dict_get_float(self.ticker_data, "liquidity")

        # For AMM pools, bid/ask aren't directly applicable
        # Set them equal to last price for compatibility
        self.bid_price = self.last_price
        self.ask_price = self.last_price

        self.has_been_init_data = True
        return self

    def get_exchange_name(self) -> str:
        return self.exchange_name

    def get_symbol_name(self) -> str:
        return self.symbol_name

    def get_ticker_symbol_name(self) -> str:
        return self.ticker_symbol_name or self.symbol_name

    def get_asset_type(self) -> str:
        return self.asset_type

    def get_server_time(self) -> float:
        return self.server_time

    def get_bid_price(self) -> float | None:
        return self.bid_price

    def get_ask_price(self) -> float | None:
        return self.ask_price

    def get_bid_volume(self) -> float | None:
        return self.bid_volume

    def get_ask_volume(self) -> float | None:
        return self.ask_volume

    def get_last_price(self) -> float | None:
        return self.last_price

    def get_last_volume(self) -> float | None:
        return self.last_volume

    def get_price_change_24h(self) -> float | None:
        return self.price_change_24h

    def get_volume_24h(self) -> float | None:
        return self.volume_24h


class BalancerWssTickerData(BalancerRequestTickerData):
    """Balancer WebSocket ticker data container.

    Note: Balancer doesn't have native WebSocket support.
    This class is provided for future compatibility if WebSocket
    support is added via third-party services.
    """

    def init_data(self) -> "BalancerRequestTickerData":
        """Parse Balancer WebSocket ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # WebSocket format would be similar to REST
        # Use the same parsing logic
        super().init_data()
        return self
