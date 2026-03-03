"""
Giottus Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.giottus_exchange_data import GiottusExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_giottus.request_base import GiottusRequestData
from bt_api_py.functions.utils import update_extra_data


class GiottusRequestDataSpot(GiottusRequestData):
    """Giottus Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "GIOTTUS___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data."""
        request_type = "get_tick"
        path = self._params.rest_paths.get("get_ticker", "GET /v1/ticker")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "normalize_function": self._get_tick_normalize_function,
            },
        )
        return path, {"symbol": symbol}, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data from Giottus API response."""
        if not input_data:
            return [], False

        # Giottus response format may vary - adapt based on actual API response
        data = input_data if isinstance(input_data, dict) else {}
        ticker = data.get("data", data)

        return [ticker], ticker is not None

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth."""
        request_type = "get_depth"
        path = self._params.rest_paths.get("get_depth", "GET /v1/orderbook")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "normalize_function": self._get_depth_normalize_function,
            },
        )
        return path, {"symbol": symbol, "limit": count}, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth data from Giottus API response."""
        if not input_data:
            return [], False

        data = input_data if isinstance(input_data, dict) else {}
        depth = data.get("data", data)

        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data."""
        request_type = "get_kline"
        path = self._params.rest_paths.get("get_kline", "GET /v1/klines")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "period": period,
                "normalize_function": self._get_kline_normalize_function,
            },
        )

        # Map period to Giottus format if needed
        giottus_period = self._params.kline_periods.get(period, period)

        return path, {"symbol": symbol, "interval": giottus_period, "limit": count}, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline data from Giottus API response."""
        if not input_data:
            return [], False

        data = input_data if isinstance(input_data, dict) else {}
        klines = data.get("data", data.get("klines", []))

        return [klines], klines is not None

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        return self._get_kline(symbol, period, count, extra_data, **kwargs)

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information (available trading pairs)."""
        request_type = "get_exchange_info"
        path = self._params.rest_paths.get("get_markets", "GET /v1/markets")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "normalize_function": self._get_exchange_info_normalize_function,
            },
        )
        return path, {}, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info from Giottus API response."""
        if not input_data:
            return [], False

        data = input_data if isinstance(input_data, dict) else {}
        markets = data.get("data", data.get("markets", []))

        return [markets], markets is not None

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information."""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)
