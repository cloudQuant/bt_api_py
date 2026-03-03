"""
Luno Ticker Data Container
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class LunoRequestTickerData(TickerData):
    """Luno ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "LUNO"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False

    def init_data(self):
        """Parse Luno ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Luno ticker response structure
        self.ticker_symbol_name = self.ticker_data.get("pair")
        self.last_price = self._parse_float(self.ticker_data.get("last_trade"))
        self.bid_price = self._parse_float(self.ticker_data.get("bid"))
        self.ask_price = self._parse_float(self.ticker_data.get("ask"))
        self.volume_24h = self._parse_float(self.ticker_data.get("rolling_24_hour_volume"))
        self.high_24h = self._parse_float(self.ticker_data.get("rolling_24_hour_high"))
        self.low_24h = self._parse_float(self.ticker_data.get("rolling_24_hour_low"))
        self.timestamp = self._parse_int(self.ticker_data.get("timestamp"))

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

    @staticmethod
    def _parse_int(value):
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
