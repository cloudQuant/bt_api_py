"""
EXMO REST API request base class.
"""

import time
import hmac
import hashlib
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.exmo_exchange_data import ExmoExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.logging_factory import get_logger


class ExmoRequestData(Feed):
    """EXMO REST API Feed base class."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_SERVER_TIME,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "EXMO___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = ExmoExchangeDataSpot()
        self.request_logger = get_logger("exmo_feed")
        self.async_logger = get_logger("exmo_feed")
        self._http_client = HttpClient(venue=self.exchange_name, timeout=10)

    def _generate_signature(self, body_params):
        """Generate HMAC SHA512 signature for EXMO API.

        EXMO uses HMAC SHA512 signature on URL-encoded body.
        """
        secret = getattr(self._params, 'api_secret', None)
        if secret:
            body = urlencode(body_params)
            signature = hmac.new(
                secret.encode("utf-8"),
                body.encode("utf-8"),
                hashlib.sha512
            ).hexdigest()
            return signature, body
        return "", ""

    def _get_headers(self, method, request_path, params=None, body=""):
        """Generate request headers.

        For public API: No auth required
        For private API: Key and Sign headers
        """
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        # Add auth headers for private endpoints
        api_key = getattr(self._params, 'api_key', None)
        if api_key:
            headers["Key"] = api_key
            if body:
                signature, _ = self._generate_signature(body)
                headers["Sign"] = signature

        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for EXMO API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        # EXMO uses POST for all private endpoints with body
        # For public endpoints, use GET with query params
        api_key = getattr(self._params, 'api_key', None)
        if method == "POST" or (api_key and body):
            # Private endpoint - use POST with body
            if not body:
                body_params = {"nonce": int(time.time() * 1000)}
                if params:
                    body_params.update(params)
                params = None
            else:
                body_params = body

            signature, encoded_body = self._generate_signature(body_params)
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Key": api_key,
                "Sign": signature,
            }
        else:
            # Public endpoint - use GET
            headers = self._get_headers(method, request_path, params, body)
            encoded_body = None

        try:
            response = self._http_client.request(
                method=method if not encoded_body else "POST",
                url=self._params.rest_url + request_path,
                headers=headers,
                json_data=None,
                params=params,
                data=encoded_body,
            )
            return self._process_response(response, extra_data)
        except Exception as e:
            self.request_logger.error(f"Request failed: {e}")
            raise

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async HTTP request for EXMO API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        api_key = getattr(self._params, 'api_key', None)
        if method == "POST" or (api_key and body):
            if not body:
                body_params = {"nonce": int(time.time() * 1000)}
                if params:
                    body_params.update(params)
                params = None
            else:
                body_params = body
            signature, encoded_body = self._generate_signature(body_params)
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Key": api_key,
                "Sign": signature,
            }
        else:
            headers = self._get_headers(method, request_path, params, body)
            encoded_body = None

        try:
            response = await self._http_client.async_request(
                method=method if not encoded_body else "POST",
                url=self._params.rest_url + request_path,
                headers=headers,
                params=params,
                data=encoded_body,
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
        request_data = RequestData(response, extra_data)
        request_data.init_data()
        request_data.has_been_init_data = True
        return request_data

    def push_data_to_queue(self, data):
        """Push data to the queue."""
        if self.data_queue is not None:
            self.data_queue.put(data)

    def _get_server_time(self, extra_data=None, **kwargs):
        """Get server time.

        Note: EXMO doesn't have a dedicated server time endpoint,
        so we use current local time.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_server_time"
        path = f"GET /time"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_server_time_normalize_function,
        })
        return path, {}, extra_data

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        """Normalize server time response.

        Since EXMO doesn't have a /time endpoint, we return current time.
        """
        import time as tm
        server_time_data = {
            "server_time": int(tm.time()),
            "exchange": "EXMO"
        }
        return [server_time_data], True

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time.

        Returns:
            RequestData: Server time response
        """
        # Since EXMO doesn't have a /time endpoint, return current time directly
        path, params, extra_data = self._get_server_time(extra_data=extra_data, **kwargs)
        # Create a mock response with current time
        import time as tm
        response = {"server_time": int(tm.time())}
        return self._process_response(response, extra_data)

    def connect(self):
        pass

    def disconnect(self):
        pass

    def is_connected(self):
        return True
