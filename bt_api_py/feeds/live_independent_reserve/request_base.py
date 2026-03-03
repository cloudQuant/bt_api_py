"""
Independent Reserve REST API request base class.

Independent Reserve is an Australian cryptocurrency exchange.
Authentication uses HMAC SHA256 signature.
Documentation: https://www.independentreserve.com/API
"""

import hmac
import hashlib
import time

from bt_api_py.containers.exchanges.independent_reserve_exchange_data import IndependentReserveExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class IndependentReserveRequestData(Feed):
    """Independent Reserve REST API Feed base class."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "INDEPENDENT_RESERVE___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = IndependentReserveExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/independent_reserve_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, url, nonce, params=None):
        """Generate HMAC SHA256 signature for Independent Reserve API.

        Independent Reserve signature format:
        url,apiKey={key},nonce={nonce},param1=val1,param2=val2

        Args:
            url: Full URL of the endpoint
            nonce: Nonce value (timestamp in milliseconds)
            params: Dictionary of parameters to include in signature

        Returns:
            Hex signature string (uppercase)
        """
        secret = self._params.api_secret if hasattr(self._params, "api_secret") else None
        if not secret:
            return ""

        api_key = self._params.api_key if hasattr(self._params, "api_key") else ""

        # Build signature string: url,apiKey={key},nonce={nonce},params
        auth_parts = [url, f"apiKey={api_key}", f"nonce={nonce}"]

        # Add parameters to signature string
        if params:
            for key, value in params.items():
                auth_parts.append(f"{key}={value}")

        message = ",".join(auth_parts)

        # Generate HMAC SHA256 signature
        signature = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest().upper()

        return signature

    def _get_headers(self, method="GET", request_path="", params=None, body=""):
        """Generate request headers for Independent Reserve API.

        For public endpoints, no special headers needed.
        For private endpoints, signature is included in the JSON body.
        """
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "bt_api_py/1.0",
        }

        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10, is_private=False):
        """HTTP request for Independent Reserve API.

        Args:
            path: REST path (e.g., "GET /Public/GetMarketSummary")
            params: Query parameters for GET requests
            body: Request body for POST requests
            extra_data: Extra data to attach to response
            timeout: Request timeout
            is_private: Whether this is a private API request

        Returns:
            RequestData with parsed response
        """
        method = path.split()[0] if " " in path else "GET"
        raw_path = path.split()[1] if " " in path else path
        request_path = raw_path if raw_path.startswith("/") else "/" + raw_path

        headers = self._get_headers(method, request_path, params, body)

        # For private endpoints, add signature to body
        if is_private and method == "POST":
            if body is None:
                body = {}

            url = self._params.rest_url + request_path
            nonce = int(time.time() * 1000)

            # Add API key and nonce to body
            if hasattr(self._params, "api_key") and self._params.api_key:
                body["apiKey"] = self._params.api_key
            body["nonce"] = nonce

            # Generate and add signature
            signature = self._generate_signature(url, nonce, body)
            if signature:
                body["signature"] = signature

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
            response = http_client.request(
                method=method,
                url=self._params.rest_url + request_path,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=params if method == "GET" else None,
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
