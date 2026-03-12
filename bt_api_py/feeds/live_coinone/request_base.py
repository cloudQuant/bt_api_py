"""
Coinone REST API request base class.

API: Public V2 (GET) / Private V2.1 (POST)
Auth: HMAC-SHA512 over Base64(JSON payload), secret uppercased
Response: {"result": "success", "errorCode": "0", ...}
Symbol: KRW-BTC  (quote-target)
"""

import base64
import hashlib
import hmac
import json
import uuid
from typing import Any
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.coinone_exchange_data import CoinoneExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger


class CoinoneRequestData(Feed):
    """Coinone REST API Feed base class."""

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
        }

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self._api_key = (
            kwargs.get("public_key") or kwargs.get("api_key") or kwargs.get("access_token") or ""
        )
        self._api_secret = (
            kwargs.get("private_key") or kwargs.get("api_secret") or kwargs.get("secret_key") or ""
        )
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "COINONE___SPOT")
        self._params = CoinoneExchangeDataSpot()
        self.request_logger = get_logger("coinone_feed")
        self.async_logger = get_logger("coinone_feed")

    @property
    def api_key(self):
        return self._api_key

    @property
    def api_secret(self):
        return self._api_secret

    # ── HMAC-SHA512 authentication ──────────────────────────────

    def _generate_payload(self, body_dict=None) -> Any:
        """Build Base64-encoded JSON payload for Coinone private API."""
        nonce = str(uuid.uuid4())
        payload_body = {"access_token": self._api_key, "nonce": nonce}
        if body_dict:
            payload_body.update(body_dict)
        json_str = json.dumps(payload_body)
        payload = base64.b64encode(json_str.encode("utf-8")).decode("utf-8")
        return payload

    def _generate_signature(self, payload) -> Any:
        """HMAC-SHA512(payload, SECRET.upper())."""
        if not self._api_secret:
            return ""
        secret = self._api_secret.upper()
        return hmac.new(
            secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

    def _generate_auth_headers(self, body_dict=None) -> Any:
        """Return Coinone auth headers dict for a POST request."""
        if not self._api_key:
            return {}, None
        payload = self._generate_payload(body_dict)
        sig = self._generate_signature(payload)
        headers = {
            "Content-Type": "application/json",
            "X-COINONE-PAYLOAD": payload,
            "X-COINONE-SIGNATURE": sig,
        }
        return headers, payload

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

        if method == "GET":
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}{'?' + qs if qs else ''}"
            json_body = None
            res = self.http_request(method, url, headers, json_body, timeout)
        else:
            # POST – private endpoints use payload auth
            url = f"{self._params.rest_url}{endpoint}"
            if is_sign and self._api_key:
                auth_headers, payload = self._generate_auth_headers(body)
                headers.update(auth_headers)
                json_body = None  # payload sent via header
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
            params: dict[str, Any] = {}
        method, endpoint = path.split(" ", 1)
        headers = {"Content-Type": "application/json"}

        if method == "GET":
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}{'?' + qs if qs else ''}"
            json_body = None
        else:
            url = f"{self._params.rest_url}{endpoint}"
            if is_sign and self._api_key:
                auth_headers, payload = self._generate_auth_headers(body)
                headers.update(auth_headers)
                json_body = None
            else:
                json_body = body

        res = await self.async_http_request(method, url, headers, json_body, timeout)
        self.async_logger.info(f"async {method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    def async_callback(self, request_data):
        if request_data is not None:
            self.push_data_to_queue(request_data)

    # ── _get_xxx internal methods ───────────────────────────────
    # Public market endpoints append /{target} to the base path.

    def _get_exchange_info(self, extra_data=None, **kwargs) -> Any:
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

    def _get_tick(self, symbol, extra_data=None, **kwargs) -> Any:
        base_path = self._params.get_rest_path("get_tick")
        quote, target = CoinoneExchangeDataSpot.parse_symbol(symbol)
        method_prefix = base_path.split(" ", 1)[0]
        rest_part = base_path.split(" ", 1)[1]
        path = f"{method_prefix} {rest_part}/{target}"
        params: dict[str, Any] = {}
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

    def _get_depth(self, symbol, count=15, extra_data=None, **kwargs) -> Any:
        base_path = self._params.get_rest_path("get_depth")
        quote, target = CoinoneExchangeDataSpot.parse_symbol(symbol)
        method_prefix = base_path.split(" ", 1)[0]
        rest_part = base_path.split(" ", 1)[1]
        path = f"{method_prefix} {rest_part}/{target}"
        params = {"size": count}
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

    def _get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs) -> Any:
        base_path = self._params.get_rest_path("get_kline")
        quote, target = CoinoneExchangeDataSpot.parse_symbol(symbol)
        method_prefix = base_path.split(" ", 1)[0]
        rest_part = base_path.split(" ", 1)[1]
        path = f"{method_prefix} {rest_part}/{target}"
        period_val = self._params.get_period(period)
        params = {"interval": period_val, "limit": count}
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

    def _get_trade_history(self, symbol, count=50, extra_data=None, **kwargs) -> Any:
        base_path = self._params.get_rest_path("get_trades")
        quote, target = CoinoneExchangeDataSpot.parse_symbol(symbol)
        method_prefix = base_path.split(" ", 1)[0]
        rest_part = base_path.split(" ", 1)[1]
        path = f"{method_prefix} {rest_part}/{target}"
        params: dict[str, Any] = {}
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
        self, symbol, size, price=None, order_type="bid", extra_data=None, **kwargs
    ) -> Any:
        path = self._params.get_rest_path("make_order")
        quote, target = CoinoneExchangeDataSpot.parse_symbol(symbol)
        body = {
            "quote_currency": quote,
            "target_currency": target,
            "type": order_type,
        }
        if size is not None:
            body["qty"] = str(size)
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

    def _cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs) -> Any:
        path = self._params.get_rest_path("cancel_order")
        body = {}
        if order_id:
            body["order_id"] = str(order_id)
        if symbol:
            quote, target = CoinoneExchangeDataSpot.parse_symbol(symbol)
            body["quote_currency"] = quote
            body["target_currency"] = target
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

    def _query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs) -> Any:
        path = self._params.get_rest_path("query_order")
        body = {}
        if order_id:
            body["order_id"] = str(order_id)
        if symbol:
            quote, target = CoinoneExchangeDataSpot.parse_symbol(symbol)
            body["quote_currency"] = quote
            body["target_currency"] = target
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

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs) -> Any:
        path = self._params.get_rest_path("get_open_orders")
        body = {}
        if symbol:
            quote, target = CoinoneExchangeDataSpot.parse_symbol(symbol)
            body["quote_currency"] = quote
            body["target_currency"] = target
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

    def _get_deals(self, symbol=None, extra_data=None, **kwargs) -> Any:
        path = self._params.get_rest_path("get_deals")
        body = {}
        if symbol:
            quote, target = CoinoneExchangeDataSpot.parse_symbol(symbol)
            body["quote_currency"] = quote
            body["target_currency"] = target
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

    def _get_account(self, extra_data=None, **kwargs) -> Any:
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

    def _get_balance(self, extra_data=None, **kwargs) -> Any:
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

    # ── normalize helpers ───────────────────────────────────────
    # Coinone: {"result": "success", "errorCode": "0", ...}
    # Error:   {"result": "error", "error_code": "107", ...}

    @staticmethod
    def _is_error(input_data):
        if input_data is None:
            return True
        if isinstance(input_data, dict):
            result = input_data.get("result")
            if result == "error":
                return True
            ec = input_data.get("errorCode") or input_data.get("error_code")
            if ec is not None and str(ec) != "0":
                return True
        return False

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if CoinoneRequestData._is_error(input_data):
            return [], False
        markets = input_data.get("markets", []) if isinstance(input_data, dict) else []
        if markets:
            return [{"markets": markets}], True
        return [], False

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if CoinoneRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            tickers = input_data.get("tickers")
            if isinstance(tickers, list) and tickers:
                return tickers, True
            # single ticker response (fields directly at root)
            if "last" in input_data:
                return [input_data], True
        return [], False

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if CoinoneRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if CoinoneRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            chart = input_data.get("chart", input_data.get("data"))
            if isinstance(chart, list) and chart:
                return chart, True
        if isinstance(input_data, list) and input_data:
            return input_data, True
        return [], False

    @staticmethod
    def _get_trade_history_normalize_function(input_data, extra_data):
        if CoinoneRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            trades = input_data.get("trades", [])
            if isinstance(trades, list) and trades:
                return trades, True
        return [], False

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if CoinoneRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if CoinoneRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        if CoinoneRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        if CoinoneRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            orders = input_data.get("limitOrders", input_data.get("open_orders", []))
            if isinstance(orders, list):
                return orders, True
        return [], False

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        if CoinoneRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            orders = input_data.get("completeOrders", input_data.get("complete_orders", []))
            if isinstance(orders, list):
                return orders, True
        return [], False

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if CoinoneRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if CoinoneRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict):
            balances = input_data.get("balances", {})
            if isinstance(balances, dict) and balances:
                return [balances], True
            if isinstance(balances, list) and balances:
                return balances, True
        return [], False
