"""
PancakeSpot Spot Trading Implementation

Implements spot trading functionality for PancakeSwap using the request_base.py
"""

import time
from typing import Any

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.binance_ticker import BinanceRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_pancakeswap.request_base import PancakeSwapRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class PancakeSpotRequestData(PancakeSwapRequestData):
    """PancakeSpot Spot Trading Request Data"""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "pancakeswap_spot.log")
        self.request_logger = get_logger("pancakeswap_spot")

    # ==================== Market Data Methods ====================

    def _get_tick(self, symbol: str, extra_data=None, **kwargs) -> tuple[str, dict[str, Any], Any]:
        """Internal get tick method for ticker data.

        Args:
            symbol: Trading pair symbol (e.g., "BTCB/USDT")
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data) - for compatibility
        """
        request_type = "get_tick"

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

        # Get symbol for API
        api_symbol = (
            self._params.get_symbol(symbol) if hasattr(self._params, "get_symbol") else symbol
        )

        params = {"symbol": api_symbol}
        path = (
            self._params.get_rest_path(request_type)
            if hasattr(self._params, "get_rest_path")
            else "/ticker"
        )

        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker response data.

        Args:
            input_data: Raw response data from API
            extra_data: Extra data attached to the request

        Returns:
            tuple: (normalized_data_list, status_bool)
        """
        if not input_data:
            return [], False

        # Handle different input formats
        if isinstance(input_data, dict) and "data" in input_data:
            input_data = input_data["data"]

        # Extract ticker data
        if isinstance(input_data, dict) and "price" in input_data:
            # Single ticker format
            ticker_data = {
                "symbol": input_data.get("symbol", ""),
                "price": float(input_data.get("price", 0)),
                "timestamp": input_data.get("timestamp", int(time.time() * 1000)),
                "volume": float(input_data.get("volume", 0)),
                "quote_volume": float(input_data.get("quote_volume", 0)),
                "high": input_data.get("high"),
                "low": input_data.get("low"),
                "bid": input_data.get("bid"),
                "ask": input_data.get("ask"),
            }
            return [ticker_data], True

        return [], False

    def get_tick(self, symbol: str, extra_data=None, **kwargs) -> RequestData:
        """Get ticker information for a symbol."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request("GET", path, params, extra_data)

    def async_get_tick(self, symbol: str, extra_data=None, **kwargs):
        """Async get ticker information."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request("GET", path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def get_ticker(self, symbol: str, extra_data=None, **kwargs) -> BinanceRequestTickerData:
        """Get ticker as BinanceRequestTickerData (for compatibility).

        Args:
            symbol: Trading pair symbol
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            BinanceRequestTickerData instance
        """
        request_data = self.get_tick(symbol, extra_data, **kwargs)
        ticker_list = request_data.data if request_data.data else []
        if ticker_list:
            ticker_data = ticker_list[0]
            return BinanceRequestTickerData(
                symbol=ticker_data.get("symbol", symbol),
                price=ticker_data.get("price", 0.0),
                timestamp=ticker_data.get("timestamp", int(time.time() * 1000)),
                volume=ticker_data.get("volume", 0.0),
                quote_volume=ticker_data.get("quote_volume", 0.0),
                high=ticker_data.get("high"),
                low=ticker_data.get("low"),
                bid=ticker_data.get("bid"),
                ask=ticker_data.get("ask"),
                count=0,
            )
        return BinanceRequestTickerData(
            symbol=symbol,
            price=0.0,
            timestamp=int(time.time() * 1000),
        )

    def _get_depth(
        self, symbol: str, limit: int = 100, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Internal get depth method for order book data.

        Args:
            symbol: Trading pair symbol
            limit: Number of depth levels
            extra_data: Extra data to attach
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_depth"

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

        api_symbol = (
            self._params.get_symbol(symbol) if hasattr(self._params, "get_symbol") else symbol
        )
        params = {"symbol": api_symbol, "limit": limit}
        path = (
            self._params.get_rest_path(request_type)
            if hasattr(self._params, "get_rest_path")
            else "/depth"
        )

        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth/orderbook response data.

        Args:
            input_data: Raw response data from API
            extra_data: Extra data attached to the request

        Returns:
            tuple: (normalized_data_list, status_bool)
        """
        if not input_data:
            return [], False

        # Handle different input formats
        if isinstance(input_data, dict) and "data" in input_data:
            input_data = input_data["data"]

        # Extract depth data
        if isinstance(input_data, dict):
            depth_data = {
                "symbol": extra_data.get("symbol_name", "") if extra_data else "",
                "bids": input_data.get("bids", []),
                "asks": input_data.get("asks", []),
                "timestamp": input_data.get("timestamp", int(time.time() * 1000)),
            }
            return [depth_data], True

        return [], False

    def get_depth(self, symbol: str, limit: int = 100, extra_data=None, **kwargs) -> RequestData:
        """Get order book depth for a symbol."""
        path, params, extra_data = self._get_depth(symbol, limit, extra_data, **kwargs)
        return self.request("GET", path, params, extra_data)

    def async_get_depth(self, symbol: str, limit: int = 100, extra_data=None, **kwargs):
        """Async get order book depth."""
        path, params, extra_data = self._get_depth(symbol, limit, extra_data, **kwargs)
        self.submit(
            self.async_request("GET", path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_kline(
        self, symbol: str, interval: str, limit: int = 100, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Internal get kline method for candlestick data.

        Args:
            symbol: Trading pair symbol
            interval: Kline interval (1m, 5m, 15m, 30m, 1h, 4h, 1d)
            limit: Number of klines to return
            extra_data: Extra data to attach
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_kline"

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "interval": interval,
                "normalize_function": self._get_kline_normalize_function,
            },
        )

        api_symbol = (
            self._params.get_symbol(symbol) if hasattr(self._params, "get_symbol") else symbol
        )
        params = {"symbol": api_symbol, "interval": interval, "limit": limit}
        path = (
            self._params.get_rest_path(request_type)
            if hasattr(self._params, "get_rest_path")
            else "/kline"
        )

        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline/candlestick response data.

        Args:
            input_data: Raw response data from API
            extra_data: Extra data attached to the request

        Returns:
            tuple: (normalized_data_list, status_bool)
        """
        if not input_data:
            return [], False

        # Handle different input formats
        klines = []
        if isinstance(input_data, dict):
            if "klines" in input_data:
                klines = input_data["klines"]
            elif "data" in input_data and isinstance(input_data["data"], list):
                klines = input_data["data"]
        elif isinstance(input_data, list):
            klines = input_data

        result = []
        for kline in klines:
            if isinstance(kline, list):
                # Array format: [timestamp, open, high, low, close, volume, ...]
                kline_data = {
                    "symbol": extra_data.get("symbol_name", "") if extra_data else "",
                    "interval": extra_data.get("interval", "1h") if extra_data else "1h",
                    "timestamp": kline[0] if len(kline) > 0 else 0,
                    "open": float(kline[1]) if len(kline) > 1 else 0.0,
                    "high": float(kline[2]) if len(kline) > 2 else 0.0,
                    "low": float(kline[3]) if len(kline) > 3 else 0.0,
                    "close": float(kline[4]) if len(kline) > 4 else 0.0,
                    "volume": float(kline[5]) if len(kline) > 5 else 0.0,
                    "quote_volume": float(kline[7]) if len(kline) > 7 else 0.0,
                    "trades": int(kline[8]) if len(kline) > 8 else 0,
                }
            elif isinstance(kline, dict):
                # Object format
                kline_data = {
                    "symbol": extra_data.get("symbol_name", "") if extra_data else "",
                    "interval": extra_data.get("interval", "1h") if extra_data else "1h",
                    "timestamp": kline.get("timestamp", 0),
                    "open": float(kline.get("open", 0)),
                    "high": float(kline.get("high", 0)),
                    "low": float(kline.get("low", 0)),
                    "close": float(kline.get("close", 0)),
                    "volume": float(kline.get("volume", 0)),
                    "quote_volume": float(kline.get("quote_volume", 0)),
                    "trades": int(kline.get("trades", 0)),
                }
            else:
                continue
            result.append(kline_data)

        return result, len(result) > 0

    def get_kline(
        self, symbol: str, interval: str, limit: int = 100, extra_data=None, **kwargs
    ) -> RequestData:
        """Get K-line/candlestick data."""
        path, params, extra_data = self._get_kline(symbol, interval, limit, extra_data, **kwargs)
        return self.request("GET", path, params, extra_data)

    def async_get_kline(
        self, symbol: str, interval: str, limit: int = 100, extra_data=None, **kwargs
    ):
        """Async get K-line/candlestick data."""
        path, params, extra_data = self._get_kline(symbol, interval, limit, extra_data, **kwargs)
        self.submit(
            self.async_request("GET", path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_exchange_info(self, extra_data=None, **kwargs) -> tuple[str, dict[str, Any], Any]:
        """Internal get exchange info method.

        Args:
            extra_data: Extra data to attach
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_exchange_info"

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_exchange_info_normalize_function,
            },
        )

        params = {}
        path = (
            self._params.get_rest_path(request_type)
            if hasattr(self._params, "get_rest_path")
            else "/exchangeInfo"
        )

        return path, params, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info response data.

        Args:
            input_data: Raw response data from API
            extra_data: Extra data attached to the request

        Returns:
            tuple: (normalized_data_list, status_bool)
        """
        if not input_data:
            return [], False

        if isinstance(input_data, dict):
            if "data" in input_data:
                input_data = input_data["data"]
            return [input_data], True

        return [], False

    def get_exchange_info(self, extra_data=None, **kwargs) -> RequestData:
        """Get exchange information.

        Args:
            extra_data: Extra data to attach
            **kwargs: Additional parameters

        Returns:
            RequestData with exchange information
        """
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request("GET", path, params, extra_data)

    # ==================== Pool Methods ====================

    def _get_pool(
        self, pool_address: str, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Internal get pool method for pool data.

        Args:
            pool_address: Pool contract address
            extra_data: Extra data to attach
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_pool"

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "pool_address": pool_address,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_pool_normalize_function,
            },
        )

        params = {"address": pool_address}
        path = (
            self._params.get_rest_path(request_type)
            if hasattr(self._params, "get_rest_path")
            else "/pool"
        )

        return path, params, extra_data

    @staticmethod
    def _get_pool_normalize_function(input_data, extra_data):
        """Normalize pool response data.

        Args:
            input_data: Raw response data from API
            extra_data: Extra data attached to the request

        Returns:
            tuple: (normalized_data_list, status_bool)
        """
        if not input_data:
            return [], False

        # Handle different input formats
        if isinstance(input_data, dict):
            if "data" in input_data:
                input_data = input_data["data"]
            if "pair" in input_data:
                input_data = input_data["pair"]

            pool_data = {
                "pool_address": input_data.get(
                    "id", extra_data.get("pool_address", "") if extra_data else ""
                ),
                "symbol": input_data.get("symbol", ""),
                "tvl": float(input_data.get("reserveUSD", 0)),
                "volume_24h": float(input_data.get("volumeUSD", 0)),
            }
            return [pool_data], True

        return [], False

    def get_pool(self, pool_address: str, extra_data=None, **kwargs) -> RequestData:
        """Get pool information.

        Args:
            pool_address: Pool contract address
            extra_data: Extra data to attach
            **kwargs: Additional parameters

        Returns:
            RequestData with pool information
        """
        path, params, extra_data = self._get_pool(pool_address, extra_data, **kwargs)
        return self.request("GET", path, params, extra_data)

    def _get_pools(
        self, first: int = 10, min_tvl: int = 0, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Internal get pools method for pool list.

        Args:
            first: Number of pools to return
            min_tvl: Minimum TVL filter
            extra_data: Extra data to attach
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_pools"

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_pools_normalize_function,
            },
        )

        params = {"first": first, "min_tvl": min_tvl}
        path = (
            self._params.get_rest_path(request_type)
            if hasattr(self._params, "get_rest_path")
            else "/pools"
        )

        return path, params, extra_data

    @staticmethod
    def _get_pools_normalize_function(input_data, extra_data):
        """Normalize pools list response data.

        Args:
            input_data: Raw response data from API
            extra_data: Extra data attached to the request

        Returns:
            tuple: (normalized_data_list, status_bool)
        """
        if not input_data:
            return [], False

        pools = []
        if isinstance(input_data, dict):
            if "data" in input_data:
                data = input_data["data"]
                if "pairs" in data:
                    pools = data["pairs"]
        elif isinstance(input_data, list):
            pools = input_data

        result = []
        for pool in pools:
            if isinstance(pool, dict):
                pool_data = {
                    "pool_address": pool.get("id", ""),
                    "symbol": pool.get("symbol", ""),
                    "tvl": float(pool.get("reserveUSD", 0)),
                    "volume_24h": float(pool.get("volumeUSD", 0)),
                }
                result.append(pool_data)

        return result, len(result) > 0

    def get_pools(
        self, first: int = 10, min_tvl: int = 0, extra_data=None, **kwargs
    ) -> RequestData:
        """Get list of pools.

        Args:
            first: Number of pools to return
            min_tvl: Minimum TVL filter
            extra_data: Extra data to attach
            **kwargs: Additional parameters

        Returns:
            RequestData with pools information
        """
        path, params, extra_data = self._get_pools(first, min_tvl, extra_data, **kwargs)
        return self.request("GET", path, params, extra_data)

    # ==================== Standard Trading Interfaces ====================

    def _make_order(
        self,
        symbol,
        volume,
        price,
        order_type,
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """Prepare order. Returns (method, path, body, extra_data).

        PancakeSwap is a DEX; trading requires on-chain transactions.
        """
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "make_order",
                "quantity": volume,
                "price": price,
                "order_type": order_type,
            }
        )
        body = {
            "symbol": symbol,
            "quantity": str(volume),
            "price": str(price),
            "orderType": order_type,
        }
        return "POST", "/orders", body, extra_data

    def make_order(
        self,
        symbol,
        volume,
        price,
        order_type,
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """Place an order. Note: PancakeSwap requires on-chain tx."""
        method, path, body, extra_data = self._make_order(
            symbol,
            volume,
            price,
            order_type,
            offset,
            post_only,
            client_order_id,
            extra_data,
            **kwargs,
        )
        return self.request(method, path, extra_data=extra_data, body=body)

    def _cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order. Returns (method, path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "cancel_order",
                "order_id": order_id,
            }
        )
        return "DELETE", f"/orders/{order_id}", {}, extra_data

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order."""
        method, path, params, extra_data = self._cancel_order(
            symbol, order_id, extra_data, **kwargs
        )
        return self.request(method, path, params=params, extra_data=extra_data)

    def _query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order. Returns (method, path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "query_order",
                "order_id": order_id,
            }
        )
        return "GET", f"/orders/{order_id}", {}, extra_data

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order status."""
        method, path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(method, path, params=params, extra_data=extra_data)

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders. Returns (method, path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_open_orders",
            }
        )
        return "GET", "/orders", {}, extra_data

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders."""
        method, path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(method, path, params=params, extra_data=extra_data)

    # ==================== Standard Account Interfaces ====================

    def _get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info. Returns (method, path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_account",
            }
        )
        return "GET", "/account", {}, extra_data

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info."""
        method, path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        return self.request(method, path, params=params, extra_data=extra_data)

    def _get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get balance. Returns (method, path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_balance",
            }
        )
        return "GET", "/balance", {}, extra_data

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get token balance."""
        method, path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(method, path, params=params, extra_data=extra_data)

    # ==================== Additional Methods ====================

    def get_pool_info(self, pool_address: str, extra_data=None, **kwargs) -> RequestData:
        """Get detailed information about a liquidity pool."""
        return self.get_pool(pool_address, extra_data, **kwargs)

    def get_token_info(self, token_address: str, extra_data=None, **kwargs) -> dict:
        """Get information about a token."""
        return {}

    def get_all_pools(self, limit: int = 100, extra_data=None, **kwargs) -> RequestData:
        """Get list of all liquidity pools."""
        return self.get_pools(first=limit, extra_data=extra_data, **kwargs)

    def get_price_impact(self, symbol: str, amount: float) -> dict:
        """Calculate price impact for a trade."""
        return {"symbol": symbol, "amount": amount, "price_impact": 0.0, "current_price": 0.0}
