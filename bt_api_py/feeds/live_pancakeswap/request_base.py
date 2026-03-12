"""PancakeSwap REST API request base class.
Handles GraphQL queries and REST API calls to PancakeSwap.
"""

import time
from typing import Any

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.errors.error_framework_pancakeswap_error_translator import PancakeSwapErrorTranslator
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.logging_factory import get_logger
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitScope, RateLimitType


class PancakeSwapRequestData(Feed):
    """Base class for PancakeSwap API requests.

    Handles GraphQL queries and REST API calls to PancakeSwap.
    """

    @classmethod
    def _capabilities(cls) -> set[Capability]:
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

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "PANCAKESWAP___DEX")
        self.api_key = kwargs.get("api_key")
        self.api_secret = kwargs.get("api_secret")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "pancakeswap_feed.log")
        self._params = kwargs.get("exchange_data")
        self.request_logger = get_logger("pancakeswap_feed")
        self.async_logger = get_logger("pancakeswap_feed")
        self._error_translator = PancakeSwapErrorTranslator()
        self._rate_limiter = kwargs.get("rate_limiter", self._create_default_rate_limiter())
        self._http_client = HttpClient(venue=self.exchange_name, timeout=30)

    @staticmethod
    def _create_default_rate_limiter():
        """Create default rate limiter for PancakeSwap API."""
        rules = [
            RateLimitRule(
                name="public_api",
                limit=60,
                interval=60,
                scope=RateLimitScope.GLOBAL,
                type=RateLimitType.TOKEN_BUCKET,
            ),
            RateLimitRule(
                name="private_api",
                limit=30,
                interval=60,
                scope=RateLimitScope.GLOBAL,
                type=RateLimitType.TOKEN_BUCKET,
            ),
        ]
        return RateLimiter(rules)

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: dict | None = None,
        params: dict | None = None,
        is_graphql: bool = False,
    ) -> dict:
        """Make request to PancakeSwap API.

        Args:
            method: HTTP method ('GET' or 'POST')
            endpoint: API endpoint path
            data: Request data (for POST)
            params: Query parameters (for GET)
            is_graphql: Whether this is a GraphQL request

        Returns:
            dict: API response

        """
        # Apply rate limiting
        if "private" in endpoint.lower() or is_graphql:
            self._rate_limiter.acquire()

        # Prepare URL
        if not self._params:
            # Use default URL
            if is_graphql:
                base_url = "https://proxy-worker-api.pancakeswap.com/bsc-exchange"
            else:
                base_url = "https://api.pancakeswap.org"
        else:
            base_url = self._params.rest_url

        url = f"{base_url}{endpoint}"

        # Make request
        headers = {"Content-Type": "application/json", "User-Agent": "bt_api_py/1.0"}

        if method.upper() == "GET":
            response = self._session.get(url, params=params, headers=headers)
        else:
            if data is None:
                data = {}
            response = self._session.post(url, json=data, headers=headers)

        # Check response
        if response.status_code != 200:
            error_msg = f"PancakeSwap API error: {response.status_code} - {response.text}"
            self.request_logger.error(error_msg)
            raise Exception(error_msg)

        # Parse JSON response
        try:
            result = response.json()
        except ValueError as exc:
            error_msg = "Invalid JSON response from PancakeSwap API"
            self.request_logger.error(error_msg)
            raise Exception(error_msg) from exc

        # Check for GraphQL errors
        if is_graphql and "errors" in result:
            error = result["errors"][0]
            error_msg = error.get("message", "Unknown GraphQL error")
            unified_code, unified_msg = self._error_translator.translate_error(result)
            self.request_logger.error(f"GraphQL error: {error_msg} -> {unified_msg}")
            raise Exception(unified_msg)

        return result

    def _make_graphql_query(self, query: str, variables: dict | None = None) -> dict:
        """Make GraphQL query to PancakeSwap Subgraph.

        Args:
            query: GraphQL query string
            variables: GraphQL variables

        Returns:
            dict: GraphQL response

        """
        return self._make_request(
            "POST",
            "",  # GraphQL endpoint is usually the base URL
            data={"query": query, "variables": variables or {}},
            is_graphql=True,
        )

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        extra_data=None,
        body: Any = None,
        timeout: int = 10,
    ):
        """HTTP request for PancakeSwap REST API.

        Args:
            method: HTTP method (GET, POST)
            path: REST endpoint path
            params: Query parameters
            extra_data: Extra data to attach to response
            body: Request body (for POST)
            timeout: Request timeout

        Returns:
            RequestData with parsed response

        """
        if not self._params:
            base_url = "https://api.pancakeswap.org"
        else:
            base_url = getattr(self._params, "rest_url", "https://api.pancakeswap.org")

        url = f"{base_url}{path}"
        headers = {"Content-Type": "application/json", "User-Agent": "bt_api_py/1.0"}

        try:
            response = self._http_client.request(
                method=method,
                url=url,
                headers=headers,
                json_data=body if method.upper() == "POST" else None,
                params=params,
            )
            return RequestData(response, extra_data)
        except Exception as e:
            self.request_logger.error(f"PancakeSwap request failed: {e}")
            raise

    async def async_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        extra_data=None,
        body: Any = None,
        timeout: int = 5,
    ):
        """Async HTTP request for PancakeSwap REST API."""
        if not self._params:
            base_url = "https://api.pancakeswap.org"
        else:
            base_url = getattr(self._params, "rest_url", "https://api.pancakeswap.org")

        url = f"{base_url}{path}"
        headers = {"Content-Type": "application/json", "User-Agent": "bt_api_py/1.0"}

        try:
            response = await self._http_client.async_request(
                method=method,
                url=url,
                headers=headers,
                json_data=body if method.upper() == "POST" else None,
                params=params,
            )
            return RequestData(response, extra_data)
        except Exception as e:
            self.async_logger.error(f"PancakeSwap async request failed: {e}")
            raise

    def async_callback(self, future):
        """Callback function for async requests, push result to data_queue."""
        try:
            result = future.result()
            if result is not None:
                self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.error(f"Async callback error: {e}")

    # ── Standard Interface: get_server_time ───────────────────────

    def _get_server_time(self, extra_data=None, **kwargs) -> float:
        """Prepare server time request. Returns (path, params, extra_data).

        PancakeSwap is a DEX — no dedicated server time endpoint.
        """
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": "get_server_time",
                "server_time": time.time(),
            }
        )
        return "GET", "/api/v1/time", {}, extra_data

    def get_server_time(self, extra_data=None, **kwargs) -> float:
        """Get server time. Returns RequestData."""
        method, path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return RequestData({"server_time": time.time()}, extra_data)

    def push_data_to_queue(self, data):
        """Push data to the queue."""
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            raise RuntimeError("Queue not initialized")

    def connect(self) -> None:
        """No-op for HTTP-based REST API."""

    def disconnect(self) -> None:
        """No-op for HTTP-based REST API."""

    def is_connected(self) -> bool:
        """Always return True for HTTP-based REST API."""
        return True

    def _get_exchange_info(self) -> dict:
        """Get exchange information including supported tokens and pairs."""
        # GraphQL query for pairs
        query = """
        {
          pairs(first: 100, orderBy: reserveUSD, orderDirection: desc) {
            id
            token0 { symbol name address decimals }
            token1 { symbol name address decimals }
            reserveUSD
            volumeUSD
            price
            liquidity
          }
        }
        """

        result = self._make_graphql_query(query)
        return result.get("data", {})

    def _get_ticker(self, symbol: str) -> dict:
        """Get ticker information for a trading pair."""
        # Convert symbol to pair address if needed
        pair_address = self._symbol_to_pair_address(symbol)

        query = """
        query GetPair($id: String!) {
          pair(id: $id) {
            id
            token0 { symbol name address decimals }
            token1 { symbol name address decimals }
            reserveUSD
            volumeUSD
            volumeToken0
            volumeToken1
            price
            liquidity
            txCount
          }
        }
        """

        variables = {"id": pair_address}
        result = self._make_graphql_query(query, variables)
        pair_data = result.get("data", {}).get("pair", {})

        if not pair_data:
            raise Exception(f"Pair not found: {symbol}")

        # Convert to ticker format
        ticker = {
            "symbol": symbol,
            "last_price": float(pair_data.get("price", 0)),
            "quote_volume": float(pair_data.get("volumeUSD", 0)),
            "base_volume": float(pair_data.get("volumeToken0", 0))
            + float(pair_data.get("volumeToken1", 0)),
            "timestamp": int(time.time() * 1000),
            "bid": None,  # DEX doesn't provide bid/ask
            "ask": None,
            "high": None,  # Would need historical data
            "low": None,
        }

        return ticker

    def _get_depth(self, symbol: str, limit: int = 100) -> dict:
        """Get order book depth for a trading pair."""
        # PancakeSwap doesn't provide a traditional order book API
        # This would need to be implemented by querying liquidity pools
        pair_address = self._symbol_to_pair_address(symbol)

        query = """
        query GetPairReserves($id: String!) {
          pair(id: $id) {
            reserve0
            reserve1
          }
        }
        """

        variables = {"id": pair_address}
        result = self._make_graphql_query(query, variables)
        pair_data = result.get("data", {}).get("pair", {})

        if not pair_data:
            raise Exception(f"Pair not found: {symbol}")

        # Simulate order book based on reserves
        reserve0 = float(pair_data.get("reserve0", 0))
        reserve1 = float(pair_data.get("reserve1", 0))

        # Simple price calculation
        price = reserve1 / reserve0 if reserve0 > 0 and reserve1 > 0 else 0

        # Create a simulated order book with limited depth
        depth = {
            "symbol": symbol,
            "bids": [[price * 0.999, reserve0 * 0.1]],  # Simulated bid
            "asks": [[price * 1.001, reserve0 * 0.1]],  # Simulated ask
            "timestamp": int(time.time() * 1000),
        }

        return depth

    def _get_kline(self, symbol: str, interval: str, limit: int = 100) -> dict:
        """Get K-line/candlestick data for a trading pair."""
        pair_address = self._symbol_to_pair_address(symbol)

        # Convert interval to appropriate GraphQL parameter
        time_intervals = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "4h": "240",
            "1d": "1440",
        }

        time_period = time_intervals.get(interval, "1440")

        query = """
        query GetPairDayDatas($id: String!, $period: String!, $limit: Int!) {
          pairDayDatas(
            first: $limit
            orderBy: date
            orderDirection: desc
            where: { pair: $id, date_gt: %NOW% }
          ) {
            date
            dailyVolumeUSD
            reserveUSD
            dailyTxns
          }
        }
        """

        # Replace placeholder with current time
        now = int(time.time()) - (limit * int(time_period) * 60)
        query = query.replace("%NOW%", str(now))

        variables = {"id": pair_address, "period": time_period, "limit": limit}

        result = self._make_graphql_query(query, variables)
        day_datas = result.get("data", {}).get("pairDayDatas", [])

        # Convert to K-line format
        klines = []
        for data in day_datas:
            klines.append(
                {
                    "symbol": symbol,
                    "timestamp": int(data.get("date", 0)),
                    "open": None,  # Would need historical open prices
                    "high": None,
                    "low": None,
                    "close": None,
                    "volume": float(data.get("dailyVolumeUSD", 0)),
                    "quote_volume": float(data.get("dailyVolumeUSD", 0)),
                }
            )

        return {"klines": klines}

    def _get_pool_info(self, pool_address: str) -> dict:
        """Get detailed information about a liquidity pool."""
        query = """
        query GetPool($id: String!) {
          pair(id: $id) {
            id
            token0 { symbol name address decimals }
            token1 { symbol name address decimals }
            reserve0
            reserve1
            reserveUSD
            volumeUSD
            volumeUSD
            price
            liquidity
            feeTier
            txCount
            createdAtTimestamp
          }
        }
        """

        variables = {"id": pool_address}
        result = self._make_graphql_query(query, variables)
        return result.get("data", {})

    def _get_token_info(self, token_address: str) -> dict:
        """Get information about a token."""
        query = """
        query GetToken($id: String!) {
          token(id: $id) {
            id
            symbol
            name
            decimals
            totalSupply
            tradeVolumeUSD
            totalLiquidity
            priceUSD
          }
        }
        """

        variables = {"id": token_address}
        result = self._make_graphql_query(query, variables)
        return result.get("data", {})

    def _symbol_to_pair_address(self, symbol: str) -> str:
        """Convert trading pair symbol to pair address."""
        # This would normally need a mapping from symbol to contract address
        # For now, return a default address
        symbol_mapping = {
            "BTCB/USDT": "0x58F876857a02D6762EeFA1aF755FEE1271A3ACaC",
            "ETH/USDT": "0x70e36197034F56Bf06712e97d19AEd5C0b8453D1",
            "CAKE/USDT": "0x04514E7Ba3F091234D6Be8E39864a7a3Ad4a1E1e",
        }

        return symbol_mapping.get(symbol, "0x0")

    def _get_balance(self, address: str) -> dict:
        """Get token balances for an address."""
        # This would require a different GraphQL endpoint
        # Placeholder implementation
        return {"balances": []}

    def _get_position(self, address: str) -> dict:
        """Get position information for an address."""
        # This would require querying position data
        # Placeholder implementation
        return {"positions": []}
