"""
Zaif REST API request base class.
"""

import time
import hmac
import hashlib
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.zaif_exchange_data import ZaifExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class ZaifRequestData(Feed):
    """Zaif REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "ZAIF___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = ZaifExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/zaif_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, body):
        """Generate HMAC SHA512 signature for Zaif API.

        Zaif uses a special nonce format: seconds with 8 decimal places.
        Signature is HMAC-SHA512 of the URL-encoded body.
        """
        secret = self._params.api_secret
        if secret:
            signature = hmac.new(
                secret.encode("utf-8"),
                body.encode("utf-8"),
                hashlib.sha512
            ).hexdigest()
            return signature
        return ""

    def _get_headers(self, body=""):
        """Generate request headers."""
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }
        # Add auth headers if credentials available
        if self._params.api_key:
            headers["Key"] = self._params.api_key
        if self._params.api_secret and body:
            headers["Sign"] = self._generate_signature(body)
        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for Zaif API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        # For private API, construct body with method and nonce
        post_body = None
        if method == "POST":
            nonce = format(time.time(), '.8f')
            body_params = {"method": path.split()[-1] if " " in path else path, "nonce": nonce}
            if params:
                body_params.update(params)
            post_body = urlencode(body_params)
        elif body:
            post_body = body

        headers = self._get_headers(post_body)

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
            url = self._params.rest_url + request_path

            if method == "POST" and post_body:
                response = http_client.request(
                    method="POST",
                    url=url + "/tapi",  # Zaif private API uses /tapi endpoint
                    headers=headers,
                    data=post_body.encode("utf-8") if post_body else None,
                )
            else:
                response = http_client.request(
                    method=method,
                    url=url,
                    headers=headers,
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
