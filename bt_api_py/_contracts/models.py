"""v1 BtApi typed request/result domain model.

These are frozen, Decimal-based dataclasses and StrEnum values consumed by
``BtApi``. They carry no network behaviour and are not a second trading client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class Consistency(StrEnum):
    LIVE = "live"
    CACHE_OK = "cache_ok"


class TransportMode(StrEnum):
    DIRECT = "direct"
    ZMQ = "zmq"


@dataclass(frozen=True)
class Freshness:
    source: str  # live | cache | replay | legacy_float_conversion
    observed_at: datetime
    stale: bool = False
    stale_reason: str | None = None


@dataclass(frozen=True)
class ForwardingConfig:
    command_endpoint: str
    market_endpoint: str
    private_endpoint: str
    account_id: str
    strategy_id: str


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    account_id: str
    client_order_id: str
    price: Decimal | None = None
    time_in_force: str = "GTC"
    reduce_only: bool = False
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must be a non-empty string")
        if not isinstance(self.side, Side):
            raise TypeError("side must be a Side enum")
        if not isinstance(self.order_type, OrderType):
            raise TypeError("order_type must be an OrderType enum")
        if not isinstance(self.quantity, Decimal):
            raise TypeError("quantity must be a Decimal")
        if self.quantity <= 0:
            raise ValueError("quantity must be > 0")
        if self.price is not None and not isinstance(self.price, Decimal):
            raise TypeError("price must be a Decimal or None")
        if not self.account_id:
            raise ValueError("account_id must be a non-empty string")
        if not self.client_order_id:
            raise ValueError("client_order_id must be a non-empty string")
        if self.order_type is OrderType.LIMIT and self.price is None:
            raise ValueError("limit order requires a price")
        if self.order_type is OrderType.MARKET and self.price is not None:
            raise ValueError("market order must not carry a price")


@dataclass(frozen=True)
class CancelOrderRequest:
    symbol: str
    account_id: str
    order_id: str | None = None
    client_order_id: str | None = None
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must be a non-empty string")
        if not self.account_id:
            raise ValueError("account_id must be a non-empty string")
        if not self.order_id and not self.client_order_id:
            raise ValueError("order_id or client_order_id must be provided")


@dataclass(frozen=True)
class CancelAllRequest:
    account_id: str
    symbol: str | None = None
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        if not self.account_id:
            raise ValueError("account_id must be a non-empty string")


@dataclass(frozen=True)
class QueryOrderRequest:
    symbol: str
    account_id: str
    order_id: str | None = None
    client_order_id: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must be a non-empty string")
        if not self.account_id:
            raise ValueError("account_id must be a non-empty string")
        if not self.order_id and not self.client_order_id:
            raise ValueError("order_id or client_order_id must be provided")


@dataclass(frozen=True)
class SubscribeRequest:
    exchange_name: str
    symbols: list[str]
    topics: list[str]
    account_id: str | None = None


@dataclass(frozen=True)
class TickerSnapshot:
    id: str
    symbol: str
    last_price: Decimal | None
    freshness: Freshness
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DepthSnapshot:
    id: str
    symbol: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    freshness: Freshness
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KlineSnapshot:
    id: str
    symbol: str
    period: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    freshness: Freshness
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AccountSnapshot:
    id: str
    account_id: str
    currency: str
    cash: Decimal
    equity: Decimal
    margin_used: Decimal
    available_cash: Decimal
    freshness: Freshness
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BalanceSnapshot:
    id: str
    account_id: str
    currency: str
    available: Decimal
    frozen: Decimal
    freshness: Freshness
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionSnapshot:
    id: str
    account_id: str
    symbol: str
    quantity: Decimal
    average_price: Decimal
    freshness: Freshness
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderSnapshot:
    id: str
    order_id: str
    account_id: str
    symbol: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    status: str
    price: Decimal | None
    filled_quantity: Decimal
    freshness: Freshness
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FillSnapshot:
    id: str
    fill_id: str
    order_id: str
    account_id: str
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    fee: Decimal
    freshness: Freshness
    raw: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "AccountSnapshot",
    "BalanceSnapshot",
    "CancelAllRequest",
    "CancelOrderRequest",
    "Consistency",
    "DepthSnapshot",
    "FillSnapshot",
    "ForwardingConfig",
    "Freshness",
    "KlineSnapshot",
    "OrderRequest",
    "OrderSnapshot",
    "OrderType",
    "PositionSnapshot",
    "QueryOrderRequest",
    "Side",
    "SubscribeRequest",
    "TickerSnapshot",
    "TransportMode",
]
