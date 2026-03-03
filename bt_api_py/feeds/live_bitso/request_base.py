"""
Bitso REST API request base class.
"""

import hmac
import hashlib
import json
import time

from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class BitsoRequestData(Feed):
    """Bitso REST API Feed base class."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "BITSO___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.request_logger = SpdLogManager(
            "./logs/bitso_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, method, request_path, body=""):
        """Generate HMAC SHA256 signature for Bitso API."""
        # Bitso signature: nonce + method + requestPath + body
        secret = self.secret
        api_key = self.public_key
        if secret and api_key:
            nonce = str(int(time.time() * 1000))
            body_str = json.dumps(body) if body else ""
            sign_str = nonce + method.upper() + request_path + body_str
            signature = hmac.new(
                secret.encode("utf-8"),
                sign_str.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            return f"Bitso {api_key}:{nonce}:{signature}"
        return ""

    def _get_headers(self, method, request_path, params=None, body="", is_sign=False):
        """Generate request headers."""
        headers = {
            "Content-Type": "application/json",
        }
        if is_sign:
            # Build request path with query params for signing
            path = request_path
            if params and method == "GET":
                query = "&".join(f"{k}={v}" for k, v in params.items())
                path = f"{request_path}?{query}"
            headers["Authorization"] = self._generate_signature(method, path, body)
        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for Bitso API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path

        is_sign = extra_data.get("is_sign", False) if extra_data else False

        headers = self._get_headers(method, request_path, params, body, is_sign)

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
            response = http_client.request(
                method=method,
                url=self.rest_url + request_path,
                headers=headers,
                json_data=body if method in ("POST", "DELETE") else None,
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
