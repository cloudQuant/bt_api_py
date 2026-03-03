"""
BigONE REST API request base class.
"""

import time
import jwt
from bt_api_py.containers.exchanges.bigone_exchange_data import BigONEExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class BigONERequestData(Feed):
    """BigONE REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "BIGONE___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BigONEExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/bigone_feed.log", "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/bigone_feed.log", "async_request", 0, 0, False
        ).create_logger()

    def _generate_jwt_token(self):
        """Generate JWT token for BigONE API authentication."""
        api_secret = getattr(self._params, 'api_secret', None)
        api_key = getattr(self._params, 'api_key', None)
        if api_secret:
            payload = {
                "type": "OpenAPIV2",
                "sub": api_key,
                "nonce": str(int(time.time() * 1e9)),  # nanosecond timestamp
            }
            token = jwt.encode(payload, api_secret, algorithm="HS256")
            return token
        return ""

    def _get_headers(self, method, request_path, params=None, body=""):
        """Generate request headers."""
        headers = {
            "Content-Type": "application/json",
        }

        # Add JWT auth for private endpoints
        api_key = getattr(self._params, 'api_key', None)
        if api_key and request_path.startswith("/viewer"):
            token = self._generate_jwt_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"

        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for BigONE API."""
        method = path.split()[0] if " " in path else "GET"
        # For path templates like /asset_pairs/{symbol}/ticker, we need to replace symbol
        request_path = "/" + path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)

            # Handle path parameters for symbol
            url = self._params.rest_url + request_path
            if params and "symbol" in params:
                # Replace {symbol} in path with actual symbol
                url = url.replace("{symbol}", params.pop("symbol", ""))

            response = http_client.request(
                method=method,
                url=url,
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
        """Async HTTP request for BigONE API - returns coroutine."""
        method = path.split()[0] if " " in path else "GET"
        # For path templates like /asset_pairs/{symbol}/ticker, we need to replace symbol
        request_path = "/" + path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        try:
            # Handle path parameters for symbol
            url = self._params.rest_url + request_path
            if params and "symbol" in params:
                # Replace {symbol} in path with actual symbol
                url = url.replace("{symbol}", params.pop("symbol", ""))

            response = await self.async_http_request(
                method=method,
                url=url,
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

    def async_callback(self, future):
        """Callback function for async requests, push result to data_queue.

        Args:
            future: asyncio future object
        """
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.warn(f"async_callback::{e}")

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
