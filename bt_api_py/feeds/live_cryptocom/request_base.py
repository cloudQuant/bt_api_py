"""
Crypto.com REST API request base class.
Handles authentication, signing, and all REST API methods.
"""

import hashlib
import hmac
import time
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.cryptocom_exchange_data import CryptoComExchangeDataSpot
from bt_api_py.containers.orderbooks.cryptocom_orderbook import CryptoComOrderBook
from bt_api_py.containers.orders.cryptocom_order import CryptoComOrder
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.cryptocom_ticker import CryptoComTicker
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class CryptoComRequestData(Feed):
    """Crypto.com REST API feed implementation.

    Crypto.com private endpoints use a signed JSON POST body:
    {id, method, api_key, params, sig, nonce}
    Public endpoints use standard GET with query params.
    """

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_DEALS,
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
        self.api_key = kwargs.get("public_key") or kwargs.get("api_key")
        self.api_secret = kwargs.get("private_key") or kwargs.get("api_secret")
        self.logger_name = kwargs.get("logger_name", "cryptocom_spot_feed.log")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "CRYPTOCOM___SPOT")
        self._params = CryptoComExchangeDataSpot()
        self.request_logger = get_logger("cryptocom_spot_feed")
        self.async_logger = get_logger("cryptocom_spot_feed")

    # ── authentication ──────────────────────────────────────────

    @staticmethod
    def _build_param_string(params):
        """Build parameter string for signature (keys sorted, values concatenated)."""
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

    def sign(self, api_method, params, req_id, nonce):
        """Generate HMAC-SHA256 signature for Crypto.com private request.

        sign_str = method + id + api_key + param_string + nonce
        """
        param_string = self._build_param_string(params)
        sign_str = f"{api_method}{req_id}{self.api_key}{param_string}{nonce}"
        return hmac.new(
            self.api_secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # ── core request helpers ────────────────────────────────────

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            assert 0, "Queue not initialized"

    def request(self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=False):
        """HTTP request function using Feed.http_request().

        Args:
            path: Request path in "METHOD /endpoint" format
            params: URL query parameters (public) or body params (private)
            body: Request body (private signed requests)
            extra_data: Extra data for RequestData
            timeout: Request timeout in seconds
            is_sign: Whether to sign the request

        Returns:
            RequestData object
        """
        if params is None:
            params = {}

        method, endpoint = path.split(" ", 1)
        url = f"{self._params.rest_url}{endpoint}"

        headers = {"Content-Type": "application/json"}
        req_body = None

        if not is_sign:
            # Public endpoint — GET with query params
            if params:
                url = f"{url}?{urlencode(params)}"
            res = self.http_request(method, url, headers, req_body, timeout)
        else:
            # Private endpoint — POST with signed JSON body
            # Crypto.com uses "method" field = the private API method name
            # e.g., endpoint = "/private/create-order" → api_method = "private/create-order"
            api_method = endpoint.lstrip("/")
            nonce = int(time.time() * 1000)
            req_id = nonce
            sig = self.sign(api_method, body or params, req_id, nonce)
            req_body = {
                "id": req_id,
                "method": api_method,
                "api_key": self.api_key,
                "params": body or params or {},
                "sig": sig,
                "nonce": nonce,
            }
            res = self.http_request("POST", url, headers, req_body, timeout)

        return RequestData(res, extra_data)

    async def async_request(
        self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=False
    ):
        """Async HTTP request function.

        Args:
            path: Request path in "METHOD /endpoint" format
            params: URL query parameters (public) or body params (private)
            body: Request body (private signed requests)
            extra_data: Extra data for RequestData
            timeout: Request timeout in seconds
            is_sign: Whether to sign the request

        Returns:
            RequestData object
        """
        if params is None:
            params = {}

        method, endpoint = path.split(" ", 1)
        url = f"{self._params.rest_url}{endpoint}"

        headers = {"Content-Type": "application/json"}
        req_body = None

        if not is_sign:
            if params:
                url = f"{url}?{urlencode(params)}"
        else:
            api_method = endpoint.lstrip("/")
            nonce = int(time.time() * 1000)
            req_id = nonce
            sig = self.sign(api_method, body or params, req_id, nonce)
            req_body = {
                "id": req_id,
                "method": api_method,
                "api_key": self.api_key,
                "params": body or params or {},
                "sig": sig,
                "nonce": nonce,
            }
            method = "POST"

        res = await self._http_client.async_request(method, url, headers, req_body, timeout)
        return RequestData(res, extra_data)

    # ==================== Server Time ====================

    def _get_server_time(self, extra_data=None, **kwargs):
        """Get server time internal method."""
        request_type = "get_server_time"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_server_time_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        """Normalize server time response."""
        status = input_data is not None
        server_time = None
        if status and isinstance(input_data, dict):
            # Crypto.com returns {"id": ..., "method": ..., "code": 0, "result": {...}}
            result = input_data.get("result", {})
            if "data" in result:
                server_time = result["data"]
        return [{"server_time": server_time}], status

    # ==================== Exchange Info ====================

    def _get_exchange_info(self, symbol=None, extra_data=None, **kwargs):
        """Get exchange information internal method."""
        request_type = "get_exchange_info"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_exchange_info_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info response."""
        status = input_data is not None
        symbols = []
        if status and isinstance(input_data, dict):
            result = input_data.get("result", {})
            symbols = result.get("data", [])
        return [{"symbols": symbols}], status

    # ==================== Ticker ====================

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker internal method."""
        request_type = "get_tick"
        request_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path(request_type)
        params = {"instrument_name": request_symbol}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_tick_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data from API response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if not status:
            return [], False

        result = input_data.get("result", {}) if isinstance(input_data, dict) else {}
        ticker_list = result.get("data", [])
        if not ticker_list:
            return [], False

        data = []
        for t in ticker_list:
            ticker_data = {
                "a": str(t.get("a", "0")),
                "b": str(t.get("b", "0")),
                "k": str(t.get("k", "0")),
                "h": str(t.get("h", "0")),
                "l": str(t.get("l", "0")),
                "v": str(t.get("v", "0")),
                "vv": str(t.get("vv", "0")),
                "t": t.get("t", 0),
            }
            data.append(CryptoComTicker(ticker_data, symbol_name, asset_type, True))
        return data, True

    # ==================== Depth (Order Book) ====================

    def _get_depth(self, symbol, size=20, extra_data=None, **kwargs):
        """Get orderbook internal method."""
        request_type = "get_depth"
        request_symbol = self._params.get_symbol(symbol)
        depth = min(size, 50)
        path = self._params.get_rest_path(request_type)
        params = {"instrument_name": request_symbol, "depth": depth}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_depth_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize orderbook data from API response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]

        if not status:
            return [], False

        result = input_data.get("result", {}) if isinstance(input_data, dict) else {}
        book_data = result.get("data", [])
        if not book_data:
            return [], False

        orderbook_data = {"data": book_data, "t": int(time.time() * 1000)}
        orderbook = CryptoComOrderBook.from_api_response(orderbook_data, symbol_name)
        return [orderbook], True

    # ==================== Kline (Candlestick) ====================

    def _get_kline(self, symbol, period="1m", count=100, extra_data=None, **kwargs):
        """Get kline internal method."""
        request_type = "get_kline"
        request_symbol = self._params.get_symbol(symbol)
        kline_period = self._params.get_kline_period(period)
        path = self._params.get_rest_path(request_type)
        params = {
            "instrument_name": request_symbol,
            "period": kline_period,
            "count": min(count, 300),
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_kline_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline data from API response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]

        if not status:
            return [], False

        result = input_data.get("result", {}) if isinstance(input_data, dict) else {}
        candles = result.get("data", [])
        if not candles:
            return [], False

        klines = []
        for c in candles:
            kline_data = {
                "timestamp": float(c["t"]) / 1000,
                "open": float(c["o"]),
                "high": float(c["h"]),
                "low": float(c["l"]),
                "close": float(c["c"]),
                "volume": float(c["v"]),
            }
            klines.append(kline_data)
        return klines, True

    # ==================== Trade History ====================

    def _get_trade_history(self, symbol, count=100, extra_data=None, **kwargs):
        """Get trade history internal method."""
        request_type = "get_trade_history"
        request_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path(request_type)
        params = {"instrument_name": request_symbol, "count": min(count, 150)}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_trade_history_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_trade_history_normalize_function(input_data, extra_data):
        """Normalize trade history response."""
        status = input_data is not None
        if not status:
            return [], False
        result = input_data.get("result", {}) if isinstance(input_data, dict) else {}
        trades = result.get("data", [])
        return trades, bool(trades)

    # ==================== Private APIs ====================

    def _make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """Create an order internal method."""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)

        side, order_subtype = order_type.split("-")
        params = {
            "instrument_name": request_symbol,
            "side": side.upper(),
            "type": order_subtype.upper(),
        }

        if order_subtype.upper() == "MARKET" and side.upper() == "BUY":
            if kwargs.get("notional"):
                params["notional"] = str(kwargs["notional"])
            else:
                params["quantity"] = str(vol)
        else:
            params["quantity"] = str(vol)

        if price is not None and order_subtype.upper() != "MARKET":
            params["price"] = str(price)

        if client_order_id is not None:
            params["client_oid"] = str(client_order_id)

        if post_only:
            params["exec_inst"] = "POST_ONLY"

        if kwargs.get("time_in_force"):
            params["time_in_force"] = kwargs["time_in_force"]

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._make_order_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        """Normalize make order response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if not status:
            return [], False

        result = input_data.get("result", {}) if isinstance(input_data, dict) else {}
        if result:
            order_data = {
                "order_id": str(result.get("order_id", "")),
                "instrument_name": symbol_name,
                "side": result.get("side", ""),
                "type": result.get("type", ""),
                "quantity": str(result.get("quantity", "0")),
                "price": str(result.get("price", "0")),
                "status": result.get("status", ""),
            }
            return [CryptoComOrder(order_data, symbol_name, asset_type, True)], True
        return [], False

    def _cancel_order(
        self, symbol=None, order_id=None, client_order_id=None, extra_data=None, **kwargs
    ):
        """Cancel an order internal method."""
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)

        params = {}
        if symbol:
            params["instrument_name"] = self._params.get_symbol(symbol)
        if order_id is not None:
            params["order_id"] = str(order_id)

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "order_id": order_id,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._cancel_order_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        """Normalize cancel order response."""
        status = input_data is not None
        if not status:
            return [], False
        code = input_data.get("code", -1) if isinstance(input_data, dict) else -1
        return [{"success": code == 0}], code == 0

    def _query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        """Query order detail internal method."""
        request_type = "query_order"
        path = self._params.get_rest_path(request_type)

        params = {}
        if order_id is not None:
            params["order_id"] = str(order_id)

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "order_id": order_id,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._query_order_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        """Normalize query order response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if not status:
            return [], False

        result = input_data.get("result", {}) if isinstance(input_data, dict) else {}
        if result:
            return [CryptoComOrder(result, symbol_name, asset_type, True)], True
        return [], False

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders internal method."""
        request_type = "get_open_orders"
        path = self._params.get_rest_path(request_type)

        params = {}
        if symbol:
            params["instrument_name"] = self._params.get_symbol(symbol)

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_open_orders_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        """Normalize open orders response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if not status:
            return [], False

        result = input_data.get("result", {}) if isinstance(input_data, dict) else {}
        orders = result.get("data", [])
        return [CryptoComOrder(o, symbol_name, asset_type, True) for o in orders], bool(orders)

    def _get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account summary internal method."""
        request_type = "get_account"
        path = self._params.get_rest_path(request_type)
        params = {}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_account_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        """Normalize account summary response."""
        status = input_data is not None
        if not status:
            return [], False
        result = input_data.get("result", {}) if isinstance(input_data, dict) else {}
        accounts = result.get("data", [])
        return accounts, bool(accounts)

    def _get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get balance internal method."""
        request_type = "get_balance"
        path = self._params.get_rest_path(request_type)
        params = {}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_balance_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        """Normalize balance response."""
        status = input_data is not None
        if not status:
            return [], False
        result = input_data.get("result", {}) if isinstance(input_data, dict) else {}
        balances = result.get("data", [])
        return balances, bool(balances)
