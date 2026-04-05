"""
Giottus Spot Feed implementation.
"""

from __future__ import annotations

from typing import Any

from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_giottus.request_base import GiottusRequestData
from bt_api_py.functions.utils import update_extra_data


class GiottusRequestDataSpot(GiottusRequestData):
    """Giottus Spot Feed for market data."""

    @classmethod
    def _capabilities(cls) -> set[Capability]:
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
        }

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "GIOTTUS___SPOT")

    # ==================== Market Data ====================

    def _get_tick(self, symbol, extra_data=None, **kwargs) -> Any:
        """Get ticker data. Returns (path, params, extra_data)."""
        path = self._params.rest_paths.get("get_ticker", "GET /v1/ticker")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_tick",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": GiottusRequestDataSpot._get_tick_normalize_function,
            },
        )
        return path, {"symbol": symbol}, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        data = input_data if isinstance(input_data, dict) else {}
        ticker = data.get("data", data)
        return [ticker], ticker is not None

    def get_tick(self, symbol, extra_data=None, **kwargs) -> Any:
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Async get ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs) -> Any:
        """Get order book depth. Returns (path, params, extra_data)."""
        path = self._params.rest_paths.get("get_depth", "GET /v1/orderbook")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_depth",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": GiottusRequestDataSpot._get_depth_normalize_function,
            },
        )
        return path, {"symbol": symbol, "limit": count}, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        data = input_data if isinstance(input_data, dict) else {}
        depth = data.get("data", data)
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs) -> Any:
        """Get order book."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Async get depth."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs) -> Any:
        """Get kline/candlestick data. Returns (path, params, extra_data)."""
        path = self._params.rest_paths.get("get_kline", "GET /v1/klines")
        giottus_period = self._params.kline_periods.get(period, period)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_kline",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": GiottusRequestDataSpot._get_kline_normalize_function,
            },
        )
        return path, {"symbol": symbol, "interval": giottus_period, "limit": count}, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        data = input_data if isinstance(input_data, dict) else {}
        klines = data.get("data", data.get("klines", []))
        return [klines], klines is not None

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs) -> Any:
        """Get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Async get kline."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_exchange_info(self, extra_data=None, **kwargs) -> Any:
        """Get exchange information. Returns (path, params, extra_data)."""
        path = self._params.rest_paths.get("get_markets", "GET /v1/markets")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_exchange_info",
                "symbol_name": "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": GiottusRequestDataSpot._get_exchange_info_normalize_function,
            },
        )
        return path, {}, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        data = input_data if isinstance(input_data, dict) else {}
        markets = data.get("data", data.get("markets", []))
        return [markets], markets is not None

    def get_exchange_info(self, extra_data=None, **kwargs) -> Any:
        """Get exchange information."""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Account / Trading ====================

    def _get_balance(self, symbol=None, extra_data=None, **kwargs) -> Any:
        """Get account balance. Returns (path, params, extra_data)."""
        path = self._params.rest_paths.get("get_balance", "POST /v1/balance")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_balance",
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": GiottusRequestDataSpot._get_balance_normalize_function,
            },
        )
        return path, {}, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        return [input_data], True

    def get_balance(self, symbol=None, extra_data=None, **kwargs) -> Any:
        """Get account balance."""
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_account(self, extra_data=None, **kwargs) -> Any:
        """Get account information. Returns (path, params, extra_data)."""
        path = self._params.rest_paths.get("get_balance", "POST /v1/balance")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_account",
                "symbol_name": "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": GiottusRequestDataSpot._get_account_normalize_function,
            },
        )
        return path, {}, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        return [input_data], True

    def get_account(self, symbol="ALL", extra_data=None, **kwargs) -> Any:
        """Get account information."""
        path, params, extra_data = self._get_account(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _make_order(
        self, symbol, volume, price, order_type, offset="open", extra_data=None, **kwargs
    ):
        """Prepare make order request. Returns (path, params, extra_data)."""
        path = self._params.rest_paths.get("make_order", "POST /v1/order")
        side = "BUY" if "buy" in order_type.lower() else "SELL"
        params = {
            "symbol": symbol,
            "side": side,
            "quantity": str(volume),
            "price": str(price),
            "type": "LIMIT" if price else "MARKET",
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "make_order",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": GiottusRequestDataSpot._make_order_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        return [input_data], True

    def make_order(
        self, symbol, volume, price, order_type, offset="open", extra_data=None, **kwargs
    ):
        """Place an order."""
        path, params, extra_data = self._make_order(
            symbol, volume, price, order_type, offset, extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    def _cancel_order(self, symbol, order_id, extra_data=None, **kwargs) -> Any:
        """Prepare cancel order request. Returns (path, params, extra_data)."""
        path = self._params.rest_paths.get("cancel_order", "POST /v1/cancel")
        params = {"orderId": order_id}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "cancel_order",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "order_id": order_id,
            },
        )
        return path, params, extra_data

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel an order."""
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
