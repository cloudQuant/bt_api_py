"""
Foxbit Spot Feed implementation.
"""

import json

from bt_api_py.containers.exchanges.foxbit_exchange_data import FoxbitExchangeDataSpot
from bt_api_py.containers.tickers.foxbit_ticker import FoxbitRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_foxbit.request_base import FoxbitRequestData


class FoxbitRequestDataSpot(FoxbitRequestData):
    """Foxbit Spot Feed for market data."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "FOXBIT___SPOT")

    def _format_market(self, symbol):
        """Convert symbol to Foxbit market format.

        Foxbit uses lowercase format like 'btcbrl'.
        """
        return symbol.replace("/", "").replace("-", "").lower()

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data - returns (path, params, extra_data) tuple."""
        request_type = "get_ticker"
        market = self._format_market(symbol)
        path = f"GET /rest/v3/markets/{market}/ticker/24hr"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return path, None, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data."""
        if not input_data:
            return None, False
        ticker = input_data.get("data", input_data)
        # Foxbit API returns {"data": [...]} format
        if isinstance(ticker, list) and len(ticker) > 0:
            ticker = ticker[0]

        if isinstance(ticker, dict):
            symbol_name = extra_data.get("symbol_name", "")
            asset_type = extra_data.get("asset_type", "SPOT")
            # Create ticker container with JSON string
            ticker_json = json.dumps({"data": ticker})
            ticker_container = FoxbitRequestTickerData(ticker_json, symbol_name, asset_type, True)
            return ticker_container, True
        return None, False

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_all_tickers(self, extra_data=None, **kwargs):
        """Get all tickers - returns (path, params, extra_data) tuple."""
        request_type = "get_all_tickers"
        path = f"GET /rest/v3/markets/ticker/24hr"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_all_tickers_normalize_function,
        })
        return path, None, extra_data

    @staticmethod
    def _get_all_tickers_normalize_function(input_data, extra_data):
        """Normalize all tickers data."""
        if not input_data:
            return [], False
        tickers = input_data.get("data", input_data)
        if isinstance(tickers, list):
            return [tickers], True
        return [], False

    def get_all_tickers(self, extra_data=None, **kwargs):
        """Get all symbol tickers."""
        path, params, extra_data = self._get_all_tickers(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth - returns (path, params, extra_data) tuple."""
        request_type = "get_depth"
        market = self._format_market(symbol)
        path = f"GET /rest/v3/markets/{market}/orderbook"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        # Foxbit API doesn't accept limit parameter for orderbook
        return path, None, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize orderbook data."""
        if not input_data:
            return [], False
        depth = input_data.get("data", input_data)
        if isinstance(depth, dict):
            return [depth], True
        return [], False

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data - returns (path, params, extra_data) tuple."""
        request_type = "get_kline"
        market = self._format_market(symbol)
        path = f"GET /rest/v3/markets/{market}/candlesticks"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })

        # Convert period to Foxbit format
        period_map = {
            "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
            "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "12h": "12h",
            "1d": "1d", "1w": "1w", "2w": "2w", "1M": "1M"
        }
        interval = period_map.get(period, "1h")

        return path, {"interval": interval, "limit": count}, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline data."""
        if not input_data:
            return [], False
        data = input_data.get("data", input_data)
        if isinstance(data, list):
            return [data], True
        return [], False

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_trades(self, symbol, count=20, extra_data=None, **kwargs):
        """Get recent trades - returns (path, params, extra_data) tuple."""
        request_type = "get_trades"
        market = self._format_market(symbol)
        path = f"GET /rest/v3/markets/{market}/trades/history"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_trades_normalize_function,
        })
        return path, {"limit": count}, extra_data

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize trades data."""
        if not input_data:
            return [], False
        data = input_data.get("data", input_data)
        if isinstance(data, list):
            return [data], True
        return [], False

    def get_trades(self, symbol, count=20, extra_data=None, **kwargs):
        """Get recent trades."""
        path, params, extra_data = self._get_trades(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)
