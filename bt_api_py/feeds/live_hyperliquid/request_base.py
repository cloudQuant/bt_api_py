"""
Hyperliquid REST API request base class.

Handles authentication, signing, and all REST API methods for Hyperliquid.
Hyperliquid uses EIP-712 signatures for authenticated requests.
"""

from typing import Any

import requests
from eth_account import Account

from bt_api_py.containers.exchanges.hyperliquid_exchange_data import (
    HyperliquidExchangeDataSpot,
    HyperliquidExchangeDataSwap,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.error import ErrorTranslator
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitScope, RateLimitType


class HyperliquidErrorTranslator(ErrorTranslator):
    """Translate Hyperliquid API errors to unified error codes"""

    @staticmethod
    def translate_error(error_msg):
        """Translate Hyperliquid error to unified error code"""
        error_msg = error_msg.lower()

        if "invalid signature" in error_msg:
            return ("INVALID_SIGNATURE", 2002)
        elif "insufficient margin" in error_msg:
            return ("INSUFFICIENT_MARGIN", 4005)
        elif "order not found" in error_msg:
            return ("ORDER_NOT_FOUND", 4006)
        elif "rate limit" in error_msg or "too many requests" in error_msg:
            return ("RATE_LIMIT_EXCEEDED", 3001)
        elif "price slippage" in error_msg:
            return ("PRICE_SLIPPAGE", 4003)
        elif "min trade size" in error_msg:
            return ("MIN_NOTIONAL", 4014)
        elif "invalid api key" in error_msg:
            return ("INVALID_API_KEY", 2001)
        elif "permission denied" in error_msg:
            return ("PERMISSION_DENIED", 2004)
        else:
            return ("UNKNOWN_ERROR", 5000)


class HyperliquidRequestData(Feed):
    """Base class for Hyperliquid API requests"""

    @classmethod
    def _capabilities(cls) -> set[Capability]:
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.QUERY_ORDER,
            Capability.QUERY_OPEN_ORDERS,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MARKET_STREAM,
            Capability.ACCOUNT_STREAM,
        }

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)

        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "hyperliquid_feed.log")
        params = kwargs.get("exchange_data")
        if params is None:
            params = (
                HyperliquidExchangeDataSpot()
                if self.asset_type == "SPOT"
                else HyperliquidExchangeDataSwap()
            )
        self._params: HyperliquidExchangeDataSpot | HyperliquidExchangeDataSwap = params

        self.request_logger = get_logger("hyperliquid_feed")
        self.async_logger = get_logger("hyperliquid_feed")

        # Rate limiting for Hyperliquid
        self.rate_limiter = RateLimiter(
            rules=[
                RateLimitRule(
                    name="hyperliquid_general",
                    limit=1200,
                    interval=60,
                    type=RateLimitType.SLIDING_WINDOW,
                    scope=RateLimitScope.IP,
                    endpoint="/info",
                )
            ]
        )

        # API key and private key for authentication
        self.api_key = kwargs.get("api_key", "")
        self.private_key = kwargs.get("private_key", "")
        self.address = None

        if self.private_key:
            try:
                self.account = Account.from_key(self.private_key)
                self.address = self.account.address
            except Exception as e:
                self.request_logger.error(f"Invalid private key: {e}")

        # Error translator
        self.error_translator = HyperliquidErrorTranslator()

    def request(self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=False):
        """HTTP request function following the standard pattern.

        Hyperliquid uses POST for all requests with JSON body.

        Args:
            path (str): Request path (e.g. '/info' or '/exchange')
            params (dict, optional): Not used for Hyperliquid (uses body instead)
            body (dict, optional): JSON body for POST request
            extra_data (dict, optional): Extra data for RequestData
            timeout (int, optional): Request timeout in seconds
            is_sign (bool, optional): Whether this is a signed request

        Returns:
            RequestData: Response data container
        """
        if extra_data is None:
            extra_data = {}
        if body is None:
            body = {}

        url = self._params.rest_url + path
        headers = {"Content-Type": "application/json", "User-Agent": "bt_api_py/1.0"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        response = self.http_request("POST", url, headers, body, timeout)
        return RequestData(response, extra_data)

    async def async_request(
        self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=False
    ):
        """Async HTTP request function."""
        if extra_data is None:
            extra_data = {}
        if body is None:
            body = {}

        url = self._params.rest_url + path
        headers = {"Content-Type": "application/json", "User-Agent": "bt_api_py/1.0"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        response = self.http_request("POST", url, headers, body, timeout)
        self.async_logger.info(f"Async Request: POST {url}")
        return RequestData(response, extra_data)

    def async_callback(self, future):
        """Callback function for async requests."""
        try:
            result = future.result()
            self.data_queue.put(result)
        except Exception as e:
            self.async_logger.warning(f"async_callback::{e}")

    def _make_request(self, request_type, **kwargs):
        """Make HTTP request to Hyperliquid API (legacy helper)"""
        headers = {"Content-Type": "application/json", "User-Agent": "bt_api_py/1.0"}

        if self.api_key:
            headers["X-API-Key"] = self.api_key

        url = self._params.rest_url + self._params.get_rest_path(request_type)

        try:
            response = requests.post(url, json=kwargs, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            self.request_logger.error(f"Request failed: {e}")
            return {"status": "error", "message": str(e)}

    def _get_request_data(self, data, extra_data):
        """Create RequestData object"""
        return RequestData(data, extra_data)

    # ── Standard Interface: get_tick ──────────────────────────────

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Prepare tick request parameters. Returns (path, body, extra_data)."""
        if extra_data is None:
            extra_data = {}
        path = self._params.get_rest_path("get_all_mids")
        body = {"type": "allMids"}
        extra_data.update(
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "get_tick",
            }
        )
        return path, body, extra_data

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get latest tick price for symbol. Returns RequestData."""
        path, body, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra_data)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Async get tick price for symbol."""
        path, body, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=body, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ── Standard Interface: get_depth ────────────────────────────

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Prepare depth request parameters. Returns (path, body, extra_data)."""
        if extra_data is None:
            extra_data = {}
        path = self._params.get_rest_path("get_l2_book")
        coin = self._params.get_symbol(symbol)
        body = {"type": "l2Book", "coin": coin}
        extra_data.update(
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "get_depth",
            }
        )
        return path, body, extra_data

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth for symbol. Returns RequestData."""
        path, body, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra_data)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Async get depth for symbol."""
        path, body, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=body, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ── Standard Interface: get_kline ────────────────────────────

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Prepare kline request parameters. Returns (path, body, extra_data)."""
        if extra_data is None:
            extra_data = {}
        path = self._params.get_rest_path("get_candle_snapshot")
        coin = self._params.get_symbol(symbol)
        interval = self._params.kline_periods.get(period, period)
        req = {"coin": coin, "interval": interval}
        if "start_time" in kwargs and kwargs["start_time"]:
            req["startTime"] = kwargs["start_time"]
        if "end_time" in kwargs and kwargs["end_time"]:
            req["endTime"] = kwargs["end_time"]
        body = {"type": "candleSnapshot", "req": req}
        extra_data.update(
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "get_kline",
            }
        )
        return path, body, extra_data

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candle data for symbol. Returns RequestData."""
        path, body, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra_data)

    def async_get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Async get kline for symbol."""
        path, body, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=body, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ── Standard Interface: get_exchange_info ─────────────────────

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange metadata (asset list). Returns RequestData."""
        if extra_data is None:
            extra_data = {}
        path = self._params.get_rest_path("get_meta")
        body = {"type": "meta"}
        extra_data.update(
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": "get_exchange_info",
            }
        )
        return self.request(path, body=body, extra_data=extra_data)

    # ── Standard Interface: get_server_time ───────────────────────

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time. Hyperliquid doesn't have a dedicated endpoint;
        we use allMids as a health check and return local time."""
        if extra_data is None:
            extra_data = {}
        path = self._params.get_rest_path("get_all_mids")
        body = {"type": "allMids"}
        extra_data.update(
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": "get_server_time",
            }
        )
        return self.request(path, body=body, extra_data=extra_data)

    # ── Hyperliquid-specific: get_all_mids ───────────────────────

    def get_all_mids(self):
        """Get all mid prices"""
        request_type = "get_all_mids"
        body = {"type": "allMids"}

        result = self._make_request(request_type, **body)
        return self._get_request_data(
            result,
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": request_type,
            },
        )

    def get_meta(self):
        """Get metadata for all assets"""
        request_type = "get_meta"
        body = {"type": "meta"}

        result = self._make_request(request_type, **body)
        return self._get_request_data(
            result,
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": request_type,
            },
        )

    def get_spot_meta(self):
        """Get spot metadata"""
        request_type = "get_spot_meta"
        body = {"type": "spotMeta"}

        result = self._make_request(request_type, **body)
        return self._get_request_data(
            result,
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": request_type,
            },
        )

    def get_l2_book(self, coin, depth=5):
        """Get L2 order book"""
        request_type = "get_l2_book"
        body = {"type": "l2Book", "coin": coin, "level": 2}

        result = self._make_request(request_type, **body)
        return self._get_request_data(
            result,
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": coin,
                "asset_type": self.asset_type,
                "request_type": request_type,
            },
        )

    def get_candle_snapshot(self, coin, interval, start_time=None, end_time=None):
        """Get candle data"""
        request_type = "get_candle_snapshot"
        req = {"coin": coin, "interval": interval}

        if start_time:
            req["startTime"] = start_time
        if end_time:
            req["endTime"] = end_time

        body = {"type": "candleSnapshot", "req": req}

        result = self._make_request(request_type, **body)
        return self._get_request_data(
            result,
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": coin,
                "asset_type": self.asset_type,
                "request_type": request_type,
            },
        )

    def get_recent_trades(self, coin, limit=100):
        """Get recent trades"""
        request_type = "get_recent_trades"
        body = {"type": "recentTrades", "coin": coin, "limit": limit}

        result = self._make_request(request_type, **body)
        return self._get_request_data(
            result,
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": coin,
                "asset_type": self.asset_type,
                "request_type": request_type,
            },
        )

    def get_exchange_status(self):
        """Get exchange status"""
        request_type = "get_exchange_status"
        body = {"type": "exchangeStatus"}

        result = self._make_request(request_type, **body)
        return self._get_request_data(
            result,
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": request_type,
            },
        )

    def get_clearinghouse_state(self, user=None):
        """Get perpetual account state"""
        request_type = "get_clearinghouse_state"
        user = user or self.address

        if not user:
            raise ValueError("User address required for clearinghouse state")

        body = {"type": "clearinghouseState", "user": user}

        result = self._make_request(request_type, **body)
        return self._get_request_data(
            result,
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": request_type,
            },
        )

    def get_spot_clearinghouse_state(self, user=None):
        """Get spot account state"""
        request_type = "get_spot_clearinghouse_state"
        user = user or self.address

        if not user:
            raise ValueError("User address required for spot clearinghouse state")

        body = {"type": "spotClearinghouseState", "user": user}

        result = self._make_request(request_type, **body)
        return self._get_request_data(
            result,
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": request_type,
            },
        )

    def get_order_status(self, user, oid):
        """Get order status"""
        request_type = "get_order_status"
        body = {"type": "orderStatus", "user": user, "oid": oid}

        result = self._make_request(request_type, **body)
        return self._get_request_data(
            result,
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": request_type,
            },
        )

    def get_user_fills(self, user, limit=100):
        """Get user fills"""
        request_type = "get_user_fills"
        body = {"type": "userFills", "user": user, "limit": limit}

        result = self._make_request(request_type, **body)
        return self._get_request_data(
            result,
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": request_type,
            },
        )

    def get_user_funding(self, user):
        """Get user funding payments"""
        request_type = "get_user_funding"
        body = {"type": "userFunding", "user": user}

        result = self._make_request(request_type, **body)
        return self._get_request_data(
            result,
            {
                "exchange_name": self._params.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": request_type,
            },
        )

    def _make_signed_request(self, request_type, **kwargs):
        """Make signed request to /exchange endpoint"""
        if not self.account:
            raise ValueError("Private key required for signed requests")

        headers = {"Content-Type": "application/json", "User-Agent": "bt_api_py/1.0"}

        url = self._params.rest_url + self._params.get_rest_path(request_type)

        # Apply rate limiting (removed for now)
        # self.rate_limiter.wait_if_needed(request_type)

        try:
            response = requests.post(url, json=kwargs, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            self.request_logger.error(f"Signed request failed: {e}")
            return {"status": "error", "message": str(e)}
