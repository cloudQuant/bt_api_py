"""
Balancer Spot Feed implementation.

Provides market data access for Balancer DEX pools using GraphQL queries.
"""

from typing import Any

from bt_api_py.containers.exchanges.balancer_exchange_data import (
    BalancerExchangeDataSpot,
    GqlChain,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_balancer.request_base import BalancerRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class BalancerRequestDataSpot(BalancerRequestData):
    """Balancer Spot Feed for DEX market data.

    Supports:
    - Getting pool information (TVL, APR, tokens)
    - Getting token prices
    - Getting swap paths (SOR)
    - Querying pool events
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
        self.exchange_name = kwargs.get("exchange_name", "BALANCER___DEX")
        self.asset_type = kwargs.get("asset_type", "DEX")
        # Chain is already handled by parent class
        self.logger_name = kwargs.get("logger_name", "balancer_dex_feed.log")
        self._params = BalancerExchangeDataSpot(self.chain)
        self.request_logger = get_logger("balancer_dex_feed")

    # ==================== Pool Queries ====================

    def _get_pool(
        self, pool_id: str, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Get single pool details.

        Args:
            pool_id: Pool ID (bytes32 as hex string)
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data) - for compatibility
        """
        request_type = "get_pool"

        query = """
        query($poolId: ID!, $chain: GqlChain!) {
          poolGetPool(id: $poolId, chain: $chain) {
            id
            address
            name
            symbol
            type
            version
            allTokens {
              address
              name
              symbol
              decimals
            }
            poolTokens {
              address
              symbol
              balance
              balanceUsd
              weight
              priceRate
            }
            dynamicData {
              totalLiquidity
              totalShares
              volume24h
              fees24h
              aprItems {
                title
                type
                apr
              }
            }
            tokens {
              address
              symbol
              decimals
            }
          }
        }
        """

        variables = {
            "poolId": pool_id,
            "chain": self._params.get_chain_value(),
        }

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "pool_id": pool_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
                "normalize_function": self._get_pool_normalize_function,
                "_graphql_query": query,
                "_graphql_variables": variables,
            },
        )

        # Return path placeholder for compatibility
        return self._params.get_rest_path(request_type), {}, extra_data

    @staticmethod
    def _get_pool_normalize_function(input_data, extra_data):
        """Normalize pool query response."""
        if not input_data:
            return [], False

        pool_data = input_data.get("data", {}).get("poolGetPool")
        status = pool_data is not None

        if not pool_data:
            return [], status

        # Return as list for consistency with other APIs
        return [pool_data], status

    def get_pool(self, pool_id: str, extra_data=None, **kwargs):
        """Get pool details.

        Args:
            pool_id: Pool ID (e.g., "0x7f2b3b7fbd3226c5be438cde49a519f442ca2eda00020000000000000000067d")
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with pool information
        """
        path, params, extra_data = self._get_pool(pool_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_pools(
        self,
        extra_data=None,
        first: int = 10,
        min_tvl: float | None = None,
        skip: int = 0,
        **kwargs,
    ) -> tuple[str, dict[str, Any], Any]:
        """Get multiple pools with filtering.

        Args:
            extra_data: Extra data
            first: Number of pools to return
            min_tvl: Minimum TVL filter
            skip: Number of pools to skip
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_pools"

        query = """
        query($chains: [GqlChain!], $first: Int, $skip: Int, $minTvl: Float) {
          poolGetPools(
            first: $first
            skip: $skip
            orderBy: totalLiquidity
            orderDirection: desc
            where: { chainIn: $chains, minTvl: $minTvl }
          ) {
            id
            address
            name
            symbol
            type
            dynamicData {
              totalLiquidity
              volume24h
              fees24h
              aprItems {
                title
                type
                apr
              }
            }
          }
        }
        """

        variables = {
            "chains": [self._params.get_chain_value()],
            "first": first,
            "skip": skip,
        }
        if min_tvl is not None:
            variables["minTvl"] = min_tvl

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
                "normalize_function": self._get_pools_normalize_function,
                "_graphql_query": query,
                "_graphql_variables": variables,
            },
        )

        return self._params.get_rest_path(request_type), {}, extra_data

    @staticmethod
    def _get_pools_normalize_function(input_data, extra_data):
        """Normalize pools query response."""
        if not input_data:
            return [], False

        pools = input_data.get("data", {}).get("poolGetPools", [])
        status = pools is not None

        return pools or [], status

    def get_pools(
        self,
        first: int = 10,
        min_tvl: float | None = None,
        extra_data=None,
        **kwargs,
    ):
        """Get list of pools sorted by TVL.

        Args:
            first: Number of pools to return
            min_tvl: Minimum TVL filter
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with list of pools
        """
        path, params, extra_data = self._get_pools(
            extra_data, first, min_tvl, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Token Price Queries ====================

    def _get_tick(
        self, symbol: str, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Get token price/ticker.

        Args:
            symbol: Token address (e.g., "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2" for WETH)
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_tick"

        query = """
        query($address: String!, $chain: GqlChain!) {
          tokenGetTokenDynamicData(address: $address, chain: $chain) {
            price
            priceChange24h
            marketCap
          }
        }
        """

        variables = {
            "address": self._params.get_symbol(symbol),
            "chain": self._params.get_chain_value(),
        }

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_tick_normalize_function,
                "_graphql_query": query,
                "_graphql_variables": variables,
            },
        )

        return self._params.get_rest_path(request_type), {}, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker response."""
        if not input_data:
            return [], False

        ticker_data = input_data.get("data", {}).get("tokenGetTokenDynamicData")
        status = ticker_data is not None

        if not ticker_data:
            return [], status

        return [ticker_data], status

    def get_tick(self, symbol: str, extra_data=None, **kwargs):
        """Get token price and 24h statistics.

        Args:
            symbol: Token address
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with token price data
        """
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_tick(self, symbol: str, extra_data=None, **kwargs):
        """Async get token price."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Swap Path (SOR) Queries ====================

    def _get_swap_path(
        self,
        token_in: str,
        token_out: str,
        amount: str,
        swap_type: str = "EXACT_IN",
        extra_data=None,
        **kwargs,
    ) -> tuple[str, dict[str, Any], Any]:
        """Get optimal swap path using SOR (Smart Order Router).

        Args:
            token_in: Input token address
            token_out: Output token address
            amount: Swap amount (human-readable, e.g., "1" for 1 token)
            swap_type: EXACT_IN or EXACT_OUT
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_swap_path"

        query = """
        query($chain: GqlChain!, $swapAmount: AmountHumanReadable!, $swapType: GqlSorSwapType!, $tokenIn: String!, $tokenOut: String!) {
          sorGetSwapPaths(
            chain: $chain
            swapAmount: $swapAmount
            swapType: $swapType
            tokenIn: $tokenIn
            tokenOut: $tokenOut
          ) {
            swapAmountRaw
            returnAmountRaw
            priceImpact {
              priceImpact
              error
            }
            tokenAddresses
          }
        }
        """

        variables = {
            "chain": self._params.get_chain_value(),
            "swapAmount": str(amount),
            "swapType": swap_type,
            "tokenIn": self._params.get_symbol(token_in),
            "tokenOut": self._params.get_symbol(token_out),
        }

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "token_in": token_in,
                "token_out": token_out,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
                "normalize_function": self._get_swap_path_normalize_function,
                "_graphql_query": query,
                "_graphql_variables": variables,
            },
        )

        return self._params.get_rest_path(request_type), {}, extra_data

    @staticmethod
    def _get_swap_path_normalize_function(input_data, extra_data):
        """Normalize swap path response."""
        if not input_data:
            return [], False

        swap_data = input_data.get("data", {}).get("sorGetSwapPaths")
        status = swap_data is not None

        if not swap_data:
            return [], status

        return [swap_data], status

    def get_swap_path(
        self,
        token_in: str,
        token_out: str,
        amount: str,
        swap_type: str = "EXACT_IN",
        extra_data=None,
        **kwargs,
    ):
        """Get optimal swap path for a token swap.

        Args:
            token_in: Input token address
            token_out: Output token address
            amount: Amount to swap (human-readable)
            swap_type: EXACT_IN or EXACT_OUT
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with swap path information
        """
        path, params, extra_data = self._get_swap_path(
            token_in, token_out, amount, swap_type, extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Depth/Liquidity ====================

    def _get_depth(
        self, symbol: str, count: int = 20, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Get liquidity depth for a token/pool.

        Note: Balancer doesn't have traditional order books. This returns
        pool liquidity information instead.

        Args:
            symbol: Pool ID or token address
            count: Number of levels (not applicable for AMM pools)
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_depth"

        # For Balancer, depth is pool liquidity. Use get_pools filtered by token
        # or get_pool if symbol is a pool ID
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

        return self._params.get_rest_path(request_type), {}, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth response."""
        # Balancer pools don't have order books
        # Return liquidity information instead
        if not input_data:
            return [], False

        return [input_data], True

    def get_depth(self, symbol: str, count: int = 20, extra_data=None, **kwargs):
        """Get liquidity depth.

        Note: This is not a traditional order book. For Balancer pools,
        consider using get_pool() to get full pool liquidity info.
        """
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(self, symbol: str, count: int = 20, extra_data=None, **kwargs):
        """Async get liquidity depth."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Kline/Historical Data ====================

    def _get_kline(
        self, symbol: str, period: str, count: int = 20, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Get historical price data (kline).

        Note: Balancer GraphQL API doesn't directly provide klines.
        This would need to be fetched from pool swap events or subgraph.

        Args:
            symbol: Token address or pool ID
            period: Time period (not directly supported)
            count: Number of data points
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_kline"

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

        return self._params.get_rest_path(request_type), {}, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline response."""
        # Balancer doesn't have direct kline data
        return [], True

    def get_kline(
        self, symbol: str, period: str, count: int = 20, extra_data=None, **kwargs
    ):
        """Get kline data.

        Note: Balancer doesn't provide direct kline data through GraphQL.
        Use pool events query or external data sources.
        """
        path, params, extra_data = self._get_kline(
            symbol, period, count, extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_kline(
        self, symbol: str, period: str, count: int = 20, extra_data=None, **kwargs
    ):
        """Async get kline data."""
        path, params, extra_data = self._get_kline(
            symbol, period, count, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Exchange Info ====================

    def _get_exchange_info(
        self, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Get exchange information (list of pools for Balancer).

        Args:
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        # For Balancer, exchange info is the list of pools
        return self._get_pools(extra_data=extra_data, **kwargs)

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information.

        Returns:
            RequestData with exchange info (pools list)
        """
        return self.get_pools(extra_data=extra_data, **kwargs)

    # ==================== Pool Events ====================

    def _get_pool_events(
        self,
        pool_id: str,
        event_type: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        extra_data=None,
        **kwargs,
    ) -> tuple[str, dict[str, Any], Any]:
        """Get pool swap/add/remove liquidity events.

        Args:
            pool_id: Pool ID
            event_type: Event type filter (SWAP, JOIN, EXIT)
            start_time: Start timestamp
            end_time: End timestamp
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_pool_events"

        query = """
        query($poolId: String!, $chain: GqlChain!, $range: PoolTimeRangeInput!, $typeIn: [GqlPoolEventType!]) {
          poolGetEvents(poolId: $poolId, chain: $chain, range: $range, typeIn: $typeIn) {
            id
            type
            timestamp
            tx
            sender
            {
              swap {
                poolId {
                  id
                  address
                }
                tokenIn {
                  address
                  symbol
                }
                tokenOut {
                  address
                  symbol
                }
                amountIn
                amountOut
                amountInUsd
                amountOutUsd
              }
            }
          }
        }
        """

        # Default range: last 24 hours
        import time

        now = int(time.time())
        range_vars = {"start": start_time or (now - 86400), "end": end_time or now}

        variables = {
            "poolId": pool_id,
            "chain": self._params.get_chain_value(),
            "range": range_vars,
        }
        if event_type:
            variables["typeIn"] = [event_type]

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "pool_id": pool_id,
                "event_type": event_type,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_pool_events_normalize_function,
                "_graphql_query": query,
                "_graphql_variables": variables,
            },
        )

        return self._params.get_rest_path(request_type), {}, extra_data

    @staticmethod
    def _get_pool_events_normalize_function(input_data, extra_data):
        """Normalize pool events response."""
        if not input_data:
            return [], False

        events = input_data.get("data", {}).get("poolGetEvents", [])
        status = events is not None

        return events or [], status

    def get_pool_events(
        self,
        pool_id: str,
        event_type: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        extra_data=None,
        **kwargs,
    ):
        """Get pool events (swaps, joins, exits).

        Args:
            pool_id: Pool ID
            event_type: Event type filter (SWAP, JOIN, EXIT)
            start_time: Start timestamp
            end_time: End timestamp
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with pool events
        """
        path, params, extra_data = self._get_pool_events(
            pool_id, event_type, start_time, end_time, extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Standard Trading Interfaces ====================
    # Note: Balancer is a DEX. Trading is done via on-chain transactions.
    # These methods prepare swap parameters using SOR (Smart Order Router).

    def _make_order(self, symbol, volume, price, order_type, offset="open",
                    post_only=False, client_order_id=None, extra_data=None, **kwargs):
        """Prepare swap order parameters. Returns (path, body, extra_data).

        For Balancer DEX, 'making an order' means preparing a swap path.
        symbol should be 'tokenIn-tokenOut' format or kwargs should contain
        token_in and token_out addresses.
        """
        if extra_data is None:
            extra_data = {}
        token_in = kwargs.get("token_in", "")
        token_out = kwargs.get("token_out", "")
        if not token_in and "-" in symbol:
            parts = symbol.split("-", 1)
            token_in, token_out = parts[0], parts[1]

        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": "make_order",
            "side": kwargs.get("side", "BUY"),
            "quantity": volume,
            "price": price,
            "order_type": order_type,
            "token_in": token_in,
            "token_out": token_out,
        })
        body = {
            "token_in": token_in,
            "token_out": token_out,
            "amount": str(volume),
            "swap_type": "EXACT_IN",
        }
        return "POST /swap", body, extra_data

    def make_order(self, symbol, volume, price, order_type, offset="open",
                   post_only=False, client_order_id=None, extra_data=None, **kwargs):
        """Place a swap order following standard Feed interface. Returns RequestData.

        Note: Balancer swaps require on-chain transaction signing.
        This method prepares the swap parameters.
        """
        path, body, extra_data = self._make_order(
            symbol, volume, price, order_type, offset, post_only,
            client_order_id, extra_data, **kwargs
        )
        return self.request(path, body=body, extra_data=extra_data)

    def _cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Prepare cancel order parameters. Returns (path, params, extra_data).

        Note: Balancer is a DEX — on-chain swaps cannot be cancelled
        once submitted. This is a no-op placeholder.
        """
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": "cancel_order",
            "order_id": order_id,
        })
        return "DELETE /order", {}, extra_data

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order. Returns RequestData.

        Note: DEX swaps are atomic and cannot be cancelled once on-chain.
        """
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Prepare query order parameters. Returns (path, params, extra_data).

        For DEX, this queries transaction status on-chain.
        """
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": "query_order",
            "order_id": order_id,
        })
        return "GET /order", {}, extra_data

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order status. Returns RequestData."""
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Prepare get open orders parameters. Returns (path, params, extra_data).

        Note: DEX has no open orders concept — swaps are atomic.
        """
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol or "",
            "asset_type": self.asset_type,
            "request_type": "get_open_orders",
        })
        return "GET /open_orders", {}, extra_data

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders. Returns RequestData.

        Note: DEX swaps are atomic; no pending orders.
        """
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Standard Account Interfaces ====================
    # Note: Balancer is a DEX. Account/balance queries require Web3/on-chain.

    def _get_account(self, symbol=None, extra_data=None, **kwargs):
        """Prepare get account parameters. Returns (path, params, extra_data).

        For DEX, account info is the wallet address and connected chain.
        """
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol or "",
            "asset_type": self.asset_type,
            "request_type": "get_account",
            "chain": self.chain.value,
        })
        return "GET /account", {}, extra_data

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info. Returns RequestData.

        Note: DEX account info is chain/wallet-based.
        """
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Prepare get balance parameters. Returns (path, params, extra_data).

        For DEX, balance queries require Web3 connection to read on-chain data.
        """
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol or "",
            "asset_type": self.asset_type,
            "request_type": "get_balance",
            "chain": self.chain.value,
        })
        return "GET /balance", {}, extra_data

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get token balance. Returns RequestData.

        Note: DEX balance requires Web3/on-chain query.
        """
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
