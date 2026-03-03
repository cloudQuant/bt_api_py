"""
Coinone REST API request base class.
"""

import time
import hmac
import hashlib
import base64
import json
import uuid

from bt_api_py.containers.exchanges.coinone_exchange_data import CoinoneExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class CoinoneRequestData(Feed):
    """Coinone REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "COINONE___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = CoinoneExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/coinone_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_payload(self, params=None):
        """Generate payload for Coinone API."""
        nonce = str(uuid.uuid4())
        body = {
            "access_token": self._params.api_key,
            "nonce": nonce,
        }
        if params:
            body.update(params)

        json_str = json.dumps(body)
        payload = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
        return payload, body

    def _generate_signature(self, payload):
        """Generate HMAC SHA512 signature for Coinone API."""
        secret = self._params.api_secret.upper() if self._params.api_secret else ""
        if secret:
            signature = hmac.new(
                secret.encode('utf-8'),
                payload.encode('utf-8'),
                hashlib.sha512
            ).hexdigest()
            return signature
        return ""

    def _get_headers(self, payload):
        """Generate request headers."""
        headers = {
            "Content-Type": "application/json",
            "X-COINONE-PAYLOAD": payload,
            "X-COINONE-SIGNATURE": self._generate_signature(payload),
        }
        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for Coinone API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        # For public GET endpoints
        if method == "GET" and not body:
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
            # POST request for private endpoints
            payload, _ = self._generate_payload(body)
            headers = self._get_headers(payload)
            try:
                from bt_api_py.feeds.http_client import HttpClient

                http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
                response = http_client.request(
                    method="POST",
                    url=self._params.rest_url + request_path,
                    headers=headers,
                    data=payload,
                )
                return self._process_response(response, extra_data)
            except Exception as e:
                self.request_logger.error(f"Request failed: {e}")
                raise

    def _process_response(self, response, extra_data=None):
        """Process API response."""
        from bt_api_py.containers.requestdatas.request_data import RequestData
        request_data = RequestData(response, extra_data)
        # Initialize data to trigger normalization and set status
        request_data.init_data()
        return request_data

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
