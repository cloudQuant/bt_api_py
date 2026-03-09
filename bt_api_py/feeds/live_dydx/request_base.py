"""
dYdX REST API request base class.
Handles authentication, signing, and all REST API methods.
dYdX is a decentralized exchange, so authentication uses wallet signatures.
"""

from urllib.parse import urlencode

from bt_api_py.containers.exchanges.dydx_exchange_data import DydxExchangeDataSwap
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.error import ErrorTranslator
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitScope, RateLimitType


class DydxErrorTranslator(ErrorTranslator):
    """dYdX error translator"""

    def translate(self, raw_response: Any, exchange_name: Any) -> None:
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
    def _capabilities(cls: Any) -> None:
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

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
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
        self.request_logger = get_logger("dydx_swap_feed")
        self.async_logger = get_logger("dydx_swap_feed")

        # Error handling and rate limiting
        self._error_translator = DydxErrorTranslator()
        self._rate_limiter = kwargs.get("rate_limiter", self._create_default_rate_limiter())

    @staticmethod
    def _create_default_rate_limiter() -> None:
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

    def translate_error(self, raw_response: Any) -> None:
        """Translate raw dYdX response to UnifiedError"""
        return self._error_translator.translate(raw_response, self.exchange_name)

    def push_data_to_queue(self, data: Any) -> None:
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            assert 0, "队列未初始化"

    def request(
        self,
        path: Any,
        params: Any = None,
        body: Any = None,
        extra_data: Any = None,
        timeout: Any = 10,
    ) -> None:
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
        headers = {"Content-Type": "application/json", "User-Agent": "bt_api_py/1.0"}

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

        headers = {"Content-Type": "application/json", "User-Agent": "bt_api_py/1.0"}

        res = await self.async_http_request(method, url, headers, body, timeout)
        return RequestData(res, extra_data)

    def async_callback(self, future: Any) -> None:
        """Callback function for async requests"""
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.warn(f"async_callback::{e}")

    # Market Data Methods

    def get_perpetual_markets(self, extra_data: Any = None, **kwargs: Any) -> None:
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
    def _get_perpetual_markets_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize perpetual markets response"""
        status = True
        if "markets" in input_data:
            data = list(input_data["markets"].items())
            return data, status
        return None, False

    def get_orderbook(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> None:
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
    def _get_orderbook_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize orderbook response"""
        status = True
        data = {
            "symbol": extra_data.get("symbol_name"),
            "bids": input_data.get("bids", []),
            "asks": input_data.get("asks", []),
            "timestamp": input_data.get("time"),
        }
        return data, status

    def get_trades(
        self, symbol: Any, limit: Any = 100, extra_data: Any = None, **kwargs: Any
    ) -> None:
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
    def _get_trades_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize trades response"""
        status = True
        trades = input_data.get("trades", [])
        data = {
            "symbol": extra_data.get("symbol_name"),
            "trades": trades,
            "count": len(trades),
        }
        return data, status

    def get_candles(
        self,
        symbol: Any,
        resolution: Any = "1HOUR",
        limit: Any = 100,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
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
    def _get_candles_normalize_function(input_data: Any, extra_data: Any) -> None:
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

    def get_historical_funding(
        self, symbol: Any, limit: Any = 100, extra_data: Any = None, **kwargs: Any
    ) -> None:
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
    def _get_historical_funding_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize historical funding response"""
        status = True
        data = {
            "symbol": extra_data.get("symbol_name"),
            "historical_funding": input_data.get("historicalFunding", []),
        }
        return data, status

    def get_ticker(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> None:
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
    def _get_ticker_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize ticker response"""
        status = True
        symbol_name = extra_data.get("symbol_name")
        data = input_data.get("markets", {}).get(symbol_name, {})
        return data, status

    # ── Standard Interface: get_tick ──────────────────────────────

    def _get_tick(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> None:
        """Prepare tick request parameters. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        request_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_perpetual_markets")
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": request_symbol,
                "asset_type": self.asset_type,
                "request_type": "get_tick",
                "normalize_function": self._get_ticker_normalize_function,
            }
        )
        return path, {}, extra_data

    def get_tick(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> None:
        """Get latest tick price for symbol. Returns RequestData."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_tick(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> None:
        """Async get tick price for symbol."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ── Standard Interface: get_depth ────────────────────────────

    def _get_depth(
        self, symbol: Any, count: Any = 20, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Prepare depth request parameters. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        request_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_orderbook")
        path = path.replace("<placeholder>", request_symbol)
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": request_symbol,
                "asset_type": self.asset_type,
                "request_type": "get_depth",
                "normalize_function": self._get_orderbook_normalize_function,
            }
        )
        return path, {}, extra_data

    def get_depth(
        self, symbol: Any, count: Any = 20, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Get order book depth for symbol. Returns RequestData."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(
        self, symbol: Any, count: Any = 20, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get depth for symbol."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ── Standard Interface: get_kline ────────────────────────────

    def _get_kline(
        self, symbol: Any, period: Any, count: Any = 20, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Prepare kline request parameters. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        request_symbol = self._params.get_symbol(symbol)
        exchange_period = self._params.get_period(period)
        path = self._params.get_rest_path("get_candles")
        path = path.replace("<placeholder>", request_symbol)
        params = {"resolution": exchange_period, "limit": count}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": request_symbol,
                "asset_type": self.asset_type,
                "request_type": "get_kline",
                "period": period,
                "normalize_function": self._get_candles_normalize_function,
            }
        )
        return path, params, extra_data

    def get_kline(
        self, symbol: Any, period: Any, count: Any = 20, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Get kline/candle data for symbol. Returns RequestData."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_kline(
        self, symbol: Any, period: Any, count: Any = 20, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get kline for symbol."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ── Standard Interface: get_exchange_info ─────────────────────

    def _get_exchange_info(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Prepare exchange info request. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        path = self._params.get_rest_path("get_exchange_info")
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": "get_exchange_info",
            }
        )
        return path, {}, extra_data

    def get_exchange_info(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Get exchange metadata. Returns RequestData."""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ── Standard Interface: get_server_time ───────────────────────

    def _get_server_time(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Prepare server time request. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        path = self._params.get_rest_path("get_time")
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": "get_server_time",
            }
        )
        return path, {}, extra_data

    def get_server_time(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Get server time. Returns RequestData."""
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # Account Data Methods (require authentication)

    def get_subaccount(
        self, address: Any, subaccount_number: Any = 0, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Get subaccount information"""
        request_type = "get_subaccount"
        path = self._params.get_rest_path(request_type)
        path = path.replace("<address>", address).replace(
            "<subaccount_number>", str(subaccount_number)
        )

        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            exchange_name=self.exchange_name,
            asset_type=self.asset_type,
            normalize_function=self._get_subaccount_normalize_function,
        )

        return self.request(path, extra_data=extra_data)

    @staticmethod
    def _get_subaccount_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize subaccount response"""
        status = True
        data = input_data.get("subaccount", {})
        return data, status

    def get_orders(
        self,
        address: Any = None,
        subaccount_number: Any = None,
        market: Any = None,
        status: Any = None,
        limit: Any = 50,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
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
    def _get_orders_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize orders response"""
        status = True
        orders = input_data if isinstance(input_data, list) else []
        data = {
            "orders": orders,
            "count": len(orders),
        }
        return data, status

    def get_fills(
        self,
        address: Any = None,
        subaccount_number: Any = None,
        market: Any = None,
        limit: Any = 100,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
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
    def _get_fills_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize fills response"""
        status = True
        fills = input_data if isinstance(input_data, list) else []
        data = {
            "fills": fills,
            "count": len(fills),
        }
        return data, status

    def get_funding_payments(
        self,
        address: Any = None,
        subaccount_number: Any = None,
        market: Any = None,
        limit: Any = 100,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
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
    def _get_funding_payments_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize funding payments response"""
        status = True
        data = {
            "funding_payments": input_data if isinstance(input_data, list) else [],
        }
        return data, status

    def get_historical_pnl(
        self,
        address: Any = None,
        subaccount_number: Any = None,
        market: Any = None,
        limit: Any = 100,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
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
    def _get_historical_pnl_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize historical PnL response"""
        status = True
        data = {
            "historical_pnl": input_data if isinstance(input_data, list) else [],
        }
        return data, status

    # Trading Methods (require wallet signatures)
    # Note: These are placeholders for future implementation
    # Actual trading would require wallet signing integration

    def place_order(self, **kwargs: Any) -> None:
        """Place an order (requires wallet signature)"""
        # This would need to integrate with v4-client-py for signing
        raise NotImplementedError("Trading requires wallet signature integration")

    def cancel_order(self, order_id: Any, **kwargs: Any) -> None:
        """Cancel an order (requires wallet signature)"""
        raise NotImplementedError("Trading requires wallet signature integration")

    def get_order(self, order_id: Any, **kwargs: Any) -> None:
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
    def _get_order_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize single order response"""
        status = True
        # dYdX returns a list of orders, we need to filter by order ID
        if isinstance(input_data, list):
            for order in input_data:
                if order.get("id") == extra_data.get("order_id"):
                    return order, status
        return None, False

    @staticmethod
    def _update_extra_data(extra_data: Any, **kwargs: Any) -> None:
        """Update extra_data with additional information"""
        if extra_data is None:
            extra_data = {}
        extra_data.update(kwargs)
        return extra_data
