"""
CoinSpot Spot Feed – three-layer sync / async wrappers.
"""

from bt_api_py.feeds.live_coinspot.request_base import CoinSpotRequestData


class CoinSpotRequestDataSpot(CoinSpotRequestData):
    """CoinSpot Spot REST Feed."""

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

    def get_all_tickers(self, extra_data=None, **kwargs):
        path, params, extra = self._get_all_tickers(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_all_tickers(self, extra_data=None, **kwargs):
        path, params, extra = self._get_all_tickers(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_depth(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_depth(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_depth(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_depth(symbol, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_deals(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_deals(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_deals(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_deals(symbol, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    # ── trading (private POST) ──────────────────────────────────

    def make_order(self, symbol, amount, rate, side="buy", extra_data=None, **kwargs):
        path, body, extra = self._make_order(symbol, amount, rate, side, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra, is_sign=True)

    async def async_make_order(self, symbol, amount, rate, side="buy", extra_data=None, **kwargs):
        path, body, extra = self._make_order(symbol, amount, rate, side, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra, is_sign=True)

    def cancel_order(self, order_id, side="buy", extra_data=None, **kwargs):
        path, body, extra = self._cancel_order(order_id, side, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra, is_sign=True)

    async def async_cancel_order(self, order_id, side="buy", extra_data=None, **kwargs):
        path, body, extra = self._cancel_order(order_id, side, extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra, is_sign=True)

    # ── account (private POST) ──────────────────────────────────

    def get_balance(self, extra_data=None, **kwargs):
        path, body, extra = self._get_balance(extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra, is_sign=True)

    async def async_get_balance(self, extra_data=None, **kwargs):
        path, body, extra = self._get_balance(extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra, is_sign=True)

    def get_account(self, extra_data=None, **kwargs):
        path, body, extra = self._get_account(extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra, is_sign=True)

    async def async_get_account(self, extra_data=None, **kwargs):
        path, body, extra = self._get_account(extra_data, **kwargs)
        return await self.async_request(path, body=body, extra_data=extra, is_sign=True)
