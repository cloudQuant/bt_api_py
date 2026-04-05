"""CoW Swap Ticker Data Container
CoW Swap is a DEX - ticker data comes from on-chain events and settlement contracts.
"""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class CowSwapRequestTickerData(TickerData):
    """CoW Swap ticker data container."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize CowSwap ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.ticker_symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "COW_SWAP"
        self.local_update_time = time.time()
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.last_price: float | None = None
        self.has_been_init_data = False

    def init_data(self) -> CowSwapRequestTickerData:
        """Parse CoW Swap ticker response.
        Note: As a DEX, CoW Swap doesn't provide traditional ticker data.
        This container is provided for compatibility but may be populated from
        subgraph queries or external data sources.
        """
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # CoW Swap API returns various data structures depending on endpoint
        data = self.ticker_data
        if isinstance(data, dict):
            # Token price endpoint
            if "price" in data:
                self.last_price = parse_float(data.get("price"))
            # Trade/settlement data
            if "sellAmount" in data and "buyAmount" in data:
                # Could compute price from trade data
                pass

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
        return getattr(self, "last_price", None)

    def get_ask_price(self) -> float | None:
        return getattr(self, "last_price", None)
