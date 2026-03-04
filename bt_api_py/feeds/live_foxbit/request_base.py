"""
Foxbit REST API request base class.
"""

import time
import hmac
import hashlib
import json

from bt_api_py.containers.exchanges.foxbit_exchange_data import FoxbitExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.logging_factory import get_logger


class FoxbitRequestData(Feed):
    """Foxbit REST API Feed base class."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "FOXBIT___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = FoxbitExchangeDataSpot()
        # Set API credentials if provided
        self._params.api_key = kwargs.get("api_key")
        self._params.api_secret = kwargs.get("api_secret")
        self.request_logger = get_logger("foxbit_feed")
        self.async_logger = get_logger("foxbit_feed")
        self._http_client = HttpClient(venue=self.exchange_name, timeout=10)

    def _generate_signature(self, timestamp, method, request_path, body=""):
        """Generate HMAC SHA256 signature for Foxbit API.

        Foxbit signature: timestamp + method + path + body
        """
        secret = self._params.api_secret
        if secret:
            pre_hash = f"{timestamp}{method}{request_path}{body}"
            signature = hmac.new(
                secret.encode("utf-8"),
                pre_hash.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            return signature
        return ""

    def _get_headers(self, method, request_path, params=None, body=""):
        """Generate request headers.

        For public API: No auth required
        For private API: X-FB-ACCESS-KEY, X-FB-ACCESS-TIMESTAMP, X-FB-ACCESS-SIGNATURE
        """
        timestamp = str(int(time.time() * 1000))
        headers = {
            "Content-Type": "application/json",
        }

        # Add auth headers for private endpoints
        if self._params.api_key:
            body_str = body if body else ""
            signature = self._generate_signature(timestamp, method, request_path, body_str)
            headers["X-FB-ACCESS-KEY"] = self._params.api_key
            headers["X-FB-ACCESS-TIMESTAMP"] = timestamp
            headers["X-FB-ACCESS-SIGNATURE"] = signature

        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for Foxbit API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path

        # Build query string for GET requests
        query_string = ""
        if params and method == "GET":
            from urllib.parse import urlencode
            query_string = "?" + urlencode(params)
            request_path += query_string

        # Prepare body
        body_str = json.dumps(body) if body else ""

        headers = self._get_headers(method, request_path, params, body_str)

        try:
            response = self._http_client.request(
                method=method,
                url=self._params.rest_url + request_path,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=None if method == "POST" else params,
            )
            return self._process_response(response, extra_data)
        except Exception as e:
            self.request_logger.error(f"Request failed: {e}")
            raise

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async HTTP request for Foxbit API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path

        query_string = ""
        if params and method == "GET":
            from urllib.parse import urlencode
            query_string = "?" + urlencode(params)
            request_path += query_string

        body_str = json.dumps(body) if body else ""
        headers = self._get_headers(method, request_path, params, body_str)

        try:
            response = await self._http_client.async_request(
                method=method,
                url=self._params.rest_url + request_path,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=None if method == "POST" else params,
            )
            return self._process_response(response, extra_data)
        except Exception as e:
            self.async_logger.error(f"Async request failed: {e}")
            raise

    def async_callback(self, future):
        """Callback for async requests, push result to data_queue."""
        try:
            result = future.result()
            if result is not None:
                self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.error(f"Async callback error: {e}")

    def _process_response(self, response, extra_data=None):
        """Process API response."""
        if extra_data is None:
            extra_data = {}
        return RequestData(response, extra_data, status=True)

    def _get_server_time(self, extra_data=None, **kwargs):
        """Prepare server time request. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": "",
            "asset_type": self.asset_type,
            "request_type": "get_server_time",
            "normalize_function": self._get_server_time_normalize_function,
        })
        return "GET /rest/v3/system/time", {}, extra_data

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time."""
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        """Normalize server time response."""
        if not input_data:
            return None, False
        data = input_data.get("data", input_data)
        if isinstance(data, dict):
            # Return timestamp if available
            timestamp = data.get("timestamp", data.get("iso"))
            return timestamp, True
        return None, False

    def push_data_to_queue(self, data):
        """Push data to the queue."""
        if self.data_queue is not None:
            self.data_queue.put(data)

    def connect(self):
        pass

    def disconnect(self):
        pass

    def is_connected(self):
        return True
