"""Module-level docstring."""
from __future__ import annotations

from typing import Any

from bt_api_py.brokers.base import BrokerAdapter
from bt_api_py.brokers.errors import BrokerError, BrokerErrorCode
from bt_api_py.brokers.types import (
    AccountSnapshot,
    BrokerCapabilities,
    CancelOrderRequest,
    OrderRequest,
    OrderSnapshot,
    PositionSnapshot,
)


class GatewayBridgeAdapter(BrokerAdapter):
    """Class GatewayBridgeAdapter"""
    def __init__(self, gateway_service: Any | None = None, *, account_id: str = "gateway") -> None:
        """__init__ method"""
        self.gateway_service = gateway_service or {}
        self.account_id = account_id
        self.connected = False

    async def connect(self) -> bool:
        """connect method"""
        self.connected = True
        return True

    async def disconnect(self) -> bool:
        """disconnect method"""
        self.connected = False
        return True

    async def health(self) -> dict[str, Any]:
        """health method"""
        return {
            "connected": self.connected,
            "adapter": "gateway_bridge",
            "gateway": self.gateway_service,
        }

    def capabilities(self) -> BrokerCapabilities:
        """capabilities method"""
        return BrokerCapabilities(
            supports_streaming=False,
            supports_native_paper=False,
            supports_order_submit=False,
            supports_order_cancel=False,
            supports_destructive_write=False,
        )

    async def list_accounts(self) -> list[AccountSnapshot]:
        """list_accounts method"""
        return [
            AccountSnapshot(account_id=self.account_id, cash=0.0, equity=0.0, available_cash=0.0)
        ]

    async def get_account(self, account_id: str) -> AccountSnapshot:
        """get_account method"""
        return AccountSnapshot(account_id=account_id, cash=0.0, equity=0.0, available_cash=0.0)

    async def list_positions(self, account_id: str) -> list[PositionSnapshot]:
        """list_positions method"""
        return []

    async def list_orders(self, account_id: str) -> list[OrderSnapshot]:
        """list_orders method"""
        return []

    async def place_order(self, request: OrderRequest) -> OrderSnapshot:
        """place_order method"""
        raise BrokerError(
            BrokerErrorCode.NOT_SUPPORTED, "gateway bridge order submit is not implemented"
        )

    async def cancel_order(self, request: CancelOrderRequest) -> OrderSnapshot:
        """cancel_order method"""
        raise BrokerError(
            BrokerErrorCode.NOT_SUPPORTED, "gateway bridge order cancel is not implemented"
        )

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        """get_quote method"""
        return {"symbol": symbol, "price": 0.0, "provider": "gateway_bridge"}
