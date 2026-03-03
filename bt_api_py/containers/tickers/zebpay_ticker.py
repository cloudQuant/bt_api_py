"""
Zebpay Ticker Data Container
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class ZebpayRequestTickerData(TickerData):
    """Zebpay ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "ZEBPAY"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False

    def init_data(self):
        """Parse Zebpay ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data.get("data", {})
        if data:
            self.ticker_symbol_name = data.get("symbol")
            self.last_price = self._parse_float(data.get("last"))
            self.bid_price = self._parse_float(data.get("bid"))
            self.ask_price = self._parse_float(data.get("ask"))
            self.high_24h = self._parse_float(data.get("high"))
            self.low_24h = self._parse_float(data.get("low"))
            self.volume_24h = self._parse_float(data.get("volume"))
            self.quote_volume = self._parse_float(data.get("quoteVolume"))

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
