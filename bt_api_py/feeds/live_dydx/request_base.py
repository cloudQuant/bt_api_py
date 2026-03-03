"""
dYdX REST API request base class.
Handles authentication, signing, and all REST API methods.
dYdX is a decentralized exchange, so authentication uses wallet signatures.
"""

import json
import time
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.dydx_exchange_data import DydxExchangeDataSwap
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.error_framework import ErrorTranslator
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitScope, RateLimitType


class DydxErrorTranslator(ErrorTranslator):
    """dYdX error translator"""

    def translate(self, raw_response, exchange_name):
        """Translate dYdX error response to UnifiedError"""
        if isinstance(raw_response, dict):
            error_code = raw_response.get("code")
            error_message = raw_response.get("message", "")

            if error_code:
                error_class = self.get_error_class(error_code)
                error_text = f"{error_class}: {error_message}"
                return self.create_unified_error(
                    error_class=error_class,
                    error_code=error_code,
                    error_text=error_text,
                    raw_response=raw_response,
                    exchange_name=exchange_name,
                )
        return None


class DydxRequestData(Feed):
    """dYdX request base class for REST API calls"""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_FUNDING_RATE,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.GET_POSITION,
            Capability.QUERY_ORDER,
            Capability.QUERY_OPEN_ORDERS,
            Capability.GET_DEALS,
            Capability.GET_EXCHANGE_INFO,
            # Note: Trading capabilities may be limited due to decentralized nature
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.CANCEL_ALL,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "DYDX___SWAP")
        self.asset_type = kwargs.get("asset_type", "swap")
        self.logger_name = kwargs.get("logger_name", "dydx_swap_feed.log")
        self._params = DydxExchangeDataSwap()

        # Configuration
        self.api_key = kwargs.get("api_key")
        self.private_key = kwargs.get("private_key")
        self.mnemonic = kwargs.get("mnemonic")
        self.subaccount_number = kwargs.get("subaccount_number", 0)
        self.address = kwargs.get("address")

        # Testnet/mainnet selection
        if kwargs.get("testnet", False):
            self._params.rest_url = self._params.testnet_rest_url
            self._params.wss_url = self._params.testnet_wss_url

        # Logging
        from bt_api_py.functions.log_message import SpdLogManager
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

        # Error handling and rate limiting
        self._error_translator = DydxErrorTranslator()
        self._rate_limiter = kwargs.get("rate_limiter", self._create_default_rate_limiter())

    @staticmethod
    def _create_default_rate_limiter():
        rules = [
            RateLimitRule(
                name="dydx_indexer_get",
                limit=100,
                interval=10,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.ENDPOINT,
                endpoint="/v4/*",
                weight=1,
            ),
        ]
        return RateLimiter(rules)

    def translate_error(self, raw_response):
        """Translate raw dYdX response to UnifiedError"""
        return self._error_translator.translate(raw_response, self.exchange_name)

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            assert 0, "队列未初始化"

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request function"""
        if params is None:
            params = {}

        method, path = path.split(" ", 1)
        req = urlencode(params) if params else ""
        url = f"{self._params.rest_url}{path}"
        if req:
            url += f"?{req}"

        # dYdX Indexer API doesn't require authentication for public endpoints
        # For private endpoints, authentication would need to be implemented
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "bt_api_py/1.0"
        }

        res = self.http_request(method, url, headers, body, timeout)
        return RequestData(res, extra_data)

    async def async_request(
        self, path, params=None, body=None, extra_data=None, timeout=5
    ) -> RequestData:
        """Async HTTP request function"""
        if params is None:
            params = {}

        method, path = path.split(" ", 1)
        req = urlencode(params) if params else ""
        url = f"{self._params.rest_url}{path}"
        if req:
            url += f"?{req}"

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "bt_api_py/1.0"
        }

        res = await self.async_http_request(method, url, headers, body, timeout)
        return RequestData(res, extra_data)

    def async_callback(self, future):
        """Callback function for async requests"""
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.warn(f"async_callback::{e}")

    # Market Data Methods

    def get_perpetual_markets(self, extra_data=None, **kwargs):
        """Get perpetual markets information"""
        request_type = "get_perpetual_markets"
        path = self._params.get_rest_path(request_type)

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=self._get_perpetual_markets_normalize_function,
        )

        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_perpetual_markets_normalize_function(input_data, extra_data):
        """Normalize perpetual markets response"""
        status = True
        if "markets" in input_data:
            data = list(input_data["markets"].items())
            return data, status
        return None, False

    def get_orderbook(self, symbol, extra_data=None, **kwargs):
        """Get orderbook for a symbol"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_orderbook"
        path = self._params.get_rest_path(request_type).replace("<symbol>", request_symbol)

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=request_symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=self._get_orderbook_normalize_function,
        )

        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_orderbook_normalize_function(input_data, extra_data):
        """Normalize orderbook response"""
        status = True
        data = {
            "symbol": extra_data.get("symbol_name"),
            "bids": input_data.get("bids", []),
            "asks": input_data.get("asks", []),
            "timestamp": input_data.get("time"),
        }
        return data, status

    def get_trades(self, symbol, limit=100, extra_data=None, **kwargs):
        """Get recent trades for a symbol"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_trades"
        path = self._params.get_rest_path(request_type).replace("<symbol>", request_symbol)

        params = {"limit": limit}

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=request_symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=self._get_trades_normalize_function,
        )

        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize trades response"""
        status = True
        trades = input_data.get("trades", [])
        data = {
            "symbol": extra_data.get("symbol_name"),
            "trades": trades,
            "count": len(trades),
        }
        return data, status

    def get_candles(self, symbol, resolution="1HOUR", limit=100, extra_data=None, **kwargs):
        """Get candle data for a symbol"""
        request_symbol = self._params.get_symbol(symbol)
        period = self._params.get_period(resolution)
        request_type = "get_candles"
        path = self._params.get_rest_path(request_type).replace("<symbol>", request_symbol)

        params = {
            "resolution": period,
            "limit": limit,
        }

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=request_symbol,
            period=resolution,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=self._get_candles_normalize_function,
        )

        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_candles_normalize_function(input_data, extra_data):
        """Normalize candles response"""
        status = True
        candles = input_data.get("candles", [])
        data = {
            "symbol": extra_data.get("symbol_name"),
            "period": extra_data.get("period"),
            "candles": candles,
            "count": len(candles),
        }
        return data, status

    def get_historical_funding(self, symbol, limit=100, extra_data=None, **kwargs):
        """Get historical funding rates for a symbol"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_historical_funding"
        path = self._params.get_rest_path(request_type).replace("<symbol>", request_symbol)

        params = {"limit": limit}

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=request_symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=self._get_historical_funding_normalize_function,
        )

        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_historical_funding_normalize_function(input_data, extra_data):
        """Normalize historical funding response"""
        status = True
        data = {
            "symbol": extra_data.get("symbol_name"),
            "historical_funding": input_data.get("historicalFunding", []),
        }
        return data, status

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker information for a symbol"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_perpetual_markets"
        path = self._params.get_rest_path(request_type)

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=request_symbol,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=self._get_ticker_normalize_function,
        )

        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_ticker_normalize_function(input_data, extra_data):
        """Normalize ticker response"""
        status = True
        symbol_name = extra_data.get("symbol_name")
        data = input_data.get("markets", {}).get(symbol_name, {})
        return data, status

    # Account Data Methods (require authentication)

    def get_subaccount(self, address, subaccount_number=0, extra_data=None, **kwargs):
        """Get subaccount information"""
        request_type = "get_subaccount"
        path = self._params.get_rest_path(request_type)
        path = path.replace("<address>", address).replace("<subaccount_number>", str(subaccount_number))

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=self._get_subaccount_normalize_function,
        )

        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_subaccount_normalize_function(input_data, extra_data):
        """Normalize subaccount response"""
        status = True
        data = input_data.get("subaccount", {})
        return data, status

    def get_orders(self, address=None, subaccount_number=None, market=None, status=None,
                   limit=50, extra_data=None, **kwargs):
        """Get orders (requires address and subaccount_number for authenticated requests)"""
        request_type = "get_orders"
        path = self._params.get_rest_path(request_type)

        params = {}
        if address:
            params["address"] = address
        if subaccount_number is not None:
            params["subaccountNumber"] = subaccount_number
        if market:
            params["market"] = market
        if status:
            params["status"] = status
        if limit:
            params["limit"] = limit

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=self._get_orders_normalize_function,
        )

        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_orders_normalize_function(input_data, extra_data):
        """Normalize orders response"""
        status = True
        orders = input_data if isinstance(input_data, list) else []
        data = {
            "orders": orders,
            "count": len(orders),
        }
        return data, status

    def get_fills(self, address=None, subaccount_number=None, market=None, limit=100,
                  extra_data=None, **kwargs):
        """Get fills/trades"""
        request_type = "get_fills"
        path = self._params.get_rest_path(request_type)

        params = {}
        if address:
            params["address"] = address
        if subaccount_number is not None:
            params["subaccountNumber"] = subaccount_number
        if market:
            params["market"] = market
        if limit:
            params["limit"] = limit

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=self._get_fills_normalize_function,
        )

        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_fills_normalize_function(input_data, extra_data):
        """Normalize fills response"""
        status = True
        fills = input_data if isinstance(input_data, list) else []
        data = {
            "fills": fills,
            "count": len(fills),
        }
        return data, status

    def get_funding_payments(self, address=None, subaccount_number=None, market=None,
                           limit=100, extra_data=None, **kwargs):
        """Get funding payments"""
        request_type = "get_funding_payments"
        path = self._params.get_rest_path(request_type)

        params = {}
        if address:
            params["address"] = address
        if subaccount_number is not None:
            params["subaccountNumber"] = subaccount_number
        if market:
            params["market"] = market
        if limit:
            params["limit"] = limit

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=self._get_funding_payments_normalize_function,
        )

        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_funding_payments_normalize_function(input_data, extra_data):
        """Normalize funding payments response"""
        status = True
        data = {
            "funding_payments": input_data if isinstance(input_data, list) else [],
        }
        return data, status

    def get_historical_pnl(self, address=None, subaccount_number=None, market=None,
                          limit=100, extra_data=None, **kwargs):
        """Get historical PnL"""
        request_type = "get_historical_pnl"
        path = self._params.get_rest_path(request_type)

        params = {}
        if address:
            params["address"] = address
        if subaccount_number is not None:
            params["subaccountNumber"] = subaccount_number
        if market:
            params["market"] = market
        if limit:
            params["limit"] = limit

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=self._get_historical_pnl_normalize_function,
        )

        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_historical_pnl_normalize_function(input_data, extra_data):
        """Normalize historical PnL response"""
        status = True
        data = {
            "historical_pnl": input_data if isinstance(input_data, list) else [],
        }
        return data, status

    # Trading Methods (require wallet signatures)
    # Note: These are placeholders for future implementation
    # Actual trading would require wallet signing integration

    def place_order(self, **kwargs):
        """Place an order (requires wallet signature)"""
        # This would need to integrate with v4-client-py for signing
        raise NotImplementedError("Trading requires wallet signature integration")

    def cancel_order(self, order_id, **kwargs):
        """Cancel an order (requires wallet signature)"""
        raise NotImplementedError("Trading requires wallet signature integration")

    def get_order(self, order_id, **kwargs):
        """Get order details (requires authentication)"""
        request_type = "get_orders"
        path = self._params.get_rest_path(request_type)

        params = {"orderId": order_id}

        extra_data = self._update_extra_data(
            None,
            request_type="get_order",
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=self._get_order_normalize_function,
        )

        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_order_normalize_function(input_data, extra_data):
        """Normalize single order response"""
        status = True
        # dYdX returns a list of orders, we need to filter by order ID
        if isinstance(input_data, list):
            for order in input_data:
                if order.get("id") == extra_data.get("order_id"):
                    return order, status
        return None, False

    @staticmethod
    def _update_extra_data(extra_data, **kwargs):
        """Update extra_data with additional information"""
        if extra_data is None:
            extra_data = {}
        extra_data.update(kwargs)
        return extra_data