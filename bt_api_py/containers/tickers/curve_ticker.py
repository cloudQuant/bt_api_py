"""
Curve Pool Data Container

Curve doesn't have traditional tickers. This container handles pool data.
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class CurveRequestTickerData(TickerData):
    """Curve pool data container.

    Curve is a DEX with pools rather than traditional trading pairs.
    This container normalizes pool data into ticker-like format.
    """

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.ticker_symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "CURVE"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.has_been_init_data = False

    def init_data(self):
        """Parse Curve pool data response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data.get("data", {}) if isinstance(self.ticker_data, dict) else {}
        if data:
            # Pool data fields
            self.ticker_symbol_name = (
                self.ticker_symbol_name or data.get("name") or data.get("address")
            )
            self.last_price = self._parse_float(data.get("virtualPrice"))
            self.volume_24h = self._parse_float(data.get("volume")) or self._parse_float(
                data.get("usdVolume")
            )
            self.high_24h = None  # Not applicable for Curve pools
            self.low_24h = None  # Not applicable for Curve pools

            # Additional pool-specific fields
            self.pool_address = data.get("address")
            self.pool_name = data.get("name")
            self.pool_tvl = self._parse_float(data.get("usdTotal"))
            self.base_apy = self._parse_float(data.get("baseApy"))
            self.rewards_apy = self._parse_float(data.get("rewardApy"))

        self.has_been_init_data = True
        return self

    # ── Standard getters ────────────────────────────────────────

    def get_symbol_name(self):
        return self.ticker_symbol_name

    def get_ticker_symbol_name(self):
        return self.ticker_symbol_name

    def get_last_price(self):
        return getattr(self, "last_price", None)

    def get_exchange_name(self):
        return self.exchange_name

    def get_local_update_time(self):
        return self.local_update_time

    def get_asset_type(self):
        return self.asset_type

    def get_all_data(self):
        return self.ticker_data

    def get_server_time(self):
        return self.local_update_time

    def get_bid_price(self):
        return getattr(self, "last_price", None)

    def get_ask_price(self):
        return getattr(self, "last_price", None)

    @staticmethod
    def _parse_float(value):
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
