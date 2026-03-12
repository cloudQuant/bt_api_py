"""
CoW Swap Spot Feed implementation.
CoW Swap is a DEX - market data is primarily obtained through on-chain events and subgraphs.
"""

from typing import Any

from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_cow_swap.request_base import CowSwapRequestData
from bt_api_py.functions.utils import update_extra_data


class CowSwapRequestDataSpot(CowSwapRequestData):
    """CoW Swap Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "COW_SWAP___SPOT")

    # ==================== Token Price (Tick) ====================

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get token native price. Returns (path, params, extra_data)."""
        path = f"GET /api/v1/token/{self._params.get_symbol(symbol)}/native_price"
        extra_data = update_extra_data(
            extra_data,
            request_type="get_tick",
            symbol_name=symbol,
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_tick_normalize_function,
        )
        return path, {}, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize token price response."""
        if not input_data:
            return [], False
        return [input_data], True

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get token price. Returns RequestData."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Async get token price."""
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Depth/Liquidity ====================

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get liquidity depth. Returns (path, params, extra_data).

        Note: CoW Swap is a batch auction DEX without an order book.
        This returns auction data as a proxy.
        """
        extra_data = update_extra_data(
            extra_data,
            request_type="get_depth",
            symbol_name=symbol,
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_depth_normalize_function,
        )
        return "GET /api/v2/solver_competition/latest", {}, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth response."""
        if not input_data:
            return [], False
        return [input_data], True

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get liquidity depth. Returns RequestData."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Async get liquidity depth."""
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Kline ====================

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data. Returns (path, params, extra_data).

        Note: CoW Swap doesn't provide kline data directly.
        """
        extra_data = update_extra_data(
            extra_data,
            request_type="get_kline",
            symbol_name=symbol,
            period=period,
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_kline_normalize_function,
        )
        return "GET /api/v1/version", {}, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline response. CoW Swap doesn't provide klines."""
        return [], True

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data. Returns RequestData.

        Note: CoW Swap doesn't provide kline data.
        """
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def async_get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Async get kline data."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Order Queries (CoW Swap native) ====================

    def get_order(self, order_uid, extra_data=None, **kwargs):
        """Get order information by UID."""
        request_type = "get_order"
        path = f"GET /api/v1/orders/{order_uid}"
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=kwargs.get("symbol", ""),
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_order_normalize_function,
        )
        return self.request(path, params=None, extra_data=extra_data)

    @staticmethod
    def _get_order_normalize_function(input_data, extra_data):
        """Normalize order data from CoW Swap API response."""
        if not input_data:
            return [], False
        return [input_data], True

    def get_order_status(self, order_uid, extra_data=None, **kwargs):
        """Get order status by UID."""
        request_type = "get_order_status"
        path = f"GET /api/v1/orders/{order_uid}/status"
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=kwargs.get("symbol", ""),
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_order_status_normalize_function,
        )
        return self.request(path, params=None, extra_data=extra_data)

    @staticmethod
    def _get_order_status_normalize_function(input_data, extra_data):
        """Normalize order status from CoW Swap API response."""
        if not input_data:
            return [], False
        return [input_data], True

    def get_account_orders(self, owner, extra_data=None, **kwargs):
        """Get orders for an account address."""
        request_type = "get_account_orders"
        path = f"GET /api/v1/account/{owner}/orders"
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=kwargs.get("symbol", ""),
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_account_orders_normalize_function,
        )
        params: dict[str, Any] = {}
        if "offset" in kwargs:
            params["offset"] = kwargs["offset"]
        if "limit" in kwargs:
            params["limit"] = kwargs["limit"]
        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_account_orders_normalize_function(input_data, extra_data):
        """Normalize account orders from CoW Swap API response."""
        if not input_data:
            return [], False
        orders = input_data.get("orders", [])
        return [orders], True

    def get_trades(self, extra_data=None, **kwargs):
        """Get trades information."""
        request_type = "get_trades"
        path = "GET /api/v2/trades"
        extra_data = update_extra_data(
            extra_data,
            request_type=request_type,
            symbol_name=kwargs.get("symbol", ""),
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_trades_normalize_function,
        )
        params: dict[str, Any] = {}
        if "offset" in kwargs:
            params["offset"] = kwargs["offset"]
        if "limit" in kwargs:
            params["limit"] = kwargs["limit"]
        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize trades from CoW Swap API response."""
        if not input_data:
            return [], False
        trades = input_data.get("trades", [])
        return [trades], True

    # ==================== Exchange Info ====================

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information (tokens). Returns (path, params, extra_data)."""
        extra_data = update_extra_data(
            extra_data,
            request_type="get_exchange_info",
            symbol_name="",
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_exchange_info_normalize_function,
        )
        return "GET /api/v1/tokens", {}, extra_data

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information. Returns RequestData."""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info from CoW Swap API response."""
        if not input_data:
            return [], False
        tokens = input_data if isinstance(input_data, list) else [input_data]
        return [tokens], True

    # ==================== Quote ====================

    def _get_quote(self, sell_token, buy_token, amount, extra_data=None, **kwargs):
        """Get quote for a swap. Returns (path, params, extra_data)."""
        extra_data = update_extra_data(
            extra_data,
            request_type="get_quote",
            symbol_name=f"{sell_token}-{buy_token}",
            asset_type=self.asset_type,
            exchange_name=self.exchange_name,
            sell_token=sell_token,
            buy_token=buy_token,
            normalize_function=self._get_quote_normalize_function,
        )
        params = {
            "sellToken": sell_token,
            "buyToken": buy_token,
            "amount": amount,
        }
        return "GET /api/v1/quote", params, extra_data

    @staticmethod
    def _get_quote_normalize_function(input_data, extra_data):
        """Normalize quote from CoW Swap API response."""
        if not input_data:
            return [], False
        quote = input_data.get("quote", input_data)
        return [quote], True

    def get_quote(self, sell_token, buy_token, amount, extra_data=None, **kwargs):
        """Get swap quote. Returns RequestData."""
        path, params, extra_data = self._get_quote(
            sell_token, buy_token, amount, extra_data, **kwargs
        )
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Standard Trading Interfaces ====================
    # Note: CoW Swap is a batch auction DEX. Orders are signed off-chain
    # and submitted to solvers.

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
        """Prepare swap order parameters. Returns (path, body, extra_data).

        For CoW Swap, 'making an order' means submitting a signed order
        to the CoW Protocol order book.
        """
        if extra_data is None:
            extra_data = {}
        sell_token = kwargs.get("sell_token", "")
        buy_token = kwargs.get("buy_token", "")
        if not sell_token and "-" in symbol:
            parts = symbol.split("-", 1)
            sell_token, buy_token = parts[0], parts[1]

        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "make_order",
                "side": kwargs.get("side", "sell"),
                "quantity": volume,
                "price": price,
                "order_type": order_type,
                "sell_token": sell_token,
                "buy_token": buy_token,
            }
        )
        body = {
            "sellToken": sell_token,
            "buyToken": buy_token,
            "sellAmount": str(volume),
            "kind": "sell",
        }
        return "POST /api/v1/orders", body, extra_data

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
        """Place a swap order. Returns RequestData.

        Note: CoW Swap orders require off-chain EIP-712 signing.
        This method prepares the order parameters.
        """
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
        """Prepare cancel order parameters. Returns (path, params, extra_data)."""
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
        return f"DELETE /api/v1/orders/{order_id}", {}, extra_data

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order. Returns RequestData."""
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Prepare query order parameters. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "query_order",
                "order_id": order_id,
                "normalize_function": self._get_order_normalize_function,
            }
        )
        return f"GET /api/v1/orders/{order_id}", {}, extra_data

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order status. Returns RequestData."""
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Prepare get open orders parameters. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_open_orders",
                "normalize_function": self._get_account_orders_normalize_function,
            }
        )
        return "GET /api/v1/orders", {}, extra_data

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders. Returns RequestData."""
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Standard Account Interfaces ====================

    def _get_account(self, symbol=None, extra_data=None, **kwargs):
        """Prepare get account parameters. Returns (path, params, extra_data).

        For CoW Swap DEX, account info is wallet/chain-based.
        """
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_account",
                "chain": self.chain,
            }
        )
        return "GET /api/v1/version", {}, extra_data

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info. Returns RequestData."""
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Prepare get balance parameters. Returns (path, params, extra_data).

        For CoW Swap DEX, balance queries require Web3/on-chain.
        """
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_balance",
                "chain": self.chain,
            }
        )
        return "GET /api/v1/version", {}, extra_data

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get token balance. Returns RequestData.

        Note: DEX balance requires Web3/on-chain query.
        """
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)
