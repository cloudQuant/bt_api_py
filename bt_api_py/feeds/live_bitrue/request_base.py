"""
Bitrue REST API request base class.

Bitrue Spot API with Binance-compatible HMAC SHA256 authentication.
Signature: HMAC-SHA256(query_string_with_timestamp, secret_key)
Header: X-MBX-APIKEY
Symbol format: BTCUSDT (concatenated uppercase).
"""

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.bitrue_exchange_data import BitrueExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger


class BitrueRequestData(Feed):
    """Bitrue REST API Feed base class."""

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
        self._api_key = kwargs.get("public_key") or kwargs.get("api_key") or ""
        self._api_secret = kwargs.get("private_key") or kwargs.get("api_secret") or ""
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "BITRUE___SPOT")
        self._params = BitrueExchangeDataSpot()
        self.request_logger = get_logger("bitrue_feed")
        self.async_logger = get_logger("bitrue_feed")

    @property
    def api_key(self):
        return self._api_key

    @property
    def api_secret(self):
        return self._api_secret

    # ── authentication ──────────────────────────────────────────

    def _generate_signature(self, query_string):
        """Binance-style HMAC SHA256 signature over query string."""
        if not self._api_secret:
            return ""
        return hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # ── core request helpers ────────────────────────────────────

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)

    def request(self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=False):
        """Synchronous HTTP request using Feed.http_request()."""
        if params is None:
            params: dict[str, Any] = {}
        method, endpoint = path.split(" ", 1)

        headers = {"Content-Type": "application/json"}

        if is_sign and self._api_key:
            params["timestamp"] = int(time.time() * 1000)
            qs = urlencode(sorted(params.items()))
            sig = self._generate_signature(qs)
            params["signature"] = sig
            headers["X-MBX-APIKEY"] = self._api_key

        if method in ("GET", "DELETE"):
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}"
            if qs:
                url = f"{url}?{qs}"
            json_body = None
        else:
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body or params

        res = self.http_request(method, url, headers, json_body, timeout)
        self.request_logger.info(f"{method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    async def async_request(
        self, path, params=None, body=None, extra_data=None, timeout=5, is_sign=False
    ):
        """Async HTTP request using Feed.async_http_request()."""
        if params is None:
            params: dict[str, Any] = {}
        method, endpoint = path.split(" ", 1)

        headers = {"Content-Type": "application/json"}

        if is_sign and self._api_key:
            params["timestamp"] = int(time.time() * 1000)
            qs = urlencode(sorted(params.items()))
            sig = self._generate_signature(qs)
            params["signature"] = sig
            headers["X-MBX-APIKEY"] = self._api_key

        if method in ("GET", "DELETE"):
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}"
            if qs:
                url = f"{url}?{qs}"
            json_body = None
        else:
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body or params

        res = await self.async_http_request(method, url, headers, json_body, timeout)
        self.async_logger.info(f"async {method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    def async_callback(self, request_data):
        if request_data is not None:
            self.push_data_to_queue(request_data)

    # ── _get_xxx internal methods ───────────────────────────────

    def _get_server_time(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_server_time")
        params: dict[str, Any] = {}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_server_time",
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_server_time_normalize_function,
            }
        )
        return path, params, extra_data

    def _get_exchange_info(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_exchange_info")
        params: dict[str, Any] = {}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_exchange_info",
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_exchange_info_normalize_function,
            }
        )
        return path, params, extra_data

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        br_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_tick")
        params = {"symbol": br_symbol}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_tick",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_tick_normalize_function,
            }
        )
        return path, params, extra_data

    def _get_depth(self, symbol, count=100, extra_data=None, **kwargs):
        br_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_depth")
        params = {"symbol": br_symbol, "limit": min(count, 1000)}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_depth",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_depth_normalize_function,
            }
        )
        return path, params, extra_data

    def _get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        br_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_kline")
        br_scale = self._params.get_period(period)
        params = {"symbol": br_symbol, "scale": br_scale, "limit": count}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_kline",
                "symbol_name": symbol,
                "period": period,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_kline_normalize_function,
            }
        )
        return path, params, extra_data

    def _get_trade_history(self, symbol, count=50, extra_data=None, **kwargs):
        br_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_trades")
        params = {"symbol": br_symbol, "limit": min(count, 1000)}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_trades",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_trade_history_normalize_function,
            }
        )
        return path, params, extra_data

    def _make_order(
        self, symbol, size, price=None, order_type="buy-limit", extra_data=None, **kwargs
    ):
        path = self._params.get_rest_path("make_order")
        br_symbol = self._params.get_symbol(symbol)
        parts = order_type.upper().replace("-", " ").split()
        side = parts[0] if parts else "BUY"
        otype = parts[1] if len(parts) > 1 else "LIMIT"
        params = {
            "symbol": br_symbol,
            "side": side,
            "type": otype,
            "quantity": str(size),
        }
        if price is not None and otype == "LIMIT":
            params["price"] = str(price)
            params["timeInForce"] = kwargs.get("timeInForce", "GTC")
        if kwargs.get("newClientOrderId"):
            params["newClientOrderId"] = kwargs["newClientOrderId"]
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "make_order",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._make_order_normalize_function,
            }
        )
        return path, params, extra_data

    def _cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("cancel_order")
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = self._params.get_symbol(symbol)
        if order_id:
            params["orderId"] = str(order_id)
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "cancel_order",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._cancel_order_normalize_function,
            }
        )
        return path, params, extra_data

    def _query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("query_order")
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = self._params.get_symbol(symbol)
        if order_id:
            params["orderId"] = str(order_id)
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "query_order",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._query_order_normalize_function,
            }
        )
        return path, params, extra_data

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_open_orders")
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = self._params.get_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_open_orders",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_open_orders_normalize_function,
            }
        )
        return path, params, extra_data

    def _get_deals(self, symbol=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_deals")
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = self._params.get_symbol(symbol)
        params["limit"] = kwargs.get("limit", 50)
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_deals",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_deals_normalize_function,
            }
        )
        return path, params, extra_data

    def _get_account(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_account")
        params: dict[str, Any] = {}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_account",
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_account_normalize_function,
            }
        )
        return path, params, extra_data

    def _get_balance(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_balance")
        params: dict[str, Any] = {}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_balance",
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_balance_normalize_function,
            }
        )
        return path, params, extra_data

    # ── normalize functions ─────────────────────────────────────
    # Bitrue returns Binance-style responses (direct JSON, errors have "code" < 0)

    @staticmethod
    def _is_error(input_data):
        if not input_data:
            return True
        if isinstance(input_data, dict):
            code = input_data.get("code")
            if code is not None and isinstance(code, int) and code < 0:
                return True
        return False

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        if BitrueRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and "serverTime" in input_data:
            return [{"server_time": input_data["serverTime"]}], True
        return [input_data], True

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if BitrueRequestData._is_error(input_data):
            return [], False
        return [input_data], True

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if BitrueRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list) and len(input_data) > 0:
            return [input_data[0]], True
        if isinstance(input_data, dict):
            return [input_data], True
        return [], False

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if BitrueRequestData._is_error(input_data):
            return [], False
        if not input_data:
            return [], False
        return [input_data], True

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if BitrueRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list) and len(input_data) > 0:
            return input_data, True
        return [], False

    @staticmethod
    def _get_trade_history_normalize_function(input_data, extra_data):
        if BitrueRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list) and len(input_data) > 0:
            return input_data, True
        return [], False

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if BitrueRequestData._is_error(input_data):
            return [], False
        if not input_data:
            return [], False
        return [input_data], True

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if BitrueRequestData._is_error(input_data):
            return [], False
        return [{"success": True}], True

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        if BitrueRequestData._is_error(input_data):
            return [], False
        if not input_data:
            return [], False
        return [input_data], True

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        if BitrueRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        return [], False

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        if BitrueRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        return [], False

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if BitrueRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            balances = input_data.get("balances", input_data)
            if isinstance(balances, list):
                return balances, True
            return [balances], True
        return [input_data], True

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if BitrueRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            balances = input_data.get("balances", input_data)
            if isinstance(balances, list):
                return balances, True
            return [balances], True
        return [input_data], True
