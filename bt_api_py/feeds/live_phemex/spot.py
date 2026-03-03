"""
Phemex Spot Feed implementation.

Provides market data access for Phemex spot trading.
"""

from bt_api_py.containers.exchanges.phemex_exchange_data import PhemexExchangeDataSpot
from bt_api_py.containers.tickers.phemex_ticker import PhemexRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_phemex.request_base import PhemexRequestData
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class PhemexRequestDataSpot(PhemexRequestData):
    """Phemex Spot Feed for spot market data.

    Supports:
    - Getting ticker information (24hr statistics)
    - Getting order book depth
    - Getting kline/candlestick data
    - Getting recent trades
    - Getting exchange info (trading pairs)
    """

    @classmethod
    def _capabilities(cls):
        """Declare supported capabilities."""
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "PHEMEX___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = PhemexExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()

    # ==================== Ticker ====================

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data.

        Args:
            symbol: Trading pair symbol
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_tick"
        path = self._params.get_rest_path(request_type)

        # Phemex spot symbols use 's' prefix
        phemex_symbol = self._params.get_symbol(symbol)

        params = {"symbol": phemex_symbol}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_tick_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker response."""
        if not input_data:
            return [], False

        # Check for success code
        if input_data.get("code") != 0:
            return [], False

        ticker = input_data.get("data", {})
        status = ticker is not None

        return [ticker], status

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker information.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT')
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with ticker information
        """
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Order Book ====================

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth.

        Args:
            symbol: Trading pair symbol
            count: Number of levels
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type)

        phemex_symbol = self._params.get_symbol(symbol)

        params = {
            "symbol": phemex_symbol,
            "level": min(count, 100),  # Phemex supports up to 100 levels
        }

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_depth_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth response."""
        if not input_data:
            return [], False

        if input_data.get("code") != 0:
            return [], False

        book = input_data.get("data", {}).get("book", {})
        status = book is not None

        return [book], status

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book.

        Args:
            symbol: Trading pair symbol
            count: Number of levels
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with order book data
        """
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Klines ====================

    def _get_kline(
        self,
        symbol,
        period,
        count=100,
        from_time=None,
        to_time=None,
        extra_data=None,
        **kwargs
    ):
        """Get kline data.

        Args:
            symbol: Trading pair symbol
            period: Time period (1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w)
            count: Number of data points
            from_time: Start time (seconds)
            to_time: End time (seconds)
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type)

        phemex_symbol = self._params.get_symbol(symbol)
        resolution = self._params.get_period(period)

        params = {
            "symbol": phemex_symbol,
            "resolution": resolution,
            "limit": min(count, 1000),
        }

        if from_time:
            params["from"] = from_time
        if to_time:
            params["to"] = to_time

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "period": period,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_kline_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline response."""
        if not input_data:
            return [], False

        if input_data.get("code") != 0:
            return [], False

        rows = input_data.get("data", {}).get("rows", [])
        status = rows is not None

        return rows or [], status

    def get_kline(
        self,
        symbol,
        period,
        count=100,
        from_time=None,
        to_time=None,
        extra_data=None,
        **kwargs
    ):
        """Get kline/candlestick data.

        Args:
            symbol: Trading pair symbol
            period: Time period (1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w)
            count: Number of data points
            from_time: Start time (seconds)
            to_time: End time (seconds)
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with kline data
        """
        path, params, extra_data = self._get_kline(
            symbol, period, count, from_time, to_time, extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Exchange Info ====================

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information (trading pairs).

        Args:
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_exchange_info"
        path = self._params.get_rest_path(request_type)

        params = {}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_exchange_info_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info response."""
        if not input_data:
            return [], False

        if input_data.get("code") != 0:
            return [], False

        products = input_data.get("data", {}).get("products", [])
        status = products is not None

        return products or [], status

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information.

        Args:
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with exchange information
        """
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Recent Trades ====================

    def _get_trades(self, symbol, limit=100, extra_data=None, **kwargs):
        """Get recent trades.

        Args:
            symbol: Trading pair symbol
            limit: Number of trades
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_trades"
        path = self._params.get_rest_path(request_type)

        phemex_symbol = self._params.get_symbol(symbol)

        params = {
            "symbol": phemex_symbol,
            "limit": min(limit, 500),
        }

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_trades_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize trades response."""
        if not input_data:
            return [], False

        if input_data.get("code") != 0:
            return [], False

        trades = input_data.get("data", {}).get("trades", [])
        status = trades is not None

        return trades or [], status

    def get_trades(self, symbol, limit=100, extra_data=None, **kwargs):
        """Get recent trades.

        Args:
            symbol: Trading pair symbol
            limit: Number of trades
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with recent trades
        """
        path, params, extra_data = self._get_trades(symbol, limit, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Async Methods ====================

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data asynchronously.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT')
            extra_data: Extra data
            **kwargs: Additional parameters
        """
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data), self.async_callback)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth asynchronously.

        Args:
            symbol: Trading pair symbol
            count: Number of levels
            extra_data: Extra data
            **kwargs: Additional parameters
        """
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data), self.async_callback)

    def async_get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        """Get kline/candlestick data asynchronously.

        Args:
            symbol: Trading pair symbol
            period: Time period (1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w)
            count: Number of data points
            extra_data: Extra data
            **kwargs: Additional parameters
        """
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data=extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data), self.async_callback)
