"""
BigONE Spot Feed implementation.

Three-layer pattern: _get_xxx (params), get_xxx (sync), async_get_xxx (async).
Private endpoints use is_sign=True for JWT Bearer auth.
"""

from bt_api_py.feeds.live_bigone.request_base import BigONERequestData


class BigONERequestDataSpot(BigONERequestData):
    """BigONE Spot Feed – full public + private REST wrappers."""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "BIGONE___SPOT")

    # ── public: server time ─────────────────────────────────────

    def get_server_time(self, extra_data=None, **kwargs):
        path, params, ed = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params, extra_data=ed)

    def async_get_server_time(self, extra_data=None, **kwargs):
        path, params, ed = self._get_server_time(extra_data, **kwargs)
        self.submit(self.async_request(path, params, extra_data=ed), callback=self.async_callback)

    # ── public: exchange info ───────────────────────────────────

    def get_exchange_info(self, extra_data=None, **kwargs):
        path, params, ed = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params, extra_data=ed)

    def async_get_exchange_info(self, extra_data=None, **kwargs):
        path, params, ed = self._get_exchange_info(extra_data, **kwargs)
        self.submit(self.async_request(path, params, extra_data=ed), callback=self.async_callback)

    # ── public: ticker ──────────────────────────────────────────

    def get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, ed = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=ed)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, ed = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params, extra_data=ed), callback=self.async_callback)

    get_ticker = get_tick
    async_get_ticker = async_get_tick

    # ── public: depth ───────────────────────────────────────────

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path, params, ed = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=ed)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path, params, ed = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(self.async_request(path, params, extra_data=ed), callback=self.async_callback)

    # ── public: kline ───────────────────────────────────────────

    def get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        path, params, ed = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=ed)

    def async_get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        path, params, ed = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(self.async_request(path, params, extra_data=ed), callback=self.async_callback)

    # ── public: trade history ───────────────────────────────────

    def get_trade_history(self, symbol, count=100, extra_data=None, **kwargs):
        path, params, ed = self._get_trade_history(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=ed)

    def async_get_trade_history(self, symbol, count=100, extra_data=None, **kwargs):
        path, params, ed = self._get_trade_history(symbol, count, extra_data, **kwargs)
        self.submit(self.async_request(path, params, extra_data=ed), callback=self.async_callback)

    get_trades = get_trade_history
    async_get_trades = async_get_trade_history

    # ── private: make order ─────────────────────────────────────

    def make_order(self, symbol, amount, price=None, order_type="buy-limit", extra_data=None, **kwargs):
        path, body, ed = self._make_order(symbol, amount, price, order_type, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=ed, is_sign=True)

    def async_make_order(self, symbol, amount, price=None, order_type="buy-limit", extra_data=None, **kwargs):
        path, body, ed = self._make_order(symbol, amount, price, order_type, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=ed, is_sign=True), callback=self.async_callback)

    # ── private: cancel order ───────────────────────────────────

    def cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, params, ed = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params, extra_data=ed, is_sign=True)

    def async_cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, params, ed = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params, extra_data=ed, is_sign=True), callback=self.async_callback)

    # ── private: query order ────────────────────────────────────

    def query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, params, ed = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params, extra_data=ed, is_sign=True)

    def async_query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, params, ed = self._query_order(symbol, order_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params, extra_data=ed, is_sign=True), callback=self.async_callback)

    # ── private: open orders ────────────────────────────────────

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, ed = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=ed, is_sign=True)

    def async_get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, ed = self._get_open_orders(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params, extra_data=ed, is_sign=True), callback=self.async_callback)

    # ── private: account ────────────────────────────────────────

    def get_account(self, extra_data=None, **kwargs):
        path, params, ed = self._get_account(extra_data, **kwargs)
        return self.request(path, params, extra_data=ed, is_sign=True)

    def async_get_account(self, extra_data=None, **kwargs):
        path, params, ed = self._get_account(extra_data, **kwargs)
        self.submit(self.async_request(path, params, extra_data=ed, is_sign=True), callback=self.async_callback)

    # ── private: balance ────────────────────────────────────────

    def get_balance(self, extra_data=None, **kwargs):
        path, params, ed = self._get_balance(extra_data, **kwargs)
        return self.request(path, params, extra_data=ed, is_sign=True)

    def async_get_balance(self, extra_data=None, **kwargs):
        path, params, ed = self._get_balance(extra_data, **kwargs)
        self.submit(self.async_request(path, params, extra_data=ed, is_sign=True), callback=self.async_callback)
