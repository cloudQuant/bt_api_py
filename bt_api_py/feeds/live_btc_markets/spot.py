"""
BTC Markets Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.btc_markets_exchange_data import BtcMarketsExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_btc_markets.request_base import BtcMarketsRequestData


class BtcMarketsRequestDataSpot(BtcMarketsRequestData):
    """BTC Markets Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "BTC_MARKETS___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data."""
        request_type = "get_tick"
        # BTC Markets uses GET /v3/markets/{marketId}/ticker
        path = f"GET /v3/markets/{symbol}/ticker"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return self.request(path, params=None, extra_data=extra_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # BTC Markets returns ticker object directly
        ticker = input_data if isinstance(input_data, dict) else {}
        return [ticker], bool(ticker)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        return self._get_tick(symbol, extra_data, **kwargs)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth."""
        request_type = "get_depth"
        # BTC Markets uses GET /v3/markets/{marketId}/orderbook
        path = f"GET /v3/markets/{symbol}/orderbook"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        return self.request(path, params=None, extra_data=extra_data)

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        depth = input_data if isinstance(input_data, dict) else {}
        return [depth], bool(depth)

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data."""
        request_type = "get_kline"
        # BTC Markets uses GET /v3/markets/{marketId}/candles
        path = f"GET /v3/markets/{symbol}/candles"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        # Map period to exchange format
        interval = self._params.get_period(period, period)
        return self.request(path, params={
            "interval": interval,
            "limit": count,
        }, extra_data=extra_data)

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        klines = input_data if isinstance(input_data, list) else []
        return [klines], True

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        return self._get_kline(symbol, period, count, extra_data, **kwargs)
