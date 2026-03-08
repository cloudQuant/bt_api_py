"""
MEXC Spot Trading Feed

Implements spot trading functionality for MEXC exchange.
"""

from bt_api_py.containers.balances.mexc_balance import MexcRequestBalanceData
from bt_api_py.containers.exchanges.mexc_exchange_data import MexcExchangeDataSpot
from bt_api_py.containers.orderbooks.mexc_orderbook import MexcRequestOrderBookData
from bt_api_py.containers.orders.mexc_order import MexcRequestOrderData
from bt_api_py.containers.tickers.mexc_ticker import MexcRequestTickerData
from bt_api_py.containers.trades.mexc_trade import MexcRequestTradeData
from bt_api_py.feeds.live_mexc.request_base import MexcRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class MexcRequestDataSpot(MexcRequestData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "mexc_spot_feed.log")
        self._params = kwargs.get("exchange_data", MexcExchangeDataSpot())
        self.request_logger = get_logger("mexc_spot_feed")
        self.async_logger = get_logger("mexc_spot_feed")

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
        """Place a new order

        Args:
            symbol (str): Trading symbol
            vol (str): Order quantity
            price (str, optional): Order price
            order_type (str, optional): Order type ("buy-limit", "sell-limit", "buy-market", "sell-market")
            offset (str, optional): Offset type ("open" for new orders)
            post_only (bool, optional): Whether to post only
            client_order_id (str, optional): Client order ID
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        side, order_type_str = order_type.split("-", 1)
        time_in_force = kwargs.get("time_in_force", "GTC")

        params = {
            "symbol": request_symbol,
            "side": side.upper(),
            "quantity": vol,
            "type": order_type_str.upper(),
            "timeInForce": time_in_force,
        }

        if price:
            params["price"] = str(price)

        if client_order_id is not None:
            params["newClientOrderId"] = client_order_id

        if order_type_str.upper() == "MARKET":
            params.pop("timeInForce", None)
            params.pop("price", None)

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "post_only": post_only,
                "normalize_function": MexcRequestDataSpot._make_order_normalize_function,
            },
        )

        request_type = "make_order"
        path = self._params.get_rest_path(request_type)

        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        """Normalize order placement response"""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if isinstance(input_data, list):
            data = [MexcRequestOrderData(i, symbol_name, asset_type, True) for i in input_data]
        elif isinstance(input_data, dict):
            data = [MexcRequestOrderData(input_data, symbol_name, asset_type, True)]
        else:
            data = []

        return data, status

    # ==================== Market Data Methods ====================

    def _get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get 24-hour ticker price change statistics

        Args:
            symbol (str): Trading symbol
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_24hr_ticker"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_ticker_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_ticker_normalize_function(input_data, extra_data):
        """Normalize ticker response"""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if status:
            ticker = MexcRequestTickerData(input_data, symbol_name, asset_type)
            return [ticker], status
        else:
            return [], status

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
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_order_book"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol, "limit": limit}

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
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if status:
            orderbook = MexcRequestOrderBookData(input_data, symbol_name, asset_type)
            return [orderbook], status
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
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_recent_trades"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol, "limit": limit}

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
    def _get_recent_trades_normalize_function(input_data, extra_data):
        """Normalize recent trades response"""
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if status and isinstance(input_data, list):
            trades = [MexcRequestTradeData(trade, symbol_name, asset_type) for trade in input_data]
            return [trades], status
        else:
            return [], status

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
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_klines"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol, "interval": interval, "limit": limit}

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
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        interval = extra_data["interval"]

        if status and isinstance(input_data, list):
            bars = []
            for kline in input_data:
                if len(kline) >= 6:
                    bar = {
                        "symbol": symbol_name,
                        "interval": interval,
                        "open_time": int(kline[0]),
                        "open": float(kline[1]),
                        "high": float(kline[2]),
                        "low": float(kline[3]),
                        "close": float(kline[4]),
                        "volume": float(kline[5]),
                        "close_time": int(kline[6]),
                        "quote_volume": float(kline[7]) if len(kline) > 7 else 0,
                        "trades": int(kline[8]) if len(kline) > 8 else 0,
                        "taker_buy_base_volume": float(kline[9]) if len(kline) > 9 else 0,
                        "taker_buy_quote_volume": float(kline[10]) if len(kline) > 10 else 0,
                    }
                    bars.append(bar)
            return [bars], status
        else:
            return [], status

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
        params = {}

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

        if status:
            return [{"server_time": input_data.get("serverTime")}], status
        else:
            return [], status

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
        params = {}

        if symbol:
            params["symbol"] = self._params.get_symbol(symbol)

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

        if status:
            return [{"exchange_info": input_data}], status
        else:
            return [], status

    # ==================== Trading Methods ====================

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
        request_symbol = self._params.get_symbol(symbol)
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol}

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
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if status:
            return [MexcRequestOrderData(input_data, symbol_name, asset_type)], status
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
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_order"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol}

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
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if status:
            return [MexcRequestOrderData(input_data, symbol_name, asset_type)], status
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
        params = {}

        if symbol:
            params["symbol"] = self._params.get_symbol(symbol)

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
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if status and isinstance(input_data, list):
            orders = [MexcRequestOrderData(order, symbol_name, asset_type) for order in input_data]
            return [orders], status
        else:
            return [], status

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
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_all_orders"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol, "limit": limit}

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
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if status and isinstance(input_data, list):
            orders = [MexcRequestOrderData(order, symbol_name, asset_type) for order in input_data]
            return [orders], status
        else:
            return [], status

    # ==================== Account Methods ====================

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
        params = {}

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
                    balance_data = MexcRequestBalanceData(balance, None, extra_data["asset_type"])
                    balances.append(balance_data)

            account_info = {
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

            return [account_info], status
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
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_my_trades"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol, "limit": limit}

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
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if status and isinstance(input_data, list):
            trades = [MexcRequestTradeData(trade, symbol_name, asset_type) for trade in input_data]
            return [trades], status
        else:
            return [], status

    # ==================== Public API Methods ====================

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time

        Args:
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            RequestData: Response data container
        """
        path, params, extra_data = self._get_server_time(extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get 24-hour ticker price change statistics

        Args:
            symbol (str): Trading symbol
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            RequestData: Response data container
        """
        path, params, extra_data = self._get_ticker(symbol, extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth

        Args:
            symbol (str): Trading symbol
            count (int): Depth limit (default 20)
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            RequestData: Response data container
        """
        path, params, extra_data = self._get_order_book(
            symbol, limit=count, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def get_kline(self, symbol, period="1h", count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data

        Args:
            symbol (str): Trading symbol
            period (str): Kline interval (default "1h")
            count (int): Number of klines (default 20)
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            RequestData: Response data container
        """
        path, params, extra_data = self._get_klines(
            symbol, interval=period, limit=count, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data asynchronously

        Args:
            symbol (str): Trading symbol
            extra_data: Extra data for processing
            **kwargs: Additional parameters
        """
        path, params, extra_data = self._get_ticker(symbol, extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth asynchronously

        Args:
            symbol (str): Trading symbol
            count (int): Depth limit (default 20)
            extra_data: Extra data for processing
            **kwargs: Additional parameters
        """
        path, params, extra_data = self._get_order_book(
            symbol, limit=count, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    def async_get_kline(self, symbol, period="1h", count=20, extra_data=None, **kwargs):
        """Get kline data asynchronously

        Args:
            symbol (str): Trading symbol
            period (str): Kline interval (default "1h")
            count (int): Number of klines (default 20)
            extra_data: Extra data for processing
            **kwargs: Additional parameters
        """
        path, params, extra_data = self._get_klines(
            symbol, interval=period, limit=count, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    def get_exchange_info(self, symbol=None, extra_data=None, **kwargs):
        """Get exchange information

        Args:
            symbol (str, optional): Trading symbol
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            RequestData: Response data container
        """
        path, params, extra_data = self._get_exchange_info(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    # ==================== Trading Public Methods ====================

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
        """Place a new order

        Args:
            symbol (str): Trading symbol
            vol (str): Order quantity
            price (str, optional): Order price
            order_type (str, optional): Order type
            offset (str, optional): Offset type
            post_only (bool, optional): Whether to post only
            client_order_id (str, optional): Client order ID
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            RequestData: Response data container
        """
        path, params, extra_data = self._make_order(
            symbol=symbol,
            vol=vol,
            price=price,
            order_type=order_type,
            offset=offset,
            post_only=post_only,
            client_order_id=client_order_id,
            extra_data=extra_data,
            **kwargs,
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def cancel_order(self, symbol, order_id=None, client_order_id=None, extra_data=None, **kwargs):
        """Cancel an existing order

        Args:
            symbol (str): Trading symbol
            order_id (str, optional): Order ID
            client_order_id (str, optional): Client order ID
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            RequestData: Response data container
        """
        path, params, extra_data = self._cancel_order(
            symbol=symbol,
            order_id=order_id,
            client_order_id=client_order_id,
            extra_data=extra_data,
            **kwargs,
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def query_order(self, symbol, order_id=None, client_order_id=None, extra_data=None, **kwargs):
        """Query an order's status

        Args:
            symbol (str): Trading symbol
            order_id (str, optional): Order ID
            client_order_id (str, optional): Client order ID
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            RequestData: Response data container
        """
        path, params, extra_data = self._get_order(
            symbol=symbol,
            order_id=order_id,
            client_order_id=client_order_id,
            extra_data=extra_data,
            **kwargs,
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Query all open orders

        Args:
            symbol (str, optional): Trading symbol
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            RequestData: Response data container
        """
        path, params, extra_data = self._get_open_orders(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def get_account(self, extra_data=None, **kwargs):
        """Get account information

        Args:
            extra_data: Extra data for processing
            **kwargs: Additional parameters

        Returns:
            RequestData: Response data container
        """
        path, params, extra_data = self._get_account(extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def get_balance(self, extra_data=None, **kwargs):
        """Get balance data — delegates to get_account.

        Returns:
            RequestData: Response data container
        """
        return self.get_account(extra_data=extra_data, **kwargs)

    # ==================== Async Private/Account Methods ====================

    def async_get_server_time(self, extra_data=None, **kwargs):
        """Get server time asynchronously"""
        path, params, extra_data = self._get_server_time(extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    def async_get_exchange_info(self, symbol=None, extra_data=None, **kwargs):
        """Get exchange information asynchronously"""
        path, params, extra_data = self._get_exchange_info(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    def async_make_order(
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
        """Place a new order asynchronously"""
        path, params, extra_data = self._make_order(
            symbol=symbol,
            vol=vol,
            price=price,
            order_type=order_type,
            offset=offset,
            post_only=post_only,
            client_order_id=client_order_id,
            extra_data=extra_data,
            **kwargs,
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def async_cancel_order(
        self, symbol, order_id=None, client_order_id=None, extra_data=None, **kwargs
    ):
        """Cancel an order asynchronously"""
        path, params, extra_data = self._cancel_order(
            symbol=symbol,
            order_id=order_id,
            client_order_id=client_order_id,
            extra_data=extra_data,
            **kwargs,
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def async_query_order(
        self, symbol, order_id=None, client_order_id=None, extra_data=None, **kwargs
    ):
        """Query an order asynchronously"""
        path, params, extra_data = self._get_order(
            symbol=symbol,
            order_id=order_id,
            client_order_id=client_order_id,
            extra_data=extra_data,
            **kwargs,
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def async_get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders asynchronously"""
        path, params, extra_data = self._get_open_orders(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def async_get_account(self, extra_data=None, **kwargs):
        """Get account information asynchronously"""
        path, params, extra_data = self._get_account(extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def async_get_balance(self, extra_data=None, **kwargs):
        """Get account balance asynchronously"""
        self.async_get_account(extra_data=extra_data, **kwargs)
