"""
Bitbns REST API request base class.
"""

import hmac
import hashlib
import time

from bt_api_py.containers.exchanges.bitbns_exchange_data import BitbnsExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class BitbnsRequestData(Feed):
    """Bitbns REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITBNS___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BitbnsExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/bitbns_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, timestamp, body=""):
        """Generate HMAC SHA512 signature for Bitbns API."""
        secret = self._params.api_secret
        if secret:
            # Bitbns uses HMAC SHA512
            message = str(timestamp) + body
            signature = hmac.new(
                secret.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha512
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

            headers.update({
                "X-API-KEY": self._params.api_key,
                "X-API-SIGNATURE": signature,
                "X-API-TIMESTAMP": str(timestamp),
            })

        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for Bitbns API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)

            # Use public API URL for public endpoints
            if path.startswith("GET /") and not self._params.api_key:
                base_url = self._params.rest_public_url
            else:
                base_url = self._params.rest_url

            response = http_client.request(
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
