"""
KuCoin REST API request base class.
Handles authentication, signing, and all REST API methods.
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from urllib import parse

from bt_api_py.containers.exchanges.kucoin_exchange_data import KuCoinExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.error import KuCoinErrorTranslator
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitScope, RateLimitType


class KuCoinRequestData(Feed):
    """KuCoin REST API base class with authentication and signing."""

    @classmethod
    def _capabilities(cls) -> set[Capability]:
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

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.public_key = kwargs.get("public_key")
        self.private_key = kwargs.get("private_key")
        self.passphrase = kwargs.get("passphrase")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "kucoin_spot_feed.log")
        self._params = KuCoinExchangeDataSpot()
        self.request_logger = get_logger("kucoin_spot_feed")
        self.async_logger = get_logger("kucoin_spot_feed")
        self._error_translator = KuCoinErrorTranslator()
        self._rate_limiter = kwargs.get("rate_limiter", self._create_default_rate_limiter())

    @staticmethod
    def _create_default_rate_limiter() -> Any | None:
        """Create default rate limiter for KuCoin API.

        KuCoin rate limits:
        - Private endpoints: 200 requests / 10s per API key
        - Trade endpoints: 45 requests / 3s per API key
        """
        rules = [
            RateLimitRule(
                name="kucoin_general",
                limit=200,
                interval=10,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.GLOBAL,
            ),
            RateLimitRule(
                name="kucoin_trade",
                limit=45,
                interval=3,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.ENDPOINT,
                endpoint="/api/v1/orders",
            ),
        ]
        return RateLimiter(rules)

    def translate_error(self, raw_response):
        """Translate KuCoin API error response to UnifiedError."""
        if isinstance(raw_response, dict):
            code = raw_response.get("code", "0")
            if str(code) != "200000":
                return self._error_translator.translate(raw_response, self.exchange_name)
        return None

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            assert 0, "Queue not initialized"

    # noinspection PyMethodMayBeStatic
    def signature(self, timestamp, method, request_path, secret_key, body=""):
        """Generate KuCoin API signature.

        Args:
            timestamp: Request timestamp in milliseconds
            method: HTTP method (GET, POST, DELETE)
            request_path: Request endpoint path with query string
            secret_key: API secret key
            body: Request body (empty string for GET/DELETE)

        Returns:
            Base64 encoded signature
        """
        message = str(timestamp) + method.upper() + request_path + body
        mac = hmac.new(
            bytes(secret_key, encoding="utf-8"),
            bytes(message, encoding="utf-8"),
            digestmod=hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode()

    # noinspection PyMethodMayBeStatic
    def get_encrypted_passphrase(self, passphrase, secret_key):
        """Encrypt passphrase using secret key.

        Args:
            passphrase: API passphrase
            secret_key: API secret key

        Returns:
            Base64 encoded encrypted passphrase
        """
        mac = hmac.new(
            bytes(secret_key, encoding="utf-8"),
            bytes(passphrase, encoding="utf-8"),
            digestmod=hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode()

    # noinspection PyMethodMayBeStatic
    def get_header(self, api_key, sign, timestamp, passphrase):
        """Generate request headers with authentication.

        Args:
            api_key: API key
            sign: Base64 encoded signature
            timestamp: Request timestamp in milliseconds
            passphrase: Encrypted passphrase

        Returns:
            Dictionary of request headers
        """
        return {
            "KC-API-KEY": api_key,
            "KC-API-SIGN": sign,
            "KC-API-TIMESTAMP": str(timestamp),
            "KC-API-PASSPHRASE": passphrase,
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json",
        }

    def request(self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=False):
        """HTTP request function.

        Args:
            path: Request path (format: "METHOD /api/v1/endpoint")
            params: URL query parameters
            body: Request body for POST requests
            extra_data: Extra data generated by user
            timeout: Request timeout in seconds
            is_sign: Whether to sign the request

        Returns:
            RequestData object
        """
        if params is None:
            params: dict[str, Any] = {}
        method, endpoint = path.split(" ", 1)

        # Build URL with query string
        query_string = parse.urlencode(params) if params else ""
        url = f"{self._params.rest_url}{endpoint}"
        if query_string:
            url = f"{url}?{query_string}"
            request_path = f"{endpoint}?{query_string}"
        else:
            request_path = endpoint

        headers = {}
        if is_sign:
            # Generate signature for authenticated requests
            timestamp = int(time.time() * 1000)
            body_str = json.dumps(body) if body is not None else ""
            signature_ = self.signature(timestamp, method, request_path, self.private_key, body_str)
            encrypted_passphrase = self.get_encrypted_passphrase(self.passphrase, self.private_key)
            headers = self.get_header(self.public_key, signature_, timestamp, encrypted_passphrase)

        res = self.http_request(method, url, headers, body, timeout)
        return RequestData(res, extra_data)

    async def async_request(
        self, path, params=None, body=None, extra_data=None, timeout=5, is_sign=False
    ) -> RequestData:
        """Async HTTP request function.

        Args:
            path: Request path
            params: URL query parameters
            body: Request body
            timeout: Request timeout in seconds
            extra_data: Extra data
            is_sign: Whether to sign the request

        Returns:
            RequestData object
        """
        if params is None:
            params: dict[str, Any] = {}
        method, endpoint = path.split(" ", 1)

        query_string = parse.urlencode(params) if params else ""
        url = f"{self._params.rest_url}{endpoint}"
        if query_string:
            url = f"{url}?{query_string}"
            request_path = f"{endpoint}?{query_string}"
        else:
            request_path = endpoint

        headers = {}
        if is_sign:
            timestamp = int(time.time() * 1000)
            body_str = json.dumps(body) if body is not None else ""
            signature_ = self.signature(timestamp, method, request_path, self.private_key, body_str)
            encrypted_passphrase = self.get_encrypted_passphrase(self.passphrase, self.private_key)
            headers = self.get_header(self.public_key, signature_, timestamp, encrypted_passphrase)

        res = await self.async_http_request(method, url, headers, body, timeout)
        return RequestData(res, extra_data)

    def async_callback(self, future):
        """Callback function for async requests.

        Args:
            future: asyncio future object
        """
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.warning(f"async_callback::{e}")

    @staticmethod
    def _generic_normalize_function(input_data, extra_data) -> Any | None:
        """Generic normalize function for KuCoin API responses.

        KuCoin API response format:
        {
            "code": "200000",
            "data": {...}
        }

        Args:
            input_data: Raw API response
            extra_data: Extra data containing metadata

        Returns:
            Tuple of (normalized_data, success_status)
        """
        if input_data is None:
            return None, False

        # Check for success code
        if isinstance(input_data, dict):
            code = input_data.get("code", "0")
            if str(code) == "200000":
                # Return data field if it exists
                if "data" in input_data:
                    return input_data["data"], True
                return input_data, True
            # Error response
            return None, False

        return input_data, False
