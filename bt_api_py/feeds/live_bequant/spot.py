"""
BeQuant Spot Trading Implementation

Thin sync/async wrappers that delegate to _get_xxx methods in request_base.
Mirrors HitBTC spot.py since BeQuant uses the same V3 API.
"""

from __future__ import annotations

from bt_api_py.feeds.live_bequant.request_base import BeQuantRequestData


class BeQuantRequestDataSpot(BeQuantRequestData):
    """BeQuant Spot – three-layer sync / async wrappers."""

    # ── public market data ──────────────────────────────────────

    def get_server_time(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_server_time(extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_server_time(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_server_time(extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data), self.async_callback
        )

    def get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_exchange_info(extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_exchange_info(extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data), self.async_callback
        )

    def get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_tick(symbol, extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_tick(symbol, extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data), self.async_callback
        )

    # aliases
    get_ticker = get_tick
    async_get_ticker = async_get_tick

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(
            symbol, count=count, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(
            symbol, count=count, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data), self.async_callback
        )

    def get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(
            symbol, period=period, count=count, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(
            symbol, period=period, count=count, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data), self.async_callback
        )

    def get_trade_history(self, symbol, count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_trade_history(
            symbol, count=count, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_trade_history(self, symbol, count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_trade_history(
            symbol, count=count, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data), self.async_callback
        )

    # aliases
    get_trades = get_trade_history
    async_get_trades = async_get_trade_history

    # ── private trading ─────────────────────────────────────────

    def make_order(
        self, symbol, amount, price=None, order_type="buy-limit", extra_data=None, **kwargs
    ):
        path, body, extra_data = self._make_order(
            symbol, amount, price=price, order_type=order_type, extra_data=extra_data, **kwargs
        )
        return self.request(path, body=body, extra_data=extra_data, is_sign=True)

    def async_make_order(
        self, symbol, amount, price=None, order_type="buy-limit", extra_data=None, **kwargs
    ):
        path, body, extra_data = self._make_order(
            symbol, amount, price=price, order_type=order_type, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=body, extra_data=extra_data, is_sign=True),
            self.async_callback,
        )

    def cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_order(
            symbol=symbol, order_id=order_id, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def async_cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_order(
            symbol=symbol, order_id=order_id, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            self.async_callback,
        )

    def query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(
            symbol=symbol, order_id=order_id, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def async_query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(
            symbol=symbol, order_id=order_id, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            self.async_callback,
        )

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def async_get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            self.async_callback,
        )

    # ── private account ─────────────────────────────────────────

    def get_account(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_account(extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def async_get_account(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_account(extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            self.async_callback,
        )

    def get_balance(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def async_get_balance(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            self.async_callback,
        )
