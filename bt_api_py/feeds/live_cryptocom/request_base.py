"""
Crypto.com REST API request base class.
Handles authentication, signing, and all REST API methods.
"""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import requests

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.containers.exchanges.cryptocom_exchange_data import CryptoComExchangeDataSpot
from bt_api_py.containers.orderbooks.cryptocom_orderbook import CryptoComOrderBook
from bt_api_py.containers.orders.cryptocom_order import CryptoComOrder
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.cryptocom_ticker import CryptoComTicker
from bt_api_py.containers.bars.bar import BarData
from bt_api_py.error_framework_cryptocom import CryptoComErrorTranslator
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitScope, RateLimitType


class CryptoComRequestData(Feed):
    """Crypto.com REST API feed implementation for Spot trading."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_TRADE,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.QUERY_ORDER,
            Capability.QUERY_OPEN_ORDERS,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_SERVER_TIME,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.public_key = kwargs.get("public_key")
        self.private_key = kwargs.get("private_key")
        self.logger_name = kwargs.get("logger_name", "cryptocom_spot_feed.log")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = CryptoComExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()
        self._error_translator = CryptoComErrorTranslator()
        self._rate_limiter = kwargs.get("rate_limiter", self._create_default_rate_limiter())
        # Initialize session for HTTP requests
        self._session = requests.Session()

    @staticmethod
    def _create_default_rate_limiter():
        rules = [
            RateLimitRule(
                name="public_api",
                type=RateLimitType.FIXED_WINDOW,
                interval=1,
                limit=100,
                scope=RateLimitScope.ENDPOINT,
                endpoint="/public*",
            ),
            RateLimitRule(
                name="create_order",
                type=RateLimitType.FIXED_WINDOW,
                interval=1,
                limit=15,
                scope=RateLimitScope.ENDPOINT,
                endpoint="*/create-order",
            ),
            RateLimitRule(
                name="cancel_order",
                type=RateLimitType.FIXED_WINDOW,
                interval=1,
                limit=15,
                scope=RateLimitScope.ENDPOINT,
                endpoint="*/cancel-order",
            ),
            RateLimitRule(
                name="get_order",
                type=RateLimitType.FIXED_WINDOW,
                interval=1,
                limit=30,
                scope=RateLimitScope.ENDPOINT,
                endpoint="*/get-order-detail",
            ),
        ]
        return RateLimiter(rules)

    def _build_param_string(self, params):
        """Build parameter string for signature."""
        if not params:
            return ""
        sorted_keys = sorted(params.keys())
        parts = []
        for key in sorted_keys:
            value = params[key]
            if isinstance(value, list):
                for item in value:
                    parts.append(f"{key}{item}")
            else:
                parts.append(f"{key}{value}")
        return "".join(parts)

    def _generate_signature(self, method, path, params, req_id):
        """Generate HMAC-SHA256 signature."""
        param_string = self._build_param_string(params)
        nonce = int(time.time() * 1000)
        sign_str = f"{method}{req_id}{self.public_key}{param_string}{nonce}"

        signature = hmac.new(
            self.private_key.encode('utf-8'),
            sign_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return {
            "id": req_id,
            "method": method,
            "api_key": self.public_key,
            "params": params or {},
            "sig": signature,
            "nonce": nonce,
        }

    def _make_request(self, method, path, params=None, is_public=False):
        """Make HTTP request to Crypto.com API."""
        req_id = int(time.time() * 1000)

        url = f"{self._params.rest_url}{path}"

        if is_public:
            resp = self._session.get(url, params=params)
        else:
            signature = self._generate_signature(method, path, params, req_id)
            resp = self._session.post(url, json=signature)

        return resp.json()

    def _process_response(self, response, method_name):
        """Process API response and handle errors."""
        if not response or "code" not in response:
            raise Exception(f"Crypto.com API error: Invalid response format")

        if response["code"] != 0:
            error_msg = response.get("message", "Unknown error")
            raise Exception(f"Crypto.com API error [{response['code']}]: {error_msg}")

        return response.get("result")

    def request(self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=False):
        """HTTP request function that returns RequestData.

        Args:
            path: Request URL path with method (e.g., "GET /public/get-server-time")
            params: URL parameters
            body: Request body
            extra_data: Extra data for RequestData
            timeout: Request timeout
            is_sign: Whether to sign the request

        Returns:
            RequestData object
        """
        if params is None:
            params = {}

        method, path = path.split(" ", 1)
        url = f"{self._params.rest_url}{path}"

        # Build URL with params
        if params:
            url = f"{url}?{urlencode(params)}"

        headers = {}
        req_body = None

        if not is_sign:
            # Public endpoint
            res = self.http_request(method, url, headers, req_body, timeout)
        else:
            # Private endpoint with signature
            req_id = int(time.time() * 1000)
            signature = self._generate_signature(method, path, body or params, req_id)
            headers["Content-Type"] = "application/json"
            res = self.http_request(method, url, headers, signature, timeout)

        return RequestData(res, extra_data)

    # ==================== Server Time ====================

    def _get_server_time(self, extra_data=None, **kwargs):
        """Get server time internal method."""
        request_type = "get_server_time"
        path = f"GET /public/get-server-time"
        params = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time."""
        path, params, extra_data = self._get_server_time(extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    # ==================== Ticker ====================

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker internal method."""
        request_type = "get_tick"
        request_symbol = self._params.get_symbol(symbol)
        path = f"GET /public/get-tickers"
        params = {"instrument_name": request_symbol}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": CryptoComRequestData._get_tick_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data from API response."""
        status = input_data is not None and input_data.get("code") == 0
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if not status or not input_data.get("result", {}).get("data"):
            return [], False

        data = []
        for t in input_data["result"]["data"]:
            ticker_data = {
                "a": str(t.get("a", "0")),  # last_price
                "b": str(t.get("b", "0")),  # best_bid
                "k": str(t.get("k", "0")),  # best_ask
                "h": str(t.get("h", "0")),  # high_24h
                "l": str(t.get("l", "0")),  # low_24h
                "v": str(t.get("v", "0")),  # volume_24h
                "vv": str(t.get("vv", "0")),  # quote_volume_24h
                "t": t.get("t", 0),  # timestamp
            }
            data.append(CryptoComTicker(ticker_data, symbol_name, asset_type, True))
        return data, status

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker for a specific symbol."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker for a specific symbol (alias for get_tick)."""
        return self.get_tick(symbol, extra_data, **kwargs)

    # ==================== Depth (Order Book) ====================

    def _get_depth(self, symbol, size=20, extra_data=None, **kwargs):
        """Get orderbook internal method."""
        request_type = "get_depth"
        request_symbol = self._params.get_symbol(symbol)
        depth = min(size, 50)  # Max 50 for Crypto.com
        path = f"GET /public/get-book"
        params = {"instrument_name": request_symbol, "depth": depth}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": CryptoComRequestData._get_depth_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize orderbook data from API response."""
        status = input_data is not None and input_data.get("code") == 0
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if not status or not input_data.get("result", {}).get("data"):
            return [], False

        orderbook_data = {"data": input_data["result"]["data"], "t": int(time.time() * 1000)}
        orderbook = CryptoComOrderBook.from_api_response(orderbook_data, symbol_name)
        return [orderbook], status

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get orderbook for a specific symbol."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    # ==================== Kline (Candlestick) ====================

    def _get_kline(self, symbol, period="1m", count=100, extra_data=None, **kwargs):
        """Get kline internal method."""
        request_type = "get_kline"
        request_symbol = self._params.get_symbol(symbol)
        kline_period = self._params.get_kline_period(period)
        path = f"GET /public/get-candlestick"
        params = {
            "instrument_name": request_symbol,
            "period": kline_period,
            "count": min(count, 300)
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": CryptoComRequestData._get_kline_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline data from API response."""
        status = input_data is not None and input_data.get("code") == 0
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if not status or not input_data.get("result", {}).get("data"):
            return [], False

        klines = []
        for c in input_data["result"]["data"]:
            kline_data = {
                "timestamp": float(c["t"]) / 1000,
                "open": float(c["o"]),
                "high": float(c["h"]),
                "low": float(c["l"]),
                "close": float(c["c"]),
                "volume": float(c["v"]),
            }
            klines.append(kline_data)
        return klines, status

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data for a specific symbol."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    # ==================== Exchange Info ====================

    def get_exchange_info(self):
        """Get exchange information and symbols."""
        response = self._make_request("GET", "/public/get-instruments", is_public=True)
        data = self._process_response(response, "get_exchange_info")

        if data and "data" in data:
            symbols = []
            for inst in data["data"]:
                symbols.append({
                    "symbol": inst["instrument_name"],
                    "base_currency": inst["base_ccy"],
                    "quote_currency": inst["quote_ccy"],
                    "price_decimals": inst["price_decimals"],
                    "quantity_decimals": inst["quantity_decimals"],
                    "status": inst["status"],
                })
            return symbols
        return []

    # ==================== Account & Trading ====================

    def get_account_summary(self):
        """Get account summary."""
        response = self._make_request("POST", "/private/get-account-summary")
        return self._process_response(response, "get_account_summary")

    def get_balance(self):
        """Get account balance."""
        summary = self.get_account_summary()
        if not summary or "data" not in summary:
            return []

        balances = []
        for acc in summary["data"]:
            if float(acc.get("total_balance", 0)) > 0:
                balances.append(BalanceData(acc, has_been_json_encoded=True))
        return balances

    def create_order(self, symbol, side, type, quantity=None, price=None,
                    notional=None, client_oid=None, time_in_force="GOOD_TILL_CANCEL"):
        """Create an order."""
        params = {
            "instrument_name": symbol,
            "side": side,
            "type": type,
        }

        if type == "MARKET" and side == "BUY":
            params["notional"] = str(notional)
        elif type == "MARKET" and side == "SELL":
            params["quantity"] = str(quantity)
        else:  # LIMIT orders
            params["quantity"] = str(quantity)
            params["price"] = str(price)

        if client_oid:
            params["client_oid"] = client_oid
        if time_in_force:
            params["time_in_force"] = time_in_force

        response = self._make_request("POST", "/private/create-order", params)
        data = self._process_response(response, "create_order")

        if data:
            return OrderData({
                "symbol": symbol,
                "order_id": data.get("order_id"),
                "client_order_id": client_oid,
                "side": side,
                "type": type,
                "quantity": quantity,
                "price": price,
                "status": data.get("status", "FILLED"),
                "filled_quantity": float(data.get("filled_quantity", 0)),
                "remaining_quantity": float(data.get("remaining_quantity", quantity or 0)),
                "timestamp": time.time(),
            })
        return None

    def cancel_order(self, symbol, order_id):
        """Cancel an order."""
        params = {
            "instrument_name": symbol,
            "order_id": order_id,
        }

        response = self._make_request("POST", "/private/cancel-order", params)
        data = self._process_response(response, "cancel_order")

        return data is not None

    def get_order(self, symbol, order_id):
        """Get order details."""
        params = {
            "instrument_name": symbol,
            "order_id": order_id,
        }

        response = self._make_request("POST", "/private/get-order-detail", params)
        data = self._process_response(response, "get_order")

        if data:
            return OrderData({
                "symbol": symbol,
                "order_id": data.get("order_id"),
                "client_order_id": data.get("client_oid"),
                "side": data.get("side"),
                "type": data.get("type"),
                "quantity": float(data.get("quantity", 0)),
                "price": float(data.get("price", 0)),
                "status": data.get("status"),
                "filled_quantity": float(data.get("filled_quantity", 0)),
                "remaining_quantity": float(data.get("remaining_quantity", 0)),
                "timestamp": time.time(),
            })
        return None

    def get_open_orders(self, symbol=None):
        """Get open orders."""
        params = {"instrument_name": symbol} if symbol else {}

        response = self._make_request("POST", "/private/get-open-orders", params)
        data = self._process_response(response, "get_open_orders")

        if data and "data" in data:
            orders = []
            for o in data["data"]:
                orders.append(OrderData({
                    "symbol": o["instrument_name"],
                    "order_id": o["order_id"],
                    "side": o["side"],
                    "type": o["type"],
                    "quantity": float(o["quantity"]),
                    "price": float(o["price"]),
                    "status": o["status"],
                    "filled_quantity": float(o.get("filled_quantity", 0)),
                    "remaining_quantity": float(o.get("remaining_quantity", 0)),
                    "timestamp": time.time(),
                }))
            return orders
        return []

    def get_order_history(self, symbol=None, count=100):
        """Get order history."""
        params = {"instrument_name": symbol, "count": min(count, 100)} if symbol else {"count": min(count, 100)}

        response = self._make_request("POST", "/private/get-order-history", params)
        data = self._process_response(response, "get_order_history")

        if data and "data" in data:
            orders = []
            for o in data["data"]:
                orders.append(OrderData({
                    "symbol": o["instrument_name"],
                    "order_id": o["order_id"],
                    "client_order_id": o.get("client_oid"),
                    "side": o["side"],
                    "type": o["type"],
                    "quantity": float(o["quantity"]),
                    "price": float(o["price"]),
                    "status": o["status"],
                    "filled_quantity": float(o.get("filled_quantity", 0)),
                    "remaining_quantity": float(o.get("remaining_quantity", 0)),
                    "timestamp": time.time(),
                }))
            return orders
        return []

    def get_trades_history(self, symbol=None, count=100):
        """Get trade history."""
        params = {"instrument_name": symbol, "count": min(count, 100)} if symbol else {"count": min(count, 100)}

        response = self._make_request("POST", "/private/get-trades", params)
        data = self._process_response(response, "get_trades_history")

        if data and "data" in data:
            trades = []
            for t in data["data"]:
                trades.append({
                    "symbol": t["instrument_name"],
                    "trade_id": t["trade_id"],
                    "order_id": t.get("order_id"),
                    "side": t["side"],
                    "price": float(t["price"]),
                    "quantity": float(t["quantity"]),
                    "fee": float(t.get("fee", 0)),
                    "fee_currency": t.get("fee_ccy"),
                    "timestamp": float(t["time"]) / 1000 if "time" in t else None,
                })
            return trades
        return []