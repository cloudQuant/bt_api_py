"""VALR REST API request base class – Feed pattern."""

import hashlib
import hmac
import time
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.valr_exchange_data import ValrExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger


class ValrRequestData(Feed):
    """VALR REST API Feed base class."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "VALR___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.api_key = kwargs.get("public_key", kwargs.get("api_key"))
        self.api_secret = kwargs.get("secret_key", kwargs.get("api_secret"))
        self._params = ValrExchangeDataSpot()
        self.request_logger = get_logger("valr_feed")
        self.async_logger = get_logger("valr_feed")

    # ── auth ────────────────────────────────────────────────────

    def _generate_signature(self, timestamp, verb, path, body=""):
        if self.api_secret:
            sign_str = f"{timestamp}{verb.upper()}{path}{body}"
            return hmac.new(
                self.api_secret.encode("utf-8"),
                sign_str.encode("utf-8"),
                hashlib.sha512,
            ).hexdigest()
        return ""

    def _get_headers(self, method="GET", request_path="", **kwargs):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            ts = str(int(time.time() * 1000))
            headers["X-VALR-API-KEY"] = self.api_key
            headers["X-VALR-TIMESTAMP"] = ts
            headers["X-VALR-SIGNATURE"] = self._generate_signature(ts, method, request_path)
        return headers

    # ── request / async_request ─────────────────────────────────

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        method = path.split()[0] if " " in path else "GET"
        endpoint = path.split()[1] if " " in path else path
        url = self._params.rest_url + endpoint
        if params:
            url = url + "?" + urlencode(params)
        headers = self._get_headers(method=method, request_path=endpoint)
        response = self.http_request(
            method=method,
            url=url,
            headers=headers,
            body=body,
            timeout=timeout,
        )
        return self._process_response(response, extra_data)

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=10):
        method = path.split()[0] if " " in path else "GET"
        endpoint = path.split()[1] if " " in path else path
        url = self._params.rest_url + endpoint
        if params:
            url = url + "?" + urlencode(params)
        headers = self._get_headers(method=method, request_path=endpoint)
        response = await self.async_http_request(
            method=method,
            url=url,
            headers=headers,
            body=body,
            timeout=timeout,
        )
        return self._process_response(response, extra_data)

    def _process_response(self, response, extra_data=None):
        return RequestData(response, extra_data)

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)

    def async_callback(self, future):
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.warn(f"async_callback::{e}")

    def connect(self):
        pass

    def disconnect(self):
        pass

    def is_connected(self):
        return True

    # ── error detection ─────────────────────────────────────────

    @staticmethod
    def _is_error(data):
        if data is None:
            return True
        return bool(isinstance(data, dict) and ("error" in data or "message" in data))

    # ── _get_xxx internal methods ───────────────────────────────

    def _get_server_time(self, extra_data=None, **kwargs) -> float:
        path = self._params.get_rest_path("get_server_time")
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_server_time",
                "normalize_function": self._get_server_time_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_tick", symbol=symbol)
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_tick",
                "symbol_name": symbol,
                "normalize_function": self._get_tick_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_depth", symbol=symbol)
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_depth",
                "symbol_name": symbol,
                "normalize_function": self._get_depth_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_kline", symbol=symbol)
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_kline",
                "symbol_name": symbol,
                "normalize_function": self._get_kline_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_exchange_info(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_exchange_info")
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_exchange_info",
                "normalize_function": self._get_exchange_info_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_balance(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_balance")
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_balance",
                "normalize_function": self._get_balance_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_account(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_account")
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_account",
                "normalize_function": self._get_account_normalize_function,
            }
        )
        return path, None, extra_data

    # ── normalization functions ──────────────────────────────────

    @staticmethod
    def _get_server_time_normalize_function(data, extra_data):
        if data is None:
            return [], False
        return [data] if isinstance(data, dict) else [{"serverTime": data}], True

    @staticmethod
    def _get_tick_normalize_function(data, extra_data):
        if ValrRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        if isinstance(data, list):
            return data, bool(data)
        return [], False

    @staticmethod
    def _get_depth_normalize_function(data, extra_data):
        if ValrRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        return [], False

    @staticmethod
    def _get_kline_normalize_function(data, extra_data):
        if ValrRequestData._is_error(data):
            return [], False
        if isinstance(data, list):
            return data, bool(data)
        if isinstance(data, dict):
            return [data], True
        return [], False

    @staticmethod
    def _get_exchange_info_normalize_function(data, extra_data):
        if ValrRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        if isinstance(data, list):
            return data, bool(data)
        return [], False

    @staticmethod
    def _get_balance_normalize_function(data, extra_data):
        if ValrRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        if isinstance(data, list):
            return data, bool(data)
        return [], False

    @staticmethod
    def _get_account_normalize_function(data, extra_data):
        if ValrRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        if isinstance(data, list):
            return data, bool(data)
        return [], False
