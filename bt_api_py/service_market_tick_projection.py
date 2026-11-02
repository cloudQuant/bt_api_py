"""Bind neutral tick payloads to one authenticated market-client frame."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from bt_api_base.market_tick_protocol import (
    MarketEventCursor,
    MarketFreshnessStatus,
    MarketTickContractError,
    MarketTickEvent,
    MarketTickPayload,
    bind_market_tick_event,
)
from bt_api_py.service_contracts import MarketSourceDescriptor

from bt_api_py.service_market_projection import (
    ServiceMarketBootstrapProjection,
    ServiceMarketEventProjection,
    ServiceMarketEventProjector,
)


class ServiceMarketTickProjectionError(RuntimeError):
    """Finite, value-free failure while binding a market tick frame."""

    _CODES = frozenset(
        {
            "INVALID_MARKET_EVENT_PROJECTOR",
            "MARKET_TICK_PAYLOAD_UNAVAILABLE",
            "MARKET_TICK_PAYLOAD_INVALID",
            "MARKET_TICK_FACT_BINDING_MISMATCH",
            "MARKET_TICK_CLOCK_EVIDENCE_MISMATCH",
            "MARKET_TICK_FRESHNESS_EVIDENCE_MISMATCH",
            "INVALID_MARKET_TICK_IDENTITY_CODE",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self._CODES else "MARKET_TICK_PAYLOAD_INVALID"
        super().__init__(self.code)


class MarketTickIdentityStatus(str, Enum):
    """Whether an exact authenticated start receipt binds this projector."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class MarketTickProjectorState(str, Enum):
    """Current polling state, kept separate from the stream identity proof."""

    NOT_STARTED = "not_started"
    ACTIVE = "active"
    RESYNC_REQUIRED = "resync_required"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    CLOSED = "closed"


_MARKET_TICK_IDENTITY_ISSUER = object()
_IDENTITY_UNAVAILABLE_CODES = frozenset(
    {
        "PROJECTOR_NOT_CREATED",
        "PROJECTOR_NOT_STARTED",
        "PROJECTOR_CLOSED",
        "START_RECEIPT_UNAVAILABLE",
        "SOURCE_DESCRIPTOR_UNAVAILABLE",
        "SOURCE_DESCRIPTOR_MISMATCH",
        "EVENT_SOURCE_MISMATCH",
    }
)


@dataclass(frozen=True, init=False)
class MarketTickStreamIdentity:
    """SDK-issued identity facts for one exact authenticated tick projector.

    Availability records only that a successful SDK start receipt bound the
    source identity to this projector. It does not qualify freshness, grant
    write authority, or claim that no events were lost. ``cursor`` is the last
    exact typed event accepted by this projector and can be absent when start
    returned no event. A separate provider stream-generation contract is not
    present today and is deliberately reported as ``None``.
    """

    status: MarketTickIdentityStatus
    start_receipt: Optional["ServiceMarketTickBootstrapProjection"]
    cursor: Optional[MarketEventCursor]
    projector_state: MarketTickProjectorState
    resync_required: Optional[bool]
    unavailable_code: Optional[str]

    def __init__(
        self,
        *,
        _issuer: object,
        status: MarketTickIdentityStatus,
        start_receipt: Optional["ServiceMarketTickBootstrapProjection"],
        cursor: Optional[MarketEventCursor],
        projector_state: MarketTickProjectorState,
        resync_required: Optional[bool],
        unavailable_code: Optional[str],
    ) -> None:
        if _issuer is not _MARKET_TICK_IDENTITY_ISSUER:
            raise TypeError("MarketTickStreamIdentity is issued by the SDK projector")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "start_receipt", start_receipt)
        object.__setattr__(self, "cursor", cursor)
        object.__setattr__(self, "projector_state", projector_state)
        object.__setattr__(self, "resync_required", resync_required)
        object.__setattr__(self, "unavailable_code", unavailable_code)
        self.__post_init__()

    def __post_init__(self) -> None:
        if type(self.status) is not MarketTickIdentityStatus:
            raise TypeError("status must be a MarketTickIdentityStatus")
        if type(self.projector_state) is not MarketTickProjectorState:
            raise TypeError("projector_state must be a MarketTickProjectorState")
        if self.resync_required is not None and type(self.resync_required) is not bool:
            raise TypeError("resync_required must be bool or None")
        if self.cursor is not None and type(self.cursor) is not MarketEventCursor:
            raise TypeError("cursor must be a MarketEventCursor or None")

        if self.status is MarketTickIdentityStatus.AVAILABLE:
            if (
                type(self.start_receipt) is not ServiceMarketTickBootstrapProjection
                or type(self.start_receipt.source) is not MarketSourceDescriptor
                or self.unavailable_code is not None
                or self.projector_state
                in {MarketTickProjectorState.NOT_STARTED, MarketTickProjectorState.CLOSED}
            ):
                raise TypeError("available identity requires an exact active start receipt")
            if self.cursor is not None and (
                self.cursor.stream_id != self.start_receipt.source.stream_id
                or self.cursor.epoch != self.start_receipt.source.epoch
            ):
                raise TypeError("cursor must belong to the start receipt source")
            return

        if (
            self.start_receipt is not None
            or self.cursor is not None
            or type(self.unavailable_code) is not str
            or self.unavailable_code not in _IDENTITY_UNAVAILABLE_CODES
        ):
            raise TypeError("unavailable identity requires a finite reason and no source facts")

    @property
    def source_descriptor(self) -> Optional[MarketSourceDescriptor]:
        if self.status is not MarketTickIdentityStatus.AVAILABLE:
            return None
        return self.start_receipt.source

    @property
    def key(self):
        source = self.source_descriptor
        return source.key if source is not None else None

    @property
    def source_ref(self) -> Optional[str]:
        key = self.key
        return key.source_ref if key is not None else None

    @property
    def stream_id(self) -> Optional[str]:
        source = self.source_descriptor
        return source.stream_id if source is not None else None

    @property
    def epoch(self) -> Optional[str]:
        source = self.source_descriptor
        return source.epoch if source is not None else None

    @property
    def connection_generation(self) -> Optional[int]:
        source = self.source_descriptor
        return source.connection_generation if source is not None else None

    @property
    def clock_source_session_id(self) -> Optional[str]:
        source = self.source_descriptor
        return source.clock_source_session_id if source is not None else None

    @property
    def provider_stream_generation(self) -> None:
        """No distinct provider stream-generation fact is currently contracted."""

        return None


@dataclass(frozen=True)
class ServiceMarketTickBootstrapProjection:
    """Authenticated latest-event bootstrap and its optional typed tick."""

    bootstrap: ServiceMarketBootstrapProjection
    latest_event: Optional[MarketTickEvent]

    @property
    def source(self):
        return self.bootstrap.source

    @property
    def snapshot_complete(self) -> bool:
        return self.bootstrap.snapshot_complete

    @property
    def full_baseline_available(self) -> bool:
        return self.bootstrap.full_baseline_available

    @property
    def resync_required(self) -> bool:
        return self.bootstrap.resync_required


class ServiceMarketTickProjector:
    """Own a ``ServiceMarketEventProjector`` and bind its ticks to wire cursors.

    The wrapped projector retains the authenticated ``ServiceMarketClient``
    start/poll/resync/close lifecycle. This class accepts no application-built
    market events or clock claims.
    """

    def __init__(self, event_projector: ServiceMarketEventProjector) -> None:
        if type(event_projector) is not ServiceMarketEventProjector:
            raise ServiceMarketTickProjectionError("INVALID_MARKET_EVENT_PROJECTOR")
        self._event_projector = event_projector
        self._owner_thread_id = threading.get_ident()
        self._started = False
        self._closed = False
        self._last_event: Optional[MarketTickEvent] = None

    @property
    def healthy(self) -> bool:
        return self._event_projector.healthy

    @property
    def key(self):
        return self._event_projector.key

    @property
    def source_descriptor(self):
        return self._event_projector.source_descriptor

    @property
    def recovery_required(self) -> bool:
        return self._event_projector.recovery_required

    @property
    def resync_required(self) -> bool:
        return self._event_projector.resync_required

    @property
    def freshness_available(self) -> bool:
        return self._event_projector.freshness_available

    @property
    def stream_identity(self) -> MarketTickStreamIdentity:
        """Return identity facts issued from this exact projector's start receipt.

        Identity remains separate from clock qualification and recovery state:
        a bound source can coexist with unknown freshness or a required resync.
        """

        self._check_owner()
        if self._closed:
            return _unavailable_market_tick_identity(
                "PROJECTOR_CLOSED", MarketTickProjectorState.CLOSED
            )
        if not self._started:
            return _unavailable_market_tick_identity(
                "PROJECTOR_NOT_STARTED", MarketTickProjectorState.NOT_STARTED
            )

        receipt = self._event_projector.last_bootstrap
        if type(receipt) is not ServiceMarketBootstrapProjection:
            return _unavailable_market_tick_identity(
                "START_RECEIPT_UNAVAILABLE", MarketTickProjectorState.UNKNOWN
            )
        source = receipt.source
        active_source = self._event_projector.source_descriptor
        key = self._event_projector.key
        if type(source) is not MarketSourceDescriptor:
            return _unavailable_market_tick_identity(
                "SOURCE_DESCRIPTOR_UNAVAILABLE", MarketTickProjectorState.UNKNOWN
            )
        if (
            type(active_source) is not MarketSourceDescriptor
            or active_source != source
            or source.key != key
            or source.key.channel != "tick"
        ):
            return _unavailable_market_tick_identity(
                "SOURCE_DESCRIPTOR_MISMATCH", MarketTickProjectorState.UNKNOWN
            )

        cursor = None
        if self._last_event is not None:
            event = self._last_event
            if (
                type(event) is not MarketTickEvent
                or event.source != source
                or event.key != source.key
                or event.cursor.stream_id != source.stream_id
                or event.cursor.epoch != source.epoch
            ):
                return _unavailable_market_tick_identity(
                    "EVENT_SOURCE_MISMATCH", MarketTickProjectorState.UNKNOWN
                )
            cursor = event.cursor

        try:
            resync_required = self._event_projector.resync_required
            healthy = self._event_projector.healthy
        except Exception:
            resync_required = None
            healthy = None
        if type(resync_required) is not bool:
            resync_required = None
        if type(healthy) is not bool:
            healthy = None
        if resync_required is True:
            state = MarketTickProjectorState.RESYNC_REQUIRED
        elif healthy is True:
            state = MarketTickProjectorState.ACTIVE
        elif healthy is False:
            state = MarketTickProjectorState.UNHEALTHY
        else:
            state = MarketTickProjectorState.UNKNOWN

        return MarketTickStreamIdentity(
            _issuer=_MARKET_TICK_IDENTITY_ISSUER,
            status=MarketTickIdentityStatus.AVAILABLE,
            start_receipt=receipt,
            cursor=cursor,
            projector_state=state,
            resync_required=resync_required,
            unavailable_code=None,
        )

    def start(self) -> ServiceMarketTickBootstrapProjection:
        self._check_owner()
        bootstrap = self._event_projector.start()
        snapshot = bootstrap.latest_snapshot
        latest_event = self._bind(snapshot) if snapshot is not None else None
        self._started = True
        self._closed = False
        self._last_event = latest_event
        return ServiceMarketTickBootstrapProjection(
            bootstrap=bootstrap,
            latest_event=latest_event,
        )

    def poll(self, timeout_ms: int = 0) -> Optional[MarketTickEvent]:
        self._check_owner()
        event = self._event_projector.poll(timeout_ms)
        if event is None:
            return None
        bound_event = self._bind(event)
        self._last_event = bound_event
        return bound_event

    def accept_resync(
        self, *, acknowledge_data_loss: bool
    ) -> ServiceMarketTickBootstrapProjection:
        self._check_owner()
        bootstrap = self._event_projector.accept_resync(
            acknowledge_data_loss=acknowledge_data_loss
        )
        snapshot = bootstrap.latest_snapshot
        latest_event = self._bind(snapshot) if snapshot is not None else None
        self._last_event = latest_event
        return ServiceMarketTickBootstrapProjection(
            bootstrap=bootstrap,
            latest_event=latest_event,
        )

    def close(self) -> None:
        self._check_owner()
        self._event_projector.close()
        self._started = False
        self._closed = True
        self._last_event = None

    def _bind(self, projection: ServiceMarketEventProjection) -> MarketTickEvent:
        if type(projection) is not ServiceMarketEventProjection:
            raise ServiceMarketTickProjectionError("MARKET_TICK_PAYLOAD_INVALID")
        data = projection.normalized_data
        if data.get("schema") != "sdk.market.tick":
            raise ServiceMarketTickProjectionError("MARKET_TICK_PAYLOAD_UNAVAILABLE")
        try:
            payload = MarketTickPayload.from_dict(data)
        except (MarketTickContractError, TypeError, ValueError):
            raise ServiceMarketTickProjectionError("MARKET_TICK_PAYLOAD_INVALID") from None

        source = projection.source
        cursor = MarketEventCursor(
            stream_id=projection.cursor.stream_id,
            epoch=projection.cursor.epoch,
            sequence=projection.cursor.sequence,
        )
        if (
            projection.freshness.source != source
            or projection.freshness.cursor.key != source.key
            or projection.freshness.cursor.stream_id != cursor.stream_id
            or projection.freshness.cursor.epoch != cursor.epoch
            or projection.freshness.cursor.sequence != cursor.sequence
        ):
            raise ServiceMarketTickProjectionError("MARKET_TICK_FACT_BINDING_MISMATCH")

        # A local observation taken after client.poll returns is not a provider
        # event or receive timestamp. Until a reviewed SDK contract binds both
        # source and receive clocks, never promote this payload's status.
        clock = projection.clock
        if clock.event_time_utc is None or clock.clock_calibration != "VERIFIED":
            if (
                payload.source_clock_quality.value != "unknown"
                or payload.receive_clock_quality.value != "unknown"
                or payload.freshness_status is not MarketFreshnessStatus.UNAVAILABLE
            ):
                raise ServiceMarketTickProjectionError("MARKET_TICK_CLOCK_EVIDENCE_MISMATCH")
        elif payload.provider_event_time_utc != clock.event_time_utc:
            raise ServiceMarketTickProjectionError("MARKET_TICK_CLOCK_EVIDENCE_MISMATCH")

        if payload.freshness_status.value != projection.freshness.status.value:
            raise ServiceMarketTickProjectionError("MARKET_TICK_FRESHNESS_EVIDENCE_MISMATCH")
        if projection.freshness.status is MarketFreshnessStatus.UNAVAILABLE:
            if not set(projection.freshness.reason_codes).issubset(
                set(payload.freshness_reason_codes)
            ):
                raise ServiceMarketTickProjectionError("MARKET_TICK_FRESHNESS_EVIDENCE_MISMATCH")
        try:
            return bind_market_tick_event(payload, source, cursor)
        except MarketTickContractError:
            raise ServiceMarketTickProjectionError("MARKET_TICK_FACT_BINDING_MISMATCH") from None

    def _check_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceMarketTickProjectionError("INVALID_MARKET_EVENT_PROJECTOR")


def _unavailable_market_tick_identity(
    code: str, state: MarketTickProjectorState
) -> MarketTickStreamIdentity:
    if type(code) is not str or code not in _IDENTITY_UNAVAILABLE_CODES:
        code = "START_RECEIPT_UNAVAILABLE"
    return MarketTickStreamIdentity(
        _issuer=_MARKET_TICK_IDENTITY_ISSUER,
        status=MarketTickIdentityStatus.UNAVAILABLE,
        start_receipt=None,
        cursor=None,
        projector_state=state,
        resync_required=None,
        unavailable_code=code,
    )


def unavailable_market_tick_stream_identity(code: str) -> MarketTickStreamIdentity:
    """Issue a source-free identity result when no exact projector is bound.

    This public constructor never accepts or copies source, generation,
    session, stream, or cursor values. Callers must provide one finite reason
    and may only receive an explicitly unavailable SDK DTO.
    """

    if type(code) is not str or code not in _IDENTITY_UNAVAILABLE_CODES:
        raise ServiceMarketTickProjectionError("INVALID_MARKET_TICK_IDENTITY_CODE")
    state = {
        "PROJECTOR_NOT_CREATED": MarketTickProjectorState.NOT_STARTED,
        "PROJECTOR_NOT_STARTED": MarketTickProjectorState.NOT_STARTED,
        "PROJECTOR_CLOSED": MarketTickProjectorState.CLOSED,
    }.get(code, MarketTickProjectorState.UNKNOWN)
    return _unavailable_market_tick_identity(code, state)


__all__ = [
    "MarketTickIdentityStatus",
    "MarketTickProjectorState",
    "MarketTickStreamIdentity",
    "ServiceMarketTickBootstrapProjection",
    "ServiceMarketTickProjectionError",
    "ServiceMarketTickProjector",
    "unavailable_market_tick_stream_identity",
]
