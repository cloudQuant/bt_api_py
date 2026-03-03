"""
SatoshiTango Spot Feed implementation.
"""

import json

from typing import Any

from bt_api_py.containers.exchanges.satoshitango_exchange_data import SatoshiTangoExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_satoshitango.request_base import SatoshiTangoRequestData
from bt_api_py.functions.utils import update_extra_data


class SatoshiTangoRequestDataSpot(SatoshiTangoRequestData):
    """SatoshiTango Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "SATOSHITANGO___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data."""
        request_type = "get_tick"
        path = f"GET /v1/ticker"
        extra_data = extra_data or {}
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
        """Normalize ticker response."""
        if not input_data:
            return [], False
        ticker = input_data if isinstance(input_data, dict) else {}
        return [ticker], bool(ticker)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker asynchronously."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth."""
        request_type = "get_depth"
        path = f"GET /v1/orderbook"
        extra_data = extra_data or {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "normalize_function": self._get_depth_normalize_function,
            },
        )
        return path, {"symbol": symbol, "depth": count}, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth response."""
        if not input_data:
            return [], False
        depth = input_data if isinstance(input_data, dict) else {}
        return [depth], bool(depth)

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book asynchronously."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data."""
        request_type = "get_kline"
        path = f"GET /v1/klines"
        extra_data = extra_data or {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "normalize_function": self._get_kline_normalize_function,
            },
        )
        return path, {
            "symbol": symbol,
            "interval": self._params.get_period(period),
            "limit": count,
        }, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline response."""
        if not input_data:
            return [], False
        klines = input_data if isinstance(input_data, list) else []
        return klines, bool(klines)

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def async_get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data asynchronously."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information."""
        request_type = "get_exchange_info"
        path = f"GET /v1/markets"
        extra_data = extra_data or {}
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
        """Normalize exchange info response."""
        if not input_data:
            return [], False
        info = input_data if isinstance(input_data, dict) else {}
        return [info], bool(info)

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information."""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def async_callback(self, future):
        """Callback function for async requests, push result to data_queue.

        Args:
            future: asyncio future object
        """
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.warn(f"async_callback::{e}")
