"""
Mercado Bitcoin Ticker Data Container
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class MercadoBitcoinRequestTickerData(TickerData):
    """Mercado Bitcoin ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "MERCADO_BITCOIN"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False

    def init_data(self):
        """Parse Mercado Bitcoin ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Mercado Bitcoin ticker response structure
        # Response format: {"ticker": {"last": "...", "buy": "...", "sell": "...", ...}}
        ticker = (
            self.ticker_data.get("ticker", {})
            if isinstance(self.ticker_data, dict)
            else self.ticker_data
        )

        self.ticker_symbol_name = self.symbol_name
        self.last_price = self._parse_float(ticker.get("last"))
        self.bid_price = self._parse_float(ticker.get("buy"))
        self.ask_price = self._parse_float(ticker.get("sell"))
        self.high_24h = self._parse_float(ticker.get("high"))
        self.low_24h = self._parse_float(ticker.get("low"))
        self.volume_24h = self._parse_float(ticker.get("vol"))
        self.timestamp = self._parse_int(ticker.get("date"))

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
