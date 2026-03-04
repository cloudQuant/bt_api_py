"""
CoinDCX REST API request base class.
"""

import time
import hmac
import hashlib
import json

from bt_api_py.containers.exchanges.coindcx_exchange_data import CoinDCXExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.logging_factory import get_logger


class CoinDCXRequestData(Feed):
    """CoinDCX REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "COINDCX___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = CoinDCXExchangeDataSpot()
        self.request_logger = get_logger("coindcx_feed")
        self.async_logger = get_logger("coindcx_feed")
        self._http_client = HttpClient(venue=self.exchange_name, timeout=10)

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
        request_path = path.split()[1] if " " in path else path

        if method == "GET":
            headers = {"Content-Type": "application/json"}
            try:
                response = self._http_client.request(
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
            json_body = json.dumps(body, separators=(',', ':')) if body else "{}"
            headers = self._get_headers(json_body)
            try:
                response = self._http_client.request(
                    method=method,
                    url=self._params.rest_url + request_path,
                    headers=headers,
                    json_data=json.loads(json_body),
                )
                return self._process_response(response, extra_data)
            except Exception as e:
                self.request_logger.error(f"Request failed: {e}")
                raise

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async HTTP request for CoinDCX API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path

        if method == "GET":
            headers = {"Content-Type": "application/json"}
            try:
                response = await self._http_client.async_request(
                    method=method,
                    url=self._params.rest_url + request_path,
                    headers=headers,
                    params=params,
                )
                return self._process_response(response, extra_data)
            except Exception as e:
                self.async_logger.error(f"Async request failed: {e}")
                raise
        else:
            json_body = json.dumps(body, separators=(',', ':')) if body else "{}"
            headers = self._get_headers(json_body)
            try:
                response = await self._http_client.async_request(
                    method=method,
                    url=self._params.rest_url + request_path,
                    headers=headers,
                    json_data=json.loads(json_body),
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
        return "GET /exchange/v1/time", {}, extra_data

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
