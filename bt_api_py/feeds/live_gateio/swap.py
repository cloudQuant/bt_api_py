"""
Gate.io Futures (USDT-M) Trading Feed Implementation
"""

from typing import Any

from bt_api_py.containers.balances.gateio_balance import GateioBalanceData
from bt_api_py.containers.exchanges.gateio_exchange_data import GateioExchangeDataSwap
from bt_api_py.containers.orderbooks.gateio_orderbook import GateioOrderBookData
from bt_api_py.containers.orders.gateio_order import GateioOrderData
from bt_api_py.containers.tickers.gateio_ticker import GateioTickerData
from bt_api_py.feeds.live_gateio.request_base import GateioRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class GateioRequestDataSwap(GateioRequestData):
    """Gate.io Futures (USDT-M) Trading Feed"""

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        kwargs["asset_type"] = "swap"
        kwargs.setdefault("logger_name", "gateio_swap_feed.log")
        super().__init__(data_queue, **kwargs)
        self._params = GateioExchangeDataSwap()
        self.request_logger = get_logger("request")
        self.async_logger = get_logger("async_request")

    # ==================== Market Data Methods ====================

    def _get_ticker(self, symbol, extra_data=None, **kwargs) -> Any:
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_tickers"
        path = self._params.get_rest_path(request_type)
        params = {"currency_pair": request_symbol}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=GateioRequestDataSwap._get_ticker_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_ticker_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, dict) and "label" in input_data:
            return [], False
        if isinstance(input_data, list):
            return [
                GateioTickerData(item, extra_data["symbol_name"], extra_data["asset_type"], True)
                for item in input_data
            ], True
        if isinstance(input_data, dict):
            return [
                GateioTickerData(
                    input_data, extra_data["symbol_name"], extra_data["asset_type"], True
                )
            ], True
        return [], False

    def get_ticker(self, symbol, extra_data=None, **kwargs) -> Any:
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_tick(self, symbol, extra_data=None, **kwargs) -> Any:
        return self.get_ticker(symbol, extra_data=extra_data, **kwargs)

    def async_get_ticker(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Depth Methods ====================

    def _get_depth(self, symbol, limit=20, extra_data=None, **kwargs) -> Any:
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type)
        params = {"currency_pair": request_symbol, "limit": limit}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=GateioRequestDataSwap._get_depth_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, dict) and "label" in input_data:
            return [], False
        if isinstance(input_data, dict):
            return [
                GateioOrderBookData(
                    input_data, extra_data["symbol_name"], extra_data["asset_type"], True
                )
            ], True
        return [], False

    def get_depth(self, symbol, limit=20, extra_data=None, **kwargs) -> Any:
        path, params, extra_data = self._get_depth(symbol, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(self, symbol, limit=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, limit, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Kline Methods ====================

    def _get_kline(self, symbol, period="1m", limit=100, extra_data=None, **kwargs) -> Any:
        request_symbol = self._params.get_symbol(symbol)
        request_period = self._params.get_period(period)
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type)
        params = {"currency_pair": request_symbol, "interval": request_period, "limit": limit}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=GateioRequestDataSwap._get_kline_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, dict) and "label" in input_data:
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        return [input_data], True

    def get_kline(self, symbol, period="1m", limit=100, extra_data=None, **kwargs) -> Any:
        path, params, extra_data = self._get_kline(symbol, period, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_kline(self, symbol, period="1m", limit=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, limit, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Account Methods ====================

    def _get_balance(self, extra_data=None, **kwargs) -> Any:
        request_type = "get_account"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name="ALL",
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=GateioRequestDataSwap._get_balance_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, dict) and "label" in input_data:
            return [], False
        if isinstance(input_data, list):
            return [
                GateioBalanceData(item, extra_data["asset_type"], True) for item in input_data
            ], True
        if isinstance(input_data, dict):
            return [GateioBalanceData(input_data, extra_data["asset_type"], True)], True
        return [], False

    def get_balance(self, extra_data=None, **kwargs) -> Any:
        path, params, extra_data = self._get_balance(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_balance(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

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

        parts = order_type.split("-")
        parts[0].lower() if len(parts) >= 1 else "buy"
        otype = parts[1].lower() if len(parts) >= 2 else "limit"

        body = {
            "contract": request_symbol,
            "size": int(vol),
            "price": str(price) if price else "0",
        }
        if otype == "market":
            body["price"] = "0"
            body["tif"] = "ioc"
        else:
            body["tif"] = kwargs.get("time_in_force", "gtc")

        if client_order_id:
            body["text"] = client_order_id

        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=GateioRequestDataSwap._make_order_normalize_function,
        )
        return path, body, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, dict) and "label" in input_data:
            return [], False
        if isinstance(input_data, list):
            return [
                GateioOrderData(o, extra_data["symbol_name"], extra_data["asset_type"], True)
                for o in input_data
            ], True
        if isinstance(input_data, dict):
            return [
                GateioOrderData(
                    input_data, extra_data["symbol_name"], extra_data["asset_type"], True
                )
            ], True
        return [], False

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

    def _cancel_order(
        self, symbol, order_id=None, client_order_id=None, extra_data=None, **kwargs
    ) -> Any:
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        body = {}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            order_id=order_id,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=None,
        )
        return path, params, body, extra_data

    def cancel_order(self, symbol, order_id=None, client_order_id=None, extra_data=None, **kwargs):
        path, params, body, extra_data = self._cancel_order(
            symbol, order_id, client_order_id, extra_data, **kwargs
        )
        return self.request(path, params=params, body=body, extra_data=extra_data)

    def _query_order(
        self, symbol, order_id=None, client_order_id=None, extra_data=None, **kwargs
    ) -> Any:
        request_type = "query_order"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            order_id=order_id,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=GateioRequestDataSwap._query_order_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, dict) and "label" in input_data:
            return [], False
        if isinstance(input_data, list):
            return [
                GateioOrderData(o, extra_data["symbol_name"], extra_data["asset_type"], True)
                for o in input_data
            ], True
        if isinstance(input_data, dict):
            return [
                GateioOrderData(
                    input_data, extra_data["symbol_name"], extra_data["asset_type"], True
                )
            ], True
        return [], False

    def query_order(self, symbol, order_id=None, client_order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(
            symbol, order_id, client_order_id, extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    def _get_deals(self, symbol=None, limit=100, extra_data=None, **kwargs) -> Any:
        request_type = "get_deals"
        path = self._params.get_rest_path(request_type)
        params = {"limit": limit}
        if symbol:
            params["currency_pair"] = self._params.get_symbol(symbol)
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol or "ALL",
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=GateioRequestData._extract_data_normalize_function,
        )
        return path, params, extra_data

    def get_deals(self, symbol=None, limit=100, extra_data=None, **kwargs) -> Any:
        path, params, extra_data = self._get_deals(symbol, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)


# ==================== WebSocket Placeholder Classes ====================


class GateioMarketWssDataSwap:
    """Placeholder for Gate.io Futures Market WebSocket data handler."""


class GateioAccountWssDataSwap:
    """Placeholder for Gate.io Futures Account WebSocket data handler."""
