"""
CoinDCX REST API request base class.
"""

import time
import hmac
import hashlib
import json

from bt_api_py.containers.exchanges.coindcx_exchange_data import CoinDCXExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class CoinDCXRequestData(Feed):
    """CoinDCX REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "COINDCX___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = CoinDCXExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/coindcx_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, body=""):
        """Generate HMAC SHA256 signature for CoinDCX API."""
        secret = self._params.api_secret
        if secret:
            signature = hmac.new(
                secret.encode("utf-8"),
                body.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            return signature
        return ""

    def _get_headers(self, body=""):
        """Generate request headers."""
        timestamp = int(time.time() * 1000)
        headers = {
            "Content-Type": "application/json",
            "X-AUTH-APIKEY": self._params.api_key,
            "X-AUTH-SIGNATURE": self._generate_signature(body),
        }
        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for CoinDCX API."""
        method = path.split()[0] if " " in path else "GET"
        # Extract path part after method (e.g., "GET /exchange/ticker" -> "/exchange/ticker")
        # path.split()[1] already includes the leading slash, so don't add another one
        request_path = path.split()[1] if " " in path else path

        # CoinDCX uses POST for private endpoints with JSON body
        # For public endpoints, use GET
        if method == "GET":
            headers = {"Content-Type": "application/json"}
            try:
                from bt_api_py.feeds.http_client import HttpClient

                http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
                response = http_client.request(
                    method=method,
                    url=self._params.rest_url + request_path,
                    headers=headers,
                    params=params,
                )
                return self._process_response(response, extra_data)
            except Exception as e:
                self.request_logger.error(f"Request failed: {e}")
                raise
        else:
            # POST request with body
            import json
            json_body = json.dumps(body, separators=(',', ':')) if body else "{}"
            headers = self._get_headers(json_body)
            try:
                from bt_api_py.feeds.http_client import HttpClient

                http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
                response = http_client.request(
                    method=method,
                    url=self._params.rest_url + request_path,
                    headers=headers,
                    json_data=json.loads(json_body),
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
