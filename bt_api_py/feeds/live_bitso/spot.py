"""
Bitso Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.bitso_exchange_data import BitsoExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitso.request_base import BitsoRequestData


class BitsoRequestDataSpot(BitsoRequestData):
    """Bitso Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITSO___SPOT")
        self._params = BitsoExchangeDataSpot()
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
        return self.request(path, params={"book": request_symbol}, extra_data=extra_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # Bitso returns {success: true, payload: ticker}
        if isinstance(input_data, dict):
            payload = input_data.get("payload", {})
            return [payload], payload is not None
        return [], False

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
        return self.request(path, params={"book": request_symbol, "aggregate": True}, extra_data=extra_data)

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # Bitso returns {success: true, payload: {bids: [], asks: []}}
        if isinstance(input_data, dict):
            payload = input_data.get("payload", {})
            return [payload], payload is not None
        return [], False

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get OHLC/kline data."""
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
        # Bitso OHLC API uses 'time_bucket' parameter (in seconds)
        return self.request(path, params={
            "book": request_symbol,
            "time_bucket": request_period,
        }, extra_data=extra_data)

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # Bitso returns {success: true, "payload": [...]}
        # where payload is a list of OHLC objects
        if isinstance(input_data, dict):
            payload = input_data.get("payload", [])
            # payload is already a list of OHLC candles
            return [payload], payload is not None and len(payload) > 0
        return [], False

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        return self._get_kline(symbol, period, count, extra_data, **kwargs)

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get available trading pairs."""
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
        # Bitso returns {success: true, payload: [books]}
        if isinstance(input_data, dict):
            payload = input_data.get("payload", [])
            return [payload], payload is not None
        return [], False

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange info."""
        return self._get_exchange_info(extra_data, **kwargs)
