"""Project authenticated ``ServiceMarketClient`` frames into neutral SDK facts.

The gateway currently publishes a ``WireEnvelope`` that carries the complete
subscription key, stream epoch, sequence, and wire ``sent_at_utc`` value.  Its
provider acquisition generation is intentionally kept server-side, and the
public market frame has no generic event-time or source-clock-calibration
contract.  This module therefore exposes only facts that the authenticated
client can verify and marks freshness unavailable until those missing facts
are carried by a reviewed SDK contract.

The wrapper owns the start/poll call path.  It does not accept an application
constructed provider event, source descriptor, cursor, clock, or freshness
claim.  In particular, it does not adapt a consumer ``WireEnvelope`` back into
``MarketProviderEvent``: doing so would invent the provider generation that
the gateway intentionally does not publish.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from bt_api_base.market_protocol import (
    MARKET_EVENT_OPERATION,
    MARKET_EVENT_SCHEMA,
    MARKET_EVENT_SCHEMA_VERSION,
    MarketSourceDescriptor,
    MarketSubscriptionKey,
)
from bt_api_base.service_protocol import WireEnvelope, validate_event

from bt_api_py.service_market import MarketBootstrap, ServiceMarketClient
from bt_api_py.service_market_events import MarketEventCursor, MarketFreshnessStatus


class ServiceMarketProjectionError(RuntimeError):
    """Finite failures while preserving the authenticated market lifecycle."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ServiceMarketClockObservation:
    """Clock values observed by the SDK projection around one client call.

    ``wire_sent_at_utc`` is kept under its protocol name.  The projection does
    not relabel it as an exchange event time.  The local values are sampled
    after ``start`` or ``poll`` returns, so they are delivery observations, not
    transport-arrival timestamps.  Source clock identifiers come only from
    the active source descriptor; no calibration claim is inferred from them.
    """

    source: MarketSourceDescriptor
    wire_sent_at_utc: str
    local_observed_at_utc: str
    local_observed_at_unix_ns: int
    local_observed_at_monotonic_ns: int
    observation_point: Literal["after_start_return", "after_poll_return", "after_resync_return"]
    event_time_utc: None = None
    provider_stream_generation: None = None
    clock_calibration: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"

    def __post_init__(self) -> None:
        if type(self.source) is not MarketSourceDescriptor:
            raise ServiceMarketProjectionError("SOURCE_DESCRIPTOR_UNAVAILABLE")
        if type(self.wire_sent_at_utc) is not str or not self.wire_sent_at_utc:
            raise ServiceMarketProjectionError("WIRE_TIMESTAMP_UNAVAILABLE")
        if type(self.local_observed_at_utc) is not str or not self.local_observed_at_utc:
            raise ServiceMarketProjectionError("LOCAL_CLOCK_INVALID")
        if type(self.local_observed_at_unix_ns) is not int or self.local_observed_at_unix_ns <= 0:
            raise ServiceMarketProjectionError("LOCAL_CLOCK_INVALID")
        if (
            type(self.local_observed_at_monotonic_ns) is not int
            or self.local_observed_at_monotonic_ns <= 0
        ):
            raise ServiceMarketProjectionError("LOCAL_CLOCK_INVALID")
        if type(self.observation_point) is not str or self.observation_point not in {
            "after_start_return",
            "after_poll_return",
            "after_resync_return",
        }:
            raise ServiceMarketProjectionError("LOCAL_CLOCK_INVALID")
        if self.event_time_utc is not None or self.provider_stream_generation is not None:
            raise ServiceMarketProjectionError("UNSUPPORTED_SYNTHETIC_CLOCK_FACT")
        if self.clock_calibration != "NOT_PROVIDED":
            raise ServiceMarketProjectionError("UNSUPPORTED_CLOCK_CALIBRATION_CLAIM")

        _require_utc_timestamp(self.wire_sent_at_utc, "WIRE_TIMESTAMP_INVALID")
        _require_utc_timestamp(self.local_observed_at_utc, "LOCAL_CLOCK_INVALID")

    @property
    def clock_domain_id(self) -> str:
        return self.source.clock_domain_id

    @property
    def clock_source_session_id(self) -> str:
        return self.source.clock_source_session_id

    @property
    def clock_source(self) -> str:
        return self.source.clock_source

    @property
    def connection_generation(self) -> int:
        return self.source.connection_generation


@dataclass(frozen=True)
class ServiceMarketFreshnessProjection:
    """Explicit unavailable freshness result for the current wire contract."""

    source: MarketSourceDescriptor
    cursor: MarketEventCursor
    clock: ServiceMarketClockObservation
    status: MarketFreshnessStatus
    reason_codes: tuple[str, ...]
    age_ns: None = None
    max_age_ns: None = None

    def __post_init__(self) -> None:
        if type(self.source) is not MarketSourceDescriptor:
            raise ServiceMarketProjectionError("FRESHNESS_SOURCE_UNAVAILABLE")
        if type(self.cursor) is not MarketEventCursor:
            raise ServiceMarketProjectionError("FRESHNESS_CURSOR_UNAVAILABLE")
        if type(self.clock) is not ServiceMarketClockObservation:
            raise ServiceMarketProjectionError("FRESHNESS_CLOCK_UNAVAILABLE")
        if (
            self.cursor.key != self.source.key
            or self.cursor.stream_id != self.source.stream_id
            or self.cursor.epoch != self.source.epoch
            or self.clock.source != self.source
        ):
            raise ServiceMarketProjectionError("FRESHNESS_BINDING_MISMATCH")
        if type(self.status) is not MarketFreshnessStatus or (
            self.status is not MarketFreshnessStatus.UNAVAILABLE
        ):
            raise ServiceMarketProjectionError("FRESHNESS_NOT_PROVEN")
        if type(self.reason_codes) is not tuple or not self.reason_codes:
            raise ServiceMarketProjectionError("FRESHNESS_REASON_REQUIRED")
        if any(type(value) is not str or not value for value in self.reason_codes):
            raise ServiceMarketProjectionError("FRESHNESS_REASON_INVALID")
        if self.age_ns is not None or self.max_age_ns is not None:
            raise ServiceMarketProjectionError("FRESHNESS_NOT_PROVEN")


_CURRENT_FRESHNESS_GAPS = (
    "GENERIC_EVENT_TIME_NOT_BOUND",
    "SOURCE_CLOCK_CALIBRATION_NOT_PROVIDED",
)


@dataclass(frozen=True)
class ServiceMarketEventProjection:
    """One exact SDK-delivered frame with authenticated source and cursor facts."""

    frame: WireEnvelope
    source: MarketSourceDescriptor
    cursor: MarketEventCursor
    clock: ServiceMarketClockObservation
    freshness: ServiceMarketFreshnessProjection
    delivery: Literal["latest_snapshot", "poll"]

    def __post_init__(self) -> None:
        if type(self.frame) is not WireEnvelope:
            raise ServiceMarketProjectionError("INVALID_MARKET_FRAME")
        if type(self.source) is not MarketSourceDescriptor:
            raise ServiceMarketProjectionError("SOURCE_DESCRIPTOR_UNAVAILABLE")
        if type(self.cursor) is not MarketEventCursor:
            raise ServiceMarketProjectionError("MARKET_CURSOR_INVALID")
        if type(self.clock) is not ServiceMarketClockObservation:
            raise ServiceMarketProjectionError("CLOCK_OBSERVATION_INVALID")
        if type(self.freshness) is not ServiceMarketFreshnessProjection:
            raise ServiceMarketProjectionError("FRESHNESS_PROJECTION_INVALID")
        if type(self.delivery) is not str or self.delivery not in {"latest_snapshot", "poll"}:
            raise ServiceMarketProjectionError("INVALID_MARKET_DELIVERY_KIND")
        header = self.frame.header
        if (
            self.cursor.key != self.source.key
            or self.cursor.stream_id != self.source.stream_id
            or self.cursor.epoch != self.source.epoch
            or header.stream_id != self.cursor.stream_id
            or header.stream_epoch != self.cursor.epoch
            or header.sequence != self.cursor.sequence
            or self.clock.source != self.source
        ):
            raise ServiceMarketProjectionError("MARKET_FACT_BINDING_MISMATCH")
        if self.freshness.status is not MarketFreshnessStatus.UNAVAILABLE:
            raise ServiceMarketProjectionError("FRESHNESS_NOT_PROVEN")
        if (
            self.freshness.source != self.source
            or self.freshness.cursor != self.cursor
            or self.freshness.clock != self.clock
        ):
            raise ServiceMarketProjectionError("FRESHNESS_BINDING_MISMATCH")

    @property
    def key(self) -> MarketSubscriptionKey:
        return self.cursor.key

    @property
    def normalized_data(self) -> Mapping[str, Any]:
        """Return the provider-neutral data mapping already validated by the SDK."""

        return self.frame.payload["data"]

    @property
    def header(self) -> Any:
        """Expose the original validated wire header without copying it."""

        return self.frame.header

    @property
    def payload(self) -> Mapping[str, Any]:
        """Expose the original immutable wire payload without rebuilding it."""

        return self.frame.payload

    @property
    def event_time_utc(self) -> None:
        """The current wire contract does not supply a generic event-time fact."""

        return None

    @property
    def provider_stream_generation(self) -> None:
        """The producer validates this generation but intentionally does not publish it."""

        return None


@dataclass(frozen=True)
class ServiceMarketBootstrapProjection:
    """Authenticated start result; latest-event snapshots remain incomplete."""

    stream_id: str
    snapshot_epoch: str
    snapshot_sequence: int
    cursor_epoch: str | None
    cursor_sequence: int
    source: MarketSourceDescriptor
    latest_snapshot: ServiceMarketEventProjection | None
    snapshot_kind: Literal["latest_event"] = "latest_event"
    snapshot_complete: Literal[False] = False
    full_baseline_available: Literal[False] = False
    resync_required: bool = False

    @property
    def snapshot(self) -> ServiceMarketEventProjection | None:
        """Return the latest-event projection, which is never a full baseline."""

        return self.latest_snapshot

    def __post_init__(self) -> None:
        if type(self.source) is not MarketSourceDescriptor:
            raise ServiceMarketProjectionError("SOURCE_DESCRIPTOR_UNAVAILABLE")
        if self.stream_id != self.source.stream_id:
            raise ServiceMarketProjectionError("BOOTSTRAP_SOURCE_MISMATCH")
        if self.snapshot_epoch != self.source.epoch:
            raise ServiceMarketProjectionError("BOOTSTRAP_EPOCH_MISMATCH")
        if type(self.snapshot_sequence) is not int or self.snapshot_sequence < 0:
            raise ServiceMarketProjectionError("BOOTSTRAP_CURSOR_INVALID")
        if self.cursor_epoch is not None and type(self.cursor_epoch) is not str:
            raise ServiceMarketProjectionError("BOOTSTRAP_CURSOR_INVALID")
        if self.cursor_epoch != self.source.epoch:
            raise ServiceMarketProjectionError("BOOTSTRAP_EPOCH_MISMATCH")
        if type(self.cursor_sequence) is not int or self.cursor_sequence < 0:
            raise ServiceMarketProjectionError("BOOTSTRAP_CURSOR_INVALID")
        if type(self.snapshot_kind) is not str or self.snapshot_kind != "latest_event":
            raise ServiceMarketProjectionError("BOOTSTRAP_COMPLETENESS_CLAIM_INVALID")
        if self.snapshot_complete is not False:
            raise ServiceMarketProjectionError("BOOTSTRAP_COMPLETENESS_CLAIM_INVALID")
        if self.full_baseline_available is not False:
            raise ServiceMarketProjectionError("BOOTSTRAP_COMPLETENESS_CLAIM_INVALID")
        if type(self.resync_required) is not bool:
            raise ServiceMarketProjectionError("BOOTSTRAP_RESYNC_STATE_INVALID")
        snapshot = self.latest_snapshot
        if snapshot is None:
            if self.snapshot_sequence != 0:
                raise ServiceMarketProjectionError("BOOTSTRAP_SNAPSHOT_MISSING")
        elif (
            type(snapshot) is not ServiceMarketEventProjection
            or snapshot.delivery != "latest_snapshot"
            or snapshot.source != self.source
            or snapshot.cursor.sequence != self.snapshot_sequence
            or snapshot.cursor.epoch != self.snapshot_epoch
        ):
            raise ServiceMarketProjectionError("BOOTSTRAP_SNAPSHOT_MISMATCH")


class ServiceMarketEventProjector:
    """Read-only adapter around one source-descriptor-required market client.

    The wrapped client's own authenticated start and poll implementations stay
    the authority for binding identity, wire validation, epoch recovery, and
    queue sequencing. This layer rechecks their outputs before exposing one
    provider-neutral projection. It never accepts caller-supplied facts.
    """

    def __init__(self, market_client: ServiceMarketClient) -> None:
        if type(market_client) is not ServiceMarketClient:
            raise ServiceMarketProjectionError("INVALID_MARKET_CLIENT")
        # The descriptor is an authenticated subscribe-reply fact only when
        # ServiceMarketClient was explicitly configured to require it.
        if getattr(market_client, "_require_source_descriptor", None) is not True:
            raise ServiceMarketProjectionError("SOURCE_DESCRIPTOR_REQUIRED")
        self._market_client = market_client
        self._owner_thread_id = threading.get_ident()
        self._started = False
        self._invalidated = False
        self._cursor_epoch: str | None = None
        self._cursor_sequence = 0
        self._bootstrap_projection: ServiceMarketBootstrapProjection | None = None

    @property
    def healthy(self) -> bool:
        return self._started and not self._invalidated and self._market_client.healthy

    @property
    def key(self) -> MarketSubscriptionKey:
        """Return the fixed key bound to the wrapped SDK subscription."""

        return self._market_client.key

    @property
    def stream_scope(self) -> Any:
        """Return the public wire scope bound to the wrapped SDK subscription."""

        return self._market_client.stream_scope

    @property
    def source_descriptor(self) -> MarketSourceDescriptor | None:
        """Return the client's current authenticated descriptor, if still active."""

        return self._market_client.source_descriptor

    @property
    def recovery_required(self) -> bool:
        return self._invalidated or self._market_client.recovery_required

    @property
    def resync_required(self) -> bool:
        return self._market_client.resync_required

    @property
    def last_bootstrap(self) -> ServiceMarketBootstrapProjection | None:
        return self._bootstrap_projection

    @property
    def freshness_available(self) -> bool:
        """Return false: this wire contract has no certified event-time basis."""

        return False

    def start(self) -> ServiceMarketBootstrapProjection:
        """Run the authenticated client start and project its exact bootstrap."""

        self._assert_owner()
        if self._bootstrap_projection is not None:
            return self._bootstrap_projection
        try:
            bootstrap = self._market_client.start()
        except BaseException:
            self._invalidated = True
            raise
        self._started = True
        self._cursor_epoch = bootstrap.cursor_epoch or bootstrap.snapshot_epoch
        self._cursor_sequence = bootstrap.cursor_sequence
        self._invalidated = bootstrap.resync_required
        try:
            projected = self._project_bootstrap(bootstrap, "after_start_return")
        except BaseException:
            self._invalidated = True
            raise
        self._bootstrap_projection = projected
        if projected.resync_required:
            self._invalidated = True
        return projected

    def poll(self, timeout_ms: int = 0) -> ServiceMarketEventProjection | None:
        """Return one authenticated event with explicit unavailable freshness."""

        self._assert_owner()
        if not self._started:
            raise ServiceMarketProjectionError("CLIENT_NOT_STARTED")
        if self._invalidated:
            code = (
                "RESYNC_REQUIRED"
                if self._market_client.resync_required
                else "PROJECTION_INVALIDATED"
            )
            raise ServiceMarketProjectionError(code)
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= 60_000:
            raise ServiceMarketProjectionError("INVALID_POLL_TIMEOUT")
        try:
            frame = self._market_client.poll(timeout_ms)
        except BaseException:
            self._invalidated = True
            raise
        if frame is None:
            return None
        wall_ns = time.time_ns()
        monotonic_ns = time.monotonic_ns()
        try:
            projection = self._project_frame(
                frame,
                delivery="poll",
                observation_point="after_poll_return",
                wall_ns=wall_ns,
                monotonic_ns=monotonic_ns,
            )
        except BaseException:
            self._invalidated = True
            raise
        if (
            self._cursor_epoch is None
            or projection.cursor.epoch != self._cursor_epoch
            or projection.cursor.sequence != self._cursor_sequence + 1
        ):
            self._invalidated = True
            raise ServiceMarketProjectionError("MARKET_CURSOR_DISCONTINUITY")
        self._cursor_sequence = projection.cursor.sequence
        return projection

    def accept_resync(self, *, acknowledge_data_loss: bool) -> ServiceMarketBootstrapProjection:
        """Forward the client's explicit resync acknowledgement and rebind cursor."""

        self._assert_owner()
        if not self._started:
            raise ServiceMarketProjectionError("CLIENT_NOT_STARTED")
        if type(acknowledge_data_loss) is not bool or not acknowledge_data_loss:
            raise ServiceMarketProjectionError("RESYNC_ACKNOWLEDGEMENT_REQUIRED")
        try:
            bootstrap = self._market_client.accept_resync(
                acknowledge_data_loss=acknowledge_data_loss
            )
        except BaseException:
            self._invalidated = True
            raise
        wall_ns = time.time_ns()
        monotonic_ns = time.monotonic_ns()
        try:
            projected = self._project_bootstrap(
                bootstrap,
                "after_resync_return",
                wall_ns=wall_ns,
                monotonic_ns=monotonic_ns,
            )
        except BaseException:
            self._invalidated = True
            raise
        self._cursor_epoch = bootstrap.cursor_epoch or bootstrap.snapshot_epoch
        self._cursor_sequence = bootstrap.cursor_sequence
        self._invalidated = projected.resync_required
        self._bootstrap_projection = projected
        return projected

    def close(self) -> None:
        """Close through the original client lifecycle without hiding uncertainty."""

        self._assert_owner()
        try:
            self._market_client.close()
        except BaseException:
            self._invalidated = True
            raise
        self._started = False
        self._invalidated = True
        self._cursor_epoch = None
        self._cursor_sequence = 0
        self._bootstrap_projection = None

    def history(self, request: Any) -> Any:
        """Delegate the exact bounded history request to the wrapped SDK client."""

        self._assert_owner()
        return self._market_client.history(request)

    def _project_bootstrap(
        self,
        bootstrap: MarketBootstrap,
        observation_point: Literal["after_start_return", "after_resync_return"],
        *,
        wall_ns: int | None = None,
        monotonic_ns: int | None = None,
    ) -> ServiceMarketBootstrapProjection:
        if type(bootstrap) is not MarketBootstrap:
            raise ServiceMarketProjectionError("INVALID_MARKET_BOOTSTRAP")
        if bootstrap.resync_required:
            raise ServiceMarketProjectionError("RESYNC_REQUIRED")
        source = self._active_source_descriptor()
        if bootstrap.source_descriptor != source:
            raise ServiceMarketProjectionError("BOOTSTRAP_SOURCE_MISMATCH")
        if (
            bootstrap.stream_id != source.stream_id
            or bootstrap.snapshot_epoch != source.epoch
            or bootstrap.cursor_epoch != source.epoch
        ):
            raise ServiceMarketProjectionError("BOOTSTRAP_EPOCH_MISMATCH")

        snapshot_projection = None
        if bootstrap.snapshot is not None:
            if wall_ns is None:
                wall_ns = time.time_ns()
            if monotonic_ns is None:
                monotonic_ns = time.monotonic_ns()
            snapshot_projection = self._project_frame(
                bootstrap.snapshot,
                delivery="latest_snapshot",
                observation_point=observation_point,
                wall_ns=wall_ns,
                monotonic_ns=monotonic_ns,
                source=source,
            )
        return ServiceMarketBootstrapProjection(
            stream_id=bootstrap.stream_id,
            snapshot_epoch=bootstrap.snapshot_epoch,
            snapshot_sequence=bootstrap.snapshot_sequence,
            cursor_epoch=bootstrap.cursor_epoch,
            cursor_sequence=bootstrap.cursor_sequence,
            source=source,
            latest_snapshot=snapshot_projection,
            snapshot_kind=bootstrap.snapshot_kind,
            snapshot_complete=bootstrap.snapshot_complete,
            full_baseline_available=bootstrap.full_baseline_available,
            resync_required=bootstrap.resync_required,
        )

    def _project_frame(
        self,
        frame: WireEnvelope,
        *,
        delivery: Literal["latest_snapshot", "poll"],
        observation_point: Literal[
            "after_start_return", "after_poll_return", "after_resync_return"
        ],
        wall_ns: int,
        monotonic_ns: int,
        source: MarketSourceDescriptor | None = None,
    ) -> ServiceMarketEventProjection:
        if type(frame) is not WireEnvelope:
            raise ServiceMarketProjectionError("INVALID_MARKET_FRAME")
        client = self._market_client
        binding = getattr(client, "_binding", None)
        if binding is None or not client.healthy:
            raise ServiceMarketProjectionError("AUTHENTICATED_MARKET_BINDING_UNAVAILABLE")
        if source is None:
            source = self._active_source_descriptor()

        try:
            validate_event(
                frame,
                expected_scope=client.stream_scope,
                expected_stream_id=client.key.stream_id,
                expected_stream_epoch=source.epoch,
            )
            key = MarketSubscriptionKey.from_dict(frame.payload["key"])
        except Exception:
            raise ServiceMarketProjectionError("INVALID_MARKET_FRAME") from None
        data = frame.payload.get("data")
        header = frame.header
        if (
            header.identity != binding.peer
            or header.operation != MARKET_EVENT_OPERATION
            or header.schema_id != MARKET_EVENT_SCHEMA
            or header.schema_version != MARKET_EVENT_SCHEMA_VERSION
            or set(frame.payload) != {"key", "data"}
            or not isinstance(data, Mapping)
            or any(type(name) is not str for name in data)
            or key != client.key
            or source.key != key
            or header.stream_id != source.stream_id
            or header.stream_epoch != source.epoch
            or type(header.sequence) is not int
            or header.sequence <= 0
        ):
            raise ServiceMarketProjectionError("MARKET_FRAME_SOURCE_MISMATCH")

        cursor = MarketEventCursor(
            key=key,
            stream_id=header.stream_id,
            epoch=header.stream_epoch,
            sequence=header.sequence,
        )
        clock = ServiceMarketClockObservation(
            source=source,
            wire_sent_at_utc=header.sent_at_utc,
            local_observed_at_utc=_utc_from_unix_ns(wall_ns),
            local_observed_at_unix_ns=wall_ns,
            local_observed_at_monotonic_ns=monotonic_ns,
            observation_point=observation_point,
        )
        freshness = ServiceMarketFreshnessProjection(
            source=source,
            cursor=cursor,
            clock=clock,
            status=MarketFreshnessStatus.UNAVAILABLE,
            reason_codes=_CURRENT_FRESHNESS_GAPS,
        )
        return ServiceMarketEventProjection(
            frame=frame,
            source=source,
            cursor=cursor,
            clock=clock,
            freshness=freshness,
            delivery=delivery,
        )

    def _active_source_descriptor(self) -> MarketSourceDescriptor:
        source = self._market_client.source_descriptor
        if type(source) is not MarketSourceDescriptor:
            raise ServiceMarketProjectionError("SOURCE_DESCRIPTOR_UNAVAILABLE")
        if (
            source.key != self._market_client.key
            or source.stream_id != self._market_client.key.stream_id
        ):
            raise ServiceMarketProjectionError("SOURCE_DESCRIPTOR_MISMATCH")
        return source

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceMarketProjectionError("WRONG_OWNER_THREAD")


def _utc_from_unix_ns(value: int) -> str:
    if type(value) is not int or value <= 0:
        raise ServiceMarketProjectionError("LOCAL_CLOCK_INVALID")
    seconds, nanoseconds = divmod(value, 1_000_000_000)
    try:
        moment = datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
            microsecond=nanoseconds // 1_000
        )
    except (OverflowError, OSError, ValueError):
        raise ServiceMarketProjectionError("LOCAL_CLOCK_INVALID") from None
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_utc_timestamp(value: str, code: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise ServiceMarketProjectionError(code) from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ServiceMarketProjectionError(code)


__all__ = [
    "ServiceMarketBootstrapProjection",
    "ServiceMarketClockObservation",
    "ServiceMarketEventProjection",
    "ServiceMarketEventProjector",
    "ServiceMarketFreshnessProjection",
    "ServiceMarketProjectionError",
]
