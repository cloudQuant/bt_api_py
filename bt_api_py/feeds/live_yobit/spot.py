"""
YoBit Spot Feed – three-layer sync / async wrappers + WSS stubs.
"""

from __future__ import annotations

from typing import Any

from bt_api_py.containers.exchanges.yobit_exchange_data import YobitExchangeDataSpot
from bt_api_py.feeds.live_yobit.request_base import YobitRequestData
from bt_api_py.logging_factory import get_logger


class YobitRequestDataSpot(YobitRequestData):
    """YoBit Spot REST Feed."""

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)

    # ── server time ─────────────────────────────────────────────

    def get_server_time(self, extra_data=None, **kwargs):
        path, params, extra = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    def async_get_server_time(self, extra_data=None, **kwargs):
        path, params, extra = self._get_server_time(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params, extra_data=extra),
            callback=self.async_callback,
        )

    # ── market data ─────────────────────────────────────────────

    def get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params, extra_data=extra),
            callback=self.async_callback,
        )

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        return self.get_tick(symbol, extra_data, **kwargs)

    def async_get_ticker(self, symbol, extra_data=None, **kwargs):
        self.async_get_tick(symbol, extra_data, **kwargs)

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path, params, extra = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path, params, extra = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params, extra_data=extra),
            callback=self.async_callback,
        )

    def get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    def async_get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra = self._get_exchange_info(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params, extra_data=extra),
            callback=self.async_callback,
        )

    # ── account ─────────────────────────────────────────────────

    def get_balance(self, extra_data=None, **kwargs):
        path, params, extra = self._get_balance(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    def async_get_balance(self, extra_data=None, **kwargs):
        path, params, extra = self._get_balance(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params, extra_data=extra),
            callback=self.async_callback,
        )

    def get_account(self, extra_data=None, **kwargs):
        path, params, extra = self._get_account(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    def async_get_account(self, extra_data=None, **kwargs):
        path, params, extra = self._get_account(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params, extra_data=extra),
            callback=self.async_callback,
        )

    # ── trading ──────────────────────────────────────────────────

    def make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        path, params, extra = self._make_order(
            symbol,
            vol,
            price,
            order_type,
            offset,
            post_only,
            client_order_id,
            extra_data,
            **kwargs,
        )
        return self.request(path, params, extra_data=extra)

    def async_make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        path, params, extra = self._make_order(
            symbol,
            vol,
            price,
            order_type,
            offset,
            post_only,
            client_order_id,
            extra_data,
            **kwargs,
        )
        self.submit(
            self.async_request(path, params, extra_data=extra),
            callback=self.async_callback,
        )

    def cancel_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    def async_cancel_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params, extra_data=extra),
            callback=self.async_callback,
        )

    def query_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    def async_query_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra = self._query_order(symbol, order_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params, extra_data=extra),
            callback=self.async_callback,
        )


# ── WebSocket stubs ──────────────────────────────────────────


class YobitMarketWssDataSpot:
    """YoBit Spot Market WebSocket Data Handler (stub)."""

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        self.data_queue = data_queue
        self._params = YobitExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "yobit_spot_market_wss.log")
        self.request_logger = get_logger("yobit_spot_market_wss")
        self.wss_url = kwargs.get("wss_url", self._params.wss_url)
        self.topics = kwargs.get("topics", [])
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class YobitAccountWssDataSpot:
    """YoBit Spot Account WebSocket Data Handler (stub)."""

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        self.data_queue = data_queue
        self._params = YobitExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "yobit_spot_account_wss.log")
        self.request_logger = get_logger("yobit_spot_account_wss")
        self.wss_url = kwargs.get("wss_url", self._params.wss_url)
        self.topics = kwargs.get("topics", [])
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False
