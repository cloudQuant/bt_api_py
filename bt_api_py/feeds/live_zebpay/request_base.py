"""
Zebpay REST API request base class.
"""

import time
import hmac
import hashlib
import json

from bt_api_py.containers.exchanges.zebpay_exchange_data import ZebpayExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class ZebpayRequestData(Feed):
    """Zebpay REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "ZEBPAY___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = ZebpayExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/zebpay_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, payload):
        """Generate HMAC SHA256 signature for Zebpay API.

        Zebpay signature rules:
        - GET/DELETE: sign the URL query string
        - POST/PUT: sign the JSON body
        """
        secret = self._params.api_secret
        if secret:
            signature = hmac.new(
                secret.encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            return signature
        return ""

    def _get_headers(self, method, params=None, body=None):
        """Generate request headers."""
        headers = {
            "Content-Type": "application/json",
        }

        # Add auth headers if credentials available
        if self._params.api_key:
            headers["X-AUTH-APIKEY"] = self._params.api_key

        if self._params.api_secret:
            timestamp = str(int(time.time() * 1000))
            if method in ["GET", "DELETE"]:
                # Sign query string
                query_params = params.copy() if params else {}
                query_params["timestamp"] = timestamp
                from urllib.parse import urlencode
                query_string = urlencode(query_params)
                headers["X-AUTH-SIGNATURE"] = self._generate_signature(query_string)
            elif method in ["POST", "PUT"]:
                # Sign JSON body
                body_params = body.copy() if body else {}
                body_params["timestamp"] = timestamp
                json_body = json.dumps(body_params, separators=(",", ":"))
                headers["X-AUTH-SIGNATURE"] = self._generate_signature(json_body)

        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for Zebpay API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        headers = self._get_headers(method, params, body)

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
            response = http_client.request(
                method=method,
                url=self._params.rest_url + request_path,
                headers=headers,
                params=params,
                json_data=body if method in ["POST", "PUT"] else None,
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
