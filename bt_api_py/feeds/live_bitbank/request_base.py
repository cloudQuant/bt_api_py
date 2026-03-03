"""
Bitbank REST API request base class.
"""

import time
import hmac
import hashlib

from bt_api_py.containers.exchanges.bitbank_exchange_data import BitbankExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class BitbankRequestData(Feed):
    """Bitbank REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITBANK___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BitbankExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/bitbank_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, timestamp, time_window, message):
        """Generate HMAC SHA256 signature for Bitbank API."""
        secret = getattr(self._params, 'api_secret', None)
        if secret:
            signature = hmac.new(
                secret.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            return signature
        return ""

    def _get_headers(self, method, request_path, params=None, body=""):
        """Generate request headers with authentication."""
        headers = {
            "Content-Type": "application/json",
        }

        # Add auth headers if API key is configured
        api_key = getattr(self._params, 'api_key', None)
        if api_key:
            timestamp = str(int(time.time() * 1000))
            time_window = "5000"

            # Build signature message based on method
            if method == "GET":
                full_path = request_path
                if params:
                    query_string = "&".join(f"{k}={v}" for k, v in params.items())
                    full_path = f"{request_path}?{query_string}"
                message = timestamp + time_window + full_path
            else:  # POST
                message = timestamp + time_window + body

            signature = self._generate_signature(timestamp, time_window, message)

            headers.update({
                "ACCESS-KEY": api_key,
                "ACCESS-SIGNATURE": signature,
                "ACCESS-REQUEST-TIME": timestamp,
                "ACCESS-TIME-WINDOW": time_window,
            })

        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for Bitbank API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
            response = http_client.request(
                method=method,
                url=self._params.rest_url + request_path,
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
