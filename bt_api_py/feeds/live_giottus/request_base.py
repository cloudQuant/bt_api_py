"""
Giottus REST API request base class.

Giottus is an Indian cryptocurrency exchange.
API documentation: https://api.giottus.com/
"""

import time

from bt_api_py.containers.exchanges.giottus_exchange_data import GiottusExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class GiottusRequestData(Feed):
    """Giottus REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "GIOTTUS___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = GiottusExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/giottus_feed.log", "request", 0, 0, False
        ).create_logger()

    def _get_headers(self, method="GET", request_path="", params=None, body=""):
        """Generate request headers for Giottus API."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "bt_api_py/1.0",
        }

        # Add API key if available
        if hasattr(self._params, "api_key") and self._params.api_key:
            headers["X-API-KEY"] = self._params.api_key

        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for Giottus API."""
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
