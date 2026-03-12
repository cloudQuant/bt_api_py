"""
Bitbank Spot Feed implementation.
"""

from datetime import datetime
from typing import Any

from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitbank.request_base import BitbankRequestData


class BitbankRequestDataSpot(BitbankRequestData):
    """Bitbank Spot Feed for market data."""

    @classmethod
    def _capabilities(cls) -> set[Capability]:
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

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "BITBANK___SPOT")

    def _normalize_pair(self, symbol) -> Any:
        """Normalize symbol to bitbank format (e.g., btc_jpy)."""
        symbol = symbol.upper().replace("/", "_").replace("-", "_")
        return symbol.lower()

    # ==================== Ticker ====================

    def _get_tick(self, symbol, extra_data=None, **kwargs) -> Any:
        """Prepare ticker request. Returns (path, params, extra_data)."""
        pair = self._normalize_pair(symbol)
        path = f"GET /{pair}/ticker"
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
        """Normalize ticker data."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("success") == 1:
            return [data], True
        return [], False

    def get_tick(self, symbol, extra_data=None, **kwargs) -> Any:
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

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs) -> Any:
        """Prepare depth request. Returns (path, params, extra_data)."""
        pair = self._normalize_pair(symbol)
        path = f"GET /{pair}/depth"
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
        """Normalize depth data."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("success") == 1:
            return [data], True
        return [], False

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs) -> Any:
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

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs) -> Any:
        """Prepare kline request. Returns (path, params, extra_data)."""
        pair = self._normalize_pair(symbol)
        kline_periods = getattr(self._params, "kline_periods", {})
        candle_type = kline_periods.get(period, period)

        long_periods = ["4hour", "8hour", "12hour", "1day", "1week", "1month"]
        if candle_type in long_periods:
            date_str = datetime.now().strftime("%Y")
        else:
            date_str = datetime.now().strftime("%Y%m%d")

        path = f"GET /{pair}/candlestick/{candle_type}/{date_str}"
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
        return path, None, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline data."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("success") == 1:
            candlestick = data.get("candlestick", [])
            if candlestick and len(candlestick) > 0:
                ohlcv = candlestick[0].get("ohlcv", [])
                if ohlcv:
                    return [{"ohlcv": ohlcv}], True
        return [], False

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs) -> Any:
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

    def _get_trades(self, symbol, limit=60, extra_data=None, **kwargs) -> Any:
        """Prepare trades request. Returns (path, params, extra_data)."""
        pair = self._normalize_pair(symbol)
        path = f"GET /{pair}/transactions"
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
        return path, None, extra_data

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize trades data."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("success") == 1:
            transactions = data.get("transactions", [])
            return [transactions], True
        return [], False

    def get_trades(self, symbol, limit=60, extra_data=None, **kwargs) -> Any:
        """Get recent trades."""
        path, params, extra_data = self._get_trades(symbol, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Exchange Info ====================

    def _get_exchange_info(self, extra_data=None, **kwargs) -> Any:
        """Prepare exchange info request. Returns (path, params, extra_data)."""
        path = "GET /spot/pairs"
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
        if data and input_data.get("success") == 1:
            pairs = data.get("pairs", data) if isinstance(data, dict) else data
            return pairs if isinstance(pairs, list) else [pairs], True
        return [], False

    def get_exchange_info(self, extra_data=None, **kwargs) -> Any:
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
        path = "POST /v1/user/spot/order"
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
            "pair": self._normalize_pair(symbol),
            "amount": str(volume),
            "price": str(price),
            "side": offset if offset in ("buy", "sell") else "buy",
            "type": order_type,
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

    def _cancel_order(self, symbol, order_id, extra_data=None, **kwargs) -> Any:
        """Cancel order. Returns (path, params, extra_data)."""
        path = "POST /v1/user/spot/cancel_order"
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
        params = {"pair": self._normalize_pair(symbol), "order_id": order_id}
        return path, params, extra_data

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order."""
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _query_order(self, symbol, order_id, extra_data=None, **kwargs) -> Any:
        """Query order. Returns (path, params, extra_data)."""
        path = "GET /v1/user/spot/order"
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
        params = {"pair": self._normalize_pair(symbol), "order_id": order_id}
        return path, params, extra_data

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order status."""
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs) -> Any:
        """Get open orders. Returns (path, params, extra_data)."""
        path = "GET /v1/user/spot/active_orders"
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
        params = {"pair": self._normalize_pair(symbol)} if symbol else {}
        return path, params, extra_data

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs) -> Any:
        """Get open orders."""
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Account Interfaces ====================

    def _get_account(self, symbol=None, extra_data=None, **kwargs) -> Any:
        """Get account info. Returns (path, params, extra_data)."""
        path = "GET /v1/user/assets"
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

    def get_account(self, symbol=None, extra_data=None, **kwargs) -> Any:
        """Get account info."""
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_balance(self, symbol=None, extra_data=None, **kwargs) -> Any:
        """Get balance. Returns (path, params, extra_data)."""
        path = "GET /v1/user/assets"
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

    def get_balance(self, symbol=None, extra_data=None, **kwargs) -> Any:
        """Get token balance."""
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
