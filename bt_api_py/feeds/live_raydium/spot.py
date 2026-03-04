"""
Raydium Spot Feed implementation.

Provides market data access for Raydium DEX pools.
"""

from typing import Any

from bt_api_py.containers.exchanges.raydium_exchange_data import RaydiumExchangeDataSpot
from bt_api_py.containers.tickers.raydium_ticker import RaydiumRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_raydium.request_base import RaydiumRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class RaydiumRequestDataSpot(RaydiumRequestData):
    """Raydium DEX Feed for pool market data.

    Raydium is a Solana-based DEX. This feed provides:
    - Pool information (TVL, liquidity, tokens)
    - Token prices (derived from pool reserves)
    - Pool list with filtering
    - Swap path estimation (via SOR)

    Note: Raydium doesn't have traditional order books or klines.
    Use pool events or subgraph for historical data.
    """

    @classmethod
    def _capabilities(cls):
        """Declare supported capabilities."""
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "RAYDIUM___DEX")
        self.asset_type = kwargs.get("asset_type", "DEX")
        self._params = RaydiumExchangeDataSpot()
        self.chain = self._params.chain  # Expose chain from params
        self.request_logger = get_logger("raydium_spot")

    # ==================== Pools ====================

    def _get_pools(
        self,
        extra_data=None,
        first: int = 10,
        min_tvl: float | None = None,
        pool_type: str | None = None,
        **kwargs
    ):
        """Get list of pools.

        Args:
            extra_data: Extra data
            first: Number of pools to return
            min_tvl: Minimum TVL filter
            pool_type: Pool type filter (Standard, Concentrated, Stable)
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_pools"
        path = self._params.get_rest_path(request_type)

        params = {
            "poolType": pool_type or "all",
            "poolSortField": "default",
            "sortType": "desc",
            "pageSize": min(first, 100),
            "page": 1,
        }

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_pools_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_pools_normalize_function(input_data, extra_data):
        """Normalize pools response."""
        if not input_data:
            return [], False

        pools = input_data.get("data", [])
        status = input_data.get("success", False)

        return pools or [], status

    def get_pools(
        self,
        first: int = 10,
        min_tvl: float | None = None,
        pool_type: str | None = None,
        extra_data=None,
        **kwargs
    ):
        """Get list of pools.

        Args:
            first: Number of pools to return
            min_tvl: Minimum TVL filter
            pool_type: Pool type filter
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with list of pools
        """
        path, params, extra_data = self._get_pools(
            extra_data, first, min_tvl, pool_type, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Pool Detail ====================

    def _get_pool(self, pool_id: str, extra_data=None, **kwargs):
        """Get single pool details.

        Args:
            pool_id: Pool ID
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_pool_ids"
        path = self._params.get_rest_path(request_type)

        params = {"ids": pool_id}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "pool_id": pool_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_pool_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_pool_normalize_function(input_data, extra_data):
        """Normalize pool response."""
        if not input_data:
            return [], False

        pools = input_data.get("data", [])
        status = input_data.get("success", False)

        if pools and isinstance(pools, list):
            return pools, status

        return [], status

    def get_pool(self, pool_id: str, extra_data=None, **kwargs):
        """Get pool details.

        Args:
            pool_id: Pool ID
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with pool information
        """
        path, params, extra_data = self._get_pool(pool_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Ticker/Price ====================

    def _get_tick(self, symbol: str, extra_data=None, **kwargs):
        """Get token price/ticker.

        For Raydium, this returns pool information for the token pair.

        Args:
            symbol: Trading pair (e.g., 'SOL/USDC')
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_pool_by_mint"
        path = self._params.get_rest_path(request_type)

        # Symbol format: SOL/USDC
        # For Raydium, we search by token mint addresses
        tokens = symbol.split("/")

        if len(tokens) != 2:
            raise ValueError(f"Invalid symbol format: {symbol}. Use 'BASE/QUOTE' format.")

        params = {
            "mint1": tokens[0].strip(),
            "mint2": tokens[1].strip(),
        }

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

        pools = input_data.get("data", [])
        status = input_data.get("success", False)

        # Return first matching pool
        if pools and isinstance(pools, list) and len(pools) > 0:
            return [pools[0]], status

        return [], status

    def get_tick(self, symbol: str, extra_data=None, **kwargs):
        """Get token price/ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_tick(self, symbol: str, extra_data=None, **kwargs):
        """Async get token price/ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Depth/Liquidity ====================

    def _get_depth(self, symbol: str, count: int = 20, extra_data=None, **kwargs):
        """Get liquidity depth.

        For DEX, this returns pool reserves instead of order book.

        Args:
            symbol: Trading pair
            count: Number of levels (not applicable for AMM)
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        # Use get_tick for pool data
        return self._get_tick(symbol, extra_data, **kwargs)

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth response."""
        return RaydiumRequestDataSpot._get_tick_normalize_function(input_data, extra_data)

    def get_depth(self, symbol: str, count: int = 20, extra_data=None, **kwargs):
        """Get liquidity depth. For AMM pools, returns pool reserves."""
        return self.get_tick(symbol, extra_data, **kwargs)

    def async_get_depth(self, symbol: str, count: int = 20, extra_data=None, **kwargs):
        """Async get liquidity depth."""
        self.async_get_tick(symbol, extra_data, **kwargs)

    # ==================== Exchange Info ====================

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information (all pools).

        Args:
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        return self._get_pools(extra_data, first=100, **kwargs)

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information.

        Args:
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with all pools
        """
        return self.get_pools(first=100, extra_data=extra_data, **kwargs)

    # ==================== Mint Prices ====================

    def _get_mint_prices(self, extra_data=None, **kwargs):
        """Get mint token prices.

        Args:
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_mint_price"
        path = self._params.get_rest_path(request_type)

        params = {}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_mint_prices_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_mint_prices_normalize_function(input_data, extra_data):
        """Normalize mint prices response."""
        if not input_data:
            return [], False

        prices = input_data.get("data", [])
        status = input_data.get("success", False)

        return prices or [], status

    def get_mint_prices(self, extra_data=None, **kwargs):
        """Get mint token prices."""
        path, params, extra_data = self._get_mint_prices(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Standard Trading Interfaces ====================

    def _make_order(self, symbol, volume, price, order_type, offset="open",
                    post_only=False, client_order_id=None, extra_data=None, **kwargs):
        """Prepare order. Returns (path, params, extra_data).

        Raydium is a DEX; trading requires on-chain transactions.
        """
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": "make_order",
        })
        params = {
            "symbol": symbol, "quantity": str(volume),
            "price": str(price), "orderType": order_type,
        }
        return "/trade/swap", params, extra_data

    def make_order(self, symbol, volume, price, order_type, offset="open",
                   post_only=False, client_order_id=None, extra_data=None, **kwargs):
        """Place an order. Note: Raydium requires on-chain tx."""
        path, params, extra_data = self._make_order(
            symbol, volume, price, order_type, offset, post_only,
            client_order_id, extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    def _cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": "cancel_order",
            "order_id": order_id,
        })
        return f"/orders/{order_id}", {}, extra_data

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order."""
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": "query_order",
            "order_id": order_id,
        })
        return f"/orders/{order_id}", {}, extra_data

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order status."""
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol or "",
            "asset_type": self.asset_type,
            "request_type": "get_open_orders",
        })
        return "/orders", {}, extra_data

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders."""
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Standard Account Interfaces ====================

    def _get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol or "",
            "asset_type": self.asset_type,
            "request_type": "get_account",
        })
        return "/account", {}, extra_data

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info."""
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get balance. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update({
            "exchange_name": self.exchange_name,
            "symbol_name": symbol or "",
            "asset_type": self.asset_type,
            "request_type": "get_balance",
        })
        return "/balance", {}, extra_data

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get token balance."""
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
