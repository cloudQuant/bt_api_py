"""
CoinEx Spot Feed – three-layer sync/async wrappers.
"""

from __future__ import annotations

from typing import Any

from bt_api_py.feeds.live_coinex.request_base import CoinExRequestData


class CoinExRequestDataSpot(CoinExRequestData):
    """CoinEx Spot Feed."""

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        kwargs.setdefault("exchange_name", "COINEX___SPOT")
        kwargs.setdefault("asset_type", "SPOT")
        super().__init__(data_queue, **kwargs)

    # ── public market data ──────────────────────────────────────

    def get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    get_ticker = get_tick
    async_get_ticker = async_get_tick

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def get_trade_history(self, symbol, count=50, extra_data=None, **kwargs):
        path, params, extra_data = self._get_trade_history(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_trade_history(self, symbol, count=50, extra_data=None, **kwargs):
        path, params, extra_data = self._get_trade_history(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    get_trades = get_trade_history
    async_get_trades = async_get_trade_history

    # ── private trading ─────────────────────────────────────────

    def make_order(
        self, symbol, size, price=None, order_type="buy-limit", extra_data=None, **kwargs
    ):
        path, body, extra_data = self._make_order(
            symbol, size, price, order_type, extra_data, **kwargs
        )
        return self.request(path, body=body, extra_data=extra_data, is_sign=True)

    def async_make_order(
        self, symbol, size, price=None, order_type="buy-limit", extra_data=None, **kwargs
    ):
        path, body, extra_data = self._make_order(
            symbol, size, price, order_type, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=body, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def async_cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def async_query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def async_get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def get_deals(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_deals(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def async_get_deals(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_deals(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    # ── private account ─────────────────────────────────────────

    def get_account(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_account(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def async_get_account(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_account(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def get_balance(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def async_get_balance(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )
