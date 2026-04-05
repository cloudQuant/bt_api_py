"""
Luno Spot Feed implementation.
"""

from __future__ import annotations

import time
from typing import Any

from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_luno.request_base import LunoRequestData
from bt_api_py.functions.utils import update_extra_data


class LunoRequestDataSpot(LunoRequestData):
    """Luno Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "LUNO___SPOT")

    # ==================== Market Data ====================

    def _get_tick(
        self, symbol: str, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, str], Any]:
        """Get ticker data. Returns (path, params, extra_data)."""
        path = "GET /ticker"
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_tick",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": LunoRequestDataSpot._get_tick_normalize_function,
            },
        )
        return path, {"pair": symbol}, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data: Any, extra_data: Any) -> tuple[list[Any], bool]:
        if not input_data:
            return [], False
        return [input_data], True

    def get_tick(self, symbol: str, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_tick(self, symbol: str, extra_data: Any = None, **kwargs: Any) -> None:
        """Async get ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_depth(
        self, symbol: str, count: int = 20, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, str], Any]:
        """Get order book depth. Returns (path, params, extra_data)."""
        path = "GET /orderbook"
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_depth",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": LunoRequestDataSpot._get_depth_normalize_function,
            },
        )
        return path, {"pair": symbol}, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data: Any, extra_data: Any) -> tuple[list[Any], bool]:
        if not input_data:
            return [], False
        return [input_data], True

    def get_depth(self, symbol: str, count: int = 20, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get order book."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(
        self, symbol: str, count: int = 20, extra_data: Any = None, **kwargs: Any
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
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], Any]:
        """Get kline/candlestick data. Returns (path, params, extra_data)."""
        path = "GET /candles"
        since = int((time.time() - 86400) * 1000)
        duration = self._params.get_period(period, "3600")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_kline",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": LunoRequestDataSpot._get_kline_normalize_function,
            },
        )
        return path, {"pair": symbol, "since": since, "duration": duration}, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data: Any, extra_data: Any) -> tuple[list[Any], bool]:
        if not input_data:
            return [], False
        klines = input_data.get("candles", []) if isinstance(input_data, dict) else []
        return [klines], klines is not None

    def get_kline(
        self, symbol: str, period: str, count: int = 20, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """Get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_kline(
        self, symbol: str, period: str, count: int = 20, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get kline."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_exchange_info(
        self, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], Any]:
        """Get exchange information. Returns (path, params, extra_data)."""
        path = "GET /markets"
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_exchange_info",
                "symbol_name": "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": LunoRequestDataSpot._get_exchange_info_normalize_function,
            },
        )
        return path, {}, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[list[Any], bool]:
        if not input_data:
            return [], False
        markets = input_data.get("markets", []) if isinstance(input_data, dict) else []
        return [markets], markets is not None

    def get_exchange_info(self, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get exchange information."""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Account / Trading ====================

    def _get_balance(
        self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], Any]:
        """Get account balance. Returns (path, params, extra_data)."""
        path = "GET /balance"
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_balance",
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": LunoRequestDataSpot._get_balance_normalize_function,
            },
        )
        return path, {}, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data: Any, extra_data: Any) -> tuple[list[Any], bool]:
        if not input_data:
            return [], False
        return [input_data], True

    def get_balance(self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get account balance."""
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_account(
        self, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], Any]:
        """Get account information. Returns (path, params, extra_data)."""
        path = "GET /balance"
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_account",
                "symbol_name": "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": LunoRequestDataSpot._get_account_normalize_function,
            },
        )
        return path, {}, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data: Any, extra_data: Any) -> tuple[list[Any], bool]:
        if not input_data:
            return [], False
        return [input_data], True

    def get_account(self, symbol: str = "ALL", extra_data: Any = None, **kwargs: Any) -> Any:
        """Get account information."""
        path, params, extra_data = self._get_account(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        offset: str = "open",
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, str], Any]:
        """Prepare make order request. Returns (path, params, extra_data)."""
        path = "POST /postorder"
        side = "BID" if "buy" in order_type.lower() else "ASK"
        params = {
            "pair": symbol,
            "type": side,
            "volume": str(volume),
            "price": str(price),
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "make_order",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": LunoRequestDataSpot._make_order_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data: Any, extra_data: Any) -> tuple[list[Any], bool]:
        if not input_data:
            return [], False
        return [input_data], True

    def make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        offset: str = "open",
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Place an order."""
        path, params, extra_data = self._make_order(
            symbol, volume, price, order_type, offset, extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    def _cancel_order(
        self, symbol: str, order_id: str, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, str], Any]:
        """Prepare cancel order request. Returns (path, params, extra_data)."""
        path = "POST /stoporder"
        params = {"order_id": order_id}
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

    def cancel_order(
        self, symbol: str, order_id: str, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """Cancel an order."""
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
