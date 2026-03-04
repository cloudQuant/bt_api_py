"""
CoW Swap REST API request base class.
CoW Swap is a DEX (Decentralized Exchange) that uses Ethereum smart contracts.
"""

import time

from bt_api_py.containers.exchanges.cow_swap_exchange_data import CowSwapExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.logging_factory import get_logger


class CowSwapRequestData(Feed):
    """CoW Swap REST API Feed base class."""

    @classmethod
    def _capabilities(cls):
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

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "COW_SWAP___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = CowSwapExchangeDataSpot()
        self.chain = kwargs.get("chain", "mainnet")
        self.request_logger = get_logger("cow_swap_feed")
        self.async_logger = get_logger("cow_swap_feed")
        self._http_client = HttpClient(venue=self.exchange_name, timeout=10)

    def _get_headers(self):
        """Generate request headers."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        return headers

    def _get_base_url(self):
        """Get base URL with chain path."""
        chain_path = f"/{self.chain}" if self.chain else ""
        return f"{self._params.rest_url}{chain_path}"

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for CoW Swap API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        headers = self._get_headers()

        try:
            response = self._http_client.request(
                method=method,
                url=self._get_base_url() + request_path,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=params,
            )
            return RequestData(response, extra_data)
        except Exception as e:
            self.request_logger.error(f"Request failed: {e}")
            raise

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async HTTP request for CoW Swap API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        headers = self._get_headers()

        try:
            response = await self._http_client.async_request(
                method=method,
                url=self._get_base_url() + request_path,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=params,
            )
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

        CoW Swap provides a version endpoint but no dedicated server time.
        Returns local time as fallback.
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
        return "GET /api/v1/version", {}, extra_data

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time. Returns RequestData.

        CoW Swap is a DEX — returns local timestamp as proxy.
        """
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return RequestData({"server_time": time.time()}, extra_data)

    def push_data_to_queue(self, data):
        """Push data to the queue."""
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            raise RuntimeError("Queue not initialized")

    def connect(self):
        pass

    def disconnect(self):
        pass

    def is_connected(self):
        return True
