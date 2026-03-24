"""
Latoken REST Feed – base class with HMAC-SHA512 auth and _get_xxx internal methods.
"""

import hashlib
import hmac
from typing import Any
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.latoken_exchange_data import LatokenExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class LatokenRequestData(Feed):
    """Latoken REST Feed base – HMAC-SHA512 auth, _get_xxx pattern."""

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "LATOKEN___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "latoken_spot_feed.log")
        self._params = kwargs.get("exchange_data", LatokenExchangeDataSpot())

        self.request_logger = get_logger("latoken_spot_feed")
        self.async_logger = get_logger("latoken_spot_feed")

        self.api_key = kwargs.get("public_key") or kwargs.get("api_key")
        self.api_secret = kwargs.get("private_key") or kwargs.get("api_secret")
        self.rest_url = self._params.rest_url

    # ── core request helpers ────────────────────────────────────

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """Synchronous HTTP request."""
        if params is None:
            params: dict[str, Any] = {}
        if extra_data is None:
            extra_data = {}
        method, endpoint = path.split(" ", 1)
        headers = self._get_headers(method, endpoint, params)

        if method == "GET":
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}{'?' + qs if qs else ''}"
            json_body = None
        else:
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body if body is not None else params
            if not json_body:
                json_body = None

        res = self.http_request(method, url, headers, json_body, timeout)
        self.request_logger.info(f"{method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async HTTP request."""
        if params is None:
            params: dict[str, Any] = {}
        if extra_data is None:
            extra_data = {}
        method, endpoint = path.split(" ", 1)
        headers = self._get_headers(method, endpoint, params)

        if method == "GET":
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}{'?' + qs if qs else ''}"
            json_body = None
        else:
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body if body is not None else params
            if not json_body:
                json_body = None

        res = await self._http_client.async_request(
            method=method,
            url=url,
            headers=headers,
            json_data=json_body,
            timeout=timeout,
        )
        self.async_logger.info(f"async {method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    def async_callback(self, request_data):
        if request_data is not None:
            self.push_data_to_queue(request_data)

    # ── capabilities ────────────────────────────────────────────

    @classmethod
    def _capabilities(cls) -> set[Capability]:
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.QUERY_OPEN_ORDERS,
        }

    # ── auth helpers ────────────────────────────────────────────

    def _generate_signature(self, method, path, params=None):
        query = urlencode(params) if params else ""
        auth = f"{method}{path}{query}"
        if self.api_secret:
            return hmac.new(self.api_secret.encode(), auth.encode(), hashlib.sha512).hexdigest()
        return ""

    def _get_headers(self, method="GET", path="", params=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["X-LA-APIKEY"] = self.api_key
            if self.api_secret:
                headers["X-LA-SIGNATURE"] = self._generate_signature(method, path, params)
                headers["X-LA-DIGEST"] = "HMAC-SHA512"
        return headers

    # ── symbol helper ───────────────────────────────────────────

    def _split_symbol(self, symbol):
        """Return (base, quote) from any user-facing symbol."""
        s = symbol.lower().replace("/", "_").replace("-", "_")
        parts = s.split("_")
        return parts[0], parts[1] if len(parts) > 1 else "usdt"

    # ── error detection ─────────────────────────────────────────

    @staticmethod
    def _is_error(data):
        if data is None:
            return True
        if isinstance(data, dict) and data.get("status") == "FAILURE":
            return True
        return bool(isinstance(data, dict) and "error" in data)

    # ── internal _get_xxx methods ───────────────────────────────

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        base, quote = self._split_symbol(symbol)
        path = self._params.get_rest_path("get_tick", base=base, quote=quote)
        params: dict[str, Any] = {}
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

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if LatokenRequestData._is_error(input_data):
            return [input_data], False
        if isinstance(input_data, dict):
            return [input_data], True
        if isinstance(input_data, list):
            return input_data, True
        return [], False

    def _get_depth(self, symbol, extra_data=None, **kwargs):
        base, quote = self._split_symbol(symbol)
        path = self._params.get_rest_path("get_depth", currency=base, quote=quote)
        params: dict[str, Any] = {}
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

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if LatokenRequestData._is_error(input_data):
            return [input_data], False
        if isinstance(input_data, (dict, list)):
            return [input_data], True
        return [], False

    def _get_exchange_info(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_exchange_info")
        params: dict[str, Any] = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_exchange_info",
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_exchange_info_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if LatokenRequestData._is_error(input_data):
            return [input_data], False
        if isinstance(input_data, list):
            return input_data, True
        if isinstance(input_data, dict):
            return [input_data], True
        return [], False

    def _get_deals(self, symbol, extra_data=None, **kwargs):
        base, quote = self._split_symbol(symbol)
        path = self._params.get_rest_path("get_deals", currency=base, quote=quote)
        params: dict[str, Any] = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_deals",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_deals_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        if LatokenRequestData._is_error(input_data):
            return [input_data], False
        if isinstance(input_data, list):
            return input_data, True
        return [], False

    def _get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        base, quote = self._split_symbol(symbol)
        path = self._params.get_rest_path("get_kline", currency=base, quote=quote)
        params: dict[str, Any] = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_kline",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_kline_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if LatokenRequestData._is_error(input_data):
            return [input_data], False
        if isinstance(input_data, list):
            return input_data, True
        return [], False

    def _get_server_time(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_server_time")
        params: dict[str, Any] = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_server_time",
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_server_time_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        return [input_data], True

    # ── trading ─────────────────────────────────────────────────

    def _make_order(self, symbol, side, order_type, amount, price=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("make_order")
        base, quote = self._split_symbol(symbol)
        body = {
            "baseCurrency": base,
            "quoteCurrency": quote,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": str(amount),
        }
        if price is not None:
            body["price"] = str(price)
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
        return path, body, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if LatokenRequestData._is_error(input_data):
            return [input_data], False
        return [input_data], True

    def _cancel_order(self, order_id, extra_data=None, **kwargs):
        path = self._params.get_rest_path("cancel_order")
        body = {"id": order_id}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "cancel_order",
                "exchange_name": self.exchange_name,
                "normalize_function": self._cancel_order_normalize_function,
            },
        )
        return path, body, extra_data

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if LatokenRequestData._is_error(input_data):
            return [input_data], False
        return [input_data], True

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        if symbol:
            base, quote = self._split_symbol(symbol)
            path = self._params.get_rest_path("get_open_orders", currency=base, quote=quote)
        else:
            path = self._params.get_rest_path("get_balance")  # fallback
        params: dict[str, Any] = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_open_orders",
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_open_orders_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        if LatokenRequestData._is_error(input_data):
            return [input_data], False
        if isinstance(input_data, list):
            return input_data, True
        return [input_data], True

    # ── account ─────────────────────────────────────────────────

    def _get_balance(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_balance")
        params: dict[str, Any] = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_balance",
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_balance_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if LatokenRequestData._is_error(input_data):
            return [input_data], False
        if isinstance(input_data, list):
            return input_data, True
        return [input_data], True

    def _get_account(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_account")
        params: dict[str, Any] = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_account",
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_account_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if LatokenRequestData._is_error(input_data):
            return [input_data], False
        if isinstance(input_data, list):
            return input_data, True
        return [input_data], True
