"""
KuCoin Futures trading feed implementation.
"""

import uuid
from typing import Any

from bt_api_py.containers.exchanges.kucoin_exchange_data import KuCoinExchangeDataFutures
from bt_api_py.containers.orderbooks.kucoin_orderbook import KuCoinRequestOrderBookData
from bt_api_py.containers.orders.kucoin_order import KuCoinRequestOrderData
from bt_api_py.containers.tickers.kucoin_ticker import KuCoinRequestTickerData
from bt_api_py.feeds.live_kucoin.request_base import KuCoinRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class KuCoinRequestDataFutures(KuCoinRequestData):
    """KuCoin Futures trading REST API implementation."""

    def __init__(self, data_queue, **kwargs) -> Any | None:
        kwargs.setdefault("asset_type", "FUTURES")
        kwargs.setdefault("logger_name", "kucoin_futures_feed.log")
        super().__init__(data_queue, **kwargs)
        self.asset_type = "FUTURES"
        self._params = KuCoinExchangeDataFutures()
        self.request_logger = get_logger("kucoin_futures")
        self.async_logger = get_logger("kucoin_futures")

    # ==================== Market Data ====================

    def _get_ticker(self, symbol, extra_data=None, **kwargs) -> Any | None:
        request_type = "get_tick"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": symbol}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=KuCoinRequestDataFutures._get_ticker_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_ticker_normalize_function(input_data, extra_data) -> Any | None:
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if input_data and "data" in input_data:
            return [KuCoinRequestTickerData(input_data, symbol_name, asset_type, True)], status
        return [], status

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_ticker(symbol=symbol, extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    get_tick = get_ticker

    def async_get_ticker(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_ticker(symbol=symbol, extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    async_get_tick = async_get_ticker

    # ==================== Depth ====================

    def _get_depth(self, symbol, limit=20, extra_data=None, **kwargs) -> Any | None:
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": symbol}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=KuCoinRequestDataFutures._get_depth_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data) -> Any | None:
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if input_data and "data" in input_data:
            return [KuCoinRequestOrderBookData(input_data, symbol_name, asset_type, True)], status
        return [], status

    def get_depth(self, symbol, limit=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(
            symbol=symbol, limit=limit, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def async_get_depth(self, symbol, limit=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(
            symbol=symbol, limit=limit, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    # ==================== Kline ====================

    def _get_kline(
        self,
        symbol,
        period="1hour",
        start_time=None,
        end_time=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ) -> Any | None:
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": symbol, "granularity": self._params.get_period(period)}
        if start_time:
            params["from"] = int(start_time)
        if end_time:
            params["to"] = int(end_time)
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=None,
        )
        return path, params, extra_data

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
        path, params, extra_data = self._get_kline(
            symbol=symbol,
            period=period,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            extra_data=extra_data,
            **kwargs,
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

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

    # ==================== Server Time & Exchange Info ====================

    def _get_server_time(self, extra_data=None, **kwargs) -> Any | None:
        request_type = "get_server_time"
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name="ALL",
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=None,
        )
        return path, {}, extra_data

    def get_server_time(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_server_time(extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    def _get_exchange_info(self, extra_data=None, **kwargs) -> Any | None:
        request_type = "get_contract"
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name="ALL",
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=KuCoinRequestDataFutures._get_exchange_info_normalize_function,
        )
        return path, {}, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data) -> Any | None:
        if input_data and isinstance(input_data, dict) and "data" in input_data:
            data = input_data["data"]
            if isinstance(data, list):
                return data, True
            return [data], True
        return [], False

    def get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_exchange_info(extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=False)

    # ==================== Account ====================

    def _get_account(self, currency=None, extra_data=None, **kwargs) -> Any | None:
        request_type = "get_account"
        path = self._params.get_rest_path(request_type)
        params = {}
        if currency:
            params["currency"] = currency
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=currency or "ALL",
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=None,
        )
        return path, params, extra_data

    def get_account(self, currency=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_account(
            currency=currency, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        return self.get_account(currency=symbol, extra_data=extra_data, **kwargs)

    def async_get_account(self, currency=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_account(
            currency=currency, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
            callback=self.async_callback,
        )

    def async_get_balance(self, currency=None, extra_data=None, **kwargs):
        self.async_get_account(currency=currency, extra_data=extra_data, **kwargs)

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
    ) -> Any | None:
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)
        side, ord_type = order_type.split("-")
        side = side.upper()
        ord_type = ord_type.upper()
        if client_order_id is None:
            client_order_id = str(uuid.uuid4())
        params = {
            "clientOid": client_order_id,
            "side": side.lower(),
            "symbol": symbol,
            "type": ord_type.lower(),
            "size": str(vol),
            "leverage": str(kwargs.get("leverage", 1)),
        }
        if ord_type == "LIMIT" and price is not None:
            params["price"] = str(price)
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=KuCoinRequestDataFutures._make_order_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data) -> Any | None:
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if input_data and "data" in input_data and "orderId" in input_data["data"]:
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
        path, params, extra_data = self._make_order(
            symbol,
            vol,
            price,
            order_type,
            offset,
            post_only,
            client_order_id,
            extra_data,
            **kwargs,
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def _cancel_order(self, order_id=None, extra_data=None, **kwargs) -> Any | None:
        if order_id is None:
            raise ValueError("order_id must be provided")
        path = f"DELETE /api/v1/orders/{order_id}"
        params = {}
        extra_data = update_extra_data(
            extra_data,
            request_type="cancel_order",
            symbol_name=kwargs.get("symbol", "UNKNOWN"),
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=None,
        )
        return path, params, extra_data

    def cancel_order(self, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_order(
            order_id=order_id, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs) -> Any | None:
        request_type = "get_open_orders"
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbol:
            params["symbol"] = symbol
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol or "ALL",
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=None,
        )
        return path, params, extra_data

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def query_order(self, order_id=None, extra_data=None, **kwargs):
        if order_id is None:
            raise ValueError("order_id must be provided")
        path = f"GET /api/v1/orders/{order_id}"
        params = {}
        extra_data = update_extra_data(
            extra_data,
            request_type="query_order",
            symbol_name=kwargs.get("symbol", "UNKNOWN"),
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=None,
        )
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)


# ==================== WebSocket Placeholder Classes ====================


class KuCoinMarketWssDataFutures:
    """Placeholder for KuCoin Futures Market WebSocket data handler."""

    pass


class KuCoinAccountWssDataFutures:
    """Placeholder for KuCoin Futures Account WebSocket data handler."""

    pass
