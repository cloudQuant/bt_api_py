"""Module-level docstring."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]
OrderStatus = Literal["new", "submitted", "filled", "cancelled", "rejected"]


class BrokerEvent(StrEnum):
    """Class BrokerEvent"""

    ORDER_UPDATED = "order_updated"
    POSITION_UPDATED = "position_updated"
    ACCOUNT_UPDATED = "account_updated"
    ERROR = "error"
    CONNECTION_LOST = "connection_lost"
    RESYNC_REQUIRED = "resync_required"


@dataclass(slots=True)
class BrokerCapabilities:
    """Class BrokerCapabilities"""

    supports_market_data: bool = True
    supports_order_submit: bool = True
    supports_order_cancel: bool = True
    supports_positions: bool = True
    supports_account: bool = True
    supports_streaming: bool = False
    supports_native_paper: bool = False
    supports_margin: bool = False
    supports_options: bool = False
    supports_destructive_write: bool = False

    def as_dict(self) -> dict[str, bool]:
        """as_dict method"""
        return {name: bool(getattr(self, name)) for name in self.__dataclass_fields__}


@dataclass(slots=True)
class OrderRequest:
    """Class OrderRequest"""

    account_id: str
    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = "market"
    price: float | None = None
    client_order_id: str | None = None
    idempotency_key: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CancelOrderRequest:
    """Class CancelOrderRequest"""

    account_id: str
    order_id: str
    symbol: str | None = None
    idempotency_key: str | None = None


@dataclass(slots=True)
class OrderSnapshot:
    """Class OrderSnapshot"""

    order_id: str
    account_id: str
    symbol: str
    side: OrderSide
    quantity: float
    status: OrderStatus
    order_type: OrderType = "market"
    price: float | None = None
    filled_quantity: float = 0.0
    average_price: float | None = None
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class PositionSnapshot:
    """Class PositionSnapshot"""

    account_id: str
    symbol: str
    quantity: float
    average_price: float
    market_price: float | None = None
    unrealized_pnl: float = 0.0


@dataclass(slots=True)
class AccountSnapshot:
    """Class AccountSnapshot"""

    account_id: str
    currency: str = "CNY"
    cash: float = 0.0
    equity: float = 0.0
    margin_used: float = 0.0
    available_cash: float = 0.0
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
