"""
Mercado Bitcoin REST API request base class.
"""

import hmac
import hashlib
import time
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.mercado_bitcoin_exchange_data import MercadoBitcoinExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.logging_factory import get_logger


class MercadoBitcoinRequestData(Feed):
    """Mercado Bitcoin REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "MERCADO_BITCOIN___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = MercadoBitcoinExchangeDataSpot()
        self.request_logger = get_logger("mercado_bitcoin_feed")
        self.async_logger = get_logger("mercado_bitcoin_feed")
        self._http_client = HttpClient(venue=self.exchange_name, timeout=10)

    def _generate_signature(self, tapi_nonce, method_name, params=None):
        """Generate HMAC SHA512 signature for Mercado Bitcoin API."""
        secret = self._params.api_secret
        if secret:
            body_params = {
                "tapi_method": method_name,
                "tapi_nonce": tapi_nonce,
            }
            if params:
                body_params.update(params)

            body = urlencode(body_params)
            auth = f"/tapi/v3/?{body}"
            signature = hmac.new(
                secret.encode("utf-8"),
                auth.encode("utf-8"),
                hashlib.sha512
            ).hexdigest()
            return signature, body
        return "", ""

    def _get_headers(self, method_name, params=None):
        """Generate request headers for private API."""
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        if self._params.api_key:
            nonce = int(time.time())
            signature, body = self._generate_signature(nonce, method_name, params)
            headers["TAPI-ID"] = self._params.api_key
            headers["TAPI-MAC"] = signature

        return headers

    def _resolve_url(self, request_path, original_path=""):
        """Resolve base URL based on request path."""
        base_url = self._params.rest_url
        if "candles" in request_path or "v4" in request_path:
            base_url = getattr(self._params, 'rest_v4_url', self._params.rest_url)
        elif "tapi" in original_path.lower():
            base_url = getattr(self._params, 'rest_private_url', self._params.rest_url)
        return base_url

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for Mercado Bitcoin API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path
        if not request_path.startswith("/"):
            request_path = "/" + request_path

        headers = {"Content-Type": "application/json"}
        base_url = self._resolve_url(request_path, path)

        try:
            response = self._http_client.request(
                method=method,
                url=base_url + request_path,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=params,
            )
            return self._process_response(response, extra_data)
        except Exception as e:
            self.request_logger.error(f"Request failed: {e}")
            raise

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async HTTP request for Mercado Bitcoin API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path
        if not request_path.startswith("/"):
            request_path = "/" + request_path
        headers = {"Content-Type": "application/json"}
        base_url = self._resolve_url(request_path, path)
        try:
            response = await self._http_client.async_request(
                method=method,
                url=base_url + request_path,
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
        path = "GET /BTC/ticker/"
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": "",
            "asset_type": self.asset_type,
            "request_type": "get_server_time",
            "normalize_function": self._get_server_time_normalize_function,
        })
        return path, {}, extra_data

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time (uses ticker timestamp)."""
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        if not input_data:
            return None, False
        if isinstance(input_data, dict):
            ticker = input_data.get("ticker", input_data)
            ts = ticker.get("date") if isinstance(ticker, dict) else None
            return ts, True
        return input_data, True

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
