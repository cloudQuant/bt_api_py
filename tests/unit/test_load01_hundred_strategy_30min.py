"""Bounded LOAD01 synthetic sustained-load runner on fixed committed sources."""

from __future__ import annotations

import hashlib
import json
from math import ceil, fsum, isfinite
import multiprocessing
from multiprocessing.connection import wait as wait_connections
import os
import queue
import random
import sqlite3
import sys
import threading
import time
import traceback
import zipfile
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

if multiprocessing.current_process().name == "MainProcess":
    import psutil
    import pytest
else:
    # Worker imports execute this pytest module as __mp_main__. The strategy
    # worker never runs test functions, so avoid importing the test runner and
    # resource sampler into each strategy process.
    psutil = None
    pytest = None
import zmq
from tests.unit._load01_resource_diagnostics import (
    aggregate_strategy_queue_snapshots,
    aggregate_role_resources,
    append_and_validate_load01_resource_sample,
    capture_market_queue_snapshot,
    capture_process_resource_row,
)
from tests.unit._load01_recovery_timing import (
    aggregate_recovery_observations,
    capture_strategy_market_recovery_heads,
    new_recovery_incident,
    record_market_delivery_receipt,
)
from tests.unit._load01_profiles import (
    load01_profile,
    market_keys_for_profile,
    market_payload_data,
)
from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

from bt_api_py.service_account import ServiceAccountClient
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_execution import ServiceExecutionClient, ServiceExecutionError
from bt_api_py.service_market import (
    ServiceMarketClient,
    ServiceMarketError,
    ZmqMarketTransportFactory,
)
from bt_api_py.service_registry import (
    ActiveRegistrationContext,
    PrincipalGrant,
    ResourceGrant,
)
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ServiceRuntimeRequestNotSentError,
    ZmqTransportFactory,
)
from bt_api_py.service_telemetry import ServiceTelemetryEmitter
from bt_api_transport_zmq.service_v2 import (
    RequestNotSentError,
    RequestOutcomeUnknownError,
)
from tests.unit._load01_metrics import (
    OperationLatencyCollector,
    nearest_rank,
    summarize_cpu_samples,
)

_SERVER_IMPORTS_LOADED = False


def _load_load01_server_imports() -> None:
    """Load the existing server/test dependency closure only in owning roles."""
    global _SERVER_IMPORTS_LOADED
    if _SERVER_IMPORTS_LOADED:
        return
    global ActiveRegistrationContext
    global CancelDispatchClaimReceiptV1, CancelObservation
    global ComponentResources, DurableOutbox
    global ExecutionJournalReader, ExecutionOrderEntryService
    global ExecutionScope, ExecutionState, GatewayAccountSessionWorker
    global GatewayCommandKind, GatewayEventJournal, GatewayMarketHub
    global GatewayPrincipal, GatewayServiceAccountScope
    global GroupLegPhase, GroupOrderSnapshot, GroupOrderState
    global INSTRUMENT_METADATA_DIGEST_TAG, InstrumentRiskMetadata
    global LocalAccountSessionGuard, MonitorIngressTarget, MonitorTelemetryService
    global N5_BASE_UNIT_SCALES, N5_GROUP_QUANTITY_UNIT, N5_LEG_IDS, N5_LEG_SPECS
    global OrderIntent, PrincipalGrant, ProviderObservation, ResourceGrant
    global ServiceExecutionMonitorRelay, ServiceHost, SqliteExecutionStore
    global TrustedServiceAuthority, _CountedSyntheticAccountProvider
    global _plain_rpc_json, _relay_outbox_scope, _free_endpoints, _start_process
    global build_n5_coordinator_service_config, build_n5_group_metadata
    global build_n5_group_report, build_n5_group_request, group_leg_intent_id
    global n5_coordinator_host_process, n5_coordinator_service_info
    global query_n5_group, submit_n5_group

    from tests.unit._load01_group_coordinator import (
        build_n5_coordinator_service_config,
        build_n5_group_metadata,
        n5_coordinator_host_process,
        n5_coordinator_service_info,
    )
    from tests.unit._load01_n5_report import build_n5_group_report
    from tests.unit._load01_group_workload import (
        N5_GROUP_QUANTITY_UNIT,
        N5_BASE_UNIT_SCALES,
        N5_LEG_IDS,
        N5_LEG_SPECS,
        build_n5_group_request,
        query_n5_group,
        submit_n5_group,
    )
    from bt_api_execution import (
        CancelDispatchClaimReceiptV1,
        CancelObservation,
        ExecutionState,
        ExecutionScope,
        GroupLegPhase,
        GroupOrderSnapshot,
        GroupOrderState,
        OrderIntent,
        ProviderObservation,
        SqliteExecutionStore,
        group_leg_intent_id,
    )
    from bt_api_execution.journal_reader import ExecutionJournalReader
    from bt_api_execution.service_order_entry import ExecutionOrderEntryService
    from bt_api_gateway import GatewayCommandKind, GatewayEventJournal, GatewayPrincipal
    from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker
    from bt_api_gateway.market_hub import GatewayMarketHub
    from bt_api_gateway.service_component import GatewayServiceAccountScope
    from bt_api_gateway.session_guard import LocalAccountSessionGuard
    from bt_api_monitor.durable import DurableOutbox
    from bt_api_monitor.service_ingress import MonitorIngressTarget
    from bt_api_monitor.service_telemetry import MonitorTelemetryService
    from bt_api_py.service_components import ComponentResources
    from bt_api_py.service_execution_monitor import (
        ServiceExecutionMonitorRelay,
        _outbox_scope as _relay_outbox_scope,
    )
    from bt_api_py.service_execution_group import _plain_rpc_json
    from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
    from bt_api_py.service_registry import (
        ActiveRegistrationContext,
        PrincipalGrant,
        ResourceGrant,
    )
    from bt_api_risk import INSTRUMENT_METADATA_DIGEST_TAG, InstrumentRiskMetadata
    from tests.unit.test_service_gateway_hundred_strategy_integration import (
        _CountedSyntheticAccountProvider,
        _free_endpoints,
        _start_process,
    )
    _SERVER_IMPORTS_LOADED = True


if multiprocessing.current_process().name == "MainProcess":
    _load_load01_server_imports()

_FIXED_PARENT = "145e7e07d84583b8eb94e0ea99fa0a7af09e24b9"
_FIXED_PACKAGE_GITLINKS = {
    "bt_api/bt_api_base": "fb47f3dd733613494aa1d86cfd94b2d6fe70314d",
    "bt_api/bt_api_execution": "79645cbe0c2ddab2761b5cb31b0ab9eff432cc1d",
    "bt_api/bt_api_gateway": "d678a58cb0788f9a8aae90cb6117496ea84bd031",
    "bt_api/bt_api_monitor": "fcc092ebfd87c5f67041565d369c492422c91cf1",
    "bt_api/bt_api_risk": "9875c04499b559fb59a8d2d13861796babf1f7e2",
    "bt_api/bt_api_transport_zmq": "acd74ba03494615aeb2d60423ac853e06029cba7",
}
_N6_QUOTA_WAVE_START_SECONDS = 60.0
_N6_QUERY_QUOTA_DOMAIN_REF = "synthetic:load01-n6-server-time-query-v1"
_N6_QUERY_QUOTA_CAPACITY = 3
_N6_QUERY_QUOTA_WINDOW_SECONDS = 3_600.0
_N6_QUERY_QUOTA_MAX_WAIT_SECONDS = 0.25
_N6_QUERY_RPC_TIMEOUT_MS = 5_000
_LOAD01_CASE_ID = "LOAD01_LOCAL_SYNTHETIC_10_STRATEGY_2_ACCOUNT"
_LOAD01_REGISTRATION_CONTRACT_ID = "LOAD01-10S-2A-R1"
_N6_QUOTA_SOURCE_PINS = {
    "tests/unit/_load01_quota_wave.py": "12fc242570911a5af1a2b1152bd25e2876030130ce15c4ff7e61c967a78ef754",
    "tests/unit/_load01_n6_quota_wave.py": "83cdd749707ceb65cc3410f6e164649f1593f66878475eca91992c848e9c9620",
    "tests/unit/_load01_query_quota_probe.py": "589a598b2ae0dcd5d1583f4eddf9d5d915208272143b8077988d082f66f329aa",
    "tests/integration/test_load01_n6_server_time_quota.py": "7201172b096d2b0c50429610200cbfa60ba268f03dfa5b12ab5168670a6828d0",
    "bt_api/bt_api_gateway/src/bt_api_gateway/__init__.py": "c772784c83ba6019d403633bc735c9ff26391d92885678dddc0f287e89db5cc7",
    "bt_api/bt_api_gateway/src/bt_api_gateway/database_identity.py": "a66cf0c742c08ab6b17c69d00dc8ebfa817ced209b82c76a0a42d573890d2002",
    "bt_api/bt_api_gateway/src/bt_api_gateway/quota_admission.py": "e40c6243b746b3eeb03897ddbcc3a2e538a18b241fdbddc59cfb130343fd8aa4",
    "bt_api/bt_api_gateway/src/bt_api_gateway/service_component.py": "87dac9d8f62d271601ca3fa8bb370b46dfe3e812968e5ee7fe87e0b53ae34efb",
    "bt_api/bt_api_base/src/bt_api_base/account_certification_protocol.py": "5bf274561a4b8b5f48feabda8d0075910f056de7c268a16e47b5a499bf20be20",
    "bt_api/bt_api_base/src/bt_api_base/service_protocol.py": "f0ed739ac98cda400bed07bf8487b79ee0a5ebe6842b9d0711b4b12e8af6b15a",
    "bt_api_py/service_account_certification.py": "b8d059b39bd035444b68a9eab7a678c7ad4c0b0734961c7eafc8dc0745db020e",
    "bt_api_py/service_registry.py": "58c5cfb09253762259470c19cf8e12b8dea69223395cb4c5dc0370db98df8614",
    "bt_api_py/service_runtime.py": "2b5a54aab95feeb62352de07727eb648e2d5209b43550ee564b25d62c5925c30",
}
_N6_PROFILE_OVERLAY_SHA256 = "34cd3021017b37e5635c394b7a89cedd97f0e9c9e0cad1a6957711d698c8465d"
_N6_PROFILE_TEST_OVERLAY_SHA256 = "82a2bc72425da1ae1f1571ca71fab88fcba6667c5f0731bfecb284dc83368653"
_LOAD01_THRESHOLD_REGISTRATION_PATH = "docs/evidence/iteration10-load01-engineering-registration-20261020/threshold-registration-r1.json"
_LOAD01_THRESHOLD_REGISTRATION_SHA256 = "ec29c150a0de396e374776258d17a0e33386d3eb5a5b642214f76091b12e4536"
_LOAD01_RESOURCE_DIAGNOSTICS_OVERLAY_SHA256 = "55972ecfe55f9c17494dc616c70c7cc7eda300f598588ea976bb018402cadfc4"
_LOAD01_RESOURCE_DIAGNOSTICS_TEST_OVERLAY_SHA256 = "c6e31128cca39d14bac2a1feb7d22d3c019919cc8000742029a361f3e5921092"
_LOAD01_GROUP_TOPOLOGY_OVERLAY_SHA256 = "e21cd0bbd152947ed695a3b4a095fecbf64cec148d1c316eb2570f0e7802bffd"
_LOAD01_RUNNER_METRICS_TEST_OVERLAY_SHA256 = "8c34b5a78ae7e69168301878127a690850e4856c2229deb9f0acf468b9557862"
_N6_QUOTA_WAVE_INTEGRATION_TEST_OVERLAY_SHA256 = "2060ab15792cad8cb8ff54f29282ac7420eeac477f1a0215b11313b7a5f9e4da"
_LOAD01_CPU_SAMPLE_INTERVAL_SECONDS = 5.0
_LOAD01_CPU_SAMPLE_MAX_GAP_SECONDS = 7.5
_DOMAIN = "paper:load01-synthetic"
_STRATEGIES = 10
_ACCOUNTS = 2
_PER_ACCOUNT = 5
_STARTUP_STRATEGY_SLOTS = 10
_TOPICS = 20
_MARKET_HZ = 200
_MARKET_PUBLISH_BATCH_MAX = 4
_STRATEGY_MARKET_POLL_BATCH_MAX = 20
_STRATEGY_MARKET_POLL_BATCH_BUDGET_NS = 10_000_000
_LIFECYCLES_PER_ACCOUNT_HZ = 2
_EVENT_TYPES = ("execution.lifecycle", "execution.cancellation")
_RELAY_POLL_INTERVAL_SECONDS = 0.5
_RELAY_EVENTS_PER_SLICE = 1
_RELAY_IDLE_POLL_INTERVAL_SECONDS = 0.05
_RELAY_DRAIN_DEADLINE_SECONDS = 120.0
_EMERGENCY_CLOSE_GRACE_SECONDS = 20.0
_EMERGENCY_TERMINATE_GRACE_SECONDS = 10.0
_EMERGENCY_KILL_GRACE_SECONDS = 10.0
_OWNER_CLOSE_GRACE_SECONDS = (
    _RELAY_DRAIN_DEADLINE_SECONDS + _EMERGENCY_CLOSE_GRACE_SECONDS
)
_RELAY_POLL_TIMING_SAMPLE_CAPACITY = 512
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
        "market.read_after_bounded",
    }
)
_ACCOUNT_OPS = frozenset({"account.snapshot"})
_N6_ACCOUNT_QUERY_OPS = _ACCOUNT_OPS | frozenset({"account.server_time_probe"})
_ORDER_OPS = frozenset({"orders.submit", "orders.cancel"})
_QUERY_OPS = frozenset({"orders.query"})
_N5_GROUP_OPS = frozenset({"groups.submit", "groups.query"})
_N4_MARKET_STREAM_TAG = "load01_market_stream"
_N4_LIFECYCLE_TAG = "load01_n4_lifecycle"
_TELEMETRY_OPS = frozenset({"telemetry.append"})
_INGRESS_OPS = frozenset({"events.append"})
_MAX_RPC_BYTES = 262_144
_RPC_TIMEOUT_MS = 5_000
_RELAY_DRAIN_RPC_HEADROOM_SECONDS = (_RPC_TIMEOUT_MS / 1000) + 0.25
_HEARTBEAT_SECONDS = 5
_LEASE_SECONDS = 30
_RPC_LEASE_HEADROOM_SECONDS = (_RPC_TIMEOUT_MS / 1000) + 0.25
_N5_OWNER_CONTROL_WAIT_SECONDS = 300.0
_MARKET_WIRE_VERSION = 2
_MARKET_HWM = 128
_MARKET_REPLAY_EVENTS = 2_048
_MARKET_REPLAY_BYTES = 1024 * 1024

_RESOURCE = AccountResourceKey("spot", "cash")
_ACCOUNT_REFS = tuple(f"gw02-account-{index:02d}" for index in range(_ACCOUNTS))
_ACCOUNT_KEYS = tuple(
    AccountKey("synthetic", "loopback", "paper", f"gw02-owner-{index:02d}")
    for index in range(_ACCOUNTS)
)


def _load01_unit_marker(function):
    """Retain pytest's unit mark in the parent without importing pytest in workers."""
    if pytest is None:
        return function
    return pytest.mark.unit(function)

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
_N5_PORTFOLIO_REF = "load01-n5-portfolio-v1"
_N5_COORDINATOR_PRINCIPAL = "load01-n5-group-coordinator-principal"
_N5_COORDINATOR_SERVICE_ID = "load01-n5-group-coordinator-service"

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


def _strategy_telemetry_scope_ref(
    profile_id: str, strategy_index: int, account_index: int
) -> str:
    if profile_id != "N3":
        return _TELEMETRY_SCOPE_REF
    return (
        f"load01-monitor-telemetry-a{account_index:02d}-"
        f"s{strategy_index:03d}-v1"
    )


def _principal_ref(index: int) -> str:
    return f"{_PRINCIPAL_PREFIX}{index:03d}"


def _merge_authority_resource(
    grants: dict[str, tuple[PrincipalGrant, ...]],
    principal: str,
    identity: WireIdentity,
    resource: ResourceGrant,
) -> None:
    existing = grants.get(principal, ())
    if existing:
        if len(existing) != 1 or existing[0].identity != identity:
            raise AssertionError("LOAD01 principal grant identity changed across account routes")
        resources = existing[0].resources + (resource,)
    else:
        resources = (resource,)
    grants[principal] = (PrincipalGrant(identity, resources),)


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


def _load01_terminal_market_poll_budget(
    *, stop_requested: bool, terminal_request_active: bool, stream_count: int
) -> int:
    """Bound ordinary market polling and terminal catch-up rounds."""

    if (
        type(stop_requested) is not bool
        or type(terminal_request_active) is not bool
        or type(stream_count) is not int
        or stream_count < 0
    ):
        raise ValueError("LOAD01 terminal market poll budget is invalid")
    if stop_requested and terminal_request_active:
        return stream_count
    if stop_requested:
        return min(stream_count, 1)
    return min(stream_count, _STRATEGY_MARKET_POLL_BATCH_MAX)


def _load01_run_due_market_publish_batch(
    publish_one,
    *,
    topic_schedule: Sequence[int],
    market_keys: Sequence[MarketSubscriptionKey],
    schedule_index: int,
    next_publish_ns: int,
    interval_ns: int,
    batch_limit: int,
    monotonic_ns,
) -> tuple[int, int, int]:
    """Publish only genuinely due events, with a bounded service-loop turn."""

    if (
        not callable(publish_one)
        or not isinstance(topic_schedule, Sequence)
        or not topic_schedule
        or not isinstance(market_keys, Sequence)
        or not market_keys
        or type(schedule_index) is not int
        or not 0 <= schedule_index < len(topic_schedule)
        or type(next_publish_ns) is not int
        or type(interval_ns) is not int
        or interval_ns <= 0
        or type(batch_limit) is not int
        or batch_limit < 0
        or not callable(monotonic_ns)
        or any(type(topic_index) is not int or not 0 <= topic_index < len(market_keys)
               for topic_index in topic_schedule)
    ):
        raise ValueError("LOAD01 due market publish batch is invalid")
    published = 0
    while published < batch_limit:
        publish_ns = monotonic_ns()
        if type(publish_ns) is not int or publish_ns < 0:
            raise ValueError("LOAD01 market publish clock is invalid")
        if publish_ns < next_publish_ns:
            break
        publish_one(market_keys[topic_schedule[schedule_index]], publish_ns)
        schedule_index = (schedule_index + 1) % len(topic_schedule)
        next_publish_ns += interval_ns
        published += 1
    return schedule_index, next_publish_ns, published


def _load01_market_host_sleep_seconds(
    *, next_publish_ns: int | None, now_ns: int, stop_requested: bool
) -> float:
    """Yield only when publication is caught up; keep the existing idle cadence."""

    if (
        (next_publish_ns is not None and type(next_publish_ns) is not int)
        or type(now_ns) is not int
        or now_ns < 0
        or type(stop_requested) is not bool
    ):
        raise ValueError("LOAD01 market host sleep inputs are invalid")
    if stop_requested or next_publish_ns is None:
        return 0.001
    delay_ns = next_publish_ns - now_ns
    if delay_ns <= 0:
        return 0.0
    return max(0.0002, min(0.001, delay_ns / 1_000_000_000))


def _load01_run_bounded_market_poll_batch(
    poll_one,
    *,
    poll_budget: int,
    poll_pause: bool,
    deadline_monotonic_ns: int | None,
    monotonic_ns,
) -> int:
    """Run the bounded batch without starting another poll past its deadline."""

    if (
        not callable(poll_one)
        or type(poll_budget) is not int
        or poll_budget < 0
        or type(poll_pause) is not bool
        or (deadline_monotonic_ns is not None and type(deadline_monotonic_ns) is not int)
        or not callable(monotonic_ns)
    ):
        raise ValueError("LOAD01 bounded market poll batch is invalid")
    started = 0
    for _ in range(poll_budget):
        if (
            deadline_monotonic_ns is not None
            and monotonic_ns() >= deadline_monotonic_ns
        ):
            break
        poll_one(pause=poll_pause)
        started += 1
    return started


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
        self._last_rpc_diagnostic: dict[str, Any] | None = None
        self._first_rpc_boundary_error: dict[str, Any] | None = None

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
        call_started_ns = time.perf_counter_ns()
        rpc_diagnostic: dict[str, Any] = {
            "phase": _bounded_diagnostic_text(self.phase),
            "mount": _bounded_diagnostic_text(mount),
            "operation": _bounded_diagnostic_text(operation),
            "started_monotonic_ns": call_started_ns,
            "stage": "preflight",
            "request_started": False,
            "transport_outcome": "not_started",
        }
        self._last_rpc_diagnostic = rpc_diagnostic
        self._rpc_boundary_checks += 1
        phase = self.phase
        stage = "preflight"
        began: float | None = None
        transport_entered = False
        transport_recorded = False
        try:
            self._maintain_if_due("rpc_preflight")
            self._require_healthy_after_boundary()
            phase = self.phase
            rpc_diagnostic["phase"] = _bounded_diagnostic_text(phase)
            if mount == "store.market" and operation == "market.unsubscribe":
                try:
                    rpc_diagnostic["pre_unsubscribe_lease"] = _runtime_lease_snapshot(
                        self.runtime
                    )
                    rpc_diagnostic["pre_unsubscribe_lease_available"] = True
                    rpc_diagnostic[
                        "pre_unsubscribe_lease_observed_monotonic_ns"
                    ] = time.perf_counter_ns()
                except BaseException:
                    # Diagnostics must not change the transport outcome.
                    rpc_diagnostic["pre_unsubscribe_lease_available"] = False
            stage = "transport"
            rpc_diagnostic["stage"] = stage
            began = self._clock()
            rpc_diagnostic["request_started_monotonic_ns"] = time.perf_counter_ns()
            rpc_diagnostic["request_started"] = True
            rpc_diagnostic["transport_outcome"] = "in_flight"
            transport_entered = True
            result = self._call(mount, operation, arguments, **kwargs)
            elapsed_ms = (self._clock() - began) * 1000
            transport_recorded = True
            rpc_diagnostic["transport_outcome"] = "returned"
            rpc_diagnostic["request_duration_ms"] = round(elapsed_ms, 3)
            self._record_rpc(phase, mount, operation, elapsed_ms)
            stage = "postflight"
            rpc_diagnostic["stage"] = stage
            self._require_healthy_after_boundary()
            self._rpc_boundary_checks += 1
            self._maintain_if_due("rpc_postflight")
            self._require_healthy_after_boundary()
            rpc_diagnostic["completed_monotonic_ns"] = time.perf_counter_ns()
            rpc_diagnostic["stage"] = "complete"
            return result
        except BaseException as exc:
            observed_ns = time.perf_counter_ns()
            if transport_entered and not transport_recorded and began is not None:
                elapsed_ms = (self._clock() - began) * 1000
                transport_recorded = True
                self._record_rpc(phase, mount, operation, elapsed_ms)
                rpc_diagnostic["transport_outcome"] = "raised"
                rpc_diagnostic["request_duration_ms"] = round(elapsed_ms, 3)
            try:
                safe_error_code = _safe_diagnostic_code(exc)
            except BaseException:
                safe_error_code = "UNEXPECTED_ERROR"
            self._last_runtime_error_code = safe_error_code
            try:
                cause_code = _bounded_diagnostic_text(getattr(exc, "cause_code", None))
            except BaseException:
                cause_code = None
            rpc_diagnostic.update(
                {
                    "stage": f"{stage}-error",
                    "observed_monotonic_ns": observed_ns,
                    "safe_error_code": safe_error_code,
                    "cause_code": cause_code,
                    "exception_type": _bounded_diagnostic_text(type(exc).__name__),
                }
            )
            if self._first_rpc_boundary_error is None:
                self._first_rpc_boundary_error = dict(rpc_diagnostic)
            raise

    def rpc_error_diagnostic(self) -> dict[str, Any]:
        return {
            "last_rpc": (
                dict(self._last_rpc_diagnostic)
                if self._last_rpc_diagnostic is not None
                else None
            ),
            "first_rpc_boundary_error": (
                dict(self._first_rpc_boundary_error)
                if self._first_rpc_boundary_error is not None
                else None
            ),
        }

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
    try:
        value = getattr(error, "code", None)
    except BaseException:
        return "UNEXPECTED_ERROR"
    return _safe_diagnostic_code_value(value)


_LOAD01_RPC_RING_SIZE = 256
_LOAD01_RPC_TRACE: _Load01RpcTrace | None = None
_LOAD01_RPC_TRACE_CONTEXT = threading.local()


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


def _load01_drain_relay_until_empty(
    poll_one,
    terminal_state,
    source_rows: Mapping[str, int],
    expected_delivered: int,
    deadline_monotonic: float,
    *,
    already_delivered: int = 0,
    clock=time.monotonic,
    rpc_headroom: float = _RELAY_DRAIN_RPC_HEADROOM_SECONDS,
) -> dict[str, Any]:
    """Drain one relay, counting pre-drain deliveries toward its final total."""

    if (
        not callable(poll_one)
        or not callable(terminal_state)
        or not isinstance(source_rows, Mapping)
        or not source_rows
        or any(type(value) is not int or value < 0 for value in source_rows.values())
        or type(expected_delivered) is not int
        or expected_delivered != sum(source_rows.values())
        or type(already_delivered) is not int
        or already_delivered < 0
        or already_delivered > expected_delivered
        or not callable(clock)
        or not isinstance(deadline_monotonic, (int, float))
        or not isinstance(rpc_headroom, (int, float))
        or rpc_headroom < 0
    ):
        raise ValueError("LOAD01 relay drain contract is invalid")

    drained = 0
    poll_count = 0
    while True:
        if deadline_monotonic - clock() <= rpc_headroom:
            raise TimeoutError("LOAD01 relay drain deadline headroom exhausted")
        result = poll_one()
        if type(result) is not int or result not in (0, 1):
            raise AssertionError("LOAD01 relay poll did not return one bounded delivery count")
        poll_count += 1
        drained += result
        if result:
            continue

        observed = terminal_state()
        if clock() >= deadline_monotonic:
            raise TimeoutError("LOAD01 relay terminal snapshot exceeded the drain deadline")
        if not isinstance(observed, Mapping) or set(observed) != set(source_rows):
            raise AssertionError("LOAD01 relay terminal stream set changed")
        for stream, expected_rows in source_rows.items():
            if clock() >= deadline_monotonic:
                raise TimeoutError("LOAD01 relay terminal validation exceeded the drain deadline")
            state = observed[stream]
            if not isinstance(state, Mapping):
                raise AssertionError("LOAD01 relay terminal cursor row is invalid")
            rows = state.get("source_rows")
            source_sequence = state.get("source_sequence")
            checkpoint_sequence = state.get("checkpoint_sequence")
            if (
                type(rows) is not int
                or type(source_sequence) is not int
                or type(checkpoint_sequence) is not int
                or rows != expected_rows
                or source_sequence < 0
                or checkpoint_sequence != source_sequence
            ):
                raise AssertionError("LOAD01 relay source and checkpoint cursors differ")
        delivered = already_delivered + drained
        if delivered != expected_delivered:
            raise AssertionError("LOAD01 relay delivered count does not match source rows")
        return {
            "poll_count": poll_count,
            "delivered": delivered,
            "source_rows": dict(source_rows),
        }


def _load01_project_owner_close_facts_for_report(
    owner_close_facts: dict[str, Any],
) -> dict[str, Any]:
    """Project worker close facts to the finite JSON report contract.

    The in-process source evidence retains its typed ``WireScope`` for identity
    checks. Reports carry the separately checked scope key/generation/counts and
    terminal cursors, rather than serializing that redundant protocol object.
    """

    if type(owner_close_facts) is not dict:
        raise TypeError("LOAD01 owner close facts must be a dict")
    raw_workers = owner_close_facts.get("relay_workers_by_scope")
    if type(raw_workers) is not dict:
        raise TypeError("LOAD01 relay worker close facts must be a dict")

    workers: dict[str, dict[str, Any]] = {}
    for scope_index, raw_worker in raw_workers.items():
        if type(scope_index) is not str or type(raw_worker) is not dict:
            raise TypeError("LOAD01 relay worker report row is invalid")
        raw_source = raw_worker.get("source_evidence")
        if type(raw_source) is not dict:
            raise TypeError("LOAD01 relay worker source evidence is missing")
        source_rows_by_stream = raw_source.get("source_rows_by_stream")
        source_cursor_by_stream = raw_source.get("source_cursor_by_stream")
        source_scope_key = raw_source.get("source_scope_key")
        source_generation = raw_source.get("source_generation")
        source_rows = raw_source.get("source_rows")
        relay_delivered = raw_source.get("relay_delivered")
        scope_ref = raw_source.get("scope_ref")
        source_event_digest = raw_source.get("source_event_digest")
        invalid_source_fields = []
        if type(scope_ref) is not str:
            invalid_source_fields.append("scope_ref")
        if type(source_scope_key) is not str:
            invalid_source_fields.append("source_scope_key")
        if type(source_generation) is not str:
            invalid_source_fields.append("source_generation")
        if type(source_rows) is not int or source_rows < 0:
            invalid_source_fields.append("source_rows")
        if type(relay_delivered) is not int or relay_delivered != source_rows:
            invalid_source_fields.append("relay_delivered")
        if type(source_event_digest) is not str:
            invalid_source_fields.append("source_event_digest")
        if type(source_rows_by_stream) is not dict:
            invalid_source_fields.append("source_rows_by_stream")
        elif any(
            type(stream) is not str or type(count) is not int or count < 0
            for stream, count in source_rows_by_stream.items()
        ):
            invalid_source_fields.append("source_rows_by_stream_values")
        elif type(source_rows) is int and sum(source_rows_by_stream.values()) != source_rows:
            invalid_source_fields.append("source_rows_by_stream_total")
        if type(source_cursor_by_stream) is not dict:
            invalid_source_fields.append("source_cursor_by_stream")
        elif type(source_rows_by_stream) is dict and set(source_cursor_by_stream) != set(
            source_rows_by_stream
        ):
            invalid_source_fields.append("source_cursor_by_stream_keys")
        if invalid_source_fields:
            raise TypeError(
                "LOAD01 relay worker source identity projection is invalid: "
                + ",".join(invalid_source_fields)
            )
        cursors: dict[str, dict[str, int]] = {}
        for stream, cursor_row in source_cursor_by_stream.items():
            if (
                type(stream) is not str
                or type(cursor_row) is not dict
                or type(cursor_row.get("source_sequence")) is not int
                or type(cursor_row.get("checkpoint_sequence")) is not int
                or cursor_row["source_sequence"] != cursor_row["checkpoint_sequence"]
                or cursor_row["source_sequence"] < 0
            ):
                raise TypeError("LOAD01 relay worker source cursor projection is invalid")
            cursors[stream] = {
                "source_sequence": cursor_row["source_sequence"],
                "checkpoint_sequence": cursor_row["checkpoint_sequence"],
            }

        source_projection = {
            field: raw_source[field]
            for field in (
                "scope_ref",
                "source_scope_key",
                "source_generation",
                "source_rows",
                "source_rows_by_stream",
                "source_event_digest",
                "relay_delivered",
            )
        }
        source_projection["source_cursor_by_stream"] = cursors
        for field in (
            "account_ref",
            "strategy_index",
            "strategy_ref",
            "provider",
            "environment",
            "outbox_scope",
        ):
            if field in raw_source:
                source_projection[field] = raw_source[field]

        worker_projection = {
            field: raw_worker[field]
            for field in (
                "account_index",
                "local_index",
                "scope_ref",
                "delivered",
                "drain",
                "relay_runtime",
                "reader_closed",
                "relay_closed",
                "timing",
                "cleanup_errors",
            )
        }
        if worker_projection["scope_ref"] != scope_ref:
            raise AssertionError("LOAD01 relay worker scope identity differs in report projection")
        worker_projection["source_evidence"] = source_projection
        workers[scope_index] = worker_projection

    projected = {
        field: value
        for field, value in owner_close_facts.items()
        if field != "relay_workers_by_scope"
    }
    projected["relay_workers_by_scope"] = workers
    # This validates the entire persisted payload while preserving its exact
    # built-in values; unsupported typed objects fail closed instead of stringifying.
    json.dumps(projected, sort_keys=True, allow_nan=False)
    return projected


def _load01_diagnostic_error_facts(phase: str, error: BaseException) -> dict[str, str]:
    return {
        "phase": phase,
        "type": f"{type(error).__module__}.{type(error).__qualname__}",
        "code": _safe_diagnostic_code(error),
    }


def _load01_close_facts_for_failure_report(close_facts) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    try:
        raw_facts = close_facts()
        return _load01_project_owner_close_facts_for_report(raw_facts), None
    except BaseException as error:
        return None, _load01_diagnostic_error_facts("close-facts-projection", error)


def _load01_owner_failure_record(
    error: BaseException,
    rpc_timing: Any,
    close_facts,
    failure_path: Path,
) -> dict[str, Any]:
    exception_chain: list[dict[str, Any]] = []
    current_error: BaseException | None = error
    seen_errors: set[int] = set()
    while isinstance(current_error, BaseException) and id(current_error) not in seen_errors:
        seen_errors.add(id(current_error))
        try:
            message = str(current_error)
        except BaseException:
            message = "<message-unavailable>"
        exception_chain.append(
            {
                "type": f"{type(current_error).__module__}.{type(current_error).__qualname__}",
                "code": _safe_diagnostic_code(current_error),
                "message": message,
            }
        )
        current_error = current_error.__cause__ or current_error.__context__
    try:
        formatted_traceback = "".join(traceback.format_exception(error))
    except BaseException:
        formatted_traceback = ""

    projected_close_facts, projection_error = _load01_close_facts_for_failure_report(
        close_facts
    )
    failure_facts: dict[str, Any] = {
        "exception_chain": exception_chain,
        "traceback": formatted_traceback,
        "close_facts_before_exception_cleanup": projected_close_facts,
    }
    if projection_error is not None:
        failure_facts["close_facts_projection_error"] = projection_error
    failure_record: dict[str, Any] = {
        "evidence_level": "SOURCE_FAKE_SINGLE_OWNER_REAL_HOST_RELAY_OUTBOX_ONLY",
        "failure": failure_facts,
        "rpc_timing_at_failure": rpc_timing,
    }
    try:
        encoded = json.dumps(failure_record, sort_keys=True, indent=2, allow_nan=False) + "\n"
    except BaseException as serialization_error:
        failure_facts["diagnostic_artifact_serialization_error"] = (
            _load01_diagnostic_error_facts("failure-record-serialization", serialization_error)
        )
        # The bounded error chain and projected close facts are the primary
        # evidence. Drop only the optional timing payload for a best-effort retry.
        failure_record["rpc_timing_at_failure"] = None
        try:
            encoded = json.dumps(
                failure_record, sort_keys=True, indent=2, allow_nan=False
            ) + "\n"
        except BaseException:
            encoded = None
    if encoded is not None:
        try:
            failure_path.write_text(encoded, encoding="utf-8")
        except BaseException as write_error:
            failure_facts["diagnostic_artifact_write_error"] = _load01_diagnostic_error_facts(
                "failure-record-write", write_error
            )
    return failure_record


def _load01_send_owner_failure(
    control: Any,
    account_index: int,
    error: BaseException,
    owner_diagnostic: dict[str, Any],
) -> None:
    try:
        error_code = getattr(error, "code", type(error).__name__)
    except BaseException:
        error_code = type(error).__name__
    with suppress(BaseException):
        control.send(
            ("error", "owner", account_index, error_code, owner_diagnostic)
        )


def _load01_timing_summary(samples: Sequence[int], total_ns: int, count: int) -> dict[str, int]:
    ordered = sorted(samples)

    def percentile(fraction: float) -> int:
        if not ordered:
            return 0
        index = max(0, min(len(ordered) - 1, ceil(fraction * len(ordered)) - 1))
        return int(ordered[index])

    return {
        "count": int(count),
        "total_ns": int(total_ns),
        "max_ns": max(ordered, default=0),
        "p50_ns": percentile(0.50),
        "p95_ns": percentile(0.95),
        "sample_count": len(ordered),
        "sample_capacity": _RELAY_POLL_TIMING_SAMPLE_CAPACITY,
    }


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
    send = socket_type.send
    request = service_v2.ZmqServiceClient.request
    client_maintain = ServiceClientRuntime.maintain
    if process_role == "strategy":
        send_multipart = receive = server_init = None
        host_maintain = component_maintain = None
    else:
        send_multipart = socket_type.send_multipart
        receive = service_v2._BoundedMultipartReceiver.receive
        server_init = service_v2.ZmqServiceServer.__init__
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
            active_trace = getattr(_LOAD01_RPC_TRACE_CONTEXT, "trace", trace)
            try:
                identity = getattr(owner, "identity", None)
                if identity is None:
                    identity = getattr(getattr(owner, "_authority", None), "identity", None)
                service = _bounded_diagnostic_text(getattr(identity, "actor_ref", None)) or "unknown"
            except BaseException:
                service = "unknown"
            start = active_trace.now()
            try:
                return original(owner, *args, **kwargs)
            finally:
                active_trace.maintain(f"{label}:{service}", start, active_trace.now())
        return wrapper

    patches = [
        (socket_type, "send", send, send_wrapper),
        (service_v2.ZmqServiceClient, "request", request, request_wrapper),
        (ServiceClientRuntime, "maintain", client_maintain,
         timed_maintain(client_maintain, "service_runtime.maintain")),
    ]
    if process_role != "strategy":
        patches.extend(
            (
                (socket_type, "send_multipart", send_multipart, send_multipart_wrapper),
                (service_v2._BoundedMultipartReceiver, "receive", receive, receive_wrapper),
                (service_v2.ZmqServiceServer, "__init__", server_init, server_init_wrapper),
                (ServiceHost, "maintain", host_maintain,
                 timed_maintain(host_maintain, "service_host.maintain")),
                (ServiceHost, "_maintain_components", component_maintain,
                 timed_maintain(component_maintain, "service_host._maintain_components")),
            )
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
                    "options": {
                        "role": "market_hub",
                        "bounded_read_after": True,
                    },
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


def _execution_service_config(
    service: dict[str, Any], *, group_owner: bool = False
) -> dict[str, Any]:
    index = service["index"]
    execution_instance_id = service["instance_id"]
    components: dict[str, Any] = {
        "execution": {
            "config_schema_version": 2,
            "settings": {
                "instance_id": execution_instance_id,
                "bind_ports": ["service.order_entry", "service.order_query"],
                "options": {
                    "role": "account_group_owner" if group_owner else "account_owner"
                },
            },
        }
    }
    if group_owner:
        components["risk"] = {
            "config_schema_version": 2,
            "settings": {
                "instance_id": f"{execution_instance_id}-group-reservations",
                "bind_ports": ["service.group_reservations"],
                "options": {"role": "group_reservations"},
            },
        }
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": service["service_id"],
        "scope_ref": f"load01-execution-service-root-{index:02d}-v1",
        "transport_ref": service["listener_ref"],
        "registration_policy_ref": f"load01-execution-policy-{index:02d}-v1",
        "service_requirements_ref": f"load01-execution-requirements-{index:02d}-v1",
        "components": components,
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
    local_index: int,
) -> dict[str, Any]:
    client_ref = f"{_RELAY_CLIENT_PREFIX}{account_index:02d}-{local_index:02d}-v1"
    bindings = [
        {
            "mount": "upstream.telemetry",
            "service_id": _DURABLE_ID,
            "instance_id": _DURABLE_INSTANCE,
            "scope_ref": _durable_scope_ref(account_index, local_index),
            "transport_ref": client_ref,
        }
    ]
    profiles: dict[str, Any] = {
        f"owner-relay-unused-listener-{account_index:02d}-{local_index:02d}-v1": {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_bind": unused_listener_endpoint},
            "security_profile_ref": f"owner-relay-unused-server-{account_index:02d}-{local_index:02d}-v1",
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
        "service_id": f"load01-owner-relay-config-{account_index:02d}-{local_index:02d}",
        "scope_ref": f"load01-owner-relay-config-scope-{account_index:02d}-{local_index:02d}-v1",
        "transport_ref": f"owner-relay-unused-listener-{account_index:02d}-{local_index:02d}-v1",
        "registration_policy_ref": f"load01-owner-relay-config-policy-{account_index:02d}-{local_index:02d}",
        "service_requirements_ref": f"load01-owner-relay-config-req-{account_index:02d}-{local_index:02d}",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": f"owner-relay-unused-monitor-{account_index:02d}-{local_index:02d}",
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


def _load01_owner_relay_worker(
    account_index: int,
    local_index: int,
    scope_record: Mapping[str, Any],
    execution_store_path: str,
    checkpoint_directory: str,
    unused_relay_endpoint: str,
    abort_event: threading.Event,
    command_queue: queue.Queue,
    event_queue: queue.Queue,
) -> None:
    """Own one source reader, ZMQ runtime, relay and checkpoint on this thread."""

    phase = "reader-open"
    reader = None
    runtime = None
    relay = None
    opened_binding_count = 0
    worker_trace = _Load01RpcTrace(f"owner-relay-{account_index:02d}-{local_index:02d}")
    prior_trace = getattr(_LOAD01_RPC_TRACE_CONTEXT, "trace", None)
    _LOAD01_RPC_TRACE_CONTEXT.trace = worker_trace
    cleanup_errors: list[dict[str, str]] = []
    poll_samples: list[int] = []
    sink_samples: list[int] = []
    poll_count = poll_total_ns = sink_call_count = sink_total_ns = delivered = 0

    def emit(message: tuple[Any, ...]) -> None:
        event_queue.put(message)

    def cleanup_one(name: str, operation) -> None:
        try:
            operation()
        except BaseException as error:
            cleanup_errors.append(
                {"phase": name, "code": _safe_diagnostic_code(error)}
            )

    try:
        strategy_index = int(scope_record["strategy_index"])
        scope_provider = "fixture" if scope_record.get("provider") == "fixture" else "synthetic"
        scope_environment = "simulation" if scope_record.get("environment") == "simulation" else "paper"
        execution_scope = ExecutionScope(
            scope_provider,
            scope_environment,
            str(scope_record["account_ref"]),
            _strategy_ref(strategy_index),
            _EXECUTION_DAY,
        )
        reader = ExecutionJournalReader(execution_store_path, scope=execution_scope)
        if (
            (
                scope_record.get("source_generation") is not None
                and reader.generation != scope_record.get("source_generation")
            )
            or (
                scope_record.get("source_scope_key") is not None
                and execution_scope.key != scope_record.get("source_scope_key")
            )
        ):
            raise AssertionError("LOAD01 relay reader source identity changed")
        scope_record = {
            **dict(scope_record),
            "source_generation": reader.generation,
            "source_scope_key": execution_scope.key,
            "outbox_scope": _relay_outbox_scope(reader.generation, execution_scope.key),
        }
        emit(("source-ready", local_index, scope_record))

        command = None
        while command is None:
            if abort_event.is_set():
                raise RuntimeError("LOAD01 relay worker aborted before connect")
            try:
                command = command_queue.get(timeout=0.05)
            except queue.Empty:
                continue
        if command == ("abort",):
            raise RuntimeError("LOAD01 relay worker aborted before connect")
        if not isinstance(command, tuple) or len(command) != 4 or command[0] != "connect":
            raise AssertionError("LOAD01 relay worker received an invalid connect command")

        phase = "runtime-start"
        _, monitor_endpoint, monitor_server_public, relay_identity = command
        relay_principal, relay_actor_ref, relay_public, relay_secret = relay_identity
        relay_config_raw = _owner_relay_config(
            account_index,
            monitor_endpoint,
            unused_relay_endpoint,
            local_index=local_index,
        )
        relay_config = parse_service_config(relay_config_raw)
        relay_identity_obj = WireIdentity(WireRole.SERVICE, relay_principal, relay_actor_ref)
        monitor_peer = WireIdentity(WireRole.SERVICE, _DURABLE_PRINCIPAL, _DURABLE_ID)
        scope_ref = str(scope_record["scope_ref"])
        wire_scope = scope_record["wire_scope"]
        runtime = ServiceClientRuntime(
            relay_config,
            identity=relay_identity_obj,
            scope_resolver=lambda ref: wire_scope if ref == scope_ref else (_ for _ in ()).throw(KeyError(ref)),
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
        runtime.start()
        original_call = runtime.call

        def measured_runtime_call(*args, **kwargs):
            nonlocal sink_call_count, sink_total_ns
            started = time.perf_counter_ns()
            try:
                return original_call(*args, **kwargs)
            finally:
                elapsed = max(0, time.perf_counter_ns() - started)
                sink_call_count += 1
                sink_total_ns += elapsed
                if len(sink_samples) < _RELAY_POLL_TIMING_SAMPLE_CAPACITY:
                    sink_samples.append(elapsed)

        runtime.call = measured_runtime_call
        phase = "relay-open"
        relay = ServiceExecutionMonitorRelay(
            runtime,
            reader,
            scope_ref=scope_ref,
            checkpoint_path=Path(checkpoint_directory)
            / f"relay-{account_index:02d}-{local_index:02d}-checkpoint.sqlite3",
        )
        if len(runtime.bindings) != 1 or not all(binding.active for binding in runtime.bindings):
            raise AssertionError("LOAD01 relay worker did not register exactly one binding")
        opened_binding_count = len(runtime.bindings)
        emit(
            (
                "connected",
                local_index,
                {
                    "account_index": account_index,
                    "scope_ref": scope_ref,
                    "runtime_count": 1,
                    "binding_count": len(runtime.bindings),
                    "runtime_state": runtime.state,
                },
            )
        )

        phase = "relay-poll"
        last_maintain = time.monotonic()
        drain_deadline = None

        def maintain_if_due(*, enforce_drain_deadline: bool = False) -> None:
            nonlocal last_maintain
            now = time.monotonic()
            if now - last_maintain < 0.5:
                return
            if (
                enforce_drain_deadline
                and drain_deadline is not None
                and drain_deadline - now <= _RELAY_DRAIN_RPC_HEADROOM_SECONDS
            ):
                raise TimeoutError("LOAD01 relay maintenance would consume drain RPC headroom")
            runtime.maintain()
            last_maintain = time.monotonic()

        def poll_one(*, enforce_drain_deadline: bool = False) -> int:
            nonlocal poll_count, poll_total_ns
            maintain_if_due(enforce_drain_deadline=enforce_drain_deadline)
            if (
                enforce_drain_deadline
                and (
                    drain_deadline is None
                    or drain_deadline - time.monotonic()
                    <= _RELAY_DRAIN_RPC_HEADROOM_SECONDS
                )
            ):
                raise TimeoutError("LOAD01 relay poll would consume drain RPC headroom")
            started = time.perf_counter_ns()
            try:
                result = relay.poll(limit=1)
                return int(result.remote_delivered)
            finally:
                elapsed = max(0, time.perf_counter_ns() - started)
                poll_count += 1
                poll_total_ns += elapsed
                if len(poll_samples) < _RELAY_POLL_TIMING_SAMPLE_CAPACITY:
                    poll_samples.append(elapsed)

        while True:
            if abort_event.is_set():
                raise RuntimeError("LOAD01 relay worker aborted")
            try:
                command = command_queue.get_nowait()
            except queue.Empty:
                command = None
            if command == ("abort",):
                raise RuntimeError("LOAD01 relay worker aborted")
            if isinstance(command, tuple) and command and command[0] == "drain":
                if len(command) != 2 or drain_deadline is not None:
                    raise AssertionError("LOAD01 relay worker received an invalid drain command")
                drain_deadline = float(command[1])
                source_before = _reader_source_evidence(reader)
                fixed_source_rows = dict(source_before["source_rows_by_stream"])
                terminal_cursor_snapshot: dict[str, dict[str, int]] = {}

                def terminal_state() -> dict[str, dict[str, int]]:
                    state: dict[str, dict[str, int]] = {}
                    for stream in sorted(fixed_source_rows):
                        cursor = 0
                        rows = 0
                        while True:
                            if time.monotonic() >= drain_deadline:
                                raise TimeoutError(
                                    "LOAD01 relay terminal source scan exceeded the drain deadline"
                                )
                            page = reader.read_page(stream, after_sequence=cursor, limit=1000)
                            if not page.events:
                                break
                            rows += len(page.events)
                            cursor = page.events[-1].sequence
                            if len(page.events) < 1000:
                                break
                        state[stream] = {
                            "source_rows": rows,
                            "source_sequence": cursor,
                            "checkpoint_sequence": relay.checkpoint(stream).sequence,
                        }
                    terminal_cursor_snapshot.clear()
                    terminal_cursor_snapshot.update(
                        {
                            stream: {
                                "source_sequence": row["source_sequence"],
                                "checkpoint_sequence": row["checkpoint_sequence"],
                            }
                            for stream, row in state.items()
                        }
                    )
                    return state

                drain_facts = _load01_drain_relay_until_empty(
                    lambda: poll_one(enforce_drain_deadline=True),
                    terminal_state,
                    fixed_source_rows,
                    sum(fixed_source_rows.values()),
                    drain_deadline,
                    already_delivered=delivered,
                )
                delivered = drain_facts["delivered"]
                source_after = _reader_source_evidence(reader)
                if source_after != source_before:
                    raise AssertionError("LOAD01 relay source journal changed during drain")
                break

            increment = poll_one()
            delivered += increment
            if increment:
                try:
                    event_queue.put_nowait(("progress", local_index, delivered))
                except queue.Full:
                    # Progress is sampled; the final worker receipt is exact.
                    pass
            else:
                try:
                    command = command_queue.get(timeout=_RELAY_IDLE_POLL_INTERVAL_SECONDS)
                except queue.Empty:
                    command = None
                if command is not None:
                    # The loop handles the command before starting another poll.
                    command_queue.put(command)

        phase = "relay-close"
        relay.close()
        reader.close()
        runtime.close()
        relay_facts = {
            "state": runtime.state,
            "cleanup_failures": runtime.cleanup_failures,
            "unregister_uncertainties": runtime.unregister_uncertainties,
            "unknown_registration_count": runtime.unknown_registration_count,
            "binding_count": len(runtime.bindings),
            "opened_binding_count": opened_binding_count,
            "active_binding_count": sum(bool(binding.active) for binding in runtime.bindings),
            "worker_thread_id": threading.get_ident(),
            "worker_joined": False,
        }
        facts = {
            "account_index": account_index,
            "local_index": local_index,
            "scope_ref": scope_ref,
            "delivered": delivered,
            "drain": drain_facts,
            "source_evidence": {
                **dict(scope_record),
                **source_after,
                    "source_cursor_by_stream": dict(terminal_cursor_snapshot),
                "relay_delivered": delivered,
            },
            "relay_runtime": relay_facts,
            "reader_closed": True,
            "relay_closed": True,
            "timing": {
                "relay_poll": _load01_timing_summary(poll_samples, poll_total_ns, poll_count),
                "sink_runtime_call": _load01_timing_summary(
                    sink_samples, sink_total_ns, sink_call_count
                ),
                "maintenance_durations": worker_trace.snapshot(1)["maintenance_durations"],
            },
            "cleanup_errors": cleanup_errors,
        }
        if (
            cleanup_errors
            or runtime.state != "closed"
            or relay_facts["active_binding_count"] != 0
            or relay_facts["unknown_registration_count"] != 0
            or relay_facts["cleanup_failures"]
            or relay_facts["unregister_uncertainties"]
        ):
            raise RuntimeError("LOAD01 relay worker close was not clean")
        emit(("completed", local_index, facts))
    except BaseException as error:
        phase_code = _safe_diagnostic_code(error)
        cleanup_one("relay-close", lambda: relay.close() if relay is not None else None)
        cleanup_one("reader-close", lambda: reader.close() if reader is not None else None)
        cleanup_one(
            "runtime-close",
            lambda: runtime.close()
            if runtime is not None and runtime.state != "closed"
            else None,
        )
        try:
            runtime_failure_facts = (
                {
                    "state": runtime.state,
                    "cleanup_failures": runtime.cleanup_failures,
                    "unregister_uncertainties": runtime.unregister_uncertainties,
                    "unknown_registration_count": runtime.unknown_registration_count,
                    "binding_count": len(runtime.bindings),
                    "opened_binding_count": opened_binding_count,
                    "active_binding_count": sum(
                        bool(binding.active) for binding in runtime.bindings
                    ),
                    "worker_thread_id": threading.get_ident(),
                    "worker_joined": False,
                }
                if runtime is not None
                else None
            )
        except BaseException:
            runtime_failure_facts = {"snapshot_available": False}
        try:
            trace_failure_facts = {
                "relay_poll": _load01_timing_summary(
                    poll_samples, poll_total_ns, poll_count
                ),
                "sink_runtime_call": _load01_timing_summary(
                    sink_samples, sink_total_ns, sink_call_count
                ),
                "maintenance_durations": worker_trace.snapshot(1)["maintenance_durations"],
            }
        except BaseException:
            trace_failure_facts = {"snapshot_available": False}
        try:
            emit(
                (
                    "error",
                    local_index,
                    {
                        "phase": phase,
                        "error_type": f"{type(error).__module__}.{type(error).__qualname__}",
                        "code": phase_code,
                        "delivered": delivered,
                        "runtime": runtime_failure_facts,
                        "timing": trace_failure_facts,
                        "cleanup_errors": cleanup_errors,
                    },
                )
            )
        except BaseException:
            pass
    finally:
        if prior_trace is None:
            with suppress(AttributeError):
                del _LOAD01_RPC_TRACE_CONTEXT.trace
        else:
            _LOAD01_RPC_TRACE_CONTEXT.trace = prior_trace


_N5_PORTFOLIO_REF = "load01-n5-portfolio-v1"
_N5_GROUP_RPC_TARGETS_MS = {
    operation: {
        "observational_percentiles": ["p50", "p95"],
        "p99_max": 5_000.0,
        "single_call_timeout_ms": 5_000.0,
        "unexpected_timeout_count_max": 0,
    }
    for operation in ("groups.submit", "groups.query")
}


def _load01_n5_group_service_binding(
    coordinator_service: Mapping[str, Any],
    *,
    strategy_index: int,
    account_index: int,
) -> dict[str, str]:
    """Validate and select the one frozen coordinator route for this strategy."""

    if (
        not isinstance(coordinator_service, Mapping)
        or type(strategy_index) is not int
        or type(account_index) is not int
    ):
        raise ValueError("LOAD01 N5 coordinator route is not a typed service record")
    required_service_text = (
        "service_id",
        "instance_id",
        "endpoint",
        "security_ref",
        "server_public",
    )
    if any(
        type(coordinator_service.get(name)) is not str
        or not coordinator_service[name]
        for name in required_service_text
    ) or coordinator_service.get("peer") is None:
        raise ValueError("LOAD01 N5 coordinator service identity is incomplete")
    bindings = coordinator_service.get("strategy_bindings")
    if not isinstance(bindings, Mapping):
        raise ValueError("LOAD01 N5 coordinator strategy bindings are missing")
    selected = bindings.get(strategy_index)
    if not isinstance(selected, Mapping):
        raise ValueError("LOAD01 N5 coordinator has no binding for this strategy")
    scope_ref = selected.get("scope_ref")
    portfolio_ref = selected.get("portfolio_ref")
    owner_run_id = selected.get("origin_strategy_run_id")
    if (
        type(scope_ref) is not str
        or scope_ref != f"load01-n5-group-scope-a{account_index:02d}-v1"
        or type(portfolio_ref) is not str
        or portfolio_ref != _N5_PORTFOLIO_REF
        or type(owner_run_id) is not str
        or owner_run_id != f"load01-n5-owner-run-a{account_index:02d}-v1"
    ):
        raise ValueError("LOAD01 N5 coordinator scope, portfolio, or owner run binding is invalid")
    return {
        "scope_ref": scope_ref,
        "portfolio_ref": portfolio_ref,
        "owner_run_id": owner_run_id,
    }


def _load01_validate_n5_group_metadata(metadata: object) -> tuple[InstrumentRiskMetadata, ...]:
    """Validate the parent-injected synthetic metadata without re-aging it."""

    if (
        type(metadata) is not tuple
        or len(metadata) != len(N5_LEG_SPECS)
        or any(type(item) is not InstrumentRiskMetadata for item in metadata)
    ):
        raise ValueError("LOAD01 N5 group metadata must be the parent tuple of three typed rows")
    now_ns = time.time_ns()
    timestamps = set()
    for spec, item in zip(N5_LEG_SPECS, metadata, strict=True):
        if (
            type(item.instrument) is not str
            or item.instrument != spec.instrument
            or type(item.metadata_version) is not str
            or item.metadata_version != f"load01-n5-{spec.instrument.lower()}-v1"
            or type(item.as_of_ns) is not int
            or item.as_of_ns <= 0
            or item.as_of_ns > now_ns
            or type(item.expires_at_ns) is not int
            or item.expires_at_ns <= now_ns
            or item.expires_at_ns <= item.as_of_ns
            or type(item.tick_size) is not Decimal
            or item.tick_size != Decimal("0.01")
            or type(item.quantity_step) is not Decimal
            or item.quantity_step != spec.quantity_step
            or type(item.contract_multiplier) is not Decimal
            or item.contract_multiplier != spec.contract_multiplier
            or type(item.max_gross_notional) is not Decimal
            or item.max_gross_notional != Decimal("100000")
            or type(item.taker_fee_bps) is not Decimal
            or item.taker_fee_bps != spec.taker_fee_bps
            or type(item.fixed_fee) is not Decimal
            or item.fixed_fee != spec.fixed_fee
            or type(item.max_slippage_bps) is not Decimal
            or item.max_slippage_bps != spec.max_slippage_bps
            or type(item.quantity_unit) is not str
            or item.quantity_unit != N5_GROUP_QUANTITY_UNIT
            or type(item.valuation_unit) is not str
            or item.valuation_unit != "USD"
            or type(item.quote_currency) is not str
            or item.quote_currency != "USD"
            or item.fx_source_ref is not None
            or item.fixed_fee_unit is not None
            or item.min_quantity is not None
            or item.max_quantity is not None
            or item.underlying_ref is not None
        ):
            raise ValueError("LOAD01 N5 group metadata differs from the fixed synthetic template")
        timestamps.add((item.as_of_ns, item.expires_at_ns))
    if len(timestamps) != 1:
        raise ValueError("LOAD01 N5 group metadata rows do not share one parent capture window")
    return metadata


def _strategy_config(
    index: int,
    account_service: dict[str, Any] | dict[int, dict[str, Any]],
    execution_service: dict[str, Any] | dict[int, dict[str, Any]],
    market_rpc: str,
    market_pub: str,
    telemetry_rpc: str,
    *,
    account_index: int | None = None,
    profile_id: str = "U0",
    coordinator_service: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    profile = load01_profile(profile_id)
    if account_index is None:
        account_index = profile.accounts_for_strategy(index)[0]
    if "service_id" in account_service:
        account_services = {account_index: account_service}
        execution_services = {account_index: execution_service}
        account_indices = (account_index,)
    else:
        account_services = account_service
        execution_services = execution_service
        account_indices = profile.accounts_for_strategy(index)
        if tuple(sorted(account_services)) != tuple(sorted(account_indices)) or tuple(
            sorted(execution_services)
        ) != tuple(sorted(account_indices)):
            raise ValueError("LOAD01 strategy services do not match its account routes")
    market_transport = _MARKET_CLIENT_REF
    telemetry_transport = "load01-telemetry-client-v1"
    market_binding = {
        "mount": "store.market",
        "service_id": _MARKET_ID,
        "instance_id": _MARKET_INSTANCE,
        "scope_ref": _MARKET_SCOPE_REF,
        "transport_ref": market_transport,
    }
    account_bindings = []
    execution_bindings = []
    telemetry_bindings = []
    group_binding = None
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
        telemetry_transport: {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": telemetry_rpc},
            "security_profile_ref": _TELEMETRY_SECURITY_REF,
            "options": {"request_timeout_ms": _RPC_TIMEOUT_MS},
        },
    }
    for route_account_index in account_indices:
        route_account_service = account_services[route_account_index]
        route_execution_service = execution_services[route_account_index]
        route_account_ref = _ACCOUNT_REFS[route_account_index]
        account_transport = f"{_ACCOUNT_CLIENT_PREFIX}{route_account_index:02d}-v1"
        execution_transport = f"load01-execution-client-{route_account_index:02d}-v1"
        account_bindings.append(
            {
                "mount": "store.account",
                "service_id": route_account_service["service_id"],
                "instance_id": route_account_service["instance_id"],
                "scope_ref": route_account_service["scope_ref"],
                "account_ref": route_account_ref,
                "transport_ref": account_transport,
            }
        )
        execution_bindings.extend(
            (
                {
                    "mount": "broker.orders",
                    "service_id": route_execution_service["service_id"],
                    "instance_id": route_execution_service["instance_id"],
                    "scope_ref": route_execution_service["order_scope_ref"],
                    "account_ref": route_account_ref,
                    "transport_ref": execution_transport,
                },
                {
                    "mount": "broker.order_query",
                    "service_id": route_execution_service["service_id"],
                    "instance_id": route_execution_service["instance_id"],
                    "scope_ref": route_execution_service["query_scope_ref"],
                    "account_ref": route_account_ref,
                    "transport_ref": execution_transport,
                },
            )
        )
        telemetry_scope_ref = _strategy_telemetry_scope_ref(
            profile_id, index, route_account_index
        )
        telemetry_bindings.append(
            {
                "mount": "runtime.telemetry",
                "service_id": _TELEMETRY_ID,
                "instance_id": _TELEMETRY_INSTANCE,
                "scope_ref": telemetry_scope_ref,
                "transport_ref": telemetry_transport,
                "required": False,
            }
        )
        profiles[account_transport] = {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": route_account_service["endpoint"]},
            "security_profile_ref": route_account_service["security_ref"],
            "options": {"request_timeout_ms": _RPC_TIMEOUT_MS},
        }
        profiles[execution_transport] = {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": route_execution_service["endpoint"]},
            "security_profile_ref": route_execution_service["security_ref"],
            "options": {"request_timeout_ms": _RPC_TIMEOUT_MS},
        }
    profile = load01_profile(profile_id)
    if profile_id == "N5" and index in profile.expected_group_capable_strategies:
        selected_group_route = _load01_n5_group_service_binding(
            coordinator_service,
            strategy_index=index,
            account_index=account_index,
        )
        group_transport = f"load01-n5-group-client-{index:03d}-v1"
        group_binding = {
            "mount": "broker.order_groups",
            "service_id": coordinator_service["service_id"],
            "instance_id": coordinator_service["instance_id"],
            "scope_ref": selected_group_route["scope_ref"],
            "portfolio_ref": selected_group_route["portfolio_ref"],
            "transport_ref": group_transport,
        }
        profiles[group_transport] = {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": coordinator_service["endpoint"]},
            "security_profile_ref": coordinator_service["security_ref"],
            "options": {"request_timeout_ms": _RPC_TIMEOUT_MS},
        }
        execution_bindings.append(group_binding)
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": _strategy_ref(index)},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [market_binding, *account_bindings],
                    "options": {
                        "request_timeout_ms": _RPC_TIMEOUT_MS,
                        "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
                    },
                },
            },
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": execution_bindings,
                    "options": {"request_timeout_ms": _RPC_TIMEOUT_MS},
                },
            },
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": telemetry_bindings,
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
            _ACCOUNT_REFS[profile.accounts_for_strategy(index)[0]],
        )
        for index in range(_STRATEGIES)
    )


def _sample_intent(
    intent_id: str,
    *,
    instrument: str = "LOAD01-USD",
    market_stream: str | None = None,
    lifecycle: str | None = None,
) -> dict[str, Any]:
    tags = {"scenario": "LOAD01", "synthetic": "true"}
    if market_stream is not None or lifecycle is not None:
        if (
            type(market_stream) is not str
            or not market_stream
            or lifecycle not in {"fill", "cancel"}
        ):
            raise ValueError("LOAD01 N4 intents require a stream and lifecycle tag")
        tags[_N4_MARKET_STREAM_TAG] = market_stream
        tags[_N4_LIFECYCLE_TAG] = lifecycle
    return {
        "intent_id": intent_id,
        "signal_id": f"signal-{intent_id}",
        "instrument": instrument,
        "side": "BUY",
        "position_effect": "OPEN",
        "order_type": "LIMIT",
        "quantity": "1",
        "price": "1",
        "time_in_force": "GTC",
        "reduce_only": False,
        "metadata_version": "load01-synthetic-v1",
        "deadline_ns": None,
        "tags": tags,
    }


class _SyntheticOrderProvider:
    def __init__(self) -> None:
        self.submitted = 0
        self.cancelled = 0
        self.filled = 0
        self._store: SqliteExecutionStore | None = None
        self._n4_market_facts: dict[tuple[str, str, str], Counter[str]] = {}
        self._n4_active: dict[
            str, tuple[tuple[str, str, str], str, str]
        ] = {}
        self._n4_terminal_fills: dict[str, tuple[str, str, str]] = {}

    def attach_store(self, store: SqliteExecutionStore) -> None:
        if type(store) is not SqliteExecutionStore or self._store is not None:
            raise AssertionError("LOAD01 synthetic provider store binding is invalid")
        self._store = store

    def _n4_context(self, intent: OrderIntent) -> tuple[tuple[str, str, str], str] | None:
        stream_id = intent.tags.get(_N4_MARKET_STREAM_TAG)
        lifecycle = intent.tags.get(_N4_LIFECYCLE_TAG)
        if stream_id is None and lifecycle is None:
            return None
        if (
            type(stream_id) is not str
            or not stream_id
            or lifecycle not in {"fill", "cancel"}
            or self._store is None
            or type(intent.scope.account_ref) is not str
        ):
            raise AssertionError("LOAD01 N4 provider received incomplete source facts")
        persisted = self._store.get_intent(intent.intent_id, scope=intent.scope)
        if (
            persisted is None
            or persisted != intent
            or persisted.fingerprint != intent.fingerprint
            or persisted.instrument != intent.instrument
            or persisted.tags.get(_N4_MARKET_STREAM_TAG) != stream_id
            or persisted.tags.get(_N4_LIFECYCLE_TAG) != lifecycle
        ):
            raise AssertionError("LOAD01 N4 provider intent differed from its durable journal")
        key = (intent.scope.account_ref, intent.instrument, stream_id)
        self._n4_market_facts.setdefault(key, Counter())["intent_readbacks"] += 1
        return key, lifecycle

    def facts_snapshot(self) -> dict[str, Any]:
        market_counter_fields = (
            "submit_calls",
            "intent_readbacks",
            "cumulative_fill_observations",
            "acknowledged_orders",
            "cancel_calls",
        )
        return {
            "evidence": "synthetic_provider_cumulative_fill_observation_and_sqlite_query",
            "market_facts": [
                {
                    "account_ref": account_ref,
                    "instrument_id": instrument_id,
                    "market_stream_id": stream_id,
                    **{name: counts[name] for name in market_counter_fields},
                }
                for (account_ref, instrument_id, stream_id), counts in sorted(
                    self._n4_market_facts.items()
                )
            ],
            "active_acknowledged_orders": len(self._n4_active),
            "terminal_fill_count": len(self._n4_terminal_fills),
        }

    def submit(self, intent: OrderIntent) -> ProviderObservation:
        self.submitted += 1
        provider_order_id = f"paper-{intent.intent_id}"
        n4_context = self._n4_context(intent)
        if n4_context is None:
            return ProviderObservation.accepted(intent.intent_id, provider_order_id)
        key, lifecycle = n4_context
        facts = self._n4_market_facts[key]
        facts["submit_calls"] += 1
        if lifecycle == "fill":
            if intent.price is None or intent.quantity <= 0:
                raise AssertionError("LOAD01 N4 synthetic fill requires a priced quantity")
            self.filled += 1
            facts["cumulative_fill_observations"] += 1
            self._n4_terminal_fills[intent.intent_id] = key
            if len(self._n4_terminal_fills) > _PER_ACCOUNT:
                raise AssertionError("LOAD01 N4 provider retained too many terminal fills")
            return ProviderObservation(
                intent.intent_id,
                ExecutionState.FILLED,
                provider_order_id=provider_order_id,
                filled_quantity=intent.quantity,
                average_price=intent.price,
            )
        if intent.intent_id in self._n4_active or intent.intent_id in self._n4_terminal_fills:
            raise AssertionError("LOAD01 N4 provider saw a duplicate active order")
        self._n4_active[intent.intent_id] = (key, intent.scope.key, provider_order_id)
        facts["acknowledged_orders"] += 1
        return ProviderObservation.accepted(intent.intent_id, provider_order_id)

    def cancel(self, intent: Any) -> CancelObservation:
        self.cancelled += 1
        active = self._n4_active.get(intent.target_intent_id)
        filled_key = self._n4_terminal_fills.get(intent.target_intent_id)
        if active is None and filled_key is None:
            return CancelObservation.accepted(
                intent.cancel_id, intent.target_intent_id, intent.provider_order_id
            )
        if self._store is None:
            raise AssertionError("LOAD01 N4 cancel lost its execution store")
        persisted = self._store.get_intent(intent.target_intent_id, scope=intent.scope)
        target_record = self._store.get(intent.target_intent_id, scope=intent.scope)
        if persisted is None or target_record is None:
            raise AssertionError("LOAD01 N4 cancellation target was not journaled")
        if (
            persisted.scope != intent.scope
            or persisted.fingerprint != target_record.payload_sha256
            or target_record.intent_id != intent.target_intent_id
            or target_record.scope_key != intent.scope.key
            or target_record.provider_order_id != intent.provider_order_id
        ):
            raise AssertionError("LOAD01 N4 cancel target failed journal binding")
        if target_record.state is ExecutionState.FILLED:
            return CancelObservation.rejected(
                intent.cancel_id,
                intent.target_intent_id,
                intent.provider_order_id,
                "already_filled",
            )
        if target_record.state not in {
            ExecutionState.ACKED,
            ExecutionState.PARTIALLY_FILLED,
        }:
            return CancelObservation.rejected(
                intent.cancel_id,
                intent.target_intent_id,
                intent.provider_order_id,
                "target_not_cancellable",
            )
        if (
            active is None
            or active[1] != persisted.scope.key
            or active[2] != intent.provider_order_id
            or filled_key is not None
        ):
            raise AssertionError("LOAD01 N4 acknowledged cancel did not match owner facts")
        facts = self._n4_market_facts[active[0]]
        facts["cancel_calls"] += 1
        self._n4_active.pop(intent.target_intent_id)
        return CancelObservation.cancelled(
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
        self.hub = None
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


def _bind_n6_quota_source_bytes(
    root: Path,
    file_pins: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    """Bind quota loader inputs before importing any N6-only module."""

    bound: dict[str, Path] = {}
    for relative, expected in _N6_QUOTA_SOURCE_PINS.items():
        path = root / relative
        if (
            not path.is_file()
            or _sha256(path) != expected
            or (file_pins is not None and file_pins.get(relative) != expected)
        ):
            raise AssertionError(f"LOAD01 N6 quota source pin mismatch: {relative}")
        bound[relative] = path.resolve()
    return bound


def _require_fixed_package_path(
    module: Any,
    *,
    module_name: str,
    expected_directory: Path,
) -> None:
    locations = getattr(module, "__path__", None)
    if locations is None:
        raise AssertionError(f"LOAD01 N6 package search path is unavailable: {module_name}")
    resolved = tuple(Path(location).resolve() for location in locations)
    if resolved != (expected_directory.resolve(),):
        raise AssertionError(f"LOAD01 N6 package search path is not fixed: {module_name}")


def _require_preloaded_module_bound(
    sys_modules: Mapping[str, Any],
    *,
    module_name: str,
    expected_path: Path,
    expected_sha256: str,
) -> None:
    existing = sys_modules.get(module_name)
    if existing is None:
        return
    origin = getattr(existing, "__file__", None)
    if origin is None:
        raise AssertionError(f"LOAD01 N6 preloaded module has no file origin: {module_name}")
    existing_path = Path(origin).resolve()
    if existing_path != expected_path or _sha256(existing_path) != expected_sha256:
        raise AssertionError(f"LOAD01 N6 preloaded module is not fixed-source bound: {module_name}")


def _load_n6_quota_wave_module(
    file_pins: Mapping[str, Any] | None = None,
) -> Any:
    """Load N6 admission code only after its exact helper/Gateway bytes are bound."""

    root = _fixed_source_root()
    bound = _bind_n6_quota_source_bytes(root, file_pins)
    import importlib
    import sys

    tests_package = sys.modules.get("tests")
    tests_unit_package = sys.modules.get("tests.unit")
    gateway_package = sys.modules.get("bt_api_gateway")
    if tests_package is None or tests_unit_package is None or gateway_package is None:
        raise AssertionError("LOAD01 N6 fixed-source packages must already be imported")
    _require_fixed_package_path(
        tests_package, module_name="tests", expected_directory=root / "tests"
    )
    _require_fixed_package_path(
        tests_unit_package,
        module_name="tests.unit",
        expected_directory=root / "tests" / "unit",
    )
    gateway_directory = root / "bt_api" / "bt_api_gateway" / "src" / "bt_api_gateway"
    _require_fixed_package_path(
        gateway_package, module_name="bt_api_gateway", expected_directory=gateway_directory
    )
    for module_name, relative in (
        ("tests.unit._load01_quota_wave", "tests/unit/_load01_quota_wave.py"),
        (
            "tests.unit._load01_n6_quota_wave",
            "tests/unit/_load01_n6_quota_wave.py",
        ),
        (
            "bt_api_gateway.database_identity",
            "bt_api/bt_api_gateway/src/bt_api_gateway/database_identity.py",
        ),
        (
            "bt_api_gateway.quota_admission",
            "bt_api/bt_api_gateway/src/bt_api_gateway/quota_admission.py",
        ),
    ):
        _require_preloaded_module_bound(
            sys.modules,
            module_name=module_name,
            expected_path=bound[relative],
            expected_sha256=_N6_QUOTA_SOURCE_PINS[relative],
        )

    dim07_helper = importlib.import_module("tests.unit._load01_quota_wave")
    helper = importlib.import_module("tests.unit._load01_n6_quota_wave")
    quota = importlib.import_module("bt_api_gateway.quota_admission")
    database_identity = importlib.import_module("bt_api_gateway.database_identity")
    expected_modules = {
        "tests.unit._load01_quota_wave": (
            _N6_QUOTA_SOURCE_PINS["tests/unit/_load01_quota_wave.py"],
            bound["tests/unit/_load01_quota_wave.py"],
            dim07_helper,
        ),
        "tests.unit._load01_n6_quota_wave": (
            _N6_QUOTA_SOURCE_PINS["tests/unit/_load01_n6_quota_wave.py"],
            bound["tests/unit/_load01_n6_quota_wave.py"],
            helper,
        ),
        "bt_api_gateway": (
            _N6_QUOTA_SOURCE_PINS[
                "bt_api/bt_api_gateway/src/bt_api_gateway/__init__.py"
            ],
            bound["bt_api/bt_api_gateway/src/bt_api_gateway/__init__.py"],
            gateway_package,
        ),
        "bt_api_gateway.database_identity": (
            _N6_QUOTA_SOURCE_PINS[
                "bt_api/bt_api_gateway/src/bt_api_gateway/database_identity.py"
            ],
            bound["bt_api/bt_api_gateway/src/bt_api_gateway/database_identity.py"],
            database_identity,
        ),
        "bt_api_gateway.quota_admission": (
            _N6_QUOTA_SOURCE_PINS[
                "bt_api/bt_api_gateway/src/bt_api_gateway/quota_admission.py"
            ],
            bound["bt_api/bt_api_gateway/src/bt_api_gateway/quota_admission.py"],
            quota,
        ),
    }
    for name, (expected_sha, expected_path, module) in expected_modules.items():
        module_path = Path(module.__file__).resolve()
        if module_path != expected_path or _sha256(module_path) != expected_sha:
            raise AssertionError(f"LOAD01 N6 quota module origin mismatch: {name}")
    if sys.modules.get("tests.unit._load01_quota_wave") is not dim07_helper:
        raise AssertionError("LOAD01 N6 DIM07 baseline helper identity changed")
    if sys.modules.get("tests.unit._load01_n6_quota_wave") is not helper:
        raise AssertionError("LOAD01 N6 adapter module identity changed")
    return helper


def _load_n6_query_quota_probe_modules(
    file_pins: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load the query-only SDK probe after binding its complete source closure."""

    root = _fixed_source_root()
    bound = _bind_n6_quota_source_bytes(root, file_pins)
    import importlib
    import sys

    packages = {
        "tests": root / "tests",
        "tests.unit": root / "tests" / "unit",
        "bt_api_base": root / "bt_api" / "bt_api_base" / "src" / "bt_api_base",
        "bt_api_gateway": root / "bt_api" / "bt_api_gateway" / "src" / "bt_api_gateway",
        "bt_api_py": root / "bt_api_py",
    }
    for module_name, expected_directory in packages.items():
        package = sys.modules.get(module_name)
        if package is None:
            raise AssertionError(
                f"LOAD01 N6 query package must already be imported: {module_name}"
            )
        _require_fixed_package_path(
            package,
            module_name=module_name,
            expected_directory=expected_directory,
        )

    module_sources = {
        "tests.unit._load01_query_quota_probe": (
            "tests/unit/_load01_query_quota_probe.py",
        ),
        "bt_api_gateway.database_identity": (
            "bt_api/bt_api_gateway/src/bt_api_gateway/database_identity.py",
        ),
        "bt_api_gateway.quota_admission": (
            "bt_api/bt_api_gateway/src/bt_api_gateway/quota_admission.py",
        ),
        "bt_api_gateway.service_component": (
            "bt_api/bt_api_gateway/src/bt_api_gateway/service_component.py",
        ),
        "bt_api_py.service_account_certification": (
            "bt_api_py/service_account_certification.py",
        ),
        "bt_api_py.service_registry": ("bt_api_py/service_registry.py",),
        "bt_api_py.service_runtime": ("bt_api_py/service_runtime.py",),
        "bt_api_base.service_protocol": (
            "bt_api/bt_api_base/src/bt_api_base/service_protocol.py",
        ),
        "bt_api_base.account_certification_protocol": (
            "bt_api/bt_api_base/src/bt_api_base/account_certification_protocol.py",
        ),
    }
    for module_name, (relative,) in module_sources.items():
        _require_preloaded_module_bound(
            sys.modules,
            module_name=module_name,
            expected_path=bound[relative],
            expected_sha256=_N6_QUOTA_SOURCE_PINS[relative],
        )

    probe = importlib.import_module("tests.unit._load01_query_quota_probe")
    certification = importlib.import_module("bt_api_py.service_account_certification")
    expected_modules = {
        "tests.unit._load01_query_quota_probe": (
            "tests/unit/_load01_query_quota_probe.py",
            probe,
        ),
        "bt_api_gateway": (
            "bt_api/bt_api_gateway/src/bt_api_gateway/__init__.py",
            sys.modules["bt_api_gateway"],
        ),
        "bt_api_gateway.database_identity": (
            "bt_api/bt_api_gateway/src/bt_api_gateway/database_identity.py",
            importlib.import_module("bt_api_gateway.database_identity"),
        ),
        "bt_api_gateway.quota_admission": (
            "bt_api/bt_api_gateway/src/bt_api_gateway/quota_admission.py",
            importlib.import_module("bt_api_gateway.quota_admission"),
        ),
        "bt_api_gateway.service_component": (
            "bt_api/bt_api_gateway/src/bt_api_gateway/service_component.py",
            importlib.import_module("bt_api_gateway.service_component"),
        ),
        "bt_api_py.service_account_certification": (
            "bt_api_py/service_account_certification.py",
            certification,
        ),
        "bt_api_py.service_registry": (
            "bt_api_py/service_registry.py",
            importlib.import_module("bt_api_py.service_registry"),
        ),
        "bt_api_py.service_runtime": (
            "bt_api_py/service_runtime.py",
            importlib.import_module("bt_api_py.service_runtime"),
        ),
        "bt_api_base.service_protocol": (
            "bt_api/bt_api_base/src/bt_api_base/service_protocol.py",
            importlib.import_module("bt_api_base.service_protocol"),
        ),
        "bt_api_base.account_certification_protocol": (
            "bt_api/bt_api_base/src/bt_api_base/account_certification_protocol.py",
            importlib.import_module("bt_api_base.account_certification_protocol"),
        ),
    }
    for module_name, (relative, module) in expected_modules.items():
        module_path = Path(module.__file__).resolve()
        expected_sha = _N6_QUOTA_SOURCE_PINS[relative]
        if module_path != bound[relative] or _sha256(module_path) != expected_sha:
            raise AssertionError(f"LOAD01 N6 query module origin mismatch: {module_name}")
        if sys.modules.get(module_name) is not module:
            raise AssertionError(f"LOAD01 N6 query module identity changed: {module_name}")
    return {
        "probe": probe,
        "client_type": certification.ServiceAccountCertificationClient,
        "client_error_type": certification.ServiceAccountCertificationError,
    }


def _check_fixed_source_manifest(
    manifest_path: str | Path,
    *,
    profile_id: str | None = None,
) -> dict[str, Any]:
    _load_load01_server_imports()
    root = _fixed_source_root()
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("parent_commit") != _FIXED_PARENT:
        raise AssertionError("LOAD01 source manifest parent commit does not match")
    if profile_id is None:
        profile_id = "U0"
    file_pins = manifest["files"]
    if profile_id == "N6":
        _bind_n6_quota_source_bytes(root, file_pins)
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
        "tests.unit._load01_metrics",
        "tests.unit._load01_recovery_timing",
        "tests.unit._load01_group_workload",
        "tests.unit._load01_group_coordinator",
        "tests.unit._load01_group_topology",
        "tests.unit._load01_n5_owner_resources",
        "tests.unit._load01_n5_report",
        "tests.unit._service_group_provider_fixtures",
        "tests.integration._dim09_three_leg_fixtures",
    )
    import importlib

    if profile_id == "N6":
        _load_n6_quota_wave_module(file_pins)

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
    if profile_id == "N6":
        for name in (
            "bt_api_gateway.database_identity",
            "bt_api_gateway.quota_admission",
            "tests.unit._load01_quota_wave",
            "tests.unit._load01_n6_quota_wave",
        ):
            module = sys.modules[name]
            path = Path(module.__file__).resolve()
            relative = path.relative_to(root).as_posix()
            actual = _sha256(path)
            if file_pins.get(relative) != actual:
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
    source_test_paths = [
        ("load_profile_tests", Path("tests/unit/test_load01_profiles.py")),
        ("load_resource_diagnostics_tests", Path("tests/unit/test_load01_resource_diagnostics.py")),
        ("load_metrics_tests", Path("tests/unit/test_load01_runner_metrics.py")),
        ("load_recovery_timing_tests", Path("tests/unit/test_load01_recovery_timing.py")),
        ("load_n5_report_tests", Path("tests/unit/test_load01_n5_report.py")),
        ("load_n5_group_workload_tests", Path("tests/unit/test_load01_group_workload.py")),
        ("load_n5_group_coordinator_tests", Path("tests/unit/test_load01_group_coordinator.py")),
        ("load_n5_coordinator_outbound_config_tests", Path("tests/unit/test_load01_coordinator_outbound_config.py")),
        ("load_n5_strategy_group_wiring_tests", Path("tests/unit/test_load01_n5_strategy_group_runner_wiring.py")),
        ("load_n5_spawn_smoke_tests", Path("tests/unit/test_load01_n5_spawn_smoke.py")),
    ]
    if profile_id == "N6":
        source_test_paths.append(
            (
                "load_quota_wave_integration_tests",
                Path("tests/unit/test_load01_quota_wave_integration.py"),
            )
        )
        source_test_paths.append(
            (
                "load_server_time_query_integration_tests",
                Path("tests/integration/test_load01_n6_server_time_quota.py"),
            )
        )
    for label, relative_path in source_test_paths:
        candidate = root / relative_path
        candidate_relative = candidate.relative_to(root).as_posix()
        actual = _sha256(candidate)
        if file_pins.get(candidate_relative) != actual:
            raise AssertionError(
                f"LOAD01 {label} digest does not match its fixed-source manifest"
            )
        checked[label] = {"path": candidate_relative, "sha256": actual}
    registration, registration_pin, _registration_raw = _load01_read_threshold_registration(
        root, file_pins
    )
    checked["load01_threshold_registration"] = registration_pin
    return checked


def _check_fixed_strategy_source_manifest(
    manifest_path: str | Path,
    *,
    profile_id: str,
) -> dict[str, Any]:
    """Bind a strategy's loaded client closure without importing server code."""
    root = _fixed_source_root()
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if type(manifest) is not dict or manifest.get("parent_commit") != _FIXED_PARENT:
        raise AssertionError("LOAD01 source manifest parent commit does not match")
    file_pins = manifest.get("files")
    if type(file_pins) is not dict:
        raise AssertionError("LOAD01 source manifest file pins are malformed")

    runner = Path(__file__).resolve()
    if not runner.is_relative_to(root):
        raise AssertionError("LOAD01 strategy runner did not load from fixed source export")
    runner_relative = runner.relative_to(root).as_posix()
    runner_digest = _sha256(runner)
    if file_pins.get(runner_relative) != runner_digest:
        raise AssertionError("LOAD01 strategy runner digest differs from source manifest")
    overlay_digest = os.environ.get("BT_API_LOAD01_RUNNER_OVERLAY_SHA256")
    if overlay_digest != runner_digest:
        raise AssertionError("LOAD01 strategy runner differs from external overlay pin")

    profile_path = root / "tests/unit/_load01_profiles.py"
    profile_test_path = root / "tests/unit/test_load01_profiles.py"
    for path, expected in (
        (profile_path, _N6_PROFILE_OVERLAY_SHA256),
        (profile_test_path, _N6_PROFILE_TEST_OVERLAY_SHA256),
    ):
        relative = path.relative_to(root).as_posix()
        actual = _sha256(path)
        if actual != expected or file_pins.get(relative) != actual:
            raise AssertionError(f"LOAD01 strategy profile pin mismatch: {relative}")

    registration, registration_pin, _registration_raw = _load01_read_threshold_registration(
        root, file_pins
    )
    del registration
    if profile_id not in {"U0", "N1", "N2", "N3", "N4"}:
        raise AssertionError("LOAD01 light source check is limited to ordinary profiles")

    forbidden_prefixes = (
        "bt_api_execution",
        "bt_api_gateway",
        "bt_api_monitor",
        "bt_api_risk",
        "bt_api_py.service_host",
        "bt_api_py.service_components",
        "bt_api_py.service_execution_monitor",
        "bt_api_py.service_execution_group",
        "tests.unit.test_service_gateway_hundred_strategy_integration",
        "tests.unit._load01_group_",
        "tests.unit._load01_n5_",
        "tests.integration._dim09_three_leg_fixtures",
        "tests.unit._load01_quota_wave",
    )
    # The market facade lazily imports this transport when started. Bind its
    # source before opening any client transport, without loading server domains.
    __import__("bt_api_transport_zmq.market_v2")
    def matches_module_prefix(name: str, prefix: str) -> bool:
        if prefix in {
            "tests.unit._load01_",
            "tests.unit._load01_group_",
            "tests.unit._load01_n5_",
        }:
            return name.startswith(prefix)
        return name == prefix or name.startswith(prefix + ".")

    loaded_names = tuple(sys.modules)
    for prefix in forbidden_prefixes:
        if any(matches_module_prefix(name, prefix) for name in loaded_names):
            raise AssertionError(f"LOAD01 ordinary strategy imported server module: {prefix}")

    required_names = (
        "bt_api_base.account_protocol",
        "bt_api_base.market_protocol",
        "bt_api_base.service_protocol",
        "bt_api_py.service_account",
        "bt_api_py.service_config",
        "bt_api_py.service_deployment",
        "bt_api_py.service_market",
        "bt_api_py.service_registry",
        "bt_api_py.service_runtime",
        "bt_api_py.service_telemetry",
        "bt_api_transport_zmq.service_v2",
        "bt_api_transport_zmq.market_v2",
        "bt_api_transport_zmq.client",
        "bt_api_transport_zmq.wire",
        "bt_api_transport_zmq.events",
        "tests.unit._load01_profiles",
        "tests.unit._load01_resource_diagnostics",
        "tests.unit._load01_recovery_timing",
        "tests.unit._load01_metrics",
    )
    missing = [name for name in required_names if name not in sys.modules]
    if missing:
        raise AssertionError(
            "LOAD01 strategy client modules were not loaded from source: " + ", ".join(missing)
        )

    checked: dict[str, Any] = {
        "load_runner": {"path": runner_relative, "sha256": runner_digest},
        "load01_threshold_registration": registration_pin,
        "loaded_client_modules": {},
    }
    source_prefixes = (
        "bt_api_base",
        "bt_api_py",
        "bt_api_transport_zmq",
        "tests.unit._load01_",
    )
    for name, module in tuple(sys.modules.items()):
        if not any(matches_module_prefix(name, prefix) for prefix in source_prefixes):
            continue
        module_path_value = getattr(module, "__file__", None)
        if module_path_value is None:
            continue
        module_path = Path(module_path_value).resolve()
        try:
            relative = module_path.relative_to(root).as_posix()
        except ValueError:
            raise AssertionError(f"LOAD01 strategy imported non-fixed module: {name}") from None
        actual = _sha256(module_path)
        if file_pins.get(relative) != actual:
            raise AssertionError(f"LOAD01 strategy module digest mismatch: {relative}")
        checked["loaded_client_modules"][name] = {"path": relative, "sha256": actual}
    unbound = [name for name in required_names if name not in checked["loaded_client_modules"]]
    if unbound:
        raise AssertionError(
            "LOAD01 required strategy module was not source-bound: " + ", ".join(unbound)
        )
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
    profile_id: str = "U0",
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
        Path("tests/unit/_load01_metrics.py"),
        Path("tests/unit/test_load01_runner_metrics.py"),
        Path("tests/unit/_load01_recovery_timing.py"),
        Path("tests/unit/test_load01_recovery_timing.py"),
        Path("tests/unit/_load01_group_workload.py"),
        Path("tests/unit/test_load01_resource_diagnostics.py"),
        Path("tests/unit/_load01_group_coordinator.py"),
        Path("tests/unit/_load01_group_topology.py"),
        Path("tests/unit/_load01_n5_owner_resources.py"),
        Path("tests/unit/_load01_n5_report.py"),
        Path("tests/unit/test_load01_n5_report.py"),
        Path("tests/unit/_service_group_provider_fixtures.py"),
        Path("tests/integration/_dim09_three_leg_fixtures.py"),
        Path("tests/unit/test_load01_group_workload.py"),
        Path("tests/unit/test_load01_group_coordinator.py"),
        Path("tests/unit/test_load01_n5_strategy_group_runner_wiring.py"),
        Path("tests/unit/test_load01_coordinator_outbound_config.py"),
        Path("tests/unit/test_load01_n5_spawn_smoke.py"),
        Path("docs/evidence/iteration10-load01-engineering-registration-20261020/threshold-registration-r1.json"),
    ):
        paths.add(root / relative)
    if profile_id == "N6":
        paths.update(
            {
                root / "tests/unit/_load01_quota_wave.py",
                root / "tests/unit/_load01_n6_quota_wave.py",
                root / "tests/unit/_load01_query_quota_probe.py",
                root / "tests/unit/test_load01_quota_wave_integration.py",
                root / "tests/integration/test_load01_n6_server_time_quota.py",
            }
        )
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


def _n6_quota_wave_seed_if_due(
    *,
    profile_id: str,
    now: float,
    load_start: float,
    all_strategies_ready: bool,
    admission: Any,
    wave_event: Any,
    seed_fn=None,
) -> dict[str, Any] | None:
    """Seed the N6 quota window once, after readiness and at scheduled t+60."""

    if profile_id != "N6" or now < load_start + _N6_QUOTA_WAVE_START_SECONDS:
        return None
    if admission is None or wave_event is None:
        raise AssertionError("LOAD01 N6 quota wave is missing its parent admission or event")
    if wave_event.is_set():
        raise AssertionError("LOAD01 N6 quota wave event was set before parent seeding")
    if not all_strategies_ready:
        raise RuntimeError("LOAD01 N6 quota seed is not allowed before all strategies are ready")

    quota_wave = _load_n6_quota_wave_module()
    if seed_fn is None:
        seed_fn = quota_wave.seed_quota_window
    started_ns = time.perf_counter_ns()
    grants = seed_fn(admission)
    if type(grants) is not tuple or len(grants) != quota_wave.CAPACITY_PER_WINDOW:
        raise AssertionError(
            f"LOAD01 N6 parent seed did not create exactly {quota_wave.CAPACITY_PER_WINDOW} grants"
        )
    completed_ns = time.perf_counter_ns()
    wave_event.set()
    return {
        "scheduled_after_load_start_seconds": _N6_QUOTA_WAVE_START_SECONDS,
        "seed_started_monotonic_ns": started_ns,
        "seed_completed_monotonic_ns": completed_ns,
        "seed_grant_count": len(grants),
        "worker_trigger_set_monotonic_ns": time.perf_counter_ns(),
    }


def _n6_quota_admission_wave_contract(quota_wave: Any) -> dict[str, Any]:
    """Return the registered ten-strategy LOAD01 quota-wave target projection."""

    offered = quota_wave.STRATEGY_COUNT * quota_wave.REQUESTS_PER_STRATEGY
    return {
        "domain_ref": quota_wave.QUOTA_DOMAIN_REF,
        "admission_operation": "CTP_QUERY",
        "scheduled_after_load_start_seconds": _N6_QUOTA_WAVE_START_SECONDS,
        "strategy_count": quota_wave.STRATEGY_COUNT,
        "requests_per_strategy": quota_wave.REQUESTS_PER_STRATEGY,
        "offered_requests_target": offered,
        "expected_grants": quota_wave.CAPACITY_PER_WINDOW,
        "expected_deadline_exceeded": offered - quota_wave.CAPACITY_PER_WINDOW,
        "expected_grants_by_strategy": 1,
        "expected_deadlines_by_strategy": 1,
        "capacity_per_window": quota_wave.CAPACITY_PER_WINDOW,
        "preseeded_capacity": quota_wave.CAPACITY_PER_WINDOW,
        "capacity_window_seconds": quota_wave.WINDOW_SECONDS,
        "reserved_capacity": quota_wave.RESERVED_CAPACITY,
        "request_deadline_seconds": quota_wave.REQUEST_DEADLINE_SECONDS,
        "grant_latency_ms_max": 30_000,
        "denial_p95_cleanup_ms_max": 30_750,
        "provider_query_dispatched_by_wave": False,
        "pending_waiters_after_collection_max": 0,
        "report_separation_invariant": (
            "Keep quota admission receipts outside ordinary orders.query RPC counts and ordinary RPC timeout totals."
        ),
        "execution_boundary": (
            "synthetic SQLite admission receipts only; this wave does not issue a provider query"
        ),
    }


def _n6_quota_wave_outcomes_from_strategy_results(
    strategy_results: Mapping[int, Mapping[str, Any]],
) -> tuple[Any, ...]:
    """Restore typed receipts only after every strategy close has returned."""

    quota_wave = _load_n6_quota_wave_module()
    if type(strategy_results) is not dict or set(strategy_results) != set(
        range(_STRATEGIES)
    ):
        raise AssertionError(f"LOAD01 N6 results must contain all {_STRATEGIES} strategies")
    expected_fields = {
        "strategy_index",
        "request_index",
        "outcome",
        "subject_digest",
        "run_digest",
        "elapsed_ns",
    }
    outcomes: list[Any] = []
    for strategy_index in range(_STRATEGIES):
        wave = strategy_results[strategy_index].get("quota_admission_wave")
        if (
            type(wave) is not dict
            or wave.get("started") is not True
            or wave.get("complete") is not True
            or type(wave.get("outcomes")) is not list
            or len(wave["outcomes"]) != quota_wave.REQUESTS_PER_STRATEGY
        ):
            raise AssertionError(
                f"LOAD01 N6 strategy {strategy_index} did not return both quota outcomes"
            )
        for row in wave["outcomes"]:
            if type(row) is not dict or set(row) != expected_fields:
                raise AssertionError("LOAD01 N6 quota outcome fields are malformed")
            outcome = quota_wave.QuotaWaveOutcome(
                strategy_index=row["strategy_index"],
                request_index=row["request_index"],
                outcome=row["outcome"],
                subject_digest=row["subject_digest"],
                run_digest=row["run_digest"],
                elapsed_ns=row["elapsed_ns"],
            )
            if outcome.strategy_index != strategy_index:
                raise AssertionError("LOAD01 N6 quota outcome crossed strategy ownership")
            outcomes.append(outcome)
    return tuple(outcomes)


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
    _check_fixed_source_manifest(
        os.environ["BT_API_LOAD01_SOURCE_MANIFEST"], profile_id=profile_id
    )
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
                schedule_index, next_publish, _published = (
                    _load01_run_due_market_publish_batch(
                        provider.publish_one,
                        topic_schedule=topic_schedule,
                        market_keys=market_keys,
                        schedule_index=schedule_index,
                        next_publish_ns=next_publish,
                        interval_ns=interval_ns,
                        batch_limit=_MARKET_PUBLISH_BATCH_MAX,
                        monotonic_ns=time.perf_counter_ns,
                    )
                )

            host.serve_once(timeout_ms=0)
            if control.poll(0):
                command = control.recv()
                if command == ("status",):
                    control.send(("status", _market_status(provider, hub)))
                elif (
                    isinstance(command, tuple)
                    and len(command) == 2
                    and command[0] == "terminal-watermark"
                    and type(command[1]) is int
                ):
                    control.send(
                        (
                            "terminal-watermark",
                            command[1],
                            _market_terminal_status(provider, hub, stop_event),
                        )
                    )
                elif command == ("close",):
                    stop_event.set()
                    host.close()
                    control.send(
                        (
                            "closed",
                            os.getpid(),
                            host.state,
                            host.summary["cleanup_failures"],
                            _market_terminal_status(provider, hub, stop_event),
                        )
                    )
                    return
                else:
                    raise AssertionError("unknown LOAD01 market control")

            if now - last_report >= 30:
                control.send(("progress", "market", _market_status(provider, hub)))
                last_report = now
            sleep_seconds = _load01_market_host_sleep_seconds(
                next_publish_ns=next_publish,
                now_ns=time.perf_counter_ns(),
                stop_requested=stop_event.is_set(),
            )
            if sleep_seconds:
                time.sleep(sleep_seconds)
    except BaseException as exc:
        if host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", "market", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _normalize_load01_market_watermarks(
    watermarks: Any, expected_stream_ids: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    expected = tuple(sorted(expected_stream_ids))
    if (
        any(type(stream_id) is not str or not stream_id for stream_id in expected)
        or len(expected) != len(set(expected))
        or type(watermarks) is not dict
        or set(watermarks) != set(expected)
    ):
        raise AssertionError("LOAD01 terminal watermark topic coverage was invalid")
    normalized: dict[str, dict[str, Any]] = {}
    for stream_id in expected:
        row = watermarks[stream_id]
        if type(row) is not dict or set(row) != {"epoch", "sequence"}:
            raise AssertionError("LOAD01 terminal watermark row shape was invalid")
        epoch = row["epoch"]
        sequence = row["sequence"]
        if type(epoch) is not str or not epoch or type(sequence) is not int or sequence < 0:
            raise AssertionError("LOAD01 terminal watermark value was invalid")
        normalized[stream_id] = {"epoch": epoch, "sequence": sequence}
    return normalized


def _validate_load01_terminal_market_message(
    message: Any, *, expected_token: int, expected_stream_ids: tuple[str, ...]
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        type(expected_token) is not int
        or type(message) is not tuple
        or len(message) != 3
        or type(message[0]) is not str
        or message[0] != "terminal-watermark"
        or type(message[1]) is not int
        or message[1] != expected_token
        or type(message[2]) is not dict
    ):
        raise AssertionError("LOAD01 market terminal watermark response schema was invalid")
    status = message[2]
    watermark = _validate_load01_terminal_market_status(status, expected_stream_ids)
    return status, watermark


def _validate_load01_strategy_watermark_message(
    message: Any, *, expected_index: int, expected_pid: int, expected_token: int
) -> dict[str, Any]:
    if (
        type(expected_index) is not int
        or type(expected_pid) is not int
        or type(expected_token) is not int
        or type(message) is not tuple
        or len(message) != 5
        or type(message[0]) is not str
        or message[0] != "market-watermark-result"
        or type(message[1]) is not int
        or message[1] != expected_index
        or type(message[2]) is not int
        or message[2] != expected_pid
        or type(message[3]) is not int
        or message[3] != expected_token
        or type(message[4]) is not dict
    ):
        raise AssertionError("LOAD01 strategy terminal ACK response schema was invalid")
    result = message[4]
    expected_result_fields = {
        "complete",
        "failures",
        "targets",
        "receipts",
        "last_sequences",
        "last_epochs",
        "client_states",
        "runtime_state",
        "counter_deltas",
        "caught_up_at_monotonic_ns",
        "ack_sent_monotonic_ns",
    }
    if set(result) != expected_result_fields:
        raise AssertionError("LOAD01 strategy terminal ACK map schema was invalid")
    caught_up_ns = result["caught_up_at_monotonic_ns"]
    sent_ns = result["ack_sent_monotonic_ns"]
    if (
        type(caught_up_ns) is not int
        or caught_up_ns < 0
        or type(sent_ns) is not int
        or sent_ns < caught_up_ns
    ):
        raise AssertionError("LOAD01 strategy terminal ACK timestamps were invalid")
    return result


def _validate_load01_terminal_market_status(
    status: Any, expected_stream_ids: tuple[str, ...]
) -> dict[str, Any]:
    if type(status) is not dict or status.get("source_publishing_stopped") is not True:
        raise AssertionError("LOAD01 market publisher had not stopped at its terminal watermark")
    expected_status_fields = {
        "provider_starts",
        "provider_stops",
        "active_provider_keys",
        "source_sequences",
        "source_payload_bytes",
        "source_payload_bytes_by_topic",
        "first_publish_ns",
        "last_publish_ns",
        "hub_readiness",
        "source_publishing_stopped",
        "hub_watermarks",
        "watermark_captured_monotonic_ns",
    }
    if set(status) != expected_status_fields:
        raise AssertionError("LOAD01 terminal market status map schema was invalid")
    captured_ns = status.get("watermark_captured_monotonic_ns")
    if type(captured_ns) is not int or captured_ns < 0:
        raise AssertionError("LOAD01 terminal watermark capture time was invalid")
    source_sequences = status.get("source_sequences")
    if (
        type(source_sequences) is not dict
        or set(source_sequences) != set(expected_stream_ids)
        or any(type(value) is not int or value < 0 for value in source_sequences.values())
    ):
        raise AssertionError("LOAD01 provider source sequence coverage was invalid")
    watermarks = _normalize_load01_market_watermarks(
        status.get("hub_watermarks"), expected_stream_ids
    )
    for stream_id, row in watermarks.items():
        if source_sequences[stream_id] != row["sequence"]:
            raise AssertionError("LOAD01 provider counter differed from Gateway head")
    return {"captured_monotonic_ns": captured_ns, "watermarks": watermarks}


def _load01_market_client_terminal_state(client):
    return {
        "queue_count": client.queue_count,
        "queue_bytes": client.queue_bytes,
        "healthy": client.healthy,
        "recovery_required": client.recovery_required,
        "resync_required": client.resync_required,
        "unsubscription_uncertain": client.unsubscription_uncertain,
        "pending_poll_replay": client._pending_poll_replay is not None,
        "pending_poll_gap_event": client._pending_poll_gap_event is not None,
        "pending_resync": client._pending_resync is not None,
    }


def _load01_market_watermark_time_failures(
    *, receipts, barrier_captured_monotonic_ns, caught_up_at_monotonic_ns
):
    failures = []
    if type(receipts) is not dict:
        receipts = {}
    for stream_id, receipt in receipts.items():
        if type(receipt) is not dict:
            continue
        received_ns = receipt.get("received_monotonic_ns")
        if type(received_ns) is not int or received_ns < 0:
            failures.append(f"receipt_time_invalid:{stream_id}")
        elif (
            type(caught_up_at_monotonic_ns) is int
            and received_ns > caught_up_at_monotonic_ns
        ):
            failures.append(f"receipt_after_ack:{stream_id}")
    if type(barrier_captured_monotonic_ns) is not int or barrier_captured_monotonic_ns < 0:
        failures.append("barrier_capture_time_invalid")
    if type(caught_up_at_monotonic_ns) is not int or caught_up_at_monotonic_ns < 0:
        failures.append("catchup_ack_time_invalid")
    elif type(barrier_captured_monotonic_ns) is int and (
        caught_up_at_monotonic_ns < barrier_captured_monotonic_ns
        or caught_up_at_monotonic_ns - barrier_captured_monotonic_ns > 10_000_000_000
    ):
        failures.append("catchup_outside_ten_second_window")
    return tuple(failures)


def _load01_market_terminal_ack_timing_failures(
    *,
    ack_sent_monotonic_ns,
    parent_collected_monotonic_ns,
    deadline_monotonic_ns,
    collection_deadline_monotonic_ns,
):
    """Keep child completion timing separate from parent diagnostic collection."""

    timestamps = (
        ack_sent_monotonic_ns,
        parent_collected_monotonic_ns,
        deadline_monotonic_ns,
        collection_deadline_monotonic_ns,
    )
    if any(type(value) is not int or value < 0 for value in timestamps):
        return ("terminal_ack_timing_invalid",)
    failures = []
    if ack_sent_monotonic_ns > deadline_monotonic_ns:
        failures.append("strategy_ack_sent_after_ten_second_deadline")
    if ack_sent_monotonic_ns > parent_collected_monotonic_ns:
        failures.append("strategy_ack_timestamp_after_parent_collection")
    if parent_collected_monotonic_ns > collection_deadline_monotonic_ns:
        failures.append("parent_collection_after_diagnostic_grace")
    return tuple(failures)


def _load01_market_terminal_cursors_match(terminal_result, final_strategy):
    if type(terminal_result) is not dict or type(final_strategy) is not dict:
        return False
    for final_field, ack_field in (
        ("last_sequences", "last_sequences"),
        ("last_epochs", "last_epochs"),
        ("market_delivery_receipts", "receipts"),
    ):
        final_value = final_strategy.get(final_field)
        ack_value = terminal_result.get(ack_field)
        if type(final_value) is not dict or type(ack_value) is not dict:
            return False
        if final_value != ack_value:
            return False
    return True


def _load01_market_watermark_completion_failures(
    *,
    targets,
    expected_stream_ids,
    receipts,
    last_sequences,
    last_epochs,
    client_states,
    runtime_state,
    counter_deltas,
    barrier_captured_monotonic_ns,
    caught_up_at_monotonic_ns,
    check_ack_time=True,
):
    failures = []
    expected = tuple(sorted(expected_stream_ids))
    try:
        normalized_targets = _normalize_load01_market_watermarks(targets, expected)
    except AssertionError:
        normalized_targets = {}
        failures.append("target_coverage_or_shape_invalid")
    for label, mapping in (
        ("receipt", receipts),
        ("sequence_cursor", last_sequences),
        ("epoch_cursor", last_epochs),
        ("client_state", client_states),
    ):
        if type(mapping) is not dict or set(mapping) != set(expected):
            failures.append(f"{label}_coverage_invalid")
    if type(receipts) is not dict:
        receipts = {}
    if type(last_sequences) is not dict:
        last_sequences = {}
    if type(last_epochs) is not dict:
        last_epochs = {}
    if type(client_states) is not dict:
        client_states = {}
    for stream_id in expected:
        target = normalized_targets.get(stream_id)
        if target is None:
            continue
        target_epoch = target["epoch"]
        target_sequence = target["sequence"]
        receipt = receipts.get(stream_id)
        if type(receipt) is not dict:
            failures.append(f"receipt_missing:{stream_id}")
        else:
            receipt_epoch = receipt.get("epoch")
            receipt_sequence = receipt.get("sequence")
            if receipt_epoch != target_epoch:
                failures.append(f"receipt_epoch_mismatch:{stream_id}")
            if type(receipt_sequence) is not int or receipt_sequence != target_sequence:
                kind = (
                    "receipt_past_target"
                    if type(receipt_sequence) is int and receipt_sequence > target_sequence
                    else "receipt_behind_target"
                )
                failures.append(f"{kind}:{stream_id}")
        cursor_epoch = last_epochs.get(stream_id)
        if cursor_epoch != target_epoch:
            failures.append(f"cursor_epoch_mismatch:{stream_id}")
        cursor_sequence = last_sequences.get(stream_id)
        if type(cursor_sequence) is not int or cursor_sequence != target_sequence:
            kind = (
                "cursor_past_target"
                if type(cursor_sequence) is int and cursor_sequence > target_sequence
                else "cursor_behind_target"
            )
            failures.append(f"{kind}:{stream_id}")
        state = client_states.get(stream_id)
        if type(state) is not dict:
            failures.append(f"client_state_missing:{stream_id}")
            continue
        for field in ("queue_count", "queue_bytes"):
            value = state.get(field)
            if type(value) is not int or value < 0:
                failures.append(f"{field}_invalid:{stream_id}")
            elif value != 0:
                failures.append(f"{field}_nonzero:{stream_id}")
        if state.get("healthy") is not True:
            failures.append(f"healthy_false:{stream_id}")
        for field in (
            "recovery_required",
            "resync_required",
            "unsubscription_uncertain",
            "pending_poll_replay",
            "pending_poll_gap_event",
            "pending_resync",
        ):
            if state.get(field) is not False:
                failures.append(f"{field}_true_or_missing:{stream_id}")
    if type(runtime_state) is not dict:
        failures.append("runtime_state_missing")
    else:
        if runtime_state.get("healthy") is not True:
            failures.append("runtime_unhealthy")
        if (
            type(runtime_state.get("active_bindings")) is not int
            or type(runtime_state.get("expected_bindings")) is not int
            or runtime_state.get("expected_bindings") <= 0
            or runtime_state.get("active_bindings") != runtime_state.get("expected_bindings")
        ):
            failures.append("runtime_binding_count_changed")
        if type(runtime_state.get("cleanup_failures")) is not int or runtime_state["cleanup_failures"] != 0:
            failures.append("runtime_cleanup_failure")
        if (
            type(runtime_state.get("unregister_uncertainties")) is not int
            or runtime_state["unregister_uncertainties"] != 0
        ):
            failures.append("runtime_unregister_uncertain")
    if type(counter_deltas) is not dict:
        failures.append("counter_deltas_missing")
    else:
        for name in ("duplicates", "gap_count", "gap_events", "epoch_changes"):
            if type(counter_deltas.get(name)) is not int or counter_deltas[name] != 0:
                failures.append(f"counter_delta_nonzero:{name}")
    if check_ack_time:
        failures.extend(
            _load01_market_watermark_time_failures(
                receipts=receipts,
                barrier_captured_monotonic_ns=barrier_captured_monotonic_ns,
                caught_up_at_monotonic_ns=caught_up_at_monotonic_ns,
            )
        )
    return tuple(failures)


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


def _market_terminal_status(provider, hub, stop_event):
    hub_watermarks = {}
    for key in sorted(provider.keys, key=lambda item: item.stream_id):
        head = hub.head(key)
        hub_watermarks[key.stream_id] = {
            "epoch": head["epoch"],
            "sequence": head["head"],
        }
    return {
        **_market_status(provider, hub),
        "source_publishing_stopped": stop_event.is_set(),
        "hub_watermarks": hub_watermarks,
        "watermark_captured_monotonic_ns": time.monotonic_ns(),
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
        for resource in grants_for_principal:
            _merge_authority_resource(
                durable_grants, principal, service_identity, resource
            )

    telemetry_principals = {}
    telemetry_grants = {}
    telemetry_targets = set()
    for audience in strategy_audience:
        public_key, principal, strategy_ref, account_ref = audience[:4]
        telemetry_scope_ref = (
            audience[4] if len(audience) > 4 else _TELEMETRY_SCOPE_REF
        )
        telemetry_principals[public_key] = principal
        scope = WireScope(_DOMAIN, account_ref=account_ref, strategy_ref=strategy_ref)
        telemetry_targets.add(scope)
        grant = ResourceGrant(
            _TELEMETRY_INSTANCE,
            "service.telemetry_ingress",
            telemetry_scope_ref,
            scope,
            _TELEMETRY_OPS,
        )
        _merge_authority_resource(
            telemetry_grants,
            principal,
            WireIdentity(WireRole.STRATEGY_CLIENT, principal, strategy_ref),
            grant,
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

    _load_load01_server_imports()
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
    strategy_audience: tuple[tuple[Any, ...], ...],
) -> tuple[dict[str, Any], ...]:
    principal_ref = "load01-monitor-readonly-observer"
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, principal_ref, principal_ref)
    snapshots = []
    for audience in strategy_audience:
        _public_key, expected_principal, strategy_ref, account_ref = audience[:4]
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
    status = {
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
    quota_facts = getattr(provider, "quota_server_time_facts_snapshot", None)
    if callable(quota_facts):
        status["server_time_quota_facts"] = quota_facts()
    return status


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


def _activate_n5_owner_group_authority(
    execution_host: ServiceHost,
    group_runtime,
    *,
    account_index: int,
    coordinator_identity: tuple[str, str],
) -> dict[str, Any]:
    """Activate the exact group generation from the live owner Registry row."""

    registry = getattr(execution_host, "_registry", None)
    if registry is None or execution_host.state != "started":
        raise AssertionError("LOAD01 N5 owner Registry is not live")
    principal_ref, service_id = coordinator_identity
    identity = WireIdentity(WireRole.SERVICE, principal_ref, service_id)
    scope = group_runtime.proof_scope
    origin_run_id = group_runtime.route["origin_strategy_run_id"]
    execution_instance_id = f"{_EXEC_INSTANCE_PREFIX}{account_index:02d}"

    def exact_registration(
        *,
        instance_id: str,
        port: str,
        scope_ref: str,
        operations: frozenset[str],
        write_operations: frozenset[str],
    ) -> ActiveRegistrationContext:
        rows = registry.active_registration_contexts(instance_id=instance_id, port=port)
        matches = tuple(
            item
            for item in rows
            if type(item) is ActiveRegistrationContext
            and item.instance_id == instance_id
            and item.port == port
            and item.scope_ref == scope_ref
            and item.context.identity == identity
            and item.context.principal_ref == principal_ref
            and item.context.scope == scope
            and item.context.scope.run_id is None
            and item.strategy_write_run_id == origin_run_id
            and item.operations == operations
            and item.write_operations == write_operations
        )
        if len(matches) != 1:
            raise AssertionError("LOAD01 N5 coordinator Registry binding is not exact")
        return matches[0]

    entry = exact_registration(
        instance_id=execution_instance_id,
        port="service.order_entry",
        scope_ref=group_runtime.route["order_scope_ref"],
        operations=frozenset(
            {
                "orders.submit_group_leg",
                "orders.cancel_group_leg",
                "orders.resolve_group_leg",
            }
        ),
        write_operations=frozenset(
            {
                "orders.submit_group_leg",
                "orders.cancel_group_leg",
                "orders.resolve_group_leg",
            }
        ),
    )
    query = exact_registration(
        instance_id=execution_instance_id,
        port="service.order_query",
        scope_ref=group_runtime.route["query_scope_ref"],
        operations=frozenset({"orders.query_group_leg", "orders.query_group_cancel"}),
        write_operations=frozenset(),
    )
    reservation = exact_registration(
        instance_id=group_runtime.reservation_instance_id,
        port="service.group_reservations",
        scope_ref=group_runtime.route["reservation_scope_ref"],
        operations=frozenset(
            {"reservations.reserve", "reservations.query", "reservations.release"}
        ),
        write_operations=frozenset(
            {"reservations.reserve", "reservations.release"}
        ),
    )
    generation = group_runtime.execution_authority.control.activate(
        entry.context,
        execution_scope=group_runtime.execution_scope,
        risk_account_scope_key=group_runtime.risk_scope.key,
    )
    expected_risk_fingerprint = group_runtime.route["risk_scope_fingerprint"]
    if (
        generation.registration_id != entry.context.registration_id
        or generation.principal_ref != principal_ref
        or generation.portfolio_ref != group_runtime.route["portfolio_ref"]
        or generation.account_ref != group_runtime.route["account_ref"]
        or generation.strategy_id != group_runtime.route["strategy_ref"]
        or generation.execution_scope_key != group_runtime.execution_scope.key
        or generation.risk_scope_fingerprint != expected_risk_fingerprint
        or generation.active is not True
    ):
        raise AssertionError("LOAD01 N5 owner group generation receipt is invalid")
    return {
        "principal_ref": generation.principal_ref,
        "registration_id": generation.registration_id,
        "generation": generation.generation,
        "portfolio_ref": generation.portfolio_ref,
        "account_ref": generation.account_ref,
        "strategy_id": generation.strategy_id,
        "origin_strategy_run_id": entry.strategy_write_run_id,
        "execution_scope_key": generation.execution_scope_key,
        "risk_scope_fingerprint": generation.risk_scope_fingerprint,
        "active": generation.active,
        "entry_scope_ref": entry.scope_ref,
        "query_registration_id": query.context.registration_id,
        "reservation_registration_id": reservation.context.registration_id,
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
    n5_metadata=None,
    n5_reservation_scope_ref=None,
    n5_coordinator_identity=None,
    n5_origin_strategy_run_id=None,
    n5_coordinator_public_key=None,
    query_server_time_quota_database_path=None,
):
    source_manifest_path = os.environ["BT_API_LOAD01_SOURCE_MANIFEST"]
    query_probe_modules = None
    if query_server_time_quota_database_path is None:
        _check_fixed_source_manifest(source_manifest_path)
    else:
        if account_index != 0:
            raise AssertionError("LOAD01 N6 query quota belongs to account owner zero")
        _check_fixed_source_manifest(source_manifest_path, profile_id="N6")
        manifest_data = json.loads(Path(source_manifest_path).read_text(encoding="utf-8"))
        query_probe_modules = _load_n6_query_quota_probe_modules(manifest_data["files"])
    rpc_trace = _install_load01_rpc_trace("owner")
    account_config = parse_service_config(account_config_raw)
    execution_config = parse_service_config(execution_config_raw)
    n5_group_values = (
        n5_metadata,
        n5_reservation_scope_ref,
        n5_coordinator_identity,
        n5_origin_strategy_run_id,
        n5_coordinator_public_key,
    )
    n5_group_owner = any(value is not None for value in n5_group_values)
    scope_provider = "fixture" if n5_group_owner else "synthetic"
    scope_environment = "simulation" if n5_group_owner else "paper"
    if n5_group_owner:
        if (
            any(value is None for value in n5_group_values)
            or n5_coordinator_identity
            != (_N5_COORDINATOR_PRINCIPAL, _N5_COORDINATOR_SERVICE_ID)
            or type(n5_coordinator_public_key) is not bytes
            or len(n5_coordinator_public_key) != 40
        ):
            raise ValueError("LOAD01 N5 owner coordinator inputs are invalid")
    account_service_id = account_config.service_id
    execution_service_id = execution_config.service_id
    account_ref = _ACCOUNT_REFS[account_index]
    account_provider = _CountedSyntheticAccountProvider(account_index)
    account_provider_for_worker = account_provider
    if query_probe_modules is not None:
        synthetic_provider = query_probe_modules["probe"].N6SyntheticServerTimeProvider(
            account_provider
        )
        account_provider_for_worker = query_probe_modules["probe"].ServerTimeQuotaProvider(
            synthetic_provider,
            query_server_time_quota_database_path,
            account_ref,
        )
    order_provider = _SyntheticOrderProvider()
    admission = _SyntheticAdmission()
    journal = None
    store = None
    account_host = None
    execution_host = None
    n5_group_runtime = None
    execution_authority = None
    relay_runtime = None
    readers: dict[int, ExecutionJournalReader] = {}
    relays: dict[int, ServiceExecutionMonitorRelay] = {}
    relay_worker_threads: dict[int, threading.Thread] = {}
    relay_worker_commands: dict[int, queue.Queue] = {}
    relay_worker_facts_by_scope: dict[int, dict[str, Any]] = {}
    relay_worker_error_facts_by_scope: dict[int, dict[str, Any]] = {}
    relay_worker_states: dict[int, str] = {}
    relay_abort_event = threading.Event()
    relay_event_queue: queue.Queue = queue.Queue(maxsize=256)
    scope_records: list[dict[str, Any]] = []
    relay_delivered_by_scope: Counter[str] = Counter()
    gateway_worker = None
    owner_phase = "owner-setup"
    close_phase: str | None = None
    close_stages_completed: list[str] = []
    normal_drain_completed = False
    source_evidence: list[dict[str, Any]] | None = None
    group_activation_receipt: dict[str, Any] | None = None
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

        worker_rows = list(relay_worker_facts_by_scope.values())
        runtime_rows = [row.get("relay_runtime", {}) for row in worker_rows]
        relay_facts = None
        if relay_worker_threads:
            all_closed = (
                len(worker_rows) == len(relay_worker_threads)
                and all(row.get("state") == "closed" for row in runtime_rows)
                and all(row.get("worker_joined") is True for row in runtime_rows)
            )
            relay_facts = {
                "state": "closed" if all_closed else "registered",
                "cleanup_failures": sum(
                    int(row.get("cleanup_failures", 0)) for row in runtime_rows
                ),
                "unregister_uncertainties": sum(
                    int(row.get("unregister_uncertainties", 0)) for row in runtime_rows
                ),
                "unknown_registration_count": sum(
                    int(row.get("unknown_registration_count", 0)) for row in runtime_rows
                ),
                "binding_count": sum(int(row.get("binding_count", 0)) for row in runtime_rows),
                "opened_binding_count": sum(
                    int(row.get("opened_binding_count", 0)) for row in runtime_rows
                ),
                "active_binding_count": sum(
                    int(row.get("active_binding_count", 0)) for row in runtime_rows
                ),
                "runtime_count": len(relay_worker_threads),
                "worker_thread_count": len(relay_worker_threads),
                "worker_joined_count": sum(
                    bool(row.get("worker_joined")) for row in runtime_rows
                ),
            }

        try:
            worker_facts = None if gateway_worker is None else {
                "state": gateway_worker.state,
                "ready": gateway_worker.ready,
                "provider_connect_calls": account_provider.connect_calls,
                "provider_read_calls": account_provider.read_calls,
                "provider_close_calls": account_provider.close_calls,
            }
            if worker_facts is not None and query_probe_modules is not None:
                worker_facts["server_time_quota_facts"] = (
                    account_provider_for_worker.quota_server_time_facts_snapshot()
                )
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
            "relay_count": sum(
                state not in {"completed", "failed"} for state in relay_worker_states.values()
            ),
            "reader_count": len(readers),
            "total_relayed": total_relayed,
            "relay_delivered_by_scope": dict(relay_delivered_by_scope),
            "source_drain_by_scope": drain_facts,
            "relay_runtime": relay_facts,
            "relay_runtime_count": len(relay_worker_threads),
            "relay_runtime_binding_count": sum(
                int(row.get("opened_binding_count", 0)) for row in runtime_rows
            ),
            "relay_runtime_binding_count_after_close": sum(
                int(row.get("binding_count", 0)) for row in runtime_rows
            ),
            "relay_runtime_active_binding_count": sum(
                int(row.get("active_binding_count", 0)) for row in runtime_rows
            ),
            "relay_workers_by_scope": {
                str(index): dict(facts) for index, facts in relay_worker_facts_by_scope.items()
            },
            "relay_worker_errors_by_scope": {
                str(index): dict(facts)
                for index, facts in relay_worker_error_facts_by_scope.items()
            },
            "relay_worker_states": {
                str(index): state for index, state in relay_worker_states.items()
            },
            "execution_host": host_facts(execution_host),
            "n5_group_runtime": (
                {
                    **n5_group_runtime.facts_snapshot(),
                    "activation_receipt": group_activation_receipt,
                }
                if n5_group_runtime is not None
                else None
            ),
            "account_host": host_facts(account_host),
            "account_worker": worker_facts,
            "synthetic_execution": order_provider.facts_snapshot(),
            "component_close_callback_errors": list(close_callback_errors),
        }

    def remember_relay_worker_error(event: tuple[Any, ...]) -> None:
        if (
            type(event) is not tuple
            or len(event) != 3
            or event[0] != "error"
            or type(event[1]) is not int
            or type(event[2]) is not dict
        ):
            return
        index = event[1]
        payload = event[2]
        allowed = (
            "phase",
            "error_type",
            "code",
            "delivered",
            "runtime",
            "timing",
            "cleanup_errors",
        )
        relay_worker_error_facts_by_scope[index] = {
            key: payload[key] for key in allowed if key in payload
        }
        relay_worker_states[index] = "failed"

    last_execution_pump_end_ns: int | None = None

    def pump_owner_hosts() -> None:
        nonlocal last_execution_pump_end_ns
        if account_host is None or execution_host is None:
            return
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

    def wait_owner_control(timeout_seconds: float) -> Any:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            pump_owner_hosts()
            try:
                event = relay_event_queue.get_nowait()
            except queue.Empty:
                event = None
            if event is not None:
                if event[0] == "error":
                    remember_relay_worker_error(event)
                    raise RuntimeError(
                        f"LOAD01 relay worker failed in {event[2].get('phase', 'unknown')}"
                    )
                if event[0] == "progress":
                    local_index, delivered = event[1], event[2]
                    relay_worker_states[local_index] = "connected"
                    relay_delivered_by_scope[scope_records[local_index]["scope_ref"]] = delivered
                    total_relayed_value = sum(relay_delivered_by_scope.values())
                    nonlocal total_relayed
                    total_relayed = total_relayed_value
                else:
                    raise AssertionError("LOAD01 relay worker emitted an unexpected startup event")
            if control.poll(0):
                return control.recv()
            time.sleep(0.001)
        raise TimeoutError("LOAD01 owner control command deadline expired")

    def take_relay_event(timeout_seconds: float = 0.002) -> Any:
        pump_owner_hosts()
        try:
            return relay_event_queue.get(timeout=timeout_seconds)
        except queue.Empty:
            return None

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
        account_operations = _ACCOUNT_OPS
        if query_probe_modules is not None and strategy_index == 0:
            account_operations = _N6_ACCOUNT_QUERY_OPS
        execution_scope = ExecutionScope(
            scope_provider, scope_environment, account_ref, strategy_ref, _EXECUTION_DAY
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
                        account_operations,
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

    if n5_group_owner:
        designated_index = account_index * _PER_ACCOUNT
        designated_strategy = next(
            (item for item in strategy_audience if item["index"] == designated_index),
            None,
        )
        if designated_strategy is None or n5_coordinator_public_key in execution_principals:
            raise ValueError("LOAD01 N5 owner audience or coordinator key is invalid")
        group_scope = WireScope(
            _DOMAIN,
            account_ref=account_ref,
            strategy_ref=designated_strategy["strategy_ref"],
            portfolio_ref=_N5_PORTFOLIO_REF,
        )
        coordinator_principal, coordinator_service_id = n5_coordinator_identity
        coordinator_identity = WireIdentity(
            WireRole.SERVICE, coordinator_principal, coordinator_service_id
        )
        execution_principals[n5_coordinator_public_key] = coordinator_principal
        execution_grants[coordinator_principal] = (
            PrincipalGrant(
                coordinator_identity,
                (
                    ResourceGrant(
                        f"{_EXEC_INSTANCE_PREFIX}{account_index:02d}",
                        "service.order_entry",
                        f"{_EXEC_ORDER_SCOPE_PREFIX}{account_index:02d}-v1",
                        group_scope,
                        frozenset(
                            {
                                "orders.submit_group_leg",
                                "orders.cancel_group_leg",
                                "orders.resolve_group_leg",
                            }
                        ),
                    ),
                    ResourceGrant(
                        f"{_EXEC_INSTANCE_PREFIX}{account_index:02d}",
                        "service.order_query",
                        f"{_EXEC_QUERY_SCOPE_PREFIX}{account_index:02d}-v1",
                        group_scope,
                        frozenset({"orders.query_group_leg", "orders.query_group_cancel"}),
                    ),
                    ResourceGrant(
                        f"{_EXEC_INSTANCE_PREFIX}{account_index:02d}-group-reservations",
                        "service.group_reservations",
                        n5_reservation_scope_ref,
                        group_scope,
                        frozenset(
                            {
                                "reservations.reserve",
                                "reservations.query",
                                "reservations.release",
                            }
                        ),
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
    try:
        journal = GatewayEventJournal(gateway_journal_path)
        worker_holder: dict[str, GatewayAccountSessionWorker] = {}

        def account_resources(component, _instance_id, options, _scope):
            nonlocal gateway_worker
            if component != "gateway" or options.get("role") != "account_session_worker":
                raise AssertionError("LOAD01 account host selected an unexpected component")
            gateway_worker = GatewayAccountSessionWorker(
                account_provider_for_worker,
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
                # The N6 quota wrapper treats provider timeout as its admission
                # deadline. Limit only this dedicated owner to the queue-wait
                # budget; SDK RPC calls keep their five-second timeout.
                request_timeout_seconds=(
                    _N6_QUERY_QUOTA_MAX_WAIT_SECONDS
                    if query_probe_modules is not None
                    else 5
                ),
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

        def execution_resources(component, _instance_id, options, _scope):
            nonlocal n5_group_runtime
            if component == "execution":
                role = options.get("role")
                if role == "account_owner":
                    return ComponentResources.borrowed(
                        {
                            "store": store,
                            "targets": execution_targets,
                            "dispatch": order_provider,
                            "admission": {
                                "order_gate": admission,
                                "cancel_gate": admission,
                            },
                        },
                        ready=lambda: store is not None,
                    )
                if role == "account_group_owner" and n5_group_runtime is not None:
                    return ComponentResources.borrowed(
                        n5_group_runtime.topology.execution_component_resources,
                        ready=lambda: n5_group_runtime is not None
                        and n5_group_runtime.ready,
                    )
            if component == "risk" and options.get("role") == "group_reservations":
                if n5_group_runtime is not None:
                    return ComponentResources.borrowed(
                        n5_group_runtime.topology.reservation_component_resources,
                        ready=lambda: n5_group_runtime is not None
                        and n5_group_runtime.ready,
                    )
            raise AssertionError("LOAD01 execution host selected an unexpected component")

        store = SqliteExecutionStore(execution_store_path)
        order_provider.attach_store(store)
        if n5_group_owner:
            from tests.unit._load01_n5_owner_resources import (
                build_n5_owner_group_runtime,
                wait_for_owner_control_command,
            )

            designated_index = account_index * _PER_ACCOUNT
            designated_strategy = next(
                item for item in strategy_audience if item["index"] == designated_index
            )
            designated_scope = execution_targets[
                WireScope(
                    _DOMAIN,
                    account_ref=account_ref,
                    strategy_ref=designated_strategy["strategy_ref"],
                )
            ]
            execution_instance_id = f"{_EXEC_INSTANCE_PREFIX}{account_index:02d}"
            reservation_instance_id = f"{execution_instance_id}-group-reservations"
            n5_group_runtime = build_n5_owner_group_runtime(
                account_index=account_index,
                account_ref=account_ref,
                strategy_index=designated_index,
                strategy_ref=designated_strategy["strategy_ref"],
                origin_strategy_run_id=n5_origin_strategy_run_id,
                execution_scope=designated_scope,
                ordinary_targets=execution_targets,
                portfolio_ref=_N5_PORTFOLIO_REF,
                reservation_scope_ref=n5_reservation_scope_ref,
                order_scope_ref=f"{_EXEC_ORDER_SCOPE_PREFIX}{account_index:02d}-v1",
                query_scope_ref=f"{_EXEC_QUERY_SCOPE_PREFIX}{account_index:02d}-v1",
                coordinator_identity=n5_coordinator_identity,
                reservation_instance_id=reservation_instance_id,
                execution_instance_id=execution_instance_id,
                metadata=n5_metadata,
                execution_store=store,
                execution_store_path=execution_store_path,
                dispatch=order_provider,
                admission={"order_gate": admission, "cancel_gate": admission},
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
            group_write_policies=(
                (n5_group_runtime.submit_policy, n5_group_runtime.cancel_policy)
                if n5_group_runtime is not None
                else ()
            ),
            group_reservation_policies=(
                (n5_group_runtime.reservation_policy,)
                if n5_group_runtime is not None
                else ()
            ),
            group_resolution_policies=(
                (n5_group_runtime.resolution_policy,)
                if n5_group_runtime is not None
                else ()
            ),
        )
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
                scope_provider,
                scope_environment,
                account_ref,
                _strategy_ref(strategy_index),
                _EXECUTION_DAY,
            )
            record = {
                "local_index": local_index,
                "strategy_index": strategy_index,
                "strategy_ref": _strategy_ref(strategy_index),
                "account_ref": account_ref,
                "scope_ref": _durable_scope_ref(account_index, local_index),
                "wire_scope": WireScope(
                    _DOMAIN,
                    account_ref=account_ref,
                    strategy_ref=_strategy_ref(strategy_index),
                ),
                "provider": scope_provider,
                "environment": scope_environment,
                "source_generation": None,
                "source_scope_key": execution_scope.key,
                "outbox_scope": None,
            }
            scope_records.append(record)
            worker_command = queue.Queue(maxsize=2)
            relay_worker_commands[local_index] = worker_command
            worker = threading.Thread(
                target=_load01_owner_relay_worker,
                args=(
                    account_index,
                    local_index,
                    record,
                    execution_store_path,
                    checkpoint_directory,
                    unused_relay_endpoint,
                    relay_abort_event,
                    worker_command,
                    relay_event_queue,
                ),
                name=f"load01-relay-{account_index:02d}-{local_index:02d}",
                daemon=False,
            )
            relay_worker_threads[local_index] = worker
            relay_worker_states[local_index] = "starting"
            worker.start()

        owner_phase = "owner-relay-readers-starting"
        source_ready: set[int] = set()
        source_deadline = time.monotonic() + 60.0
        while len(source_ready) != len(scope_records):
            if time.monotonic() >= source_deadline:
                raise TimeoutError("LOAD01 relay source-reader startup timed out")
            event = take_relay_event()
            if event is None:
                continue
            if event[0] == "error":
                remember_relay_worker_error(event)
                raise RuntimeError(
                    f"LOAD01 relay worker source startup failed: {event[2].get('code')}"
                )
            if event[0] != "source-ready" or event[1] not in relay_worker_threads:
                raise AssertionError("LOAD01 relay worker emitted an invalid source-ready event")
            local_index = event[1]
            if local_index in source_ready:
                raise AssertionError("LOAD01 relay worker duplicated source readiness")
            scope_records[local_index] = event[2]
            source_ready.add(local_index)
            relay_worker_states[local_index] = "source-ready"

        ready_message = (
            "ready",
            "owner",
            account_index,
            os.getpid(),
            account_host.endpoint,
            execution_host.endpoint,
            _account_status(account_provider_for_worker, gateway_worker, account_host),
            tuple(scope_records),
        )
        if n5_group_runtime is not None:
            group_route = dict(n5_group_runtime.route)
            group_route.update(
                {
                    "owner_service_id": execution_service_id,
                    "owner_instance_id": f"{_EXEC_INSTANCE_PREFIX}{account_index:02d}",
                    "owner_endpoint": execution_host.endpoint,
                    "owner_server_public": execution_server_keys[0],
                    "owner_peer": WireIdentity(
                        WireRole.SERVICE,
                        _EXEC_SERVICE_PRINCIPAL,
                        execution_service_id,
                    ),
                    "owner_scope_ref": execution_config.scope_ref,
                    "owner_security_ref": f"{_EXEC_SECURITY_PREFIX}{account_index:02d}v1",
                    "owner_registration_policy_ref": execution_config.registration_policy_ref,
                    "owner_service_requirements_ref": execution_config.service_requirements_ref,
                }
            )
            control.send((*ready_message, group_route))
        else:
            control.send(ready_message)

        if n5_group_runtime is not None:
            command = wait_owner_control(_N5_OWNER_CONTROL_WAIT_SECONDS)
            if command != ("arm-group",) or group_activation_receipt is not None:
                raise AssertionError("LOAD01 N5 owner expected one group arm command")
            group_activation_receipt = _activate_n5_owner_group_authority(
                execution_host,
                n5_group_runtime,
                account_index=account_index,
                coordinator_identity=n5_coordinator_identity,
            )
            control.send(
                ("group-armed", account_index, os.getpid(), group_activation_receipt)
            )

        command = wait_owner_control(_N5_OWNER_CONTROL_WAIT_SECONDS)
        if not isinstance(command, tuple) or not command or command[0] != "connect-monitor":
            raise AssertionError("LOAD01 owner did not receive Monitor relay activation")
        _, monitor_endpoint, monitor_server_public, relay_identity = command
        for local_index, worker_command in relay_worker_commands.items():
            worker_command.put(
                ("connect", monitor_endpoint, monitor_server_public, relay_identity)
            )

        owner_phase = "owner-relay-registering"
        connected: set[int] = set()
        connect_deadline = time.monotonic() + 90.0
        while len(connected) != len(scope_records):
            if time.monotonic() >= connect_deadline:
                raise TimeoutError("LOAD01 owner relay registration timed out")
            event = take_relay_event()
            if event is None:
                continue
            if event[0] == "error":
                remember_relay_worker_error(event)
                raise RuntimeError(
                    f"LOAD01 relay worker registration failed: {event[2].get('code')}"
                )
            if event[0] != "connected" or event[1] not in relay_worker_threads:
                raise AssertionError("LOAD01 relay worker emitted an invalid registration event")
            local_index = event[1]
            if local_index in connected or event[2].get("binding_count") != 1:
                raise AssertionError("LOAD01 relay worker registration cardinality changed")
            connected.add(local_index)
            relay_worker_states[local_index] = "connected"

        binding_count = len(connected)
        runtime_count = len(relay_worker_threads)
        if binding_count != len(scope_records) or runtime_count != len(scope_records):
            raise AssertionError("LOAD01 relay runtime/binding topology changed")
        control.send(("relay-ready", account_index, os.getpid(), binding_count, runtime_count, binding_count))
        owner_phase = "owner-active"

        last_report = time.monotonic()
        draining = False
        drain_deadline = 0.0
        while True:
            pump_owner_hosts()
            while True:
                try:
                    event = relay_event_queue.get_nowait()
                except queue.Empty:
                    break
                if event[0] == "error":
                    remember_relay_worker_error(event)
                    raise RuntimeError(
                        f"LOAD01 relay worker failed in {event[2].get('phase', 'unknown')}"
                    )
                if event[0] == "progress":
                    local_index, delivered = event[1], event[2]
                    relay_worker_states[local_index] = "connected"
                    scope_ref = scope_records[local_index]["scope_ref"]
                    relay_delivered_by_scope[scope_ref] = delivered
                    total_relayed = sum(relay_delivered_by_scope.values())
                else:
                    raise AssertionError("LOAD01 relay worker emitted an unexpected active event")

            now = time.monotonic()
            if control.poll(0):
                next_command = control.recv()
                if next_command == ("status",):
                    runtime_state = (
                        "registered"
                        if all(state == "connected" for state in relay_worker_states.values())
                        else "starting"
                    )
                    control.send(
                        (
                            "status",
                            account_index,
                            _account_status(account_provider_for_worker, gateway_worker, account_host),
                            order_provider.submitted,
                            order_provider.cancelled,
                            total_relayed,
                            runtime_state,
                            0,
                            0,
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
                    drain_deadline = time.monotonic() + _RELAY_DRAIN_DEADLINE_SECONDS
                    for worker_command in relay_worker_commands.values():
                        worker_command.put(("drain", drain_deadline))
                    control.send(
                        (
                            "owner-close-progress",
                            os.getpid(),
                            account_index,
                            "relay-drain",
                            0,
                            len(relay_worker_threads),
                        )
                    )
                else:
                    raise AssertionError("unknown LOAD01 owner control")

            if draining:
                completed: set[int] = set()
                while len(completed) != len(relay_worker_threads):
                    if time.monotonic() >= drain_deadline:
                        raise TimeoutError("LOAD01 owner relay workers exceeded shared drain deadline")
                    event = take_relay_event()
                    if event is None:
                        if control.poll(0):
                            status_command = control.recv()
                            if status_command == ("status",):
                                control.send(
                                    (
                                        "status",
                                        account_index,
                                        _account_status(account_provider_for_worker, gateway_worker, account_host),
                                        order_provider.submitted,
                                        order_provider.cancelled,
                                        total_relayed,
                                        "draining",
                                        0,
                                        0,
                                    )
                                )
                            elif status_command in {("close",), ("close", "diagnostic")}:
                                continue
                            else:
                                raise AssertionError("unknown LOAD01 owner control while draining")
                        continue
                    if event[0] == "error":
                        remember_relay_worker_error(event)
                        raise RuntimeError(
                            "LOAD01 relay worker drain failed: "
                            f"{event[2].get('code')} "
                            f"phase={event[2].get('phase')} "
                            f"error_type={event[2].get('error_type')} "
                            f"delivered={event[2].get('delivered')}"
                        )
                    if event[0] == "progress":
                        local_index, delivered = event[1], event[2]
                        scope_ref = scope_records[local_index]["scope_ref"]
                        relay_delivered_by_scope[scope_ref] = delivered
                        total_relayed = sum(relay_delivered_by_scope.values())
                        continue
                    if event[0] != "completed" or event[1] not in relay_worker_threads:
                        raise AssertionError("LOAD01 relay worker emitted an invalid drain event")
                    local_index = event[1]
                    if local_index in completed:
                        raise AssertionError("LOAD01 relay worker duplicated its drain receipt")
                    facts = event[2]
                    if facts.get("cleanup_errors"):
                        raise RuntimeError("LOAD01 relay worker reported cleanup failures")
                    relay_worker_facts_by_scope[local_index] = facts
                    relay_worker_states[local_index] = "completed"
                    relay_delivered_by_scope[facts["scope_ref"]] = int(facts["delivered"])
                    total_relayed = sum(relay_delivered_by_scope.values())
                    completed.add(local_index)
                    control.send(
                        (
                            "owner-close-progress",
                            os.getpid(),
                            account_index,
                            "relay-drain",
                            len(completed),
                            len(relay_worker_threads),
                        )
                    )

                for worker in relay_worker_threads.values():
                    while worker.is_alive():
                        remaining = drain_deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("LOAD01 relay worker thread did not join before deadline")
                        pump_owner_hosts()
                        worker.join(timeout=min(0.01, remaining))
                if any(worker.is_alive() for worker in relay_worker_threads.values()):
                    raise TimeoutError("LOAD01 relay worker thread remained alive after drain")
                if len(relay_worker_facts_by_scope) != len(scope_records):
                    raise AssertionError("LOAD01 relay worker close receipts are incomplete")
                for worker_facts in relay_worker_facts_by_scope.values():
                    worker_facts["relay_runtime"]["worker_joined"] = True
                source_evidence = [
                    {
                        **relay_worker_facts_by_scope[index]["source_evidence"],
                        "relay_delivered": relay_worker_facts_by_scope[index]["delivered"],
                    }
                    for index in sorted(relay_worker_facts_by_scope)
                ]
                total_relayed = sum(item["relay_delivered"] for item in source_evidence)
                normal_drain_completed = True
                owner_phase = "normal-drain-complete"
                close_phase = "source-snapshot"
                close_stages_completed.append(close_phase)
                close_phase = "relay-workers-close"
                close_stages_completed.append(close_phase)
                relay_runtime_state = "closed"
                relay_cleanup_failures = sum(
                    int(item["relay_runtime"]["cleanup_failures"])
                    for item in relay_worker_facts_by_scope.values()
                )
                relay_unregister_uncertainties = sum(
                    int(item["relay_runtime"]["unregister_uncertainties"])
                    for item in relay_worker_facts_by_scope.values()
                )
                relays.clear()
                readers.clear()
                break

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
                                account_provider_for_worker, gateway_worker, account_host
                            ),
                        },
                    )
                )
                last_report = now
            time.sleep(0.001)

        owner_phase = "normal-close"
        close_phase = "execution-host-close"
        control.send(
            (
                "owner-close-progress",
                os.getpid(),
                account_index,
                close_phase,
                len(relay_worker_threads),
                len(relay_worker_threads),
            )
        )
        execution_host.close()
        close_stages_completed.append(close_phase)
        execution_state = execution_host.state
        execution_cleanup_failures = execution_host.summary["cleanup_failures"]
        close_phase = "account-host-close"
        control.send(
            (
                "owner-close-progress",
                os.getpid(),
                account_index,
                close_phase,
                len(relay_worker_threads),
                len(relay_worker_threads),
            )
        )
        account_host.close()
        close_stages_completed.append(close_phase)
        account_state = account_host.state
        account_cleanup_failures = account_host.summary["cleanup_failures"]
        if n5_group_runtime is not None:
            close_phase = "n5-group-runtime-close"
            control.send(
                (
                    "owner-close-progress",
                    os.getpid(),
                    account_index,
                    close_phase,
                    len(relay_worker_threads),
                    len(relay_worker_threads),
                )
            )
            n5_group_runtime.close()
            close_stages_completed.append(close_phase)
        close_phase = "store-close"
        control.send(
            (
                "owner-close-progress",
                os.getpid(),
                account_index,
                close_phase,
                len(relay_worker_threads),
                len(relay_worker_threads),
            )
        )
        store.close()
        store = None
        close_stages_completed.append(close_phase)
        close_phase = "closed-ack-send"
        control.send(
            (
                "owner-close-progress",
                os.getpid(),
                account_index,
                close_phase,
                len(relay_worker_threads),
                len(relay_worker_threads),
            )
        )
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
                _account_status(account_provider_for_worker, gateway_worker, account_host),
                tuple(scope_records),
                tuple(source_evidence),
                _load01_trace_snapshot_or_none(rpc_trace),
                close_facts(),
            )
        )
    except BaseException as exc:
        rpc_timing_at_failure = _load01_failure_trace_snapshot_or_none(rpc_trace)
        failure_path = Path(execution_store_path).with_name(
            "single-owner-close-failure.json"
        )
        failure_record = _load01_owner_failure_record(
            exc, rpc_timing_at_failure, close_facts, failure_path
        )
        failure_facts = failure_record["failure"]
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

        relay_abort_event.set()
        for worker_command in relay_worker_commands.values():
            with suppress(queue.Full):
                worker_command.put_nowait(("abort",))
        worker_cleanup_deadline = time.monotonic() + _RELAY_DRAIN_DEADLINE_SECONDS
        while any(worker.is_alive() for worker in relay_worker_threads.values()):
            for host_name, host in (("account", account_host), ("execution", execution_host)):
                if host is not None and host.state not in {"closed", "failed"}:
                    cleanup_after_failure(
                        f"{host_name}-host-pump-during-worker-cleanup",
                        lambda host=host: host.serve_once(timeout_ms=0),
                    )
            while True:
                try:
                    event = relay_event_queue.get_nowait()
                except queue.Empty:
                    break
                if event[0] == "error":
                    remember_relay_worker_error(event)
                    exception_cleanup_errors.append(
                        {
                            "phase": "relay-worker-error",
                            "type": str(event[2].get("error_type", "worker-error")),
                            "code": str(event[2].get("code", "UNEXPECTED_ERROR")),
                            "delivered": event[2].get("delivered"),
                            "runtime": event[2].get("runtime"),
                            "timing": event[2].get("timing"),
                        }
                    )
            for worker in relay_worker_threads.values():
                if worker.is_alive():
                    remaining = worker_cleanup_deadline - time.monotonic()
                    if remaining > 0:
                        worker.join(timeout=min(0.01, remaining))
            if time.monotonic() >= worker_cleanup_deadline:
                exception_cleanup_errors.append(
                    {
                        "phase": "relay-worker-join",
                        "type": "TimeoutError",
                        "code": "WORKER_JOIN_DEADLINE_EXCEEDED",
                    }
                )
                break
        workers_stopped = not any(
            worker.is_alive() for worker in relay_worker_threads.values()
        )
        if workers_stopped:
            for worker_facts in relay_worker_facts_by_scope.values():
                runtime_facts = worker_facts.get("relay_runtime")
                if isinstance(runtime_facts, dict):
                    runtime_facts["worker_joined"] = True
        if workers_stopped:
            for local_index, relay in relays.items():
                cleanup_after_failure(f"legacy-relay-close:{local_index:02d}", relay.close)
            for local_index, reader in readers.items():
                cleanup_after_failure(f"legacy-reader-close:{local_index:02d}", reader.close)
        for phase, host in (
            ("execution-host-close", execution_host),
            ("account-host-close", account_host),
        ):
            if workers_stopped and host is not None and host.state not in {"closed", "failed"}:
                cleanup_after_failure(phase, host.close)
        if n5_group_runtime is not None:
            cleanup_after_failure("n5-group-runtime-close", n5_group_runtime.close)
        if store is not None:
            cleanup_after_failure("store-close", store.close)
        cleanup_ended_ns = rpc_trace.now()
        close_facts_after_cleanup, close_facts_after_error = (
            _load01_close_facts_for_failure_report(close_facts)
        )
        if close_facts_after_error is not None:
            failure_facts["close_facts_after_cleanup_projection_error"] = (
                close_facts_after_error
            )
        owner_diagnostic = {
            **failure_record,
            "cleanup_duration_ns": (
                max(0, cleanup_ended_ns - cleanup_started_ns)
                if type(cleanup_started_ns) is int and type(cleanup_ended_ns) is int
                else None
            ),
            "close_facts_after_exception_cleanup": close_facts_after_cleanup,
            "exception_cleanup_errors": exception_cleanup_errors,
        }
        try:
            json.dumps(owner_diagnostic, sort_keys=True, allow_nan=False)
        except BaseException as serialization_error:
            failure_facts["owner_diagnostic_serialization_error"] = (
                _load01_diagnostic_error_facts(
                    "owner-error-emission-serialization", serialization_error
                )
            )
            owner_diagnostic = {
                "evidence_level": failure_record["evidence_level"],
                "failure": {
                    "exception_chain": failure_facts["exception_chain"],
                    "traceback": failure_facts["traceback"],
                    "diagnostic_error": failure_facts[
                        "owner_diagnostic_serialization_error"
                    ],
                },
                "cleanup_duration_ns": owner_diagnostic["cleanup_duration_ns"],
                "close_facts_after_exception_cleanup": None,
                "exception_cleanup_errors": [],
            }
        _load01_send_owner_failure(control, account_index, exc, owner_diagnostic)
        raise
    finally:
        control.close()


def _strategy_process(
    control,
    index,
    raw_config,
    account_services,
    execution_services,
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
    quota_database_path=None,
    quota_wave_event=None,
):
    if profile_id in {"U0", "N1", "N2", "N3", "N4"}:
        _check_fixed_strategy_source_manifest(
            os.environ["BT_API_LOAD01_SOURCE_MANIFEST"], profile_id=profile_id
        )
    else:
        _check_fixed_source_manifest(
            os.environ["BT_API_LOAD01_SOURCE_MANIFEST"], profile_id=profile_id
        )
    rpc_trace = _install_load01_rpc_trace("strategy")
    profile = load01_profile(profile_id)
    planned_duration_seconds = 120 if mode == "pilot" else _FULL_LOAD_SECONDS
    account_indices = profile.accounts_for_strategy(index)
    account_index = account_indices[0]
    n5_group_strategy = (
        profile_id == "N5" and index in profile.expected_group_capable_strategies
    )
    if profile_id == "N5":
        if not isinstance(service_records, Mapping):
            raise ValueError("LOAD01 N5 strategy service records must include the group inputs")
        service_record_rows = service_records.get("services")
        if not isinstance(service_record_rows, Sequence) or isinstance(
            service_record_rows, (str, bytes)
        ):
            raise ValueError("LOAD01 N5 strategy service record list is invalid")
        n5_group_metadata = _load01_validate_n5_group_metadata(
            service_records.get("n5_group_metadata")
        )
        n5_group_service_record = (
            service_records.get("n5_coordinator") if n5_group_strategy else None
        )
        n5_group_route = (
            _load01_n5_group_service_binding(
                n5_group_service_record,
                strategy_index=index,
                account_index=account_index,
            )
            if n5_group_strategy
            else None
        )
    else:
        service_record_rows = service_records
        n5_group_metadata = None
        n5_group_service_record = None
        n5_group_route = None
    local_index = profile.strategy_indices_by_account[account_index].index(index)
    subscribed_market_keys = tuple(
        market_keys[topic_index] for topic_index in profile.subscribed_topic_indices(index)
    )
    subscribed_stream_ids = tuple(key.stream_id for key in subscribed_market_keys)
    account_ref = _ACCOUNT_REFS[account_index]
    strategy_ref = _strategy_ref(index)
    principal_ref = _principal_ref(index)
    runtime = None
    quota_wave = None
    quota_wave_module = None
    quota_wave_launch_attempted = False
    query_probe_modules = None
    server_time_client = None
    query_probe_attempted = False
    query_probe_outcomes = None
    query_probe_start_elapsed_seconds: float | None = None
    query_probe_started_monotonic_ns: int | None = None
    query_probe_completed_monotonic_ns: int | None = None
    account_clients: dict[int, ServiceAccountClient] = {}
    runtime_probe = None
    telemetry_clients: dict[int, ServiceTelemetryEmitter] = {}
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
    market_delivery_receipts: dict[str, dict[str, Any]] = {}
    market_watermark_request: dict[str, Any] | None = None
    market_watermark_result_sent = False
    last_market_watermark_check_ns = 0
    last_sequences: dict[str, int] = {}
    last_epochs: dict[str, str] = {}
    e2e_samples: list[float] = []
    order_samples: list[float] = []
    required_order_operations = (
        ("orders.submit", "orders.query", "orders.cancel")
        if profile_id == "N4"
        else ("orders.submit", "orders.cancel")
    )
    order_rpc_metrics = OperationLatencyCollector(
        sample_capacity=2048, required_operations=required_order_operations
    )
    n5_group_rpc_metrics = _Load01N5GroupRpcMetrics() if n5_group_strategy else None
    n5_group_metrics_state = (
        {
            "group_logical_count": 0,
            "group_success_count": 0,
            "group_failure_count": 0,
            "group_unresolved_count": 0,
            "group_logical_by_account": Counter(),
            "group_child_dispatch_count": 0,
            "group_child_dispatch_by_account": Counter(),
            "group_child_dispatch_count_by_leg": Counter(
                {leg_id: 0 for leg_id in N5_LEG_IDS}
            ),
            "group_child_outcome_by_leg": {
                leg_id: Counter({phase.value: 0 for phase in GroupLegPhase})
                for leg_id in N5_LEG_IDS
            },
            "group_child_execution_state_by_leg": {
                leg_id: Counter(
                    {
                        **{state.value: 0 for state in ExecutionState},
                        "DUPLICATE": 0,
                        "UNRESOLVED": 0,
                    }
                )
                for leg_id in N5_LEG_IDS
            },
            "group_child_intent_ids_by_leg": {
                leg_id: [] for leg_id in N5_LEG_IDS
            },
            "group_durable_records": [],
            "group_child_dispatch_latency_ns_by_account_strategy": None,
            "group_child_dispatch_latency_status": (
                "NOT_REGISTERED_BY_COORDINATOR_SNAPSHOT"
            ),
            "group_lifecycle_samples_ms": [],
            "rpc_collector": n5_group_rpc_metrics,
        }
        if n5_group_strategy
        else None
    )
    market_e2e_due_count = 0
    market_e2e_valid_count = 0
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
    market_recovery_observations: list[dict[str, Any]] = []
    slow_recovery_incident: dict[str, Any] | None = None
    slow_pause_observed: dict[str, float] | None = None
    account_reads = 0
    account_reads_by_account: Counter[int] = Counter()
    order_cycles = 0
    order_cycles_by_account: Counter[int] = Counter()
    n4_order_facts_by_stream: dict[str, dict[str, Any]] = {}
    order_ticks = 0
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
    account_identities: dict[int, dict[str, Any]] = {}
    load_start = 0.0
    next_account_read = 0.0
    next_order = 0.0
    next_telemetry = 0.0
    next_metrics = 0.0
    next_report = 0.0
    reconnect_windows_seen: set[int] = set()
    finished_normally = False
    normal_close_diagnostic: dict[str, Any] = {
        "phase": "normal-strategy-shutdown",
        "attempted_count": 0,
        "returned_count": 0,
        "current": None,
    }

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

    def capture_recovery_heads(incident: dict[str, Any]) -> None:
        capture_strategy_market_recovery_heads(
            runtime_probe,
            runtime,
            subscribed_market_keys,
            incident,
        )

    def reconnect_markets(elapsed: float, window: int) -> None:
        nonlocal markets, reconnect_gap_events, reconnect_gaps_by_stream
        began = time.perf_counter_ns()
        fault_started_ns = time.monotonic_ns()
        previous = dict(last_sequences)
        previous_cursors = {
            stream_id: (last_epochs[stream_id], last_sequences[stream_id])
            for stream_id in subscribed_stream_ids
        }
        for market in markets.values():
            market.close()
        markets = {}
        snapshots = market_clients_start()
        fault_ended_ns = time.monotonic_ns()
        lost = 0
        snapshot_cursors: dict[str, tuple[str, int] | None] = {
            stream_id: None for stream_id in subscribed_stream_ids
        }
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
                        snapshot_cursors[stream_id] = (
                            bootstrap.snapshot_epoch, bootstrap.snapshot_sequence
                        )
                    break
        reconnect_gap_events += lost
        recovery_ms = (time.perf_counter_ns() - began) / 1_000_000
        incident_id = f"reconnect-{index}-{window}"
        incident = new_recovery_incident(
            kind="reconnect",
            incident_id=incident_id,
            fault_started_monotonic_ns=fault_started_ns,
            prior_cursors=previous_cursors,
            snapshot_cursors=snapshot_cursors,
        )
        incident["fault_ended_monotonic_ns"] = fault_ended_ns
        incident["window_number"] = window
        capture_recovery_heads(incident)
        market_recovery_observations.append(incident)
        reconnect_records.append(
            {
                "window": window,
                "elapsed_seconds": elapsed,
                "recovery_ms": recovery_ms,
                "recovery_incident_id": incident_id,
                "head_capture_ms": (
                    incident["head_capture_completed_monotonic_ns"]
                    - incident["head_capture_started_monotonic_ns"]
                ) / 1_000_000,
                "head_rpc_count": incident["head_rpc_count"],
                "snapshot_skipped_sequence_count": (
                    sum(
                        row["snapshot_skipped_sequence_count"]
                        for row in incident["streams"].values()
                        if type(row["snapshot_skipped_sequence_count"]) is int
                    )
                    if all(
                        type(row["snapshot_skipped_sequence_count"]) is int
                        for row in incident["streams"].values()
                    )
                    else None
                ),
                "snapshot_skipped_sequence_unknown_stream_count": sum(
                    type(row["snapshot_skipped_sequence_count"]) is not int
                    for row in incident["streams"].values()
                ),
                "intentional_disconnect_gap_events": lost,
                "subscriptions_restored": len(markets),
            }
        )

    def read_market_events(*, pause: bool) -> str | None:
        nonlocal market_seen, duplicate_events, unrecovered_gap_count
        nonlocal unrecovered_gap_events, market_epoch_changes, max_queue_count
        nonlocal max_queue_bytes, max_market_poll_ms, market_poll_failure
        nonlocal market_e2e_due_count, market_e2e_valid_count
        stream_id = market_poll_scheduler.next_stream_id(enabled=not pause)
        if stream_id is None:
            return None
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
            return None
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
        receipt_monotonic_ns = time.monotonic_ns()
        record_market_delivery_receipt(
            market_recovery_observations,
            market_delivery_receipts,
            stream_id=stream_id,
            epoch=epoch,
            sequence=sequence,
            received_monotonic_ns=receipt_monotonic_ns,
        )
        market_receives[stream_id] += 1
        market_seen += 1
        if market_seen % 128 == 0:
            market_e2e_due_count += 1
            data = event.payload.get("data") if isinstance(event.payload, Mapping) else None
            published_ns = data.get("publish_ns") if isinstance(data, Mapping) else None
            observed_ns = time.perf_counter_ns()
            latency = _load01_market_e2e_latency_ms(published_ns, observed_ns)
            if latency is not None:
                market_e2e_valid_count += 1
                if len(e2e_samples) < market_sample_cap:
                    e2e_samples.append(latency)
                else:
                    slot = randomizer.randrange(market_e2e_valid_count)
                    if slot < market_sample_cap:
                        e2e_samples[slot] = latency
        return stream_id

    def snapshot_market_watermark_result(*, force: bool = False) -> None:
        nonlocal market_watermark_result_sent, last_market_watermark_check_ns
        request = market_watermark_request
        if request is None or market_watermark_result_sent:
            return
        snapshot_started_ns = time.monotonic_ns()
        deadline_ns = request["barrier_captured_monotonic_ns"] + 10_000_000_000
        targets = request["targets"]
        receipts_at_target = True
        impossible_receipt = False
        for stream_id, row in targets.items():
            receipt = market_delivery_receipts.get(stream_id)
            if type(receipt) is not dict:
                receipts_at_target = False
                continue
            if receipt.get("epoch") != row["epoch"]:
                receipts_at_target = False
                impossible_receipt = True
            elif type(receipt.get("sequence")) is not int:
                receipts_at_target = False
                impossible_receipt = True
            elif receipt["sequence"] != row["sequence"]:
                receipts_at_target = False
                impossible_receipt |= receipt["sequence"] > row["sequence"]
        if (
            not receipts_at_target
            and not impossible_receipt
            and snapshot_started_ns < deadline_ns
        ):
            return
        if (
            receipts_at_target
            and not force
            and snapshot_started_ns - last_market_watermark_check_ns < 50_000_000
            and snapshot_started_ns < deadline_ns
        ):
            return
        last_market_watermark_check_ns = snapshot_started_ns
        client_states = {
            stream_id: _load01_market_client_terminal_state(markets[stream_id])
            for stream_id in targets
        }
        runtime_state = {
            "healthy": runtime.healthy,
            "active_bindings": len(runtime.bindings),
            "expected_bindings": initial_bindings,
            "cleanup_failures": runtime.cleanup_failures,
            "unregister_uncertainties": runtime.unregister_uncertainties,
        }
        counter_deltas = {
            "duplicates": duplicate_events - request["duplicates"],
            "gap_count": unrecovered_gap_count - request["gap_count"],
            "gap_events": unrecovered_gap_events - request["gap_events"],
            "epoch_changes": market_epoch_changes - request["epoch_changes"],
        }
        receipts = {
            stream_id: dict(market_delivery_receipts[stream_id])
            for stream_id in targets
            if stream_id in market_delivery_receipts
        }
        sequence_cursors = {
            stream_id: last_sequences.get(stream_id) for stream_id in targets
        }
        epoch_cursors = {stream_id: last_epochs.get(stream_id) for stream_id in targets}
        failures = _load01_market_watermark_completion_failures(
            targets=targets,
            expected_stream_ids=tuple(targets),
            receipts=receipts,
            last_sequences=sequence_cursors,
            last_epochs=epoch_cursors,
            client_states=client_states,
            runtime_state=runtime_state,
            counter_deltas=counter_deltas,
            barrier_captured_monotonic_ns=request["barrier_captured_monotonic_ns"],
            caught_up_at_monotonic_ns=0,
            check_ack_time=False,
        )
        ack_ns = time.monotonic_ns()
        failures += _load01_market_watermark_time_failures(
            receipts=receipts,
            barrier_captured_monotonic_ns=request["barrier_captured_monotonic_ns"],
            caught_up_at_monotonic_ns=ack_ns,
        )
        transient_prefixes = (
            "receipt_coverage_invalid",
            "receipt_missing:",
            "receipt_behind_target:",
            "cursor_behind_target:",
            "queue_count_nonzero:",
            "queue_bytes_nonzero:",
            "pending_poll_replay_true_or_missing:",
            "pending_poll_gap_event_true_or_missing:",
        )
        hard_failures = tuple(
            failure
            for failure in failures
            if not failure.startswith(transient_prefixes)
        )
        deadline_expired = ack_ns >= deadline_ns
        if hard_failures or not failures or deadline_expired:
            final_failures = failures if deadline_expired else hard_failures
            if deadline_expired:
                final_failures = tuple(
                    dict.fromkeys((*final_failures, "catchup_deadline_expired"))
                )
            snapshot = {
                "complete": not final_failures,
                "failures": final_failures,
                "targets": {stream_id: dict(row) for stream_id, row in targets.items()},
                "receipts": receipts,
                "last_sequences": sequence_cursors,
                "last_epochs": epoch_cursors,
                "client_states": client_states,
                "runtime_state": runtime_state,
                "counter_deltas": counter_deltas,
                "caught_up_at_monotonic_ns": ack_ns,
            }
            sent_ns = time.monotonic_ns()
            snapshot["ack_sent_monotonic_ns"] = sent_ns
            if sent_ns > deadline_ns:
                snapshot["complete"] = False
                snapshot["failures"] = tuple(
                    dict.fromkeys(
                        (*snapshot["failures"], "catchup_deadline_expired_before_send")
                    )
                )
            control.send(
                (
                    "market-watermark-result",
                    index,
                    os.getpid(),
                    request["token"],
                    snapshot,
                )
            )
            market_watermark_result_sent = True

    def run_order_cycle(cycle: int) -> None:
        nonlocal order_cycles, order_failures
        for route_account_index in account_indices:
            route_local_index = profile.strategy_indices_by_account[
                route_account_index
            ].index(index)
            route_account_ref = _ACCOUNT_REFS[route_account_index]
            route_execution_service = execution_services[route_account_index]
            order_scope_ref = route_execution_service["order_scope_ref"]
            query_scope_ref = route_execution_service["query_scope_ref"]
            intent_id = (
                f"load01-{route_account_index:02d}-{route_local_index:02d}-"
                f"{cycle:07d}"
            )
            if (
                n5_group_strategy
                and profile.order_kind_for_cycle(index, cycle) == "group"
            ):
                group_state = n5_group_metrics_state
                group_id = (
                    f"load01-n5-group-a{route_account_index:02d}-"
                    f"s{index:03d}-c{cycle:07d}"
                )
                group_started_ns = time.perf_counter_ns()
                group_state["group_logical_count"] += 1
                group_state["group_logical_by_account"][route_account_index] += 1
                try:
                    deadline_ns = time.time_ns() + 30_000_000_000
                    request = build_n5_group_request(
                        group_id=group_id,
                        account_ref=route_account_ref,
                        scope=ExecutionScope(
                            "fixture",
                            "simulation",
                            route_account_ref,
                            strategy_ref,
                            _EXECUTION_DAY,
                        ),
                        metadata=n5_group_metadata,
                        deadline_ns=deadline_ns,
                    )
                    submitted = _load01_timed_n5_group_call(
                        n5_group_rpc_metrics,
                        "groups.submit",
                        lambda: submit_n5_group(
                            runtime,
                            profile,
                            strategy_index=index,
                            cycle=cycle,
                            request=request,
                            account_ref=route_account_ref,
                            portfolio_ref=n5_group_route["portfolio_ref"],
                            scope_ref=n5_group_route["scope_ref"],
                        ),
                    )
                    if (
                        not isinstance(submitted, Mapping)
                        or submitted.get("ok") is not True
                        or submitted.get("code") != "OK"
                    ):
                        raise AssertionError(
                            "LOAD01 N5 group submit was not acknowledged"
                        )
                    queried = _load01_timed_n5_group_call(
                        n5_group_rpc_metrics,
                        "groups.query",
                        lambda: query_n5_group(
                            runtime,
                            profile,
                            strategy_index=index,
                            cycle=cycle,
                            group_id=group_id,
                            portfolio_ref=n5_group_route["portfolio_ref"],
                            scope_ref=n5_group_route["scope_ref"],
                        ),
                    )
                    durable_record = _load01_n5_group_query_facts(
                        queried,
                        group_id=group_id,
                        portfolio_ref=n5_group_route["portfolio_ref"],
                        strategy_ref=strategy_ref,
                        account_ref=route_account_ref,
                        metadata=n5_group_metadata,
                    )
                    child_facts = durable_record["legs"]
                    if tuple(fact["leg_id"] for fact in child_facts) != N5_LEG_IDS:
                        raise AssertionError(
                            "LOAD01 N5 group query did not resolve the three registered legs"
                        )
                    group_state["group_success_count"] += 1
                    group_state["group_durable_records"].append(
                        {
                            key: value
                            for key, value in durable_record.items()
                            if key != "legs"
                        }
                    )
                    for fact in child_facts:
                        leg_id = fact["leg_id"]
                        distinct_child_intent_count = fact[
                            "distinct_durable_filled_child_intent_count"
                        ]
                        group_state["group_child_dispatch_count"] += (
                            distinct_child_intent_count
                        )
                        group_state["group_child_dispatch_by_account"][
                            route_account_index
                        ] += distinct_child_intent_count
                        group_state["group_child_dispatch_count_by_leg"][
                            leg_id
                        ] += distinct_child_intent_count
                        group_state["group_child_outcome_by_leg"][leg_id][
                            fact["phase"]
                        ] += 1
                        group_state["group_child_execution_state_by_leg"][leg_id][
                            fact["execution_state"]
                        ] += 1
                        group_state["group_child_intent_ids_by_leg"][leg_id].append(
                            fact["intent_id"]
                        )
                    order_cycles += 1
                    order_cycles_by_account[route_account_index] += 1
                except BaseException:
                    group_state["group_failure_count"] += 1
                    group_state["group_unresolved_count"] += 1
                    order_failures += 1
                    raise
                finally:
                    group_state["group_lifecycle_samples_ms"].append(
                        (time.perf_counter_ns() - group_started_ns) / 1_000_000
                    )
                continue
            n4 = profile.profile_id == "N4"
            if n4:
                topic_index = profile.order_topic_index(index, cycle)
                if topic_index is None:
                    raise AssertionError("LOAD01 N4 order cycle omitted its market key")
                market_key = market_keys[topic_index]
                instrument_id = market_key.instrument.instrument_id
                market_stream_id = market_key.stream_id
                lifecycle = "fill" if cycle == 1 else "cancel"
                order_facts = n4_order_facts_by_stream.setdefault(
                    market_stream_id,
                    {
                        "instrument_id": instrument_id,
                        "cycles": 0,
                        "submit_calls": 0,
                        "cumulative_fill_observations": 0,
                        "cancelled_orders": 0,
                        "query_count": 0,
                        "query_states": Counter(),
                        "journal_fingerprint_matches": 0,
                    },
                )
                if order_facts["instrument_id"] != instrument_id:
                    raise AssertionError("LOAD01 N4 stream resolved to multiple instruments")
                order_facts["cycles"] += 1
                order_facts["submit_calls"] += 1
            else:
                instrument_id = "LOAD01-USD"
                market_stream_id = None
                lifecycle = None
                order_facts = None
            order_started = time.perf_counter_ns()
            submitted = _load01_timed_order_call(
                order_rpc_metrics,
                runtime,
                "broker.orders",
                "orders.submit",
                _sample_intent(
                    intent_id,
                    instrument=instrument_id,
                    market_stream=market_stream_id,
                    lifecycle=lifecycle,
                ),
                account_ref=route_account_ref,
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
            if n4:
                expected_state = "FILLED" if lifecycle == "fill" else "ACKED"
                expected_filled_quantity = "1" if lifecycle == "fill" else "0"
                expected_average_price = "1" if lifecycle == "fill" else None
                if (
                    record.get("state") != expected_state
                    or record.get("filled_quantity") != expected_filled_quantity
                    or record.get("average_price") != expected_average_price
                    or record.get("is_terminal") is not (lifecycle == "fill")
                ):
                    order_failures += 1
                    raise AssertionError("LOAD01 N4 submit did not match its lifecycle facts")
                if lifecycle == "fill":
                    order_facts["cumulative_fill_observations"] += 1
                else:
                    order_facts["cancelled_orders"] += 1

                def query_and_verify(expected: str) -> None:
                    nonlocal order_failures
                    queried = _load01_timed_order_call(
                        order_rpc_metrics,
                        runtime,
                        "broker.order_query",
                        "orders.query",
                        {"intent_id": intent_id},
                        account_ref=route_account_ref,
                        scope_ref=query_scope_ref,
                    )
                    result = queried.get("result", {})
                    query_record = result.get("record") if result.get("found") else None
                    if (
                        queried.get("code") != "OK"
                        or queried.get("ok") is not True
                        or type(query_record) is not dict
                        or query_record.get("intent_id") != intent_id
                        or query_record.get("payload_sha256") != record.get("payload_sha256")
                        or query_record.get("provider_order_id") != provider_order_id
                        or query_record.get("state") != expected
                        or query_record.get("filled_quantity") != expected_filled_quantity
                        or query_record.get("average_price") != expected_average_price
                        or query_record.get("is_terminal") is not (expected != "ACKED")
                    ):
                        order_failures += 1
                        raise AssertionError(
                            f"LOAD01 N4 journal query did not match {expected} submit facts"
                        )
                    order_facts["query_count"] += 1
                    order_facts["query_states"][expected] += 1
                    order_facts["journal_fingerprint_matches"] += 1

                if lifecycle == "fill":
                    query_and_verify("FILLED")
                else:
                    query_and_verify("ACKED")
                    cancel_id = f"cancel-{intent_id}"
                    cancelled = _load01_timed_order_call(
                        order_rpc_metrics,
                        runtime,
                        "broker.orders",
                        "orders.cancel",
                        {
                            "cancel_id": cancel_id,
                            "target_intent_id": intent_id,
                            "provider_order_id": provider_order_id,
                            "metadata_version": "load01-synthetic-v1",
                            "tags": {"scenario": "LOAD01", "synthetic": "true"},
                        },
                        account_ref=route_account_ref,
                        scope_ref=order_scope_ref,
                    )
                    if cancelled.get("code") != "OK":
                        order_failures += 1
                        raise AssertionError("synthetic execution cancel was not acknowledged")
                    cancel_record = cancelled.get("result", {}).get("record")
                    if (
                        type(cancel_record) is not dict
                        or cancel_record.get("target_intent_id") != intent_id
                        or cancel_record.get("provider_order_id") != provider_order_id
                        or cancel_record.get("state") != "CANCELLED"
                    ):
                        order_failures += 1
                        raise AssertionError("LOAD01 N4 cancel did not persist a terminal result")
                    query_and_verify("CANCELLED")
            else:
                cancel_id = f"cancel-{intent_id}"
                cancelled = _load01_timed_order_call(
                    order_rpc_metrics,
                    runtime,
                    "broker.orders",
                    "orders.cancel",
                    {
                        "cancel_id": cancel_id,
                        "target_intent_id": intent_id,
                        "provider_order_id": provider_order_id,
                        "metadata_version": "load01-synthetic-v1",
                        "tags": {"scenario": "LOAD01", "synthetic": "true"},
                    },
                    account_ref=route_account_ref,
                    scope_ref=order_scope_ref,
                )
                if cancelled.get("code") != "OK":
                    order_failures += 1
                    raise AssertionError("synthetic execution cancel was not acknowledged")
            order_cycles += 1
            order_cycles_by_account[route_account_index] += 1
            elapsed_ms = (time.perf_counter_ns() - order_started) / 1_000_000
            if len(order_samples) < 512:
                order_samples.append(elapsed_ms)
            elif order_cycles % 8 == 0:
                order_samples[order_cycles % 512] = elapsed_ms

    try:
        config = parse_client_config(raw_config)
        scope_map = {
            _MARKET_SCOPE_REF: WireScope(
                _DOMAIN, account_ref=account_ref, strategy_ref=strategy_ref
            )
        }
        for route_account_index in account_indices:
            route_wire_scope = WireScope(
                _DOMAIN,
                account_ref=_ACCOUNT_REFS[route_account_index],
                strategy_ref=strategy_ref,
            )
            route_account_service = account_services[route_account_index]
            route_execution_service = execution_services[route_account_index]
            scope_map[route_account_service["scope_ref"]] = route_wire_scope
            scope_map[route_execution_service["order_scope_ref"]] = route_wire_scope
            scope_map[route_execution_service["query_scope_ref"]] = route_wire_scope
            scope_map[
                _strategy_telemetry_scope_ref(profile_id, index, route_account_index)
            ] = route_wire_scope
        if n5_group_strategy:
            scope_map[n5_group_route["scope_ref"]] = WireScope(
                _DOMAIN,
                account_refs=(account_ref,),
                portfolio_ref=n5_group_route["portfolio_ref"],
                strategy_ref=strategy_ref,
            )
        peer_map = {
            record["service_id"]: record["peer"] for record in service_record_rows
        }
        if n5_group_strategy:
            peer_map[n5_group_service_record["service_id"]] = n5_group_service_record[
                "peer"
            ]
        keys = {
            security_ref: ClientCurveKeys(*material)
            for security_ref, material in security_material.items()
        }
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref)
        account_operations = _ACCOUNT_OPS
        if profile_id == "N6" and index == 0:
            source_manifest = json.loads(
                Path(os.environ["BT_API_LOAD01_SOURCE_MANIFEST"]).read_text(
                    encoding="utf-8"
                )
            )
            query_probe_modules = _load_n6_query_quota_probe_modules(
                source_manifest["files"]
            )
            account_operations = _N6_ACCOUNT_QUERY_OPS
        requested_operations = {
            "store.market": _MARKET_OPS,
            "store.account": account_operations,
            "broker.orders": _ORDER_OPS,
            "broker.order_query": _QUERY_OPS,
            "runtime.telemetry": _TELEMETRY_OPS,
        }
        if n5_group_strategy:
            requested_operations["broker.order_groups"] = _N5_GROUP_OPS
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda scope_ref: scope_map[scope_ref],
            peer_resolver=lambda service_id: peer_map[service_id],
            transport_factory=ZmqTransportFactory(keys),
            run_id=n5_group_route["owner_run_id"] if n5_group_strategy else None,
            requested_operations=requested_operations,
        )
        if query_probe_modules is not None:
            server_time_client = query_probe_modules["client_type"](
                runtime,
                "store.account",
                account_ref=account_ref,
                expected_account=_ACCOUNT_KEYS[account_index],
                expected_scope_ref=account_services[account_index]["scope_ref"],
            )
        runtime_probe = _StartupRuntimeMaintenanceProbe(runtime)
        for route_account_index in account_indices:
            route_account_service = account_services[route_account_index]
            account_clients[route_account_index] = ServiceAccountClient(
                runtime,
                "store.account",
                account_ref=_ACCOUNT_REFS[route_account_index],
                expected_account=_ACCOUNT_KEYS[route_account_index],
                required_coverage=(_RESOURCE,),
                expected_scope_ref=route_account_service["scope_ref"],
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
        account_views = {}
        account_refresh_durations_ms = {}
        for route_account_index, client in account_clients.items():
            route_refresh_started_ns = time.perf_counter_ns()
            account_views[route_account_index] = client.refresh()
            account_refresh_durations_ms[route_account_index] = (
                time.perf_counter_ns() - route_refresh_started_ns
            ) / 1_000_000
        account_refresh_duration_ms = (
            time.perf_counter_ns() - account_refresh_started_ns
        ) / 1_000_000
        account_read_lease_snapshot = _runtime_lease_snapshot(runtime)
        if (
            not account_read_lease_snapshot["healthy"]
            or account_read_lease_snapshot["required_min_lease_remaining_ms"] <= 0
        ):
            raise _Load01StartupLeaseError()
        for route_account_index, account_view in account_views.items():
            account_key = account_view.observation.account_key
            if account_key != _ACCOUNT_KEYS[route_account_index]:
                raise AssertionError("LOAD01 strategy read another account session")
            account_identities[route_account_index] = account_key.to_dict()
        account_identity = account_identities[account_index]
        for route_account_index in account_indices:
            telemetry_clients[route_account_index] = ServiceTelemetryEmitter.from_config(
                runtime,
                config,
                scope_ref=_strategy_telemetry_scope_ref(
                    profile_id, index, route_account_index
                ),
            )
        initial_bindings = len(runtime.bindings)
        initial_degraded = startup.degraded_count
        if (
            initial_degraded
            or initial_bindings != profile.expected_bindings_per_strategy(index)
            or len(markets) != len(subscribed_market_keys)
        ):
            raise AssertionError("LOAD01 strategy did not activate its routes and profile topics")
        startup_maintenance_snapshot = runtime_probe.snapshot()
        runtime_probe.set_phase("steady-load")
        if profile_id == "N6":
            if quota_database_path is None or quota_wave_event is None:
                raise AssertionError("LOAD01 N6 worker is missing its shared quota-wave inputs")
            quota_wave_module = _load_n6_quota_wave_module()
            quota_wave = quota_wave_module.StrategyQuotaWave(quota_database_path, index)
        elif quota_database_path is not None or quota_wave_event is not None:
            raise AssertionError("LOAD01 non-N6 worker received quota-wave inputs")
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
                    "account_refresh_duration_ms_by_account": {
                        _ACCOUNT_REFS[route_account_index]: duration_ms
                        for route_account_index, duration_ms
                        in account_refresh_durations_ms.items()
                    },
                    "maintenance": startup_maintenance_snapshot,
                    "quota_wave_initialized": quota_wave is not None,
                },
                account_identities,
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
            if (
                quota_wave is not None
                and quota_wave_event.is_set()
                and not quota_wave_launch_attempted
            ):
                # start() creates only two bounded admission threads; it does
                # no SQLite admission work on this market/order loop.
                quota_wave_launch_attempted = True
                quota_wave.start()
            if quota_wave_launch_attempted:
                quota_wave.poll()
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
            if (
                profile_id == "N6"
                and index == 0
                and quota_wave_event is not None
                and quota_wave_event.is_set()
                and not query_probe_attempted
            ):
                if query_probe_modules is None or server_time_client is None:
                    raise AssertionError("LOAD01 N6 strategy zero has no bound SDK query client")
                query_probe_attempted = True
                query_probe_start_elapsed_seconds = max(
                    0.0, time.monotonic() - load_start
                )
                query_probe_started_monotonic_ns = time.perf_counter_ns()
                query_probe_outcomes = query_probe_modules["probe"].capture_two_server_time_queries(
                    server_time_client,
                    runtime,
                    _ACCOUNT_REFS[account_index],
                    query_probe_modules["client_error_type"],
                )
                query_probe_completed_monotonic_ns = time.perf_counter_ns()

            if not stop_event.is_set():
                if index == 0 and 30 <= elapsed < 45 and slow_pause_observed is None:
                    slow_pause_observed = {"pause_started_seconds": elapsed}
                    slow_recovery_incident = new_recovery_incident(
                        kind="slow_consumer",
                        incident_id=f"slow-{index}",
                        fault_started_monotonic_ns=time.monotonic_ns(),
                        prior_cursors={
                            stream_id: (last_epochs[stream_id], last_sequences[stream_id])
                            for stream_id in subscribed_stream_ids
                        },
                    )
                if (
                    index == 0
                    and elapsed >= 45
                    and slow_pause_observed is not None
                    and slow_recovery_incident is not None
                ):
                    slow_pause_observed.setdefault("pause_ended_seconds", elapsed)
                    slow_recovery_incident["fault_ended_monotonic_ns"] = time.monotonic_ns()
                    capture_recovery_heads(slow_recovery_incident)
                    market_recovery_observations.append(slow_recovery_incident)
                    slow_recovery_incident = None

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
                    order_ticks += 1
                    run_order_cycle(order_ticks)
                    next_order += order_interval
                    after_order = time.monotonic()
                    if next_order < after_order - order_interval / 2:
                        skipped = int((after_order - next_order) // order_interval) + 1
                        next_order += skipped * order_interval

                now = time.monotonic()
                if now >= next_account_read:
                    for route_account_index, client in account_clients.items():
                        read_started = time.perf_counter_ns()
                        refreshed = client.refresh()
                        if (
                            refreshed.observation.account_key
                            != _ACCOUNT_KEYS[route_account_index]
                        ):
                            raise AssertionError(
                                "LOAD01 periodic account read crossed sessions"
                            )
                        account_reads += 1
                        account_reads_by_account[route_account_index] += 1
                        account_samples.append(
                            (time.perf_counter_ns() - read_started) / 1_000_000
                        )
                    next_account_read = time.monotonic() + 60.0

                now = time.monotonic()
                if now >= next_telemetry:
                    for telemetry_client in telemetry_clients.values():
                        if not telemetry_client.enqueue_heartbeat():
                            telemetry_queue_drops += 1
                    next_telemetry = time.monotonic() + 30.0
                now = time.monotonic()
                if now >= next_metrics:
                    for telemetry_client in telemetry_clients.values():
                        if not telemetry_client.enqueue_metrics(
                            {
                                "market_events": market_seen,
                                "order_cycles": order_cycles,
                                "account_reads": account_reads,
                            }
                        ):
                            telemetry_queue_drops += 1
                    next_metrics = time.monotonic() + 60.0
            for telemetry_client in telemetry_clients.values():
                if telemetry_client.stats.queued_events:
                    flush = telemetry_client.flush(max_events=4)
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
                    last_epochs=last_epochs,
                    market_delivery_receipts=market_delivery_receipts,
                    order_cycles=order_cycles,
                    account_reads=account_reads,
                    e2e_samples=e2e_samples,
                    order_samples=order_samples,
                    order_rpc_metrics=order_rpc_metrics,
                    market_e2e_due_count=market_e2e_due_count,
                    market_e2e_valid_count=market_e2e_valid_count,
                    account_samples=account_samples,
                    duplicate_events=duplicate_events,
                    unrecovered_gap_count=unrecovered_gap_count,
                    unrecovered_gap_events=unrecovered_gap_events,
                    reconnect_gap_events=reconnect_gap_events,
                    reconnect_records=reconnect_records,
                    reconnect_gaps_by_stream=reconnect_gaps_by_stream,
                    slow_pause_observed=slow_pause_observed,
                    market_stream_ids=subscribed_stream_ids,
                    market_recovery_observations=market_recovery_observations,
                    profile_id=profile_id,
                    telemetry=telemetry_clients,
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
                    account_identities=account_identities,
                    order_cycles_by_account=order_cycles_by_account,
                    account_reads_by_account=account_reads_by_account,
                    runtime=runtime,
                    n5_group_metrics_state=n5_group_metrics_state,
                    planned_duration_seconds=planned_duration_seconds,
                )
                control.send(("progress", index, stats))
                next_report = time.monotonic() + 30.0

            if control.poll(0):
                command = control.recv()
                if command == ("close",) or command == ("close", "diagnostic"):
                    if command == ("close", "diagnostic"):
                        _send_load01_rpc_diagnostic(
                            control,
                            f"strategy-{index:03d}",
                            os.getpid(),
                            _load01_failure_trace_snapshot_or_none(rpc_trace),
                            "emergency",
                        )
                    break
                if (
                    isinstance(command, tuple)
                    and len(command) == 4
                    and command[0] == "catch-up-to-watermark"
                ):
                    if (
                        market_watermark_request is not None
                        or not stop_event.is_set()
                        or type(command[1]) is not int
                        or command[1] <= 0
                        or type(command[2]) is not int
                        or command[2] < 0
                    ):
                        raise AssertionError("LOAD01 terminal market request was invalid")
                    targets = _normalize_load01_market_watermarks(
                        command[3], subscribed_stream_ids
                    )
                    market_watermark_request = {
                        "token": command[1],
                        "barrier_captured_monotonic_ns": command[2],
                        "targets": targets,
                        "duplicates": duplicate_events,
                        "gap_count": unrecovered_gap_count,
                        "gap_events": unrecovered_gap_events,
                        "epoch_changes": market_epoch_changes,
                    }
                    snapshot_market_watermark_result(force=True)
                else:
                    raise AssertionError("unknown LOAD01 strategy control")
            # Normal work checks a fair bounded stream batch after orders,
            # account work, and telemetry. The short per-turn budget prevents
            # multiple synchronous head/replay RPCs from consuming a full loop.
            # Terminal catch-up keeps its one-round batch and absolute cutoff.
            market_poll_budget = _load01_terminal_market_poll_budget(
                stop_requested=stop_event.is_set(),
                terminal_request_active=market_watermark_request is not None,
                stream_count=len(subscribed_stream_ids),
            )
            if pause_market and not stop_event.is_set():
                market_poll_budget = 0
            terminal_poll_deadline_ns = (
                market_watermark_request["barrier_captured_monotonic_ns"]
                + 10_000_000_000
                if stop_event.is_set() and market_watermark_request is not None
                else None
            )
            market_poll_deadline_ns = terminal_poll_deadline_ns
            if (
                market_poll_deadline_ns is None
                and not stop_event.is_set()
                and market_poll_budget > 0
            ):
                market_poll_deadline_ns = (
                    time.monotonic_ns() + _STRATEGY_MARKET_POLL_BATCH_BUDGET_NS
                )
            _load01_run_bounded_market_poll_batch(
                read_market_events,
                poll_budget=market_poll_budget,
                poll_pause=False,
                deadline_monotonic_ns=market_poll_deadline_ns,
                monotonic_ns=time.monotonic_ns,
            )
            snapshot_market_watermark_result()
            if slow_pause_observed is not None and elapsed >= 45:
                slow_pause_observed.setdefault("pause_ended_seconds", elapsed)
            time.sleep(0.0005)

        final_market_queue_snapshot = capture_market_queue_snapshot(
            markets.values(),
            load_start_monotonic_seconds=load_start,
            monotonic_clock=time.monotonic,
        )
        normal_close_diagnostic["started_monotonic_ns"] = time.perf_counter_ns()
        if runtime_probe is not None:
            runtime_probe.set_phase("normal-strategy-shutdown")
        for stream_id, market in markets.items():
            close_started_ns = time.perf_counter_ns()
            normal_close_diagnostic["attempted_count"] += 1
            close_context = {
                "stream_id": _bounded_diagnostic_text(stream_id),
                "started_monotonic_ns": close_started_ns,
                "returned": False,
            }
            normal_close_diagnostic["current"] = close_context
            market.close()
            close_context["returned"] = True
            close_context["observed_monotonic_ns"] = time.perf_counter_ns()
            normal_close_diagnostic["returned_count"] += 1
        normal_close_diagnostic["ended_monotonic_ns"] = time.perf_counter_ns()
        markets.clear()
        if server_time_client is not None:
            server_time_client.close()
        for client in account_clients.values():
            client.close()
        for telemetry_client in telemetry_clients.values():
            telemetry_client.close()
        runtime.close()
        finished_normally = runtime.state == "closed"
        quota_wave_outcomes: tuple[Any, ...] = ()
        if quota_wave is not None:
            if not quota_wave_launch_attempted:
                raise AssertionError("LOAD01 N6 quota-wave trigger was never observed by its worker")
            quota_wave_outcomes = quota_wave.join(
                timeout_seconds=quota_wave_module.RESULT_COLLECTION_SECONDS
            )
            if not quota_wave.complete:
                raise AssertionError("LOAD01 N6 worker did not reap both quota admission threads")
        final_stats = _strategy_stats(
            index=index,
            market_receives=market_receives,
            last_sequences=last_sequences,
            last_epochs=last_epochs,
            market_delivery_receipts=market_delivery_receipts,
            order_cycles=order_cycles,
            account_reads=account_reads,
            e2e_samples=e2e_samples,
            order_samples=order_samples,
            order_rpc_metrics=order_rpc_metrics,
            market_e2e_due_count=market_e2e_due_count,
            market_e2e_valid_count=market_e2e_valid_count,
            account_samples=account_samples,
            duplicate_events=duplicate_events,
            unrecovered_gap_count=unrecovered_gap_count,
            unrecovered_gap_events=unrecovered_gap_events,
            reconnect_gap_events=reconnect_gap_events,
            reconnect_records=reconnect_records,
            reconnect_gaps_by_stream=reconnect_gaps_by_stream,
            slow_pause_observed=slow_pause_observed,
            market_stream_ids=subscribed_stream_ids,
            market_recovery_observations=market_recovery_observations,
            telemetry=telemetry_clients,
            telemetry_queue_drops=telemetry_queue_drops,
            telemetry_delivery_drops=telemetry_delivery_drops,
            telemetry_delivered=telemetry_delivered,
            max_queue_count=max_queue_count,
            max_queue_bytes=max_queue_bytes,
            market_queue_snapshot=final_market_queue_snapshot,
            max_market_poll_ms=max_market_poll_ms,
            account_identity=account_identity,
            account_identities=account_identities,
            order_cycles_by_account=order_cycles_by_account,
            account_reads_by_account=account_reads_by_account,
            runtime=runtime,
            profile_id=profile_id,
            include_samples=True,
            n5_group_metrics_state=n5_group_metrics_state,
            planned_duration_seconds=planned_duration_seconds,
        )
        if quota_wave is not None:
            final_stats["quota_admission_wave"] = {
                "started": True,
                "complete": quota_wave.complete,
                "outcomes": [
                    {
                        "strategy_index": row.strategy_index,
                        "request_index": row.request_index,
                        "outcome": row.outcome,
                        "subject_digest": row.subject_digest,
                        "run_digest": row.run_digest,
                        "elapsed_ns": row.elapsed_ns,
                    }
                    for row in quota_wave_outcomes
                ],
            }
        if query_probe_attempted:
            if (
                query_probe_outcomes is None
                or query_probe_started_monotonic_ns is None
                or query_probe_completed_monotonic_ns is None
            ):
                raise AssertionError("LOAD01 N6 server-time query result is incomplete")
            final_stats["server_time_query_probe"] = {
                "started": True,
                "scheduled_after_load_start_seconds": _N6_QUOTA_WAVE_START_SECONDS,
                "trigger_elapsed_seconds": query_probe_start_elapsed_seconds,
                "started_monotonic_ns": query_probe_started_monotonic_ns,
                "completed_monotonic_ns": query_probe_completed_monotonic_ns,
                "runtime_rpc_timeout_ms": _N6_QUERY_RPC_TIMEOUT_MS,
                "quota_max_queue_wait_seconds": _N6_QUERY_QUOTA_MAX_WAIT_SECONDS,
                "outcomes": list(query_probe_outcomes),
            }
        if profile.profile_id == "N4":
            n4_execution_by_stream = {}
            for stream_id, facts in sorted(n4_order_facts_by_stream.items()):
                query_states = dict(sorted(facts["query_states"].items()))
                filled = facts["cumulative_fill_observations"]
                cancelled = facts["cancelled_orders"]
                if (
                    facts["submit_calls"] != facts["cycles"]
                    or filled + cancelled != facts["cycles"]
                    or query_states.get("FILLED", 0) != filled
                    or query_states.get("ACKED", 0) != cancelled
                    or query_states.get("CANCELLED", 0) != cancelled
                    or facts["query_count"] != filled + 2 * cancelled
                    or facts["journal_fingerprint_matches"] != facts["query_count"]
                ):
                    raise AssertionError(
                        f"LOAD01 N4 lifecycle/query facts diverged for {stream_id}"
                    )
                n4_execution_by_stream[stream_id] = {
                    **{
                        key: value
                        for key, value in facts.items()
                        if key != "query_states"
                    },
                    "query_states": query_states,
                }
            final_stats["n4_execution_by_stream"] = n4_execution_by_stream
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
        failure_observed_ns = time.perf_counter_ns()
        failure_phase = (
            _bounded_diagnostic_text(runtime_probe.phase)
            if runtime_probe is not None
            else "process-initialization"
        )
        try:
            failure_safe_code = _safe_diagnostic_code(exc)
        except BaseException:
            failure_safe_code = "UNEXPECTED_ERROR"
        try:
            runtime_lease_failure_snapshot = (
                _runtime_lease_snapshot(runtime) if runtime is not None else None
            )
        except BaseException:
            runtime_lease_failure_snapshot = None
        first_failure_diagnostic = {
            "phase": failure_phase,
            "observed_monotonic_ns": failure_observed_ns,
            "elapsed_since_load_ms": (
                round(max(0.0, time.monotonic() - load_start) * 1000, 3)
                if load_start > 0
                else None
            ),
            "safe_error_code": failure_safe_code,
            "exception_type": _bounded_diagnostic_text(type(exc).__name__),
        }
        close_context = normal_close_diagnostic.get("current")
        if type(close_context) is dict and close_context.get("returned") is False:
            close_context.update(
                {
                    "observed_monotonic_ns": failure_observed_ns,
                    "duration_ms": round(
                        max(0, failure_observed_ns - close_context["started_monotonic_ns"])
                        / 1_000_000,
                        3,
                    ),
                    "safe_error_code": failure_safe_code,
                }
            )
        if runtime_probe is not None:
            first_failure_diagnostic.update(runtime_probe.rpc_error_diagnostic())
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
            "first_failure": first_failure_diagnostic,
            "normal_close": normal_close_diagnostic,
            "runtime_lease_at_failure": runtime_lease_failure_snapshot,
            "safe_error_code": failure_safe_code,
            "order_rpc_metrics": order_rpc_metrics.summary(),
            "market_poll_failure": market_poll_failure,
            "rpc_timing_at_failure": rpc_timing_at_failure,
        }
        cleanup_diagnostic: dict[str, Any] = {
            "phase": "exception-cleanup",
            "started_monotonic_ns": time.perf_counter_ns(),
            "market_clients": [],
            "account_clients": {},
            "server_time_query_client": None,
            "telemetry_clients": {},
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
        if server_time_client is not None:
            try:
                server_time_client.close()
                cleanup_diagnostic["server_time_query_client"] = {
                    "close_returned": True
                }
            except BaseException as cleanup_exc:
                cleanup_diagnostic["server_time_query_client"] = {
                    "close_returned": False,
                    "safe_error_code": _safe_diagnostic_code(cleanup_exc),
                }
        for route_account_index, account_client in account_clients.items():
            try:
                account_client.close()
                cleanup_diagnostic["account_clients"][
                    _ACCOUNT_REFS[route_account_index]
                ] = {"close_returned": True}
            except BaseException as cleanup_exc:
                cleanup_diagnostic["account_clients"][
                    _ACCOUNT_REFS[route_account_index]
                ] = {
                    "close_returned": False,
                    "safe_error_code": _safe_diagnostic_code(cleanup_exc),
                }
        for route_account_index, telemetry_client in telemetry_clients.items():
            try:
                telemetry_client.close()
                cleanup_diagnostic["telemetry_clients"][
                    _ACCOUNT_REFS[route_account_index]
                ] = {
                    "close_returned": True,
                    "state": telemetry_client.stats.state,
                }
            except BaseException as cleanup_exc:
                cleanup_diagnostic["telemetry_clients"][
                    _ACCOUNT_REFS[route_account_index]
                ] = {
                    "close_returned": False,
                    "safe_error_code": _safe_diagnostic_code(cleanup_exc),
                    "state": telemetry_client.stats.state,
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
        if quota_wave is not None:
            quota_cleanup: dict[str, Any] = {
                "launch_attempted": quota_wave_launch_attempted,
                "complete": False,
            }
            if quota_wave_launch_attempted:
                try:
                    quota_outcomes = quota_wave.join(
                        timeout_seconds=quota_wave_module.RESULT_COLLECTION_SECONDS
                    )
                    quota_cleanup.update(
                        {
                            "join_returned": True,
                            "complete": quota_wave.complete,
                            "outcome_count": len(quota_outcomes),
                        }
                    )
                except BaseException as quota_exc:
                    quota_cleanup.update(
                        {
                            "join_returned": False,
                            "thread_custody_uncertain": True,
                            "safe_error_code": _safe_diagnostic_code(quota_exc),
                            "exception_type": _bounded_diagnostic_text(
                                type(quota_exc).__name__
                            ),
                        }
                    )
            child_diagnostic["quota_admission_wave_cleanup"] = quota_cleanup
        cleanup_diagnostic["ended_monotonic_ns"] = time.perf_counter_ns()
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

    _check_fixed_strategy_source_manifest(
        os.environ["BT_API_LOAD01_SOURCE_MANIFEST"], profile_id="U0"
    )
    _install_load01_rpc_trace("strategy")
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
    last_epochs,
    market_delivery_receipts,
    order_cycles,
    account_reads,
    e2e_samples,
    order_samples,
    order_rpc_metrics,
    market_e2e_due_count,
    market_e2e_valid_count,
    account_samples,
    duplicate_events,
    unrecovered_gap_count,
    unrecovered_gap_events,
    reconnect_gap_events,
    reconnect_records,
    reconnect_gaps_by_stream,
    slow_pause_observed,
    market_stream_ids,
    market_recovery_observations,
    telemetry,
    telemetry_queue_drops,
    telemetry_delivery_drops,
    telemetry_delivered,
    max_queue_count,
    max_queue_bytes,
    market_queue_snapshot,
    max_market_poll_ms,
    account_identity,
    account_identities,
    order_cycles_by_account,
    account_reads_by_account,
    runtime,
    profile_id="U0",
    include_samples=False,
    n5_group_metrics_state=None,
    planned_duration_seconds=_FULL_LOAD_SECONDS,
):
    telemetry_stats = tuple(client.stats for client in telemetry.values())
    profile = load01_profile(profile_id)
    primary_account_index = profile.accounts_for_strategy(index)[0]
    telemetry_states = {item.state for item in telemetry_stats}
    telemetry_state = (
        next(iter(telemetry_states)) if len(telemetry_states) == 1 else "degraded"
    )
    n5_group_metrics = None
    if profile_id == "N5":
        group_expected = _load01_expected_group_operation_counts(
            profile, index, planned_duration_seconds
        )
        group_state = n5_group_metrics_state or {}
        group_logical_by_account = group_state.get("group_logical_by_account", Counter())
        group_child_by_account = group_state.get("group_child_dispatch_by_account", Counter())
        group_child_by_leg = group_state.get(
            "group_child_dispatch_count_by_leg", Counter({leg_id: 0 for leg_id in N5_LEG_IDS})
        )
        group_outcomes_by_leg = group_state.get(
            "group_child_outcome_by_leg",
            {
                leg_id: Counter({phase.value: 0 for phase in GroupLegPhase})
                for leg_id in N5_LEG_IDS
            },
        )
        group_execution_states_by_leg = group_state.get(
            "group_child_execution_state_by_leg",
            {
                leg_id: Counter(
                    {
                        **{state.value: 0 for state in ExecutionState},
                        "DUPLICATE": 0,
                        "UNRESOLVED": 0,
                    }
                )
                for leg_id in N5_LEG_IDS
            },
        )
        durable_records = list(group_state.get("group_durable_records", ()))
        durable_group_ids = [row["group_id"] for row in durable_records]
        if len(set(durable_group_ids)) != len(durable_group_ids):
            raise AssertionError("LOAD01 N5 group durable identities are duplicated")
        for leg_id in N5_LEG_IDS:
            intent_ids = group_state.get("group_child_intent_ids_by_leg", {}).get(
                leg_id, ()
            )
            if len(set(intent_ids)) != len(intent_ids):
                raise AssertionError(
                    "LOAD01 N5 group child intent identities are duplicated"
                )
        n5_group_metrics = {
            "enabled": index in profile.expected_group_capable_strategies,
            "expected": group_expected,
            "expected_logical_group_count": group_expected["group_logical_count"],
            "expected_child_dispatch_count": group_expected[
                "group_child_dispatch_count"
            ],
            "observed": {
                "group_logical_count": int(group_state.get("group_logical_count", 0)),
                "group_success_count": int(group_state.get("group_success_count", 0)),
                "group_failure_count": int(group_state.get("group_failure_count", 0)),
                "group_unresolved_count": int(group_state.get("group_unresolved_count", 0)),
                "group_child_dispatch_count": int(
                    group_state.get("group_child_dispatch_count", 0)
                ),
                "group_child_dispatch_count_source": (
                    "distinct_durable_filled_child_intent_observation_not_provider_write_log"
                ),
            },
            "observed_logical_group_count": int(
                group_state.get("group_logical_count", 0)
            ),
            "observed_child_dispatch_count": int(
                group_state.get("group_child_dispatch_count", 0)
            ),
            "group_child_dispatch_count_source": (
                "distinct_durable_filled_child_intent_observation_not_provider_write_log"
            ),
            "group_child_provider_write_count": None,
            "group_child_provider_write_count_status": "NOT_REGISTERED_FROM_OWNER_LOG",
            "group_logical_count_by_account": {
                _ACCOUNT_REFS[account_index]: int(group_logical_by_account[account_index])
                for account_index in profile.accounts_for_strategy(index)
            },
            "group_child_dispatch_count_by_account": {
                _ACCOUNT_REFS[account_index]: int(group_child_by_account[account_index])
                for account_index in profile.accounts_for_strategy(index)
            },
            "group_child_dispatch_count_by_leg": {
                leg_id: int(group_child_by_leg[leg_id]) for leg_id in N5_LEG_IDS
            },
            "group_child_outcome_by_leg": {
                leg_id: dict(sorted(group_outcomes_by_leg[leg_id].items()))
                for leg_id in N5_LEG_IDS
            },
            "group_child_execution_state_by_leg": {
                leg_id: dict(sorted(group_execution_states_by_leg[leg_id].items()))
                for leg_id in N5_LEG_IDS
            },
            "group_child_routes_by_leg": {
                leg_id: {
                    "account_ref": (
                        _ACCOUNT_REFS[primary_account_index]
                        if index in profile.expected_group_capable_strategies
                        else None
                    ),
                    "strategy_ref": (
                        _strategy_ref(index)
                        if index in profile.expected_group_capable_strategies
                        else None
                    ),
                    "portfolio_ref": (
                        _N5_PORTFOLIO_REF
                        if index in profile.expected_group_capable_strategies
                        else None
                    ),
                    "scope_ref": (
                        f"load01-n5-group-scope-a{primary_account_index:02d}-v1"
                        if index in profile.expected_group_capable_strategies
                        else None
                    ),
                    "expected_dispatch_count": group_expected[
                        "group_child_dispatch_count_by_leg"
                    ][leg_id],
                    "observed_dispatch_count": int(group_child_by_leg[leg_id]),
                    "observed_dispatch_count_source": (
                        "distinct_durable_filled_child_intent_observation_not_provider_write_log"
                    ),
                    "intent_ids": list(
                        group_state.get("group_child_intent_ids_by_leg", {}).get(
                            leg_id, ()
                        )
                    ),
                }
                for leg_id in N5_LEG_IDS
            },
            "group_durable_record_count": len(durable_records),
            "group_durable_records": durable_records,
            "group_child_dispatch_latency_ns_by_account_strategy": group_state.get(
                "group_child_dispatch_latency_ns_by_account_strategy"
            ),
            "group_child_dispatch_latency_status": group_state.get(
                "group_child_dispatch_latency_status",
                "NOT_REGISTERED_BY_COORDINATOR_SNAPSHOT",
            ),
            "group_lifecycle_ms": _percentile_summary(
                group_state.get("group_lifecycle_samples_ms", [])
            ),
            "group_rpc_metrics": _load01_n5_group_rpc_metrics_report(
                group_state.get("rpc_collector") if n5_group_metrics_state is not None else None
            ),
        }
    return {
        "index": index,
        "account_index": primary_account_index,
        "account_indices": list(profile.accounts_for_strategy(index)),
        "local_index": profile.strategy_indices_by_account[
            primary_account_index
        ].index(index),
        "market_events": sum(market_receives.values()),
        "market_by_stream": dict(market_receives),
        "last_sequences": dict(last_sequences),
        "last_epochs": dict(last_epochs),
        "market_delivery_receipts": {
            stream_id: dict(receipt)
            for stream_id, receipt in market_delivery_receipts.items()
        },
        "order_cycles": order_cycles,
        "order_cycles_by_account": {
            _ACCOUNT_REFS[account_index]: int(order_cycles_by_account[account_index])
            for account_index in profile.accounts_for_strategy(index)
        },
        "account_reads": account_reads,
        "account_reads_by_account": {
            _ACCOUNT_REFS[account_index]: int(account_reads_by_account[account_index])
            for account_index in profile.accounts_for_strategy(index)
        },
        "market_e2e_ms": _percentile_summary(e2e_samples),
        "order_lifecycle_ms": _percentile_summary(order_samples),
        "market_e2e_due_count": market_e2e_due_count,
        "market_e2e_valid_count": market_e2e_valid_count,
        "order_rpc_metrics": order_rpc_metrics.summary() if include_samples else None,
        "n5_group_metrics": n5_group_metrics,
        "account_read_ms": _percentile_summary(account_samples),
        "duplicate_events": duplicate_events,
        "unrecovered_gap_count": unrecovered_gap_count,
        "unrecovered_gap_events": unrecovered_gap_events,
        "intentional_reconnect_gap_events": reconnect_gap_events,
        "intentional_reconnect_gaps_by_stream": dict(reconnect_gaps_by_stream),
        "reconnect_records": list(reconnect_records),
        "slow_pause": slow_pause_observed,
        "market_stream_ids": (
            list(market_stream_ids) if include_samples else None
        ),
        "market_recovery_observations": (
            list(market_recovery_observations) if include_samples else []
        ),
        "market_queue_max_count": max_queue_count,
        "market_queue_max_bytes": max_queue_bytes,
        "market_queue_snapshot": market_queue_snapshot,
        "market_poll_max_ms": max_market_poll_ms,
        "telemetry": {
            "queued_events": sum(item.queued_events for item in telemetry_stats),
            "queued_bytes": sum(item.queued_bytes for item in telemetry_stats),
            "dropped_queue_total": sum(
                item.dropped_queue_total for item in telemetry_stats
            ),
            "dropped_delivery_total": sum(
                item.dropped_delivery_total for item in telemetry_stats
            ),
            "delivered_total": sum(item.delivered_total for item in telemetry_stats),
            "state": telemetry_state,
        },
        "account_identity": account_identity,
        "account_identities": {
            _ACCOUNT_REFS[account_index]: account_identities[account_index]
            for account_index in profile.accounts_for_strategy(index)
        },
        "runtime_state": runtime.state,
        "runtime_cleanup_failures": runtime.cleanup_failures,
        "runtime_unregister_uncertainties": runtime.unregister_uncertainties,
        "active_bindings": len(runtime.bindings),
        "market_e2e_samples_ms": list(e2e_samples) if include_samples else None,
        "order_samples_ms": list(order_samples) if include_samples else None,
        "account_samples_ms": list(account_samples) if include_samples else None,
    }



def _load01_order_rpc_outcome(error: BaseException) -> str:
    """Classify only the two transport causes that prove a timeout."""

    if type(error) is RequestNotSentError:
        return "timeout" if error.cause_code == "send_timeout" else "error"
    if type(error) is RequestOutcomeUnknownError:
        return "timeout" if error.cause_code == "response_timeout" else "error"
    if type(error) is ServiceRuntimeRequestNotSentError:
        cause = error.__context__
        if (
            type(cause) is RequestNotSentError
            and cause.cause_code == "send_timeout"
        ):
            return "timeout"
    return "error"


def _load01_timed_order_call(
    collector: OperationLatencyCollector,
    runtime: ServiceClientRuntime,
    mount: str,
    operation: str,
    payload: Mapping[str, Any],
    **kwargs: Any,
) -> Mapping[str, Any]:
    """Measure one actual order RPC and preserve its return/exception verbatim."""

    started_ns = time.perf_counter_ns()
    try:
        result = runtime.call(mount, operation, payload, **kwargs)
    except BaseException as exc:
        elapsed_ns = max(0, time.perf_counter_ns() - started_ns)
        try:
            outcome = _load01_order_rpc_outcome(exc)
        except BaseException:
            # Metrics are diagnostic only and must never replace the RPC error.
            outcome = "error"
        collector.record(operation, elapsed_ns, outcome)
        raise
    elapsed_ns = max(0, time.perf_counter_ns() - started_ns)
    outcome = (
        "success"
        if (
            isinstance(result, Mapping)
            and result.get("ok") is True
            and result.get("code") == "OK"
        )
        else "error"
    )
    collector.record(operation, elapsed_ns, outcome)
    return result


class _Load01N5GroupRpcMetrics:
    """Keep group RPC samples isolated while reusing the accepted collector."""

    _ALIASES = {
        "groups.submit": "orders.submit",
        "groups.query": "orders.query",
    }

    def __init__(self) -> None:
        self._collector = OperationLatencyCollector(
            sample_capacity=2048,
            required_operations=("orders.submit", "orders.query"),
        )

    def record(self, operation: str, elapsed_ns: int, outcome: str) -> None:
        try:
            alias = self._ALIASES[operation]
        except KeyError as exc:
            raise ValueError("LOAD01 N5 group RPC operation is not registered") from exc
        self._collector.record(alias, elapsed_ns, outcome)

    def summary(self) -> dict[str, Any]:
        raw = self._collector.summary()
        operations = {
            operation: raw["operations"][alias]
            for operation, alias in self._ALIASES.items()
        }
        reverse = {alias: operation for operation, alias in self._ALIASES.items()}
        return {
            **raw,
            "measurement_scope": "single_strategy_n5_group_client",
            "required_operations": list(self._ALIASES),
            "missing_required_operations": [
                reverse[operation]
                for operation in raw["missing_required_operations"]
                if operation in reverse
            ],
            "unobserved_operations": [
                reverse[operation]
                for operation in raw["unobserved_operations"]
                if operation in reverse
            ],
            "operations": operations,
            "operation_aliases_are_collector_internal_only": True,
        }


def _load01_timed_n5_group_call(
    collector: _Load01N5GroupRpcMetrics,
    operation: str,
    invoke,
) -> Mapping[str, Any]:
    """Measure one helper-mediated group RPC and preserve its result/error."""

    started_ns = time.perf_counter_ns()
    try:
        result = invoke()
    except BaseException as exc:
        elapsed_ns = max(0, time.perf_counter_ns() - started_ns)
        try:
            outcome = _load01_order_rpc_outcome(exc)
        except BaseException:
            outcome = "error"
        collector.record(operation, elapsed_ns, outcome)
        raise
    elapsed_ns = max(0, time.perf_counter_ns() - started_ns)
    outcome = (
        "success"
        if isinstance(result, Mapping)
        and result.get("ok") is True
        and result.get("code") == "OK"
        else "error"
    )
    collector.record(operation, elapsed_ns, outcome)
    return result


def _load01_scheduled_order_cycles(profile, strategy_index: int, duration_seconds: int) -> int:
    interval = profile.order_interval_seconds_by_strategy[strategy_index]
    phase = profile.order_phase_seconds_by_strategy[strategy_index]
    remaining = max(0.0, float(duration_seconds) - phase)
    return ceil(remaining / interval) if remaining else 0


def _load01_expected_order_operation_counts(
    profile, strategy_index: int, duration_seconds: int
) -> dict[str, int]:
    """Derive scheduled RPC counts from the selected profile, not observations."""

    cycles = _load01_scheduled_order_cycles(profile, strategy_index, duration_seconds)
    route_count = len(profile.accounts_for_strategy(strategy_index))
    group_cycles = (
        sum(
            profile.order_kind_for_cycle(strategy_index, cycle) == "group"
            for cycle in range(1, cycles + 1)
        )
        if profile.profile_id == "N5"
        else 0
    )
    single_cycles = cycles - group_cycles
    counts = {
        "orders.submit": single_cycles * route_count,
        "orders.query": 0,
        "orders.cancel": single_cycles * route_count,
    }
    if profile.profile_id == "N4":
        counts["orders.cancel"] = max(0, cycles - 1) * route_count
        counts["orders.query"] = max(0, 2 * cycles - 1) * route_count
    return counts


def _load01_expected_group_operation_counts(
    profile, strategy_index: int, duration_seconds: int
) -> dict[str, Any]:
    """Derive logical groups and three child dispatches from the N5 schedule."""

    cycles = _load01_scheduled_order_cycles(profile, strategy_index, duration_seconds)
    route_count = len(profile.accounts_for_strategy(strategy_index))
    group_cycles = (
        sum(
            profile.order_kind_for_cycle(strategy_index, cycle) == "group"
            for cycle in range(1, cycles + 1)
        )
        if profile.profile_id == "N5"
        else 0
    )
    logical_groups = group_cycles * route_count
    child_dispatches = logical_groups * len(N5_LEG_IDS)
    return {
        "groups.submit": logical_groups,
        "groups.query": logical_groups,
        "group_logical_count": logical_groups,
        "group_child_dispatch_count": child_dispatches,
        "group_logical_count_by_account": {
            _ACCOUNT_REFS[account_index]: group_cycles
            for account_index in profile.accounts_for_strategy(strategy_index)
        },
        "group_child_dispatch_count_by_account": {
            _ACCOUNT_REFS[account_index]: group_cycles * len(N5_LEG_IDS)
            for account_index in profile.accounts_for_strategy(strategy_index)
        },
        "group_child_dispatch_count_by_leg": {
            leg_id: logical_groups for leg_id in N5_LEG_IDS
        },
    }


def _load01_n5_group_rpc_metrics_report(
    collector: _Load01N5GroupRpcMetrics | None,
) -> dict[str, Any]:
    summary = (collector or _Load01N5GroupRpcMetrics()).summary()
    operations = {}
    for operation in ("groups.submit", "groups.query"):
        row = summary["operations"][operation]
        percentiles_ns = row["percentiles_ns"]
        percentiles_ms = {
            key: value / 1_000_000 if value is not None else None
            for key, value in percentiles_ns.items()
        }
        operations[operation] = {
            **row,
            "percentiles_ms": percentiles_ms,
            "p50_ms": percentiles_ms["p50"],
            "p95_ms": percentiles_ms["p95"],
            "p99_ms": percentiles_ms["p99"],
        }
    return {
        **summary,
        "measurement_scope": "single_strategy_n5_group_client",
        "operations": operations,
        "timeout_count_total": sum(
            row["timeout_count"] for row in operations.values()
        ),
        "engineering_targets_ms": _N5_GROUP_RPC_TARGETS_MS,
        "thresholds_are_engineering_targets_not_user_sla": True,
    }


def _load01_n5_group_query_facts(
    query_result: Mapping[str, Any],
    *,
    group_id: str,
    portfolio_ref: str,
    strategy_ref: str,
    account_ref: str,
    metadata: tuple[InstrumentRiskMetadata, ...],
) -> dict[str, Any]:
    """Validate the durable V3 query and project its three child outcomes."""

    if (
        not isinstance(query_result, Mapping)
        or query_result.get("ok") is not True
        or query_result.get("code") != "OK"
    ):
        raise AssertionError("LOAD01 N5 group query was not acknowledged")
    result = query_result.get("result")
    if not isinstance(result, Mapping) or result.get("found") is not True:
        raise AssertionError("LOAD01 N5 group query did not find the submitted group")
    snapshot_payload = result.get("snapshot")
    if not isinstance(snapshot_payload, Mapping):
        raise AssertionError("LOAD01 N5 group query omitted its durable snapshot")
    snapshot = GroupOrderSnapshot.from_payload(_plain_rpc_json(snapshot_payload))
    if (
        snapshot.request.group_id != group_id
        or snapshot.request.version != 3
        or snapshot.request.portfolio_ref != portfolio_ref
        or snapshot.request.strategy_id != strategy_ref
        or snapshot.request.account_refs != (account_ref,)
        or snapshot.request.first_leg_id != N5_LEG_IDS[0]
        or snapshot.state is not GroupOrderState.PAIRED
        or tuple(leg.leg_id for leg in snapshot.legs) != N5_LEG_IDS
        or tuple(plan.leg_id for plan in snapshot.request.legs) != N5_LEG_IDS
        or snapshot.revision <= 0
        or snapshot.created_at_ns > snapshot.updated_at_ns
    ):
        raise AssertionError("LOAD01 N5 durable group snapshot does not match its request")
    facts = []
    for spec, item, plan, leg in zip(
        N5_LEG_SPECS,
        metadata,
        snapshot.request.legs,
        snapshot.legs,
        strict=True,
    ):
        intent = leg.submitted_intent
        child = leg.child_status
        if (
            leg.leg_id != spec.leg_id
            or type(intent) is not OrderIntent
            or intent.scope.account_ref != account_ref
            or intent.scope.strategy_id != strategy_ref
            or intent.intent_id != group_leg_intent_id(group_id, leg.leg_id)
            or intent.instrument != spec.instrument
            or intent.side is not spec.side
            or intent.quantity != spec.quantity
            or intent.price != spec.price
            or intent.time_in_force.value != "IOC"
            or intent.metadata_version != item.metadata_version
            or intent.tags.get(INSTRUMENT_METADATA_DIGEST_TAG) != item.digest
            or plan.maximum_intent.intent_id != intent.intent_id
            or plan.account_ref != account_ref
            or plan.ratio != spec.ratio
            or plan.maximum_intent.instrument != spec.instrument
            or plan.maximum_intent.side is not spec.side
            or plan.maximum_intent.quantity != spec.quantity
            or plan.maximum_intent.price != spec.price
            or plan.quantity_step != item.quantity_step
            or plan.base_units_per_order_unit
            != dict(N5_BASE_UNIT_SCALES)[spec.instrument]
            or plan.metadata_valid_until_ns != item.expires_at_ns
            or leg.phase is not GroupLegPhase.OBSERVED
            or child is None
            or child.state is not ExecutionState.FILLED
            or type(child.intent_id) is not str
            or child.intent_id != intent.intent_id
        ):
            raise AssertionError("LOAD01 N5 group child dispatch is unresolved")
        facts.append(
            {
                "leg_id": leg.leg_id,
                "account_ref": intent.scope.account_ref,
                "strategy_ref": intent.scope.strategy_id,
                "phase": leg.phase.value,
                "distinct_durable_filled_child_intent_count": 1,
                "distinct_durable_filled_child_intent_count_source": (
                    "group_query_snapshot"
                ),
                "provider_write_count": None,
                "provider_write_count_status": "NOT_REGISTERED_FROM_OWNER_LOG",
                "outcome": leg.phase.value,
                "execution_state": child.state.value,
                "intent_id": child.intent_id,
                "order_revision": child.order_revision,
                "trade_count": child.trade_count,
                "dispatch_latency_ns": None,
                "dispatch_latency_status": "NOT_REGISTERED_BY_COORDINATOR_SNAPSHOT",
            }
        )
    return {
        "query_found": True,
        "group_id": snapshot.request.group_id,
        "version": snapshot.request.version,
        "revision": snapshot.revision,
        "state": snapshot.state.value,
        "portfolio_ref": snapshot.request.portfolio_ref,
        "strategy_ref": snapshot.request.strategy_id,
        "created_at_ns": snapshot.created_at_ns,
        "updated_at_ns": snapshot.updated_at_ns,
        "leg_intent_ids": {
            fact["leg_id"]: fact["intent_id"] for fact in facts
        },
        "legs": tuple(facts),
    }


def _load01_cpu_schedule_summary(
    resource_samples: list[dict[str, Any]], *, planned_duration_seconds: int
) -> dict[str, Any]:
    expected_count = planned_duration_seconds // int(_LOAD01_CPU_SAMPLE_INTERVAL_SECONDS)
    expected_ticks = list(range(1, expected_count + 1))
    steady_rows = [
        row
        for row in resource_samples
        if row.get("load01_cpu_steady_load_sample") is True
    ]
    tick_ids = [row.get("load01_cpu_sample_tick") for row in steady_rows]
    values = [row.get("system_cpu_percent") for row in steady_rows]
    try:
        summary = summarize_cpu_samples(values, expected_samples=expected_count)
    except (TypeError, ValueError, OverflowError):
        summary = {
            "status": "INCOMPLETE_INVALID_SAMPLE",
            "acceptance_status": "INCOMPLETE",
            "sample_count": len(values),
            "expected_sample_count": expected_count,
            "coverage_fraction": min(1.0, len(values) / expected_count)
            if expected_count
            else None,
            "mean_percent": None,
            "p95_percent": None,
            "max_percent": None,
            "min_percent": None,
        }
    gaps = [row.get("sample_interval_seconds") for row in steady_rows]
    actual_elapsed = [row.get("elapsed_seconds") for row in steady_rows]
    valid_gaps = all(
        isinstance(gap, (int, float))
        and not isinstance(gap, bool)
        and 0 < gap <= _LOAD01_CPU_SAMPLE_MAX_GAP_SECONDS
        for gap in gaps
    )
    valid_elapsed = all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= 0
        for value in actual_elapsed
    ) and all(
        current > previous
        for previous, current in zip(actual_elapsed, actual_elapsed[1:])
    )
    contiguous = tick_ids == expected_ticks
    complete = summary["status"] == "COMPLETE" and contiguous and valid_gaps and valid_elapsed
    if not complete:
        summary["status"] = "INCOMPLETE_SAMPLE_SCHEDULE"
        summary["acceptance_status"] = "INCOMPLETE"
    summary.update(
        {
            "sample_scope": "steady_load_only; baseline and shutdown excluded",
            "planned_duration_seconds": planned_duration_seconds,
            "sample_interval_seconds": _LOAD01_CPU_SAMPLE_INTERVAL_SECONDS,
            "maximum_sample_gap_seconds": _LOAD01_CPU_SAMPLE_MAX_GAP_SECONDS,
            "observed_tick_ids": tick_ids,
            "expected_tick_ids": expected_ticks,
            "sample_gaps_seconds": gaps,
            "elapsed_seconds_by_tick": actual_elapsed,
            "schedule_complete": complete,
        }
    )
    return summary


def _load01_market_e2e_latency_ms(published_ns: Any, observed_ns: Any) -> float | None:
    """Return a same-clock non-negative latency, or None for unusable timestamps."""

    if (
        type(published_ns) is not int
        or published_ns <= 0
        or type(observed_ns) is not int
        or observed_ns < published_ns
    ):
        return None
    return (observed_ns - published_ns) / 1_000_000


def _load01_market_e2e_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    events = row.get("market_events", 0)
    due = row.get("market_e2e_due_count", 0)
    valid = row.get("market_e2e_valid_count", 0)
    samples = row.get("market_e2e_samples_ms") or ()
    expected_due = events // 128 if type(events) is int and events >= 0 else None
    sample_count = len(samples)
    valid_source = (
        type(due) is int
        and type(valid) is int
        and due == expected_due
        and valid == due
    )
    percentiles = {
        "p50": nearest_rank(samples, 0.50) if samples else None,
        "p95": nearest_rank(samples, 0.95) if samples else None,
        "p99": nearest_rank(samples, 0.99) if samples else None,
    }
    percentiles_complete = all(
        type(value) in {int, float} and isfinite(value)
        for value in percentiles.values()
    )
    return {
        "market_event_count": events,
        "expected_latency_observations": expected_due,
        "scheduled_latency_observations": due,
        "valid_latency_observations": valid,
        "sampled_count": sample_count,
        "sample_capacity": 2048,
        "sample_coverage_fraction": sample_count / valid if valid else None,
        "full_population_retained": sample_count == valid if valid else None,
        "sample_method": "every_128th_event_then_seeded_reservoir",
        "source_measurement_coverage_complete": valid_source,
        "percentiles_ms": percentiles,
        "observational_percentiles": ["p50", "p95", "p99"],
        "status": "OBSERVED" if valid_source and sample_count and percentiles_complete else "INCOMPLETE",
    }


def _load01_read_threshold_registration(root: Path, file_pins: Mapping[str, str]):
    path = root / _LOAD01_THRESHOLD_REGISTRATION_PATH
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != _LOAD01_THRESHOLD_REGISTRATION_SHA256:
        raise AssertionError("LOAD01 threshold registration bytes differ from the pinned contract")
    relative = path.relative_to(root).as_posix()
    if file_pins.get(relative) != digest:
        raise AssertionError("LOAD01 threshold registration is absent from the fixed-source manifest")
    registration = json.loads(raw.decode("utf-8"))
    if (
        type(registration) is not dict
        or type(registration.get("schema_version")) is not int
        or registration.get("schema_version") != 1
        or registration.get("case_id") != _LOAD01_CASE_ID
        or registration.get("contract_id") != _LOAD01_REGISTRATION_CONTRACT_ID
        or registration.get("status") != "ROOT_PROPOSED_ENGINEERING_CONTRACT_NOT_USER_SLA"
        or type(registration.get("workload_contract", {}).get("strategy_processes"))
        is not int
        or registration.get("workload_contract", {}).get("strategy_processes")
        != _STRATEGIES
        or type(registration.get("workload_contract", {}).get("account_owner_processes"))
        is not int
        or registration.get("workload_contract", {}).get("account_owner_processes")
        != _ACCOUNTS
        or registration.get("engineering_targets", {}).get("label")
        != "ROOT_PROPOSED_ENGINEERING_TARGETS_SUBJECT_TO_ROOT_REVIEW_NOT_USER_SLA"
    ):
        raise AssertionError("LOAD01 threshold registration schema or evidence label is invalid")
    return (
        registration,
        {"path": relative, "sha256": digest, "status": registration["status"]},
        raw,
    )


def _load01_json_value_matches_exactly(left: Any, right: Any) -> bool:
    """Compare parsed JSON with exact value types, including bool versus int."""

    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(
            _load01_json_value_matches_exactly(left[key], right[key]) for key in left
        )
    if type(left) is list:
        return len(left) == len(right) and all(
            _load01_json_value_matches_exactly(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def _load01_performance_measurement_report(
    *,
    profile,
    mode: str,
    strategy_results: Mapping[int, Mapping[str, Any]],
    resource_samples: list[dict[str, Any]],
    planned_duration_seconds: int,
    actual_duration_seconds: float,
    topic_stream_ids_by_index: Sequence[str],
    source_sequences_by_stream: Mapping[str, int],
    registration: Mapping[str, Any],
    registration_pin: Mapping[str, str],
    registration_raw: bytes,
) -> dict[str, Any]:
    if type(registration_raw) is not bytes:
        raise AssertionError("LOAD01 threshold registration snapshot must be bytes")
    registration_digest = hashlib.sha256(registration_raw).hexdigest()
    if registration_pin.get("sha256") != registration_digest:
        raise AssertionError("LOAD01 threshold registration snapshot differs from its trusted pin")
    try:
        registration_projection = json.loads(registration_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AssertionError("LOAD01 threshold registration snapshot is not valid UTF-8 JSON") from None
    if not _load01_json_value_matches_exactly(registration, registration_projection):
        raise AssertionError("LOAD01 threshold mapping differs from its pinned byte snapshot")
    eligible = (
        profile.formal_acceptance_eligible(mode)
        and profile.profile_id in registration["workload_contract"]["p0_full_evaluation_eligible_profiles"]
        and profile.profile_id == "U0"
    )
    targets = registration["engineering_targets"]
    tolerance = targets["goodput"]["tolerance_fraction"]
    order_thresholds = targets["latency"]["order_rpc_ms_each_operation"]
    market_thresholds = targets["latency"]["market_end_to_end_ms"]
    expected_by_strategy = {}
    order_by_strategy = {}
    market_by_strategy = {}
    violations: list[str] = []
    for strategy_index in range(_STRATEGIES):
        row = strategy_results.get(strategy_index, {})
        expected = _load01_expected_order_operation_counts(
            profile, strategy_index, planned_duration_seconds
        )
        expected_by_strategy[strategy_index] = expected
        metrics = row.get("order_rpc_metrics")
        operations = metrics.get("operations", {}) if type(metrics) is dict else {}
        order_rows = {}
        for operation, scheduled_count in expected.items():
            operation_row = operations.get(operation, {}) if type(operations) is dict else {}
            observed_count = operation_row.get("observed_count", 0)
            error_count = operation_row.get("error_count", 0)
            timeout_count = operation_row.get("timeout_count", 0)
            sampled_count = operation_row.get("sampled_count", 0)
            percentiles_ns = operation_row.get("percentiles_ns", {})
            percentiles_ms = {}
            for name in ("p50", "p95", "p99"):
                value = percentiles_ns.get(name) if type(percentiles_ns) is dict else None
                if type(value) not in {int, float}:
                    percentiles_ms[name] = None
                    continue
                try:
                    value_ms = value / 1_000_000
                except OverflowError:
                    value_ms = None
                percentiles_ms[name] = (
                    value_ms
                    if value_ms is not None and isfinite(value_ms) and value_ms >= 0
                    else None
                )
            count_complete = (
                type(observed_count) is int
                and abs(observed_count - scheduled_count) <= scheduled_count * tolerance
            )
            percentile_complete = type(sampled_count) is int and sampled_count > 0 and all(
                percentiles_ms.get(name) is not None
                for name in ("p50", "p95", "p99")
            )
            within_limits = (
                percentile_complete
                and percentiles_ms["p99"] <= order_thresholds["p99_max"]
            )
            unexpected_observation = scheduled_count == 0 and observed_count != 0
            order_rows[operation] = {
                "scheduled_count": scheduled_count,
                "observed_count": observed_count,
                "volume_within_tolerance": count_complete,
                "error_count": error_count,
                "timeout_count": timeout_count,
                "sampled_count": sampled_count,
                "sample_capacity": operation_row.get("sample_capacity"),
                "sample_coverage_fraction": operation_row.get("sample_coverage_fraction"),
                "full_population_retained": operation_row.get("full_population_retained"),
                "percentiles_ms_nearest_rank": percentiles_ms,
                "observational_percentiles": list(order_thresholds["observational_percentiles"]),
                "percentile_targets_ms": {"p99": order_thresholds["p99_max"]},
                "status": (
                    "NOT_REQUIRED" if scheduled_count == 0 and not unexpected_observation else
                    "UNEXPECTED_OBSERVATION" if unexpected_observation else
                    "TARGETS_MET" if count_complete and error_count == 0 and timeout_count == 0 and within_limits else
                    "INCOMPLETE" if not count_complete or not percentile_complete else
                    "TARGETS_FAILED"
                ),
            }
            if eligible and unexpected_observation:
                violations.append(f"strategy-{strategy_index:03d}:{operation}:unplanned_operation")
            if eligible and scheduled_count:
                if not count_complete:
                    violations.append(f"strategy-{strategy_index:03d}:{operation}:volume")
                if error_count != 0:
                    violations.append(f"strategy-{strategy_index:03d}:{operation}:errors")
                if timeout_count > targets["latency"]["ordinary_order_rpc_timeout_count_max"]:
                    violations.append(f"strategy-{strategy_index:03d}:{operation}:timeouts")
                if not percentile_complete:
                    violations.append(f"strategy-{strategy_index:03d}:{operation}:missing_latency")
                elif not within_limits:
                    violations.append(f"strategy-{strategy_index:03d}:{operation}:latency")
        order_by_strategy[strategy_index] = order_rows
        market_summary = _load01_market_e2e_summary(row)
        market_summary["acceptance_scope"] = market_thresholds["acceptance_scope"]
        market_by_strategy[strategy_index] = market_summary
        if eligible:
            if market_summary["status"] != "OBSERVED":
                violations.append(f"strategy-{strategy_index:03d}:market:missing_latency")

    input_by_topic = {}
    for topic_index, stream_id in enumerate(topic_stream_ids_by_index):
        expected_rate = profile.topic_messages_per_second[topic_index]
        expected_count = expected_rate * actual_duration_seconds
        observed_count = source_sequences_by_stream.get(stream_id)
        within_tolerance = (
            type(observed_count) is int
            and abs(observed_count - expected_count) <= expected_count * tolerance
        )
        input_by_topic[f"K{topic_index:02d}"] = {
            "stream_id": stream_id,
            "target_per_second": expected_rate,
            "expected_count_for_actual_duration": expected_count,
            "observed_source_sequence": observed_count,
            "volume_within_tolerance": within_tolerance,
            "status": "TARGETS_MET" if within_tolerance else "INCOMPLETE_OR_OUTSIDE_TARGET",
        }
        if eligible and not within_tolerance:
            violations.append(f"topic-K{topic_index:02d}:input_volume")

    delivery_by_strategy_topic = {}
    for strategy_index in range(_STRATEGIES):
        row = strategy_results.get(strategy_index, {})
        observed_by_stream = row.get("market_by_stream")
        observed_by_stream = observed_by_stream if type(observed_by_stream) is dict else {}
        topic_rows = {}
        for topic_index in profile.subscribed_topic_indices(strategy_index):
            stream_id = topic_stream_ids_by_index[topic_index]
            target_rate = profile.topic_messages_per_second[topic_index]
            expected_count = target_rate * actual_duration_seconds
            observed_count = observed_by_stream.get(stream_id)
            within_tolerance = (
                type(observed_count) is int
                and abs(observed_count - expected_count) <= expected_count * tolerance
            )
            topic_rows[f"K{topic_index:02d}"] = {
                "stream_id": stream_id,
                "target_per_second": target_rate,
                "expected_count_for_actual_duration": expected_count,
                "observed_delivery_count": observed_count,
                "volume_within_tolerance": within_tolerance,
                "status": "TARGETS_MET" if within_tolerance else "INCOMPLETE_OR_OUTSIDE_TARGET",
            }
            if eligible and not within_tolerance:
                violations.append(
                    f"strategy-{strategy_index:03d}:topic-K{topic_index:02d}:delivery_volume"
                )
        delivery_by_strategy_topic[strategy_index] = topic_rows

    order_cycles_by_account = {}
    for account_index, strategy_indices in enumerate(profile.strategy_indices_by_account):
        account_ref = _ACCOUNT_REFS[account_index]
        expected_rate = profile.order_targets_per_account_per_second[account_index]
        expected_count = expected_rate * actual_duration_seconds
        observed_count = 0
        observed_complete = True
        for strategy_index in strategy_indices:
            row = strategy_results.get(strategy_index, {})
            by_account = row.get("order_cycles_by_account")
            value = by_account.get(account_ref) if type(by_account) is dict else None
            if type(value) is not int or value < 0:
                observed_complete = False
            else:
                observed_count += value
        within_tolerance = (
            observed_complete
            and abs(observed_count - expected_count) <= expected_count * tolerance
        )
        order_cycles_by_account[account_ref] = {
            "target_cycles_per_second": expected_rate,
            "expected_count_for_actual_duration": expected_count,
            "observed_order_cycles": observed_count if observed_complete else None,
            "volume_within_tolerance": within_tolerance,
            "status": "TARGETS_MET" if within_tolerance else "INCOMPLETE_OR_OUTSIDE_TARGET",
        }
        if eligible and not within_tolerance:
            violations.append(f"account-{account_index:02d}:order_cycle_volume")

    aggregate_operations: dict[str, dict[str, int]] = {
        operation: {
            "scheduled_count": 0,
            "observed_count": 0,
            "success_count": 0,
            "error_count": 0,
            "timeout_count": 0,
            "sampled_count": 0,
        }
        for operation in ("orders.submit", "orders.query", "orders.cancel")
    }
    for strategy_index in range(_STRATEGIES):
        for operation, scheduled_count in expected_by_strategy[strategy_index].items():
            aggregate = aggregate_operations[operation]
            aggregate["scheduled_count"] += scheduled_count
            operation_row = order_by_strategy[strategy_index][operation]
            for field in ("observed_count", "error_count", "timeout_count", "sampled_count"):
                value = operation_row.get(field)
                if type(value) is int and value >= 0:
                    aggregate[field] += value
            metrics = strategy_results.get(strategy_index, {}).get("order_rpc_metrics")
            metric_ops = metrics.get("operations", {}) if type(metrics) is dict else {}
            observed = metric_ops.get(operation, {}) if type(metric_ops) is dict else {}
            success_count = observed.get("success_count", 0) if type(observed) is dict else 0
            if type(success_count) is int and success_count >= 0:
                aggregate["success_count"] += success_count

    cpu = _load01_cpu_schedule_summary(
        resource_samples, planned_duration_seconds=planned_duration_seconds
    )
    cpu_targets = targets["cpu"]
    cpu_complete = cpu.get("status") == "COMPLETE" and cpu.get("schedule_complete") is True
    cpu_within_limits = (
        cpu_complete
        and cpu.get("mean_percent") is not None
        and cpu.get("p95_percent") is not None
        and cpu["mean_percent"] <= cpu_targets["system_mean_percent_max"]
        and cpu["p95_percent"] <= cpu_targets["system_p95_percent_max"]
    )
    if eligible:
        if not cpu_complete:
            violations.append("system_cpu:incomplete_schedule")
        elif not cpu_within_limits:
            violations.append("system_cpu:targets")
    if eligible:
        evaluation_status = "TARGETS_MET" if not violations else "TARGETS_FAILED_OR_INCOMPLETE"
    else:
        evaluation_status = "NOT_EVALUATED_PROFILE_NOT_REGISTERED"
    return {
        "schema_version": 1,
        "evidence_level": "LOCAL_SYNTHETIC_LOAD_MEASUREMENT_NOT_FORMAL_CASE_ACCEPTANCE",
        "registration": dict(registration_pin),
        "registration_label": targets["label"],
        "profile_id": profile.profile_id,
        "mode": mode,
        "p0_evaluation_eligible": eligible,
        "evaluation_status": evaluation_status,
        "goodput_tolerance_fraction": tolerance,
        "expected_order_rpc_counts_by_strategy": expected_by_strategy,
        "input_messages_by_topic": input_by_topic,
        "delivered_messages_by_strategy_and_topic": delivery_by_strategy_topic,
        "order_cycles_by_account": order_cycles_by_account,
        "order_rpc_by_strategy": order_by_strategy,
        "order_rpc_aggregate_counts": aggregate_operations,
        "market_end_to_end_by_strategy": market_by_strategy,
        "system_cpu": cpu,
        "system_cpu_targets_percent": {
            "mean_max": cpu_targets["system_mean_percent_max"],
            "p95_max": cpu_targets["system_p95_percent_max"],
        },
        "violations": violations,
        "formal_acceptance_status": "NOT_CLAIMED_BY_METRICS_WIRING",
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


def _retain_owner_close_progress(record: dict[str, Any], message: Any) -> bool:
    if not isinstance(message, tuple) or not message or message[0] != "owner-close-progress":
        return False
    if (
        record.get("kind") != "owner"
        or len(message) != 6
        or type(message[1]) is not int
        or message[1] != record.get("pid")
        or type(message[2]) is not int
        or type(message[3]) is not str
        or message[3]
        not in {
            "relay-drain",
            "execution-host-close",
            "account-host-close",
            "n5-group-runtime-close",
            "store-close",
            "closed-ack-send",
        }
        or type(message[4]) is not int
        or type(message[5]) is not int
        or not 0 <= message[2] < _ACCOUNTS
        or not 0 <= message[4] <= message[5] <= _STRATEGIES
    ):
        return False
    progress = record.setdefault("owner_close_progress", [])
    if len(progress) < (2 * _PER_ACCOUNT + 8):
        progress.append(
            {
                "account_index": message[2],
                "phase": message[3],
                "relay_workers_completed": message[4],
                "relay_workers_total": message[5],
            }
        )
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
                if _retain_owner_close_progress(record, message):
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
                if _retain_owner_close_progress(record, message):
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
                    if _retain_owner_close_progress(record, message):
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
            elif _retain_owner_close_progress(record, message):
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
    profile_id: str,
) -> dict[str, Any]:
    expected_provider = "fixture" if profile_id == "N5" else "synthetic"
    expected_environment = "simulation" if profile_id == "N5" else "paper"
    expected_by_scope = {record["outbox_scope"]: record for record in source_records}
    if len(expected_by_scope) != len(source_records):
        raise AssertionError("LOAD01 owner source map contained duplicate scopes")
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
            or source_scope.get("provider") != expected_provider
            or source_scope.get("environment") != expected_environment
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
    total = 0
    for path in candidates:
        try:
            total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


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
    progress: dict[str, Any], *, expected_clients_by_strategy: Mapping[int, int]
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
            expected_clients_by_strategy=expected_clients_by_strategy,
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
        elif tag == "owner-close-progress":
            _retain_owner_close_progress(record, message)
        elif tag == "error" and len(message) >= 5 and type(message[4]) is dict:
            retain_snapshot(record, "at_failure", message[4].get("rpc_timing_at_failure"))
        elif tag == "closed":
            trace_index = 8 if record["kind"] == "strategy" else 16
            if len(message) > trace_index:
                retain_snapshot(record, "emergency_close", message[trace_index])
            if record["kind"] == "owner" and len(message) > 17:
                close_facts = message[17]
                # Emergency cleanup evidence only; the run remains failed even
                # when the owner acknowledges its close normally.
                record["owner_emergency_close_ack_observed"] = bool(
                    type(message[1]) is int
                    and 0 <= message[1] < _ACCOUNTS
                    and type(message[2]) is int
                    and message[2] == record.get("pid")
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
                    and type(close_facts) is dict
                    and close_facts.get("normal_drain_completed") is True
                )

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

    monitor_records = {
        name: record for name, record in records.items() if record["kind"] == "monitor"
    }
    strategy_records = {
        name: record for name, record in records.items() if record["kind"] == "strategy"
    }
    market_records = {
        name: record for name, record in records.items() if record["kind"] == "market"
    }
    other_dependent_records = {
        name: record
        for name, record in records.items()
        if record["kind"] not in {"monitor", "market", "strategy", "owner"}
    }
    owner_records = {
        name: record for name, record in records.items() if record["kind"] == "owner"
    }

    def send_close_commands(selected: dict[str, dict[str, Any]]) -> None:
        for record in selected.values():
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

    def join_until(
        timeout: float, selected: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        alive = []
        while time.monotonic() < deadline:
            alive = []
            for item in selected.values():
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
                    # A failed state query is not proof that the child stopped.
                    alive.append(item)
            if not alive:
                return []
            time.sleep(0.05)
        return alive

    def reap_group(
        selected: dict[str, dict[str, Any]],
        *,
        grace_timeout: float = _EMERGENCY_CLOSE_GRACE_SECONDS,
    ) -> list[dict[str, Any]]:
        if not selected:
            return []
        survivors = join_until(grace_timeout, selected)
        if survivors:
            # A bounded final nonblocking drain closes the race where the child
            # publishes its snapshot just after the grace loop's last poll.
            for item in selected.values():
                if not item["closed"]:
                    drain_diagnostics(item, 64)
            still_alive = []
            for item in survivors:
                try:
                    if item["process"].is_alive():
                        still_alive.append(item)
                except (AssertionError, OSError, ValueError) as exc:
                    cleanup_errors.append(f"{item['name']}:state:{type(exc).__name__}")
                    still_alive.append(item)
            survivors = still_alive
            for item in survivors:
                item["rpc_snapshot_received_before_terminate"] = bool(
                    item.get("rpc_timing_snapshot_stages")
                )
                try:
                    item["process"].terminate()
                    terminated.append(item["name"])
                except (OSError, ValueError) as exc:
                    cleanup_errors.append(f"{item['name']}:terminate:{type(exc).__name__}")
            survivors = join_until(_EMERGENCY_TERMINATE_GRACE_SECONDS, selected)
        if survivors:
            for item in survivors:
                try:
                    item["process"].kill()
                except (OSError, ValueError) as exc:
                    cleanup_errors.append(f"{item['name']}:kill:{type(exc).__name__}")
            survivors = join_until(_EMERGENCY_KILL_GRACE_SECONDS, selected)

        unresolved = []
        for record in selected.values():
            if record["closed"]:
                continue
            process = record["process"]
            drain_diagnostics(record, 64)
            try:
                alive = process.is_alive()
            except (AssertionError, OSError, ValueError) as exc:
                cleanup_errors.append(f"{record['name']}:state:{type(exc).__name__}")
                record["normal_exit"] = False
                unresolved.append(record)
                continue
            if alive:
                cleanup_errors.append(f"{record['name']}:still-alive")
                record["normal_exit"] = False
                unresolved.append(record)
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
                unresolved.append(record)
        return unresolved

    # Strategies own market clients whose close path sends market.unsubscribe.
    # Keep every service host alive while those clients close; the market host
    # is shut down only after each strategy process is confirmed stopped.
    send_close_commands(strategy_records)
    reap_group(strategy_records)
    strategy_stop_uncertain = False
    for strategy in strategy_records.values():
        if strategy["closed"]:
            continue
        process = strategy["process"]
        try:
            process.join(0)
            if process.is_alive() or process.exitcode is None:
                strategy_stop_uncertain = True
        except (AssertionError, OSError, ValueError) as exc:
            state_error = f"{strategy['name']}:state:{type(exc).__name__}"
            if state_error not in cleanup_errors:
                cleanup_errors.append(state_error)
            strategy_stop_uncertain = True

    if strategy_stop_uncertain:
        # A strategy can still be using any of its service clients. Preserve
        # the complete service side until every strategy stop is confirmed.
        for service_group in (
            market_records,
            owner_records,
            other_dependent_records,
            monitor_records,
        ):
            for service in service_group.values():
                service["normal_exit"] = False
                cleanup_errors.append(
                    f"{service['name']}:close-withheld-strategy-not-stopped"
                )
        return

    send_close_commands(market_records)
    reap_group(market_records)

    def stop_is_uncertain(selected: dict[str, dict[str, Any]]) -> bool:
        uncertain = False
        for item in selected.values():
            if item["closed"]:
                continue
            try:
                process = item["process"]
                process.join(0)
                if process.is_alive() or process.exitcode is None:
                    uncertain = True
            except (AssertionError, OSError, ValueError) as exc:
                state_error = f"{item['name']}:state:{type(exc).__name__}"
                if state_error not in cleanup_errors:
                    cleanup_errors.append(state_error)
                uncertain = True
        return uncertain

    # Owners can spend their full relay drain deadline before closing the two
    # service hosts. Give only owners that longer grace; other dependents keep
    # the ordinary close budget, starting after their own close command.
    send_close_commands(owner_records)
    reap_group(owner_records, grace_timeout=_OWNER_CLOSE_GRACE_SECONDS)
    if stop_is_uncertain(owner_records):
        for service in other_dependent_records.values():
            service["normal_exit"] = False
            cleanup_errors.append(
                f"{service['name']}:close-withheld-owner-not-stopped"
            )
        if monitor_records:
            cleanup_errors.append("monitor:close-withheld-owner-not-stopped")
            for monitor in monitor_records.values():
                monitor["normal_exit"] = False
        return

    send_close_commands(other_dependent_records)
    reap_group(other_dependent_records)
    if stop_is_uncertain(other_dependent_records):
        if monitor_records:
            cleanup_errors.append("monitor:close-withheld-dependent-not-stopped")
            for monitor in monitor_records.values():
                monitor["normal_exit"] = False
        return

    send_close_commands(monitor_records)
    reap_group(monitor_records)


def test_load01_ten_strategy_sustained(tmp_path: Path) -> None:
    """Run ten real strategy child processes against synthetic local services."""
    if os.name != "nt":
        pytest.skip("LOAD01 acceptance runner uses the Windows spawn baseline")

    mode = os.environ.get("BT_API_LOAD01_MODE", "full")
    if mode not in {"pilot", "full"}:
        raise ValueError("BT_API_LOAD01_MODE must be pilot or full")
    profile_id = os.environ.get("BT_API_LOAD01_PROFILE", "U0")
    profile = load01_profile(profile_id)
    n5_enabled = profile_id == "N5"
    expected_child_count = _STRATEGIES + _ACCOUNTS + 2 + int(n5_enabled)
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
    expected_clients_by_strategy = {
        index: len(topic_indices)
        for index, topic_indices in enumerate(profile.topic_indices_by_strategy)
    }
    duration_seconds = 120 if mode == "pilot" else _FULL_LOAD_SECONDS
    source_root = _fixed_source_root()
    export_pin_path = Path(os.environ["BT_API_LOAD01_EXPORT_PINS"]).resolve()
    runner_path = Path(__file__).resolve()
    runner_relative = runner_path.relative_to(source_root).as_posix()
    runner_overlay_sha256 = os.environ["BT_API_LOAD01_RUNNER_OVERLAY_SHA256"]
    if _sha256(runner_path) != runner_overlay_sha256:
        raise AssertionError("LOAD01 runner overlay differs from the external spec pin")
    profile_relative = Path("tests/unit/_load01_profiles.py").as_posix()
    profile_path = source_root / profile_relative
    if _sha256(profile_path) != _N6_PROFILE_OVERLAY_SHA256:
        raise AssertionError("LOAD01 profile overlay differs from its fixed source pin")
    profile_test_relative = Path("tests/unit/test_load01_profiles.py").as_posix()
    profile_test_path = source_root / profile_test_relative
    if _sha256(profile_test_path) != _N6_PROFILE_TEST_OVERLAY_SHA256:
        raise AssertionError("LOAD01 profile contract test differs from its fixed source pin")
    overlay_specs = {
        profile_relative: _N6_PROFILE_OVERLAY_SHA256,
        profile_test_relative: _N6_PROFILE_TEST_OVERLAY_SHA256,
        "tests/unit/_load01_resource_diagnostics.py": _LOAD01_RESOURCE_DIAGNOSTICS_OVERLAY_SHA256,
        "tests/unit/test_load01_resource_diagnostics.py": _LOAD01_RESOURCE_DIAGNOSTICS_TEST_OVERLAY_SHA256,
        "tests/unit/_load01_group_topology.py": _LOAD01_GROUP_TOPOLOGY_OVERLAY_SHA256,
        "tests/unit/test_load01_runner_metrics.py": _LOAD01_RUNNER_METRICS_TEST_OVERLAY_SHA256,
    }
    if profile_id == "N6":
        overlay_specs[
            "tests/unit/test_load01_quota_wave_integration.py"
        ] = _N6_QUOTA_WAVE_INTEGRATION_TEST_OVERLAY_SHA256
    allowed_overlays = {runner_relative: runner_overlay_sha256}
    with zipfile.ZipFile(export_pin_path.parent / "parent.zip") as parent_archive:
        for relative, digest in overlay_specs.items():
            path = source_root / relative
            if not path.is_file() or _sha256(path) != digest:
                raise AssertionError(
                    f"LOAD01 fixed-source overlay differs from its source pin: {relative}"
                )
            try:
                archived_bytes = parent_archive.read(relative)
            except KeyError:
                raise AssertionError(
                    f"LOAD01 source overlay path is absent from parent archive: {relative}"
                ) from None
            if path.read_bytes() != archived_bytes:
                allowed_overlays[relative] = digest
    archive_validation = _verify_fixed_archives(
        source_root,
        export_pin_path,
        allowed_overlays=allowed_overlays,
    )
    source_manifest = _write_source_run_manifest(
        source_root,
        tmp_path,
        profile_id=profile_id,
        allowed_overlays=allowed_overlays,
    )
    os.environ["BT_API_LOAD01_SOURCE_ROOT"] = str(source_root)
    os.environ["BT_API_LOAD01_SOURCE_MANIFEST"] = str(source_manifest)
    source_bindings = _check_fixed_source_manifest(
        source_manifest, profile_id=profile_id
    )
    (
        threshold_registration,
        threshold_registration_pin,
        threshold_registration_raw,
    ) = _load01_read_threshold_registration(
        source_root, json.loads(source_manifest.read_text(encoding="utf-8"))["files"]
    )
    quota_wave_module = (
        _load_n6_quota_wave_module() if profile_id == "N6" else None
    )
    output_path = Path(
        os.environ.get(
            "BT_API_LOAD01_OUTPUT",
            str(Path("D:/temp") / f"load01-{mode}-result.json"),
        )
    ).resolve()
    projected_process_rss = expected_child_count * 72 * 1024**2
    preflight_required = projected_process_rss + _MIN_FREE_BYTES
    memory = psutil.virtual_memory()
    logical_cpu_count = psutil.cpu_count(logical=True) or 1
    report: dict[str, Any] = {
        "schema_version": 1,
        "case": _LOAD01_CASE_ID,
        "case_id": _LOAD01_CASE_ID,
        "registration_contract_id": _LOAD01_REGISTRATION_CONTRACT_ID,
        "mode": mode,
        "load_profile": profile_id,
        "profile_registration_status": profile.registration_status,
        "profile_registration_status_source": (
            "docs/迭代计划/迭代10-可选组件与可配置通信/正式迭代计划.md:807-813 (U0 initial LOAD01 baseline)"
            if profile_id == "U0"
            else (
                "docs/evidence/iteration10-load01-n1n2-source-20261018/candidate-registration.md:24 (N4 candidate only)"
                if profile_id == "N4"
                else (
                    "docs/evidence/iteration10-load01-n6-quota-source-20261018/round-record.json (N6 candidate only)"
                    if profile_id == "N6"
                    else "docs/evidence/iteration10-load01-n1n2-source-20261018/candidate-registration.md:17-26 (candidate only)"
                )
            )
        ),
        "state": "PREFLIGHT",
        "formal_acceptance_eligible": profile.formal_acceptance_eligible(mode),
        "started_at_utc": _utc_now(),
        "fixed_parent_commit": _FIXED_PARENT,
        "python_executable": sys.executable,
        "source_archive_pins": json.loads(export_pin_path.read_text(encoding="utf-8")),
        "source_bindings": source_bindings,
        "performance_threshold_registration": threshold_registration_pin,
        "topology": {
            "strategy_child_processes": _STRATEGIES,
            "account_execution_owner_child_processes": _ACCOUNTS,
            "account_owner_relay_worker_threads": profile.strategy_account_binding_count,
            "account_owner_relay_runtime_clients": profile.strategy_account_binding_count,
            "relay_bindings_per_runtime_client": 1,
            "relay_runtime_bindings_target": profile.strategy_account_binding_count,
            "shared_market_child_processes": 1,
            "independent_monitor_child_processes": 1,
            "shared_group_coordinator_processes": int(n5_enabled),
            "total_child_processes": expected_child_count,
            "pytest_controller_processes": 1,
            "total_load_processes_including_controller": expected_child_count + 1,
            "n6_quota_admission_threads": (
                quota_wave_module.STRATEGY_COUNT
                * quota_wave_module.REQUESTS_PER_STRATEGY
                if quota_wave_module is not None
                else 0
            ),
            "strategies_per_account": (
                next(iter(account_strategy_targets.values()))
                if len(set(account_strategy_targets.values())) == 1
                else None
            ),
            "strategies_per_account_by_account": account_strategy_targets,
            "public_market_topics": _TOPICS,
            "market_topics_per_strategy": (
                len(profile.topic_indices_by_strategy[0])
                if len({len(topics) for topics in profile.topic_indices_by_strategy}) == 1
                else None
            ),
            "market_topics_per_strategy_by_strategy": {
                str(index): len(topics)
                for index, topics in enumerate(profile.topic_indices_by_strategy)
            },
            "market_registry_sessions_target": _STRATEGIES,
            "strategy_count": _STRATEGIES,
            "account_count": _ACCOUNTS,
            "market_subscription_client_count_target": sum(
                len(topics) for topics in profile.topic_indices_by_strategy
            ),
            "strategy_topic_binding_count": sum(
                len(topics) for topics in profile.topic_indices_by_strategy
            ),
            "strategy_account_bindings_target": profile.strategy_account_binding_count,
            "strategy_runtime_bindings_target": sum(
                profile.expected_bindings_per_strategy(index)
                for index in range(_STRATEGIES)
            ),
            "topic_input_target_per_second": topic_rate_targets,
            "topic_subscriber_count": topic_subscriber_targets,
            "upstream_input_target_per_second": profile.total_input_target_per_second,
            "strategy_market_deliveries_target_per_second": profile.total_delivery_target_per_second,
            "order_lifecycles_target_per_account_per_second": account_order_rate_targets,
            "order_lifecycles_target_total_per_second": sum(
                profile.order_targets_per_account_per_second
            ),
            "startup_wave_count": len(startup_waves),
            "topic_payload_target_bytes": 256 if profile_id in {"U0", "N3", "N5", "N6"} else None,
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
            f"LOAD01 resource preflight did not meet the measured {expected_child_count}-child projection: "
            f"available={memory.available}, required={preflight_required}"
        )

    progress: dict[str, Any] = {"strategy": {}, "owner": {}}
    child_records: dict[str, dict[str, Any]] = {}
    strategy_records: dict[int, dict[str, Any]] = {}
    owner_records: dict[int, dict[str, Any]] = {}
    market_record: dict[str, Any] | None = None
    monitor_record: dict[str, Any] | None = None
    coordinator_record: dict[str, Any] | None = None
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
    quota_wave_database_path: Path | None = None
    server_time_query_quota_database_path: Path | None = None
    query_startup_measurement: dict[str, int] | None = None
    quota_wave_admission = None
    quota_wave_event = None
    quota_wave_fire_record: dict[str, Any] | None = None

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
        # Owners do not bind their legacy relay endpoint. N5 uses one reserved slot.
        n5_coordinator_endpoint = owner_unused_endpoints[0] if n5_enabled else None

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
        n5_coordinator_keys = zmq.curve_keypair() if n5_enabled else None
        n5_coordinator = (
            n5_coordinator_service_info(n5_coordinator_endpoint, n5_coordinator_keys[0])
            if n5_enabled else None
        )
        n5_metadata = build_n5_group_metadata() if n5_enabled else None

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
                _ACCOUNT_REFS[account_index],
                _strategy_telemetry_scope_ref(profile_id, index, account_index),
            )
            for index in range(_STRATEGIES)
            for account_index in profile.accounts_for_strategy(index)
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
            index: _execution_service_config(execution_services[index], group_owner=n5_enabled)
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
        if profile_id == "N6":
            quota_wave_database_path = work_root / "dim07-quota-wave.sqlite3"
            quota_wave_admission = quota_wave_module.create_quota_wave_admission(
                quota_wave_database_path
            )
            server_time_query_quota_database_path = (
                work_root / "n6-server-time-query-quota.sqlite3"
            )
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
        n5_coordinator_registry = registry_directory / "group-coordinator.sqlite3"
        n5_group_store = journal_directory / "n5-groups.sqlite3"
        n5_origin_runs = journal_directory / "n5-origin-runs.sqlite3"
        registries = {
            "market": market_registry,
            "monitor-durable": durable_registry,
            "monitor-telemetry": telemetry_registry,
        }
        for index in range(_ACCOUNTS):
            registries[f"account-{index:02d}"] = account_registry_paths[index]
            registries[f"execution-{index:02d}"] = execution_registry_paths[index]
        if n5_enabled:
            registries["group-coordinator"] = n5_coordinator_registry
        database_paths = (
            outbox_path,
            *((n5_group_store, n5_origin_runs) if n5_enabled else ()),
            *(
                execution_store_paths[index].with_name(
                    f"{execution_store_paths[index].stem}.n5-{kind}-a{index:02d}.sqlite3"
                )
                for index in range(_ACCOUNTS) if n5_enabled
                for kind in ("provider", "risk-order", "proof", "risk-gate", "accepted-group", "group-authority")
            ),
            *((quota_wave_database_path,) if quota_wave_database_path is not None else ()),
            *(
                (server_time_query_quota_database_path,)
                if server_time_query_quota_database_path is not None
                else ()
            ),
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
        quota_wave_event = context.Event() if profile_id == "N6" else None
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
            progress, expected_clients_by_strategy=expected_clients_by_strategy
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
                    n5_metadata,
                    f"load01-n5-reservation-scope-a{account_index:02d}-v1" if n5_enabled else None,
                    (_N5_COORDINATOR_PRINCIPAL, _N5_COORDINATOR_SERVICE_ID) if n5_enabled else None,
                    f"load01-n5-owner-run-a{account_index:02d}-v1" if n5_enabled else None,
                    n5_coordinator_keys[0] if n5_enabled else None,
                    (
                        str(server_time_query_quota_database_path)
                        if account_index == 0
                        and server_time_query_quota_database_path is not None
                        else None
                    ),
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
            if profile_id == "N6" and account_index == 0:
                quota_facts = account_status.get("server_time_quota_facts")
                if type(quota_facts) is not dict:
                    raise AssertionError("LOAD01 N6 account owner omitted query quota facts")
                startup_connects = quota_facts.get("provider_connect_dispatches")
                startup_account_reads = quota_facts.get("provider_account_read_dispatches")
                if type(startup_connects) is not int or type(startup_account_reads) is not int:
                    raise AssertionError("LOAD01 N6 owner startup dispatch counts are invalid")
                consumed_startup_slots = startup_connects + startup_account_reads
                expected_startup_facts = {
                    "quota_domain_ref": _N6_QUERY_QUOTA_DOMAIN_REF,
                    "subject_scope": "account_only",
                    "strategy_ref": None,
                    "capacity_per_window": _N6_QUERY_QUOTA_CAPACITY,
                    "reserved_capacity": 0,
                    "window_seconds": _N6_QUERY_QUOTA_WINDOW_SECONDS,
                    "max_queue_wait_seconds": _N6_QUERY_QUOTA_MAX_WAIT_SECONDS,
                    "runtime_rpc_timeout_ms": _N6_QUERY_RPC_TIMEOUT_MS,
                    "server_time_attempts": 0,
                    "server_time_admitted_returns": 0,
                    "server_time_typed_denials": {},
                    "provider_connect_dispatches": 1,
                    "provider_account_read_dispatches": 1,
                    "provider_server_time_dispatches": 0,
                    "provider_close_calls": 0,
                    "grants_in_window": consumed_startup_slots,
                    "pending_waiters": 0,
                }
                if (
                    set(quota_facts) != set(expected_startup_facts)
                    or any(
                        quota_facts.get(key) != value
                        for key, value in expected_startup_facts.items()
                    )
                    or consumed_startup_slots != 2
                ):
                    raise AssertionError(
                        "LOAD01 N6 query quota did not record exactly the startup login and account read"
                    )
                query_startup_measurement = {
                    "provider_connect_dispatches": startup_connects,
                    "initial_account_read_dispatches": startup_account_reads,
                    "consumed_startup_slots": consumed_startup_slots,
                    "grants_at_owner_ready": quota_facts["grants_in_window"],
                    "reserved_capacity": quota_facts["reserved_capacity"],
                }
            record["ready"] = True
            record["account_service_endpoint"] = message[4]
            record["execution_service_endpoint"] = message[5]
            scope_records_by_account[account_index] = message[7]

        if n5_enabled:
            phase = "start-group-coordinator"
            n5_routes = {}
            n5_owner_services = {}
            for account_index in range(_ACCOUNTS):
                message = owner_ready[account_index]
                route = message[8] if len(message) > 8 else None
                if (
                    type(route) is not dict
                    or route.get("account_ref") != _ACCOUNT_REFS[account_index]
                    or route.get("strategy_index") != account_index * _PER_ACCOUNT
                    or route.get("owner_endpoint") != message[5]
                ):
                    raise AssertionError("LOAD01 N5 owner did not publish its exact group route")
                n5_routes[account_index * _PER_ACCOUNT] = route
                n5_owner_services[account_index] = {
                    **execution_services[account_index],
                    "service_config": execution_configs[account_index],
                    "endpoint": message[5],
                    "server_public": execution_server_keys[account_index][0],
                    "n5_group_route": route,
                }
            n5_config = build_n5_coordinator_service_config(
                endpoint=n5_coordinator_endpoint,
                server_security_ref=n5_coordinator["security_ref"],
                owner_services=n5_owner_services,
            )
            coordinator_record = launch_child(
                "group-coordinator", "coordinator", n5_coordinator_host_process,
                (
                    n5_config, n5_owner_services, n5_routes, n5_metadata,
                    str(n5_coordinator_registry), str(n5_group_store), str(n5_origin_runs),
                    n5_coordinator_keys,
                    {index: strategy_keys[index][0] for index in profile.expected_group_capable_strategies},
                ),
            )
            n5_ready = _collect_expected(
                {0: coordinator_record}, "ready", 90.0, "group coordinator", progress
            )[0]
            if (
                n5_ready[1] != n5_coordinator_endpoint
                or n5_ready[3] != coordinator_record["pid"]
                or len(n5_ready[2]) != _ACCOUNTS * 3
            ):
                raise AssertionError("LOAD01 N5 coordinator upstream barrier failed")
            coordinator_record["ready"] = True
            for record in owner_records.values():
                record["pipe"].send(("arm-group",))
            armed = _collect_expected(owner_records, "group-armed", 90.0, "N5 owner arm", progress)
            for account_index, message in armed.items():
                receipt = message[3]
                if (
                    message[1:3] != (account_index, owner_records[account_index]["pid"])
                    or type(receipt) is not dict
                    or receipt.get("active") is not True
                    or receipt.get("account_ref") != _ACCOUNT_REFS[account_index]
                    or receipt.get("strategy_id") != _strategy_ref(account_index * _PER_ACCOUNT)
                    or receipt.get("origin_strategy_run_id") != n5_routes[account_index * _PER_ACCOUNT]["origin_strategy_run_id"]
                    or any(type(receipt.get(key)) is not str or not receipt[key]
                           for key in ("registration_id", "query_registration_id", "reservation_registration_id"))
                ):
                    raise AssertionError("LOAD01 N5 owner activation barrier failed")
            report["n5_group_startup"] = {
                "coordinator_pid": coordinator_record["pid"],
                "upstream_bindings": n5_ready[2],
                "owner_activation_receipts": {_ACCOUNT_REFS[i]: message[3] for i, message in armed.items()},
            }

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
                or message[4] != account_strategy_targets[_ACCOUNT_REFS[account_index]]
                or message[5] != account_strategy_targets[_ACCOUNT_REFS[account_index]]
            ):
                raise AssertionError(
                    f"LOAD01 owner relay runtime/binding topology changed: {account_index}"
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
        if n5_enabled:
            service_records = {
                "services": service_records,
                "n5_group_metadata": n5_metadata,
                "n5_coordinator": n5_coordinator,
            }

        phase = "load-strategy-processes"
        for index in range(_STRATEGIES):
            strategy_account_indices = profile.accounts_for_strategy(index)
            strategy_account_services = {
                account_index: account_services[account_index]
                for account_index in strategy_account_indices
            }
            strategy_execution_services = {
                account_index: execution_services[account_index]
                for account_index in strategy_account_indices
            }
            raw_strategy_config = _strategy_config(
                index,
                strategy_account_services,
                strategy_execution_services,
                market_rpc,
                market_pub,
                monitor_message[4],
                account_index=strategy_account_indices[0],
                profile_id=profile_id,
                coordinator_service=n5_coordinator,
            )
            client_public, client_secret = strategy_keys[index]
            security_material = {
                _MARKET_SECURITY_REF: (
                    market_server_keys[0],
                    client_public,
                    client_secret,
                ),
                _TELEMETRY_SECURITY_REF: (
                    telemetry_server_keys[0],
                    client_public,
                    client_secret,
                ),
            }
            if n5_enabled and index in profile.expected_group_capable_strategies:
                security_material[n5_coordinator["security_ref"]] = (
                    n5_coordinator_keys[0], client_public, client_secret,
                )
            for account_index in strategy_account_indices:
                account_service = account_services[account_index]
                execution_service = execution_services[account_index]
                security_material[account_service["security_ref"]] = (
                    account_server_keys[account_index][0],
                    client_public,
                    client_secret,
                )
                security_material[execution_service["security_ref"]] = (
                    execution_server_keys[account_index][0],
                    client_public,
                    client_secret,
                )
            strategy_records[index] = launch_child(
                f"strategy-{index:03d}",
                "strategy",
                _strategy_process,
                (
                    index,
                    raw_strategy_config,
                    strategy_account_services,
                    strategy_execution_services,
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
                    quota_wave_database_path,
                    quota_wave_event,
                ),
            )
        loaded = _collect_expected(
            strategy_records, "loaded", 180.0, "strategy process import", progress
        )
        if len({message[2] for message in loaded.values()}) != _STRATEGIES:
            raise AssertionError(f"LOAD01 did not start {_STRATEGIES} distinct strategy processes")
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
                    or message[4] != _ACCOUNT_REFS[profile.accounts_for_strategy(index)[0]]
                    or message[7] != profile.expected_bindings_per_strategy(index)
                    or message[8] != 0
                    or message[9]
                    != _ACCOUNT_KEYS[profile.accounts_for_strategy(index)[0]].to_dict()
                    or len(message[10]) != expected_clients_by_strategy[index]
                    or dict(message[10])
                    != dict.fromkeys(strategy_market_streams[index], 0)
                    or message[12]
                    != {
                        account_index: _ACCOUNT_KEYS[account_index].to_dict()
                        for account_index in profile.accounts_for_strategy(index)
                    }
                    or type(message[11]) is not dict
                    or message[11].get("quota_wave_initialized") is not (profile_id == "N6")
                ):
                    raise AssertionError(
                        f"LOAD01 strategy did not pass its registration barrier: {index}"
                    )
                record["ready"] = True
                record["account_index"] = profile.accounts_for_strategy(index)[0]
                record["account_indices"] = list(profile.accounts_for_strategy(index))
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
        if (
            len(child_records) != expected_child_count
            or len(set(all_pids)) != expected_child_count
            or len(ready_pids) != _STRATEGIES
        ):
            raise AssertionError(f"LOAD01 must keep exactly {expected_child_count} distinct child processes alive")
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
                "LOAD01 service registrations do not match the selected profile topology"
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
            "all_children_alive": True,
            "expected_child_count": expected_child_count,
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
        if profile_id == "N6" and query_startup_measurement is None:
            raise AssertionError("LOAD01 N6 startup quota slots were not measured")
        load_event.set()
        report["load"] = {
            "scheduled_monotonic_start": load_start,
            "requested_duration_seconds": duration_seconds,
            "profile_id": profile_id,
            "strategy_count": _STRATEGIES,
            "account_count": _ACCOUNTS,
            "market_input_target_per_second": profile.total_input_target_per_second,
            "topic_input_target_per_second": topic_rate_targets,
            "strategy_market_deliveries_target_per_second": profile.total_delivery_target_per_second,
            "market_payload_bytes_target": 256 if profile_id in {"U0", "N3", "N5", "N6"} else None,
            "market_payload_bytes_reference_target": {"profile_id": "U0", "bytes_per_message": 256},
            "order_lifecycle_target_per_account_per_second": account_order_rate_targets,
            "order_lifecycle_target_total_per_second": sum(
                profile.order_targets_per_account_per_second
            ),
            "quota_admission_wave": (
                _n6_quota_admission_wave_contract(quota_wave_module)
                if profile_id == "N6"
                else None
            ),
            "server_time_query_probe": (
                {
                    "scenario": "n6_sdk_server_time_quota_probe",
                    "scheduled_after_load_start_seconds": _N6_QUOTA_WAVE_START_SECONDS,
                    "planned_sdk_query_count": 2,
                    "quota_domain_ref": _N6_QUERY_QUOTA_DOMAIN_REF,
                    "quota_capacity": _N6_QUERY_QUOTA_CAPACITY,
                    "reserved_capacity": query_startup_measurement["reserved_capacity"],
                    "quota_window_seconds": _N6_QUERY_QUOTA_WINDOW_SECONDS,
                    "quota_max_queue_wait_seconds": _N6_QUERY_QUOTA_MAX_WAIT_SECONDS,
                    "runtime_rpc_timeout_ms": _N6_QUERY_RPC_TIMEOUT_MS,
                    "startup_slots_measured": query_startup_measurement,
                    "subject_scope": "account_only; no strategy-fairness claim",
                    "provider_boundary": "synthetic local provider; no exchange request",
                    "evidence_scope": "query-path source integration only",
                    "measurement_scope": (
                        "wall time around the two serial SDK calls; "
                        "no separate quota queue-residence latency"
                    ),
                }
                if profile_id == "N6"
                else None
            ),
            "load_start_at_utc": _utc_now(),
        }
        _write_report(output_path, report)

        phase = "sustained-load"
        low_memory_samples = 0
        high_cpu_samples = 0
        last_resource_at = load_start
        next_resource_tick = 0
        next_resource_at = load_start
        last_recorded_resource_tick = None
        next_report_at = load_start + 5
        stop_requested_at = None
        while True:
            _drain_progress(child_records, progress)
            now = time.monotonic()
            if profile_id == "N6" and quota_wave_fire_record is None:
                quota_wave_fire_record = _n6_quota_wave_seed_if_due(
                    profile_id=profile_id,
                    now=now,
                    load_start=load_start,
                    all_strategies_ready=(
                        len(strategy_ready) == _STRATEGIES
                        and all(record["ready"] for record in strategy_records.values())
                    ),
                    admission=quota_wave_admission,
                    wave_event=quota_wave_event,
                )
                if quota_wave_fire_record is not None:
                    wave_report = report["load"]["quota_admission_wave"]
                    wave_report.update(
                        {
                            "max_queue": quota_wave_module.MAX_QUEUE,
                            "max_queue_per_subject": quota_wave_module.MAX_QUEUE_PER_SUBJECT,
                            "max_backoff_seconds": quota_wave_module.MAX_BACKOFF_SECONDS,
                            "max_queue_wait_seconds": quota_wave_module.MAX_QUEUE_WAIT_SECONDS,
                            "poll_interval_seconds": quota_wave_module.POLL_INTERVAL_SECONDS,
                            "seed": quota_wave_fire_record,
                            "state": "seeded_and_triggered",
                        }
                    )
                    _write_report(output_path, report)
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
                sample_tick = next_resource_tick
                sample, prior_cpu = _resource_sample(
                    child_records,
                    started_at=load_start,
                    prior_cpu=prior_cpu,
                    sample_at=now,
                    previous_sample_at=last_resource_at,
                    logical_cpu_count=logical_cpu_count,
                )
                sample["database_tree_bytes"] = _database_tree_size(database_paths)
                sample["load01_cpu_sample_tick"] = sample_tick
                sample["load01_cpu_scheduled_elapsed_seconds"] = (
                    sample_tick * _LOAD01_CPU_SAMPLE_INTERVAL_SECONDS
                )
                sample["load01_cpu_steady_load_sample"] = sample_tick > 0
                append_and_validate_load01_resource_sample(report, sample, child_records)
                previous_resource_tick = last_recorded_resource_tick
                last_recorded_resource_tick = sample_tick
                last_resource_at = now
                next_resource_tick = max(
                    sample_tick + 1,
                    int((now - load_start) // _LOAD01_CPU_SAMPLE_INTERVAL_SECONDS) + 1,
                )
                next_resource_at = (
                    load_start
                    + next_resource_tick * _LOAD01_CPU_SAMPLE_INTERVAL_SECONDS
                )
                low_memory_samples = (
                    low_memory_samples + 1
                    if sample["available_memory_bytes"] < _MIN_FREE_BYTES
                    else 0
                )
                if sample_tick > 0:
                    high_cpu_samples = (
                        high_cpu_samples + 1
                        if sample["system_cpu_percent"] >= 95.0
                        else 0
                    )
                if sample["database_tree_bytes"] > 2 * 1024**3:
                    raise RuntimeError("LOAD01 SQLite work tree exceeded 2 GiB")
                if low_memory_samples >= 2:
                    raise RuntimeError("LOAD01 free memory remained below 2 GiB for two samples")
                if high_cpu_samples >= 12:
                    raise RuntimeError("LOAD01 system CPU remained above 95% for one minute")
            report["progress"] = _progress_summary(
                progress, expected_clients_by_strategy=expected_clients_by_strategy
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
            progress, expected_clients_by_strategy=expected_clients_by_strategy
        )

        phase = "market-terminal-watermark"
        terminal_token = time.monotonic_ns()
        market_record["pipe"].send(("terminal-watermark", terminal_token))
        try:
            terminal_message = _collect_expected(
                {0: market_record},
                "terminal-watermark",
                10.0,
                "market terminal watermark",
                progress,
            )[0]
            if type(terminal_message) is not tuple or len(terminal_message) != 3:
                raise AssertionError("LOAD01 market terminal watermark identity failed")
            terminal_market_status, terminal_watermark = (
                _validate_load01_terminal_market_message(
                    terminal_message,
                    expected_token=terminal_token,
                    expected_stream_ids=tuple(key.stream_id for key in market_keys),
                )
            )
        except Exception as exc:
            report["market_terminal_watermark"] = {
                "state": "failed",
                "error_code": _safe_diagnostic_code(exc),
            }
            _write_report(output_path, report)
            raise
        terminal_capture_ns = terminal_watermark["captured_monotonic_ns"]
        terminal_deadline_ns = terminal_capture_ns + 10_000_000_000
        terminal_collection_deadline_ns = terminal_deadline_ns + 7_000_000_000
        report["market_terminal_watermark"] = {
            "state": "captured",
            "source_publishing_stopped": True,
            "captured_monotonic_ns": terminal_capture_ns,
            "source_sequences": dict(terminal_market_status["source_sequences"]),
            "hub_watermarks": terminal_watermark["watermarks"],
        }
        phase = "market-terminal-catchup"
        terminal_strategy_targets = {
            index: {
                stream_id: terminal_watermark["watermarks"][stream_id]
                for stream_id in strategy_market_streams[index]
            }
            for index in strategy_records
        }
        for index, record in strategy_records.items():
            record["pipe"].send(
                (
                    "catch-up-to-watermark",
                    terminal_token,
                    terminal_capture_ns,
                    terminal_strategy_targets[index],
                )
            )
        # The extra seven seconds collect finite late/incomplete diagnostics;
        # each child's ACK timestamp below still has to meet the original
        # ten-second performance deadline to pass.
        remaining_seconds = max(
            0.0,
            (terminal_collection_deadline_ns - time.monotonic_ns())
            / 1_000_000_000,
        )
        if remaining_seconds <= 0:
            report["market_terminal_catchup"] = {
                "state": "timed_out_before_strategy_wait",
                "barrier_captured_monotonic_ns": terminal_capture_ns,
                "deadline_monotonic_ns": terminal_deadline_ns,
                "strategy_results": {},
            }
            _write_report(output_path, report)
            raise TimeoutError("LOAD01 terminal market catch-up exceeded ten seconds")
        try:
            terminal_catchup_messages = _collect_expected(
                strategy_records,
                "market-watermark-result",
                remaining_seconds,
                "market terminal strategy catch-up",
                progress,
            )
        except Exception as exc:
            report["market_terminal_catchup"] = {
                "state": "timed_out_or_failed",
                "barrier_captured_monotonic_ns": terminal_capture_ns,
                "deadline_monotonic_ns": terminal_deadline_ns,
                "error_code": _safe_diagnostic_code(exc),
                "strategy_results": {},
            }
            _write_report(output_path, report)
            raise
        collected_at_ns = time.monotonic_ns()
        terminal_catchup_by_index = {}
        terminal_catchup_failures: dict[int, tuple[str, ...]] = {}
        for index, message in terminal_catchup_messages.items():
            failures = []
            expected_targets = terminal_strategy_targets[index]
            try:
                result = _validate_load01_strategy_watermark_message(
                    message,
                    expected_index=index,
                    expected_pid=strategy_records[index]["pid"],
                    expected_token=terminal_token,
                )
            except AssertionError:
                failures.append("strategy_terminal_ack_identity_invalid")
                terminal_catchup_by_index[index] = {
                    "message_shape": len(message) if type(message) is tuple else None
                }
                terminal_catchup_failures[index] = tuple(failures)
                continue
            if result.get("targets") != expected_targets:
                failures.append("strategy_terminal_targets_mismatch")
            failures.extend(
                _load01_market_terminal_ack_timing_failures(
                    ack_sent_monotonic_ns=result["ack_sent_monotonic_ns"],
                    parent_collected_monotonic_ns=collected_at_ns,
                    deadline_monotonic_ns=terminal_deadline_ns,
                    collection_deadline_monotonic_ns=(
                        terminal_collection_deadline_ns
                    ),
                )
            )
            checked = _load01_market_watermark_completion_failures(
                targets=expected_targets,
                expected_stream_ids=tuple(strategy_market_streams[index]),
                receipts=result.get("receipts"),
                last_sequences=result.get("last_sequences"),
                last_epochs=result.get("last_epochs"),
                client_states=result.get("client_states"),
                runtime_state=result.get("runtime_state"),
                counter_deltas=result.get("counter_deltas"),
                barrier_captured_monotonic_ns=terminal_capture_ns,
                caught_up_at_monotonic_ns=result.get("caught_up_at_monotonic_ns"),
            )
            failures.extend(checked)
            if result.get("complete") is not True:
                failures.append("strategy_reported_incomplete")
            reported_failures = result.get("failures")
            if type(reported_failures) not in (tuple, list) or any(
                type(item) is not str for item in reported_failures
            ):
                failures.append("strategy_failure_codes_invalid")
            elif reported_failures:
                failures.extend(reported_failures)
            terminal_catchup_by_index[index] = result
            if failures:
                terminal_catchup_failures[index] = tuple(dict.fromkeys(failures))
        if collected_at_ns > terminal_collection_deadline_ns:
            terminal_catchup_failures[-1] = (
                "parent_collection_after_diagnostic_grace",
            )
        ack_times = [
            result["ack_sent_monotonic_ns"]
            for result in terminal_catchup_by_index.values()
            if type(result.get("ack_sent_monotonic_ns")) is int
        ]
        catchup_completed_at_ns = max(ack_times, default=collected_at_ns)
        report["market_terminal_catchup"] = {
            "state": "complete" if not terminal_catchup_failures else "failed",
            "barrier_captured_monotonic_ns": terminal_capture_ns,
            "deadline_monotonic_ns": terminal_deadline_ns,
            "collected_at_monotonic_ns": collected_at_ns,
            "catchup_elapsed_ms": max(
                0.0,
                (catchup_completed_at_ns - terminal_capture_ns) / 1_000_000,
            ),
            "strategy_results": terminal_catchup_by_index,
            "failure_codes_by_strategy": terminal_catchup_failures,
        }
        _write_report(output_path, report)
        if terminal_catchup_failures:
            raise AssertionError("LOAD01 terminal market catch-up did not prove delivery")

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
        if profile_id == "N6":
            if (
                quota_wave_fire_record is None
                or quota_wave_event is None
                or not quota_wave_event.is_set()
                or quota_wave_admission is None
            ):
                raise AssertionError("LOAD01 N6 quota wave was not seeded and triggered")
            quota_outcomes = _n6_quota_wave_outcomes_from_strategy_results(strategy_results)
            quota_summary = quota_wave_module.finish_quota_wave(
                quota_wave_admission, quota_outcomes
            )
            report["load"]["quota_admission_wave"].update(
                {
                    "offered": quota_summary.offered,
                    "granted": quota_summary.granted,
                    "deadline_exceeded": quota_summary.deadline_exceeded,
                    "grants_by_strategy": list(quota_summary.grants_by_strategy),
                    "denials_by_strategy": list(quota_summary.denials_by_strategy),
                    "grant_p95_ns": quota_summary.grant_p95_ns,
                    "denial_p95_ns": quota_summary.denial_p95_ns,
                    "grant_admission_call_elapsed_p95_ms": (
                        quota_summary.grant_p95_ns / 1_000_000
                    ),
                    "deadline_denial_admission_call_elapsed_p95_ms": (
                        quota_summary.denial_p95_ns / 1_000_000
                    ),
                    "latency_measurement_scope": (
                        "time from each admission thread starting GatewayQuotaAdmission.admit "
                        "until its return; no separate queue-residence latency is measured"
                    ),
                    "pending_waiters_after_collection": quota_wave_admission.pending_waiter_count,
                    "state": "complete",
                }
            )
        for index, terminal_result in terminal_catchup_by_index.items():
            final_strategy = strategy_results[index]
            if not _load01_market_terminal_cursors_match(terminal_result, final_strategy):
                raise AssertionError(
                    f"LOAD01 strategy market cursor changed after terminal ACK: {index}"
                )
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
        final_terminal_watermark = _validate_load01_terminal_market_status(
            market_status,
            tuple(key.stream_id for key in market_keys),
        )
        if (
            final_terminal_watermark["watermarks"] != terminal_watermark["watermarks"]
            or market_status["source_sequences"] != terminal_market_status["source_sequences"]
        ):
            raise AssertionError("LOAD01 market terminal watermark changed during normal close")
        report["market_terminal_watermark"]["final_close_captured_monotonic_ns"] = (
            final_terminal_watermark["captured_monotonic_ns"]
        )
        _write_report(output_path, report)
        if coordinator_record is not None:
            phase = "normal-group-coordinator-shutdown"
            coordinator_record["pipe"].send(("close",))
            coordinator_closed = _collect_expected(
                {0: coordinator_record}, "closed", 60.0, "group coordinator normal close", progress
            )[0]
            if coordinator_closed[2:] != ("closed", "closed"):
                raise AssertionError("LOAD01 N5 coordinator or upstream runtime did not close")
            _normal_close_records(
                {"group-coordinator": coordinator_record},
                {"group-coordinator": coordinator_closed},
            )
            report["n5_group_close"] = {
                "pid": coordinator_closed[1], "host_state": coordinator_closed[2],
                "upstream_state": coordinator_closed[3], "normal_exit": True,
            }
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
            relay_close_facts = message[17]
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
                "relay_runtime_client_count": relay_close_facts["relay_runtime_count"],
                "relay_runtime_binding_count": relay_close_facts[
                    "relay_runtime_binding_count"
                ],
                "relay_runtime_binding_count_after_close": relay_close_facts[
                    "relay_runtime_binding_count_after_close"
                ],
                "relay_runtime_active_binding_count": relay_close_facts[
                    "relay_runtime_active_binding_count"
                ],
                "relay_runtime_unknown_registration_count": relay_close_facts[
                    "relay_runtime"
                ]["unknown_registration_count"],
                "relay_worker_thread_count": relay_close_facts["relay_runtime"][
                    "worker_thread_count"
                ],
                "relay_worker_joined_count": relay_close_facts["relay_runtime"][
                    "worker_joined_count"
                ],
                "relay_workers_by_scope": relay_close_facts["relay_workers_by_scope"],
                "account_provider": status,
                "n5_group_runtime": (
                    message[17].get("n5_group_runtime")
                    if len(message) > 17 and type(message[17]) is dict else None
                ),
                "synthetic_execution": (
                    message[17].get("synthetic_execution")
                    if len(message) > 17 and type(message[17]) is dict
                    else None
                ),
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
                or relay_close_facts["relay_runtime_count"]
                != account_strategy_targets[_ACCOUNT_REFS[account_index]]
                or relay_close_facts["relay_runtime_binding_count"]
                != account_strategy_targets[_ACCOUNT_REFS[account_index]]
                or relay_close_facts["relay_runtime_binding_count_after_close"] != 0
                or relay_close_facts["relay_runtime_active_binding_count"] != 0
                or relay_close_facts["relay_runtime"]["unknown_registration_count"] != 0
                or relay_close_facts["relay_runtime"]["worker_joined_count"]
                != account_strategy_targets[_ACCOUNT_REFS[account_index]]
                or status["provider_close_calls"] != 1
            ):
                raise AssertionError(
                    f"LOAD01 owner or account session failed normal close: {account_index}"
                )
            if n5_enabled and (
                type(account_record["n5_group_runtime"]) is not dict
                or account_record["n5_group_runtime"].get("state") != "closed"
            ):
                raise AssertionError("LOAD01 N5 owner proof resources did not close")
            owner_totals[account_index] = account_record
            owner_source_records.extend(message[15])
        if profile_id == "N6":
            query_probe_result = strategy_results[0].get("server_time_query_probe")
            if (
                type(query_probe_result) is not dict
                or query_probe_result.get("started") is not True
                or query_probe_result.get("scheduled_after_load_start_seconds")
                != _N6_QUOTA_WAVE_START_SECONDS
                or type(query_probe_result.get("trigger_elapsed_seconds"))
                not in {int, float}
                or query_probe_result["trigger_elapsed_seconds"]
                < _N6_QUOTA_WAVE_START_SECONDS
                or query_probe_result.get("runtime_rpc_timeout_ms")
                != _N6_QUERY_RPC_TIMEOUT_MS
                or query_probe_result.get("quota_max_queue_wait_seconds")
                != _N6_QUERY_QUOTA_MAX_WAIT_SECONDS
                or type(query_probe_result.get("outcomes")) is not list
                or len(query_probe_result["outcomes"]) != 2
                or type(query_probe_result.get("started_monotonic_ns")) is not int
                or type(query_probe_result.get("completed_monotonic_ns")) is not int
                or query_probe_result["completed_monotonic_ns"]
                < query_probe_result["started_monotonic_ns"]
            ):
                raise AssertionError("LOAD01 N6 SDK server-time query result is incomplete")
            manifest_data = json.loads(
                Path(os.environ["BT_API_LOAD01_SOURCE_MANIFEST"]).read_text(
                    encoding="utf-8"
                )
            )
            query_probe = _load_n6_query_quota_probe_modules(
                manifest_data["files"]
            )["probe"]
            query_summary = query_probe.summarize_server_time_probe(
                query_probe_result["outcomes"],
                owner_totals[0]["account_provider"].get("server_time_quota_facts"),
            )
            query_summary["trigger_elapsed_seconds"] = query_probe_result[
                "trigger_elapsed_seconds"
            ]
            query_summary["sdk_call_sequence_wall_time_ms"] = (
                query_probe_result["completed_monotonic_ns"]
                - query_probe_result["started_monotonic_ns"]
            ) / 1_000_000
            query_summary["measurement_scope"] = (
                "wall time around the two serial SDK calls; no separate quota queue-residence latency"
            )
            report["load"]["server_time_query_probe"].update(query_summary)
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
            or monitor_status["durable_scope_count"] != profile.strategy_account_binding_count
            or len(telemetry_snapshots) != profile.strategy_account_binding_count
            or any(
                item["stream_count"] != 1 or item["state"] != "collected"
                for item in telemetry_snapshots
            )
        ):
            raise AssertionError(
                "LOAD01 Monitor outbox or telemetry scope coverage failed: "
                f"closed={monitor_closed[2]!r} "
                f"close_error={monitor_closed[3]!r} "
                f"durable_state={monitor_closed[4]!r} "
                f"forced={monitor_closed[5]!r} "
                f"durable_scope_count={monitor_status['durable_scope_count']} "
                f"expected={profile.strategy_account_binding_count} "
                f"snapshots={len(telemetry_snapshots)} "
                f"snapshot_states="
                f"{[(item.get('stream_count'), item.get('state')) for item in telemetry_snapshots]}"
            )
        phase = "exact-durable-and-registration-audit"
        durable_audit = _durable_outbox_audit(
            outbox_path, tuple(owner_source_records), profile.profile_id
        )
        total_strategy_cycles = sum(row["order_cycles"] for row in strategy_results.values())
        n4_strategy_market_totals: dict[tuple[str, str, str], Counter[str]] = {}
        n4_strategy_instrument_totals: dict[tuple[str, str], Counter[str]] = {}
        if profile.profile_id == "N4":
            for row in strategy_results.values():
                account_ref = _ACCOUNT_REFS[row["account_index"]]
                for stream_id, facts in row.get("n4_execution_by_stream", {}).items():
                    key = (account_ref, facts["instrument_id"], stream_id)
                    totals = n4_strategy_market_totals.setdefault(key, Counter())
                    instrument_totals = n4_strategy_instrument_totals.setdefault(
                        (account_ref, facts["instrument_id"]), Counter()
                    )
                    for field in (
                        "cycles",
                        "submit_calls",
                        "cumulative_fill_observations",
                        "cancelled_orders",
                        "query_count",
                        "journal_fingerprint_matches",
                    ):
                        totals[field] += facts[field]
                        instrument_totals[field] += facts[field]
                    for state, count in facts["query_states"].items():
                        totals[f"query_{state}"] += count
                        instrument_totals[f"query_{state}"] += count
        for account_index, account_record in owner_totals.items():
            account_cycles = sum(
                row["order_cycles_by_account"].get(_ACCOUNT_REFS[account_index], 0)
                for row in strategy_results.values()
            )
            if profile.profile_id == "N4":
                synthetic_execution = account_record["synthetic_execution"]
                if (
                    type(synthetic_execution) is not dict
                    or type(synthetic_execution.get("market_facts")) is not list
                ):
                    raise AssertionError("LOAD01 N4 owner omitted bounded provider facts")
                expected_fill_count = sum(
                    totals["cumulative_fill_observations"]
                    for key, totals in n4_strategy_market_totals.items()
                    if key[0] == _ACCOUNT_REFS[account_index]
                )
                expected_cancel_count = account_cycles - expected_fill_count
                actual_market_facts = {
                    (
                        fact["account_ref"],
                        fact["instrument_id"],
                        fact["market_stream_id"],
                    ): fact
                    for fact in synthetic_execution["market_facts"]
                }
                account_ref = _ACCOUNT_REFS[account_index]
                expected_keys = {
                    (
                        account_ref,
                        market_keys[topic_index].instrument.instrument_id,
                        market_keys[topic_index].stream_id,
                    )
                    for strategy_index in profile.strategy_indices_by_account[account_index]
                    for topic_index in profile.subscribed_topic_indices(strategy_index)
                }
                expected_instrument_keys = {
                    (account_ref, market_keys[topic_index].instrument.instrument_id)
                    for strategy_index in profile.strategy_indices_by_account[account_index]
                    for topic_index in profile.subscribed_topic_indices(strategy_index)
                }
                actual_instrument_facts: dict[tuple[str, str], Counter[str]] = {}
                for (fact_account, instrument_id, _stream_id), fact in actual_market_facts.items():
                    instrument_fact = actual_instrument_facts.setdefault(
                        (fact_account, instrument_id), Counter()
                    )
                    for field in (
                        "submit_calls",
                        "intent_readbacks",
                        "cumulative_fill_observations",
                        "cancel_calls",
                        "acknowledged_orders",
                    ):
                        instrument_fact[field] += fact.get(field, 0)
                if (
                    account_record["provider_submit_calls"] != account_cycles
                    or account_record["provider_cancel_calls"] != expected_cancel_count
                    or account_record["provider_submit_calls"] - expected_cancel_count
                    != expected_fill_count
                    or synthetic_execution["active_acknowledged_orders"] != 0
                    or synthetic_execution["terminal_fill_count"] != expected_fill_count
                    or set(actual_market_facts) != expected_keys
                    or set(actual_instrument_facts) != expected_instrument_keys
                ):
                    raise AssertionError(
                        f"LOAD01 N4 lifecycle provider totals differ for account {account_index}"
                    )
                for key in expected_keys:
                    expected = n4_strategy_market_totals[key]
                    actual = actual_market_facts[key]
                    if (
                        actual.get("submit_calls") != expected["submit_calls"]
                        or actual.get("intent_readbacks") != expected["submit_calls"]
                        or actual.get("cumulative_fill_observations")
                        != expected["cumulative_fill_observations"]
                        or actual.get("cancel_calls") != expected["cancelled_orders"]
                        or actual.get("acknowledged_orders") != expected["cancelled_orders"]
                        or expected["cycles"]
                        != expected["cumulative_fill_observations"]
                        + expected["cancelled_orders"]
                        or expected["query_FILLED"]
                        != expected["cumulative_fill_observations"]
                        or expected["query_ACKED"] != expected["cancelled_orders"]
                        or expected["query_CANCELLED"] != expected["cancelled_orders"]
                        or expected["query_count"]
                        != expected["cumulative_fill_observations"]
                        + 2 * expected["cancelled_orders"]
                        or expected["journal_fingerprint_matches"] != expected["query_count"]
                    ):
                        raise AssertionError(
                            f"LOAD01 N4 instrument/query journal facts diverged for {key}"
                        )
                for instrument_key in expected_instrument_keys:
                    expected = n4_strategy_instrument_totals[instrument_key]
                    actual = actual_instrument_facts[instrument_key]
                    if (
                        actual.get("submit_calls") != expected["submit_calls"]
                        or actual.get("intent_readbacks") != expected["submit_calls"]
                        or actual.get("cumulative_fill_observations")
                        != expected["cumulative_fill_observations"]
                        or actual.get("cancel_calls") != expected["cancelled_orders"]
                        or actual.get("acknowledged_orders") != expected["cancelled_orders"]
                        or expected["query_FILLED"]
                        != expected["cumulative_fill_observations"]
                        or expected["query_ACKED"] != expected["cancelled_orders"]
                        or expected["query_CANCELLED"] != expected["cancelled_orders"]
                        or expected["query_count"]
                        != expected["cumulative_fill_observations"]
                        + 2 * expected["cancelled_orders"]
                        or expected["journal_fingerprint_matches"] != expected["query_count"]
                    ):
                        raise AssertionError(
                            f"LOAD01 N4 instrument-aggregated journal facts diverged for {instrument_key}"
                        )
            elif n5_enabled:
                group_cycles = sum(
                    row["n5_group_metrics"]["group_logical_count_by_account"].get(_ACCOUNT_REFS[account_index], 0)
                    for row in strategy_results.values()
                )
                ordinary_cycles = account_cycles - group_cycles
                if (
                    ordinary_cycles < 0
                    or account_record["provider_submit_calls"] != ordinary_cycles
                    or account_record["provider_cancel_calls"] != ordinary_cycles
                ):
                    raise AssertionError("LOAD01 N5 ordinary provider counts differ from observed cycles")
            elif (
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
            len(owner_source_records) != profile.strategy_account_binding_count
            or durable_audit["durable_scopes"] != profile.strategy_account_binding_count
        ):
            raise AssertionError(
                "LOAD01 Monitor did not preserve every strategy-account journal"
            )
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
        recovery_timing_summary = aggregate_recovery_observations(
            strategy_results, observed_through_monotonic_ns=time.monotonic_ns()
        )
        report["load"]["market_recovery_timing"] = recovery_timing_summary
        if recovery_timing_summary["target_timing_status"] != "WITHIN_10S_FOR_ALL_TARGETS":
            raise AssertionError("LOAD01 recovery target receipt missed its ten-second bound or lacks evidence")
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

        report["registered_performance_metrics"] = _load01_performance_measurement_report(
            profile=profile,
            mode=mode,
            strategy_results=strategy_results,
            resource_samples=report["resource_samples"],
            planned_duration_seconds=duration_seconds,
            actual_duration_seconds=actual_duration,
            topic_stream_ids_by_index=tuple(key.stream_id for key in market_keys),
            source_sequences_by_stream=market_status["source_sequences"],
            registration=threshold_registration,
            registration_pin=threshold_registration_pin,
            registration_raw=threshold_registration_raw,
        )
        metric_evaluation = report["registered_performance_metrics"]
        if metric_evaluation["p0_evaluation_eligible"] and metric_evaluation["violations"]:
            raise AssertionError("LOAD01 registered performance targets were not met")
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
                        row["order_cycles_by_account"].get(_ACCOUNT_REFS[index], 0)
                        for row in strategy_results.values()
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
            "performance_threshold_registration": threshold_registration_pin,
            "registered_metrics_scope": "per-strategy operation RPC and market E2E; lifecycle remains separate",
        }
        if profile.profile_id == "N4":
            report["latency_ms"]["order_submit_cancel_roundtrip_semantics"] = (
                "Per-cycle samples include submit plus durable query; non-fill cycles also "
                "include ACKED query, cancel, and CANCELLED query. No N4 latency threshold is registered."
            )
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
            "recovery_timing": recovery_timing_summary,
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
            "relay_runtime_client_count": sum(
                value["relay_runtime_client_count"] for value in owner_totals.values()
            ),
            "relay_runtime_binding_count": sum(
                value["relay_runtime_binding_count"] for value in owner_totals.values()
            ),
            "relay_runtime_binding_count_after_close": sum(
                value["relay_runtime_binding_count_after_close"]
                for value in owner_totals.values()
            ),
            "relay_runtime_active_binding_count": sum(
                value["relay_runtime_active_binding_count"]
                for value in owner_totals.values()
            ),
            "relay_runtime_unknown_registration_count": sum(
                value["relay_runtime_unknown_registration_count"]
                for value in owner_totals.values()
            ),
            "relay_worker_thread_count": sum(
                value["relay_worker_thread_count"] for value in owner_totals.values()
            ),
            "relay_worker_joined_count": sum(
                value["relay_worker_joined_count"] for value in owner_totals.values()
            ),
            "relay_runtime_clients_by_account": {
                _ACCOUNT_REFS[index]: value["relay_runtime_client_count"]
                for index, value in owner_totals.items()
            },
            "relay_runtime_bindings_by_account": {
                _ACCOUNT_REFS[index]: value["relay_runtime_binding_count"]
                for index, value in owner_totals.items()
            },
            "relay_runtime_bindings_after_close_by_account": {
                _ACCOUNT_REFS[index]: value["relay_runtime_binding_count_after_close"]
                for index, value in owner_totals.items()
            },
            "relay_runtime_active_bindings_by_account": {
                _ACCOUNT_REFS[index]: value["relay_runtime_active_binding_count"]
                for index, value in owner_totals.items()
            },
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
        if n5_enabled:
            report["accounts_and_execution"]["n5_group_metrics"] = build_n5_group_report(
                profile, strategy_results, owner_totals, duration_seconds, actual_duration,
            )
        if profile.profile_id == "N4":
            topic_execution_distribution = []
            instrument_execution_distribution = []
            order_topic_targets = profile.order_targets_by_account_topic_per_second
            for account_index in range(_ACCOUNTS):
                account_ref = _ACCOUNT_REFS[account_index]
                account_topics = set(
                    topic_index
                    for strategy_index in profile.strategy_indices_by_account[account_index]
                    for topic_index in profile.subscribed_topic_indices(strategy_index)
                )
                account_instruments = sorted(
                    {
                        market_keys[topic_index].instrument.instrument_id
                        for topic_index in account_topics
                    }
                )
                provider_by_instrument: dict[str, Counter[str]] = {}
                for provider_fact in owner_totals[account_index]["synthetic_execution"][
                    "market_facts"
                ]:
                    provider_instrument = provider_by_instrument.setdefault(
                        provider_fact["instrument_id"], Counter()
                    )
                    for field in (
                        "submit_calls",
                        "intent_readbacks",
                        "cumulative_fill_observations",
                        "cancel_calls",
                        "acknowledged_orders",
                    ):
                        provider_instrument[field] += provider_fact.get(field, 0)
                for instrument_id in account_instruments:
                    instrument_totals = n4_strategy_instrument_totals[
                        (account_ref, instrument_id)
                    ]
                    provider_totals = provider_by_instrument[instrument_id]
                    instrument_topics = tuple(
                        topic_index
                        for topic_index in sorted(account_topics)
                        if market_keys[topic_index].instrument.instrument_id == instrument_id
                    )
                    instrument_target_rate = fsum(
                        order_topic_targets[account_index][topic_index]
                        for topic_index in instrument_topics
                    )
                    instrument_execution_distribution.append(
                        {
                            "account_ref": account_ref,
                            "instrument_id": instrument_id,
                            "market_topic_keys": [
                                f"K{topic_index:02d}" for topic_index in instrument_topics
                            ],
                            "market_stream_ids": [
                                market_keys[topic_index].stream_id
                                for topic_index in instrument_topics
                            ],
                            "expected_lifecycle_targets_per_second": instrument_target_rate,
                            "expected_lifecycle_targets_per_10_seconds": 10
                            * instrument_target_rate,
                            "observed_lifecycle_count": instrument_totals["cycles"],
                            "observed_lifecycles_per_second": (
                                instrument_totals["cycles"] / actual_duration
                            ),
                            "provider_submit_calls": provider_totals["submit_calls"],
                            "journal_intent_readbacks": provider_totals["intent_readbacks"],
                            "cumulative_fill_observations": provider_totals[
                                "cumulative_fill_observations"
                            ],
                            "provider_cancel_calls": provider_totals["cancel_calls"],
                            "acknowledged_orders": provider_totals[
                                "acknowledged_orders"
                            ],
                            "query_states": {
                                state: instrument_totals.get(f"query_{state}", 0)
                                for state in ("FILLED", "ACKED", "CANCELLED")
                            },
                            "journal_fingerprint_matches": instrument_totals[
                                "journal_fingerprint_matches"
                            ],
                        }
                    )
                for topic_index, market_key in enumerate(market_keys):
                    facts = n4_strategy_market_totals.get(
                        (
                            account_ref,
                            market_key.instrument.instrument_id,
                            market_key.stream_id,
                        ),
                        Counter(),
                    )
                    cycles = facts.get("cycles", 0)
                    topic_execution_distribution.append(
                        {
                            "account_ref": account_ref,
                            "topic_key": f"K{topic_index:02d}",
                            "market_stream_id": market_key.stream_id,
                            "instrument_id": market_key.instrument.instrument_id,
                            "subscribed": topic_index in account_topics,
                            "expected_lifecycle_targets_per_second": order_topic_targets[
                                account_index
                            ][topic_index],
                            "expected_lifecycle_targets_per_10_seconds": (
                                10 * order_topic_targets[account_index][topic_index]
                            ),
                            "observed_lifecycle_count": cycles,
                            "observed_lifecycles_per_second": cycles / actual_duration,
                            "cumulative_fill_observations": facts.get(
                                "cumulative_fill_observations", 0
                            ),
                            "cancelled_orders": facts.get("cancelled_orders", 0),
                            "query_states": {
                                state: facts.get(f"query_{state}", 0)
                                for state in ("FILLED", "ACKED", "CANCELLED")
                            },
                            "journal_fingerprint_matches": facts.get(
                                "journal_fingerprint_matches", 0
                            ),
                        }
                    )
            report["accounts_and_execution"]["n4_market_execution_distribution"] = {
                "evidence_basis": (
                    "Synthetic cumulative fill observations persisted through the execution "
                    "facade and SQLite journal; query fingerprints and states were checked. "
                    "This is not per-trade OrderFillFact evidence."
                ),
                "cycle_target_window_seconds": 10,
                "distinct_instrument_ids_by_account": {
                    _ACCOUNT_REFS[account_index]: sorted(
                        {
                            market_keys[topic_index].instrument.instrument_id
                            for strategy_index in profile.strategy_indices_by_account[
                                account_index
                            ]
                            for topic_index in profile.subscribed_topic_indices(
                                strategy_index
                            )
                        }
                    )
                    for account_index in range(_ACCOUNTS)
                },
                "by_account_instrument": instrument_execution_distribution,
                "by_account_topic_stream": topic_execution_distribution,
                "per_topic_target_is_report_only": True,
                "latency_threshold_registered": False,
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
                {
                    item["strategy_ref"]
                    for item in telemetry_snapshots
                    if item["stream_count"] == 1
                }
            ),
            "monitor_telemetry_authorized_scopes_received": len(
                [item for item in telemetry_snapshots if item["stream_count"] == 1]
            ),
            "monitor_telemetry_strategy_account_bindings_received": len(
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
            expected_clients_by_strategy=expected_clients_by_strategy,
        )
        report["latency_samples"] = {
            "market_end_to_end_ms": e2e_values,
            "order_submit_cancel_roundtrip_ms": order_values,
            "account_read_ms": account_values,
        }
        report["process_lifecycle"] = {
            "expected_child_count": expected_child_count,
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
                "owner_emergency_close_ack_observed": record.get(
                    "owner_emergency_close_ack_observed"
                ),
                "owner_close_progress": record.get("owner_close_progress"),
            }
            for record in child_records.values()
        ]
        if "registered_performance_metrics" not in report:
            report["registered_performance_metrics"] = {
                "schema_version": 1,
                "evidence_level": "LOCAL_SYNTHETIC_LOAD_MEASUREMENT_NOT_FORMAL_CASE_ACCEPTANCE",
                "registration": threshold_registration_pin,
                "profile_id": profile_id,
                "mode": mode,
                "p0_evaluation_eligible": profile.formal_acceptance_eligible(mode),
                "evaluation_status": "INCOMPLETE_RUN",
                "formal_acceptance_status": "NOT_CLAIMED_BY_METRICS_WIRING",
            }
        report["registered_performance_metrics"]["system_cpu"] = (
            _load01_cpu_schedule_summary(
                report.get("resource_samples", []),
                planned_duration_seconds=duration_seconds,
            )
        )
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
        "market.read_after_bounded",
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
        ("store.market", "market.read_after_bounded"),
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
        "store.market.market.read_after_bounded": 6000.0,
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


def test_load01_stop_poll_budget_is_round_robin_and_bounded() -> None:
    stream_ids = tuple(f"stream-{index:02d}" for index in range(_TOPICS))
    assert _STRATEGY_MARKET_POLL_BATCH_MAX == 20
    assert _load01_terminal_market_poll_budget(
        stop_requested=False,
        terminal_request_active=False,
        stream_count=len(stream_ids),
    ) == _STRATEGY_MARKET_POLL_BATCH_MAX
    assert _load01_terminal_market_poll_budget(
        stop_requested=False,
        terminal_request_active=False,
        stream_count=3,
    ) == 3
    assert _load01_terminal_market_poll_budget(
        stop_requested=True,
        terminal_request_active=False,
        stream_count=len(stream_ids),
    ) == 1
    assert _load01_terminal_market_poll_budget(
        stop_requested=True,
        terminal_request_active=True,
        stream_count=len(stream_ids),
    ) == len(stream_ids)
    assert _load01_terminal_market_poll_budget(
        stop_requested=True,
        terminal_request_active=True,
        stream_count=0,
    ) == 0

    scheduler = _RoundRobinMarketPollScheduler(stream_ids)
    pending_by_stream = dict.fromkeys(stream_ids, 2)
    terminal_budget = _load01_terminal_market_poll_budget(
        stop_requested=True,
        terminal_request_active=True,
        stream_count=len(stream_ids),
    )
    polled: list[str] = []

    def poll_one(*, pause: bool) -> None:
        assert pause is False
        stream_id = scheduler.next_stream_id()
        assert stream_id is not None
        polled.append(stream_id)
        pending_by_stream[stream_id] -= 1

    for _ in range(2):
        polled.clear()
        assert _load01_run_bounded_market_poll_batch(
            poll_one,
            poll_budget=terminal_budget,
            poll_pause=False,
            deadline_monotonic_ns=None,
            monotonic_ns=lambda: 0,
        ) == len(stream_ids)
        assert polled == list(stream_ids)
    assert pending_by_stream == dict.fromkeys(stream_ids, 0)

    # Ordinary polling stops starting new synchronous polls when its per-turn
    # elapsed budget expires, even if stream slots remain in the batch.
    ordinary_budget = _load01_terminal_market_poll_budget(
        stop_requested=False,
        terminal_request_active=False,
        stream_count=len(stream_ids),
    )
    elapsed_ns = [0]
    ordinary_polls: list[str] = []

    def ordinary_poll(*, pause: bool) -> None:
        assert pause is False
        stream_id = scheduler.next_stream_id()
        assert stream_id is not None
        ordinary_polls.append(stream_id)
        elapsed_ns[0] += 4_000_000

    assert _load01_run_bounded_market_poll_batch(
        ordinary_poll,
        poll_budget=ordinary_budget,
        poll_pause=False,
        deadline_monotonic_ns=_STRATEGY_MARKET_POLL_BATCH_BUDGET_NS,
        monotonic_ns=lambda: elapsed_ns[0],
    ) == 3
    assert len(ordinary_polls) == 3
    assert len(set(ordinary_polls)) == 3

    # Fast local polls can visit every subscribed stream in one ordinary turn;
    # the elapsed-time check above still bounds synchronous head/replay calls.
    fast_scheduler = _RoundRobinMarketPollScheduler(stream_ids)
    fast_polls: list[str] = []
    fast_elapsed_ns = [0]

    def fast_poll(*, pause: bool) -> None:
        assert pause is False
        stream_id = fast_scheduler.next_stream_id()
        assert stream_id is not None
        fast_polls.append(stream_id)
        fast_elapsed_ns[0] += 100_000

    assert _load01_run_bounded_market_poll_batch(
        fast_poll,
        poll_budget=ordinary_budget,
        poll_pause=False,
        deadline_monotonic_ns=_STRATEGY_MARKET_POLL_BATCH_BUDGET_NS,
        monotonic_ns=lambda: fast_elapsed_ns[0],
    ) == len(stream_ids)
    assert fast_polls == list(stream_ids)
    assert fast_elapsed_ns[0] < _STRATEGY_MARKET_POLL_BATCH_BUDGET_NS

    # A poll already in progress may cross the absolute cutoff, but the next
    # synchronous poll is not started after that point.
    deadline_ns = 50_000_000_000
    now_ns = [deadline_ns - 1]
    poll_starts = [0]

    def slow_poll(*, pause: bool) -> None:
        assert pause is False
        poll_starts[0] += 1
        now_ns[0] = deadline_ns + 1

    assert _load01_run_bounded_market_poll_batch(
        slow_poll,
        poll_budget=len(stream_ids),
        poll_pause=False,
        deadline_monotonic_ns=deadline_ns,
        monotonic_ns=lambda: now_ns[0],
    ) == 1
    assert poll_starts == [1]
    assert _load01_run_bounded_market_poll_batch(
        slow_poll,
        poll_budget=len(stream_ids),
        poll_pause=False,
        deadline_monotonic_ns=deadline_ns,
        monotonic_ns=lambda: now_ns[0],
    ) == 0
    assert poll_starts == [1]


def test_load01_due_market_publish_batch_is_capped_and_uses_real_clock() -> None:
    market_keys = ("topic-a", "topic-b")
    topic_schedule = (0, 1)
    now_ns = [100]
    published: list[tuple[str, int]] = []

    def publish_one(key: str, publish_ns: int) -> None:
        published.append((key, publish_ns))

    schedule_index, next_publish_ns, published_count = (
        _load01_run_due_market_publish_batch(
            publish_one,
            topic_schedule=topic_schedule,
            market_keys=market_keys,
            schedule_index=0,
            next_publish_ns=70,
            interval_ns=10,
            batch_limit=_MARKET_PUBLISH_BATCH_MAX,
            monotonic_ns=lambda: now_ns[0],
        )
    )
    assert published_count == _MARKET_PUBLISH_BATCH_MAX
    assert published == [
        ("topic-a", 100),
        ("topic-b", 100),
        ("topic-a", 100),
        ("topic-b", 100),
    ]
    assert schedule_index == 0
    assert next_publish_ns == 110

    now_ns[0] = 105
    schedule_index, next_publish_ns, published_count = (
        _load01_run_due_market_publish_batch(
            publish_one,
            topic_schedule=topic_schedule,
            market_keys=market_keys,
            schedule_index=schedule_index,
            next_publish_ns=next_publish_ns,
            interval_ns=10,
            batch_limit=_MARKET_PUBLISH_BATCH_MAX,
            monotonic_ns=lambda: now_ns[0],
        )
    )
    assert published_count == 0
    assert schedule_index == 0
    assert next_publish_ns == 110
    assert len(published) == 4

    now_ns[0] = 120
    schedule_index, next_publish_ns, published_count = (
        _load01_run_due_market_publish_batch(
            publish_one,
            topic_schedule=topic_schedule,
            market_keys=market_keys,
            schedule_index=schedule_index,
            next_publish_ns=next_publish_ns,
            interval_ns=10,
            batch_limit=_MARKET_PUBLISH_BATCH_MAX,
            monotonic_ns=lambda: now_ns[0],
        )
    )
    assert published_count == 2
    assert published[-2:] == [("topic-a", 120), ("topic-b", 120)]
    assert schedule_index == 0
    assert next_publish_ns == 130


def test_load01_market_host_skips_sleep_while_publications_are_due() -> None:
    assert _load01_market_host_sleep_seconds(
        next_publish_ns=100,
        now_ns=100,
        stop_requested=False,
    ) == 0.0
    assert _load01_market_host_sleep_seconds(
        next_publish_ns=90,
        now_ns=100,
        stop_requested=False,
    ) == 0.0
    assert _load01_market_host_sleep_seconds(
        next_publish_ns=101,
        now_ns=100,
        stop_requested=False,
    ) == 0.0002
    assert _load01_market_host_sleep_seconds(
        next_publish_ns=10_000_000,
        now_ns=0,
        stop_requested=False,
    ) == 0.001
    assert _load01_market_host_sleep_seconds(
        next_publish_ns=100,
        now_ns=100,
        stop_requested=True,
    ) == 0.001
    assert _load01_market_host_sleep_seconds(
        next_publish_ns=None,
        now_ns=100,
        stop_requested=False,
    ) == 0.001


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


def test_load01_owner_close_report_projection_excludes_wire_scope_and_keeps_source_identity() -> None:
    wire_scope = WireScope(
        _DOMAIN,
        account_ref="account-00",
        strategy_ref="load01-strategy-000",
    )
    source_evidence = {
        "scope_ref": "gw02-account-00:scope-000",
        "account_ref": "gw02-account-00",
        "strategy_index": 0,
        "strategy_ref": "load01-strategy-000",
        "provider": "synthetic",
        "environment": "simulation",
        "source_scope_key": "synthetic|simulation|account-00|strategy-000|2026-10-21",
        "source_generation": "generation-abc123",
        "outbox_scope": "generation-abc123:scope-key",
        "wire_scope": wire_scope,
        "source_rows": 3,
        "source_rows_by_stream": {"execution": 2, "cancellation": 1},
        "source_event_digest": "a" * 64,
        "source_cursor_by_stream": {
            "execution": {"source_sequence": 2, "checkpoint_sequence": 2},
            "cancellation": {"source_sequence": 1, "checkpoint_sequence": 1},
        },
        "relay_delivered": 3,
    }
    worker = {
        "account_index": 0,
        "local_index": 0,
        "scope_ref": source_evidence["scope_ref"],
        "delivered": 3,
        "drain": {
            "poll_count": 4,
            "delivered": 3,
            "source_rows": {"execution": 2, "cancellation": 1},
        },
        "source_evidence": source_evidence,
        "relay_runtime": {
            "state": "closed",
            "binding_count": 0,
            "opened_binding_count": 1,
            "active_binding_count": 0,
            "unknown_registration_count": 0,
            "worker_thread_id": 12345,
            "worker_joined": True,
        },
        "reader_closed": True,
        "relay_closed": True,
        "timing": {
            "relay_poll": {"count": 4, "total_ns": 500, "max_ns": 200},
            "sink_runtime_call": {"count": 3, "total_ns": 300, "max_ns": 150},
            "maintenance_durations": {},
        },
        "cleanup_errors": [],
    }
    owner_close_facts = {
        "normal_drain_completed": True,
        "source_drain_by_scope": [
            {
                "scope_ref": source_evidence["scope_ref"],
                "source_rows": 3,
                "source_rows_by_stream": {"execution": 2, "cancellation": 1},
                "relay_delivered": 3,
            }
        ],
        "relay_workers_by_scope": {"0": worker},
    }

    projected = _load01_project_owner_close_facts_for_report(owner_close_facts)
    encoded = json.dumps(projected, sort_keys=True, allow_nan=False)
    decoded = json.loads(encoded)
    source = decoded["relay_workers_by_scope"]["0"]["source_evidence"]

    assert "wire_scope" not in source
    assert source["scope_ref"] == source_evidence["scope_ref"]
    assert source["source_scope_key"] == source_evidence["source_scope_key"]
    assert source["source_generation"] == source_evidence["source_generation"]
    assert source["source_rows"] == 3
    assert source["source_rows_by_stream"] == {"execution": 2, "cancellation": 1}
    assert source["source_cursor_by_stream"] == source_evidence["source_cursor_by_stream"]
    assert source["relay_delivered"] == 3


def test_load01_single_owner_five_scope_slow_sink_interleaves_and_drains(
    tmp_path: Path, monkeypatch
) -> None:
    """Overload one shared synthetic writer while the production owner pump stays live."""

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
    for relative in (
        "tests/unit/_load01_resource_diagnostics.py",
        "tests/unit/_load01_metrics.py",
        "tests/unit/test_load01_resource_diagnostics.py",
        "tests/unit/test_load01_runner_metrics.py",
        "tests/unit/_load01_profiles.py",
        "tests/unit/_load01_quota_wave.py",
        "tests/unit/test_load01_profiles.py",
        "tests/unit/test_load01_quota_wave_integration.py",
        "tests/unit/_load01_recovery_timing.py",
        "tests/unit/test_load01_recovery_timing.py",
        "tests/unit/_load01_group_workload.py",
        "tests/unit/_load01_group_coordinator.py",
        "tests/unit/_load01_group_topology.py",
        "tests/unit/_load01_n5_owner_resources.py",
        "tests/unit/_load01_n5_report.py",
        "tests/unit/test_load01_n5_report.py",
        "tests/unit/_service_group_provider_fixtures.py",
        "tests/integration/_dim09_three_leg_fixtures.py",
        "tests/unit/test_load01_group_workload.py",
        "tests/unit/test_load01_group_coordinator.py",
        "tests/unit/test_load01_coordinator_outbound_config.py",
        "tests/unit/test_load01_n5_strategy_group_runner_wiring.py",
        "tests/unit/test_load01_n5_spawn_smoke.py",
        _LOAD01_THRESHOLD_REGISTRATION_PATH,
    ):
        candidate = source_root / relative
        manifest_files[relative] = _sha256(candidate)
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
    _check_fixed_source_manifest(manifest_path, profile_id="U0")

    assert _PER_ACCOUNT == 5
    assert _LIFECYCLES_PER_ACCOUNT_HZ == 2
    assert _RPC_TIMEOUT_MS == 5_000
    cycle_period = _PER_ACCOUNT / _LIFECYCLES_PER_ACCOUNT_HZ
    cycles_per_strategy = 2
    rate_window_seconds = cycle_period * cycles_per_strategy
    slow_sink_delay_seconds = 0.080
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
    assert cycle_period == 2.5
    assert slow_sink_delay_seconds >= 0.075
    assert order_rpc_calls_per_cycle == 2
    assert source_events_per_cycle == 8
    assert source_events_per_scope == 16
    assert expected_source_rows_by_stream_per_scope == {"execution": 8, "cancellation": 8}
    assert expected_event_rows == 80
    assert expected_rpc_count == 20
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
    synthetic_shared_writer_ideal_upper_bound_per_owner_per_second = (
        1 / slow_sink_delay_seconds
    )
    synthetic_overload_ratio_vs_ideal_upper_bound = (
        scheduled_source_events_per_owner_per_second
        / synthetic_shared_writer_ideal_upper_bound_per_owner_per_second
    )
    synthetic_signed_headroom_fraction_of_owner_demand = (
        synthetic_shared_writer_ideal_upper_bound_per_owner_per_second
        - scheduled_source_events_per_owner_per_second
    ) / scheduled_source_events_per_owner_per_second
    assert scheduled_cycles_per_scope_per_second == pytest.approx(0.4)
    assert scheduled_rpc_requests_per_scope_per_second == pytest.approx(0.8)
    assert scheduled_rpc_requests_per_owner_per_second == pytest.approx(4.0)
    assert scheduled_source_events_per_scope_per_second == pytest.approx(3.2)
    assert scheduled_source_events_per_owner_per_second == pytest.approx(16.0)
    assert synthetic_shared_writer_ideal_upper_bound_per_owner_per_second == pytest.approx(
        12.5
    )
    assert scheduled_source_events_per_owner_per_second == pytest.approx(16.0)
    assert synthetic_overload_ratio_vs_ideal_upper_bound == pytest.approx(1.28)
    assert synthetic_signed_headroom_fraction_of_owner_demand == pytest.approx(-0.21875)
    assert scheduled_source_events_per_owner_per_second * 2 == pytest.approx(32.0)

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
        cycle_start_schedule_lags: dict[int, list[float]] = {
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
                cycle_start_schedule_lags[index].append(cycle_started - scheduled)
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
            "relay-workers-close",
            "execution-host-close",
            "account-host-close",
            "store-close",
        ]
        assert owner_close_facts["relay_count"] == owner_close_facts["reader_count"] == 0
        assert owner_close_facts["total_relayed"] == expected_event_rows
        assert owner_close_facts["relay_runtime"]["state"] == "closed"
        assert owner_close_facts["relay_runtime"]["runtime_count"] == _PER_ACCOUNT
        assert owner_close_facts["relay_runtime"]["worker_thread_count"] == _PER_ACCOUNT
        assert owner_close_facts["relay_runtime"]["worker_joined_count"] == _PER_ACCOUNT
        assert owner_close_facts["relay_runtime"]["binding_count"] == 0
        assert owner_close_facts["relay_runtime"]["opened_binding_count"] == _PER_ACCOUNT
        assert owner_close_facts["relay_runtime"]["active_binding_count"] == 0
        assert owner_close_facts["relay_runtime"]["unknown_registration_count"] == 0
        assert owner_close_facts["relay_runtime_binding_count"] == _PER_ACCOUNT
        assert owner_close_facts["relay_runtime_binding_count_after_close"] == 0
        assert owner_close_facts["relay_runtime_active_binding_count"] == 0
        assert owner_close_facts["relay_runtime_count"] == _PER_ACCOUNT
        assert owner_close_facts["relay_runtime"]["cleanup_failures"] == 0
        assert owner_close_facts["relay_runtime"]["unregister_uncertainties"] == 0
        assert len(owner_close_facts["relay_workers_by_scope"]) == _PER_ACCOUNT
        assert all(
            worker["relay_runtime"]["binding_count"] == 0
            and worker["relay_runtime"]["opened_binding_count"] == 1
            and worker["relay_runtime"]["active_binding_count"] == 0
            and worker["relay_runtime"]["unknown_registration_count"] == 0
            and worker["relay_runtime"]["worker_joined"] is True
            and worker["reader_closed"] is True
            and worker["relay_closed"] is True
            and worker["cleanup_errors"] == []
            for worker in owner_close_facts["relay_workers_by_scope"].values()
        )
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
        timing = owner_closed[16]["timing_durations"]
        worker_rows = owner_close_facts["relay_workers_by_scope"]
        poll_rows = [
            worker_rows[str(item["local_index"])] ["timing"]["relay_poll"]
            for item in scope_records
        ]
        sink_rows = [
            worker_rows[str(item["local_index"])]["timing"]["sink_runtime_call"]
            for item in scope_records
        ]
        pump_gap = timing["owner.host_pump_gap:execution_to_account"]
        account_to_execution_pump_gap = timing[
            "owner.host_pump_gap:account_to_execution"
        ]

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
        ordered_delays = sorted(measured_delays)
        measured_delay_p50_ns = (
            ordered_delays[ceil(0.50 * len(ordered_delays)) - 1]
            if ordered_delays
            else None
        )
        measured_delay_p95_ns = (
            ordered_delays[ceil(0.95 * len(ordered_delays)) - 1]
            if ordered_delays
            else None
        )
        measured_delay_min_ns = ordered_delays[0] if ordered_delays else None
        measured_delay_max_ns = ordered_delays[-1] if ordered_delays else None
        rpc_handler_durations_ms = [
            request["durations_ms"]["handler"]
            for request in rpc_report["correlated_requests"]
        ]
        rpc_dequeue_to_handler_durations_ms = [
            request["durations_ms"][
                "dequeue_to_handler_including_auth_decode_validation"
            ]
            for request in rpc_report["correlated_requests"]
        ]
        poll_scope_max_ns = [row["max_ns"] for row in poll_rows]
        sink_scope_max_ns = [row["max_ns"] for row in sink_rows]
        poll_p95_ns = [row["p95_ns"] for row in poll_rows]
        relay_poll_counts_cover_source = all(
            row["count"] >= source_events_per_scope + 1 for row in poll_rows
        ) and sum(row["count"] for row in poll_rows) >= expected_event_rows
        relay_sink_counts_match_source = all(
            row["count"] == source_events_per_scope for row in sink_rows
        )
        owner_host_pump_gaps_observed = (
            pump_gap["count"] > 0 and account_to_execution_pump_gap["count"] > 0
        )
        correlated_order_rpc_count_matches = (
            len(rpc_report["correlated_requests"]) == expected_rpc_count
        )
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
                        "scheduled_source_events_per_owner_per_second": (
                            scheduled_source_events_per_owner_per_second
                        ),
                        "scheduled_source_events_total_per_second": (
                            scheduled_source_events_per_owner_per_second * 2
                        ),
                        "synthetic_shared_writer_ideal_upper_bound_per_owner_per_second": (
                            synthetic_shared_writer_ideal_upper_bound_per_owner_per_second
                        ),
                        "synthetic_overload_ratio_vs_ideal_upper_bound": (
                            synthetic_overload_ratio_vs_ideal_upper_bound
                        ),
                        "synthetic_signed_headroom_fraction_of_owner_demand": (
                            synthetic_signed_headroom_fraction_of_owner_demand
                        ),
                        "capacity_model_note": (
                            "Synthetic overload fixture only: five scopes share one serial "
                            "durable writer. An injected 80ms append delay gives an ideal "
                            "upper bound of 12.5 deliveries/s per owner before SQLite/RPC "
                            "overhead, below the 16 deliveries/s owner demand (32/s across "
                            "two owners). This intentionally creates a finite backlog to test "
                            "owner-pump responsiveness and exact drain; it is not measured "
                            "production capacity. The next pilot must measure shared-writer "
                            "throughput and reconcile full event/cursor totals."
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
                        "cycle_start_schedule_lags_seconds": {
                            str(index): lags
                            for index, lags in cycle_start_schedule_lags.items()
                        },
                        "cycle_schedule_tolerance_ms": 250,
                        "rpc_timeout_ms": _RPC_TIMEOUT_MS,
                        "lease_seconds": _LEASE_SECONDS,
                    },
                    "sink_delay": {
                        "target_ms": slow_sink_delay_seconds * 1000,
                        "measured_count": delay_count.value,
                        "measured_min_ms": (
                            measured_delay_min_ns / 1_000_000
                            if measured_delay_min_ns is not None
                            else None
                        ),
                        "measured_max_ms": (
                            measured_delay_max_ns / 1_000_000
                            if measured_delay_max_ns is not None
                            else None
                        ),
                        "measured_p50_ms": (
                            measured_delay_p50_ns / 1_000_000
                            if measured_delay_p50_ns is not None
                            else None
                        ),
                        "measured_p95_ms": (
                            measured_delay_p95_ns / 1_000_000
                            if measured_delay_p95_ns is not None
                            else None
                        ),
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
                    "owner_host_pump_gap_timing": {
                        "execution_to_account": dict(pump_gap),
                        "account_to_execution": dict(account_to_execution_pump_gap),
                    },
                    "relay_poll_by_scope": {
                        item["scope_ref"]: {
                            "poll": worker_rows[str(item["local_index"])]["timing"][
                                "relay_poll"
                            ],
                            "sink_runtime_call": worker_rows[str(item["local_index"])][
                                "timing"
                            ]["sink_runtime_call"],
                        }
                        for item in scope_records
                    },
                    "correlated_order_rpc_count": len(rpc_report["correlated_requests"]),
                    "correlated_order_rpc_handler_duration_ms": (
                        rpc_handler_durations_ms
                    ),
                    "correlated_order_rpc_dequeue_to_handler_duration_ms": (
                        rpc_dequeue_to_handler_durations_ms
                    ),
                    "owner_pid": owner_record["pid"],
                    "monitor_pid": monitor_record["pid"],
                    "owner_close_facts": _load01_project_owner_close_facts_for_report(
                        owner_close_facts
                    ),
                    "normal_close": True,
                },
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        within_injection_bounds = len(measured_delays) == expected_event_rows and all(
            75_000_000 <= value < _RPC_TIMEOUT_MS * 1_000_000
            for value in measured_delays
        )
        rpc_handlers_within_timeout = all(
            type(duration_ms) in (int, float)
            and duration_ms < _RPC_TIMEOUT_MS
            for duration_ms in rpc_handler_durations_ms
        ) and all(
            type(duration_ms) in (int, float)
            and duration_ms < _RPC_TIMEOUT_MS
            for duration_ms in rpc_dequeue_to_handler_durations_ms
        )
        relay_timings_within_bounds = (
            min(poll_scope_max_ns) >= 75_000_000
            and max(poll_scope_max_ns) < _RPC_TIMEOUT_MS * 1_000_000
            and min(sink_scope_max_ns) >= 75_000_000
            and max(sink_scope_max_ns) < _RPC_TIMEOUT_MS * 1_000_000
            and all(value < _RPC_TIMEOUT_MS * 1_000_000 for value in poll_p95_ns)
        )
        cycle_schedule_within_profile = all(
            0 <= lag < 0.250
            for lags in cycle_start_schedule_lags.values()
            for lag in lags
        ) and all(
            abs(interval - cycle_period) <= 0.250
            for interval in observed_cycle_intervals.values()
        )
        observation_path = tmp_path / "single-owner-slow-sink-observation.json"
        observation = json.loads(observation_path.read_text(encoding="utf-8"))
        observation["timing_gate_observations"] = {
            "injected_delay_samples_within_floor_and_rpc_timeout": within_injection_bounds,
            "correlated_order_rpc_handlers_within_timeout": rpc_handlers_within_timeout,
            "relay_timings_within_fault_floor_and_rpc_timeout": relay_timings_within_bounds,
            "cycle_schedule_within_profile_tolerance": cycle_schedule_within_profile,
            "relay_poll_counts_cover_source_and_terminal": relay_poll_counts_cover_source,
            "relay_sink_counts_match_source_rows": relay_sink_counts_match_source,
            "owner_host_pump_gaps_observed": owner_host_pump_gaps_observed,
            "correlated_order_rpc_count_matches_expected": correlated_order_rpc_count_matches,
            "owner_pump_gap_is_observed_only": True,
        }
        observation_path.write_text(
            json.dumps(observation, sort_keys=True, indent=2), encoding="utf-8"
        )
        assert within_injection_bounds, (
            "slow sink timing sample count/floor/RPC timeout check failed: "
            f"min_ns={measured_delay_min_ns}, max_ns={measured_delay_max_ns}, "
            f"p50_ns={measured_delay_p50_ns}, p95_ns={measured_delay_p95_ns}"
        )
        assert rpc_handlers_within_timeout, (
            "correlated order RPC handler timing was absent or exceeded the 5s timeout: "
            f"handler_ms={rpc_handler_durations_ms}, "
            f"dequeue_to_handler_ms={rpc_dequeue_to_handler_durations_ms}"
        )
        assert relay_timings_within_bounds, (
            "relay timing exceeded the injected-delay floor or 5s RPC timeout: "
            f"poll_scope_max_ns={poll_scope_max_ns}, "
            f"poll_scope_p95_ns={poll_p95_ns}, sink_scope_max_ns={sink_scope_max_ns}"
        )
        assert cycle_schedule_within_profile, (
            "synthetic source cycles fell outside the 250ms profile scheduling tolerance: "
            f"cycle_start_schedule_lags={cycle_start_schedule_lags}, "
            f"cycle_intervals={observed_cycle_intervals}, expected_period={cycle_period}"
        )
        assert relay_poll_counts_cover_source, (
            "relay polls did not cover the expected source and terminal snapshots: "
            f"counts={[row['count'] for row in poll_rows]}, "
            f"expected_source_events_per_scope={source_events_per_scope}"
        )
        assert relay_sink_counts_match_source, (
            "relay sink-call counts did not match the source event rows: "
            f"counts={[row['count'] for row in sink_rows]}, "
            f"expected_per_scope={source_events_per_scope}"
        )
        assert owner_host_pump_gaps_observed, (
            "owner host-pump gap timing counters were not observed: "
            f"execution_to_account={pump_gap}, "
            f"account_to_execution={account_to_execution_pump_gap}"
        )
        assert correlated_order_rpc_count_matches, (
            "correlated order RPC timing count did not match the expected requests: "
            f"observed={len(rpc_report['correlated_requests'])}, expected={expected_rpc_count}"
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


def test_load01_n3_strategy_config_binds_each_assigned_account_route() -> None:
    account_services = {
        index: {
            "service_id": f"load01-test-account-{index}",
            "instance_id": f"load01-test-account-owner-{index}",
            "scope_ref": f"load01-test-account-scope-{index}",
            "endpoint": f"tcp://127.0.0.1:{31100 + index}",
            "security_ref": f"load01-test-account-security-{index}",
        }
        for index in (0, 1)
    }
    execution_services = {
        index: {
            "service_id": f"load01-test-execution-{index}",
            "instance_id": f"load01-test-execution-owner-{index}",
            "order_scope_ref": f"load01-test-orders-scope-{index}",
            "query_scope_ref": f"load01-test-query-scope-{index}",
            "endpoint": f"tcp://127.0.0.1:{31200 + index}",
            "security_ref": f"load01-test-execution-security-{index}",
        }
        for index in (0, 1)
    }

    raw = _strategy_config(
        0,
        account_services,
        execution_services,
        "tcp://127.0.0.1:31300",
        "tcp://127.0.0.1:31301",
        "tcp://127.0.0.1:31302",
        profile_id="N3",
    )
    parsed = parse_client_config(raw)
    port_by_mount = {
        "store.market": "service.market_source",
        "store.account": "service.account_read",
        "broker.orders": "service.order_entry",
        "broker.order_query": "service.order_query",
        "runtime.telemetry": "service.telemetry_ingress",
    }
    operations_by_port = {
        "service.market_source": _MARKET_OPS,
        "service.account_read": _ACCOUNT_OPS,
        "service.order_entry": _ORDER_OPS,
        "service.order_query": _QUERY_OPS,
        "service.telemetry_ingress": _TELEMETRY_OPS,
    }
    capabilities_by_service: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for client in raw["clients"].values():
        for binding in client["settings"]["bindings"]:
            port = port_by_mount[binding["mount"]]
            capability = {
                "instance_id": binding["instance_id"],
                "port": port,
                "operations": sorted(operations_by_port[port]),
            }
            if port == "service.market_source":
                capability["market_wire_version"] = _MARKET_WIRE_VERSION
            service_capabilities = capabilities_by_service.setdefault(
                binding["service_id"], {}
            )
            service_capabilities[(binding["instance_id"], port)] = capability

    class RouteCaptureTransport:
        def __init__(self, endpoint: str, peer: WireIdentity, capabilities) -> None:
            self.endpoint = endpoint
            self.peer = peer
            self.capabilities = tuple(capabilities)
            self.registrations: dict[str, dict[str, Any]] = {}
            self.closed = False

        def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
            del timeout
            operation = request.header.operation
            payload = request.payload
            if operation == "service.discover":
                result = {
                    "protocol_version": 2,
                    "service_id": self.peer.actor_ref,
                    "ready": True,
                    "capabilities": self.capabilities,
                }
            elif operation == "service.register":
                registration_id = payload["registration_key"]
                registration = {
                    "registration_id": registration_id,
                    "service_epoch": "load01-test-epoch-v1",
                    "scope_ref": payload["scope_ref"],
                    "port": payload["port"],
                    "operations": tuple(payload["operations"]),
                }
                self.registrations[registration_id] = registration
                result = {
                    **registration,
                    "policy_version": "load01-test-policy-v1",
                    "requirements_version": "load01-test-requirements-v1",
                    "expires_at": time.time() + 3600.0,
                    "lease_remaining_seconds": 3600.0,
                }
            elif operation == "service.renew":
                registration = self.registrations[payload["registration_id"]]
                result = {
                    **registration,
                    "policy_version": "load01-test-policy-v1",
                    "requirements_version": "load01-test-requirements-v1",
                    "expires_at": time.time() + 3600.0,
                    "lease_remaining_seconds": 3600.0,
                }
            elif operation == "service.unregister":
                result = {
                    "unregistered": True,
                    "registration_id": payload["registration_id"],
                }
            else:
                registration = self.registrations[payload["registration_id"]]
                result = {
                    "endpoint": self.endpoint,
                    "service_id": self.peer.actor_ref,
                    "operation": operation,
                    "port": registration["port"],
                    "scope_ref": registration["scope_ref"],
                    "account_ref": request.header.scope.account_ref,
                    "strategy_ref": request.header.scope.strategy_ref,
                }
            return WireEnvelope(
                replace(
                    request.header,
                    kind=WireMessageKind.REPLY,
                    message_id="reply-" + request.header.message_id,
                    correlation_id=request.header.message_id,
                    identity=self.peer,
                ),
                {"ok": True, "code": "OK", "result": result},
            )

        def close(self) -> None:
            self.closed = True

    scopes = {
        _MARKET_SCOPE_REF: WireScope(
            _DOMAIN,
            account_ref=_ACCOUNT_REFS[0],
            strategy_ref=_strategy_ref(0),
        )
    }
    for account_index in (0, 1):
        route_scope = WireScope(
            _DOMAIN,
            account_ref=_ACCOUNT_REFS[account_index],
            strategy_ref=_strategy_ref(0),
        )
        scopes[account_services[account_index]["scope_ref"]] = route_scope
        scopes[execution_services[account_index]["order_scope_ref"]] = route_scope
        scopes[execution_services[account_index]["query_scope_ref"]] = route_scope
        scopes[_strategy_telemetry_scope_ref("N3", 0, account_index)] = route_scope

    transports: dict[tuple[str, str], RouteCaptureTransport] = {}

    def peer_for(service_id: str) -> WireIdentity:
        return WireIdentity(
            WireRole.SERVICE,
            f"load01-test-peer-{service_id}",
            service_id,
        )

    def transport_factory(profile, peer: WireIdentity) -> RouteCaptureTransport:
        endpoint = profile["endpoints"]["rpc_connect"]
        transport = RouteCaptureTransport(
            endpoint,
            peer,
            capabilities_by_service[peer.actor_ref].values(),
        )
        transports[(peer.actor_ref, endpoint)] = transport
        return transport

    runtime = ServiceClientRuntime(
        parsed,
        identity=WireIdentity(
            WireRole.STRATEGY_CLIENT,
            _principal_ref(0),
            _strategy_ref(0),
        ),
        scope_resolver=scopes.__getitem__,
        peer_resolver=peer_for,
        transport_factory=transport_factory,
        client_session_id="load01-n3-route-test",
        run_id="load01-n3-route-test-run",
    )
    try:
        summary = runtime.start()
        assert len(summary.bindings) == 9
        market_view = runtime.market_binding_view(
            "store.market", required_operations=frozenset({"market.snapshot"})
        )
        assert market_view.market_endpoint == "tcp://127.0.0.1:31301"
        with pytest.raises(ServiceRuntimeError, match="AMBIGUOUS_OR_MISSING_ROUTE"):
            runtime.call("broker.orders", "orders.submit", {"intent_id": "ambiguous"})

        for account_index in (0, 1):
            account_ref = _ACCOUNT_REFS[account_index]
            service = execution_services[account_index]
            for operation, scope_ref, port in (
                ("orders.submit", service["order_scope_ref"], "service.order_entry"),
                ("orders.cancel", service["order_scope_ref"], "service.order_entry"),
                ("orders.query", service["query_scope_ref"], "service.order_query"),
            ):
                routed = runtime.call(
                    "broker.order_query" if operation == "orders.query" else "broker.orders",
                    operation,
                    {"route_probe": operation},
                    account_ref=account_ref,
                    scope_ref=scope_ref,
                )
                assert routed == {
                    "endpoint": service["endpoint"],
                    "service_id": service["service_id"],
                    "operation": operation,
                    "port": port,
                    "scope_ref": scope_ref,
                    "account_ref": account_ref,
                    "strategy_ref": _strategy_ref(0),
                }
    finally:
        runtime.close()

    assert runtime.cleanup_failures == 0
    assert all(transport.closed for transport in transports.values())


@_load01_unit_marker
def test_load01_n4_client_facade_sqlite_fill_cancel_query_binding(tmp_path: Path) -> None:
    """Exercise N4 key selection through the client, real execution facade and journal.

    Only the runtime transport leaf is synthetic: the SDK route selector, SDK
    execution client, execution service, provider observation and SQLite
    journal are the actual implementations. No service Host or network socket
    is started by this focused node.
    """

    profile = load01_profile("N4")
    account_index = 1
    strategy_index = profile.strategy_indices_by_account[account_index][0]
    account_ref = _ACCOUNT_REFS[account_index]
    strategy_ref = _strategy_ref(strategy_index)
    assert profile.accounts_for_strategy(strategy_index) == (account_index,)
    topic_indices = tuple(
        profile.order_topic_index(strategy_index, cycle) for cycle in range(1, 5)
    )
    assert topic_indices == (2, 3, 4, 5)
    market_keys = market_keys_for_profile("N4")
    selected_keys = tuple(market_keys[index] for index in topic_indices)
    assert tuple(key.instrument.instrument_id for key in selected_keys) == (
        "SYNTH-X",
        "SYNTH-X",
        "SYNTH-X",
        "load01-instrument-05",
    )
    assert len({key.instrument.instrument_id for key in selected_keys}) == 2

    order_scope_ref = "load01-n4-client-test-orders-v1"
    query_scope_ref = "load01-n4-client-test-query-v1"
    service_id = f"{_EXEC_SERVICE_PREFIX}{account_index:02d}"
    instance_id = f"{_EXEC_INSTANCE_PREFIX}{account_index:02d}"
    transport_ref = "load01-n4-client-test-execution-v1"
    security_ref = "load01-n4-client-test-security-v1"
    scope_template = WireScope(
        _DOMAIN,
        account_ref=account_ref,
        strategy_ref=strategy_ref,
    )
    execution_scope = ExecutionScope(
        "synthetic", "paper", account_ref, strategy_ref, _EXECUTION_DAY
    )
    identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        _principal_ref(strategy_index),
        strategy_ref,
    )
    peer = WireIdentity(WireRole.SERVICE, _EXEC_SERVICE_PRINCIPAL, service_id)
    provider = _SyntheticOrderProvider()
    store = SqliteExecutionStore(tmp_path / "load01-n4-order-entry.sqlite")
    provider.attach_store(store)
    entry = ExecutionOrderEntryService(
        store,
        {scope_template: execution_scope},
        provider,
        allow_unprotected=True,
    )

    class ExecutionServiceTransport:
        """Fake the wire leaf while dispatching into the actual execution service."""

        def __init__(self) -> None:
            self.registrations: dict[str, dict[str, Any]] = {}
            self.business_routes: list[dict[str, str]] = []
            self.closed = False

        def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
            assert timeout > 0
            operation = request.header.operation
            payload = request.payload
            if operation == "service.discover":
                result: dict[str, Any] = {
                    "protocol_version": 2,
                    "service_id": service_id,
                    "ready": True,
                    "capabilities": [
                        {
                            "instance_id": instance_id,
                            "port": "service.order_entry",
                            "operations": sorted(_ORDER_OPS),
                        },
                        {
                            "instance_id": instance_id,
                            "port": "service.order_query",
                            "operations": sorted(_QUERY_OPS),
                        },
                    ],
                }
            elif operation == "service.register":
                registration_id = "load01-n4-registration-" + payload["port"]
                registration = {
                    "registration_id": registration_id,
                    "service_epoch": "load01-n4-test-epoch-v1",
                    "scope_ref": payload["scope_ref"],
                    "port": payload["port"],
                    "operations": tuple(payload["operations"]),
                }
                self.registrations[registration_id] = registration
                result = {
                    "registration_id": registration_id,
                    "service_epoch": registration["service_epoch"],
                    "policy_version": "load01-n4-test-policy-v1",
                    "requirements_version": "load01-n4-test-requirements-v1",
                    "operations": list(payload["operations"]),
                    "expires_at": time.time() + 120.0,
                    "lease_remaining_seconds": 120.0,
                }
            elif operation == "service.renew":
                registration = self.registrations[payload["registration_id"]]
                result = {
                    "registration_id": registration["registration_id"],
                    "service_epoch": registration["service_epoch"],
                    "policy_version": "load01-n4-test-policy-v1",
                    "requirements_version": "load01-n4-test-requirements-v1",
                    "operations": list(registration["operations"]),
                    "expires_at": time.time() + 120.0,
                    "lease_remaining_seconds": 120.0,
                }
            elif operation == "service.unregister":
                registration_id = payload["registration_id"]
                result = {
                    "unregistered": self.registrations.pop(registration_id, None) is not None,
                    "registration_id": registration_id,
                }
            else:
                registration = self.registrations[payload["registration_id"]]
                assert operation in registration["operations"]
                expected_scope_ref = (
                    query_scope_ref if operation == "orders.query" else order_scope_ref
                )
                assert registration["scope_ref"] == expected_scope_ref
                assert request.header.scope.account_ref == account_ref
                assert request.header.scope.strategy_ref == strategy_ref
                self.business_routes.append(
                    {
                        "operation": operation,
                        "scope_ref": registration["scope_ref"],
                        "account_ref": request.header.scope.account_ref,
                        "strategy_ref": request.header.scope.strategy_ref,
                    }
                )
                result = entry.handle(
                    operation,
                    principal_ref=request.header.identity.principal_ref,
                    identity=request.header.identity,
                    scope=request.header.scope,
                    arguments=payload["arguments"],
                )
            return WireEnvelope(
                replace(
                    request.header,
                    kind=WireMessageKind.REPLY,
                    message_id="reply-" + request.header.message_id,
                    correlation_id=request.header.message_id,
                    identity=peer,
                ),
                {"ok": True, "result": result},
            )

        def close(self) -> None:
            self.closed = True

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
                            "service_id": service_id,
                            "instance_id": instance_id,
                            "scope_ref": order_scope_ref,
                            "account_ref": account_ref,
                            "transport_ref": transport_ref,
                        },
                        {
                            "mount": "broker.order_query",
                            "service_id": service_id,
                            "instance_id": instance_id,
                            "scope_ref": query_scope_ref,
                            "account_ref": account_ref,
                            "transport_ref": transport_ref,
                        },
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
                    "endpoints": {"rpc_connect": "tcp://127.0.0.1:31991"},
                    "security_profile_ref": security_ref,
                    "options": {"request_timeout_ms": _RPC_TIMEOUT_MS},
                }
            },
        },
    }
    parsed_config = parse_client_config(raw_config)
    transports: list[ExecutionServiceTransport] = []
    def peer_for(requested_service: str) -> WireIdentity:
        if requested_service != service_id:
            raise AssertionError("unexpected N4 service route")
        return peer

    def transport_factory(_profile, _peer: WireIdentity) -> ExecutionServiceTransport:
        transport = ExecutionServiceTransport()
        transports.append(transport)
        return transport

    runtime = ServiceClientRuntime(
        parsed_config,
        identity=identity,
        scope_resolver=lambda _scope_ref: scope_template,
        peer_resolver=peer_for,
        transport_factory=transport_factory,
        client_session_id="load01-n4-client-test-session",
        run_id="load01-n4-client-test-run",
    )

    try:
        runtime.start()
        orders = ServiceExecutionClient(
            runtime,
            "broker.orders",
            scope_ref=order_scope_ref,
            query_mount="broker.order_query",
            query_scope_ref=query_scope_ref,
        )
        for cycle, market_key in enumerate(selected_keys, start=1):
            intent_id = f"load01-n4-a01-cycle-{cycle:02d}"
            lifecycle = "fill" if cycle == 1 else "cancel"
            tags = {
                "scenario": "LOAD01",
                "synthetic": "true",
                _N4_MARKET_STREAM_TAG: market_key.stream_id,
                _N4_LIFECYCLE_TAG: lifecycle,
            }
            submitted = orders.submit(
                intent_id=intent_id,
                signal_id=f"signal-{intent_id}",
                instrument=market_key.instrument.instrument_id,
                side="BUY",
                position_effect="OPEN",
                order_type="LIMIT",
                quantity="1",
                price="1",
                metadata_version="load01-synthetic-v1",
                tags=tags,
            )
            expected_submit_state = "FILLED" if lifecycle == "fill" else "ACKED"
            expected_filled_quantity = Decimal("1") if lifecycle == "fill" else Decimal("0")
            expected_average_price = Decimal("1") if lifecycle == "fill" else None
            assert submitted.state == expected_submit_state
            assert submitted.filled_quantity == expected_filled_quantity
            assert submitted.average_price == expected_average_price
            assert submitted.provider_order_id == f"paper-{intent_id}"
            assert submitted.is_terminal is (lifecycle == "fill")

            durable_intent = store.get_intent(intent_id, scope=execution_scope)
            durable_record = store.get(intent_id, scope=execution_scope)
            assert durable_intent is not None and durable_record is not None
            assert durable_intent.scope.account_ref == account_ref
            assert durable_intent.scope.strategy_id == strategy_ref
            assert durable_intent.instrument == market_key.instrument.instrument_id
            assert durable_intent.tags[_N4_MARKET_STREAM_TAG] == market_key.stream_id
            assert durable_intent.tags[_N4_LIFECYCLE_TAG] == lifecycle
            assert durable_intent.fingerprint == durable_record.payload_sha256
            assert durable_record.scope_key == execution_scope.key

            queried = orders.query(intent_id)
            assert queried is not None
            assert queried.state == expected_submit_state
            assert queried.scope_key == execution_scope.key
            assert queried.provider_order_id == submitted.provider_order_id
            assert queried.filled_quantity == expected_filled_quantity
            assert queried.average_price == expected_average_price
            assert queried.is_terminal is (lifecycle == "fill")

            if lifecycle == "fill":
                assert provider.cancelled == 0
                assert durable_record.state is ExecutionState.FILLED
                assert durable_record.payload_sha256 == durable_intent.fingerprint
                with pytest.raises(ServiceExecutionError) as cancel_error:
                    orders.cancel(
                        cancel_id=f"cancel-filled-{intent_id}",
                        target_intent_id=intent_id,
                        provider_order_id=submitted.provider_order_id,
                        metadata_version="load01-synthetic-v1",
                        tags={"scenario": "LOAD01", "synthetic": "true"},
                    )
                assert cancel_error.value.code == "INVALID_REQUEST"
                assert provider.cancelled == 0
                still_filled = orders.query(intent_id)
                assert still_filled is not None
                assert still_filled.state == "FILLED"
                assert still_filled.scope_key == execution_scope.key
                assert still_filled.provider_order_id == submitted.provider_order_id
                assert still_filled.filled_quantity == Decimal("1")
                assert still_filled.average_price == Decimal("1")
                assert still_filled.is_terminal is True
                durable_after_cancel = store.get(intent_id, scope=execution_scope)
                assert durable_after_cancel is not None
                assert durable_after_cancel.state is ExecutionState.FILLED
                assert durable_after_cancel.payload_sha256 == durable_intent.fingerprint
                continue

            cancellation = orders.cancel(
                cancel_id=f"cancel-{intent_id}",
                target_intent_id=intent_id,
                provider_order_id=submitted.provider_order_id,
                metadata_version="load01-synthetic-v1",
                tags={"scenario": "LOAD01", "synthetic": "true"},
            )
            assert cancellation.state == "CANCELLED"
            cancelled_query = orders.query(intent_id)
            assert cancelled_query is not None
            assert cancelled_query.state == "CANCELLED"
            assert cancelled_query.scope_key == execution_scope.key
            assert cancelled_query.provider_order_id == submitted.provider_order_id
            assert cancelled_query.filled_quantity == Decimal("0")
            assert cancelled_query.average_price is None
            assert cancelled_query.is_terminal is True
            durable_record = store.get(intent_id, scope=execution_scope)
            assert durable_record is not None
            assert durable_record.state is ExecutionState.CANCELLED
            assert durable_record.payload_sha256 == durable_intent.fingerprint

        assert provider.submitted == 4
        assert provider.filled == 1
        assert provider.cancelled == 3
        assert not provider._n4_active
        assert len(provider._n4_terminal_fills) == 1
        assert len(transports) == 1
        assert transports[0].business_routes == [
            {
                "operation": operation,
                "scope_ref": query_scope_ref if operation == "orders.query" else order_scope_ref,
                "account_ref": account_ref,
                "strategy_ref": strategy_ref,
            }
            for operation in (
                "orders.submit",
                "orders.query",
                "orders.cancel",
                "orders.query",
                "orders.submit",
                "orders.query",
                "orders.cancel",
                "orders.query",
                "orders.submit",
                "orders.query",
                "orders.cancel",
                "orders.query",
                "orders.submit",
                "orders.query",
                "orders.cancel",
                "orders.query",
            )
        ]
        facts_by_key = {
            (fact["account_ref"], fact["instrument_id"], fact["market_stream_id"]): fact
            for fact in provider.facts_snapshot()["market_facts"]
        }
        expected_fact_keys = {
            (account_ref, key.instrument.instrument_id, key.stream_id)
            for key in selected_keys
        }
        assert set(facts_by_key) == expected_fact_keys
        for cycle, market_key in enumerate(selected_keys, start=1):
            facts = facts_by_key[(account_ref, market_key.instrument.instrument_id, market_key.stream_id)]
            assert facts["submit_calls"] == 1
            assert facts["intent_readbacks"] == 1
            assert facts["cumulative_fill_observations"] == (1 if cycle == 1 else 0)
            assert facts["acknowledged_orders"] == (0 if cycle == 1 else 1)
            assert facts["cancel_calls"] == (0 if cycle == 1 else 1)
        by_instrument: dict[str, Counter[str]] = {}
        for fact in facts_by_key.values():
            totals = by_instrument.setdefault(fact["instrument_id"], Counter())
            for name in (
                "submit_calls",
                "intent_readbacks",
                "cumulative_fill_observations",
                "acknowledged_orders",
                "cancel_calls",
            ):
                totals[name] += fact[name]
        assert by_instrument == {
            "SYNTH-X": Counter(
                {
                    "submit_calls": 3,
                    "intent_readbacks": 3,
                    "cumulative_fill_observations": 1,
                    "acknowledged_orders": 2,
                    "cancel_calls": 2,
                }
            ),
            "load01-instrument-05": Counter(
                {
                    "submit_calls": 1,
                    "intent_readbacks": 1,
                    "cumulative_fill_observations": 0,
                    "acknowledged_orders": 1,
                    "cancel_calls": 1,
                }
            ),
        }
    finally:
        if runtime.state == "registered":
            runtime.close()
        store.close()

    assert runtime.cleanup_failures == 0
    assert all(transport.closed for transport in transports)


def test_load01_ten_strategy_twenty_topic_startup_wave_stays_healthy_and_closes(
    tmp_path: Path, monkeypatch
) -> None:
    """Run ten strategy processes through fair startup waves and normal close."""

    if os.name != "nt":
        pytest.skip("LOAD01 focused startup-wave check uses the Windows spawn baseline")

    assert _RPC_TIMEOUT_MS == 5_000
    assert _LEASE_SECONDS == 30
    assert _STARTUP_STRATEGY_SLOTS == 10
    assert _ACCOUNTS == 2
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
    profile_relative = Path("tests/unit/_load01_profiles.py").as_posix()
    assert _sha256(source_root / profile_relative) == _N6_PROFILE_OVERLAY_SHA256
    manifest_path = _write_source_run_manifest(
        source_root,
        manifest_work,
        allowed_overlays={
            runner_relative: runner_overlay_sha256,
            "tests/unit/test_load01_runner_metrics.py": _LOAD01_RUNNER_METRICS_TEST_OVERLAY_SHA256,
            "tests/unit/_load01_resource_diagnostics.py": _LOAD01_RESOURCE_DIAGNOSTICS_OVERLAY_SHA256,
            "tests/unit/test_load01_resource_diagnostics.py": _LOAD01_RESOURCE_DIAGNOSTICS_TEST_OVERLAY_SHA256,
            "tests/unit/_load01_group_topology.py": _LOAD01_GROUP_TOPOLOGY_OVERLAY_SHA256,
            profile_relative: _N6_PROFILE_OVERLAY_SHA256,
            "tests/unit/test_load01_profiles.py": _N6_PROFILE_TEST_OVERLAY_SHA256,
        },
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
    strategy_indices = tuple(index for wave in startup_waves for index in wave)
    assert sorted(strategy_indices) == list(range(_STRATEGIES))
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

        strategy_ready = {}
        for wave_number, wave_indices in enumerate(startup_waves):
            if wave_number:
                # Every strategy in the preceding wave has crossed its start
                # barrier and reported readiness before the next wave begins.
                wave_start_event.clear()
            wave_records = {}
            for index in wave_indices:
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
                wave_records[index] = record
                all_records[record["name"]] = record

            loaded = _collect_expected(
                wave_records,
                "loaded",
                90.0,
                f"startup wave {wave_number + 1} process import",
                progress,
            )
            assert len({message[2] for message in loaded.values()}) == len(wave_indices)
            assert all(
                message[1] == index
                and message[2] == wave_records[index]["pid"]
                and wave_records[index]["process"].is_alive()
                for index, message in loaded.items()
            )
            wave_start_event.set()
            wave_ready = _collect_expected(
                wave_records,
                "ready",
                300.0,
                f"startup wave {wave_number + 1} readiness",
                progress,
            )
            assert len({message[2] for message in wave_ready.values()}) == len(wave_indices)
            for index, record in wave_records.items():
                message = wave_ready[index]
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
            strategy_ready.update(wave_ready)

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
            "source_payload_bytes_by_topic",
            "first_publish_ns",
            "last_publish_ns",
            "hub_readiness",
            "source_publishing_stopped",
            "hub_watermarks",
            "watermark_captured_monotonic_ns",
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
        assert final_status["source_payload_bytes_by_topic"] == {}
        assert final_status["source_publishing_stopped"] is True
        assert set(final_status["hub_watermarks"]) == {key.stream_id for key in market_keys}
        for watermark in final_status["hub_watermarks"].values():
            assert set(watermark) == {"epoch", "sequence"}
            assert type(watermark["epoch"]) is str and watermark["epoch"]
            assert type(watermark["sequence"]) is int and watermark["sequence"] == 0
        captured_ns = final_status["watermark_captured_monotonic_ns"]
        assert type(captured_ns) is int and captured_ns > 0
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
