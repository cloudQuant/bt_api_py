"""
BingX Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.bingx_exchange_data import BingXExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bingx.request_base import BingXRequestData


class BingXRequestDataSpot(BingXRequestData):
    """BingX Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "BINGX___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data."""
        request_type = "get_tick"
        path = f"GET /openApi/spot/v1/ticker/24hr"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        # BingX uses BTC-USDT format
        return self.request(path, params={"symbol": symbol}, extra_data=extra_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # BingX wraps data in "data" field as a list
        data_list = input_data.get("data", [])
        if isinstance(data_list, list) and len(data_list) > 0:
            return [data_list[0]], True
        return [data_list] if data_list else [], False

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        return self._get_tick(symbol, extra_data, **kwargs)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth."""
        request_type = "get_depth"
        path = f"GET /openApi/spot/v1/market/depth"
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
        depth = input_data.get("data", {})
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data."""
        request_type = "get_kline"
        path = f"GET /openApi/spot/v2/market/kline"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        bingx_period = self._params.kline_periods.get(period, period)
        return self.request(path, params={
            "symbol": symbol,
            "interval": bingx_period,
            "limit": count,
        }, extra_data=extra_data)

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        klines = input_data.get("data", [])
        return [klines], klines is not None

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        return self._get_kline(symbol, period, count, extra_data, **kwargs)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Async get ticker - not implemented for BingX."""
        pass

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Async get orderbook - not implemented for BingX."""
        pass

    def async_get_kline(self, symbol, period="1m", count=20, extra_data=None, **kwargs):
        """Async get kline - not implemented for BingX."""
        pass
