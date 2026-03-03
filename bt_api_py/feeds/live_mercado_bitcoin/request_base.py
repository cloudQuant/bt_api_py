"""
Mercado Bitcoin REST API request base class.
"""

import hmac
import hashlib
import time
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.mercado_bitcoin_exchange_data import MercadoBitcoinExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class MercadoBitcoinRequestData(Feed):
    """Mercado Bitcoin REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "MERCADO_BITCOIN___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = MercadoBitcoinExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/mercado_bitcoin_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, tapi_nonce, method_name, params=None):
        """Generate HMAC SHA512 signature for Mercado Bitcoin API."""
        secret = self._params.api_secret
        if secret:
            # Build the parameter string
            body_params = {
                "tapi_method": method_name,
                "tapi_nonce": tapi_nonce,
            }
            if params:
                body_params.update(params)

            body = urlencode(body_params)
            # Signature string: /tapi/v3/? + body
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

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for Mercado Bitcoin API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path
        # Don't add leading slash if path already has one
        if not request_path.startswith("/"):
            request_path = "/" + request_path

        headers = {"Content-Type": "application/json"}

        # Determine base URL
        base_url = self._params.rest_url
        if "candles" in request_path or "v4" in request_path:
            base_url = getattr(self._params, 'rest_v4_url', self._params.rest_url)
        elif "tapi" in path.lower():
            base_url = getattr(self._params, 'rest_private_url', self._params.rest_url)

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
            response = http_client.request(
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
