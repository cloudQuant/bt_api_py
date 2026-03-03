"""
CoinSpot Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.coinspot_exchange_data import CoinSpotExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_coinspot.request_base import CoinSpotRequestData


class CoinSpotRequestDataSpot(CoinSpotRequestData):
    """CoinSpot Spot Feed for market data."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "COINSPOT___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Build ticker request parameters for a specific coin."""
        request_type = "get_tick"
        # CoinSpot uses coin shortname as path parameter
        path = f"GET /pubapi/v2/latest/{symbol}"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return path, None, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data from CoinSpot API response."""
        if not input_data:
            return [], False
        # CoinSpot returns status and prices fields
        data = input_data.get("prices", {})
        if input_data.get("status") != "ok":
            return [], False
        return [data], data is not None

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_all_tickers(self, extra_data=None, **kwargs):
        """Build all tickers request parameters."""
        request_type = "get_ticker_all"
        path = "GET /pubapi/v2/latest"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_all_tickers_normalize_function,
        })
        return path, None, extra_data

    @staticmethod
    def _get_all_tickers_normalize_function(input_data, extra_data):
        """Normalize all tickers data from CoinSpot API response."""
        if not input_data:
            return [], False
        prices = input_data.get("prices", {})
        if input_data.get("status") != "ok":
            return [], False
        # Convert to list format
        result = [{"prices": prices, "status": "ok"}]
        return result, prices is not None
