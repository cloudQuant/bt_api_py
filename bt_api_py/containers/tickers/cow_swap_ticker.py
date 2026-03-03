"""
CoW Swap Ticker Data Container
CoW Swap is a DEX - ticker data comes from on-chain events and settlement contracts.
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class CowSwapRequestTickerData(TickerData):
    """CoW Swap ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.ticker_symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "COW_SWAP"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False

    def init_data(self):
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
                self.last_price = self._parse_float(data.get("price"))
            # Trade/settlement data
            if "sellAmount" in data and "buyAmount" in data:
                # Could compute price from trade data
                pass

        self.has_been_init_data = True
        return self

    @staticmethod
    def _parse_float(value):
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
