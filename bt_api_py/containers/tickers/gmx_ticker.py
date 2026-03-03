"""
GMX Ticker Data Container

GMX is a decentralized perpetual exchange.
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class GmxRequestTickerData(TickerData):
    """GMX ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.ticker_symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "GMX"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.has_been_init_data = False

    def init_data(self):
        """Parse GMX ticker response.

        GMX ticker response format:
        {
            "BTC": {
                "minPrice": "50000.00",
                "maxPrice": "51000.00",
                ...
            },
            ...
        }
        """
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data if isinstance(self.ticker_data, dict) else {}

        # GMX returns a dict of tokens with their price data
        if data and isinstance(data, dict):
            # If symbol_name is provided, try to get that specific token's data
            if self.ticker_symbol_name and self.ticker_symbol_name in data:
                ticker = data[self.ticker_symbol_name]
            else:
                # Use the first token's data if no symbol match
                ticker = next(iter(data.values())) if data else {}

            if isinstance(ticker, dict):
                self.ticker_symbol_name = self.ticker_symbol_name or ticker.get("symbol")
                self.last_price = self._parse_float(
                    ticker.get("minPrice") or ticker.get("maxPrice") or ticker.get("price")
                )
                self.bid_price = self._parse_float(ticker.get("minPrice"))
                self.ask_price = self._parse_float(ticker.get("maxPrice"))
                self.high_24h = self._parse_float(ticker.get("maxPrice"))
                self.low_24h = self._parse_float(ticker.get("minPrice"))

        self.has_been_init_data = True
        return self

    # ── Standard getters ────────────────────────────────────────

    def get_symbol_name(self):
        return self.ticker_symbol_name

    def get_ticker_symbol_name(self):
        return self.ticker_symbol_name

    def get_last_price(self):
        return getattr(self, "last_price", None)

    def get_exchange_name(self):
        return self.exchange_name

    def get_local_update_time(self):
        return self.local_update_time

    def get_asset_type(self):
        return self.asset_type

    def get_all_data(self):
        return self.ticker_data

    def get_server_time(self):
        return self.local_update_time

    def get_bid_price(self):
        return getattr(self, "bid_price", None)

    def get_ask_price(self):
        return getattr(self, "ask_price", None)

    @staticmethod
    def _parse_float(value):
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
