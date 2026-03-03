"""
BTCTurk Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.btcturk_exchange_data import BTCTurkExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_btcturk.request_base import BTCTurkRequestData


class BTCTurkRequestDataSpot(BTCTurkRequestData):
    """BTCTurk Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "BTCTURK___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data.

        BTCTurk API endpoint: /api/v2/ticker?pairSymbol={symbol}

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_tick"
        path = "GET /api/v2/ticker"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return path, {"pairSymbol": symbol}, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize BTCTurk ticker response."""
        if not input_data:
            return [], False
        data = input_data.get("data", [])
        # BTCTurk returns an array of tickers or single ticker
        if isinstance(data, list) and len(data) > 0:
            return [data[0]], True
        return [data], data is not None

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth.

        BTCTurk API endpoint: /api/v2/orderbook?pairSymbol={symbol}&limit={count}
        """
        request_type = "get_depth"
        path = "GET /api/v2/orderbook"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        limit = min(count, 1000)  # BTCTurk max limit is 1000
        return self.request(path, params={"pairSymbol": symbol, "limit": limit}, extra_data=extra_data)

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize BTCTurk orderbook response."""
        if not input_data:
            return [], False
        depth = input_data.get("data", {})
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data.

        BTCTurk API endpoint: /api/v2/ohlcs?pair={symbol}&from={timestamp}&to={timestamp}

        Note: BTCTurk OHLC requires from/to timestamps, not count.
        We'll calculate from based on period and count.
        """
        import time as t
        request_type = "get_kline"
        path = "GET /api/v2/ohlcs"

        # Convert period to minutes
        period_minutes = self._params.get_period(period)
        to_timestamp = int(t.time())
        from_timestamp = to_timestamp - (period_minutes * 60 * count)

        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        return self.request(path, params={
            "pair": symbol,
            "from": from_timestamp,
            "to": to_timestamp,
        }, extra_data=extra_data)

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize BTCTurk kline response."""
        if not input_data:
            return [], False
        klines = input_data.get("data", [])
        return [klines], klines is not None

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        return self._get_kline(symbol, period, count, extra_data, **kwargs)

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange trading rules.

        BTCTurk API endpoint: /api/v2/server/exchangeinfo
        """
        request_type = "get_exchange_info"
        path = "GET /api/v2/server/exchangeinfo"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_exchange_info_normalize_function,
        })
        return self.request(path, params={}, extra_data=extra_data)

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize BTCTurk exchange info response."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        symbols = data.get("symbols", [])
        return [symbols], symbols is not None

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information."""
        return self._get_exchange_info(extra_data, **kwargs)

    def _get_trades(self, symbol, count=10, extra_data=None, **kwargs):
        """Get recent trades.

        BTCTurk API endpoint: /api/v2/trades?pairSymbol={symbol}&last={count}
        """
        request_type = "get_trades"
        path = "GET /api/v2/trades"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_trades_normalize_function,
        })
        return self.request(path, params={"pairSymbol": symbol, "last": count}, extra_data=extra_data)

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize BTCTurk trades response."""
        if not input_data:
            return [], False
        trades = input_data.get("data", [])
        return [trades], trades is not None

    def get_trades(self, symbol, count=10, extra_data=None, **kwargs):
        """Get recent trades."""
        return self._get_trades(symbol, count, extra_data, **kwargs)
