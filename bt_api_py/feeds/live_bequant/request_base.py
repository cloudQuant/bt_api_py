"""
BeQuant REST API request base class.

BeQuant uses HitBTC V3 API (white-label).
Authentication: HTTP Basic Auth (API key as username, secret as password).
"""

import base64
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.bequant_exchange_data import BeQuantExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger


class BeQuantRequestData(Feed):
    """BeQuant REST API Feed base class (HitBTC V3 compatible)."""

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
        self.exchange_name = kwargs.get("exchange_name", "BEQUANT___SPOT")
        self._params = BeQuantExchangeDataSpot()
        self.request_logger = get_logger("bequant_feed")
        self.async_logger = get_logger("bequant_feed")

    @property
    def api_key(self):
        return self._api_key

    @property
    def api_secret(self):
        return self._api_secret

    # ── authentication ──────────────────────────────────────────

    def _basic_auth_header(self):
        """Build HTTP Basic Auth header value."""
        auth_string = f"{self._api_key}:{self._api_secret}"
        return "Basic " + base64.b64encode(auth_string.encode("ascii")).decode("ascii")

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
        if is_sign and self._api_key and self._api_secret:
            headers["Authorization"] = self._basic_auth_header()

        json_body = body if body and method in ("POST", "PUT") else None
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
        query_string = urlencode(params) if params else ""
        url = f"{self._params.rest_url}{endpoint}"
        if query_string:
            url = f"{url}?{query_string}"

        headers = {"Content-Type": "application/json"}
        if is_sign and self._api_key and self._api_secret:
            headers["Authorization"] = self._basic_auth_header()

        json_body = body if body and method in ("POST", "PUT") else None
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
        bq_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_tick").replace("{symbol}", bq_symbol)
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

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        bq_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_depth").replace("{symbol}", bq_symbol)
        params = {"depth": count}
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
        bq_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_kline").replace("{symbol}", bq_symbol)
        bq_period = self._params.get_period(period)
        params = {"period": bq_period, "limit": min(count, 1000)}
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

    def _get_trade_history(self, symbol, count=100, extra_data=None, **kwargs):
        bq_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_trades").replace("{symbol}", bq_symbol)
        params = {"limit": count}
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
        self, symbol, amount, price=None, order_type="buy-limit", extra_data=None, **kwargs
    ):
        path = self._params.get_rest_path("make_order")
        bq_symbol = self._params.get_symbol(symbol)
        parts = order_type.lower().replace("-", " ").split()
        side = parts[0] if parts else "buy"
        otype = parts[1] if len(parts) > 1 else "limit"
        body = {
            "symbol": bq_symbol,
            "side": side,
            "type": otype,
            "quantity": str(amount),
        }
        if price is not None and otype == "limit":
            body["price"] = str(price)
        body["time_in_force"] = kwargs.get("time_in_force", "GTC")
        if kwargs.get("client_order_id"):
            body["client_order_id"] = kwargs["client_order_id"]
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
        path = self._params.get_rest_path("cancel_order").replace(
            "{client_order_id}", str(order_id or "")
        )
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
        return path, params, extra_data

    def _query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("query_order").replace(
            "{client_order_id}", str(order_id or "")
        )
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
        return path, params, extra_data

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_open_orders")
        params = {}
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

    @staticmethod
    def _is_error(input_data):
        if not input_data:
            return True
        if isinstance(input_data, dict) and "error" in input_data:
            return True
        return False

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "error" in input_data:
            return [], False
        return [{"server_time": input_data}], True

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "error" in input_data:
            return [], False
        if isinstance(input_data, dict):
            return [{"symbols": input_data}], True
        return [{"symbols": {}}], True

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "error" in input_data:
            return [], False
        return [input_data], True

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "error" in input_data:
            return [], False
        return [input_data], True

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "error" in input_data:
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        return [], False

    @staticmethod
    def _get_trade_history_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "error" in input_data:
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        return [], False

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "error" in input_data:
            return [], False
        return [input_data], True

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "error" in input_data:
            return [], False
        return [{"success": True}], True

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "error" in input_data:
            return [], False
        return [input_data], True

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "error" in input_data:
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        return [], False

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "error" in input_data:
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        return [input_data], True

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "error" in input_data:
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        return [input_data], True
