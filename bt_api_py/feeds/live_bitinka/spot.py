"""
Bitinka Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.bitinka_exchange_data import BitinkaExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitinka.request_base import BitinkaRequestData


class BitinkaRequestDataSpot(BitinkaRequestData):
    """Bitinka Spot Feed for market data."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "BITINKA___SPOT")

    def _convert_symbol(self, symbol):
        """Convert symbol to Bitinka format.

        Bitinka uses various fiat currency pairs.
        Common format: BTC/USD, ETH/EUR, etc.
        """
        if "-" in symbol:
            return symbol.replace("-", "/")
        elif "_" in symbol:
            return symbol.replace("_", "/")
        return symbol

    # ==================== Market Data ====================

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data. Returns (path, params, extra_data)."""
        request_type = "get_tick"
        path = "GET /ticker"
        bitinka_symbol = self._convert_symbol(symbol)
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bitinka_symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_tick_normalize_function,
        })
        params = {"market": bitinka_symbol}
        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data from Bitinka response."""
        if not input_data:
            return [], False
        ticker = input_data.get("data", input_data)
        return [ticker], ticker is not None

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
        request_type = "get_depth"
        path = "GET /orderbook"
        bitinka_symbol = self._convert_symbol(symbol)
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bitinka_symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_depth_normalize_function,
        })
        params = {"market": bitinka_symbol}
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth data from Bitinka response."""
        if not input_data:
            return [], False
        depth = input_data.get("data", input_data)
        return [depth], depth is not None

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

    def _get_trades(self, symbol, count=50, extra_data=None, **kwargs):
        """Get recent trades. Returns (path, params, extra_data)."""
        request_type = "get_trades"
        path = "GET /trades"
        bitinka_symbol = self._convert_symbol(symbol)
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bitinka_symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_trades_normalize_function,
        })
        params = {"market": bitinka_symbol}
        return path, params, extra_data

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize trades data from Bitinka response."""
        if not input_data:
            return [], False
        trades = input_data.get("data", input_data)
        return [trades], trades is not None

    def get_trades(self, symbol, count=50, extra_data=None, **kwargs):
        """Get recent trades."""
        path, params, extra_data = self._get_trades(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange configuration. Returns (path, params, extra_data)."""
        request_type = "get_exchange_info"
        path = "GET /markets"
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": "",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_exchange_info_normalize_function,
        })
        return path, {}, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info from Bitinka response."""
        if not input_data:
            return [], False
        markets = input_data.get("data", input_data)
        return [markets], markets is not None

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange trading pairs configuration."""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Account Interfaces ====================

    def _get_account(self, extra_data=None, **kwargs):
        """Get account information. Returns (path, params, extra_data)."""
        request_type = "get_account"
        path = "GET /account"
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": "",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_account_normalize_function,
        })
        return path, {}, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        """Normalize account data from Bitinka response."""
        if not input_data:
            return [], False
        account = input_data.get("data", input_data)
        return [account], account is not None

    def get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account information."""
        path, params, extra_data = self._get_account(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account information asynchronously."""
        path, params, extra_data = self._get_account(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get account balance. Returns (path, params, extra_data)."""
        request_type = "get_balance"
        path = "GET /balance"
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol or "",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_balance_normalize_function,
        })
        params = {}
        if symbol:
            params["currency"] = symbol
        return path, params, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        """Normalize balance data from Bitinka response."""
        if not input_data:
            return [], False
        balance = input_data.get("data", input_data)
        return [balance], balance is not None

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get account balance."""
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Trading Interfaces ====================

    def _make_order(self, symbol, volume, price, order_type, offset="open",
                    post_only=False, client_order_id=None, extra_data=None, **kwargs):
        """Prepare order. Returns (path, params, extra_data)."""
        bitinka_symbol = self._convert_symbol(symbol)
        path = "POST /order"
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": bitinka_symbol,
            "asset_type": self.asset_type,
            "request_type": "make_order",
        })
        params = {
            "market": bitinka_symbol,
            "side": offset.upper() if offset in ("BUY", "SELL", "buy", "sell") else "BUY",
            "type": order_type.upper(),
            "amount": str(volume),
            "price": str(price),
        }
        return path, params, extra_data

    def make_order(self, symbol, volume, price, order_type, offset="open",
                   post_only=False, client_order_id=None, extra_data=None, **kwargs):
        """Place an order."""
        path, params, extra_data = self._make_order(
            symbol, volume, price, order_type, offset, post_only,
            client_order_id, extra_data, **kwargs
        )
        return self.request(path, body=params, extra_data=extra_data)

    def _cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order. Returns (path, params, extra_data)."""
        bitinka_symbol = self._convert_symbol(symbol)
        path = "POST /cancelOrder"
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": bitinka_symbol,
            "asset_type": self.asset_type,
            "request_type": "cancel_order",
            "order_id": order_id,
        })
        params = {"market": bitinka_symbol, "orderId": order_id}
        return path, params, extra_data

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order."""
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, body=params, extra_data=extra_data)

    def _query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order. Returns (path, params, extra_data)."""
        bitinka_symbol = self._convert_symbol(symbol)
        path = "GET /orderStatus"
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": bitinka_symbol,
            "asset_type": self.asset_type,
            "request_type": "query_order",
            "order_id": order_id,
        })
        params = {"market": bitinka_symbol, "orderId": order_id}
        return path, params, extra_data

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order status."""
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders. Returns (path, params, extra_data)."""
        path = "GET /openOrders"
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol or "",
            "asset_type": self.asset_type,
            "request_type": "get_open_orders",
        })
        params = {}
        if symbol:
            bitinka_symbol = self._convert_symbol(symbol)
            params["market"] = bitinka_symbol
        return path, params, extra_data

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders."""
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Deals ====================

    def _get_deals(self, symbol, count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        """Get trade history/deals. Returns (path, params, extra_data)."""
        request_type = "get_deals"
        path = "GET /trades"
        bitinka_symbol = self._convert_symbol(symbol)
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bitinka_symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_deals_normalize_function,
        })
        params = {"market": bitinka_symbol, "limit": count}
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        return path, params, extra_data

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        """Normalize deals data from Bitinka response."""
        if not input_data:
            return [], False
        deals = input_data.get("data", input_data)
        return [deals], deals is not None

    def get_deals(self, symbol="BTC/USD", count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        """Get trade history."""
        path, params, extra_data = self._get_deals(symbol, count, start_time, end_time, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_deals(self, symbol, count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        """Get trade history asynchronously."""
        path, params, extra_data = self._get_deals(symbol, count, start_time, end_time, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )
