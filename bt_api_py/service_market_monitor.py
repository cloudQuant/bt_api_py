"""Best-effort relay from authenticated Gateway market frames to Monitor.

This module consumes frames through the normal ``ServiceMarketClient`` owner
thread API and appends bounded system-scope observations through the registered
``upstream.telemetry/events.append`` runtime route. Monitor observations report
that this consumer received a Gateway frame; they are not source-side durable
facts and do not establish market truth, complete coverage, or strategy health.

The relay borrows both SDK resources. The caller remains responsible for
starting/closing the runtime and market client. Append failures never replace
or discard the original returned market frame. Pending Monitor requests are
held in a bounded in-memory queue and retried with their exact stable event ID
and payload while retained.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bt_api_base.market_protocol import MarketSubscriptionKey
from bt_api_base.service_protocol import WireEnvelope, WireIdentity, WireRole, WireScope
from bt_api_monitor.market_observation import (
    MonitorMarketObservationError,
    MonitorMarketObservationProjector,
)

from bt_api_py.service_market import (
    MarketBootstrap,
    ServiceMarketClient,
    ServiceMarketError,
)
from bt_api_py.service_runtime import (
    RegisteredBinding,
    ServiceCallOutcomeUnknownError,
    ServiceClientRuntime,
    ServiceMarketBindingView,
    ServiceRuntimeError,
    ServiceRuntimeRequestNotSentError,
)

_MARKET_OPERATIONS = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)
_TELEMETRY_MOUNT = "upstream.telemetry"
_TELEMETRY_OPERATION = "events.append"
_MAX_PENDING_EVENTS = 100_000
_MAX_PENDING_BYTES = 64 * 1024 * 1024
_DEFAULT_PENDING_EVENTS = 128
_DEFAULT_PENDING_BYTES = 1024 * 1024


class ServiceMarketMonitorError(ValueError):
    """Finite configuration and lifecycle errors for the Monitor relay."""

    def __init__(self, code: str, *, cause_code: str | None = None) -> None:
        self.code = code
        self.cause_code = cause_code
        super().__init__(code)


@dataclass(frozen=True)
class ServiceMarketMonitorStatus:
    """Finite consumer and delivery counters; deliberately has no health flag."""

    started: bool
    market_state: str
    delivery_state: str
    received_frames: int
    queued_observations: int
    appended_observations: int
    lost_observations: int
    pending_events: int
    pending_bytes: int
    append_attempts: int
    append_failures: int
    unknown_append_outcomes: int
    duplicate_frames: int
    conflicting_event_ids: int
    market_recovery_required: bool
    market_resync_required: bool
    last_market_error: str | None
    last_delivery_error: str | None
    last_loss_code: str | None


@dataclass(frozen=True)
class _PendingAppend:
    source_event_id: str
    payload: bytes


class ServiceMarketMonitorRelay:
    """Relay one Gateway frame at a time into an authorized system Monitor.

    ``runtime`` and ``market_client`` are trusted, already composed SDK
    objects. Construction verifies the active Gateway binding and requires a
    unique account-free ``upstream.telemetry/events.append`` binding selected
    by the supplied fixed ``monitor_scope_ref``. The append route is never
    selected by an event, account, or strategy field.

    ``pump_once`` polls at most one normal Gateway frame and attempts at most
    one Monitor append. It returns the original frame to its caller even when
    projection or append fails, so a Monitor outage does not turn into a
    market-frame failure. The caller should continue its ordinary market
    handling with the returned frame.
    """

    def __init__(
        self,
        market_client: ServiceMarketClient,
        runtime: ServiceClientRuntime,
        *,
        monitor_scope_ref: str,
        system_ref: str,
        process_ref: str,
        max_pending_events: int = _DEFAULT_PENDING_EVENTS,
        max_pending_bytes: int = _DEFAULT_PENDING_BYTES,
    ) -> None:
        if type(market_client) is not ServiceMarketClient:
            raise ServiceMarketMonitorError("INVALID_MARKET_CLIENT")
        if type(runtime) is not ServiceClientRuntime:
            raise ServiceMarketMonitorError("INVALID_RUNTIME")
        _validate_reference(monitor_scope_ref, "INVALID_MONITOR_SCOPE_REF")
        if (
            type(max_pending_events) is not int
            or not 1 <= max_pending_events <= _MAX_PENDING_EVENTS
            or type(max_pending_bytes) is not int
            or not 1 <= max_pending_bytes <= _MAX_PENDING_BYTES
        ):
            raise ServiceMarketMonitorError("INVALID_PENDING_LIMITS")
        if (
            type(runtime.identity) is not WireIdentity
            or runtime.identity.role is not WireRole.SERVICE
        ):
            raise ServiceMarketMonitorError("SERVICE_IDENTITY_REQUIRED")
        if getattr(market_client, "_runtime", None) is not runtime:
            raise ServiceMarketMonitorError("MARKET_RUNTIME_MISMATCH")

        try:
            market_binding = runtime.market_binding_view(
                market_client.mount,
                required_operations=_MARKET_OPERATIONS,
            )
        except ServiceRuntimeError as error:
            raise ServiceMarketMonitorError(
                "MARKET_BINDING_UNAVAILABLE", cause_code=error.code
            ) from None
        if type(market_binding) is not ServiceMarketBindingView:
            raise ServiceMarketMonitorError("INVALID_MARKET_BINDING")
        if (
            type(market_client.key) is not MarketSubscriptionKey
            or type(market_client.stream_scope) is not WireScope
            or not _is_system_scope(market_client.stream_scope)
            or market_client.stream_scope.execution_domain_ref
            != market_binding.rpc_scope.execution_domain_ref
        ):
            raise ServiceMarketMonitorError("PUBLIC_MARKET_SCOPE_REQUIRED")

        monitor_bindings = tuple(
            binding
            for binding in runtime.bindings
            if binding.active
            and binding.mount == _TELEMETRY_MOUNT
            and binding.scope_ref == monitor_scope_ref
            and _TELEMETRY_OPERATION in binding.operations
        )
        if len(monitor_bindings) != 1:
            raise ServiceMarketMonitorError("MONITOR_BINDING_UNAVAILABLE")
        monitor_binding = monitor_bindings[0]
        if (
            type(monitor_binding.scope) is not WireScope
            or not _is_system_scope(monitor_binding.scope)
            or monitor_binding.scope.execution_domain_ref
            != market_client.stream_scope.execution_domain_ref
        ):
            raise ServiceMarketMonitorError("SYSTEM_MONITOR_SCOPE_REQUIRED")

        try:
            projector = MonitorMarketObservationProjector(
                market_binding.peer,
                market_client.stream_scope,
                system_ref=system_ref,
                process_ref=process_ref,
            )
        except MonitorMarketObservationError as error:
            raise ServiceMarketMonitorError(
                "INVALID_MARKET_OBSERVATION_BINDING", cause_code=error.code
            ) from None

        self._market_client = market_client
        self._runtime = runtime
        self._caller_identity = runtime.identity
        self._monitor_scope_ref = monitor_scope_ref
        self._monitor_route_key = _monitor_route_key(monitor_binding)
        self._monitor_scope = monitor_binding.scope
        self._projector = projector
        self._max_pending_events = max_pending_events
        self._max_pending_bytes = max_pending_bytes
        self._owner_thread_id = threading.get_ident()
        self._started = False
        self._pending: deque[_PendingAppend] = deque()
        self._pending_bytes = 0
        self._received_frames = 0
        self._queued_observations = 0
        self._appended_observations = 0
        self._lost_observations = 0
        self._append_attempts = 0
        self._append_failures = 0
        self._unknown_append_outcomes = 0
        self._duplicate_frames = 0
        self._conflicting_event_ids = 0
        self._delivery_state = "IDLE"
        self._last_market_error: str | None = None
        self._last_delivery_error: str | None = None
        self._last_loss_code: str | None = None

    @property
    def status(self) -> ServiceMarketMonitorStatus:
        """Return cumulative relay diagnostics without implying readiness."""

        self._assert_owner()
        if self._market_client.resync_required:
            market_state = "RESYNC_REQUIRED"
        elif self._market_client.recovery_required:
            market_state = "RECOVERY_REQUIRED"
        elif self._last_market_error is not None:
            market_state = "POLL_FAILED"
        elif self._started:
            market_state = "STARTED"
        else:
            market_state = "NEW"
        return ServiceMarketMonitorStatus(
            started=self._started,
            market_state=market_state,
            delivery_state=self._delivery_state,
            received_frames=self._received_frames,
            queued_observations=self._queued_observations,
            appended_observations=self._appended_observations,
            lost_observations=self._lost_observations,
            pending_events=len(self._pending),
            pending_bytes=self._pending_bytes,
            append_attempts=self._append_attempts,
            append_failures=self._append_failures,
            unknown_append_outcomes=self._unknown_append_outcomes,
            duplicate_frames=self._duplicate_frames,
            conflicting_event_ids=self._conflicting_event_ids,
            market_recovery_required=self._market_client.recovery_required,
            market_resync_required=self._market_client.resync_required,
            last_market_error=self._last_market_error,
            last_delivery_error=self._last_delivery_error,
            last_loss_code=self._last_loss_code,
        )

    def start(self) -> MarketBootstrap:
        """Start the borrowed market client and return its normal bootstrap."""

        self._assert_owner()
        try:
            result = self._market_client.start()
        except ServiceMarketError as error:
            self._last_market_error = error.code
            raise ServiceMarketMonitorError("MARKET_START_FAILED", cause_code=error.code) from None
        self._started = True
        self._last_market_error = None
        return result

    def pump_once(self, timeout_ms: int = 0) -> WireEnvelope | None:
        """Poll one normal frame, enqueue its projection, and try one append."""

        self._assert_owner()
        if not self._started:
            raise ServiceMarketMonitorError("RELAY_NOT_STARTED")
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= 60_000:
            raise ServiceMarketMonitorError("INVALID_POLL_TIMEOUT")

        frame: WireEnvelope | None = None
        try:
            frame = self._market_client.poll(timeout_ms)
        except ServiceMarketError as error:
            self._last_market_error = error.code
        except Exception:
            self._last_market_error = "POLL_FAILED"
        else:
            self._last_market_error = None

        if frame is not None:
            self._received_frames += 1
            self._enqueue_projection(frame)

        self._append_one()
        return frame

    def _enqueue_projection(self, frame: WireEnvelope) -> None:
        try:
            projected = self._projector.project(frame)
            payload = json.dumps(
                projected,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("ascii")
            source_event_id = projected["event_id"]
        except MonitorMarketObservationError as error:
            self._lose("INVALID_MARKET_FRAME")
            self._last_delivery_error = error.code
            return
        except Exception:
            self._lose("INVALID_PROJECTION")
            self._last_delivery_error = "INVALID_PROJECTION"
            return

        if type(source_event_id) is not str:
            self._lose("INVALID_PROJECTION")
            self._last_delivery_error = "INVALID_PROJECTION"
            return
        for pending in self._pending:
            if pending.source_event_id == source_event_id:
                if pending.payload == payload:
                    self._duplicate_frames += 1
                    self._delivery_state = "PENDING"
                else:
                    self._conflicting_event_ids += 1
                    self._lose("EVENT_ID_CONFLICT")
                    self._last_delivery_error = "EVENT_ID_CONFLICT"
                return

        if (
            len(self._pending) >= self._max_pending_events
            or self._pending_bytes + len(payload) > self._max_pending_bytes
        ):
            self._lose("PENDING_CAPACITY")
            self._last_delivery_error = "PENDING_CAPACITY"
            return

        self._pending.append(_PendingAppend(source_event_id, payload))
        self._pending_bytes += len(payload)
        self._queued_observations += 1
        self._delivery_state = "PENDING"

    def _append_one(self) -> None:
        if not self._pending:
            if self._delivery_state == "PENDING":
                self._delivery_state = "IDLE"
            return
        if self._runtime.identity != self._caller_identity or not self._monitor_route_is_current():
            self._append_failures += 1
            self._last_delivery_error = "MONITOR_BINDING_CHANGED"
            self._delivery_state = "RETRY_PENDING"
            return
        pending = self._pending[0]
        try:
            arguments = json.loads(pending.payload.decode("ascii"))
        except Exception:
            self._append_failures += 1
            self._last_delivery_error = "RETAINED_REQUEST_INVALID"
            self._delivery_state = "RETRY_PENDING"
            return

        self._append_attempts += 1
        try:
            receipt = self._runtime.call(
                _TELEMETRY_MOUNT,
                _TELEMETRY_OPERATION,
                arguments,
                scope_ref=self._monitor_scope_ref,
            )
        except ServiceRuntimeRequestNotSentError:
            self._append_failures += 1
            self._last_delivery_error = "REQUEST_NOT_SENT"
            self._delivery_state = "RETRY_PENDING"
            return
        except ServiceCallOutcomeUnknownError:
            self._append_failures += 1
            self._unknown_append_outcomes += 1
            self._last_delivery_error = "BUSINESS_OUTCOME_UNKNOWN"
            self._delivery_state = "UNKNOWN_RETRY_PENDING"
            return
        except ServiceRuntimeError as error:
            self._append_failures += 1
            self._last_delivery_error = error.code
            self._delivery_state = "RETRY_PENDING"
            return
        except Exception:
            self._append_failures += 1
            self._unknown_append_outcomes += 1
            self._last_delivery_error = "APPEND_OUTCOME_UNKNOWN"
            self._delivery_state = "UNKNOWN_RETRY_PENDING"
            return

        if not _valid_append_receipt(receipt, pending.source_event_id):
            self._append_failures += 1
            self._unknown_append_outcomes += 1
            self._last_delivery_error = "INVALID_APPEND_RECEIPT"
            self._delivery_state = "UNKNOWN_RETRY_PENDING"
            return

        if self._pending[0] is not pending:
            # All queue mutation is owner-thread-only. Fail closed if that
            # invariant changes in a later caller or test double.
            self._append_failures += 1
            self._unknown_append_outcomes += 1
            self._last_delivery_error = "PENDING_ORDER_CHANGED"
            self._delivery_state = "UNKNOWN_RETRY_PENDING"
            return
        self._pending.popleft()
        self._pending_bytes -= len(pending.payload)
        self._appended_observations += 1
        self._delivery_state = "APPENDED" if not self._pending else "PENDING"
        self._last_delivery_error = None

    def _monitor_route_is_current(self) -> bool:
        matches = tuple(
            binding
            for binding in self._runtime.bindings
            if binding.active
            and binding.mount == _TELEMETRY_MOUNT
            and binding.scope_ref == self._monitor_scope_ref
            and _TELEMETRY_OPERATION in binding.operations
        )
        return (
            len(matches) == 1
            and matches[0].scope == self._monitor_scope
            and _monitor_route_key(matches[0]) == self._monitor_route_key
        )

    def _lose(self, code: str) -> None:
        self._lost_observations += 1
        self._last_loss_code = code

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceMarketMonitorError("WRONG_OWNER_THREAD")


def _is_system_scope(scope: WireScope) -> bool:
    return (
        scope.account_ref is None
        and not scope.account_refs
        and scope.strategy_ref is None
        and scope.run_id is None
        and scope.manual_book_ref is None
        and scope.portfolio_ref is None
        and scope.group_id is None
        and scope.leg_id is None
    )


def _validate_reference(value: Any, error_code: str) -> None:
    if type(value) is not str:
        raise ServiceMarketMonitorError(error_code)
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ServiceMarketMonitorError(error_code) from None
    if not encoded or len(encoded) > 256 or value != value.strip() or not value.isprintable():
        raise ServiceMarketMonitorError(error_code)


def _valid_append_receipt(receipt: object, source_event_id: str) -> bool:
    if not isinstance(receipt, Mapping) or set(receipt) != {
        "event_id",
        "source_event_id",
        "sequence",
    }:
        return False
    return (
        type(receipt.get("event_id")) is str
        and bool(receipt.get("event_id"))
        and receipt.get("source_event_id") == source_event_id
        and type(receipt.get("sequence")) is int
        and receipt["sequence"] > 0
    )


def _monitor_route_key(binding: RegisteredBinding) -> tuple[object, ...]:
    return (
        binding.mount,
        binding.service_id,
        binding.instance_id,
        binding.scope_ref,
        binding.registration_id,
        binding.service_epoch,
        binding.operations,
        binding.transport_ref,
    )


__all__ = [
    "ServiceMarketMonitorError",
    "ServiceMarketMonitorRelay",
    "ServiceMarketMonitorStatus",
]
