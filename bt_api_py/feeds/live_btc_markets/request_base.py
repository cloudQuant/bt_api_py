"""
BTC Markets REST API request base class.
"""

import time
import hmac
import hashlib
import base64
import json

from bt_api_py.containers.exchanges.btc_markets_exchange_data import BtcMarketsExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.logging_factory import get_logger


class BtcMarketsRequestData(Feed):
    """BTC Markets REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "BTC_MARKETS___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BtcMarketsExchangeDataSpot()
        self.api_key = kwargs.get("api_key", None)
        self.api_secret = kwargs.get("api_secret", None)
        self.request_logger = get_logger("btc_markets_feed")
        self.async_logger = get_logger("btc_markets_feed")
        self._http_client = HttpClient(venue=self.exchange_name, timeout=10)

    def _generate_signature(self, method, path, nonce, body=""):
        """Generate HMAC SHA512 signature for BTC Markets API.

        Signature: METHOD + path + nonce (+ body for POST)
        Then HMAC-SHA512 with base64 decoded secret
        Finally base64 encode the result
        """
        secret_b64 = self.api_secret
        if not secret_b64:
            return ""

        # Decode secret from base64
        secret = base64.b64decode(secret_b64)

        # Build auth string
        auth = method + path + nonce
        if method == "POST" and body:
            auth += body

        # HMAC-SHA512 signature
        signature = hmac.new(
            secret,
            auth.encode(),
            hashlib.sha512
        ).digest()

        # Base64 encode signature
        return base64.b64encode(signature).decode()

    def _get_headers(self, method, request_path, params=None, body=None):
        """Generate request headers."""
        nonce = str(int(time.time() * 1000))

        # Build body string for POST requests
        body_str = ""
        if method == "POST" and body:
            body_str = json.dumps(body, separators=(',', ':'))

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "BM-AUTH-APIKEY": self.api_key if self.api_key else "",
            "BM-AUTH-TIMESTAMP": nonce,
            "BM-AUTH-SIGNATURE": self._generate_signature(method, request_path, nonce, body_str),
        }
        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for BTC Markets API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        try:
            url = self._params.rest_url + request_path
            if method == "GET" and params:
                query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
                url = url + "?" + query

            response = self._http_client.request(
                method=method,
                url=url,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=None,
            )
            return self._process_response(response, extra_data)
        except Exception as e:
            self.request_logger.error(f"Request failed: {e}")
            raise

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async HTTP request for BTC Markets API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        try:
            url = self._params.rest_url + request_path
            if method == "GET" and params:
                query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
                url = url + "?" + query

            response = await self._http_client.async_request(
                method=method,
                url=url,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=None,
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
        return "GET /v3/time", {}, extra_data

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
