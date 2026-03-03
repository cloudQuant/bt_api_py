"""
CoW Swap REST API request base class.
CoW Swap is a DEX (Decentralized Exchange) that uses Ethereum smart contracts.
"""

import time

from bt_api_py.containers.exchanges.cow_swap_exchange_data import CowSwapExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class CowSwapRequestData(Feed):
    """CoW Swap REST API Feed base class."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "COW_SWAP___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = CowSwapExchangeDataSpot()
        self.chain = kwargs.get("chain", "mainnet")
        self.request_logger = SpdLogManager(
            "./logs/cow_swap_feed.log", "request", 0, 0, False
        ).create_logger()

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
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
            response = http_client.request(
                method=method,
                url=self._get_base_url() + request_path,
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
