"""
CoinSwitch Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.coinswitch_exchange_data import CoinSwitchExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_coinswitch.request_base import CoinSwitchRequestData


class CoinSwitchRequestDataSpot(CoinSwitchRequestData):
    """CoinSwitch Spot Feed for market data."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "COINSWITCH___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data for a specific trading pair.

        Returns path, params, extra_data tuple for the request.
        """
        request_type = "get_tick"
        # CoinSwitch uses symbol in path parameter
        path = f"GET /v2/tickers/{symbol}"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return path, None, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data from CoinSwitch API response."""
        if not input_data:
            return [], False
        # CoinSwitch API response structure varies
        data = input_data.get("data", input_data)
        return [data], data is not None

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker.

        Makes the actual HTTP request to the API.
        """
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_all_tickers(self, extra_data=None, **kwargs):
        """Get all ticker data.

        Returns path, params, extra_data tuple for the request.
        """
        request_type = "get_ticker_all"
        path = "GET /v2/tickers"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_all_tickers_normalize_function,
        })
        return path, None, extra_data

    @staticmethod
    def _get_all_tickers_normalize_function(input_data, extra_data):
        """Normalize all tickers data from CoinSwitch API response."""
        if not input_data:
            return [], False
        data = input_data.get("data", input_data)
        return [data], data is not None

    def get_all_tickers(self, extra_data=None, **kwargs):
        """Get all tickers.

        Makes the actual HTTP request to the API.
        """
        path, params, extra_data = self._get_all_tickers(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)
