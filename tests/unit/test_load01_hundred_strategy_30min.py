"""Bounded LOAD01 synthetic sustained-load runner on fixed committed sources."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import random
import sqlite3
import sys
import threading
import time
import zipfile
from collections import Counter, deque
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil
import pytest
import zmq
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    CancelDispatchClaimReceiptV1,
    CancelObservation,
    ExecutionScope,
    OrderIntent,
    ProviderObservation,
    SqliteExecutionStore,
)
from bt_api_execution.journal_reader import ExecutionJournalReader
from bt_api_gateway import GatewayCommandKind, GatewayEventJournal, GatewayPrincipal
from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker
from bt_api_gateway.market_hub import GatewayMarketHub
from bt_api_gateway.service_component import GatewayServiceAccountScope
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_monitor.durable import DurableOutbox
from bt_api_monitor.service_ingress import MonitorIngressTarget
from bt_api_monitor.service_telemetry import MonitorTelemetryService

from bt_api_py.service_account import ServiceAccountClient
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_execution_monitor import (
    ServiceExecutionMonitorRelay,
)
from bt_api_py.service_execution_monitor import (
    _outbox_scope as _relay_outbox_scope,
)
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_market import (
    ServiceMarketClient,
    ServiceMarketError,
    ZmqMarketTransportFactory,
)
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from bt_api_py.service_telemetry import ServiceTelemetryEmitter
from tests.unit.test_service_gateway_hundred_strategy_integration import (
    _ACCOUNT_KEYS,
    _ACCOUNT_REFS,
    _RESOURCE,
    _CountedSyntheticAccountProvider,
    _free_endpoints,
    _start_process,
)

_FIXED_PARENT = "436e55751fdf71fd46a145738d1bde4c498e8e6f"
_FIXED_PACKAGE_GITLINKS = {
    "bt_api/bt_api_base": "b4aabd1e17b9c7252fa7b4d266932087d99b5517",
    "bt_api/bt_api_execution": "84d97c9f4f85828cb649ed69c147e2ad4cf77c00",
    "bt_api/bt_api_gateway": "59b164ab7f89c6428ae6546103aad6058dab4c16",
    "bt_api/bt_api_monitor": "536a4f04747182749fabdac5e28624f80465bc53",
    "bt_api/bt_api_risk": "72d955e6ff1ddc35aabaf67ef88dc48a765f38df",
    "bt_api/bt_api_transport_zmq": "29ab21e714292a363538491e4ff8461d336d1dfc",
}
_DOMAIN = "paper:load01-synthetic"
_STRATEGIES = 100
_ACCOUNTS = 10
_PER_ACCOUNT = 10
_TOPICS = 20
_MARKET_HZ = 200
_LIFECYCLES_PER_ACCOUNT_HZ = 2
_EVENT_TYPES = ("execution.lifecycle", "execution.cancellation")
_RELAY_MAX_EVENTS = 1_000_000
_RELAY_MAX_BYTES = 512 * 1024 * 1024
_MIN_FREE_BYTES = 2 * 1024**3
_FULL_LOAD_SECONDS = 30 * 60
_MARKET_OPS = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)
_ACCOUNT_OPS = frozenset({"account.snapshot"})
_ORDER_OPS = frozenset({"orders.submit", "orders.cancel"})
_QUERY_OPS = frozenset({"orders.query"})
_TELEMETRY_OPS = frozenset({"telemetry.append"})
_INGRESS_OPS = frozenset({"events.append"})
_MAX_RPC_BYTES = 262_144
_RPC_TIMEOUT_MS = 5_000
_HEARTBEAT_SECONDS = 5
_LEASE_SECONDS = 30
_RPC_LEASE_HEADROOM_SECONDS = (_RPC_TIMEOUT_MS / 1000) + 0.25
_MARKET_WIRE_VERSION = 2
_MARKET_HWM = 128
_MARKET_REPLAY_EVENTS = 2_048
_MARKET_REPLAY_BYTES = 1024 * 1024

_MARKET_ID = "load01-market-service"
_MARKET_PRINCIPAL = "load01-market-service-principal"
_MARKET_INSTANCE = "load01-market-hub"
_MARKET_SCOPE_REF = "load01-market-scope-v1"
_MARKET_SECURITY_REF = "load01-market-curve-v1"
_MARKET_LISTENER_REF = "load01-market-listener-v1"
_MARKET_CLIENT_REF = "load01-market-client-v1"

_DURABLE_ID = "load01-monitor-durable-service"
_DURABLE_PRINCIPAL = "load01-monitor-durable-principal"
_DURABLE_INSTANCE = "load01-monitor-durable-ingress"
_DURABLE_ROOT_SCOPE_REF = "load01-monitor-durable-root-v1"
_DURABLE_SECURITY_REF = "load01-monitor-durable-curve-v1"
_DURABLE_LISTENER_REF = "load01-monitor-durable-listener-v1"

_TELEMETRY_ID = "load01-monitor-telemetry-service"
_TELEMETRY_PRINCIPAL = "load01-monitor-telemetry-principal"
_TELEMETRY_INSTANCE = "load01-monitor-telemetry-ingress"
_TELEMETRY_SCOPE_REF = "load01-monitor-telemetry-scope-v1"
_TELEMETRY_SECURITY_REF = "load01-monitor-telemetry-curve-v1"
_TELEMETRY_LISTENER_REF = "load01-monitor-telemetry-listener-v1"

_ACCOUNT_SERVICE_PRINCIPAL = "load01-account-owner-principal"
_ACCOUNT_SECURITY_PREFIX = "load01-account-curve-"
_ACCOUNT_LISTENER_PREFIX = "load01-account-listener-"
_ACCOUNT_SCOPE_PREFIX = "load01-account-scope-"
_ACCOUNT_CLIENT_PREFIX = "load01-account-client-"
_ACCOUNT_INSTANCE_PREFIX = "load01-account-gateway-"
_ACCOUNT_SERVICE_PREFIX = "load01-account-service-"

_EXEC_SERVICE_PRINCIPAL = "load01-execution-owner-principal"
_EXEC_SECURITY_PREFIX = "load01-execution-curve-"
_EXEC_LISTENER_PREFIX = "load01-execution-listener-"
_EXEC_ORDER_SCOPE_PREFIX = "load01-execution-orders-"
_EXEC_QUERY_SCOPE_PREFIX = "load01-execution-query-"
_EXEC_INSTANCE_PREFIX = "load01-execution-owner-"
_EXEC_SERVICE_PREFIX = "load01-execution-service-"
_EXECUTION_DAY = "20261015"

_STRATEGY_PREFIX = "load01-strategy-"
_PRINCIPAL_PREFIX = "load01-principal-"
_MONITOR_SCOPE_PREFIX = "load01-monitor-scope-"
_MONITOR_OUTBOX_SCOPE_PREFIX = "load01-events-"
_RELAY_CLIENT_PREFIX = "load01-owner-relay-"
_RELAY_PRINCIPAL_PREFIX = "load01-owner-relay-principal-"
_RELAY_SECURITY_PREFIX = "load01-owner-relay-curve-"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _strategy_ref(index: int) -> str:
    return f"{_STRATEGY_PREFIX}{index:03d}"


def _principal_ref(index: int) -> str:
    return f"{_PRINCIPAL_PREFIX}{index:03d}"


class _Load01StartupLeaseError(RuntimeError):
    """A required client lease expired while the initial registrations ran."""

    code = "INITIAL_REGISTRATION_LEASE_EXPIRED"


class _RoundRobinMarketPollScheduler:
    """Give each configured stream one poll opportunity per scheduler turn."""

    def __init__(self, stream_ids: tuple[str, ...]) -> None:
        if len(set(stream_ids)) != len(stream_ids):
            raise ValueError("market stream IDs must be unique")
        self._stream_ids = stream_ids
        self._next_index = 0

    def next_stream_id(self, *, enabled: bool = True) -> str | None:
        if not enabled or not self._stream_ids:
            return None
        stream_id = self._stream_ids[self._next_index]
        self._next_index = (self._next_index + 1) % len(self._stream_ids)
        return stream_id


def _load01_has_started(now: float, scheduled_start: float) -> bool:
    return now >= scheduled_start


def _wait_until_load01_start(
    scheduled_start: float,
    *,
    stop_event,
    abort_event,
    maintain_if_due,
    clock=time.monotonic,
) -> bool:
    """Wait without market/order work, while keeping required leases alive."""

    while True:
        now = clock()
        if _load01_has_started(now, scheduled_start):
            return not stop_event.is_set() and not abort_event.is_set()
        if stop_event.is_set() or abort_event.is_set():
            return False
        maintain_if_due("pre-scheduled-load-start")
        remaining = max(0.0, scheduled_start - clock())
        if remaining > 0:
            stop_event.wait(min(0.05, remaining))


def _safe_diagnostic_code_value(value: Any) -> str:
    if (
        type(value) is str
        and 1 <= len(value) <= 64
        and value.isascii()
        and value.replace("_", "").isalnum()
    ):
        return value
    return "UNEXPECTED_ERROR"


def _bounded_diagnostic_text(value: Any, *, maximum: int = 128) -> str | None:
    if (
        type(value) is str
        and 1 <= len(value) <= maximum
        and value.isascii()
        and all(character.isalnum() or character in "-_.:" for character in value)
    ):
        return value
    return None


def _market_poll_failure_diagnostic(
    *,
    strategy_index: int,
    key: MarketSubscriptionKey,
    client: ServiceMarketClient,
    error: BaseException,
    started_monotonic: float,
    poll_duration_ms: float,
    load_start: float,
    market_seen: int,
    last_epoch: str | None,
    last_sequence: int | None,
    runtime: ServiceClientRuntime,
) -> dict[str, Any]:
    bootstrap = client.last_bootstrap
    return {
        "phase": "strategy-market-client-poll",
        "strategy_index": strategy_index,
        "stream_id": _bounded_diagnostic_text(key.stream_id),
        "observed_at_utc": _utc_now(),
        "elapsed_seconds": round(max(0.0, started_monotonic - load_start), 6),
        "poll_duration_ms": round(poll_duration_ms, 3),
        "safe_error_code": _safe_diagnostic_code(error),
        "service_market_error": isinstance(error, ServiceMarketError),
        "cause_code": _bounded_diagnostic_text(getattr(error, "cause_code", None)),
        "client": {
            "healthy": client.healthy,
            "recovery_required": client.recovery_required,
            "resync_required": client.resync_required,
            "queue_count": client.queue_count,
            "queue_bytes": client.queue_bytes,
            "last_bootstrap": (
                {
                    "cursor_epoch": _bounded_diagnostic_text(bootstrap.cursor_epoch),
                    "cursor_sequence": bootstrap.cursor_sequence,
                    "snapshot_epoch": _bounded_diagnostic_text(bootstrap.snapshot_epoch),
                    "snapshot_sequence": bootstrap.snapshot_sequence,
                    "snapshot_kind": _bounded_diagnostic_text(bootstrap.snapshot_kind),
                    "snapshot_complete": bootstrap.snapshot_complete,
                    "full_baseline_available": bootstrap.full_baseline_available,
                    "resync_required": bootstrap.resync_required,
                }
                if bootstrap is not None
                else None
            ),
        },
        "strategy_cursor": {
            "epoch": _bounded_diagnostic_text(last_epoch),
            "sequence": last_sequence,
            "market_events_seen": market_seen,
        },
        "runtime_lease": _runtime_lease_snapshot(runtime),
    }


class _StartupRuntimeMaintenanceProbe:
    """Observe public runtime calls and maintain due leases at RPC boundaries."""

    def __init__(self, runtime: Any, *, clock=time.monotonic) -> None:
        self.runtime = runtime
        self._clock = clock
        self._call = runtime.call
        self._maintain = runtime.maintain
        self._next_maintenance_delay = runtime.next_maintenance_delay
        self._market_binding_view = runtime.market_binding_view
        self._account_binding_view = runtime.account_binding_view
        self.phase = "registration"
        self._delay_checks = 0
        self._rpc_boundary_checks = 0
        self._rpc_calls_by_phase: Counter[str] = Counter()
        self._maintenance_calls_by_reason: Counter[str] = Counter()
        self._max_rpc_ms_by_operation: dict[str, float] = {}
        self._max_rpc_ms_by_phase: dict[str, float] = {}
        self._max_maintenance_ms = 0.0
        self._maintenance_failures = 0
        self._last_runtime_error_code: str | None = None
        self._last_maintenance_delay: float | None = None

        # ServiceMarketClient and ServiceAccountClient require the concrete
        # ServiceClientRuntime type. Bind wrappers to this instance so those
        # public facades keep their type contract while every call is observed.
        runtime.call = self.call
        runtime.maintain = self.maintain
        runtime.next_maintenance_delay = self.next_maintenance_delay
        runtime.market_binding_view = self.market_binding_view
        runtime.account_binding_view = self.account_binding_view

    def set_phase(self, phase: str) -> None:
        self.phase = phase

    def next_maintenance_delay(self) -> float | None:
        delay = self._next_maintenance_delay()
        self._delay_checks += 1
        self._last_maintenance_delay = delay
        return delay

    def _run_maintenance(self, reason: str) -> Any:
        began = self._clock()
        try:
            result = self._maintain()
        except BaseException as exc:
            self._maintenance_failures += 1
            self._last_runtime_error_code = _safe_diagnostic_code(exc)
            raise
        finally:
            elapsed_ms = (self._clock() - began) * 1000
            self._max_maintenance_ms = max(self._max_maintenance_ms, elapsed_ms)
            self._maintenance_calls_by_reason[reason] += 1
        return result

    def maintain(self) -> Any:
        return self._run_maintenance("client_refresh_or_worker")

    def _maintain_if_due(self, reason: str) -> None:
        delay = self.next_maintenance_delay()
        if delay is None:
            return
        if delay <= 0:
            self._run_maintenance(reason)

    def _require_healthy_after_boundary(self) -> None:
        if not self.runtime.healthy:
            self._last_runtime_error_code = "REQUIRED_LEASE_EXPIRED_AFTER_STARTUP_RPC"
            raise _Load01StartupLeaseError()

    def _binding_view(self, method, *args, **kwargs):
        self._maintain_if_due("binding_view")
        try:
            result = method(*args, **kwargs)
            self._require_healthy_after_boundary()
            return result
        except BaseException as exc:
            self._last_runtime_error_code = _safe_diagnostic_code(exc)
            raise

    def market_binding_view(self, *args, **kwargs):
        return self._binding_view(self._market_binding_view, *args, **kwargs)

    def account_binding_view(self, *args, **kwargs):
        return self._binding_view(self._account_binding_view, *args, **kwargs)

    def call(self, mount, operation, arguments, **kwargs):
        self._rpc_boundary_checks += 1
        self._maintain_if_due("rpc_preflight")
        self._require_healthy_after_boundary()
        phase = self.phase
        began = self._clock()
        try:
            result = self._call(mount, operation, arguments, **kwargs)
        except BaseException as exc:
            elapsed_ms = (self._clock() - began) * 1000
            self._record_rpc(phase, mount, operation, elapsed_ms)
            self._last_runtime_error_code = _safe_diagnostic_code(exc)
            raise
        elapsed_ms = (self._clock() - began) * 1000
        self._record_rpc(phase, mount, operation, elapsed_ms)
        self._require_healthy_after_boundary()
        self._rpc_boundary_checks += 1
        self._maintain_if_due("rpc_postflight")
        self._require_healthy_after_boundary()
        return result

    def _record_rpc(self, phase: str, mount: str, operation: str, elapsed_ms: float) -> None:
        operation_key = f"{mount}.{operation}"
        self._rpc_calls_by_phase[phase] += 1
        self._max_rpc_ms_by_operation[operation_key] = max(
            self._max_rpc_ms_by_operation.get(operation_key, 0.0), elapsed_ms
        )
        self._max_rpc_ms_by_phase[phase] = max(
            self._max_rpc_ms_by_phase.get(phase, 0.0), elapsed_ms
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "maintenance_delay_checks": self._delay_checks,
            "rpc_boundary_checks": self._rpc_boundary_checks,
            "rpc_calls_by_phase": dict(self._rpc_calls_by_phase),
            "maintenance_calls_by_reason": dict(self._maintenance_calls_by_reason),
            "rpc_max_duration_ms_by_operation": dict(self._max_rpc_ms_by_operation),
            "rpc_max_duration_ms_by_phase": dict(self._max_rpc_ms_by_phase),
            "maintenance_max_duration_ms": self._max_maintenance_ms,
            "maintenance_failure_count": self._maintenance_failures,
            "last_maintenance_delay_seconds": self._last_maintenance_delay,
            "last_runtime_error_code": self._last_runtime_error_code,
        }


def _runtime_lease_snapshot(runtime: Any) -> dict[str, Any]:
    now = time.monotonic()
    bindings = [
        {
            "mount": binding.mount,
            "required": binding.required,
            "active": binding.active,
            "remote_lease_inactive": binding.remote_lease_inactive,
            "lease_remaining_ms": round((binding.lease_deadline - now) * 1000, 3),
            "renewal_due_in_ms": round((binding.renewal_due_at - now) * 1000, 3),
        }
        for binding in runtime.bindings
    ]
    return {
        "runtime_state": runtime.state,
        "healthy": runtime.healthy,
        "cleanup_failures": runtime.cleanup_failures,
        "unregister_uncertainties": runtime.unregister_uncertainties,
        "unknown_registration_count": runtime.unknown_registration_count,
        "bindings": bindings,
        "required_min_lease_remaining_ms": min(
            (
                item["lease_remaining_ms"]
                for item in bindings
                if item["required"] and item["active"]
            ),
            default=None,
        ),
    }


def _safe_diagnostic_code(error: BaseException) -> str:
    return _safe_diagnostic_code_value(getattr(error, "code", None))


_LOAD01_RPC_RING_SIZE = 256
_LOAD01_RPC_TRACE: _Load01RpcTrace | None = None


class _Load01RpcTrace:
    """Bounded per-child trace for synthetic orders.submit/cancel RPCs."""

    def __init__(self, role: str) -> None:
        self.role, self.pid = role, os.getpid()
        self.events: deque[dict[str, Any]] = deque(maxlen=_LOAD01_RPC_RING_SIZE)
        self.sequence = self.total = self.overwritten = 0
        self.emit_ns_total = self.emit_ns_max = 0
        self.installation_error: str | None = None
        self.maintenance: dict[str, dict[str, int]] = {}
        self.server_sockets: dict[int, str] = {}
        self.dequeued_ns: dict[int, int] = {}
        self.local = threading.local()

    @staticmethod
    def now() -> int | None:
        try:
            return time.perf_counter_ns()
        except BaseException:
            return None

    def add(
        self, event: str, key: tuple[str, str, str], *, at: int | None = None,
        start: int | None = None, end: int | None = None, service: str | None = None,
        outcome: str | None = None, error: str | None = None,
    ) -> None:
        try:
            began = self.now()
            stamp = at if type(at) is int else end
            if type(stamp) is not int:
                return
            self.overwritten += len(self.events) == _LOAD01_RPC_RING_SIZE
            self.sequence += 1
            self.total += 1
            row = {
                "sequence": self.sequence, "event": event, "at_perf_counter_ns": stamp,
                "operation": key[0], "message_id": key[1], "correlation_id": key[2],
                "process_role": self.role, "pid": self.pid,
            }
            if key[0] == "orders.cancel":
                row["cancel_id"] = key[2]
            elif key[0] == "orders.submit":
                row["intent_id"] = key[2]
            if service is not None:
                row["service_id"] = service
            if outcome is not None:
                row["outcome"] = outcome
            if type(start) is int and type(end) is int:
                row["duration_ns"] = max(0, end - start)
            if error is not None:
                row["error_code"] = error
            self.events.append(row)
            cost = self.now()
            if type(began) is int and type(cost) is int:
                duration = max(0, cost - began)
                self.emit_ns_total += duration
                self.emit_ns_max = max(self.emit_ns_max, duration)
        except BaseException:
            pass

    def maintain(self, name: str, start: int | None, end: int | None) -> None:
        if type(start) is not int or type(end) is not int:
            return
        try:
            row = self.maintenance.setdefault(name, {"count": 0, "total_ns": 0, "max_ns": 0})
            duration = max(0, end - start)
            row["count"] += 1
            row["total_ns"] += duration
            row["max_ns"] = max(row["max_ns"], duration)
        except BaseException:
            pass

    def snapshot(self, limit: int = 32) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= _LOAD01_RPC_RING_SIZE:
            raise ValueError("LOAD01 RPC trace snapshot limit is invalid")
        info = time.get_clock_info("perf_counter")
        return {
            "process_role": self.role, "pid": self.pid, "clock": "time.perf_counter_ns",
            "python_version": ".".join(str(part) for part in sys.version_info[:3]),
            "clock_facts": {
                "implementation": info.implementation, "monotonic": info.monotonic,
                "adjustable": info.adjustable, "resolution_seconds": info.resolution,
                "cross_process_domain": "same Windows host; Python 3.10+",
            },
            "ring_capacity": _LOAD01_RPC_RING_SIZE, "event_count_total": self.total,
            "event_count_overwritten": self.overwritten,
            "event_recording_overhead_measured_ns_total": self.emit_ns_total,
            "event_recording_overhead_measured_ns_max": self.emit_ns_max,
            "installation_error": self.installation_error,
            "overhead_note": "Measured record construction/append; clock reads, wrapper dispatch, "
            "maintenance timing, and output serialization add further cost.",
            "maintenance_durations": {key: dict(value) for key, value in self.maintenance.items()},
            "events": list(self.events)[-limit:],
        }


def _load01_trace_snapshot_or_none(
    trace: _Load01RpcTrace, max_events: int = 32
) -> dict[str, Any] | None:
    try:
        return trace.snapshot(max_events)
    except BaseException:
        return None


def _send_load01_rpc_diagnostic(
    control: Any, role: str, pid: int, snapshot: Any, stage: str
) -> None:
    if type(snapshot) is not dict:
        return
    with suppress(BaseException):
        control.send(("rpc-diagnostic", role, pid, snapshot, stage))


def _load01_order_key(request: Any) -> tuple[str, str, str] | None:
    try:
        header = request.header
        operation = header.operation
        if operation not in _ORDER_OPS:
            return None
        payload = request.payload
        args = payload.get("arguments") if isinstance(payload, Mapping) else None
        if not isinstance(args, Mapping):
            return None
        message_id = _bounded_diagnostic_text(header.message_id, maximum=128)
        argument_name = "cancel_id" if operation == "orders.cancel" else "intent_id"
        correlation_id = _bounded_diagnostic_text(args.get(argument_name), maximum=128)
        return (operation, message_id, correlation_id) if message_id and correlation_id else None
    except BaseException:
        return None


def _install_load01_rpc_trace_impl(process_role: str) -> _Load01RpcTrace:
    """Install pass-through hooks in a synthetic child; non-order calls fast-path."""
    global _LOAD01_RPC_TRACE
    if _LOAD01_RPC_TRACE is not None:
        return _LOAD01_RPC_TRACE
    import bt_api_transport_zmq.service_v2 as service_v2

    trace = _Load01RpcTrace(process_role)
    state = trace.local
    socket_type = zmq.Socket
    send, send_multipart = socket_type.send, socket_type.send_multipart
    receive = service_v2._BoundedMultipartReceiver.receive
    request = service_v2.ZmqServiceClient.request
    server_init = service_v2.ZmqServiceServer.__init__
    client_maintain = ServiceClientRuntime.maintain
    host_maintain = ServiceHost.maintain
    component_maintain = ServiceHost._maintain_components

    def safe_error_code(exc: BaseException) -> str:
        try:
            return _safe_diagnostic_code(exc)
        except BaseException:
            return "UNEXPECTED_ERROR"

    def measured_send(original, event: str, context, sock, args, kwargs):
        start = trace.now()
        try:
            result = original(sock, *args, **kwargs)
        except zmq.Again:
            trace.add(event, context["key"], start=start, end=trace.now(),
                      service=context.get("service"), outcome="again")
            raise
        except BaseException as exc:
            trace.add(event, context["key"], start=start, end=trace.now(),
                      service=context.get("service"), outcome="error",
                      error=safe_error_code(exc))
            raise
        trace.add(event, context["key"], start=start, end=trace.now(),
                  service=context.get("service"), outcome="sent")
        return result

    def send_wrapper(sock, *args, **kwargs):
        context = getattr(state, "client_request", None)
        try:
            matches = context is not None and context["client"]._socket is sock
        except BaseException:
            matches = False
        if not matches:
            return send(sock, *args, **kwargs)
        return measured_send(send, "client_send", context, sock, args, kwargs)

    def send_multipart_wrapper(sock, *args, **kwargs):
        context = getattr(state, "server_request", None)
        if context is None or context["socket_id"] != id(sock):
            return send_multipart(sock, *args, **kwargs)
        try:
            return measured_send(send_multipart, "reply_send", context, sock, args, kwargs)
        finally:
            state.server_request = None

    def receive_wrapper(receiver, *args, **kwargs):
        frames = receive(receiver, *args, **kwargs)
        try:
            socket_id = id(receiver._socket)
            if frames is not None and socket_id in trace.server_sockets:
                at = trace.now()
                if type(at) is int:
                    trace.dequeued_ns[socket_id] = at
        except BaseException:
            pass
        return frames

    def request_wrapper(client, envelope, *, timeout):
        key = _load01_order_key(envelope)
        if key is None:
            return request(client, envelope, timeout=timeout)
        prior = getattr(state, "client_request", None)
        state.client_request = {"key": key, "client": client}
        start = trace.now()
        try:
            result = request(client, envelope, timeout=timeout)
        except BaseException as exc:
            try:
                error = _bounded_diagnostic_text(getattr(exc, "cause_code", None))
                error = error or safe_error_code(exc)
            except BaseException:
                error = "UNEXPECTED_ERROR"
            trace.add(
                "client_outcome", key, start=start, end=trace.now(),
                outcome="timeout" if isinstance(exc, TimeoutError) else "exception",
                error=error,
            )
            raise
        else:
            trace.add("client_outcome", key, start=start, end=trace.now(), outcome="reply")
            return result
        finally:
            state.client_request = prior

    def server_init_wrapper(server, *args, **kwargs):
        handler = args[3] if len(args) >= 4 else kwargs.get("handler")
        if not callable(handler):
            return server_init(server, *args, **kwargs)

        def handler_wrapper(principal, envelope):
            key = _load01_order_key(envelope)
            socket_id = id(server._socket)
            if key is None:
                active = getattr(state, "server_request", None)
                if active is not None and active["socket_id"] == socket_id:
                    state.server_request = None
                return handler(principal, envelope)
            service = trace.server_sockets.get(socket_id)
            dequeued = trace.dequeued_ns.pop(socket_id, None)
            if type(dequeued) is int:
                trace.add("server_dequeue", key, at=dequeued, service=service)
            start = trace.now()
            state.server_request = {"socket_id": socket_id, "key": key, "service": service}
            trace.add("handler_enter", key, at=start, service=service)
            try:
                result = handler(principal, envelope)
            except BaseException as exc:
                trace.add("handler_return", key, start=start, end=trace.now(), service=service,
                          outcome="raised", error=safe_error_code(exc))
                raise
            trace.add("handler_return", key, start=start, end=trace.now(), service=service,
                      outcome="returned")
            return result

        args = list(args)
        kwargs = dict(kwargs)
        if len(args) >= 4:
            args[3] = handler_wrapper
        else:
            kwargs["handler"] = handler_wrapper
        result = server_init(server, *args, **kwargs)
        try:
            service = _bounded_diagnostic_text(server._identity.actor_ref, maximum=128)
            trace.server_sockets[id(server._socket)] = service or "unknown"
        except BaseException:
            pass
        return result

    def timed_maintain(original, label):
        def wrapper(owner, *args, **kwargs):
            try:
                identity = getattr(owner, "identity", None)
                if identity is None:
                    identity = getattr(getattr(owner, "_authority", None), "identity", None)
                service = _bounded_diagnostic_text(getattr(identity, "actor_ref", None)) or "unknown"
            except BaseException:
                service = "unknown"
            start = trace.now()
            try:
                return original(owner, *args, **kwargs)
            finally:
                trace.maintain(f"{label}:{service}", start, trace.now())
        return wrapper

    patches = (
        (socket_type, "send", send, send_wrapper),
        (socket_type, "send_multipart", send_multipart, send_multipart_wrapper),
        (service_v2._BoundedMultipartReceiver, "receive", receive, receive_wrapper),
        (service_v2.ZmqServiceClient, "request", request, request_wrapper),
        (service_v2.ZmqServiceServer, "__init__", server_init, server_init_wrapper),
        (ServiceClientRuntime, "maintain", client_maintain,
         timed_maintain(client_maintain, "service_runtime.maintain")),
        (ServiceHost, "maintain", host_maintain,
         timed_maintain(host_maintain, "service_host.maintain")),
        (ServiceHost, "_maintain_components", component_maintain,
         timed_maintain(component_maintain, "service_host._maintain_components")),
    )
    applied = []
    try:
        for owner, name, original, replacement in patches:
            setattr(owner, name, replacement)
            applied.append((owner, name, original))
    except BaseException as exc:
        for owner, name, original in reversed(applied):
            try:
                setattr(owner, name, original)
            except BaseException:
                pass
        trace.installation_error = type(exc).__name__
    _LOAD01_RPC_TRACE = trace
    return trace


def _install_load01_rpc_trace(process_role: str) -> _Load01RpcTrace:
    try:
        return _install_load01_rpc_trace_impl(process_role)
    except BaseException as exc:
        trace = _Load01RpcTrace(process_role)
        trace.installation_error = type(exc).__name__
        return trace


def _load01_rpc_timing_report(snapshots: Mapping[str, Any]) -> dict[str, Any]:
    by_request: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    processes, seen = [], set()
    for name, values in sorted(snapshots.items()):
        group = values if type(values) is list else [values]
        group = [value for value in group if type(value) is dict]
        if not group:
            continue
        latest = max(group, key=lambda value: value.get("event_count_total", -1))
        processes.append({key: latest.get(key) for key in (
            "process_role", "pid", "clock", "python_version", "clock_facts",
            "installation_error",
            "event_count_total", "event_count_overwritten",
            "event_recording_overhead_measured_ns_total",
            "event_recording_overhead_measured_ns_max", "maintenance_durations",
        )} | {"name": name})
        for value in group:
            for event in value.get("events", ()):
                key = (
                    event.get("operation"), event.get("message_id"),
                    event.get("correlation_id"),
                )
                marker = (event.get("pid"), event.get("sequence"))
                if all(type(item) is str for item in key) and marker not in seen:
                    seen.add(marker)
                    by_request.setdefault(key, []).append(event)

    requests = []
    for (operation, message_id, correlation_id), events in sorted(by_request.items()):
        events.sort(key=lambda event: event["at_perf_counter_ns"])
        stamps = {}
        for event in events:
            stamps.setdefault(event["event"], event["at_perf_counter_ns"])
        def duration(first, last):
            start, end = stamps.get(first), stamps.get(last)
            return round((end - start) / 1_000_000, 3) if (
                type(start) is int and type(end) is int and end >= start
            ) else None
        requests.append({
            "operation": operation, "message_id": message_id,
            "correlation_id": correlation_id,
            **({"cancel_id": correlation_id} if operation == "orders.cancel" else {}),
            **({"intent_id": correlation_id} if operation == "orders.submit" else {}),
            "events": events,
            "durations_ms": {
                "client_send_to_server_dequeue": duration("client_send", "server_dequeue"),
                "dequeue_to_handler_including_auth_decode_validation": duration(
                    "server_dequeue", "handler_enter"
                ),
                "handler": duration("handler_enter", "handler_return"),
                "handler_return_to_reply_send": duration("handler_return", "reply_send"),
                "reply_send_to_client_outcome": duration("reply_send", "client_outcome"),
                "client_send_to_client_outcome": duration("client_send", "client_outcome"),
            },
        })
    return {
        "ring_capacity_per_process": _LOAD01_RPC_RING_SIZE,
        "event_filter": "orders.submit and orders.cancel only",
        "clock_note": "perf_counter_ns; same-host cross-process comparison is supported by Python 3.10+",
        "hook_overhead_note": "Wrappers and boundary clock reads add runtime cost; measured record "
        "construction/append is reported per process, excluding wrapper and clock-read costs.",
        "processes": processes, "correlated_requests": requests,
    }

def _market_keys() -> tuple[MarketSubscriptionKey, ...]:
    return tuple(
        MarketSubscriptionKey(
            source_ref="load01-synthetic-source",
            entitlement_ref="load01-public-entitlement",
            instrument=MarketInstrumentKey(
                provider="synthetic",
                venue="loopback",
                environment="paper",
                product_type="spot",
                instrument_id=f"load01-instrument-{index:02d}",
                native_symbol=f"LOAD{index:02d}-USD",
            ),
            channel="trades",
        )
        for index in range(_TOPICS)
    )


def _market_service_config(rpc_endpoint: str, market_endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _MARKET_ID,
        "scope_ref": "load01-market-service-root-v1",
        "transport_ref": _MARKET_LISTENER_REF,
        "registration_policy_ref": "load01-market-policy-v1",
        "service_requirements_ref": "load01-market-requirements-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _MARKET_INSTANCE,
                    "bind_ports": ["service.market_source"],
                    "options": {"role": "market_hub"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _MARKET_LISTENER_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": rpc_endpoint, "market_bind": market_endpoint},
                    "security_profile_ref": _MARKET_SECURITY_REF,
                    "options": {
                        "high_water_mark": _MARKET_HWM,
                        "max_message_bytes": _MAX_RPC_BYTES,
                        "market_wire_version": _MARKET_WIRE_VERSION,
                    },
                }
            },
        },
    }


def _account_service_info(index: int, endpoint: str, server_public: bytes) -> dict[str, Any]:
    return {
        "index": index,
        "account_ref": _ACCOUNT_REFS[index],
        "endpoint": endpoint,
        "server_public": server_public,
        "service_id": f"{_ACCOUNT_SERVICE_PREFIX}{index:02d}",
        "instance_id": f"{_ACCOUNT_INSTANCE_PREFIX}{index:02d}",
        "scope_ref": f"{_ACCOUNT_SCOPE_PREFIX}{index:02d}-v1",
        "security_ref": f"{_ACCOUNT_SECURITY_PREFIX}{index:02d}v1",
        "listener_ref": f"{_ACCOUNT_LISTENER_PREFIX}{index:02d}v1",
        "peer": WireIdentity(
            WireRole.SERVICE,
            _ACCOUNT_SERVICE_PRINCIPAL,
            f"{_ACCOUNT_SERVICE_PREFIX}{index:02d}",
        ),
    }


def _execution_service_info(index: int, endpoint: str, server_public: bytes) -> dict[str, Any]:
    return {
        "index": index,
        "account_ref": _ACCOUNT_REFS[index],
        "endpoint": endpoint,
        "server_public": server_public,
        "service_id": f"{_EXEC_SERVICE_PREFIX}{index:02d}",
        "instance_id": f"{_EXEC_INSTANCE_PREFIX}{index:02d}",
        "order_scope_ref": f"{_EXEC_ORDER_SCOPE_PREFIX}{index:02d}-v1",
        "query_scope_ref": f"{_EXEC_QUERY_SCOPE_PREFIX}{index:02d}-v1",
        "security_ref": f"{_EXEC_SECURITY_PREFIX}{index:02d}v1",
        "listener_ref": f"{_EXEC_LISTENER_PREFIX}{index:02d}v1",
        "peer": WireIdentity(
            WireRole.SERVICE,
            _EXEC_SERVICE_PRINCIPAL,
            f"{_EXEC_SERVICE_PREFIX}{index:02d}",
        ),
    }


def _account_service_config(service: dict[str, Any], endpoint: str) -> dict[str, Any]:
    index = service["index"]
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": service["service_id"],
        "scope_ref": f"load01-account-service-root-{index:02d}-v1",
        "transport_ref": service["listener_ref"],
        "registration_policy_ref": f"load01-account-policy-{index:02d}-v1",
        "service_requirements_ref": f"load01-account-requirements-{index:02d}-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": service["instance_id"],
                    "bind_ports": ["service.account_read", "service.provider_session"],
                    "options": {
                        "role": "account_session_worker",
                        "account_session_ref": f"load01-session-{index:02d}-v1",
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                service["listener_ref"]: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": service["security_ref"],
                    "options": {"high_water_mark": 64, "max_message_bytes": _MAX_RPC_BYTES},
                }
            },
        },
    }


def _execution_service_config(service: dict[str, Any]) -> dict[str, Any]:
    index = service["index"]
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": service["service_id"],
        "scope_ref": f"load01-execution-service-root-{index:02d}-v1",
        "transport_ref": service["listener_ref"],
        "registration_policy_ref": f"load01-execution-policy-{index:02d}-v1",
        "service_requirements_ref": f"load01-execution-requirements-{index:02d}-v1",
        "components": {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": service["instance_id"],
                    "bind_ports": ["service.order_entry", "service.order_query"],
                    "options": {"role": "account_owner"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                service["listener_ref"]: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": service["endpoint"]},
                    "security_profile_ref": service["security_ref"],
                    "options": {"high_water_mark": 64, "max_message_bytes": _MAX_RPC_BYTES},
                }
            },
        },
    }


def _monitor_service_config(
    *,
    service_id: str,
    scope_ref: str,
    instance_id: str,
    listener_ref: str,
    endpoint: str,
    security_ref: str,
    role: str,
) -> dict[str, Any]:
    if role == "durable_ingress":
        options = {
            "role": role,
            "max_events": _RELAY_MAX_EVENTS,
            "max_bytes": _RELAY_MAX_BYTES,
            "max_event_bytes": 16_384,
        }
    else:
        options = {
            "role": role,
            "max_streams": 256,
            "max_bytes": 16 * 1024 * 1024,
            "heartbeat_timeout_seconds": 90,
            "retention_seconds": 3600,
        }
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": service_id,
        "scope_ref": scope_ref,
        "transport_ref": listener_ref,
        "registration_policy_ref": f"{scope_ref}-policy",
        "service_requirements_ref": f"{scope_ref}-requirements",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": instance_id,
                    "bind_ports": ["service.telemetry_ingress"],
                    "options": options,
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                listener_ref: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": security_ref,
                    "options": {"high_water_mark": 128, "max_message_bytes": _MAX_RPC_BYTES},
                }
            },
        },
    }


def _durable_scope_ref(account_index: int, local_index: int) -> str:
    return f"{_MONITOR_SCOPE_PREFIX}{account_index:02d}-{local_index:02d}-v1"


def _outbox_scope(account_index: int, local_index: int) -> str:
    return f"{_MONITOR_OUTBOX_SCOPE_PREFIX}{account_index:02d}-{local_index:02d}"


def _owner_relay_config(
    account_index: int,
    monitor_endpoint: str,
    unused_listener_endpoint: str,
) -> dict[str, Any]:
    client_ref = f"{_RELAY_CLIENT_PREFIX}{account_index:02d}-v1"
    bindings = [
        {
            "mount": "upstream.telemetry",
            "service_id": _DURABLE_ID,
            "instance_id": _DURABLE_INSTANCE,
            "scope_ref": _durable_scope_ref(account_index, local_index),
            "transport_ref": client_ref,
        }
        for local_index in range(_PER_ACCOUNT)
    ]
    profiles: dict[str, Any] = {
        f"owner-relay-unused-listener-{account_index:02d}-v1": {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_bind": unused_listener_endpoint},
            "security_profile_ref": f"owner-relay-unused-server-{account_index:02d}-v1",
            "options": {"high_water_mark": 32, "max_message_bytes": _MAX_RPC_BYTES},
        },
        client_ref: {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": monitor_endpoint},
            "security_profile_ref": _DURABLE_SECURITY_REF,
            "options": {
                "request_timeout_ms": _RPC_TIMEOUT_MS,
                "high_water_mark": 32,
                "max_message_bytes": _MAX_RPC_BYTES,
            },
        },
    }
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": f"load01-owner-relay-config-{account_index:02d}",
        "scope_ref": f"load01-owner-relay-config-scope-{account_index:02d}-v1",
        "transport_ref": f"owner-relay-unused-listener-{account_index:02d}-v1",
        "registration_policy_ref": f"load01-owner-relay-config-policy-{account_index:02d}",
        "service_requirements_ref": f"load01-owner-relay-config-req-{account_index:02d}",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": f"owner-relay-unused-monitor-{account_index:02d}",
                    "bind_ports": ["service.telemetry_ingress"],
                    "options": {"role": "durable_ingress"},
                },
            }
        },
        "clients": {
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": bindings,
                    "options": {"request_timeout_ms": _RPC_TIMEOUT_MS},
                },
            }
        },
        "communication": {"config_schema_version": 1, "profiles": profiles},
    }


def _strategy_config(
    index: int,
    account_service: dict[str, Any],
    execution_service: dict[str, Any],
    market_rpc: str,
    market_pub: str,
    telemetry_rpc: str,
) -> dict[str, Any]:
    account_index = index // _PER_ACCOUNT
    account_ref = _ACCOUNT_REFS[account_index]
    market_transport = _MARKET_CLIENT_REF
    account_transport = f"{_ACCOUNT_CLIENT_PREFIX}{account_index:02d}-v1"
    execution_transport = f"load01-execution-client-{account_index:02d}-v1"
    telemetry_transport = "load01-telemetry-client-v1"
    bindings = [
        {
            "mount": "store.market",
            "service_id": _MARKET_ID,
            "instance_id": _MARKET_INSTANCE,
            "scope_ref": _MARKET_SCOPE_REF,
            "transport_ref": market_transport,
        },
        {
            "mount": "store.account",
            "service_id": account_service["service_id"],
            "instance_id": account_service["instance_id"],
            "scope_ref": account_service["scope_ref"],
            "account_ref": account_ref,
            "transport_ref": account_transport,
        },
        {
            "mount": "broker.orders",
            "service_id": execution_service["service_id"],
            "instance_id": execution_service["instance_id"],
            "scope_ref": execution_service["order_scope_ref"],
            "account_ref": account_ref,
            "transport_ref": execution_transport,
        },
        {
            "mount": "broker.order_query",
            "service_id": execution_service["service_id"],
            "instance_id": execution_service["instance_id"],
            "scope_ref": execution_service["query_scope_ref"],
            "account_ref": account_ref,
            "transport_ref": execution_transport,
        },
        {
            "mount": "runtime.telemetry",
            "service_id": _TELEMETRY_ID,
            "instance_id": _TELEMETRY_INSTANCE,
            "scope_ref": _TELEMETRY_SCOPE_REF,
            "transport_ref": telemetry_transport,
            "required": False,
        },
    ]
    profiles: dict[str, Any] = {
        market_transport: {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": market_rpc, "market_connect": market_pub},
            "security_profile_ref": _MARKET_SECURITY_REF,
            "options": {
                "request_timeout_ms": _RPC_TIMEOUT_MS,
                "high_water_mark": _MARKET_HWM,
                "max_message_bytes": _MAX_RPC_BYTES,
                "market_wire_version": _MARKET_WIRE_VERSION,
            },
        },
        account_transport: {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": account_service["endpoint"]},
            "security_profile_ref": account_service["security_ref"],
            "options": {"request_timeout_ms": _RPC_TIMEOUT_MS},
        },
        execution_transport: {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": execution_service["endpoint"]},
            "security_profile_ref": execution_service["security_ref"],
            "options": {"request_timeout_ms": _RPC_TIMEOUT_MS},
        },
        telemetry_transport: {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": telemetry_rpc},
            "security_profile_ref": _TELEMETRY_SECURITY_REF,
            "options": {"request_timeout_ms": _RPC_TIMEOUT_MS},
        },
    }
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": _strategy_ref(index)},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": bindings[:2],
                    "options": {
                        "request_timeout_ms": _RPC_TIMEOUT_MS,
                        "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
                    },
                },
            },
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": bindings[2:4],
                    "options": {"request_timeout_ms": _RPC_TIMEOUT_MS},
                },
            },
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": bindings[4:],
                    "options": {
                        "request_timeout_ms": _RPC_TIMEOUT_MS,
                        "telemetry_queue_max_events": 16,
                        "telemetry_queue_max_bytes": 65_536,
                    },
                },
            },
        },
        "communication": {"config_schema_version": 1, "profiles": profiles},
    }


def _market_authority_audience(strategy_keys: dict[int, tuple[bytes, bytes]]):
    return tuple(
        (
            strategy_keys[index][0],
            _principal_ref(index),
            _strategy_ref(index),
            _ACCOUNT_REFS[index // _PER_ACCOUNT],
        )
        for index in range(_STRATEGIES)
    )


def _sample_intent(intent_id: str) -> dict[str, Any]:
    return {
        "intent_id": intent_id,
        "signal_id": f"signal-{intent_id}",
        "instrument": "LOAD01-USD",
        "side": "BUY",
        "position_effect": "OPEN",
        "order_type": "LIMIT",
        "quantity": "1",
        "price": "1",
        "time_in_force": "GTC",
        "reduce_only": False,
        "metadata_version": "load01-synthetic-v1",
        "deadline_ns": None,
        "tags": {"scenario": "LOAD01", "synthetic": "true"},
    }


class _SyntheticOrderProvider:
    def __init__(self) -> None:
        self.submitted = 0
        self.cancelled = 0

    def submit(self, intent: OrderIntent) -> ProviderObservation:
        self.submitted += 1
        return ProviderObservation.accepted(intent.intent_id, f"paper-{intent.intent_id}")

    def cancel(self, intent: Any) -> CancelObservation:
        self.cancelled += 1
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


class _SyntheticAdmission:
    def reserve(self, intent: Any) -> Any:
        return _SyntheticPermit(
            f"load01-permit-{getattr(intent, 'intent_id', None) or intent.cancel_id}"
        )

    def validate(self, permit_id: str, _intent: Any) -> Any:
        return _SyntheticPermit(permit_id)

    def claim_for_dispatch(self, permit_id: str, _intent: OrderIntent) -> Any:
        return _SyntheticPermit(permit_id)

    def claim_before_dispatch(self, permit_id: str, intent: Any):
        return CancelDispatchClaimReceiptV1(
            permit_reference=permit_id,
            scope_key=intent.scope.key,
            cancel_id=intent.cancel_id,
            intent_fingerprint=intent.fingerprint,
        )

    def release(self, _permit_id: str, _reason: str) -> None:
        return None

    def settle(self, _permit_id: str) -> None:
        return None


class _SyntheticPermit:
    def __init__(self, permit_id: str) -> None:
        self.permit_id = permit_id


class _CountedMarketProvider:
    def __init__(self, keys: tuple[MarketSubscriptionKey, ...]) -> None:
        self.keys = frozenset(keys)
        self.starts: Counter[str] = Counter()
        self.stops: Counter[str] = Counter()
        self.generations: dict[str, str] = {}
        self.hub: GatewayMarketHub | None = None
        self.source_sequence: Counter[str] = Counter()
        self.source_payload_bytes = 0
        self.first_publish_ns: int | None = None
        self.last_publish_ns: int | None = None

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        if key not in self.keys or key.stream_id in self.generations or not generation:
            raise AssertionError("synthetic upstream key was incompatible or duplicated")
        self.starts[key.stream_id] += 1
        self.generations[key.stream_id] = generation
        return (key.stream_id, generation)

    def unsubscribe(self, handle: object) -> bool:
        if not isinstance(handle, tuple) or len(handle) != 2:
            raise AssertionError("market provider received an invalid handle")
        stream_id, generation = handle
        if self.generations.get(stream_id) != generation:
            raise AssertionError("market provider received a stale generation handle")
        self.stops[stream_id] += 1
        del self.generations[stream_id]
        return True

    def publish_one(self, key: MarketSubscriptionKey, publish_ns: int) -> int:
        if self.hub is None or self.generations.get(key.stream_id) is None:
            raise AssertionError("market hub did not retain its one synthetic source")
        self.source_sequence[key.stream_id] += 1
        ordinal = self.source_sequence[key.stream_id]
        data = {
            "source": "load01-synthetic-provider",
            "topic": key.stream_id,
            "ordinal": ordinal,
            "publish_ns": publish_ns,
            "padding": "x" * 160,
        }
        self.source_payload_bytes += len(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        if self.first_publish_ns is None:
            self.first_publish_ns = publish_ns
        self.last_publish_ns = publish_ns
        event = self.hub.publish(
            key,
            data,
            _utc_now(),
            generation=self.generations[key.stream_id],
        )
        return event.header.sequence


def _fixed_source_root() -> Path:
    override = os.environ.get("BT_API_LOAD01_SOURCE_ROOT")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_fixed_source_manifest(manifest_path: str | Path) -> dict[str, Any]:
    root = _fixed_source_root()
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("parent_commit") != _FIXED_PARENT:
        raise AssertionError("LOAD01 source manifest parent commit does not match")
    checked = {}
    module_names = (
        "bt_api_py.service_runtime",
        "bt_api_py.service_host",
        "bt_api_py.service_market",
        "bt_api_py.service_account",
        "bt_api_py.service_telemetry",
        "bt_api_py.service_execution_monitor",
        "bt_api_py.service_components",
        "bt_api_py.service_config",
        "bt_api_base.service_protocol",
        "bt_api_base.market_protocol",
        "bt_api_base.account_protocol",
        "bt_api_gateway.market_hub",
        "bt_api_gateway.account_session_worker",
        "bt_api_gateway.service_component",
        "bt_api_execution.store",
        "bt_api_execution.service_order_entry",
        "bt_api_execution.journal_reader",
        "bt_api_execution.contracts",
        "bt_api_monitor.durable",
        "bt_api_monitor.service_ingress",
        "bt_api_monitor.service_telemetry",
        "bt_api_transport_zmq.service_v2",
        "bt_api_transport_zmq.market_v2",
        "bt_api_transport_zmq.client",
        "bt_api_transport_zmq.wire",
        "bt_api_transport_zmq.events",
        "tests.unit.test_service_gateway_hundred_strategy_integration",
    )
    import importlib

    file_pins = manifest["files"]
    for name in module_names:
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            raise AssertionError(f"LOAD01 imported a non-fixed source module: {name}") from None
        expected = file_pins.get(relative)
        actual = _sha256(path)
        if expected is None or expected != actual:
            raise AssertionError(f"LOAD01 fixed source digest mismatch: {relative}")
        checked[name] = {"path": relative, "sha256": actual}
    runner = Path(__file__).resolve()
    if not runner.is_relative_to(root):
        raise AssertionError("LOAD01 runner did not load from the fixed source export")
    runner_relative = runner.relative_to(root).as_posix()
    if file_pins.get(runner_relative) != _sha256(runner):
        raise AssertionError("LOAD01 runner digest does not match its fixed-source manifest")
    checked["load_runner"] = {
        "path": runner_relative,
        "sha256": _sha256(runner),
    }
    return checked


def _verify_fixed_archives(root: Path, pin_path: Path) -> dict[str, Any]:
    pins = json.loads(pin_path.read_text(encoding="utf-8"))
    archives = [("parent.zip", root, pins["parent_archive_sha256"], pins["parent_archive_bytes"])]
    for package in pins["package_gitlinks"]:
        archive_name = Path(package["path"]).name + ".zip"
        package_root = root / package["path"]
        archives.append(
            (archive_name, package_root, package["archive_sha256"], package["archive_bytes"])
        )
    checked_py_files = 0
    for name, source_root, expected_digest, expected_bytes in archives:
        archive_path = pin_path.parent / name
        if (
            not archive_path.is_file()
            or archive_path.stat().st_size != expected_bytes
            or _sha256(archive_path) != expected_digest
        ):
            raise AssertionError(f"LOAD01 fixed source archive pin mismatch: {name}")
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if member.is_dir() or not member.filename.endswith(".py"):
                    continue
                extracted = source_root / Path(member.filename)
                if not extracted.is_file():
                    raise AssertionError(f"LOAD01 committed source file missing: {member.filename}")
                archived_bytes = archive.read(member)
                if _sha256(extracted) != hashlib.sha256(archived_bytes).hexdigest():
                    raise AssertionError(
                        f"LOAD01 extracted committed source changed: {member.filename}"
                    )
                checked_py_files += 1
    return {"archive_count": len(archives), "python_file_count": checked_py_files}


def _write_source_run_manifest(root: Path, work_dir: Path) -> Path:
    source_pin_path = Path(os.environ["BT_API_LOAD01_EXPORT_PINS"]).resolve()
    _verify_fixed_archives(root, source_pin_path)
    export_pins = json.loads(source_pin_path.read_text(encoding="utf-8"))
    expected_gitlinks = _FIXED_PACKAGE_GITLINKS
    actual_gitlinks = {item["path"]: item["commit"] for item in export_pins["package_gitlinks"]}
    if export_pins.get("parent_commit") != _FIXED_PARENT or actual_gitlinks != expected_gitlinks:
        raise AssertionError("LOAD01 exported parent/package refs differ from the accepted pin set")

    roots = (
        root / "bt_api_py",
        root / "bt_api" / "bt_api_base" / "src",
        root / "bt_api" / "bt_api_execution" / "src",
        root / "bt_api" / "bt_api_gateway" / "src",
        root / "bt_api" / "bt_api_monitor" / "src",
        root / "bt_api" / "bt_api_risk" / "src",
        root / "bt_api" / "bt_api_transport_zmq" / "src",
    )
    paths: set[Path] = set()
    for source_root in roots:
        paths.update(path for path in source_root.rglob("*.py") if "__pycache__" not in path.parts)
    for relative in (
        Path("tests/unit/test_service_gateway_hundred_strategy_integration.py"),
        Path("tests/unit/test_load01_hundred_strategy_30min.py"),
    ):
        paths.add(root / relative)
    file_pins = {path.relative_to(root).as_posix(): _sha256(path) for path in sorted(paths)}
    manifest = {
        "schema_version": 1,
        "parent_commit": _FIXED_PARENT,
        "parent_archive_sha256": export_pins["parent_archive_sha256"],
        "package_gitlinks": export_pins["package_gitlinks"],
        "runner_path": Path(__file__).resolve().relative_to(root).as_posix(),
        "runner_sha256": _sha256(Path(__file__).resolve()),
        "files": file_pins,
    }
    manifest_path = work_dir / "fixed-source-run-manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
    return manifest_path


def _market_host_process(
    control,
    raw_config,
    registry_path,
    server_keys,
    audience,
    market_keys,
    load_start_event,
    load_start_value,
    stop_event,
):
    _check_fixed_source_manifest(os.environ["BT_API_LOAD01_SOURCE_MANIFEST"])
    config = parse_service_config(raw_config)
    service_identity = WireIdentity(WireRole.SERVICE, _MARKET_PRINCIPAL, _MARKET_ID)
    service_scope = WireScope(_DOMAIN)
    principals = {public: principal for public, principal, _strategy, _account in audience}
    grants = {}
    all_keys = frozenset(market_keys)
    for _public, principal, strategy_ref, account_ref in audience:
        scope = WireScope(_DOMAIN, account_ref=account_ref, strategy_ref=strategy_ref)
        grant = ResourceGrant(
            _MARKET_INSTANCE,
            "service.market_source",
            _MARKET_SCOPE_REF,
            scope,
            _MARKET_OPS,
        )
        grants[principal] = (
            PrincipalGrant(
                WireIdentity(WireRole.STRATEGY_CLIENT, principal, strategy_ref),
                (grant,),
            ),
        )
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=service_scope,
        scope_ref=config.scope_ref,
        scope_version="load01-market-scope-v1",
        security_ref=_MARKET_SECURITY_REF,
        security_version="load01-market-curve-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="load01-market-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="load01-market-requirements-v1",
        curve_public_key=server_keys[0],
        curve_secret_key=server_keys[1],
        principals=principals,
        grants=grants,
    )
    provider = _CountedMarketProvider(market_keys)
    hub = GatewayMarketHub(
        provider,
        key_universe=all_keys,
        principal_allowed_keys=dict.fromkeys(principals.values(), all_keys),
        service_identity=service_identity,
        service_scope=service_scope,
        max_replay_events=_MARKET_REPLAY_EVENTS,
        max_replay_bytes=_MARKET_REPLAY_BYTES,
    )
    provider.hub = hub

    def resource_provider(component, _instance_id, _options, _scope):
        if component != "gateway":
            raise AssertionError("LOAD01 market host selected an unexpected component")
        return ComponentResources.owned({"hub": hub}, ready=lambda: hub.ready, close=hub.close)

    host = ServiceHost(config, authority, registry_path, resource_provider)
    try:
        host.start()
        control.send(("ready", "market", os.getpid(), host.endpoint, host.market_endpoint))
        next_publish = None
        interval_ns = 1_000_000_000 // _MARKET_HZ
        topic_index = 0
        last_report = time.monotonic()
        while True:
            now = time.monotonic()
            if (
                load_start_event.is_set()
                and time.monotonic() >= load_start_value.value
                and not stop_event.is_set()
            ):
                if next_publish is None:
                    next_publish = time.perf_counter_ns()
                now_ns = time.perf_counter_ns()
                if now_ns >= next_publish:
                    key = market_keys[topic_index]
                    provider.publish_one(key, now_ns)
                    topic_index = (topic_index + 1) % len(market_keys)
                    next_publish = max(next_publish + interval_ns, now_ns + interval_ns // 4)

            host.serve_once(timeout_ms=0)
            if control.poll(0):
                command = control.recv()
                if command == ("status",):
                    control.send(("status", _market_status(provider, hub)))
                elif command == ("close",):
                    stop_event.set()
                    host.close()
                    control.send(
                        (
                            "closed",
                            os.getpid(),
                            host.state,
                            host.summary["cleanup_failures"],
                            _market_status(provider, hub),
                        )
                    )
                    return
                else:
                    raise AssertionError("unknown LOAD01 market control")

            if now - last_report >= 30:
                control.send(("progress", "market", _market_status(provider, hub)))
                last_report = now
            if next_publish is None or stop_event.is_set():
                time.sleep(0.001)
            else:
                delay = (next_publish - time.perf_counter_ns()) / 1_000_000_000
                time.sleep(max(0.0002, min(0.001, delay)))
    except BaseException as exc:
        if host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", "market", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _market_status(provider: _CountedMarketProvider, hub: GatewayMarketHub) -> dict[str, Any]:
    return {
        "provider_starts": dict(provider.starts),
        "provider_stops": dict(provider.stops),
        "active_provider_keys": tuple(sorted(provider.generations)),
        "source_sequences": dict(provider.source_sequence),
        "source_payload_bytes": provider.source_payload_bytes,
        "first_publish_ns": provider.first_publish_ns,
        "last_publish_ns": provider.last_publish_ns,
        "hub_readiness": dict(hub.readiness),
    }


def _monitor_process(
    control,
    durable_config_raw,
    telemetry_config_raw,
    durable_registry_path,
    telemetry_registry_path,
    outbox_path,
    durable_server_keys,
    telemetry_server_keys,
    owner_audiences,
    strategy_audience,
):
    _check_fixed_source_manifest(os.environ["BT_API_LOAD01_SOURCE_MANIFEST"])
    durable_config = parse_service_config(durable_config_raw)
    telemetry_config = parse_service_config(telemetry_config_raw)
    durable_identity = WireIdentity(WireRole.SERVICE, _DURABLE_PRINCIPAL, _DURABLE_ID)
    telemetry_identity = WireIdentity(WireRole.SERVICE, _TELEMETRY_PRINCIPAL, _TELEMETRY_ID)
    domain_scope = WireScope(_DOMAIN)

    durable_principals = {}
    durable_grants = {}
    durable_targets = {}
    for owner_record in owner_audiences:
        public_key = owner_record["public_key"]
        principal = owner_record["principal_ref"]
        actor_ref = owner_record["actor_ref"]
        durable_principals[public_key] = principal
        service_identity = WireIdentity(WireRole.SERVICE, principal, actor_ref)
        grants_for_principal = []
        for scope_record in owner_record["scopes"]:
            wire_scope = scope_record["wire_scope"]
            scope_ref = scope_record["scope_ref"]
            grants_for_principal.append(
                ResourceGrant(
                    _DURABLE_INSTANCE,
                    "service.telemetry_ingress",
                    scope_ref,
                    wire_scope,
                    _INGRESS_OPS,
                )
            )
            durable_targets[wire_scope] = MonitorIngressTarget(
                scope_record["outbox_scope"], _EVENT_TYPES
            )
        durable_grants[principal] = (PrincipalGrant(service_identity, tuple(grants_for_principal)),)

    telemetry_principals = {}
    telemetry_grants = {}
    telemetry_targets = set()
    for public_key, principal, strategy_ref, account_ref in strategy_audience:
        telemetry_principals[public_key] = principal
        scope = WireScope(_DOMAIN, account_ref=account_ref, strategy_ref=strategy_ref)
        telemetry_targets.add(scope)
        grant = ResourceGrant(
            _TELEMETRY_INSTANCE,
            "service.telemetry_ingress",
            _TELEMETRY_SCOPE_REF,
            scope,
            _TELEMETRY_OPS,
        )
        telemetry_grants[principal] = (
            PrincipalGrant(
                WireIdentity(WireRole.STRATEGY_CLIENT, principal, strategy_ref), (grant,)
            ),
        )

    durable_authority = TrustedServiceAuthority(
        identity=durable_identity,
        scope=domain_scope,
        scope_ref=durable_config.scope_ref,
        scope_version="load01-monitor-durable-scope-v1",
        security_ref=_DURABLE_SECURITY_REF,
        security_version="load01-monitor-durable-curve-v1",
        registration_policy_ref=durable_config.registration_policy_ref,
        registration_policy_version="load01-monitor-durable-policy-v1",
        service_requirements_ref=durable_config.service_requirements_ref,
        service_requirements_version="load01-monitor-durable-requirements-v1",
        curve_public_key=durable_server_keys[0],
        curve_secret_key=durable_server_keys[1],
        principals=durable_principals,
        grants=durable_grants,
    )
    telemetry_authority = TrustedServiceAuthority(
        identity=telemetry_identity,
        scope=domain_scope,
        scope_ref=telemetry_config.scope_ref,
        scope_version="load01-monitor-telemetry-scope-v1",
        security_ref=_TELEMETRY_SECURITY_REF,
        security_version="load01-monitor-telemetry-curve-v1",
        registration_policy_ref=telemetry_config.registration_policy_ref,
        registration_policy_version="load01-monitor-telemetry-policy-v1",
        service_requirements_ref=telemetry_config.service_requirements_ref,
        service_requirements_version="load01-monitor-telemetry-requirements-v1",
        curve_public_key=telemetry_server_keys[0],
        curve_secret_key=telemetry_server_keys[1],
        principals=telemetry_principals,
        grants=telemetry_grants,
    )

    outbox = DurableOutbox(outbox_path)
    telemetry = MonitorTelemetryService(
        frozenset(telemetry_targets),
        max_streams=256,
        max_bytes=16 * 1024 * 1024,
        heartbeat_timeout_seconds=90,
        retention_seconds=3600,
    )

    def durable_resources(component, _instance_id, _options, _scope):
        if component != "monitor":
            raise AssertionError("LOAD01 durable host selected an unexpected component")
        return ComponentResources.borrowed(
            {"outbox": outbox, "targets": durable_targets}, ready=lambda: True
        )

    def telemetry_resources(component, _instance_id, _options, _scope):
        if component != "monitor":
            raise AssertionError("LOAD01 telemetry host selected an unexpected component")
        return ComponentResources.borrowed(
            {"targets": frozenset(telemetry_targets)}, ready=lambda: True
        )

    durable_host = ServiceHost(
        durable_config, durable_authority, durable_registry_path, durable_resources
    )
    telemetry_host = ServiceHost(
        telemetry_config, telemetry_authority, telemetry_registry_path, telemetry_resources
    )
    try:
        durable_host.start()
        telemetry_host.start()
        control.send(
            (
                "ready",
                "monitor",
                os.getpid(),
                durable_host.endpoint,
                telemetry_host.endpoint,
                durable_host.state,
                telemetry_host.state,
            )
        )
        last_report = time.monotonic()
        while True:
            durable_host.serve_once(timeout_ms=0)
            telemetry_host.serve_once(timeout_ms=0)
            if control.poll(0):
                command = control.recv()
                if command == ("status",):
                    control.send(
                        (
                            "status",
                            _monitor_db_status(outbox_path, telemetry),
                            _monitor_telemetry_status(telemetry, strategy_audience),
                        )
                    )
                elif command == ("close",):
                    telemetry_status = _monitor_telemetry_status(telemetry, strategy_audience)
                    telemetry_host.close()
                    durable_host.close()
                    control.send(
                        (
                            "closed",
                            os.getpid(),
                            durable_host.state,
                            durable_host.summary["cleanup_failures"],
                            telemetry_host.state,
                            telemetry_host.summary["cleanup_failures"],
                            _monitor_db_status(outbox_path, telemetry),
                            telemetry_status,
                        )
                    )
                    return
                else:
                    raise AssertionError("unknown LOAD01 monitor control")
            now = time.monotonic()
            if now - last_report >= 30:
                control.send(("progress", "monitor", _monitor_db_status(outbox_path, telemetry)))
                last_report = now
            time.sleep(0.001)
    except BaseException as exc:
        for host in (telemetry_host, durable_host):
            if host.state not in {"closed", "failed"}:
                with suppress(BaseException):
                    host.close()
        with suppress(Exception):
            control.send(("error", "monitor", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _monitor_db_status(outbox_path: str, telemetry: MonitorTelemetryService) -> dict[str, Any]:
    path = Path(outbox_path).resolve()
    uri = path.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=1.0) as connection:
        connection.execute("PRAGMA query_only=ON")
        row_count = int(
            connection.execute("SELECT count(*) FROM monitor_outbox_events").fetchone()[0]
        )
        scope_count = int(
            connection.execute(
                "SELECT count(DISTINCT scope) FROM monitor_outbox_events"
            ).fetchone()[0]
        )
    return {
        "durable_event_rows": row_count,
        "durable_scope_count": scope_count,
        "durable_db_bytes": _sqlite_tree_bytes(path),
        "telemetry_service_ready": type(telemetry) is MonitorTelemetryService,
    }


def _monitor_telemetry_status(
    telemetry: MonitorTelemetryService,
    strategy_audience: tuple[tuple[bytes, str, str, str], ...],
) -> tuple[dict[str, Any], ...]:
    principal_ref = "load01-monitor-readonly-observer"
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, principal_ref, principal_ref)
    snapshots = []
    for _public_key, expected_principal, strategy_ref, account_ref in strategy_audience:
        result = telemetry.handle(
            "telemetry.snapshot",
            principal_ref=principal_ref,
            identity=identity,
            scope=WireScope(_DOMAIN, account_ref=account_ref, strategy_ref=strategy_ref),
            arguments={},
        )
        streams = result.get("streams")
        if not isinstance(streams, list) or len(streams) > 1:
            raise AssertionError("LOAD01 monitor telemetry snapshot was not strategy-isolated")
        for stream in streams:
            producer = stream.get("producer", {})
            scope = stream.get("scope", {})
            if (
                producer.get("principal_ref") != expected_principal
                or producer.get("actor_ref") != strategy_ref
                or scope.get("account_ref") != account_ref
                or scope.get("strategy_ref") != strategy_ref
            ):
                raise AssertionError("LOAD01 Monitor returned telemetry across strategy scopes")
        snapshots.append(
            {
                "strategy_ref": strategy_ref,
                "account_ref": account_ref,
                "state": result.get("state"),
                "stream_count": len(streams),
                "streams": streams,
            }
        )
    return tuple(snapshots)


def _sqlite_tree_bytes(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
        if candidate.exists()
    )


def _account_status(provider, worker, host) -> dict[str, Any]:
    snapshot = worker.latest_snapshot
    return {
        "service_state": host.state,
        "worker_state": worker.state,
        "worker_ready": worker.ready,
        "account_key": worker.account_key.to_dict(),
        "session_generation": worker.session_generation,
        "snapshot_id": None if snapshot is None else snapshot.snapshot_id,
        "provider_connect_calls": provider.connect_calls,
        "provider_read_calls": provider.read_calls,
        "provider_close_calls": provider.close_calls,
    }


def _reader_source_evidence(reader: ExecutionJournalReader) -> dict[str, Any]:
    digest = hashlib.sha256()
    rows_by_stream: dict[str, int] = {}
    for stream in sorted(("execution", "cancellation")):
        cursor = 0
        count = 0
        while True:
            page = reader.read_page(stream, after_sequence=cursor, limit=1000)
            if not page.events:
                break
            for event in page.events:
                digest.update(f"{stream}\0{event.sequence}\0{event.event_id}\n".encode())
                cursor = event.sequence
                count += 1
            if len(page.events) < 1000:
                break
        rows_by_stream[stream] = count
    return {
        "source_rows": sum(rows_by_stream.values()),
        "source_rows_by_stream": rows_by_stream,
        "source_event_digest": digest.hexdigest(),
    }


def _owner_process(
    control,
    account_index,
    account_config_raw,
    execution_config_raw,
    account_registry_path,
    execution_registry_path,
    gateway_journal_path,
    guard_directory,
    execution_store_path,
    checkpoint_directory,
    account_server_keys,
    execution_server_keys,
    strategy_audience,
    unused_relay_endpoint,
):
    _check_fixed_source_manifest(os.environ["BT_API_LOAD01_SOURCE_MANIFEST"])
    rpc_trace = _install_load01_rpc_trace("owner")
    account_config = parse_service_config(account_config_raw)
    execution_config = parse_service_config(execution_config_raw)
    account_service_id = account_config.service_id
    execution_service_id = execution_config.service_id
    account_ref = _ACCOUNT_REFS[account_index]
    account_provider = _CountedSyntheticAccountProvider(account_index)
    order_provider = _SyntheticOrderProvider()
    admission = _SyntheticAdmission()
    journal = None
    store = None
    account_host = None
    execution_host = None
    relay_runtime = None
    readers: dict[int, ExecutionJournalReader] = {}
    relays: dict[int, ServiceExecutionMonitorRelay] = {}
    relay_delivered_by_scope: Counter[str] = Counter()
    gateway_worker = None

    account_scope_ref = f"load01-gateway-journal-{account_index:02d}"
    state_scope_ref = f"load01-account-state-{account_index:02d}"
    account_scope = GatewayServiceAccountScope(
        account_ref=account_ref,
        execution_domain_ref=_DOMAIN,
        journal_account_scope=account_scope_ref,
        account_read_strategy_scope=state_scope_ref,
        shared_account_facts_scope=state_scope_ref,
        shared_fact_strategy_refs=frozenset(
            strategy["strategy_ref"] for strategy in strategy_audience
        ),
    )
    gateway_principals = {
        strategy["principal_ref"]: GatewayPrincipal(
            principal_id=strategy["principal_ref"],
            account_scopes=frozenset({account_scope_ref}),
            strategy_scopes=frozenset({state_scope_ref}),
            allowed_kinds=frozenset({GatewayCommandKind.READ}),
        )
        for strategy in strategy_audience
    }
    execution_targets = {}
    execution_grants = {}
    account_grants = {}
    account_principals = {}
    execution_principals = {}
    for strategy in strategy_audience:
        strategy_index = strategy["index"]
        principal = strategy["principal_ref"]
        strategy_ref = strategy["strategy_ref"]
        wire_scope = WireScope(_DOMAIN, account_ref=account_ref, strategy_ref=strategy_ref)
        execution_scope = ExecutionScope(
            "synthetic", "paper", account_ref, strategy_ref, _EXECUTION_DAY
        )
        execution_targets[wire_scope] = execution_scope
        account_principals[strategy["public_key"]] = principal
        execution_principals[strategy["public_key"]] = principal
        account_grants[principal] = (
            PrincipalGrant(
                WireIdentity(WireRole.STRATEGY_CLIENT, principal, strategy_ref),
                (
                    ResourceGrant(
                        f"{_ACCOUNT_INSTANCE_PREFIX}{account_index:02d}",
                        "service.account_read",
                        f"{_ACCOUNT_SCOPE_PREFIX}{account_index:02d}-v1",
                        wire_scope,
                        _ACCOUNT_OPS,
                    ),
                ),
            ),
        )
        execution_grants[principal] = (
            PrincipalGrant(
                WireIdentity(WireRole.STRATEGY_CLIENT, principal, strategy_ref),
                (
                    ResourceGrant(
                        f"{_EXEC_INSTANCE_PREFIX}{account_index:02d}",
                        "service.order_entry",
                        f"{_EXEC_ORDER_SCOPE_PREFIX}{account_index:02d}-v1",
                        wire_scope,
                        _ORDER_OPS,
                    ),
                    ResourceGrant(
                        f"{_EXEC_INSTANCE_PREFIX}{account_index:02d}",
                        "service.order_query",
                        f"{_EXEC_QUERY_SCOPE_PREFIX}{account_index:02d}-v1",
                        wire_scope,
                        _QUERY_OPS,
                    ),
                ),
            ),
        )

    account_authority = TrustedServiceAuthority(
        identity=WireIdentity(
            WireRole.SERVICE,
            _ACCOUNT_SERVICE_PRINCIPAL,
            account_service_id,
        ),
        scope=WireScope(_DOMAIN),
        scope_ref=account_config.scope_ref,
        scope_version=f"load01-account-scope-{account_index:02d}-v1",
        security_ref=f"{_ACCOUNT_SECURITY_PREFIX}{account_index:02d}v1",
        security_version=f"load01-account-curve-{account_index:02d}-v1",
        registration_policy_ref=account_config.registration_policy_ref,
        registration_policy_version=f"load01-account-policy-{account_index:02d}-v1",
        service_requirements_ref=account_config.service_requirements_ref,
        service_requirements_version=f"load01-account-requirements-{account_index:02d}-v1",
        curve_public_key=account_server_keys[0],
        curve_secret_key=account_server_keys[1],
        principals=account_principals,
        grants=account_grants,
    )
    execution_authority = TrustedServiceAuthority(
        identity=WireIdentity(
            WireRole.SERVICE,
            _EXEC_SERVICE_PRINCIPAL,
            execution_service_id,
        ),
        scope=WireScope(_DOMAIN),
        scope_ref=execution_config.scope_ref,
        scope_version=f"load01-execution-scope-{account_index:02d}-v1",
        security_ref=f"{_EXEC_SECURITY_PREFIX}{account_index:02d}v1",
        security_version=f"load01-execution-curve-{account_index:02d}-v1",
        registration_policy_ref=execution_config.registration_policy_ref,
        registration_policy_version=f"load01-execution-policy-{account_index:02d}-v1",
        service_requirements_ref=execution_config.service_requirements_ref,
        service_requirements_version=f"load01-execution-requirements-{account_index:02d}-v1",
        curve_public_key=execution_server_keys[0],
        curve_secret_key=execution_server_keys[1],
        principals=execution_principals,
        grants=execution_grants,
    )

    try:
        journal = GatewayEventJournal(gateway_journal_path)
        worker_holder: dict[str, GatewayAccountSessionWorker] = {}

        def account_resources(component, _instance_id, options, _scope):
            nonlocal gateway_worker
            if component != "gateway" or options.get("role") != "account_session_worker":
                raise AssertionError("LOAD01 account host selected an unexpected component")
            gateway_worker = GatewayAccountSessionWorker(
                account_provider,
                journal,
                account_key=_ACCOUNT_KEYS[account_index],
                journal_account_scope=account_scope_ref,
                state_scope=state_scope_ref,
                guard=LocalAccountSessionGuard(
                    Path(guard_directory),
                    _ACCOUNT_KEYS[account_index].account_key_id,
                    owner_ref=f"load01-owner-{account_index:02d}",
                ),
                required_coverage=(_RESOURCE,),
                refresh_interval_seconds=3600,
                max_staleness_seconds=7200,
                request_timeout_seconds=5,
            )
            worker_holder["worker"] = gateway_worker

            def close_gateway_resources() -> None:
                if gateway_worker is not None:
                    gateway_worker.close()
                if journal is not None:
                    journal.close()

            return ComponentResources.owned(
                {
                    "journal": journal,
                    "accounts": {_ACCOUNT_REFS[account_index]: account_scope},
                    "principals": gateway_principals,
                },
                ready=lambda: gateway_worker is not None and gateway_worker.ready,
                close=close_gateway_resources,
                internal_ports={"service.provider_session": gateway_worker},
            )

        def execution_resources(component, _instance_id, _options, _scope):
            if component != "execution":
                raise AssertionError("LOAD01 execution host selected an unexpected component")
            return ComponentResources.borrowed(
                {
                    "store": store,
                    "targets": execution_targets,
                    "dispatch": order_provider,
                    "admission": {"order_gate": admission, "cancel_gate": admission},
                },
                ready=lambda: store is not None,
            )

        store = SqliteExecutionStore(execution_store_path)
        account_host = ServiceHost(
            account_config, account_authority, account_registry_path, account_resources
        )
        execution_host = ServiceHost(
            execution_config, execution_authority, execution_registry_path, execution_resources
        )
        account_host.start()
        execution_host.start()

        scope_records = []
        for local_index in range(_PER_ACCOUNT):
            strategy_index = account_index * _PER_ACCOUNT + local_index
            execution_scope = ExecutionScope(
                "synthetic",
                "paper",
                account_ref,
                _strategy_ref(strategy_index),
                _EXECUTION_DAY,
            )
            reader = ExecutionJournalReader(execution_store_path, scope=execution_scope)
            readers[local_index] = reader
            scope_records.append(
                {
                    "local_index": local_index,
                    "strategy_index": strategy_index,
                    "strategy_ref": _strategy_ref(strategy_index),
                    "scope_ref": _durable_scope_ref(account_index, local_index),
                    "wire_scope": WireScope(
                        _DOMAIN,
                        account_ref=account_ref,
                        strategy_ref=_strategy_ref(strategy_index),
                    ),
                    "source_generation": reader.generation,
                    "source_scope_key": execution_scope.key,
                    "outbox_scope": _relay_outbox_scope(reader.generation, execution_scope.key),
                }
            )
        control.send(
            (
                "ready",
                "owner",
                account_index,
                os.getpid(),
                account_host.endpoint,
                execution_host.endpoint,
                _account_status(account_provider, gateway_worker, account_host),
                tuple(scope_records),
            )
        )

        command = control.recv()
        if not isinstance(command, tuple) or not command or command[0] != "connect-monitor":
            raise AssertionError("LOAD01 owner did not receive Monitor relay activation")
        _, monitor_endpoint, monitor_server_public, relay_identity = command
        relay_principal, relay_actor_ref, relay_public, relay_secret = relay_identity
        relay_config_raw = _owner_relay_config(
            account_index, monitor_endpoint, unused_relay_endpoint
        )
        relay_config = parse_service_config(relay_config_raw)
        relay_identity_obj = WireIdentity(WireRole.SERVICE, relay_principal, relay_actor_ref)
        monitor_peer = WireIdentity(WireRole.SERVICE, _DURABLE_PRINCIPAL, _DURABLE_ID)
        resolved_scopes = {record["scope_ref"]: record["wire_scope"] for record in scope_records}
        relay_runtime = ServiceClientRuntime(
            relay_config,
            identity=relay_identity_obj,
            scope_resolver=lambda ref: resolved_scopes[ref],
            peer_resolver=lambda service_id: (
                monitor_peer
                if service_id == _DURABLE_ID
                else (_ for _ in ()).throw(KeyError(service_id))
            ),
            transport_factory=ZmqTransportFactory(
                {
                    _DURABLE_SECURITY_REF: ClientCurveKeys(
                        monitor_server_public, relay_public, relay_secret
                    )
                }
            ),
            requested_operations={"upstream.telemetry": _INGRESS_OPS},
        )
        relay_runtime.start()
        for local_index, record in enumerate(scope_records):
            relays[local_index] = ServiceExecutionMonitorRelay(
                relay_runtime,
                readers[local_index],
                scope_ref=record["scope_ref"],
                checkpoint_path=Path(checkpoint_directory)
                / f"relay-{account_index:02d}-{local_index:02d}-checkpoint.sqlite3",
            )
        if len(relay_runtime.bindings) != _PER_ACCOUNT or not all(
            binding.active for binding in relay_runtime.bindings
        ):
            raise AssertionError("LOAD01 owner failed to register its ten Monitor relay scopes")
        control.send(("relay-ready", account_index, os.getpid(), len(relay_runtime.bindings)))

        total_relayed = 0
        last_report = time.monotonic()
        next_relay_poll = time.monotonic()
        next_relay_maintenance = time.monotonic()
        draining = False
        drain_deadline = 0.0
        while True:
            account_host.serve_once(timeout_ms=0)
            execution_host.serve_once(timeout_ms=0)
            now = time.monotonic()
            if now >= next_relay_maintenance:
                relay_runtime.maintain()
                next_relay_maintenance = time.monotonic() + 0.5
            if now >= next_relay_poll or draining:
                round_delivered = 0
                for local_index, relay in relays.items():
                    result = relay.poll(limit=64)
                    round_delivered += result.remote_delivered
                    relay_delivered_by_scope[scope_records[local_index]["scope_ref"]] += (
                        result.remote_delivered
                    )
                total_relayed += round_delivered
                next_relay_poll = now + 0.5
                if draining and round_delivered == 0:
                    break
                if draining and now > drain_deadline:
                    raise TimeoutError("LOAD01 durable Monitor relay did not drain")

            if control.poll(0):
                next_command = control.recv()
                if next_command == ("status",):
                    control.send(
                        (
                            "status",
                            account_index,
                            _account_status(account_provider, gateway_worker, account_host),
                            order_provider.submitted,
                            order_provider.cancelled,
                            total_relayed,
                            relay_runtime.state,
                            relay_runtime.cleanup_failures,
                            relay_runtime.unregister_uncertainties,
                        )
                    )
                elif next_command in {("close",), ("close", "diagnostic")}:
                    if next_command == ("close", "diagnostic"):
                        _send_load01_rpc_diagnostic(
                            control,
                            f"owner-{account_index:02d}",
                            os.getpid(),
                            _load01_trace_snapshot_or_none(rpc_trace, max_events=32),
                            "emergency",
                        )
                    draining = True
                    drain_deadline = time.monotonic() + 120
                else:
                    raise AssertionError("unknown LOAD01 owner control")

            if now - last_report >= 30:
                control.send(
                    (
                        "progress",
                        "owner",
                        account_index,
                        {
                            "submit_calls": order_provider.submitted,
                            "cancel_calls": order_provider.cancelled,
                            "relayed_events": total_relayed,
                            "account_provider": _account_status(
                                account_provider, gateway_worker, account_host
                            ),
                        },
                    )
                )
                last_report = now
            time.sleep(0.001)

        source_evidence = []
        for local_index, reader in readers.items():
            source_evidence.append(
                {
                    **scope_records[local_index],
                    **_reader_source_evidence(reader),
                    "relay_delivered": relay_delivered_by_scope[
                        scope_records[local_index]["scope_ref"]
                    ],
                }
            )
        for relay in relays.values():
            relay.close()
        relays.clear()
        for reader in readers.values():
            reader.close()
        readers.clear()
        relay_runtime.close()
        relay_runtime_state = relay_runtime.state
        relay_cleanup_failures = relay_runtime.cleanup_failures
        relay_unregister_uncertainties = relay_runtime.unregister_uncertainties
        execution_host.close()
        execution_state = execution_host.state
        execution_cleanup_failures = execution_host.summary["cleanup_failures"]
        account_host.close()
        account_state = account_host.state
        account_cleanup_failures = account_host.summary["cleanup_failures"]
        store.close()
        store = None
        control.send(
            (
                "closed",
                account_index,
                os.getpid(),
                account_state,
                account_cleanup_failures,
                execution_state,
                execution_cleanup_failures,
                relay_runtime_state,
                relay_cleanup_failures,
                relay_unregister_uncertainties,
                order_provider.submitted,
                order_provider.cancelled,
                total_relayed,
                _account_status(account_provider, gateway_worker, account_host),
                tuple(scope_records),
                tuple(source_evidence),
                _load01_trace_snapshot_or_none(rpc_trace),
            )
        )
    except BaseException as exc:
        rpc_timing_at_failure = _load01_trace_snapshot_or_none(rpc_trace, max_events=32)
        _send_load01_rpc_diagnostic(
            control,
            f"owner-{account_index:02d}",
            os.getpid(),
            rpc_timing_at_failure,
            "failure",
        )
        cleanup_started_ns = rpc_trace.now()
        for relay in relays.values():
            with suppress(BaseException):
                relay.close()
        for reader in readers.values():
            with suppress(BaseException):
                reader.close()
        if relay_runtime is not None and relay_runtime.state != "closed":
            with suppress(BaseException):
                relay_runtime.close()
        for host in (execution_host, account_host):
            if host is not None and host.state not in {"closed", "failed"}:
                with suppress(BaseException):
                    host.close()
        if store is not None:
            with suppress(BaseException):
                store.close()
        with suppress(BaseException):
            if journal is not None:
                journal.close()
        cleanup_ended_ns = rpc_trace.now()
        owner_diagnostic = {
            "rpc_timing_at_failure": rpc_timing_at_failure,
            "cleanup_duration_ns": (
                max(0, cleanup_ended_ns - cleanup_started_ns)
                if type(cleanup_started_ns) is int and type(cleanup_ended_ns) is int
                else None
            ),
        }
        with suppress(Exception):
            control.send(
                (
                    "error",
                    "owner",
                    account_index,
                    getattr(exc, "code", type(exc).__name__),
                    owner_diagnostic,
                )
            )
        raise
    finally:
        control.close()


def _strategy_process(
    control,
    index,
    raw_config,
    account_service,
    execution_service,
    service_records,
    security_material,
    market_keys,
    registration_event,
    load_event,
    load_start_value,
    stop_event,
    abort_event,
    mode,
):
    _check_fixed_source_manifest(os.environ["BT_API_LOAD01_SOURCE_MANIFEST"])
    rpc_trace = _install_load01_rpc_trace("strategy")
    account_index = index // _PER_ACCOUNT
    local_index = index % _PER_ACCOUNT
    account_ref = _ACCOUNT_REFS[account_index]
    strategy_ref = _strategy_ref(index)
    principal_ref = _principal_ref(index)
    runtime = None
    account_client = None
    runtime_probe = None
    telemetry = None
    markets: dict[str, ServiceMarketClient] = {}
    pending_markets: dict[str, ServiceMarketClient] = {}
    market_poll_scheduler = _RoundRobinMarketPollScheduler(
        tuple(key.stream_id for key in market_keys)
    )
    market_poll_failure: dict[str, Any] | None = None
    market_client_start_samples: list[dict[str, Any]] = []
    runtime_start_duration_ms: float | None = None
    account_refresh_duration_ms: float | None = None
    registration_lease_snapshot: dict[str, Any] | None = None
    market_receives: Counter[str] = Counter()
    last_sequences: dict[str, int] = {}
    last_epochs: dict[str, str] = {}
    e2e_samples: list[float] = []
    order_samples: list[float] = []
    account_samples: list[float] = []
    randomizer = random.Random(index + 10_000)
    market_sample_cap = 2048
    market_seen = 0
    duplicate_events = 0
    unrecovered_gap_count = 0
    unrecovered_gap_events = 0
    reconnect_gap_events = 0
    reconnect_gaps_by_stream: Counter[str] = Counter()
    market_epoch_changes = 0
    reconnect_records: list[dict[str, Any]] = []
    slow_pause_observed: dict[str, float] | None = None
    account_reads = 0
    order_cycles = 0
    order_failures = 0
    telemetry_queue_drops = 0
    telemetry_delivery_drops = 0
    telemetry_delivered = 0
    max_queue_count = 0
    max_queue_bytes = 0
    max_market_poll_ms = 0.0
    initial_bindings = 0
    initial_degraded = -1
    account_identity: dict[str, Any] | None = None
    load_start = 0.0
    next_account_read = 0.0
    next_order = 0.0
    next_telemetry = 0.0
    next_metrics = 0.0
    next_report = 0.0
    reconnect_windows_seen: set[int] = set()
    finished_normally = False

    def market_clients_start() -> dict[str, int]:
        nonlocal markets, pending_markets
        snapshots: dict[str, int] = {}
        market_security = ClientCurveKeys(*security_material[_MARKET_SECURITY_REF])
        factory = ZmqMarketTransportFactory({_MARKET_SECURITY_REF: market_security})
        for key in market_keys:
            client = ServiceMarketClient(
                runtime,
                "store.market",
                key,
                subscriber_factory=factory,
                stream_scope=WireScope(_DOMAIN),
                queue_capacity=256,
                queue_max_bytes=512 * 1024,
                head_check_interval_seconds=1.0,
            )
            pending_markets[key.stream_id] = client
            started_ns = time.perf_counter_ns()
            try:
                bootstrap = client.start()
            finally:
                market_client_start_samples.append(
                    {
                        "stream_id": key.stream_id,
                        "duration_ms": (time.perf_counter_ns() - started_ns) / 1_000_000,
                    }
                )
            runtime_probe._maintain_if_due("market-client-start-boundary")
            if not runtime.healthy:
                raise _Load01StartupLeaseError()
            markets[key.stream_id] = client
            pending_markets.pop(key.stream_id, None)
            snapshots[key.stream_id] = bootstrap.snapshot_sequence
            if key.stream_id not in last_sequences:
                last_sequences[key.stream_id] = bootstrap.snapshot_sequence
                last_epochs[key.stream_id] = bootstrap.snapshot_epoch
        return snapshots

    def reconnect_markets(elapsed: float, window: int) -> None:
        nonlocal markets, reconnect_gap_events, reconnect_gaps_by_stream
        began = time.perf_counter_ns()
        previous = dict(last_sequences)
        for market in markets.values():
            market.close()
        markets = {}
        snapshots = market_clients_start()
        lost = 0
        for stream_id, snapshot_sequence in snapshots.items():
            prior = previous.get(stream_id, 0)
            if snapshot_sequence > prior:
                stream_lost = snapshot_sequence - prior
                lost += stream_lost
                reconnect_gaps_by_stream[stream_id] += stream_lost
            last_sequences[stream_id] = snapshot_sequence
            for key in market_keys:
                if key.stream_id == stream_id:
                    market = markets[stream_id]
                    bootstrap = market.last_bootstrap
                    if bootstrap is not None:
                        last_epochs[stream_id] = bootstrap.snapshot_epoch
                    break
        reconnect_gap_events += lost
        reconnect_records.append(
            {
                "window": window,
                "elapsed_seconds": elapsed,
                "recovery_ms": (time.perf_counter_ns() - began) / 1_000_000,
                "intentional_disconnect_gap_events": lost,
                "subscriptions_restored": len(markets),
            }
        )

    def read_market_events(*, pause: bool) -> None:
        nonlocal market_seen, duplicate_events, unrecovered_gap_count
        nonlocal unrecovered_gap_events, market_epoch_changes, max_queue_count
        nonlocal max_queue_bytes, max_market_poll_ms, market_poll_failure
        stream_id = market_poll_scheduler.next_stream_id(enabled=not pause)
        if stream_id is None:
            return
        key = next(key for key in market_keys if key.stream_id == stream_id)
        client = markets[stream_id]
        poll_started_monotonic = time.monotonic()
        poll_started = time.perf_counter_ns()
        try:
            event = client.poll(timeout_ms=0)
        except BaseException as poll_error:
            poll_ms = (time.perf_counter_ns() - poll_started) / 1_000_000
            max_market_poll_ms = max(max_market_poll_ms, poll_ms)
            market_poll_failure = _market_poll_failure_diagnostic(
                strategy_index=index,
                key=key,
                client=client,
                error=poll_error,
                started_monotonic=poll_started_monotonic,
                poll_duration_ms=poll_ms,
                load_start=load_start,
                market_seen=market_seen,
                last_epoch=last_epochs.get(stream_id),
                last_sequence=last_sequences.get(stream_id),
                runtime=runtime,
            )
            raise
        poll_ms = (time.perf_counter_ns() - poll_started) / 1_000_000
        max_market_poll_ms = max(max_market_poll_ms, poll_ms)
        max_queue_count = max(max_queue_count, client.queue_count)
        max_queue_bytes = max(max_queue_bytes, client.queue_bytes)
        if event is None:
            return
        epoch = event.header.stream_epoch
        sequence = event.header.sequence
        last_sequence = last_sequences[stream_id]
        last_epoch = last_epochs[stream_id]
        if epoch != last_epoch:
            market_epoch_changes += 1
            last_epochs[stream_id] = epoch
            last_sequences[stream_id] = sequence - 1
            last_sequence = sequence - 1
        if sequence <= last_sequence:
            duplicate_events += 1
        elif sequence > last_sequence + 1:
            unrecovered_gap_count += 1
            unrecovered_gap_events += sequence - last_sequence - 1
        last_sequences[stream_id] = sequence
        market_receives[stream_id] += 1
        market_seen += 1
        if market_seen % 128 == 0:
            data = event.payload["data"]
            published_ns = data.get("publish_ns")
            if type(published_ns) is int and published_ns > 0:
                latency = max(0, time.perf_counter_ns() - published_ns) / 1_000_000
                if len(e2e_samples) < market_sample_cap:
                    e2e_samples.append(latency)
                else:
                    slot = randomizer.randrange(market_seen)
                    if slot < market_sample_cap:
                        e2e_samples[slot] = latency

    def run_order_cycle(cycle: int) -> None:
        nonlocal order_cycles, order_failures
        intent_id = f"load01-{account_index:02d}-{local_index:02d}-{cycle:07d}"
        order_scope_ref = execution_service["order_scope_ref"]
        order_started = time.perf_counter_ns()
        submitted = runtime.call(
            "broker.orders",
            "orders.submit",
            _sample_intent(intent_id),
            scope_ref=order_scope_ref,
        )
        if submitted.get("code") != "OK":
            order_failures += 1
            raise AssertionError("synthetic execution submit was not acknowledged")
        record = submitted["result"]["record"]
        provider_order_id = record.get("provider_order_id")
        if type(provider_order_id) is not str or not provider_order_id:
            order_failures += 1
            raise AssertionError("synthetic execution submit omitted its provider identity")
        cancel_id = f"cancel-{intent_id}"
        cancelled = runtime.call(
            "broker.orders",
            "orders.cancel",
            {
                "cancel_id": cancel_id,
                "target_intent_id": intent_id,
                "provider_order_id": provider_order_id,
                "metadata_version": "load01-synthetic-v1",
                "tags": {"scenario": "LOAD01", "synthetic": "true"},
            },
            scope_ref=order_scope_ref,
        )
        if cancelled.get("code") != "OK":
            order_failures += 1
            raise AssertionError("synthetic execution cancel was not acknowledged")
        order_cycles += 1
        elapsed_ms = (time.perf_counter_ns() - order_started) / 1_000_000
        if len(order_samples) < 512:
            order_samples.append(elapsed_ms)
        elif order_cycles % 8 == 0:
            order_samples[order_cycles % 512] = elapsed_ms

    try:
        config = parse_client_config(raw_config)
        wire_scope = WireScope(_DOMAIN, account_ref=account_ref, strategy_ref=strategy_ref)
        scope_map = {
            _MARKET_SCOPE_REF: wire_scope,
            account_service["scope_ref"]: wire_scope,
            execution_service["order_scope_ref"]: wire_scope,
            execution_service["query_scope_ref"]: wire_scope,
            _TELEMETRY_SCOPE_REF: wire_scope,
        }
        peer_map = {record["service_id"]: record["peer"] for record in service_records}
        keys = {
            security_ref: ClientCurveKeys(*material)
            for security_ref, material in security_material.items()
        }
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref)
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda scope_ref: scope_map[scope_ref],
            peer_resolver=lambda service_id: peer_map[service_id],
            transport_factory=ZmqTransportFactory(keys),
            requested_operations={
                "store.market": _MARKET_OPS,
                "store.account": _ACCOUNT_OPS,
                "broker.orders": _ORDER_OPS,
                "broker.order_query": _QUERY_OPS,
                "runtime.telemetry": _TELEMETRY_OPS,
            },
        )
        runtime_probe = _StartupRuntimeMaintenanceProbe(runtime)
        account_client = ServiceAccountClient(
            runtime,
            "store.account",
            account_ref=account_ref,
            expected_account=_ACCOUNT_KEYS[account_index],
            required_coverage=(_RESOURCE,),
            expected_scope_ref=account_service["scope_ref"],
        )
        control.send(("loaded", index, os.getpid()))
        deadline = time.monotonic() + 300
        while not registration_event.wait(0.05):
            if abort_event.is_set():
                control.send(("aborted", index, os.getpid()))
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("LOAD01 common registration barrier expired")
        if abort_event.is_set():
            control.send(("aborted", index, os.getpid()))
            return

        runtime_start_ns = time.perf_counter_ns()
        runtime_probe.set_phase("initial-registration")
        registration_started_ns = time.perf_counter_ns()
        try:
            startup = runtime.start()
        finally:
            runtime_start_duration_ms = (
                time.perf_counter_ns() - registration_started_ns
            ) / 1_000_000
        registration_lease_snapshot = _runtime_lease_snapshot(runtime)
        if runtime_start_duration_ms >= _LEASE_SECONDS * 1000:
            raise _Load01StartupLeaseError()
        if (
            not registration_lease_snapshot["healthy"]
            or registration_lease_snapshot["required_min_lease_remaining_ms"] is None
        ):
            raise _Load01StartupLeaseError()
        runtime_probe._maintain_if_due("post-registration-boundary")
        post_registration_lease_snapshot = _runtime_lease_snapshot(runtime)
        if (
            not post_registration_lease_snapshot["healthy"]
            or post_registration_lease_snapshot["required_min_lease_remaining_ms"]
            <= _RPC_LEASE_HEADROOM_SECONDS * 1000
        ):
            raise _Load01StartupLeaseError()
        runtime_probe.set_phase("startup-market-subscription")
        market_clients_start()
        runtime_probe._maintain_if_due("market-loop-complete")
        market_lease_snapshot = _runtime_lease_snapshot(runtime)
        if (
            not market_lease_snapshot["healthy"]
            or market_lease_snapshot["required_min_lease_remaining_ms"]
            <= _RPC_LEASE_HEADROOM_SECONDS * 1000
        ):
            raise _Load01StartupLeaseError()
        runtime_probe.set_phase("startup-account-read")
        account_refresh_started_ns = time.perf_counter_ns()
        account_view = account_client.refresh()
        account_refresh_duration_ms = (
            time.perf_counter_ns() - account_refresh_started_ns
        ) / 1_000_000
        account_read_lease_snapshot = _runtime_lease_snapshot(runtime)
        if (
            not account_read_lease_snapshot["healthy"]
            or account_read_lease_snapshot["required_min_lease_remaining_ms"] <= 0
        ):
            raise _Load01StartupLeaseError()
        account_identity = account_view.observation.account_key.to_dict()
        if account_view.observation.account_key != _ACCOUNT_KEYS[account_index]:
            raise AssertionError("LOAD01 strategy read another account session")
        telemetry = ServiceTelemetryEmitter.from_config(
            runtime, config, scope_ref=_TELEMETRY_SCOPE_REF
        )
        initial_bindings = len(runtime.bindings)
        initial_degraded = startup.degraded_count
        if initial_degraded or initial_bindings != 5 or len(markets) != _TOPICS:
            raise AssertionError("LOAD01 strategy did not activate all five routes and 20 topics")
        startup_maintenance_snapshot = runtime_probe.snapshot()
        runtime_probe.set_phase("steady-load")
        control.send(
            (
                "ready",
                index,
                os.getpid(),
                strategy_ref,
                account_ref,
                runtime_start_ns,
                (time.perf_counter_ns() - runtime_start_ns) / 1_000_000,
                initial_bindings,
                initial_degraded,
                account_identity,
                tuple(last_sequences.items()),
                {
                    "runtime_start_duration_ms": runtime_start_duration_ms,
                    "registration_lease": registration_lease_snapshot,
                    "post_registration_lease": post_registration_lease_snapshot,
                    "market_loop_lease": market_lease_snapshot,
                    "account_read_lease": account_read_lease_snapshot,
                    "market_client_start_durations": market_client_start_samples,
                    "account_refresh_duration_ms": account_refresh_duration_ms,
                    "maintenance": startup_maintenance_snapshot,
                },
            )
        )

        while not load_event.is_set():
            runtime_probe._maintain_if_due("common-load-barrier")
            load_event.wait(0.05)
            if abort_event.is_set():
                control.send(("aborted", index, os.getpid()))
                return
        load_start = load_start_value.value
        if not _wait_until_load01_start(
            load_start,
            stop_event=stop_event,
            abort_event=abort_event,
            maintain_if_due=runtime_probe._maintain_if_due,
        ):
            control.send(("aborted", index, os.getpid()))
            return
        next_account_read = load_start + 60
        phase_offset = local_index * 0.5
        next_order = load_start + phase_offset
        next_telemetry = load_start + 30
        next_metrics = load_start + 60
        next_report = load_start + 30
        while True:
            now = time.monotonic()
            if not _load01_has_started(now, load_start):
                runtime_probe._maintain_if_due("scheduled-load-start-guard")
                stop_event.wait(min(0.05, max(0.0, load_start - time.monotonic())))
                if stop_event.is_set() or abort_event.is_set():
                    break
                continue
            elapsed = max(0.0, now - load_start)
            if (
                runtime.next_maintenance_delay() is not None
                and runtime.next_maintenance_delay() <= 0
            ):
                runtime.maintain()

            if not stop_event.is_set():
                if index == 0 and 30 <= elapsed < 45 and slow_pause_observed is None:
                    slow_pause_observed = {"pause_started_seconds": elapsed}
                if index == 0 and elapsed >= 45 and slow_pause_observed is not None:
                    slow_pause_observed.setdefault("pause_ended_seconds", elapsed)

                reconnect_window = 0
                reconnect_due = False
                if mode == "pilot":
                    reconnect_due = (
                        local_index == 0
                        and elapsed >= 60 + account_index * 0.2
                        and 1 not in reconnect_windows_seen
                    )
                    reconnect_window = 1
                else:
                    window = int(elapsed // 300)
                    # Window 6 would begin exactly at the t=1800 s end boundary:
                    # the controller can publish stop_event a few milliseconds
                    # after this process already sees elapsed >= 1800, which
                    # would schedule reconnects past the 5-window budget and
                    # make len(reconnect_records) exceed 5 * _ACCOUNTS. Only
                    # windows whose nominal start is strictly inside the load
                    # timeline may schedule a reconnect, so window 5 is the
                    # terminal window of the full-mode timeline.
                    if window >= 1 and window < _FULL_LOAD_SECONDS // 300:
                        reconnect_window = window
                        target_local = (window - 1) % _PER_ACCOUNT
                        target_at = window * 300 + account_index * 1.0
                        reconnect_due = (
                            local_index == target_local
                            and elapsed >= target_at
                            and window not in reconnect_windows_seen
                        )
                if reconnect_due:
                    reconnect_markets(elapsed, reconnect_window)
                    reconnect_windows_seen.add(reconnect_window)

                now = time.monotonic()
                elapsed = max(0.0, now - load_start)
                now = time.monotonic()
                if now >= next_order:
                    cycle = order_cycles + 1
                    run_order_cycle(cycle)
                    next_order += 5.0
                    after_order = time.monotonic()
                    if next_order < after_order - 2.5:
                        skipped = int((after_order - next_order) // 5.0) + 1
                        next_order += skipped * 5.0

                now = time.monotonic()
                if now >= next_account_read:
                    read_started = time.perf_counter_ns()
                    refreshed = account_client.refresh()
                    if refreshed.observation.account_key != _ACCOUNT_KEYS[account_index]:
                        raise AssertionError("LOAD01 periodic account read crossed sessions")
                    account_reads += 1
                    account_samples.append((time.perf_counter_ns() - read_started) / 1_000_000)
                    next_account_read = time.monotonic() + 60.0

                now = time.monotonic()
                if now >= next_telemetry:
                    if not telemetry.enqueue_heartbeat():
                        telemetry_queue_drops += 1
                    next_telemetry = time.monotonic() + 30.0
                now = time.monotonic()
                if now >= next_metrics:
                    if not telemetry.enqueue_metrics(
                        {
                            "market_events": market_seen,
                            "order_cycles": order_cycles,
                            "account_reads": account_reads,
                        }
                    ):
                        telemetry_queue_drops += 1
                    next_metrics = time.monotonic() + 60.0
            if telemetry.stats.queued_events:
                flush = telemetry.flush(max_events=4)
                if not flush.ready or flush.dropped:
                    telemetry_delivery_drops += flush.dropped or 1
                telemetry_delivered += flush.delivered

            now = time.monotonic()
            elapsed = max(0.0, now - load_start)
            pause_market = index == 0 and 30 <= elapsed < 45 and not stop_event.is_set()
            if now >= next_report and not stop_event.is_set():
                stats = _strategy_stats(
                    index=index,
                    market_receives=market_receives,
                    last_sequences=last_sequences,
                    order_cycles=order_cycles,
                    account_reads=account_reads,
                    e2e_samples=e2e_samples,
                    order_samples=order_samples,
                    account_samples=account_samples,
                    duplicate_events=duplicate_events,
                    unrecovered_gap_count=unrecovered_gap_count,
                    unrecovered_gap_events=unrecovered_gap_events,
                    reconnect_gap_events=reconnect_gap_events,
                    reconnect_records=reconnect_records,
                    reconnect_gaps_by_stream=reconnect_gaps_by_stream,
                    slow_pause_observed=slow_pause_observed,
                    telemetry=telemetry,
                    telemetry_queue_drops=telemetry_queue_drops,
                    telemetry_delivery_drops=telemetry_delivery_drops,
                    telemetry_delivered=telemetry_delivered,
                    max_queue_count=max_queue_count,
                    max_queue_bytes=max_queue_bytes,
                    max_market_poll_ms=max_market_poll_ms,
                    account_identity=account_identity,
                    runtime=runtime,
                )
                control.send(("progress", index, stats))
                next_report = time.monotonic() + 30.0

            if control.poll(0):
                command = control.recv()
                if command in {("close",), ("close", "diagnostic")}:
                    if command == ("close", "diagnostic"):
                        _send_load01_rpc_diagnostic(
                            control,
                            f"strategy-{index:03d}",
                            os.getpid(),
                            _load01_trace_snapshot_or_none(rpc_trace, max_events=32),
                            "emergency",
                        )
                    break
                raise AssertionError("unknown LOAD01 strategy control")
            # Business work, control handling and progress publication happen
            # before one round-robin market poll. This prevents a serial sweep
            # of all twenty clients from running ahead of due work; one slow
            # synchronous poll can still pause this strategy loop, and its
            # duration is measured and preserved in diagnostics below.
            read_market_events(pause=pause_market)
            if slow_pause_observed is not None and elapsed >= 45:
                slow_pause_observed.setdefault("pause_ended_seconds", elapsed)
            time.sleep(0.0005)

        for market in markets.values():
            market.close()
        markets.clear()
        account_client.close()
        telemetry.close()
        runtime.close()
        finished_normally = runtime.state == "closed"
        final_stats = _strategy_stats(
            index=index,
            market_receives=market_receives,
            last_sequences=last_sequences,
            order_cycles=order_cycles,
            account_reads=account_reads,
            e2e_samples=e2e_samples,
            order_samples=order_samples,
            account_samples=account_samples,
            duplicate_events=duplicate_events,
            unrecovered_gap_count=unrecovered_gap_count,
            unrecovered_gap_events=unrecovered_gap_events,
            reconnect_gap_events=reconnect_gap_events,
            reconnect_records=reconnect_records,
            reconnect_gaps_by_stream=reconnect_gaps_by_stream,
            slow_pause_observed=slow_pause_observed,
            telemetry=telemetry,
            telemetry_queue_drops=telemetry_queue_drops,
            telemetry_delivery_drops=telemetry_delivery_drops,
            telemetry_delivered=telemetry_delivered,
            max_queue_count=max_queue_count,
            max_queue_bytes=max_queue_bytes,
            max_market_poll_ms=max_market_poll_ms,
            account_identity=account_identity,
            runtime=runtime,
            include_samples=True,
        )
        control.send(
            (
                "closed",
                index,
                os.getpid(),
                finished_normally,
                runtime.cleanup_failures,
                runtime.unregister_uncertainties,
                order_failures,
                final_stats,
                _load01_trace_snapshot_or_none(rpc_trace),
            )
        )
    except BaseException as exc:
        if runtime_probe is not None:
            runtime_probe.set_phase("startup-failed")
        rpc_timing_at_failure = _load01_trace_snapshot_or_none(rpc_trace, max_events=32)
        _send_load01_rpc_diagnostic(
            control,
            f"strategy-{index:03d}",
            os.getpid(),
            rpc_timing_at_failure,
            "failure",
        )
        cleanup_started_ns = rpc_trace.now()
        child_diagnostic = {
            "runtime_start_duration_ms": runtime_start_duration_ms,
            "registration_lease": registration_lease_snapshot,
            "post_registration_lease": (
                post_registration_lease_snapshot
                if "post_registration_lease_snapshot" in locals()
                else None
            ),
            "market_loop_lease": (
                market_lease_snapshot if "market_lease_snapshot" in locals() else None
            ),
            "account_read_lease": (
                account_read_lease_snapshot if "account_read_lease_snapshot" in locals() else None
            ),
            "market_client_start_durations": market_client_start_samples,
            "account_refresh_duration_ms": account_refresh_duration_ms,
            "maintenance": runtime_probe.snapshot() if runtime_probe is not None else None,
            "runtime_lease_at_failure": (
                _runtime_lease_snapshot(runtime) if runtime is not None else None
            ),
            "safe_error_code": _safe_diagnostic_code(exc),
            "market_poll_failure": market_poll_failure,
            "rpc_timing_at_failure": rpc_timing_at_failure,
        }
        cleanup_diagnostic: dict[str, Any] = {
            "market_clients": [],
            "account_client": None,
            "telemetry": None,
            "runtime": None,
        }
        market_clients = {
            id(client): (stream_id, client)
            for stream_id, client in (*markets.items(), *pending_markets.items())
        }
        for stream_id, market in market_clients.values():
            close_returned = False
            safe_error_code = None
            try:
                market.close()
                close_returned = True
            except BaseException as cleanup_exc:
                safe_error_code = _safe_diagnostic_code(cleanup_exc)
            cleanup_diagnostic["market_clients"].append(
                {
                    "stream_id": stream_id,
                    "close_returned": close_returned,
                    "safe_error_code": safe_error_code,
                    "unsubscription_uncertain": market.unsubscription_uncertain,
                    "recovery_required": market.recovery_required,
                    "healthy_after_close": market.healthy,
                }
            )
        if account_client is not None:
            try:
                account_client.close()
                cleanup_diagnostic["account_client"] = {"close_returned": True}
            except BaseException as cleanup_exc:
                cleanup_diagnostic["account_client"] = {
                    "close_returned": False,
                    "safe_error_code": _safe_diagnostic_code(cleanup_exc),
                }
        if telemetry is not None:
            try:
                telemetry.close()
                cleanup_diagnostic["telemetry"] = {
                    "close_returned": True,
                    "state": telemetry.stats.state,
                }
            except BaseException as cleanup_exc:
                cleanup_diagnostic["telemetry"] = {
                    "close_returned": False,
                    "safe_error_code": _safe_diagnostic_code(cleanup_exc),
                    "state": telemetry.stats.state,
                }
        if runtime is not None:
            if runtime.state == "closed":
                cleanup_diagnostic["runtime"] = {
                    "close_returned": True,
                    "state": runtime.state,
                    "cleanup_failures": runtime.cleanup_failures,
                    "unregister_uncertainties": runtime.unregister_uncertainties,
                    "unknown_registration_count": runtime.unknown_registration_count,
                }
            else:
                try:
                    runtime.close()
                    cleanup_diagnostic["runtime"] = {
                        "close_returned": True,
                        "state": runtime.state,
                        "cleanup_failures": runtime.cleanup_failures,
                        "unregister_uncertainties": runtime.unregister_uncertainties,
                        "unknown_registration_count": runtime.unknown_registration_count,
                    }
                except BaseException as cleanup_exc:
                    cleanup_diagnostic["runtime"] = {
                        "close_returned": False,
                        "safe_error_code": _safe_diagnostic_code(cleanup_exc),
                        "state": runtime.state,
                        "cleanup_failures": runtime.cleanup_failures,
                        "unregister_uncertainties": runtime.unregister_uncertainties,
                        "unknown_registration_count": runtime.unknown_registration_count,
                    }
        child_diagnostic["cleanup"] = cleanup_diagnostic
        child_diagnostic["runtime_lease_after_cleanup"] = (
            _runtime_lease_snapshot(runtime) if runtime is not None else None
        )
        cleanup_ended_ns = rpc_trace.now()
        child_diagnostic["rpc_timing_cleanup_duration_ns"] = (
            max(0, cleanup_ended_ns - cleanup_started_ns)
            if type(cleanup_started_ns) is int and type(cleanup_ended_ns) is int
            else None
        )
        with suppress(Exception):
            control.send(
                (
                    "error",
                    index,
                    os.getpid(),
                    _safe_diagnostic_code(exc),
                    child_diagnostic,
                )
            )
        raise
    finally:
        control.close()


def _strategy_stats(
    *,
    index,
    market_receives,
    last_sequences,
    order_cycles,
    account_reads,
    e2e_samples,
    order_samples,
    account_samples,
    duplicate_events,
    unrecovered_gap_count,
    unrecovered_gap_events,
    reconnect_gap_events,
    reconnect_records,
    reconnect_gaps_by_stream,
    slow_pause_observed,
    telemetry,
    telemetry_queue_drops,
    telemetry_delivery_drops,
    telemetry_delivered,
    max_queue_count,
    max_queue_bytes,
    max_market_poll_ms,
    account_identity,
    runtime,
    include_samples=False,
):
    telem = telemetry.stats
    return {
        "index": index,
        "account_index": index // _PER_ACCOUNT,
        "local_index": index % _PER_ACCOUNT,
        "market_events": sum(market_receives.values()),
        "market_by_stream": dict(market_receives),
        "last_sequences": dict(last_sequences),
        "order_cycles": order_cycles,
        "account_reads": account_reads,
        "market_e2e_ms": _percentile_summary(e2e_samples),
        "order_lifecycle_ms": _percentile_summary(order_samples),
        "account_read_ms": _percentile_summary(account_samples),
        "duplicate_events": duplicate_events,
        "unrecovered_gap_count": unrecovered_gap_count,
        "unrecovered_gap_events": unrecovered_gap_events,
        "intentional_reconnect_gap_events": reconnect_gap_events,
        "intentional_reconnect_gaps_by_stream": dict(reconnect_gaps_by_stream),
        "reconnect_records": list(reconnect_records),
        "slow_pause": slow_pause_observed,
        "market_queue_max_count": max_queue_count,
        "market_queue_max_bytes": max_queue_bytes,
        "market_poll_max_ms": max_market_poll_ms,
        "telemetry": {
            "queued_events": telem.queued_events,
            "queued_bytes": telem.queued_bytes,
            "dropped_queue_total": telem.dropped_queue_total,
            "dropped_delivery_total": telem.dropped_delivery_total,
            "delivered_total": telem.delivered_total,
            "state": telem.state,
        },
        "account_identity": account_identity,
        "runtime_state": runtime.state,
        "runtime_cleanup_failures": runtime.cleanup_failures,
        "runtime_unregister_uncertainties": runtime.unregister_uncertainties,
        "active_bindings": len(runtime.bindings),
        "market_e2e_samples_ms": list(e2e_samples) if include_samples else None,
        "order_samples_ms": list(order_samples) if include_samples else None,
        "account_samples_ms": list(account_samples) if include_samples else None,
    }


def _percentile_summary(values) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "p50": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(float(value) for value in values)

    def at(percentile: float) -> float:
        index = min(len(ordered) - 1, int((len(ordered) - 1) * percentile))
        return ordered[index]

    return {
        "count": len(ordered),
        "p50": at(0.50),
        "p95": at(0.95),
        "p99": at(0.99),
        "max": ordered[-1],
    }


def _startup_maintenance_report(
    diagnostics_by_strategy: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    runtime_start_ms = []
    account_refresh_ms = []
    market_start_ms = []
    boundary_checks = 0
    delay_checks = 0
    rpc_calls_by_phase: Counter[str] = Counter()
    maintenance_calls_by_reason: Counter[str] = Counter()
    max_rpc_ms_by_operation: dict[str, float] = {}
    max_maintenance_ms = 0.0
    maintenance_failures = 0
    details = []
    for strategy_index, diagnostic in sorted(diagnostics_by_strategy.items()):
        runtime_start_ms.append(diagnostic["runtime_start_duration_ms"])
        account_refresh_ms.append(diagnostic["account_refresh_duration_ms"])
        market_start_ms.extend(
            item["duration_ms"] for item in diagnostic["market_client_start_durations"]
        )
        maintenance = diagnostic["maintenance"]
        boundary_checks += maintenance["rpc_boundary_checks"]
        delay_checks += maintenance["maintenance_delay_checks"]
        rpc_calls_by_phase.update(maintenance["rpc_calls_by_phase"])
        maintenance_calls_by_reason.update(maintenance["maintenance_calls_by_reason"])
        maintenance_failures += maintenance["maintenance_failure_count"]
        max_maintenance_ms = max(max_maintenance_ms, maintenance["maintenance_max_duration_ms"])
        for operation, elapsed_ms in maintenance["rpc_max_duration_ms_by_operation"].items():
            max_rpc_ms_by_operation[operation] = max(
                max_rpc_ms_by_operation.get(operation, 0.0), elapsed_ms
            )
        details.append(
            {
                "strategy_index": strategy_index,
                "runtime_start_duration_ms": diagnostic["runtime_start_duration_ms"],
                "registration_lease": diagnostic["registration_lease"],
                "post_registration_lease": diagnostic["post_registration_lease"],
                "market_loop_lease": diagnostic["market_loop_lease"],
                "account_read_lease": diagnostic["account_read_lease"],
                "market_client_start_durations": diagnostic["market_client_start_durations"],
                "account_refresh_duration_ms": diagnostic["account_refresh_duration_ms"],
                "maintenance": maintenance,
            }
        )
    return {
        "lease_seconds": _LEASE_SECONDS,
        "rpc_timeout_seconds": _RPC_TIMEOUT_MS / 1000,
        "runtime_start_duration_ms": _percentile_summary(runtime_start_ms),
        "single_market_client_start_duration_ms": _percentile_summary(market_start_ms),
        "account_refresh_duration_ms": _percentile_summary(account_refresh_ms),
        "rpc_boundary_checks": boundary_checks,
        "maintenance_delay_checks": delay_checks,
        "rpc_calls_by_phase": dict(rpc_calls_by_phase),
        "maintenance_calls_by_reason": dict(maintenance_calls_by_reason),
        "rpc_max_duration_ms_by_operation": max_rpc_ms_by_operation,
        "maintenance_max_duration_ms": max_maintenance_ms,
        "maintenance_failures": maintenance_failures,
        "per_strategy": details,
    }


def _consume_rpc_diagnostic(record: dict[str, Any], message: Any) -> bool:
    if not isinstance(message, tuple) or not message or message[0] != "rpc-diagnostic":
        return False
    if (
        len(message) != 5
        or type(message[1]) is not str
        or type(message[2]) is not int
        or message[2] != record.get("pid")
        or type(message[3]) is not dict
        or type(message[4]) is not str
        or message[4] not in {"failure", "emergency"}
    ):
        return True
    stage = message[4]
    record.setdefault("rpc_timing_snapshots", {})[stage] = message[3]
    stages = record.setdefault("rpc_timing_snapshot_stages", [])
    if stage not in stages:
        stages.append(stage)
    return True


def _collect_expected(
    records: dict[int, dict[str, Any]],
    expected_tag: str,
    timeout: float,
    label: str,
    progress: dict[str, Any],
) -> dict[int, tuple[Any, ...]]:
    pending = set(records)
    received: dict[int, tuple[Any, ...]] = {}
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        made_progress = False
        for key in tuple(pending):
            record = records[key]
            deferred = record.setdefault("deferred_messages", [])
            if deferred or record["pipe"].poll(0):
                message = deferred.pop(0) if deferred else record["pipe"].recv()
                made_progress = True
                tag = message[0] if isinstance(message, tuple) and message else None
                if tag == "rpc-diagnostic":
                    _consume_rpc_diagnostic(record, message)
                    continue
                if tag == "progress":
                    _save_progress(message, progress)
                    continue
                if tag == "error":
                    raise _load01_child_failure(label, record["name"], message)
                if tag != expected_tag:
                    raise AssertionError(
                        f"{label} child returned an unexpected control tag: {record['name']}:{tag}"
                    )
                received[key] = message
                pending.remove(key)
                continue
            if not record["process"].is_alive():
                while record["pipe"].poll(0):
                    message = record["pipe"].recv()
                    tag = message[0] if isinstance(message, tuple) and message else None
                    if tag == "rpc-diagnostic":
                        _consume_rpc_diagnostic(record, message)
                        continue
                    if tag == "progress":
                        _save_progress(message, progress)
                        continue
                    if tag == "error":
                        raise _load01_child_failure(label, record["name"], message)
                raise AssertionError(
                    f"{label} child exited before {expected_tag}: "
                    f"{record['name']}:{record['process'].exitcode}"
                )
        for key, record in records.items():
            if key in pending:
                continue
            if not record["pipe"].poll(0):
                continue
            message = record["pipe"].recv()
            tag = message[0] if isinstance(message, tuple) and message else None
            if tag == "error":
                raise _load01_child_failure(
                    label, record["name"], message, after_tag=expected_tag
                )
            if tag == "progress":
                _save_progress(message, progress)
                made_progress = True
            elif tag == "rpc-diagnostic":
                _consume_rpc_diagnostic(record, message)
                made_progress = True
            else:
                record.setdefault("deferred_messages", []).append(message)
        if not made_progress:
            time.sleep(0.01)
    if pending:
        state = {records[key]["name"]: records[key]["process"].exitcode for key in pending}
        raise TimeoutError(f"{label} timed out waiting for {expected_tag}: {state}")
    return received


class _Load01ChildFailureError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        diagnostic: dict[str, Any] | None,
        code: str,
    ) -> None:
        super().__init__(message)
        self.child_diagnostic = diagnostic
        self.code = code


def _load01_child_failure(
    label: str,
    name: str,
    message: tuple[Any, ...],
    *,
    after_tag: str | None = None,
) -> _Load01ChildFailureError:
    raw_code = message[3] if len(message) > 3 else None
    code = _safe_diagnostic_code_value(raw_code)
    if code == "UNEXPECTED_ERROR":
        code = "CHILD_FAILED"
    diagnostic = message[4] if len(message) > 4 and type(message[4]) is dict else None
    if diagnostic is not None:
        try:
            encoded = json.dumps(diagnostic, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
        except (TypeError, ValueError, UnicodeError):
            diagnostic = None
        else:
            if len(encoded) > 65_536:
                diagnostic = None
    suffix = f" after {after_tag}" if after_tag else ""
    return _Load01ChildFailureError(
        f"{label} child failed{suffix}: {name}:{code}",
        diagnostic=diagnostic,
        code=code,
    )


def _save_progress(message: tuple[Any, ...], progress: dict[str, Any]) -> None:
    if len(message) >= 3 and type(message[1]) is int:
        progress.setdefault("strategy", {})[message[1]] = message[2]
    elif len(message) >= 4 and message[1] == "owner":
        progress.setdefault("owner", {})[message[2]] = message[3]
    elif len(message) >= 3 and message[1] in {"market", "monitor"}:
        progress[message[1]] = message[2]


def _drain_progress(records: dict[str, dict[str, Any]], progress: dict[str, Any]) -> None:
    for record in records.values():
        while record["pipe"].poll(0):
            message = record["pipe"].recv()
            tag = message[0] if isinstance(message, tuple) and message else None
            if tag == "progress":
                _save_progress(message, progress)
            elif tag == "rpc-diagnostic":
                _consume_rpc_diagnostic(record, message)
                continue
            elif tag == "error":
                raise _load01_child_failure("LOAD01", record["name"], message)
            else:
                raise AssertionError(
                    f"LOAD01 child sent an unexpected message during load: {record['name']}:{tag}"
                )
        if not record["process"].is_alive():
            raise AssertionError(
                f"LOAD01 child exited before shutdown: "
                f"{record['name']}:{record['process'].exitcode}"
            )


def _durable_outbox_audit(
    database_path: Path,
    source_records: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    expected_by_scope = {record["outbox_scope"]: record for record in source_records}
    if len(expected_by_scope) != _STRATEGIES:
        raise AssertionError("LOAD01 owner source map did not contain 100 unique scopes")
    actual: dict[str, list[tuple[str, int, str, dict[str, Any]]]] = {
        scope: [] for scope in expected_by_scope
    }
    path = database_path.resolve()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5.0) as connection:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT scope, event_id, data_json FROM monitor_outbox_events ORDER BY sequence"
        ).fetchall()

    seen_outbox_ids: set[str] = set()
    seen_source_ids: set[tuple[str, str, int]] = set()
    for scope, event_id, data_json in rows:
        if scope not in expected_by_scope or event_id in seen_outbox_ids:
            raise AssertionError("LOAD01 durable outbox had an unexpected or duplicate row")
        seen_outbox_ids.add(event_id)
        data = json.loads(data_json)
        source = expected_by_scope[scope]
        source_scope = data.get("source_scope")
        stream = data.get("stream")
        sequence = data.get("source_sequence")
        source_event_id = data.get("source_event_id")
        if (
            data.get("journal_generation") != source["source_generation"]
            or data.get("source_scope_key") != source["source_scope_key"]
            or not isinstance(source_scope, dict)
            or source_scope.get("provider") != "synthetic"
            or source_scope.get("environment") != "paper"
            or source_scope.get("account_ref") != source["account_ref"]
            or source_scope.get("strategy_id") != source["strategy_ref"]
            or source_scope.get("trading_day") != _EXECUTION_DAY
            or stream not in {"execution", "cancellation"}
            or type(sequence) is not int
            or sequence <= 0
            or type(source_event_id) is not str
            or len(source_event_id) != 32
        ):
            raise AssertionError("LOAD01 durable event crossed account/strategy scope")
        source_key = (scope, stream, sequence)
        if source_key in seen_source_ids:
            raise AssertionError("LOAD01 durable outbox duplicated a source journal event")
        seen_source_ids.add(source_key)
        actual[scope].append((stream, sequence, source_event_id, data))

    rows_by_scope: dict[str, int] = {}
    for scope, source in expected_by_scope.items():
        scope_rows = sorted(actual[scope], key=lambda item: (item[0], item[1]))
        digest = hashlib.sha256()
        counts: Counter[str] = Counter()
        for stream, sequence, event_id, _data in scope_rows:
            digest.update(f"{stream}\0{sequence}\0{event_id}\n".encode())
            counts[stream] += 1
        if (
            len(scope_rows) != source["source_rows"]
            or dict(counts) != source["source_rows_by_stream"]
            or digest.hexdigest() != source["source_event_digest"]
            or len(scope_rows) != source["relay_delivered"]
        ):
            raise AssertionError("LOAD01 Monitor durable rows differ from the exact source journal")
        rows_by_scope[scope] = len(scope_rows)

    return {
        "durable_rows": len(rows),
        "durable_scopes": len(actual),
        "unique_outbox_event_ids": len(seen_outbox_ids),
        "unique_source_event_keys": len(seen_source_ids),
        "rows_by_scope": rows_by_scope,
        "source_digest_match_count": sum(len(items) for items in actual.values()),
    }


def _registry_count(path: Path) -> int:
    resolved = path.resolve()
    with sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=5.0) as connection:
        connection.execute("PRAGMA query_only=ON")
        return int(connection.execute("SELECT count(*) FROM sessions").fetchone()[0])


def _registered_session_inventory(registries: dict[str, Path]) -> dict[str, int]:
    return {name: _registry_count(path) for name, path in registries.items()}


def _database_tree_size(paths: tuple[Path, ...]) -> int:
    candidates: set[Path] = set()
    for path in paths:
        candidates.update((path, Path(f"{path}-wal"), Path(f"{path}-shm")))
    return sum(path.stat().st_size for path in candidates if path.exists())


def _resource_sample(
    records: dict[str, dict[str, Any]],
    *,
    started_at: float,
    prior_cpu: dict[int, float],
    sample_at: float,
    previous_sample_at: float,
    logical_cpu_count: int,
) -> tuple[dict[str, Any], dict[int, float]]:
    rss_values = {}
    current_cpu = {}
    cpu_delta_seconds = 0.0
    for record in records.values():
        process = record["process"]
        pid = record["pid"]
        proc = psutil.Process(pid)
        rss_values[record["name"]] = proc.memory_info().rss
        cpu = proc.cpu_times()
        total_cpu = float(cpu.user + cpu.system)
        current_cpu[pid] = total_cpu
        cpu_delta_seconds += max(0.0, total_cpu - prior_cpu.get(pid, total_cpu))
        if not process.is_alive():
            raise AssertionError(f"LOAD01 process exited during load: {record['name']}")
    interval = max(0.001, sample_at - previous_sample_at)
    return (
        {
            "elapsed_seconds": sample_at - started_at,
            "sample_interval_seconds": interval,
            "available_memory_bytes": psutil.virtual_memory().available,
            "child_rss_bytes": sum(rss_values.values()),
            "child_rss_max_bytes": max(rss_values.values(), default=0),
            "child_process_count": len(rss_values),
            "child_cpu_percent_of_machine": (
                100.0 * cpu_delta_seconds / interval / logical_cpu_count
            ),
            "system_cpu_percent": psutil.cpu_percent(interval=None),
        },
        current_cpu,
    )


def _progress_summary(progress: dict[str, Any]) -> dict[str, Any]:
    strategy_rows = tuple(progress.get("strategy", {}).values())
    return {
        "strategy_progress_count": len(strategy_rows),
        "order_cycles": sum(int(row.get("order_cycles", 0)) for row in strategy_rows),
        "market_events": sum(int(row.get("market_events", 0)) for row in strategy_rows),
        "unrecovered_market_gap_events": sum(
            int(row.get("unrecovered_gap_events", 0)) for row in strategy_rows
        ),
        "telemetry_queue_drops": sum(
            int(row.get("telemetry", {}).get("dropped_queue_total", 0)) for row in strategy_rows
        ),
        "telemetry_delivery_drops": sum(
            int(row.get("telemetry", {}).get("dropped_delivery_total", 0)) for row in strategy_rows
        ),
        "max_market_queue_events": max(
            (int(row.get("market_queue_max_count", 0)) for row in strategy_rows), default=0
        ),
        "max_market_queue_bytes": max(
            (int(row.get("market_queue_max_bytes", 0)) for row in strategy_rows), default=0
        ),
        "market": progress.get("market"),
        "monitor": progress.get("monitor"),
        "owners": progress.get("owner", {}),
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _normal_close_records(
    records: dict[str, dict[str, Any]],
    messages: dict[str, tuple[Any, ...]],
) -> None:
    for name, record in records.items():
        message = messages[name]
        expected_pid = record["pid"]
        message_pid = message[2] if record["kind"] in {"strategy", "owner"} else message[1]
        if message_pid != expected_pid:
            raise AssertionError(f"LOAD01 normal-close pid mismatch: {name}")
    for name, record in records.items():
        process = record["process"]
        process.join(30)
        if process.is_alive() or process.exitcode != 0:
            raise AssertionError(f"LOAD01 child did not exit normally: {name}:{process.exitcode}")
        record["exit_code"] = process.exitcode
        record["normal_exit"] = True
        record["normal_close_ack"] = True
        record["pipe"].close()
        process.close()
        record["closed"] = True


def _emergency_cleanup(
    records: dict[str, dict[str, Any]],
    *,
    abort_event,
    stop_event,
    terminated: list[str],
    cleanup_errors: list[str],
) -> None:
    abort_event.set()
    stop_event.set()

    def retain_snapshot(record: dict[str, Any], label: str, value: Any) -> None:
        if type(value) is dict:
            record.setdefault("rpc_timing_snapshots", {})[label] = value
            stages = record.setdefault("rpc_timing_snapshot_stages", [])
            if label not in stages:
                stages.append(label)

    def retain_message(record: dict[str, Any], message: Any) -> None:
        if not isinstance(message, tuple) or not message:
            return
        tag = message[0]
        if tag == "rpc-diagnostic":
            _consume_rpc_diagnostic(record, message)
        elif tag == "error" and len(message) >= 5 and type(message[4]) is dict:
            retain_snapshot(record, "at_failure", message[4].get("rpc_timing_at_failure"))
        elif tag == "closed":
            trace_index = 8 if record["kind"] == "strategy" else 16
            if len(message) > trace_index:
                retain_snapshot(record, "emergency_close", message[trace_index])

    def drain_diagnostics(record: dict[str, Any], limit: int) -> None:
        pipe = record["pipe"]
        for _ in range(limit):
            try:
                if not pipe.poll(0):
                    return
                retain_message(record, pipe.recv())
            except (EOFError, OSError, ValueError) as exc:
                record["rpc_timing_collection_error"] = type(exc).__name__
                return

    # Failed children can already have queued their bounded failure snapshot.
    # Drain those pipes before asking the surviving workers to close.
    for record in records.values():
        if record["closed"]:
            continue
        try:
            if not record["process"].is_alive():
                drain_diagnostics(record, 64)
        except (AssertionError, OSError, ValueError) as exc:
            cleanup_errors.append(f"{record['name']}:state:{type(exc).__name__}")

    for record in records.values():
        if record["closed"]:
            continue
        process = record["process"]
        diagnostic_ready = False
        try:
            if process.is_alive() and not (
                record["kind"] == "strategy" and not record.get("ready")
            ):
                diagnostic_ready = (
                    record["kind"] == "strategy" and record.get("ready")
                ) or (record["kind"] == "owner" and record.get("rpc_diagnostic_ready"))
                record["pipe"].send(
                    ("close", "diagnostic") if diagnostic_ready else ("close",)
                )
                if diagnostic_ready:
                    record["rpc_diagnostic_close_sent"] = True
        except (BrokenPipeError, EOFError, OSError) as exc:
            cleanup_errors.append(f"{record['name']}:close-send:{type(exc).__name__}")
            if diagnostic_ready:
                drain_diagnostics(record, 64)

    def join_until(timeout: float) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        alive = []
        while time.monotonic() < deadline:
            alive = []
            for item in records.values():
                if item["closed"]:
                    continue
                process = item["process"]
                drain_diagnostics(item, 4)
                try:
                    process.join(0)
                    if process.is_alive():
                        alive.append(item)
                except (AssertionError, OSError, ValueError) as exc:
                    cleanup_errors.append(f"{item['name']}:state:{type(exc).__name__}")
            if not alive:
                return []
            time.sleep(0.05)
        return alive

    survivors = join_until(20)
    if survivors:
        # A bounded final nonblocking drain closes the race where the child
        # publishes its snapshot just after the grace loop's last poll.
        for item in records.values():
            if not item["closed"]:
                drain_diagnostics(item, 64)
        survivors = [item for item in survivors if item["process"].is_alive()]
        for item in survivors:
            item["rpc_snapshot_received_before_terminate"] = bool(
                item.get("rpc_timing_snapshot_stages")
            )
            try:
                item["process"].terminate()
                terminated.append(item["name"])
            except (OSError, ValueError) as exc:
                cleanup_errors.append(f"{item['name']}:terminate:{type(exc).__name__}")
        survivors = join_until(10)
    if survivors:
        for item in survivors:
            try:
                item["process"].kill()
            except (OSError, ValueError) as exc:
                cleanup_errors.append(f"{item['name']}:kill:{type(exc).__name__}")
        survivors = join_until(10)

    for record in records.values():
        if record["closed"]:
            continue
        process = record["process"]
        drain_diagnostics(record, 64)
        if process.is_alive():
            cleanup_errors.append(f"{record['name']}:still-alive")
            continue
        record["exit_code"] = process.exitcode
        record["normal_exit"] = False
        drain_diagnostics(record, 64)
        try:
            record["pipe"].close()
        except (OSError, ValueError) as exc:
            cleanup_errors.append(f"{record['name']}:pipe-close:{type(exc).__name__}")
        try:
            process.close()
            record["closed"] = True
        except (OSError, ValueError) as exc:
            cleanup_errors.append(f"{record['name']}:process-close:{type(exc).__name__}")


def test_load01_hundred_strategy_sustained(tmp_path: Path) -> None:
    """Run 100 real strategy child processes against synthetic local services."""
    if os.name != "nt":
        pytest.skip("LOAD01 acceptance runner uses the Windows spawn baseline")

    mode = os.environ.get("BT_API_LOAD01_MODE", "full")
    if mode not in {"pilot", "full"}:
        raise ValueError("BT_API_LOAD01_MODE must be pilot or full")
    duration_seconds = 120 if mode == "pilot" else _FULL_LOAD_SECONDS
    source_root = _fixed_source_root()
    export_pin_path = Path(os.environ["BT_API_LOAD01_EXPORT_PINS"]).resolve()
    archive_validation = _verify_fixed_archives(source_root, export_pin_path)
    source_manifest = _write_source_run_manifest(source_root, tmp_path)
    os.environ["BT_API_LOAD01_SOURCE_ROOT"] = str(source_root)
    os.environ["BT_API_LOAD01_SOURCE_MANIFEST"] = str(source_manifest)
    source_bindings = _check_fixed_source_manifest(source_manifest)
    output_path = Path(
        os.environ.get(
            "BT_API_LOAD01_OUTPUT",
            str(Path("D:/temp") / f"load01-{mode}-result.json"),
        )
    ).resolve()
    projected_process_rss = 112 * 72 * 1024**2
    preflight_required = projected_process_rss + _MIN_FREE_BYTES
    memory = psutil.virtual_memory()
    logical_cpu_count = psutil.cpu_count(logical=True) or 1
    report: dict[str, Any] = {
        "schema_version": 1,
        "case": "LOAD01_LOCAL_SYNTHETIC_100_STRATEGY_10_ACCOUNT",
        "mode": mode,
        "state": "PREFLIGHT",
        "formal_acceptance_eligible": mode == "full",
        "started_at_utc": _utc_now(),
        "fixed_parent_commit": _FIXED_PARENT,
        "python_executable": sys.executable,
        "source_archive_pins": json.loads(export_pin_path.read_text(encoding="utf-8")),
        "source_bindings": source_bindings,
        "topology": {
            "strategy_child_processes": _STRATEGIES,
            "account_execution_owner_child_processes": _ACCOUNTS,
            "shared_market_child_processes": 1,
            "independent_monitor_child_processes": 1,
            "total_child_processes": 112,
            "pytest_controller_processes": 1,
            "total_load_processes_including_controller": 113,
            "strategies_per_account": _PER_ACCOUNT,
            "public_market_topics": _TOPICS,
            "upstream_input_target_per_second": _MARKET_HZ,
            "strategy_market_deliveries_target_per_second": _MARKET_HZ * _STRATEGIES,
            "order_lifecycles_target_per_account_per_second": _LIFECYCLES_PER_ACCOUNT_HZ,
            "order_lifecycles_target_total_per_second": _LIFECYCLES_PER_ACCOUNT_HZ * _ACCOUNTS,
            "topic_payload_target_bytes": 256,
        },
        "preflight": {
            "logical_cpu_count": logical_cpu_count,
            "total_memory_bytes": memory.total,
            "available_memory_bytes": memory.available,
            "projected_mean_child_rss_bytes": 72 * 1024**2,
            "projected_all_child_rss_bytes": projected_process_rss,
            "required_available_for_start_bytes": preflight_required,
            "minimum_free_memory_reserve_bytes": _MIN_FREE_BYTES,
            "system_cpu_percent_before_start": psutil.cpu_percent(interval=None),
        },
        "resource_samples": [],
        "progress": {},
        "children": [],
        "cleanup": {"termination_requests": [], "errors": []},
    }
    _write_report(output_path, report)
    if memory.available < preflight_required:
        pytest.skip(
            "LOAD01 resource preflight did not meet the measured 112-child projection: "
            f"available={memory.available}, required={preflight_required}"
        )

    progress: dict[str, Any] = {"strategy": {}, "owner": {}}
    child_records: dict[str, dict[str, Any]] = {}
    strategy_records: dict[int, dict[str, Any]] = {}
    owner_records: dict[int, dict[str, Any]] = {}
    market_record: dict[str, Any] | None = None
    monitor_record: dict[str, Any] | None = None
    terminated: list[str] = []
    cleanup_errors: list[str] = []
    rpc_timing_snapshots: dict[str, dict[str, dict[str, Any]]] = {}
    abort_event = None
    stop_event = None
    failure: dict[str, Any] | None = None
    phase = "prepare"
    normal_shutdown_complete = False
    database_paths: tuple[Path, ...] = ()
    registries: dict[str, Path] = {}

    def launch_child(name: str, kind: str, target, args: tuple[Any, ...]) -> dict[str, Any]:
        record = _start_process(context, target, args, name)
        record.update(
            {
                "pid": record["process"].pid,
                "kind": kind,
                "ready": False,
                "closed": False,
            }
        )
        child_records[name] = record
        return record

    try:
        phase = "fixed-source-verification"
        if Path(__file__).resolve().is_relative_to(source_root) is False:
            raise AssertionError("LOAD01 runner was imported outside the fixed-source export")
        context = multiprocessing.get_context("spawn")
        endpoints = _free_endpoints(34)
        endpoint_index = 0
        market_rpc, market_pub = endpoints[endpoint_index : endpoint_index + 2]
        endpoint_index += 2
        account_endpoints = {index: endpoints[endpoint_index + index] for index in range(_ACCOUNTS)}
        endpoint_index += _ACCOUNTS
        execution_endpoints = {
            index: endpoints[endpoint_index + index] for index in range(_ACCOUNTS)
        }
        endpoint_index += _ACCOUNTS
        durable_endpoint, telemetry_endpoint = endpoints[endpoint_index : endpoint_index + 2]
        endpoint_index += 2
        owner_unused_endpoints = {
            index: endpoints[endpoint_index + index] for index in range(_ACCOUNTS)
        }

        market_keys = _market_keys()
        market_server_keys = zmq.curve_keypair()
        strategy_keys = {index: zmq.curve_keypair() for index in range(_STRATEGIES)}
        account_server_keys = {index: zmq.curve_keypair() for index in range(_ACCOUNTS)}
        execution_server_keys = {index: zmq.curve_keypair() for index in range(_ACCOUNTS)}
        durable_server_keys = zmq.curve_keypair()
        telemetry_server_keys = zmq.curve_keypair()
        relay_keys = {index: zmq.curve_keypair() for index in range(_ACCOUNTS)}

        account_services = {
            index: _account_service_info(
                index, account_endpoints[index], account_server_keys[index][0]
            )
            for index in range(_ACCOUNTS)
        }
        execution_services = {
            index: _execution_service_info(
                index, execution_endpoints[index], execution_server_keys[index][0]
            )
            for index in range(_ACCOUNTS)
        }
        market_audience = _market_authority_audience(strategy_keys)
        strategy_audience = tuple(
            (
                strategy_keys[index][0],
                _principal_ref(index),
                _strategy_ref(index),
                _ACCOUNT_REFS[index // _PER_ACCOUNT],
            )
            for index in range(_STRATEGIES)
        )
        owner_audiences = {
            account_index: tuple(
                {
                    "index": strategy_index,
                    "public_key": strategy_keys[strategy_index][0],
                    "principal_ref": _principal_ref(strategy_index),
                    "strategy_ref": _strategy_ref(strategy_index),
                }
                for strategy_index in range(
                    account_index * _PER_ACCOUNT, (account_index + 1) * _PER_ACCOUNT
                )
            )
            for account_index in range(_ACCOUNTS)
        }
        account_configs = {
            index: _account_service_config(account_services[index], account_endpoints[index])
            for index in range(_ACCOUNTS)
        }
        execution_configs = {
            index: _execution_service_config(execution_services[index])
            for index in range(_ACCOUNTS)
        }
        market_config = _market_service_config(market_rpc, market_pub)
        durable_config = _monitor_service_config(
            service_id=_DURABLE_ID,
            scope_ref=_DURABLE_ROOT_SCOPE_REF,
            instance_id=_DURABLE_INSTANCE,
            listener_ref=_DURABLE_LISTENER_REF,
            endpoint=durable_endpoint,
            security_ref=_DURABLE_SECURITY_REF,
            role="durable_ingress",
        )
        telemetry_config = _monitor_service_config(
            service_id=_TELEMETRY_ID,
            scope_ref=_TELEMETRY_SCOPE_REF,
            instance_id=_TELEMETRY_INSTANCE,
            listener_ref=_TELEMETRY_LISTENER_REF,
            endpoint=telemetry_endpoint,
            security_ref=_TELEMETRY_SECURITY_REF,
            role="telemetry_ingress",
        )

        work_root = tmp_path / "load01-runtime"
        work_root.mkdir(parents=True, exist_ok=True)
        guard_directory = work_root / "session-guards"
        guard_directory.mkdir(exist_ok=True)
        registry_directory = work_root / "registries"
        registry_directory.mkdir(exist_ok=True)
        journal_directory = work_root / "journals"
        journal_directory.mkdir(exist_ok=True)
        checkpoint_directory = work_root / "relay-checkpoints"
        checkpoint_directory.mkdir(exist_ok=True)
        outbox_path = work_root / "monitor-outbox.sqlite3"
        market_registry = registry_directory / "market.sqlite3"
        durable_registry = registry_directory / "monitor-durable.sqlite3"
        telemetry_registry = registry_directory / "monitor-telemetry.sqlite3"
        account_registry_paths = {
            index: registry_directory / f"account-{index:02d}.sqlite3" for index in range(_ACCOUNTS)
        }
        execution_registry_paths = {
            index: registry_directory / f"execution-{index:02d}.sqlite3"
            for index in range(_ACCOUNTS)
        }
        gateway_journal_paths = {
            index: journal_directory / f"gateway-account-{index:02d}.sqlite3"
            for index in range(_ACCOUNTS)
        }
        execution_store_paths = {
            index: journal_directory / f"execution-account-{index:02d}.sqlite3"
            for index in range(_ACCOUNTS)
        }
        registries = {
            "market": market_registry,
            "monitor-durable": durable_registry,
            "monitor-telemetry": telemetry_registry,
        }
        for index in range(_ACCOUNTS):
            registries[f"account-{index:02d}"] = account_registry_paths[index]
            registries[f"execution-{index:02d}"] = execution_registry_paths[index]
        database_paths = (
            outbox_path,
            *gateway_journal_paths.values(),
            *execution_store_paths.values(),
            *registries.values(),
            *(
                checkpoint_directory
                / f"relay-{account_index:02d}-{local_index:02d}-checkpoint.sqlite3"
                for account_index in range(_ACCOUNTS)
                for local_index in range(_PER_ACCOUNT)
            ),
        )
        registration_event = context.Event()
        load_event = context.Event()
        stop_event = context.Event()
        abort_event = context.Event()
        load_start_value = context.Value("d", 0.0)
        report["preflight"].update(
            {
                "source_verified_archive_count": archive_validation["archive_count"],
                "source_verified_python_file_count": archive_validation["python_file_count"],
                "fixed_source_root": str(source_root),
                "work_root": str(work_root),
            }
        )

        phase = "start-market-host"
        market_record = launch_child(
            "market-hub",
            "market",
            _market_host_process,
            (
                market_config,
                str(market_registry),
                market_server_keys,
                market_audience,
                market_keys,
                load_event,
                load_start_value,
                stop_event,
            ),
        )
        market_message = _collect_expected({0: market_record}, "ready", 60.0, "market", progress)[0]
        if (
            market_message[2] != market_record["pid"]
            or not market_message[3]
            or not market_message[4]
        ):
            raise AssertionError("LOAD01 market host did not bind its exact local endpoints")
        market_record["ready"] = True
        market_rpc = market_message[3]
        market_pub = market_message[4]
        report["progress"] = _progress_summary(progress)

        phase = "start-account-owners"
        for account_index in range(_ACCOUNTS):
            owner_records[account_index] = launch_child(
                f"owner-{account_index:02d}",
                "owner",
                _owner_process,
                (
                    account_index,
                    account_configs[account_index],
                    execution_configs[account_index],
                    str(account_registry_paths[account_index]),
                    str(execution_registry_paths[account_index]),
                    str(gateway_journal_paths[account_index]),
                    str(guard_directory),
                    str(execution_store_paths[account_index]),
                    str(checkpoint_directory),
                    account_server_keys[account_index],
                    execution_server_keys[account_index],
                    owner_audiences[account_index],
                    owner_unused_endpoints[account_index],
                ),
            )
        owner_ready = _collect_expected(owner_records, "ready", 90.0, "account owner", progress)
        scope_records_by_account: dict[int, tuple[dict[str, Any], ...]] = {}
        for account_index, record in owner_records.items():
            message = owner_ready[account_index]
            if message[2] != account_index or message[3] != record["pid"]:
                raise AssertionError(f"LOAD01 owner identity mismatch for account {account_index}")
            account_status = message[6]
            if (
                account_status["service_state"] != "started"
                or account_status["worker_ready"] is not True
                or account_status["account_key"] != _ACCOUNT_KEYS[account_index].to_dict()
                or account_status["provider_connect_calls"] != 1
                or account_status["provider_read_calls"] != 1
                or account_status["provider_close_calls"] != 0
            ):
                raise AssertionError(f"LOAD01 account session isolation failed: {account_index}")
            record["ready"] = True
            record["account_service_endpoint"] = message[4]
            record["execution_service_endpoint"] = message[5]
            scope_records_by_account[account_index] = message[7]

        phase = "start-independent-monitor"
        monitor_owner_audiences = tuple(
            {
                "public_key": relay_keys[index][0],
                "principal_ref": f"{_RELAY_PRINCIPAL_PREFIX}{index:02d}",
                "actor_ref": f"{_RELAY_CLIENT_PREFIX}{index:02d}",
                "scopes": scope_records_by_account[index],
            }
            for index in range(_ACCOUNTS)
        )
        monitor_record = launch_child(
            "monitor",
            "monitor",
            _monitor_process,
            (
                durable_config,
                telemetry_config,
                str(durable_registry),
                str(telemetry_registry),
                str(outbox_path),
                durable_server_keys,
                telemetry_server_keys,
                monitor_owner_audiences,
                strategy_audience,
            ),
        )
        monitor_message = _collect_expected(
            {0: monitor_record}, "ready", 60.0, "monitor", progress
        )[0]
        if (
            monitor_message[2] != monitor_record["pid"]
            or monitor_message[5] != "started"
            or monitor_message[6] != "started"
        ):
            raise AssertionError("LOAD01 independent Monitor hosts did not become ready")
        monitor_record["ready"] = True

        for account_index, record in owner_records.items():
            relay_identity = (
                f"{_RELAY_PRINCIPAL_PREFIX}{account_index:02d}",
                f"{_RELAY_CLIENT_PREFIX}{account_index:02d}",
                relay_keys[account_index][0],
                relay_keys[account_index][1],
            )
            record["pipe"].send(
                ("connect-monitor", monitor_message[3], durable_server_keys[0], relay_identity)
            )
        relay_ready = _collect_expected(owner_records, "relay-ready", 90.0, "owner relay", progress)
        for account_index, message in relay_ready.items():
            if (
                message[1] != account_index
                or message[2] != owner_records[account_index]["pid"]
                or message[3] != 10
            ):
                raise AssertionError(
                    f"LOAD01 owner did not register ten durable scopes: {account_index}"
                )
            owner_records[account_index]["rpc_diagnostic_ready"] = True

        market_identity = WireIdentity(WireRole.SERVICE, _MARKET_PRINCIPAL, _MARKET_ID)
        telemetry_identity = WireIdentity(WireRole.SERVICE, _TELEMETRY_PRINCIPAL, _TELEMETRY_ID)
        market_service = {
            "service_id": _MARKET_ID,
            "instance_id": _MARKET_INSTANCE,
            "scope_ref": _MARKET_SCOPE_REF,
            "peer": market_identity,
            "endpoint": market_rpc,
            "market_endpoint": market_pub,
        }
        telemetry_service = {
            "service_id": _TELEMETRY_ID,
            "instance_id": _TELEMETRY_INSTANCE,
            "scope_ref": _TELEMETRY_SCOPE_REF,
            "peer": telemetry_identity,
            "endpoint": monitor_message[4],
        }
        service_records = [market_service, telemetry_service]
        service_records.extend(account_services.values())
        service_records.extend(execution_services.values())

        phase = "load-strategy-processes"
        for index in range(_STRATEGIES):
            account_index = index // _PER_ACCOUNT
            account_service = account_services[account_index]
            execution_service = execution_services[account_index]
            raw_strategy_config = _strategy_config(
                index,
                account_service,
                execution_service,
                market_rpc,
                market_pub,
                monitor_message[4],
            )
            client_public, client_secret = strategy_keys[index]
            security_material = {
                _MARKET_SECURITY_REF: (
                    market_server_keys[0],
                    client_public,
                    client_secret,
                ),
                account_service["security_ref"]: (
                    account_server_keys[account_index][0],
                    client_public,
                    client_secret,
                ),
                execution_service["security_ref"]: (
                    execution_server_keys[account_index][0],
                    client_public,
                    client_secret,
                ),
                _TELEMETRY_SECURITY_REF: (
                    telemetry_server_keys[0],
                    client_public,
                    client_secret,
                ),
            }
            strategy_records[index] = launch_child(
                f"strategy-{index:03d}",
                "strategy",
                _strategy_process,
                (
                    index,
                    raw_strategy_config,
                    account_service,
                    execution_service,
                    service_records,
                    security_material,
                    market_keys,
                    registration_event,
                    load_event,
                    load_start_value,
                    stop_event,
                    abort_event,
                    mode,
                ),
            )
        loaded = _collect_expected(
            strategy_records, "loaded", 180.0, "strategy process import", progress
        )
        if len({message[2] for message in loaded.values()}) != _STRATEGIES:
            raise AssertionError("LOAD01 did not start 100 distinct strategy processes")
        if any(
            message[1] != index
            or message[2] != strategy_records[index]["pid"]
            or not strategy_records[index]["process"].is_alive()
            for index, message in loaded.items()
        ):
            raise AssertionError("LOAD01 strategy loaded barrier lost a process")
        barrier_memory = psutil.virtual_memory().available
        report["preflight"]["memory_after_all_strategy_imports_bytes"] = barrier_memory
        if barrier_memory < _MIN_FREE_BYTES:
            raise RuntimeError(
                "LOAD01 all-strategy barrier could not retain 2 GiB free: "
                f"available={barrier_memory}"
            )
        registration_event.set()
        strategy_ready = _collect_expected(
            strategy_records, "ready", 300.0, "strategy registration and subscriptions", progress
        )
        for index, record in strategy_records.items():
            message = strategy_ready[index]
            if (
                message[1] != index
                or message[2] != record["pid"]
                or message[3] != _strategy_ref(index)
                or message[4] != _ACCOUNT_REFS[index // _PER_ACCOUNT]
                or message[7] != 5
                or message[8] != 0
                or message[9] != _ACCOUNT_KEYS[index // _PER_ACCOUNT].to_dict()
                or len(message[10]) != _TOPICS
                or any(sequence != 0 for _stream, sequence in message[10])
            ):
                raise AssertionError(
                    f"LOAD01 strategy did not pass its registration barrier: {index}"
                )
            record["ready"] = True
            record["account_index"] = index // _PER_ACCOUNT
            record["startup_ms"] = message[6]
            record["startup_diagnostic"] = message[11]
        ready_pids = {message[2] for message in strategy_ready.values()}
        all_pids = [record["pid"] for record in child_records.values()]
        if len(child_records) != 112 or len(set(all_pids)) != 112 or len(ready_pids) != 100:
            raise AssertionError("LOAD01 must keep exactly 112 distinct child processes alive")
        if not all(record["process"].is_alive() for record in child_records.values()):
            raise AssertionError("LOAD01 common start barrier includes a dead child")
        barrier_memory = psutil.virtual_memory().available
        report["preflight"]["memory_at_all_ready_barrier_bytes"] = barrier_memory
        if barrier_memory < _MIN_FREE_BYTES:
            raise RuntimeError(
                f"LOAD01 all-ready barrier could not retain 2 GiB free: available={barrier_memory}"
            )
        registry_before = _registered_session_inventory(registries)
        expected_registry_sessions = {
            "market": 100,
            "monitor-durable": 100,
            "monitor-telemetry": 100,
            **{f"account-{index:02d}": 10 for index in range(_ACCOUNTS)},
            **{f"execution-{index:02d}": 20 for index in range(_ACCOUNTS)},
        }
        if registry_before != expected_registry_sessions:
            raise AssertionError(
                "LOAD01 service registrations do not match the 100-strategy topology"
            )
        report["barrier"] = {
            "loaded_strategies": len(loaded),
            "ready_strategies": len(strategy_ready),
            "loaded_strategy_pids": sorted(message[2] for message in loaded.values()),
            "ready_strategy_pids": sorted(ready_pids),
            "distinct_child_pids": len(set(all_pids)),
            "all_112_children_alive": True,
            "free_memory_bytes": barrier_memory,
            "active_registry_sessions": registry_before,
            "strategy_startup_ms": _percentile_summary(
                [record["startup_ms"] for record in strategy_records.values()]
            ),
            "startup_lease_maintenance": _startup_maintenance_report(
                {index: record["startup_diagnostic"] for index, record in strategy_records.items()}
            ),
        }

        phase = "publish-common-load-barrier"
        load_start = time.monotonic() + 1.0
        load_start_value.value = load_start
        psutil.cpu_percent(interval=None)
        prior_cpu = {}
        for record in child_records.values():
            cpu_times = psutil.Process(record["pid"]).cpu_times()
            prior_cpu[record["pid"]] = float(cpu_times.user + cpu_times.system)
        load_event.set()
        report["load"] = {
            "scheduled_monotonic_start": load_start,
            "requested_duration_seconds": duration_seconds,
            "market_input_target_per_second": _MARKET_HZ,
            "market_payload_bytes_target": 256,
            "order_lifecycle_target_per_account_per_second": _LIFECYCLES_PER_ACCOUNT_HZ,
            "load_start_at_utc": _utc_now(),
        }
        _write_report(output_path, report)

        phase = "sustained-load"
        low_memory_samples = 0
        high_cpu_samples = 0
        last_resource_at = load_start
        next_resource_at = load_start
        next_report_at = load_start + 30
        stop_requested_at = None
        while True:
            _drain_progress(child_records, progress)
            now = time.monotonic()
            strategy_progress = tuple(progress.get("strategy", {}).values())
            if any(
                int(row.get("market_queue_max_count", 0)) > 256
                or int(row.get("market_queue_max_bytes", 0)) > 512 * 1024
                or int(row.get("telemetry", {}).get("dropped_queue_total", 0)) > 0
                or int(row.get("telemetry", {}).get("dropped_delivery_total", 0)) > 0
                for row in strategy_progress
            ):
                raise RuntimeError("LOAD01 bounded strategy queue/drop limit was exceeded")
            monitor_progress = progress.get("monitor", {})
            if int(monitor_progress.get("durable_event_rows", 0)) > int(
                _RELAY_MAX_EVENTS * 0.9
            ) or int(monitor_progress.get("durable_db_bytes", 0)) > int(_RELAY_MAX_BYTES * 0.9):
                raise RuntimeError("LOAD01 durable Monitor outbox crossed its 90% stop line")
            if now >= next_resource_at:
                sample, prior_cpu = _resource_sample(
                    child_records,
                    started_at=load_start,
                    prior_cpu=prior_cpu,
                    sample_at=now,
                    previous_sample_at=last_resource_at,
                    logical_cpu_count=logical_cpu_count,
                )
                sample["database_tree_bytes"] = _database_tree_size(database_paths)
                report["resource_samples"].append(sample)
                last_resource_at = now
                next_resource_at = now + 5.0
                low_memory_samples = (
                    low_memory_samples + 1
                    if sample["available_memory_bytes"] < _MIN_FREE_BYTES
                    else 0
                )
                high_cpu_samples = (
                    high_cpu_samples + 1 if sample["system_cpu_percent"] >= 95.0 else 0
                )
                if sample["database_tree_bytes"] > 2 * 1024**3:
                    raise RuntimeError("LOAD01 SQLite work tree exceeded 2 GiB")
                if low_memory_samples >= 2:
                    raise RuntimeError("LOAD01 free memory remained below 2 GiB for two samples")
                if high_cpu_samples >= 12:
                    raise RuntimeError("LOAD01 system CPU remained above 95% for one minute")
            report["progress"] = _progress_summary(progress)
            if now >= next_report_at:
                report["last_progress_at_utc"] = _utc_now()
                _write_report(output_path, report)
                next_report_at = now + 30.0
            if now >= load_start + duration_seconds:
                stop_requested_at = time.monotonic()
                stop_event.set()
                break
            time.sleep(0.02)

        assert stop_requested_at is not None
        load_elapsed = stop_requested_at - load_start
        report["load"].update(
            {
                "actual_duration_seconds": load_elapsed,
                "stop_requested_at_utc": _utc_now(),
            }
        )
        settle_deadline = time.monotonic() + 2.0
        while time.monotonic() < settle_deadline:
            _drain_progress(child_records, progress)
            time.sleep(0.02)
        report["progress"] = _progress_summary(progress)

        phase = "normal-strategy-shutdown"
        for record in strategy_records.values():
            record["pipe"].send(("close",))
        strategy_closed = _collect_expected(
            strategy_records, "closed", 180.0, "strategy normal close", progress
        )
        _normal_close_records(
            {f"strategy-{index:03d}": strategy_records[index] for index in strategy_records},
            {f"strategy-{index:03d}": strategy_closed[index] for index in strategy_closed},
        )
        strategy_results = {}
        for index, message in strategy_closed.items():
            if len(message) > 8 and type(message[8]) is dict:
                rpc_timing_snapshots.setdefault(f"strategy-{index:03d}", {})[
                    "normal_close"
                ] = message[8]
            if message[1] != index or message[2] != strategy_records[index]["pid"]:
                raise AssertionError(f"LOAD01 strategy close identity mismatch: {index}")
            if (
                message[3] is not True
                or message[4]
                or message[5]
                or message[6]
                or message[7]["runtime_state"] != "closed"
                or message[7]["active_bindings"] != 0
                or message[7]["telemetry"]["state"] != "closed"
                or message[7]["telemetry"]["dropped_queue_total"]
                or message[7]["telemetry"]["dropped_delivery_total"]
            ):
                raise AssertionError(
                    f"LOAD01 strategy reported uncertain cleanup or drops: {index}"
                )
            strategy_results[index] = message[7]
        phase = "normal-market-shutdown"
        market_record["pipe"].send(("close",))
        market_closed = _collect_expected(
            {0: market_record}, "closed", 60.0, "market normal close", progress
        )[0]
        _normal_close_records({"market-hub": market_record}, {"market-hub": market_closed})
        market_status = market_closed[4]
        market_start_counts = market_status["provider_starts"]
        market_stop_counts = market_status["provider_stops"]
        if (
            market_closed[2] != "closed"
            or market_closed[3]
            or set(market_start_counts) != {key.stream_id for key in market_keys}
            or any(market_start_counts.get(key.stream_id) != 1 for key in market_keys)
            or any(market_stop_counts.get(key.stream_id) != 1 for key in market_keys)
            or market_status["active_provider_keys"]
        ):
            raise AssertionError("LOAD01 market source sharing or normal final release failed")
        phase = "normal-owner-and-durable-relay-shutdown"
        for record in owner_records.values():
            record["pipe"].send(("close",))
        owner_closed = _collect_expected(
            owner_records, "closed", 180.0, "owner normal close", progress
        )
        _normal_close_records(
            {f"owner-{index:02d}": owner_records[index] for index in owner_records},
            {f"owner-{index:02d}": owner_closed[index] for index in owner_closed},
        )
        owner_source_records: list[dict[str, Any]] = []
        owner_totals = {}
        for account_index, message in owner_closed.items():
            if len(message) > 16 and type(message[16]) is dict:
                rpc_timing_snapshots.setdefault(f"owner-{account_index:02d}", {})[
                    "normal_close"
                ] = message[16]
            status = message[13]
            account_record = {
                "account_ref": _ACCOUNT_REFS[account_index],
                "process_pid": message[2],
                "account_service_state": message[3],
                "account_service_cleanup_failures": message[4],
                "execution_service_state": message[5],
                "execution_service_cleanup_failures": message[6],
                "relay_runtime_state": message[7],
                "relay_runtime_cleanup_failures": message[8],
                "relay_runtime_unregister_uncertainties": message[9],
                "provider_submit_calls": message[10],
                "provider_cancel_calls": message[11],
                "relayed_event_count": message[12],
                "account_provider": status,
            }
            if (
                message[1] != account_index
                or message[2] != owner_records[account_index]["pid"]
                or message[3] != "closed"
                or message[4]
                or message[5] != "closed"
                or message[6]
                or message[7] != "closed"
                or message[8]
                or message[9]
                or status["provider_close_calls"] != 1
            ):
                raise AssertionError(
                    f"LOAD01 owner or account session failed normal close: {account_index}"
                )
            owner_totals[account_index] = account_record
            owner_source_records.extend(message[15])
        phase = "normal-monitor-shutdown"
        monitor_record["pipe"].send(("close",))
        monitor_closed = _collect_expected(
            {0: monitor_record}, "closed", 60.0, "monitor normal close", progress
        )[0]
        _normal_close_records({"monitor": monitor_record}, {"monitor": monitor_closed})
        monitor_status = monitor_closed[6]
        telemetry_snapshots = monitor_closed[7]
        if (
            monitor_closed[2] != "closed"
            or monitor_closed[3]
            or monitor_closed[4] != "closed"
            or monitor_closed[5]
            or monitor_status["durable_scope_count"] != _STRATEGIES
            or len(telemetry_snapshots) != _STRATEGIES
            or any(
                item["stream_count"] != 1 or item["state"] != "collected"
                for item in telemetry_snapshots
            )
        ):
            raise AssertionError("LOAD01 Monitor outbox or telemetry scope coverage failed")
        phase = "exact-durable-and-registration-audit"
        durable_audit = _durable_outbox_audit(outbox_path, tuple(owner_source_records))
        total_strategy_cycles = sum(row["order_cycles"] for row in strategy_results.values())
        for account_index, account_record in owner_totals.items():
            account_cycles = sum(
                row["order_cycles"]
                for index, row in strategy_results.items()
                if index // _PER_ACCOUNT == account_index
            )
            if (
                account_record["provider_submit_calls"] != account_cycles
                or account_record["provider_cancel_calls"] != account_cycles
            ):
                raise AssertionError(
                    f"LOAD01 execution provider count differs for account {account_index}"
                )
        registry_after = _registered_session_inventory(registries)
        if any(registry_after.values()):
            raise AssertionError("LOAD01 left service registrations after normal shutdown")
        if (
            len(owner_source_records) != _STRATEGIES
            or durable_audit["durable_scopes"] != _STRATEGIES
        ):
            raise AssertionError("LOAD01 Monitor did not preserve 100 isolated strategy journals")
        normal_shutdown_complete = True

        total_source_events = sum(market_status["source_sequences"].values())
        actual_duration = report["load"]["actual_duration_seconds"]
        market_rate = total_source_events / actual_duration
        market_delivery_total = sum(row["market_events"] for row in strategy_results.values())
        delivery_rate = market_delivery_total / actual_duration
        lifecycle_rate = total_strategy_cycles / actual_duration
        e2e_values = [
            value
            for row in strategy_results.values()
            for value in row["market_e2e_samples_ms"] or ()
        ]
        order_values = [
            value for row in strategy_results.values() for value in row["order_samples_ms"] or ()
        ]
        account_values = [
            value for row in strategy_results.values() for value in row["account_samples_ms"] or ()
        ]
        reconnects = [
            record for row in strategy_results.values() for record in row["reconnect_records"]
        ]
        expected_reconnects = _ACCOUNTS if mode == "pilot" else 5 * _ACCOUNTS
        if (
            len(reconnects) != expected_reconnects
            or max((record["recovery_ms"] for record in reconnects), default=0.0) > 10_000
        ):
            raise AssertionError("LOAD01 reconnect coverage or ten-second recovery bound failed")
        pause = strategy_results[0]["slow_pause"]
        if (
            not pause
            or pause.get("pause_ended_seconds", 0) - pause.get("pause_started_seconds", 0) < 14
        ):
            raise AssertionError("LOAD01 slow market consumer did not sustain its 15-second pause")
        if any(
            row["duplicate_events"]
            or row["unrecovered_gap_count"]
            or row["runtime_cleanup_failures"]
            or row["runtime_unregister_uncertainties"]
            or row["active_bindings"]
            or row["telemetry"]["dropped_queue_total"]
            or row["telemetry"]["dropped_delivery_total"]
            for row in strategy_results.values()
        ):
            raise AssertionError("LOAD01 found an unrecovered data gap, duplicate, or client drop")
        source_sequences = market_status["source_sequences"]
        if max(source_sequences.values()) - min(source_sequences.values()) > 1:
            raise AssertionError("LOAD01 synthetic provider did not rotate evenly across 20 topics")

        report["state"] = (
            "PILOT_COMPLETE_NOT_FORMAL" if mode == "pilot" else "LOAD_PROFILE_COMPLETE"
        )
        report["completed_at_utc"] = _utc_now()
        report["load"].update(
            {
                "actual_input_messages": total_source_events,
                "actual_input_messages_per_second": market_rate,
                "input_payload_bytes": market_status["source_payload_bytes"],
                "input_payload_bytes_per_message": (
                    market_status["source_payload_bytes"] / total_source_events
                    if total_source_events
                    else None
                ),
                "input_bytes_per_second": market_status["source_payload_bytes"] / actual_duration,
                "actual_logical_strategy_deliveries": market_delivery_total,
                "logical_strategy_deliveries_per_second": delivery_rate,
                "actual_order_lifecycles": total_strategy_cycles,
                "order_lifecycles_per_second": lifecycle_rate,
                "order_lifecycles_per_account_per_second": {
                    _ACCOUNT_REFS[index]: sum(
                        row["order_cycles"]
                        for strategy_index, row in strategy_results.items()
                        if strategy_index // _PER_ACCOUNT == index
                    )
                    / actual_duration
                    for index in range(_ACCOUNTS)
                },
            }
        )
        report["latency_ms"] = {
            "market_end_to_end": _percentile_summary(e2e_values),
            "order_submit_cancel_roundtrip": _percentile_summary(order_values),
            "account_read": _percentile_summary(account_values),
            "performance_threshold_registration": "NOT_REGISTERED_IN_P0",
        }
        report["market"] = {
            "provider_start_counts": market_start_counts,
            "provider_stop_counts": market_stop_counts,
            "active_provider_keys_after_close": list(market_status["active_provider_keys"]),
            "input_messages_by_topic": source_sequences,
            "logical_strategy_deliveries_by_strategy": {
                _strategy_ref(index): row["market_events"]
                for index, row in strategy_results.items()
            },
            "intentional_reconnect_gap_events": sum(
                row["intentional_reconnect_gap_events"] for row in strategy_results.values()
            ),
            "slow_consumer": pause,
            "reconnect_count": len(reconnects),
            "reconnect_max_recovery_ms": max(
                (record["recovery_ms"] for record in reconnects), default=0.0
            ),
            "unrecovered_gap_count": sum(
                row["unrecovered_gap_count"] for row in strategy_results.values()
            ),
            "duplicate_event_count": sum(
                row["duplicate_events"] for row in strategy_results.values()
            ),
            "maximum_client_queue_events": max(
                row["market_queue_max_count"] for row in strategy_results.values()
            ),
            "maximum_client_queue_bytes": max(
                row["market_queue_max_bytes"] for row in strategy_results.values()
            ),
        }
        report["accounts_and_execution"] = {
            "owner_process_count": len(owner_totals),
            "account_session_connects": {
                key: value["account_provider"]["provider_connect_calls"]
                for key, value in owner_totals.items()
            },
            "account_session_closes": {
                key: value["account_provider"]["provider_close_calls"]
                for key, value in owner_totals.items()
            },
            "synthetic_submit_calls_by_account": {
                _ACCOUNT_REFS[key]: value["provider_submit_calls"]
                for key, value in owner_totals.items()
            },
            "synthetic_cancel_calls_by_account": {
                _ACCOUNT_REFS[key]: value["provider_cancel_calls"]
                for key, value in owner_totals.items()
            },
            "provider_timeouts": 0,
            "provider_errors": 0,
        }
        report["durable_monitor"] = {
            **durable_audit,
            "sqlite_bytes_after_close": _sqlite_tree_bytes(outbox_path),
            "max_event_capacity": _RELAY_MAX_EVENTS,
            "max_logical_event_bytes": _RELAY_MAX_BYTES,
            "monitor_telemetry_stream_count": sum(
                item["stream_count"] for item in telemetry_snapshots
            ),
            "monitor_telemetry_strategies_received": len(
                [item for item in telemetry_snapshots if item["stream_count"] == 1]
            ),
        }
        report["service_registry_sessions_at_ready_barrier"] = registry_before
        report["service_registry_sessions_after_close"] = registry_after
        report["strategy_results"] = {
            _strategy_ref(index): {
                key: value for key, value in row.items() if not key.endswith("_samples_ms")
            }
            for index, row in strategy_results.items()
        }
        report["latency_samples"] = {
            "market_end_to_end_ms": e2e_values,
            "order_submit_cancel_roundtrip_ms": order_values,
            "account_read_ms": account_values,
        }
        report["process_lifecycle"] = {
            "expected_child_count": 112,
            "actual_child_count": len(child_records),
            "normal_exit_count": sum(
                1 for record in child_records.values() if record["normal_exit"]
            ),
            "forced_termination_count": len(terminated),
            "all_child_exit_codes_zero": all(
                record.get("exit_code") == 0 for record in child_records.values()
            ),
            "distinct_child_pids": len({record["pid"] for record in child_records.values()}),
        }
        if mode == "full" and abs(market_rate - _MARKET_HZ) > _MARKET_HZ * 0.05:
            raise AssertionError("LOAD01 sustained provider input was outside the 5% target band")
        target_order_rate = _LIFECYCLES_PER_ACCOUNT_HZ * _ACCOUNTS
        if mode == "full" and abs(lifecycle_rate - target_order_rate) > target_order_rate * 0.05:
            raise AssertionError(
                "LOAD01 sustained order lifecycle rate was outside the 5% target band"
            )
        if mode == "full" and abs(delivery_rate - _MARKET_HZ * _STRATEGIES) > (
            _MARKET_HZ * _STRATEGIES * 0.05
        ):
            raise AssertionError("LOAD01 logical delivery rate was outside the 5% target band")
    except BaseException as exc:
        failure = {
            "phase": phase,
            "exception_type": type(exc).__name__,
            "safe_code": getattr(exc, "code", None),
        }
        if isinstance(exc, _Load01ChildFailureError) and exc.child_diagnostic is not None:
            failure["child_diagnostic"] = exc.child_diagnostic
        report["state"] = "FAILED"
        report["failure"] = failure
        raise
    finally:
        if child_records and any(not record["closed"] for record in child_records.values()):
            _emergency_cleanup(
                child_records,
                abort_event=abort_event,
                stop_event=stop_event,
                terminated=terminated,
                cleanup_errors=cleanup_errors,
            )
        snapshots_by_process: dict[str, list[dict[str, Any]]] = {}

        def add_rpc_snapshot(process_name: str, snapshot: Any) -> None:
            if type(snapshot) is dict:
                snapshots_by_process.setdefault(process_name, []).append(snapshot)

        for process_name, snapshots in rpc_timing_snapshots.items():
            for snapshot in snapshots.values():
                add_rpc_snapshot(process_name, snapshot)
        for process_name, record in child_records.items():
            for snapshot in record.get("rpc_timing_snapshots", {}).values():
                add_rpc_snapshot(process_name, snapshot)
        if failure is not None:
            diagnostic = failure.get("child_diagnostic")
            if type(diagnostic) is dict:
                failure_snapshot = diagnostic.get("rpc_timing_at_failure")
                if type(failure_snapshot) is dict and type(failure_snapshot.get("pid")) is int:
                    for process_name, record in child_records.items():
                        if record.get("pid") == failure_snapshot["pid"]:
                            add_rpc_snapshot(process_name, failure_snapshot)
                            break
        try:
            report["rpc_timing"] = _load01_rpc_timing_report(snapshots_by_process)
        except BaseException as exc:
            report["rpc_timing"] = {"collection_error": type(exc).__name__}
        report["cleanup"] = {"termination_requests": terminated, "errors": cleanup_errors}
        report["children"] = [
            {
                "name": record["name"],
                "kind": record["kind"],
                "pid": record["pid"],
                "exit_code": (
                    record["exit_code"] if "exit_code" in record else record["process"].exitcode
                ),
                "normal_exit": bool(record.get("normal_exit", False)),
                "closed": bool(record.get("closed", False)),
                "rpc_diagnostic_close_sent": bool(
                    record.get("rpc_diagnostic_close_sent", False)
                ),
                "rpc_timing_snapshot_stages": list(
                    record.get("rpc_timing_snapshot_stages", ())
                ),
                "rpc_snapshot_received_before_terminate": record.get(
                    "rpc_snapshot_received_before_terminate"
                ),
                "rpc_timing_collection_error": record.get("rpc_timing_collection_error"),
            }
            for record in child_records.values()
        ]
        report["normal_shutdown_complete"] = normal_shutdown_complete
        report["last_phase"] = phase
        report["completed_at_utc"] = _utc_now()
        _write_report(output_path, report)


def test_load01_startup_rpc_maintenance_checks_each_boundary_with_controlled_clock() -> None:
    class _ClockedRuntime:
        def __init__(self) -> None:
            self.now = 0.0
            self.deadline = 30.0
            self.due_at = 5.0
            self.calls: list[tuple[str, str]] = []
            self.maintenance_at: list[float] = []

        @property
        def healthy(self) -> bool:
            return self.now < self.deadline

        def call(self, mount, operation, arguments, **kwargs):
            self.calls.append((mount, operation))
            if not self.healthy:
                raise RuntimeError("REQUIRED_LEASE_EXPIRED")
            self.now += 6.0
            return {"ok": True}

        def maintain(self):
            self.maintenance_at.append(self.now)
            if not self.healthy:
                raise RuntimeError("REQUIRED_LEASE_EXPIRED")
            if self.now >= self.due_at:
                self.deadline = self.now + 30.0
                self.due_at = self.now + 5.0
            return None

        def next_maintenance_delay(self):
            return self.due_at - self.now

        def market_binding_view(self, *args, **kwargs):
            return None

        def account_binding_view(self, *args, **kwargs):
            return None

    unmanaged = _ClockedRuntime()
    unmanaged_completed = 0
    unmanaged_error = None
    for _ in range(6):
        try:
            unmanaged.call("store.market", "market.snapshot", {})
        except RuntimeError as exc:
            unmanaged_error = str(exc)
            break
        unmanaged_completed += 1

    runtime = _ClockedRuntime()
    probe = _StartupRuntimeMaintenanceProbe(runtime, clock=lambda: runtime.now)
    probe.set_phase("startup-market-subscription")

    for operation in (
        "market.subscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
        "market.snapshot",
        "market.head",
    ):
        runtime.call("store.market", operation, {})

    observed = probe.snapshot()
    assert unmanaged_completed == 5
    assert unmanaged_error == "REQUIRED_LEASE_EXPIRED"
    assert unmanaged.now == 30.0
    assert runtime.now == 36.0
    assert runtime.calls == [
        ("store.market", "market.subscribe"),
        ("store.market", "market.snapshot"),
        ("store.market", "market.head"),
        ("store.market", "market.read_after"),
        ("store.market", "market.snapshot"),
        ("store.market", "market.head"),
    ]
    assert runtime.maintenance_at == [6.0, 12.0, 18.0, 24.0, 30.0, 36.0]
    assert observed["rpc_boundary_checks"] == 12
    assert observed["maintenance_delay_checks"] == 12
    assert observed["rpc_calls_by_phase"] == {"startup-market-subscription": 6}
    assert observed["maintenance_calls_by_reason"] == {"rpc_postflight": 6}
    assert observed["rpc_max_duration_ms_by_operation"] == {
        "store.market.market.subscribe": 6000.0,
        "store.market.market.snapshot": 6000.0,
        "store.market.market.head": 6000.0,
        "store.market.market.read_after": 6000.0,
    }


def test_load01_scheduled_start_and_round_robin_market_budget() -> None:
    class _Clock:
        now = 12.25

        def monotonic(self) -> float:
            return self.now

    class _Event:
        def __init__(self, clock: _Clock) -> None:
            self.clock = clock
            self.set = False
            self.waits: list[float] = []

        def is_set(self) -> bool:
            return self.set

        def wait(self, timeout: float) -> bool:
            self.waits.append(timeout)
            self.clock.now += timeout
            return self.set

    clock = _Clock()
    stop_event = _Event(clock)
    abort_event = _Event(clock)
    maintenance_reasons: list[str] = []
    assert not _load01_has_started(99.999, 100.0)
    assert _load01_has_started(100.0, 100.0)
    assert _wait_until_load01_start(
        12.35,
        stop_event=stop_event,
        abort_event=abort_event,
        maintain_if_due=maintenance_reasons.append,
        clock=clock.monotonic,
    )
    assert clock.now == 12.35
    assert stop_event.waits == pytest.approx([0.05, 0.05])
    assert maintenance_reasons == [
        "pre-scheduled-load-start",
        "pre-scheduled-load-start",
    ]

    stream_ids = tuple(f"stream-{index:02d}" for index in range(_TOPICS))
    scheduler = _RoundRobinMarketPollScheduler(stream_ids)
    assert scheduler.next_stream_id(enabled=False) is None
    first_turn = [scheduler.next_stream_id() for _ in range(_TOPICS)]
    second_turn = [scheduler.next_stream_id() for _ in range(_TOPICS)]
    assert first_turn == list(stream_ids)
    assert second_turn == list(stream_ids)


def test_load01_market_host_and_client_profiles_request_negotiated_v2() -> None:
    account_service = {
        "service_id": "load01-test-account-service",
        "instance_id": "load01-test-account-owner",
        "scope_ref": "load01-test-account-scope-v1",
        "endpoint": "tcp://127.0.0.1:31001",
        "security_ref": "load01-test-account-security-v1",
    }
    execution_service = {
        "service_id": "load01-test-execution-service",
        "instance_id": "load01-test-execution-owner",
        "order_scope_ref": "load01-test-orders-scope-v1",
        "query_scope_ref": "load01-test-query-scope-v1",
        "endpoint": "tcp://127.0.0.1:31002",
        "security_ref": "load01-test-execution-security-v1",
    }
    market_rpc = "tcp://127.0.0.1:31003"
    market_pub = "tcp://127.0.0.1:31004"
    telemetry_rpc = "tcp://127.0.0.1:31005"

    market_config = parse_service_config(_market_service_config(market_rpc, market_pub))
    strategy_config = parse_client_config(
        _strategy_config(
            0,
            account_service,
            execution_service,
            market_rpc,
            market_pub,
            telemetry_rpc,
        )
    )

    assert (
        market_config.communication["profiles"][_MARKET_LISTENER_REF]["options"][
            "market_wire_version"
        ]
        == _MARKET_WIRE_VERSION
        == 2
    )
    assert (
        strategy_config.communication["profiles"][_MARKET_CLIENT_REF]["options"][
            "market_wire_version"
        ]
        == _MARKET_WIRE_VERSION
        == 2
    )


def test_load01_market_poll_failure_survives_parent_progress_drain() -> None:
    from types import SimpleNamespace

    class _MarketClient:
        healthy = True
        recovery_required = True
        resync_required = True
        queue_count = 3
        queue_bytes = 1024
        last_bootstrap = SimpleNamespace(
            cursor_epoch="epoch-current",
            cursor_sequence=99,
            snapshot_epoch="epoch-new",
            snapshot_sequence=104,
            snapshot_kind="latest_event",
            snapshot_complete=False,
            full_baseline_available=False,
            resync_required=True,
        )

    class _Runtime:
        bindings = ()
        state = "started"
        healthy = True
        cleanup_failures = 0
        unregister_uncertainties = 0
        unknown_registration_count = 0

    class _Pipe:
        def __init__(self, message) -> None:
            self.message = message

        def poll(self, _timeout: float) -> bool:
            return self.message is not None

        def recv(self):
            message, self.message = self.message, None
            return message

    class _Process:
        def is_alive(self) -> bool:
            return True

    error = ServiceMarketError("RESYNC_REQUIRED", cause_code="MARKET_STREAM_GAP")
    error.args = ("synthetic underlying detail is intentionally omitted",)
    diagnostic = _market_poll_failure_diagnostic(
        strategy_index=48,
        key=_market_keys()[0],
        client=_MarketClient(),
        error=error,
        started_monotonic=105.0,
        poll_duration_ms=640.0,
        load_start=100.0,
        market_seen=0,
        last_epoch="epoch-current",
        last_sequence=99,
        runtime=_Runtime(),
    )
    progress: dict[str, Any] = {}
    message = ("error", 48, 2048, "RESYNC_REQUIRED", {"market_poll_failure": diagnostic})
    records = {
        "strategy-048": {
            "name": "strategy-048",
            "pipe": _Pipe(message),
            "process": _Process(),
        }
    }
    with pytest.raises(_Load01ChildFailureError) as raised:
        _drain_progress(records, progress)

    assert raised.value.code == "RESYNC_REQUIRED"
    assert raised.value.child_diagnostic == {"market_poll_failure": diagnostic}
    projected = json.dumps(raised.value.child_diagnostic)
    assert "synthetic underlying detail" not in projected
    assert diagnostic["phase"] == "strategy-market-client-poll"
    assert diagnostic["service_market_error"] is True
    assert diagnostic["client"]["last_bootstrap"]["snapshot_sequence"] == 104
    assert diagnostic["client"]["last_bootstrap"]["full_baseline_available"] is False
    assert diagnostic["strategy_cursor"] == {
        "epoch": "epoch-current",
        "sequence": 99,
        "market_events_seen": 0,
    }
