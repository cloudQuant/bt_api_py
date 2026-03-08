"""
bitFlyer REST API request base class.
"""

import hashlib
import hmac
import time

from bt_api_py.containers.exchanges.bitflyer_exchange_data import BitflyerExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.logging_factory import get_logger


class BitflyerRequestData(Feed):
    """bitFlyer REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITFLYER___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BitflyerExchangeDataSpot()
        self.request_logger = get_logger("bitflyer_feed")
        self.async_logger = get_logger("bitflyer_feed")
        self._http_client = HttpClient(venue=self.exchange_name, timeout=10)

    def _generate_signature(self, timestamp, method, request_path, body=""):
        """Generate HMAC SHA256 signature for bitFlyer API.

        bitFlyer signature: timestamp + method + path + body
        """
        secret = self._params.api_secret
        if secret:
            text = str(timestamp) + method + request_path + body
            signature = hmac.new(
                secret.encode("utf-8"), text.encode("utf-8"), hashlib.sha256
            ).hexdigest()
            return signature
        return ""

    def _get_headers(self, method, request_path, params=None, body=""):
        """Generate request headers with authentication."""
        headers = {
            "Content-Type": "application/json",
        }

        # Add auth headers if API key is configured
        if self._params.api_key and hasattr(self._params, "api_secret") and self._params.api_secret:
            timestamp = time.time()
            body_str = body if body else ""

            signature = self._generate_signature(timestamp, method, request_path, body_str)

            headers.update(
                {
                    "ACCESS-KEY": self._params.api_key,
                    "ACCESS-TIMESTAMP": str(timestamp),
                    "ACCESS-SIGN": signature,
                }
            )

        return headers

    def _build_signature_path(self, method, request_path, params):
        """Build the full path used for signature."""
        if method == "GET" and params:
            query_string = "&".join(f"{k}={v}" for k, v in params.items())
            return f"{request_path}?{query_string}"
        return request_path

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for bitFlyer API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path
        full_request_path = self._build_signature_path(method, request_path, params)
        headers = self._get_headers(method, full_request_path, params, body)

        try:
            response = self._http_client.request(
                method=method,
                url=self._params.rest_url + request_path,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=params if method == "GET" else None,
            )
            return self._process_response(response, extra_data)
        except Exception as e:
            self.request_logger.error(f"Request failed: {e}")
            raise

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async HTTP request for bitFlyer API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path
        full_request_path = self._build_signature_path(method, request_path, params)
        headers = self._get_headers(method, full_request_path, params, body)

        try:
            response = await self._http_client.async_request(
                method=method,
                url=self._params.rest_url + request_path,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=params if method == "GET" else None,
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
        return RequestData(response, extra_data)

    def _get_server_time(self, extra_data=None, **kwargs):
        """Prepare server time request. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": "get_server_time",
            }
        )
        return "GET /v1/gethealth", {}, extra_data

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time. Returns RequestData."""
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

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
