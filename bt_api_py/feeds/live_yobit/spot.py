"""
YoBit Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.yobit_exchange_data import YobitExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_yobit.request_base import YobitRequestData


class YobitRequestDataSpot(YobitRequestData):
    """YoBit Spot Feed for market data."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "YOBIT___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data.

        YoBit uses lowercase format with underscore like btc_usdt
        """
        # Convert symbol to YoBit format (e.g., BTCUSDT -> btc_usdt)
        yobit_symbol = self._to_yobit_symbol(symbol)
        request_type = "get_tick"
        path = f"GET /api/3/ticker/{yobit_symbol}"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return self.request(path, params=None, extra_data=extra_data)

    def _to_yobit_symbol(self, symbol):
        """Convert symbol to YoBit format."""
        # YoBit uses lowercase with underscore: btc_usdt
        return symbol.lower().replace("/", "_")

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # YoBit returns data in format: {"btc_usdt": {...}}
        if isinstance(input_data, dict):
            # Get the first key's value
            for key, value in input_data.items():
                if isinstance(value, dict):
                    return [value], value is not None
        return [input_data], input_data is not None

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        return self._get_tick(symbol, extra_data, **kwargs)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth."""
        yobit_symbol = self._to_yobit_symbol(symbol)
        request_type = "get_depth"
        path = f"GET /api/3/depth/{yobit_symbol}"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        return self.request(path, params={"limit": count}, extra_data=extra_data)

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # YoBit returns data in format: {"btc_usdt": {...}}
        if isinstance(input_data, dict):
            for key, value in input_data.items():
                if isinstance(value, dict):
                    return [value], value is not None
        return [input_data], input_data is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_info(self, extra_data=None, **kwargs):
        """Get exchange info - all available pairs."""
        request_type = "get_info"
        path = "GET /api/3/info"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_info_normalize_function,
        })
        return self.request(path, params=None, extra_data=extra_data)

    @staticmethod
    def _get_info_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        info = input_data
        return [info], info is not None

    def get_info(self, extra_data=None, **kwargs):
        """Get exchange info."""
        return self._get_info(extra_data, **kwargs)
