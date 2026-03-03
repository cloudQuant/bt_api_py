"""
Bitrue Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.bitrue_exchange_data import BitrueExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitrue.request_base import BitrueRequestData


class BitrueRequestDataSpot(BitrueRequestData):
    """Bitrue Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITRUE___SPOT")
        self._params = BitrueExchangeDataSpot()
        self.rest_url = self._params.rest_url

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data."""
        request_type = "get_tick"
        path = self._params.get_rest_path(request_type)
        request_symbol = self._params.get_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return self.request(path, params={"symbol": request_symbol}, extra_data=extra_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # Bitrue returns ticker object directly or in array
        if isinstance(input_data, list) and len(input_data) > 0:
            ticker = input_data[0]
        elif isinstance(input_data, dict):
            ticker = input_data
        else:
            return [], False
        return [ticker], ticker is not None

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        return self._get_tick(symbol, extra_data, **kwargs)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth."""
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type)
        request_symbol = self._params.get_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        return self.request(path, params={"symbol": request_symbol, "limit": count}, extra_data=extra_data)

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
        path = self._params.get_rest_path(request_type)
        request_symbol = self._params.get_symbol(symbol)
        request_period = self._params.get_period(period)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        return self.request(path, params={
            "symbol": request_symbol,
            "scale": request_period,
            "limit": count,
        }, extra_data=extra_data)

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        klines = input_data if isinstance(input_data, list) else []
        return [klines], klines is not None

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        return self._get_kline(symbol, period, count, extra_data, **kwargs)

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange trading rules."""
        request_type = "get_contract"
        path = self._params.get_rest_path(request_type)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": "ALL",
            "normalize_function": self._get_exchange_info_normalize_function,
        })
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        info = input_data
        return [info], info is not None

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange info."""
        return self._get_exchange_info(extra_data, **kwargs)
