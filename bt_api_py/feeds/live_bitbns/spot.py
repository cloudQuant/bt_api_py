"""
Bitbns Spot Feed implementation.
"""

from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitbns.request_base import BitbnsRequestData


class BitbnsRequestDataSpot(BitbnsRequestData):
    """Bitbns Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITBNS___SPOT")

    def _normalize_symbol(self, symbol):
        """Normalize symbol to bitbns format (uppercase, base asset only)."""
        symbol = symbol.upper()
        if "_" in symbol:
            return symbol
        if "/" in symbol:
            return symbol.replace("/", "_")
        if "-" in symbol:
            return symbol.replace("-", "_")
        return symbol

    def _parse_symbol(self, symbol):
        """Parse symbol into base and market."""
        sym = self._normalize_symbol(symbol)
        market = "USDT" if "_USDT" in sym.upper() else "INR"
        base = sym.replace("_USDT", "").replace("_INR", "")
        return base, market

    # ==================== Ticker ====================

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Prepare ticker request. Returns (path, params, extra_data)."""
        base, market = self._parse_symbol(symbol)
        path = "GET /ticker"
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
        params = {"symbol": base, "market": market}
        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("status") == 1:
            return [data], True
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

    # ==================== Depth ====================

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Prepare depth request. Returns (path, params, extra_data)."""
        base, market = self._parse_symbol(symbol)
        path = "GET /orderBook"
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
        params = {"symbol": base, "market": market}
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth data."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("status") == 1:
            return [data], True
        return [], False

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Async get orderbook."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Kline ====================

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Prepare kline request. Returns (path, params, extra_data)."""
        base, market = self._parse_symbol(symbol)
        path = "GET /kline"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "request_type": "get_kline",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "period": period,
                "normalize_function": self._get_kline_normalize_function,
            }
        )
        params = {"symbol": base, "market": market, "period": period, "page": 1}
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline data."""
        if not input_data:
            return [], False
        data = input_data.get("data", [])
        if data and input_data.get("status") == 1:
            return [data], True
        return [], False

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_kline(self, symbol, period="1m", count=20, extra_data=None, **kwargs):
        """Async get kline."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Trades ====================

    def _get_trades(self, symbol, limit=100, extra_data=None, **kwargs):
        """Prepare trades request. Returns (path, params, extra_data)."""
        base, market = self._parse_symbol(symbol)
        path = "GET /recentTrades"
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
        params = {"symbol": base, "market": market, "limit": limit}
        return path, params, extra_data

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize trades data."""
        if not input_data:
            return [], False
        data = input_data.get("data", [])
        if data and input_data.get("status") == 1:
            return [data], True
        return [], False

    def get_trades(self, symbol, limit=100, extra_data=None, **kwargs):
        """Get recent trades."""
        path, params, extra_data = self._get_trades(symbol, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Exchange Info ====================

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Prepare exchange info request. Returns (path, params, extra_data)."""
        path = "GET /coins"
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
        data = input_data.get("data", {})
        if data and input_data.get("status") == 1:
            return [data] if not isinstance(data, list) else data, True
        return [], False

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange info."""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
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
        base, market = self._parse_symbol(symbol)
        path = "POST /api/v2/orders"
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
        params = {
            "symbol": base,
            "market": market,
            "side": offset if offset in ("buy", "sell") else "buy",
            "type": order_type,
            "quantity": str(volume),
            "rate": str(price),
        }
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
        return self.request(path, params=params, extra_data=extra_data)

    def _cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order. Returns (path, params, extra_data)."""
        path = "POST /api/v2/cancel"
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
        base, market = self._parse_symbol(symbol)
        params = {"symbol": base, "market": market, "entry_id": order_id}
        return path, params, extra_data

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order."""
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order. Returns (path, params, extra_data)."""
        path = "GET /api/v2/orderStatus"
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
        base, market = self._parse_symbol(symbol)
        params = {"symbol": base, "market": market, "entry_id": order_id}
        return path, params, extra_data

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order status."""
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders. Returns (path, params, extra_data)."""
        path = "GET /api/v2/orders"
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
        params = {}
        if symbol:
            base, market = self._parse_symbol(symbol)
            params = {"symbol": base, "market": market}
        return path, params, extra_data

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders."""
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Account Interfaces ====================

    def _get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info. Returns (path, params, extra_data)."""
        path = "GET /api/v1/currentCoinBalance/EVERYTHING"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_account",
            }
        )
        return path, None, extra_data

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info."""
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get balance. Returns (path, params, extra_data)."""
        path = "GET /api/v1/currentCoinBalance/EVERYTHING"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_balance",
            }
        )
        return path, None, extra_data

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get token balance."""
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
