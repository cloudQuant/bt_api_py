import base64
import hashlib
import hmac
import json
import time
from typing import Dict, List, Optional, Union

from bt_api_py.containers.accounts.bitfinex_account import BitfinexSpotRequestAccountData
from bt_api_py.containers.balances.bitfinex_balance import BitfinexSpotRequestBalanceData
from bt_api_py.containers.bars.bitfinex_bar import BitfinexRequestBarData
from bt_api_py.containers.exchanges.bitfinex_exchange_data import BitfinexExchangeDataSpot
from bt_api_py.containers.fundingrates.bitfinex_funding_rate import (
    BitfinexRequestFundingRateData,
)
from bt_api_py.containers.orderbooks.bitfinex_orderbook import BitfinexRequestOrderBookData
from bt_api_py.containers.orders.bitfinex_order import BitfinexRequestOrderData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.bitfinex_ticker import BitfinexRequestTickerData
from bt_api_py.containers.trades.bitfinex_trade import BitfinexRequestTradeData
from bt_api_py.error_framework import BitfinexErrorTranslator
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.calculate_time import datetime2timestamp
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class BitfinexRequestData(RequestData):
    """Bitfinex Request Data Handler

    Handles REST API requests to Bitfinex exchange with HMAC SHA384 authentication.
    """

    def __init__(self, data_queue, **kwargs):
        # Initialize RequestData with appropriate parameters
        exchange_name = kwargs.get("exchange_name", "bitfinex")
        extra_data = {
            "exchange_name": exchange_name,
            "asset_type": kwargs.get("asset_type", "SPOT"),
            "logger_name": kwargs.get("logger_name", "bitfinex_spot_feed.log"),
        }
        super().__init__(None, extra_data)

        # Exchange configuration
        self.exchange_name = kwargs.get("exchange_name", "bitfinex")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "bitfinex_spot_feed.log")

        # Exchange data configuration
        self._params = BitfinexExchangeDataSpot()

        # Logger setup
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()

        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

        # Error translator
        self.error_translator = BitfinexErrorTranslator()

        # Rate limiting
        self.rate_limiter = kwargs.get("rate_limiter", None)

        # General logger (alias to request_logger for compatibility)
        self.logger = self.request_logger

        # API credentials
        self.api_key = kwargs.get("api_key", None)
        self.api_secret = kwargs.get("api_secret", None)

        # API URLs
        self.rest_url = self._params.get_rest_path("base_url")

        # Default headers
        self.default_headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

    def _generate_signature(self, path: str, nonce: str, body: str = "") -> str:
        """Generate HMAC SHA384 signature for Bitfinex API

        Args:
            path: API endpoint path (e.g., '/v2/auth/r/wallets')
            nonce: Request nonce (timestamp in microseconds)
            body: Request body JSON string

        Returns:
            Hexadecimal signature string
        """
        signature_payload = f'/api{path}{nonce}{body}'
        signature = hmac.new(
            self.api_secret.encode(),
            signature_payload.encode(),
            hashlib.sha384
        ).hexdigest()
        return signature

    def _get_headers(self, path: str, body: str = "") -> Dict[str, str]:
        """Generate authentication headers for Bitfinex API

        Args:
            path: API endpoint path
            body: Request body JSON string

        Returns:
            Dictionary of headers including authentication
        """
        nonce = str(int(time.time() * 1000000))  # Current time in microseconds
        signature = self._generate_signature(path, nonce, body)

        headers = self.default_headers.copy()
        headers.update({
            'bfx-apikey': self.api_key,
            'bfx-signature': signature,
            'bfx-nonce': nonce
        })

        return headers

    def _make_request(self, method: str, path: str, params: Optional[Dict] = None) -> Union[Dict, List]:
        """Make HTTP request to Bitfinex API

        Args:
            method: HTTP method ('GET', 'POST', 'DELETE', etc.)
            path: API endpoint path
            params: Request parameters

        Returns:
            Response data as JSON
        """
        url = f"{self.rest_url}{path}"

        # Apply rate limiting if configured
        if self.rate_limiter:
            self.rate_limiter.wait_if_needed()

        # Prepare request
        if method.upper() == 'GET':
            response = self._session.request(
                method, url, params=params, headers=self.default_headers
            )
        else:
            body = json.dumps(params) if params else ""
            headers = self._get_headers(path, body)
            response = self._session.request(
                method, url, data=body, headers=headers
            )

        # Handle response
        if response.status_code == 429:
            # Rate limit exceeded
            retry_after = int(response.headers.get('Retry-After', 1))
            time.sleep(retry_after)
            return self._make_request(method, path, params)

        response.raise_for_status()

        try:
            return response.json()
        except json.JSONDecodeError:
            return response.text

    def _make_order(
        self,
        symbol: str,
        vol: str,
        price: Optional[float] = None,
        order_type: str = "buy-limit",
        offset: str = "open",
        post_only: bool = False,
        client_order_id: Optional[str] = None,
        extra_data: Optional[Dict] = None,
        **kwargs
    ) -> tuple:
        """Create an order

        Args:
            symbol: Trading pair (e.g., 'tBTCUSD')
            vol: Order volume
            price: Order price (for limit orders)
            order_type: Order type ('buy-limit', 'sell-limit', 'buy-market', 'sell-market')
            offset: Order offset ('open' or 'close')
            post_only: Whether to make order post-only
            client_order_id: Custom order ID
            extra_data: Additional data for processing
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)

        # Parse order type
        side, order_type_subtype = order_type.split("-")

        # Prepare parameters
        params = {
            'type': order_type_subtype.upper(),
            'symbol': request_symbol,
            'amount': str(vol),
            'flags': 64 if post_only else 0  # 64 = hidden order
        }

        # Add price for limit orders
        if price is not None and order_type_subtype != 'market':
            params['price'] = str(price)

        # Add client order ID if provided
        if client_order_id is not None:
            params['cid'] = int(client_order_id)

        # Add leverage for derivative orders
        if 'lev' in kwargs:
            params['lev'] = kwargs['lev']

        # Prepare extra data
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "post_only": post_only,
                "normalize_function": self._make_order_normalize_function,
            }
        )

        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        """Normalize order response data"""
        status = input_data is not None

        if isinstance(input_data, list):
            data = [
                BitfinexRequestOrderData(i, extra_data["symbol_name"], extra_data["asset_type"], True)
                for i in input_data
            ]
        elif isinstance(input_data, dict):
            data = [BitfinexRequestOrderData(input_data, extra_data["symbol_name"], extra_data["asset_type"], True)]
        else:
            data = []

        return data, status

    def _get_ticker(self, symbol: str, extra_data: Optional[Dict] = None, **kwargs) -> tuple:
        """Get ticker information

        Args:
            symbol: Trading pair
            extra_data: Additional data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "ticker"
        # Bitfinex ticker endpoint includes symbol in path: /ticker/tBTCUSD
        path = self._params.get_rest_path(request_type, symbol=request_symbol)

        params = {}  # No params needed, symbol is in path

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

        if isinstance(input_data, list) and len(input_data) >= 8:
            ticker = input_data
            symbol_name = extra_data["symbol_name"]
            asset_type = extra_data["asset_type"]

            # Extract ticker data
            # Format: [SYMBOL, BID, BID_SIZE, ASK, ASK_SIZE, DAILY_CHANGE, DAILY_CHANGE_PERC, LAST_PRICE, VOLUME, HIGH, LOW]
            ticker_data = {
                "symbol": ticker[0],
                "bid_price": ticker[1],
                "bid_size": ticker[2],
                "ask_price": ticker[3],
                "ask_size": ticker[4],
                "daily_change": ticker[5],
                "daily_change_perc": ticker[6],
                "last_price": ticker[7],
                "volume": ticker[8] if len(ticker) > 8 else None,
                "high": ticker[9] if len(ticker) > 9 else None,
                "low": ticker[10] if len(ticker) > 10 else None,
                "timestamp": time.time()
            }

            data = [BitfinexRequestTickerData(ticker_data, symbol_name, asset_type, True)]
        else:
            data = []

        return data, status

    def _get_order_book(self, symbol: str, precision: str = "P0", length: str = "25", extra_data: Optional[Dict] = None, **kwargs) -> tuple:
        """Get order book data

        Args:
            symbol: Trading pair
            precision: Price precision ('P0', 'P1', 'P2', 'P3', 'R0')
            length: Number of price levels ('1', '25', '100')
            extra_data: Additional data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "orderbook"
        # Bitfinex orderbook endpoint: /book/tBTCUSD/P0
        path = self._params.get_rest_path(request_type, symbol=request_symbol, precision=precision)

        params = {'len': length}

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

        if isinstance(input_data, list):
            symbol_name = extra_data["symbol_name"]
            asset_type = extra_data["asset_type"]

            # Bitfinex orderbook format: [[PRICE, COUNT, AMOUNT], ...]
            orderbook_data = []
            for level in input_data:
                if len(level) >= 3:
                    orderbook_data.append({
                        "price": level[0],
                        "count": level[1],
                        "amount": level[2]
                    })

            data = [BitfinexRequestOrderBookData(orderbook_data, symbol_name, asset_type, True)]
        else:
            data = []

        return data, status

    def _get_account_balance(self, extra_data: Optional[Dict] = None, **kwargs) -> tuple:
        """Get account balance information

        Args:
            extra_data: Additional data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "account_balance"
        path = self._params.get_rest_path(request_type)

        params = {}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_account_balance_normalize_function,
            }
        )

        return path, params, extra_data

    @staticmethod
    def _get_account_balance_normalize_function(input_data, extra_data):
        """Normalize account balance response data"""
        status = input_data is not None

        if isinstance(input_data, list):
            asset_type = extra_data["asset_type"]

            # Bitfinex wallet format: [WALLET_TYPE, CURRENCY, BALANCE, UNSETTLED_INTEREST, BALANCE_AVAILABLE]
            balance_data = []
            for wallet in input_data:
                if len(wallet) >= 5:
                    balance_data.append({
                        "wallet_type": wallet[0],
                        "currency": wallet[1],
                        "balance": wallet[2],
                        "unsettled_interest": wallet[3],
                        "balance_available": wallet[4]
                    })

            data = [BitfinexSpotRequestBalanceData(balance_data, asset_type, True)]
        else:
            data = []

        return data, status

    # Implement other required methods from RequestData base class
    def _get_klines(self, symbol, period, limit, extra_data=None, **kwargs):
        """Get kline/candlestick data"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "klines"
        # Bitfinex klines endpoint: /candles/trade:1m:tBTCUSD/hist
        path = self._params.get_rest_path(request_type, symbol=request_symbol, timeframe=period)

        params = {'limit': limit}

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
            symbol_name = extra_data["symbol_name"]
            asset_type = extra_data["asset_type"]

            # Bitfinex kline format: [MTS, OPEN, CLOSE, HIGH, LOW, VOLUME]
            kline_data = []
            for kline in input_data:
                if len(kline) >= 6:
                    kline_data.append({
                        "timestamp": kline[0],
                        "open": kline[1],
                        "close": kline[2],
                        "high": kline[3],
                        "low": kline[4],
                        "volume": kline[5]
                    })

            data = [BitfinexRequestBarData(kline_data, symbol_name, asset_type, True)]
        else:
            data = []

        return data, status

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders"""
        request_type = "open_orders"
        path = self._params.get_rest_path(request_type)

        params = {}
        if symbol:
            request_symbol = self._params.get_symbol(symbol)
            path = f"{path}/{request_symbol}"

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_open_orders_normalize_function,
            }
        )

        return path, params, extra_data

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        """Normalize open orders response data"""
        status = input_data is not None

        if isinstance(input_data, list):
            symbol_name = extra_data.get("symbol_name")
            asset_type = extra_data["asset_type"]

            order_data = []
            for order in input_data:
                if len(order) >= 16:  # Ensure we have enough fields
                    order_data.append({
                        "id": order[0],
                        "gid": order[1],
                        "cid": order[2],
                        "symbol": order[3],
                        "mts_create": order[4],
                        "mts_update": order[5],
                        "amount": order[6],
                        "amount_orig": order[7],
                        "type": order[8],
                        "type_prev": order[9],
                        "flags": order[10],
                        "status": order[11],
                        "price": order[12],
                        "price_avg": order[13],
                        "price_trail": order[14],
                        "aux_price": order[15],
                        # Additional fields can be added as needed
                    })

            data = [BitfinexRequestOrderData(order_data, symbol_name, asset_type, True)]
        else:
            data = []

        return data, status

    def _cancel_order(self, order_id, extra_data=None, **kwargs):
        """Cancel an order"""
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)

        params = {'id': order_id}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "order_id": order_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._cancel_order_normalize_function,
            }
        )

        return path, params, extra_data

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        """Normalize cancel order response data"""
        status = input_data is not None

        if isinstance(input_data, list) and len(input_data) > 0:
            order_id = extra_data["order_id"]
            asset_type = extra_data["asset_type"]

            # Bitfinex cancel response format: [ID, ...]
            cancel_data = {
                "id": input_data[0],
                "status": "SUCCESS"
            }

            data = [BitfinexRequestOrderData(cancel_data, f"order_{order_id}", asset_type, True)]
        else:
            data = []

        return data, status

    def _request(self, path, params=None, extra_data=None):
        """Internal request method"""
        return self.request(path, method="GET", params=params, extra_data=extra_data)

    def request(self, path, method="POST", params=None, data=None, extra_data=None):
        """Make authenticated request to Bitfinex API"""
        import requests

        url = f"{self.rest_url}{path}"

        # Prepare headers
        headers = self.default_headers.copy()

        # Add authentication for private endpoints
        if path.startswith('/v2/auth/'):
            nonce = str(int(time.time() * 1000000))  # Microsecond precision
            body = json.dumps(data) if data else ""
            signature = self._generate_signature(path, nonce, body)

            headers.update({
                'bfx-apikey': self.api_key,
                'bfx-nonce': nonce,
                'bfx-signature': signature
            })

        # Make the request
        try:
            if method.upper() == "GET":
                response = requests.get(url, headers=headers, params=params, timeout=30)
            elif method.upper() == "POST":
                response = requests.post(url, headers=headers, json=data, params=params, timeout=30)
            elif method.upper() == "DELETE":
                response = requests.delete(url, headers=headers, json=data, params=params, timeout=30)
            else:
                raise ValueError(f"Unsupported method: {method}")

            # Check response status
            response.raise_for_status()

            # Parse JSON response
            json_response = response.json()

            # Apply normalization if provided
            if extra_data and "normalize_function" in extra_data:
                normalized_data, status = extra_data["normalize_function"](json_response, extra_data)
                # Remove normalize_function to prevent RequestData.init_data() from re-normalizing
                extra_data = {k: v for k, v in extra_data.items() if k != "normalize_function"}
            else:
                normalized_data = [json_response]
                status = True

            return RequestData(normalized_data, extra_data or {}, status)

        except requests.exceptions.HTTPError as e:
            self.logger.error(f"HTTP error: {e}")
            return RequestData(None, extra_data or {}, False)
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Request failed: {e}")
            return RequestData(None, extra_data or {}, False)
        except json.JSONDecodeError as e:
            self.logger.error(f"JSON decode error: {e}")
            raise ValueError(f"Failed to decode response: {e}")

    # Additional methods can be implemented as needed
    def _get_trade_history(self, symbol, limit, extra_data=None, **kwargs):
        """Get trade history"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "trade_history"
        path = self._params.get_rest_path(request_type, symbol=request_symbol)

        params = {'limit': limit, 'sort': -1}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_trade_history_normalize_function,
            }
        )

        return path, params, extra_data

    @staticmethod
    def _get_trade_history_normalize_function(input_data, extra_data):
        """Normalize trade history response data"""
        status = input_data is not None

        if isinstance(input_data, list):
            symbol_name = extra_data["symbol_name"]
            asset_type = extra_data["asset_type"]

            # Bitfinex trade format: [ID, MTS, AMOUNT, PRICE]
            trade_data = []
            for trade in input_data:
                if len(trade) >= 4:
                    trade_data.append({
                        "id": trade[0],
                        "timestamp": trade[1],
                        "amount": trade[2],
                        "price": trade[3]
                    })

            data = [BitfinexRequestTradeData(trade_data, symbol_name, asset_type, True)]
        else:
            data = []

        return data, status
