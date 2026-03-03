"""
Raydium REST API request base class.

Handles HTTP requests to Raydium's public API.
Raydium is a Solana-based DEX that doesn't require authentication for public data.
"""

import time
from typing import Any

from bt_api_py.containers.exchanges.raydium_exchange_data import (
    RaydiumExchangeData,
    RaydiumExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.functions.log_message import SpdLogManager


class RaydiumRequestData(Feed):
    """Raydium REST API Feed base class.

    Raydium is a DEX with public REST API for pool data.
    No authentication required for public endpoints.
    """

    @classmethod
    def _capabilities(cls):
        """Declare supported capabilities for Raydium."""
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "RAYDIUM___DEX")
        self.asset_type = kwargs.get("asset_type", "DEX")
        self.logger_name = kwargs.get("logger_name", "raydium_feed.log")
        self._params = RaydiumExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

        # Use HttpClient for HTTP requests
        self._http_client = HttpClient(venue=self.exchange_name, timeout=30)

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
        is_sign: bool = False
    ) -> RequestData:
        """Send HTTP request.

        Args:
            path: API path (e.g., "/pools/info/list")
            params: Query parameters
            body: Request body (not used for GET)
            extra_data: Extra data to attach to response
            timeout: Request timeout
            is_sign: Not used for Raydium public API

        Returns:
            RequestData with parsed response
        """
        headers = {
            "Accept": "application/json",
        }

        url = self._build_url(path, params)

        try:
            response = self._http_client.request(
                method="GET",
                url=url,
                headers=headers,
            )

            self.request_logger.info(f"Request: GET {url} - Response code: {response.get('success', 'N/A')}")
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
            body: Request body (not used)
            extra_data: Extra data
            timeout: Request timeout
            is_sign: Not used for Raydium

        Returns:
            RequestData with parsed response
        """
        headers = {
            "Accept": "application/json",
        }

        url = self._build_url(path, params)

        try:
            response = await self._http_client.async_request(
                method="GET",
                url=url,
                headers=headers,
            )

            self.async_logger.info(f"Async Request: GET {url} - Response code: {response.get('success', 'N/A')}")
            return RequestData(response, extra_data)

        except Exception as e:
            self.async_logger.error(f"Async request failed: {e}")
            raise

    def async_callback(self, future):
        """Callback function for async requests, push result to data_queue."""
        try:
            result = future.result()
            if result is not None:
                self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.error(f"Async callback error: {e}")

    # ── Standard Interface: get_server_time ───────────────────────

    def _get_server_time(self, extra_data=None, **kwargs):
        """Prepare server time request. Returns (path, params, extra_data).

        Raydium is a DEX — no dedicated server time endpoint.
        """
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": "",
            "asset_type": self.asset_type,
            "request_type": "get_server_time",
            "server_time": time.time(),
        })
        return "/main/chain-time", {}, extra_data

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time. Returns RequestData."""
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return RequestData({"server_time": time.time()}, extra_data)

    def push_data_to_queue(self, data):
        """Push data to the queue."""
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            raise RuntimeError("Queue not initialized")

    def connect(self) -> None:
        """No-op for HTTP-based API."""
        pass

    def disconnect(self) -> None:
        """No-op for HTTP-based API."""
        pass

    def is_connected(self) -> bool:
        """Always return True for HTTP-based API."""
        return True
