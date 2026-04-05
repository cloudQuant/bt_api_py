"""
Bitstamp Spot Feed – three-layer sync/async wrappers.
"""

from __future__ import annotations

from bt_api_py.feeds.live_bitstamp.request_base import BitstampRequestData


class BitstampRequestDataSpot(BitstampRequestData):
    """Bitstamp Spot REST Feed."""

    # ── server_time ─────────────────────────────────────────────

    def get_server_time(self, **kw):
        path, params, ed = self._get_server_time(**kw)
        return self.request(path, params, extra_data=ed)

    async def async_get_server_time(self, **kw):
        path, params, ed = self._get_server_time(**kw)
        rd = await self.async_request(path, params, extra_data=ed)
        self.async_callback(rd)
        return rd

    # ── exchange_info ───────────────────────────────────────────

    def get_exchange_info(self, **kw):
        path, params, ed = self._get_exchange_info(**kw)
        return self.request(path, params, extra_data=ed)

    async def async_get_exchange_info(self, **kw):
        path, params, ed = self._get_exchange_info(**kw)
        rd = await self.async_request(path, params, extra_data=ed)
        self.async_callback(rd)
        return rd

    # ── tick / ticker ───────────────────────────────────────────

    def get_tick(self, symbol, **kw):
        path, params, ed = self._get_tick(symbol, **kw)
        return self.request(path, params, extra_data=ed)

    async def async_get_tick(self, symbol, **kw):
        path, params, ed = self._get_tick(symbol, **kw)
        rd = await self.async_request(path, params, extra_data=ed)
        self.async_callback(rd)
        return rd

    get_ticker = get_tick
    async_get_ticker = async_get_tick

    # ── depth ───────────────────────────────────────────────────

    def get_depth(self, symbol, count=50, **kw):
        path, params, ed = self._get_depth(symbol, count, **kw)
        return self.request(path, params, extra_data=ed)

    async def async_get_depth(self, symbol, count=50, **kw):
        path, params, ed = self._get_depth(symbol, count, **kw)
        rd = await self.async_request(path, params, extra_data=ed)
        self.async_callback(rd)
        return rd

    # ── kline ───────────────────────────────────────────────────

    def get_kline(self, symbol, period="1h", count=100, **kw):
        path, params, ed = self._get_kline(symbol, period, count, **kw)
        return self.request(path, params, extra_data=ed)

    async def async_get_kline(self, symbol, period="1h", count=100, **kw):
        path, params, ed = self._get_kline(symbol, period, count, **kw)
        rd = await self.async_request(path, params, extra_data=ed)
        self.async_callback(rd)
        return rd

    # ── trade_history / trades ──────────────────────────────────

    def get_trade_history(self, symbol, count=50, **kw):
        path, params, ed = self._get_trade_history(symbol, count, **kw)
        return self.request(path, params, extra_data=ed)

    async def async_get_trade_history(self, symbol, count=50, **kw):
        path, params, ed = self._get_trade_history(symbol, count, **kw)
        rd = await self.async_request(path, params, extra_data=ed)
        self.async_callback(rd)
        return rd

    get_trades = get_trade_history
    async_get_trades = async_get_trade_history

    # ── make_order ──────────────────────────────────────────────

    def make_order(self, symbol, size, price=None, order_type="buy-limit", **kw):
        path, body, ed = self._make_order(symbol, size, price, order_type, **kw)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_make_order(self, symbol, size, price=None, order_type="buy-limit", **kw):
        path, body, ed = self._make_order(symbol, size, price, order_type, **kw)
        rd = await self.async_request(path, body=body, extra_data=ed, is_sign=True)
        self.async_callback(rd)
        return rd

    # ── cancel_order ────────────────────────────────────────────

    def cancel_order(self, symbol=None, order_id=None, **kw):
        path, body, ed = self._cancel_order(symbol, order_id, **kw)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_cancel_order(self, symbol=None, order_id=None, **kw):
        path, body, ed = self._cancel_order(symbol, order_id, **kw)
        rd = await self.async_request(path, body=body, extra_data=ed, is_sign=True)
        self.async_callback(rd)
        return rd

    # ── query_order ─────────────────────────────────────────────

    def query_order(self, symbol=None, order_id=None, **kw):
        path, body, ed = self._query_order(symbol, order_id, **kw)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_query_order(self, symbol=None, order_id=None, **kw):
        path, body, ed = self._query_order(symbol, order_id, **kw)
        rd = await self.async_request(path, body=body, extra_data=ed, is_sign=True)
        self.async_callback(rd)
        return rd

    # ── open_orders ─────────────────────────────────────────────

    def get_open_orders(self, symbol=None, **kw):
        path, body, ed = self._get_open_orders(symbol, **kw)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_get_open_orders(self, symbol=None, **kw):
        path, body, ed = self._get_open_orders(symbol, **kw)
        rd = await self.async_request(path, body=body, extra_data=ed, is_sign=True)
        self.async_callback(rd)
        return rd

    # ── deals ───────────────────────────────────────────────────

    def get_deals(self, symbol=None, **kw):
        path, body, ed = self._get_deals(symbol, **kw)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_get_deals(self, symbol=None, **kw):
        path, body, ed = self._get_deals(symbol, **kw)
        rd = await self.async_request(path, body=body, extra_data=ed, is_sign=True)
        self.async_callback(rd)
        return rd

    # ── account ─────────────────────────────────────────────────

    def get_account(self, **kw):
        path, body, ed = self._get_account(**kw)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_get_account(self, **kw):
        path, body, ed = self._get_account(**kw)
        rd = await self.async_request(path, body=body, extra_data=ed, is_sign=True)
        self.async_callback(rd)
        return rd

    # ── balance ─────────────────────────────────────────────────

    def get_balance(self, **kw):
        path, body, ed = self._get_balance(**kw)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_get_balance(self, **kw):
        path, body, ed = self._get_balance(**kw)
        rd = await self.async_request(path, body=body, extra_data=ed, is_sign=True)
        self.async_callback(rd)
        return rd
