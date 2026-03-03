"""
Giottus Ticker Data Container
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class GiottusRequestTickerData(TickerData):
    """Giottus ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "GIOTTUS"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False
        # Initialize all attributes to None
        self.last_price = None
        self.bid_price = None
        self.ask_price = None
        self.volume_24h = None
        self.high_24h = None
        self.low_24h = None
        self.price_change_24h = None

    def init_data(self):
        """Parse Giottus ticker response."""
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
        self.price_change_24h = None

        data = self.ticker_data if isinstance(self.ticker_data, dict) else {}
        # Adapt to Giottus API response format when available
        ticker = data.get("data", data) if isinstance(data, dict) else {}

        if ticker:
            self.ticker_symbol_name = ticker.get("symbol") or self.symbol_name
            self.last_price = self._parse_float(ticker.get("last") or ticker.get("lastPrice"))
            self.bid_price = self._parse_float(ticker.get("bid") or ticker.get("buy"))
            self.ask_price = self._parse_float(ticker.get("ask") or ticker.get("sell"))
            self.volume_24h = self._parse_float(ticker.get("volume") or ticker.get("volume_24h"))
            self.high_24h = self._parse_float(ticker.get("high") or ticker.get("high_24h"))
            self.low_24h = self._parse_float(ticker.get("low") or ticker.get("low_24h"))
            self.price_change_24h = self._parse_float(
                ticker.get("price_change") or ticker.get("priceChange24h") or ticker.get("change")
            )

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

    def get_bid_volume(self):
        return None

    def get_ask_volume(self):
        return None

    def get_last_volume(self):
        return None

    def get_all_data(self):
        return {
            'exchange_name': self.exchange_name,
            'symbol_name': self.symbol_name,
            'ticker_symbol_name': self.ticker_symbol_name,
            'asset_type': self.asset_type,
            'last_price': self.last_price,
            'bid_price': self.bid_price,
            'ask_price': self.ask_price,
            'volume_24h': self.volume_24h,
            'high_24h': self.high_24h,
            'low_24h': self.low_24h,
            'price_change_24h': self.price_change_24h,
            'local_update_time': self.local_update_time,
        }

    def __str__(self):
        return f"GiottusTicker({self.symbol_name}: {self.last_price})"

    def __repr__(self):
        return self.__str__()
