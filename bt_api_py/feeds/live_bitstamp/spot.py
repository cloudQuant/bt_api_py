"""
Bitstamp Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.bitstamp_exchange_data import BitstampExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitstamp.request_base import BitstampRequestData


class BitstampRequestDataSpot(BitstampRequestData):
    """Bitstamp Spot Feed for market data."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "BITSTAMP___SPOT")
        self._params = BitstampExchangeDataSpot()
        self.rest_url = self._params.rest_url
        # Set API keys for authentication
        self.public_key = kwargs.get("public_key")
        self.secret = kwargs.get("private_key") or kwargs.get("secret")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data."""
        request_type = "get_tick"
        path = self._params.get_rest_path(request_type)
        request_symbol = self._params.get_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        # Bitstamp ticker path: /api/v2/ticker/{currency_pair}/
        # path is like "GET /api/v2/ticker", construct full request path
        if " " in path:
            method, rest_path = path.split(" ", 1)
            path = f"{method} {rest_path}/{request_symbol}/"
        else:
            path = f"{path}/{request_symbol}/"
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # Bitstamp returns ticker object directly
        ticker = input_data if isinstance(input_data, dict) else {}
        return [ticker], bool(ticker)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        return self._get_tick(symbol, extra_data, **kwargs)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth."""
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type)
        request_symbol = self._params.get_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        # Bitstamp order book path: /api/v2/order_book/{currency_pair}/
        # path is like "GET /api/v2/order_book", construct full request path
        if " " in path:
            method, rest_path = path.split(" ", 1)
            path = f"{method} {rest_path}/{request_symbol}/"
        else:
            path = f"{path}/{request_symbol}/"
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # Bitstamp returns {timestamp: ..., bids: [[price, qty], ...], asks: [[price, qty], ...]}
        depth = input_data if isinstance(input_data, dict) else {}
        return [depth], bool(depth)

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get OHLC/kline data."""
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type)
        request_symbol = self._params.get_symbol(symbol)
        request_period = self._params.get_period(period)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        # Bitstamp OHLC path: /api/v2/ohlc/{currency_pair}/
        # path is like "GET /api/v2/ohlc", construct full request path
        if " " in path:
            method, rest_path = path.split(" ", 1)
            path = f"{method} {rest_path}/{request_symbol}/"
        else:
            path = f"{path}/{request_symbol}/"
        return self.request(path, params={
            "step": request_period,
            "limit": count,
        }, extra_data=extra_data)

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # Bitstamp returns {data: {ohlc: [[...]...]}}
        if isinstance(input_data, dict):
            data = input_data.get("data", {})
            ohlc = data.get("ohlc", []) if isinstance(data, dict) else []
            return [ohlc], bool(ohlc)
        return [], False

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        return self._get_kline(symbol, period, count, extra_data, **kwargs)

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get available trading pairs."""
        request_type = "get_contract"
        path = self._params.get_rest_path(request_type)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": "ALL",
            "normalize_function": self._get_exchange_info_normalize_function,
        })
        # path is like "GET /api/v2/trading-pairs-info/", pass as-is
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # Bitstamp returns array of trading pair info
        pairs = input_data if isinstance(input_data, list) else []
        return [pairs], bool(pairs)

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange info."""
        return self._get_exchange_info(extra_data, **kwargs)

    def _get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account information."""
        request_type = "get_account"
        path = self._params.get_rest_path(request_type)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_account_normalize_function,
            "is_sign": True if self.public_key and self.secret else False,  # Requires authentication
        })
        # path is like "POST /api/v2/balance/"
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # Bitstamp returns balance dict with currency balances
        account = input_data if isinstance(input_data, dict) else {}
        return [account], bool(account)

    def get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account information."""
        return self._get_account(symbol, extra_data, **kwargs)

    def async_get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account information asynchronously."""
        # For now, just call the sync version
        # In a full implementation, this would use async HTTP client
        return self._get_account(symbol, extra_data, **kwargs)

    def _get_balance(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account balance."""
        request_type = "get_balance"
        path = self._params.get_rest_path(request_type)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_balance_normalize_function,
            "is_sign": True if self.public_key and self.secret else False,  # Requires authentication
        })
        # path is like "POST /api/v2/balance/"
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # Bitstamp returns balance dict
        balance = input_data if isinstance(input_data, dict) else {}
        return [balance], bool(balance)

    def get_balance(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account balance."""
        return self._get_balance(symbol, extra_data, **kwargs)

    def _get_deals(self, symbol="BTC-USD", count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        """Get trade/deal history."""
        request_type = "get_deals"
        path = self._params.get_rest_path(request_type)
        request_symbol = self._params.get_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_deals_normalize_function,
            "is_sign": True if self.public_key and self.secret else False,  # Requires authentication
        })
        # path is like "POST /api/v2/user_transactions"
        params = {}
        if count:
            params['limit'] = count
        if start_time:
            params['start_time'] = start_time
        if end_time:
            params['end_time'] = end_time
        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # Bitstamp returns array of transactions
        deals = input_data if isinstance(input_data, list) else []
        return [deals], bool(deals)

    def get_deals(self, symbol="BTC-USD", count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        """Get trade/deal history."""
        return self._get_deals(symbol, count, start_time, end_time, extra_data, **kwargs)
