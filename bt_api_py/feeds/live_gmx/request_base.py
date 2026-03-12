"""
GMX REST API request base class.

GMX is a decentralized perpetual exchange supporting:
- Arbitrum
- Avalanche
- Botanix

Uses REST API for oracle/pricing data (not GraphQL).
Documentation: https://docs.gmx.io/docs/api/rest/
"""

import time
from typing import Any

from bt_api_py.containers.exchanges.gmx_exchange_data import (
    GmxChain,
    GmxExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.logging_factory import get_logger


class GmxRequestData(Feed):
    """GMX REST API Feed base class.

    GMX uses REST API endpoints for oracle/pricing data.
    No authentication required for public endpoints.
    """

    @classmethod
    def _capabilities(cls) -> set[Capability]:
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
        self.exchange_name = kwargs.get("exchange_name", "GMX___DEX")
        self.asset_type = kwargs.get("asset_type", "SPOT")

        # Get chain parameter
        chain_value = kwargs.get("chain", GmxChain.ARBITRUM)
        if isinstance(chain_value, str):
            try:
                self.chain = GmxChain(chain_value)
            except ValueError:
                self.chain = GmxChain.ARBITRUM
        else:
            self.chain = chain_value

        self._params = GmxExchangeDataSpot(self.chain)
        self.request_logger = get_logger("gmx_feed")
        self.async_logger = get_logger("gmx_feed")

        # Use HttpClient for REST requests
        self._http_client = HttpClient(venue=self.exchange_name, timeout=30)

    def _get_headers(self) -> dict[str, str]:
        """Generate request headers for GMX API."""
        return {
            "Content-Type": "application/json",
            "User-Agent": "bt_api_py/1.0",
        }

    def request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        body: Any = None,
        extra_data=None,
        timeout: int = 10,
    ):
        """HTTP request for GMX REST API.

        Args:
            path: REST path (e.g., "GET /prices/tickers")
            params: Query parameters
            body: Request body (not used for GET requests)
            extra_data: Extra data to attach to response
            timeout: Request timeout in seconds

        Returns:
            RequestData with parsed response
        """
        method = path.split()[0] if " " in path else "GET"
        endpoint = path.split()[1] if " " in path else path

        url = self._params.get_rest_url() + endpoint
        headers = self._get_headers()

        try:
            response = self._http_client.request(
                method=method,
                url=url,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=params,
            )
            return RequestData(response, extra_data)

        except Exception as e:
            self.request_logger.error(f"GMX request failed: {e}")
            raise

    async def async_request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        body: Any = None,
        extra_data=None,
        timeout: int = 5,
    ):
        """Async HTTP request for GMX REST API."""
        method = path.split()[0] if " " in path else "GET"
        endpoint = path.split()[1] if " " in path else path

        url = self._params.get_rest_url() + endpoint
        headers = self._get_headers()

        try:
            response = await self._http_client.async_request(
                method=method,
                url=url,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=params,
            )
            return RequestData(response, extra_data)
        except Exception as e:
            self.async_logger.error(f"GMX async request failed: {e}")
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

        GMX is a DEX — no dedicated server time endpoint.
        """
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": "get_server_time",
                "server_time": time.time(),
            }
        )
        return "GET /prices/tickers", {}, extra_data

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

    def disconnect(self) -> None:
        """No-op for HTTP-based REST API."""

    def is_connected(self) -> bool:
        """Always return True for HTTP-based REST API."""
        return True
