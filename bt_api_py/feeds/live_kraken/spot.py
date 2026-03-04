"""
Kraken Spot Trading Feed Implementation
"""

import time

from bt_api_py.containers.exchanges.kraken_exchange_data import KrakenExchangeDataSpot
from bt_api_py.containers.tickers.kraken_ticker import KrakenRequestTickerData
from bt_api_py.containers.orderbooks.kraken_orderbook import KrakenRequestOrderBookData
from bt_api_py.containers.orders.kraken_order import KrakenRequestOrderData
from bt_api_py.containers.balances.kraken_balance import KrakenSpotWssBalanceData
from bt_api_py.feeds.live_kraken.request_base import KrakenRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class KrakenRequestDataSpot(KrakenRequestData):
    """Kraken Spot Trading Feed"""

    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault("asset_type", "SPOT")
        kwargs.setdefault("logger_name", "kraken_spot_feed.log")
        super().__init__(data_queue, **kwargs)
        self._params = KrakenExchangeDataSpot()
        self.asset_type = "SPOT"
        self.request_logger = get_logger("request")
        self.async_logger = get_logger("async_request")

    # ==================== Market Data Methods ====================

    def _get_ticker(self, symbol, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_tick"
        path = self._params.get_rest_path(request_type)
        params = {"pair": request_symbol}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=KrakenRequestDataSpot._get_ticker_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_ticker_normalize_function(input_data, extra_data):
        """Kraken ticker response: {"error":[], "result":{"XBTUSD":{...}}}"""
        if input_data is None:
            return [], False
        if isinstance(input_data, dict):
            errors = input_data.get("error", [])
            if errors:
                return [], False
            result = input_data.get("result", input_data)
            tickers = []
            for pair_name, ticker_data in result.items():
                if not isinstance(ticker_data, dict):
                    continue
                tickers.append(KrakenRequestTickerData(
                    {"error": [], "result": {pair_name: ticker_data}},
                    extra_data["symbol_name"],
                    extra_data["asset_type"],
                ))
            return tickers, bool(tickers)
        return [], False

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        return self.get_ticker(symbol, extra_data=extra_data, **kwargs)

    def async_get_ticker(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        self.async_get_ticker(symbol, extra_data=extra_data, **kwargs)

    # ==================== Depth Methods ====================

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type)
        params = {"pair": request_symbol, "count": min(count, 500)}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=KrakenRequestDataSpot._get_depth_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Kraken depth response: {"error":[], "result":{"XBTUSD":{"asks":[...],"bids":[...]}}}"""
        if input_data is None:
            return [], False
        if isinstance(input_data, dict):
            errors = input_data.get("error", [])
            if errors:
                return [], False
            result = input_data.get("result", input_data)
            books = []
            for pair_name, book_data in result.items():
                if not isinstance(book_data, dict):
                    continue
                books.append(KrakenRequestOrderBookData(
                    {"error": [], "result": {pair_name: book_data}},
                    extra_data["symbol_name"],
                    extra_data["asset_type"],
                ))
            return books, bool(books)
        return [], False

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Kline Methods ====================

    def _get_kline(self, symbol, period="1m", count=100, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_period = self._params.get_period(period)
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type)
        params = {"pair": request_symbol, "interval": request_period}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=KrakenRequestData._extract_data_normalize_function,
        )
        return path, params, extra_data

    def get_kline(self, symbol, period="1m", count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_kline(self, symbol, period="1m", count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Server Time & Exchange Info ====================

    def _get_server_time(self, extra_data=None, **kwargs):
        request_type = "get_server_time"
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name="ALL",
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=KrakenRequestData._extract_data_normalize_function,
        )
        return path, {}, extra_data

    def get_server_time(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_exchange_info(self, symbol=None, extra_data=None, **kwargs):
        request_type = "get_exchange_info"
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbol:
            params["pair"] = self._params.get_symbol(symbol)
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol or "ALL",
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=KrakenRequestData._extract_data_normalize_function,
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
            normalize_function=KrakenRequestDataSpot._get_balance_normalize_function,
        )
        return path, params, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        """Kraken balance: {"error":[], "result":{"XXBT":"0.5","ZEUR":"1000"}}"""
        if input_data is None:
            return [], False
        if isinstance(input_data, dict):
            errors = input_data.get("error", [])
            if errors:
                return [], False
            result = input_data.get("result", input_data)
            if isinstance(result, dict):
                balances = []
                for currency, amount in result.items():
                    try:
                        if float(amount) > 0:
                            balances.append(KrakenSpotWssBalanceData(
                                {currency: amount}, extra_data["asset_type"]
                            ))
                    except (ValueError, TypeError):
                        pass
                return balances, True
        return [], False

    def get_balance(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        return self.get_balance(extra_data=extra_data, **kwargs)

    def async_get_balance(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def async_get_account(self, symbol=None, extra_data=None, **kwargs):
        self.async_get_balance(extra_data=extra_data, **kwargs)

    # ==================== Trading Methods ====================

    def _make_order(self, symbol, vol, price=None, order_type="buy-limit",
                    client_order_id=None, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)

        parts = order_type.split("-")
        side = parts[0].lower() if len(parts) >= 1 else "buy"
        otype = parts[1].lower() if len(parts) >= 2 else "limit"

        body = {
            "pair": request_symbol,
            "type": side,
            "ordertype": otype,
            "volume": str(vol),
        }
        if otype == "limit" and price is not None:
            body["price"] = str(price)
        if client_order_id:
            body["userref"] = client_order_id

        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=KrakenRequestDataSpot._make_order_normalize_function,
        )
        return path, body, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        """Kraken order: {"error":[], "result":{"descr":{...},"txid":["..."]}}"""
        if input_data is None:
            return [], False
        if isinstance(input_data, dict):
            errors = input_data.get("error", [])
            if errors:
                return [input_data], False
            result = input_data.get("result", input_data)
            if isinstance(result, dict):
                txid_list = result.get("txid", [])
                if txid_list:
                    order_data = {
                        "txid": txid_list[0] if isinstance(txid_list, list) else txid_list,
                        "descr": result.get("descr", {}),
                        "status": "open",
                    }
                    return [KrakenRequestOrderData(
                        order_data,
                        extra_data["symbol_name"],
                        extra_data["asset_type"],
                        True,
                    )], True
                return [KrakenRequestOrderData(
                    result, extra_data["symbol_name"], extra_data["asset_type"], True
                )], True
        return [], False

    def make_order(self, symbol, vol, price=None, order_type="buy-limit",
                   client_order_id=None, extra_data=None, **kwargs):
        path, body, extra_data = self._make_order(
            symbol, vol, price, order_type, client_order_id, extra_data, **kwargs
        )
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def async_make_order(self, symbol, vol, price=None, order_type="buy-limit",
                         client_order_id=None, extra_data=None, **kwargs):
        path, body, extra_data = self._make_order(
            symbol, vol, price, order_type, client_order_id, extra_data, **kwargs
        )
        self.submit(self.async_request(path, params={}, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_order(self, order_id, extra_data=None, **kwargs):
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)
        body = {"txid": order_id}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            order_id=order_id,
            symbol_name=kwargs.get("symbol", ""),
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=KrakenRequestData._extract_data_normalize_function,
        )
        return path, body, extra_data

    def cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, body, extra_data = self._cancel_order(order_id, extra_data, symbol=symbol, **kwargs)
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def _query_order(self, order_id, extra_data=None, **kwargs):
        request_type = "query_order"
        path = self._params.get_rest_path(request_type)
        body = {"txid": order_id}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            order_id=order_id,
            symbol_name=kwargs.get("symbol", ""),
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=KrakenRequestDataSpot._query_order_normalize_function,
        )
        return path, body, extra_data

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        """Kraken query order: {"error":[], "result":{"TXID":{order_info}}}"""
        if input_data is None:
            return [], False
        if isinstance(input_data, dict):
            errors = input_data.get("error", [])
            if errors:
                return [], False
            result = input_data.get("result", input_data)
            if isinstance(result, dict):
                orders = []
                for txid, order_info in result.items():
                    if isinstance(order_info, dict):
                        order_info["txid"] = txid
                        orders.append(KrakenRequestOrderData(
                            order_info,
                            extra_data.get("symbol_name", ""),
                            extra_data["asset_type"],
                            True,
                        ))
                return orders, bool(orders)
        return [], False

    def query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        path, body, extra_data = self._query_order(order_id, extra_data, symbol=symbol, **kwargs)
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def _get_open_orders(self, extra_data=None, **kwargs):
        request_type = "get_open_orders"
        path = self._params.get_rest_path(request_type)
        body = {}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=kwargs.get("symbol", "ALL"),
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=KrakenRequestDataSpot._get_open_orders_normalize_function,
        )
        return path, body, extra_data

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        """Kraken open orders: {"error":[], "result":{"open":{"TXID":{...}}}}"""
        if input_data is None:
            return [], False
        if isinstance(input_data, dict):
            errors = input_data.get("error", [])
            if errors:
                return [], False
            result = input_data.get("result", input_data)
            open_orders = result.get("open", {}) if isinstance(result, dict) else {}
            orders = []
            for txid, order_info in open_orders.items():
                if isinstance(order_info, dict):
                    order_info["txid"] = txid
                    orders.append(KrakenRequestOrderData(
                        order_info,
                        order_info.get("descr", {}).get("pair", ""),
                        extra_data["asset_type"],
                        True,
                    ))
            return orders, True
        return [], False

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, body, extra_data = self._get_open_orders(extra_data, symbol=symbol, **kwargs)
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def _get_deals(self, symbol=None, limit=100, extra_data=None, **kwargs):
        request_type = "get_deals"
        path = self._params.get_rest_path(request_type)
        body = {}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol or "ALL",
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=KrakenRequestData._extract_data_normalize_function,
        )
        return path, body, extra_data

    def get_deals(self, symbol=None, limit=100, extra_data=None, **kwargs):
        path, body, extra_data = self._get_deals(symbol, limit, extra_data, **kwargs)
        return self.request(path, params={}, body=body, extra_data=extra_data)


# ==================== WebSocket Placeholder Classes ====================

class KrakenMarketWssDataSpot:
    """Placeholder for Kraken Spot Market WebSocket data handler."""
    pass


class KrakenAccountWssDataSpot:
    """Placeholder for Kraken Spot Account WebSocket data handler."""
    pass
