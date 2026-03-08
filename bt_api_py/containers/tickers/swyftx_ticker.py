"""
Swyftx Ticker Data Container
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class SwyftxRequestTickerData(TickerData):
    """Swyftx ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "SWYFTX"
        self.local_update_time = time.time()
        self.has_been_init_data = False
        self.ticker_data = ticker_info if has_been_json_encoded else None

    def init_data(self):
        """Parse Swyftx ticker response."""
        if not self.has_been_json_encoded:
            if isinstance(self.ticker_info, str):
                self.ticker_data = json.loads(self.ticker_info)
            else:
                self.ticker_data = self.ticker_info
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data if isinstance(self.ticker_data, dict) else {}
        if data:
            self.ticker_symbol_name = (
                data.get("symbol") or data.get("market") or data.get("instrument")
            )
            self.last_price = self._parse_float(
                data.get("last") or data.get("lastPrice") or data.get("price")
            )
            self.bid_price = self._parse_float(data.get("bid"))
            self.ask_price = self._parse_float(data.get("ask"))
            self.volume_24h = self._parse_float(data.get("volume") or data.get("volume_24h"))
            self.high_24h = self._parse_float(data.get("high") or data.get("high_24h"))
            self.low_24h = self._parse_float(data.get("low") or data.get("low_24h"))

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
