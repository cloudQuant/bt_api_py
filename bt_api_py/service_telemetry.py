"""Bounded best-effort ordinary telemetry delivery through the SDK runtime.

The emitter owns only its in-memory queue. It borrows an already-created
``ServiceClientRuntime`` and never starts a thread, maintains registrations,
closes the runtime, or retries a telemetry call whose outcome is unknown.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_base.telemetry_protocol import (
    TelemetryProtocolError,
    TelemetrySample,
    sample_size_bytes,
)

from bt_api_py.service_config import ClientConfig
from bt_api_py.service_runtime import RegisteredBinding, ServiceClientRuntime

_MOUNT = "runtime.telemetry"
_OPERATION = "telemetry.append"
_DEFAULT_MAX_EVENTS = 64
_DEFAULT_MAX_BYTES = 65_536
_MAX_EVENTS = 4096
_MAX_BYTES = 4 * 1024 * 1024
_DEFAULT_FLUSH_EVENTS = 16
_MAX_FLUSH_EVENTS = 256
_MAX_COUNTER = (1 << 63) - 1
_SCOPE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$", re.ASCII)


class ServiceTelemetryEmitterError(ValueError):
    """Finite local diagnostics for invalid emitter setup or input."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TelemetryEmitterStats:
    queued_events: int
    queued_bytes: int
    dropped_queue_total: int
    dropped_delivery_total: int
    delivered_total: int
    last_error_code: str | None
    state: Literal["ready", "degraded", "closed"]


@dataclass(frozen=True, slots=True)
class TelemetryFlushResult:
    """One bounded flush result; attempted counts samples removed from the queue."""

    attempted: int
    delivered: int
    dropped: int
    ready: bool


@dataclass(frozen=True, slots=True)
class _QueuedSample:
    sample: TelemetrySample
    size_bytes: int


class ServiceTelemetryEmitter:
    """Queue flat metric samples and deliver them on the runtime owner thread.

    Queue overflow drops the newest sample without blocking. A failed or
    malformed delivery drops that sample and stops the current flush; later
    samples remain queued. The caller is responsible for periodically calling
    ``runtime.maintain()`` and for closing the borrowed runtime.
    """

    def __init__(
        self,
        runtime: ServiceClientRuntime,
        *,
        scope_ref: str,
        max_events: int = _DEFAULT_MAX_EVENTS,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        clock: Callable[[], int | float] = time.time,
    ) -> None:
        if not isinstance(runtime, ServiceClientRuntime):
            raise ServiceTelemetryEmitterError("INVALID_RUNTIME")
        if not _valid_scope_ref(scope_ref):
            raise ServiceTelemetryEmitterError("INVALID_SCOPE_REF")
        _validate_limits(max_events, max_bytes)
        if not callable(clock):
            raise ServiceTelemetryEmitterError("INVALID_CLOCK")

        identity = runtime.identity
        if type(identity) is not WireIdentity or identity.role is not WireRole.STRATEGY_CLIENT:
            raise ServiceTelemetryEmitterError("STRATEGY_CLIENT_REQUIRED")

        self._runtime = runtime
        self._scope_ref = scope_ref
        self._max_events = max_events
        self._max_bytes = max_bytes
        self._clock = clock
        self._identity = identity
        self._owner_thread_id = threading.get_ident()
        self._stream_id = uuid.uuid4().hex
        self._sequence = 0
        self._queue: deque[_QueuedSample] = deque()
        self._queued_bytes = 0
        self._dropped_queue_total = 0
        self._dropped_delivery_total = 0
        self._delivered_total = 0
        self._last_error_code: str | None = None
        self._state: Literal["ready", "degraded", "closed"] = "degraded"
        self._closed = False
        self._lock = threading.Lock()

        self._pinned_binding: tuple[Any, ...] | None = None
        self._initial_ambiguity = False
        candidates = _configured_bindings(runtime, scope_ref)
        if len(candidates) > 1:
            self._initial_ambiguity = True
            self._last_error_code = "BINDING_AMBIGUOUS"
        elif candidates:
            binding = candidates[0]
            if binding.required is not False:
                raise ServiceTelemetryEmitterError("TELEMETRY_BINDING_MUST_BE_OPTIONAL")
            self._pinned_binding = _binding_signature(binding)
            if _runtime_route_ready(runtime, binding):
                self._state = "ready"
            else:
                self._last_error_code = "BINDING_UNAVAILABLE"
        else:
            # The optional registration may have been omitted or may have
            # failed during startup. Enqueue remains available in either case.
            self._last_error_code = "BINDING_UNAVAILABLE"

    @classmethod
    def from_config(
        cls,
        runtime: ServiceClientRuntime,
        config: ClientConfig,
        *,
        scope_ref: str,
        clock: Callable[[], int | float] = time.time,
    ) -> ServiceTelemetryEmitter:
        """Build from parsed strategy-client monitor queue limits, if present."""

        if type(config) is not ClientConfig or config.process_role != "strategy_client":
            raise ServiceTelemetryEmitterError("INVALID_CLIENT_CONFIG")
        monitor = config.clients.get("monitor")
        if monitor is None:
            return cls(runtime, scope_ref=scope_ref, clock=clock)
        settings = monitor.get("settings") if isinstance(monitor, Mapping) else None
        bindings = settings.get("bindings") if isinstance(settings, Mapping) else None
        if not isinstance(bindings, (tuple, list)):
            raise ServiceTelemetryEmitterError("INVALID_CLIENT_CONFIG")
        matching = [
            binding
            for binding in bindings
            if isinstance(binding, Mapping)
            and binding.get("mount") == _MOUNT
            and binding.get("scope_ref") == scope_ref
        ]
        if len(matching) > 1:
            raise ServiceTelemetryEmitterError("BINDING_AMBIGUOUS")
        if matching and matching[0].get("required", True) is not False:
            raise ServiceTelemetryEmitterError("TELEMETRY_BINDING_MUST_BE_OPTIONAL")
        options = settings.get("options", {})
        if not isinstance(options, Mapping):
            raise ServiceTelemetryEmitterError("INVALID_CLIENT_CONFIG")
        return cls(
            runtime,
            scope_ref=scope_ref,
            max_events=options.get("telemetry_queue_max_events", _DEFAULT_MAX_EVENTS),
            max_bytes=options.get("telemetry_queue_max_bytes", _DEFAULT_MAX_BYTES),
            clock=clock,
        )

    @property
    def stream_id(self) -> str:
        return self._stream_id

    @property
    def stats(self) -> TelemetryEmitterStats:
        with self._lock:
            return TelemetryEmitterStats(
                queued_events=len(self._queue),
                queued_bytes=self._queued_bytes,
                dropped_queue_total=self._dropped_queue_total,
                dropped_delivery_total=self._dropped_delivery_total,
                delivered_total=self._delivered_total,
                last_error_code=self._last_error_code,
                state=self._state,
            )

    def enqueue_heartbeat(self) -> bool:
        """Queue one empty heartbeat; return False when the bounded queue is full."""

        return self._enqueue("heartbeat", {})

    def enqueue_metrics(self, metrics: Mapping[str, int | float]) -> bool:
        """Queue one flat numeric sample without performing I/O."""

        return self._enqueue("metrics", metrics)

    def flush(self, *, max_events: int = _DEFAULT_FLUSH_EVENTS) -> TelemetryFlushResult:
        """Deliver at most ``max_events`` samples once, stopping at the first failure."""

        self._assert_owner_thread()
        if type(max_events) is not int or not 1 <= max_events <= _MAX_FLUSH_EVENTS:
            raise ServiceTelemetryEmitterError("INVALID_FLUSH_LIMIT")
        with self._lock:
            if self._closed:
                return TelemetryFlushResult(0, 0, 0, False)

        attempted = delivered = dropped = 0
        while attempted < max_events:
            with self._lock:
                if self._closed or not self._queue:
                    break
                queued = self._queue.popleft()
                self._queued_bytes -= queued.size_bytes
                current_dropped_queue = self._dropped_queue_total
                current_dropped_delivery = self._dropped_delivery_total

            attempted += 1
            try:
                self._resolve_route()
                sample = _with_current_counters(
                    queued.sample,
                    dropped_queue_total=current_dropped_queue,
                    dropped_delivery_total=current_dropped_delivery,
                )
                arguments = sample.to_dict()
                sample_size_bytes(sample)
                receipt = self._runtime.call(
                    _MOUNT,
                    _OPERATION,
                    arguments,
                    scope_ref=self._scope_ref,
                )
                self._resolve_route()
                _validate_receipt(receipt, sample)
            except Exception as error:
                with self._lock:
                    self._increment_delivery_drop_locked()
                    self._set_degraded_locked(_error_code(error))
                dropped += 1
                break

            with self._lock:
                self._delivered_total += 1
                if not self._closed:
                    self._last_error_code = None
                    self._state = "ready"
            delivered += 1

        with self._lock:
            if self._closed:
                ready = False
            elif attempted == 0 and not self._queue:
                try:
                    self._resolve_route()
                except Exception as error:
                    self._set_degraded_locked(_error_code(error))
                    ready = False
                else:
                    if self._last_error_code in {
                        "BINDING_UNAVAILABLE",
                        "RUNTIME_UNHEALTHY",
                        "RUNTIME_NOT_REGISTERED",
                    }:
                        self._last_error_code = None
                    ready = self._last_error_code is None
                    self._state = "ready" if ready else "degraded"
            else:
                ready = self._state == "ready"
        return TelemetryFlushResult(attempted, delivered, dropped, ready)

    def close(self) -> None:
        """Discard this emitter's queue; the borrowed runtime stays open."""

        with self._lock:
            if self._closed:
                return
            pending = len(self._queue)
            self._queue.clear()
            self._queued_bytes = 0
            if pending:
                self._add_delivery_drops_locked(pending)
            self._closed = True
            self._state = "closed"

    def _enqueue(self, kind: str, metrics: Mapping[str, int | float]) -> bool:
        try:
            observed_at = self._clock()
        except Exception:
            raise ServiceTelemetryEmitterError("INVALID_OBSERVED_AT") from None

        with self._lock:
            if self._closed:
                raise ServiceTelemetryEmitterError("CLOSED")
            if self._sequence >= _MAX_COUNTER:
                raise ServiceTelemetryEmitterError("SEQUENCE_EXHAUSTED")
            sequence = self._sequence + 1
            try:
                sample = TelemetrySample(
                    stream_id=self._stream_id,
                    sequence=sequence,
                    kind=kind,
                    observed_at=observed_at,
                    metrics=metrics,
                    dropped_queue_total=self._dropped_queue_total,
                    dropped_delivery_total=self._dropped_delivery_total,
                )
                size_bytes = sample_size_bytes(sample)
            except TelemetryProtocolError:
                raise ServiceTelemetryEmitterError("INVALID_SAMPLE") from None

            if (
                len(self._queue) >= self._max_events
                or self._queued_bytes + size_bytes > self._max_bytes
            ):
                self._increment_queue_drop_locked()
                self._set_degraded_locked("QUEUE_FULL")
                return False

            self._sequence = sequence
            self._queue.append(_QueuedSample(sample, size_bytes))
            self._queued_bytes += size_bytes
            return True

    def _resolve_route(self) -> RegisteredBinding:
        if self._runtime.identity != self._identity:
            raise _RouteUnavailableError("IDENTITY_CHANGED")
        if self._runtime.state != "registered":
            raise _RouteUnavailableError("RUNTIME_NOT_REGISTERED")
        try:
            if self._runtime.healthy is not True:
                raise _RouteUnavailableError("RUNTIME_UNHEALTHY")
            candidates = _configured_bindings(self._runtime, self._scope_ref)
        except _RouteUnavailableError:
            raise
        except Exception:
            raise _RouteUnavailableError("BINDING_UNAVAILABLE") from None
        if self._initial_ambiguity:
            raise _RouteUnavailableError("BINDING_AMBIGUOUS")
        if len(candidates) != 1:
            raise _RouteUnavailableError(
                "BINDING_UNAVAILABLE" if not candidates else "BINDING_AMBIGUOUS"
            )
        binding = candidates[0]
        if binding.required is not False:
            raise _RouteUnavailableError("TELEMETRY_BINDING_MUST_BE_OPTIONAL")
        signature = _binding_signature(binding)
        if self._pinned_binding is None:
            self._pinned_binding = signature
        elif signature != self._pinned_binding:
            raise _RouteUnavailableError("BINDING_CHANGED")
        if _runtime_route_ready(self._runtime, binding) is not True:
            raise _RouteUnavailableError("ACTION_NOT_GRANTED")
        return binding

    def _assert_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceTelemetryEmitterError("WRONG_OWNER_THREAD")

    def _increment_queue_drop_locked(self) -> None:
        if self._dropped_queue_total >= _MAX_COUNTER:
            raise ServiceTelemetryEmitterError("COUNTER_EXHAUSTED")
        self._dropped_queue_total += 1

    def _increment_delivery_drop_locked(self) -> None:
        self._add_delivery_drops_locked(1)

    def _add_delivery_drops_locked(self, count: int) -> None:
        if self._dropped_delivery_total + count > _MAX_COUNTER:
            raise ServiceTelemetryEmitterError("COUNTER_EXHAUSTED")
        self._dropped_delivery_total += count

    def _set_degraded_locked(self, error_code: str) -> None:
        self._last_error_code = error_code
        if not self._closed:
            self._state = "degraded"


class _RouteUnavailableError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _configured_bindings(
    runtime: ServiceClientRuntime, scope_ref: str
) -> tuple[RegisteredBinding, ...]:
    bindings = runtime.bindings
    if type(bindings) is not tuple:
        raise ValueError("invalid runtime bindings")
    return tuple(
        binding
        for binding in bindings
        if type(binding) is RegisteredBinding
        and binding.mount == _MOUNT
        and binding.scope_ref == scope_ref
    )


def _runtime_route_ready(runtime: ServiceClientRuntime, binding: RegisteredBinding) -> bool:
    return (
        runtime.state == "registered"
        and runtime.healthy is True
        and binding.active is True
        and binding.required is False
        and type(binding.scope) is WireScope
        and type(binding.operations) is frozenset
        and _OPERATION in binding.operations
    )


def _binding_signature(binding: RegisteredBinding) -> tuple[Any, ...]:
    if type(binding.scope) is not WireScope:
        raise ValueError("invalid binding scope")
    return (
        binding.mount,
        binding.service_id,
        binding.instance_id,
        binding.scope_ref,
        binding.scope,
        binding.registration_id,
        binding.service_epoch,
        binding.operations,
        binding.transport_ref,
        binding.active,
        binding.required,
    )


def _with_current_counters(
    sample: TelemetrySample,
    *,
    dropped_queue_total: int,
    dropped_delivery_total: int,
) -> TelemetrySample:
    return TelemetrySample(
        stream_id=sample.stream_id,
        sequence=sample.sequence,
        kind=sample.kind,
        observed_at=sample.observed_at,
        metrics=sample.metrics,
        dropped_queue_total=dropped_queue_total,
        dropped_delivery_total=dropped_delivery_total,
    )


def _validate_receipt(reply: object, sample: TelemetrySample) -> None:
    if not isinstance(reply, Mapping) or set(reply) != {
        "stream_id",
        "sequence",
        "accepted",
    }:
        raise ServiceTelemetryEmitterError("INVALID_RECEIPT")
    if (
        type(reply.get("stream_id")) is not str
        or reply.get("stream_id") != sample.stream_id
        or type(reply.get("sequence")) is not int
        or reply.get("sequence") != sample.sequence
        or reply.get("accepted") is not True
    ):
        raise ServiceTelemetryEmitterError("INVALID_RECEIPT")


def _valid_scope_ref(value: object) -> bool:
    return type(value) is str and _SCOPE_REF_RE.fullmatch(value) is not None


def _validate_limits(max_events: object, max_bytes: object) -> None:
    if type(max_events) is not int or not 1 <= max_events <= _MAX_EVENTS:
        raise ServiceTelemetryEmitterError("INVALID_QUEUE_LIMIT")
    if type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_BYTES:
        raise ServiceTelemetryEmitterError("INVALID_QUEUE_LIMIT")


def _error_code(error: Exception) -> str:
    if isinstance(error, TelemetryProtocolError):
        return "INVALID_SAMPLE"
    code = getattr(error, "code", None)
    if type(code) is str and _ERROR_CODE_RE.fullmatch(code) is not None:
        return code
    if isinstance(error, ServiceTelemetryEmitterError):
        return error.code
    return "DELIVERY_FAILED"


__all__ = [
    "ServiceTelemetryEmitter",
    "ServiceTelemetryEmitterError",
    "TelemetryEmitterStats",
    "TelemetryFlushResult",
]
