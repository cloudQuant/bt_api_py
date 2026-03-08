"""
Coinbase Spot trading feed implementation.
Three-layer pattern: _get_xxx() -> get_xxx() / async_get_xxx()
"""

import uuid

from bt_api_py.containers.accounts.coinbase_account import CoinbaseRequestAccountData
from bt_api_py.containers.bars.coinbase_bar import CoinbaseRequestBarData
from bt_api_py.containers.exchanges.coinbase_exchange_data import CoinbaseExchangeDataSpot
from bt_api_py.containers.orderbooks.coinbase_orderbook import CoinbaseRequestOrderBookData
from bt_api_py.containers.orders.coinbase_order import CoinbaseRequestOrderData
from bt_api_py.containers.tickers.coinbase_ticker import CoinbaseRequestTickerData
from bt_api_py.feeds.live_coinbase.request_base import CoinbaseRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class CoinbaseRequestDataSpot(CoinbaseRequestData):
    """Coinbase Spot trading REST API implementation."""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "COINBASE___SPOT")
        self.logger_name = kwargs.get("logger_name", "coinbase_spot_feed.log")
        self._params = CoinbaseExchangeDataSpot()
        self.request_logger = get_logger("coinbase_spot_feed")
        self.async_logger = get_logger("coinbase_spot_feed")

    # ==================== Market Data ====================

    def _get_ticker(self, symbol, extra_data=None, **kwargs):
        """Create get ticker parameters.

        Args:
            symbol: Trading pair symbol (e.g., "BTC-USD")
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "get_ticker"
        path = self._params.get_rest_path(request_type)
        # Replace {product_id} placeholder with actual symbol
        path = path.replace("{product_id}", self._params.get_symbol(symbol))
        params = {}
        if "limit" in kwargs:
            params["limit"] = kwargs["limit"]

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": CoinbaseRequestDataSpot._get_ticker_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_ticker_normalize_function(input_data, extra_data):
        """Normalize ticker response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if input_data and isinstance(input_data, dict):
            # Coinbase ticker returns trades + best_bid/best_ask at top level
            return [CoinbaseRequestTickerData(input_data, symbol_name, asset_type, True)], status
        return [], status

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker data.

        Args:
            symbol: Trading pair symbol

        Returns:
            RequestData with ticker data
        """
        path, params, extra_data = self._get_ticker(symbol=symbol, extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    # Alias for get_ticker to match standard interface
    get_tick = get_ticker

    def async_get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker data asynchronously."""
        path, params, extra_data = self._get_ticker(symbol=symbol, extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    async_get_tick = async_get_ticker

    # ── order book ──────────────────────────────────────────────

    def _get_depth(self, symbol, limit=50, extra_data=None, **kwargs):
        """Create get order book parameters.

        Args:
            symbol: Trading pair symbol
            limit: Number of price levels
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type)
        params = {
            "product_id": self._params.get_symbol(symbol),
            "limit": min(limit, 100),
        }

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": CoinbaseRequestDataSpot._get_depth_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize order book response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if input_data and isinstance(input_data, dict):
            return [CoinbaseRequestOrderBookData(input_data, symbol_name, asset_type, True)], status
        return [], status

    def get_depth(self, symbol, limit=50, extra_data=None, **kwargs):
        """Get order book data.

        Args:
            symbol: Trading pair symbol
            limit: Number of price levels

        Returns:
            RequestData with order book data
        """
        path, params, extra_data = self._get_depth(
            symbol=symbol, limit=limit, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def async_get_depth(self, symbol, limit=50, extra_data=None, **kwargs):
        """Get order book data asynchronously."""
        path, params, extra_data = self._get_depth(
            symbol=symbol, limit=limit, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    # ── kline / candles ─────────────────────────────────────────

    def _get_kline(
        self,
        symbol,
        period="ONE_HOUR",
        start_time=None,
        end_time=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Create get kline parameters.

        Args:
            symbol: Trading pair symbol
            period: Kline period (ONE_MINUTE, FIVE_MINUTE, etc.)
            start_time: Start timestamp (seconds)
            end_time: End timestamp (seconds)
            limit: Not used by Coinbase
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type)
        path = path.replace("{product_id}", self._params.get_symbol(symbol))

        # Convert period if needed
        granularity = (
            self._params.get_period(period) if period in self._params.kline_periods else period
        )

        params = {"granularity": granularity}
        if start_time:
            params["start"] = str(int(start_time))
        if end_time:
            params["end"] = str(int(end_time))

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": CoinbaseRequestDataSpot._get_kline_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if input_data and isinstance(input_data, dict) and "candles" in input_data:
            candles = input_data["candles"]
            return [
                CoinbaseRequestBarData(c, symbol_name, asset_type, True) for c in candles
            ], status
        return [], status

    def get_kline(
        self,
        symbol,
        period="ONE_HOUR",
        start_time=None,
        end_time=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Get kline / candle data.

        Args:
            symbol: Trading pair symbol
            period: Kline period
            start_time: Start timestamp
            end_time: End timestamp

        Returns:
            RequestData with kline data
        """
        path, params, extra_data = self._get_kline(
            symbol=symbol,
            period=period,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def async_get_kline(
        self,
        symbol,
        period="ONE_HOUR",
        start_time=None,
        end_time=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Get kline data asynchronously."""
        path, params, extra_data = self._get_kline(
            symbol=symbol,
            period=period,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            extra_data=extra_data,
            **kwargs,
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    # ── server time ─────────────────────────────────────────────

    def _get_server_time(self, extra_data=None, **kwargs):
        """Create get server time parameters."""
        request_type = "get_server_time"
        path = self._params.get_rest_path(request_type)
        params = {}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time.

        Returns:
            RequestData with server time
        """
        path, params, extra_data = self._get_server_time(extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    # ── exchange info ───────────────────────────────────────────

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Create get exchange info parameters."""
        request_type = "get_exchange_info"
        path = self._params.get_rest_path(request_type)
        params = {}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": CoinbaseRequestDataSpot._get_exchange_info_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info response."""
        if input_data and isinstance(input_data, dict) and "products" in input_data:
            return input_data["products"], True
        if input_data and isinstance(input_data, list):
            return input_data, True
        return [], False

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange trading rules and product info.

        Returns:
            RequestData with exchange info
        """
        path, params, extra_data = self._get_exchange_info(extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    # ==================== Order Management ====================

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
        """Create order parameters.

        Args:
            symbol: Trading pair symbol (e.g., "BTC-USD")
            vol: Order volume
            price: Order price (required for limit orders)
            order_type: Order type (buy-limit, sell-limit, buy-market, sell-market)
            offset: Not used for Coinbase spot
            post_only: Post only flag
            client_order_id: Client order ID
            extra_data: Extra data

        Returns:
            Tuple of (path, body, extra_data)
        """
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)

        # Parse order type
        side, ord_type = order_type.split("-")
        side = side.upper()

        # Generate client order ID if not provided
        if client_order_id is None:
            client_order_id = str(uuid.uuid4())

        # Build order configuration based on type
        if ord_type.lower() == "limit":
            order_config = {
                "limit_limit_gtc": {
                    "base_size": str(vol),
                    "limit_price": str(price),
                    "post_only": post_only,
                }
            }
        else:
            # market
            if side == "BUY":
                order_config = {"market_market_ioc": {"quote_size": str(vol)}}
            else:
                order_config = {"market_market_ioc": {"base_size": str(vol)}}

        body = {
            "client_order_id": client_order_id,
            "product_id": self._params.get_symbol(symbol),
            "side": side,
            "order_configuration": order_config,
        }

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": CoinbaseRequestDataSpot._make_order_normalize_function,
            },
        )
        return path, body, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        """Normalize order response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if isinstance(input_data, dict) and input_data.get("success"):
            resp = input_data.get("success_response", {})
            return [CoinbaseRequestOrderData(resp, symbol_name, asset_type, True)], status
        return [], status

    def make_order(
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
        """Place a new order.

        Returns:
            RequestData with order response
        """
        path, body, extra_data = self._make_order(
            symbol, vol, price, order_type, offset, post_only, client_order_id, extra_data, **kwargs
        )
        data = self.request(path, body=body, extra_data=extra_data, is_sign=True)
        return data

    # ── cancel order ────────────────────────────────────────────

    def _cancel_order(
        self, symbol=None, order_id=None, client_order_id=None, extra_data=None, **kwargs
    ):
        """Create cancel order parameters.

        Args:
            symbol: Trading pair symbol
            order_id: Order ID to cancel
            client_order_id: Client order ID to cancel
            extra_data: Extra data

        Returns:
            Tuple of (path, body, extra_data)
        """
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)

        order_ids = []
        if order_id:
            order_ids.append(order_id)
        if client_order_id:
            order_ids.append(client_order_id)

        body = {"order_ids": order_ids}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, body, extra_data

    def cancel_order(
        self, symbol=None, order_id=None, client_order_id=None, extra_data=None, **kwargs
    ):
        """Cancel an order.

        Returns:
            RequestData with cancel response
        """
        path, body, extra_data = self._cancel_order(
            symbol=symbol,
            order_id=order_id,
            client_order_id=client_order_id,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, body=body, extra_data=extra_data, is_sign=True)
        return data

    # ── query order ─────────────────────────────────────────────

    def _query_order(self, order_id, extra_data=None, **kwargs):
        """Create query order parameters.

        Args:
            order_id: Order ID
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "query_order"
        path = self._params.get_rest_path(request_type)
        path = path.replace("{order_id}", order_id)
        params = {}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": kwargs.get("symbol", "ALL"),
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": CoinbaseRequestDataSpot._query_order_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        """Normalize query order response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if input_data and isinstance(input_data, dict) and "order" in input_data:
            return [
                CoinbaseRequestOrderData(input_data["order"], symbol_name, asset_type, True)
            ], status
        return [], status

    def query_order(self, order_id, extra_data=None, **kwargs):
        """Query order details.

        Args:
            order_id: Order ID

        Returns:
            RequestData with order details
        """
        path, params, extra_data = self._query_order(
            order_id=order_id, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # ── open orders ─────────────────────────────────────────────

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Create get open orders parameters.

        Args:
            symbol: Trading pair symbol
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "get_open_orders"
        path = self._params.get_rest_path(request_type)
        params = {"order_status": "OPEN"}
        if symbol:
            params["product_id"] = self._params.get_symbol(symbol)

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": CoinbaseRequestDataSpot._get_open_orders_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        """Normalize open orders response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if input_data and isinstance(input_data, dict) and "orders" in input_data:
            orders = input_data["orders"]
            return [
                CoinbaseRequestOrderData(o, symbol_name, asset_type, True) for o in orders
            ], status
        return [], status

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get all open orders.

        Returns:
            RequestData with open orders
        """
        path, params, extra_data = self._get_open_orders(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # ==================== Account Management ====================

    def _get_account(self, extra_data=None, **kwargs):
        """Create get account parameters.

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "get_account"
        path = self._params.get_rest_path(request_type)
        params = {}
        if "limit" in kwargs:
            params["limit"] = kwargs["limit"]

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": CoinbaseRequestDataSpot._get_account_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        """Normalize account response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if input_data and isinstance(input_data, dict) and "accounts" in input_data:
            accounts = input_data["accounts"]
            return [
                CoinbaseRequestAccountData(acc, symbol_name, asset_type, True) for acc in accounts
            ], status
        return [], status

    def get_account(self, extra_data=None, **kwargs):
        """Get account information.

        Returns:
            RequestData with account data
        """
        path, params, extra_data = self._get_account(extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get balance data — delegates to get_account.

        Returns:
            RequestData with balance data
        """
        return self.get_account(extra_data=extra_data, **kwargs)

    def async_get_account(self, extra_data=None, **kwargs):
        """Get account information asynchronously."""
        path, params, extra_data = self._get_account(extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def async_get_balance(self, extra_data=None, **kwargs):
        """Get account balance asynchronously."""
        self.async_get_account(extra_data=extra_data, **kwargs)


class CoinbaseMarketWssData:
    """Placeholder for Coinbase WebSocket market data implementation."""

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = CoinbaseExchangeDataSpot()


class CoinbaseAccountWssData:
    """Placeholder for Coinbase WebSocket account data implementation."""

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = CoinbaseExchangeDataSpot()
