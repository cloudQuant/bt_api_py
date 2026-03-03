"""
Poloniex REST API request base class.
Handles authentication, signing, and all REST API methods.
"""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.poloniex_exchange_data import PoloniexExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.error_framework import ErrorTranslator
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitScope, RateLimitType


class PoloniexErrorTranslator(ErrorTranslator):
    """
    Poloniex Error Translation

    Poloniex uses HTTP status codes and business error codes.
    Common error patterns:
    - 200: Success with optional error code in response body
    - 400: Bad request
    - 401: Authentication failed
    - 404: Resource not found
    - 429: Rate limit exceeded
    - 500: Internal server error

    Business error codes (from response body):
    - 21301: Order not found
    - 21302: Order amount too small
    - 21303: Order quantity too small
    - 21304: Insufficient balance
    - 21305: Exceeded maximum order count
    - 21308: Price precision error
    - 21309: Quantity precision error
    """

    ERROR_MAP = {
        # Authentication errors
        401: (1001, "Authentication failed"),
        1001: (1001, "Invalid API key"),
        1002: (1002, "Invalid signature"),
        1003: (1003, "Timestamp expired"),

        # Order errors
        21301: (2001, "Order not found"),
        21302: (2002, "Order amount too small"),
        21303: (2003, "Order quantity too small"),
        21304: (2004, "Insufficient balance"),
        21305: (2005, "Exceeded maximum order count"),
        21308: (2006, "Price precision error"),
        21309: (2007, "Quantity precision error"),

        # Rate limiting
        429: (3001, "Rate limit exceeded"),

        # System errors
        500: (5001, "Internal server error"),
        503: (5002, "Service unavailable"),
    }


class PoloniexRequestData(Feed):
    """Base class for Poloniex REST API requests.

    Handles authentication, signing, rate limiting, and request/response processing.
    """

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
            Capability.MARKET_STREAM,
            Capability.ACCOUNT_STREAM,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_SERVER_TIME,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.public_key = kwargs.get("public_key")
        self.private_key = kwargs.get("private_key")
        self.topics = kwargs.get("topics", {})
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "poloniex_feed.log")
        self._params = PoloniexExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()
        self._error_translator = PoloniexErrorTranslator()
        self._rate_limiter = kwargs.get("rate_limiter", self._create_default_rate_limiter())

    @staticmethod
    def _create_default_rate_limiter():
        """Create default rate limiter for Poloniex."""
        rules = [
            RateLimitRule(
                name="poloniex_public",
                limit=200,
                interval=1,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.ENDPOINT,
                endpoint="/*",
            ),
            RateLimitRule(
                name="poloniex_private",
                limit=50,
                interval=1,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.ENDPOINT,
                endpoint="/accounts/*",
            ),
            RateLimitRule(
                name="poloniex_trade",
                limit=50,
                interval=1,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.ENDPOINT,
                endpoint="/orders",
            ),
        ]
        return RateLimiter(rules)

    def translate_error(self, raw_response):
        """Translate Poloniex API response to UnifiedError (if error exists), else return None."""
        if isinstance(raw_response, dict):
            code = raw_response.get("code")
            # Poloniex returns 200 for success, other codes indicate errors
            if code and code != 200 and code != 0:
                return self._error_translator.translate(raw_response, self.exchange_name)
        return None

    def push_data_to_queue(self, data):
        """Push data to the queue."""
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            assert 0, "Queue not initialized"

    def signature(self, timestamp, method, request_path, secret_key, body=None):
        """
        Generate HMAC-SHA256 signature for Poloniex API.

        Args:
            timestamp: Millisecond timestamp
            method: HTTP method (GET, POST, DELETE)
            request_path: Request path including query parameters
            secret_key: API secret key
            body: Request body (JSON string for POST/DELETE)

        Returns:
            Base64 encoded signature
        """
        body_str = json.dumps(body) if body else ""
        sign_str = str(timestamp) + method.upper() + request_path + body_str

        signature = base64.b64encode(
            hmac.new(
                secret_key.encode("utf-8"),
                sign_str.encode("utf-8"),
                hashlib.sha256
            ).digest()
        ).decode("utf-8")

        return signature

    def get_header(self, api_key, sign, timestamp):
        """
        Generate request headers for Poloniex API.

        Args:
            api_key: API key
            sign: Base64 encoded signature
            timestamp: Millisecond timestamp

        Returns:
            Dictionary of headers
        """
        headers = {
            "Content-Type": "application/json",
            "key": api_key,
            "signTimestamp": str(timestamp),
            "signature": sign,
        }
        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """
        Send HTTP request to Poloniex API.

        Args:
            path: Request path (e.g., "GET /markets/BTC_USDT/ticker24h")
            params: URL parameters (for GET requests)
            body: Request body (for POST/DELETE requests)
            extra_data: Extra data attached to response
            timeout: Request timeout in seconds

        Returns:
            RequestData object
        """
        if params is None:
            params = {}

        # Parse method and path
        parts = path.split(" ", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid path format: {path}")

        method = parts[0]
        endpoint = parts[1]

        # Build URL with query parameters for GET requests
        url = self._params.rest_url + endpoint
        request_path = endpoint

        if method == "GET" and params:
            query_string = urlencode(params)
            url += "?" + query_string
            request_path += "?" + query_string

        # Generate signature for private endpoints
        headers = {}
        if endpoint.startswith("/accounts/") or endpoint.startswith("/orders") or endpoint.startswith("/trades"):
            if self.public_key and self.private_key:
                timestamp = int(time.time() * 1000)
                sign = self.signature(timestamp, method, request_path, self.private_key, body)
                headers = self.get_header(self.public_key, sign, timestamp)
            else:
                raise ValueError("API keys required for private endpoints")

        # Add content-type header
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        # Check rate limit
        self._rate_limiter.acquire(endpoint)

        # Send request
        res = self.http_request(method, url, headers, body, timeout)
        return RequestData(res, extra_data)

    async def async_request(
        self, path, params=None, body=None, extra_data=None, timeout=5
    ) -> RequestData:
        """
        Send async HTTP request to Poloniex API.

        Args:
            path: Request path
            params: URL parameters
            body: Request body
            timeout: Request timeout in seconds
            extra_data: Extra data attached to response

        Returns:
            RequestData object
        """
        if params is None:
            params = {}

        parts = path.split(" ", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid path format: {path}")

        method = parts[0]
        endpoint = parts[1]

        url = self._params.rest_url + endpoint
        request_path = endpoint

        if method == "GET" and params:
            query_string = urlencode(params)
            url += "?" + query_string
            request_path += "?" + query_string

        headers = {}
        if endpoint.startswith("/accounts/") or endpoint.startswith("/orders") or endpoint.startswith("/trades"):
            if self.public_key and self.private_key:
                timestamp = int(time.time() * 1000)
                sign = self.signature(timestamp, method, request_path, self.private_key, body)
                headers = self.get_header(self.public_key, sign, timestamp)
            else:
                raise ValueError("API keys required for private endpoints")

        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        self._rate_limiter.acquire(endpoint)
        res = await self.async_http_request(method, url, headers, body, timeout)
        return RequestData(res, extra_data)

    def async_callback(self, future):
        """
        Callback function for async requests.

        Args:
            future: asyncio future object
        """
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.warn(f"async_callback::{e}")

    @staticmethod
    def _generic_normalize_function(input_data, extra_data):
        """
        Generic normalize function for Poloniex API responses.

        Args:
            input_data: Raw API response
            extra_data: Extra metadata

        Returns:
            Tuple of (normalized_data, status)
        """
        status = input_data is not None

        # Poloniex returns data directly in response body
        # Check for errors
        if isinstance(input_data, dict):
            code = input_data.get("code")
            if code and code != 200 and code != 0:
                return [], False

            # Extract data array if present
            if "data" in input_data:
                return input_data["data"], status

        return input_data, status
