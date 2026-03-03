"""
BitMart REST API request base class.
"""

import time
import hmac
import hashlib
import json

from bt_api_py.containers.exchanges.bitmart_exchange_data import BitmartExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class BitmartRequestData(Feed):
    """BitMart REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITMART___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BitmartExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/bitmart_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, timestamp, memo, body_str):
        """Generate HMAC SHA256 signature for BitMart API.

        BitMart signature format: timestamp + "#" + memo + "#" + body
        """
        secret = getattr(self._params, 'api_secret', None)
        if secret:
            message = f"{timestamp}#{memo}#{body_str}"
            signature = hmac.new(
                secret.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            return signature
        return ""

    def _get_headers(self, method, request_path, params=None, body=""):
        """Generate request headers.

        BitMart uses X-BM-KEY, X-BM-TIMESTAMP, and X-BM-SIGN headers.
        """
        timestamp = str(int(time.time() * 1000))
        memo = getattr(self._params, 'api_memo', None) or ""
        body_str = body if method == "POST" else ""

        headers = {
            "Content-Type": "application/json",
            "X-BM-TIMESTAMP": timestamp,
        }

        # Add API key and signature for private endpoints
        api_key = getattr(self._params, 'api_key', None)
        api_secret = getattr(self._params, 'api_secret', None)

        if api_key:
            headers["X-BM-KEY"] = api_key

        if api_secret:
            signature = self._generate_signature(timestamp, memo, body_str)
            headers["X-BM-SIGN"] = signature

        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for BitMart API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path

        # Prepare body for POST requests
        body_str = ""
        if method == "POST":
            if body:
                body_str = json.dumps(body) if isinstance(body, dict) else body

        headers = self._get_headers(method, request_path, params, body_str)

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
