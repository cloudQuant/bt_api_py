"""
Bitbns Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.bitbns_exchange_data import BitbnsExchangeDataSpot
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
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "BITBNS___SPOT")

    def _normalize_symbol(self, symbol):
        """Normalize symbol to bitbns format (uppercase, base asset only)."""
        # Bitbns uses base asset symbol (e.g., BTC, ETH)
        # For USDT pairs, use format like BTC_USDT
        symbol = symbol.upper()
        if "_" in symbol:
            # Already in format like BTC_USDT
            return symbol
        if "/" in symbol:
            # Convert BTC/USDT to BTC_USDT
            return symbol.replace("/", "_")
        if "-" in symbol:
            return symbol.replace("-", "_")
        # Just base asset
        return symbol

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data."""
        request_type = "get_tick"
        sym = self._normalize_symbol(symbol)
        # Bitbns public API uses /ticker endpoint
        # Determine market (INR or USDT)
        market = "USDT" if "_USDT" in sym.upper() else "INR"
        base = sym.replace("_USDT", "").replace("_INR", "")

        path = f"GET /ticker"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return self.request(path, params={"symbol": base, "market": market}, extra_data=extra_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        if data and input_data.get("status") == 1:
            # Return all tickers or specific ticker
            return [data], True
        return [], False

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        return self._get_tick(symbol, extra_data, **kwargs)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth."""
        request_type = "get_depth"
        sym = self._normalize_symbol(symbol)
        # Determine market (INR or USDT)
        market = "USDT" if "_USDT" in sym.upper() else "INR"
        base = sym.replace("_USDT", "").replace("_INR", "")

        path = f"GET /orderBook"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        return self.request(path, params={"symbol": base, "market": market}, extra_data=extra_data)

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
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data."""
        request_type = "get_kline"
        sym = self._normalize_symbol(symbol)
        # Determine market (INR or USDT)
        market = "USDT" if "_USDT" in sym.upper() else "INR"
        base = sym.replace("_USDT", "").replace("_INR", "")

        path = f"GET /kline"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "period": period,
            "normalize_function": self._get_kline_normalize_function,
        })
        return self.request(path, params={
            "symbol": base,
            "market": market,
            "period": period,
            "page": 1,
        }, extra_data=extra_data)

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
        return self._get_kline(symbol, period, count, extra_data, **kwargs)

    def _get_trades(self, symbol, limit=100, extra_data=None, **kwargs):
        """Get recent trades."""
        request_type = "get_trades"
        sym = self._normalize_symbol(symbol)
        # Determine market (INR or USDT)
        market = "USDT" if "_USDT" in sym.upper() else "INR"
        base = sym.replace("_USDT", "").replace("_INR", "")

        path = f"GET /recentTrades"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_trades_normalize_function,
        })
        return self.request(path, params={
            "symbol": base,
            "market": market,
            "limit": limit,
        }, extra_data=extra_data)

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
        return self._get_trades(symbol, limit, extra_data, **kwargs)
