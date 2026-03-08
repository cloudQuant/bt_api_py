"""
BitMart Spot Feed – three-layer sync/async wrappers.

Public:  get_tick, get_depth, get_kline, get_trade_history, get_server_time, get_exchange_info
Private: make_order, cancel_order, query_order, get_open_orders, get_deals, get_account, get_balance
"""

from bt_api_py.feeds.live_bitmart.request_base import BitmartRequestData


class BitmartRequestDataSpot(BitmartRequestData):
    """BitMart Spot Feed with three-layer method wrappers."""

    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault("exchange_name", "BITMART___SPOT")
        kwargs.setdefault("asset_type", "SPOT")
        super().__init__(data_queue, **kwargs)

    # ── public endpoints ────────────────────────────────────────

    def get_server_time(self, extra_data=None, **kwargs):
        path, params, ed = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed)

    async def async_get_server_time(self, extra_data=None, **kwargs):
        path, params, ed = self._get_server_time(extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed)

    def get_exchange_info(self, extra_data=None, **kwargs):
        path, params, ed = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed)

    async def async_get_exchange_info(self, extra_data=None, **kwargs):
        path, params, ed = self._get_exchange_info(extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, ed = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed)

    async def async_get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, ed = self._get_tick(symbol, extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed)

    # aliases
    get_ticker = get_tick
    async_get_ticker = async_get_tick

    def get_depth(self, symbol, count=35, extra_data=None, **kwargs):
        path, params, ed = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed)

    async def async_get_depth(self, symbol, count=35, extra_data=None, **kwargs):
        path, params, ed = self._get_depth(symbol, count, extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed)

    def get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        path, params, ed = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed)

    async def async_get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        path, params, ed = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed)

    def get_trade_history(self, symbol, count=50, extra_data=None, **kwargs):
        path, params, ed = self._get_trade_history(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed)

    async def async_get_trade_history(self, symbol, count=50, extra_data=None, **kwargs):
        path, params, ed = self._get_trade_history(symbol, count, extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed)

    # aliases
    get_trades = get_trade_history
    async_get_trades = async_get_trade_history

    # ── private endpoints (signed) ──────────────────────────────

    def make_order(
        self, symbol, size, price=None, order_type="buy-limit", extra_data=None, **kwargs
    ):
        path, body, ed = self._make_order(symbol, size, price, order_type, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_make_order(
        self, symbol, size, price=None, order_type="buy-limit", extra_data=None, **kwargs
    ):
        path, body, ed = self._make_order(symbol, size, price, order_type, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=ed, is_sign=True)

    def cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, body, ed = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, body, ed = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=ed, is_sign=True)

    def query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, body, ed = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, body, ed = self._query_order(symbol, order_id, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=ed, is_sign=True)

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, body, ed = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, body, ed = self._get_open_orders(symbol, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=ed, is_sign=True)

    def get_deals(self, symbol=None, extra_data=None, **kwargs):
        path, body, ed = self._get_deals(symbol, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    async def async_get_deals(self, symbol=None, extra_data=None, **kwargs):
        path, body, ed = self._get_deals(symbol, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=ed, is_sign=True)

    def get_account(self, extra_data=None, **kwargs):
        path, params, ed = self._get_account(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed, is_sign=True)

    async def async_get_account(self, extra_data=None, **kwargs):
        path, params, ed = self._get_account(extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed, is_sign=True)

    def get_balance(self, extra_data=None, **kwargs):
        path, params, ed = self._get_balance(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=ed, is_sign=True)

    async def async_get_balance(self, extra_data=None, **kwargs):
        path, params, ed = self._get_balance(extra_data, **kwargs)
        return await self.async_request(path, params=params, extra_data=ed, is_sign=True)
