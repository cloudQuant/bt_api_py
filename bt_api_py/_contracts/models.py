"""v1 BtApi typed request/result domain model.

These are frozen, Decimal-based dataclasses and StrEnum values consumed by
``BtApi``. They carry no network behaviour and are not a second trading client.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
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


def _require_decimal(name: str, value: Decimal | None, *, positive: bool = False) -> None:
    """Validate Decimal contract fields without accepting implicit float coercion."""
    if value is None:
        return
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal or None")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if positive and value <= 0:
        raise ValueError(f"{name} must be > 0")


@dataclass(frozen=True)
class InstrumentSpec:
    """Venue-independent quantity and price rules for one instrument."""

    exchange_name: str
    symbol: str
    asset_type: str
    base_currency: str
    quote_currency: str
    contract_type: str
    linear: bool
    contract_value: Decimal | None
    contract_multiplier: Decimal | None
    price_tick: Decimal | None
    quantity_step: Decimal | None
    min_quantity: Decimal | None
    max_quantity: Decimal | None
    min_notional: Decimal | None
    quantity_unit: str
    status: str
    freshness: Freshness
    raw_rule_fingerprint: str
    source: str = "exchange"
    available: bool = True
    unavailable_reason: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.exchange_name or not self.symbol:
            raise ValueError("exchange_name and symbol are required")
        if self.quantity_unit not in {"base", "contracts", "lots", "native"}:
            raise ValueError("quantity_unit must be base, contracts, lots or native")
        for name in (
            "contract_value",
            "contract_multiplier",
            "price_tick",
            "quantity_step",
            "min_quantity",
            "max_quantity",
        ):
            _require_decimal(name, getattr(self, name), positive=True)
        if self.available and any(
            getattr(self, name) is None
            for name in (
                "contract_value",
                "contract_multiplier",
                "price_tick",
                "quantity_step",
                "min_quantity",
            )
        ):
            raise ValueError("available InstrumentSpec requires complete numeric rules")
        _require_decimal("min_notional", self.min_notional)
        if self.min_notional is not None and self.min_notional < 0:
            raise ValueError("min_notional must be >= 0")
        if self.available and (not self.raw_rule_fingerprint or self.unavailable_reason):
            raise ValueError("available InstrumentSpec requires a fingerprint and no reason")
        if not self.available and not self.unavailable_reason:
            raise ValueError("unavailable InstrumentSpec requires unavailable_reason")

    @staticmethod
    def _decimal(name: str, value: Decimal) -> Decimal:
        _require_decimal(name, value)
        return value

    def _require_available(self) -> None:
        if not self.available:
            raise ValueError(f"instrument_spec_unavailable:{self.unavailable_reason}")
        if self.status.lower() not in {"live", "trading", "enabled"}:
            raise ValueError(f"instrument_not_tradable:{self.status}")

    def _base_per_native(self) -> Decimal:
        self._require_available()
        if self.quantity_unit == "base":
            return Decimal(1)
        if self.quantity_unit == "contracts" and not self.linear:
            raise ValueError("inverse_contract_conversion_requires_price")
        assert self.contract_value is not None
        assert self.contract_multiplier is not None
        return self.contract_value * self.contract_multiplier

    def base_to_native_quantity(self, base_qty: Decimal) -> Decimal:
        base_qty = self._decimal("base_qty", base_qty)
        if base_qty < 0:
            raise ValueError("base_qty must be >= 0")
        return base_qty / self._base_per_native()

    def native_to_base_quantity(self, native_qty: Decimal) -> Decimal:
        native_qty = self._decimal("native_qty", native_qty)
        if native_qty < 0:
            raise ValueError("native_qty must be >= 0")
        return native_qty * self._base_per_native()

    def quantize_quantity(self, qty: Decimal, rounding: str = "floor") -> Decimal:
        self._require_available()
        qty = self._decimal("qty", qty)
        if qty < 0:
            raise ValueError("qty must be >= 0")
        modes = {
            "floor": ROUND_FLOOR,
            "ceil": ROUND_CEILING,
            "nearest": ROUND_HALF_EVEN,
        }
        try:
            mode = modes[rounding]
        except KeyError as exc:
            raise ValueError("rounding must be floor, ceil or nearest") from exc
        assert self.quantity_step is not None
        units = (qty / self.quantity_step).to_integral_value(rounding=mode)
        return units * self.quantity_step

    def quantize_price(
        self,
        price: Decimal,
        side: Side | str,
        aggressiveness: str = "passive",
    ) -> Decimal:
        self._require_available()
        price = self._decimal("price", price)
        if price <= 0:
            raise ValueError("price must be > 0")
        side = Side(side)
        if aggressiveness not in {"passive", "aggressive", "nearest"}:
            raise ValueError("aggressiveness must be passive, aggressive or nearest")
        if aggressiveness == "nearest":
            mode = ROUND_HALF_EVEN
        else:
            buy_up = aggressiveness == "aggressive"
            mode = ROUND_CEILING if (side is Side.BUY) == buy_up else ROUND_FLOOR
        assert self.price_tick is not None
        units = (price / self.price_tick).to_integral_value(rounding=mode)
        return units * self.price_tick

    def validate_order_quantity(self, qty: Decimal, price: Decimal | None = None) -> None:
        self._require_available()
        qty = self._decimal("qty", qty)
        if qty <= 0:
            raise ValueError("quantity_must_be_positive")
        if self.quantize_quantity(qty) != qty:
            raise ValueError("quantity_not_on_step")
        assert self.min_quantity is not None
        if qty < self.min_quantity:
            raise ValueError("quantity_below_minimum")
        if self.max_quantity is not None and qty > self.max_quantity:
            raise ValueError("quantity_above_maximum")
        if self.min_notional is not None and self.min_notional > 0:
            if price is None:
                raise ValueError("price_required_for_min_notional")
            price = self._decimal("price", price)
            if price <= 0:
                raise ValueError("price_must_be_positive")
            if self.native_to_base_quantity(qty) * price < self.min_notional:
                raise ValueError("notional_below_minimum")


@dataclass(frozen=True)
class FeeSchedule:
    exchange_name: str
    symbol: str
    account_id: str
    maker_rate: Decimal | None
    taker_rate: Decimal | None
    currency: str | None
    source: str
    freshness: Freshness
    available: bool = True
    unavailable_reason: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_decimal("maker_rate", self.maker_rate)
        _require_decimal("taker_rate", self.taker_rate)
        if self.available and (self.maker_rate is None or self.taker_rate is None):
            raise ValueError("available FeeSchedule requires maker and taker rates")
        if not self.available and not self.unavailable_reason:
            raise ValueError("unavailable FeeSchedule requires unavailable_reason")


@dataclass(frozen=True)
class FundingSnapshot:
    exchange_name: str
    symbol: str
    rate: Decimal | None
    next_funding_time: datetime | None
    settlement_interval_seconds: int | None
    source: str
    freshness: Freshness
    available: bool = True
    unavailable_reason: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_decimal("rate", self.rate)
        if self.next_funding_time is not None:
            if not isinstance(self.next_funding_time, datetime):
                raise TypeError("next_funding_time must be a datetime or None")
            if self.next_funding_time.utcoffset() is None:
                raise ValueError("next_funding_time must be timezone-aware")
            object.__setattr__(
                self,
                "next_funding_time",
                self.next_funding_time.astimezone(UTC),
            )
        if self.settlement_interval_seconds is not None and (
            isinstance(self.settlement_interval_seconds, bool)
            or not isinstance(self.settlement_interval_seconds, int)
        ):
            raise TypeError("settlement_interval_seconds must be an int or None")
        if self.settlement_interval_seconds is not None and self.settlement_interval_seconds <= 0:
            raise ValueError("settlement_interval_seconds must be > 0")
        if self.available and (
            self.rate is None
            or self.next_funding_time is None
            or self.settlement_interval_seconds is None
        ):
            raise ValueError("available FundingSnapshot requires a complete funding schedule")
        if self.available:
            if self.freshness.observed_at.utcoffset() is None:
                raise ValueError("freshness.observed_at must be timezone-aware")
            if self.freshness.stale:
                raise ValueError("available FundingSnapshot requires non-stale freshness")
            if self.next_funding_time <= self.freshness.observed_at:
                raise ValueError("next_funding_time must be strictly after freshness.observed_at")
        if not self.available and not self.unavailable_reason:
            raise ValueError("unavailable FundingSnapshot requires unavailable_reason")

    @property
    def next_funding_epoch(self) -> Decimal:
        """Return the canonical UTC settlement instant as a Decimal epoch.

        Cross-venue decision code compares normalized time values without
        converting a typed funding contract back into a venue-specific mapping.
        """

        if self.next_funding_time is None:
            raise ValueError("funding_snapshot_incomplete")
        return Decimal(str(self.next_funding_time.timestamp()))

    def as_dict(self) -> dict[str, Any]:
        """Return a report-safe representation of this public contract."""

        return {
            "exchange_name": self.exchange_name,
            "symbol": self.symbol,
            "rate": None if self.rate is None else str(self.rate),
            "next_funding_epoch": (
                None if self.next_funding_time is None else str(self.next_funding_epoch)
            ),
            "settlement_interval_seconds": self.settlement_interval_seconds,
            "source": self.source,
            "observed_at": self.freshness.observed_at.isoformat(),
            "stale": self.freshness.stale,
            "stale_reason": self.freshness.stale_reason,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True)
class TradingReadiness:
    exchange_name: str
    symbol: str
    account_id: str
    environment: str
    can_trade: bool | None
    position_mode: str | None
    instrument_status: str | None
    max_size: Decimal | None
    leverage: Decimal | None
    margin_mode: str | None
    definite_failure: bool
    blocked_reasons: tuple[str, ...]
    source: str
    freshness: Freshness
    available: bool = True
    unavailable_reason: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_decimal("max_size", self.max_size)
        _require_decimal("leverage", self.leverage)
        if self.position_mode not in {None, "net", "dual_side"}:
            raise ValueError("position_mode must be net or dual_side")
        if not self.available and not self.unavailable_reason:
            raise ValueError("unavailable TradingReadiness requires unavailable_reason")

    @property
    def ready(self) -> bool:
        return bool(
            self.available
            and self.can_trade is True
            and not self.blocked_reasons
            and str(self.instrument_status or "").lower() in {"live", "trading", "enabled"}
        )

    @property
    def reasons(self) -> tuple[str, ...]:
        """Compatibility spelling used by the legacy readiness dictionary."""
        return self.blocked_reasons


@dataclass(frozen=True)
class PositionModeUpdate:
    """Verified result of an account-wide perpetual position-mode change."""

    exchange_name: str
    requested_mode: str
    position_mode: str
    acknowledged: bool
    verified: bool
    cache_updated: bool
    source: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.exchange_name:
            raise ValueError("exchange_name must be a non-empty string")
        if self.requested_mode not in {"net", "dual_side"}:
            raise ValueError("requested_mode must be net or dual_side")
        if self.position_mode not in {"net", "dual_side"}:
            raise ValueError("position_mode must be net or dual_side")
        if not isinstance(self.acknowledged, bool):
            raise TypeError("acknowledged must be a bool")
        if not isinstance(self.verified, bool):
            raise TypeError("verified must be a bool")
        if not isinstance(self.cache_updated, bool):
            raise TypeError("cache_updated must be a bool")
        if not self.acknowledged or not self.verified or not self.cache_updated:
            raise ValueError("PositionModeUpdate only represents verified success")
        if self.position_mode != self.requested_mode:
            raise ValueError("position_mode must match requested_mode")
        if not self.source:
            raise ValueError("source must be a non-empty string")
        if not isinstance(self.observed_at, datetime):
            raise TypeError("observed_at must be a datetime")
        if self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))


@dataclass(frozen=True)
class ForwardingConfig:
    command_endpoint: str
    market_endpoint: str
    private_endpoint: str
    account_id: str
    strategy_id: str
    market_read_timeout_ms: int = 250
    max_cache_age_ms: int = 5_000

    def __post_init__(self) -> None:
        if self.market_read_timeout_ms < 0:
            raise ValueError("market_read_timeout_ms must be non-negative")
        if self.max_cache_age_ms < 0:
            raise ValueError("max_cache_age_ms must be non-negative")


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
    quantity_unit: str = "base"
    position_side: str | None = None
    offset: str | None = None
    position_id: str | None = None
    position_mode: str | None = None
    exchange_id: str | None = None
    execution_cycle_id: str | None = None
    execution_role: str | None = None
    strategy_identity_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must be a non-empty string")
        if not isinstance(self.side, Side):
            raise TypeError("side must be a Side enum")
        if not isinstance(self.order_type, OrderType):
            raise TypeError("order_type must be an OrderType enum")
        if not isinstance(self.quantity, Decimal):
            raise TypeError("quantity must be a Decimal")
        if not self.quantity.is_finite() or self.quantity <= 0:
            raise ValueError("quantity must be > 0")
        if self.price is not None and not isinstance(self.price, Decimal):
            raise TypeError("price must be a Decimal or None")
        if self.price is not None and (not self.price.is_finite() or self.price <= 0):
            raise ValueError("price must be finite and > 0")
        if self.quantity_unit not in {"base", "contracts", "lots", "native"}:
            raise ValueError("quantity_unit must be base, contracts, lots or native")
        if self.offset not in {None, "open", "close", "close_today", "close_yesterday"}:
            raise ValueError("offset must be open, close, close_today or close_yesterday")
        if self.position_mode not in {None, "net", "dual_side"}:
            raise ValueError("position_mode must be net or dual_side")
        if not self.account_id:
            raise ValueError("account_id must be a non-empty string")
        if not self.client_order_id:
            raise ValueError("client_order_id must be a non-empty string")
        if self.execution_cycle_id is not None and (
            not isinstance(self.execution_cycle_id, str)
            or not self.execution_cycle_id.strip()
            or self.execution_cycle_id != self.execution_cycle_id.strip()
            or len(self.execution_cycle_id) > 128
        ):
            raise ValueError("execution_cycle_id must be a bounded non-empty string or None")
        if self.execution_role not in {None, "entry", "exit", "recovery_exit"}:
            raise ValueError("execution_role must be entry, exit, recovery_exit or None")
        if self.strategy_identity_sha256 is not None and (
            not isinstance(self.strategy_identity_sha256, str)
            or len(self.strategy_identity_sha256) != 64
            or self.strategy_identity_sha256 != self.strategy_identity_sha256.lower()
            or any(
                character not in "0123456789abcdef" for character in self.strategy_identity_sha256
            )
        ):
            raise ValueError(
                "strategy_identity_sha256 must be a lowercase SHA-256 hex digest or None"
            )
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
    exchange_id: str | None = None
    front_id: int | None = None
    session_id: int | None = None
    order_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must be a non-empty string")
        if not self.account_id:
            raise ValueError("account_id must be a non-empty string")
        if not self.order_id and not self.client_order_id and not self.order_ref:
            raise ValueError("order_id, client_order_id or order_ref must be provided")


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
    exchange_id: str | None = None
    front_id: int | None = None
    session_id: int | None = None
    order_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must be a non-empty string")
        if not self.account_id:
            raise ValueError("account_id must be a non-empty string")
        if not self.order_id and not self.client_order_id and not self.order_ref:
            raise ValueError("order_id, client_order_id or order_ref must be provided")


@dataclass(frozen=True)
class CommandStatus:
    """A bounded forwarding-router receipt used after an unknown command result."""

    command_id: str
    idempotency_key: str
    status: str  # pending | succeeded | failed | expired
    account_id: str
    strategy_id: str
    accepted: bool | None = None
    order_id: str | None = None
    reason: str = ""
    expires_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


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
    exchange_time: datetime | None = None
    received_wall_time: datetime | None = None
    received_monotonic_ns: int | None = None
    clock_domain_id: str | None = None
    sequence: int | None = None
    previous_sequence: int | None = None
    snapshot_or_delta: str = "snapshot"
    continuity_status: str = "unverified"
    stale: bool = False
    stale_reason: str | None = None
    event_id: str = ""
    coalesced_count: int = 0

    def __post_init__(self) -> None:
        if self.snapshot_or_delta not in {"snapshot", "delta"}:
            raise ValueError("snapshot_or_delta must be snapshot or delta")
        if self.continuity_status not in {
            "continuous",
            "snapshot",
            "duplicate",
            "out_of_order",
            "gap",
            "checksum_failed",
            "unverified",
        }:
            raise ValueError("invalid continuity_status")
        if self.received_monotonic_ns is not None and self.received_monotonic_ns < 0:
            raise ValueError("received_monotonic_ns must be non-negative")
        if self.coalesced_count < 0:
            raise ValueError("coalesced_count must be non-negative")


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
    "CommandStatus",
    "Consistency",
    "DepthSnapshot",
    "FillSnapshot",
    "FeeSchedule",
    "ForwardingConfig",
    "Freshness",
    "FundingSnapshot",
    "InstrumentSpec",
    "KlineSnapshot",
    "OrderRequest",
    "OrderSnapshot",
    "OrderType",
    "PositionModeUpdate",
    "PositionSnapshot",
    "QueryOrderRequest",
    "Side",
    "SubscribeRequest",
    "TickerSnapshot",
    "TransportMode",
    "TradingReadiness",
]
