"""
Bitbns REST API request base class.
"""

import hashlib
import hmac
import time
from typing import Any

from bt_api_py.containers.exchanges.bitbns_exchange_data import BitbnsExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.logging_factory import get_logger


class BitbnsRequestData(Feed):
    """Bitbns REST API Feed base class."""

    @classmethod
    def _capabilities(cls) -> set[Capability]:
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

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "BITBNS___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BitbnsExchangeDataSpot()
        self._params.api_key = kwargs.get("public_key") or kwargs.get("api_key")
        self._params.api_secret = (
            kwargs.get("private_key") or kwargs.get("secret_key") or kwargs.get("api_secret")
        )
        self.request_logger = get_logger("bitbns_feed")
        self.async_logger = get_logger("bitbns_feed")
        self._http_client = HttpClient(venue=self.exchange_name, timeout=10)

    def _generate_signature(self, timestamp, body=""):
        """Generate HMAC SHA512 signature for Bitbns API."""
        secret = self._params.api_secret
        if secret:
            # Bitbns uses HMAC SHA512
            message = str(timestamp) + body
            signature = hmac.new(
                secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha512
            ).hexdigest()
            return signature
        return ""

    def _get_headers(self, method, request_path, params=None, body=""):
        """Generate request headers with authentication."""
        headers = {
            "Content-Type": "application/json",
        }

        # Add auth headers if API key is configured
        if self._params.api_key:
            timestamp = int(time.time() * 1000)
            body_str = body if body else ""

            signature = self._generate_signature(timestamp, body_str)

            headers.update(
                {
                    "X-API-KEY": self._params.api_key,
                    "X-API-SIGNATURE": signature,
                    "X-API-TIMESTAMP": str(timestamp),
                }
            )

        return headers

    def _get_base_url(self, path):
        """Get base URL based on endpoint type."""
        if path.startswith("GET /") and not getattr(self._params, "api_key", None):
            return getattr(self._params, "rest_public_url", self._params.rest_url)
        return self._params.rest_url

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for Bitbns API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)
        base_url = self._get_base_url(path)

        try:
            response = self._http_client.request(
                method=method,
                url=base_url + request_path,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=params,
            )
            return self._process_response(response, extra_data)
        except Exception as e:
            self.request_logger.error(f"Request failed: {e}")
            raise

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async HTTP request for Bitbns API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)
        base_url = self._get_base_url(path)

        try:
            response = await self._http_client.async_request(
                method=method,
                url=base_url + request_path,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=params,
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
        return "GET /api/v1/serverTime", {}, extra_data

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
        super().disconnect()

    def is_connected(self):
        return True
