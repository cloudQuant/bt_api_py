"""
Bitvavo REST API request base class.
"""

import time
import hmac
import hashlib

from bt_api_py.containers.exchanges.bitvavo_exchange_data import BitvavoExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class BitvavoRequestData(Feed):
    """Bitvavo REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITVAVO___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BitvavoExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/bitvavo_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, timestamp, method, url_path, body=""):
        """Generate HMAC SHA256 signature for Bitvavo API.

        Signature string: timestamp + method + url + body
        """
        secret = self._params.api_secret
        if not secret:
            return ""

        sign_str = timestamp + method.upper() + url_path + body
        signature = hmac.new(
            secret.encode('utf-8'),
            sign_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _get_headers(self, method, request_path, params=None, body=""):
        """Generate request headers."""
        timestamp = str(int(time.time() * 1000))

        # Build URL path with query params for GET requests
        url_path = "/v2" + request_path
        if params and method == "GET":
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url_path = url_path + "?" + query

        body_str = body if body else ""

        headers = {
            "Content-Type": "application/json",
            "Bitvavo-Access-Key": self._params.api_key if self._params.api_key else "",
            "Bitvavo-Access-Signature": self._generate_signature(timestamp, method, url_path, body_str),
            "Bitvavo-Access-Timestamp": timestamp,
            "Bitvavo-Access-Window": "10000",
        }
        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for Bitvavo API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
            response = http_client.request(
                method=method,
                url=self._params.rest_url + request_path,
                headers=headers,
                json_data=body if method in ["POST", "PUT"] else None,
                params=None,  # Params already in headers for signature
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
