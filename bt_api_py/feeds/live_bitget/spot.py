"""Bitget Spot Trading Feed"""

from bt_api_py.containers.balances.bitget_balance import BitgetBalanceData
from bt_api_py.containers.exchanges.bitget_exchange_data import BitgetExchangeDataSpot
from bt_api_py.containers.orderbooks.bitget_orderbook import BitgetOrderBookData
from bt_api_py.containers.orders.bitget_order import BitgetOrderData
from bt_api_py.containers.tickers.bitget_ticker import BitgetTickerData
from bt_api_py.feeds.live_bitget.request_base import BitgetRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class BitgetRequestDataSpot(BitgetRequestData):
    """Bitget Spot trading REST API feed."""

    def __init__(self, data_queue, **kwargs):
        kwargs["asset_type"] = "spot"
        kwargs.setdefault("logger_name", "bitget_spot_feed.log")
        super().__init__(data_queue, **kwargs)
        self._params = BitgetExchangeDataSpot()
        self.request_logger = get_logger("request")
        self.async_logger = get_logger("async_request")

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
            normalize_function=BitgetRequestDataSpot._get_ticker_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_ticker_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        status = input_data.get("code") == "00000" if isinstance(input_data, dict) else False
        data_list = input_data.get("data", []) if isinstance(input_data, dict) else []
        if isinstance(data_list, dict):
            data_list = [data_list]
        result = []
        for item in data_list:
            result.append(
                BitgetTickerData(item, extra_data["symbol_name"], extra_data["asset_type"], True)
            )
        return result, status

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        return self.get_ticker(symbol, extra_data=extra_data, **kwargs)

    def async_get_ticker(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        self.async_get_ticker(symbol, extra_data=extra_data, **kwargs)

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
            normalize_function=BitgetRequestDataSpot._get_depth_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        status = input_data.get("code") == "00000" if isinstance(input_data, dict) else False
        data = input_data.get("data", {}) if isinstance(input_data, dict) else {}
        if not data:
            return [], status
        result = [
            BitgetOrderBookData(data, extra_data["symbol_name"], extra_data["asset_type"], True)
        ]
        return result, status

    def get_depth(self, symbol, limit=50, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_orderbook(self, symbol, limit=50, extra_data=None, **kwargs):
        return self.get_depth(symbol, limit, extra_data=extra_data, **kwargs)

    def async_get_depth(self, symbol, limit=50, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, limit, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Kline Methods ====================

    def _get_kline(self, symbol, period="1m", limit=200, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_period = self._params.get_period(period)
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol, "granularity": request_period, "limit": limit}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=BitgetRequestDataSpot._get_kline_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        status = input_data.get("code") == "00000" if isinstance(input_data, dict) else False
        data = input_data.get("data", []) if isinstance(input_data, dict) else []
        if isinstance(data, list):
            return data, status
        return [data], status

    def get_kline(self, symbol, period="1m", limit=200, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_klines(self, symbol, interval, limit=100, extra_data=None, **kwargs):
        return self.get_kline(symbol, period=interval, limit=limit, extra_data=extra_data, **kwargs)

    def async_get_kline(self, symbol, period="1m", limit=200, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, limit, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

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
            normalize_function=BitgetRequestData._extract_data_normalize_function,
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
            normalize_function=BitgetRequestData._extract_data_normalize_function,
        )
        return self.request(path, params=params, extra_data=extra_data)

    def get_symbols(self, extra_data=None, **kwargs):
        return self.get_exchange_info(extra_data=extra_data, **kwargs)

    # ==================== Account Methods ====================

    def _get_balance(self, extra_data=None, **kwargs):
        request_type = "get_balance"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name="ALL",
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=BitgetRequestDataSpot._get_balance_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        status = input_data.get("code") == "00000" if isinstance(input_data, dict) else False
        data_list = input_data.get("data", []) if isinstance(input_data, dict) else []
        if isinstance(data_list, dict):
            data_list = [data_list]
        result = []
        for item in data_list:
            result.append(BitgetBalanceData(item, "ALL", extra_data["asset_type"], True))
        return result, status

    def get_balance(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        request_type = "get_account"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name="ALL",
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=BitgetRequestData._extract_data_normalize_function,
        )
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_balance(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_get_account(self, extra_data=None, **kwargs):
        self.async_get_balance(extra_data=extra_data, **kwargs)

    # ==================== Trading Methods ====================

    def _make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)

        # Parse order_type like "buy-limit" -> side=BUY, orderType=LIMIT
        parts = order_type.split("-")
        side = parts[0].upper() if len(parts) >= 1 else "BUY"
        otype = parts[1].upper() if len(parts) >= 2 else "LIMIT"

        body = {
            "symbol": request_symbol,
            "side": side,
            "orderType": otype,
            "size": vol,
        }
        if price is not None and otype == "LIMIT":
            body["price"] = price
        if client_order_id:
            body["clientOid"] = client_order_id

        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=BitgetRequestDataSpot._make_order_normalize_function,
        )
        return path, body, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        status = input_data.get("code") == "00000" if isinstance(input_data, dict) else False
        data = input_data.get("data", {}) if isinstance(input_data, dict) else {}
        if not data:
            return [], status
        if isinstance(data, list):
            result = [
                BitgetOrderData(d, extra_data["symbol_name"], extra_data["asset_type"], True)
                for d in data
            ]
        else:
            result = [
                BitgetOrderData(data, extra_data["symbol_name"], extra_data["asset_type"], True)
            ]
        return result, status

    def make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        path, body, extra_data = self._make_order(
            symbol, vol, price, order_type, client_order_id, extra_data, **kwargs
        )
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def async_make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        path, body, extra_data = self._make_order(
            symbol, vol, price, order_type, client_order_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params={}, body=body, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _cancel_order(self, symbol, order_id=None, client_order_id=None, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)
        body = {"symbol": request_symbol}
        if order_id:
            body["orderId"] = str(order_id)
        if client_order_id:
            body["clientOid"] = str(client_order_id)
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=None,
        )
        return path, body, extra_data

    def cancel_order(self, symbol, order_id=None, client_order_id=None, extra_data=None, **kwargs):
        path, body, extra_data = self._cancel_order(
            symbol, order_id, client_order_id, extra_data, **kwargs
        )
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def _query_order(self, symbol, order_id=None, client_order_id=None, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "query_order"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol}
        if order_id:
            params["orderId"] = str(order_id)
        if client_order_id:
            params["clientOid"] = str(client_order_id)
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=BitgetRequestDataSpot._query_order_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        status = input_data.get("code") == "00000" if isinstance(input_data, dict) else False
        data = input_data.get("data", {}) if isinstance(input_data, dict) else {}
        if not data:
            return [], status
        if isinstance(data, list):
            result = [
                BitgetOrderData(d, extra_data["symbol_name"], extra_data["asset_type"], True)
                for d in data
            ]
        else:
            result = [
                BitgetOrderData(data, extra_data["symbol_name"], extra_data["asset_type"], True)
            ]
        return result, status

    def query_order(self, symbol, order_id=None, client_order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(
            symbol, order_id, client_order_id, extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    def _get_deals(self, symbol=None, limit=50, extra_data=None, **kwargs):
        request_type = "get_deals"
        path = self._params.get_rest_path(request_type)
        params = {"limit": limit}
        if symbol:
            params["symbol"] = self._params.get_symbol(symbol)
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol or "ALL",
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=BitgetRequestData._extract_data_normalize_function,
        )
        return path, params, extra_data

    def get_deals(self, symbol=None, limit=50, extra_data=None, **kwargs):
        path, params, extra_data = self._get_deals(symbol, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)


# ==================== WebSocket Placeholder Classes ====================


class BitgetMarketWssDataSpot:
    """Placeholder for Bitget Spot Market WebSocket data handler."""

    pass


class BitgetAccountWssDataSpot:
    """Placeholder for Bitget Spot Account WebSocket data handler."""

    pass
