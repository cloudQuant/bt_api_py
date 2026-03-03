"""
bitFlyer Ticker Data Container
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class BitflyerRequestTickerData(TickerData):
    """bitFlyer ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BITFLYER"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.last_price = None
        self.bid_price = None
        self.ask_price = None
        self.volume_24h = None
        self.volume_quote_24h = None
        self.bid_size = None
        self.ask_size = None
        self.total_bid_depth = None
        self.total_ask_depth = None
        self.timestamp = None
        self.has_been_init_data = False

    def init_data(self):
        """Parse bitFlyer ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # bitFlyer ticker response format
        if isinstance(self.ticker_data, dict):
            data = self.ticker_data
            if "product_code" in data:
                self.ticker_symbol_name = data.get("product_code")
                self.last_price = self._parse_float(data.get("ltp"))  # Last traded price
                self.bid_price = self._parse_float(data.get("best_bid"))
                self.ask_price = self._parse_float(data.get("best_ask"))
                self.volume_24h = self._parse_float(data.get("volume"))
                self.volume_quote_24h = self._parse_float(data.get("volume_by_product"))
                self.bid_size = self._parse_float(data.get("best_bid_size"))
                self.ask_size = self._parse_float(data.get("best_ask_size"))
                self.total_bid_depth = self._parse_float(data.get("total_bid_depth"))
                self.total_ask_depth = self._parse_float(data.get("total_ask_depth"))

                # Parse timestamp
                timestamp_str = data.get("timestamp")
                if timestamp_str:
                    self.timestamp = self._parse_timestamp(timestamp_str)

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
    def _parse_timestamp(timestamp_str):
        """Parse ISO 8601 timestamp to unix timestamp in milliseconds."""
        if not timestamp_str:
            return None
        try:
            from datetime import datetime
            # bitFlyer returns ISO format like "2024-01-01T00:00:00.000"
            dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            return None

    def get_exchange_name(self):
        """Get exchange name."""
        return self.exchange_name

    def get_symbol_name(self):
        """Get symbol name."""
        return self.symbol_name

    def get_asset_type(self):
        """Get asset type."""
        return self.asset_type

    def get_last_price(self):
        """Get last price."""
        self.init_data()
        return self.last_price

    def get_bid_price(self):
        """Get bid price."""
        self.init_data()
        return self.bid_price

    def get_ask_price(self):
        """Get ask price."""
        self.init_data()
        return self.ask_price
