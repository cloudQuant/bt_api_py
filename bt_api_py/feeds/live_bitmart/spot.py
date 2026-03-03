"""
BitMart Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.bitmart_exchange_data import BitmartExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitmart.request_base import BitmartRequestData


class BitmartRequestDataSpot(BitmartRequestData):
    """BitMart Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITMART___SPOT")

    def _convert_symbol(self, symbol):
        """Convert symbol to BitMart format.

        BitMart uses underscore separator: BTC_USDT
        Converts:
        - BTC/USDT -> BTC_USDT
        - BTC-USDT -> BTC_USDT
        - BTCUSDT -> BTC_USDT (by intelligent split)
        """
        if "/" in symbol:
            return symbol.replace("/", "_")
        elif "-" in symbol:
            return symbol.replace("-", "_")
        # Try to intelligently split symbols without separators
        # Use legal currencies list to find the quote currency
        legal_currencies = self._params.legal_currency
        for currency in sorted(legal_currencies, key=len, reverse=True):
            if symbol.endswith(currency) and len(symbol) > len(currency):
                base = symbol[:-len(currency)]
                return f"{base}_{currency}"
        # If no match, return as-is
        return symbol

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data.

        BitMart ticker endpoint: GET /spot/quotation/v3/ticker
        Symbol format: BTC_USDT
        """
        request_type = "get_tick"
        path = f"GET /spot/quotation/v3/ticker"
        bitmart_symbol = self._convert_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bitmart_symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return self.request(path, params={"symbol": bitmart_symbol}, extra_data=extra_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data from BitMart response.

        BitMart ticker response format:
        {
          "code": 1000,
          "message": "OK",
          "data": {
            "symbol": "BTC_USDT",
            "last_price": "50000",
            "bid_1": "49999",
            "ask_1": "50001",
            "high_24h": "51000",
            "low_24h": "49000",
            "volume_24h": "1234.56",
            "timestamp": 1234567890
          }
        }
        """
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        return [data], data is not None

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        return self._get_tick(symbol, extra_data, **kwargs)

    def _get_tickers(self, extra_data=None, **kwargs):
        """Get all tickers.

        BitMart all tickers endpoint: GET /spot/quotation/v3/tickers
        """
        request_type = "get_tickers"
        path = f"GET /spot/quotation/v3/tickers"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_tickers_normalize_function,
        })
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_tickers_normalize_function(input_data, extra_data):
        """Normalize all tickers data from BitMart response."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        tickers = data.get("tickers", []) if isinstance(data, dict) else data
        return [tickers], tickers is not None

    def get_tickers(self, extra_data=None, **kwargs):
        """Get all tickers."""
        return self._get_tickers(extra_data, **kwargs)

    def _get_depth(self, symbol, count=35, extra_data=None, **kwargs):
        """Get order book depth.

        BitMart orderbook endpoint: GET /spot/quotation/v3/books
        Parameters: symbol (required), limit (optional, default 35, max 50)
        """
        request_type = "get_depth"
        path = f"GET /spot/quotation/v3/books"
        bitmart_symbol = self._convert_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bitmart_symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        # BitMart uses 'limit' parameter, max 50
        limit = min(count, 50)
        return self.request(path, params={"symbol": bitmart_symbol, "limit": limit}, extra_data=extra_data)

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth data from BitMart response.

        BitMart orderbook response format:
        {
          "code": 1000,
          "message": "OK",
          "data": {
            "symbol": "BTC_USDT",
            "timestamp": 1234567890,
            "buys": [["49999", "0.5"], ...],  # [price, quantity]
            "sells": [["50001", "0.5"], ...]
          }
        }
        """
        if not input_data:
            return [], False
        depth = input_data.get("data", {})
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_trades(self, symbol, count=50, extra_data=None, **kwargs):
        """Get recent trades.

        BitMart trades endpoint: GET /spot/quotation/v3/trades
        Parameters: symbol (required), limit (optional, default 50, max 50)
        """
        request_type = "get_trades"
        path = f"GET /spot/quotation/v3/trades"
        bitmart_symbol = self._convert_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bitmart_symbol,
            "normalize_function": self._get_trades_normalize_function,
        })
        limit = min(count, 50)
        return self.request(path, params={"symbol": bitmart_symbol, "limit": limit}, extra_data=extra_data)

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize trades data from BitMart response.

        BitMart trades response format:
        {
          "code": 1000,
          "message": "OK",
          "data": {
            "symbol": "BTC_USDT",
            "trades": [
              {
                "amount": "0.1234",
                "count": "1",
                "price": "50000",
                "time": 1234567890
              },
              ...
            ]
          }
        }
        """
        if not input_data:
            return [], False
        trades_data = input_data.get("data", {})
        trades = trades_data.get("trades", []) if isinstance(trades_data, dict) else []
        return [trades], trades is not None

    def get_trades(self, symbol, count=50, extra_data=None, **kwargs):
        """Get recent trades."""
        return self._get_trades(symbol, count, extra_data, **kwargs)

    def _get_kline(self, symbol, period, count=100, extra_data=None, **kwargs):
        """Get kline/candlestick data.

        BitMart kline endpoint: GET /spot/quotation/v3/klines
        Parameters:
        - symbol (required)
        - step (required): K-line period in minutes: 1,3,5,15,30,45,60,120,180,240,1440,10080,43200
        - limit (optional)
        - before/after (optional): timestamps
        """
        import time
        request_type = "get_kline"
        path = f"GET /spot/quotation/v3/klines"
        bitmart_symbol = self._convert_symbol(symbol)

        # Get period in minutes
        bitmart_period = self._params.get_period(period)

        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bitmart_symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        return self.request(path, params={
            "symbol": bitmart_symbol,
            "step": bitmart_period,
            "limit": count,
        }, extra_data=extra_data)

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline data from BitMart response.

        BitMart kline response format:
        {
          "code": 1000,
          "message": "OK",
          "data": {
            "symbol": "BTC_USDT",
            "klines": [
              ["50000", "51000", "49000", "50500", "123.45", 1234567890],
              # [open, high, low, close, volume, timestamp]
              ...
            ]
          }
        }
        """
        if not input_data:
            return [], False
        kline_data = input_data.get("data", {})
        klines = kline_data.get("klines", []) if isinstance(kline_data, dict) else []
        return [klines], klines is not None

    def get_kline(self, symbol, period, count=100, extra_data=None, **kwargs):
        """Get kline data."""
        return self._get_kline(symbol, period, count, extra_data, **kwargs)

    def _get_currencies(self, extra_data=None, **kwargs):
        """Get all supported currencies.

        BitMart currencies endpoint: GET /spot/v1/currencies
        """
        request_type = "get_currencies"
        path = f"GET /spot/v1/currencies"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_currencies_normalize_function,
        })
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_currencies_normalize_function(input_data, extra_data):
        """Normalize currencies data from BitMart response."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        currencies = data.get("currencies", []) if isinstance(data, dict) else data
        return [currencies], currencies is not None

    def get_currencies(self, extra_data=None, **kwargs):
        """Get all supported currencies."""
        return self._get_currencies(extra_data, **kwargs)

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange configuration (trading pairs info)."""
        # Use currencies to get exchange info
        return self._get_currencies(extra_data, **kwargs)

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange trading pairs configuration."""
        return self._get_exchange_info(extra_data, **kwargs)

    def _get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account information.

        BitMart account endpoint: GET /spot/v1/wallet
        Requires API key and secret.
        """
        request_type = "get_account"
        path = f"GET /spot/v1/wallet"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_account_normalize_function,
        })
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        """Normalize account data from BitMart response."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        return [data], data is not None

    def get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account information."""
        return self._get_account(symbol, extra_data, **kwargs)

    def async_get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account information asynchronously."""
        # For now, just call the sync version
        # TODO: Implement proper async version
        return self._get_account(symbol, extra_data, **kwargs)

    def _get_balance(self, symbol="USDT", extra_data=None, **kwargs):
        """Get balance for a specific currency.

        BitMart balance endpoint: GET /spot/v1/wallet
        Requires API key and secret.
        """
        request_type = "get_balance"
        path = f"GET /spot/v1/wallet"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_balance_normalize_function,
        })
        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        """Normalize balance data from BitMart response."""
        if not input_data:
            return [], False
        data = input_data.get("data", {})
        wallet = data.get("wallet", []) if isinstance(data, dict) else []
        return [wallet], wallet is not None

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get balance for a currency."""
        if symbol is None:
            symbol = "USDT"
        return self._get_balance(symbol, extra_data, **kwargs)

    def async_get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get balance asynchronously."""
        # For now, just call the sync version
        # TODO: Implement proper async version
        return self.get_balance(symbol, extra_data, **kwargs)

    def _get_deals(self, symbol="BTCUSDT", count=50, extra_data=None, **kwargs):
        """Get trade history/deals for a symbol.

        Note: This uses the public trades endpoint.
        For private trade history, a different endpoint would be needed.
        """
        request_type = "get_deals"
        return self._get_trades(symbol, count, extra_data, **kwargs)

    def get_deals(self, symbol=None, count=50, extra_data=None, **kwargs):
        """Get trade history for a symbol."""
        if symbol is None:
            symbol = "BTCUSDT"
        return self._get_deals(symbol, count, extra_data, **kwargs)

    def async_get_deals(self, symbol=None, count=50, extra_data=None, **kwargs):
        """Get trade history asynchronously."""
        # For now, just call the sync version
        # TODO: Implement proper async version
        return self.get_deals(symbol, count, extra_data, **kwargs)
