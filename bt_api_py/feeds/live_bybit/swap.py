"""Bybit Swap (Linear Futures) Trading Feed"""

from bt_api_py.containers.exchanges.bybit_exchange_data import BybitExchangeDataSwap
from bt_api_py.containers.tickers.bybit_ticker import BybitSwapTickerData
from bt_api_py.containers.orderbooks.bybit_orderbook import BybitSwapOrderBookData
from bt_api_py.containers.orders.bybit_order import BybitSwapOrderData
from bt_api_py.containers.balances.bybit_balance import BybitSwapBalanceData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bybit.request_base import BybitRequestData
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class BybitRequestDataSwap(BybitRequestData):
    """Bybit Swap (Linear) trading REST API feed."""

    def __init__(self, data_queue, **kwargs):
        kwargs["asset_type"] = "swap"
        kwargs.setdefault("logger_name", "bybit_swap_feed.log")
        super().__init__(data_queue, **kwargs)
        self._params = BybitExchangeDataSwap()
        self.request_logger = SpdLogManager(
            self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            self.logger_name, "async_request", 0, 0, False
        ).create_logger()

    # ==================== Market Data Methods ====================

    def _get_ticker(self, symbol, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_tick"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=BybitRequestDataSwap._get_ticker_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_ticker_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        status = input_data.get("retCode") == 0
        data = [BybitSwapTickerData(input_data, extra_data["symbol_name"], True)]
        return data, status

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        return self.get_ticker(symbol, extra_data=extra_data, **kwargs)

    def async_get_ticker(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Depth Methods ====================

    def _get_depth(self, symbol, limit=50, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol, "limit": limit}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=BybitRequestDataSwap._get_depth_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        status = input_data.get("retCode") == 0
        data = [BybitSwapOrderBookData(input_data, extra_data["symbol_name"], True)]
        return data, status

    def get_depth(self, symbol, limit=50, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Kline Methods ====================

    def _get_kline(self, symbol, period="1m", limit=200, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_period = self._params.get_period(period)
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol, "interval": request_period, "limit": limit}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=BybitRequestDataSwap._get_kline_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        status = input_data.get("retCode") == 0
        result = input_data.get("result", {})
        klines = result.get("list", [])
        return klines, status

    def get_kline(self, symbol, period="1m", limit=200, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Server Time & Exchange Info ====================

    def get_server_time(self, extra_data=None, **kwargs):
        request_type = "get_server_time"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name="ALL",
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=None,
        )
        return self.request(path, params=params, extra_data=extra_data)

    def get_exchange_info(self, symbol=None, extra_data=None, **kwargs):
        request_type = "get_contract"
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbol:
            params["symbol"] = self._params.get_symbol(symbol)
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol or "ALL",
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=BybitRequestDataSwap._generic_normalize_function,
        )
        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _generic_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        status = input_data.get("retCode") == 0
        result = input_data.get("result", {})
        data = result.get("list", [result])
        return data, status

    # ==================== Account Methods ====================

    def _get_balance(self, account_type="UNIFIED", coin=None, extra_data=None, **kwargs):
        request_type = "get_balance"
        path = self._params.get_rest_path(request_type)
        params = {"accountType": account_type}
        if coin:
            params["coin"] = coin
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name="ALL",
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=BybitRequestDataSwap._get_balance_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        status = input_data.get("retCode") == 0
        data = [BybitSwapBalanceData(input_data, True)]
        return data, status

    def get_balance(self, account_type="UNIFIED", coin=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(account_type, coin, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        return self.get_balance(extra_data=extra_data, **kwargs)

    # ==================== Trading Methods ====================

    def _make_order(self, symbol, qty, side="Buy", order_type="Limit",
                    price=None, time_in_force="GTC", extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)
        body = {
            "category": "linear",
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
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=BybitRequestDataSwap._make_order_normalize_function,
        )
        return path, body, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        status = input_data.get("retCode") == 0
        data = [BybitSwapOrderData(input_data, extra_data["symbol_name"], True)]
        return data, status

    def make_order(self, symbol, qty, side="Buy", order_type="Limit",
                   price=None, time_in_force="GTC", extra_data=None, **kwargs):
        path, body, extra_data = self._make_order(
            symbol, qty, side, order_type, price, time_in_force, extra_data, **kwargs
        )
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def _cancel_order(self, symbol, order_id=None, order_link_id=None, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)
        body = {"category": "linear", "symbol": request_symbol}
        if order_id:
            body["orderId"] = str(order_id)
        elif order_link_id:
            body["orderLinkId"] = str(order_link_id)
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=None,
        )
        return path, body, extra_data

    def cancel_order(self, symbol, order_id=None, order_link_id=None, extra_data=None, **kwargs):
        path, body, extra_data = self._cancel_order(symbol, order_id, order_link_id, extra_data, **kwargs)
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def _query_order(self, symbol, order_id=None, order_link_id=None, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_open_orders"
        path = self._params.get_rest_path(request_type)
        params = {"category": "linear", "symbol": request_symbol}
        if order_id:
            params["orderId"] = str(order_id)
        elif order_link_id:
            params["orderLinkId"] = str(order_link_id)
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=BybitRequestDataSwap._query_order_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        status = input_data.get("retCode") == 0
        data = [BybitSwapOrderData(input_data, extra_data["symbol_name"], True)]
        return data, status

    def query_order(self, symbol, order_id=None, order_link_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(symbol, order_id, order_link_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_deals(self, symbol=None, limit=50, extra_data=None, **kwargs):
        request_type = "get_deals"
        path = self._params.get_rest_path(request_type)
        params = {"category": "linear", "limit": limit}
        if symbol:
            params["symbol"] = self._params.get_symbol(symbol)
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol or "ALL",
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=BybitRequestDataSwap._generic_normalize_function,
        )
        return path, params, extra_data

    def get_deals(self, symbol=None, limit=50, extra_data=None, **kwargs):
        path, params, extra_data = self._get_deals(symbol, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
