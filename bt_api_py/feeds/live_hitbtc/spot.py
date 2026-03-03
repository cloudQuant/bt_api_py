"""
HitBTC Spot Trading Implementation

This module implements spot trading functionality for HitBTC exchange.
Provides market data and trading operations through REST API.
"""

import time
from bt_api_py.containers.exchanges.hitbtc_exchange_data import HitBtcExchangeData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.hitbtc_ticker import HitBtcRequestTickerData
from bt_api_py.containers.orderbooks.hitbtc_orderbook import HitBtcRequestOrderBookData
from bt_api_py.containers.trades.hitbtc_trade import HitBtcRequestTradeData
from bt_api_py.containers.bars.hitbtc_bar import HitBtcRequestBarData
from bt_api_py.containers.orders.hitbtc_order import HitBtcRequestOrderData
from bt_api_py.containers.balances.hitbtc_balance import HitBtcRequestBalanceData
from bt_api_py.error_framework_hitbtc import HitBtcErrorTranslator
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_hitbtc.request_base import HitBtcRequestData


class HitBtcSpotRequestData(HitBtcRequestData):
    @classmethod
    def _capabilities(cls):
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
            Capability.MARKET_STREAM,
            Capability.ACCOUNT_STREAM,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_SERVER_TIME,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = "HITBTC_SPOT"
        self.asset_type = "SPOT"
        self._params = HitBtcExchangeData()

    # Market Data Methods

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker for a symbol"""
        request_type = "get_ticker"
        path = self._params.get_rest_path(request_type, symbol)
        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            normalize_function=self._get_ticker_normalize_function,
        )
        return self.request("GET", path, extra_data=extra_data, is_sign=False)

    def _get_ticker_normalize_function(self, input_data, extra_data):
        status = input_data is not None
        symbol_name = extra_data.get("symbol_name")
        if status and isinstance(input_data, dict):
            data = HitBtcRequestTickerData(input_data, symbol_name, self.asset_type, True)
        else:
            data = None
        return data, status

    def get_ticker_all(self, extra_data=None, **kwargs):
        """Get all tickers"""
        request_type = "get_ticker_all"
        path = self._params.get_rest_path(request_type)
        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_ticker_all_normalize_function,
        )
        return self.request("GET", path, extra_data=extra_data, is_sign=False)

    def _get_ticker_all_normalize_function(self, input_data, extra_data):
        status = input_data is not None
        if status and isinstance(input_data, dict):
            data_list = []
            for symbol, ticker_data in input_data.items():
                ticker = HitBtcRequestTickerData(ticker_data, symbol, self.asset_type, True)
                data_list.append(ticker)
            data = data_list
        else:
            data = []
        return data, status

    def get_orderbook(self, symbol, depth=10, extra_data=None, **kwargs):
        """Get orderbook for a symbol"""
        request_type = "get_orderbook"
        path = self._params.get_rest_path(request_type, symbol)
        params = {"depth": depth}
        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            normalize_function=self._get_orderbook_normalize_function,
        )
        return self.request("GET", path, params=params, extra_data=extra_data, is_sign=False)

    def _get_orderbook_normalize_function(self, input_data, extra_data):
        status = input_data is not None
        symbol_name = extra_data.get("symbol_name")
        if status and isinstance(input_data, dict):
            data = HitBtcRequestOrderBookData(input_data, symbol_name, self.asset_type, True)
        else:
            data = None
        return data, status

    def get_trades(self, symbol, sort="ASC", limit=10, extra_data=None, **kwargs):
        """Get trades for a symbol"""
        request_type = "get_trades"
        path = self._params.get_rest_path(request_type, symbol)
        params = {"sort": sort, "limit": limit}
        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            normalize_function=self._get_trades_normalize_function,
        )
        return self.request("GET", path, params=params, extra_data=extra_data, is_sign=False)

    def _get_trades_normalize_function(self, input_data, extra_data):
        status = input_data is not None
        symbol_name = extra_data.get("symbol_name")
        if status and isinstance(input_data, list):
            data_list = []
            for trade_data in input_data:
                trade = HitBtcRequestTradeData(trade_data, symbol_name, self.asset_type, True)
                data_list.append(trade)
            data = data_list
        else:
            data = []
        return data, status

    def get_candles(self, symbol, period="M30", limit=100, extra_data=None, **kwargs):
        """Get kline data for a symbol"""
        request_type = "get_candles"
        path = self._params.get_rest_path(request_type, symbol)
        params = {"period": period, "limit": limit}
        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            normalize_function=self._get_candles_normalize_function,
        )
        return self.request("GET", path, params=params, extra_data=extra_data, is_sign=False)

    def _get_candles_normalize_function(self, input_data, extra_data):
        status = input_data is not None
        symbol_name = extra_data.get("symbol_name")
        if status and isinstance(input_data, list):
            data_list = []
            for candle_data in input_data:
                candle = HitBtcRequestBarData(candle_data, symbol_name, self.asset_type, True)
                data_list.append(candle)
            data = data_list
        else:
            data = []
        return data, status

    # Trading Methods

    def place_order(self, symbol, side, type, quantity, price=None,
                   time_in_force="GTC", client_order_id=None,
                   post_only=False, stop_price=None, extra_data=None, **kwargs):
        """Place an order"""
        request_type = "place_order"
        path = self._params.get_rest_path(request_type)

        order_data = {
            "symbol": symbol,
            "side": side,
            "type": type,
            "quantity": str(quantity),
        }

        if price:
            order_data["price"] = str(price)
        if stop_price:
            order_data["stop_price"] = str(stop_price)
        if time_in_force:
            order_data["time_in_force"] = time_in_force
        if client_order_id:
            order_data["client_order_id"] = client_order_id
        if post_only:
            order_data["post_only"] = str(post_only).lower()

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            side=side,
            order_type=type,
            exchange_name=self.exchange_name,
            normalize_function=self._place_order_normalize_function,
        )

        return self.request("POST", path, body=order_data, extra_data=extra_data, is_sign=True)

    def _place_order_normalize_function(self, input_data, extra_data):
        status = input_data is not None
        symbol_name = extra_data.get("symbol_name")
        if status and isinstance(input_data, dict):
            data = HitBtcRequestOrderData(input_data, symbol_name, self.asset_type, True)
        else:
            data = None
        return data, status

    def cancel_order(self, client_order_id, symbol=None, extra_data=None, **kwargs):
        """Cancel an order by client order id"""
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type, client_order_id)
        params = {}
        if symbol:
            params["symbol"] = symbol

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            client_order_id=client_order_id,
            exchange_name=self.exchange_name,
            normalize_function=self._cancel_order_normalize_function,
        )

        return self.request("DELETE", path, params=params, extra_data=extra_data, is_sign=True)

    def _cancel_order_normalize_function(self, input_data, extra_data):
        status = input_data is not None
        return status, input_data

    def cancel_all_orders(self, symbol, extra_data=None, **kwargs):
        """Cancel all orders for a symbol"""
        request_type = "cancel_all_orders"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": symbol}

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
        )

        return self.request("DELETE", path, params=params, extra_data=extra_data, is_sign=True)

    def get_order(self, client_order_id, symbol=None, extra_data=None, **kwargs):
        """Get order by client order id"""
        request_type = "get_order"
        path = self._params.get_rest_path(request_type, client_order_id)
        params = {}
        if symbol:
            params["symbol"] = symbol

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            client_order_id=client_order_id,
            exchange_name=self.exchange_name,
            normalize_function=self._get_order_normalize_function,
        )

        return self.request("GET", path, params=params, extra_data=extra_data, is_sign=True)

    def _get_order_normalize_function(self, input_data, extra_data):
        status = input_data is not None
        symbol_name = extra_data.get("symbol_name")
        if status and isinstance(input_data, dict):
            data = HitBtcRequestOrderData(input_data, symbol_name, self.asset_type, True)
        else:
            data = None
        return data, status

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get all open orders"""
        request_type = "get_open_orders"
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbol:
            params["symbol"] = symbol

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            normalize_function=self._get_open_orders_normalize_function,
        )

        return self.request("GET", path, params=params, extra_data=extra_data, is_sign=True)

    def _get_open_orders_normalize_function(self, input_data, extra_data):
        status = input_data is not None
        symbol_name = extra_data.get("symbol_name")
        if status and isinstance(input_data, list):
            data_list = []
            for order_data in input_data:
                order = HitBtcRequestOrderData(order_data, symbol_name, self.asset_type, True)
                data_list.append(order)
            data = data_list
        else:
            data = []
        return data, status

    def get_order_history(self, symbol=None, sort="ASC", limit=100, extra_data=None, **kwargs):
        """Get order history"""
        request_type = "get_order_history"
        path = self._params.get_rest_path(request_type)
        params = {"sort": sort, "limit": limit}
        if symbol:
            params["symbol"] = symbol

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            normalize_function=self._get_order_history_normalize_function,
        )

        return self.request("GET", path, params=params, extra_data=extra_data, is_sign=True)

    def _get_order_history_normalize_function(self, input_data, extra_data):
        status = input_data is not None
        symbol_name = extra_data.get("symbol_name")
        if status and isinstance(input_data, list):
            data_list = []
            for order_data in input_data:
                order = HitBtcRequestOrderData(order_data, symbol_name, self.asset_type, True)
                data_list.append(order)
            data = data_list
        else:
            data = []
        return data, status

    def get_trades_history(self, symbol=None, sort="ASC", limit=100, extra_data=None, **kwargs):
        """Get trades history"""
        request_type = "get_trades_history"
        path = self._params.get_rest_path(request_type)
        params = {"sort": sort, "limit": limit}
        if symbol:
            params["symbol"] = symbol

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            normalize_function=self._get_trades_history_normalize_function,
        )

        return self.request("GET", path, params=params, extra_data=extra_data, is_sign=True)

    def _get_trades_history_normalize_function(self, input_data, extra_data):
        status = input_data is not None
        symbol_name = extra_data.get("symbol_name")
        if status and isinstance(input_data, list):
            data_list = []
            for trade_data in input_data:
                trade = HitBtcRequestTradeData(trade_data, symbol_name, self.asset_type, True)
                data_list.append(trade)
            data = data_list
        else:
            data = []
        return data, status

    # Account Methods

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get account balance"""
        request_type = "get_balance"
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbol:
            params["currency"] = symbol

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            normalize_function=self._get_balance_normalize_function,
        )

        return self.request("GET", path, params=params, extra_data=extra_data, is_sign=True)

    def _get_balance_normalize_function(self, input_data, extra_data):
        status = input_data is not None
        if status and isinstance(input_data, list):
            data_list = []
            for balance_data in input_data:
                balance = HitBtcRequestBalanceData(balance_data, None, self.asset_type, True)
                data_list.append(balance)
            data = data_list
        else:
            data = []
        return data, status

    # Async methods

    def async_get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker async"""
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request("GET", path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    def _get_ticker(self, symbol, extra_data=None, **kwargs):
        request_type = "get_ticker"
        path = self._params.get_rest_path(request_type, symbol)
        params = {}
        extra_data = self._update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_ticker_normalize_function,
            },
        )
        return path, params, extra_data

    def async_get_orderbook(self, symbol, depth=10, extra_data=None, **kwargs):
        """Get orderbook async"""
        path, params, extra_data = self._get_orderbook(symbol, depth, extra_data, **kwargs)
        self.submit(
            self.async_request("GET", path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    def _get_orderbook(self, symbol, depth=10, extra_data=None, **kwargs):
        request_type = "get_orderbook"
        path = self._params.get_rest_path(request_type, symbol)
        params = {"depth": depth}
        extra_data = self._update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_orderbook_normalize_function,
            },
        )
        return path, params, extra_data