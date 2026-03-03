import hashlib
import hmac
import json
import time
from typing import Dict, List, Optional, Union
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.localbitcoins_exchange_data import LocalBitcoinsExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.localbitcoins_ticker import LocalBitcoinsRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class LocalBitcoinsRequestData(Feed):
    """LocalBitcoins Request Data Handler

    Handles REST API requests to LocalBitcoins exchange with HMAC-SHA256 authentication.
    LocalBitcoins is a P2P cryptocurrency exchange focusing on fiat-to-BTC trading.
    """

    def __init__(self, data_queue, **kwargs):

        # Exchange configuration
        self.exchange_name = kwargs.get("exchange_name", "localbitcoins")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "localbitcoins_spot_feed.log")

        # Data queue for async requests
        self.data_queue = data_queue

        # Exchange data configuration
        self._params = LocalBitcoinsExchangeDataSpot()

        # Logger setup
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()

        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

        # Rate limiting
        self.rate_limiter = kwargs.get("rate_limiter", None)

        # API credentials (HMAC-SHA256)
        self.api_key = kwargs.get("api_key", None)
        self.api_secret = kwargs.get("api_secret", None)

        # API URLs
        self.rest_url = self._params.rest_url

        # Default headers
        self.default_headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

    def _generate_signature(self, nonce: int, method: str, url: str, params: Optional[Dict] = None, body: Optional[str] = None) -> str:
        """Generate HMAC-SHA256 signature for LocalBitcoins API

        Args:
            nonce: Nonce value (increasing number, typically timestamp)
            method: HTTP method (GET, POST, etc.)
            url: Full request URL
            params: Query parameters
            body: Request body

        Returns:
            Base64 encoded signature string
        """
        # Build signature string: nonce + api_key + url + query + body
        query_string = urlencode(params) if params else ""

        # Remove domain from URL for signature
        path = url.replace(self.rest_url, "")

        signature_string = f"{nonce}{self.api_key}{path}{query_string}{body or ''}"

        # Generate HMAC-SHA256
        signature = hmac.new(
            self.api_secret.encode(),
            signature_string.encode(),
            hashlib.sha256
        ).digest()

        # Return as base64
        import base64
        return base64.b64encode(signature).decode()

    def _get_headers(self, nonce: int, method: str = "GET", url: str = "", params: Optional[Dict] = None, body: Optional[str] = None) -> Dict[str, str]:
        """Generate authentication headers for LocalBitcoins API

        Args:
            nonce: Nonce value
            method: HTTP method
            url: Request URL
            params: Request parameters
            body: Request body

        Returns:
            Dictionary of headers including authentication
        """
        headers = self.default_headers.copy()

        if self.api_key and self.api_secret:
            signature = self._generate_signature(nonce, method, url, params, body)

            # Apiauth header format: api_key:nonce:signature
            auth_value = f"{self.api_key}:{nonce}:{signature}"
            headers['Apiauth'] = auth_value

        return headers

    @classmethod
    def _capabilities(cls) -> set:
        """Return the capabilities of this feed"""
        return {
            Capability.GET_TICK,
            Capability.GET_EXCHANGE_INFO,
        }

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for LocalBitcoins API."""
        method = "GET"  # LocalBitcoins public API is mostly GET
        headers = self.default_headers.copy()
        url = self.rest_url + path

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
            response = http_client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
            )
            return self._process_response(response, extra_data)
        except Exception as e:
            self.request_logger.error(f"Request failed: {e}")
            raise

    def _process_response(self, response, extra_data=None):
        """Process API response."""
        from bt_api_py.containers.requestdatas.request_data import RequestData
        return RequestData(response, extra_data)

    def push_data_to_queue(self, data):
        """Push data to the queue."""
        if self.data_queue is not None:
            self.data_queue.put(data)

    def connect(self):
        pass

    def disconnect(self):
        pass

    def is_connected(self):
        return True

    def _get_ticker(self, symbol: str, extra_data: Optional[Dict] = None, **kwargs) -> tuple:
        """Get ticker information

        Args:
            symbol: Trading pair (e.g., 'BTC-USD', 'btc_usd')
            extra_data: Additional data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_ticker"
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

            data = [LocalBitcoinsRequestTickerData(input_data, symbol_name, asset_type, True)]
        else:
            data = []

        return data, status

    def _get_ads(self, ad_id: str, extra_data: Optional[Dict] = None, **kwargs) -> tuple:
        """Get advertisement information

        Args:
            ad_id: Advertisement ID
            extra_data: Additional data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_ads"
        path = self._params.get_rest_path(request_type, id=ad_id)

        params = {}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "ad_id": ad_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_ads_normalize_function,
            }
        )

        return path, params, extra_data

    @staticmethod
    def _get_ads_normalize_function(input_data, extra_data):
        """Normalize ads response data"""
        status = input_data is not None

        if isinstance(input_data, dict):
            data = [input_data]
        else:
            data = []

        return data, status

    def _get_online_ads(self, currency: str = "USD", country_code: str = "all", extra_data: Optional[Dict] = None, **kwargs) -> tuple:
        """Get online advertisements

        Args:
            currency: Fiat currency code
            country_code: Country code (or 'all' for global)
            extra_data: Additional data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_online_ads"
        path = self._params.get_rest_path(request_type, currency=currency.lower(), country_code=country_code.lower())

        params = {}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "currency": currency,
                "country_code": country_code,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_ads_normalize_function,
            }
        )

        return path, params, extra_data

    def get_server_time(self, extra_data: Optional[Dict] = None, **kwargs) -> tuple:
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
            data = [input_data]
        else:
            data = []

        return data, status

    # Public methods for external use
    def get_ticker(self, symbol, extra_data=None, **kwargs):
        """Public method to get ticker data"""
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def get_ads(self, ad_id, extra_data=None, **kwargs):
        """Public method to get advertisement data"""
        path, params, extra_data = self._get_ads(ad_id, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def get_online_ads(self, currency="USD", country_code="all", extra_data=None, **kwargs):
        """Public method to get online advertisements"""
        path, params, extra_data = self._get_online_ads(currency, country_code, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)
