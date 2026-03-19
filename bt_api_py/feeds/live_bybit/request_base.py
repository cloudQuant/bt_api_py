"""
Bybit REST API request base class.
Handles HMAC SHA256 authentication and all REST API methods.
Follows the standard three-layer pattern: _get_xxx() → get_xxx() → async_get_xxx()
"""

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.bybit_exchange_data import BybitExchangeData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.error import BybitErrorTranslator
from bt_api_py.exceptions import QueueNotInitializedError
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger


class BybitRequestData(Feed):
    """Bybit REST API base request class with HMAC SHA256 authentication."""

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
        self.recv_window = kwargs.get("recv_window", "5000")
        self.exchange_name = kwargs.get("exchange_name", "BYBIT___SPOT")
        self.asset_type = kwargs.get("asset_type", "spot")
        self.logger_name = kwargs.get("logger_name", "bybit_feed.log")
        self._params = BybitExchangeData()
        self.request_logger = get_logger("request")
        self.async_logger = get_logger("async_request")
        self._error_translator = BybitErrorTranslator()

    def translate_error(self, raw_response):
        """Translate Bybit API error response to UnifiedError."""
        if isinstance(raw_response, dict):
            ret_code = raw_response.get("retCode", 0)
            if ret_code != 0:
                return self._error_translator.translate(raw_response, self.exchange_name)
        return None

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            raise QueueNotInitializedError("data_queue not initialized")

    def _generate_signature(self, payload_string):
        """Generate HMAC SHA256 signature for Bybit V5 API.

        Args:
            payload_string: The string to sign (timestamp + apiKey + recvWindow + queryString/body)

        Returns:
            str: Hex digest of HMAC SHA256 signature
        """
        return hmac.new(
            (self.private_key or "").encode("utf-8"),
            (payload_string or "").encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request function.

        Args:
            path: Request path (e.g., "GET /v5/market/tickers?category=spot")
            params: URL query parameters
            body: Request body (for POST requests)
            extra_data: Extra data for processing
            timeout: Request timeout in seconds

        Returns:
            RequestData: Response data
        """
        if params is None:
            params: dict[str, Any] = {}
        if extra_data is None:
            extra_data = {}

        # Split method and path
        parts = path.split(" ", 1)
        if len(parts) == 2:
            method, request_path = parts
        else:
            method = "GET"
            request_path = path

        # Separate path and existing query params
        if "?" in request_path:
            base_path, existing_query = request_path.split("?", 1)
            # Parse existing params from path
            for pair in existing_query.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    if k not in params:
                        params[k] = v
            request_path = base_path

        # Build URL
        url = f"{self._params.rest_url}{request_path}"

        # Determine if request needs authentication
        is_public = "/market" in request_path

        # Build query string and headers
        if is_public:
            # Public endpoints: no auth
            if params:
                query_string = urlencode(sorted(params.items()))
                url = f"{url}?{query_string}"
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "bt_api_py/1.0",
            }
        else:
            # Private endpoints: HMAC SHA256 auth via headers
            timestamp = str(int(time.time() * 1000))

            if method == "GET":
                query_string = urlencode(sorted(params.items())) if params else ""
                sign_payload = f"{timestamp}{self.public_key}{self.recv_window}{query_string}"
                if query_string:
                    url = f"{url}?{query_string}"
            else:
                # POST: sign the JSON body
                body_string = json.dumps(body) if body else ""
                sign_payload = f"{timestamp}{self.public_key}{self.recv_window}{body_string}"

            signature = self._generate_signature(sign_payload)

            headers = {
                "X-BAPI-API-KEY": self.public_key,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-SIGN": signature,
                "X-BAPI-RECV-WINDOW": self.recv_window,
                "Content-Type": "application/json",
                "User-Agent": "bt_api_py/1.0",
            }

        # Make request
        if method == "GET":
            res = self.http_request(method, url, headers, None, timeout)
        elif method == "POST":
            json_body = json.dumps(body) if body else None
            res = self.http_request(method, url, headers, json_body, timeout)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        return RequestData(res, extra_data)

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async HTTP request function.

        Args:
            path: Request path
            params: URL query parameters
            body: Request body
            extra_data: Extra data for processing
            timeout: Request timeout in seconds

        Returns:
            RequestData: Response data
        """
        if params is None:
            params: dict[str, Any] = {}
        if extra_data is None:
            extra_data = {}

        # Split method and path
        parts = path.split(" ", 1)
        if len(parts) == 2:
            method, request_path = parts
        else:
            method = "GET"
            request_path = path

        # Separate path and existing query params
        if "?" in request_path:
            base_path, existing_query = request_path.split("?", 1)
            for pair in existing_query.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    if k not in params:
                        params[k] = v
            request_path = base_path

        # Build URL
        url = f"{self._params.rest_url}{request_path}"

        # Determine if request needs authentication
        is_public = "/market" in request_path

        if is_public:
            if params:
                query_string = urlencode(sorted(params.items()))
                url = f"{url}?{query_string}"
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "bt_api_py/1.0",
            }
        else:
            timestamp = str(int(time.time() * 1000))
            if method == "GET":
                query_string = urlencode(sorted(params.items())) if params else ""
                sign_payload = f"{timestamp}{self.public_key}{self.recv_window}{query_string}"
                if query_string:
                    url = f"{url}?{query_string}"
            else:
                body_string = json.dumps(body) if body else ""
                sign_payload = f"{timestamp}{self.public_key}{self.recv_window}{body_string}"

            signature = self._generate_signature(sign_payload)
            headers = {
                "X-BAPI-API-KEY": self.public_key,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-SIGN": signature,
                "X-BAPI-RECV-WINDOW": self.recv_window,
                "Content-Type": "application/json",
                "User-Agent": "bt_api_py/1.0",
            }

        # Make async request
        json_body = json.dumps(body) if (method == "POST" and body) else None
        res = await self.async_http_request(method, url, headers, json_body, timeout)
        return RequestData(res, extra_data)

    def async_callback(self, future):
        """Callback function for async requests.

        Args:
            future: asyncio future object
        """
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.warning(f"async_callback::{e}")

    @staticmethod
    def _generic_normalize_function(input_data, extra_data):
        """Generic normalize function for Bybit API responses.

        Bybit V5 response format:
        {
            "retCode": 0,
            "retMsg": "OK",
            "result": {...},
            "time": timestamp
        }
        """
        if input_data is None:
            return [], False
        status = input_data.get("retCode") == 0
        data = input_data.get("result", {})
        return [data], status
