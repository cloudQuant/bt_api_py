"""Neutral contracts for read-only service market bindings.

These checks validate supplied bindings only; they do not prove provider
identity, account authority, freshness, or successful subscription.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from bt_api_base.account_protocol import AccountResourceKey
from bt_api_base.market_protocol import MarketSubscriptionKey
from bt_api_base.service_protocol import WireScope
from .service_market_history import fixed_timeframe_seconds as _timeframe_seconds
from .service_market_history import parse_market_utc as _parse_utc


def check_service_text(value: Any, field: str) -> str:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        raise ValueError(f"{field} must be non-empty printable text")
    value.encode("utf-8", errors="strict")
    return value


def parse_service_decimal(value: Any, field: str) -> Decimal:
    if type(value) is not str or len(value) > 128:
        raise TypeError(f"{field} must be a bounded decimal string")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} is invalid") from None
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


def validate_service_feed_binding(*, market_client, resource, qty_unit, timeframe, quantity_multiplier, history_start_utc, history_end_utc):
    if type(resource) is not AccountResourceKey:
        raise TypeError("resource must be an AccountResourceKey")
    check_service_text(qty_unit, "qty_unit")
    _timeframe_seconds(timeframe)
    if timeframe.endswith("w"):
        raise ValueError(
            "weekly feeds are unsupported without an explicit calendar-week anchor"
        )
    multiplier = parse_service_decimal(quantity_multiplier, "quantity_multiplier")
    if multiplier <= 0:
        raise ValueError("quantity_multiplier must be positive")
    key = getattr(market_client, "key", None)
    if type(key) is not MarketSubscriptionKey:
        raise TypeError("market_client.key must be a MarketSubscriptionKey")
    if type(getattr(market_client, "stream_scope", None)) is not WireScope:
        raise TypeError("market_client.stream_scope must be a WireScope")
    for method in ("start", "history", "poll", "close"):
        if not callable(getattr(market_client, method, None)):
            raise TypeError(f"market_client must provide {method}()")
    if key.channel != "bar" or key.timeframe != timeframe:
        raise ValueError("market client must bind the configured fixed bar timeframe")
    if key.depth is not None:
        raise ValueError("bar feed key must not declare depth")
    if key.instrument.product_type != resource.product_type:
        raise ValueError("feed resource product_type must match its instrument")
    if (history_start_utc is None) != (history_end_utc is None):
        raise ValueError("history_start_utc and history_end_utc must be supplied together")
    if history_start_utc is not None:
        start = _parse_utc(history_start_utc, "history_start_utc")
        end = _parse_utc(history_end_utc, "history_end_utc")
        if start >= end:
            raise ValueError("history start must precede history end")


def validate_service_orderbook_binding(*, market_client, resource, qty_unit, quantity_multiplier, max_depth, max_event_age_seconds):
    if type(resource) is not AccountResourceKey:
        raise TypeError("resource must be an AccountResourceKey")
    check_service_text(qty_unit, "qty_unit")
    if parse_service_decimal(quantity_multiplier, "quantity_multiplier") <= 0:
        raise ValueError("quantity_multiplier must be positive")
    if type(max_depth) is not int or not 1 <= max_depth <= 1000:
        raise ValueError("max_depth must be between 1 and 1000")
    if parse_service_decimal(max_event_age_seconds, "max_event_age_seconds") <= 0:
        raise ValueError("max_event_age_seconds must be positive")
    key = getattr(market_client, "key", None)
    if type(key) is not MarketSubscriptionKey:
        raise TypeError("market_client.key must be a MarketSubscriptionKey")
    if type(getattr(market_client, "stream_scope", None)) is not WireScope:
        raise TypeError("market_client.stream_scope must be a WireScope")
    for method in ("start", "poll", "close"):
        if not callable(getattr(market_client, method, None)):
            raise TypeError(f"market_client must provide {method}()")
    if (
        key.channel != "orderbook"
        or key.timeframe is not None
        or type(key.depth) is not int
        or not 1 <= key.depth <= max_depth
    ):
        raise ValueError("orderbook key requires a bounded explicit depth")
    if key.instrument.product_type != resource.product_type:
        raise ValueError("feed resource product_type must match its instrument")


@dataclass(frozen=True)
class ServiceFeedBinding:
    """Bind a service market client and resource to a fixed bar stream."""

    market_client: Any
    resource: AccountResourceKey
    qty_unit: str
    timeframe: str
    quantity_multiplier: str = "1"
    history_start_utc: str | None = None
    history_end_utc: str | None = None

    def __post_init__(self) -> None:
        validate_service_feed_binding(
            market_client=self.market_client,
            resource=self.resource,
            qty_unit=self.qty_unit,
            timeframe=self.timeframe,
            quantity_multiplier=self.quantity_multiplier,
            history_start_utc=self.history_start_utc,
            history_end_utc=self.history_end_utc,
        )


@dataclass(frozen=True)
class ServiceOrderBookBinding:
    """Bind a service market client and resource to a bounded order book."""

    market_client: Any
    resource: AccountResourceKey
    qty_unit: str
    quantity_multiplier: str = "1"
    max_depth: int = 50
    max_event_age_seconds: str = "2"

    def __post_init__(self) -> None:
        validate_service_orderbook_binding(
            market_client=self.market_client,
            resource=self.resource,
            qty_unit=self.qty_unit,
            quantity_multiplier=self.quantity_multiplier,
            max_depth=self.max_depth,
            max_event_age_seconds=self.max_event_age_seconds,
        )


def validate_service_market_bindings(feeds, *, account_client, coverage):
    """Copy and check client/resource bindings without asserting account authority."""
    if not isinstance(feeds, Mapping) or not feeds:
        raise TypeError("feeds must be a non-empty mapping")
    copied = {}
    market_clients = set()
    for dataname, binding in feeds.items():
        check_service_text(dataname, "dataname")
        if type(binding) not in {ServiceFeedBinding, ServiceOrderBookBinding}:
            raise TypeError(
                "feed mappings require a bar or orderbook binding; tick feeds "
                "need the SDK neutral market event and data-clock contract"
            )
        if id(binding.market_client) in market_clients:
            raise ValueError("each feed requires its own market client")
        market_clients.add(id(binding.market_client))
        if binding.resource not in coverage:
            raise ValueError("feed resource is outside the account client's coverage")
        copied[dataname] = binding
    if id(account_client) in market_clients:
        raise ValueError("account and market service clients must be distinct facades")
    return copied


__all__ = (
    "ServiceFeedBinding",
    "ServiceOrderBookBinding",
    "check_service_text",
    "parse_service_decimal",
    "validate_service_feed_binding",
    "validate_service_market_bindings",
    "validate_service_orderbook_binding",
)
