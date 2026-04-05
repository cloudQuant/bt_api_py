"""
Bitvavo Spot Feed implementation.
"""

from __future__ import annotations

from typing import Any, Optional

from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitvavo.request_base import BitvavoRequestData

RequestParams = dict[str, Any]
RequestExtraData = dict[str, Any]
RequestSpec = tuple[str, Optional[RequestParams], RequestExtraData]
NormalizeResult = tuple[list[Any], bool]


class BitvavoRequestDataSpot(BitvavoRequestData):
    """Bitvavo Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITVAVO___SPOT")

    # ==================== Market Data ====================

    def _get_tick(
        self, symbol: str, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> RequestSpec:
        """Get ticker data. Returns (path, params, extra_data)."""
        path = "GET /ticker/24h"
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
        params = {"market": symbol}
        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data: Any, extra_data: Any) -> NormalizeResult:
        if not input_data:
            return [], False
        ticker = input_data if isinstance(input_data, dict) else {}
        return [ticker], bool(ticker)

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

    def _get_depth(
        self,
        symbol: str,
        count: int = 20,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> RequestSpec:
        """Get order book depth. Returns (path, params, extra_data)."""
        path = f"GET /{symbol}/book"
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
        params = {"depth": count}
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data: Any, extra_data: Any) -> NormalizeResult:
        if not input_data:
            return [], False
        depth = input_data if isinstance(input_data, dict) else {}
        return [depth], bool(depth)

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
        """Async get depth."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_kline(
        self,
        symbol: str,
        period: str,
        count: int = 20,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> RequestSpec:
        """Get kline/candlestick data. Returns (path, params, extra_data)."""
        path = f"GET /{symbol}/candles"
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
        interval = self._params.kline_periods.get(period, period)
        params = {"interval": interval, "limit": count}
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data: Any, extra_data: Any) -> NormalizeResult:
        if not input_data:
            return [], False
        klines = input_data if isinstance(input_data, list) else []
        return [klines], True

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
        period: str,
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

    def _get_exchange_info(
        self, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> RequestSpec:
        """Get exchange configuration. Returns (path, params, extra_data)."""
        path = "GET /markets"
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
    def _get_exchange_info_normalize_function(input_data: Any, extra_data: Any) -> NormalizeResult:
        if not input_data:
            return [], False
        info = input_data if isinstance(input_data, list) else [input_data]
        return [info], True

    def get_exchange_info(self, extra_data: RequestExtraData | None = None, **kwargs: Any) -> Any:
        """Get exchange info."""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Account Interfaces ====================

    def _get_account(
        self, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> RequestSpec:
        """Get account information. Returns (path, params, extra_data)."""
        path = "GET /account"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_account",
                "symbol_name": "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_account_normalize_function,
            }
        )
        return path, {}, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data: Any, extra_data: Any) -> NormalizeResult:
        if not input_data:
            return [], False
        account = input_data if isinstance(input_data, dict) else {}
        return [account], bool(account)

    def get_account(
        self, symbol: str = "ALL", extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> Any:
        """Get account information."""
        path, params, extra_data = self._get_account(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_account(
        self, symbol: str = "ALL", extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> None:
        """Get account information asynchronously."""
        path, params, extra_data = self._get_account(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_balance(
        self, symbol: str | None = None, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> RequestSpec:
        """Get account balance. Returns (path, params, extra_data)."""
        path = "GET /balance"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_balance",
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_balance_normalize_function,
            }
        )
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        return path, params, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data: Any, extra_data: Any) -> NormalizeResult:
        if not input_data:
            return [], False
        balance = input_data if isinstance(input_data, (dict, list)) else {}
        return [balance], bool(balance)

    def get_balance(
        self, symbol: str | None = None, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> Any:
        """Get account balance."""
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
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
        path = "POST /order"
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
            "market": symbol,
            "side": offset.lower() if offset in ("BUY", "SELL", "buy", "sell") else "buy",
            "orderType": order_type.lower(),
            "amount": str(volume),
            "price": str(price),
        }
        if post_only:
            params["postOnly"] = True
        if client_order_id:
            params["clientOrderId"] = client_order_id
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
        return self.request(path, body=params, extra_data=extra_data)

    def _cancel_order(
        self,
        symbol: str,
        order_id: str | int,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> RequestSpec:
        """Cancel order. Returns (path, params, extra_data)."""
        path = "DELETE /order"
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
        params = {"market": symbol, "orderId": order_id}
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
        return self.request(path, body=params, extra_data=extra_data)

    def _query_order(
        self,
        symbol: str,
        order_id: str | int,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> RequestSpec:
        """Query order. Returns (path, params, extra_data)."""
        path = "GET /order"
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
        params = {"market": symbol, "orderId": order_id}
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
        path = "GET /ordersOpen"
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
        params: dict[str, Any] = {}
        if symbol:
            params["market"] = symbol
        return path, params, extra_data

    def get_open_orders(
        self, symbol: str | None = None, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> Any:
        """Get open orders."""
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
