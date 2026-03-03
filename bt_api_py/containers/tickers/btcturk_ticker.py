"""
BTCTurk Ticker Data Container
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class BTCTurkRequestTickerData(TickerData):
    """BTCTurk ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BTCTURK"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False
        self.last_price = None
        self.bid_price = None
        self.ask_price = None
        self.volume_24h = None
        self.high_24h = None
        self.low_24h = None
        self.timestamp = None

    def init_data(self):
        """Parse BTCTurk ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Initialize all attributes to None
        self.ticker_symbol_name = None
        self.last_price = None
        self.bid_price = None
        self.ask_price = None
        self.volume_24h = None
        self.high_24h = None
        self.low_24h = None
        self.timestamp = None

        data = self.ticker_data.get("data", {})
        # BTCTurk returns either an array or single object
        if isinstance(data, list) and len(data) > 0:
            ticker = data[0]
        elif isinstance(data, dict):
            ticker = data
        else:
            ticker = {}

        if ticker:
            self.ticker_symbol_name = ticker.get("pairSymbol")
            self.last_price = self._parse_float(ticker.get("last"))
            self.bid_price = self._parse_float(ticker.get("bid"))
            self.ask_price = self._parse_float(ticker.get("ask"))
            self.volume_24h = self._parse_float(ticker.get("volume"))
            self.high_24h = self._parse_float(ticker.get("high"))
            self.low_24h = self._parse_float(ticker.get("low"))
            self.timestamp = self._parse_int(ticker.get("timestamp"))

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

    def get_exchange_name(self):
        return self.exchange_name

    def get_local_update_time(self):
        return self.local_update_time

    def get_symbol_name(self):
        return self.symbol_name

    def get_ticker_symbol_name(self):
        return self.ticker_symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_server_time(self):
        return getattr(self, 'timestamp', None)

    def get_bid_price(self):
        return self.bid_price

    def get_ask_price(self):
        return self.ask_price

    def get_last_price(self):
        return self.last_price
