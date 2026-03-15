"""
HTX REST API request base class.
Handles authentication, signing, and all REST API methods.
"""

import base64
import hashlib
import hmac
import urllib.parse
from datetime import datetime
from typing import Any

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.errors.error_framework_htx import HtxErrorTranslator
from bt_api_py.exceptions import QueueNotInitializedError
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitScope, RateLimitType


class HtxRequestData(Feed):
    """HTX REST API base request class with HMAC SHA256 authentication."""

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
        self.account_id = kwargs.get("account_id")  # HTX requires account ID for trading
        self.topics = kwargs.get("topics", {})
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "htx_feed.log")
        from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeData

        self._params = HtxExchangeData()
        self.request_logger = get_logger("request")
        self.async_logger = get_logger("async_request")
        self._error_translator = HtxErrorTranslator()
        self._rate_limiter = kwargs.get("rate_limiter", self._create_default_rate_limiter())

    def translate_error(self, raw_response):
        """Translate HTX API error response to UnifiedError."""
        if isinstance(raw_response, dict):
            # HTX uses 'status' field: 'ok' for success, 'error' for failure
            status = raw_response.get("status", "")
            err_code = raw_response.get("err-code", "")
            if status != "ok" and err_code:
                return self._error_translator.translate(raw_response, self.exchange_name)
        return None

    @staticmethod
    def _create_default_rate_limiter():
        """Create default rate limiter for HTX API."""
        rules = [
            RateLimitRule(
                name="htx_public",
                limit=100,
                interval=10,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.GLOBAL,
            ),
            RateLimitRule(
                name="htx_private",
                limit=100,
                interval=10,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.GLOBAL,
            ),
            RateLimitRule(
                name="htx_trade",
                limit=100,
                interval=2,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.GLOBAL,
            ),
        ]
        return RateLimiter(rules)

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            raise QueueNotInitializedError("data_queue not initialized")

    # noinspection PyMethodMayBeStatic
    def create_signature(self, method, host, path, params):
        """Generate HTX API signature.

        HTX signature format:
        HTTPMethod + "\n" + host + "\n" + path + "\n" + params

        Args:
            method: HTTP method (GET, POST)
            host: API host (e.g., api.huobi.pro)
            path: Request path
            params: Query parameters dict

        Returns:
            Base64 encoded signature
        """
        # Sort parameters alphabetically
        sorted_params = sorted(params.items())
        encoded_params = urllib.parse.urlencode(sorted_params)

        # Build signature string
        payload = f"{method}\n{host}\n{path}\n{encoded_params}"

        # HMAC SHA256 signature
        signature = hmac.new(
            self.private_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).digest()

        # Base64 encoding
        signature = base64.b64encode(signature).decode()

        return signature

    def get_signed_params(self, method, path, params=None):
        """Generate signed parameters for authenticated requests.

        Args:
            method: HTTP method
            path: Request path
            params: Query parameters

        Returns:
            dict: Parameters with signature added
        """
        if params is None:
            params: dict[str, Any] = {}

        # Add required authentication parameters
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        params.update(
            {
                "AccessKeyId": self.public_key,
                "SignatureMethod": "HmacSHA256",
                "SignatureVersion": "2",
                "Timestamp": timestamp,
            }
        )

        # Generate signature
        host = self._params.rest_url.replace("https://", "").replace("http://", "")
        signature = self.create_signature(method, host, path, params)
        params["Signature"] = signature

        return params

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request function.

        Args:
            path: Request path (e.g., "GET /v1/order/orders")
            params: URL query parameters
            body: Request body (for POST requests)
            extra_data: Extra data for processing
            timeout: Request timeout in seconds

        Returns:
            RequestData: Response data
        """
        if params is None:
            params: dict[str, Any] = {}

        # Split method and path
        parts = path.split(" ", 1)
        if len(parts) == 2:
            method, request_path = parts
        else:
            method = "GET"
            request_path = path

        # Build URL
        url = f"{self._params.rest_url}{request_path}"

        # Determine if request needs authentication
        is_public = (
            "/market" in request_path
            or request_path.startswith("/v1/common")
            or request_path.startswith("/heartbeat")
            or request_path.endswith("_contract_info")
            or request_path.endswith("_index")
            or request_path.endswith("_open_interest")
        )

        if not is_public:
            # Add signature for private endpoints
            params = self.get_signed_params(method, request_path, params)

        # Build query string
        if params:
            query_string = urllib.parse.urlencode(sorted(params.items()))
            url = f"{url}?{query_string}"

        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "bt_api_py/1.0",
        }

        # Make request
        if method == "GET":
            res = self.http_request(method, url, headers, None, timeout)
        elif method == "POST":
            res = self.http_request(method, url, headers, body, timeout)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        return RequestData(res, extra_data)

    async def async_request(
        self, path, params=None, body=None, extra_data=None, timeout=5
    ) -> RequestData:
        """Async HTTP request function.

        Args:
            path: Request path
            params: URL query parameters
            body: Request body
            extra_data: Extra data for processing
            timeout: Request timeout in seconds

        Returns:
            RequestData: Response data
        """
        if params is None:
            params: dict[str, Any] = {}

        # Split method and path
        parts = path.split(" ", 1)
        if len(parts) == 2:
            method, request_path = parts
        else:
            method = "GET"
            request_path = path

        # Build URL
        url = f"{self._params.rest_url}{request_path}"

        # Determine if request needs authentication
        is_public = (
            "/market" in request_path
            or request_path.startswith("/v1/common")
            or request_path.startswith("/heartbeat")
            or request_path.endswith("_contract_info")
            or request_path.endswith("_index")
            or request_path.endswith("_open_interest")
        )

        if not is_public:
            # Add signature for private endpoints
            params = self.get_signed_params(method, request_path, params)

        # Build query string
        if params:
            query_string = urllib.parse.urlencode(sorted(params.items()))
            url = f"{url}?{query_string}"

        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "bt_api_py/1.0",
        }

        # Make async request
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
    def _generic_normalize_function(input_data, extra_data):
        """Generic normalize function for HTX API responses.

        HTX response format:
        {
            "status": "ok",
            "data": [...],
            "ch": "channel",
            "ts": timestamp
        }
        """
        if input_data is None:
            return [], False

        # Check status
        status = input_data.get("status") == "ok"

        # Extract data
        data = input_data.get("data", [])

        return data, status
