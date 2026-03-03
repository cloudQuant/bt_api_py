"""
WazirX REST API request base class – Feed pattern.
"""

import hashlib
import hmac
import time
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.wazirx_exchange_data import WazirxExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class WazirxRequestData(Feed):
    """WazirX REST API Feed base class."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "WAZIRX___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.api_key = kwargs.get("public_key", kwargs.get("api_key", None))
        self.api_secret = kwargs.get("secret_key", kwargs.get("api_secret", None))
        self._params = WazirxExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/wazirx_feed.log", "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/wazirx_feed.log", "async_request", 0, 0, False
        ).create_logger()

    # ── auth ────────────────────────────────────────────────────

    def _generate_signature(self, query_string):
        if self.api_secret:
            return hmac.new(
                self.api_secret.encode("utf-8"),
                query_string.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        return ""

    def _get_headers(self, **kwargs):
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if self.api_key:
            headers["X-API-KEY"] = self.api_key
        return headers

    # ── request / async_request ─────────────────────────────────

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        method = path.split()[0] if " " in path else "GET"
        endpoint = path.split()[1] if " " in path else path
        url = self._params.rest_url + endpoint
        if params:
            url = url + "?" + urlencode(params)
        headers = self._get_headers()
        response = self.http_request(
            method=method, url=url, headers=headers,
            body=body, timeout=timeout,
        )
        return self._process_response(response, extra_data)

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=10):
        method = path.split()[0] if " " in path else "GET"
        endpoint = path.split()[1] if " " in path else path
        url = self._params.rest_url + endpoint
        if params:
            url = url + "?" + urlencode(params)
        headers = self._get_headers()
        response = await self.async_http_request(
            method=method, url=url, headers=headers,
            body=body, timeout=timeout,
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
        if isinstance(data, dict) and ("code" in data or "message" in data):
            return True
        return False

    # ── _get_xxx internal methods ───────────────────────────────

    def _get_server_time(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_server_time")
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_server_time",
            "normalize_function": self._get_server_time_normalize_function,
        })
        return path, None, extra_data

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_tick")
        params = {"symbol": symbol}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_tick",
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return path, params, extra_data

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_depth")
        params = {"symbol": symbol, "limit": count}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_depth",
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        return path, params, extra_data

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_kline")
        params = {
            "symbol": symbol,
            "interval": self._params.get_period(period),
            "limit": count,
        }
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_kline",
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        return path, params, extra_data

    def _get_exchange_info(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_exchange_info")
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_exchange_info",
            "normalize_function": self._get_exchange_info_normalize_function,
        })
        return path, None, extra_data

    def _get_balance(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_balance")
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_balance",
            "normalize_function": self._get_balance_normalize_function,
        })
        return path, None, extra_data

    def _get_account(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_account")
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": "get_account",
            "normalize_function": self._get_account_normalize_function,
        })
        return path, None, extra_data

    # ── normalization functions ──────────────────────────────────

    @staticmethod
    def _get_server_time_normalize_function(data, extra_data):
        if data is None:
            return [], False
        return [data] if isinstance(data, dict) else [{"serverTime": data}], True

    @staticmethod
    def _get_tick_normalize_function(data, extra_data):
        if WazirxRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        if isinstance(data, list):
            return data, bool(data)
        return [], False

    @staticmethod
    def _get_depth_normalize_function(data, extra_data):
        if WazirxRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        return [], False

    @staticmethod
    def _get_kline_normalize_function(data, extra_data):
        if WazirxRequestData._is_error(data):
            return [], False
        if isinstance(data, list):
            return data, bool(data)
        if isinstance(data, dict):
            return [data], True
        return [], False

    @staticmethod
    def _get_exchange_info_normalize_function(data, extra_data):
        if WazirxRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        if isinstance(data, list):
            return data, bool(data)
        return [], False

    @staticmethod
    def _get_balance_normalize_function(data, extra_data):
        if WazirxRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        if isinstance(data, list):
            return data, bool(data)
        return [], False

    @staticmethod
    def _get_account_normalize_function(data, extra_data):
        if WazirxRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        if isinstance(data, list):
            return data, bool(data)
        return [], False
