"""
SatoshiTango REST API request base class.
"""

import time
from bt_api_py.containers.exchanges.satoshitango_exchange_data import SatoshiTangoExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class SatoshiTangoRequestData(Feed):
    """SatoshiTango REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "SATOSHITANGO___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = SatoshiTangoExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/satoshitango_feed.log", "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/satoshitango_feed.log", "async_request", 0, 0, False
        ).create_logger()

    def _get_headers(self, method=None, request_path=None, params=None, body=""):
        """Generate request headers."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "bt_api_py/1.0",
        }
        # Add API key if available
        api_key = self._params.api_key if hasattr(self._params, 'api_key') else None
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for SatoshiTango API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path

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

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """Async HTTP request for SatoshiTango API - returns coroutine."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        try:
            response = await self.async_http_request(
                method=method,
                url=self._params.rest_url + request_path,
                headers=headers,
                body=body,
                timeout=timeout,
            )
            self.async_logger.info(
                f"Async request: {method} {request_path} - Success"
            )
            return self._process_response(response, extra_data)
        except Exception as e:
            self.async_logger.error(f"Async request failed: {e}")
            raise

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
