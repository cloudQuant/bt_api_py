"""Bybit Spot Trading Feed"""

from bt_api_py.containers.exchanges.bybit_exchange_data import BybitExchangeDataSpot
from bt_api_py.containers.tickers.bybit_ticker import BybitSpotTickerData
from bt_api_py.containers.orderbooks.bybit_orderbook import BybitSpotOrderBookData
from bt_api_py.containers.orders.bybit_order import BybitSpotOrderData
from bt_api_py.containers.balances.bybit_balance import BybitSpotBalanceData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bybit.request_base import BybitRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class BybitRequestDataSpot(BybitRequestData):
    """Bybit Spot trading REST API feed."""

    def __init__(self, data_queue, **kwargs):
        kwargs["asset_type"] = "spot"
        kwargs.setdefault("logger_name", "bybit_spot_feed.log")
        super().__init__(data_queue, **kwargs)
        self._params = BybitExchangeDataSpot()
        self.request_logger = get_logger("request")
        self.async_logger = get_logger("async_request")

    # ==================== Market Data Methods ====================

    def _get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker data.

        Args:
            symbol: Trading pair symbol
            extra_data: Extra data for processing

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_tick"
        path = self._params.get_rest_path(request_type)
        params = {
            "symbol": request_symbol,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": BybitRequestDataSpot._get_ticker_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_ticker_normalize_function(input_data, extra_data):
        """Normalize ticker response."""
        if input_data is None:
            return [], False
        status = input_data.get("retCode") == 0
        data = [BybitSpotTickerData(input_data, extra_data["symbol_name"], True)]
        return data, status

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker data for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            RequestData: Ticker data
        """
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data (standard interface alias for get_ticker)."""
        return self.get_ticker(symbol, extra_data=extra_data, **kwargs)

    def async_get_ticker(self, symbol, extra_data=None, **kwargs):
        """Async get ticker data."""
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Async get ticker data (alias)."""
        self.async_get_ticker(symbol, extra_data=extra_data, **kwargs)

    # ==================== Depth Methods ====================

    def _get_depth(self, symbol, limit=50, extra_data=None, **kwargs):
        """Get orderbook depth.

        Args:
            symbol: Trading pair symbol
            limit: Depth limit
            extra_data: Extra data for processing

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type)
        params = {
            "symbol": request_symbol,
            "limit": limit,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": BybitRequestDataSpot._get_depth_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth response."""
        if input_data is None:
            return [], False
        status = input_data.get("retCode") == 0
        data = [BybitSpotOrderBookData(input_data, extra_data["symbol_name"], True)]
        return data, status

    def get_depth(self, symbol, limit=50, extra_data=None, **kwargs):
        """Get orderbook depth."""
        path, params, extra_data = self._get_depth(symbol, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(self, symbol, limit=50, extra_data=None, **kwargs):
        """Async get orderbook depth."""
        path, params, extra_data = self._get_depth(symbol, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Kline Methods ====================

    def _get_kline(self, symbol, period="1m", limit=200, extra_data=None, **kwargs):
        """Get kline/candlestick data.

        Args:
            symbol: Trading pair symbol
            period: Kline period (1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w, 1M)
            limit: Number of klines (max 1000)
            extra_data: Extra data for processing

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_period = self._params.get_period(period)
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type)
        params = {
            "symbol": request_symbol,
            "interval": request_period,
            "limit": limit,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": BybitRequestDataSpot._get_kline_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline response."""
        if input_data is None:
            return [], False
        status = input_data.get("retCode") == 0
        result = input_data.get("result", {})
        klines = result.get("list", [])
        return klines, status

    def get_kline(self, symbol, period="1m", limit=200, extra_data=None, **kwargs):
        """Get kline/candlestick data."""
        path, params, extra_data = self._get_kline(symbol, period, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_kline(self, symbol, period="1m", limit=200, extra_data=None, **kwargs):
        """Async get kline/candlestick data."""
        path, params, extra_data = self._get_kline(symbol, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Server Time & Exchange Info Methods ====================

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time."""
        request_type = "get_server_time"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": None,
            },
        )
        return self.request(path, params=params, extra_data=extra_data)

    def get_exchange_info(self, symbol=None, extra_data=None, **kwargs):
        """Get exchange instruments info."""
        request_type = "get_contract"
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbol:
            params["symbol"] = self._params.get_symbol(symbol)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "ALL",
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": BybitRequestDataSpot._generic_normalize_function,
            },
        )
        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _generic_normalize_function(input_data, extra_data):
        """Generic normalize function for Bybit responses."""
        if input_data is None:
            return [], False
        status = input_data.get("retCode") == 0
        result = input_data.get("result", {})
        data = result.get("list", [result])
        return data, status

    # ==================== Account Methods ====================

    def _get_balance(self, account_type="UNIFIED", coin=None, extra_data=None, **kwargs):
        """Get account balance.

        Args:
            account_type: Account type (UNIFIED, SPOT, etc.)
            coin: Specific coin to query
            extra_data: Extra data for processing

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_balance"
        path = self._params.get_rest_path(request_type)
        params = {"accountType": account_type}
        if coin:
            params["coin"] = coin
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": BybitRequestDataSpot._get_balance_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        """Normalize balance response."""
        if input_data is None:
            return [], False
        status = input_data.get("retCode") == 0
        data = [BybitSpotBalanceData(input_data, True)]
        return data, status

    def get_balance(self, account_type="UNIFIED", coin=None, extra_data=None, **kwargs):
        """Get account balance."""
        path, params, extra_data = self._get_balance(account_type, coin, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info (standard interface alias for get_balance)."""
        return self.get_balance(extra_data=extra_data, **kwargs)

    def async_get_balance(self, account_type="UNIFIED", coin=None, extra_data=None, **kwargs):
        """Async get account balance."""
        path, params, extra_data = self._get_balance(account_type, coin, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Trading Methods ====================

    def _make_order(self, symbol, qty, side="Buy", order_type="Limit",
                    price=None, time_in_force="GTC", extra_data=None, **kwargs):
        """Place an order.

        Args:
            symbol: Trading pair symbol
            qty: Order quantity
            side: Buy or Sell
            order_type: Limit or Market
            price: Order price (required for Limit orders)
            time_in_force: GTC, IOC, FOK
            extra_data: Extra data for processing

        Returns:
            tuple: (path, body, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)

        body = {
            "category": "spot",
            "symbol": request_symbol,
            "side": side,
            "orderType": order_type,
            "qty": str(qty),
        }

        if price is not None and order_type == "Limit":
            body["price"] = str(price)
            body["timeInForce"] = time_in_force

        body.update(kwargs)

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": BybitRequestDataSpot._make_order_normalize_function,
            },
        )
        return path, body, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        """Normalize order response."""
        if input_data is None:
            return [], False
        status = input_data.get("retCode") == 0
        result = input_data.get("result", {})
        data = [BybitSpotOrderData(input_data, extra_data["symbol_name"], True)]
        return data, status

    def make_order(self, symbol, qty, side="Buy", order_type="Limit",
                   price=None, time_in_force="GTC", extra_data=None, **kwargs):
        """Place an order."""
        path, body, extra_data = self._make_order(
            symbol, qty, side, order_type, price, time_in_force, extra_data, **kwargs
        )
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def async_make_order(self, symbol, qty, side="Buy", order_type="Limit",
                         price=None, time_in_force="GTC", extra_data=None, **kwargs):
        """Async place an order."""
        path, body, extra_data = self._make_order(
            symbol, qty, side, order_type, price, time_in_force, extra_data, **kwargs
        )
        self.submit(self.async_request(path, params={}, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_order(self, symbol, order_id=None, order_link_id=None, extra_data=None, **kwargs):
        """Cancel an order.

        Args:
            symbol: Trading pair symbol
            order_id: Order ID
            order_link_id: Client order link ID
            extra_data: Extra data for processing

        Returns:
            tuple: (path, body, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)

        body = {
            "category": "spot",
            "symbol": request_symbol,
        }

        if order_id:
            body["orderId"] = str(order_id)
        elif order_link_id:
            body["orderLinkId"] = str(order_link_id)

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": None,
            },
        )
        return path, body, extra_data

    def cancel_order(self, symbol, order_id=None, order_link_id=None, extra_data=None, **kwargs):
        """Cancel an order."""
        path, body, extra_data = self._cancel_order(symbol, order_id, order_link_id, extra_data, **kwargs)
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def _query_order(self, symbol, order_id=None, order_link_id=None, extra_data=None, **kwargs):
        """Query order details.

        Args:
            symbol: Trading pair symbol
            order_id: Order ID
            order_link_id: Client order link ID
            extra_data: Extra data for processing

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_open_orders"
        path = self._params.get_rest_path(request_type)
        params = {
            "category": "spot",
            "symbol": request_symbol,
        }

        if order_id:
            params["orderId"] = str(order_id)
        elif order_link_id:
            params["orderLinkId"] = str(order_link_id)

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": BybitRequestDataSpot._query_order_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        """Normalize query order response."""
        if input_data is None:
            return [], False
        status = input_data.get("retCode") == 0
        data = [BybitSpotOrderData(input_data, extra_data["symbol_name"], True)]
        return data, status

    def query_order(self, symbol, order_id=None, order_link_id=None, extra_data=None, **kwargs):
        """Query order details."""
        path, params, extra_data = self._query_order(symbol, order_id, order_link_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_deals(self, symbol=None, limit=50, extra_data=None, **kwargs):
        """Get trade execution records.

        Args:
            symbol: Trading pair symbol
            limit: Number of records
            extra_data: Extra data for processing

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_deals"
        path = self._params.get_rest_path(request_type)
        params = {
            "category": "spot",
            "limit": limit,
        }
        if symbol:
            params["symbol"] = self._params.get_symbol(symbol)

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "ALL",
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": BybitRequestDataSpot._generic_normalize_function,
            },
        )
        return path, params, extra_data

    def get_deals(self, symbol=None, limit=50, extra_data=None, **kwargs):
        """Get trade execution records."""
        path, params, extra_data = self._get_deals(symbol, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
