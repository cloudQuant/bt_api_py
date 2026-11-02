"""Provider-neutral market event binding for SDK and host-framework clients.

The provider SDK maps and validates native observations before constructing a
``MarketProviderEvent``.  This module binds that normalized event to its
authenticated source descriptor, clock facts, complete subscription cursor,
and typed freshness assessment.  It does not parse service envelopes or
provider payloads and has no dependency on Backtrader or a venue package.

Only the SDK market-client/provider boundary should call
:func:`bind_market_provider_event` from its authenticated start/poll lifecycle.
Host frameworks consume the resulting immutable DTO; they must not supply or
reconstruct its source, clock, cursor, session, or freshness facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from bt_api_base.market_protocol import (
    MarketProviderEvent,
    MarketSourceDescriptor,
    MarketSubscriptionKey,
)


class ServiceMarketEventBindingError(ValueError):
    """A finite rejection for a missing or inconsistent market binding."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class MarketFreshnessStatus(str, Enum):
    """SDK-assessed freshness states carried with one exact market cursor."""

    FRESH = "FRESH"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


def _reject(code: str) -> None:
    raise ServiceMarketEventBindingError(code)


def _text(value: object, code: str) -> None:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        _reject(code)
    try:
        encoded_length = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        _reject(code)
    if encoded_length > 512:
        _reject(code)


def _utc(value: object, code: str) -> datetime:
    _text(value, code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        _reject(code)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        _reject(code)
    return parsed


def _delta_ns(start: datetime, end: datetime) -> int:
    delta = end - start
    return ((delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1_000


@dataclass(frozen=True)
class MarketEventCursor:
    """One contiguous sequence position bound to its complete subscription."""

    key: MarketSubscriptionKey
    stream_id: str
    epoch: str
    sequence: int

    def __post_init__(self) -> None:
        if type(self.key) is not MarketSubscriptionKey:
            _reject("MARKET_EVENT_CURSOR_KEY_INVALID")
        _text(self.stream_id, "MARKET_EVENT_CURSOR_INVALID")
        _text(self.epoch, "MARKET_EVENT_CURSOR_INVALID")
        if type(self.sequence) is not int or self.sequence <= 0:
            _reject("MARKET_EVENT_CURSOR_INVALID")
        if self.stream_id != self.key.stream_id:
            _reject("MARKET_EVENT_CURSOR_KEY_MISMATCH")

    @property
    def subscription_epoch(self) -> str:
        """Return the exact epoch under which this sequence is meaningful."""

        return self.epoch


@dataclass(frozen=True)
class MarketEventClockBinding:
    """Market-source and receive-clock facts for one normalized observation.

    ``clock_source_session_id`` is an identifier, not an account-session or
    generation claim. ``connection_generation`` stays an integer source fact;
    ``stream_generation`` is the provider event's string generation. They are
    separate dimensions and are never coerced or compared with one another.
    """

    source: str
    clock_domain_id: str
    clock_source_session_id: str
    clock_source: str
    connection_generation: int
    stream_generation: str
    event_time_utc: str
    observed_at_utc: str
    received_at_utc: str
    received_monotonic_ns: int

    def __post_init__(self) -> None:
        for value in (
            self.source,
            self.clock_domain_id,
            self.clock_source_session_id,
            self.clock_source,
            self.stream_generation,
        ):
            _text(value, "MARKET_EVENT_CLOCK_BINDING_INVALID")
        if type(self.connection_generation) is not int or self.connection_generation <= 0:
            _reject("MARKET_EVENT_CLOCK_BINDING_INVALID")
        if type(self.received_monotonic_ns) is not int or self.received_monotonic_ns <= 0:
            _reject("MARKET_EVENT_CLOCK_BINDING_INVALID")
        for value in (self.event_time_utc, self.observed_at_utc, self.received_at_utc):
            _utc(value, "MARKET_EVENT_CLOCK_BINDING_INVALID")


@dataclass(frozen=True)
class MarketEventFreshness:
    """Typed SDK freshness result bound to one source, generation, and cursor."""

    status: MarketFreshnessStatus
    source: MarketSourceDescriptor
    clock: MarketEventClockBinding
    cursor: MarketEventCursor
    assessed_at_utc: str
    age_ns: int | None
    max_age_ns: int | None
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not MarketFreshnessStatus:
            _reject("MARKET_FRESHNESS_RESULT_INVALID")
        if type(self.source) is not MarketSourceDescriptor:
            _reject("MARKET_FRESHNESS_RESULT_INVALID")
        if type(self.clock) is not MarketEventClockBinding:
            _reject("MARKET_FRESHNESS_RESULT_INVALID")
        if type(self.cursor) is not MarketEventCursor:
            _reject("MARKET_FRESHNESS_RESULT_INVALID")
        assessed_at = _utc(self.assessed_at_utc, "MARKET_FRESHNESS_RESULT_INVALID")
        if self.status is MarketFreshnessStatus.UNAVAILABLE:
            if self.age_ns is not None or self.max_age_ns is not None:
                _reject("MARKET_FRESHNESS_RESULT_INVALID")
            _text(self.reason, "MARKET_FRESHNESS_RESULT_INVALID")
            return
        if (
            type(self.age_ns) is not int
            or self.age_ns < 0
            or type(self.max_age_ns) is not int
            or self.max_age_ns <= 0
        ):
            _reject("MARKET_FRESHNESS_RESULT_INVALID")
        event_time = _utc(self.clock.event_time_utc, "MARKET_FRESHNESS_RESULT_INVALID")
        received_at = _utc(self.clock.received_at_utc, "MARKET_FRESHNESS_RESULT_INVALID")
        if assessed_at < received_at or assessed_at < event_time:
            _reject("MARKET_FRESHNESS_TIME_ORDER_INVALID")
        if self.age_ns != _delta_ns(event_time, assessed_at):
            _reject("MARKET_FRESHNESS_AGE_MISMATCH")
        if self.status is MarketFreshnessStatus.FRESH:
            if self.age_ns > self.max_age_ns or self.reason is not None:
                _reject("MARKET_FRESHNESS_RESULT_INVALID")
        elif self.age_ns <= self.max_age_ns:
            _reject("MARKET_FRESHNESS_RESULT_INVALID")
        else:
            _text(self.reason, "MARKET_FRESHNESS_RESULT_INVALID")


@dataclass(frozen=True)
class BoundMarketProviderEvent:
    """Normalized market event with complete SDK source and clock provenance."""

    event: MarketProviderEvent
    source: MarketSourceDescriptor
    cursor: MarketEventCursor
    clock: MarketEventClockBinding
    freshness: MarketEventFreshness

    def __post_init__(self) -> None:
        _validate_bound_market_provider_event(
            self.event,
            self.source,
            self.cursor,
            self.clock,
            self.freshness,
        )

    @property
    def subscription_epoch(self) -> str:
        """Return the active source epoch attached to this event."""

        return self.cursor.subscription_epoch

    @property
    def normalized_data(self) -> Any:
        """Return the provider-normalized payload without venue interpretation."""

        return self.event.data


def _validate_bound_market_provider_event(
    event: object,
    source: object,
    cursor: object,
    clock: object,
    freshness: object,
) -> None:
    if type(event) is not MarketProviderEvent:
        _reject("MARKET_PROVIDER_EVENT_INVALID")
    if type(source) is not MarketSourceDescriptor:
        _reject("MARKET_SOURCE_DESCRIPTOR_UNAVAILABLE")
    if type(cursor) is not MarketEventCursor:
        _reject("MARKET_EVENT_CURSOR_INVALID")
    if type(clock) is not MarketEventClockBinding:
        _reject("MARKET_EVENT_CLOCK_BINDING_UNAVAILABLE")
    if type(freshness) is not MarketEventFreshness:
        _reject("MARKET_FRESHNESS_RESULT_UNAVAILABLE")

    if (
        cursor.key != source.key
        or cursor.stream_id != source.stream_id
        or cursor.epoch != source.epoch
    ):
        _reject("MARKET_EVENT_SOURCE_CURSOR_MISMATCH")
    if (
        clock.source != source.source
        or clock.clock_domain_id != source.clock_domain_id
        or clock.clock_source_session_id != source.clock_source_session_id
        or clock.clock_source != source.clock_source
        or clock.connection_generation != source.connection_generation
    ):
        _reject("MARKET_EVENT_CLOCK_SOURCE_MISMATCH")
    if (
        type(event.generation) is not str
        or event.generation != clock.stream_generation
        or event.observed_at_utc != clock.observed_at_utc
    ):
        _reject("MARKET_EVENT_GENERATION_OR_TIME_MISMATCH")
    if (
        freshness.source != source
        or freshness.clock != clock
        or freshness.cursor != cursor
    ):
        _reject("MARKET_FRESHNESS_BINDING_MISMATCH")
    if freshness.status is MarketFreshnessStatus.UNAVAILABLE:
        _reject("MARKET_FRESHNESS_UNAVAILABLE")


def bind_market_provider_event(
    event: MarketProviderEvent,
    source: MarketSourceDescriptor,
    cursor: MarketEventCursor,
    clock: MarketEventClockBinding,
    freshness: MarketEventFreshness,
) -> BoundMarketProviderEvent:
    """Bind one SDK-normalized event to authenticated source and quality facts.

    The SDK market-client/provider boundary supplies every argument after its
    authenticated subscription and event validation. Missing source, clock,
    cursor, or a usable freshness assessment is rejected; there is no inferred
    source and no stale-to-fresh fallback. Provider adapters must normalize
    ``event.data`` before this call and must not pass native venue payloads.
    """

    return BoundMarketProviderEvent(
        event=event,
        source=source,
        cursor=cursor,
        clock=clock,
        freshness=freshness,
    )


__all__ = [
    "BoundMarketProviderEvent",
    "MarketEventClockBinding",
    "MarketEventCursor",
    "MarketEventFreshness",
    "MarketFreshnessStatus",
    "ServiceMarketEventBindingError",
    "bind_market_provider_event",
]
