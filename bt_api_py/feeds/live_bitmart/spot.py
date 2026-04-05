"""
BitMart Spot Feed – three-layer sync/async wrappers.

Public:  get_tick, get_depth, get_kline, get_trade_history, get_server_time, get_exchange_info
Private: make_order, cancel_order, query_order, get_open_orders, get_deals, get_account, get_balance
"""

from __future__ import annotations

from typing import Any

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bitmart.request_base import BitmartRequestData


class BitmartRequestDataSpot(BitmartRequestData):
    """BitMart Spot Feed with three-layer method wrappers."""

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        kwargs.setdefault("exchange_name", "BITMART___SPOT")
        kwargs.setdefault("asset_type", "SPOT")
        super().__init__(data_queue, **kwargs)

    # ── public endpoints ────────────────────────────────────────

    def get_server_time(self, extra_data: Any = None, **kwargs: Any) -> RequestData:
        path, params, ed = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed)

    async def async_get_server_time(self, extra_data: Any = None, **kwargs: Any) -> RequestData:
        path, params, ed = self._get_server_time(extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed)

    def get_exchange_info(self, extra_data: Any = None, **kwargs: Any) -> RequestData:
        path, params, ed = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed)

    async def async_get_exchange_info(self, extra_data: Any = None, **kwargs: Any) -> RequestData:
        path, params, ed = self._get_exchange_info(extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed)

    def get_tick(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> RequestData:
        path, params, ed = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed)

    async def async_get_tick(
        self, symbol: Any, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, ed = self._get_tick(symbol, extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed)

    # aliases
    get_ticker = get_tick
    async_get_ticker = async_get_tick

    def get_depth(
        self, symbol: Any, count: Any = 35, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, ed = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed)

    async def async_get_depth(
        self, symbol: Any, count: Any = 35, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, ed = self._get_depth(symbol, count, extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed)

    def get_kline(
        self,
        symbol: Any,
        period: Any = "1h",
        count: Any = 100,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> RequestData:
        path, params, ed = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed)

    async def async_get_kline(
        self,
        symbol: Any,
        period: Any = "1h",
        count: Any = 100,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> RequestData:
        path, params, ed = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed)

    def get_trade_history(
        self, symbol: Any, count: Any = 50, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, ed = self._get_trade_history(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed)

    async def async_get_trade_history(
        self, symbol: Any, count: Any = 50, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, ed = self._get_trade_history(symbol, count, extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed)

    # aliases
    get_trades = get_trade_history
    async_get_trades = async_get_trade_history

    # ── private endpoints (signed) ──────────────────────────────

    def make_order(
        self,
        symbol: Any,
        volume: Any,
        price: Any = None,
        order_type: Any = "buy-limit",
        offset: Any = "open",
        post_only: Any = False,
        client_order_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> RequestData:
        path, body, ed = self._make_order(symbol, volume, price, order_type, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_make_order(
        self,
        symbol: Any,
        volume: Any,
        price: Any = None,
        order_type: Any = "buy-limit",
        offset: Any = "open",
        post_only: Any = False,
        client_order_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> RequestData:
        path, body, ed = self._make_order(symbol, volume, price, order_type, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=ed, is_sign=True)

    def cancel_order(
        self, symbol: Any = None, order_id: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, body, ed = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_cancel_order(
        self, symbol: Any = None, order_id: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, body, ed = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=ed, is_sign=True)

    def query_order(
        self, symbol: Any = None, order_id: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, body, ed = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_query_order(
        self, symbol: Any = None, order_id: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, body, ed = self._query_order(symbol, order_id, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=ed, is_sign=True)

    def get_open_orders(
        self, symbol: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, body, ed = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_get_open_orders(
        self, symbol: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, body, ed = self._get_open_orders(symbol, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=ed, is_sign=True)

    def get_deals(
        self,
        symbol: Any = None,
        count: Any = 100,
        start_time: Any = None,
        end_time: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> RequestData:
        path, body, ed = self._get_deals(symbol, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_get_deals(
        self,
        symbol: Any = None,
        count: Any = 100,
        start_time: Any = None,
        end_time: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> RequestData:
        path, body, ed = self._get_deals(symbol, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=ed, is_sign=True)

    def get_account(
        self, symbol: Any = "ALL", extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, ed = self._get_account(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed, is_sign=True)

    async def async_get_account(
        self, symbol: Any = "ALL", extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, ed = self._get_account(extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed, is_sign=True)

    def get_balance(self, symbol: Any = None, extra_data: Any = None, **kwargs: Any) -> RequestData:
        path, params, ed = self._get_balance(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed, is_sign=True)

    async def async_get_balance(
        self, symbol: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, ed = self._get_balance(extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed, is_sign=True)
