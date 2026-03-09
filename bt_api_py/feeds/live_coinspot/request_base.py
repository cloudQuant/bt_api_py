"""
CoinSpot REST API request base class.

API: Public V2 (GET) / Private V2 (POST)
Auth: HMAC-SHA512 over JSON body (with nonce); headers: key + sign
Response: {"status": "ok", ...} / {"status": "error", "message": "..."}
Symbol: coin shortname "BTC"
"""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.coinspot_exchange_data import CoinSpotExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger


class CoinSpotRequestData(Feed):
    """CoinSpot REST API Feed base class."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_DEPTH,
            Capability.GET_DEALS,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
        }

    def __init__(self, data_queue, **kwargs) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self._api_key = kwargs.get("public_key") or kwargs.get("api_key") or ""
        self._api_secret = (
            kwargs.get("private_key") or kwargs.get("api_secret") or kwargs.get("secret_key") or ""
        )
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "COINSPOT___SPOT")
        self._params = CoinSpotExchangeDataSpot()
        self.request_logger = get_logger("coinspot_feed")
        self.async_logger = get_logger("coinspot_feed")

    @property
    def api_key(self):
        return self._api_key

    @property
    def api_secret(self):
        return self._api_secret

    # ── HMAC-SHA512 authentication ──────────────────────────────

    def _generate_signature(self, body_str):
        """HMAC-SHA512(body_json_string, secret)."""
        if not self._api_secret:
            return ""
        return hmac.new(
            self._api_secret.encode("utf-8"),
            body_str.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

    def _generate_auth_headers(self, body_dict=None):
        """Return CoinSpot auth headers + serialised body for a POST request."""
        nonce = int(time.time() * 1000)
        payload = {"nonce": nonce}
        if body_dict:
            payload.update(body_dict)
        body_str = json.dumps(payload)
        sig = self._generate_signature(body_str)
        headers = {
            "Content-Type": "application/json",
            "key": self._api_key,
            "sign": sig,
        }
        return headers, body_str

    # ── core request helpers ────────────────────────────────────

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)

    def request(self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=False):
        """Synchronous HTTP request."""
        if params is None:
            params = {}
        method, endpoint = path.split(" ", 1)
        headers = {"Content-Type": "application/json"}

        if method == "GET":
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}{'?' + qs if qs else ''}"
            json_body = None
            res = self.http_request(method, url, headers, json_body, timeout)
        else:
            url = f"{self._params.rest_url}{endpoint}"
            if is_sign and self._api_key:
                auth_headers, body_str = self._generate_auth_headers(body)
                headers.update(auth_headers)
                json_body = json.loads(body_str)
            else:
                json_body = body
            res = self.http_request(method, url, headers, json_body, timeout)

        self.request_logger.info(f"{method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    async def async_request(
        self, path, params=None, body=None, extra_data=None, timeout=5, is_sign=False
    ):
        """Async HTTP request."""
        if params is None:
            params = {}
        method, endpoint = path.split(" ", 1)
        headers = {"Content-Type": "application/json"}

        if method == "GET":
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}{'?' + qs if qs else ''}"
            json_body = None
        else:
            url = f"{self._params.rest_url}{endpoint}"
            if is_sign and self._api_key:
                auth_headers, body_str = self._generate_auth_headers(body)
                headers.update(auth_headers)
                json_body = json.loads(body_str)
            else:
                json_body = body

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
        base_path = self._params.get_rest_path("get_tick")
        method_prefix = base_path.split(" ", 1)[0]
        rest_part = base_path.split(" ", 1)[1]
        path = f"{method_prefix} {rest_part}/{symbol}"
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

    def _get_all_tickers(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_tick")
        params = {}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_ticker_all",
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_all_tickers_normalize_function,
            }
        )
        return path, params, extra_data

    def _get_depth(self, symbol, extra_data=None, **kwargs):
        base_path = self._params.get_rest_path("get_depth")
        method_prefix = base_path.split(" ", 1)[0]
        rest_part = base_path.split(" ", 1)[1]
        path = f"{method_prefix} {rest_part}/{symbol}"
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

    def _get_deals(self, symbol, extra_data=None, **kwargs):
        base_path = self._params.get_rest_path("get_deals")
        method_prefix = base_path.split(" ", 1)[0]
        rest_part = base_path.split(" ", 1)[1]
        path = f"{method_prefix} {rest_part}/{symbol}"
        params = {}
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

    def _make_order(self, symbol, amount, rate, side="buy", extra_data=None, **kwargs):
        key = "make_order_buy" if side == "buy" else "make_order_sell"
        path = self._params.get_rest_path(key)
        body = {"cointype": symbol, "amount": amount, "rate": rate}
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

    def _cancel_order(self, order_id, side="buy", extra_data=None, **kwargs):
        key = "cancel_order_buy" if side == "buy" else "cancel_order_sell"
        path = self._params.get_rest_path(key)
        body = {"id": str(order_id)}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "cancel_order",
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._cancel_order_normalize_function,
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

    # ── normalize helpers ───────────────────────────────────────
    # CoinSpot: {"status": "ok", "prices": {...}}  or  {"status": "error", "message": "..."}

    @staticmethod
    def _is_error(input_data):
        if input_data is None:
            return True
        if isinstance(input_data, dict):
            if input_data.get("status") == "error":
                return True
        return False

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if CoinSpotRequestData._is_error(input_data):
            return [], False
        prices = input_data.get("prices", {}) if isinstance(input_data, dict) else {}
        if prices:
            return [{"prices": prices}], True
        return [], False

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if CoinSpotRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            prices = input_data.get("prices", {})
            if prices:
                return [prices], True
        return [], False

    @staticmethod
    def _get_all_tickers_normalize_function(input_data, extra_data):
        if CoinSpotRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            prices = input_data.get("prices", {})
            if prices:
                return [{"prices": prices, "status": "ok"}], True
        return [], False

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if CoinSpotRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            orders = input_data.get("buyorders", input_data.get("sellorders", []))
            if orders or "buyorders" in input_data or "sellorders" in input_data:
                return [input_data], True
        return [], False

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        if CoinSpotRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            orders = input_data.get("buyorders", input_data.get("sellorders", []))
            if orders or "buyorders" in input_data or "sellorders" in input_data:
                return [input_data], True
        return [], False

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if CoinSpotRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if CoinSpotRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if CoinSpotRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            balances = input_data.get("balances", input_data.get("balance", {}))
            if balances:
                if isinstance(balances, list):
                    return balances, True
                return [balances], True
        return [], False

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if CoinSpotRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False
