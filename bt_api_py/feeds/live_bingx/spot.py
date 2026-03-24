"""
BingX Spot Feed implementation.
"""

from __future__ import annotations

from typing import Any

from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bingx.request_base import BingXRequestData

RequestParams = dict[str, Any]
RequestExtraData = dict[str, Any]
RequestSpec = tuple[str, RequestParams, RequestExtraData]
NormalizeResult = tuple[list[Any], bool]


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

    def _get_tick(
        self, symbol: str, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> RequestSpec:
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
    def _get_tick_normalize_function(
        input_data: dict[str, Any], extra_data: Any
    ) -> NormalizeResult:
        if not input_data:
            return [], False
        data_list = input_data.get("data", [])
        if isinstance(data_list, list) and len(data_list) > 0:
            return [data_list[0]], True
        return [data_list] if data_list else [], False

    def get_tick(
        self, symbol: str, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> Any:
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_tick(
        self, symbol: str, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> None:
        """Async get ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Depth ====================

    def _get_depth(
        self,
        symbol: str,
        count: int = 20,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> RequestSpec:
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
    def _get_depth_normalize_function(
        input_data: dict[str, Any], extra_data: Any
    ) -> NormalizeResult:
        if not input_data:
            return [], False
        depth = input_data.get("data", {})
        return [depth], depth is not None

    def get_depth(
        self,
        symbol: str,
        count: int = 20,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> Any:
        """Get order book."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(
        self,
        symbol: str,
        count: int = 20,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> None:
        """Async get orderbook."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Kline ====================

    def _get_kline(
        self,
        symbol: str,
        period: str,
        count: int = 20,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> RequestSpec:
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
    def _get_kline_normalize_function(
        input_data: dict[str, Any], extra_data: Any
    ) -> NormalizeResult:
        if not input_data:
            return [], False
        klines = input_data.get("data", [])
        return [klines], klines is not None

    def get_kline(
        self,
        symbol: str,
        period: str,
        count: int = 20,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> Any:
        """Get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_kline(
        self,
        symbol: str,
        period: str = "1m",
        count: int = 20,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> None:
        """Async get kline."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Exchange Info ====================

    def _get_exchange_info(
        self, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> RequestSpec:
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
    def _get_exchange_info_normalize_function(
        input_data: dict[str, Any], extra_data: Any
    ) -> NormalizeResult:
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        symbols = data.get("symbols", []) if isinstance(data, dict) else data
        return symbols if isinstance(symbols, list) else [symbols], True

    def get_exchange_info(self, extra_data: RequestExtraData | None = None, **kwargs: Any) -> Any:
        """Get exchange info."""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Trading Interfaces ====================

    def _make_order(
        self,
        symbol: str,
        volume: Any,
        price: Any,
        order_type: str,
        offset: str = "open",
        post_only: bool = False,
        client_order_id: str | None = None,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> RequestSpec:
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
        symbol: str,
        volume: Any,
        price: Any,
        order_type: str,
        offset: str = "open",
        post_only: bool = False,
        client_order_id: str | None = None,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> Any:
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

    def _cancel_order(
        self,
        symbol: str,
        order_id: str | int,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> RequestSpec:
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

    def cancel_order(
        self,
        symbol: str,
        order_id: str | int,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> Any:
        """Cancel order."""
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _query_order(
        self,
        symbol: str,
        order_id: str | int,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> RequestSpec:
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

    def query_order(
        self,
        symbol: str,
        order_id: str | int,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> Any:
        """Query order status."""
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_open_orders(
        self, symbol: str | None = None, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> RequestSpec:
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

    def get_open_orders(
        self, symbol: str | None = None, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> Any:
        """Get open orders."""
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Account Interfaces ====================

    def _get_account(
        self, symbol: str | None = None, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> RequestSpec:
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

    def get_account(
        self, symbol: str | None = None, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> Any:
        """Get account info."""
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_balance(
        self, symbol: str | None = None, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> RequestSpec:
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

    def get_balance(
        self, symbol: str | None = None, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> Any:
        """Get token balance."""
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
