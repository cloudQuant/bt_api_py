"""
Phemex REST API request base class.
Handles authentication (HMAC SHA256), signing, and all REST API methods.
Phemex uses scaled precision (Ep/Ev suffixes) for prices and amounts.
"""

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.phemex_exchange_data import PhemexExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.phemex_ticker import PhemexRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class PhemexRequestData(Feed):
    """Phemex REST API feed implementation.

    Phemex private endpoints use HMAC SHA256 signature:
    sign_str = path + queryString + expiry + body
    Headers: x-phemex-access-token, x-phemex-request-expiry, x-phemex-request-signature
    Public endpoints use standard GET with query params.
    """

    @classmethod
    def _capabilities(cls) -> set[Capability]:
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_DEALS,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.QUERY_ORDER,
            Capability.QUERY_OPEN_ORDERS,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_SERVER_TIME,
        }

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.api_key = kwargs.get("public_key") or kwargs.get("api_key") or ""
        self.api_secret = kwargs.get("private_key") or kwargs.get("api_secret") or ""
        self.logger_name = kwargs.get("logger_name", "phemex_spot_feed.log")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "PHEMEX___SPOT")
        self._params = PhemexExchangeDataSpot()
        self.request_logger = get_logger("phemex_spot_feed")
        self.async_logger = get_logger("phemex_spot_feed")

    # ── authentication ──────────────────────────────────────────

    def sign(self, path, query_string, expiry, body_str=""):
        """Generate HMAC SHA256 signature for Phemex.

        sign_str = path + queryString + expiry + body
        """
        sign_str = path + query_string + str(expiry) + body_str
        return hmac.new(
            self.api_secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # ── core request helpers ────────────────────────────────────

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            assert 0, "Queue not initialized"

    def request(self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=False):
        """HTTP request function using Feed.http_request().

        Args:
            path: Request path in "METHOD /endpoint" format
            params: URL query parameters
            body: Request body (for POST/PUT/DELETE)
            extra_data: Extra data for RequestData
            timeout: Request timeout in seconds
            is_sign: Whether to sign the request
        """
        if params is None:
            params: dict[str, Any] = {}

        method, endpoint = path.split(" ", 1)
        query_string = urlencode(params) if params else ""
        url = f"{self._params.rest_url}{endpoint}"
        if query_string:
            url = f"{url}?{query_string}"

        headers = {"Content-Type": "application/json"}
        body_str = json.dumps(body) if body else ""

        if is_sign and self.api_key and self.api_secret:
            expiry = str(int(time.time()) + 60)
            sig = self.sign(endpoint, query_string, expiry, body_str)
            headers["x-phemex-access-token"] = self.api_key
            headers["x-phemex-request-expiry"] = expiry
            headers["x-phemex-request-signature"] = sig

        json_body = body if body and method in ("POST", "PUT") else None
        res = self.http_request(method, url, headers, json_body, timeout)
        self.request_logger.info(f"{method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    async def async_request(
        self, path, params=None, body=None, extra_data=None, timeout=5, is_sign=False
    ):
        """Async HTTP request function using Feed.async_http_request()."""
        if params is None:
            params: dict[str, Any] = {}

        method, endpoint = path.split(" ", 1)
        query_string = urlencode(params) if params else ""
        url = f"{self._params.rest_url}{endpoint}"
        if query_string:
            url = f"{url}?{query_string}"

        headers = {"Content-Type": "application/json"}
        body_str = json.dumps(body) if body else ""

        if is_sign and self.api_key and self.api_secret:
            expiry = str(int(time.time()) + 60)
            sig = self.sign(endpoint, query_string, expiry, body_str)
            headers["x-phemex-access-token"] = self.api_key
            headers["x-phemex-request-expiry"] = expiry
            headers["x-phemex-request-signature"] = sig

        json_body = body if body and method in ("POST", "PUT") else None
        res = await self.async_http_request(method, url, headers, json_body, timeout)
        self.async_logger.info(f"async {method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    # ── _get_xxx internal methods ───────────────────────────────

    def _get_server_time(self, extra_data=None, **kwargs) -> Any:
        path = self._params.get_rest_path("get_server_time")
        params: dict[str, Any] = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_server_time",
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_server_time_normalize_function,
            },
        )
        return path, params, extra_data

    def _get_exchange_info(self, extra_data=None, **kwargs) -> Any:
        path = self._params.get_rest_path("get_exchange_info")
        params: dict[str, Any] = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_exchange_info",
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_exchange_info_normalize_function,
            },
        )
        return path, params, extra_data

    def _get_tick(self, symbol, extra_data=None, **kwargs) -> Any:
        path = self._params.get_rest_path("get_tick")
        phemex_symbol = self._params.get_symbol(symbol)
        params = {"symbol": phemex_symbol}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_tick",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_tick_normalize_function,
            },
        )
        return path, params, extra_data

    def _get_depth(self, symbol, size=20, extra_data=None, **kwargs) -> Any:
        path = self._params.get_rest_path("get_depth")
        phemex_symbol = self._params.get_symbol(symbol)
        params = {"symbol": phemex_symbol}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_depth",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_depth_normalize_function,
            },
        )
        return path, params, extra_data

    def _get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs) -> Any:
        path = self._params.get_rest_path("get_kline")
        phemex_symbol = self._params.get_symbol(symbol)
        resolution = self._params.get_period(period)
        params = {
            "symbol": phemex_symbol,
            "resolution": resolution,
            "limit": min(count, 1000),
        }
        if kwargs.get("from_time"):
            params["from"] = kwargs["from_time"]
        if kwargs.get("to_time"):
            params["to"] = kwargs["to_time"]
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_kline",
                "symbol_name": symbol,
                "period": period,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_kline_normalize_function,
            },
        )
        return path, params, extra_data

    def _get_trade_history(self, symbol, count=100, extra_data=None, **kwargs) -> Any:
        path = self._params.get_rest_path("get_trades")
        phemex_symbol = self._params.get_symbol(symbol)
        params = {"symbol": phemex_symbol}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_trades",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_trade_history_normalize_function,
            },
        )
        return path, params, extra_data

    def _make_order(
        self, symbol, amount, price=None, order_type="buy-limit", extra_data=None, **kwargs
    ):
        path = self._params.get_rest_path("make_order")
        phemex_symbol = self._params.get_symbol(symbol)
        side_str, type_str = order_type.upper().replace("-", " ").split()
        # Phemex spot uses scaled precision
        SCALE = int(1e8)
        params = {
            "symbol": phemex_symbol,
            "side": side_str.capitalize(),
            "type": type_str.capitalize(),
            "qtyType": "ByBase",
            "baseQtyEv": int(float(amount) * SCALE),
        }
        if price is not None and type_str.upper() == "LIMIT":
            params["priceEp"] = int(float(price) * SCALE)
            params["timeInForce"] = kwargs.get("time_in_force", "GoodTillCancel")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "make_order",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._make_order_normalize_function,
            },
        )
        return path, params, extra_data

    def _cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs) -> Any:
        path = self._params.get_rest_path("cancel_order")
        phemex_symbol = self._params.get_symbol(symbol) if symbol else ""
        params = {"symbol": phemex_symbol, "orderID": order_id}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "cancel_order",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._cancel_order_normalize_function,
            },
        )
        return path, params, extra_data

    def _query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs) -> Any:
        path = self._params.get_rest_path("query_order")
        phemex_symbol = self._params.get_symbol(symbol) if symbol else ""
        params = {"symbol": phemex_symbol}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "query_order",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._query_order_normalize_function,
            },
        )
        return path, params, extra_data

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs) -> Any:
        path = self._params.get_rest_path("get_open_orders")
        phemex_symbol = self._params.get_symbol(symbol) if symbol else ""
        params = {"symbol": phemex_symbol}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_open_orders",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_open_orders_normalize_function,
            },
        )
        return path, params, extra_data

    def _get_account(self, extra_data=None, **kwargs) -> Any:
        path = self._params.get_rest_path("get_account")
        params: dict[str, Any] = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_account",
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_account_normalize_function,
            },
        )
        return path, params, extra_data

    def _get_balance(self, extra_data=None, **kwargs) -> Any:
        path = self._params.get_rest_path("get_balance")
        params: dict[str, Any] = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_balance",
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_balance_normalize_function,
            },
        )
        return path, params, extra_data

    # ── normalize functions ─────────────────────────────────────

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if input_data.get("code") != 0:
            return [], False
        data = input_data.get("data", {})
        return [{"server_time": data}], True

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if input_data.get("code") != 0:
            return [], False
        products = input_data.get("data", {}).get("products", [])
        return [{"symbols": products}], True

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if input_data.get("code") != 0:
            return [], False
        result = input_data.get("data", {})
        if not result:
            return [], False
        symbol_name = extra_data.get("symbol_name", "")
        asset_type = extra_data.get("asset_type", "SPOT")
        ticker = PhemexRequestTickerData(input_data, symbol_name, asset_type)
        return [ticker], True

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if input_data.get("code") != 0:
            return [], False
        book = input_data.get("data", {}).get("book", {})
        return [book] if book else [], book is not None

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if input_data.get("code") != 0:
            return [], False
        rows = input_data.get("data", {}).get("rows", [])
        return rows or [], True

    @staticmethod
    def _get_trade_history_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if input_data.get("code") != 0:
            return [], False
        trades = input_data.get("data", {}).get("trades", [])
        return trades or [], True

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if input_data.get("code") != 0:
            return [], False
        result = input_data.get("data", {})
        return [result], True

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        code = input_data.get("code", -1)
        return [{"success": code == 0}], code == 0

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if input_data.get("code") != 0:
            return [], False
        rows = input_data.get("data", {}).get("rows", [])
        return rows or [], True

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if input_data.get("code") != 0:
            return [], False
        rows = input_data.get("data", {}).get("rows", [])
        return rows or [], True

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if input_data.get("code") != 0:
            return [], False
        data = input_data.get("data", [])
        if isinstance(data, list):
            return data, True
        return [data], True

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if input_data.get("code") != 0:
            return [], False
        data = input_data.get("data", [])
        if isinstance(data, list):
            return data, True
        return [data], True
