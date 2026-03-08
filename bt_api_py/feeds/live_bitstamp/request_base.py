"""
Bitstamp REST API request base class.

Auth: HMAC SHA256 v2 with X-Auth headers.
Symbol format: lowercase concatenated (btcusd).
Market-data endpoints use /{pair}/ suffix.
Response: direct JSON (no envelope). Errors have status="error".
"""

import hashlib
import hmac
import time
import uuid
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.bitstamp_exchange_data import BitstampExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger


class BitstampRequestData(Feed):
    """Bitstamp REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITSTAMP___SPOT")
        self._params = BitstampExchangeDataSpot()
        self.request_logger = get_logger("bitstamp_feed")
        self.async_logger = get_logger("bitstamp_feed")

    @property
    def api_key(self):
        return self._api_key

    @property
    def api_secret(self):
        return self._api_secret

    # ── authentication ──────────────────────────────────────────

    def _generate_auth_headers(self, method, request_path, content_type="", body_str=""):
        """Bitstamp v2 HMAC SHA256 auth headers."""
        if not self._api_secret or not self._api_key:
            return {}
        timestamp = str(int(time.time() * 1000))
        nonce = str(uuid.uuid4())
        message = (
            f"BITSTAMP {self._api_key}"
            f"{method.upper()}"
            f"www.bitstamp.net"
            f"/api/v2{request_path}"
            f"{content_type}"
            f"{nonce}"
            f"{timestamp}"
            f"v2"
            f"{body_str}"
        )
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-Auth": f"BITSTAMP {self._api_key}",
            "X-Auth-Signature": signature,
            "X-Auth-Nonce": nonce,
            "X-Auth-Timestamp": timestamp,
            "X-Auth-Version": "v2",
        }

    # ── core request helpers ────────────────────────────────────

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)

    def request(self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=False):
        """Synchronous HTTP request using Feed.http_request()."""
        if params is None:
            params = {}
        method, endpoint = path.split(" ", 1)
        headers = {}

        if method == "GET":
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}"
            if qs:
                url = f"{url}?{qs}"
            json_body = None
            if is_sign:
                headers.update(self._generate_auth_headers(method, endpoint))
        else:
            # POST: form-encoded for signed, JSON otherwise
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body or params
            if is_sign:
                content_type = "application/x-www-form-urlencoded"
                body_str = urlencode(json_body) if json_body else ""
                headers["Content-Type"] = content_type
                headers.update(
                    self._generate_auth_headers(method, endpoint, content_type, body_str)
                )
            else:
                headers["Content-Type"] = "application/json"

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
        headers = {}

        if method == "GET":
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}"
            if qs:
                url = f"{url}?{qs}"
            json_body = None
            if is_sign:
                headers.update(self._generate_auth_headers(method, endpoint))
        else:
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body or params
            if is_sign:
                content_type = "application/x-www-form-urlencoded"
                body_str = urlencode(json_body) if json_body else ""
                headers["Content-Type"] = content_type
                headers.update(
                    self._generate_auth_headers(method, endpoint, content_type, body_str)
                )
            else:
                headers["Content-Type"] = "application/json"

        res = await self.async_http_request(method, url, headers, json_body, timeout)
        self.async_logger.info(f"async {method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    def async_callback(self, request_data):
        if request_data is not None:
            self.push_data_to_queue(request_data)

    # ── _get_xxx internal methods ───────────────────────────────
    # Bitstamp market data endpoints append /{pair}/ to the base path.

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
        pair = self._params.get_symbol(symbol)
        base_path = self._params.get_rest_path("get_tick")
        method, ep = base_path.split(" ", 1)
        path = f"{method} {ep}/{pair}/"
        params = {}
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
        pair = self._params.get_symbol(symbol)
        base_path = self._params.get_rest_path("get_depth")
        method, ep = base_path.split(" ", 1)
        path = f"{method} {ep}/{pair}/"
        params = {}
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
        pair = self._params.get_symbol(symbol)
        base_path = self._params.get_rest_path("get_kline")
        method, ep = base_path.split(" ", 1)
        path = f"{method} {ep}/{pair}/"
        step = self._params.get_period(period)
        params = {"step": step, "limit": count}
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
        pair = self._params.get_symbol(symbol)
        base_path = self._params.get_rest_path("get_trades")
        method, ep = base_path.split(" ", 1)
        path = f"{method} {ep}/{pair}/"
        params = {}
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
        pair = self._params.get_symbol(symbol)
        parts = order_type.lower().replace("-", " ").split()
        side = parts[0] if parts else "buy"
        # Bitstamp: POST /buy/{pair}/ or POST /sell/{pair}/
        base_path = self._params.get_rest_path("make_order")
        method, ep = base_path.split(" ", 1)
        path = f"{method} /{side}/{pair}/"
        body = {"amount": str(size)}
        if price is not None:
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
        path = self._params.get_rest_path("cancel_order")
        body = {}
        if order_id:
            body["id"] = str(order_id)
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
        return path, body, extra_data

    def _query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("query_order")
        body = {}
        if order_id:
            body["id"] = str(order_id)
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
        return path, body, extra_data

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_open_orders")
        body = {}
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
        return path, body, extra_data

    def _get_deals(self, symbol=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_deals")
        body = {}
        if kwargs.get("limit"):
            body["limit"] = str(kwargs["limit"])
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
        return path, body, extra_data

    def _get_account(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_account")
        body = {}
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
        return path, body, extra_data

    def _get_balance(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_balance")
        body = {}
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
        return path, body, extra_data

    # ── normalize functions ─────────────────────────────────────
    # Bitstamp returns direct JSON. Errors: {"status": "error", "reason": ...}

    @staticmethod
    def _is_error(input_data):
        if not input_data:
            return True
        if isinstance(input_data, dict):
            if input_data.get("status") == "error":
                return True
            if "error" in input_data:
                return True
        return False

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        if BitstampRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and "server_time" in input_data:
            return [{"server_time": input_data["server_time"]}], True
        return [{"server_time": input_data}], True

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if BitstampRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list):
            return [{"pairs": input_data}], True
        return [input_data], True

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if BitstampRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            return [input_data], True
        return [], False

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if BitstampRequestData._is_error(input_data):
            return [], False
        if not input_data:
            return [], False
        return [input_data], True

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if BitstampRequestData._is_error(input_data):
            return [], False
        # Bitstamp OHLC: {data: {pair: ..., ohlc: [...]}}
        if isinstance(input_data, dict):
            data = input_data.get("data", {})
            ohlc = data.get("ohlc", []) if isinstance(data, dict) else []
            if ohlc:
                return ohlc, True
        return [], False

    @staticmethod
    def _get_trade_history_normalize_function(input_data, extra_data):
        if BitstampRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list) and len(input_data) > 0:
            return input_data, True
        return [], False

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if BitstampRequestData._is_error(input_data):
            return [], False
        if not input_data:
            return [], False
        return [input_data], True

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if BitstampRequestData._is_error(input_data):
            return [], False
        return [{"success": True}], True

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        if BitstampRequestData._is_error(input_data):
            return [], False
        if not input_data:
            return [], False
        return [input_data], True

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        if isinstance(input_data, list):
            return input_data, True
        if BitstampRequestData._is_error(input_data):
            return [], False
        return [], False

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        if isinstance(input_data, list):
            return input_data, True
        if BitstampRequestData._is_error(input_data):
            return [], False
        return [], False

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if BitstampRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            return [input_data], True
        return [input_data], True

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if BitstampRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            return [input_data], True
        return [input_data], True
