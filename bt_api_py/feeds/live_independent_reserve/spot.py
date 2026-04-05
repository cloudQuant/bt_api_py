"""
Independent Reserve Spot Feed – three-layer sync / async wrappers.
"""

from __future__ import annotations

from typing import Any

from bt_api_py.feeds.live_independent_reserve.request_base import IndependentReserveRequestData


class IndependentReserveRequestDataSpot(IndependentReserveRequestData):
    """Independent Reserve Spot REST Feed."""

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)

    # ── market data ─────────────────────────────────────────────

    def get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_tick(symbol, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        return self.get_tick(symbol, extra_data, **kwargs)

    async def async_get_ticker(self, symbol, extra_data=None, **kwargs):
        return await self.async_get_tick(symbol, extra_data, **kwargs)

    def get_depth(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_depth(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_depth(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_depth(symbol, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra = self._get_exchange_info(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_deals(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_deals(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_deals(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_deals(symbol, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_recent_trades(self, symbol, extra_data=None, **kwargs):
        return self.get_deals(symbol, extra_data, **kwargs)

    async def async_get_recent_trades(self, symbol, extra_data=None, **kwargs):
        return await self.async_get_deals(symbol, extra_data, **kwargs)

    # ── trading ─────────────────────────────────────────────────

    def make_order(self, symbol, side, order_type, amount, price=None, extra_data=None, **kwargs):
        path, body, extra = self._make_order(
            symbol, side, order_type, amount, price, extra_data, **kwargs
        )
        return self.request(path, body=body, extra_data=extra)

    async def async_make_order(
        self, symbol, side, order_type, amount, price=None, extra_data=None, **kwargs
    ):
        path, body, extra = self._make_order(
            symbol, side, order_type, amount, price, extra_data, **kwargs
        )
        return await self.async_request(path, body=body, extra_data=extra)

    def cancel_order(self, order_id, extra_data=None, **kwargs):
        path, body, extra = self._cancel_order(order_id, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra)

    async def async_cancel_order(self, order_id, extra_data=None, **kwargs):
        path, body, extra = self._cancel_order(order_id, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra)

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, body, extra = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra)

    async def async_get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, body, extra = self._get_open_orders(symbol, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra)

    # ── account ─────────────────────────────────────────────────

    def get_balance(self, extra_data=None, **kwargs):
        path, body, extra = self._get_balance(extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra)

    async def async_get_balance(self, extra_data=None, **kwargs):
        path, body, extra = self._get_balance(extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra)

    def get_account(self, extra_data=None, **kwargs):
        path, body, extra = self._get_account(extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra)

    async def async_get_account(self, extra_data=None, **kwargs):
        path, body, extra = self._get_account(extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra)
