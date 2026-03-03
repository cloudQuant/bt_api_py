"""
SushiSwap REST API request base class.

Handles REST API queries to SushiSwap's API. SushiSwap uses REST endpoints
instead of GraphQL for most operations.
"""

import os
import time
from typing import Any

from bt_api_py.containers.exchanges.sushiswap_exchange_data import (
    SushiSwapExchangeData,
    SushiSwapChain,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class SushiSwapRequestData(Feed):
    """SushiSwap REST API Feed base class.

    Handles REST API queries to SushiSwap's API. Since SushiSwap is a DEX,
    traditional signing is not required for public queries.
    """

    @classmethod
    def _capabilities(cls):
        """Declare supported capabilities for SushiSwap."""
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_KLINE,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "SUSHISWAP___DEX")
        chain_value = kwargs.get("chain", SushiSwapChain.ETHEREUM)
        # Convert string to enum if needed
        if isinstance(chain_value, str):
            try:
                self.chain = SushiSwapChain(chain_value)
            except ValueError:
                self.chain = SushiSwapChain.ETHEREUM
        else:
            self.chain = chain_value
        self.logger_name = kwargs.get("logger_name", "sushiswap_feed.log")
        self._params = SushiSwapExchangeData(self.chain)
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

        # Use HttpClient for REST requests
        self._http_client = HttpClient(venue=self.exchange_name, timeout=30)

    def _execute_rest_query(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        extra_data=None,
    ) -> RequestData:
        """Execute a REST API query.

        Args:
            method: HTTP method (GET, POST)
            endpoint: API endpoint path
            params: Query parameters
            extra_data: Extra data to attach to response

        Returns:
            RequestData with parsed response
        """
        url = f"{self._params.get_rest_url()}{endpoint}"

        headers = {
            "Content-Type": "application/json",
        }

        # Add API key if available (optional)
        api_key = self._params.get_api_key()
        if api_key:
            headers["x-api-key"] = api_key

        try:
            response = self._http_client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
            )

            return RequestData(response, extra_data)

        except Exception as e:
            self.request_logger.error(f"REST query failed: {e}")
            raise

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request function (legacy interface)."""
        method = path.split()[0] if " " in path else "GET"
        endpoint = "/" + path.split()[1] if " " in path else path

        # For SushiSwap, construct proper URL with chain ID
        if "{address}" in endpoint and params and "address" in params:
            endpoint = endpoint.replace("{address}", params["address"])

        return self._execute_rest_query(method, endpoint, params, extra_data)

    async def async_request(
        self, path, params=None, body=None, extra_data=None, timeout=5
    ):
        """Async HTTP request function."""
        method = path.split()[0] if " " in path else "GET"
        endpoint = "/" + path.split()[1] if " " in path else path

        # For SushiSwap, construct proper URL with chain ID
        if "{address}" in endpoint and params and "address" in params:
            endpoint = endpoint.replace("{address}", params["address"])

        url = f"{self._params.get_rest_url()}{endpoint}"

        headers = {
            "Content-Type": "application/json",
        }

        api_key = self._params.get_api_key()
        if api_key:
            headers["x-api-key"] = api_key

        try:
            response = await self._http_client.async_request(
                method=method,
                url=url,
                headers=headers,
                params=params,
            )

            return RequestData(response, extra_data)

        except Exception as e:
            self.request_logger.error(f"Async REST query failed: {e}")
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

        SushiSwap is a DEX — no dedicated server time endpoint.
        """
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": "",
            "asset_type": "DEX",
            "request_type": "get_server_time",
            "server_time": time.time(),
        })
        return "/time", {}, extra_data

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
        """No-op for HTTP-based REST API."""
        pass

    def disconnect(self) -> None:
        """No-op for HTTP-based REST API."""
        pass

    def is_connected(self) -> bool:
        """Always return True for HTTP-based REST API."""
        return True
