"""
BigONE Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.bigone_exchange_data import BigONEExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bigone.request_base import BigONERequestData


class BigONERequestDataSpot(BigONERequestData):
    """BigONE Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "BIGONE___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data."""
        request_type = "get_tick"
        path = f"GET /asset_pairs/{{symbol}}/ticker"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return path, {"symbol": symbol}, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # BigONE wraps data in "data" field
        ticker = input_data.get("data", {})
        return [ticker], ticker is not None

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker asynchronously."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth."""
        request_type = "get_depth"
        path = f"GET /asset_pairs/{{symbol}}/depth"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        return path, {"symbol": symbol, "limit": count}, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        depth = input_data.get("data", {})
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book asynchronously."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data."""
        request_type = "get_kline"
        path = f"GET /asset_pairs/{{symbol}}/candles"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        bigone_period = self._params.kline_periods.get(period, period)
        return path, {
            "symbol": symbol,
            "period": bigone_period,
            "limit": count,
        }, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        klines = input_data.get("data", [])
        return [klines], klines is not None

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def async_get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data asynchronously."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params, extra_data=extra_data),
            callback=self.async_callback,
        )
