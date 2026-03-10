"""
Bitso REST API request base class.

Auth: Authorization: Bitso {key}:{nonce}:{signature}
Signature: nonce + method + requestPath + body
Response envelope: {success: true/false, payload: ...}
Symbol format: lowercase underscore (btc_mxn).
"""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.bitso_exchange_data import BitsoExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger


class BitsoRequestData(Feed):
    """Bitso REST API Feed base class."""

    @classmethod
    def _capabilities(cls):
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

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self._api_key = kwargs.get("public_key") or kwargs.get("api_key") or ""
        self._api_secret = kwargs.get("private_key") or kwargs.get("api_secret") or ""
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "BITSO___SPOT")
        self._params = BitsoExchangeDataSpot()
        self.request_logger = get_logger("bitso_feed")
        self.async_logger = get_logger("bitso_feed")

    @property
    def api_key(self):
        return self._api_key

    @property
    def api_secret(self):
        return self._api_secret

    # ── authentication ──────────────────────────────────────────

    def _generate_signature(self, nonce, method, request_path, body_str=""):
        """Bitso HMAC SHA256: sign_str = nonce + method + requestPath + body."""
        if not self._api_secret:
            return ""
        sign_str = nonce + method.upper() + request_path + body_str
        return hmac.new(
            self._api_secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # ── core request helpers ────────────────────────────────────

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)

    def request(self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=False):
        """Synchronous HTTP request using Feed.http_request()."""
        if params is None:
            params = {}
        method, endpoint = path.split(" ", 1)

        headers = {"Content-Type": "application/json"}

        # Build full request path for signing (/api/v3/...)
        if method in ("GET", "DELETE"):
            qs = urlencode(params) if params else ""
            full_path = f"/api/v3{endpoint}"
            if qs:
                full_path = f"{full_path}?{qs}"
            url = f"{self._params.rest_url}{endpoint}"
            if qs:
                url = f"{url}?{qs}"
            json_body = None
        else:
            full_path = f"/api/v3{endpoint}"
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body or params
            json.dumps(json_body) if json_body else ""

        if is_sign and self._api_key:
            nonce = str(int(time.time() * 1000))
            body_s = json.dumps(json_body) if json_body else ""
            sig = self._generate_signature(nonce, method, full_path, body_s)
            headers["Authorization"] = f"Bitso {self._api_key}:{nonce}:{sig}"

        res = self.http_request(method, url, headers, json_body, timeout)
        self.request_logger.info(f"{method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    async def async_request(
        self, path, params=None, body=None, extra_data=None, timeout=5, is_sign=False
    ):
        """Async HTTP request using Feed.async_http_request()."""
        if params is None:
            params = {}
        method, endpoint = path.split(" ", 1)

        headers = {"Content-Type": "application/json"}

        if method in ("GET", "DELETE"):
            qs = urlencode(params) if params else ""
            full_path = f"/api/v3{endpoint}"
            if qs:
                full_path = f"{full_path}?{qs}"
            url = f"{self._params.rest_url}{endpoint}"
            if qs:
                url = f"{url}?{qs}"
            json_body = None
        else:
            full_path = f"/api/v3{endpoint}"
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body or params

        if is_sign and self._api_key:
            nonce = str(int(time.time() * 1000))
            body_s = json.dumps(json_body) if json_body else ""
            sig = self._generate_signature(nonce, method, full_path, body_s)
            headers["Authorization"] = f"Bitso {self._api_key}:{nonce}:{sig}"

        res = await self.async_http_request(method, url, headers, json_body, timeout)
        self.async_logger.info(f"async {method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    def async_callback(self, request_data):
        if request_data is not None:
            self.push_data_to_queue(request_data)

    # ── _get_xxx internal methods ───────────────────────────────

    def _get_server_time(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_server_time")
        params = {}
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
        params = {}
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
        book = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_tick")
        params = {"book": book}
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

    def _get_depth(self, symbol, count=50, extra_data=None, **kwargs):
        book = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_depth")
        params = {"book": book, "aggregate": "true"}
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
        book = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_kline")
        bucket = self._params.get_period(period)
        params = {"book": book, "time_bucket": bucket}
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
        book = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_trades")
        params = {"book": book, "limit": min(count, 100)}
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
        book = self._params.get_symbol(symbol)
        parts = order_type.lower().replace("-", " ").split()
        side = parts[0] if parts else "buy"
        otype = parts[1] if len(parts) > 1 else "limit"
        body = {
            "book": book,
            "side": side,
            "type": otype,
            "major": str(size),
        }
        if price is not None and otype == "limit":
            body["price"] = str(price)
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
        return path, body, extra_data

    def _cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        # Bitso: DELETE /orders/{oid}
        base = self._params.get_rest_path("cancel_order")
        method_path = base  # "DELETE /orders"
        if order_id:
            method_path = f"{base}/{order_id}"
        params = {}
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
        return method_path, params, extra_data

    def _query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        # Bitso: GET /orders/{oid}
        base = self._params.get_rest_path("query_order")
        method_path = base
        if order_id:
            method_path = f"{base}/{order_id}"
        params = {}
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
        return method_path, params, extra_data

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_open_orders")
        params = {}
        if symbol:
            params["book"] = self._params.get_symbol(symbol)
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
        params = {}
        if symbol:
            params["book"] = self._params.get_symbol(symbol)
        params["limit"] = kwargs.get("limit", 25)
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
        params = {}
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
        params = {}
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
    # Bitso envelope: {success: true/false, payload: ...}
    # Errors: {success: false, error: {...}} or HTTP error

    @staticmethod
    def _is_error(input_data):
        if not input_data:
            return True
        if isinstance(input_data, dict):
            if input_data.get("success") is False:
                return True
            if "error" in input_data:
                return True
        return False

    @staticmethod
    def _unwrap(input_data):
        """Unwrap Bitso {success, payload} envelope."""
        if isinstance(input_data, dict) and "payload" in input_data:
            return input_data["payload"]
        return input_data

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        if BitsoRequestData._is_error(input_data):
            return [], False
        payload = BitsoRequestData._unwrap(input_data)
        if isinstance(payload, dict) and "iso" in payload:
            return [{"server_time": payload["iso"]}], True
        return [{"server_time": payload}], True

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if BitsoRequestData._is_error(input_data):
            return [], False
        payload = BitsoRequestData._unwrap(input_data)
        if isinstance(payload, list):
            return [{"books": payload}], True
        return [payload], True

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if BitsoRequestData._is_error(input_data):
            return [], False
        payload = BitsoRequestData._unwrap(input_data)
        if isinstance(payload, list) and len(payload) > 0:
            return [payload[0]], True
        if isinstance(payload, dict):
            return [payload], True
        return [], False

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if BitsoRequestData._is_error(input_data):
            return [], False
        payload = BitsoRequestData._unwrap(input_data)
        if not payload:
            return [], False
        return [payload], True

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if BitsoRequestData._is_error(input_data):
            return [], False
        payload = BitsoRequestData._unwrap(input_data)
        if isinstance(payload, list) and len(payload) > 0:
            return payload, True
        return [], False

    @staticmethod
    def _get_trade_history_normalize_function(input_data, extra_data):
        if BitsoRequestData._is_error(input_data):
            return [], False
        payload = BitsoRequestData._unwrap(input_data)
        if isinstance(payload, list) and len(payload) > 0:
            return payload, True
        return [], False

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if BitsoRequestData._is_error(input_data):
            return [], False
        payload = BitsoRequestData._unwrap(input_data)
        if not payload:
            return [], False
        return [payload], True

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if BitsoRequestData._is_error(input_data):
            return [], False
        return [{"success": True}], True

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        if BitsoRequestData._is_error(input_data):
            return [], False
        payload = BitsoRequestData._unwrap(input_data)
        if not payload:
            return [], False
        if isinstance(payload, list):
            return payload, True
        return [payload], True

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        if BitsoRequestData._is_error(input_data):
            return [], False
        payload = BitsoRequestData._unwrap(input_data)
        if isinstance(payload, list):
            return payload, True
        return [], False

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        if BitsoRequestData._is_error(input_data):
            return [], False
        payload = BitsoRequestData._unwrap(input_data)
        if isinstance(payload, list):
            return payload, True
        return [], False

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if BitsoRequestData._is_error(input_data):
            return [], False
        payload = BitsoRequestData._unwrap(input_data)
        if isinstance(payload, dict):
            balances = payload.get("balances", [payload])
            return balances, True
        if isinstance(payload, list):
            return payload, True
        return [payload], True

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if BitsoRequestData._is_error(input_data):
            return [], False
        payload = BitsoRequestData._unwrap(input_data)
        if isinstance(payload, dict):
            balances = payload.get("balances", [payload])
            return balances, True
        if isinstance(payload, list):
            return payload, True
        return [payload], True
