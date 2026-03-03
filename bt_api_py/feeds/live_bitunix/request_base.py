"""
Bitunix REST API request base class.
"""

import time
import hashlib
import uuid
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.bitunix_exchange_data import BitunixExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class BitunixRequestData(Feed):
    """Bitunix REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITUNIX___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BitunixExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/bitunix_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, nonce, timestamp, query_params="", body=""):
        """Generate dual SHA256 signature for Bitunix API.

        Bitunix uses dual SHA256 signature (not HMAC):
        1. First round: SHA256(nonce + timestamp + apiKey + queryParams + body)
        2. Second round: SHA256(digest + secretKey)
        """
        api_key = self._params.api_key
        secret = self._params.api_secret
        if not secret:
            return ""

        # First SHA256
        first_input = nonce + timestamp + api_key + query_params + body
        digest = hashlib.sha256(first_input.encode('utf-8')).hexdigest()

        # Second SHA256
        second_input = digest + secret
        return hashlib.sha256(second_input.encode('utf-8')).hexdigest()

    def _get_headers(self, method, request_path, params=None, body=""):
        """Generate request headers."""
        nonce = str(uuid.uuid4()).replace("-", "")[:32]
        timestamp = str(int(time.time() * 1000))

        # Build query string for GET requests
        query_params = ""
        if params and method == "GET":
            sorted_params = sorted(params.items())
            query_params = "&".join(f"{k}={v}" for k, v in sorted_params)

        headers = {
            "Content-Type": "application/json",
            "api-key": self._params.api_key if self._params.api_key else "",
            "nonce": nonce,
            "timestamp": timestamp,
            "sign": self._generate_signature(nonce, timestamp, query_params, body),
        }
        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for Bitunix API."""
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
