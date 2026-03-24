"""
Bitget REST API request base class.
Handles HMAC SHA256 + Base64 authentication and all REST API methods.
Follows the standard three-layer pattern: _get_xxx() / get_xxx() / async_get_xxx()
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.bitget_exchange_data import BitgetExchangeData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.error import BitgetErrorTranslator
from bt_api_py.exceptions import QueueNotInitializedError
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger


class BitgetRequestData(Feed):
    """Bitget REST API base request class with HMAC SHA256 + Base64 auth."""

    @classmethod
    def _capabilities(cls) -> set[Capability]:
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_SERVER_TIME,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.QUERY_ORDER,
            Capability.QUERY_OPEN_ORDERS,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.GET_DEALS,
        }

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.public_key = kwargs.get("public_key") or kwargs.get("api_key")
        self.private_key = (
            kwargs.get("private_key") or kwargs.get("secret_key") or kwargs.get("api_secret")
        )
        self.passphrase = kwargs.get("passphrase", "")
        self.exchange_name = kwargs.get("exchange_name", "BITGET___SPOT")
        self.asset_type = kwargs.get("asset_type", "spot")
        self.logger_name = kwargs.get("logger_name", "bitget_feed.log")
        self._params = BitgetExchangeData()
        self.request_logger = get_logger("request")
        self.async_logger = get_logger("async_request")
        self._error_translator = BitgetErrorTranslator()

    def translate_error(self, raw_response):
        if isinstance(raw_response, dict):
            code = raw_response.get("code", "00000")
            if code != "00000":
                return self._error_translator.translate(raw_response, self.exchange_name)
        return None

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            raise QueueNotInitializedError("data_queue not initialized")

    def _generate_signature(self, message):
        pk = self.private_key or ""
        mac = hmac.new(pk.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _build_auth_headers(self, method, request_path, body_string=""):
        timestamp = str(int(time.time() * 1000))
        sign_message = f"{timestamp}{method.upper()}{request_path}{body_string}"
        signature = self._generate_signature(sign_message)
        return {
            "ACCESS-KEY": self.public_key,
            "ACCESS-SIGN": signature,
            "ACCESS-PASSPHRASE": self.passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
            "locale": "en-US",
        }

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        if params is None:
            params: dict[str, Any] = {}
        if extra_data is None:
            extra_data = {}
        parts = path.split(" ", 1)
        method, request_path = (parts[0], parts[1]) if len(parts) == 2 else ("GET", path)
        url = f"{self._params.rest_url}{request_path}"
        is_public = "/public/" in request_path or "/market/" in request_path

        if is_public:
            if params:
                qs = urlencode(sorted(params.items()))
                url = f"{url}?{qs}"
            headers = {"Content-Type": "application/json"}
        else:
            if method.upper() == "GET" and params:
                qs = urlencode(sorted(params.items()))
                sign_path = f"{request_path}?{qs}"
                url = f"{url}?{qs}"
                headers = self._build_auth_headers(method, sign_path)
            elif method.upper() == "POST":
                body_str = json.dumps(body) if body else ""
                headers = self._build_auth_headers(method, request_path, body_str)
            else:
                headers = self._build_auth_headers(method, request_path)

        json_body = json.dumps(body) if (method.upper() == "POST" and body) else None
        res = self.http_request(method, url, headers, json_body, timeout)
        return RequestData(res, extra_data)

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        if params is None:
            params: dict[str, Any] = {}
        if extra_data is None:
            extra_data = {}
        parts = path.split(" ", 1)
        method, request_path = (parts[0], parts[1]) if len(parts) == 2 else ("GET", path)
        url = f"{self._params.rest_url}{request_path}"
        is_public = "/public/" in request_path or "/market/" in request_path

        if is_public:
            if params:
                qs = urlencode(sorted(params.items()))
                url = f"{url}?{qs}"
            headers = {"Content-Type": "application/json"}
        else:
            if method.upper() == "GET" and params:
                qs = urlencode(sorted(params.items()))
                sign_path = f"{request_path}?{qs}"
                url = f"{url}?{qs}"
                headers = self._build_auth_headers(method, sign_path)
            elif method.upper() == "POST":
                body_str = json.dumps(body) if body else ""
                headers = self._build_auth_headers(method, request_path, body_str)
            else:
                headers = self._build_auth_headers(method, request_path)

        json_body = json.dumps(body) if (method.upper() == "POST" and body) else None
        res = await self._http_client.async_request(
            method=method,
            url=url,
            headers=headers,
            data=json_body,
            timeout=timeout,
        )
        return RequestData(res, extra_data)

    def async_callback(self, future):
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.warning(f"async_callback::{e}")

    @staticmethod
    def _extract_data_normalize_function(input_data, extra_data):
        if isinstance(input_data, dict) and "code" in input_data:
            status = input_data.get("code") == "00000"
            data = input_data.get("data")
            if data is None:
                return [], status
            if isinstance(data, list):
                return data, status
            return [data], status
        if isinstance(input_data, list):
            return input_data, True
        return [input_data] if input_data is not None else [], input_data is not None
