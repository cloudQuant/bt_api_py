"""
CoinSpot REST API request base class.
"""

import time
import hmac
import hashlib
import json

from bt_api_py.containers.exchanges.coinspot_exchange_data import CoinSpotExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class CoinSpotRequestData(Feed):
    """CoinSpot REST API Feed base class."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "COINSPOT___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = CoinSpotExchangeDataSpot()
        self.api_key = kwargs.get("api_key", self._params.api_key)
        self.api_secret = kwargs.get("api_secret", self._params.api_secret)
        self.request_logger = SpdLogManager(
            "./logs/coinspot_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, body):
        """Generate HMAC SHA512 signature for CoinSpot API."""
        secret = self.api_secret
        if secret:
            signature = hmac.new(
                secret.encode("utf-8"),
                body.encode("utf-8"),
                hashlib.sha512
            ).hexdigest()
            return signature
        return ""

    def _get_headers(self, body=""):
        """Generate request headers."""
        headers = {
            "Content-Type": "application/json",
            "key": self.api_key,
            "sign": self._generate_signature(body),
        }
        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for CoinSpot API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path

        # For GET requests, no auth needed for public endpoints
        if method == "GET":
            headers = {"Content-Type": "application/json"}
        else:
            # POST requests require auth
            body_str = json.dumps(body) if body else "{}"
            headers = self._get_headers(body_str)

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
            response = http_client.request(
                method=method,
                url=self._params.rest_url + request_path,
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
