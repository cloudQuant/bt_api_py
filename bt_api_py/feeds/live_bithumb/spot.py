"""
Bithumb Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.bithumb_exchange_data import BithumbExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bithumb.request_base import BithumbRequestData


class BithumbRequestDataSpot(BithumbRequestData):
    """Bithumb Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "BITHUMB___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data parameters.

        Bithumb uses symbol format like BTC-USDT (hyphen separator).
        """
        request_type = "get_tick"
        path = f"GET /spot/ticker"
        # Convert common formats to Bithumb format
        bithumb_symbol = self._convert_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bithumb_symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        params = {"symbol": bithumb_symbol}
        return path, params, extra_data

    def _convert_symbol(self, symbol):
        """Convert symbol to Bithumb format.

        Converts BTC/USDT -> BTC-USDT
        Converts BTCUSDT -> BTC-USDT
        Keeps BTC-USDT as is
        """
        if "/" in symbol:
            return symbol.replace("/", "-")
        elif "_" in symbol:
            return symbol.replace("_", "-")
        # If already has hyphen, return as is
        if "-" in symbol:
            return symbol
        # Try to detect common pattern (e.g., BTCUSDT -> BTC-USDT)
        # This is a simple heuristic - may need improvement
        for quote in ["USDT", "USD", "BTC", "ETH", "KRW"]:
            if symbol.endswith(quote) and len(symbol) > len(quote):
                base = symbol[:-len(quote)]
                return f"{base}-{quote}"
        return symbol

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data from Bithumb response.

        Bithumb ticker response format:
        {
          "success": true,
          "data": [
            {
              "s": "BTC-USDT",  # symbol
              "c": "50000",      # last price (close)
              "h": "51000",      # 24h high
              "l": "49000",      # 24h low
              "v": "1234.56",    # 24h volume
              "p": "2.5"         # 24h change percent
            }
          ]
        }
        """
        if not input_data:
            return [], False
        data_list = input_data.get("data", [])
        if data_list and isinstance(data_list, list) and len(data_list) > 0:
            ticker = data_list[0]
            return [ticker], ticker is not None
        return [], False

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth parameters.

        Bithumb orderbook response format:
        {
          "success": true,
          "data": {
            "symbol": "BTC-USDT",
            "ver": "123",
            "b": [[price, quantity], ...],  # bids
            "s": [[price, quantity], ...]   # asks (sells)
          }
        }
        """
        request_type = "get_depth"
        path = f"GET /spot/orderBook"
        bithumb_symbol = self._convert_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bithumb_symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        params = {"symbol": bithumb_symbol, "limit": count}
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth data from Bithumb response."""
        if not input_data:
            return [], False
        depth = input_data.get("data", {})
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data parameters.

        Bithumb kline types: m1,m3,m5,m15,m30,h1,h2,h4,h6,h8,h12,d1,d3,w1,M1
        """
        import time
        request_type = "get_kline"
        path = f"GET /spot/kline"
        bithumb_symbol = self._convert_symbol(symbol)

        # Get period mapping
        bithumb_period = self._params.get_period(period)

        # Calculate time range
        end_time = int(time.time())
        start_time = end_time - (3600 * 24)  # Default 24 hours

        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bithumb_symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        params = {
            "symbol": bithumb_symbol,
            "type": bithumb_period,
            "start": start_time,
            "end": end_time,
        }
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline data from Bithumb response.

        Bithumb kline response format:
        {
          "success": true,
          "data": [
            {
              "o": "50000",  # open
              "h": "51000",  # high
              "l": "49000",  # low
              "c": "50500",  # close
              "v": "123.45"  # volume
            },
            ...
          ]
        }
        """
        if not input_data:
            return [], False
        klines = input_data.get("data", [])
        return [klines], klines is not None

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_trades(self, symbol, count=50, extra_data=None, **kwargs):
        """Get recent trades parameters."""
        request_type = "get_trades"
        path = f"GET /spot/trades"
        bithumb_symbol = self._convert_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bithumb_symbol,
            "normalize_function": self._get_trades_normalize_function,
        })
        params = {"symbol": bithumb_symbol, "limit": count}
        return path, params, extra_data

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize trades data from Bithumb response.

        Bithumb trades response format:
        {
          "success": true,
          "data": [
            {
              "s": "BTC-USDT",  # symbol
              "p": "50000",     # price
              "v": "0.1234",    # quantity
              "t": "1234567890" # timestamp
            },
            ...
          ]
        }
        """
        if not input_data:
            return [], False
        trades = input_data.get("data", [])
        return [trades], trades is not None

    def get_trades(self, symbol, count=50, extra_data=None, **kwargs):
        """Get recent trades."""
        path, params, extra_data = self._get_trades(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange configuration (trading pairs info) parameters."""
        request_type = "get_config"
        path = f"GET /spot/config"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_exchange_info_normalize_function,
        })
        return path, {}, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info from Bithumb response."""
        if not input_data:
            return [], False
        config = input_data.get("data", {})
        return [config], config is not None

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange trading pairs configuration."""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_account(self, extra_data=None, **kwargs):
        """Get account information.

        Bithumb account API requires authentication.
        Returns user account information including balances.
        """
        request_type = "get_account"
        path = f"GET /spot/account"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_account_normalize_function,
        })
        return path, {}, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        """Normalize account data from Bithumb response.

        Bithumb account response format:
        {
          "success": true,
          "data": {
            "accountId": "123456",
            "balances": [
              {"currency": "BTC", "available": "1.234", "frozen": "0.123"},
              ...
            ]
          }
        }
        """
        if not input_data:
            return [], False
        account = input_data.get("data", {})
        return [account], account is not None

    def get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account information.

        Args:
            symbol: Currency symbol (optional, "ALL" for all currencies)
            extra_data: Additional data dict
        """
        path, params, extra_data = self._get_account(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account information asynchronously.

        Note: Bithumb doesn't have async implementation yet.
        Falls back to synchronous call.
        """
        return self.get_account(symbol, extra_data, **kwargs)

    def _get_balance(self, symbol, extra_data=None, **kwargs):
        """Get account balance for a specific currency.

        Args:
            symbol: Currency symbol (e.g., "BTC", "USDT")
        """
        request_type = "get_balance"
        path = f"GET /spot/balance"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_balance_normalize_function,
        })
        return path, {"currency": symbol}, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        """Normalize balance data from Bithumb response.

        Bithumb balance response format:
        {
          "success": true,
          "data": {
            "currency": "BTC",
            "available": "1.234",
            "frozen": "0.123"
          }
        }
        """
        if not input_data:
            return [], False
        balance = input_data.get("data", {})
        return [balance], balance is not None

    def get_balance(self, symbol, extra_data=None, **kwargs):
        """Get balance for a specific currency.

        Args:
            symbol: Currency symbol (e.g., "BTC", "USDT")
        """
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_deals(self, symbol, count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        """Get trade history/deals.

        Args:
            symbol: Trading pair symbol (e.g., "BTC-USDT")
            count: Number of trades to retrieve
            start_time: Start timestamp (optional)
            end_time: End timestamp (optional)
        """
        request_type = "get_deals"
        path = f"GET /spot/trades"
        bithumb_symbol = self._convert_symbol(symbol)
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": bithumb_symbol,
            "normalize_function": self._get_deals_normalize_function,
        })
        params = {"symbol": bithumb_symbol, "limit": count}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return path, params, extra_data

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        """Normalize deals/trades data from Bithumb response.

        Bithumb trades response format:
        {
          "success": true,
          "data": [
            {
              "s": "BTC-USDT",
              "p": "50000",
              "v": "0.1234",
              "t": "1234567890"
            },
            ...
          ]
        }
        """
        if not input_data:
            return [], False
        trades = input_data.get("data", [])
        return [trades], trades is not None

    def get_deals(self, symbol, count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        """Get trade history.

        Args:
            symbol: Trading pair symbol (e.g., "BTC-USDT")
            count: Number of trades to retrieve
            start_time: Start timestamp (optional)
            end_time: End timestamp (optional)
        """
        path, params, extra_data = self._get_deals(symbol, count, start_time, end_time, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_deals(self, symbol, count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        """Get trade history asynchronously.

        Note: Bithumb doesn't have async implementation yet.
        Falls back to synchronous call.
        """
        return self.get_deals(symbol, count, start_time, end_time, extra_data, **kwargs)
