"""Neutral L2 projection for authenticated service market streams.

``ServiceMarketClient`` owns the service wire, source descriptor, queue cursor,
and transport recovery. This module composes that existing owner with the
provider order-book sequence and snapshot state machine. It never depends on a
host framework and it does not infer a provider generation from the service
connection generation.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from bt_api_base.market_protocol import MarketSourceDescriptor, MarketSubscriptionKey

from bt_api_py.service_market import ServiceMarketClient
from bt_api_py.service_market_events import MarketEventCursor, MarketFreshnessStatus
from bt_api_py.service_market_projection import (
    ServiceMarketBootstrapProjection,
    ServiceMarketEventProjection,
    ServiceMarketEventProjector,
)


ORDERBOOK_EVENT_SCHEMA_VERSION = "backtrader.orderbook.snapshot.v1"

_ORDERBOOK_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "complete_state",
        "timestamp",
        "symbol",
        "exchange",
        "asset_type",
        "bids",
        "asks",
        "sequence",
        "previous_sequence",
        "snapshot_or_delta",
        "continuity_status",
        "stale",
        "stale_reason",
        "recovery_snapshot",
    }
)
_HEALTHY_CONTINUITY = frozenset({"continuous", "ok", "recovered", "snapshot"})
_UNHEALTHY_CONTINUITY = frozenset(
    {"checksum_failed", "disconnected", "gap", "out_of_order", "stale"}
)
_CONTINUITY = _HEALTHY_CONTINUITY | _UNHEALTHY_CONTINUITY
_PROJECTION_ISSUER = object()
_BOOTSTRAP_ISSUER = object()


class ServiceMarketOrderBookProjectionError(RuntimeError):
    """Finite, value-free errors from the SDK L2 projection owner."""

    _CODES = frozenset(
        {
            "INVALID_MARKET_CLIENT",
            "SOURCE_DESCRIPTOR_REQUIRED",
            "ORDERBOOK_KEY_INVALID",
            "INVALID_ORDERBOOK_LIMITS",
            "CLIENT_NOT_STARTED",
            "PROJECTOR_CLOSED",
            "SOURCE_DESCRIPTOR_UNAVAILABLE",
            "SOURCE_SCOPE_CHANGED",
            "MARKET_BOOTSTRAP_INVALID",
            "MARKET_STREAM_UNHEALTHY",
            "MARKET_EVENT_INVALID",
            "ORDERBOOK_EVENT_INVALID",
            "ORDERBOOK_INITIAL_DELTA",
            "ORDERBOOK_SEQUENCE_INVALID",
            "ORDERBOOK_SEQUENCE_GAP",
            "ORDERBOOK_PROJECTION_FAILED",
            "WRONG_OWNER_THREAD",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = (
            code
            if type(code) is str and code in self._CODES
            else "ORDERBOOK_PROJECTION_FAILED"
        )
        super().__init__(self.code)


@dataclass(frozen=True, init=False)
class ServiceMarketOrderBookProjection:
    """One validated provider L2 event bound to its exact service source/cursor.

    The service event retains the authenticated ``MarketSourceDescriptor``,
    wire ``MarketEventCursor``, local observation and explicit unavailable
    freshness. Provider sequence is a separate field. The current service wire
    does not publish a provider stream generation, so it is always ``None``.
    """

    market_event: ServiceMarketEventProjection
    timestamp_seconds: Decimal
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    provider_sequence: int
    provider_previous_sequence: int | None
    snapshot_or_delta: Literal["snapshot", "delta"]
    continuity_status: str
    stale: bool
    stale_reason: str
    complete_state: Literal[True]
    recovery_snapshot: bool

    def __init__(
        self,
        *,
        _issuer: object,
        market_event: ServiceMarketEventProjection,
        timestamp_seconds: Decimal,
        bids: tuple[tuple[Decimal, Decimal], ...],
        asks: tuple[tuple[Decimal, Decimal], ...],
        provider_sequence: int,
        provider_previous_sequence: int | None,
        snapshot_or_delta: Literal["snapshot", "delta"],
        continuity_status: str,
        stale: bool,
        stale_reason: str,
        complete_state: Literal[True],
        recovery_snapshot: bool,
    ) -> None:
        if _issuer is not _PROJECTION_ISSUER:
            raise TypeError("ServiceMarketOrderBookProjection is issued by its SDK projector")
        object.__setattr__(self, "market_event", market_event)
        object.__setattr__(self, "timestamp_seconds", timestamp_seconds)
        object.__setattr__(self, "bids", bids)
        object.__setattr__(self, "asks", asks)
        object.__setattr__(self, "provider_sequence", provider_sequence)
        object.__setattr__(self, "provider_previous_sequence", provider_previous_sequence)
        object.__setattr__(self, "snapshot_or_delta", snapshot_or_delta)
        object.__setattr__(self, "continuity_status", continuity_status)
        object.__setattr__(self, "stale", stale)
        object.__setattr__(self, "stale_reason", stale_reason)
        object.__setattr__(self, "complete_state", complete_state)
        object.__setattr__(self, "recovery_snapshot", recovery_snapshot)
        self.__post_init__()

    def __post_init__(self) -> None:
        event = self.market_event
        if type(event) is not ServiceMarketEventProjection:
            raise TypeError("market_event must be an exact SDK market projection")
        if (
            type(event.source) is not MarketSourceDescriptor
            or type(event.cursor) is not MarketEventCursor
        ):
            raise TypeError("market event must retain exact source and cursor facts")
        if event.source.key != event.cursor.key or event.source.epoch != event.cursor.epoch:
            raise ValueError("market event source and cursor differ")
        if event.freshness.status is not MarketFreshnessStatus.UNAVAILABLE:
            raise ValueError("service market freshness is unavailable")
        if type(self.timestamp_seconds) is not Decimal or not self.timestamp_seconds.is_finite():
            raise TypeError("timestamp_seconds must be a finite Decimal")
        if self.timestamp_seconds <= 0:
            raise ValueError("timestamp_seconds must be positive")
        if type(self.bids) is not tuple or type(self.asks) is not tuple:
            raise TypeError("book levels must be immutable tuples")
        for name, levels, descending in (("bids", self.bids, True), ("asks", self.asks, False)):
            previous: Decimal | None = None
            for level in levels:
                if (
                    type(level) is not tuple
                    or len(level) != 2
                    or type(level[0]) is not Decimal
                    or type(level[1]) is not Decimal
                    or not level[0].is_finite()
                    or not level[1].is_finite()
                    or level[0] <= 0
                    or level[1] <= 0
                    or (
                        previous is not None
                        and (
                            (descending and level[0] >= previous)
                            or (not descending and level[0] <= previous)
                        )
                    )
                ):
                    raise ValueError(f"{name} levels are invalid")
                previous = level[0]
        if not self.bids or not self.asks or self.bids[0][0] >= self.asks[0][0]:
            raise ValueError("book top of book is invalid")
        if type(self.provider_sequence) is not int or self.provider_sequence <= 0:
            raise TypeError("provider_sequence must be positive")
        if self.provider_previous_sequence is not None and (
            type(self.provider_previous_sequence) is not int
            or self.provider_previous_sequence < 0
        ):
            raise TypeError("provider_previous_sequence must be non-negative or None")
        if self.snapshot_or_delta not in {"snapshot", "delta"}:
            raise ValueError("snapshot_or_delta is invalid")
        if type(self.continuity_status) is not str or self.continuity_status not in _CONTINUITY:
            raise ValueError("continuity_status is invalid")
        if type(self.stale) is not bool or type(self.recovery_snapshot) is not bool:
            raise TypeError("stale and recovery_snapshot must be booleans")
        if type(self.stale_reason) is not str or len(self.stale_reason) > 128:
            raise TypeError("stale_reason must be bounded text")
        if not self.stale_reason.isprintable():
            raise ValueError("stale_reason must be printable")
        if self.stale != bool(self.stale_reason):
            raise ValueError("stale and stale_reason disagree")
        if self.complete_state is not True:
            raise ValueError("only complete provider states can be projected")
        if self.snapshot_or_delta == "delta" and self.provider_previous_sequence is None:
            raise ValueError("delta requires provider_previous_sequence")
        if self.recovery_snapshot and self.snapshot_or_delta != "snapshot":
            raise ValueError("recovery_snapshot requires a snapshot")

    @property
    def source(self) -> MarketSourceDescriptor:
        return self.market_event.source

    @property
    def cursor(self) -> MarketEventCursor:
        return self.market_event.cursor

    @property
    def key(self) -> MarketSubscriptionKey:
        return self.market_event.key

    @property
    def freshness_status(self) -> MarketFreshnessStatus:
        return self.market_event.freshness.status

    @property
    def provider_stream_generation(self) -> None:
        """No provider generation is present in this service wire contract."""

        return None

    @property
    def observed_at_unix_ns(self) -> int:
        return self.market_event.clock.local_observed_at_unix_ns

    @property
    def observed_at_monotonic_ns(self) -> int:
        return self.market_event.clock.local_observed_at_monotonic_ns


@dataclass(frozen=True)
class ServiceMarketOrderBookBootstrap:
    """SDK start receipt plus its parsed latest event, if one was available."""

    service_bootstrap: ServiceMarketBootstrapProjection
    latest_orderbook: ServiceMarketOrderBookProjection | None

    def __init__(
        self,
        *,
        _issuer: object,
        service_bootstrap: ServiceMarketBootstrapProjection,
        latest_orderbook: ServiceMarketOrderBookProjection | None,
    ) -> None:
        if _issuer is not _BOOTSTRAP_ISSUER:
            raise TypeError("ServiceMarketOrderBookBootstrap is issued by its SDK projector")
        object.__setattr__(self, "service_bootstrap", service_bootstrap)
        object.__setattr__(self, "latest_orderbook", latest_orderbook)
        self.__post_init__()

    def __post_init__(self) -> None:
        bootstrap = self.service_bootstrap
        if type(bootstrap) is not ServiceMarketBootstrapProjection:
            raise TypeError("service_bootstrap must be an exact SDK bootstrap")
        if bootstrap.snapshot_complete or bootstrap.full_baseline_available:
            raise ValueError("service bootstrap must retain its latest-event-only meaning")
        event = bootstrap.latest_snapshot
        if event is None:
            if self.latest_orderbook is not None:
                raise ValueError("latest orderbook has no service bootstrap event")
            return
        projection = self.latest_orderbook
        if (
            type(projection) is not ServiceMarketOrderBookProjection
            or projection.market_event != event
            or projection.source != bootstrap.source
            or projection.cursor.sequence != bootstrap.snapshot_sequence
        ):
            raise ValueError("latest orderbook is not bound to the bootstrap event")

    @property
    def source(self) -> MarketSourceDescriptor:
        return self.service_bootstrap.source

    @property
    def snapshot_kind(self) -> Literal["latest_event"]:
        return "latest_event"

    @property
    def snapshot_complete(self) -> Literal[False]:
        return False

    @property
    def full_baseline_available(self) -> Literal[False]:
        return False

    @property
    def snapshot_sequence(self) -> int:
        return self.service_bootstrap.snapshot_sequence

    @property
    def cursor_sequence(self) -> int:
        return self.service_bootstrap.cursor_sequence

    @property
    def resync_required(self) -> bool:
        return self.service_bootstrap.resync_required


def require_service_orderbook_healthy(projector: Any, *, error_type: type[Exception], code: str) -> None:
    """Require the supplied projector to report a healthy transport state."""
    if (
        projector.healthy is not True
        or projector.transport_recovery_required is not False
    ):
        raise error_type(code)


def validate_service_orderbook_bootstrap(
    projector: Any, bootstrap: Any, *, error_type: type[Exception]
) -> ServiceMarketOrderBookProjection | None:
    """Validate the selected projector's existing bootstrap result."""
    if type(bootstrap) is not ServiceMarketOrderBookBootstrap:
        raise error_type("MARKET_BOOTSTRAP_INVALID")
    if (
        projector.healthy is not True
        or projector.transport_recovery_required is not False
        or bootstrap.resync_required
    ):
        raise error_type("MARKET_START_UNHEALTHY")
    return bootstrap.latest_orderbook


def validate_service_orderbook_projection(
    projection: Any, *, error_type: type[Exception]
) -> tuple[MarketSubscriptionKey, MarketSourceDescriptor]:
    """Validate neutral source facts before a consumer-specific projection."""
    if type(projection) is not ServiceMarketOrderBookProjection:
        raise error_type("ORDERBOOK_PROJECTION_INVALID")
    key = projection.key
    source = projection.source
    if (
        key.channel != "orderbook"
        or key.instrument != source.key.instrument
        or projection.freshness_status.value != "UNAVAILABLE"
        or projection.provider_stream_generation is not None
    ):
        raise error_type("ORDERBOOK_PROJECTION_INVALID")
    return key, source


class ServiceMarketOrderBookProjector:
    """Own L2 sequence/recovery state over one exact authenticated market client."""

    def __init__(
        self,
        market_client: ServiceMarketClient,
        *,
        max_depth: int,
        max_event_age_seconds: str,
    ) -> None:
        if type(market_client) is not ServiceMarketClient:
            raise ServiceMarketOrderBookProjectionError("INVALID_MARKET_CLIENT")
        if getattr(market_client, "_require_source_descriptor", None) is not True:
            raise ServiceMarketOrderBookProjectionError("SOURCE_DESCRIPTOR_REQUIRED")
        key = market_client.key
        if (
            type(key) is not MarketSubscriptionKey
            or key.channel != "orderbook"
            or key.timeframe is not None
            or type(key.depth) is not int
            or type(max_depth) is not int
            or not 1 <= key.depth <= max_depth <= 1000
        ):
            raise ServiceMarketOrderBookProjectionError("ORDERBOOK_KEY_INVALID")
        try:
            max_age = _decimal(max_event_age_seconds)
        except Exception:
            raise ServiceMarketOrderBookProjectionError("INVALID_ORDERBOOK_LIMITS") from None
        if max_age <= 0:
            raise ServiceMarketOrderBookProjectionError("INVALID_ORDERBOOK_LIMITS")
        try:
            event_projector = ServiceMarketEventProjector(market_client)
        except Exception:
            raise ServiceMarketOrderBookProjectionError("INVALID_MARKET_CLIENT") from None
        self._event_projector = event_projector
        self._max_depth = max_depth
        self._max_event_age_seconds = max_age
        self._owner_thread_id = threading.get_ident()
        self._started = False
        self._closed = False
        self._invalidated = False
        self._source: MarketSourceDescriptor | None = None
        self._bootstrap: ServiceMarketOrderBookBootstrap | None = None
        self._last_provider_sequence: int | None = None
        self._orderbook_recovery_required = False

    @property
    def key(self) -> MarketSubscriptionKey:
        return self._event_projector.key

    @property
    def healthy(self) -> bool:
        return not self._closed and not self._invalidated and self._event_projector.healthy

    @property
    def source_descriptor(self) -> MarketSourceDescriptor | None:
        self._assert_owner()
        if not self._started or self._closed:
            return None
        return self._require_active_source()

    @property
    def transport_recovery_required(self) -> bool:
        return self._event_projector.recovery_required

    @property
    def orderbook_recovery_required(self) -> bool:
        return self._orderbook_recovery_required

    @property
    def resync_required(self) -> bool:
        return self._event_projector.resync_required

    def start(self) -> ServiceMarketOrderBookBootstrap:
        self._assert_owner()
        if self._closed:
            raise ServiceMarketOrderBookProjectionError("PROJECTOR_CLOSED")
        if self._invalidated:
            raise ServiceMarketOrderBookProjectionError("SOURCE_SCOPE_CHANGED")
        if self._bootstrap is not None:
            return self._bootstrap
        try:
            bootstrap = self._event_projector.start()
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Exception as error:
            _raise_projection_failure(error)
        if (
            type(bootstrap) is not ServiceMarketBootstrapProjection
            or bootstrap.resync_required
            or bootstrap.snapshot_kind != "latest_event"
            or bootstrap.snapshot_complete
            or bootstrap.full_baseline_available
        ):
            raise ServiceMarketOrderBookProjectionError("MARKET_BOOTSTRAP_INVALID")
        self._source = bootstrap.source
        self._started = True
        latest = None
        if bootstrap.latest_snapshot is not None:
            latest = self._bind_event(bootstrap.latest_snapshot, delivery="latest_snapshot")
        result = ServiceMarketOrderBookBootstrap(
            _issuer=_BOOTSTRAP_ISSUER,
            service_bootstrap=bootstrap,
            latest_orderbook=latest,
        )
        self._bootstrap = result
        return result

    def poll(self, timeout_ms: int = 0) -> ServiceMarketOrderBookProjection | None:
        self._assert_owner()
        if self._closed:
            raise ServiceMarketOrderBookProjectionError("PROJECTOR_CLOSED")
        if not self._started:
            raise ServiceMarketOrderBookProjectionError("CLIENT_NOT_STARTED")
        if self._invalidated:
            raise ServiceMarketOrderBookProjectionError("SOURCE_SCOPE_CHANGED")
        self._require_active_source()
        try:
            event = self._event_projector.poll(timeout_ms)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Exception as error:
            if self._event_projector.recovery_required:
                self._invalidated = True
                raise ServiceMarketOrderBookProjectionError("MARKET_STREAM_UNHEALTHY") from None
            _raise_projection_failure(error)
        if event is None:
            return None
        return self._bind_event(event, delivery="poll")

    def close(self) -> None:
        self._assert_owner()
        if self._closed:
            return
        try:
            self._event_projector.close()
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Exception as error:
            _raise_projection_failure(error)
        self._started = False
        self._closed = True
        self._invalidated = True
        self._source = None
        self._bootstrap = None
        self._last_provider_sequence = None
        self._orderbook_recovery_required = False

    def _bind_event(
        self,
        event: ServiceMarketEventProjection,
        *,
        delivery: Literal["latest_snapshot", "poll"],
    ) -> ServiceMarketOrderBookProjection:
        if type(event) is not ServiceMarketEventProjection:
            raise ServiceMarketOrderBookProjectionError("MARKET_EVENT_INVALID")
        descriptor = self._source
        current = self._event_projector.source_descriptor
        key = self.key
        if (
            type(descriptor) is not MarketSourceDescriptor
            or type(current) is not MarketSourceDescriptor
            or current != descriptor
            or event.source != descriptor
            or event.cursor.key != key
            or event.cursor.stream_id != descriptor.stream_id
            or event.cursor.epoch != descriptor.epoch
            or event.delivery != delivery
        ):
            self._invalidated = True
            raise ServiceMarketOrderBookProjectionError("SOURCE_SCOPE_CHANGED")
        if event.freshness.status is not MarketFreshnessStatus.UNAVAILABLE:
            raise ServiceMarketOrderBookProjectionError("MARKET_EVENT_INVALID")

        data = event.normalized_data
        if not isinstance(data, Mapping):
            raise ServiceMarketOrderBookProjectionError("MARKET_EVENT_INVALID")
        if set(data) != _ORDERBOOK_EVENT_FIELDS:
            raise ServiceMarketOrderBookProjectionError("ORDERBOOK_EVENT_INVALID")
        instrument = key.instrument
        if (
            data.get("schema_version") != ORDERBOOK_EVENT_SCHEMA_VERSION
            or data.get("complete_state") is not True
            or data.get("symbol") != instrument.native_symbol
            or data.get("exchange") != instrument.venue
            or data.get("asset_type") != instrument.product_type
        ):
            raise ServiceMarketOrderBookProjectionError("ORDERBOOK_EVENT_INVALID")

        try:
            timestamp = _decimal(data["timestamp"], positive=True)
            sequence = data["sequence"]
            previous_sequence = data["previous_sequence"]
            snapshot_or_delta = data["snapshot_or_delta"]
            continuity = data["continuity_status"]
            stale_from_provider = data["stale"]
            stale_reason_from_provider = data["stale_reason"]
            recovery_snapshot = data["recovery_snapshot"]
            if (
                type(sequence) is not int
                or sequence <= 0
                or (
                    previous_sequence is not None
                    and (type(previous_sequence) is not int or previous_sequence < 0)
                )
                or snapshot_or_delta not in {"snapshot", "delta"}
                or continuity not in _CONTINUITY
                or type(stale_from_provider) is not bool
                or type(recovery_snapshot) is not bool
                or type(stale_reason_from_provider) is not str
                or len(stale_reason_from_provider) > 128
                or not stale_reason_from_provider.isprintable()
                or (stale_from_provider and not stale_reason_from_provider)
                or (not stale_from_provider and stale_reason_from_provider)
                or (snapshot_or_delta == "delta" and previous_sequence is None)
                or (recovery_snapshot and snapshot_or_delta != "snapshot")
            ):
                raise ValueError
            previous = self._last_provider_sequence
            if previous is None and snapshot_or_delta != "snapshot":
                self._orderbook_recovery_required = True
                raise ServiceMarketOrderBookProjectionError("ORDERBOOK_INITIAL_DELTA")
            if previous is not None:
                if sequence <= previous:
                    self._orderbook_recovery_required = True
                    raise ServiceMarketOrderBookProjectionError("ORDERBOOK_SEQUENCE_INVALID")
                if snapshot_or_delta == "delta" and previous_sequence != previous:
                    self._orderbook_recovery_required = True
                    raise ServiceMarketOrderBookProjectionError("ORDERBOOK_SEQUENCE_GAP")

            bids = _levels(data["bids"], self._max_depth, descending=True)
            asks = _levels(data["asks"], self._max_depth, descending=False)
            if not bids or not asks or bids[0][0] >= asks[0][0]:
                raise ValueError

            wire_sent = _utc(event.clock.wire_sent_at_utc)
            observed = _utc(event.clock.local_observed_at_utc)
            if wire_sent > observed:
                raise ValueError
            delivery_age = _delta_seconds(wire_sent, observed)
            stale = stale_from_provider or delivery_age > self._max_event_age_seconds
            stale_reason = (
                stale_reason_from_provider
                if stale_from_provider
                else "gateway_event_age_exceeded"
                if stale
                else ""
            )
            if continuity in _UNHEALTHY_CONTINUITY:
                stale = True
                stale_reason = stale_reason or continuity

            recovering = self._orderbook_recovery_required
            if recovering and not (
                snapshot_or_delta == "snapshot"
                and continuity in _HEALTHY_CONTINUITY
                and not stale
            ):
                stale = True
                stale_reason = stale_reason or "orderbook_recovery_snapshot_required"

            result = ServiceMarketOrderBookProjection(
                _issuer=_PROJECTION_ISSUER,
                market_event=event,
                timestamp_seconds=timestamp,
                bids=bids,
                asks=asks,
                provider_sequence=sequence,
                provider_previous_sequence=previous_sequence,
                snapshot_or_delta=snapshot_or_delta,
                continuity_status=continuity,
                stale=stale,
                stale_reason=stale_reason,
                complete_state=True,
                recovery_snapshot=recovery_snapshot,
            )
        except ServiceMarketOrderBookProjectionError:
            raise
        except Exception:
            raise ServiceMarketOrderBookProjectionError("ORDERBOOK_EVENT_INVALID") from None

        if stale or continuity in _UNHEALTHY_CONTINUITY:
            self._orderbook_recovery_required = True
        else:
            self._orderbook_recovery_required = False
        self._last_provider_sequence = sequence
        return result

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceMarketOrderBookProjectionError("WRONG_OWNER_THREAD")

    def _require_active_source(self) -> MarketSourceDescriptor:
        current = self._event_projector.source_descriptor
        if (
            type(current) is not MarketSourceDescriptor
            or current != self._source
            or self._invalidated
        ):
            self._invalidated = True
            raise ServiceMarketOrderBookProjectionError("SOURCE_SCOPE_CHANGED")
        return current


def _decimal(value: object, *, positive: bool = False) -> Decimal:
    if type(value) not in (str, int, float, Decimal):
        raise ValueError
    if type(value) is str and len(value) > 128:
        raise ValueError
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError from None
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError
    return result


def _levels(
    value: object,
    depth: int,
    *,
    descending: bool,
) -> tuple[tuple[Decimal, Decimal], ...]:
    if type(value) not in (list, tuple) or len(value) > depth:
        raise ValueError
    levels: list[tuple[Decimal, Decimal]] = []
    previous: Decimal | None = None
    for raw_level in value:
        if type(raw_level) not in (list, tuple) or len(raw_level) != 2:
            raise ValueError
        price = _decimal(raw_level[0], positive=True)
        quantity = _decimal(raw_level[1], positive=True)
        if previous is not None and (
            (descending and price >= previous) or (not descending and price <= previous)
        ):
            raise ValueError
        levels.append((price, quantity))
        previous = price
    return tuple(levels)


def _utc(value: object) -> datetime:
    if type(value) is not str:
        raise ValueError
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise ValueError from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError
    return parsed.astimezone(timezone.utc)


def _delta_seconds(start: datetime, end: datetime) -> Decimal:
    delta = end - start
    whole_seconds = delta.days * 86_400 + delta.seconds
    return Decimal(whole_seconds) + Decimal(delta.microseconds) / Decimal(1_000_000)


def _raise_projection_failure(error: Exception) -> None:
    code = getattr(error, "code", None)
    if type(code) is str and code in ServiceMarketOrderBookProjectionError._CODES:
        raise ServiceMarketOrderBookProjectionError(code) from None
    raise ServiceMarketOrderBookProjectionError("ORDERBOOK_PROJECTION_FAILED") from None


__all__ = [
    "ORDERBOOK_EVENT_SCHEMA_VERSION",
    "ServiceMarketOrderBookBootstrap",
    "ServiceMarketOrderBookProjection",
    "ServiceMarketOrderBookProjectionError",
    "ServiceMarketOrderBookProjector",
    "require_service_orderbook_healthy",
    "validate_service_orderbook_bootstrap",
    "validate_service_orderbook_projection",
]
