"""
Buda Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.buda_exchange_data import BudaExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_buda.request_base import BudaRequestData


class BudaRequestDataSpot(BudaRequestData):
    """Buda Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "BUDA___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data.

        Buda API endpoint: /v2/markets/{market_id}/ticker
        Symbol format: BTC-CLP, ETH-COP, etc.
        """
        request_type = "get_tick"
        path = f"GET /v2/markets/{symbol}/ticker"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return self.request(path, params={}, extra_data=extra_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize Buda ticker response."""
        if not input_data:
            return [], False
        ticker = input_data.get("ticker", {})
        return [ticker], ticker is not None

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth.

        Buda API endpoint: /v2/markets/{market_id}/order_book
        """
        request_type = "get_depth"
        path = f"GET /v2/markets/{symbol}/order_book"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        return self.request(path, params={}, extra_data=extra_data)

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize Buda orderbook response."""
        if not input_data:
            return [], False
        depth = input_data.get("order_book", {})
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data.

        Buda API endpoint: /v2/markets/{market_id}/candles?time_frame={period}
        """
        request_type = "get_kline"
        period_str = self._params.get_period(period)
        path = f"GET /v2/markets/{symbol}/candles"

        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        return self.request(path, params={"time_frame": period_str}, extra_data=extra_data)

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize Buda kline response."""
        if not input_data:
            return [], False
        candles = input_data.get("candles", [])
        return [candles], candles is not None

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        return self._get_kline(symbol, period, count, extra_data, **kwargs)

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange trading rules.

        Buda API endpoint: /v2/markets
        """
        request_type = "get_exchange_info"
        path = "GET /v2/markets"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_exchange_info_normalize_function,
        })
        return self.request(path, params={}, extra_data=extra_data)

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize Buda exchange info response."""
        if not input_data:
            return [], False
        markets = input_data.get("markets", [])
        return [markets], markets is not None

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information."""
        return self._get_exchange_info(extra_data, **kwargs)

    def _get_trades(self, symbol, count=10, extra_data=None, **kwargs):
        """Get recent trades.

        Buda API endpoint: /v2/markets/{market_id}/trades
        """
        request_type = "get_trades"
        path = f"GET /v2/markets/{symbol}/trades"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_trades_normalize_function,
        })
        return self.request(path, params={"limit": count}, extra_data=extra_data)

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize Buda trades response."""
        if not input_data:
            return [], False
        # Buda wraps in "entries" key
        trades = input_data.get("entries", {}).get("trades", [])
        return [trades], trades is not None

    def get_trades(self, symbol, count=10, extra_data=None, **kwargs):
        """Get recent trades."""
        return self._get_trades(symbol, count, extra_data, **kwargs)
