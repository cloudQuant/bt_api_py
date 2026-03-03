"""
Upbit REST API request base class.
Handles JWT authentication, signing, and all REST API methods.
"""

import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import urlencode, unquote

import jwt

from bt_api_py.containers.exchanges.upbit_exchange_data import UpbitExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.error_framework import UpbitErrorTranslator
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitScope, RateLimitType

logger = SpdLogManager(
    file_name="upbit_request_base.log", logger_name="upbit_request", print_info=False
).create_logger()


class UpbitRequestDataSpot(RequestData):
    """Upbit Spot Trading Request Data

    Handles JWT authentication, request signing, and rate limiting for Upbit API calls.
    """

    # ── 类属性 ─────────────────────────────────────────────────────
    exchange_name = "UPBIT___SPOT"
    asset_type = "spot"
    capabilities = [
        Capability.GET_TICK,
        Capability.GET_DEPTH,
        Capability.GET_KLINE,
        Capability.MAKE_ORDER,
        Capability.CANCEL_ORDER,
        Capability.QUERY_ORDER,
        Capability.QUERY_OPEN_ORDERS,
        Capability.GET_BALANCE,
        Capability.GET_ACCOUNT,
        Capability.GET_EXCHANGE_INFO,
        Capability.GET_SERVER_TIME,
    ]

    def __init__(self, config: dict = None):
        # Initialize with empty data and extra_data
        extra_data = {
            "exchange_name": self.exchange_name,
            "symbol_name": "",
            "asset_type": self.asset_type,
            "request_type": ""
        }
        super().__init__([], extra_data)
        self.exchange_data = UpbitExchangeDataSpot()
        self.error_translator = UpbitErrorTranslator()

        # Initialize rate limiter
        self._init_rate_limiter()

        # JWT credentials
        self.access_key = None
        self.secret_key = None
        self._load_credentials()

    def _init_rate_limiter(self):
        """Initialize Upbit-specific rate limiter"""
        rules = [
            # Public requests: 900 requests/second per IP
            RateLimitRule(
                name="public_requests",
                limit=900,
                interval=60,
                scope=RateLimitScope.GLOBAL,
                weight=1,
            ),
            # Private requests: 30 requests/second per API key
            RateLimitRule(
                name="private_requests",
                limit=30,
                interval=60,
                scope=RateLimitScope.ACCOUNT,
                weight=1,
            ),
            # Order requests: 8 requests/second per API key
            RateLimitRule(
                name="order_requests",
                limit=8,
                interval=1,
                scope=RateLimitScope.ACCOUNT,
                weight=1,
                endpoint_pattern=r"/v1/orders",
            ),
            # Quotation API: 1800 requests/minute per IP
            RateLimitRule(
                name="quotation",
                limit=1800,
                interval=60,
                scope=RateLimitScope.GLOBAL,
                weight=1,
            ),
        ]
        self.rate_limiter = RateLimiter(rules=rules)

    def _load_credentials(self):
        """Load API credentials from config"""
        if self.config:
            self.access_key = self.config.get("access_key")
            self.secret_key = self.config.get("secret_key")

    def _generate_jwt_token(self, params=None):
        """Generate JWT token for authentication"""
        if not self.access_key or not self.secret_key:
            raise ValueError("API credentials not provided")

        nonce = str(uuid.uuid4())

        if params:
            # For requests with parameters
            query_string = unquote(urlencode(params, doseq=True))
            query_hash = hashlib.sha512(query_string.encode()).hexdigest()
            payload = {
                "access_key": self.access_key,
                "nonce": nonce,
                "query_hash": query_hash,
                "query_hash_alg": "SHA512",
            }
        else:
            # For requests without parameters
            payload = {
                "access_key": self.access_key,
                "nonce": nonce,
            }

        return jwt.encode(payload, self.secret_key, algorithm="HS256")

    def _make_request(self, method, path, params=None, data=None):
        """Make authenticated request to Upbit API"""
        url = f"{self.exchange_data.rest_url}{path}"

        # Set headers
        headers = {}
        if self.access_key:
            token = self._generate_jwt_token(params)
            headers["Authorization"] = f"Bearer {token}"

        # Apply rate limiting
        if self.access_key:
            if path.startswith("/v1/orders"):
                self.rate_limiter.wait("order_requests")
            else:
                self.rate_limiter.wait("private_requests")
        else:
            self.rate_limiter.wait("public_requests")

        # Make request
        try:
            if method == "GET":
                response = self._session.get(url, headers=headers, params=params)
            elif method == "POST":
                response = self._session.post(url, headers=headers, json=data)
            elif method == "DELETE":
                response = self._session.delete(url, headers=headers, params=params)
            else:
                raise ValueError(f"Unsupported method: {method}")

            response.raise_for_status()
            return response.json()

        except Exception as e:
            logger.error(f"Request failed: {method} {path} - {e}")
            self._handle_error(e)
            raise

    def _handle_error(self, error):
        """Handle and translate errors"""
        # Implement error translation based on Upbit error codes
        if hasattr(error, 'response') and error.response is not None:
            error_data = error.response.json()
            if "error" in error_data:
                error_info = error_data["error"]
                raise self.error_translator.translate_error(error_info)
        raise error

    # ── 市场数据 API ────────────────────────────────────────────────

    @RateLimiter.ratelimit("quotation")
    def get_market_all(self, is_details=True):
        """Get all market information"""
        params = {"isDetails": "true"} if is_details else None
        return self._make_request("GET", self.exchange_data.get_rest_path("market_all"), params)

    @RateLimiter.ratelimit("quotation")
    def get_ticker(self, markets):
        """Get ticker information for specified markets"""
        if isinstance(markets, str):
            markets = markets.split(",")
        params = {"markets": ",".join(markets)}
        return self._make_request("GET", self.exchange_data.get_rest_path("ticker"), params)

    @RateLimiter.ratelimit("quotation")
    def get_orderbook(self, markets):
        """Get orderbook information for specified markets"""
        if isinstance(markets, str):
            markets = markets.split(",")
        params = {"markets": ",".join(markets)}
        return self._make_request("GET", self.exchange_data.get_rest_path("orderbook"), params)

    @RateLimiter.ratelimit("quotation")
    def get_trades_ticks(self, market, count=1, to=None):
        """Get recent trade ticks"""
        params = {"market": market, "count": min(count, 500)}
        if to:
            params["to"] = to
        return self._make_request("GET", self.exchange_data.get_rest_path("trades_ticks"), params)

    @RateLimiter.ratelimit("quotation")
    def get_candles_days(self, market, count=200):
        """Get daily candles"""
        params = {"market": market, "count": min(count, 200)}
        return self._make_request("GET", self.exchange_data.get_rest_path("candles_days"), params)

    @RateLimiter.ratelimit("quotation")
    def get_candles_minutes(self, market, unit, count=200):
        """Get minute candles"""
        unit_map = {
            "1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30,
            "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
            "1d": 1440  # Daily candles using minutes endpoint
        }

        actual_unit = unit_map.get(unit, unit)
        path = self.exchange_data.get_rest_path("candles_minutes").format(unit=actual_unit)
        params = {"market": market, "count": min(count, 200)}
        return self._make_request("GET", path, params)

    @RateLimiter.ratelimit("quotation")
    def get_candles_weeks(self, market, count=200):
        """Get weekly candles"""
        params = {"market": market, "count": min(count, 200)}
        return self._make_request("GET", self.exchange_data.get_rest_path("candles_weeks"), params)

    @RateLimiter.ratelimit("quotation")
    def get_candles_months(self, market, count=200):
        """Get monthly candles"""
        params = {"market": market, "count": min(count, 200)}
        return self._make_request("GET", self.exchange_data.get_rest_path("candles_months"), params)

    # ── 交易 API ──────────────────────────────────────────────────

    @RateLimiter.ratelimit("order_requests")
    def place_order(self, market, side, ord_type, volume=None, price=None, identifier=None):
        """Place an order"""
        params = {
            "market": market,
            "side": side,  # "bid" for buy, "ask" for sell
            "ord_type": ord_type,  # "limit", "market", "price", "best"
        }

        # Conditionally add parameters based on order type
        if ord_type in ["limit", "market"]:
            if not volume:
                raise ValueError("Volume is required for limit/market orders")
            params["volume"] = str(volume)

        if ord_type in ["limit", "price"]:
            if not price:
                raise ValueError("Price is required for limit/price orders")
            params["price"] = str(price)

        if identifier:
            params["identifier"] = identifier

        return self._make_request("POST", self.exchange_data.get_rest_path("order_post"), data=params)

    @RateLimiter.ratelimit("order_requests")
    def cancel_order(self, uuid=None, identifier=None):
        """Cancel an order"""
        if not uuid and not identifier:
            raise ValueError("Either uuid or identifier must be provided")

        params = {}
        if uuid:
            params["uuid"] = uuid
        if identifier:
            params["identifier"] = identifier

        return self._make_request("DELETE", self.exchange_data.get_rest_path("order_delete"), params)

    @RateLimiter.ratelimit("private_requests")
    def get_order(self, uuid=None, identifier=None):
        """Get order information"""
        params = {}
        if uuid:
            params["uuid"] = uuid
        if identifier:
            params["identifier"] = identifier

        return self._make_request("GET", self.exchange_data.get_rest_path("order"), params)

    @RateLimiter.ratelimit("private_requests")
    def get_orders(self, market=None, state=None, page=None, limit=100, order_by="desc"):
        """Get order list"""
        params = {}
        if market:
            params["market"] = market
        if state:
            params["state"] = state  # "wait", "watch", "done", "cancel"
        if page:
            params["page"] = str(page)
        params["limit"] = str(limit)
        params["order_by"] = order_by

        return self._make_request("GET", self.exchange_data.get_rest_path("orders"), params)

    # ── 账户管理 API ──────────────────────────────────────────────

    @RateLimiter.ratelimit("private_requests")
    def get_accounts(self):
        """Get account information"""
        return self._make_request("GET", self.exchange_data.get_rest_path("accounts"))

    @RateLimiter.ratelimit("private_requests")
    def get_api_keys(self):
        """Get API key information"""
        return self._make_request("GET", self.exchange_data.get_rest_path("api_keys"))

    @RateLimiter.ratelimit("private_requests")
    def get_wallet_status(self):
        """Get deposit/withdrawal status"""
        return self._make_request("GET", self.exchange_data.get_rest_path("status_wallet"))

    # ── 便捷方法 ──────────────────────────────────────────────────

    def get_symbol_info(self):
        """Get all symbol information"""
        return self.get_market_all(is_details=True)

    def get_kline(self, symbol, timeframe, count=200):
        """Get kline data with timeframe alias support"""
        timeframe_map = {
            "1m": "1m", "3m": "3m", "5m": "5m", "10m": "10m", "15m": "15m", "30m": "30m",
            "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "8h": "8h", "12h": "12h",
            "1d": "1d", "3d": "3d", "1w": "1w", "1M": "1M"
        }

        actual_tf = timeframe_map.get(timeframe, timeframe)

        if actual_tf.endswith("m"):
            unit = int(actual_tf[:-1])
            return self.get_candles_minutes(symbol, unit, count)
        elif actual_tf == "1d":
            return self.get_candles_days(symbol, count)
        elif actual_tf == "3d":
            return self.get_candles_days(symbol, count * 3)  # Approximate
        elif actual_tf == "1w":
            return self.get_candles_weeks(symbol, count)
        elif actual_tf == "1M":
            return self.get_candles_months(symbol, count)
        else:
            raise ValueError(f"Unsupported timeframe: {timeframe}")