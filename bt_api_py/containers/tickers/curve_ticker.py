"""Curve Pool Data Container.

Curve doesn't have traditional tickers. This container handles pool data.
"""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class CurveRequestTickerData(TickerData):
    """Curve pool data container.

    Curve is a DEX with pools rather than traditional trading pairs.
    This container normalizes pool data into ticker-like format.
    """

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Curve ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.ticker_symbol_name: str | None = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "CURVE"
        self.local_update_time = time.time()
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.last_price: float | None = None
        self.volume_24h: float | None = None
        self.high_24h: float | None = None
        self.low_24h: float | None = None
        self.pool_address: Any = None
        self.pool_name: Any = None
        self.pool_tvl: float | None = None
        self.base_apy: float | None = None
        self.rewards_apy: float | None = None
        self.has_been_init_data = False

    def init_data(self) -> CurveRequestTickerData:
        """Parse Curve pool data response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = (self.ticker_data or {}).get("data", {})
        if data:
            # Pool data fields
            raw = self.ticker_symbol_name or data.get("name") or data.get("address")
            self.ticker_symbol_name = str(raw) if raw is not None else self.ticker_symbol_name
            self.last_price = parse_float(data.get("virtualPrice"))
            self.volume_24h = parse_float(data.get("volume")) or parse_float(data.get("usdVolume"))
            self.high_24h = None  # Not applicable for Curve pools
            self.low_24h = None  # Not applicable for Curve pools

            # Additional pool-specific fields
            self.pool_address = data.get("address")
            self.pool_name = data.get("name")
            self.pool_tvl = parse_float(data.get("usdTotal"))
            self.base_apy = parse_float(data.get("baseApy"))
            self.rewards_apy = parse_float(data.get("rewardApy"))

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
