"""
BTCTurk REST API request base class.
"""

import time
import hmac
import hashlib
import base64

from bt_api_py.containers.exchanges.btcturk_exchange_data import BTCTurkExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.functions.log_message import SpdLogManager


class BTCTurkRequestData(Feed):
    """BTCTurk REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "BTCTURK___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BTCTurkExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/btcturk_feed.log", "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/btcturk_feed.log", "async_request", 0, 0, False
        ).create_logger()
        self._http_client = HttpClient(venue=self.exchange_name, timeout=10)

    def _generate_signature(self, timestamp):
        """Generate HMAC SHA256 signature for BTCTurk API.

        BTCTurk signature format:
        1. Message = publicKey + timestamp
        2. Base64 decode private key
        3. HMAC-SHA256(message, decoded_private_key)
        4. Base64 encode result
        """
        public_key = self._params.api_key
        private_key = self._params.api_secret

        if public_key and private_key:
            message = public_key + str(timestamp)
            # Base64 decode the private key first
            private_key_bytes = base64.b64decode(private_key)
            # HMAC SHA256
            signature = base64.b64encode(
                hmac.new(
                    private_key_bytes,
                    message.encode("utf-8"),
                    hashlib.sha256
                ).digest()
            ).decode("utf-8")
            return signature
        return ""

    def _get_headers(self, method, request_path, params=None, body=""):
        """Generate request headers."""
        timestamp = int(time.time() * 1000)
        headers = {
            "Content-Type": "application/json",
        }

        # Add auth headers if API keys are configured
        if self._params.api_key:
            headers["X-PCK"] = self._params.api_key
            headers["X-Stamp"] = str(timestamp)
            headers["X-Signature"] = self._generate_signature(timestamp)

        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for BTCTurk API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        try:
            response = self._http_client.request(
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

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async HTTP request for BTCTurk API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        try:
            response = await self._http_client.async_request(
                method=method,
                url=self._params.rest_url + request_path,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=params,
            )
            return self._process_response(response, extra_data)
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

    def _process_response(self, response, extra_data=None):
        """Process API response."""
        if extra_data is None:
            extra_data = {}
        return RequestData(response, extra_data)

    def _get_server_time(self, extra_data=None, **kwargs):
        """Prepare server time request. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": "",
            "asset_type": self.asset_type,
            "request_type": "get_server_time",
        })
        return "GET /api/v2/server/time", {}, extra_data

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time. Returns RequestData."""
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

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
