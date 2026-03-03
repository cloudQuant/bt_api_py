"""
CoW Swap Spot Feed implementation.
CoW Swap is a DEX - market data is primarily obtained through on-chain events and subgraphs.
"""

from bt_api_py.containers.exchanges.cow_swap_exchange_data import CowSwapExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_cow_swap.request_base import CowSwapRequestData


class CowSwapRequestDataSpot(CowSwapRequestData):
    """CoW Swap Spot Feed for market data."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "COW_SWAP___SPOT")

    def get_order(self, order_uid, extra_data=None, **kwargs):
        """Get order information by UID."""
        request_type = "get_order"
        path = f"GET /api/v1/orders/{order_uid}"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_order_normalize_function,
        })
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
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_order_status_normalize_function,
        })
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
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_account_orders_normalize_function,
        })
        # Add pagination params if provided
        params = {}
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
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_trades_normalize_function,
        })
        # Add pagination params if provided
        params = {}
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

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information (tokens)."""
        request_type = "get_exchange_info"
        path = "GET /api/v1/tokens"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_exchange_info_normalize_function,
        })
        return self.request(path, params=None, extra_data=extra_data)

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info from CoW Swap API response."""
        if not input_data:
            return [], False
        tokens = input_data if isinstance(input_data, list) else [input_data]
        return [tokens], True

    def _get_quote(self, sell_token, buy_token, amount, extra_data=None, **kwargs):
        """Get quote for a swap."""
        request_type = "get_quote"
        path = "GET /api/v1/quote"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_quote_normalize_function,
            "sell_token": sell_token,
            "buy_token": buy_token,
        })
        params = {
            "sellToken": sell_token,
            "buyToken": buy_token,
            "amount": amount,
        }
        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_quote_normalize_function(input_data, extra_data):
        """Normalize quote from CoW Swap API response."""
        if not input_data:
            return [], False
        quote = input_data.get("quote", input_data)
        return [quote], True
