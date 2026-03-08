"""
KuCoin Spot trading feed implementation.
"""

import uuid

from bt_api_py.containers.accounts.kucoin_account import KuCoinRequestAccountData
from bt_api_py.containers.bars.kucoin_bar import KuCoinRequestBarData
from bt_api_py.containers.exchanges.kucoin_exchange_data import KuCoinExchangeDataSpot
from bt_api_py.containers.orderbooks.kucoin_orderbook import KuCoinRequestOrderBookData
from bt_api_py.containers.orders.kucoin_order import KuCoinRequestOrderData
from bt_api_py.containers.tickers.kucoin_ticker import KuCoinRequestTickerData
from bt_api_py.containers.trades.kucoin_trade import KuCoinRequestTradeData
from bt_api_py.feeds.live_kucoin.request_base import KuCoinRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class KuCoinRequestDataSpot(KuCoinRequestData):
    """KuCoin Spot trading REST API implementation."""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "kucoin_spot_feed.log")
        self._params = KuCoinExchangeDataSpot()
        self.request_logger = get_logger("kucoin_spot_feed")
        self.async_logger = get_logger("kucoin_spot_feed")

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
            symbol: Trading pair symbol (e.g., "BTC-USDT")
            vol: Order volume
            price: Order price (required for limit orders)
            order_type: Order type (buy-limit, sell-limit, buy-market, sell-market)
            offset: Order side (open/close) - not used for spot
            post_only: Post only flag
            client_order_id: Client order ID
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)

        # Parse order type
        side, ord_type = order_type.split("-")
        side = side.upper()  # BUY or SELL
        ord_type = ord_type.upper()  # LIMIT or MARKET

        # Generate client order ID if not provided
        if client_order_id is None:
            client_order_id = str(uuid.uuid4())

        params = {
            "clientOid": client_order_id,
            "side": side,
            "symbol": symbol,
            "type": ord_type,
        }

        # Add type-specific parameters
        if ord_type == "LIMIT":
            if price is None:
                raise ValueError("Price is required for limit orders")
            params["price"] = str(price)
            params["size"] = str(vol)
            if post_only:
                params["postOnly"] = True
        elif ord_type == "MARKET":
            if side == "BUY":
                # Market buy requires funds (quote currency amount)
                params["funds"] = str(vol)
            else:
                # Market sell requires size (base currency amount)
                params["size"] = str(vol)

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": KuCoinRequestDataSpot._make_order_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        """Normalize order response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if input_data and "data" in input_data and "orderId" in input_data["data"]:
            # Return single order data
            return [KuCoinRequestOrderData(input_data, symbol_name, asset_type, True)], status
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

        Args:
            symbol: Trading pair symbol
            vol: Order volume
            price: Order price (required for limit orders)
            order_type: Order type
            post_only: Post only flag
            client_order_id: Client order ID
            extra_data: Extra data

        Returns:
            RequestData with order response
        """
        path, params, extra_data = self._make_order(
            symbol, vol, price, order_type, offset, post_only, client_order_id, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _cancel_order(self, order_id=None, client_order_id=None, extra_data=None, **kwargs):
        """Create cancel order parameters.

        Args:
            order_id: Order ID
            client_order_id: Client order ID (one of order_id or client_order_id required)
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
        if order_id is None and client_order_id is None:
            raise ValueError("Either order_id or client_order_id must be provided")

        request_type = "cancel_order"

        if order_id:
            path = f"DELETE /api/v1/orders/{order_id}"
            params = {}
        else:
            path = "DELETE /api/v1/orders"
            params = {"clientOid": client_order_id}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": kwargs.get("symbol", "UNKNOWN"),
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def cancel_order(self, order_id=None, client_order_id=None, extra_data=None, **kwargs):
        """Cancel an order.

        Args:
            order_id: Order ID
            client_order_id: Client order ID

        Returns:
            RequestData with cancel response
        """
        path, params, extra_data = self._cancel_order(
            order_id=order_id, client_order_id=client_order_id, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _cancel_all_orders(self, symbol=None, extra_data=None, **kwargs):
        """Create cancel all orders parameters.

        Args:
            symbol: Trading pair symbol (optional, cancel all if not provided)
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "cancel_all"
        path = "DELETE /api/v1/orders"
        params = {}
        if symbol:
            params["symbol"] = symbol

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
        return path, params, extra_data

    def cancel_all_orders(self, symbol=None, extra_data=None, **kwargs):
        """Cancel all orders.

        Args:
            symbol: Trading pair symbol (cancel all if not provided)

        Returns:
            RequestData with cancel response
        """
        path, params, extra_data = self._cancel_all_orders(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_order(self, order_id=None, client_order_id=None, extra_data=None, **kwargs):
        """Create get order parameters.

        Args:
            order_id: Order ID
            client_order_id: Client order ID
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "get_order"

        if order_id:
            path = f"GET /api/v1/orders/{order_id}"
            params = {}
        else:
            path = "GET /api/v1/orders"
            params = {"clientOid": client_order_id}

        symbol = kwargs.get("symbol", "UNKNOWN")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": KuCoinRequestDataSpot._get_order_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_order_normalize_function(input_data, extra_data):
        """Normalize order response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if input_data and "data" in input_data:
            data = input_data["data"]
            if isinstance(data, list):
                return [
                    KuCoinRequestOrderData({"data": item}, symbol_name, asset_type, True)
                    for item in data
                ], status
            return [KuCoinRequestOrderData(input_data, symbol_name, asset_type, True)], status
        return [], status

    def get_order(self, order_id=None, client_order_id=None, extra_data=None, **kwargs):
        """Get order details.

        Args:
            order_id: Order ID
            client_order_id: Client order ID

        Returns:
            RequestData with order details
        """
        path, params, extra_data = self._get_order(
            order_id=order_id, client_order_id=client_order_id, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # Alias for get_order to match Binance/OKX pattern
    query_order = get_order

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Create get open orders parameters.

        Args:
            symbol: Trading pair symbol (optional)
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "get_open_orders"
        path = "GET /api/v1/orders"
        params = {"status": "active"}
        if symbol:
            params["symbol"] = symbol

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": KuCoinRequestDataSpot._get_open_orders_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        """Normalize open orders response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if input_data and "data" in input_data and "items" in input_data["data"]:
            items = input_data["data"]["items"]
            return [
                KuCoinRequestOrderData({"data": item}, symbol_name, asset_type, True)
                for item in items
            ], status
        return [], status

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get all open orders.

        Args:
            symbol: Trading pair symbol (optional)

        Returns:
            RequestData with open orders
        """
        path, params, extra_data = self._get_open_orders(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # ==================== Market Data ====================

    def _get_ticker(self, symbol, extra_data=None, **kwargs):
        """Create get ticker parameters.

        Args:
            symbol: Trading pair symbol
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "get_ticker"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": symbol}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": KuCoinRequestDataSpot._get_ticker_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_ticker_normalize_function(input_data, extra_data):
        """Normalize ticker response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if input_data and "data" in input_data:
            return [KuCoinRequestTickerData(input_data, symbol_name, asset_type, True)], status
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

    def _get_depth(self, symbol, limit=20, extra_data=None, **kwargs):
        """Create get order book parameters.

        Args:
            symbol: Trading pair symbol
            limit: Depth limit (20, 100)
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": symbol}

        # Use level2_100 for more depth, level2 for full depth
        if limit <= 20:
            path = "GET /api/v1/market/orderbook/level2_20"
        else:
            path = "GET /api/v1/market/orderbook/level2_100"

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": KuCoinRequestDataSpot._get_depth_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize order book response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if input_data and "data" in input_data:
            return [KuCoinRequestOrderBookData(input_data, symbol_name, asset_type, True)], status
        return [], status

    def get_depth(self, symbol, limit=20, extra_data=None, **kwargs):
        """Get order book data.

        Args:
            symbol: Trading pair symbol
            limit: Depth limit

        Returns:
            RequestData with order book data
        """
        path, params, extra_data = self._get_depth(
            symbol=symbol, limit=limit, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def _get_kline(
        self,
        symbol,
        period="1hour",
        start_time=None,
        end_time=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Create get kline parameters.

        Args:
            symbol: Trading pair symbol
            period: Kline period (1min, 5min, 15min, 30min, 1hour, 2hour, 4hour, 6hour, 8hour, 12hour, 1day, 1week)
            start_time: Start time (seconds since epoch)
            end_time: End time (seconds since epoch)
            limit: Number of klines to return
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type)

        # Convert period to KuCoin format
        period_map = {
            "1min": "1min",
            "3min": "3min",
            "5min": "5min",
            "15min": "15min",
            "30min": "30min",
            "1hour": "1hour",
            "2hour": "2hour",
            "4hour": "4hour",
            "6hour": "6hour",
            "8hour": "8hour",
            "12hour": "12hour",
            "1day": "1day",
            "1week": "1week",
        }
        kline_type = period_map.get(period, "1hour")

        params = {
            "symbol": symbol,
            "type": kline_type,
        }

        if start_time:
            params["startAt"] = int(start_time)
        if end_time:
            params["endAt"] = int(end_time)

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": KuCoinRequestDataSpot._get_kline_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if input_data and "data" in input_data:
            # KuCoin returns klines as array of arrays: [time, open, close, high, low, volume, turnover]
            klines = input_data["data"]
            return [
                KuCoinRequestBarData(kline, symbol_name, asset_type, True) for kline in klines
            ], status
        return [], status

    def get_kline(
        self,
        symbol,
        period="1hour",
        start_time=None,
        end_time=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Get kline data.

        Args:
            symbol: Trading pair symbol
            period: Kline period
            start_time: Start time
            end_time: End time
            limit: Number of klines

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

    def _get_deals(
        self,
        symbol,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Create get trade history parameters.

        Args:
            symbol: Trading pair symbol
            limit: Number of trades to return
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "get_deals"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": symbol}
        if limit:
            params["limit"] = limit

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": KuCoinRequestDataSpot._get_deals_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        """Normalize trade history response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if input_data and "data" in input_data:
            trades = input_data["data"]
            return [
                KuCoinRequestTradeData({"data": trade}, symbol_name, asset_type, True)
                for trade in trades
            ], status
        return [], status

    def get_deals(self, symbol, limit=None, extra_data=None, **kwargs):
        """Get recent trades.

        Args:
            symbol: Trading pair symbol
            limit: Number of trades

        Returns:
            RequestData with trade data
        """
        path, params, extra_data = self._get_deals(
            symbol=symbol, limit=limit, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    # ==================== Account Management ====================

    def _get_account(self, currency=None, account_type=None, extra_data=None, **kwargs):
        """Create get account parameters.

        Args:
            currency: Currency code
            account_type: Account type (main, trade, margin)
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "get_account"
        path = self._params.get_rest_path(request_type)
        params = {}
        if currency:
            params["currency"] = currency
        if account_type:
            params["type"] = account_type

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": currency or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": KuCoinRequestDataSpot._get_account_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        """Normalize account response."""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if input_data and "data" in input_data:
            accounts = input_data["data"]
            return [
                KuCoinRequestAccountData(acc, symbol_name, asset_type, True) for acc in accounts
            ], status
        return [], status

    def get_account(self, currency=None, account_type=None, extra_data=None, **kwargs):
        """Get account information.

        Args:
            currency: Currency code
            account_type: Account type (main, trade, margin)

        Returns:
            RequestData with account data
        """
        path, params, extra_data = self._get_account(
            currency=currency, account_type=account_type, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get balance data — delegates to get_account for KuCoin.

        Args:
            symbol: Currency code (optional)

        Returns:
            RequestData with balance data
        """
        return self.get_account(currency=symbol, extra_data=extra_data, **kwargs)

    # ==================== Server & Exchange Info ====================

    def _get_server_time(self, extra_data=None, **kwargs):
        """Create get server time parameters.

        Args:
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
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

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Create get exchange info parameters.

        Args:
            extra_data: Extra data

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "get_contract"
        path = self._params.get_rest_path(request_type)
        params = {}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": KuCoinRequestDataSpot._get_exchange_info_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info response — extract 'data' from KuCoin wrapper."""
        if input_data and isinstance(input_data, dict) and "data" in input_data:
            data = input_data["data"]
            if isinstance(data, list):
                return data, True
            return [data], True
        return [], False

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange trading rules and symbol info.

        Returns:
            RequestData with exchange info
        """
        path, params, extra_data = self._get_exchange_info(extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def get_symbols(self, extra_data=None, **kwargs):
        """Get available trading symbols.

        Returns:
            RequestData with symbols list
        """
        return self.get_exchange_info(extra_data=extra_data, **kwargs)

    # ==================== Async Methods ====================

    def async_get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker data asynchronously.

        Args:
            symbol: Trading pair symbol
            extra_data: Extra data
        """
        path, params, extra_data = self._get_ticker(symbol=symbol, extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    # Alias for async_get_ticker to match Binance/OKX pattern
    async_get_tick = async_get_ticker

    def async_get_depth(self, symbol, limit=20, extra_data=None, **kwargs):
        """Get order book data asynchronously.

        Args:
            symbol: Trading pair symbol
            limit: Depth limit
            extra_data: Extra data
        """
        path, params, extra_data = self._get_depth(
            symbol=symbol, limit=limit, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    def async_get_kline(
        self,
        symbol,
        period="1hour",
        start_time=None,
        end_time=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Get kline data asynchronously.

        Args:
            symbol: Trading pair symbol
            period: Kline period
            start_time: Start time
            end_time: End time
            limit: Number of klines
            extra_data: Extra data
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
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    def async_get_account(self, currency=None, account_type=None, extra_data=None, **kwargs):
        """Get account information asynchronously.

        Args:
            currency: Currency code
            account_type: Account type (main, trade, margin)
            extra_data: Extra data
        """
        path, params, extra_data = self._get_account(
            currency=currency, account_type=account_type, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def async_get_balance(self, currency=None, extra_data=None, **kwargs):
        """Get account balance asynchronously.

        Args:
            currency: Currency code
            extra_data: Extra data
        """
        path, params, extra_data = self._get_account(
            currency=currency, account_type=None, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )
