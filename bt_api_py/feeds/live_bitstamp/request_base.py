"""
Bitstamp REST API request base class.
"""

import hmac
import hashlib
import time
import uuid

from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class BitstampRequestData(Feed):
    """Bitstamp REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITSTAMP___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        # Set API keys for authentication
        self.public_key = kwargs.get("public_key")
        self.secret = kwargs.get("private_key") or kwargs.get("secret")
        self.request_logger = SpdLogManager(
            "./logs/bitstamp_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, method, path, params=None, body=""):
        """Generate HMAC SHA256 signature for Bitstamp API v2."""
        # Bitstamp v2 signature format:
        # BITSTAMP {api_key} + method + host + path + content_type + nonce + timestamp + v2 + body
        secret = self.secret
        api_key = self.public_key
        if secret and api_key:
            timestamp = str(int(time.time() * 1000))
            nonce = str(uuid.uuid4())
            content_type = "application/x-www-form-urlencoded" if (params and method == "POST") else ""
            body_str = "&".join(f"{k}={v}" for k, v in params.items()) if params else ""

            message = (
                f"BITSTAMP {api_key}"
                f"{method}"
                f"www.bitstamp.net"
                f"{path}"
                f"{content_type}"
                f"{nonce}"
                f"{timestamp}"
                f"v2"
                f"{body_str}"
            )
            signature = hmac.new(
                secret.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            return {
                "X-Auth": f"BITSTAMP {api_key}",
                "X-Auth-Signature": signature,
                "X-Auth-Nonce": nonce,
                "X-Auth-Timestamp": timestamp,
                "X-Auth-Version": "v2",
            }, content_type, body_str
        return {}, "", ""

    def _get_headers(self, method, request_path, params=None, body=None, is_sign=False):
        """Generate request headers."""
        headers = {
            "Content-Type": "application/json",
        }
        if is_sign:
            auth_headers, content_type, body_str = self._generate_signature(
                method, request_path, params, body
            )
            headers.update(auth_headers)
            if content_type:
                headers["Content-Type"] = content_type
        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for Bitstamp API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path

        is_sign = extra_data.get("is_sign", False) if extra_data else False

        # For signed POST requests, params become form-encoded body
        post_body = None
        if is_sign and method == "POST" and params:
            post_body = params
            params = None

        headers = self._get_headers(method, request_path, params, post_body, is_sign)

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
            response = http_client.request(
                method=method,
                url=self.rest_url + request_path,
                headers=headers,
                data=post_body if method == "POST" else None,
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
