"""
Uniswap Spot Feed implementation.

Provides market data access for Uniswap DEX pools using GraphQL queries.
"""

from typing import Any

from bt_api_py.containers.exchanges.uniswap_exchange_data import (
    UniswapExchangeDataSpot,
    UniswapChain,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_uniswap.request_base import UniswapRequestData
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class UniswapRequestDataSpot(UniswapRequestData):
    """Uniswap Spot Feed for DEX market data.

    Supports:
    - Getting token prices and tickers
    - Getting pool information
    - Getting swap quotes
    - Querying swappable tokens
    """

    @classmethod
    def _capabilities(cls):
        """Declare supported capabilities."""
        return {
            "GET_TICK",
            "GET_DEPTH",
            "GET_EXCHANGE_INFO",
            "GET_KLINE",
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "UNISWAP___DEX")
        self.asset_type = kwargs.get("asset_type", "ethereum")
        # Chain is already handled by parent class
        self.logger_name = kwargs.get("logger_name", "uniswap_dex_feed.log")
        self._params = UniswapExchangeDataSpot(self.chain)
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()

    # ==================== Token Price/Ticker Queries ====================

    def _get_tick(
        self, symbol: str, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Get token price/ticker.

        Args:
            symbol: Token address (e.g., "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2" for WETH)
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data) - for compatibility
        """
        request_type = "get_tick"

        query = """
        query($token: TokenInput!) {
          token {
            id
            symbol
            name
            decimals
            price {
             USD
            }
            volume {
             USD
            }
            priceChange24h {
             USD
            }
            marketCap {
             USD
            }
          }
        }
        """

        variables = {
            "token": {
                "address": self._params.get_symbol(symbol),
            }
        }

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
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

        token_data = input_data.get("data", {}).get("token")
        status = token_data is not None

        if not token_data:
            return [], status

        # Convert to standardized format
        ticker = {
            "symbol": token_data.get("symbol"),
            "name": token_data.get("name"),
            "price": token_data.get("price", {}).get("USD"),
            "price_change_24h": token_data.get("priceChange24h", {}).get("USD"),
            "volume_24h": token_data.get("volume", {}).get("USD"),
            "market_cap": token_data.get("marketCap", {}).get("USD"),
            "decimals": token_data.get("decimals"),
        }

        return [ticker], status

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
        query = extra_data.pop("_graphql_query")
        variables = extra_data.pop("_graphql_variables")
        return self._execute_graphql_query(query, variables, extra_data)

    # ==================== Pool Queries ====================

    def _get_pool(
        self, pool_id: str, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Get single pool details.

        Args:
            pool_id: Pool address or ID
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data) - for compatibility
        """
        request_type = "get_pool"

        query = """
        query($poolAddress: String!) {
          pool(id: $poolAddress) {
            id
            address
            name
            symbol
            type
            swapFee
            totalValueLockedUSD
            volumeUSD
            volumeUSDDay
            volumeUSDWeek
            feesUSD
            token0 {
              id
              symbol
              name
              decimals
              priceUSD
            }
            token1 {
              id
              symbol
              name
              decimals
              priceUSD
            }
            reserve0
            reserve1
            token0Price
            token1Price
            liquidityProviderCount
          }
        }
        """

        variables = {
            "poolAddress": pool_id,
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

        return self._params.get_rest_path(request_type), {}, extra_data

    @staticmethod
    def _get_pool_normalize_function(input_data, extra_data):
        """Normalize pool query response."""
        if not input_data:
            return [], False

        pool_data = input_data.get("data", {}).get("pool")
        status = pool_data is not None

        if not pool_data:
            return [], status

        return [pool_data], status

    def get_pool(self, pool_id: str, extra_data=None, **kwargs):
        """Get pool details.

        Args:
            pool_id: Pool address
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with pool information
        """
        path, params, extra_data = self._get_pool(pool_id, extra_data, **kwargs)
        query = extra_data.pop("_graphql_query")
        variables = extra_data.pop("_graphql_variables")
        return self._execute_graphql_query(query, variables, extra_data)

    # ==================== Swap Quote Queries ====================

    def _get_swap_quote(
        self,
        token_in: str,
        token_out: str,
        amount: str,
        swap_type: str = "EXACT_IN",
        slippage_tolerance: float = 0.5,
        extra_data=None,
        **kwargs,
    ) -> tuple[str, dict[str, Any], Any]:
        """Get swap quote.

        Args:
            token_in: Input token address
            token_out: Output token address
            amount: Swap amount (human-readable, e.g., "1" for 1 token)
            swap_type: EXACT_IN or EXACT_OUT
            slippage_tolerance: Slippage tolerance percentage
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_swap_quote"

        query = """
        query($tokenIn: String!, $tokenOut: String!, $amount: String!, $type: SwapType!, $slippageTolerance: Float!) {
          quote(
            tokenIn: $tokenIn
            tokenOut: $tokenOut
            amount: $amount
            type: $type
            slippageTolerance: $slippageTolerance
          ) {
            tokenIn {
              address
              symbol
              name
            }
            tokenOut {
              address
              symbol
              name
            }
            amountIn
            amountOut
            priceImpact
            estimatedGas
            route {
              id
              segments {
                pool {
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
              }
            }
          }
        }
        """

        variables = {
            "tokenIn": self._params.get_symbol(token_in),
            "tokenOut": self._params.get_symbol(token_out),
            "amount": str(amount),
            "type": swap_type,
            "slippageTolerance": slippage_tolerance,
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
                "normalize_function": self._get_swap_quote_normalize_function,
                "_graphql_query": query,
                "_graphql_variables": variables,
            },
        )

        return self._params.get_rest_path(request_type), {}, extra_data

    @staticmethod
    def _get_swap_quote_normalize_function(input_data, extra_data):
        """Normalize swap quote response."""
        if not input_data:
            return [], False

        quote_data = input_data.get("data", {}).get("quote")
        status = quote_data is not None

        if not quote_data:
            return [], status

        return [quote_data], status

    def get_swap_quote(
        self,
        token_in: str,
        token_out: str,
        amount: str,
        swap_type: str = "EXACT_IN",
        slippage_tolerance: float = 0.5,
        extra_data=None,
        **kwargs,
    ):
        """Get swap quote for token swap.

        Args:
            token_in: Input token address
            token_out: Output token address
            amount: Amount to swap (human-readable)
            swap_type: EXACT_IN or EXACT_OUT
            slippage_tolerance: Slippage tolerance percentage
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with swap quote information
        """
        path, params, extra_data = self._get_swap_quote(
            token_in, token_out, amount, swap_type, slippage_tolerance, extra_data, **kwargs
        )
        query = extra_data.pop("_graphql_query")
        variables = extra_data.pop("_graphql_variables")
        return self._execute_graphql_query(query, variables, extra_data)

    # ==================== Swappable Tokens ====================

    def _get_swappable_tokens(
        self, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Get list of swappable tokens.

        Args:
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_swappable_tokens"

        query = """
        query {
          swappableTokens {
            id
            address
            symbol
            name
            decimals
            priceUSD
            volumeUSD
            marketCapUSD
            totalLiquidityUSD
          }
        }
        """

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
                "normalize_function": self._get_swappable_tokens_normalize_function,
                "_graphql_query": query,
            },
        )

        return self._params.get_rest_path(request_type), {}, extra_data

    @staticmethod
    def _get_swappable_tokens_normalize_function(input_data, extra_data):
        """Normalize swappable tokens response."""
        if not input_data:
            return [], False

        tokens = input_data.get("data", {}).get("swappableTokens", [])
        status = tokens is not None

        return tokens or [], status

    def get_swappable_tokens(self, extra_data=None, **kwargs):
        """Get list of swappable tokens.

        Args:
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with list of swappable tokens
        """
        path, params, extra_data = self._get_swappable_tokens(extra_data, **kwargs)
        query = extra_data.pop("_graphql_query")
        return self._execute_graphql_query(query, {}, extra_data)

    # ==================== Depth/Liquidity ====================

    def _get_depth(
        self, symbol: str, count: int = 20, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Get liquidity depth for a token/pool.

        Note: Uniswap doesn't have traditional order books. This returns
        pool liquidity information instead.

        Args:
            symbol: Pool address or token address
            count: Number of levels (not applicable for AMM pools)
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_depth"

        # For Uniswap, depth is pool liquidity
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
        # Uniswap pools don't have order books
        # Return liquidity information instead
        if not input_data:
            return [], False

        return [input_data], True

    def get_depth(self, symbol: str, count: int = 20, extra_data=None, **kwargs):
        """Get liquidity depth.

        Note: This is not a traditional order book. For Uniswap pools,
        consider using get_pool() to get full pool liquidity info.
        """
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        # For pools, use get_pool instead
        if symbol.startswith("0x"):
            return self.get_pool(symbol, extra_data, **kwargs)
        # Otherwise return a message about AMM pools
        return RequestData(
            {"message": "Uniswap is an AMM, use get_pool() for pool liquidity"},
            extra_data,
        )

    # ==================== Kline/Historical Data ====================

    def _get_kline(
        self, symbol: str, period: str, count: int = 20, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Get historical price data (kline).

        Args:
            symbol: Token address
            period: Time period
            count: Number of data points
            extra_data: Extra data to attach to response
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
        # Uniswap doesn't have direct kline data in Trading API
        return [], True

    def get_kline(
        self, symbol: str, period: str, count: int = 20, extra_data=None, **kwargs
    ):
        """Get kline data.

        Note: Uniswap Trading API doesn't provide direct kline data.
        Use pool events or external data sources.
        """
        path, params, extra_data = self._get_kline(
            symbol, period, count, extra_data, **kwargs
        )
        return RequestData(
            {"message": "Klines not available via Uniswap Trading API. Use pool events or external sources."},
            extra_data,
        )

    # ==================== Exchange Info ====================

    def _get_exchange_info(self, extra_data=None, **kwargs) -> tuple[str, dict[str, Any], Any]:
        """Get exchange information.

        Args:
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_exchange_info"

        query = """
        query {
          service {
            apiKey
            version
            supportedChains
            features
          }
          tokens {
            totalCount
          }
          pools {
            totalCount
          }
        }
        """

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
                "normalize_function": self._get_exchange_info_normalize_function,
                "_graphql_query": query,
            },
        )

        return self._params.get_rest_path(request_type), {}, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info response."""
        if not input_data:
            return [], False

        service_data = input_data.get("data", {})
        status = service_data is not None

        exchange_info = {
            "service": service_data.get("service", {}),
            "tokens": service_data.get("tokens", {}),
            "pools": service_data.get("pools", {}),
        }

        return [exchange_info], status

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information.

        Args:
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with exchange information
        """
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        query = extra_data.pop("_graphql_query")
        return self._execute_graphql_query(query, {}, extra_data)