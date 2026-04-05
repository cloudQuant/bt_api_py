"""
CoinSwitch Spot Feed – three-layer sync / async wrappers.
"""

from __future__ import annotations

from typing import Any

from bt_api_py.feeds.live_coinswitch.request_base import CoinSwitchRequestData


class CoinSwitchRequestDataSpot(CoinSwitchRequestData):
    """CoinSwitch Spot REST Feed."""

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

    def get_all_tickers(self, extra_data=None, **kwargs):
        path, params, extra = self._get_all_tickers(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_all_tickers(self, extra_data=None, **kwargs):
        path, params, extra = self._get_all_tickers(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra = self._get_exchange_info(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_trade_history(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_trade_history(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_trade_history(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_trade_history(symbol, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_trades(self, symbol, extra_data=None, **kwargs):
        return self.get_trade_history(symbol, extra_data, **kwargs)

    async def async_get_trades(self, symbol, extra_data=None, **kwargs):
        return await self.async_get_trade_history(symbol, extra_data, **kwargs)

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
        path, params, extra = self._cancel_order(order_id, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_cancel_order(self, order_id, extra_data=None, **kwargs):
        path, params, extra = self._cancel_order(order_id, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_open_orders(self, extra_data=None, **kwargs):
        path, params, extra = self._get_open_orders(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_open_orders(self, extra_data=None, **kwargs):
        path, params, extra = self._get_open_orders(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    # ── account ─────────────────────────────────────────────────

    def get_balance(self, extra_data=None, **kwargs):
        path, params, extra = self._get_balance(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_balance(self, extra_data=None, **kwargs):
        path, params, extra = self._get_balance(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_account(self, extra_data=None, **kwargs):
        path, params, extra = self._get_account(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_account(self, extra_data=None, **kwargs):
        path, params, extra = self._get_account(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)
