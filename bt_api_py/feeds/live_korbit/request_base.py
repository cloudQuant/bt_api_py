"""Korbit REST API request base class.

API doc: https://docs.korbit.co.kr
Auth: OAuth2 Bearer token (Authorization: Bearer {token})
Symbol: {base}_{quote} lowercase (e.g. btc_krw)
"""

from urllib.parse import urlencode

from bt_api_py.containers.exchanges.korbit_exchange_data import KorbitExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger


class KorbitRequestData(Feed):
    """Korbit REST API Feed base class."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_DEALS,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.QUERY_OPEN_ORDERS,
        }

    def __init__(self, data_queue, **kwargs) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.api_key = kwargs.get("public_key") or kwargs.get("api_key") or ""
        self._api_secret = kwargs.get("private_key") or kwargs.get("api_secret") or ""
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "KORBIT___SPOT")
        self._params = KorbitExchangeDataSpot()
        self.request_logger = get_logger("korbit_spot_feed")
        self.async_logger = get_logger("korbit_spot_feed")

    # ── auth helpers ────────────────────────────────────────────

    def _get_headers(self):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
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

        if method == "GET":
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}{'?' + qs if qs else ''}"
            json_body = None
        else:
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body if body is not None else params
            if not json_body:
                json_body = None

        res = self.http_request(method, url, headers, json_body, timeout)
        self.request_logger.info(f"{method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async HTTP request."""
        if params is None:
            params = {}
        method, endpoint = path.split(" ", 1)
        headers = self._get_headers()

        if method == "GET":
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}{'?' + qs if qs else ''}"
            json_body = None
        else:
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body if body is not None else params
            if not json_body:
                json_body = None

        res = await self.async_http_request(method, url, headers, json_body, timeout)
        self.async_logger.info(f"async {method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    def async_callback(self, request_data):
        if request_data is not None:
            self.push_data_to_queue(request_data)

    # ── _get_xxx internal methods ───────────────────────────────

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_tick")
        request_symbol = self._params.get_symbol(symbol)
        params = {"currency_pair": request_symbol}
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

    def _get_depth(self, symbol, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_depth")
        request_symbol = self._params.get_symbol(symbol)
        params = {"currency_pair": request_symbol}
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

    def _get_deals(self, symbol, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_deals")
        request_symbol = self._params.get_symbol(symbol)
        params = {"currency_pair": request_symbol}
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

    def _get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_kline")
        request_symbol = self._params.get_symbol(symbol)
        exchange_period = self._params.get_period(period)
        params = {"currency_pair": request_symbol}
        if exchange_period:
            params["timeUnit"] = exchange_period
        if count:
            params["count"] = count
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_kline",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_kline_normalize_function,
            }
        )
        return path, params, extra_data

    def _make_order(self, symbol, side, order_type, amount, price=None, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        if side.lower() == "sell":
            path = self._params.get_rest_path("make_order_sell")
        else:
            path = self._params.get_rest_path("make_order")
        body = {
            "currency_pair": request_symbol,
            "type": order_type,
            "coin_amount": amount,
        }
        if price is not None and "limit" in order_type.lower():
            body["price"] = price
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

    def _cancel_order(self, order_id, extra_data=None, **kwargs):
        path = self._params.get_rest_path("cancel_order")
        body = {
            "id": order_id,
            "currency_pair": kwargs.get("currency_pair", "btc_krw"),
        }
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

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_open_orders")
        params = {}
        if symbol:
            params["currency_pair"] = self._params.get_symbol(symbol)
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

    # ── normalize helpers ───────────────────────────────────────
    # Korbit returns direct JSON; errors have "errorCode" key

    @staticmethod
    def _is_error(input_data):
        if input_data is None:
            return True
        if isinstance(input_data, dict) and "errorCode" in input_data:
            return True
        return False

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if KorbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if KorbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if KorbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list):
            return [input_data], True
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        if KorbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if KorbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if KorbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if KorbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [{}], True

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        if KorbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if KorbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        if isinstance(input_data, list):
            return input_data, True
        return [], False

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if KorbitRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        if isinstance(input_data, list):
            return input_data, True
        return [], False
