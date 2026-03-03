"""
Bitrue Ticker Data Container
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class BitrueRequestTickerData(TickerData):
    """Bitrue ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BITRUE"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.server_time = None
        self.bid_price = None
        self.ask_price = None
        self.bid_volume = None
        self.ask_volume = None
        self.last_price = None
        self.last_volume = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """Parse Bitrue ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data
        if data:
            # Bitrue 24hr ticker format
            self.ticker_symbol_name = data.get("symbol")
            self.server_time = data.get("timestamp")
            self.last_price = self._parse_float(data.get("lastPrice") or data.get("price"))
            self.bid_price = self._parse_float(data.get("bidPrice") or data.get("bid"))
            self.ask_price = self._parse_float(data.get("askPrice") or data.get("ask"))
            self.bid_volume = self._parse_float(data.get("bidQty"))
            self.ask_volume = self._parse_float(data.get("askQty"))
            self.last_volume = self._parse_float(data.get("volume"))

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

    def get_all_data(self):
        """Get all ticker data as dictionary."""
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "ticker_symbol_name": self.ticker_symbol_name,
                "server_time": self.server_time,
                "bid_price": self.bid_price,
                "ask_price": self.ask_price,
                "bid_volume": self.bid_volume,
                "ask_volume": self.ask_volume,
                "last_price": self.last_price,
                "last_volume": self.last_volume,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        """Get exchange name."""
        return self.exchange_name

    def get_local_update_time(self):
        """Get local update time."""
        return self.local_update_time

    def get_symbol_name(self):
        """Get symbol name."""
        return self.symbol_name

    def get_ticker_symbol_name(self):
        """Get ticker symbol name from response."""
        return self.ticker_symbol_name

    def get_asset_type(self):
        """Get asset type."""
        return self.asset_type

    def get_server_time(self):
        """Get server time."""
        return self.server_time

    def get_bid_price(self):
        """Get bid price."""
        return self.bid_price

    def get_ask_price(self):
        """Get ask price."""
        return self.ask_price

    def get_bid_volume(self):
        """Get bid volume."""
        return self.bid_volume

    def get_ask_volume(self):
        """Get ask volume."""
        return self.ask_volume

    def get_last_price(self):
        """Get last price."""
        return self.last_price

    def get_last_volume(self):
        """Get last volume."""
        return self.last_volume
