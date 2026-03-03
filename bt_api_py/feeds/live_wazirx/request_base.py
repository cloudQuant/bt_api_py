"""
WazirX REST API request base class.
"""

import time
import hmac
import hashlib
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.wazirx_exchange_data import WazirxExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class WazirxRequestData(Feed):
    """WazirX REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "WAZIRX___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = WazirxExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/wazirx_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, params, body=""):
        """Generate HMAC SHA256 signature for WazirX API.

        WazirX signature: HMAC SHA256 of query string + request body
        """
        secret = self._params.api_secret
        if secret:
            # Build totalParams: query string + request body
            query_string = urlencode(params) if params else ""
            total_params = query_string + body
            signature = hmac.new(
                secret.encode("utf-8"),
                total_params.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            return signature
        return ""

    def _get_headers(self, method, request_path, params=None, body=""):
        """Generate request headers."""
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        # Add authentication headers if API key is available
        if self._params.api_key:
            headers["X-API-KEY"] = self._params.api_key

        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for WazirX API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        # Add timestamp and signature for authenticated requests
        if self._params.api_key:
            params = params or {}
            if "timestamp" not in params:
                params["timestamp"] = int(time.time() * 1000)
            if "recvWindow" not in params:
                params["recvWindow"] = 5000

            # Add signature
            signature = self._generate_signature(params, body or "")
            params["signature"] = signature

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
            response = http_client.request(
                method=method,
                url=self._params.rest_url + request_path,
                headers=headers,
                data=body if method in ["POST", "DELETE"] else None,
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
