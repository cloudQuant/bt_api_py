"""
Coincheck Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.coincheck_exchange_data import CoincheckExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_coincheck.request_base import CoincheckRequestData


class CoincheckRequestDataSpot(CoincheckRequestData):
    """Coincheck Spot Feed for market data."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "COINCHECK___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data - prepares request parameters."""
        request_type = "get_tick"
        path = f"GET /api/ticker"
        params = None  # Coincheck ticker endpoint doesn't require parameters
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data and create ticker objects."""
        from bt_api_py.containers.tickers.coincheck_ticker import CoincheckRequestTickerData

        if not input_data:
            return [], False

        symbol_name = extra_data.get("symbol_name", "")
        asset_type = extra_data.get("asset_type", "SPOT")

        # input_data is already a dict (parsed by HTTP client)
        # So we pass has_been_json_encoded=True to skip json.loads
        if isinstance(input_data, dict):
            ticker = CoincheckRequestTickerData(input_data, symbol_name, asset_type, has_been_json_encoded=True)
        else:
            # If it's a string, parse it as JSON
            import json
            input_data = json.loads(input_data) if isinstance(input_data, str) else input_data
            ticker = CoincheckRequestTickerData(input_data, symbol_name, asset_type, has_been_json_encoded=True)
        return [ticker], True

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth - prepares request parameters."""
        request_type = "get_depth"
        path = f"GET /api/order_books"
        params = None  # Coincheck order book endpoint doesn't require parameters
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
