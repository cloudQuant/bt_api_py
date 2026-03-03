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

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data.

        Note: Bitinka API documentation is limited.
        This implementation follows basic patterns for Latin American exchanges.
        """
        request_type = "get_tick"
        path = f"GET /ticker"
        bitinka_symbol = self._convert_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bitinka_symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return self.request(path, params={"market": bitinka_symbol}, extra_data=extra_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data from Bitinka response.

        Note: Actual response format may vary based on Bitinka's API.
        This is a generic implementation that should be adjusted
        based on actual API responses.
        """
        if not input_data:
            return [], False
        ticker = input_data.get("data", input_data)
        return [ticker], ticker is not None

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth.

        Note: Bitinka API documentation is limited.
        This implementation follows basic patterns.
        """
        request_type = "get_depth"
        path = f"GET /orderbook"
        bitinka_symbol = self._convert_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bitinka_symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        return self.request(path, params={"market": bitinka_symbol}, extra_data=extra_data)

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth data from Bitinka response."""
        if not input_data:
            return [], False
        depth = input_data.get("data", input_data)
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_trades(self, symbol, count=50, extra_data=None, **kwargs):
        """Get recent trades.

        Note: Bitinka API documentation is limited.
        """
        request_type = "get_trades"
        path = f"GET /trades"
        bitinka_symbol = self._convert_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bitinka_symbol,
            "normalize_function": self._get_trades_normalize_function,
        })
        return self.request(path, params={"market": bitinka_symbol}, extra_data=extra_data)

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize trades data from Bitinka response."""
        if not input_data:
            return [], False
        trades = input_data.get("data", input_data)
        return [trades], trades is not None

    def get_trades(self, symbol, count=50, extra_data=None, **kwargs):
        """Get recent trades."""
        return self._get_trades(symbol, count, extra_data, **kwargs)

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange configuration (trading pairs info)."""
        request_type = "get_markets"
        path = f"GET /markets"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_exchange_info_normalize_function,
        })
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info from Bitinka response."""
        if not input_data:
            return [], False
        markets = input_data.get("data", input_data)
        return [markets], markets is not None

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange trading pairs configuration."""
        return self._get_exchange_info(extra_data, **kwargs)

    def _get_account(self, extra_data=None, **kwargs):
        """Get account information.

        Note: Bitinka API documentation is limited.
        This requires authentication.
        """
        request_type = "get_account"
        path = f"GET /account"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_account_normalize_function,
        })
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        """Normalize account data from Bitinka response."""
        if not input_data:
            return [], False
        account = input_data.get("data", input_data)
        return [account], account is not None

    def get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account information."""
        return self._get_account(extra_data, **kwargs)

    def async_get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account information asynchronously."""
        # Note: Bitinka doesn't have async implementation yet
        return self.get_account(symbol, extra_data, **kwargs)

    def _get_balance(self, symbol, extra_data=None, **kwargs):
        """Get account balance.

        Note: Bitinka API documentation is limited.
        This requires authentication.
        """
        request_type = "get_balance"
        path = f"GET /balance"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_balance_normalize_function,
        })
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        """Normalize balance data from Bitinka response."""
        if not input_data:
            return [], False
        balance = input_data.get("data", input_data)
        return [balance], balance is not None

    def get_balance(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account balance."""
        return self._get_balance(symbol, extra_data, **kwargs)

    def async_get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get account balance asynchronously."""
        # Note: Bitinka doesn't have async implementation yet
        return self.get_balance(symbol or "ALL", extra_data, **kwargs)

    def _get_deals(self, symbol, count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        """Get trade history/deals.

        Note: Bitinka API documentation is limited.
        """
        request_type = "get_deals"
        path = f"GET /trades"
        bitinka_symbol = self._convert_symbol(symbol)
        params = {"market": bitinka_symbol, "limit": count}
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time

        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bitinka_symbol,
            "normalize_function": self._get_deals_normalize_function,
        })
        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        """Normalize deals data from Bitinka response."""
        if not input_data:
            return [], False
        deals = input_data.get("data", input_data)
        return [deals], deals is not None

    def get_deals(self, symbol="BTC/USD", count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        """Get trade history."""
        return self._get_deals(symbol, count, start_time, end_time, extra_data, **kwargs)

    def async_get_deals(self, symbol, count=100, start_time=None, end_time=None, extra_data="", **kwargs):
        """Get trade history asynchronously."""
        # Note: Bitinka doesn't have async implementation yet
        return self.get_deals(symbol, count, start_time, end_time, extra_data, **kwargs)
