import json
import time
from typing import Dict, List, Optional, Union

from bt_api_py.containers.exchanges.korbit_exchange_data import KorbitExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.korbit_ticker import KorbitRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class KorbitRequestData(Feed):
    """Korbit Request Data Handler

    Handles REST API requests to Korbit exchange with OAuth2 API Key authentication.
    Korbit is a Korean cryptocurrency exchange focused on KRW trading pairs.
    """

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)

        # Exchange configuration
        self.exchange_name = kwargs.get("exchange_name", "korbit")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "korbit_spot_feed.log")

        # Exchange data configuration
        self._params = KorbitExchangeDataSpot()

        # Logger setup
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()

        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

        # Rate limiting
        self.rate_limiter = kwargs.get("rate_limiter", None)

        # API credentials (OAuth2 token)
        self.api_key = kwargs.get("api_key", None)
        self.api_secret = kwargs.get("api_secret", None)

        # API URLs
        self.rest_url = self._params.rest_url

        # Default headers
        self.default_headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

    def _get_headers(self) -> Dict[str, str]:
        """Generate authentication headers for Korbit API

        Returns:
            Dictionary of headers including authentication
        """
        headers = self.default_headers.copy()

        # Korbit uses OAuth2 Bearer token authentication
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'

        return headers

    @classmethod
    def _capabilities(cls) -> Dict[str, set]:
        """Return the capabilities of this feed"""
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
        }

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request function.

        Args:
            path: Request path (e.g., "GET /v1/ticker/detailed")
            params: URL query parameters
            body: Request body (for POST requests)
            extra_data: Extra data for processing
            timeout: Request timeout in seconds

        Returns:
            RequestData: Response data
        """
        if params is None:
            params = {}

        # Split method and path
        parts = path.split(" ", 1)
        if len(parts) == 2:
            method, request_path = parts
        else:
            method = "GET"
            request_path = path

        # Build URL
        url = f"{self._params.rest_url}{request_path}"

        # Build query string
        if params and method == "GET":
            import urllib.parse
            query_string = urllib.parse.urlencode(params)
            url = f"{url}?{query_string}" if "?" not in url else f"{url}&{query_string}"

        # Prepare headers
        headers = self._get_headers()

        # Make request
        res = self.http_request(method, url, headers, body if method == "POST" else None, timeout)
        return RequestData(res, extra_data)

    def _get_ticker(self, symbol: str, extra_data: Optional[Dict] = None, **kwargs) -> tuple:
        """Get ticker information

        Args:
            symbol: Trading pair (e.g., 'BTC-KRW', 'btc_krw')
            extra_data: Additional data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_ticker"
        path = self._params.get_rest_path(request_type)

        params = {"currency_pair": request_symbol}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_ticker_normalize_function,
            }
        )

        return path, params, extra_data

    @staticmethod
    def _get_ticker_normalize_function(input_data, extra_data):
        """Normalize ticker response data"""
        status = input_data is not None

        if isinstance(input_data, dict):
            symbol_name = extra_data["symbol_name"]
            asset_type = extra_data["asset_type"]

            data = [KorbitRequestTickerData(input_data, symbol_name, asset_type, True)]
        else:
            data = []

        return data, status

    def _get_order_book(self, symbol: str, limit: int = 50, extra_data: Optional[Dict] = None, **kwargs) -> tuple:
        """Get order book data

        Args:
            symbol: Trading pair
            limit: Number of price levels
            extra_data: Additional data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_orderbook"
        path = self._params.get_rest_path(request_type)

        params = {"currency_pair": request_symbol}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_order_book_normalize_function,
            }
        )

        return path, params, extra_data

    @staticmethod
    def _get_order_book_normalize_function(input_data, extra_data):
        """Normalize order book response data"""
        status = input_data is not None

        # Korbit orderbook format:
        # {"timestamp": "...", "bids": [["price", "amount"], ...], "asks": [["price", "amount"], ...]}
        if isinstance(input_data, dict):
            status = True
            input_data["symbol_name"] = extra_data.get("symbol_name", "") if extra_data else ""
        else:
            input_data = {}

        return [input_data], status

    def _get_trades(self, symbol: str, limit: int = 100, extra_data: Optional[Dict] = None, **kwargs) -> tuple:
        """Get recent trades

        Args:
            symbol: Trading pair
            limit: Number of trades
            extra_data: Additional data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_trades"
        path = self._params.get_rest_path(request_type)

        params = {"currency_pair": request_symbol}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_trades_normalize_function,
            }
        )

        return path, params, extra_data

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize trades response data"""
        status = input_data is not None

        if isinstance(input_data, list):
            data = input_data
        else:
            data = []

        return [data], status

    def _get_klines(self, symbol: str, period: str = "1h", limit: int = 100, extra_data: Optional[Dict] = None, **kwargs) -> tuple:
        """Get kline/candlestick data

        Args:
            symbol: Trading pair
            period: Time period (1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d)
            limit: Number of candles
            extra_data: Additional data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_klines"
        path = self._params.get_rest_path(request_type)

        # Convert period to exchange format
        exchange_period = self._params.get_period(period)

        params = {}
        if exchange_period:
            params['timeUnit'] = exchange_period
        if limit:
            params['count'] = limit

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_klines_normalize_function,
            }
        )

        return path, params, extra_data

    @staticmethod
    def _get_klines_normalize_function(input_data, extra_data):
        """Normalize kline response data"""
        status = input_data is not None

        if isinstance(input_data, list):
            data = input_data
        else:
            data = []

        return [data], status

    def _get_server_time(self, extra_data=None, **kwargs):
        """Get server time parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_server_time"
        path = self._params.get_rest_path(request_type)

        params = {}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_server_time_normalize_function,
            }
        )

        return path, params, extra_data

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time

        Returns:
            RequestData with server time
        """
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        """Normalize server time response"""
        status = input_data is not None

        if isinstance(input_data, dict):
            # Korbit returns {"timestamp": "..."}
            data = [input_data]
        else:
            data = []

        return data, status

    def _make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """Generate make order parameters for Korbit

        Args:
            symbol: Trading pair (e.g., 'BTC-KRW')
            vol: Order volume
            price: Order price (required for limit orders)
            order_type: Order type (buy-limit, sell-limit, buy-market, sell-market)
            offset: Order offset (open, close) - not used by Korbit
            post_only: Post-only flag - not used by Korbit
            client_order_id: Client order ID
            extra_data: Additional data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"

        # Parse order type
        if order_type.startswith("buy-"):
            side = "buy"
        elif order_type.startswith("sell-"):
            side = "sell"
        else:
            side = "buy"

        order_subtype = order_type.split("-")[-1] if "-" in order_type else "limit"

        path = self._params.get_rest_path(request_type, symbol=request_symbol)

        params = {
            "symbol": request_symbol,
            "type": order_subtype,
            "side": side,
            "amount": vol,
        }

        if price is not None and order_subtype != "market":
            params["price"] = price

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._make_order_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        """Normalize make order response"""
        status = input_data is not None

        if isinstance(input_data, dict):
            data = [input_data]
        elif isinstance(input_data, list):
            data = input_data
        else:
            data = []

        return data, status

    def _cancel_order(self, order_id, symbol=None, extra_data=None, **kwargs):
        """Generate cancel order parameters for Korbit

        Args:
            order_id: Order ID to cancel
            symbol: Trading pair (optional for some exchanges)
            extra_data: Additional data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type, order_id=order_id)

        params = {
            "id": order_id,
        }

        if symbol:
            params["symbol"] = self._params.get_symbol(symbol)

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._cancel_order_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        """Normalize cancel order response"""
        status = input_data is not None

        if isinstance(input_data, dict):
            data = [input_data]
        elif isinstance(input_data, list):
            data = input_data
        else:
            data = []

        return data, status

    # Public methods for external use
    def get_ticker(self, symbol, extra_data=None, **kwargs):
        """Public method to get ticker data"""
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Alias for get_ticker"""
        return self.get_ticker(symbol, extra_data, **kwargs)

    def get_order_book(self, symbol, limit=50, extra_data=None, **kwargs):
        """Public method to get order book data"""
        path, params, extra_data = self._get_order_book(symbol, limit, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def get_depth(self, symbol, count=50, extra_data=None, **kwargs):
        """Alias for get_order_book"""
        return self.get_order_book(symbol, count, extra_data, **kwargs)

    def get_trades(self, symbol, limit=100, extra_data=None, **kwargs):
        """Public method to get recent trades"""
        path, params, extra_data = self._get_trades(symbol, limit, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def get_klines(self, symbol, period="1h", limit=100, extra_data=None, **kwargs):
        """Public method to get kline data"""
        path, params, extra_data = self._get_klines(symbol, period, limit, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        """Alias for get_klines"""
        return self.get_klines(symbol, period, count, extra_data, **kwargs)

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange info (currency pairs)"""
        request_type = "get_exchange_info"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            }
        )
        return self.request(path, params, extra_data=extra_data)
