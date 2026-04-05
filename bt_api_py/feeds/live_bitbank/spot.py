"""
Bitbank Spot Feed implementation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitbank.request_base import BitbankRequestData

RequestParams = dict[str, Any]
RequestExtraData = dict[str, Any]
RequestSpec = tuple[str, Optional[RequestParams], RequestExtraData]
NormalizeResult = tuple[list[Any], bool]


class BitbankRequestDataSpot(BitbankRequestData):
    """Bitbank Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITBANK___SPOT")

    def _normalize_pair(self, symbol: str) -> str:
        """Normalize symbol to bitbank format (e.g., btc_jpy)."""
        symbol = symbol.upper().replace("/", "_").replace("-", "_")
        return symbol.lower()

    # ==================== Ticker ====================

    def _get_tick(
        self, symbol: str, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> RequestSpec:
        """Prepare ticker request. Returns (path, params, extra_data)."""
        pair = self._normalize_pair(symbol)
        path = f"GET /{pair}/ticker"
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
        return path, None, extra_data

    @staticmethod
    def _get_tick_normalize_function(
        input_data: dict[str, Any], extra_data: Any
    ) -> NormalizeResult:
        """Normalize ticker data."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("success") == 1:
            return [data], True
        return [], False

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
        pair = self._normalize_pair(symbol)
        path = f"GET /{pair}/depth"
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
        return path, None, extra_data

    @staticmethod
    def _get_depth_normalize_function(
        input_data: dict[str, Any], extra_data: Any
    ) -> NormalizeResult:
        """Normalize depth data."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("success") == 1:
            return [data], True
        return [], False

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
        pair = self._normalize_pair(symbol)
        kline_periods = getattr(self._params, "kline_periods", {})
        candle_type = kline_periods.get(period, period)

        long_periods = ["4hour", "8hour", "12hour", "1day", "1week", "1month"]
        if candle_type in long_periods:
            date_str = datetime.now().strftime("%Y")
        else:
            date_str = datetime.now().strftime("%Y%m%d")

        path = f"GET /{pair}/candlestick/{candle_type}/{date_str}"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_kline",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "period": period,
                "normalize_function": self._get_kline_normalize_function,
            }
        )
        return path, None, extra_data

    @staticmethod
    def _get_kline_normalize_function(
        input_data: dict[str, Any], extra_data: Any
    ) -> NormalizeResult:
        """Normalize kline data."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("success") == 1:
            candlestick = data.get("candlestick", [])
            if candlestick and len(candlestick) > 0:
                ohlcv = candlestick[0].get("ohlcv", [])
                if ohlcv:
                    return [{"ohlcv": ohlcv}], True
        return [], False

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

    # ==================== Trades ====================

    def _get_trades(
        self,
        symbol: str,
        limit: int = 60,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> RequestSpec:
        """Prepare trades request. Returns (path, params, extra_data)."""
        pair = self._normalize_pair(symbol)
        path = f"GET /{pair}/transactions"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_trades",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_trades_normalize_function,
            }
        )
        return path, None, extra_data

    @staticmethod
    def _get_trades_normalize_function(
        input_data: dict[str, Any], extra_data: Any
    ) -> NormalizeResult:
        """Normalize trades data."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("success") == 1:
            transactions = data.get("transactions", [])
            return [transactions], True
        return [], False

    def get_trades(
        self,
        symbol: str,
        limit: int = 60,
        extra_data: RequestExtraData | None = None,
        **kwargs: Any,
    ) -> Any:
        """Get recent trades."""
        path, params, extra_data = self._get_trades(symbol, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Exchange Info ====================

    def _get_exchange_info(
        self, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> RequestSpec:
        """Prepare exchange info request. Returns (path, params, extra_data)."""
        path = "GET /spot/pairs"
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
        return path, None, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(
        input_data: dict[str, Any], extra_data: Any
    ) -> NormalizeResult:
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("success") == 1:
            pairs = data.get("pairs", data) if isinstance(data, dict) else data
            return pairs if isinstance(pairs, list) else [pairs], True
        return [], False

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
        path = "POST /v1/user/spot/order"
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
            "pair": self._normalize_pair(symbol),
            "amount": str(volume),
            "price": str(price),
            "side": offset if offset in ("buy", "sell") else "buy",
            "type": order_type,
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
        path = "POST /v1/user/spot/cancel_order"
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
        params = {"pair": self._normalize_pair(symbol), "order_id": order_id}
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
        path = "GET /v1/user/spot/order"
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
        params = {"pair": self._normalize_pair(symbol), "order_id": order_id}
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
        path = "GET /v1/user/spot/active_orders"
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
        params = {"pair": self._normalize_pair(symbol)} if symbol else {}
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
        path = "GET /v1/user/assets"
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
        return path, None, extra_data

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
        path = "GET /v1/user/assets"
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
        return path, None, extra_data

    def get_balance(
        self, symbol: str | None = None, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> Any:
        """Get token balance."""
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
