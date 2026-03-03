"""
Bitbns Ticker Data Container
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class BitbnsRequestTickerData(TickerData):
    """Bitbns ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BITBNS"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False

    def init_data(self):
        """Parse Bitbns ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data.get("data", {})

        # Handle nested data structure - symbol is the key
        if data and isinstance(data, dict):
            # Find ticker for our symbol
            symbol = self.symbol_name.upper()

            # Try exact match first
            if symbol in data:
                ticker_data = data[symbol]
            elif "_USDT" in symbol:
                base = symbol.replace("_USDT", "")
                ticker_data = data.get(base, {})
            elif "_INR" in symbol:
                base = symbol.replace("_INR", "")
                ticker_data = data.get(base, {})
            else:
                ticker_data = data.get(symbol, {})

            if ticker_data:
                self.ticker_symbol_name = symbol
                self.last_price = self._parse_float(ticker_data.get("last_traded_price"))
                self.bid_price = self._parse_float(ticker_data.get("highest_buy_bid"))
                self.ask_price = self._parse_float(ticker_data.get("lowest_sell_bid"))

                # Volume data is nested
                volume_info = ticker_data.get("volume", {})
                if isinstance(volume_info, dict):
                    self.volume_24h = self._parse_float(volume_info.get("volume"))
                else:
                    self.volume_24h = self._parse_float(volume_info)

                # High/Low from volume info
                if isinstance(volume_info, dict):
                    self.high_24h = self._parse_float(volume_info.get("max"))
                    self.low_24h = self._parse_float(volume_info.get("min"))

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
