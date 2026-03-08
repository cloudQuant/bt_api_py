"""
Coinone Spot Feed – three-layer sync / async wrappers.
"""

from bt_api_py.feeds.live_coinone.request_base import CoinoneRequestData


class CoinoneRequestDataSpot(CoinoneRequestData):
    """Coinone Spot REST Feed."""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)

    # ── market data (public GET) ────────────────────────────────

    def get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra = self._get_exchange_info(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

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

    def get_depth(self, symbol, count=15, extra_data=None, **kwargs):
        path, params, extra = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_depth(self, symbol, count=15, extra_data=None, **kwargs):
        path, params, extra = self._get_depth(symbol, count, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        path, params, extra = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        path, params, extra = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_trade_history(self, symbol, count=50, extra_data=None, **kwargs):
        path, params, extra = self._get_trade_history(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_trade_history(self, symbol, count=50, extra_data=None, **kwargs):
        path, params, extra = self._get_trade_history(symbol, count, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_trades(self, symbol, count=50, extra_data=None, **kwargs):
        return self.get_trade_history(symbol, count, extra_data, **kwargs)

    async def async_get_trades(self, symbol, count=50, extra_data=None, **kwargs):
        return await self.async_get_trade_history(symbol, count, extra_data, **kwargs)

    # ── trading (private POST) ──────────────────────────────────

    def make_order(self, symbol, size, price=None, order_type="bid", extra_data=None, **kwargs):
        path, body, extra = self._make_order(symbol, size, price, order_type, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra, is_sign=True)

    async def async_make_order(
        self, symbol, size, price=None, order_type="bid", extra_data=None, **kwargs
    ):
        path, body, extra = self._make_order(symbol, size, price, order_type, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra, is_sign=True)

    def cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, body, extra = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra, is_sign=True)

    async def async_cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, body, extra = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra, is_sign=True)

    def query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, body, extra = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra, is_sign=True)

    async def async_query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, body, extra = self._query_order(symbol, order_id, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra, is_sign=True)

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, body, extra = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra, is_sign=True)

    async def async_get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, body, extra = self._get_open_orders(symbol, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra, is_sign=True)

    def get_deals(self, symbol=None, extra_data=None, **kwargs):
        path, body, extra = self._get_deals(symbol, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra, is_sign=True)

    async def async_get_deals(self, symbol=None, extra_data=None, **kwargs):
        path, body, extra = self._get_deals(symbol, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra, is_sign=True)

    # ── account (private POST) ──────────────────────────────────

    def get_account(self, extra_data=None, **kwargs):
        path, body, extra = self._get_account(extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra, is_sign=True)

    async def async_get_account(self, extra_data=None, **kwargs):
        path, body, extra = self._get_account(extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra, is_sign=True)

    def get_balance(self, extra_data=None, **kwargs):
        path, body, extra = self._get_balance(extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra, is_sign=True)

    async def async_get_balance(self, extra_data=None, **kwargs):
        path, body, extra = self._get_balance(extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra, is_sign=True)
