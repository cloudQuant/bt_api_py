"""
CoinEx REST API request base class.

API V2: https://docs.coinex.com/api/v2/
Auth: HMAC-SHA256  (METHOD + request_path + body + timestamp)
Response envelope: {"code": 0, "data": ..., "message": "OK"}
Symbol format: BTCUSDT (base+quote, no separator)
"""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.coinex_exchange_data import CoinExExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class CoinExRequestData(Feed):
    """CoinEx REST API Feed base class."""

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

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self._api_key = kwargs.get("public_key") or kwargs.get("api_key") or kwargs.get("access_id") or ""
        self._api_secret = kwargs.get("private_key") or kwargs.get("api_secret") or kwargs.get("secret_key") or ""
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "COINEX___SPOT")
        self._params = CoinExExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/coinex_feed.log", "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/coinex_feed.log", "async_request", 0, 0, False
        ).create_logger()

    @property
    def api_key(self):
        return self._api_key

    @property
    def api_secret(self):
        return self._api_secret

    # ── HMAC-SHA256 authentication ──────────────────────────────

    def _generate_signature(self, method, request_path, body_str, timestamp):
        """CoinEx V2: METHOD + request_path(with qs) + body_str + timestamp."""
        if not self._api_secret:
            return ""
        prepared = f"{method}{request_path}{body_str}{timestamp}"
        return hmac.new(
            self._api_secret.encode("latin-1"),
            prepared.encode("latin-1"),
            hashlib.sha256,
        ).hexdigest().lower()

    def _generate_auth_headers(self, method, request_path, body_str=""):
        """Return CoinEx auth headers dict."""
        if not self._api_key:
            return {}
        ts = str(int(time.time() * 1000))
        sig = self._generate_signature(method, request_path, body_str, ts)
        return {
            "X-COINEX-KEY": self._api_key,
            "X-COINEX-SIGN": sig,
            "X-COINEX-TIMESTAMP": ts,
            "Content-Type": "application/json",
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
        headers = {"Content-Type": "application/json"}

        if method in ("GET", "DELETE"):
            qs = urlencode(params) if params else ""
            request_path = f"{endpoint}?{qs}" if qs else endpoint
            url = f"{self._params.rest_url}{request_path}"
            json_body = None
            if is_sign:
                headers.update(self._generate_auth_headers(method, request_path))
        else:
            request_path = endpoint
            url = f"{self._params.rest_url}{endpoint}"
            body_str = json.dumps(body) if body else ""
            json_body = body
            if is_sign:
                headers.update(self._generate_auth_headers(method, request_path, body_str))

        res = self.http_request(method, url, headers, json_body, timeout)
        self.request_logger.info(f"{method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5, is_sign=False):
        """Async HTTP request."""
        if params is None:
            params = {}
        method, endpoint = path.split(" ", 1)
        headers = {"Content-Type": "application/json"}

        if method in ("GET", "DELETE"):
            qs = urlencode(params) if params else ""
            request_path = f"{endpoint}?{qs}" if qs else endpoint
            url = f"{self._params.rest_url}{request_path}"
            json_body = None
            if is_sign:
                headers.update(self._generate_auth_headers(method, request_path))
        else:
            request_path = endpoint
            url = f"{self._params.rest_url}{endpoint}"
            body_str = json.dumps(body) if body else ""
            json_body = body
            if is_sign:
                headers.update(self._generate_auth_headers(method, request_path, body_str))

        res = await self.async_http_request(method, url, headers, json_body, timeout)
        self.async_logger.info(f"async {method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    def async_callback(self, request_data):
        if request_data is not None:
            self.push_data_to_queue(request_data)

    # ── _get_xxx internal methods ───────────────────────────────

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
        path = self._params.get_rest_path("get_tick")
        market = self._params.get_symbol(symbol)
        params = {"market": market}
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
        path = self._params.get_rest_path("get_depth")
        market = self._params.get_symbol(symbol)
        params = {"market": market, "limit": count}
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
        path = self._params.get_rest_path("get_kline")
        market = self._params.get_symbol(symbol)
        period_val = self._params.get_period(period)
        params = {"market": market, "period": period_val, "limit": count}
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

    def _get_trade_history(self, symbol, count=50, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_trades")
        market = self._params.get_symbol(symbol)
        params = {"market": market, "limit": count}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_trades",
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_trade_history_normalize_function,
        })
        return path, params, extra_data

    def _make_order(self, symbol, size, price=None, order_type="buy-limit", extra_data=None, **kwargs):
        path = self._params.get_rest_path("make_order")
        market = self._params.get_symbol(symbol)
        parts = order_type.lower().replace("-", " ").split()
        side = parts[0] if parts else "buy"
        ord_type = parts[1] if len(parts) > 1 else "limit"
        body = {
            "market": market,
            "market_type": "SPOT",
            "side": side,
            "type": ord_type,
        }
        if size is not None:
            body["amount"] = str(size)
        if price is not None:
            body["price"] = str(price)
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
        path = self._params.get_rest_path("cancel_order")
        params = {}
        if symbol:
            params["market"] = self._params.get_symbol(symbol)
        if order_id:
            params["order_id"] = str(order_id)
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
        path = self._params.get_rest_path("query_order")
        params = {}
        if symbol:
            params["market"] = self._params.get_symbol(symbol)
        if order_id:
            params["order_id"] = str(order_id)
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
        params = {"market_type": "SPOT"}
        if symbol:
            params["market"] = self._params.get_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_open_orders",
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_open_orders_normalize_function,
        })
        return path, params, extra_data

    def _get_deals(self, symbol=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_deals")
        params = {"market_type": "SPOT"}
        if symbol:
            params["market"] = self._params.get_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_deals",
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_deals_normalize_function,
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
    # CoinEx V2 envelope: {"code": 0, "data": ..., "message": "OK"}

    @staticmethod
    def _is_error(input_data):
        if input_data is None:
            return True
        if isinstance(input_data, dict):
            code = input_data.get("code")
            if code is not None and code != 0:
                return True
        return False

    @staticmethod
    def _unwrap(input_data):
        """Extract 'data' from V2 envelope, or return as-is."""
        if isinstance(input_data, dict) and "data" in input_data:
            return input_data["data"]
        return input_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if CoinExRequestData._is_error(input_data):
            return [], False
        data = CoinExRequestData._unwrap(input_data)
        if isinstance(data, list):
            return [{"markets": data}], True
        if data:
            return [{"markets": data}], True
        return [], False

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if CoinExRequestData._is_error(input_data):
            return [], False
        data = CoinExRequestData._unwrap(input_data)
        if isinstance(data, list) and len(data) > 0:
            return data, True
        if isinstance(data, dict) and data:
            return [data], True
        return [], False

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if CoinExRequestData._is_error(input_data):
            return [], False
        data = CoinExRequestData._unwrap(input_data)
        if isinstance(data, dict) and data:
            return [data], True
        return [], False

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if CoinExRequestData._is_error(input_data):
            return [], False
        data = CoinExRequestData._unwrap(input_data)
        if isinstance(data, list) and len(data) > 0:
            return data, True
        return [], False

    @staticmethod
    def _get_trade_history_normalize_function(input_data, extra_data):
        if CoinExRequestData._is_error(input_data):
            return [], False
        data = CoinExRequestData._unwrap(input_data)
        if isinstance(data, list) and len(data) > 0:
            return data, True
        return [], False

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if CoinExRequestData._is_error(input_data):
            return [], False
        data = CoinExRequestData._unwrap(input_data)
        if not data:
            return [], False
        return [data], True

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if CoinExRequestData._is_error(input_data):
            return [], False
        data = CoinExRequestData._unwrap(input_data)
        if not data:
            return [], False
        return [data], True

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        if CoinExRequestData._is_error(input_data):
            return [], False
        data = CoinExRequestData._unwrap(input_data)
        if not data:
            return [], False
        return [data], True

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        if CoinExRequestData._is_error(input_data):
            return [], False
        data = CoinExRequestData._unwrap(input_data)
        if isinstance(data, list):
            return data, True
        return [], False

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        if CoinExRequestData._is_error(input_data):
            return [], False
        data = CoinExRequestData._unwrap(input_data)
        if isinstance(data, list):
            return data, True
        return [], False

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if CoinExRequestData._is_error(input_data):
            return [], False
        data = CoinExRequestData._unwrap(input_data)
        if isinstance(data, dict) and data:
            return [data], True
        if isinstance(data, list):
            return data, True
        return [], False

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if CoinExRequestData._is_error(input_data):
            return [], False
        data = CoinExRequestData._unwrap(input_data)
        if isinstance(data, list):
            return data, True
        if isinstance(data, dict) and data:
            return [data], True
        return [], False
