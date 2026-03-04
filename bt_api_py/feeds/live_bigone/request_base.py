"""
BigONE REST API request base class.

BigONE API v3 with JWT (HS256) authentication.
Public endpoints require no auth; private endpoints (under /viewer) need Bearer JWT.
All responses are wrapped in {"data": ...}.
"""

import time
from urllib.parse import urlencode

try:
    import jwt as pyjwt
except ImportError:
    pyjwt = None

from bt_api_py.containers.exchanges.bigone_exchange_data import BigONEExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger


class BigONERequestData(Feed):
    """BigONE REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "BIGONE___SPOT")
        self._params = BigONEExchangeDataSpot()
        self.request_logger = get_logger("bigone_feed")
        self.async_logger = get_logger("bigone_feed")

    @property
    def api_key(self):
        return self._api_key

    @property
    def api_secret(self):
        return self._api_secret

    # ── authentication ──────────────────────────────────────────

    def _generate_jwt_token(self):
        """Generate JWT token for BigONE API authentication."""
        if not self._api_key or not self._api_secret:
            return ""
        if pyjwt is None:
            return ""
        payload = {
            "type": "OpenAPIV2",
            "sub": self._api_key,
            "nonce": str(int(time.time() * 1e9)),
        }
        token = pyjwt.encode(payload, self._api_secret, algorithm="HS256")
        return token

    # ── core request helpers ────────────────────────────────────

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)

    def request(self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=False):
        """Synchronous HTTP request using Feed.http_request()."""
        if params is None:
            params = {}
        method, endpoint = path.split(" ", 1)
        query_string = urlencode(params) if params else ""
        url = f"{self._params.rest_url}{endpoint}"
        if query_string:
            url = f"{url}?{query_string}"

        headers = {"Content-Type": "application/json"}
        if is_sign:
            token = self._generate_jwt_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"

        json_body = body if body and method == "POST" else None
        res = self.http_request(method, url, headers, json_body, timeout)
        self.request_logger.info(f"{method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5, is_sign=False):
        """Async HTTP request using Feed.async_http_request()."""
        if params is None:
            params = {}
        method, endpoint = path.split(" ", 1)
        query_string = urlencode(params) if params else ""
        url = f"{self._params.rest_url}{endpoint}"
        if query_string:
            url = f"{url}?{query_string}"

        headers = {"Content-Type": "application/json"}
        if is_sign:
            token = self._generate_jwt_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"

        json_body = body if body and method == "POST" else None
        res = await self.async_http_request(method, url, headers, json_body, timeout)
        self.async_logger.info(f"async {method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    def async_callback(self, request_data):
        """Async callback – push RequestData to the data queue."""
        if request_data is not None:
            self.push_data_to_queue(request_data)

    # ── _get_xxx internal methods ───────────────────────────────

    def _get_server_time(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_server_time")
        params = {}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_server_time",
            "symbol_name": None,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_server_time_normalize_function,
        })
        return path, params, extra_data

    def _get_exchange_info(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_exchange_info")
        params = {}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_exchange_info",
            "symbol_name": None,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_exchange_info_normalize_function,
        })
        return path, params, extra_data

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        bo_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_tick").replace("{symbol}", bo_symbol)
        params = {}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_tick",
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_tick_normalize_function,
        })
        return path, params, extra_data

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        bo_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_depth").replace("{symbol}", bo_symbol)
        params = {"limit": count}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_depth",
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_depth_normalize_function,
        })
        return path, params, extra_data

    def _get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        bo_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_kline").replace("{symbol}", bo_symbol)
        bo_period = self._params.get_period(period)
        params = {"period": bo_period, "limit": min(count, 500)}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_kline",
            "symbol_name": symbol,
            "period": period,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_kline_normalize_function,
        })
        return path, params, extra_data

    def _get_trade_history(self, symbol, count=100, extra_data=None, **kwargs):
        bo_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_trades").replace("{symbol}", bo_symbol)
        params = {"limit": count}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_trades",
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_trade_history_normalize_function,
        })
        return path, params, extra_data

    def _make_order(self, symbol, amount, price=None, order_type="buy-limit", extra_data=None, **kwargs):
        path = self._params.get_rest_path("make_order")
        bo_symbol = self._params.get_symbol(symbol)
        parts = order_type.lower().replace("-", " ").split()
        side_str = parts[0] if parts else "buy"
        otype = parts[1] if len(parts) > 1 else "limit"
        # BigONE uses BID/ASK for side, LIMIT/MARKET for type
        side = "BID" if side_str == "buy" else "ASK"
        bo_type = otype.upper()
        body = {
            "asset_pair_name": bo_symbol,
            "side": side,
            "type": bo_type,
            "amount": str(amount),
        }
        if price is not None and bo_type == "LIMIT":
            body["price"] = str(price)
        if kwargs.get("client_order_id"):
            body["client_order_id"] = kwargs["client_order_id"]
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "make_order",
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._make_order_normalize_function,
        })
        return path, body, extra_data

    def _cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("cancel_order").replace(
            "{order_id}", str(order_id or ""))
        params = {}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "cancel_order",
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._cancel_order_normalize_function,
        })
        return path, params, extra_data

    def _query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("query_order").replace(
            "{order_id}", str(order_id or ""))
        params = {}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "query_order",
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._query_order_normalize_function,
        })
        return path, params, extra_data

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_open_orders")
        params = {"state": "PENDING"}
        if symbol:
            params["asset_pair_name"] = self._params.get_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_open_orders",
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_open_orders_normalize_function,
        })
        return path, params, extra_data

    def _get_account(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_account")
        params = {}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_account",
            "symbol_name": None,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_account_normalize_function,
        })
        return path, params, extra_data

    def _get_balance(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_balance")
        params = {}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_balance",
            "symbol_name": None,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_balance_normalize_function,
        })
        return path, params, extra_data

    # ── normalize functions ─────────────────────────────────────
    # BigONE wraps responses in {"data": ...}

    @staticmethod
    def _is_error(input_data):
        if not input_data:
            return True
        if isinstance(input_data, dict) and "errors" in input_data:
            return True
        return False

    @staticmethod
    def _unwrap(input_data):
        """Unwrap BigONE {"data": ...} envelope."""
        if isinstance(input_data, dict) and "data" in input_data:
            return input_data["data"]
        return input_data

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "errors" in input_data:
            return [], False
        data = BigONERequestData._unwrap(input_data)
        return [{"server_time": data}], True

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "errors" in input_data:
            return [], False
        data = BigONERequestData._unwrap(input_data)
        if isinstance(data, list):
            return [{"symbols": data}], True
        return [{"symbols": data}], True

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "errors" in input_data:
            return [], False
        data = BigONERequestData._unwrap(input_data)
        if not data:
            return [], False
        return [data], True

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "errors" in input_data:
            return [], False
        data = BigONERequestData._unwrap(input_data)
        if not data:
            return [], False
        return [data], True

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "errors" in input_data:
            return [], False
        data = BigONERequestData._unwrap(input_data)
        if isinstance(data, list) and len(data) > 0:
            return data, True
        return [], False

    @staticmethod
    def _get_trade_history_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "errors" in input_data:
            return [], False
        data = BigONERequestData._unwrap(input_data)
        if isinstance(data, list):
            return data, True
        return [], False

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "errors" in input_data:
            return [], False
        data = BigONERequestData._unwrap(input_data)
        if not data:
            return [], False
        return [data], True

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "errors" in input_data:
            return [], False
        return [{"success": True}], True

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "errors" in input_data:
            return [], False
        data = BigONERequestData._unwrap(input_data)
        if not data:
            return [], False
        return [data], True

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "errors" in input_data:
            return [], False
        data = BigONERequestData._unwrap(input_data)
        if isinstance(data, list):
            return data, True
        return [], False

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "errors" in input_data:
            return [], False
        data = BigONERequestData._unwrap(input_data)
        if isinstance(data, list):
            return data, True
        return [data], True

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "errors" in input_data:
            return [], False
        data = BigONERequestData._unwrap(input_data)
        if isinstance(data, list):
            return data, True
        return [data], True
