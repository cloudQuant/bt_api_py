"""
Kraken Futures Trading Feed Implementation
"""

from bt_api_py.containers.exchanges.kraken_exchange_data import KrakenExchangeDataFutures
from bt_api_py.feeds.live_kraken.request_base import KrakenRequestData
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class KrakenRequestDataFutures(KrakenRequestData):
    """Kraken Futures Trading Feed"""

    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault("asset_type", "FUTURES")
        kwargs.setdefault("logger_name", "kraken_futures_feed.log")
        super().__init__(data_queue, **kwargs)
        self._params = KrakenExchangeDataFutures()
        self.asset_type = "FUTURES"
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
            normalize_function=KrakenRequestData._extract_data_normalize_function,
        )
        return path, params, extra_data

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

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=KrakenRequestData._extract_data_normalize_function,
        )
        return path, params, extra_data

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
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
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol or "ALL",
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=KrakenRequestData._extract_data_normalize_function,
        )
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Account Methods ====================

    def _get_balance(self, extra_data=None, **kwargs):
        request_type = "get_balance"
        path = self._params.get_rest_path(request_type)
        body = {}
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name="ALL",
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=KrakenRequestData._extract_data_normalize_function,
        )
        return path, body, extra_data

    def get_balance(self, extra_data=None, **kwargs):
        path, body, extra_data = self._get_balance(extra_data, **kwargs)
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        return self.get_balance(extra_data=extra_data, **kwargs)

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
            "symbol": request_symbol,
            "side": side,
            "orderType": otype,
            "size": str(vol),
        }
        if otype == "lmt" and price is not None:
            body["limitPrice"] = str(price)
        if client_order_id:
            body["cliOrdId"] = client_order_id

        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=KrakenRequestData._extract_data_normalize_function,
        )
        return path, body, extra_data

    def make_order(self, symbol, vol, price=None, order_type="buy-limit",
                   client_order_id=None, extra_data=None, **kwargs):
        path, body, extra_data = self._make_order(
            symbol, vol, price, order_type, client_order_id, extra_data, **kwargs
        )
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def _cancel_order(self, order_id, extra_data=None, **kwargs):
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)
        body = {"order_id": order_id}
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


# ==================== WebSocket Placeholder Classes ====================

class KrakenMarketWssDataFutures:
    """Placeholder for Kraken Futures Market WebSocket data handler."""
    pass


class KrakenAccountWssDataFutures:
    """Placeholder for Kraken Futures Account WebSocket data handler."""
    pass
