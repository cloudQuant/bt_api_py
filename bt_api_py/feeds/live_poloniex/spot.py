"""
Poloniex Spot Trading Feed
Implements spot trading functionality for Poloniex exchange.
"""

from bt_api_py.containers.exchanges.poloniex_exchange_data import PoloniexExchangeDataSpot
from bt_api_py.feeds.live_poloniex.request_base import PoloniexRequestData
from bt_api_py.logging_factory import get_logger


class PoloniexRequestDataSpot(PoloniexRequestData):
    """Poloniex Spot Trading REST API Client."""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "poloniex_spot_feed.log")
        self._params = PoloniexExchangeDataSpot()
        self.exchange_name = self._params.exchange_name
        self.request_logger = get_logger("poloniex_spot_feed")
        self.async_logger = get_logger("poloniex_spot_feed")

    # ── internal _get_xxx methods ───────────────────────────────

    def _get_server_time(self, extra_data=None, **kwargs):
        request_type = "get_server_time"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": request_type,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": self._get_server_time_normalize_function,
            }
        )
        return path, params, extra_data

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if (
            isinstance(input_data, dict)
            and "code" in input_data
            and input_data["code"] not in (200, 0, None)
        ):
            return [], False
        return [input_data], True

    def _get_exchange_info(self, extra_data=None, **kwargs):
        request_type = "get_exchange_info"
        path = self._params.get_rest_path("get_markets")
        params = {}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": request_type,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": self._get_exchange_info_normalize_function,
            }
        )
        return path, params, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if (
            isinstance(input_data, dict)
            and "code" in input_data
            and input_data["code"] not in (200, 0, None)
        ):
            return [], False
        if isinstance(input_data, list):
            return [{"symbols": input_data}], True
        return [input_data], True

    def _get_ticker(self, symbol, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_ticker"
        path = self._params.get_rest_path(request_type).replace("{symbol}", request_symbol)
        params = {}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_ticker_normalize_function,
            }
        )
        return path, params, extra_data

    # Standard alias
    _get_tick = _get_ticker

    @staticmethod
    def _get_ticker_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, dict):
            if "code" in input_data and input_data["code"] not in (200, 0, None):
                return [], False
            if "data" in input_data:
                return input_data["data"], True
            return [input_data], True
        if isinstance(input_data, list):
            return input_data, True
        return [], False

    _get_tick_normalize_function = _get_ticker_normalize_function

    def _get_depth(self, symbol, limit=10, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_orderbook"
        path = self._params.get_rest_path(request_type).replace("{symbol}", request_symbol)
        params = {"limit": min(limit, 150)}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_depth_normalize_function,
            }
        )
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, dict):
            if "code" in input_data and input_data["code"] not in (200, 0, None):
                return [], False
            result = {
                "bids": input_data.get("bids", []),
                "asks": input_data.get("asks", []),
            }
            return [result], True
        if isinstance(input_data, list):
            return input_data, True
        return [], False

    def _get_kline(self, symbol, period="1m", limit=100, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type).replace("{symbol}", request_symbol)
        period_map = self._params.get_period(period)
        params = {"interval": period_map, "limit": min(limit, 500)}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_kline_normalize_function,
            }
        )
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, list):
            return input_data, bool(input_data)
        if isinstance(input_data, dict):
            if "code" in input_data and input_data["code"] not in (200, 0, None):
                return [], False
            if "data" in input_data:
                return input_data["data"], True
            return [input_data], True
        return [], False

    def _get_trades(self, symbol, limit=500, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_trades"
        path = self._params.get_rest_path(request_type).replace("{symbol}", request_symbol)
        params = {"limit": min(limit, 1000)}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_trades_normalize_function,
            }
        )
        return path, params, extra_data

    # Standard alias
    _get_trade_history = _get_trades

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, list):
            return input_data, bool(input_data)
        if isinstance(input_data, dict):
            if "code" in input_data and input_data["code"] not in (200, 0, None):
                return [], False
            if "data" in input_data:
                return input_data["data"], True
            return [input_data], True
        return [], False

    _get_trade_history_normalize_function = _get_trades_normalize_function

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

        # Parse order type
        if "-" in order_type:
            side, ot = order_type.split("-", 1)
        else:
            side = "BUY" if "buy" in order_type.lower() else "SELL"
            ot = order_type.upper()

        params = {"symbol": request_symbol, "side": side.upper()}
        ot_upper = ot.upper()
        if "MARKET" in ot_upper:
            params["type"] = "MARKET"
            if side.upper() == "BUY" and kwargs.get("amount"):
                params["amount"] = str(kwargs["amount"])
            else:
                params["quantity"] = str(vol)
        elif "LIMIT" in ot_upper:
            params["type"] = "LIMIT"
            params["quantity"] = str(vol)
            if price is None:
                raise ValueError("Price required for limit orders")
            params["price"] = str(price)
            params["timeInForce"] = kwargs.get("time_in_force", "GTC")
            if post_only:
                params["timeInForce"] = "GTX"
        else:
            params["type"] = ot_upper
            params["quantity"] = str(vol)

        if client_order_id is not None:
            params["clientOrderId"] = client_order_id

        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._make_order_normalize_function,
            }
        )
        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, dict):
            if "code" in input_data and input_data["code"] not in (200, 0, None):
                return [], False
            return [input_data], True
        if isinstance(input_data, list):
            return input_data, True
        return [], False

    def _cancel_order(
        self, symbol=None, order_id=None, client_order_id=None, extra_data=None, **kwargs
    ):
        request_type = "cancel_order"
        if order_id:
            path = f"DELETE /orders/{order_id}"
            params = {}
        else:
            path = self._params.get_rest_path("cancel_orders")
            params = {}
            if symbol:
                params["symbol"] = self._params.get_symbol(symbol)
            if client_order_id:
                params["clientOrderId"] = client_order_id

        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": request_type,
                "symbol_name": symbol or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._cancel_order_normalize_function,
            }
        )
        return path, params, extra_data

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, dict):
            if "code" in input_data and input_data["code"] not in (200, 0, None):
                return [], False
            return [input_data], True
        return [], False

    def _query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        request_type = "query_order"
        path = f"GET /orders/{order_id}"
        params = {}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": request_type,
                "symbol_name": symbol or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._query_order_normalize_function,
            }
        )
        return path, params, extra_data

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, dict):
            if "code" in input_data and input_data["code"] not in (200, 0, None):
                return [], False
            return [input_data], True
        return [], False

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        request_type = "get_open_orders"
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbol:
            params["symbol"] = self._params.get_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": request_type,
                "symbol_name": symbol or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_open_orders_normalize_function,
            }
        )
        return path, params, extra_data

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        if isinstance(input_data, dict):
            if "code" in input_data and input_data["code"] not in (200, 0, None):
                return [], False
            return [input_data], True
        return [], False

    def _get_balance(self, currency=None, extra_data=None, **kwargs):
        request_type = "get_balance"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": request_type,
                "symbol_name": currency or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_balance_normalize_function,
            }
        )
        return path, params, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, dict):
            if "code" in input_data and input_data["code"] not in (200, 0, None):
                return [], False
            if "balances" in input_data:
                return input_data["balances"], True
            return [input_data], True
        if isinstance(input_data, list):
            return input_data, True
        return [], False

    def _get_account(self, extra_data=None, **kwargs):
        request_type = "get_account"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": request_type,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": self._get_account_normalize_function,
            }
        )
        return path, params, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, dict):
            if "code" in input_data and input_data["code"] not in (200, 0, None):
                return [], False
            if "balances" in input_data:
                return input_data["balances"], True
            return [input_data], True
        if isinstance(input_data, list):
            return input_data, True
        return [], False

    def _get_deals(self, symbol, limit=100, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_deals"
        path = self._params.get_rest_path(request_type)
        params = {"symbol": request_symbol, "limit": min(limit, 1000)}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_deals_normalize_function,
            }
        )
        return path, params, extra_data

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        if input_data is None:
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        if isinstance(input_data, dict):
            if "code" in input_data and input_data["code"] not in (200, 0, None):
                return [], False
            return [input_data], True
        return [], False

    # ── public sync wrappers ────────────────────────────────────

    def get_server_time(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_server_time(extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_exchange_info(extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_tick(symbol, extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    get_ticker = get_tick

    def get_depth(self, symbol, count=10, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(
            symbol, limit=count, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    def get_kline(self, symbol, period="1m", count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(
            symbol, period=period, limit=count, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    def get_trade_history(self, symbol, count=500, extra_data=None, **kwargs):
        path, params, extra_data = self._get_trades(
            symbol, limit=count, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    get_trades = get_trade_history

    def make_order(self, symbol, vol, price=None, order_type="buy-limit", **kwargs):
        path, body, extra_data = self._make_order(
            symbol, vol, price=price, order_type=order_type, **kwargs
        )
        return self.request(path, body=body, extra_data=extra_data)

    def cancel_order(self, symbol=None, order_id=None, **kwargs):
        path, params, extra_data = self._cancel_order(symbol=symbol, order_id=order_id, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def query_order(self, symbol=None, order_id=None, **kwargs):
        path, params, extra_data = self._query_order(symbol=symbol, order_id=order_id, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    def get_balance(self, currency=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(
            currency=currency, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    def get_account(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_account(extra_data=extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_deals(self, symbol, count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_deals(
            symbol, limit=count, extra_data=extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    # ── async wrappers ──────────────────────────────────────────

    def async_get_server_time(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_server_time(extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_get_exchange_info(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_exchange_info(extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_tick(symbol, extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    async_get_ticker = async_get_tick

    def async_get_depth(self, symbol, count=10, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(
            symbol, limit=count, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_get_kline(self, symbol, period="1m", count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(
            symbol, period=period, limit=count, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_get_trade_history(self, symbol, count=500, extra_data=None, **kwargs):
        path, params, extra_data = self._get_trades(
            symbol, limit=count, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    async_get_trades = async_get_trade_history

    def async_make_order(self, symbol, vol, price=None, order_type="buy-limit", **kwargs):
        path, body, extra_data = self._make_order(
            symbol, vol, price=price, order_type=order_type, **kwargs
        )
        self.submit(
            self.async_request(path, body=body, extra_data=extra_data), callback=self.async_callback
        )

    def async_cancel_order(self, symbol=None, order_id=None, **kwargs):
        path, params, extra_data = self._cancel_order(symbol=symbol, order_id=order_id, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_query_order(self, symbol=None, order_id=None, **kwargs):
        path, params, extra_data = self._query_order(symbol=symbol, order_id=order_id, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_get_balance(self, currency=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(
            currency=currency, extra_data=extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_get_account(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_account(extra_data=extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )
