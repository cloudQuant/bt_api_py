"""
Phemex REST API request base class.

Handles HTTP requests to Phemex's API with HMAC SHA256 signature authentication.
Phemex uses scaled precision (Ep/Ev suffixes) for prices and amounts.
"""

import hmac
import time
import urllib.parse
from typing import Any, Optional

from bt_api_py.containers.exchanges.phemex_exchange_data import (
    PhemexExchangeData,
    PhemexExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.functions.log_message import SpdLogManager


class PhemexRequestData(Feed):
    """Phemex REST API Feed base class.

    Handles HTTP requests to Phemex's API with HMAC SHA256 signature.
    """

    @classmethod
    def _capabilities(cls):
        """Declare supported capabilities for Phemex."""
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "PHEMEX___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "phemex_feed.log")
        self._params = PhemexExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

        # Use HttpClient for HTTP requests
        self._http_client = HttpClient(venue=self.exchange_name, timeout=30)

        # API credentials (if provided)
        self.api_key = kwargs.get("api_key", "")
        self.api_secret = kwargs.get("api_secret", "")

    def _generate_signature(
        self,
        method: str,
        path: str,
        query_params: dict,
        body: str = ""
    ) -> tuple[str, str, str]:
        """Generate Phemex HMAC SHA256 signature.

        Args:
            method: HTTP method (GET, POST, DELETE)
            path: Request path
            query_params: Query parameters
            body: Request body (for POST requests)

        Returns:
            tuple: (signature, expiry, request_str)
        """
        expiry = str(int(time.time()) + 60)

        # Build query string
        if query_params:
            query_string = "&".join(
                f"{k}={urllib.parse.quote_plus(str(v))}"
                for k, v in sorted(query_params.items())
            )
        else:
            query_string = ""

        # Signature: path + queryString + expiry + body
        sign_str = path + query_string + expiry + body

        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            sign_str.encode('utf-8'),
            'sha256'
        ).hexdigest()

        return signature, expiry, sign_str

    def _build_headers(
        self,
        method: str,
        path: str,
        query_params: dict,
        body: str = "",
        is_sign: bool = False
    ) -> dict:
        """Build request headers.

        Args:
            method: HTTP method
            path: Request path
            query_params: Query parameters
            body: Request body
            is_sign: Whether to sign the request

        Returns:
            dict: Request headers
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        if is_sign and self.api_key and self.api_secret:
            signature, expiry, _ = self._generate_signature(method, path, query_params, body)
            headers["x-phemex-access-token"] = self.api_key
            headers["x-phemex-request-expiry"] = expiry
            headers["x-phemex-request-signature"] = signature

        return headers

    def _build_url(self, path: str, params: dict = None) -> str:
        """Build full URL.

        Args:
            path: API path
            params: Query parameters

        Returns:
            str: Full URL
        """
        url = self._params.rest_url + path

        if params:
            query_string = "&".join(
                f"{k}={urllib.parse.quote_plus(str(v))}"
                for k, v in sorted(params.items())
            )
            url = f"{url}?{query_string}"

        return url

    def request(
        self,
        path: str,
        params: dict = None,
        body: dict = None,
        extra_data: dict = None,
        timeout: int = 10,
        is_sign: bool = False
    ) -> RequestData:
        """Send HTTP request.

        Args:
            path: API path (e.g., "/md/v2/ticker/24hr")
            params: Query parameters
            body: Request body (for POST)
            extra_data: Extra data to attach to response
            timeout: Request timeout
            is_sign: Whether to sign the request

        Returns:
            RequestData with parsed response
        """
        method = "GET"
        body_str = ""

        if body:
            method = "POST"
            import json
            body_str = json.dumps(body)

        headers = self._build_headers(method, path, params or {}, body_str, is_sign)
        url = self._build_url(path, params)

        try:
            response = self._http_client.request(
                method=method,
                url=url,
                headers=headers,
                json_data=body if method == "POST" else None,
            )

            self.request_logger.info(f"Request: {method} {url} - Response: {response.get('code', 'N/A')}")
            return RequestData(response, extra_data)

        except Exception as e:
            self.request_logger.error(f"Request failed: {e}")
            raise

    async def async_request(
        self,
        path: str,
        params: dict = None,
        body: dict = None,
        extra_data: dict = None,
        timeout: int = 5,
        is_sign: bool = False
    ) -> RequestData:
        """Send async HTTP request.

        Args:
            path: API path
            params: Query parameters
            body: Request body (for POST)
            extra_data: Extra data
            timeout: Request timeout
            is_sign: Whether to sign the request

        Returns:
            RequestData with parsed response
        """
        method = "GET"
        body_str = ""

        if body:
            method = "POST"
            import json
            body_str = json.dumps(body)

        headers = self._build_headers(method, path, params or {}, body_str, is_sign)
        url = self._build_url(path, params)

        try:
            response = await self._http_client.async_request(
                method=method,
                url=url,
                headers=headers,
                json_data=body if method == "POST" else None,
            )

            self.async_logger.info(f"Async Request: {method} {url} - Response: {response.get('code', 'N/A')}")
            return RequestData(response, extra_data)

        except Exception as e:
            self.async_logger.error(f"Async request failed: {e}")
            raise

    def push_data_to_queue(self, data):
        """Push data to the queue."""
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            raise RuntimeError("Queue not initialized")

    def async_callback(self, future):
        """Callback function for async requests.

        Args:
            future: asyncio future object
        """
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.warn(f"async_callback::{e}")

    def connect(self) -> None:
        """No-op for HTTP-based API."""
        pass

    def disconnect(self) -> None:
        """No-op for HTTP-based API."""
        pass

    def is_connected(self) -> bool:
        """Always return True for HTTP-based API."""
        return True
