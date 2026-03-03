"""
CoinDCX Spot Feed implementation.
"""

import json

from bt_api_py.containers.exchanges.coindcx_exchange_data import CoinDCXExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_coindcx.request_base import CoinDCXRequestData


class CoinDCXRequestDataSpot(CoinDCXRequestData):
    """CoinDCX Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "COINDCX___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_tick"
        path = f"GET /exchange/ticker"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        # CoinDCX returns all tickers, filter by symbol
        return path, None, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data from CoinDCX API.

        CoinDCX returns a list of all tickers, we need to filter by symbol.
        """
        from bt_api_py.containers.tickers.coindcx_ticker import CoinDCXRequestTickerData

        if not input_data:
            return [], False

        tickers = input_data if isinstance(input_data, list) else []
        if not tickers:
            return [], False

        # Get the symbol we're looking for
        target_symbol = extra_data.get("symbol_name", "")

        # Find the ticker for the requested symbol
        target_ticker = None
        for ticker in tickers:
            if isinstance(ticker, dict) and ticker.get("market") == target_symbol:
                target_ticker = ticker
                break

        if not target_ticker:
            return [], False

        # Wrap in {"data": target_ticker} format as expected by the ticker class
        ticker_data = {"data": target_ticker}
        ticker_obj = CoinDCXRequestTickerData(
            ticker_data,  # Pass the dict wrapped in {"data": ...}
            target_symbol,
            "SPOT",
            has_been_json_encoded=True  # Already a dict
        )
        return [ticker_obj], True

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_depth"
        # CoinDCX uses path-based parameter for symbol
        path = f"GET /exchange/v1/orderbook/{symbol}"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        # No query params needed for orderbook
        return path, None, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        depth = input_data
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_kline"
        path = f"GET /market_data/candles"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        params = {
            "pair": symbol,
            "interval": self._params.get_period(period),
        }
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        klines = input_data
        return [klines], klines is not None

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
