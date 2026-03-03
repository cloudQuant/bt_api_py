"""
BingX REST API request base class.
"""

import time
import hmac
import hashlib
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.bingx_exchange_data import BingXExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class BingXRequestData(Feed):
    """BingX REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "BINGX___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BingXExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/bingx_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, params, secret_key):
        """Generate HMAC SHA256 signature for BingX API."""
        # Sort params and create query string
        query_string = urlencode(sorted(params.items()))
        signature = hmac.new(
            secret_key.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _get_headers(self, method, request_path, params=None, body=""):
        """Generate request headers."""
        headers = {
            "Content-Type": "application/json",
        }

        # Add API key for private endpoints
        api_key = getattr(self._params, 'api_key', None)
        if api_key and request_path.startswith(("/openApi/spot/v1/account", "/openApi/spot/v1/trade")):
            headers["X-BX-APIKEY"] = api_key

        return headers

    def _add_signature(self, params):
        """Add signature to params for private endpoints."""
        api_secret = getattr(self._params, 'api_secret', None)
        if api_secret:
            # Timestamp is already added in request() method
            params["signature"] = self._generate_signature(params, api_secret)
        return params

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for BingX API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        # Add signature for private endpoints and timestamp for all requests
        if params is None:
            params = {}
        # BingX requires timestamp for all requests
        params["timestamp"] = int(time.time() * 1000)
        if request_path.startswith(("/openApi/spot/v1/account", "/openApi/spot/v1/trade")):
            params = self._add_signature(params.copy() if params else {})

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
            response = http_client.request(
                method=method,
                url=self._params.rest_url + request_path,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=params if params else None,
            )
            return self._process_response(response, extra_data)
        except Exception as e:
            self.request_logger.error(f"Request failed: {e}")
            raise

    def _process_response(self, response, extra_data=None):
        """Process API response."""
        from bt_api_py.containers.requestdatas.request_data import RequestData
        if extra_data is None:
            extra_data = {}
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
