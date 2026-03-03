"""
CoinSwitch REST API request base class.

API: REST V2  (https://docs.coinswitch.co/)
Auth: API key in header x-api-key
Response: {"data": ...} or {"error": ...}
Symbol: pair string "BTCINR"
"""

import json
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.coinswitch_exchange_data import CoinSwitchExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class CoinSwitchRequestData(Feed):
    """CoinSwitch REST API Feed base class."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_DEALS,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.QUERY_OPEN_ORDERS,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self._api_key = kwargs.get("public_key") or kwargs.get("api_key") or ""
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "COINSWITCH___SPOT")
        self._params = CoinSwitchExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/coinswitch_feed.log", "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/coinswitch_feed.log", "async_request", 0, 0, False
        ).create_logger()

    @property
    def api_key(self):
        return self._api_key

    # ── headers ─────────────────────────────────────────────────

    def _get_headers(self):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    # ── core request helpers ────────────────────────────────────

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """Synchronous HTTP request."""
        if params is None:
            params = {}
        method, endpoint = path.split(" ", 1)
        headers = self._get_headers()

        if method in ("GET", "DELETE"):
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}{'?' + qs if qs else ''}"
            json_body = None
        else:
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body

        res = self.http_request(method, url, headers, json_body, timeout)
        self.request_logger.info(f"{method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async HTTP request."""
        if params is None:
            params = {}
        method, endpoint = path.split(" ", 1)
        headers = self._get_headers()

        if method in ("GET", "DELETE"):
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}{'?' + qs if qs else ''}"
            json_body = None
        else:
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body

        res = await self.async_http_request(method, url, headers, json_body, timeout)
        self.async_logger.info(f"async {method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    def async_callback(self, request_data):
        if request_data is not None:
            self.push_data_to_queue(request_data)

    # ── _get_xxx internal methods ───────────────────────────────

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        base_path = self._params.get_rest_path("get_tick")
        method_prefix = base_path.split(" ", 1)[0]
        rest_part = base_path.split(" ", 1)[1]
        path = f"{method_prefix} {rest_part}/{symbol}"
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

    def _get_all_tickers(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_tick")
        params = {}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_ticker_all",
            "symbol_name": None,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_all_tickers_normalize_function,
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

    def _get_trade_history(self, symbol, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_trade_history")
        params = {"symbol": symbol}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_trade_history",
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_trade_history_normalize_function,
        })
        return path, params, extra_data

    def _make_order(self, symbol, side, order_type, amount, price=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("make_order")
        body = {"symbol": symbol, "side": side, "type": order_type, "amount": amount}
        if price is not None:
            body["price"] = price
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "make_order",
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._make_order_normalize_function,
        })
        return path, body, extra_data

    def _cancel_order(self, order_id, extra_data=None, **kwargs):
        base_path = self._params.get_rest_path("cancel_order")
        method_prefix = base_path.split(" ", 1)[0]
        rest_part = base_path.split(" ", 1)[1]
        path = f"{method_prefix} {rest_part}/{order_id}"
        params = {}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "cancel_order",
            "symbol_name": None,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._cancel_order_normalize_function,
        })
        return path, params, extra_data

    def _get_open_orders(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_open_orders")
        params = {}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_open_orders",
            "symbol_name": None,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_open_orders_normalize_function,
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

    # ── normalize helpers ───────────────────────────────────────
    # CoinSwitch: {"data": ...} success  /  {"error": ...} failure

    @staticmethod
    def _is_error(input_data):
        if input_data is None:
            return True
        if isinstance(input_data, dict):
            if "error" in input_data:
                return True
        return False

    @staticmethod
    def _unwrap(input_data):
        """Unwrap {"data": ...} envelope, returning inner data."""
        if isinstance(input_data, dict):
            return input_data.get("data", input_data)
        return input_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if CoinSwitchRequestData._is_error(input_data):
            return [], False
        data = CoinSwitchRequestData._unwrap(input_data)
        if data is not None:
            return [data], True
        return [], False

    @staticmethod
    def _get_all_tickers_normalize_function(input_data, extra_data):
        if CoinSwitchRequestData._is_error(input_data):
            return [], False
        data = CoinSwitchRequestData._unwrap(input_data)
        if data is not None:
            return [data], True
        return [], False

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if CoinSwitchRequestData._is_error(input_data):
            return [], False
        data = CoinSwitchRequestData._unwrap(input_data)
        if data is not None:
            return [data], True
        return [], False

    @staticmethod
    def _get_trade_history_normalize_function(input_data, extra_data):
        if CoinSwitchRequestData._is_error(input_data):
            return [], False
        data = CoinSwitchRequestData._unwrap(input_data)
        if data is not None:
            if isinstance(data, list):
                return data, True
            return [data], True
        return [], False

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if CoinSwitchRequestData._is_error(input_data):
            return [], False
        data = CoinSwitchRequestData._unwrap(input_data)
        if data is not None:
            return [data], True
        return [], False

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if CoinSwitchRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        if CoinSwitchRequestData._is_error(input_data):
            return [], False
        data = CoinSwitchRequestData._unwrap(input_data)
        if data is not None:
            if isinstance(data, list):
                return data, True
            return [data], True
        return [], False

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if CoinSwitchRequestData._is_error(input_data):
            return [], False
        data = CoinSwitchRequestData._unwrap(input_data)
        if data is not None:
            if isinstance(data, list):
                return data, True
            return [data], True
        return [], False

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if CoinSwitchRequestData._is_error(input_data):
            return [], False
        data = CoinSwitchRequestData._unwrap(input_data)
        if data is not None:
            return [data], True
        return [], False
