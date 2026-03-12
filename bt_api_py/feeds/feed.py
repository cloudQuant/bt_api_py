"""feed类, 用于处理数据、获取数据、向交易所传递数据"""

import time as _time
from typing import Any

from bt_api_py.exceptions import RequestError, RequestFailedError, RequestTimeoutError
from bt_api_py.feeds.capability import CapabilityMixin
from bt_api_py.feeds.connection_mixin import ConnectionMixin
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.functions.async_base import AsyncBase
from bt_api_py.logging_factory import get_logger


class Feed(AsyncBase, ConnectionMixin, CapabilityMixin):
    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        """
        feed initial
        :param data_queue: queue.Queue()
        :param kwargs: pass key-worded, variable-length arguments.
        """
        super().__init__(**kwargs)
        self.__init_connection__()
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "")
        self.proxies = kwargs.get("proxies")
        self.logger = get_logger("feed")
        self._http_client = HttpClient(
            venue=self.exchange_name,
            timeout=kwargs.get("timeout", 10.0),
            proxies=self.proxies,
        )

    def handle_timeout_exception(self, url, method, body, timeout, e):
        """
        handle timeout exception
        :param url: str, url
        :param method: str, method
        :param body: dict, body params
        :param timeout: int, timeout value
        :param e: exception type, exception value, exception traceback
        :return: None
        """
        self.logger.warning(
            f"exchange -> {self.exchange_name}\n "
            f"url -> {url},\n "
            f"method -> {method},\n "
            f"body -> {body},\n"
            f"rest timeout -> {timeout}s,\n"
            f"e -> {e}"
        )
        self.raise_timeout(timeout, self.exchange_name)

    def handle_request_exception(self, url, method, body, exception):
        """
        handle request exception
        :param url: str, url
        :param method: str, method
        :param body: dict, body params
        :param exception: exception type, exception value, exception traceback
        :return: None
        """
        self.logger.warning(
            f"exchange -> {self.exchange_name}\n "
            f"rest error -> \n "
            f"URL -> {url}\n"
            f"METHOD -> {method}\n"
            f"BODY -> {body}\n"
            f"ERROR: {exception}"
        )
        raise exception

    def handle_json_decode_error(self, url, headers, body, e):
        """
        handle json decode error
        :param url: str, url
        :param headers: dict headers
        :param body: dict, body params
        :param e: exception type, exception value, exception traceback
        :return: None
        """
        self.logger.warning(f"url -> {url},\n headers -> {headers},\n body:{body},\n e:{e}")
        self.raise400(self.exchange_name)

    def raise_path_error(self, *args):
        """
        raise path error
        :param args: pass a variable number of arguments
        :return: None
        """
        raise RequestError(self.exchange_name, detail=f"api not access {args}")

    def raise_timeout(self, timeout, *args):
        """
        raise timeout error
        :param timeout: int, timeout
        :param args: pass a variable number of arguments
        :return: None
        """
        raise RequestTimeoutError(self.exchange_name, timeout=timeout)

    def raise400(self, *args):
        """
        raise 400 error
        :param args: pass a variable number of arguments
        :return: None
        """
        raise RequestError(self.exchange_name, detail="rest request response <400>")

    def raise_proxy_error(self, *args):
        """
        raise proxy error
        :param args: pass a variable number of arguments
        :return: None
        """
        raise RequestError(self.exchange_name, detail="proxy_error")

    def http_request(self, method, url, headers=None, body=None, timeout=10, max_retries=3):
        """
        request http function
        :param method: str, request method, get, post, put, delete
        :param url: str, request url
        :param headers: dict, request headers
        :param body: dict, body
        :param timeout: int, request timeout
        :param max_retries: int, max retry count for transient errors
        :return: json, http response
        """
        if headers is None:
            headers = {}
        for attempt in range(max_retries):
            try:
                return self._http_client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json_data=body,
                    timeout=timeout,
                )
            except RequestFailedError as e:
                msg = str(e)
                # Non-retryable: 404/410 endpoint errors
                if "404" in msg or "410" in msg:
                    raise RequestError(
                        self.exchange_name,
                        detail=f"endpoint gone/not found: {url}",
                    ) from None
                # Retryable errors (timeout, connection, etc.)
                if attempt < max_retries - 1:
                    self.logger.warning(f"Retry {attempt + 1}/{max_retries} for {url}: {e}")
                    _time.sleep(1)
                    continue
                # Final attempt failed
                if "timeout" in msg.lower():
                    self.handle_timeout_exception(url, method, body, timeout, e)
                else:
                    self.handle_request_exception(url, method, body, e)
            except Exception as e:
                if attempt < max_retries - 1:
                    self.logger.warning(
                        f"Retry {attempt + 1}/{max_retries} unexpected error for {url}: {e}"
                    )
                    _time.sleep(1)
                    continue
                self.handle_request_exception(url, method, body, e)

    def cancel_all(self, symbol, extra_data=None, **kwargs):
        """
        cancel all order
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """
        cancel order by order_id
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param order_id: order_id, default is None, can be a string passed by user
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """
        cancel order by order_id using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param order_id: order_id, default is None, can be a string passed by user
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """
        get account info
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """
        get account info using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_balance(self, symbol, extra_data=None, **kwargs):
        """
        get balance by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_balance(self, symbol=None, extra_data=None, **kwargs):
        """
        get balance by symbol using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_clear_price(self, symbol, extra_data=None, **kwargs):
        """
        get clear price by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_clear_price(self, symbol, extra_data=None, **kwargs):
        """
        get clear price by symbol using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_deals(
        self, symbol, count=100, start_time=None, end_time=None, extra_data=None, **kwargs
    ):
        """
        get trade history by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param count: default 100, the maximum amount of trade history can be got once
        :param start_time: default None, start time of trade history
        :param end_time: default None, end time of trade history
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_deals(
        self, symbol, count=100, start_time=None, end_time=None, extra_data=None, **kwargs
    ):
        """
        get trade history by symbol using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param count: default 100, the maximum amount of trade history can be got once
        :param start_time: default None, start time of trade history
        :param end_time: default None, end time of trade history
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_depth(self, symbol: str, count: int = 20, extra_data: Any = None, **kwargs: Any) -> Any:
        """
        get order_book_data by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param count: default 20, the maximum number of order book level
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """
        get order_book_data by symbol using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param count: default 20, the maximum number of order book level
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_funding_rate(self, symbol, extra_data=None, **kwargs):
        """
        get funding rate by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_funding_rate(self, symbol, extra_data=None, **kwargs):
        """
        get funding rate by symbol using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_kline(
        self,
        symbol: str,
        period: str,
        count: int = 20,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """
        get kline or bars by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param period: str, the period of the bar, e.g. "1m"
        :param count: default 20, the maximum number of order book level
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """
        get kline or bars by symbol using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param period: str, the period of the bar, e.g. "1m"
        :param count: default 20, the maximum number of order book level
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_open_orders(self, symbol, extra_data=None, **kwargs):
        """
        get open orders by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_open_orders(self, symbol, extra_data=None, **kwargs):
        """
        get open orders by symbol using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """
        get tick price by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """
        get tick price by symbol using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        offset: str = "open",
        post_only: bool = False,
        client_order_id: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """
        make order by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param volume: the order volume
        :param price: the order price
        :param order_type: the order type
        :param offset: the order offset
        :param post_only: post_only flag, default is False
        :param client_order_id: the client_order_id, defined by user
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_make_order(
        self,
        symbol,
        volume,
        price,
        order_type,
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """
        make order by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param volume: the order volume
        :param price: the order price
        :param order_type: the order type
        :param offset: the order offset
        :param post_only: post_only flag, default is False
        :param client_order_id: the client_order_id, defined by user
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """
        query order by order_id
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param order_id: order_id, default is None, can be a string passed by user
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """
        query order by order_id using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param order_id: order_id, default is None, can be a string passed by user
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_mark_price(self, symbol, extra_data=None, **kwargs):
        """
        get mark price from okx
        :param symbol: symbol name, eg: BTC-USDT.
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: None
        """
        raise NotImplementedError

    def async_get_mark_price(self, symbol, extra_data=None, **kwargs):
        """
        get mark price from okx using async, it is not blocked and push data to data_queue
        :param symbol: symbol name, eg: BTC-USDT.
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: None
        """
        raise NotImplementedError

    def get_position(self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any) -> Any:
        """
        get position info by symbol (futures/options)
        :param symbol: default None, get all positions.
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_position(self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any) -> Any:
        """
        get position info by symbol using async
        :param symbol: default None, get all positions.
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError
