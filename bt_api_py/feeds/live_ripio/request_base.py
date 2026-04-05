"""
Ripio REST API request base class.

Handles HTTP requests to Ripio's API with optional HMAC SHA256 signature authentication.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from bt_api_py.containers.exchanges.ripio_exchange_data import (
    RipioExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.logging_factory import get_logger


class RipioRequestData(Feed):
    """Ripio REST API Feed base class.

    Handles HTTP requests to Ripio's API with HMAC SHA256 signature.
    """

    @classmethod
    def _capabilities(cls) -> set[Capability]:
        """Declare supported capabilities for Ripio."""
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
        }

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "RIPIO___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "ripio_feed.log")
        self._params = RipioExchangeDataSpot()
        self.request_logger = get_logger("ripio_feed")
        self.async_logger = get_logger("ripio_feed")

        # Use HttpClient for HTTP requests
        self._http_client = HttpClient(venue=self.exchange_name, timeout=30)

        # API credentials (if provided)
        self.api_key = kwargs.get("public_key") or kwargs.get("api_key", "")
        self.api_secret = (
            kwargs.get("private_key") or kwargs.get("api_secret") or kwargs.get("secret_key") or ""
        )

    def _generate_signature(self, method: str, path: str, timestamp: str, body: str = "") -> str:
        """Generate Ripio HMAC SHA256 signature.

        Args:
            method: HTTP method (GET, POST, DELETE)
            path: Request path
            timestamp: Request timestamp
            body: Request body (for POST requests)

        Returns:
            str: Signature
        """
        # Build signature string: timestamp + method + path + body
        sign_str = timestamp + method + path + body

        signature = hmac.new(
            self.api_secret.encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        return signature

    def _build_headers(self, method: str, path: str, body: str = "", is_sign: bool = False) -> dict:
        """Build request headers.

        Args:
            method: HTTP method
            path: Request path
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
            timestamp = str(int(time.time() * 1000))
            signature = self._generate_signature(method, path, timestamp, body)
            headers["X-API-KEY"] = self.api_key
            headers["X-API-SIGNATURE"] = signature
            headers["X-API-TIMESTAMP"] = timestamp

        return headers

    def _build_url(self, path: str, params: dict = None) -> str:
        """Build full URL.

        Args:
            path: API path (may include HTTP method like "GET /path")
            params: Query parameters

        Returns:
            str: Full URL
        """
        # Strip HTTP method if present (e.g., "GET /api/v1/ticker" -> "/api/v1/ticker")
        if " " in path:
            path = path.split(" ", 1)[1]

        url = self._params.rest_url + path

        if params:
            from urllib.parse import urlencode

            url = f"{url}?{urlencode(params)}"

        return url

    def request(
        self,
        path: str,
        params: dict = None,
        body: dict = None,
        extra_data: dict = None,
        timeout: int = 10,
        is_sign: bool = False,
    ) -> RequestData:
        """Send HTTP request.

        Args:
            path: API path (e.g., "/api/v1/ticker")
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

        headers = self._build_headers(method, path, body_str, is_sign)
        url = self._build_url(path, params)

        try:
            response = self._http_client.request(
                method=method,
                url=url,
                headers=headers,
                json_data=body if method == "POST" else None,
            )

            self.request_logger.info(f"Request: {method} {url}")
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
        is_sign: bool = False,
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

        headers = self._build_headers(method, path, body_str, is_sign)
        url = self._build_url(path, params)

        try:
            response = await self._http_client.async_request(
                method=method,
                url=url,
                headers=headers,
                json_data=body if method == "POST" else None,
            )

            self.async_logger.info(f"Async Request: {method} {url}")
            return RequestData(response, extra_data)

        except Exception as e:
            self.async_logger.error(f"Async request failed: {e}")
            raise

    def async_callback(self, future):
        """Callback for async requests, push result to data_queue."""
        try:
            result = future.result()
            if result is not None:
                self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.error(f"Async callback error: {e}")

    def _get_server_time(self, extra_data=None, **kwargs):
        """Prepare server time request. Returns (path, params, extra_data)."""
        path = self._params.get_rest_path("get_tick").replace(":symbol", "BTC_USDT")
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": "get_server_time",
                "normalize_function": self._get_server_time_normalize_function,
            }
        )
        return path, {}, extra_data

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time."""
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        if not input_data:
            return None, False
        if isinstance(input_data, dict):
            data = input_data.get("data", input_data)
            if isinstance(data, dict):
                ts = data.get("timestamp") or data.get("time")
                return ts, True
        return input_data, True

    def push_data_to_queue(self, data):
        """Push data to the queue."""
        if self.data_queue is not None:
            self.data_queue.put(data)

    def connect(self) -> None:
        """No-op for HTTP-based API."""

    def disconnect(self) -> None:
        """No-op for HTTP-based API."""

    def is_connected(self) -> bool:
        """Always return True for HTTP-based API."""
        return True
