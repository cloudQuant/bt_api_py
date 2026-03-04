from bt_api_py.containers.exchanges.gemini_exchange_data import GeminiExchangeDataSpot
from bt_api_py.containers.orders.gemini_order import (
    GeminiRequestOrderData,
    GeminiSpotWssOrderData,
)
from bt_api_py.containers.trades.gemini_trade import GeminiSpotWssTradeData
from bt_api_py.containers.balances.gemini_balance import GeminiRequestBalanceData
from bt_api_py.containers.tickers.gemini_ticker import GeminiRequestTickerData
from bt_api_py.containers.orderbooks.gemini_orderbook import GeminiRequestOrderBookData
from bt_api_py.containers.bars.gemini_bar import GeminiRequestBarData
from bt_api_py.feeds.live_gemini.request_base import GeminiRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class GeminiRequestDataSpot(GeminiRequestData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "GEMINI___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "gemini_spot_feed.log")
        self._params = GeminiExchangeDataSpot()
        self.request_logger = get_logger("gemini_spot_feed")
        self.async_logger = get_logger("gemini_spot_feed")

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
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)
        side, order_type = order_type.split("-")

        # Map bt_api_py order types to Gemini order types
        gemini_order_type = "exchange limit"
        if order_type == "market":
            gemini_order_type = "exchange market"

        params = {
            "symbol": request_symbol,
            "side": side.upper(),
            "amount": str(vol),
            "price": str(price) if price else None,
            "type": gemini_order_type,
        }

        if client_order_id is not None:
            params["client_order_id"] = client_order_id

        # Add options if specified
        options = []
        if post_only:
            options.append("maker-or-cancel")

        if kwargs.get("time_in_force") == "IOC":
            options.append("immediate-or-cancel")
        elif kwargs.get("time_in_force") == "FOK":
            options.append("fill-or-kill")

        if options:
            params["options"] = options

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "post_only": post_only,
                "normalize_function": GeminiRequestDataSpot._make_order_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        status = input_data is not None
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]

        if isinstance(input_data, list):
            data = [GeminiRequestOrderData(i, symbol_name, asset_type, True) for i in input_data]
        elif isinstance(input_data, dict):
            data = [GeminiRequestOrderData(input_data, symbol_name, asset_type, True)]
        else:
            data = []

        return data, status

    def _get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get account balance. Returns (path, params, extra_data)."""
        request_type = "get_balance"
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": GeminiRequestDataSpot._get_balance_normalize_function,
            },
        )
        return path, {}, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        return [input_data], True

    def _get_account(self, extra_data=None, **kwargs):
        """Get account info. Returns (path, params, extra_data)."""
        request_type = "get_balance"
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_account",
                "symbol_name": "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": GeminiRequestDataSpot._get_account_normalize_function,
            },
        )
        return path, {}, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        return [input_data], True

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders. Returns (path, params, extra_data)."""
        request_type = "get_open_orders"
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbol:
            request_symbol = self._params.get_symbol(symbol)
            params["symbol"] = request_symbol
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
            },
        )
        return path, params, extra_data

    def _query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order status. Returns (path, params, extra_data)."""
        request_type = "query_order"
        path = self._params.get_rest_path(request_type)
        params = {"order_id": order_id}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "order_id": order_id,
            },
        )
        return path, params, extra_data

    def _get_order_history(self, symbol=None, limit_trades=50, extra_data=None, **kwargs):
        """Get order history. Returns (path, params, extra_data)."""
        request_type = "get_order_history"
        path = self._params.get_rest_path(request_type)
        params = {"limit_trades": limit_trades}
        if symbol:
            request_symbol = self._params.get_symbol(symbol)
            params["symbol"] = request_symbol
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
            },
        )
        return path, params, extra_data

    def _get_ticker(self, symbol, extra_data=None, **kwargs):
        """Build ticker request. Returns (path, params, extra_data)."""
        request_symbol = self._params.get_symbol(symbol) if symbol else None
        path = self._params.get_rest_path("get_ticker").format(symbol=request_symbol)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_tick",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": GeminiRequestDataSpot._get_tick_normalize_function,
            },
        )
        return path, {}, extra_data

    # Alias for standard interface
    _get_tick = _get_ticker

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        return [input_data], True

    def _get_depth(self, symbol, limit_bids=50, limit_asks=50, extra_data=None, **kwargs):
        """Build depth request. Returns (path, params, extra_data)."""
        request_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_depth").format(symbol=request_symbol)
        params = {"limit_bids": limit_bids, "limit_asks": limit_asks}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_depth",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": GeminiRequestDataSpot._get_depth_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        return [input_data], True

    def _get_trades(self, symbol, limit_trades=50, extra_data=None, **kwargs):
        """构建成交记录请求参数"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_trades"
        path = self._params.get_rest_path(request_type).format(symbol=request_symbol)
        params = {
            "limit_trades": limit_trades,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def _get_kline(self, symbol, time_frame, extra_data=None, **kwargs):
        """Build kline request. Returns (path, params, extra_data)."""
        request_symbol = self._params.get_symbol(symbol)
        gemini_time_frame = self._params.get_period(time_frame)
        path = self._params.get_rest_path("get_kline").format(
            symbol=request_symbol,
            time_frame=gemini_time_frame
        )
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_kline",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": GeminiRequestDataSpot._get_kline_normalize_function,
            },
        )
        return path, {}, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, list):
            return [input_data], True
        return [input_data], True

    def _get_symbols(self, extra_data=None, **kwargs):
        """构建交易对列表请求参数"""
        request_type = "get_symbols"
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

    def _get_symbol_details(self, symbol, extra_data=None, **kwargs):
        """构建交易对详情请求参数"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_symbol_details"
        path = self._params.get_rest_path(request_type).format(symbol=request_symbol)
        params = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    # ==================== Public methods ====================

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get account balance."""
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, method="POST", params=params, extra_data=extra_data)

    def get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account information."""
        path, params, extra_data = self._get_account(extra_data, **kwargs)
        return self.request(path, method="POST", params=params, extra_data=extra_data)

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders."""
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, method="POST", params=params, extra_data=extra_data)

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order status."""
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, method="POST", params=params, extra_data=extra_data)

    def get_order_history(self, symbol=None, limit_trades=50, extra_data=None, **kwargs):
        """Get order history."""
        path, params, extra_data = self._get_order_history(symbol, limit_trades, extra_data, **kwargs)
        return self.request(path, method="POST", params=params, extra_data=extra_data)

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker for a symbol."""
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        return self.request(path, method="GET", params=params, extra_data=extra_data)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker (standard interface)."""
        return self.get_ticker(symbol, extra_data, **kwargs)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Async get ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, method="GET", params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def get_depth(self, symbol, limit_bids=50, limit_asks=50, extra_data=None, **kwargs):
        """Get order book for a symbol."""
        path, params, extra_data = self._get_depth(symbol, limit_bids, limit_asks, extra_data, **kwargs)
        return self.request(path, method="GET", params=params, extra_data=extra_data)

    def async_get_depth(self, symbol, limit_bids=50, limit_asks=50, extra_data=None, **kwargs):
        """Async get depth."""
        path, params, extra_data = self._get_depth(symbol, limit_bids, limit_asks, extra_data, **kwargs)
        self.submit(
            self.async_request(path, method="GET", params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def get_trades(self, symbol, limit_trades=50, extra_data=None, **kwargs):
        """Get recent trades for a symbol."""
        path, params, extra_data = self._get_trades(symbol, limit_trades, extra_data, **kwargs)
        return self.request(path, method="GET", params=params, extra_data=extra_data)

    def get_kline(self, symbol, time_frame, extra_data=None, **kwargs):
        """Get kline data for a symbol."""
        path, params, extra_data = self._get_kline(symbol, time_frame, extra_data, **kwargs)
        return self.request(path, method="GET", params=params, extra_data=extra_data)

    def async_get_kline(self, symbol, time_frame, extra_data=None, **kwargs):
        """Async get kline."""
        path, params, extra_data = self._get_kline(symbol, time_frame, extra_data, **kwargs)
        self.submit(
            self.async_request(path, method="GET", params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def get_symbols(self, extra_data=None, **kwargs):
        """Get all available symbols."""
        path, params, extra_data = self._get_symbols(extra_data, **kwargs)
        return self.request(path, method="GET", params=params, extra_data=extra_data)

    def get_symbol_details(self, symbol, extra_data=None, **kwargs):
        """Get details for a symbol."""
        path, params, extra_data = self._get_symbol_details(symbol, extra_data, **kwargs)
        return self.request(path, method="GET", params=params, extra_data=extra_data)

    def make_order(self, symbol, vol, price=None, order_type="buy-limit",
                   offset="open", post_only=False, client_order_id=None,
                   extra_data=None, **kwargs):
        """Place a new order."""
        path, params, extra_data = self._make_order(symbol, vol, price, order_type, offset,
                                                     post_only, client_order_id, extra_data, **kwargs)
        return self.request(path, method="POST", params=params, extra_data=extra_data)

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel an order."""
        path = self._params.get_rest_path("cancel_order")
        params = {"order_id": order_id}
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": "cancel_order",
            "order_id": order_id,
        })
        return self.request(path, method="POST", params=params, extra_data=extra_data)

    def cancel_all_orders(self, symbol=None, extra_data=None, **kwargs):
        """Cancel all orders."""
        path = self._params.get_rest_path("cancel_orders")
        params = {}
        if symbol:
            params["symbol"] = self._params.get_symbol(symbol)
        return self.request(path, method="POST", params=params, extra_data=extra_data)
