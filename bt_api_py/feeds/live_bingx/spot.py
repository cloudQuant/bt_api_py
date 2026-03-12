"""
BingX Spot Feed implementation.
"""

from typing import Any

from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bingx.request_base import BingXRequestData


class BingXRequestDataSpot(BingXRequestData):
    """BingX Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "BINGX___SPOT")

    # ==================== Ticker ====================

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Prepare ticker request. Returns (path, params, extra_data)."""
        path = "GET /openApi/spot/v1/ticker/24hr"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_tick",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_tick_normalize_function,
            }
        )
        params = {"symbol": symbol}
        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        data_list = input_data.get("data", [])
        if isinstance(data_list, list) and len(data_list) > 0:
            return [data_list[0]], True
        return [data_list] if data_list else [], False

    def get_tick(self, symbol, extra_data=None, **kwargs):
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

    # ==================== Depth ====================

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Prepare depth request. Returns (path, params, extra_data)."""
        path = "GET /openApi/spot/v1/market/depth"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_depth",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_depth_normalize_function,
            }
        )
        params = {"symbol": symbol, "limit": count}
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        depth = input_data.get("data", {})
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Async get orderbook."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Kline ====================

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Prepare kline request. Returns (path, params, extra_data)."""
        path = "GET /openApi/spot/v2/market/kline"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_kline",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_kline_normalize_function,
            }
        )
        bingx_period = self._params.kline_periods.get(period, period)
        params = {"symbol": symbol, "interval": bingx_period, "limit": count}
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        klines = input_data.get("data", [])
        return [klines], klines is not None

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_kline(self, symbol, period="1m", count=20, extra_data=None, **kwargs):
        """Async get kline."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Exchange Info ====================

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Prepare exchange info request. Returns (path, params, extra_data)."""
        path = "GET /openApi/spot/v1/common/symbols"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_exchange_info",
                "symbol_name": "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_exchange_info_normalize_function,
            }
        )
        return path, {}, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        symbols = data.get("symbols", []) if isinstance(data, dict) else data
        return symbols if isinstance(symbols, list) else [symbols], True

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange info."""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Trading Interfaces ====================

    def _make_order(
        self,
        symbol,
        volume,
        price,
        order_type,
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """Prepare order. Returns (path, params, extra_data)."""
        path = "POST /openApi/spot/v1/trade/order"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "make_order",
            }
        )
        params = {
            "symbol": symbol,
            "side": offset.upper() if offset else "BUY",
            "type": order_type,
            "quantity": str(volume),
            "price": str(price),
        }
        return path, params, extra_data

    def make_order(
        self,
        symbol,
        volume,
        price,
        order_type,
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """Place an order."""
        path, params, extra_data = self._make_order(
            symbol,
            volume,
            price,
            order_type,
            offset,
            post_only,
            client_order_id,
            extra_data,
            **kwargs,
        )
        return self.request(path, params=params, extra_data=extra_data)

    def _cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order. Returns (path, params, extra_data)."""
        path = "POST /openApi/spot/v1/trade/cancel"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "cancel_order",
                "order_id": order_id,
            }
        )
        params = {"symbol": symbol, "orderId": order_id}
        return path, params, extra_data

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order."""
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order. Returns (path, params, extra_data)."""
        path = "GET /openApi/spot/v1/trade/query"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "query_order",
                "order_id": order_id,
            }
        )
        params = {"symbol": symbol, "orderId": order_id}
        return path, params, extra_data

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order status."""
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders. Returns (path, params, extra_data)."""
        path = "GET /openApi/spot/v1/trade/openOrders"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_open_orders",
            }
        )
        params = {"symbol": symbol} if symbol else {}
        return path, params, extra_data

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders."""
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Account Interfaces ====================

    def _get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info. Returns (path, params, extra_data)."""
        path = "GET /openApi/spot/v1/account/balance"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_account",
            }
        )
        return path, {}, extra_data

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info."""
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get balance. Returns (path, params, extra_data)."""
        path = "GET /openApi/spot/v1/account/balance"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_balance",
            }
        )
        return path, {}, extra_data

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get token balance."""
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
