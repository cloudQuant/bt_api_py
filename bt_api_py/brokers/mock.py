"""Module documentation"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
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


class MockBrokerAdapter(BrokerAdapter):
    """Class MockBrokerAdapter"""
    def __init__(self, account_id: str = "paper", initial_cash: float = 1_000_000.0) -> None:
        """__init__ method"""
        self.account_id = account_id
        self.connected = False
        self.account = AccountSnapshot(
            account_id=account_id,
            cash=initial_cash,
            equity=initial_cash,
            available_cash=initial_cash,
        )
        self.positions: dict[str, PositionSnapshot] = {}
        self.orders: dict[str, OrderSnapshot] = {}
        self.quotes: dict[str, float] = {"RB2510": 3500.0, "IF2510": 4000.0}

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
        return {"connected": self.connected, "adapter": "mock", "account_id": self.account_id}

    def capabilities(self) -> BrokerCapabilities:
        """capabilities method"""
        return BrokerCapabilities(supports_streaming=True, supports_native_paper=True)

    async def list_accounts(self) -> list[AccountSnapshot]:
        """list_accounts method"""
        return [self.account]

    async def get_account(self, account_id: str) -> AccountSnapshot:
        """get_account method"""
        if account_id != self.account_id:
            raise BrokerError(BrokerErrorCode.AUTH_FAILED, "account not found")
        return self.account

    async def list_positions(self, account_id: str) -> list[PositionSnapshot]:
        """list_positions method"""
        await self.get_account(account_id)
        return list(self.positions.values())

    async def list_orders(self, account_id: str) -> list[OrderSnapshot]:
        """list_orders method"""
        await self.get_account(account_id)
        return [order for order in self.orders.values() if order.account_id == account_id]

    async def place_order(self, request: OrderRequest) -> OrderSnapshot:
        """place_order method"""
        await self.get_account(request.account_id)
        if request.quantity <= 0:
            raise BrokerError(BrokerErrorCode.INVALID_ORDER, "quantity must be positive")
        fill_price = float(request.price or self.quotes.get(request.symbol, 100.0))
        cost = fill_price * request.quantity
        signed_quantity = request.quantity if request.side == "buy" else -request.quantity
        if request.side == "buy" and self.account.available_cash < cost:
            raise BrokerError(BrokerErrorCode.INSUFFICIENT_FUNDS, "insufficient cash")
        order = OrderSnapshot(
            order_id=str(uuid.uuid4()),
            account_id=request.account_id,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            status="filled",
            order_type=request.order_type,
            price=request.price,
            filled_quantity=request.quantity,
            average_price=fill_price,
            submitted_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.orders[order.order_id] = order
        position = self.positions.get(request.symbol)
        if position is None:
            self.positions[request.symbol] = PositionSnapshot(
                account_id=request.account_id,
                symbol=request.symbol,
                quantity=signed_quantity,
                average_price=fill_price,
                market_price=fill_price,
            )
        else:
            old_quantity = position.quantity
            old_average = position.average_price
            new_quantity = old_quantity + signed_quantity
            position.quantity = new_quantity
            position.market_price = fill_price
            if new_quantity == 0:
                position.average_price = 0.0
            elif old_quantity == 0 or (old_quantity > 0) != (signed_quantity > 0):
                # 无持仓或方向反转：成本重置为成交价
                position.average_price = fill_price
            elif abs(new_quantity) > abs(old_quantity):
                # 同向加仓：加权平均
                position.average_price = (
                    abs(old_quantity) * old_average + abs(signed_quantity) * fill_price
                ) / abs(new_quantity)
            # 同向减仓：成本价不变（保持 old_average）
        cash_delta = -cost if request.side == "buy" else cost
        self.account.cash += cash_delta
        self.account.available_cash += cash_delta
        self.account.equity = self.account.cash + sum(
            item.quantity * (item.market_price or item.average_price)
            for item in self.positions.values()
        )
        self.account.updated_at = datetime.now(timezone.utc)
        return order

    async def cancel_order(self, request: CancelOrderRequest) -> OrderSnapshot:
        """cancel_order method"""
        await self.get_account(request.account_id)
        order = self.orders.get(request.order_id)
        if order is None:
            raise BrokerError(BrokerErrorCode.ORDER_NOT_FOUND, "order not found")
        if order.status == "filled":
            return order
        order.status = "cancelled"
        order.updated_at = datetime.now(timezone.utc)
        return order

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        """get_quote method"""
        return {"symbol": symbol, "price": self.quotes.get(symbol, 100.0), "provider": "mock"}
