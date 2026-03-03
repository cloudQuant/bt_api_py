import hashlib
import hmac
import json
import time
from typing import Dict, List, Optional, Union
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.latoken_exchange_data import LatokenExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.latoken_ticker import LatokenRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class LatokenRequestData(Feed):
    """Latoken Request Data Handler

    Handles REST API requests to Latoken exchange with HMAC-SHA512 authentication.
    Latoken uses UUID-based currency identification.
    """

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)

        # Exchange configuration
        self.exchange_name = kwargs.get("exchange_name", "latoken")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "latoken_spot_feed.log")

        # Exchange data configuration
        self._params = kwargs.get("exchange_data", LatokenExchangeDataSpot())

        # Logger setup
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()

        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

        # Rate limiting
        self.rate_limiter = kwargs.get("rate_limiter", None)

        # API credentials (HMAC-SHA512)
        self.api_key = kwargs.get("api_key", None)
        self.api_secret = kwargs.get("api_secret", None)

        # API URLs
        self.rest_url = self._params.rest_url

        # Default headers
        self.default_headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

    @classmethod
    def _capabilities(cls):
        """Return the capabilities of this feed"""
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
        }

    def _generate_signature(self, method: str, path: str, params: Optional[Dict] = None) -> str:
        """Generate HMAC-SHA512 signature for Latoken API

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API endpoint path
            params: Query parameters

        Returns:
            Hexadecimal signature string
        """
        # Build query string for signature
        query = urlencode(params) if params else ""

        # Signature string: METHOD + /v2/path + query
        auth = f"{method}{path}{query}"

        # Generate HMAC-SHA512
        if self.api_secret:
            signature = hmac.new(
                self.api_secret.encode(),
                auth.encode(),
                hashlib.sha512
            ).hexdigest()
            return signature
        return ""

    def _get_headers(self, method: str = "GET", path: str = "", params: Optional[Dict] = None) -> Dict[str, str]:
        """Generate authentication headers for Latoken API

        Args:
            method: HTTP method
            path: API endpoint path
            params: Request parameters

        Returns:
            Dictionary of headers including authentication
        """
        headers = self.default_headers.copy()

        if self.api_key:
            headers['X-LA-APIKEY'] = self.api_key

            if self.api_secret:
                signature = self._generate_signature(method, path, params)
                headers['X-LA-SIGNATURE'] = signature
                headers['X-LA-DIGEST'] = 'HMAC-SHA512'

        return headers

    def _get_ticker(self, symbol: str, extra_data: Optional[Dict] = None, **kwargs) -> tuple:
        """Get ticker information

        Args:
            symbol: Trading pair (e.g., 'BTC-USDT', 'btc_usdt')
            extra_data: Additional data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_ticker"

        # For individual ticker, use get_ticker_symbol path
        if "_" in request_symbol or "/" in symbol or "-" in symbol:
            base, quote = symbol.replace("/", "_").replace("-", "_").split("_")[:2]
            path = self._params.get_rest_path("get_ticker_symbol", base=base.lower(), quote=quote.lower())
        else:
            path = self._params.get_rest_path(request_type)

        params = {}

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

            data = [LatokenRequestTickerData(json.dumps(input_data), symbol_name, asset_type, True)]
        elif isinstance(input_data, list):
            # Handle list of tickers
            symbol_name = extra_data.get("symbol_name", "")
            asset_type = extra_data["asset_type"]
            data = [LatokenRequestTickerData(json.dumps(ticker), symbol_name, asset_type, True) for ticker in input_data]
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

        # Parse base and quote from symbol
        if "_" in request_symbol or "/" in symbol or "-" in symbol:
            base, quote = symbol.replace("/", "_").replace("-", "_").split("_")[:2]
            path = self._params.get_rest_path(request_type, currency=base.lower(), quote=quote.lower())
        else:
            path = self._params.get_rest_path(request_type, currency=request_symbol, quote="usdt")

        params = {}
        if limit:
            params['limit'] = limit

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

        # Latoken orderbook format varies, return raw data
        if isinstance(input_data, dict):
            status = True
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

        # Parse base and quote from symbol
        if "_" in request_symbol or "/" in symbol or "-" in symbol:
            base, quote = symbol.replace("/", "_").replace("-", "_").split("_")[:2]
            path = self._params.get_rest_path(request_type, currency=base.lower(), quote=quote.lower())
        else:
            path = self._params.get_rest_path(request_type, currency=request_symbol, quote="usdt")

        params = {}
        if limit:
            params['limit'] = limit

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
            period: Time period (1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w)
            limit: Number of candles
            extra_data: Additional data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_chart_week"

        # Parse base and quote from symbol
        if "_" in request_symbol or "/" in symbol or "-" in symbol:
            base, quote = symbol.replace("/", "_").replace("-", "_").split("_")[:2]
            path = self._params.get_rest_path(request_type, currency=base.lower(), quote=quote.lower())
        else:
            path = self._params.get_rest_path(request_type, currency=request_symbol, quote="usdt")

        params = {}

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

    def _get_server_time(self, extra_data: Optional[Dict] = None, **kwargs) -> tuple:
        """Get server time

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

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        """Normalize server time response"""
        status = input_data is not None

        if isinstance(input_data, dict):
            # Latoken returns {"timestamp": "..."}
            data = [input_data]
        else:
            data = []

        return data, status

    def get_server_time(self, extra_data: Optional[Dict] = None, **kwargs):
        """Get server time - public method"""
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    # Public methods for external use (already defined in Feed class)
    def get_ticker(self, symbol, extra_data=None, **kwargs):
        """Public method to get ticker data"""
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def get_order_book(self, symbol, limit=50, extra_data=None, **kwargs):
        """Public method to get order book data"""
        path, params, extra_data = self._get_order_book(symbol, limit, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def get_trades(self, symbol, limit=100, extra_data=None, **kwargs):
        """Public method to get recent trades"""
        path, params, extra_data = self._get_trades(symbol, limit, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def get_klines(self, symbol, period="1h", limit=100, extra_data=None, **kwargs):
        """Public method to get kline data"""
        path, params, extra_data = self._get_klines(symbol, period, limit, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    # Alias methods for consistency with other exchanges
    def get_depth(self, symbol, limit=50, extra_data=None, **kwargs):
        """Alias for get_order_book"""
        return self.get_order_book(symbol, limit, extra_data, **kwargs)

    def get_kline(self, symbol, period="1h", count=100, extra_data=None, **kwargs):
        """Alias for get_klines"""
        return self.get_klines(symbol, period, count, extra_data, **kwargs)
