"""
Foxbit Spot Feed implementation.
"""

import json

from bt_api_py.containers.tickers.foxbit_ticker import FoxbitRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_foxbit.request_base import FoxbitRequestData


class FoxbitRequestDataSpot(FoxbitRequestData):
    """Foxbit Spot Feed for market data."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "FOXBIT___SPOT")

    def _format_market(self, symbol):
        """Convert symbol to Foxbit market format (lowercase, no separator)."""
        return symbol.replace("/", "").replace("-", "").lower()

    # ==================== Market Data ====================

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data. Returns (path, params, extra_data)."""
        market = self._format_market(symbol)
        path = f"GET /rest/v3/markets/{market}/ticker/24hr"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_tick",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_tick_normalize_function,
            }
        )
        return path, None, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        ticker = input_data.get("data", input_data)
        if isinstance(ticker, list) and len(ticker) > 0:
            ticker = ticker[0]
        if isinstance(ticker, dict):
            symbol_name = extra_data.get("symbol_name", "") if extra_data else ""
            asset_type = extra_data.get("asset_type", "SPOT") if extra_data else "SPOT"
            ticker_json = json.dumps({"data": ticker})
            ticker_container = FoxbitRequestTickerData(ticker_json, symbol_name, asset_type, True)
            return [ticker_container], True
        return [], False

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Async get ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth. Returns (path, params, extra_data)."""
        market = self._format_market(symbol)
        path = f"GET /rest/v3/markets/{market}/orderbook"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_depth",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_depth_normalize_function,
            }
        )
        return path, None, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        depth = input_data.get("data", input_data)
        if isinstance(depth, dict):
            return [depth], True
        return [], False

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Async get depth."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data. Returns (path, params, extra_data)."""
        market = self._format_market(symbol)
        path = f"GET /rest/v3/markets/{market}/candlesticks"
        period_str = self._params.kline_periods.get(period, period)
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_kline",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_kline_normalize_function,
            }
        )
        return path, {"interval": period_str, "limit": count}, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        data = input_data.get("data", input_data)
        if isinstance(data, list):
            return [data], True
        return [], False

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Async get kline."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange info. Returns (path, params, extra_data)."""
        path = "GET /rest/v3/markets"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_exchange_info",
                "symbol_name": "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_exchange_info_normalize_function,
            }
        )
        return path, None, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        return [input_data], True

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information."""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_all_tickers(self, extra_data=None, **kwargs):
        """Get all tickers. Returns (path, params, extra_data)."""
        path = "GET /rest/v3/markets/ticker/24hr"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_all_tickers",
                "symbol_name": "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_all_tickers_normalize_function,
            }
        )
        return path, None, extra_data

    @staticmethod
    def _get_all_tickers_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        tickers = input_data.get("data", input_data)
        if isinstance(tickers, list):
            return [tickers], True
        return [], False

    def get_all_tickers(self, extra_data=None, **kwargs):
        """Get all symbol tickers."""
        path, params, extra_data = self._get_all_tickers(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_trades(self, symbol, count=20, extra_data=None, **kwargs):
        """Get recent trades. Returns (path, params, extra_data)."""
        market = self._format_market(symbol)
        path = f"GET /rest/v3/markets/{market}/trades/history"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_trades",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_trades_normalize_function,
            }
        )
        return path, {"limit": count}, extra_data

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        data = input_data.get("data", input_data)
        if isinstance(data, list):
            return [data], True
        return [], False

    def get_trades(self, symbol, count=20, extra_data=None, **kwargs):
        """Get recent trades."""
        path, params, extra_data = self._get_trades(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Account Interfaces ====================

    def _get_account(self, extra_data=None, **kwargs):
        """Get account information. Returns (path, params, extra_data)."""
        path = "GET /rest/v3/me"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_account",
                "symbol_name": "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_account_normalize_function,
            }
        )
        return path, None, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        return [input_data], True

    def get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account information."""
        path, params, extra_data = self._get_account(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get account balance. Returns (path, params, extra_data)."""
        path = "GET /rest/v3/accounts"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_balance",
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_balance_normalize_function,
            }
        )
        return path, None, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        return [input_data], True

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get account balance."""
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Trading Interfaces ====================

    def _make_order(
        self,
        symbol,
        volume,
        price,
        order_type,
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """Prepare order. Returns (path, params, extra_data)."""
        path = "POST /rest/v3/orders"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "make_order",
            }
        )
        side = "BUY" if offset.upper() in ("BUY", "OPEN") else "SELL"
        market = self._format_market(symbol)
        params = {
            "market_symbol": market,
            "side": side,
            "type": order_type.upper(),
            "quantity": str(volume),
            "price": str(price),
        }
        if client_order_id:
            params["client_order_id"] = client_order_id
        return path, params, extra_data

    def make_order(
        self,
        symbol,
        volume,
        price,
        order_type,
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """Place an order."""
        path, params, extra_data = self._make_order(
            symbol,
            volume,
            price,
            order_type,
            offset,
            post_only,
            client_order_id,
            extra_data,
            **kwargs,
        )
        return self.request(path, body=params, extra_data=extra_data)

    def _cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order. Returns (path, params, extra_data)."""
        path = f"DELETE /rest/v3/orders/{order_id}"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "cancel_order",
                "order_id": order_id,
            }
        )
        return path, {}, extra_data

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order."""
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, extra_data=extra_data)

    def _query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order. Returns (path, params, extra_data)."""
        path = f"GET /rest/v3/orders/{order_id}"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "query_order",
                "order_id": order_id,
            }
        )
        return path, {}, extra_data

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order status."""
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders. Returns (path, params, extra_data)."""
        path = "GET /rest/v3/orders"
        params = {}
        if symbol:
            params["market_symbol"] = self._format_market(symbol)
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_open_orders",
            }
        )
        return path, params, extra_data

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders."""
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
