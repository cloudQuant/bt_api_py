"""
Ripio Spot Feed implementation.

Provides market data access for Ripio spot trading.
"""

from bt_api_py.containers.exchanges.ripio_exchange_data import RipioExchangeDataSpot
from bt_api_py.containers.tickers.ripio_ticker import RipioRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_ripio.request_base import RipioRequestData
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class RipioRequestDataSpot(RipioRequestData):
    """Ripio Spot Feed for spot market data.

    Supports:
    - Getting ticker information
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
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "RIPIO___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = RipioExchangeDataSpot()
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

        # Ripio uses underscore format: BTC_USDT
        ripio_symbol = self._params.get_symbol(symbol)

        params = {}

        # For GET requests with symbol in path
        path = path.replace(":symbol", ripio_symbol)

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

        # Ripio wraps response in {'success': true, 'data': {...}}
        if not input_data.get("success", False):
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

        ripio_symbol = self._params.get_symbol(symbol)

        params = {"limit": min(count, 100)}

        # Replace symbol placeholder
        path = path.replace(":symbol", ripio_symbol)

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

        if not input_data.get("success", False):
            return [], False

        depth = input_data.get("data", {})
        status = depth is not None

        return [depth], status

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

        ripio_symbol = self._params.get_symbol(symbol)
        ripio_period = self._params.get_period(period)

        params = {
            "limit": min(count, 1000),
            "period": ripio_period,
        }

        if from_time:
            params["from"] = from_time
        if to_time:
            params["to"] = to_time

        # Replace symbol placeholder
        path = path.replace(":symbol", ripio_symbol)

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

        if not input_data.get("success", False):
            return [], False

        klines = input_data.get("data", [])
        status = klines is not None

        return klines or [], status

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

        if not input_data.get("success", False):
            return [], False

        products = input_data.get("data", [])
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

        ripio_symbol = self._params.get_symbol(symbol)

        params = {"limit": min(limit, 500)}

        # Replace symbol placeholder
        path = path.replace(":symbol", ripio_symbol)

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

        if not input_data.get("success", False):
            return [], False

        trades = input_data.get("data", [])
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

    # ==================== Account / Trading ====================

    def _get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get account balance. Returns (path, params, extra_data)."""
        path = "/api/v1/balances"
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_balance",
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RipioRequestDataSpot._get_balance_normalize_function,
            },
        )
        return path, {}, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and not input_data.get("success", True):
            return [], False
        data = input_data.get("data", input_data) if isinstance(input_data, dict) else input_data
        return [data], True

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get account balance."""
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def _get_account(self, extra_data=None, **kwargs):
        """Get account information. Returns (path, params, extra_data)."""
        path = "/api/v1/balances"
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_account",
                "symbol_name": "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RipioRequestDataSpot._get_account_normalize_function,
            },
        )
        return path, {}, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        data = input_data.get("data", input_data) if isinstance(input_data, dict) else input_data
        return [data], True

    def get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account information."""
        path, params, extra_data = self._get_account(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    def _make_order(self, symbol, volume, price, order_type, offset="open",
                    extra_data=None, **kwargs):
        """Prepare make order request. Returns (path, params, extra_data)."""
        ripio_symbol = self._params.get_symbol(symbol)
        path = "/api/v1/order"
        params = {
            "pair": ripio_symbol,
            "amount": str(volume),
            "price": str(price),
            "side": "buy" if "buy" in order_type.lower() else "sell",
            "type": "limit",
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "make_order",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": RipioRequestDataSpot._make_order_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        return [input_data], True

    def make_order(self, symbol, volume, price, order_type, offset="open",
                   extra_data=None, **kwargs):
        """Place an order."""
        path, params, extra_data = self._make_order(
            symbol, volume, price, order_type, offset, extra_data, **kwargs
        )
        return self.request(path, body=params, extra_data=extra_data, is_sign=True)

    def _cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Prepare cancel order request. Returns (path, params, extra_data)."""
        path = f"/api/v1/order/{order_id}"
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "cancel_order",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "order_id": order_id,
            },
        )
        return path, {}, extra_data

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel an order."""
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data, is_sign=True)

    # ==================== Async Methods ====================

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Async get ticker information.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT')
            extra_data: Extra data
            **kwargs: Additional parameters
        """
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Async get order book.

        Args:
            symbol: Trading pair symbol
            count: Number of levels
            extra_data: Extra data
            **kwargs: Additional parameters
        """
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_get_kline(
        self,
        symbol,
        period,
        count=100,
        from_time=None,
        to_time=None,
        extra_data=None,
        **kwargs
    ):
        """Async get kline/candlestick data.

        Args:
            symbol: Trading pair symbol
            period: Time period (1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w)
            count: Number of data points
            from_time: Start time (seconds)
            to_time: End time (seconds)
            extra_data: Extra data
            **kwargs: Additional parameters
        """
        path, params, extra_data = self._get_kline(
            symbol, period, count, from_time, to_time, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )
