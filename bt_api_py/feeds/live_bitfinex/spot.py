"""
Bitfinex Spot trading feed implementation.
Three-layer pattern: _get_xxx() -> get_xxx() / async_get_xxx()
"""

from bt_api_py.containers.exchanges.bitfinex_exchange_data import BitfinexExchangeDataSpot
from bt_api_py.feeds.live_bitfinex.request_base import BitfinexRequestData
from bt_api_py.logging_factory import get_logger


class BitfinexRequestDataSpot(BitfinexRequestData):
    """Bitfinex Spot Trading REST API implementation."""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "BITFINEX___SPOT")
        self.logger_name = kwargs.get("logger_name", "bitfinex_spot_feed.log")
        self._params = BitfinexExchangeDataSpot()
        self.request_logger = get_logger("bitfinex_spot_feed")
        self.async_logger = get_logger("bitfinex_spot_feed")

    # ==================== Market Data Public Methods ====================

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time"""
        path, params, extra_data = self._get_server_time(extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def get_exchange_info(self, symbol=None, extra_data=None, **kwargs):
        """Get exchange information"""
        path, params, extra_data = self._get_exchange_info(symbol=symbol, extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker data"""
        path, params, extra_data = self._get_ticker(symbol=symbol, extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    # Alias for standard interface
    get_tick = get_ticker

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get orderbook data"""
        path, params, extra_data = self._get_order_book(
            symbol=symbol, precision="P0", length=str(count), extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def get_kline(self, symbol, period="1m", count=20, extra_data=None, **kwargs):
        """Get kline data"""
        path, params, extra_data = self._get_klines(
            symbol=symbol, period=period, limit=count, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def get_trade_history(self, symbol, limit=100, extra_data=None, **kwargs):
        """Get recent trade history"""
        path, params, extra_data = self._get_trade_history(
            symbol=symbol, limit=limit, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    # ==================== Async Market Data Methods ====================

    def async_get_server_time(self, extra_data=None, **kwargs):
        """Get server time asynchronously"""
        path, params, extra_data = self._get_server_time(extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    def async_get_exchange_info(self, symbol=None, extra_data=None, **kwargs):
        """Get exchange information asynchronously"""
        path, params, extra_data = self._get_exchange_info(symbol=symbol, extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    def async_get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker data asynchronously"""
        path, params, extra_data = self._get_ticker(symbol=symbol, extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    async_get_tick = async_get_ticker

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get orderbook data asynchronously"""
        path, params, extra_data = self._get_order_book(
            symbol=symbol, precision="P0", length=str(count), extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    def async_get_kline(self, symbol, period="1m", count=20, extra_data=None, **kwargs):
        """Get kline data asynchronously"""
        path, params, extra_data = self._get_klines(
            symbol=symbol, period=period, limit=count, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    # ==================== Trading Public Methods ====================

    def make_order(self, symbol, vol, price=None, order_type="buy-limit",
                   offset="open", post_only=False, client_order_id=None,
                   extra_data=None, **kwargs):
        """Place a new order"""
        path, params, extra_data = self._make_order(
            symbol=symbol, vol=vol, price=price, order_type=order_type,
            offset=offset, post_only=post_only, client_order_id=client_order_id,
            extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def cancel_order(self, symbol=None, order_id=None, client_order_id=None,
                     extra_data=None, **kwargs):
        """Cancel an existing order"""
        path, params, extra_data = self._cancel_order(
            symbol=symbol, order_id=order_id, client_order_id=client_order_id,
            extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def query_order(self, symbol=None, order_id=None, client_order_id=None,
                    extra_data=None, **kwargs):
        """Query an order's status"""
        path, params, extra_data = self._get_order(
            symbol=symbol, order_id=order_id, client_order_id=client_order_id,
            extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Query all open orders"""
        path, params, extra_data = self._get_open_orders(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def get_account(self, extra_data=None, **kwargs):
        """Get account information"""
        path, params, extra_data = self._get_account(extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def get_balance(self, extra_data=None, **kwargs):
        """Get balance data — delegates to get_account."""
        return self.get_account(extra_data=extra_data, **kwargs)

    # ==================== Async Private/Account Methods ====================

    def async_make_order(self, symbol, vol, price=None, order_type="buy-limit",
                         offset="open", post_only=False, client_order_id=None,
                         extra_data=None, **kwargs):
        """Place a new order asynchronously"""
        path, params, extra_data = self._make_order(
            symbol=symbol, vol=vol, price=price, order_type=order_type,
            offset=offset, post_only=post_only, client_order_id=client_order_id,
            extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def async_cancel_order(self, symbol=None, order_id=None, client_order_id=None,
                           extra_data=None, **kwargs):
        """Cancel an order asynchronously"""
        path, params, extra_data = self._cancel_order(
            symbol=symbol, order_id=order_id, client_order_id=client_order_id,
            extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def async_query_order(self, symbol=None, order_id=None, client_order_id=None,
                          extra_data=None, **kwargs):
        """Query an order asynchronously"""
        path, params, extra_data = self._get_order(
            symbol=symbol, order_id=order_id, client_order_id=client_order_id,
            extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def async_get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders asynchronously"""
        path, params, extra_data = self._get_open_orders(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def async_get_account(self, extra_data=None, **kwargs):
        """Get account information asynchronously"""
        path, params, extra_data = self._get_account(extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def async_get_balance(self, extra_data=None, **kwargs):
        """Get account balance asynchronously"""
        self.async_get_account(extra_data=extra_data, **kwargs)
