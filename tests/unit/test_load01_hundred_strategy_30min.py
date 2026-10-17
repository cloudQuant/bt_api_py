"""Bounded LOAD01 synthetic sustained-load runner on fixed committed sources."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
from multiprocessing.connection import wait as wait_connections
import os
import random
import sqlite3
import sys
import threading
import time
import traceback
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
from tests.unit._load01_resource_diagnostics import (
    aggregate_strategy_queue_snapshots,
    aggregate_role_resources,
    append_and_validate_load01_resource_sample,
    capture_market_queue_snapshot,
    capture_process_resource_row,
)
from tests.unit._load01_profiles import (
    load01_profile,
    market_keys_for_profile,
    market_payload_data,
)
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

_FIXED_PARENT = "e08da3c58cb3b3b040f7799642de38523bbd3229"
_FIXED_PACKAGE_GITLINKS = {
    "bt_api/bt_api_base": "b4aabd1e17b9c7252fa7b4d266932087d99b5517",
    "bt_api/bt_api_execution": "f5c030644dea06ce91afbaacf94e11e353538186",
    "bt_api/bt_api_gateway": "59b164ab7f89c6428ae6546103aad6058dab4c16",
    "bt_api/bt_api_monitor": "536a4f04747182749fabdac5e28624f80465bc53",
    "bt_api/bt_api_risk": "adb6ec58308149faa93c02920b72f98ac3796056",
    "bt_api/bt_api_transport_zmq": "a397fdb460d2deb22e67ceb4963992ef63dbbb0e",
}
_DOMAIN = "paper:load01-synthetic"
_STRATEGIES = 100
_ACCOUNTS = 10
_PER_ACCOUNT = 10
_STARTUP_STRATEGY_SLOTS = 10
_TOPICS = 20
_MARKET_HZ = 200
_LIFECYCLES_PER_ACCOUNT_HZ = 2
_EVENT_TYPES = ("execution.lifecycle", "execution.cancellation")
_RELAY_POLL_INTERVAL_SECONDS = 0.5
_RELAY_EVENTS_PER_SLICE = 1
_RELAY_MAX_EVENTS = 1_000_000
_RELAY_MAX_BYTES = 512 * 1024 * 1024
_MIN_FREE_BYTES = 2 * 1024**3
_FULL_LOAD_SECONDS = 30 * 60
_MARKET_OPS = frozenset(
    {
        "market.bootstrap_many",
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


class _Load01StartupWaveAborted(RuntimeError):
    """A focused startup-wave worker was asked to stop during parent teardown."""


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


class _RoundRobinRelayPollScheduler:
    """Return one due relay scope per owner service-pump turn."""

    def __init__(
        self,
        local_indices: tuple[int, ...],
        *,
        start_at: float,
        interval_seconds: float = _RELAY_POLL_INTERVAL_SECONDS,
    ) -> None:
        if len(set(local_indices)) != len(local_indices):
            raise ValueError("relay local indices must be unique")
        if type(start_at) not in (int, float) or start_at < 0:
            raise ValueError("relay scheduler start time is invalid")
        if (
            type(interval_seconds) not in (int, float)
            or interval_seconds <= 0
        ):
            raise ValueError("relay scheduler interval is invalid")
        self._local_indices = local_indices
        self._next_due_at = {index: float(start_at) for index in local_indices}
        self._interval_seconds = float(interval_seconds)
        self._next_index = 0

    def next_index(
        self,
        now: float,
        *,
        eligible: set[int] | None = None,
        force: bool = False,
    ) -> int | None:
        if type(now) not in (int, float) or now < 0:
            raise ValueError("relay scheduler time is invalid")
        if eligible is not None and not eligible.issubset(self._next_due_at):
            raise ValueError("relay scheduler eligibility is invalid")
        if not self._local_indices:
            return None
        for offset in range(len(self._local_indices)):
            position = (self._next_index + offset) % len(self._local_indices)
            local_index = self._local_indices[position]
            if eligible is not None and local_index not in eligible:
                continue
            if not force and float(now) < self._next_due_at[local_index]:
                continue
            self._next_index = (position + 1) % len(self._local_indices)
            return local_index
        return None

    def mark_polled(self, local_index: int) -> None:
        if local_index not in self._next_due_at:
            raise ValueError("relay scheduler index is unknown")
        # Keep the original fixed-rate interval. A delayed poll remains due
        # and catches up one event at a time, with service pumps between slices.
        self._next_due_at[local_index] += self._interval_seconds


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
        self.timing_durations: dict[str, dict[str, int]] = {}
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
        self._record_duration(self.maintenance, name, start, end)

    def duration(self, name: str, start: int | None, end: int | None) -> None:
        self._record_duration(self.timing_durations, name, start, end)

    @staticmethod
    def _record_duration(
        target: dict[str, dict[str, int]],
        name: str,
        start: int | None,
        end: int | None,
    ) -> None:
        if type(start) is not int or type(end) is not int:
            return
        try:
            row = target.setdefault(name, {"count": 0, "total_ns": 0, "max_ns": 0})
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
            "maintenance, pump-gap, relay-poll timing and output serialization add further cost.",
            "maintenance_durations": {key: dict(value) for key, value in self.maintenance.items()},
            "timing_durations": {
                key: dict(value) for key, value in self.timing_durations.items()
            },
            "events": list(self.events)[-limit:],
        }


def _load01_trace_snapshot_or_none(
    trace: _Load01RpcTrace, max_events: int = 32
) -> dict[str, Any] | None:
    try:
        return trace.snapshot(max_events)
    except BaseException:
        return None


def _load01_failure_trace_snapshot_or_none(
    trace: _Load01RpcTrace,
) -> dict[str, Any] | None:
    return _load01_trace_snapshot_or_none(trace, max_events=_LOAD01_RPC_RING_SIZE)


def _load01_poll_relay_slice(
    relay: ServiceExecutionMonitorRelay,
    trace: _Load01RpcTrace,
    scope_ref: str,
) -> Any:
    started = trace.now()
    try:
        return relay.poll(limit=_RELAY_EVENTS_PER_SLICE)
    finally:
        trace.duration(f"owner.relay.poll:{scope_ref}", started, trace.now())


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
            "timing_durations",
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
        "hook_overhead_note": "Measured record construction/append excludes wrapper dispatch, "
        "clock reads, aggregate timing updates, relay polling bookkeeping, and output serialization; "
        "these add runtime cost. Owner relay poll durations include synchronous sink and checkpoint work.",
        "processes": processes, "correlated_requests": requests,
    }

def _market_keys(profile_id: str = "U0") -> tuple[MarketSubscriptionKey, ...]:
    return market_keys_for_profile(profile_id)


def _strategy_startup_waves(profile_id: str = "U0") -> tuple[tuple[int, ...], ...]:
    """Release no more than the fixed ten strategy registrations in one wave."""
    return load01_profile(profile_id).startup_waves(_STARTUP_STRATEGY_SLOTS)


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
    *,
    scope_count: int = _PER_ACCOUNT,
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
        for local_index in range(scope_count)
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
    *,
    account_index: int | None = None,
) -> dict[str, Any]:
    if account_index is None:
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


def _market_wave_strategy_config(
    index: int, market_rpc: str, market_pub: str
) -> dict[str, Any]:
    """One required market binding for the real ten-client startup wave check."""

    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": _strategy_ref(index)},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "store.market",
                            "service_id": _MARKET_ID,
                            "instance_id": _MARKET_INSTANCE,
                            "scope_ref": _MARKET_SCOPE_REF,
                            "transport_ref": _MARKET_CLIENT_REF,
                        }
                    ],
                    "options": {
                        "request_timeout_ms": _RPC_TIMEOUT_MS,
                        "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _MARKET_CLIENT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {
                        "rpc_connect": market_rpc,
                        "market_connect": market_pub,
                    },
                    "security_profile_ref": _MARKET_SECURITY_REF,
                    "options": {
                        "request_timeout_ms": _RPC_TIMEOUT_MS,
                        "high_water_mark": _MARKET_HWM,
                        "max_message_bytes": _MAX_RPC_BYTES,
                        "market_wire_version": _MARKET_WIRE_VERSION,
                    },
                }
            },
        },
    }


def _market_authority_audience(
    strategy_keys: dict[int, tuple[bytes, bytes]], profile_id: str = "U0"
):
    profile = load01_profile(profile_id)
    return tuple(
        (
            strategy_keys[index][0],
            _principal_ref(index),
            _strategy_ref(index),
            _ACCOUNT_REFS[profile.account_for_strategy(index)],
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
        self.source_payload_bytes_by_topic: Counter[str] = Counter()
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
        data, payload_bytes = market_payload_data(key, ordinal, publish_ns)
        self.source_payload_bytes += payload_bytes
        self.source_payload_bytes_by_topic[key.stream_id] += payload_bytes
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
        "tests.unit._load01_resource_diagnostics",
        "tests.unit._load01_profiles",
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
    profile_test = root / "tests" / "unit" / "test_load01_profiles.py"
    profile_test_relative = profile_test.relative_to(root).as_posix()
    if file_pins.get(profile_test_relative) != _sha256(profile_test):
        raise AssertionError("LOAD01 profile regression digest does not match its fixed-source manifest")
    checked["load_profile_tests"] = {
        "path": profile_test_relative,
        "sha256": _sha256(profile_test),
    }
    return checked


def _verify_fixed_archives(
    root: Path,
    pin_path: Path,
    *,
    allowed_overlays: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    pins = json.loads(pin_path.read_text(encoding="utf-8"))
    overlays = dict(allowed_overlays or {})
    if any(
        not isinstance(path, str) or not isinstance(digest, str)
        for path, digest in overlays.items()
    ):
        raise AssertionError("LOAD01 archive overlays must be exact path-to-digest pins")
    archives = [("parent.zip", root, pins["parent_archive_sha256"], pins["parent_archive_bytes"])]
    for package in pins["package_gitlinks"]:
        archive_name = Path(package["path"]).name + ".zip"
        package_root = root / package["path"]
        archives.append(
            (archive_name, package_root, package["archive_sha256"], package["archive_bytes"])
        )
    checked_py_files = 0
    checked_overlays: set[str] = set()
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
                archived_sha256 = hashlib.sha256(archived_bytes).hexdigest()
                extracted_sha256 = _sha256(extracted)
                if extracted_sha256 != archived_sha256:
                    overlay_sha256 = overlays.get(member.filename)
                    if overlay_sha256 is None or extracted_sha256 != overlay_sha256:
                        raise AssertionError(
                            f"LOAD01 extracted committed source changed: {member.filename}"
                        )
                    checked_overlays.add(member.filename)
                elif member.filename in overlays:
                    raise AssertionError(
                        f"LOAD01 expected overlay did not differ from archive: {member.filename}"
                    )
                checked_py_files += 1
    if checked_overlays != set(overlays):
        missing = sorted(set(overlays) - checked_overlays)
        raise AssertionError(f"LOAD01 source overlay was not an archive member: {missing}")
    return {
        "archive_count": len(archives),
        "python_file_count": checked_py_files,
        "overlay_paths": sorted(checked_overlays),
    }


def _write_source_run_manifest(
    root: Path,
    work_dir: Path,
    *,
    allowed_overlays: Mapping[str, str] | None = None,
) -> Path:
    source_pin_path = Path(os.environ["BT_API_LOAD01_EXPORT_PINS"]).resolve()
    _verify_fixed_archives(root, source_pin_path, allowed_overlays=allowed_overlays)
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
        Path("tests/unit/_load01_resource_diagnostics.py"),
        Path("tests/unit/_load01_profiles.py"),
        Path("tests/unit/test_load01_profiles.py"),
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
    profile_id="U0",
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
        topic_schedule = load01_profile(profile_id).topic_publish_schedule()
        schedule_index = 0
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
                    key = market_keys[topic_schedule[schedule_index]]
                    provider.publish_one(key, now_ns)
                    schedule_index = (schedule_index + 1) % len(topic_schedule)
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
        "source_payload_bytes_by_topic": dict(provider.source_payload_bytes_by_topic),
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


def _load01_slow_sink_monitor_process(control, *args) -> None:
    """Run the real Monitor worker with one bounded terminal append delay."""

    durable_config_raw, telemetry_config_raw, *tail = args[:9]
    delay_seconds, delay_count, delay_samples = args[9:]
    original_append_bounded = DurableOutbox.append_bounded

    def delayed_append_bounded(self, event, *, max_events, max_bytes):
        started_ns = time.perf_counter_ns()
        time.sleep(delay_seconds)
        injected_delay_ns = time.perf_counter_ns() - started_ns
        try:
            return original_append_bounded(
                self, event, max_events=max_events, max_bytes=max_bytes
            )
        finally:
            with delay_count.get_lock():
                index = delay_count.value
                if index < len(delay_samples):
                    delay_samples[index] = injected_delay_ns
                delay_count.value = index + 1

    DurableOutbox.append_bounded = delayed_append_bounded
    try:
        _monitor_process(
            control,
            durable_config_raw,
            telemetry_config_raw,
            *tail,
        )
    finally:
        DurableOutbox.append_bounded = original_append_bounded


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
    owner_phase = "owner-setup"
    close_phase: str | None = None
    close_stages_completed: list[str] = []
    normal_drain_completed = False
    source_evidence: list[dict[str, Any]] | None = None
    total_relayed = 0
    close_callback_errors: list[dict[str, Any]] = []

    def close_facts() -> dict[str, Any]:
        def host_facts(host) -> dict[str, Any] | None:
            if host is None:
                return None
            try:
                summary = host.summary
                upstream = summary["upstream"]
                last_upstream_cleanup = summary["last_upstream_cleanup"]
                return {
                    "state": host.state,
                    "cleanup_failures": list(summary["cleanup_failures"]),
                    "cleanup_history": list(summary["cleanup_history"]),
                    "upstream": None if upstream is None else dict(upstream),
                    "last_upstream_cleanup": (
                        None
                        if last_upstream_cleanup is None
                        else dict(last_upstream_cleanup)
                    ),
                }
            except BaseException:
                return {"snapshot_available": False}

        if relay_runtime is None:
            relay_facts = None
        else:
            try:
                bindings = relay_runtime.bindings
                relay_facts = {
                    "state": relay_runtime.state,
                    "cleanup_failures": relay_runtime.cleanup_failures,
                    "unregister_uncertainties": relay_runtime.unregister_uncertainties,
                    "unknown_registration_count": relay_runtime.unknown_registration_count,
                    "binding_count": len(bindings),
                    "active_binding_count": sum(bool(binding.active) for binding in bindings),
                }
            except BaseException:
                relay_facts = {"snapshot_available": False}

        try:
            worker_facts = None if gateway_worker is None else {
                "state": gateway_worker.state,
                "ready": gateway_worker.ready,
                "provider_connect_calls": account_provider.connect_calls,
                "provider_read_calls": account_provider.read_calls,
                "provider_close_calls": account_provider.close_calls,
            }
        except BaseException:
            worker_facts = {"snapshot_available": False}

        if source_evidence is None:
            drain_facts = None
        else:
            drain_facts = [
                {
                    "scope_ref": item["scope_ref"],
                    "source_rows": item["source_rows"],
                    "source_rows_by_stream": dict(item["source_rows_by_stream"]),
                    "relay_delivered": item["relay_delivered"],
                }
                for item in source_evidence
            ]
        return {
            "owner_phase": owner_phase,
            "close_phase": close_phase,
            "completed_close_stages": list(close_stages_completed),
            "normal_drain_completed": normal_drain_completed,
            "relay_count": len(relays),
            "reader_count": len(readers),
            "total_relayed": total_relayed,
            "relay_delivered_by_scope": dict(relay_delivered_by_scope),
            "source_drain_by_scope": drain_facts,
            "relay_runtime": relay_facts,
            "execution_host": host_facts(execution_host),
            "account_host": host_facts(account_host),
            "account_worker": worker_facts,
            "component_close_callback_errors": list(close_callback_errors),
        }

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
                    try:
                        gateway_worker.close()
                    except BaseException as exc:
                        close_callback_errors.append(
                            {
                                "phase": "gateway-worker-close",
                                "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                                "code": _safe_diagnostic_code(exc),
                                "traceback": "".join(traceback.format_exception(exc)),
                            }
                        )
                        raise

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
        owner_phase = "owner-started"

        owner_strategy_indices = tuple(
            int(strategy["index"]) for strategy in strategy_audience
        )
        if (
            not owner_strategy_indices
            or len(set(owner_strategy_indices)) != len(owner_strategy_indices)
            or any(not 0 <= index < _STRATEGIES for index in owner_strategy_indices)
        ):
            raise AssertionError("LOAD01 owner audience has an invalid strategy assignment")
        scope_records = []
        for local_index, strategy_index in enumerate(owner_strategy_indices):
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
            account_index,
            monitor_endpoint,
            unused_relay_endpoint,
            scope_count=len(scope_records),
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
        if len(relay_runtime.bindings) != len(scope_records) or not all(
            binding.active for binding in relay_runtime.bindings
        ):
            raise AssertionError("LOAD01 owner failed to register all Monitor relay scopes")
        control.send(("relay-ready", account_index, os.getpid(), len(relay_runtime.bindings)))
        owner_phase = "owner-active"

        last_report = time.monotonic()
        relay_poll_scheduler = _RoundRobinRelayPollScheduler(
            tuple(relays), start_at=last_report
        )
        next_relay_maintenance = time.monotonic()
        draining = False
        drain_deadline = 0.0
        drain_round_remaining: set[int] = set()
        drain_round_delivered = 0
        last_execution_pump_end_ns: int | None = None
        while True:
            account_pump_started_ns = rpc_trace.now()
            if type(last_execution_pump_end_ns) is int:
                rpc_trace.duration(
                    "owner.host_pump_gap:execution_to_account",
                    last_execution_pump_end_ns,
                    account_pump_started_ns,
                )
            account_host.serve_once(timeout_ms=0)
            account_pump_ended_ns = rpc_trace.now()
            execution_pump_started_ns = rpc_trace.now()
            rpc_trace.duration(
                "owner.host_pump_gap:account_to_execution",
                account_pump_ended_ns,
                execution_pump_started_ns,
            )
            execution_host.serve_once(timeout_ms=0)
            last_execution_pump_end_ns = rpc_trace.now()
            now = time.monotonic()
            if now >= next_relay_maintenance:
                relay_runtime.maintain()
                next_relay_maintenance = time.monotonic() + 0.5
            if draining and time.monotonic() > drain_deadline:
                raise TimeoutError("LOAD01 durable Monitor relay did not drain")
            local_index = relay_poll_scheduler.next_index(
                now,
                eligible=drain_round_remaining if draining else None,
                force=draining,
            )
            if local_index is not None:
                result = _load01_poll_relay_slice(
                    relays[local_index],
                    rpc_trace,
                    scope_records[local_index]["scope_ref"],
                )
                relay_poll_scheduler.mark_polled(local_index)
                total_relayed += result.remote_delivered
                relay_delivered_by_scope[scope_records[local_index]["scope_ref"]] += (
                    result.remote_delivered
                )
                if draining:
                    drain_round_remaining.remove(local_index)
                    drain_round_delivered += result.remote_delivered
                    if not drain_round_remaining:
                        if drain_round_delivered == 0:
                            normal_drain_completed = True
                            owner_phase = "normal-drain-complete"
                            break
                        drain_round_remaining = set(relays)
                        drain_round_delivered = 0
                    if time.monotonic() > drain_deadline:
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
                            _load01_failure_trace_snapshot_or_none(rpc_trace),
                            "emergency",
                        )
                    draining = True
                    owner_phase = "normal-drain"
                    drain_deadline = time.monotonic() + 120
                    drain_round_remaining = set(relays)
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

        owner_phase = "normal-close"
        close_phase = "source-snapshot"
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
        close_stages_completed.append(close_phase)
        close_phase = "relay-close"
        for relay in relays.values():
            relay.close()
        close_stages_completed.append(close_phase)
        relays.clear()
        close_phase = "reader-close"
        for reader in readers.values():
            reader.close()
        close_stages_completed.append(close_phase)
        readers.clear()
        close_phase = "relay-runtime-close"
        relay_runtime.close()
        close_stages_completed.append(close_phase)
        relay_runtime_state = relay_runtime.state
        relay_cleanup_failures = relay_runtime.cleanup_failures
        relay_unregister_uncertainties = relay_runtime.unregister_uncertainties
        close_phase = "execution-host-close"
        execution_host.close()
        close_stages_completed.append(close_phase)
        execution_state = execution_host.state
        execution_cleanup_failures = execution_host.summary["cleanup_failures"]
        close_phase = "account-host-close"
        account_host.close()
        close_stages_completed.append(close_phase)
        account_state = account_host.state
        account_cleanup_failures = account_host.summary["cleanup_failures"]
        close_phase = "store-close"
        store.close()
        store = None
        close_stages_completed.append(close_phase)
        close_phase = "closed-ack-send"
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
                close_facts(),
            )
        )
    except BaseException as exc:
        rpc_timing_at_failure = _load01_failure_trace_snapshot_or_none(rpc_trace)
        exception_chain: list[dict[str, Any]] = []
        current_error: BaseException | None = exc
        seen_errors: set[int] = set()
        while isinstance(current_error, BaseException) and id(current_error) not in seen_errors:
            seen_errors.add(id(current_error))
            exception_chain.append(
                {
                    "type": f"{type(current_error).__module__}.{type(current_error).__qualname__}",
                    "code": _safe_diagnostic_code(current_error),
                    "message": str(current_error),
                }
            )
            current_error = current_error.__cause__ or current_error.__context__
        failure_facts = {
            "exception_chain": exception_chain,
            "traceback": "".join(traceback.format_exception(exc)),
            "close_facts_before_exception_cleanup": close_facts(),
        }
        failure_path = Path(execution_store_path).with_name(
            "single-owner-close-failure.json"
        )
        failure_record = {
            "evidence_level": "SOURCE_FAKE_SINGLE_OWNER_REAL_HOST_RELAY_OUTBOX_ONLY",
            "failure": failure_facts,
            "rpc_timing_at_failure": rpc_timing_at_failure,
        }
        try:
            failure_path.write_text(
                json.dumps(failure_record, sort_keys=True, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        except OSError as artifact_error:
            failure_facts["diagnostic_artifact_write_code"] = _safe_diagnostic_code(
                artifact_error
            )
            failure_record["failure"] = failure_facts
        _send_load01_rpc_diagnostic(
            control,
            f"owner-{account_index:02d}",
            os.getpid(),
            rpc_timing_at_failure,
            "failure",
        )
        cleanup_started_ns = rpc_trace.now()
        exception_cleanup_errors: list[dict[str, Any]] = []

        def cleanup_after_failure(phase: str, operation) -> None:
            try:
                operation()
            except BaseException as cleanup_error:
                exception_cleanup_errors.append(
                    {
                        "phase": phase,
                        "type": f"{type(cleanup_error).__module__}.{type(cleanup_error).__qualname__}",
                        "code": _safe_diagnostic_code(cleanup_error),
                        "traceback": "".join(traceback.format_exception(cleanup_error)),
                    }
                )

        for local_index, relay in relays.items():
            cleanup_after_failure(f"relay-close:{local_index:02d}", relay.close)
        for local_index, reader in readers.items():
            cleanup_after_failure(f"reader-close:{local_index:02d}", reader.close)
        if relay_runtime is not None and relay_runtime.state != "closed":
            cleanup_after_failure("relay-runtime-close", relay_runtime.close)
        for phase, host in (
            ("execution-host-close", execution_host),
            ("account-host-close", account_host),
        ):
            if host is not None and host.state not in {"closed", "failed"}:
                cleanup_after_failure(phase, host.close)
        if store is not None:
            cleanup_after_failure("store-close", store.close)
        cleanup_ended_ns = rpc_trace.now()
        owner_diagnostic = {
            **failure_record,
            "cleanup_duration_ns": (
                max(0, cleanup_ended_ns - cleanup_started_ns)
                if type(cleanup_started_ns) is int and type(cleanup_ended_ns) is int
                else None
            ),
            "close_facts_after_exception_cleanup": close_facts(),
            "exception_cleanup_errors": exception_cleanup_errors,
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
    profile_id="U0",
):
    _check_fixed_source_manifest(os.environ["BT_API_LOAD01_SOURCE_MANIFEST"])
    rpc_trace = _install_load01_rpc_trace("strategy")
    profile = load01_profile(profile_id)
    account_index = profile.account_for_strategy(index)
    local_index = profile.strategy_indices_by_account[account_index].index(index)
    subscribed_market_keys = tuple(
        market_keys[topic_index] for topic_index in profile.subscribed_topic_indices(index)
    )
    subscribed_stream_ids = tuple(key.stream_id for key in subscribed_market_keys)
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
        subscribed_stream_ids
    )
    market_poll_failure: dict[str, Any] | None = None
    market_client_start_batches: list[dict[str, Any]] = []
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
        clients = []
        for key in subscribed_market_keys:
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
            clients.append(client)
        started_ns = time.perf_counter_ns()
        try:
            bootstraps = ServiceMarketClient.start_many(clients)
        finally:
            market_client_start_batches.append(
                {
                    "stream_count": len(clients),
                    "duration_ms": (time.perf_counter_ns() - started_ns) / 1_000_000,
                    "measurement_scope": "same-registration-market-batch",
                }
            )
        runtime_probe._maintain_if_due("market-client-start-boundary")
        if not runtime.healthy:
            raise _Load01StartupLeaseError()
        for key, client, bootstrap in zip(
            subscribed_market_keys, clients, bootstraps, strict=True
        ):
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
            for key in subscribed_market_keys:
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
        key = next(key for key in subscribed_market_keys if key.stream_id == stream_id)
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
        if initial_degraded or initial_bindings != 5 or len(markets) != len(subscribed_market_keys):
            raise AssertionError("LOAD01 strategy did not activate its routes and profile topics")
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
                    "market_client_start_batches": market_client_start_batches,
                    "account_refresh_duration_ms": account_refresh_duration_ms,
                    "maintenance": startup_maintenance_snapshot,
                },
            )
        )

        # A completed startup wave keeps renewing its required leases while
        # later waves register; load_event remains the one common start barrier.
        while not load_event.is_set():
            runtime_probe._maintain_if_due("common-load-barrier")
            if control.poll(0):
                command = control.recv()
                if (
                    isinstance(command, tuple)
                    and len(command) == 2
                    and command[0] == "startup-barrier-snapshot"
                ):
                    control.send(
                        (
                            "startup-barrier-snapshot",
                            index,
                            os.getpid(),
                            command[1],
                            _runtime_lease_snapshot(runtime),
                            runtime_probe.snapshot(),
                        )
                    )
                else:
                    raise AssertionError(
                        "LOAD01 strategy received an unexpected common-barrier command"
                    )
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
        order_interval = profile.order_interval_seconds_by_strategy[index]
        phase_offset = profile.order_phase_seconds_by_strategy[index]
        next_order = load_start + phase_offset
        next_telemetry = load_start + 30
        next_metrics = load_start + 60
        next_report = load_start + 5
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
                        target_local = (window - 1) % len(
                            profile.strategy_indices_by_account[account_index]
                        )
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
                    next_order += order_interval
                    after_order = time.monotonic()
                    if next_order < after_order - order_interval / 2:
                        skipped = int((after_order - next_order) // order_interval) + 1
                        next_order += skipped * order_interval

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
                    profile_id=profile_id,
                    telemetry=telemetry,
                    telemetry_queue_drops=telemetry_queue_drops,
                    telemetry_delivery_drops=telemetry_delivery_drops,
                    telemetry_delivered=telemetry_delivered,
                    max_queue_count=max_queue_count,
                    max_queue_bytes=max_queue_bytes,
                    market_queue_snapshot=capture_market_queue_snapshot(
                        markets.values(),
                        load_start_monotonic_seconds=load_start,
                        monotonic_clock=time.monotonic,
                    ),
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
                            _load01_failure_trace_snapshot_or_none(rpc_trace),
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

        final_market_queue_snapshot = capture_market_queue_snapshot(
            markets.values(),
            load_start_monotonic_seconds=load_start,
            monotonic_clock=time.monotonic,
        )
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
            market_queue_snapshot=final_market_queue_snapshot,
            max_market_poll_ms=max_market_poll_ms,
            account_identity=account_identity,
            runtime=runtime,
            profile_id=profile_id,
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
        rpc_timing_at_failure = _load01_failure_trace_snapshot_or_none(rpc_trace)
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
            "market_client_start_batches": market_client_start_batches,
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


def _load01_market_wave_strategy_process(
    control,
    index,
    raw_config,
    market_keys,
    security_material,
    start_event,
    abort_event,
):
    """Exercise one real strategy runtime's 20 market subscriptions and close path."""

    _check_fixed_source_manifest(os.environ["BT_API_LOAD01_SOURCE_MANIFEST"])
    account_index = index // _PER_ACCOUNT
    strategy_ref = _strategy_ref(index)
    principal_ref = _principal_ref(index)
    runtime = None
    runtime_probe = None
    markets: dict[str, ServiceMarketClient] = {}
    pending_markets: dict[str, ServiceMarketClient] = {}
    market_client_start_batches: list[dict[str, Any]] = []
    try:
        config = parse_client_config(raw_config)
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref)
        service_identity = WireIdentity(WireRole.SERVICE, _MARKET_PRINCIPAL, _MARKET_ID)
        security = ClientCurveKeys(*security_material)
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda _scope_ref: WireScope(
                _DOMAIN,
                account_ref=_ACCOUNT_REFS[account_index],
                strategy_ref=strategy_ref,
            ),
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory({_MARKET_SECURITY_REF: security}),
            requested_operations={"store.market": _MARKET_OPS},
        )
        runtime_probe = _StartupRuntimeMaintenanceProbe(runtime)
        control.send(("loaded", index, os.getpid()))
        while not start_event.wait(0.05):
            if abort_event.is_set():
                raise _Load01StartupWaveAborted()
        if abort_event.is_set():
            raise _Load01StartupWaveAborted()
        startup = runtime.start()
        if startup.degraded_count or len(runtime.bindings) != 1 or not runtime.healthy:
            raise _Load01StartupLeaseError()

        subscriber_factory = ZmqMarketTransportFactory({_MARKET_SECURITY_REF: security})
        clients: list[ServiceMarketClient] = []
        for key in market_keys:
            if abort_event.is_set():
                raise _Load01StartupWaveAborted()
            client = ServiceMarketClient(
                runtime,
                "store.market",
                key,
                subscriber_factory=subscriber_factory,
                stream_scope=WireScope(_DOMAIN),
                queue_capacity=256,
                queue_max_bytes=512 * 1024,
                head_check_interval_seconds=1.0,
            )
            pending_markets[key.stream_id] = client
            clients.append(client)
        if abort_event.is_set():
            raise _Load01StartupWaveAborted()
        started_ns = time.perf_counter_ns()
        try:
            bootstraps = ServiceMarketClient.start_many(clients)
        finally:
            market_client_start_batches.append(
                {
                    "stream_count": len(clients),
                    "duration_ms": (time.perf_counter_ns() - started_ns) / 1_000_000,
                    "measurement_scope": "same-registration-market-batch",
                }
            )
        runtime_probe._maintain_if_due("market-wave-start-boundary")
        if abort_event.is_set():
            raise _Load01StartupWaveAborted()
        if not runtime.healthy:
            raise _Load01StartupLeaseError()
        for key, client, _bootstrap in zip(market_keys, clients, bootstraps, strict=True):
            markets[key.stream_id] = client
            pending_markets.pop(key.stream_id, None)

        lease_snapshot = _runtime_lease_snapshot(runtime)
        if (
            len(markets) != _TOPICS
            or not lease_snapshot["healthy"]
            or lease_snapshot["required_min_lease_remaining_ms"]
            <= _RPC_LEASE_HEADROOM_SECONDS * 1000
        ):
            raise _Load01StartupLeaseError()
        control.send(
            (
                "ready",
                index,
                os.getpid(),
                strategy_ref,
                _ACCOUNT_REFS[account_index],
                len(runtime.bindings),
                tuple(sorted(markets)),
                lease_snapshot,
                runtime_probe.snapshot(),
                tuple(market_client_start_batches),
            )
        )

        # As in the production strategy child, a ready wave renews its runtime
        # while later waves register and subscribe before the common load barrier.
        while True:
            runtime_probe._maintain_if_due("market-wave-ready-barrier")
            if abort_event.is_set():
                break
            if not control.poll(0.05):
                continue
            command = control.recv()
            if command == ("close",):
                break
            raise AssertionError("LOAD01 market-wave worker received unknown control")

        closed_streams = []
        for stream_id, client in markets.items():
            client.close()
            closed_streams.append(stream_id)
        for stream_id, client in pending_markets.items():
            client.close()
            closed_streams.append(stream_id)
        pending_markets.clear()
        barrier_maintenance = runtime_probe.snapshot()
        runtime.close()
        close_facts = {
            "expected_rpc_timeout_ms": _RPC_TIMEOUT_MS,
            "lease_seconds": _LEASE_SECONDS,
            "market_clients_started": len(markets),
            "market_clients_closed": len(closed_streams),
            "closed_streams": tuple(sorted(closed_streams)),
            "ready_barrier_maintenance": barrier_maintenance[
                "maintenance_calls_by_reason"
            ].get("market-wave-ready-barrier", 0),
            "maintenance": barrier_maintenance,
            "market_client_start_batches": tuple(market_client_start_batches),
            "runtime_state": runtime.state,
            "runtime_cleanup_failures": runtime.cleanup_failures,
            "runtime_unregister_uncertainties": runtime.unregister_uncertainties,
            "runtime_unknown_registration_count": runtime.unknown_registration_count,
        }
        if (
            len(closed_streams) != _TOPICS
            or runtime.state != "closed"
            or runtime.cleanup_failures
            or runtime.unregister_uncertainties
            or runtime.unknown_registration_count
        ):
            raise AssertionError("LOAD01 market-wave runtime did not close cleanly")
        control.send(("closed", index, os.getpid(), close_facts))
    except BaseException as exc:
        cleanup_errors = []
        clients = {
            id(client): client for client in (*markets.values(), *pending_markets.values())
        }
        for client in clients.values():
            try:
                client.close()
            except BaseException as cleanup_exc:
                cleanup_errors.append(_safe_diagnostic_code(cleanup_exc))
        if runtime is not None and runtime.state != "closed":
            try:
                runtime.close()
            except BaseException as cleanup_exc:
                cleanup_errors.append(_safe_diagnostic_code(cleanup_exc))
        runtime_clean = runtime is None or (
            runtime.state == "closed"
            and not runtime.cleanup_failures
            and not runtime.unregister_uncertainties
            and not runtime.unknown_registration_count
        )
        if isinstance(exc, _Load01StartupWaveAborted) and not cleanup_errors and runtime_clean:
            with suppress(Exception):
                control.send(("aborted", index, os.getpid(), runtime.state if runtime else None))
            return
        with suppress(Exception):
            control.send(
                (
                    "error",
                    index,
                    os.getpid(),
                    _safe_diagnostic_code(exc),
                    {
                        "phase": "market-wave-start-or-close",
                        "cleanup_error_codes": tuple(cleanup_errors),
                        "runtime_state": runtime.state if runtime is not None else None,
                        "runtime_cleanup_failures": (
                            runtime.cleanup_failures if runtime is not None else None
                        ),
                        "runtime_unregister_uncertainties": (
                            runtime.unregister_uncertainties if runtime is not None else None
                        ),
                        "maintenance": (
                            runtime_probe.snapshot() if runtime_probe is not None else None
                        ),
                    },
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
    market_queue_snapshot,
    max_market_poll_ms,
    account_identity,
    runtime,
    profile_id="U0",
    include_samples=False,
):
    telem = telemetry.stats
    profile = load01_profile(profile_id)
    return {
        "index": index,
        "account_index": profile.account_for_strategy(index),
        "local_index": profile.strategy_indices_by_account[
            profile.account_for_strategy(index)
        ].index(index),
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
        "market_queue_snapshot": market_queue_snapshot,
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
    market_start_batch_ms = []
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
        market_start_batch_ms.extend(
            item["duration_ms"] for item in diagnostic["market_client_start_batches"]
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
                "market_client_start_batches": diagnostic["market_client_start_batches"],
                "account_refresh_duration_ms": diagnostic["account_refresh_duration_ms"],
                "maintenance": maintenance,
            }
        )
    return {
        "lease_seconds": _LEASE_SECONDS,
        "rpc_timeout_seconds": _RPC_TIMEOUT_MS / 1000,
        "runtime_start_duration_ms": _percentile_summary(runtime_start_ms),
        "market_client_start_batch_duration_ms": _percentile_summary(market_start_batch_ms),
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
            if deferred:
                message = deferred.pop(0)
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
            ready, message, terminal = _recv_pipe_event_if_ready(record["pipe"])
            if terminal is not None:
                record["control_pipe_terminal_before_expected"] = terminal
                raise AssertionError(
                    f"{label} child control pipe ended before {expected_tag}: "
                    f"{record['name']}:{record['process'].exitcode}"
                )
            if ready:
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
                while True:
                    ready, message, terminal = _recv_pipe_event_if_ready(record["pipe"])
                    if terminal is not None:
                        record["control_pipe_terminal_before_expected"] = terminal
                        break
                    if not ready:
                        break
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
            if "control_pipe_terminal_after_expected" in record:
                continue
            ready, message, terminal = _recv_pipe_event_if_ready(record["pipe"])
            if terminal is not None:
                record["control_pipe_terminal_after_expected"] = terminal
                if expected_tag != "closed":
                    raise AssertionError(
                        f"{label} child control pipe ended after {expected_tag} "
                        f"before close acknowledgement: {record['name']}"
                    )
                made_progress = True
                continue
            if not ready:
                continue
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


def _recv_pipe_event_if_ready(pipe) -> tuple[bool, Any, dict[str, Any] | None]:
    """Read one control message without treating a closed Windows pipe as data."""
    try:
        if pipe not in wait_connections([pipe], timeout=0):
            return False, None, None
    except OSError as exc:
        if getattr(exc, "winerror", None) != 109 and exc.errno != 109:
            raise
        return True, None, {
            "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
            "errno": exc.errno,
            "winerror": getattr(exc, "winerror", None),
        }
    try:
        return True, pipe.recv(), None
    except EOFError as exc:
        return True, None, {"type": f"{type(exc).__module__}.{type(exc).__qualname__}"}
    except OSError as exc:
        if getattr(exc, "winerror", None) != 109 and exc.errno != 109:
            raise
        return True, None, {
            "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
            "errno": exc.errno,
            "winerror": getattr(exc, "winerror", None),
        }


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
        while True:
            ready, message, terminal = _recv_pipe_event_if_ready(record["pipe"])
            if terminal is not None:
                record["control_pipe_terminal_during_load"] = terminal
                break
            if not ready:
                break
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
        if record.get("control_pipe_terminal_during_load") is not None:
            raise AssertionError(
                f"LOAD01 child control pipe ended before shutdown: {record['name']}"
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
    resource_rows = []
    cpu_missing_pids = []
    cpu_delta_seconds = 0.0
    for record in records.values():
        pid = record["pid"]
        process_gone_errors = (psutil.NoSuchProcess, psutil.ZombieProcess)
        row, proc = capture_process_resource_row(
            str(record.get("kind", "unknown")),
            pid,
            psutil.Process,
            process_gone_errors=process_gone_errors,
            access_denied_errors=(psutil.AccessDenied,),
        )
        resource_rows.append(row)
        if row["rss_bytes"] is not None:
            rss_values[record["name"]] = row["rss_bytes"]
        if proc is None:
            cpu_missing_pids.append(pid)
            continue
        try:
            cpu = proc.cpu_times()
        except process_gone_errors:
            cpu_missing_pids.append(pid)
            continue
        except psutil.AccessDenied:
            cpu_missing_pids.append(pid)
            continue
        total_cpu = float(cpu.user + cpu.system)
        current_cpu[pid] = total_cpu
        cpu_delta_seconds += max(0.0, total_cpu - prior_cpu.get(pid, total_cpu))
    interval = max(0.001, sample_at - previous_sample_at)
    return (
        {
            "elapsed_seconds": sample_at - started_at,
            "sample_interval_seconds": interval,
            "available_memory_bytes": psutil.virtual_memory().available,
            "child_rss_bytes": (
                sum(rss_values.values()) if len(rss_values) == len(records) else None
            ),
            "child_rss_max_bytes": (
                max(rss_values.values()) if len(rss_values) == len(records) else None
            ),
            "child_process_count": len(rss_values),
            "child_pid_count": len(records),
            "child_pid_coverage_count": len(
                {
                    row["pid"]
                    for row in resource_rows
                    if type(row.get("pid")) is int and row["pid"] > 0
                }
            ),
            "child_process_coverage_count": sum(
                row["process_state"] == "available" for row in resource_rows
            ),
            "child_rss_coverage_count": len(rss_values),
            "child_cpu_time_coverage_count": len(current_cpu),
            "child_resource_missing_pids": sorted(
                set(
                    [
                        int(row["pid"])
                        for row in resource_rows
                        if row["process_state"] != "available" or row["rss_bytes"] is None
                    ]
                    + cpu_missing_pids
                )
            ),
            "role_resources": aggregate_role_resources(resource_rows),
            "child_cpu_percent_of_machine": (
                100.0 * cpu_delta_seconds / interval / logical_cpu_count
                if len(current_cpu) == len(records)
                else None
            ),
            "system_cpu_percent": psutil.cpu_percent(interval=None),
        },
        current_cpu,
    )


def _progress_summary(
    progress: dict[str, Any], *, expected_clients_per_strategy: int = _TOPICS
) -> dict[str, Any]:
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
        "market_queue_snapshot": aggregate_strategy_queue_snapshots(
            strategy_rows,
            expected_strategy_count=_STRATEGIES,
            expected_clients_per_strategy=expected_clients_per_strategy,
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
                ready, message, terminal = _recv_pipe_event_if_ready(pipe)
                if terminal is not None:
                    record["rpc_timing_collection_error"] = terminal["type"]
                    record["rpc_timing_pipe_terminal"] = terminal
                    return
                if not ready:
                    return
                retain_message(record, message)
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
    profile_id = os.environ.get("BT_API_LOAD01_PROFILE", "U0")
    profile = load01_profile(profile_id)
    startup_waves = profile.startup_waves(_STARTUP_STRATEGY_SLOTS)
    startup_wave_by_strategy = {
        strategy_index: wave_index
        for wave_index, wave in enumerate(startup_waves)
        for strategy_index in wave
    }
    topic_rate_targets = {
        f"K{index:02d}": rate
        for index, rate in enumerate(profile.topic_messages_per_second)
    }
    topic_subscriber_targets = {
        f"K{index:02d}": count
        for index, count in enumerate(profile.topic_subscriber_counts)
    }
    account_strategy_targets = {
        _ACCOUNT_REFS[index]: len(strategy_indices)
        for index, strategy_indices in enumerate(profile.strategy_indices_by_account)
    }
    account_order_rate_targets = {
        _ACCOUNT_REFS[index]: rate
        for index, rate in enumerate(profile.order_targets_per_account_per_second)
    }
    expected_clients_per_strategy = len(profile.topic_indices_by_strategy[0])
    duration_seconds = 120 if mode == "pilot" else _FULL_LOAD_SECONDS
    source_root = _fixed_source_root()
    export_pin_path = Path(os.environ["BT_API_LOAD01_EXPORT_PINS"]).resolve()
    runner_path = Path(__file__).resolve()
    runner_relative = runner_path.relative_to(source_root).as_posix()
    runner_overlay_sha256 = os.environ["BT_API_LOAD01_RUNNER_OVERLAY_SHA256"]
    if _sha256(runner_path) != runner_overlay_sha256:
        raise AssertionError("LOAD01 runner overlay differs from the external spec pin")
    allowed_overlays = {runner_relative: runner_overlay_sha256}
    archive_validation = _verify_fixed_archives(
        source_root,
        export_pin_path,
        allowed_overlays=allowed_overlays,
    )
    source_manifest = _write_source_run_manifest(
        source_root,
        tmp_path,
        allowed_overlays=allowed_overlays,
    )
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
        "load_profile": profile_id,
        "profile_registration_status": profile.registration_status,
        "profile_registration_status_source": (
            "docs/迭代计划/迭代10-可选组件与可配置通信/正式迭代计划.md:807-813 (U0 initial LOAD01 baseline)"
            if profile_id == "U0"
            else "docs/evidence/iteration10-load01-n1n2-source-20261018/candidate-registration.md:17-26 (candidate only)"
        ),
        "state": "PREFLIGHT",
        "formal_acceptance_eligible": profile.formal_acceptance_eligible(mode),
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
            "strategies_per_account": (
                _PER_ACCOUNT
                if len(set(account_strategy_targets.values())) == 1
                else None
            ),
            "strategies_per_account_by_account": account_strategy_targets,
            "public_market_topics": _TOPICS,
            "market_registry_sessions_target": _STRATEGIES,
            "market_subscription_client_count_target": sum(
                len(topics) for topics in profile.topic_indices_by_strategy
            ),
            "strategy_topic_binding_count": sum(
                len(topics) for topics in profile.topic_indices_by_strategy
            ),
            "market_topics_per_strategy": expected_clients_per_strategy,
            "topic_input_target_per_second": topic_rate_targets,
            "topic_subscriber_count": topic_subscriber_targets,
            "upstream_input_target_per_second": profile.total_input_target_per_second,
            "strategy_market_deliveries_target_per_second": profile.total_delivery_target_per_second,
            "order_lifecycles_target_per_account_per_second": account_order_rate_targets,
            "order_lifecycles_target_total_per_second": sum(
                profile.order_targets_per_account_per_second
            ),
            "startup_wave_count": len(startup_waves),
            "topic_payload_target_bytes": 256 if profile_id == "U0" else None,
            "topic_payload_reference_target": {"profile_id": "U0", "bytes_per_message": 256},
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

        market_keys = _market_keys(profile_id)
        strategy_market_streams = {
            index: tuple(
                market_keys[topic_index].stream_id
                for topic_index in profile.subscribed_topic_indices(index)
            )
            for index in range(_STRATEGIES)
        }
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
        market_audience = _market_authority_audience(strategy_keys, profile_id)
        strategy_audience = tuple(
            (
                strategy_keys[index][0],
                _principal_ref(index),
                _strategy_ref(index),
                _ACCOUNT_REFS[profile.account_for_strategy(index)],
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
                for strategy_index in profile.strategy_indices_by_account[account_index]
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
                for local_index in range(
                    len(profile.strategy_indices_by_account[account_index])
                )
            ),
        )
        registration_events = [context.Event() for _ in startup_waves]
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
                profile_id,
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
        report["progress"] = _progress_summary(
            progress, expected_clients_per_strategy=expected_clients_per_strategy
        )

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
                or message[3] != account_strategy_targets[_ACCOUNT_REFS[account_index]]
            ):
                raise AssertionError(
                    f"LOAD01 owner did not register its profile durable scopes: {account_index}"
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
            account_index = profile.account_for_strategy(index)
            account_service = account_services[account_index]
            execution_service = execution_services[account_index]
            raw_strategy_config = _strategy_config(
                index,
                account_service,
                execution_service,
                market_rpc,
                market_pub,
                monitor_message[4],
                account_index=account_index,
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
                    registration_events[startup_wave_by_strategy[index]],
                    load_event,
                    load_start_value,
                    stop_event,
                    abort_event,
                    mode,
                    profile_id,
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
        startup_deadline = time.monotonic() + 300.0
        strategy_ready: dict[int, tuple[Any, ...]] = {}
        startup_wave_receipts = []
        for wave_number, wave_indices in enumerate(startup_waves, start=1):
            phase = f"load-strategy-startup-wave-{wave_number:02d}"
            wave_started = time.monotonic()
            remaining = startup_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("LOAD01 300-second total registration barrier expired")
            registration_events[wave_number - 1].set()
            remaining = startup_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("LOAD01 300-second total registration barrier expired")
            wave_records = {index: strategy_records[index] for index in wave_indices}
            wave_ready = _collect_expected(
                wave_records,
                "ready",
                remaining,
                f"strategy startup wave {wave_number:02d}",
                progress,
            )
            for index, record in wave_records.items():
                message = wave_ready[index]
                if (
                    message[1] != index
                    or message[2] != record["pid"]
                    or message[3] != _strategy_ref(index)
                    or message[4] != _ACCOUNT_REFS[profile.account_for_strategy(index)]
                    or message[7] != 5
                    or message[8] != 0
                    or message[9]
                    != _ACCOUNT_KEYS[profile.account_for_strategy(index)].to_dict()
                    or len(message[10]) != expected_clients_per_strategy
                    or dict(message[10])
                    != dict.fromkeys(strategy_market_streams[index], 0)
                ):
                    raise AssertionError(
                        f"LOAD01 strategy did not pass its registration barrier: {index}"
                    )
                record["ready"] = True
                record["account_index"] = profile.account_for_strategy(index)
                record["startup_ms"] = message[6]
                record["startup_diagnostic"] = message[11]
            wave_receipt = {
                "wave": wave_number,
                "slot_limit": _STARTUP_STRATEGY_SLOTS,
                "strategy_indices": list(wave_indices),
                "ready_count": len(wave_ready),
            }
            if strategy_ready:
                remaining = startup_deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "LOAD01 300-second total registration barrier expired"
                    )
                prior_records = {
                    index: strategy_records[index] for index in sorted(strategy_ready)
                }
                for index, record in prior_records.items():
                    record["pipe"].send(("startup-barrier-snapshot", wave_number))
                remaining = startup_deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "LOAD01 300-second total registration barrier expired"
                    )
                prior_snapshots = _collect_expected(
                    prior_records,
                    "startup-barrier-snapshot",
                    remaining,
                    f"earlier startup-wave maintenance after wave {wave_number:02d}",
                    progress,
                )
                prior_barrier_maintenance_calls = 0
                prior_barrier_iterations = 0
                prior_min_lease_ms = None
                for index, snapshot_message in prior_snapshots.items():
                    if (
                        snapshot_message[1] != index
                        or snapshot_message[2] != prior_records[index]["pid"]
                        or snapshot_message[3] != wave_number
                    ):
                        raise AssertionError(
                            "LOAD01 startup-barrier snapshot identity did not match its child"
                        )
                    lease_snapshot, maintenance_snapshot = snapshot_message[4:6]
                    ready_maintenance = prior_records[index]["startup_diagnostic"][
                        "maintenance"
                    ]
                    if (
                        not lease_snapshot["healthy"]
                        or lease_snapshot["required_min_lease_remaining_ms"]
                        <= _RPC_LEASE_HEADROOM_SECONDS * 1000
                        or maintenance_snapshot["maintenance_delay_checks"]
                        <= ready_maintenance["maintenance_delay_checks"]
                    ):
                        raise _Load01StartupLeaseError()
                    prior_barrier_iterations += 1
                    prior_barrier_maintenance_calls += maintenance_snapshot[
                        "maintenance_calls_by_reason"
                    ].get("common-load-barrier", 0) - ready_maintenance[
                        "maintenance_calls_by_reason"
                    ].get("common-load-barrier", 0)
                    lease_ms = lease_snapshot["required_min_lease_remaining_ms"]
                    prior_min_lease_ms = (
                        lease_ms
                        if prior_min_lease_ms is None
                        else min(prior_min_lease_ms, lease_ms)
                    )
                wave_receipt.update(
                    {
                        "earlier_ready_strategy_count": len(prior_snapshots),
                        "earlier_barrier_iterations_observed": prior_barrier_iterations,
                        "earlier_barrier_maintenance_calls": prior_barrier_maintenance_calls,
                        "earlier_min_lease_remaining_ms": prior_min_lease_ms,
                    }
                )
            strategy_ready.update(wave_ready)
            wave_receipt["elapsed_seconds"] = round(time.monotonic() - wave_started, 6)
            startup_wave_receipts.append(wave_receipt)
            if not all(record["process"].is_alive() for record in strategy_records.values()):
                raise AssertionError(
                    f"LOAD01 a strategy exited while startup wave {wave_number:02d} ran"
                )
            barrier_memory = psutil.virtual_memory().available
            if barrier_memory < _MIN_FREE_BYTES:
                raise RuntimeError(
                    f"LOAD01 startup wave {wave_number:02d} crossed the 2 GiB reserve: "
                    f"available={barrier_memory}"
                )
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
        expected_registry_sessions = profile.expected_registry_sessions
        if registry_before != expected_registry_sessions:
            raise AssertionError(
                "LOAD01 service registrations do not match the 100-strategy topology"
            )
        report["barrier"] = {
            "loaded_strategies": len(loaded),
            "ready_strategies": len(strategy_ready),
            "startup_slot_limit": _STARTUP_STRATEGY_SLOTS,
            "startup_wave_count": len(startup_wave_receipts),
            "startup_waves": startup_wave_receipts,
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
            "profile_id": profile_id,
            "market_input_target_per_second": profile.total_input_target_per_second,
            "topic_input_target_per_second": topic_rate_targets,
            "strategy_market_deliveries_target_per_second": profile.total_delivery_target_per_second,
            "market_payload_bytes_target": 256 if profile_id == "U0" else None,
            "market_payload_bytes_reference_target": {"profile_id": "U0", "bytes_per_message": 256},
            "order_lifecycle_target_per_account_per_second": account_order_rate_targets,
            "order_lifecycle_target_total_per_second": sum(
                profile.order_targets_per_account_per_second
            ),
            "load_start_at_utc": _utc_now(),
        }
        _write_report(output_path, report)

        phase = "sustained-load"
        low_memory_samples = 0
        high_cpu_samples = 0
        last_resource_at = load_start
        next_resource_at = load_start
        next_report_at = load_start + 5
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
                append_and_validate_load01_resource_sample(report, sample, child_records)
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
            report["progress"] = _progress_summary(
                progress, expected_clients_per_strategy=expected_clients_per_strategy
            )
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
        report["progress"] = _progress_summary(
            progress, expected_clients_per_strategy=expected_clients_per_strategy
        )

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
                if profile.account_for_strategy(index) == account_index
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
        if set(source_sequences) != {key.stream_id for key in market_keys}:
            raise AssertionError("LOAD01 synthetic provider did not publish every profile topic")
        if mode == "full":
            for topic_index, key in enumerate(market_keys):
                expected_rate = profile.topic_messages_per_second[topic_index]
                actual_topic_rate = source_sequences[key.stream_id] / actual_duration
                if abs(actual_topic_rate - expected_rate) > expected_rate * 0.05:
                    raise AssertionError(
                        f"LOAD01 topic K{topic_index:02d} was outside its 5% input target band"
                    )

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
                        strategy_results[strategy_index]["order_cycles"]
                        for strategy_index in profile.strategy_indices_by_account[index]
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
            "input_target_per_second_by_topic": topic_rate_targets,
            "subscriber_count_by_topic": topic_subscriber_targets,
            "topic_stream_id_by_topic": {
                f"K{index:02d}": key.stream_id for index, key in enumerate(market_keys)
            },
            "input_payload_bytes_by_topic": {
                f"K{index:02d}": market_status["source_payload_bytes_by_topic"].get(
                    key.stream_id, 0
                )
                for index, key in enumerate(market_keys)
            },
            "input_payload_bytes_per_message_by_topic": {
                f"K{index:02d}": (
                    market_status["source_payload_bytes_by_topic"].get(key.stream_id, 0)
                    / source_sequences[key.stream_id]
                    if source_sequences[key.stream_id]
                    else None
                )
                for index, key in enumerate(market_keys)
            },
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
        report["market_queue_snapshot_final"] = aggregate_strategy_queue_snapshots(
            strategy_results.values(),
            expected_strategy_count=_STRATEGIES,
            expected_clients_per_strategy=expected_clients_per_strategy,
        )
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
        if mode == "full" and abs(market_rate - profile.total_input_target_per_second) > (
            profile.total_input_target_per_second * 0.05
        ):
            raise AssertionError("LOAD01 sustained provider input was outside the 5% target band")
        target_order_rate = sum(profile.order_targets_per_account_per_second)
        if mode == "full" and abs(lifecycle_rate - target_order_rate) > target_order_rate * 0.05:
            raise AssertionError(
                "LOAD01 sustained order lifecycle rate was outside the 5% target band"
            )
        if mode == "full" and abs(delivery_rate - profile.total_delivery_target_per_second) > (
            profile.total_delivery_target_per_second * 0.05
        ):
            raise AssertionError("LOAD01 logical delivery rate was outside the 5% target band")
        if mode == "full":
            account_rates = report["load"]["order_lifecycles_per_account_per_second"]
            for account_index, expected_rate in enumerate(
                profile.order_targets_per_account_per_second
            ):
                actual_rate = account_rates[_ACCOUNT_REFS[account_index]]
                if abs(actual_rate - expected_rate) > expected_rate * 0.05:
                    raise AssertionError(
                        f"LOAD01 account {account_index:02d} was outside its 5% order target band"
                    )
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


def test_load01_relay_scheduler_is_fair_and_bounds_each_poll_slice(monkeypatch) -> None:
    from types import SimpleNamespace

    relay_indices = tuple(range(_PER_ACCOUNT))
    scheduler = _RoundRobinRelayPollScheduler(relay_indices, start_at=0.0)
    observed: list[int] = []
    for _ in relay_indices:
        local_index = scheduler.next_index(0.0)
        assert local_index is not None
        observed.append(local_index)
        scheduler.mark_polled(local_index)
    assert observed == list(relay_indices)
    assert scheduler.next_index(0.499) is None
    assert scheduler.next_index(0.5) == 0

    scheduler.mark_polled(0)
    drain_eligible = {2, 7}
    assert scheduler.next_index(0.5, eligible=drain_eligible, force=True) == 2
    scheduler.mark_polled(2)
    assert scheduler.next_index(0.5, eligible=drain_eligible, force=True) == 7
    assert _RELAY_POLL_INTERVAL_SECONDS == 0.5
    assert _RELAY_EVENTS_PER_SLICE == 1

    class _Relay:
        def __init__(self) -> None:
            self.limits: list[int] = []

        def poll(self, *, limit: int) -> Any:
            self.limits.append(limit)
            return SimpleNamespace(remote_delivered=1)

    relay = _Relay()
    trace = _Load01RpcTrace("owner-test")
    stamps = iter((100, 180))
    monkeypatch.setattr(trace, "now", lambda: next(stamps))
    result = _load01_poll_relay_slice(relay, trace, "scope-test")
    assert result.remote_delivered == 1
    assert relay.limits == [1]
    assert trace.timing_durations == {
        "owner.relay.poll:scope-test": {"count": 1, "total_ns": 80, "max_ns": 80}
    }


def test_load01_failure_rpc_snapshot_retains_full_ring_and_timing_totals() -> None:
    trace = _Load01RpcTrace("owner-test")
    for sequence in range(1, _LOAD01_RPC_RING_SIZE + 46):
        trace.add(
            "client_send",
            ("orders.submit", f"message-{sequence}", f"intent-{sequence}"),
            at=sequence,
        )
    trace.duration("owner.host_pump_gap:account_to_execution", 10, 40)
    trace.duration("owner.host_pump_gap:account_to_execution", 30, 45)

    snapshot = _load01_failure_trace_snapshot_or_none(trace)
    assert snapshot is not None
    assert snapshot["ring_capacity"] == _LOAD01_RPC_RING_SIZE == 256
    assert snapshot["event_count_total"] == 301
    assert snapshot["event_count_overwritten"] == 45
    assert len(snapshot["events"]) == _LOAD01_RPC_RING_SIZE
    assert snapshot["events"][0]["sequence"] == 46
    assert snapshot["events"][-1]["sequence"] == 301
    assert snapshot["timing_durations"] == {
        "owner.host_pump_gap:account_to_execution": {
            "count": 2,
            "total_ns": 45,
            "max_ns": 30,
        }
    }
    report = _load01_rpc_timing_report({"owner-00": snapshot})
    assert report["processes"][0]["timing_durations"] == snapshot["timing_durations"]


def test_load01_single_owner_ten_scope_slow_sink_interleaves_and_drains(
    tmp_path: Path, monkeypatch
) -> None:
    """Drive two paced cycles through the production owner loop and real relay/outbox."""

    if os.name != "nt":
        pytest.skip("LOAD01 owner pump slice uses the Windows spawn baseline")

    source_root = Path(__file__).resolve().parents[2]
    source_roots = (
        source_root / "bt_api_py",
        source_root / "bt_api" / "bt_api_base" / "src",
        source_root / "bt_api" / "bt_api_execution" / "src",
        source_root / "bt_api" / "bt_api_gateway" / "src",
        source_root / "bt_api" / "bt_api_monitor" / "src",
        source_root / "bt_api" / "bt_api_risk" / "src",
        source_root / "bt_api" / "bt_api_transport_zmq" / "src",
    )
    manifest_files = {
        path.relative_to(source_root).as_posix(): _sha256(path)
        for base in source_roots
        for path in base.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    integration_runner = (
        source_root / "tests" / "unit" / "test_service_gateway_hundred_strategy_integration.py"
    )
    manifest_files[integration_runner.relative_to(source_root).as_posix()] = _sha256(
        integration_runner
    )
    load_runner = Path(__file__).resolve()
    manifest_files[load_runner.relative_to(source_root).as_posix()] = _sha256(load_runner)
    manifest_path = tmp_path / "single-owner-fixed-source-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "parent_commit": _FIXED_PARENT,
                "files": manifest_files,
            },
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BT_API_LOAD01_SOURCE_ROOT", str(source_root))
    monkeypatch.setenv("BT_API_LOAD01_SOURCE_MANIFEST", str(manifest_path))

    assert _PER_ACCOUNT == 10
    assert _LIFECYCLES_PER_ACCOUNT_HZ == 2
    assert _RPC_TIMEOUT_MS == 5_000
    cycle_period = _PER_ACCOUNT / _LIFECYCLES_PER_ACCOUNT_HZ
    cycles_per_strategy = 2
    rate_window_seconds = cycle_period * cycles_per_strategy
    slow_sink_delay_seconds = 0.028
    order_rpc_calls_per_cycle = 2
    expected_source_event_types_by_stream = {
        "execution": (
            "intent_recorded",
            "intent_admitted",
            "dispatch_claimed",
            "provider_observation",
        ),
        "cancellation": (
            "cancel_intent_recorded",
            "cancel_intent_admitted",
            "cancel_dispatch_claimed",
            "cancel_provider_observation",
        ),
    }
    expected_source_rows_by_stream_per_scope = {
        stream: cycles_per_strategy * len(event_types)
        for stream, event_types in expected_source_event_types_by_stream.items()
    }
    source_events_per_cycle = sum(
        len(event_types) for event_types in expected_source_event_types_by_stream.values()
    )
    source_events_per_scope = sum(expected_source_rows_by_stream_per_scope.values())
    expected_event_rows = source_events_per_scope * _PER_ACCOUNT
    expected_rpc_count = order_rpc_calls_per_cycle * cycles_per_strategy * _PER_ACCOUNT
    expected_source_event_type_counts = {
        (stream, event_type): cycles_per_strategy * _PER_ACCOUNT
        for stream, event_types in expected_source_event_types_by_stream.items()
        for event_type in event_types
    }
    expected_monitor_event_type_by_stream = {
        "execution": "execution.lifecycle",
        "cancellation": "execution.cancellation",
    }
    assert cycle_period == 5.0
    assert 0.020 <= slow_sink_delay_seconds <= 0.036
    assert order_rpc_calls_per_cycle == 2
    assert source_events_per_cycle == 8
    assert source_events_per_scope == 16
    assert expected_source_rows_by_stream_per_scope == {"execution": 8, "cancellation": 8}
    assert expected_event_rows == 160
    assert expected_rpc_count == 40
    scheduled_cycles_per_scope_per_second = cycles_per_strategy / rate_window_seconds
    scheduled_rpc_requests_per_scope_per_second = (
        order_rpc_calls_per_cycle * cycles_per_strategy / rate_window_seconds
    )
    scheduled_rpc_requests_per_owner_per_second = (
        scheduled_rpc_requests_per_scope_per_second * _PER_ACCOUNT
    )
    scheduled_source_events_per_scope_per_second = (
        source_events_per_cycle * cycles_per_strategy / rate_window_seconds
    )
    scheduled_source_events_per_owner_per_second = (
        scheduled_source_events_per_scope_per_second * _PER_ACCOUNT
    )
    configured_relay_capacity_per_scope_per_second = 1 / _RELAY_POLL_INTERVAL_SECONDS
    configured_relay_capacity_per_owner_per_second = (
        configured_relay_capacity_per_scope_per_second * _PER_ACCOUNT
    )
    configured_relay_capacity_utilization = (
        scheduled_source_events_per_scope_per_second
        / configured_relay_capacity_per_scope_per_second
    )
    configured_relay_headroom_fraction_of_demand = (
        configured_relay_capacity_per_scope_per_second
        - scheduled_source_events_per_scope_per_second
    ) / scheduled_source_events_per_scope_per_second
    assert scheduled_cycles_per_scope_per_second == pytest.approx(0.2)
    assert scheduled_rpc_requests_per_scope_per_second == pytest.approx(0.4)
    assert scheduled_rpc_requests_per_owner_per_second == pytest.approx(4.0)
    assert scheduled_source_events_per_scope_per_second == pytest.approx(1.6)
    assert scheduled_source_events_per_owner_per_second == pytest.approx(16.0)
    assert configured_relay_capacity_per_scope_per_second == pytest.approx(2.0)
    assert configured_relay_capacity_per_owner_per_second == pytest.approx(20.0)
    assert configured_relay_capacity_utilization == pytest.approx(0.8)
    assert configured_relay_headroom_fraction_of_demand == pytest.approx(0.25)

    context = multiprocessing.get_context("spawn")
    account_endpoint, execution_endpoint, durable_endpoint, telemetry_endpoint, unused_endpoint = (
        _free_endpoints(5)
    )
    account_server_keys = zmq.curve_keypair()
    execution_server_keys = zmq.curve_keypair()
    durable_server_keys = zmq.curve_keypair()
    telemetry_server_keys = zmq.curve_keypair()
    relay_public, relay_secret = zmq.curve_keypair()
    strategy_keys = {index: zmq.curve_keypair() for index in range(_PER_ACCOUNT)}

    account_service = _account_service_info(0, account_endpoint, account_server_keys[0])
    execution_service = _execution_service_info(0, execution_endpoint, execution_server_keys[0])
    strategy_audience = tuple(
        (
            strategy_keys[index][0],
            _principal_ref(index),
            _strategy_ref(index),
            _ACCOUNT_REFS[0],
        )
        for index in range(_PER_ACCOUNT)
    )
    owner_strategy_audience = tuple(
        {
            "index": index,
            "public_key": strategy_keys[index][0],
            "principal_ref": _principal_ref(index),
            "strategy_ref": _strategy_ref(index),
        }
        for index in range(_PER_ACCOUNT)
    )

    work_root = tmp_path / "single-owner-slow-sink"
    registry_root = work_root / "registries"
    journal_root = work_root / "journals"
    guard_root = work_root / "session-guards"
    checkpoint_root = work_root / "relay-checkpoints"
    for directory in (registry_root, journal_root, guard_root, checkpoint_root):
        directory.mkdir(parents=True, exist_ok=True)
    execution_store_path = journal_root / "execution.sqlite3"
    outbox_path = work_root / "monitor-outbox.sqlite3"

    owner_record: dict[str, Any] | None = None
    monitor_record: dict[str, Any] | None = None
    client_runtimes: list[ServiceClientRuntime] = []

    def reap_owned_process(record: dict[str, Any] | None, *, close_command: bool) -> None:
        if record is None or record.get("closed"):
            return
        process = record["process"]
        pipe = record["pipe"]
        record["cleanup_pid"] = record.get("pid", process.pid)
        record["cleanup_close_requested"] = bool(record.get("close_sent"))
        record["cleanup_forced_terminate"] = False
        record["cleanup_process_handle_closed"] = False
        record["cleanup_close_reply_observed"] = False
        record["cleanup_close_reply_clean"] = False
        liveness_observed = True
        try:
            alive = process.is_alive()
        except (AssertionError, OSError, ValueError):
            alive = False
            liveness_observed = False

        def observe_cleanup_message(message: Any) -> None:
            if not isinstance(message, tuple) or not message:
                return
            tag = message[0]
            if tag == "rpc-diagnostic":
                _consume_rpc_diagnostic(record, message)
                return
            if tag == "progress":
                _save_progress(message, progress)
                return
            if tag != "closed":
                return
            if record["kind"] == "owner" and len(message) >= 10:
                pid_matches = message[2] == record["pid"]
                clean = (
                    pid_matches
                    and message[3] == "closed"
                    and type(message[4]) is tuple
                    and message[4] == ()
                    and message[5] == "closed"
                    and type(message[6]) is tuple
                    and message[6] == ()
                    and message[7] == "closed"
                    and type(message[8]) is int
                    and message[8] == 0
                    and type(message[9]) is int
                    and message[9] == 0
                )
                record["cleanup_close_reply_observed"] = bool(pid_matches)
                record["cleanup_close_reply_clean"] = bool(clean)
                if len(message) > 17 and type(message[17]) is dict:
                    record["cleanup_close_facts"] = message[17]
            elif record["kind"] == "monitor" and len(message) >= 6:
                pid_matches = message[1] == record["pid"]
                clean = (
                    pid_matches
                    and message[2] == "closed"
                    and type(message[3]) is tuple
                    and message[3] == ()
                    and message[4] == "closed"
                    and type(message[5]) is tuple
                    and message[5] == ()
                )
                record["cleanup_close_reply_observed"] = bool(pid_matches)
                record["cleanup_close_reply_clean"] = bool(clean)

        if alive and close_command:
            if not record.get("close_sent"):
                try:
                    pipe.send(("close",))
                    record["close_sent"] = True
                    record["cleanup_close_requested"] = True
                except (EOFError, OSError, ValueError):
                    pass
            close_deadline = time.monotonic() + 12
            while alive and time.monotonic() < close_deadline:
                try:
                    ready, message, terminal = _recv_pipe_event_if_ready(pipe)
                    if terminal is not None:
                        record["cleanup_control_pipe_terminal"] = terminal
                    elif ready:
                        observe_cleanup_message(message)
                except (OSError, ValueError) as exc:
                    record["cleanup_control_pipe_read_error"] = {
                        "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                        "errno": getattr(exc, "errno", None),
                        "winerror": getattr(exc, "winerror", None),
                    }
                process.join(0.01)
                alive = process.is_alive()
        for _ in range(64):
            try:
                ready, message, terminal = _recv_pipe_event_if_ready(pipe)
                if terminal is not None:
                    record["cleanup_control_pipe_terminal"] = terminal
                    break
                if not ready:
                    break
            except (OSError, ValueError) as exc:
                record["cleanup_control_pipe_read_error"] = {
                    "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "errno": getattr(exc, "errno", None),
                    "winerror": getattr(exc, "winerror", None),
                }
                break
            observe_cleanup_message(message)
        if alive:
            # Only this test's own child is eligible for last-resort cleanup.
            record["cleanup_forced_terminate"] = True
            process.terminate()
            process.join(5)
        with suppress(EOFError, OSError, ValueError):
            pipe.close()
        try:
            exit_code = process.exitcode
        except (AssertionError, OSError, ValueError):
            exit_code = None
            liveness_observed = False
        record["cleanup_exit_code"] = exit_code
        record["cleanup_liveness_observed"] = liveness_observed
        record["normal_close_ack"] = bool(
            record.get("normal_close_ack") or record.get("cleanup_close_reply_clean")
        )
        record["normal_exit"] = bool(
            record.get("normal_exit")
            or (
                record["normal_close_ack"]
                and exit_code == 0
                and not record["cleanup_forced_terminate"]
            )
        )
        record["cleanup_normal_close_ack"] = bool(record.get("normal_close_ack"))
        record["cleanup_normal_exit"] = bool(
            record.get("normal_exit")
            and record.get("normal_close_ack")
            and exit_code == 0
            and not record["cleanup_forced_terminate"]
        )
        if record["cleanup_normal_exit"]:
            record["cleanup_mode"] = "normal_close_acknowledged_exit"
        elif record["cleanup_forced_terminate"]:
            record["cleanup_mode"] = "forced_terminate"
        elif record["cleanup_close_requested"] and exit_code is not None:
            record["cleanup_mode"] = "close_requested_exit_without_normal_ack"
        elif exit_code is not None:
            record["cleanup_mode"] = "exit_observed_without_normal_ack"
        else:
            record["cleanup_mode"] = "exit_unobserved"
        if exit_code is not None:
            with suppress(AssertionError, OSError, ValueError):
                process.close()
                record["cleanup_process_handle_closed"] = True

    try:
        owner_record = _start_process(
            context,
            _owner_process,
            (
                0,
                _account_service_config(account_service, account_endpoint),
                _execution_service_config(execution_service),
                str(registry_root / "account.sqlite3"),
                str(registry_root / "execution.sqlite3"),
                str(journal_root / "gateway.sqlite3"),
                str(guard_root),
                str(execution_store_path),
                str(checkpoint_root),
                account_server_keys,
                execution_server_keys,
                owner_strategy_audience,
                unused_endpoint,
            ),
            "load01-single-owner",
        )
        owner_record.update(
            {"pid": owner_record["process"].pid, "kind": "owner", "ready": False}
        )
        progress: dict[str, Any] = {"strategy": {}, "owner": {}}
        owner_ready = _collect_expected(
            {0: owner_record}, "ready", 60.0, "single owner", progress
        )[0]
        assert owner_ready[2] == 0
        assert owner_ready[3] == owner_record["pid"]
        assert owner_ready[6]["worker_ready"] is True
        owner_record["ready"] = True
        scope_records = owner_ready[7]
        assert len(scope_records) == _PER_ACCOUNT

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
        relay_principal = f"{_RELAY_PRINCIPAL_PREFIX}00"
        relay_actor = f"{_RELAY_CLIENT_PREFIX}00"
        monitor_owner_audience = (
            {
                "public_key": relay_public,
                "principal_ref": relay_principal,
                "actor_ref": relay_actor,
                "scopes": scope_records,
            },
        )
        delay_count = context.Value("i", 0)
        delay_samples = context.Array("Q", expected_event_rows, lock=False)
        monitor_record = _start_process(
            context,
            _load01_slow_sink_monitor_process,
            (
                durable_config,
                telemetry_config,
                str(registry_root / "monitor-durable.sqlite3"),
                str(registry_root / "monitor-telemetry.sqlite3"),
                str(outbox_path),
                durable_server_keys,
                telemetry_server_keys,
                monitor_owner_audience,
                strategy_audience,
                slow_sink_delay_seconds,
                delay_count,
                delay_samples,
            ),
            "load01-slow-sink-monitor",
        )
        monitor_record.update(
            {"pid": monitor_record["process"].pid, "kind": "monitor", "ready": False}
        )
        monitor_ready = _collect_expected(
            {0: monitor_record}, "ready", 60.0, "slow sink Monitor", progress
        )[0]
        assert monitor_ready[2] == monitor_record["pid"]
        assert monitor_ready[5:7] == ("started", "started")
        monitor_record["ready"] = True

        relay_identity = (relay_principal, relay_actor, relay_public, relay_secret)
        owner_record["pipe"].send(
            ("connect-monitor", monitor_ready[3], durable_server_keys[0], relay_identity)
        )
        relay_ready = _collect_expected(
            {0: owner_record}, "relay-ready", 60.0, "single-owner relay", progress
        )[0]
        assert relay_ready[1:4] == (0, owner_record["pid"], _PER_ACCOUNT)
        owner_record["relay_ready"] = True

        for index in range(_PER_ACCOUNT):
            strategy_ref = _strategy_ref(index)
            wire_scope = WireScope(
                _DOMAIN, account_ref=_ACCOUNT_REFS[0], strategy_ref=strategy_ref
            )
            order_scope_ref = execution_service["order_scope_ref"]
            transport_ref = f"load01-slow-execution-client-{index:02d}-v1"
            raw_config = {
                "client_config_schema_version": 1,
                "process_role": "strategy_client",
                "registration": {"strategy_ref": strategy_ref},
                "clients": {
                    "execution": {
                        "config_schema_version": 1,
                        "settings": {
                            "bindings": [
                                {
                                    "mount": "broker.orders",
                                    "service_id": execution_service["service_id"],
                                    "instance_id": execution_service["instance_id"],
                                    "scope_ref": order_scope_ref,
                                    "account_ref": _ACCOUNT_REFS[0],
                                    "transport_ref": transport_ref,
                                }
                            ],
                            "options": {
                                "request_timeout_ms": _RPC_TIMEOUT_MS,
                                "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
                            },
                        },
                    }
                },
                "communication": {
                    "config_schema_version": 1,
                    "profiles": {
                        transport_ref: {
                            "backend": "zmq",
                            "config_schema_version": 1,
                            "endpoints": {"rpc_connect": execution_endpoint},
                            "security_profile_ref": execution_service["security_ref"],
                            "options": {"request_timeout_ms": _RPC_TIMEOUT_MS},
                        }
                    },
                },
            }
            client = ServiceClientRuntime(
                parse_client_config(raw_config),
                identity=WireIdentity(
                    WireRole.STRATEGY_CLIENT, _principal_ref(index), strategy_ref
                ),
                scope_resolver=lambda ref, scopes={order_scope_ref: wire_scope}: scopes[ref],
                peer_resolver=lambda service_id, peers={
                    execution_service["service_id"]: execution_service["peer"]
                }: peers[service_id],
                transport_factory=ZmqTransportFactory(
                    {
                        execution_service["security_ref"]: ClientCurveKeys(
                            execution_service["server_public"],
                            strategy_keys[index][0],
                            strategy_keys[index][1],
                        )
                    }
                ),
                requested_operations={"broker.orders": _ORDER_OPS},
            )
            client.start()
            client_runtimes.append(client)

        period_start = time.monotonic() + 0.25
        cycle_start_times: dict[int, list[float]] = {
            index: [] for index in range(_PER_ACCOUNT)
        }
        expected_rpc_requests: set[tuple[str, str]] = set()
        for cycle in range(cycles_per_strategy):
            for index, runtime in enumerate(client_runtimes):
                scheduled = (
                    period_start
                    + cycle * cycle_period
                    + index * (cycle_period / _PER_ACCOUNT)
                )
                while True:
                    wait_seconds = scheduled - time.monotonic()
                    if wait_seconds <= 0:
                        break
                    time.sleep(wait_seconds)
                cycle_started = time.monotonic()
                assert 0 <= cycle_started - scheduled < 0.250
                cycle_start_times[index].append(cycle_started)
                intent_id = f"load01-slow-owner-{cycle:02d}-{index:02d}"
                expected_rpc_requests.add(("orders.submit", intent_id))
                submitted = runtime.call(
                    "broker.orders",
                    "orders.submit",
                    _sample_intent(intent_id),
                    scope_ref=execution_service["order_scope_ref"],
                )
                assert submitted.get("code") == "OK"
                provider_order_id = submitted["result"]["record"].get(
                    "provider_order_id"
                )
                assert provider_order_id == f"paper-{intent_id}"
                cancel_id = f"cancel-{intent_id}"
                expected_rpc_requests.add(("orders.cancel", cancel_id))
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
                    scope_ref=execution_service["order_scope_ref"],
                )
                assert cancelled.get("code") == "OK"
                assert cancelled["result"]["record"]["cancel_id"] == cancel_id

        assert all(len(starts) == cycles_per_strategy for starts in cycle_start_times.values())
        observed_cycle_intervals = {
            index: starts[1] - starts[0]
            for index, starts in cycle_start_times.items()
        }
        assert all(
            interval == pytest.approx(cycle_period, abs=0.250)
            for interval in observed_cycle_intervals.values()
        )

        def read_source_rows() -> list[tuple[str, str, str, str, str, int, str, str]]:
            rows: list[tuple[str, str, str, str, str, int, str, str]] = []
            for index in range(_PER_ACCOUNT):
                scope = ExecutionScope(
                    "synthetic",
                    "paper",
                    _ACCOUNT_REFS[0],
                    _strategy_ref(index),
                    _EXECUTION_DAY,
                )
                reader = ExecutionJournalReader(execution_store_path, scope=scope)
                try:
                    outbox_scope = _relay_outbox_scope(reader.generation, scope.key)
                    for stream, event_type in (
                        ("execution", "execution.lifecycle"),
                        ("cancellation", "execution.cancellation"),
                    ):
                        cursor = 0
                        while True:
                            page = reader.read_page(stream, after_sequence=cursor, limit=32)
                            if not page.events:
                                break
                            for event in page.events:
                                rows.append(
                                    (
                                        outbox_scope,
                                        event.event_id,
                                        event_type,
                                        stream,
                                        event.event_type,
                                        event.sequence,
                                        reader.generation,
                                        scope.key,
                                    )
                                )
                                cursor = event.sequence
                            if len(page.events) < 32:
                                break
                finally:
                    reader.close()
            return rows

        source_before_drain = read_source_rows()
        assert len(source_before_drain) == expected_event_rows
        assert len(
            {
                (scope, stream, event_id)
                for scope, event_id, _, stream, _, _, _, _ in source_before_drain
            }
        ) == expected_event_rows
        source_event_type_counts = Counter(
            (stream, source_event_type)
            for _, _, _, stream, source_event_type, _, _, _ in source_before_drain
        )
        assert dict(source_event_type_counts) == expected_source_event_type_counts
        source_rows_by_scope: dict[str, Counter[str]] = {}
        for scope, _, monitor_event_type, stream, _, _, _, _ in source_before_drain:
            assert monitor_event_type == expected_monitor_event_type_by_stream[stream]
            source_rows_by_scope.setdefault(scope, Counter())[stream] += 1
        assert len(source_rows_by_scope) == _PER_ACCOUNT
        assert all(
            dict(counts) == expected_source_rows_by_stream_per_scope
            for counts in source_rows_by_scope.values()
        )

        monitor_record["pipe"].send(("status",))
        pre_drain_status = _collect_expected(
            {0: monitor_record}, "status", 15.0, "pre-drain Monitor status", progress
        )[0][1]
        assert pre_drain_status["durable_scope_count"] <= _PER_ACCOUNT
        assert pre_drain_status["durable_event_rows"] < len(source_before_drain)
        pending_backlog_rows = len(source_before_drain) - pre_drain_status["durable_event_rows"]
        assert pending_backlog_rows > 0

        for runtime in client_runtimes:
            runtime.close()
            assert runtime.state == "closed"
            assert runtime.cleanup_failures == 0
            assert runtime.unregister_uncertainties == 0

        owner_record["pipe"].send(("close", "diagnostic"))
        owner_record["close_sent"] = True
        try:
            owner_closed = _collect_expected(
                {0: owner_record}, "closed", 30.0, "single-owner normal drain", progress
            )[0]
        except _Load01ChildFailureError as exc:
            failure_path = tmp_path / "single-owner-close-failure.json"
            with suppress(OSError):
                failure_path.write_text(
                    json.dumps(
                        {
                            "evidence_level": "SOURCE_FAKE_SINGLE_OWNER_REAL_HOST_RELAY_OUTBOX_ONLY",
                            "child_code": exc.code,
                            "child_diagnostic": exc.child_diagnostic,
                        },
                        sort_keys=True,
                        indent=2,
                        allow_nan=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            raise
        assert owner_closed[1:3] == (0, owner_record["pid"])
        assert owner_closed[3] == owner_closed[5] == owner_closed[7] == "closed"
        assert type(owner_closed[4]) is tuple and owner_closed[4] == ()
        assert type(owner_closed[6]) is tuple and owner_closed[6] == ()
        assert type(owner_closed[8]) is int and owner_closed[8] == 0
        assert type(owner_closed[9]) is int and owner_closed[9] == 0
        assert owner_closed[10:13] == (
            cycles_per_strategy * _PER_ACCOUNT,
            cycles_per_strategy * _PER_ACCOUNT,
            expected_event_rows,
        )
        assert owner_closed[13]["provider_close_calls"] == 1
        source_evidence = owner_closed[15]
        owner_close_facts = owner_closed[17]
        assert owner_close_facts["normal_drain_completed"] is True
        assert owner_close_facts["completed_close_stages"] == [
            "source-snapshot",
            "relay-close",
            "reader-close",
            "relay-runtime-close",
            "execution-host-close",
            "account-host-close",
            "store-close",
        ]
        assert owner_close_facts["relay_count"] == owner_close_facts["reader_count"] == 0
        assert owner_close_facts["total_relayed"] == expected_event_rows
        assert owner_close_facts["relay_runtime"]["state"] == "closed"
        assert owner_close_facts["relay_runtime"]["cleanup_failures"] == 0
        assert owner_close_facts["relay_runtime"]["unregister_uncertainties"] == 0
        assert owner_close_facts["execution_host"]["state"] == "closed"
        assert owner_close_facts["execution_host"]["cleanup_failures"] == []
        assert owner_close_facts["account_host"]["state"] == "closed"
        assert owner_close_facts["account_host"]["cleanup_failures"] == []
        assert owner_close_facts["account_worker"]["state"] == "closed"
        assert owner_close_facts["account_worker"]["provider_close_calls"] == 1
        assert sum(
            item["source_rows"] for item in owner_close_facts["source_drain_by_scope"]
        ) == expected_event_rows
        assert sum(
            item["relay_delivered"] for item in owner_close_facts["source_drain_by_scope"]
        ) == expected_event_rows
        assert len(source_evidence) == _PER_ACCOUNT
        assert all(
            item["source_rows"] == source_events_per_scope
            and item["source_rows_by_stream"] == expected_source_rows_by_stream_per_scope
            and item["relay_delivered"] == source_events_per_scope
            for item in source_evidence
        )
        _normal_close_records(
            {"owner-slow": owner_record}, {"owner-slow": owner_closed}
        )

        trace_snapshot = owner_record["rpc_timing_snapshots"]["emergency"]
        assert trace_snapshot["ring_capacity"] == _LOAD01_RPC_RING_SIZE
        assert trace_snapshot["event_count_total"] == 4 * expected_rpc_count
        assert trace_snapshot["event_count_overwritten"] == 0
        trace_events_by_request: dict[tuple[str, str, str], set[str]] = {}
        for event in trace_snapshot["events"]:
            key = (event["operation"], event["message_id"], event["correlation_id"])
            trace_events_by_request.setdefault(key, set()).add(event["event"])
        assert len(trace_events_by_request) == expected_rpc_count
        required_stages = {"server_dequeue", "handler_enter", "handler_return", "reply_send"}
        assert all(required_stages <= stages for stages in trace_events_by_request.values())
        assert sum(
            "server_dequeue" in stages for stages in trace_events_by_request.values()
        ) == expected_rpc_count
        observed_rpc_requests = {
            (operation, correlation_id)
            for operation, _message_id, correlation_id in trace_events_by_request
        }
        assert observed_rpc_requests == expected_rpc_requests
        rpc_report = _load01_rpc_timing_report({"owner-00": trace_snapshot})
        assert len(rpc_report["correlated_requests"]) == expected_rpc_count
        assert all(
            request["durations_ms"]["handler"] is not None
            and request["durations_ms"]["handler"] < _RPC_TIMEOUT_MS
            and request["durations_ms"]["dequeue_to_handler_including_auth_decode_validation"]
            is not None
            for request in rpc_report["correlated_requests"]
        )

        timing = owner_closed[16]["timing_durations"]
        poll_rows = [
            timing[f"owner.relay.poll:{item['scope_ref']}"] for item in scope_records
        ]
        poll_counts = [row["count"] for row in poll_rows]
        assert min(poll_counts) >= source_events_per_scope
        assert sum(poll_counts) >= expected_event_rows
        assert max(poll_counts) - min(poll_counts) <= 1
        assert min(row["max_ns"] for row in poll_rows) >= 20_000_000
        assert max(row["max_ns"] for row in poll_rows) < _RPC_TIMEOUT_MS * 1_000_000
        pump_gap = timing["owner.host_pump_gap:execution_to_account"]
        assert pump_gap["max_ns"] >= 20_000_000
        assert pump_gap["max_ns"] < _RPC_TIMEOUT_MS * 1_000_000
        assert pump_gap["count"] >= sum(poll_counts) - 1
        assert timing["owner.host_pump_gap:account_to_execution"]["count"] > 0

        monitor_record["pipe"].send(("close",))
        monitor_record["close_sent"] = True
        monitor_closed = _collect_expected(
            {0: monitor_record}, "closed", 30.0, "slow sink Monitor close", progress
        )[0]
        assert monitor_closed[1] == monitor_record["pid"]
        assert monitor_closed[2] == "closed"
        assert type(monitor_closed[3]) is tuple and monitor_closed[3] == ()
        assert monitor_closed[4] == "closed"
        assert type(monitor_closed[5]) is tuple and monitor_closed[5] == ()
        final_status = monitor_closed[6]
        assert final_status["durable_event_rows"] == expected_event_rows
        assert final_status["durable_scope_count"] == _PER_ACCOUNT

        _normal_close_records(
            {"monitor-slow": monitor_record}, {"monitor-slow": monitor_closed}
        )

        source_after_drain = read_source_rows()
        assert len(source_after_drain) == expected_event_rows
        assert sorted(source_after_drain) == sorted(source_before_drain)

        readonly_outbox_path = outbox_path.resolve()
        with sqlite3.connect(
            readonly_outbox_path.as_uri() + "?mode=ro", uri=True, timeout=5.0
        ) as connection:
            connection.execute("PRAGMA query_only=ON")
            durable_rows = connection.execute(
                "SELECT scope, event_id, event_type, data_json "
                "FROM monitor_outbox_events ORDER BY sequence"
            ).fetchall()
        expected_outbox_scopes = {item["outbox_scope"] for item in scope_records}
        expected_scope_records = {item["outbox_scope"]: item for item in scope_records}
        assert len(expected_scope_records) == _PER_ACCOUNT
        assert len(durable_rows) == expected_event_rows
        assert {row[0] for row in durable_rows} == expected_outbox_scopes
        actual_pairs: list[tuple[str, str, str, str, str, int, str, str]] = []
        actual_outbox_event_ids: set[str] = set()
        for scope, outbox_event_id, monitor_event_type, data_json in durable_rows:
            assert type(outbox_event_id) is str
            assert outbox_event_id not in actual_outbox_event_ids
            actual_outbox_event_ids.add(outbox_event_id)
            envelope_data = json.loads(data_json)
            assert type(envelope_data) is dict
            source_payload = envelope_data.get("payload")
            assert type(source_payload) is dict
            stream = source_payload.get("stream")
            assert stream in expected_monitor_event_type_by_stream
            assert monitor_event_type == expected_monitor_event_type_by_stream[stream]
            assert type(source_payload.get("source_event_id")) is str
            assert type(source_payload.get("business_event_type")) is str
            assert source_payload["business_event_type"] in (
                expected_source_event_types_by_stream[stream]
            )
            assert type(source_payload.get("source_sequence")) is int
            source_record = expected_scope_records[scope]
            assert source_payload.get("journal_generation") == source_record[
                "source_generation"
            ]
            assert source_payload.get("source_scope_key") == source_record[
                "source_scope_key"
            ]
            actual_pairs.append(
                (
                    scope,
                    source_payload["source_event_id"],
                    monitor_event_type,
                    stream,
                    source_payload["business_event_type"],
                    source_payload["source_sequence"],
                    source_payload["journal_generation"],
                    source_payload["source_scope_key"],
                )
            )
        assert len(actual_pairs) == expected_event_rows
        assert len(actual_outbox_event_ids) == expected_event_rows
        assert len(set(actual_pairs)) == len(actual_pairs)
        assert sorted(actual_pairs) == sorted(source_after_drain)

        measured_delays = [
            int(delay_samples[index]) for index in range(delay_count.value)
        ]
        assert delay_count.value == expected_event_rows
        assert len(measured_delays) == delay_count.value
        assert all(20_000_000 <= value < _RPC_TIMEOUT_MS * 1_000_000 for value in measured_delays)
        (tmp_path / "single-owner-slow-sink-observation.json").write_text(
            json.dumps(
                {
                    "evidence_level": "SOURCE_FAKE_SINGLE_OWNER_REAL_HOST_RELAY_OUTBOX_ONLY",
                    "topology": {
                        "owners": 1,
                        "relay_scopes": _PER_ACCOUNT,
                        "scheduled_cycles_per_scope_per_second": (
                            scheduled_cycles_per_scope_per_second
                        ),
                        "order_rpc_calls_per_cycle": order_rpc_calls_per_cycle,
                        "scheduled_rpc_requests_per_scope_per_second": (
                            scheduled_rpc_requests_per_scope_per_second
                        ),
                        "scheduled_rpc_requests_per_owner_per_second": (
                            scheduled_rpc_requests_per_owner_per_second
                        ),
                        "source_event_types_by_stream": expected_source_event_types_by_stream,
                        "source_events_per_cycle": source_events_per_cycle,
                        "source_rows_by_stream_per_scope": (
                            expected_source_rows_by_stream_per_scope
                        ),
                        "source_events_per_scope_per_second": (
                            scheduled_source_events_per_scope_per_second
                        ),
                        "source_events_per_owner_per_second": (
                            scheduled_source_events_per_owner_per_second
                        ),
                        "configured_relay_capacity_per_scope_per_second": (
                            configured_relay_capacity_per_scope_per_second
                        ),
                        "configured_relay_capacity_per_owner_per_second": (
                            configured_relay_capacity_per_owner_per_second
                        ),
                        "configured_relay_capacity_utilization": (
                            configured_relay_capacity_utilization
                        ),
                        "configured_relay_headroom_fraction_of_demand": (
                            configured_relay_headroom_fraction_of_demand
                        ),
                        "rate_window_seconds": rate_window_seconds,
                        "cycles_per_strategy": cycles_per_strategy,
                        "expected_source_rows_per_scope": source_events_per_scope,
                        "expected_source_rows_total": expected_event_rows,
                        "expected_rpc_requests_total": expected_rpc_count,
                        "observed_cycle_intervals_seconds": {
                            str(index): interval
                            for index, interval in observed_cycle_intervals.items()
                        },
                        "rpc_timeout_ms": _RPC_TIMEOUT_MS,
                        "lease_seconds": _LEASE_SECONDS,
                    },
                    "sink_delay": {
                        "target_ms": slow_sink_delay_seconds * 1000,
                        "measured_count": delay_count.value,
                        "measured_min_ms": min(measured_delays) / 1_000_000,
                        "measured_max_ms": max(measured_delays) / 1_000_000,
                    },
                    "before_drain": {
                        "source_rows": len(source_before_drain),
                        "durable_rows": pre_drain_status["durable_event_rows"],
                        "pending_backlog_rows": pending_backlog_rows,
                    },
                    "after_drain": {
                        "source_rows": len(source_after_drain),
                        "durable_rows": len(actual_pairs),
                        "durable_unique_rows": len(set(actual_pairs)),
                    },
                    "owner_pump_gap_execution_to_account_max_ms": (
                        pump_gap["max_ns"] / 1_000_000
                    ),
                    "relay_poll_by_scope": {
                        item["scope_ref"]: {
                            "count": timing[f"owner.relay.poll:{item['scope_ref']}"]["count"],
                            "max_ms": (
                                timing[f"owner.relay.poll:{item['scope_ref']}"]["max_ns"]
                                / 1_000_000
                            ),
                        }
                        for item in scope_records
                    },
                    "correlated_order_rpc_count": len(rpc_report["correlated_requests"]),
                    "owner_pid": owner_record["pid"],
                    "monitor_pid": monitor_record["pid"],
                    "owner_close_facts": owner_close_facts,
                    "normal_close": True,
                },
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        for runtime in client_runtimes:
            if runtime.state != "closed":
                with suppress(BaseException):
                    runtime.close()
        try:
            if owner_record is not None and not owner_record.get("closed"):
                reap_owned_process(
                    owner_record,
                    close_command=bool(owner_record.get("relay_ready")),
                )
            if monitor_record is not None and not monitor_record.get("closed"):
                reap_owned_process(
                    monitor_record, close_command=bool(monitor_record.get("ready"))
                )
        finally:
            def shutdown_fact(
                label: str, record: dict[str, Any] | None
            ) -> dict[str, Any]:
                if record is None:
                    return {
                        "role": label,
                        "pid": None,
                        "exit_code": None,
                        "cleanup_mode": "not_started",
                        "normal_close_ack": False,
                        "cleanup_close_reply_observed": False,
                        "cleanup_close_reply_clean": False,
                        "cleanup_close_facts": None,
                        "normal_exit": False,
                        "forced_terminate": False,
                        "process_handle_closed": False,
                    }
                exit_code = record.get("exit_code", record.get("cleanup_exit_code"))
                normal_close_ack = bool(record.get("normal_close_ack"))
                normal_exit = bool(
                    record.get("normal_exit")
                    and normal_close_ack
                    and exit_code == 0
                    and not record.get("cleanup_forced_terminate", False)
                )
                cleanup_mode = record.get("cleanup_mode")
                if normal_exit:
                    cleanup_mode = "normal_close_acknowledged_exit"
                elif cleanup_mode is None:
                    if record.get("cleanup_forced_terminate"):
                        cleanup_mode = "forced_terminate"
                    elif exit_code is not None:
                        cleanup_mode = "exit_observed_without_normal_ack"
                    else:
                        cleanup_mode = "exit_unobserved"
                return {
                    "role": label,
                    "pid": record.get("pid", record.get("cleanup_pid")),
                    "exit_code": exit_code,
                    "cleanup_mode": cleanup_mode,
                    "normal_close_ack": normal_close_ack,
                    "cleanup_close_reply_observed": bool(
                        record.get("cleanup_close_reply_observed")
                    ),
                    "cleanup_close_reply_clean": bool(
                        record.get("cleanup_close_reply_clean")
                    ),
                    "cleanup_close_facts": record.get("cleanup_close_facts"),
                    "normal_exit": normal_exit,
                    "close_requested": bool(
                        record.get("close_sent") or record.get("cleanup_close_requested")
                    ),
                    "forced_terminate": bool(record.get("cleanup_forced_terminate")),
                    "liveness_observed": record.get("cleanup_liveness_observed"),
                    "process_handle_closed": bool(
                        record.get("closed") or record.get("cleanup_process_handle_closed")
                    ),
                }

            cleanup_receipt = {
                "evidence_level": "SOURCE_FAKE_SINGLE_OWNER_REAL_HOST_RELAY_OUTBOX_ONLY",
                "children": [
                    shutdown_fact("owner", owner_record),
                    shutdown_fact("monitor", monitor_record),
                ],
            }
            (tmp_path / "single-owner-child-cleanup.json").write_text(
                json.dumps(cleanup_receipt, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )


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


def test_load01_ten_strategy_twenty_topic_startup_wave_stays_healthy_and_closes(
    tmp_path: Path, monkeypatch
) -> None:
    """Run one real ten-strategy market wave through startup, lease wait and normal close."""

    if os.name != "nt":
        pytest.skip("LOAD01 focused startup-wave check uses the Windows spawn baseline")

    assert _RPC_TIMEOUT_MS == 5_000
    assert _LEASE_SECONDS == 30
    assert _STARTUP_STRATEGY_SLOTS == _ACCOUNTS == 10
    assert _TOPICS == 20
    source_root = _fixed_source_root()
    source_pin_path = Path(os.environ["BT_API_LOAD01_EXPORT_PINS"]).resolve()
    load_runner = Path(__file__).resolve()
    runner_relative = load_runner.relative_to(source_root).as_posix()
    runner_overlay_sha256 = os.environ["BT_API_LOAD01_RUNNER_OVERLAY_SHA256"]
    assert _sha256(load_runner) == runner_overlay_sha256
    monkeypatch.setenv("BT_API_LOAD01_SOURCE_ROOT", str(source_root))
    manifest_work = tmp_path / "load01-market-wave-manifest"
    manifest_work.mkdir()
    manifest_path = _write_source_run_manifest(
        source_root,
        manifest_work,
        allowed_overlays={runner_relative: runner_overlay_sha256},
    )
    monkeypatch.setenv("BT_API_LOAD01_SOURCE_MANIFEST", str(manifest_path))
    checked_sources = _check_fixed_source_manifest(manifest_path)
    assert checked_sources["load_runner"]["sha256"] == runner_overlay_sha256
    fixed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert fixed_manifest["parent_commit"] == _FIXED_PARENT
    assert fixed_manifest["package_gitlinks"] == json.loads(
        source_pin_path.read_text(encoding="utf-8")
    )["package_gitlinks"]

    context = multiprocessing.get_context("spawn")
    market_rpc, market_pub = _free_endpoints(2)
    market_keys = _market_keys()
    startup_waves = _strategy_startup_waves()
    assert len(startup_waves) == _PER_ACCOUNT
    assert sorted(index for wave in startup_waves for index in wave) == list(
        range(_STRATEGIES)
    )
    assert all(
        tuple(index // _PER_ACCOUNT for index in wave) == tuple(range(_ACCOUNTS))
        for wave in startup_waves
    )
    strategy_indices = startup_waves[0]
    assert tuple(index // _PER_ACCOUNT for index in strategy_indices) == tuple(range(_ACCOUNTS))
    market_server_keys = zmq.curve_keypair()
    strategy_keys = {index: zmq.curve_keypair() for index in strategy_indices}
    audience = tuple(
        (
            strategy_keys[index][0],
            _principal_ref(index),
            _strategy_ref(index),
            _ACCOUNT_REFS[index // _PER_ACCOUNT],
        )
        for index in strategy_indices
    )
    work_root = tmp_path / "load01-market-wave"
    work_root.mkdir()
    registry_path = work_root / "market-registry.sqlite3"
    market_config = _market_service_config(market_rpc, market_pub)
    load_start_event = context.Event()
    load_start_value = context.Value("d", 0.0)
    stop_event = context.Event()
    wave_start_event = context.Event()
    abort_event = context.Event()
    progress: dict[str, Any] = {"strategy": {}, "market": {}}
    host_record = None
    strategy_records: dict[int, dict[str, Any]] = {}
    all_records: dict[str, dict[str, Any]] = {}

    try:
        host_record = _start_process(
            context,
            _market_host_process,
            (
                market_config,
                str(registry_path),
                market_server_keys,
                audience,
                market_keys,
                load_start_event,
                load_start_value,
                stop_event,
            ),
            "load01-market-wave-host",
        )
        host_record.update(
            {
                "pid": host_record["process"].pid,
                "kind": "market",
                "ready": False,
            }
        )
        all_records["market-hub"] = host_record
        host_ready = _collect_expected(
            {0: host_record}, "ready", 60.0, "startup-wave market Host", progress
        )[0]
        assert host_ready[1] == "market"
        assert host_ready[2] == host_record["pid"]
        assert host_ready[3] == market_rpc
        assert host_ready[4] == market_pub
        host_record["ready"] = True

        for index in strategy_indices:
            client_public, client_secret = strategy_keys[index]
            security_material = (market_server_keys[0], client_public, client_secret)
            raw_config = _market_wave_strategy_config(index, market_rpc, market_pub)
            record = _start_process(
                context,
                _load01_market_wave_strategy_process,
                (
                    index,
                    raw_config,
                    market_keys,
                    security_material,
                    wave_start_event,
                    abort_event,
                ),
                f"load01-wave-strategy-{index:03d}",
            )
            record.update(
                {
                    "pid": record["process"].pid,
                    "kind": "strategy",
                    "ready": False,
                }
            )
            strategy_records[index] = record
            all_records[record["name"]] = record

        loaded = _collect_expected(
            strategy_records, "loaded", 90.0, "single startup-wave process import", progress
        )
        assert len({message[2] for message in loaded.values()}) == _STARTUP_STRATEGY_SLOTS
        assert all(
            message[1] == index
            and message[2] == strategy_records[index]["pid"]
            and strategy_records[index]["process"].is_alive()
            for index, message in loaded.items()
        )
        wave_start_event.set()
        strategy_ready = _collect_expected(
            strategy_records,
            "ready",
            300.0,
            "single ten-strategy startup wave",
            progress,
        )
        assert len({message[2] for message in strategy_ready.values()}) == _STARTUP_STRATEGY_SLOTS
        for index, record in strategy_records.items():
            message = strategy_ready[index]
            assert type(message) is tuple and len(message) == 10
            assert message[1:6] == (
                index,
                record["pid"],
                _strategy_ref(index),
                _ACCOUNT_REFS[index // _PER_ACCOUNT],
                1,
            )
            assert message[6] == tuple(sorted(key.stream_id for key in market_keys))
            assert message[7]["healthy"] is True
            batch_facts = message[9]
            assert type(batch_facts) is tuple and len(batch_facts) == 1
            batch = batch_facts[0]
            assert type(batch) is dict
            assert set(batch) == {"stream_count", "duration_ms", "measurement_scope"}
            assert batch["stream_count"] == _TOPICS
            assert batch["measurement_scope"] == "same-registration-market-batch"
            assert type(batch["duration_ms"]) in (int, float)
            assert 0 <= batch["duration_ms"] < float("inf")
            assert message[7]["required_min_lease_remaining_ms"] > (
                _RPC_LEASE_HEADROOM_SECONDS * 1000
            )
            assert record["process"].is_alive()
            record["ready"] = True

        # Keep this wave in the real common-start waiting state long enough to
        # cross a 30-second lease's one-third renewal point before closing it.
        time.sleep(11.0)
        host_record["pipe"].send(("status",))
        active_status = _collect_expected(
            {0: host_record}, "status", 15.0, "startup-wave market status", progress
        )[0][1]
        assert active_status["hub_readiness"]["active_registrations"] == (
            _STARTUP_STRATEGY_SLOTS * _TOPICS
        )
        assert active_status["hub_readiness"]["active_upstreams"] == _TOPICS
        assert active_status["provider_starts"] == {
            key.stream_id: 1 for key in market_keys
        }
        assert active_status["provider_stops"] == {}

        for record in strategy_records.values():
            record["pipe"].send(("close",))
        strategy_closed = _collect_expected(
            strategy_records,
            "closed",
            60.0,
            "single ten-strategy normal unsubscribe",
            progress,
        )
        for index, message in strategy_closed.items():
            assert type(message) is tuple and len(message) == 4
            assert message[0] == "closed"
            assert message[1:3] == (index, strategy_records[index]["pid"])
            close_facts = message[3]
            assert type(close_facts) is dict
            assert close_facts["expected_rpc_timeout_ms"] == _RPC_TIMEOUT_MS
            assert close_facts["lease_seconds"] == _LEASE_SECONDS
            assert close_facts["market_clients_started"] == _TOPICS
            assert close_facts["market_clients_closed"] == _TOPICS
            assert (
                close_facts["market_client_start_batches"]
                == strategy_ready[index][9]
            )
            assert close_facts["closed_streams"] == tuple(
                sorted(key.stream_id for key in market_keys)
            )
            assert close_facts["ready_barrier_maintenance"] >= 1
            assert close_facts["runtime_state"] == "closed"
            assert close_facts["runtime_cleanup_failures"] == 0
            assert close_facts["runtime_unregister_uncertainties"] == 0
            assert close_facts["runtime_unknown_registration_count"] == 0

        host_record["pipe"].send(("close",))
        host_closed = _collect_expected(
            {0: host_record}, "closed", 60.0, "startup-wave market Host close", progress
        )[0]
        assert type(host_closed) is tuple and len(host_closed) == 5
        assert host_closed[0] == "closed"
        assert host_closed[1:3] == (host_record["pid"], "closed")
        assert type(host_closed[3]) is tuple and host_closed[3] == ()
        final_status = host_closed[4]
        assert type(final_status) is dict
        assert set(final_status) == {
            "provider_starts",
            "provider_stops",
            "active_provider_keys",
            "source_sequences",
            "source_payload_bytes",
            "first_publish_ns",
            "last_publish_ns",
            "hub_readiness",
        }
        assert final_status["provider_starts"] == {
            key.stream_id: 1 for key in market_keys
        }
        assert final_status["provider_stops"] == {
            key.stream_id: 1 for key in market_keys
        }
        assert final_status["active_provider_keys"] == ()
        assert final_status["source_sequences"] == {}
        assert final_status["source_payload_bytes"] == 0
        assert final_status["first_publish_ns"] is None
        assert final_status["last_publish_ns"] is None
        assert final_status["hub_readiness"]["active_registrations"] == 0
        assert final_status["hub_readiness"]["active_upstreams"] == 0

        close_messages = {"market-hub": host_closed}
        close_messages.update(
            {
                strategy_records[index]["name"]: message
                for index, message in strategy_closed.items()
            }
        )
        _normal_close_records(all_records, close_messages)
        assert _registered_session_inventory({"market": registry_path}) == {"market": 0}
        assert all(record["normal_exit"] and record["exit_code"] == 0 for record in all_records.values())
    finally:
        abort_event.set()
        cleanup_failures = []
        for record in strategy_records.values():
            if record.get("closed") or not record["process"].is_alive():
                continue
            with suppress(EOFError, OSError, ValueError):
                record["pipe"].send(("close",))
        strategy_cleanup_deadline = time.monotonic() + 20.0
        for record in strategy_records.values():
            process = record["process"]
            if record.get("closed"):
                continue
            if process.is_alive():
                process.join(max(0.0, strategy_cleanup_deadline - time.monotonic()))
            if process.is_alive():
                process.terminate()
                process.join(5)
            if process.is_alive():
                cleanup_failures.append(
                    {"name": record["name"], "pid": record["pid"], "state": "alive_after_terminate"}
                )
            elif process.exitcode is not None:
                with suppress(AssertionError, OSError, ValueError):
                    record["pipe"].close()
                    process.close()
                    record["closed"] = True
            else:
                cleanup_failures.append(
                    {"name": record["name"], "pid": record["pid"], "state": "missing_exitcode"}
                )
        if host_record is not None:
            process = host_record["process"]
            if not host_record.get("closed"):
                if process.is_alive():
                    with suppress(EOFError, OSError, ValueError):
                        host_record["pipe"].send(("close",))
                    process.join(20)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                if process.is_alive():
                    cleanup_failures.append(
                        {
                            "name": host_record["name"],
                            "pid": host_record["pid"],
                            "state": "alive_after_terminate",
                        }
                    )
                elif process.exitcode is not None:
                    with suppress(AssertionError, OSError, ValueError):
                        host_record["pipe"].close()
                        process.close()
                        host_record["closed"] = True
                else:
                    cleanup_failures.append(
                        {
                            "name": host_record["name"],
                            "pid": host_record["pid"],
                            "state": "missing_exitcode",
                        }
                    )
        if cleanup_failures:
            raise AssertionError(
                "LOAD01 focused startup-wave cleanup did not reap all owned children: "
                f"{cleanup_failures}"
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
