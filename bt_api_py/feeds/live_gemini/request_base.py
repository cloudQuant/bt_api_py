import base64
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.gemini_exchange_data import GeminiExchangeDataSpot
from bt_api_py.error import GeminiErrorTranslator
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.logging_factory import get_logger
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitScope, RateLimitType


class GeminiRequestData(Feed):
    """Base class for all Gemini REST API requests"""

    @classmethod
    def _capabilities(cls) -> set[Capability]:
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.QUERY_ORDER,
            Capability.QUERY_OPEN_ORDERS,
            Capability.MARKET_STREAM,
            Capability.ACCOUNT_STREAM,
        }

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.public_key = kwargs.get("public_key") or kwargs.get("api_key")
        self.private_key = (
            kwargs.get("private_key") or kwargs.get("secret_key") or kwargs.get("api_secret")
        )
        self.exchange_name = kwargs.get("exchange_name", "GEMINI___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "gemini_spot_feed.log")
        self._params = GeminiExchangeDataSpot()
        self.request_logger = get_logger("gemini_spot_feed")
        self.async_logger = get_logger("gemini_spot_feed")
        self._error_translator = GeminiErrorTranslator()
        self._rate_limiter = kwargs.get("rate_limiter", self._create_default_rate_limiter())
        self._http_client = HttpClient(venue=self.exchange_name, timeout=10)

    @staticmethod
    def _create_default_rate_limiter():
        rules = [
            RateLimitRule(
                name="public_api",
                type=RateLimitType.SLIDING_WINDOW,
                limit=120,
                interval=60,
                scope=RateLimitScope.GLOBAL,
            ),
            RateLimitRule(
                name="private_api",
                type=RateLimitType.SLIDING_WINDOW,
                limit=600,
                interval=60,
                scope=RateLimitScope.GLOBAL,
            ),
            RateLimitRule(
                name="order",
                type=RateLimitType.SLIDING_WINDOW,
                limit=100,
                interval=60,
                scope=RateLimitScope.GLOBAL,
            ),
        ]
        return RateLimiter(rules)

    def _sign_request(self, path, params=None):
        """Generate HMAC SHA384 signature for Gemini API"""
        if params is None:
            params: dict[str, Any] = {}

        # Create payload with nonce (milliseconds timestamp)
        payload = {
            "request": path,
            "nonce": int(time.time() * 1000),
        }
        payload.update(params)

        # Convert to JSON and base64 encode
        payload_json = json.dumps(payload, separators=(",", ":"))
        payload_b64 = base64.b64encode(payload_json.encode("utf-8"))

        # Generate signature
        signature = hmac.new(
            self.private_key.encode("utf-8"),
            payload_b64,
            hashlib.sha384,
        ).hexdigest()

        return payload_b64, signature

    def _build_headers(self, path, params=None):
        """Build request headers with authentication"""
        payload_b64, signature = self._sign_request(path, params)

        headers = {
            "X-GEMINI-APIKEY": self.public_key,
            "X-GEMINI-PAYLOAD": payload_b64.decode("utf-8"),
            "X-GEMINI-SIGNATURE": signature,
            "Content-Type": "text/plain",
            "Content-Length": "0",
            "Cache-Control": "no-cache",
        }

        return headers

    def _normalize_response(self, response, extra_data):
        """Normalize API response to standard format"""
        status = response is not None

        if extra_data and "normalize_function" in extra_data:
            return extra_data["normalize_function"](response, extra_data)

        return response, status

    def request(self, path, method="POST", params=None, data=None, extra_data=None):
        """Make authenticated request to Gemini API"""
        if method == "POST":
            # For POST requests, payload is in headers
            headers = self._build_headers(path, params)
            url = f"{self._params.rest_url}{path}"

            with self._rate_limiter:
                response = self.http_request(
                    method=method,
                    url=url,
                    headers=headers,
                    body=data,
                )

                normalized_response, status = self._normalize_response(response, extra_data)

                self._log_request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=data,
                    response=normalized_response,
                    status=status,
                    extra_data=extra_data,
                )

                return normalized_response

        elif method == "GET":
            # For GET requests, params go in URL
            if params:
                query_string = urlencode(params)
                url = f"{self._params.rest_url}{path}?{query_string}"
            else:
                url = f"{self._params.rest_url}{path}"

            headers = {
                "Content-Type": "application/json",
            }

            with self._rate_limiter:
                response = self.http_request(
                    method=method,
                    url=url,
                    headers=headers,
                    body=data,
                )

                normalized_response, status = self._normalize_response(response, extra_data)

                self._log_request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=data,
                    response=normalized_response,
                    status=status,
                    extra_data=extra_data,
                )

                return normalized_response

    def _log_request(self, method, url, headers, data, response, status, extra_data):
        """Log request details"""
        log_data = {
            "method": method,
            "url": url,
            "status": status,
            "exchange": "gemini",
            "asset_type": self.asset_type,
        }

        if extra_data:
            log_data.update(extra_data)

        self.request_logger.info(
            "API Request",
            extra={
                "log_data": log_data,
                "response": response,
            },
        )

    # ==================== Public API Methods ====================

    def get_symbols(self, extra_data=None, **kwargs):
        """Get all available symbols"""
        request_type = "get_symbols"
        path = self._params.get_rest_path(request_type)

        data = self.request(path, method="GET", extra_data=extra_data)
        return data

    def get_symbol_details(self, symbol, extra_data=None, **kwargs):
        """Get details for a specific symbol"""
        request_type = "get_symbol_details"
        path = self._params.get_rest_path(request_type).format(symbol=symbol)

        data = self.request(path, method="GET", extra_data=extra_data)
        return data

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker for a specific symbol"""
        request_type = "get_ticker"
        path = self._params.get_rest_path(request_type).format(symbol=symbol)

        data = self.request(path, method="GET", extra_data=extra_data)
        return data

    def get_depth(self, symbol, limit_bids=50, limit_asks=50, extra_data=None, **kwargs):
        """Get order book for a symbol"""
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type).format(symbol=symbol)
        params = {
            "limit_bids": limit_bids,
            "limit_asks": limit_asks,
        }

        data = self.request(path, method="GET", params=params, extra_data=extra_data)
        return data

    def get_trades(self, symbol, limit_trades=50, extra_data=None, **kwargs):
        """Get recent trades for a symbol"""
        request_type = "get_trades"
        path = self._params.get_rest_path(request_type).format(symbol=symbol)
        params = {
            "limit_trades": limit_trades,
        }

        data = self.request(path, method="GET", params=params, extra_data=extra_data)
        return data

    def get_kline(self, symbol, time_frame, extra_data=None, **kwargs):
        """Get kline/candlestick data"""
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type).format(symbol=symbol, time_frame=time_frame)

        data = self.request(path, method="GET", extra_data=extra_data)
        return data

    def get_price_feed(self, extra_data=None, **kwargs):
        """Get all price feed data"""
        request_type = "get_price_feed"
        path = self._params.get_rest_path(request_type)

        data = self.request(path, method="GET", extra_data=extra_data)
        return data

    def get_system_time(self, extra_data=None, **kwargs):
        """Get current system time"""
        request_type = "get_system_time"
        path = self._params.get_rest_path(request_type)

        data = self.request(path, method="GET", extra_data=extra_data)
        return data

    # ==================== Private API Methods ====================

    def get_balance(self, extra_data=None, **kwargs):
        """Get account balance"""
        request_type = "get_balance"
        path = self._params.get_rest_path(request_type)

        data = self.request(path, method="POST", extra_data=extra_data)
        return data

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders"""
        request_type = "get_open_orders"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol

        data = self.request(path, method="POST", params=params, extra_data=extra_data)
        return data

    def get_order_history(self, symbol=None, limit_trades=50, extra_data=None, **kwargs):
        """Get order history"""
        request_type = "get_order_history"
        path = self._params.get_rest_path(request_type)
        params = {
            "symbol": symbol,
            "limit_trades": limit_trades,
        }

        data = self.request(path, method="POST", params=params, extra_data=extra_data)
        return data

    def make_order(
        self,
        symbol,
        side,
        amount,
        price,
        order_type="exchange limit",
        client_order_id=None,
        options=None,
        extra_data=None,
        **kwargs,
    ):
        """Place a new order"""
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)
        params = {
            "symbol": symbol,
            "amount": str(amount),
            "price": str(price),
            "side": side,
            "type": order_type,
        }

        if client_order_id:
            params["client_order_id"] = client_order_id
        if options:
            params["options"] = options

        data = self.request(path, method="POST", params=params, extra_data=extra_data)
        return data

    def cancel_order(self, order_id, extra_data=None, **kwargs):
        """Cancel an order"""
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)
        params = {
            "order_id": order_id,
        }

        data = self.request(path, method="POST", params=params, extra_data=extra_data)
        return data

    def cancel_all_orders(self, symbol=None, extra_data=None, **kwargs):
        """Cancel all orders"""
        request_type = "cancel_orders"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol

        data = self.request(path, method="POST", params=params, extra_data=extra_data)
        return data

    def query_order(self, order_id, extra_data=None, **kwargs):
        """Query order status"""
        request_type = "query_order"
        path = self._params.get_rest_path(request_type)
        params = {
            "order_id": order_id,
        }

        data = self.request(path, method="POST", params=params, extra_data=extra_data)
        return data

    def get_transfers(self, extra_data=None, **kwargs):
        """Get transfer history"""
        request_type = "get_transfers"
        path = self._params.get_rest_path(request_type)

        data = self.request(path, method="POST", extra_data=extra_data)
        return data

    async def async_request(self, path, method="GET", params=None, extra_data=None, timeout=5):
        """Async HTTP request for Gemini API."""
        if method == "POST":
            headers = self._build_headers(path, params)
            url = f"{self._params.rest_url}{path}"
        else:
            if params:
                from urllib.parse import urlencode

                query_string = urlencode(params)
                url = f"{self._params.rest_url}{path}?{query_string}"
            else:
                url = f"{self._params.rest_url}{path}"
            headers = {"Content-Type": "application/json"}
        try:
            response = await self._http_client.async_request(
                method=method,
                url=url,
                headers=headers,
            )
            normalized, status = self._normalize_response(response, extra_data)
            return normalized
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

    def push_data_to_queue(self, data):
        """Push data to the queue."""
        if self.data_queue is not None:
            self.data_queue.put(data)

    def _get_server_time(self, extra_data=None, **kwargs):
        """Prepare server time request. Returns (path, params, extra_data)."""
        path = self._params.get_rest_path("get_system_time")
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": "get_server_time",
                "normalize_function": self._get_server_time_normalize_function,
            }
        )
        return path, {}, extra_data

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time."""
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return self.request(path, method="GET", params=params, extra_data=extra_data)

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        if not input_data:
            return None, False
        if isinstance(input_data, dict):
            ts = input_data.get("millis", input_data.get("epoch"))
            return ts, True
        return input_data, True
