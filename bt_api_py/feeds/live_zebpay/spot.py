"""
Zebpay Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.zebpay_exchange_data import ZebpayExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_zebpay.request_base import ZebpayRequestData


class ZebpayRequestDataSpot(ZebpayRequestData):
    """Zebpay Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "ZEBPAY___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data.

        Zebpay API endpoint: GET /api/v2/market/ticker?symbol=BTC-INR
        Symbol format: BTC-INR (dash separator)
        """
        request_type = "get_tick"
        zebpay_symbol = self._to_zebpay_symbol(symbol)
        path = "GET /api/v2/market/ticker"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return self.request(path, params={"symbol": zebpay_symbol}, extra_data=extra_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        data = input_data.get("data")
        return [data], data is not None

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth.

        Zebpay API endpoint: GET /api/v2/market/orderbook?symbol=BTC-INR
        """
        request_type = "get_depth"
        zebpay_symbol = self._to_zebpay_symbol(symbol)
        path = "GET /api/v2/market/orderbook"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        return self.request(path, params={"symbol": zebpay_symbol}, extra_data=extra_data)

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        depth = input_data.get("data")
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data.

        Zebpay API endpoint: GET /api/v2/market/klines?symbol=BTC-INR&interval=1m&limit=100
        """
        request_type = "get_kline"
        zebpay_symbol = self._to_zebpay_symbol(symbol)
        zebpay_period = self._to_zebpay_period(period)
        path = "GET /api/v2/market/klines"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        return self.request(path, params={
            "symbol": zebpay_symbol,
            "interval": zebpay_period,
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

    def _to_zebpay_symbol(self, symbol):
        """Convert symbol to Zebpay format.

        Examples:
            BTC/INR -> BTC-INR
            BTCINR -> BTC-INR
            btc-inr -> BTC-INR
        """
        if "-" in symbol:
            return symbol.upper().replace("-", "-")
        # Assume format like BTC/INR, BTC-INR, or BTCINR
        symbol = symbol.replace("/", "").replace("-", "").upper()
        # Common quote currencies
        quotes = ["INR", "USDT"]
        for quote in quotes:
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base}-{quote}"
        # Default: try to split at 6 chars for common pairs
        if len(symbol) >= 6:
            return f"{symbol[:-3]}-{symbol[-3:]}"
        return symbol

    def _to_zebpay_period(self, period):
        """Convert period to Zebpay format.

        Zebpay supports: 1m, 5m, 15m, 30m, 1h, 2h, 4h, 12h, 1d, 1w
        """
        period_map = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
            "12h": "12h",
            "1d": "1d",
            "1w": "1w",
        }
        return period_map.get(period, "1h")
