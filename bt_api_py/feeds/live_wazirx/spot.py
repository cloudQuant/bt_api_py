"""
WazirX Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.wazirx_exchange_data import WazirxExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_wazirx.request_base import WazirxRequestData


class WazirxRequestDataSpot(WazirxRequestData):
    """WazirX Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "WAZIRX___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data."""
        request_type = "get_tick"
        path = f"GET /sapi/v1/ticker/24hr"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return self.request(path, params={"symbol": symbol}, extra_data=extra_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        ticker = input_data
        return [ticker], ticker is not None

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        return self._get_tick(symbol, extra_data, **kwargs)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth."""
        request_type = "get_depth"
        path = f"GET /sapi/v1/depth"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        return self.request(path, params={"symbol": symbol, "limit": count}, extra_data=extra_data)

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        depth = input_data
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data."""
        request_type = "get_kline"
        path = f"GET /sapi/v1/klines"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        return self.request(path, params={
            "symbol": symbol,
            "interval": self._params.get_period(period),
            "limit": count,
        }, extra_data=extra_data)

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        klines = input_data
        return [klines], klines is not None

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        return self._get_kline(symbol, period, count, extra_data, **kwargs)
