"""
MEXC REST API request base class.
Handles authentication, signing, and all REST API methods.
"""

from __future__ import annotations

import hmac
import time
from typing import Any
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.mexc_exchange_data import MexcExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.errors.error_framework_mexc import MexcErrorTranslator
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class MexcRequestData(Feed):
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
        self.public_key = kwargs.get("public_key") or kwargs.get("api_key")
        self.private_key = (
            kwargs.get("private_key") or kwargs.get("secret_key") or kwargs.get("api_secret")
        )
        self.exchange_name = kwargs.get("exchange_name", "MEXC___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "mexc_spot_feed.log")
        self._params = kwargs.get("exchange_data", MexcExchangeDataSpot())
        self.request_logger = get_logger("mexc_spot_feed")
        self.async_logger = get_logger("mexc_spot_feed")
        self._error_translator = MexcErrorTranslator()
        self._http_client = HttpClient(venue=self.exchange_name, timeout=30)

    def push_data_to_queue(self, data) -> None:
        """Push request result to data queue."""
        if self.data_queue is not None:
            self.data_queue.put(data)

    def sign(self, content: str) -> str:
        """Generate HMAC SHA256 signature

        Args:
            content (str): Content to sign

        Returns:
            str: Hexadecimal signature
        """
        if self.private_key is None:
            raise ValueError("private_key is required for signing")
        signature = hmac.new(
            self.private_key.encode("utf-8"), content.encode("utf-8"), digestmod="sha256"
        ).hexdigest()

        return signature.lower()  # MEXC requires lowercase signature

    def request(self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=True):
        """HTTP request function

        Args:
            path (str): Request path (without base URL)
            params (dict, optional): URL parameters
            body (dict, optional): Request body for POST/PUT/DELETE
            extra_data (dict, optional): Extra data for processing
            timeout (int, optional): Request timeout in seconds
            is_sign (bool, optional): Whether to sign the request

        Returns:
            RequestData: Response data container
        """
        if params is None:
            params: dict[str, Any] = {}
        if extra_data is None:
            extra_data = {}

        method, path = path.split(" ", 1)

        if is_sign:
            # Add timestamp for signed requests
            req_dict: dict[str, str | int] = {
                "timestamp": int(time.time() * 1000),
            }
            req_dict.update(
                {k: v if isinstance(v, (str, int)) else str(v) for k, v in params.items()}
            )

            # Generate signature
            query_string = urlencode(sorted(req_dict.items()))
            req_dict["signature"] = self.sign(query_string)

            # Build URL with query parameters
            req_str = urlencode({k: str(v) for k, v in req_dict.items()})
            url = f"{self._params.rest_url}{path}?{req_str}"
            headers_dict: dict[str, str] = {
                "X-MEXC-APIKEY": self.public_key or "",
                "Content-Type": "application/json",
            }
        else:
            # Public request
            req_str = urlencode(params)
            url = f"{self._params.rest_url}{path}?{req_str}"
            headers_dict = {"Content-Type": "application/json"}

        # Make HTTP request
        response = self.http_request(method, url, headers_dict, body, timeout)

        return RequestData(response, extra_data)

    async def async_request(
        self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=True
    ):
        """Async HTTP request function

        Args:
            path (str): Request path (without base URL)
            params (dict, optional): URL parameters
            body (dict, optional): Request body for POST/PUT/DELETE
            extra_data (dict, optional): Extra data for processing
            timeout (int, optional): Request timeout in seconds
            is_sign (bool, optional): Whether to sign the request

        Returns:
            RequestData: Response data container
        """
        if params is None:
            params: dict[str, Any] = {}
        if extra_data is None:
            extra_data = {}

        method, path = path.split(" ", 1)

        if is_sign:
            # Add timestamp for signed requests
            req_dict: dict[str, str | int] = {
                "timestamp": int(time.time() * 1000),
            }
            req_dict.update(
                {k: v if isinstance(v, (str, int)) else str(v) for k, v in params.items()}
            )

            # Generate signature
            query_string = urlencode(sorted(req_dict.items()))
            req_dict["signature"] = self.sign(query_string)

            # Build URL with query parameters
            req_str = urlencode({k: str(v) for k, v in req_dict.items()})
            url = f"{self._params.rest_url}{path}?{req_str}"
            headers_dict = {
                "X-MEXC-APIKEY": self.public_key or "",
                "Content-Type": "application/json",
            }
        else:
            # Public request
            req_str = urlencode(params)
            url = f"{self._params.rest_url}{path}?{req_str}"
            headers_dict = {"Content-Type": "application/json"}

        # Make async HTTP request
        response = await self._http_client.async_request(
            method=method,
            url=url,
            headers=headers_dict,
            json_data=body if method in ["POST", "PUT", "DELETE"] else None,
        )

        self.async_logger.info(f"Async Request: {method} {url}")
        return RequestData(response, extra_data)

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

    # ==================== Market Data APIs ====================

    def _get_server_time(self, extra_data=None, **kwargs):
        """Get server time

        Args:
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_server_time"
        path = self._params.get_rest_path(request_type)
        params: dict[str, str | int] = {}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_server_time_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        """Normalize server time response"""
        status = input_data is not None
        server_time = None

        if status:
            server_time = input_data.get("serverTime")

        return [{"server_time": server_time}], status

    def _get_exchange_info(self, symbol=None, extra_data=None, **kwargs):
        """Get exchange information

        Args:
            symbol (str, optional): Trading symbol
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_exchange_info"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}

        if symbol:
            params["symbol"] = symbol

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
        """Normalize exchange info response"""
        status = input_data is not None
        symbols = []

        if status and "symbols" in input_data:
            symbols = input_data["symbols"]

        return [{"symbols": symbols}], status

    def _get_order_book(self, symbol, limit=100, extra_data=None, **kwargs):
        """Get order book depth

        Args:
            symbol (str): Trading symbol
            limit (int, optional): Depth limit (default 100, max 5000)
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_order_book"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": symbol, "limit": limit}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_order_book_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_order_book_normalize_function(input_data, extra_data):
        """Normalize order book response"""
        status = input_data is not None

        if status:
            return [
                {
                    "symbol": extra_data["symbol_name"],
                    "bids": input_data.get("bids", []),
                    "asks": input_data.get("asks", []),
                    "timestamp": input_data.get("time"),
                    "local_update_time": time.time(),
                }
            ], status
        else:
            return [], status

    def _get_recent_trades(self, symbol, limit=500, extra_data=None, **kwargs):
        """Get recent trades

        Args:
            symbol (str): Trading symbol
            limit (int, optional): Trade limit (default 500, max 1000)
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_recent_trades"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": symbol, "limit": limit}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_recent_trades_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_recent_trades_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[list[dict[str, Any]], bool]:
        """Normalize recent trades response"""
        status = input_data is not None
        trades: list[dict[str, Any]] = []

        if status and isinstance(input_data, list):
            trades = []
            for trade in input_data:
                trades.append(
                    {
                        "symbol": extra_data["symbol_name"],
                        "id": trade.get("id"),
                        "price": float(trade.get("price", 0)),
                        "qty": float(trade.get("qty", 0)),
                        "quote_qty": float(trade.get("quoteQty", 0)),
                        "time": trade.get("time"),
                        "is_buyer_maker": trade.get("isBuyerMaker", False),
                        "is_best_match": trade.get("isBestMatch", False),
                    }
                )

        return [{"trades": trades}], status

    def _get_klines(self, symbol, interval="1h", limit=100, extra_data=None, **kwargs):
        """Get kline/candlestick data

        Args:
            symbol (str): Trading symbol
            interval (str, optional): Kline interval (default "1h")
            limit (int, optional): Number of klines (default 100, max 1000)
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_klines"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": symbol, "interval": interval, "limit": limit}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "interval": interval,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_klines_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_klines_normalize_function(input_data, extra_data):
        """Normalize klines response"""
        status = input_data is not None
        klines = []

        if status and isinstance(input_data, list):
            for kline in input_data:
                if len(kline) >= 6:
                    klines.append(
                        {
                            "symbol": extra_data["symbol_name"],
                            "interval": extra_data["interval"],
                            "open_time": int(kline[0]),
                            "open": float(kline[1]),
                            "high": float(kline[2]),
                            "low": float(kline[3]),
                            "close": float(kline[4]),
                            "volume": float(kline[5]),
                            "close_time": int(kline[6]),
                            "quote_volume": float(kline[7]) if len(kline) > 7 and kline[7] else 0,
                            "trades": int(kline[8]) if len(kline) > 8 and kline[8] else 0,
                            "taker_buy_base_volume": float(kline[9])
                            if len(kline) > 9 and kline[9]
                            else 0,
                            "taker_buy_quote_volume": float(kline[10])
                            if len(kline) > 10 and kline[10]
                            else 0,
                        }
                    )

        return [{"klines": klines}], status

    def _get_24hr_ticker(self, symbol=None, extra_data=None, **kwargs):
        """Get 24-hour ticker price change statistics

        Args:
            symbol (str, optional): Trading symbol
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_24hr_ticker"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}

        if symbol:
            params["symbol"] = symbol

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_24hr_ticker_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_24hr_ticker_normalize_function(input_data, extra_data):
        """Normalize 24hr ticker response"""
        status = input_data is not None

        if status and isinstance(input_data, dict):
            return [
                {
                    "symbol": extra_data["symbol_name"],
                    "price_change": float(input_data.get("priceChange", 0)),
                    "price_change_percent": float(input_data.get("priceChangePercent", 0)),
                    "weighted_avg_price": float(input_data.get("weightedAvgPrice", 0)),
                    "prev_close_price": float(input_data.get("prevClosePrice", 0)),
                    "last_price": float(input_data.get("lastPrice", 0)),
                    "last_qty": float(input_data.get("lastQty", 0)),
                    "bid_price": float(input_data.get("bidPrice", 0)),
                    "bid_qty": float(input_data.get("bidQty", 0)),
                    "ask_price": float(input_data.get("askPrice", 0)),
                    "ask_qty": float(input_data.get("askQty", 0)),
                    "open_price": float(input_data.get("openPrice", 0)),
                    "high_price": float(input_data.get("highPrice", 0)),
                    "low_price": float(input_data.get("lowPrice", 0)),
                    "volume": float(input_data.get("volume", 0)),
                    "quote_volume": float(input_data.get("quoteVolume", 0)),
                    "open_time": input_data.get("openTime"),
                    "close_time": input_data.get("closeTime"),
                    "count": int(input_data.get("count", 0)),
                }
            ], status
        elif status and isinstance(input_data, list):
            # Multiple tickers response
            tickers = []
            for ticker in input_data:
                tickers.append(
                    {
                        "symbol": ticker.get("symbol"),
                        "price_change": float(ticker.get("priceChange", 0)),
                        "price_change_percent": float(ticker.get("priceChangePercent", 0)),
                        "last_price": float(ticker.get("lastPrice", 0)),
                        "volume": float(ticker.get("volume", 0)),
                        "quote_volume": float(ticker.get("quoteVolume", 0)),
                        "count": int(ticker.get("count", 0)),
                    }
                )
            return [{"tickers": tickers}], status
        else:
            return [], status

    # ==================== Trading APIs ====================

    def _make_order(
        self,
        symbol,
        side,
        order_type,
        quantity,
        price=None,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """Place a new order

        Args:
            symbol (str): Trading symbol
            side (str): Order side ("BUY" or "SELL")
            order_type (str): Order type ("LIMIT", "MARKET", "LIMIT_MAKER", etc.)
            quantity (str): Order quantity
            price (str, optional): Order price (required for limit orders)
            client_order_id (str, optional): Client order ID
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)
        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": str(quantity),
        }

        if price:
            params["price"] = str(price)

        if client_order_id:
            params["newClientOrderId"] = client_order_id

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
        """Normalize order placement response"""
        status = input_data is not None

        if status:
            return [
                {
                    "symbol": extra_data["symbol_name"],
                    "order_id": input_data.get("orderId"),
                    "client_order_id": input_data.get("clientOrderId"),
                    "transact_time": input_data.get("transactTime"),
                    "status": input_data.get("status"),
                    "executed_qty": input_data.get("executedQty", "0"),
                    "cummulative_quote_qty": input_data.get("cummulativeQuoteQty", "0"),
                }
            ], status
        else:
            return [], status

    def _cancel_order(self, symbol, order_id=None, client_order_id=None, extra_data=None, **kwargs):
        """Cancel an existing order

        Args:
            symbol (str): Trading symbol
            order_id (str, optional): Order ID
            client_order_id (str, optional): Client order ID
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": symbol}

        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._cancel_order_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        """Normalize order cancellation response"""
        status = input_data is not None

        if status:
            return [
                {
                    "symbol": extra_data["symbol_name"],
                    "order_id": input_data.get("orderId"),
                    "client_order_id": input_data.get("clientOrderId"),
                    "status": input_data.get("status"),
                    "transact_time": input_data.get("transactTime"),
                }
            ], status
        else:
            return [], status

    def _get_order(self, symbol, order_id=None, client_order_id=None, extra_data=None, **kwargs):
        """Query an order's status

        Args:
            symbol (str): Trading symbol
            order_id (str, optional): Order ID
            client_order_id (str, optional): Client order ID
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_order"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": symbol}

        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_order_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_order_normalize_function(input_data, extra_data):
        """Normalize order query response"""
        status = input_data is not None

        if status:
            return [
                {
                    "symbol": input_data.get("symbol") or extra_data["symbol_name"],
                    "order_id": input_data.get("orderId"),
                    "client_order_id": input_data.get("clientOrderId"),
                    "status": input_data.get("status"),
                    "side": input_data.get("side"),
                    "type": input_data.get("type"),
                    "time_in_force": input_data.get("timeInForce"),
                    "quantity": input_data.get("origQty"),
                    "executed_qty": input_data.get("executedQty"),
                    "cummulative_quote_qty": input_data.get("cummulativeQuoteQty"),
                    "price": input_data.get("price"),
                    "stop_price": input_data.get("stopPrice"),
                    "iceberg_qty": input_data.get("icebergQty"),
                    "time": input_data.get("time"),
                    "update_time": input_data.get("updateTime"),
                    "is_working": input_data.get("isWorking"),
                    "exchange": input_data.get("exchange"),
                }
            ], status
        else:
            return [], status

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Query all open orders

        Args:
            symbol (str, optional): Trading symbol
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_open_orders"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}

        if symbol:
            params["symbol"] = symbol

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
        """Normalize open orders response"""
        status = input_data is not None
        orders = []

        if status and isinstance(input_data, list):
            for order in input_data:
                orders.append(
                    {
                        "symbol": extra_data["symbol_name"],
                        "order_id": order.get("orderId"),
                        "client_order_id": order.get("clientOrderId"),
                        "status": order.get("status"),
                        "side": order.get("side"),
                        "type": order.get("type"),
                        "time_in_force": order.get("timeInForce"),
                        "quantity": order.get("origQty"),
                        "executed_qty": order.get("executedQty"),
                        "cummulative_quote_qty": order.get("cummulativeQuoteQty"),
                        "price": order.get("price"),
                        "stop_price": order.get("stopPrice"),
                        "iceberg_qty": order.get("icebergQty"),
                        "time": order.get("time"),
                        "update_time": order.get("updateTime"),
                    }
                )

        return [{"orders": orders}], status

    def _get_all_orders(self, symbol, limit=500, extra_data=None, **kwargs):
        """Query all orders (up to recent 500)

        Args:
            symbol (str): Trading symbol
            limit (int, optional): Order limit (default 500, max 1000)
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_all_orders"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": symbol, "limit": limit}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_all_orders_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_all_orders_normalize_function(input_data, extra_data):
        """Normalize all orders response"""
        status = input_data is not None
        orders = []

        if status and isinstance(input_data, list):
            for order in input_data:
                orders.append(
                    {
                        "symbol": extra_data["symbol_name"],
                        "order_id": order.get("orderId"),
                        "client_order_id": order.get("clientOrderId"),
                        "status": order.get("status"),
                        "side": order.get("side"),
                        "type": order.get("type"),
                        "time_in_force": order.get("timeInForce"),
                        "quantity": order.get("origQty"),
                        "executed_qty": order.get("executedQty"),
                        "cummulative_quote_qty": order.get("cummulativeQuoteQty"),
                        "price": order.get("price"),
                        "stop_price": order.get("stopPrice"),
                        "iceberg_qty": order.get("icebergQty"),
                        "time": order.get("time"),
                        "update_time": order.get("updateTime"),
                        "is_working": order.get("isWorking"),
                    }
                )

        return [{"orders": orders}], status

    # ==================== Account APIs ====================

    def _get_account(self, extra_data=None, **kwargs):
        """Get account information

        Args:
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_account"
        path = self._params.get_rest_path(request_type)
        params: dict[str, str | int] = {}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_account_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        """Normalize account info response"""
        status = input_data is not None

        if status:
            balances = []
            for balance in input_data.get("balances", []):
                if float(balance.get("free", 0)) > 0 or float(balance.get("locked", 0)) > 0:
                    balances.append(
                        {
                            "asset": balance.get("asset"),
                            "free": float(balance.get("free", 0)),
                            "locked": float(balance.get("locked", 0)),
                        }
                    )

            return [
                {
                    "maker_commission": input_data.get("makerCommission", 0),
                    "taker_commission": input_data.get("takerCommission", 0),
                    "buyer_commission": input_data.get("buyerCommission", 0),
                    "seller_commission": input_data.get("sellerCommission", 0),
                    "can_trade": input_data.get("canTrade", False),
                    "can_withdraw": input_data.get("canWithdraw", False),
                    "can_deposit": input_data.get("canDeposit", False),
                    "balances": balances,
                    "account_type": input_data.get("accountType"),
                }
            ], status
        else:
            return [], status

    def _get_my_trades(self, symbol, limit=500, extra_data=None, **kwargs):
        """Get user's trade history

        Args:
            symbol (str): Trading symbol
            limit (int, optional): Trade limit (default 500, max 1000)
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_my_trades"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": symbol, "limit": limit}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_my_trades_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_my_trades_normalize_function(input_data, extra_data):
        """Normalize trade history response"""
        status = input_data is not None
        trades = []

        if status and isinstance(input_data, list):
            for trade in input_data:
                trades.append(
                    {
                        "symbol": extra_data["symbol_name"],
                        "id": trade.get("id"),
                        "order_id": trade.get("orderId"),
                        "price": float(trade.get("price", 0)),
                        "qty": float(trade.get("qty", 0)),
                        "quote_qty": float(trade.get("quoteQty", 0)),
                        "commission": float(trade.get("commission", 0)),
                        "commission_asset": trade.get("commissionAsset"),
                        "time": trade.get("time"),
                        "is_buyer": trade.get("isBuyer", False),
                        "is_maker": trade.get("isMaker", False),
                        "is_best_match": trade.get("isBestMatch", False),
                    }
                )

        return [{"trades": trades}], status
