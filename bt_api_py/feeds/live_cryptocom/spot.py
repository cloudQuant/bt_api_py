"""
Crypto.com Spot trading feed implementation.
"""

from bt_api_py.containers.exchanges.cryptocom_exchange_data import CryptoComExchangeDataSpot
from bt_api_py.feeds.live_cryptocom.request_base import CryptoComRequestData
from bt_api_py.logging_factory import get_logger


class CryptoComRequestDataSpot(CryptoComRequestData):
    """Crypto.com Spot trading feed.

    Implements the three-layer method pattern:
      _get_xxx()      → assemble (path, params, extra_data)
      get_xxx()       → sync call via self.request()
      async_get_xxx() → async call via self.submit(self.async_request(...))
    """

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "CRYPTOCOM___SPOT")
        self.logger_name = kwargs.get("logger_name", "cryptocom_spot_feed.log")
        self._params = CryptoComExchangeDataSpot()
        self.request_logger = get_logger("cryptocom_spot_feed")
        self.async_logger = get_logger("cryptocom_spot_feed")

    # ==================== Public — Server Time ====================

    def get_server_time(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_server_time(extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def async_get_server_time(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_server_time(extra_data=extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False))

    # ==================== Public — Exchange Info ====================

    def get_exchange_info(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_exchange_info(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def async_get_exchange_info(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_exchange_info(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False))

    # ==================== Public — Ticker ====================

    def get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_tick(symbol, extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_tick(symbol, extra_data=extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False))

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        """Alias for get_tick."""
        return self.get_tick(symbol, extra_data=extra_data, **kwargs)

    # ==================== Public — Depth ====================

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(
            symbol, size=count, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(
            symbol, size=count, extra_data=extra_data, **kwargs
        )
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False))

    # ==================== Public — Kline ====================

    def get_kline(self, symbol, period, count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(
            symbol, period=period, count=count, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def async_get_kline(self, symbol, period, count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(
            symbol, period=period, count=count, extra_data=extra_data, **kwargs
        )
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False))

    # ==================== Public — Trade History ====================

    def get_trade_history(self, symbol, count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_trade_history(
            symbol, count=count, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def async_get_trade_history(self, symbol, count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_trade_history(
            symbol, count=count, extra_data=extra_data, **kwargs
        )
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False))

    # ==================== Private — Make Order ====================

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
        path, params, extra_data = self._make_order(
            symbol,
            vol,
            price=price,
            order_type=order_type,
            offset=offset,
            post_only=post_only,
            client_order_id=client_order_id,
            extra_data=extra_data,
            **kwargs,
        )
        return self.request(path, body=params, extra_data=extra_data, is_sign=True)

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
        path, params, extra_data = self._make_order(
            symbol,
            vol,
            price=price,
            order_type=order_type,
            offset=offset,
            post_only=post_only,
            client_order_id=client_order_id,
            extra_data=extra_data,
            **kwargs,
        )
        self.submit(self.async_request(path, body=params, extra_data=extra_data, is_sign=True))

    # ==================== Private — Cancel Order ====================

    def cancel_order(
        self, symbol=None, order_id=None, client_order_id=None, extra_data=None, **kwargs
    ):
        path, params, extra_data = self._cancel_order(
            symbol=symbol,
            order_id=order_id,
            client_order_id=client_order_id,
            extra_data=extra_data,
            **kwargs,
        )
        return self.request(path, body=params, extra_data=extra_data, is_sign=True)

    def async_cancel_order(
        self, symbol=None, order_id=None, client_order_id=None, extra_data=None, **kwargs
    ):
        path, params, extra_data = self._cancel_order(
            symbol=symbol,
            order_id=order_id,
            client_order_id=client_order_id,
            extra_data=extra_data,
            **kwargs,
        )
        self.submit(self.async_request(path, body=params, extra_data=extra_data, is_sign=True))

    # ==================== Private — Query Order ====================

    def query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(
            symbol=symbol,
            order_id=order_id,
            extra_data=extra_data,
            **kwargs,
        )
        return self.request(path, body=params, extra_data=extra_data, is_sign=True)

    def async_query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(
            symbol=symbol,
            order_id=order_id,
            extra_data=extra_data,
            **kwargs,
        )
        self.submit(self.async_request(path, body=params, extra_data=extra_data, is_sign=True))

    # ==================== Private — Open Orders ====================

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(
            symbol=symbol,
            extra_data=extra_data,
            **kwargs,
        )
        return self.request(path, body=params, extra_data=extra_data, is_sign=True)

    def async_get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(
            symbol=symbol,
            extra_data=extra_data,
            **kwargs,
        )
        self.submit(self.async_request(path, body=params, extra_data=extra_data, is_sign=True))

    # ==================== Private — Account ====================

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_account(
            symbol=symbol,
            extra_data=extra_data,
            **kwargs,
        )
        return self.request(path, body=params, extra_data=extra_data, is_sign=True)

    def async_get_account(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_account(
            symbol=symbol,
            extra_data=extra_data,
            **kwargs,
        )
        self.submit(self.async_request(path, body=params, extra_data=extra_data, is_sign=True))

    # ==================== Private — Balance ====================

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(
            symbol=symbol,
            extra_data=extra_data,
            **kwargs,
        )
        return self.request(path, body=params, extra_data=extra_data, is_sign=True)

    def async_get_balance(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(
            symbol=symbol,
            extra_data=extra_data,
            **kwargs,
        )
        self.submit(self.async_request(path, body=params, extra_data=extra_data, is_sign=True))
