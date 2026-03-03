"""
Bithumb Ticker Data Container
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class BithumbRequestTickerData(TickerData):
    """Bithumb ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BITHUMB"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.last_price = None
        self.high_24h = None
        self.low_24h = None
        self.volume_24h = None
        self.price_change_percent_24h = None
        self.has_been_init_data = False

    def init_data(self):
        """Parse Bithumb ticker response.

        Bithumb ticker format:
        {
          "s": "BTC-USDT",  # symbol
          "c": "50000",      # last price (close)
          "h": "51000",      # 24h high
          "l": "49000",      # 24h low
          "v": "1234.56",    # 24h volume
          "p": "2.5"         # 24h change percent
        }
        """
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data
        if data:
            self.ticker_symbol_name = data.get("s")
            self.last_price = self._parse_float(data.get("c"))
            self.high_24h = self._parse_float(data.get("h"))
            self.low_24h = self._parse_float(data.get("l"))
            self.volume_24h = self._parse_float(data.get("v"))
            self.price_change_percent_24h = self._parse_float(data.get("p"))

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

    def get_exchange_name(self):
        return self.exchange_name
