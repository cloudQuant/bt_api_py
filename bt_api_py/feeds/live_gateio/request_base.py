"""
Gate.io REST API request base class.
Handles authentication (HMAC SHA512), signing, and all REST API methods.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.gateio_exchange_data import GateioExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule


class GateioRequestData(Feed, RequestData):
    """Base class for Gate.io REST API requests"""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.QUERY_ORDER,
            Capability.QUERY_OPEN_ORDERS,
            Capability.GET_DEALS,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_SERVER_TIME,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.public_key = kwargs.get("public_key")
        self.private_key = kwargs.get("private_key")
        self.asset_type = kwargs.get("asset_type", "spot")
        self.exchange_name = "GATEIO"
        self.logger_name = kwargs.get("logger_name", "gateio_feed.log")
        self._params = GateioExchangeDataSpot()
        self.request_logger = SpdLogManager(
            self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            self.logger_name, "async_request", 0, 0, False
        ).create_logger()
        self._rate_limiter = kwargs.get("rate_limiter", self._create_default_rate_limiter())

    @staticmethod
    def _create_default_rate_limiter():
        return RateLimiter(
            rules=[
                RateLimitRule(
                    name="gate_ip_limit",
                    type="request_count",
                    interval=1,
                    limit=900,
                    scope="ip"
                ),
            ]
        )

    def _generate_signature(self, method, url_path, query_string='', payload_string=''):
        """Generate HMAC SHA512 signature for Gate.io API v4.

        Returns (signature, timestamp) or (None, None) for public endpoints.
        """
        if self.private_key is None:
            return None, None

        timestamp = str(int(time.time()))
        hashed_payload = hashlib.sha512(payload_string.encode()).hexdigest() if payload_string else hashlib.sha512(b'').hexdigest()
        sign_string = f"{method}\n{url_path}\n{query_string}\n{hashed_payload}\n{timestamp}"
        signature = hmac.new(
            self.private_key.encode(),
            sign_string.encode(),
            hashlib.sha512
        ).hexdigest()
        return signature, timestamp

    def _build_auth_headers(self, method, url_path, query_string='', payload_string=''):
        """Build request headers with optional authentication."""
        signature, timestamp = self._generate_signature(method, url_path, query_string, payload_string)
        headers = {'Content-Type': 'application/json'}
        if self.public_key is not None and signature is not None:
            headers['KEY'] = self.public_key
            headers['Timestamp'] = timestamp
            headers['SIGN'] = signature
        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """Synchronous HTTP request following the framework pattern.

        Args:
            path: Request path in format "METHOD /endpoint" (e.g. "GET /spot/tickers")
            params: Query parameters for GET requests
            body: Request body for POST/PUT/DELETE
            extra_data: Extra data dict for response normalization
            timeout: Request timeout in seconds
        Returns:
            RequestData wrapping the response
        """
        if params is None:
            params = {}
        if body is None:
            body = {}

        method, endpoint = path.split(" ", 1)
        method = method.upper()

        # Build URL
        base_url = self._params.rest_url
        url = base_url + endpoint

        # Query string
        query_string = ''
        if params and method == 'GET':
            query_string = urlencode(sorted(params.items()))
            url = f"{url}?{query_string}"

        # Payload
        payload_string = ''
        if body and method in ('POST', 'PUT', 'DELETE'):
            payload_string = json.dumps(body)

        # Headers
        url_path = f'/api/v4{endpoint}'
        headers = self._build_auth_headers(method, url_path, query_string, payload_string)

        # Log request
        self.request_logger.info(f"{method} {url}")

        # Make request
        response_data = self.http_request(
            method=method,
            url=url,
            headers=headers,
            body=body if method in ('POST', 'PUT', 'DELETE') else None,
            timeout=timeout
        )

        request_data = RequestData(response_data, extra_data if extra_data else {})
        request_data.init_data()
        return request_data

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Asynchronous HTTP request following the framework pattern."""
        if params is None:
            params = {}
        if body is None:
            body = {}

        method, endpoint = path.split(" ", 1)
        method = method.upper()

        base_url = self._params.rest_url
        url = base_url + endpoint

        query_string = ''
        if params and method == 'GET':
            query_string = urlencode(sorted(params.items()))
            url = f"{url}?{query_string}"

        payload_string = ''
        if body and method in ('POST', 'PUT', 'DELETE'):
            payload_string = json.dumps(body)

        url_path = f'/api/v4{endpoint}'
        headers = self._build_auth_headers(method, url_path, query_string, payload_string)

        self.async_logger.info(f"async {method} {url}")

        response_data = await self.async_http_request(
            method=method,
            url=url,
            headers=headers,
            body=body if method in ('POST', 'PUT', 'DELETE') else None,
            timeout=timeout
        )

        return RequestData(response_data, extra_data if extra_data else {})

    def async_callback(self, future):
        """Callback for async requests — push result to data queue."""
        try:
            request_data = future.result()
            if request_data is not None:
                request_data.init_data()
                self.push_data_to_queue(request_data)
        except Exception as e:
            self.async_logger.error(f"async_callback error: {e}")

    def push_data_to_queue(self, request_data):
        """Push RequestData into the data queue."""
        if self.data_queue is not None:
            self.data_queue.put(request_data)

    @staticmethod
    def _extract_data_normalize_function(input_data, extra_data):
        """Generic normalization: Gate.io v4 API returns data directly (list or dict).
        Errors return {"label": "...", "message": "..."}."""
        if input_data is None:
            return [], False
        if isinstance(input_data, dict) and "label" in input_data:
            return [input_data], False
        if isinstance(input_data, list):
            return input_data, True
        return [input_data], True
