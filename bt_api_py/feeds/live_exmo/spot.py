"""
EXMO Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.exmo_exchange_data import ExmoExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_exmo.request_base import ExmoRequestData


class ExmoRequestDataSpot(ExmoRequestData):
    """EXMO Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "EXMO___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data.

        EXMO uses underscore format for symbols (e.g., BTC_USDT).

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_ticker"
        path = f"GET /ticker"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        # EXMO returns all tickers, we filter by symbol
        return path, None, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data from EXMO response.

        EXMO returns all tickers in one response, we extract the requested symbol.
        """
        if not input_data:
            return [], False

        symbol_name = extra_data.get("symbol_name", "")

        # Get all tickers from input (response can be dict directly or wrapped in "data")
        all_tickers = input_data.get("data", input_data) if isinstance(input_data, dict) else input_data

        if not isinstance(all_tickers, dict):
            return [], False

        # Try different symbol formats
        # EXMO uses underscore format (BTC_USDT), but input might use slash (BTC/USDT)
        possible_keys = [symbol_name]  # Try original first

        # Convert to EXMO format (underscore)
        if "_" not in symbol_name:
            possible_keys.append(symbol_name.replace("/", "_"))
        elif "/" in symbol_name:
            possible_keys.append(symbol_name.replace("/", "_"))

        # Try each possible key
        for key in possible_keys:
            if key in all_tickers:
                ticker = all_tickers[key]
                ticker["symbol"] = symbol_name  # Keep original symbol format
                return [ticker], True

        # If symbol not found, return empty list but still True for successful API call
        # The API call succeeded, just the specific symbol wasn't available
        return [], len(all_tickers) > 0

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_depth"
        path = f"GET /order_book"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        # Convert symbol format for EXMO
        exmo_symbol = symbol.replace("/", "_")
        params = {"pair": exmo_symbol, "limit": count}
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize orderbook data."""
        if not input_data:
            return [], False

        symbol_name = extra_data.get("symbol_name", "")
        exmo_symbol = symbol_name.replace("/", "_")

        # Get orderbook data from input
        all_books = input_data.get("data", input_data) if isinstance(input_data, dict) else input_data

        if isinstance(all_books, dict) and exmo_symbol in all_books:
            depth = all_books[exmo_symbol]
            depth["symbol"] = symbol_name
            return [depth], True

        # Empty response but successful API call
        if isinstance(all_books, dict) and not all_books:
            return [], True

        return [], False

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_kline"
        path = f"GET /candles_history"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })

        import time as tm
        now = int(tm.time())
        # Default to 24h of data if count not specified
        to_ts = kwargs.get("to", now)
        from_ts = kwargs.get("from", now - 86400)

        # Convert period to EXMO format
        period_map = {
            "1m": "1", "5m": "5", "15m": "15", "30m": "30",
            "1h": "60", "2h": "120", "4h": "240",
            "1d": "D", "1w": "W", "1M": "M"
        }
        resolution = period_map.get(period, "60")

        # Convert symbol format
        exmo_symbol = symbol.replace("/", "_")

        params = {
            "symbol": exmo_symbol,
            "resolution": resolution,
            "from": from_ts,
            "to": to_ts,
        }
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline data."""
        if not input_data:
            return [], False

        # Handle different response formats
        if isinstance(input_data, dict):
            data = input_data.get("data", input_data)
            if isinstance(data, dict):
                candles = data.get("candles", [])
                if candles:
                    return [candles], True
            elif isinstance(data, list):
                return [data], len(data) > 0

        # Direct list response
        if isinstance(input_data, list):
            return [input_data], len(input_data) > 0

        return [], False

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)
