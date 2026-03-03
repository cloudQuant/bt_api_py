"""
GMX REST API request base class.

GMX is a decentralized perpetual exchange supporting:
- Arbitrum
- Avalanche
- Botanix

Uses REST API for oracle/pricing data (not GraphQL).
Documentation: https://docs.gmx.io/docs/api/rest/
"""

from typing import Any

from bt_api_py.containers.exchanges.gmx_exchange_data import (
    GmxChain,
    GmxExchangeDataSpot,
)
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.functions.log_message import SpdLogManager


class GmxRequestData(Feed):
    """GMX REST API Feed base class.

    GMX uses REST API endpoints for oracle/pricing data.
    No authentication required for public endpoints.
    """

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_KLINE,
        }

    def __init__(self, data_queue, **kwargs):
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
        self.request_logger = SpdLogManager(
            "./logs/gmx_feed.log", "request", 0, 0, False
        ).create_logger()

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
            return self._process_response(response, extra_data)

        except Exception as e:
            self.request_logger.error(f"GMX request failed: {e}")
            raise

    def _process_response(self, response, extra_data=None):
        """Process API response."""
        from bt_api_py.containers.requestdatas.request_data import RequestData
        return RequestData(response, extra_data)

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
