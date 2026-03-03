"""
CoinSpot Ticker Data Container
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class CoinSpotRequestTickerData(TickerData):
    """CoinSpot ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.ticker_symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "COINSPOT"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False

    def init_data(self):
        """Parse CoinSpot ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # CoinSpot API returns {"status": "ok", "prices": {"BTC": {"bid": ..., "ask": ..., "last": ...}}}
        if self.ticker_data.get("status") == "ok":
            prices = self.ticker_data.get("prices", {})
            # prices may contain the specific symbol data or all symbols
            if self.ticker_symbol_name in prices:
                data = prices.get(self.ticker_symbol_name, {})
            else:
                data = prices

            self.last_price = self._parse_float(data.get("last"))
            self.bid_price = self._parse_float(data.get("bid"))
            self.ask_price = self._parse_float(data.get("ask"))

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
