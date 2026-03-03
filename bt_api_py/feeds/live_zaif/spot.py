"""
Zaif Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.zaif_exchange_data import ZaifExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_zaif.request_base import ZaifRequestData


class ZaifRequestDataSpot(ZaifRequestData):
    """Zaif Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "ZAIF___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data.

        Zaif API endpoint: GET /api/1/ticker/{pair}
        Symbol format: btc_jpy (lowercase with underscore)
        """
        request_type = "get_tick"
        # Convert common symbol format to Zaif format (BTC/JPY -> btc_jpy)
        zaif_symbol = self._to_zaif_symbol(symbol)
        path = f"GET /api/1/ticker/{zaif_symbol}"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # Zaif returns ticker data directly as the response
        return [input_data], input_data is not None

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        return self._get_tick(symbol, extra_data, **kwargs)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth.

        Zaif API endpoint: GET /api/1/depth/{pair}
        """
        request_type = "get_depth"
        zaif_symbol = self._to_zaif_symbol(symbol)
        path = f"GET /api/1/depth/{zaif_symbol}"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        depth = input_data
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data.

        Zaif API endpoint: GET /api/1/trades/{pair}
        Note: Zaif doesn't have a dedicated kline endpoint,
        so we use trades endpoint to get recent trades.
        """
        request_type = "get_kline"
        zaif_symbol = self._to_zaif_symbol(symbol)
        path = f"GET /api/1/trades/{zaif_symbol}"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        # Zaif returns an array of trades
        trades = input_data if isinstance(input_data, list) else []
        return [trades], trades is not None

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        return self._get_kline(symbol, period, count, extra_data, **kwargs)

    def _to_zaif_symbol(self, symbol):
        """Convert symbol to Zaif format.

        Examples:
            BTC/JPY -> btc_jpy
            ETH/BTC -> eth_btc
            btc_jpy -> btc_jpy (already in correct format)
        """
        if "_" in symbol:
            return symbol.lower()
        # Assume format like BTC/JPY or BTCJPY
        symbol = symbol.replace("/", "").replace("-", "").upper()
        # Common quote currencies
        quotes = ["JPY", "BTC", "ETH", "MONA", "BCH"]
        for quote in quotes:
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base.lower()}_{quote.lower()}"
        # Default: return as-is with underscore if possible
        if len(symbol) >= 6:
            return f"{symbol[:3].lower()}_{symbol[3:].lower()}"
        return symbol.lower()
