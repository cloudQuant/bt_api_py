"""
Bitbank Spot Feed implementation.
"""

import re
from datetime import datetime

from bt_api_py.containers.exchanges.bitbank_exchange_data import BitbankExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitbank.request_base import BitbankRequestData


class BitbankRequestDataSpot(BitbankRequestData):
    """Bitbank Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITBANK___SPOT")

    def _normalize_pair(self, symbol):
        """Normalize symbol to bitbank format (e.g., btc_jpy)."""
        # Convert common formats to bitbank format
        symbol = symbol.upper().replace("/", "_").replace("-", "_")
        return symbol.lower()

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data."""
        request_type = "get_tick"
        pair = self._normalize_pair(symbol)
        path = f"GET /{pair}/ticker"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return self.request(path, params=None, extra_data=extra_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("success") == 1:
            return [data], True
        return [], False

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        return self._get_tick(symbol, extra_data, **kwargs)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth."""
        request_type = "get_depth"
        pair = self._normalize_pair(symbol)
        path = f"GET /{pair}/depth"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        return self.request(path, params=None, extra_data=extra_data)

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth data."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("success") == 1:
            return [data], True
        return [], False

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data."""
        request_type = "get_kline"
        pair = self._normalize_pair(symbol)

        # Get period from kline_periods config
        kline_periods = getattr(self._params, 'kline_periods', {})
        candle_type = kline_periods.get(period, period)

        # Determine date format based on candle type
        # For short periods (1min-1hour): YYYYMMDD
        # For long periods (4hour-1month): YYYY
        long_periods = ["4hour", "8hour", "12hour", "1day", "1week", "1month"]
        if candle_type in long_periods:
            date_str = datetime.now().strftime("%Y")
        else:
            date_str = datetime.now().strftime("%Y%m%d")

        path = f"GET /{pair}/candlestick/{candle_type}/{date_str}"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "period": period,
            "normalize_function": self._get_kline_normalize_function,
        })
        return self.request(path, params=None, extra_data=extra_data)

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline data."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("success") == 1:
            candlestick = data.get("candlestick", [])
            if candlestick and len(candlestick) > 0:
                ohlcv = candlestick[0].get("ohlcv", [])
                if ohlcv:
                    # Format: [open, high, low, close, volume, timestamp]
                    return [{"ohlcv": ohlcv}], True
        return [], False

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        return self._get_kline(symbol, period, count, extra_data, **kwargs)

    def _get_trades(self, symbol, limit=60, extra_data=None, **kwargs):
        """Get recent trades."""
        request_type = "get_trades"
        pair = self._normalize_pair(symbol)
        path = f"GET /{pair}/transactions"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_trades_normalize_function,
        })
        return self.request(path, params=None, extra_data=extra_data)

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize trades data."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("success") == 1:
            transactions = data.get("transactions", [])
            return [transactions], True
        return [], False

    def get_trades(self, symbol, limit=60, extra_data=None, **kwargs):
        """Get recent trades."""
        return self._get_trades(symbol, limit, extra_data, **kwargs)
