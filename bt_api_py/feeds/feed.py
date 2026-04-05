"""feed类, 用于处理数据、获取数据、向交易所传递数据"""

from __future__ import annotations

import time as _time
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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

    def _is_sensitive_key(self, key: Any) -> bool:
        normalized = str(key).replace("-", "").replace("_", "").lower()
        sensitive_tokens = (
            "apikey",
            "accesskey",
            "secret",
            "token",
            "signature",
            "password",
            "passphrase",
            "authorization",
            "privatekey",
            "publickey",
        )
        return any(token in normalized for token in sensitive_tokens)

    def _sanitize_for_log(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: "***" if self._is_sensitive_key(key) else self._sanitize_for_log(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._sanitize_for_log(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._sanitize_for_log(item) for item in value)
        return value

    def _sanitize_url_for_log(self, url: Any) -> Any:
        if not isinstance(url, str):
            return url
        parsed = urlsplit(url)
        if not parsed.query:
            return url
        query_items = parse_qsl(parsed.query, keep_blank_values=True)
        sanitized_query = [
            (key, "***" if self._is_sensitive_key(key) else value) for key, value in query_items
        ]
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(sanitized_query, doseq=True),
                parsed.fragment,
            )
        )

    def handle_timeout_exception(
        self,
        url: str,
        method: str,
        body: Any,
        timeout: float | int,
        e: Exception,
    ) -> None:
        """
        handle timeout exception
        :param url: str, url
        :param method: str, method
        :param body: dict, body params
        :param timeout: int, timeout value
        :param e: exception type, exception value, exception traceback
        :return: None
        """
        sanitized_url = self._sanitize_url_for_log(url)
        sanitized_body = self._sanitize_for_log(body)
        self.logger.warning(
            f"exchange -> {self.exchange_name}\n "
            f"url -> {sanitized_url},\n "
            f"method -> {method},\n "
            f"body -> {sanitized_body},\n"
            f"rest timeout -> {timeout}s,\n"
            f"e -> {e}"
        )
        self.raise_timeout(timeout, self.exchange_name)

    def handle_request_exception(
        self, url: str, method: str, body: Any, exception: Exception
    ) -> None:
        """
        handle request exception
        :param url: str, url
        :param method: str, method
        :param body: dict, body params
        :param exception: exception type, exception value, exception traceback
        :return: None
        """
        sanitized_url = self._sanitize_url_for_log(url)
        sanitized_body = self._sanitize_for_log(body)
        self.logger.warning(
            f"exchange -> {self.exchange_name}\n "
            f"rest error -> \n "
            f"URL -> {sanitized_url}\n"
            f"METHOD -> {method}\n"
            f"BODY -> {sanitized_body}\n"
            f"ERROR: {exception}"
        )
        raise exception

    def handle_json_decode_error(
        self,
        url: str,
        headers: Mapping[str, Any] | None,
        body: Any,
        e: Exception,
    ) -> None:
        """
        handle json decode error
        :param url: str, url
        :param headers: dict headers
        :param body: dict, body params
        :param e: exception type, exception value, exception traceback
        :return: None
        """
        sanitized_url = self._sanitize_url_for_log(url)
        sanitized_headers = self._sanitize_for_log(headers)
        sanitized_body = self._sanitize_for_log(body)
        self.logger.warning(
            f"url -> {sanitized_url},\n headers -> {sanitized_headers},\n body:{sanitized_body},\n e:{e}"
        )
        self.raise400(self.exchange_name)

    def raise_path_error(self, *args: Any) -> None:
        """
        raise path error
        :param args: pass a variable number of arguments
        :return: None
        """
        raise RequestError(self.exchange_name, detail=f"api not access {args}")

    def raise_timeout(self, timeout: float | int, *args: Any) -> None:
        """
        raise timeout error
        :param timeout: int, timeout
        :param args: pass a variable number of arguments
        :return: None
        """
        raise RequestTimeoutError(self.exchange_name, timeout=timeout)

    def raise400(self, *args: Any) -> None:
        """
        raise 400 error
        :param args: pass a variable number of arguments
        :return: None
        """
        raise RequestError(self.exchange_name, detail="rest request response <400>")

    def raise_proxy_error(self, *args: Any) -> None:
        """
        raise proxy error
        :param args: pass a variable number of arguments
        :return: None
        """
        raise RequestError(self.exchange_name, detail="proxy_error")

    def http_request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        body: Any = None,
        timeout: float = 10,
        max_retries: int = 3,
    ) -> Any:
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
                status_code = getattr(e, "status_code", None)
                if status_code in (404, 410) or "404" in msg or "410" in msg:
                    raise RequestError(
                        self.exchange_name,
                        detail=f"endpoint gone/not found: {url}",
                    ) from None
                if attempt < max_retries - 1:
                    self.logger.warning(
                        f"Retry {attempt + 1}/{max_retries} for {self._sanitize_url_for_log(url)}: {e}"
                    )
                    _time.sleep(min(0.5 * (2**attempt), 2.0))
                    continue
                if "timeout" in msg.lower():
                    self.handle_timeout_exception(url, method, body, timeout, e)
                else:
                    self.handle_request_exception(url, method, body, e)
            except Exception as e:
                if attempt < max_retries - 1:
                    self.logger.warning(
                        f"Retry {attempt + 1}/{max_retries} unexpected error for "
                        f"{self._sanitize_url_for_log(url)}: {e}"
                    )
                    _time.sleep(min(0.5 * (2**attempt), 2.0))
                    continue
                self.handle_request_exception(url, method, body, e)

    def disconnect(self) -> None:
        self._http_client.close()
        ConnectionMixin.disconnect(self)

    def cancel_all(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """
        cancel all order
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def cancel_order(
        self, symbol: Any, order_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """
        cancel order by order_id
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param order_id: order_id, default is None, can be a string passed by user
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_cancel_order(
        self, symbol: Any, order_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """
        cancel order by order_id using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param order_id: order_id, default is None, can be a string passed by user
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_account(self, symbol: Any = "ALL", extra_data: Any = None, **kwargs: Any) -> Any:
        """
        get account info
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_account(self, symbol: Any = "ALL", extra_data: Any = None, **kwargs: Any) -> Any:
        """
        get account info using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_balance(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """
        get balance by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_balance(self, symbol: Any = None, extra_data: Any = None, **kwargs: Any) -> Any:
        """
        get balance by symbol using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_clear_price(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """
        get clear price by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_clear_price(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """
        get clear price by symbol using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_deals(
        self,
        symbol: Any,
        count: int = 100,
        start_time: Any = None,
        end_time: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
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
        self,
        symbol: Any,
        count: int = 100,
        start_time: Any = None,
        end_time: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
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

    def async_get_depth(
        self, symbol: Any, count: int = 20, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """
        get order_book_data by symbol using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param count: default 20, the maximum number of order book level
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_funding_rate(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """
        get funding rate by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_funding_rate(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> Any:
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

    def async_get_kline(
        self,
        symbol: Any,
        period: Any,
        count: int = 20,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
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

    def get_open_orders(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """
        get open orders by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_open_orders(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """
        get open orders by symbol using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_tick(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """
        get tick price by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_get_tick(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> Any:
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
        symbol: Any,
        volume: Any,
        price: Any,
        order_type: Any,
        offset: str = "open",
        post_only: bool = False,
        client_order_id: Any = None,
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

    def query_order(self, symbol: Any, order_id: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """
        query order by order_id
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param order_id: order_id, default is None, can be a string passed by user
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def async_query_order(
        self, symbol: Any, order_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """
        query order by order_id using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param order_id: order_id, default is None, can be a string passed by user
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError

    def get_mark_price(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """
        get mark price from okx
        :param symbol: symbol name, eg: BTC-USDT.
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: None
        """
        raise NotImplementedError

    def async_get_mark_price(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> Any:
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

    def async_get_position(
        self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """
        get position info by symbol using async
        :param symbol: default None, get all positions.
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        raise NotImplementedError
