"""
Upbit REST API request base class.

Auth: JWT (HS256) with SHA512 query hash.
Symbol format: KRW-BTC (quote-base with dash).
Kline: separate endpoints for minutes/days/weeks/months.
Response: direct JSON array/object. Errors: {"error": {"name": ..., "message": ...}}.
"""

import hashlib
import uuid as _uuid
from urllib.parse import unquote, urlencode

try:
    import jwt as _jwt
except ImportError:
    _jwt = None

from bt_api_py.containers.exchanges.upbit_exchange_data import UpbitExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger


class UpbitRequestData(Feed):
    """Upbit REST API Feed base class."""

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
        }

    # ── minute-value → period key mapping ───────────────────────
    _MINUTE_PERIODS = {"1", "3", "5", "10", "15", "30", "60", "120", "240", "360", "480", "720"}

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self._api_key = (
            kwargs.get("public_key") or kwargs.get("api_key") or kwargs.get("access_key") or ""
        )
        self._api_secret = (
            kwargs.get("private_key") or kwargs.get("api_secret") or kwargs.get("secret_key") or ""
        )
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "UPBIT___SPOT")
        self._params = UpbitExchangeDataSpot()
        self.request_logger = get_logger("upbit_feed")
        self.async_logger = get_logger("upbit_feed")

    @property
    def api_key(self):
        return self._api_key

    @property
    def api_secret(self):
        return self._api_secret

    # ── JWT authentication ──────────────────────────────────────

    def _generate_jwt_token(self, params=None):
        """Generate JWT token for Upbit authentication."""
        if not self._api_key or not self._api_secret:
            return None
        if _jwt is None:
            return None
        nonce = str(_uuid.uuid4())
        if params:
            query_string = unquote(urlencode(params, doseq=True))
            query_hash = hashlib.sha512(query_string.encode()).hexdigest()
            payload = {
                "access_key": self._api_key,
                "nonce": nonce,
                "query_hash": query_hash,
                "query_hash_alg": "SHA512",
            }
        else:
            payload = {
                "access_key": self._api_key,
                "nonce": nonce,
            }
        return _jwt.encode(payload, self._api_secret, algorithm="HS256")

    def _generate_auth_headers(self, params=None):
        """Return Authorization header dict."""
        token = self._generate_jwt_token(params)
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}

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

        if method == "GET" or method == "DELETE":
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}"
            if qs:
                url = f"{url}?{qs}"
            json_body = None
            if is_sign:
                headers.update(self._generate_auth_headers(params or None))
        else:
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body or params
            headers["Content-Type"] = "application/json"
            if is_sign:
                headers.update(self._generate_auth_headers(json_body or None))

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

        if method in ("GET", "DELETE"):
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}"
            if qs:
                url = f"{url}?{qs}"
            json_body = None
            if is_sign:
                headers.update(self._generate_auth_headers(params or None))
        else:
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body or params
            headers["Content-Type"] = "application/json"
            if is_sign:
                headers.update(self._generate_auth_headers(json_body or None))

        res = await self.async_http_request(method, url, headers, json_body, timeout)
        self.async_logger.info(f"async {method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    def async_callback(self, request_data):
        if request_data is not None:
            self.push_data_to_queue(request_data)

    # ── _get_xxx internal methods ───────────────────────────────

    def _get_exchange_info(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_exchange_info")
        params = {"isDetails": "true"}
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
        path = self._params.get_rest_path("get_tick")
        market = self._params.get_symbol(symbol)
        params = {"markets": market}
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
        path = self._params.get_rest_path("get_depth")
        market = self._params.get_symbol(symbol)
        params = {"markets": market}
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

    def _get_kline(self, symbol, period="1h", count=200, extra_data=None, **kwargs):
        """Route to the correct kline endpoint based on period."""
        market = self._params.get_symbol(symbol)
        period_val = self._params.get_period(period)

        if period_val in self._MINUTE_PERIODS:
            base_path = self._params.get_rest_path("get_kline_minutes")
            # Replace {unit} placeholder
            base_path = base_path.replace("{unit}", period_val)
        elif period_val == "D" or period_val == "3D":
            base_path = self._params.get_rest_path("get_kline_days")
        elif period_val == "W":
            base_path = self._params.get_rest_path("get_kline_weeks")
        elif period_val == "M":
            base_path = self._params.get_rest_path("get_kline_months")
        else:
            base_path = self._params.get_rest_path("get_kline_minutes")
            base_path = base_path.replace("{unit}", period_val)

        params = {"market": market, "count": min(count, 200)}
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
        return base_path, params, extra_data

    def _get_trade_history(self, symbol, count=50, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_trades")
        market = self._params.get_symbol(symbol)
        params = {"market": market, "count": min(count, 500)}
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
        self, symbol, size, price=None, order_type="bid-limit", extra_data=None, **kwargs
    ):
        path = self._params.get_rest_path("make_order")
        market = self._params.get_symbol(symbol)
        parts = order_type.lower().replace("-", " ").split()
        side = parts[0] if parts else "bid"  # Upbit: "bid" for buy, "ask" for sell
        ord_type = parts[1] if len(parts) > 1 else "limit"
        body = {"market": market, "side": side, "ord_type": ord_type}
        if size is not None:
            body["volume"] = str(size)
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
        params = {}
        if order_id:
            params["uuid"] = str(order_id)
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
        params = {}
        if order_id:
            params["uuid"] = str(order_id)
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
        params = {"state": "wait"}
        if symbol:
            params["market"] = self._params.get_symbol(symbol)
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
        path = self._params.get_rest_path("get_open_orders")
        params = {"state": "done"}
        if symbol:
            params["market"] = self._params.get_symbol(symbol)
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
    # Upbit: direct JSON. Errors: {"error": {"name": ..., "message": ...}}

    @staticmethod
    def _is_error(input_data):
        if input_data is None:
            return True
        if isinstance(input_data, dict) and "error" in input_data:
            return True
        return False

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if UpbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list):
            return [{"markets": input_data}], True
        return [input_data], True

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if UpbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list) and len(input_data) > 0:
            return input_data, True
        if isinstance(input_data, dict):
            return [input_data], True
        return [], False

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if UpbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list) and len(input_data) > 0:
            return input_data, True
        if isinstance(input_data, dict):
            return [input_data], True
        return [], False

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if UpbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list) and len(input_data) > 0:
            return input_data, True
        return [], False

    @staticmethod
    def _get_trade_history_normalize_function(input_data, extra_data):
        if UpbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list) and len(input_data) > 0:
            return input_data, True
        return [], False

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if UpbitRequestData._is_error(input_data):
            return [], False
        if not input_data:
            return [], False
        return [input_data], True

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if UpbitRequestData._is_error(input_data):
            return [], False
        if not input_data:
            return [], False
        return [input_data], True

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        if UpbitRequestData._is_error(input_data):
            return [], False
        if not input_data:
            return [], False
        return [input_data], True

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        if isinstance(input_data, list):
            return input_data, True
        if UpbitRequestData._is_error(input_data):
            return [], False
        return [], False

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        if isinstance(input_data, list):
            return input_data, True
        if UpbitRequestData._is_error(input_data):
            return [], False
        return [], False

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if UpbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        if isinstance(input_data, dict):
            return [input_data], True
        return [], False

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if UpbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        if isinstance(input_data, dict):
            return [input_data], True
        return [], False
