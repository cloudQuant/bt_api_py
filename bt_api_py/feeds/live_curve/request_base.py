"""
Curve REST API request base class.

Curve is a DEX that uses REST API for querying pool data.
Trading is done on-chain through smart contracts.
"""

import requests

from bt_api_py.containers.exchanges.curve_exchange_data import CurveExchangeDataSpot, CurveChain
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class CurveRequestData(Feed):
    """Curve REST API Feed base class."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_POOLS,
            Capability.GET_VOLUMES,
            Capability.GET_TVL,
            Capability.GET_APYS,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "CURVE___DEX")
        self.asset_type = kwargs.get("asset_type", "DEX")

        # Get chain from kwargs or default to Ethereum
        chain = kwargs.get("chain", "ETHEREUM")
        if isinstance(chain, str):
            try:
                chain = CurveChain(chain)
            except ValueError:
                chain = CurveChain.ETHEREUM

        self._params = CurveExchangeDataSpot(chain=chain)
        self.request_logger = SpdLogManager(
            "./logs/curve_feed.log", "request", 0, 0, False
        ).create_logger()

    def _get_headers(self, method, request_path, params=None, body=""):
        """Generate request headers.
        Curve public API does not require authentication.
        """
        return {
            "Content-Type": "application/json",
            "User-Agent": "bt_api_py/1.0",
        }

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for Curve API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

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
