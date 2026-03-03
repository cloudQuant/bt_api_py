"""
BingX Ticker Data Container
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class BingXRequestTickerData(TickerData):
    """BingX ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BINGX"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False

    def init_data(self):
        """Parse BingX ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # BingX ticker format - data is wrapped in "data" field as a list
        data_list = self.ticker_data.get("data", [])
        data = data_list[0] if isinstance(data_list, list) and len(data_list) > 0 else {}

        if data:
            self.ticker_symbol_name = data.get("symbol")
            self.last_price = self._parse_float(data.get("lastPrice"))
            # BingX doesn't provide bid/ask in 24hr ticker
            self.bid_price = self._parse_float(data.get("bidPrice"))
            self.ask_price = self._parse_float(data.get("askPrice"))
            self.volume_24h = self._parse_float(data.get("volume"))
            self.high_24h = self._parse_float(data.get("highPrice"))
            self.low_24h = self._parse_float(data.get("lowPrice"))
            self.open_24h = self._parse_float(data.get("openPrice"))

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
