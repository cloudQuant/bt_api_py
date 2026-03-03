"""
BYDFi Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.bydfi_exchange_data import BYDFiExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bydfi.request_base import BYDFiRequestData


class BYDFiRequestDataSpot(BYDFiRequestData):
    """BYDFi Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "BYDFI___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data parameters.

        BYDFi API endpoint: /v1/public/ticker?symbol={symbol}
        Symbol format: BTC-USDT, ETH-USDT, etc.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_ticker"  # Matches the YAML config key
        path = self._params.get_rest_path(request_type)
        params = {"symbol": self._params.get_symbol(symbol)}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize BYDFi ticker response."""
        if not input_data:
            return [], False
        ticker = input_data.get("data", {})
        return [ticker], ticker is not None

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth parameters.

        BYDFi API endpoint: /v1/public/depth?symbol={symbol}&limit={count}

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type)
        params = {
            "symbol": self._params.get_symbol(symbol),
            "limit": count,
        }
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize BYDFi orderbook response."""
        if not input_data:
            return [], False
        depth = input_data.get("data", {})
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data parameters.

        BYDFi API endpoint: /v1/public/kline?symbol={symbol}&interval={period}&limit={count}

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_kline"
        period_str = self._params.get_period(period)
        path = self._params.get_rest_path(request_type)
        params = {
            "symbol": self._params.get_symbol(symbol),
            "interval": period_str,
            "limit": count,
        }
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize BYDFi kline response."""
        if not input_data:
            return [], False
        klines = input_data.get("data", [])
        return [klines], klines is not None

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange trading rules parameters.

        BYDFi API endpoint: /v1/public/exchange-info

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_exchange_info"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_exchange_info_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize BYDFi exchange info response."""
        if not input_data:
            return [], False
        symbols = input_data.get("data", {}).get("symbols", [])
        return [symbols], symbols is not None

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information."""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_trades(self, symbol, count=10, extra_data=None, **kwargs):
        """Get recent trades parameters.

        BYDFi API endpoint: /v1/public/trades?symbol={symbol}&limit={count}

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_trades"
        path = self._params.get_rest_path(request_type)
        params = {
            "symbol": self._params.get_symbol(symbol),
            "limit": count,
        }
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_trades_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize BYDFi trades response."""
        if not input_data:
            return [], False
        trades = input_data.get("data", [])
        return [trades], trades is not None

    def get_trades(self, symbol, count=10, extra_data=None, **kwargs):
        """Get recent trades."""
        path, params, extra_data = self._get_trades(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)
