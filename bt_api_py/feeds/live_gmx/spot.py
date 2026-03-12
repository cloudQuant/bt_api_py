"""
GMX Spot Feed implementation.

GMX is a decentralized perpetual exchange. This implementation uses the
REST API for oracle/pricing data on supported chains.
"""

from typing import Any

from bt_api_py.containers.exchanges.gmx_exchange_data import (
    GmxChain,
    GmxExchangeDataSpot,
)
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_gmx.request_base import GmxRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class GmxRequestDataSpot(GmxRequestData):
    """GMX Spot Feed for market data.

    Supports:
    - Getting token prices and tickers
    - Getting candlestick data
    - Getting market information
    - Getting signed prices for on-chain verification
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
        self.exchange_name = kwargs.get("exchange_name", "GMX___DEX")
        self.asset_type = kwargs.get("asset_type", "SPOT")

        # Get chain parameter
        chain_value = kwargs.get("chain", GmxChain.ARBITRUM)
        if isinstance(chain_value, str):
            try:
                self.chain = GmxChain(chain_value)
            except ValueError:
                self.chain = GmxChain.ARBITRUM
        else:
            self.chain = chain_value

        self._params = GmxExchangeDataSpot(self.chain)
        self.request_logger = get_logger("gmx_feed")

    # ==================== Ticker/Price Queries ====================

    def _get_tick(self, symbol: str, extra_data=None, **kwargs) -> tuple[str, dict[str, Any], Any]:
        """Get token price/ticker.

        Args:
            symbol: Token symbol (e.g., "BTC", "ETH")
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_tick"
        path = self._params.get_rest_path("get_tick")

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
                "normalize_function": self._get_tick_normalize_function,
            },
        )

        # GMX tickers endpoint returns all tokens
        return path, {}, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker response from GMX API.

        GMX returns tickers as a dict with token symbols as keys and prices as values.
        Format: {"BTC": {"minPrice": "...", "maxPrice": "...", ...}, ...}
        """
        if not input_data:
            return [], False

        tickers = input_data if isinstance(input_data, dict) else {}
        status = bool(tickers)

        return [tickers], status

    def get_tick(self, symbol: str, extra_data=None, **kwargs):
        """Get token price/ticker for a specific symbol.

        Args:
            symbol: Token symbol (e.g., "BTC", "ETH")
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with token price data
        """
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        response = self.request(path, params, extra_data=extra_data)

        # Filter for specific symbol if provided
        if symbol and response.data:
            tickers = response.data
            if isinstance(tickers, dict) and symbol in tickers:
                response.data = {symbol: tickers[symbol]}

        return response

    def async_get_tick(self, symbol: str, extra_data=None, **kwargs):
        """Async get token price/ticker."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Candlestick/Kline Data ====================

    def _get_kline(
        self, symbol: str, period: str, count: int = 1000, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Get candlestick/kline data.

        Args:
            symbol: Token symbol (e.g., "BTC", "ETH")
            period: Time period (1m, 5m, 15m, 1h, 4h, 1d)
            count: Number of candles to return (max 10000)
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_kline"
        path = self._params.get_rest_path("get_candles")

        # Map period to GMX format
        period_map = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "1h": "1h",
            "4h": "4h",
            "1d": "1d",
        }
        gmx_period = period_map.get(period, "1h")

        params = {
            "tokenSymbol": symbol,
            "period": gmx_period,
            "limit": min(count, 10000),  # GMX max is 10000
        }

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

        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline response from GMX API.

        GMX returns:
        {
            "period": "1d",
            "candles": [
                [timestamp, open, high, low, close],
                ...
            ]
        }
        """
        if not input_data:
            return [], False

        candles = input_data.get("candles", []) if isinstance(input_data, dict) else []
        status = "candles" in input_data if isinstance(input_data, dict) else False

        return [candles], status

    def get_kline(self, symbol: str, period: str, count: int = 1000, extra_data=None, **kwargs):
        """Get candlestick/kline data.

        Args:
            symbol: Token symbol
            period: Time period
            count: Number of candles
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with kline data
        """
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def async_get_kline(
        self, symbol: str, period: str, count: int = 1000, extra_data=None, **kwargs
    ):
        """Async get candlestick/kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Exchange/Market Info ====================

    def _get_exchange_info(self, extra_data=None, **kwargs) -> tuple[str, dict[str, Any], Any]:
        """Get exchange/market information.

        Args:
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_exchange_info"
        path = self._params.get_rest_path("get_markets")

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
                "normalize_function": self._get_exchange_info_normalize_function,
            },
        )

        return path, {}, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info response from GMX API."""
        if not input_data:
            return [], False

        markets = input_data if isinstance(input_data, list) else []
        status = bool(markets)

        return [markets], status

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange/market information.

        Returns available markets and their info.

        Args:
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with market information
        """
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def get_markets_info(self, extra_data=None, **kwargs):
        """Get detailed market information including liquidity and open interest.

        Args:
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with detailed market information
        """
        path = self._params.get_rest_path("get_markets_info")

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_markets_info",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
                "normalize_function": self._get_markets_info_normalize_function,
            },
        )

        return self.request(path, {}, extra_data=extra_data)

    @staticmethod
    def _get_markets_info_normalize_function(input_data, extra_data):
        """Normalize detailed markets info response."""
        if not input_data:
            return [], False

        markets = input_data if isinstance(input_data, list) else []
        status = bool(markets)

        return [markets], status

    # ==================== Tokens ====================

    def get_tokens(self, extra_data=None, **kwargs):
        """Get list of supported tokens.

        Args:
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with list of supported tokens
        """
        path = self._params.get_rest_path("get_tokens")

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_tokens",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
                "normalize_function": self._get_tokens_normalize_function,
            },
        )

        return self.request(path, {}, extra_data=extra_data)

    @staticmethod
    def _get_tokens_normalize_function(input_data, extra_data):
        """Normalize tokens response."""
        if not input_data:
            return [], False

        tokens = input_data if isinstance(input_data, list) else []
        status = bool(tokens)

        return [tokens], status

    # ==================== Signed Prices ====================

    def _get_signed_prices(self, extra_data=None, **kwargs) -> tuple[str, dict[str, Any], Any]:
        """Get latest signed prices for on-chain verification.

        Args:
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_signed_prices"
        path = self._params.get_rest_path("get_signed_prices")

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "chain": self.chain.value,
                "normalize_function": self._get_signed_prices_normalize_function,
            },
        )

        return path, {}, extra_data

    def get_signed_prices(self, extra_data=None, **kwargs):
        """Get latest signed prices for on-chain verification.

        Args:
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with signed prices
        """
        path, params, extra_data = self._get_signed_prices(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    @staticmethod
    def _get_signed_prices_normalize_function(input_data, extra_data):
        """Normalize signed prices response."""
        if not input_data:
            return [], False

        prices = input_data if isinstance(input_data, dict) else {}
        status = bool(prices)

        return [prices], status

    # ==================== Depth/Liquidity ====================

    def _get_depth(
        self, symbol: str, count: int = 20, extra_data=None, **kwargs
    ) -> tuple[str, dict[str, Any], Any]:
        """Get liquidity depth.

        Note: GMX is an AMM-based DEX, so traditional order book depth
        is not applicable. This returns market information instead.

        Args:
            symbol: Token symbol
            count: Number of levels (not applicable for AMM)
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

        # Use markets_info for liquidity information
        path = self._params.get_rest_path("get_markets_info")

        return path, {}, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth response.

        For GMX (an AMM), this returns market liquidity info instead of
        traditional order book depth.
        """
        if not input_data:
            return [], False

        markets = input_data if isinstance(input_data, list) else []
        status = bool(markets)

        return [markets], status

    def get_depth(self, symbol: str, count: int = 20, extra_data=None, **kwargs):
        """Get liquidity depth.

        Note: GMX is an AMM, so this returns market liquidity info.
        Consider using get_markets_info() for detailed liquidity data.
        """
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def async_get_depth(self, symbol: str, count: int = 20, extra_data=None, **kwargs):
        """Async get liquidity depth."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Standard Trading Interfaces ====================

    def _make_order(
        self,
        symbol,
        volume,
        price,
        order_type,
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """Prepare order. Returns (path, body, extra_data).

        GMX is a DEX; trading requires on-chain transactions.
        """
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "make_order",
                "quantity": volume,
                "price": price,
                "order_type": order_type,
                "chain": self.chain.value,
            }
        )
        body = {
            "market": symbol,
            "size": str(volume),
            "price": str(price),
            "orderType": order_type,
        }
        return "POST /orders", body, extra_data

    def make_order(
        self,
        symbol,
        volume,
        price,
        order_type,
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """Place an order. Note: GMX requires on-chain tx."""
        path, body, extra_data = self._make_order(
            symbol,
            volume,
            price,
            order_type,
            offset,
            post_only,
            client_order_id,
            extra_data,
            **kwargs,
        )
        return self.request(path, body=body, extra_data=extra_data)

    def _cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "cancel_order",
                "order_id": order_id,
            }
        )
        return f"DELETE /orders/{order_id}", {}, extra_data

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order."""
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "query_order",
                "order_id": order_id,
            }
        )
        return f"GET /orders/{order_id}", {}, extra_data

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order status."""
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_open_orders",
            }
        )
        return "GET /orders", {}, extra_data

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders."""
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Standard Account Interfaces ====================

    def _get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_account",
                "chain": self.chain.value,
            }
        )
        return self._params.get_rest_path("get_markets"), {}, extra_data

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info."""
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get balance. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_balance",
                "chain": self.chain.value,
            }
        )
        return self._params.get_rest_path("get_markets"), {}, extra_data

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get token balance. Note: DEX balance requires Web3/on-chain query."""
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
