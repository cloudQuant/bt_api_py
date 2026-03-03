"""
bitFlyer REST API request base class.
"""

import time
import hmac
import hashlib
import json

from bt_api_py.containers.exchanges.bitflyer_exchange_data import BitflyerExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class BitflyerRequestData(Feed):
    """bitFlyer REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITFLYER___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BitflyerExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/bitflyer_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, timestamp, method, request_path, body=""):
        """Generate HMAC SHA256 signature for bitFlyer API.

        bitFlyer signature: timestamp + method + path + body
        """
        secret = self._params.api_secret
        if secret:
            text = str(timestamp) + method + request_path + body
            signature = hmac.new(
                secret.encode("utf-8"),
                text.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            return signature
        return ""

    def _get_headers(self, method, request_path, params=None, body=""):
        """Generate request headers with authentication."""
        headers = {
            "Content-Type": "application/json",
        }

        # Add auth headers if API key is configured
        if self._params.api_key and hasattr(self._params, 'api_secret') and self._params.api_secret:
            timestamp = time.time()
            body_str = body if body else ""

            signature = self._generate_signature(timestamp, method, request_path, body_str)

            headers.update({
                "ACCESS-KEY": self._params.api_key,
                "ACCESS-TIMESTAMP": str(timestamp),
                "ACCESS-SIGN": signature,
            })

        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for bitFlyer API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        # For GET requests with params, include them in the signature
        if method == "GET" and params:
            query_string = "&".join(f"{k}={v}" for k, v in params.items())
            full_request_path = f"{request_path}?{query_string}"
        else:
            full_request_path = request_path

        headers = self._get_headers(method, full_request_path, params, body)

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
            response = http_client.request(
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

    def _process_response(self, response, extra_data=None):
        """Process API response."""
        from bt_api_py.containers.requestdatas.request_data import RequestData
        return RequestData(response, extra_data)

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
