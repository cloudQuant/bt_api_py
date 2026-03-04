"""
SushiSwap Spot Feed implementation.

Provides market data access for SushiSwap DEX pools using REST API queries.
"""

from typing import Any

from bt_api_py.containers.exchanges.sushiswap_exchange_data import (
    SushiSwapExchangeDataSpot,
    SushiSwapChain,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_sushiswap.request_base import SushiSwapRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class SushiSwapRequestDataSpot(SushiSwapRequestData):
    """SushiSwap Spot Feed for DEX market data.

    Supports:
    - Getting token prices and tickers
    - Getting pool information
    - Getting swap quotes
    - Querying tokens
    """

    @classmethod
    def _capabilities(cls):
        """Declare supported capabilities."""
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_KLINE,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "SUSHISWAP___DEX")
        self.asset_type = kwargs.get("asset_type", "ethereum")
        self.logger_name = kwargs.get("logger_name", "sushiswap_dex_feed.log")
        self._params = SushiSwapExchangeDataSpot(self.chain)
        self.request_logger = get_logger("sushiswap_dex_feed")

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

        # Construct the endpoint with token address
        chain_id = self._params.get_chain_id()
        endpoint = f"/price/v1/{chain_id}/{symbol}"

        path = f"GET {endpoint}"

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
                "normalize_function": self._get_tick_normalize_function,
                "_endpoint": endpoint,
            },
        )

        return path, {}, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker response."""
        if not input_data:
            return [], False

        # SushiSwap price API returns price directly as a number or dict
        price_data = input_data if isinstance(input_data, dict) else {"price": input_data}
        status = price_data is not None

        # Convert to standardized format
        ticker = {
            "symbol": extra_data.get("symbol_name", ""),
            "price": price_data.get("price") if isinstance(price_data, dict) else price_data,
            "chain": extra_data.get("chain", "ETHEREUM"),
        }

        return [ticker], status

    def get_tick(self, symbol: str, extra_data=None, **kwargs):
        """Get token price."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        endpoint = extra_data.pop("_endpoint", "")
        return self._execute_rest_query("GET", endpoint, params, extra_data)

    def async_get_tick(self, symbol: str, extra_data=None, **kwargs):
        """Async get token price."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        endpoint = extra_data.pop("_endpoint", "")
        self.submit(
            self.async_request(endpoint, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Pool Queries ====================

    def _get_pool(
        self, pool_address: str, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Get single pool details.

        Args:
            pool_address: Pool address
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data) - for compatibility
        """
        request_type = "get_pool"

        chain_id = self._params.get_chain_id()
        endpoint = f"/pool/v1/{chain_id}/{pool_address}"

        path = f"GET {endpoint}"

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "pool_address": pool_address,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
                "normalize_function": self._get_pool_normalize_function,
                "_endpoint": endpoint,
            },
        )

        return path, {}, extra_data

    @staticmethod
    def _get_pool_normalize_function(input_data, extra_data):
        """Normalize pool query response."""
        if not input_data:
            return [], False

        pool_data = input_data if isinstance(input_data, dict) else {}
        status = pool_data is not None

        return [pool_data], status

    def get_pool(self, pool_address: str, extra_data=None, **kwargs):
        """Get pool details.

        Args:
            pool_address: Pool address
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with pool information
        """
        path, params, extra_data = self._get_pool(pool_address, extra_data, **kwargs)
        endpoint = extra_data.pop("_endpoint", "")

        return self._execute_rest_query("GET", endpoint, params, extra_data)

    # ==================== Swap Quote Queries ====================

    def _get_swap_quote(
        self,
        token_in: str,
        token_out: str,
        amount: str,
        slippage_tolerance: float = 0.5,
        extra_data=None,
        **kwargs,
    ) -> tuple[str, dict[str, Any], Any]:
        """Get swap quote.

        Args:
            token_in: Input token address
            token_out: Output token address
            amount: Swap amount (human-readable)
            slippage_tolerance: Slippage tolerance percentage
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_quote"

        chain_id = self._params.get_chain_id()

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
                "_chain_id": chain_id,
            },
        )

        # Build query parameters for quote API
        params = {
            "tokenIn": token_in,
            "tokenOut": token_out,
            "amount": amount,
            "maxSlippage": str(slippage_tolerance / 100),  # Convert to decimal
        }

        path = f"GET /quote/v7/{chain_id}"
        return path, params, extra_data

    @staticmethod
    def _get_swap_quote_normalize_function(input_data, extra_data):
        """Normalize swap quote response."""
        if not input_data:
            return [], False

        quote_data = input_data if isinstance(input_data, dict) else {}
        status = quote_data is not None

        return [quote_data], status

    def get_swap_quote(
        self,
        token_in: str,
        token_out: str,
        amount: str,
        slippage_tolerance: float = 0.5,
        extra_data=None,
        **kwargs,
    ):
        """Get swap quote for token swap.

        Args:
            token_in: Input token address
            token_out: Output token address
            amount: Amount to swap (human-readable)
            slippage_tolerance: Slippage tolerance percentage
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with swap quote information
        """
        path, params, extra_data = self._get_swap_quote(
            token_in, token_out, amount, slippage_tolerance, extra_data, **kwargs
        )
        chain_id = extra_data.pop("_chain_id", "1")
        endpoint = f"/quote/v7/{chain_id}"

        return self._execute_rest_query("GET", endpoint, params, extra_data)

    # ==================== Exchange Info ====================

    def _get_exchange_info(self, extra_data=None, **kwargs) -> tuple[str, dict[str, Any], Any]:
        """Get exchange information (tokens list).

        Args:
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_exchange_info"

        chain_id = self._params.get_chain_id()
        endpoint = f"/tokens/v1/{chain_id}"

        path = f"GET {endpoint}"

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
                "normalize_function": self._get_exchange_info_normalize_function,
                "_endpoint": endpoint,
            },
        )

        return path, {}, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info response."""
        if not input_data:
            return [], False

        tokens_data = input_data if isinstance(input_data, list) else []
        status = tokens_data is not None

        exchange_info = {
            "tokens": tokens_data,
            "chain": extra_data.get("chain", "ETHEREUM"),
            "count": len(tokens_data) if isinstance(tokens_data, list) else 0,
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
        endpoint = extra_data.pop("_endpoint", "")

        return self._execute_rest_query("GET", endpoint, params, extra_data)

    # ==================== Depth/Liquidity ====================

    def _get_depth(
        self, symbol: str, count: int = 20, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Get liquidity depth for a pool.

        Note: SushiSwap doesn't have traditional order books. This returns
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

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
                "normalize_function": self._get_depth_normalize_function,
            },
        )

        return "GET /depth", {}, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth response."""
        # SushiSwap pools don't have order books
        if not input_data:
            return [], False

        return [input_data], True

    def get_depth(self, symbol: str, count: int = 20, extra_data=None, **kwargs):
        """Get liquidity depth."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        if symbol.startswith("0x"):
            return self.get_pool(symbol, extra_data, **kwargs)
        return RequestData(
            {"message": "SushiSwap is an AMM, use get_pool() for pool liquidity"},
            extra_data,
        )

    def async_get_depth(self, symbol: str, count: int = 20, extra_data=None, **kwargs):
        """Async get liquidity depth."""
        self.async_get_tick(symbol, extra_data, **kwargs)

    # ==================== Token Info Queries ====================

    def _get_token_info(
        self, token_address: str, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Get token information.

        Args:
            token_address: Token address
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_token_info"

        chain_id = self._params.get_chain_id()
        endpoint = f"/token/v1/{chain_id}/{token_address}"

        path = f"GET {endpoint}"

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "token_address": token_address,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
                "normalize_function": self._get_token_info_normalize_function,
                "_endpoint": endpoint,
            },
        )

        return path, {}, extra_data

    @staticmethod
    def _get_token_info_normalize_function(input_data, extra_data):
        """Normalize token info response."""
        if not input_data:
            return [], False

        token_data = input_data if isinstance(input_data, dict) else {}
        status = token_data is not None

        return [token_data], status

    def get_token_info(self, token_address: str, extra_data=None, **kwargs):
        """Get token information.

        Args:
            token_address: Token address
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with token information
        """
        path, params, extra_data = self._get_token_info(token_address, extra_data, **kwargs)
        endpoint = extra_data.pop("_endpoint", "")

        return self._execute_rest_query("GET", endpoint, params, extra_data)

    # ==================== Tokens List Queries ====================

    @staticmethod
    def _get_tokens_normalize_function(input_data, extra_data):
        """Normalize tokens list response."""
        if not input_data:
            return [], False

        tokens_data = input_data if isinstance(input_data, list) else []
        status = tokens_data is not None

        return tokens_data, status

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
                "chain": self.chain.value,
                "normalize_function": self._get_kline_normalize_function,
            },
        )

        return "GET /kline", {}, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline response."""
        # SushiSwap doesn't have direct kline data in API
        # Return the input data as-is for tests
        if not input_data:
            return [], False

        kline_data = input_data if isinstance(input_data, list) else []
        return kline_data, True

    def get_kline(
        self, symbol: str, period: str, count: int = 20, extra_data=None, **kwargs
    ):
        """Get kline data. SushiSwap API doesn't provide direct kline data."""
        path, params, extra_data = self._get_kline(
            symbol, period, count, extra_data, **kwargs
        )
        return RequestData(
            {"message": "Klines not available via SushiSwap API. Use pool events or external sources."},
            extra_data,
        )

    def async_get_kline(
        self, symbol: str, period: str, count: int = 20, extra_data=None, **kwargs
    ):
        """Async get kline data."""
        path, params, extra_data = self._get_kline(
            symbol, period, count, extra_data, **kwargs
        )
        # No-op: klines not available via SushiSwap API

    # ==================== Standard Trading Interfaces ====================

    def _make_order(self, symbol, volume, price, order_type, offset="open",
                    post_only=False, client_order_id=None, extra_data=None, **kwargs):
        """Prepare order. Returns (path, params, extra_data).

        SushiSwap is a DEX; trading requires on-chain transactions.
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
        return "/swap", params, extra_data

    def make_order(self, symbol, volume, price, order_type, offset="open",
                   post_only=False, client_order_id=None, extra_data=None, **kwargs):
        """Place an order. Note: SushiSwap requires on-chain tx."""
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
