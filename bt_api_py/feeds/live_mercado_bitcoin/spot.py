"""
Mercado Bitcoin Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.mercado_bitcoin_exchange_data import MercadoBitcoinExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_mercado_bitcoin.request_base import MercadoBitcoinRequestData


class MercadoBitcoinRequestDataSpot(MercadoBitcoinRequestData):
    """Mercado Bitcoin Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "MERCADO_BITCOIN___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data.

        Mercado Bitcoin uses coin-based ticker endpoints like /BTC/ticker/

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_tick"
        # Extract coin from symbol (e.g., BTC-BRL -> BTC)
        coin = symbol.split("-")[0] if "-" in symbol else symbol
        path = f"GET /{coin}/ticker/"
        params = None
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        ticker = input_data.get("ticker", {})
        return [ticker], ticker is not None

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
        # Extract coin from symbol (e.g., BTC-BRL -> BTC)
        coin = symbol.split("-")[0] if "-" in symbol else symbol
        path = f"GET /{coin}/orderbook/"
        params = None
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        depth = input_data
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data using V4 API.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_kline"
        path = f"GET /candles"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })

        import time as t
        to_time = int(t.time())
        from_time = to_time - 86400  # Last 24 hours default

        resolution = self._params.get_period(period, "1d")

        params = {
            "symbol": symbol,
            "resolution": resolution,
            "from": from_time,
            "to": to_time,
        }
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        klines = input_data if isinstance(input_data, list) else input_data.get("candles", [])
        return [klines], klines is not None

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker asynchronously."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self._async_request(path, params, extra_data=extra_data)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book asynchronously."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self._async_request(path, params, extra_data=extra_data)

    def async_get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data asynchronously."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self._async_request(path, params, extra_data=extra_data)

    def _async_request(self, path, params=None, extra_data=None, **kwargs):
        """Internal async request method."""
        import threading
        import time

        def run_request():
            try:
                result = self.request(path, params, extra_data=extra_data, **kwargs)
                self.push_data_to_queue(result)
            except Exception as e:
                self.request_logger.error(f"Async request failed: {e}")

        thread = threading.Thread(target=run_request)
        thread.daemon = True
        thread.start()
