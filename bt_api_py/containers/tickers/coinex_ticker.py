"""
CoinEx Ticker Data Container
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class CoinExRequestTickerData(TickerData):
    """CoinEx ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "COINEX"
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
        self.volume_24h = None
        self.high_24h = None
        self.low_24h = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """Parse CoinEx ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data.get("data", {})
        if data:
            self.ticker_symbol_name = data.get("market")
            self.last_price = self._parse_float(data.get("last"))
            self.bid_price = self._parse_float(data.get("bid"))
            self.ask_price = self._parse_float(data.get("ask"))
            self.volume_24h = self._parse_float(data.get("volume_24h"))
            self.high_24h = self._parse_float(data.get("high_24h"))
            self.low_24h = self._parse_float(data.get("low_24h"))

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        """Get all ticker data as a dictionary."""
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
                "volume_24h": self.volume_24h,
                "high_24h": self.high_24h,
                "low_24h": self.low_24h,
            }
        return self.all_data

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
        """Get ticker symbol name from exchange response."""
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

    def __str__(self):
        """String representation."""
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        """Repr representation."""
        return self.__str__()

    @staticmethod
    def _parse_float(value):
        """Parse float value safely."""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
