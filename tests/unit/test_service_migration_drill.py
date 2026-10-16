"""Controlled MIG03 migration drill over real spawned processes (iteration 10, incr-B).

This node exercises the formal plan clause at iteration-plan L784 and its
section 11.3 steps (L860-869) end to end with desensitized synthetic state:

* freeze the old client and confirm the old writer is invalid with real
  process/route/session facts, including a double-owner negative where a second
  complete execution owner cannot start on the same facts (real OS sidecar
  lock, cross-process);
* migrate orders/attribution/reservations/idempotency/cursors to the new epoch
  by same-store new-epoch takeover and per original-ID conservation (the
  service world has no separate legacy store format, so migration is proven by
  exact per-ID equality of the durable facts, not by rewriting identifiers);
* counts stay exactly once per migrated fact: no re-dispatch, no re-import,
  no re-delivery of already acknowledged consumer cursors;
* UNKNOWN stays UNKNOWN (never silently deleted), unattributable journal facts
  stay unallocated (never re-attributed, never delivered);
* rollback clarity: the pre-seal window rolls back to the complete old
  deployment with facts intact; after the first new write the old deployment
  and a marker rollback are refused with REVERSE_MIGRATION_REFUSED;
* a renamed wire scope is denied (no second order partition, no duplicate
  quota), covering the REG04 scope-decision transferred obligation.

Everything runs on synthetic local SQLite fixtures with real spawned
ServiceHost processes, CURVE RPC and only one synthetic provider leaf that
durably counts a dispatch and loses its reply to create UNKNOWN outcomes.
No brokerage adapter, credential, or network endpoint is involved.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import multiprocessing
import os
import socket
import sqlite3
import sys
import traceback
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import zmq
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_execution import ExecutionScope, SqliteExecutionStore
from bt_api_gateway import (
    GatewayCommandKind,
    GatewayEventJournal,
    GatewayEventJournalError,
    GatewayPrincipal,
)
from bt_api_gateway.service_component import GatewayServiceAccountScope
from bt_api_risk import (
    INSTRUMENT_METADATA_DIGEST_TAG,
    AccountScope,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskPolicy,
)

import bt_api_py.service_host as service_host_module
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_execution import ServiceExecutionClient, ServiceExecutionError
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_migration_gate import (
    REFUSAL_REVERSE_MIGRATION_REFUSED,
    MigrationGateError,
    arm_migration_cutover,
    enforce_migration_start,
    marker_path,
    read_migration_marker,
    record_migration_first_write,
    rollback_migration_cutover,
)
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceRegistry
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)

_DOMAIN = "paper:mig03b-drill"
_SERVICE_ID = "mig03b-synthetic-service"
_SERVICE_PRINCIPAL = "mig03b-service-principal"
_STRATEGY = "mig03b-strategy"
_STRATEGY_PRINCIPAL = "mig03b-strategy-principal"
_ACCOUNT = "mig03b-paper-account"
_STRATEGY_IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, _STRATEGY_PRINCIPAL, _STRATEGY)
_SERVICE_IDENTITY = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
_EXEC_INSTANCE = "mig03b-execution-owner"
_GW_INSTANCE = "mig03b-gateway-read"
_COMPETITOR_INSTANCE = "mig03b-competitor-owner"
_SERVICE_SCOPE_REF = "mig03b-service-scope-v1"
_LISTENER_PROFILE = "mig03b-service-listener-v1"
_CLIENT_CONNECT_PROFILE = "mig03b-execution-connect-v1"
_SERVER_SECURITY_REF = "mig03b-server-curve-v1"
_CLIENT_SECURITY_REF = "mig03b-client-curve-v1"
_REGISTRATION_POLICY_REF = "mig03b-registration-policy-v1"
_SERVICE_REQUIREMENTS_REF = "mig03b-service-requirements-v1"
_ORDER_SCOPE_REF = "mig03b-order-scope-v1"
_QUERY_SCOPE_REF = "mig03b-query-scope-v1"
_GATEWAY_SCOPE_REF = "mig03b-gateway-scope-v1"
_INTENT_ID = "mig03b-unknown-order-001"
_NEW_INTENT_ID = "mig03b-unknown-order-002"
_RENAMED_SCOPE_INTENT_ID = "mig03b-renamed-scope-order"
_RUN_ID = "mig03b-stable-run-001"
_JOURNAL_ACCOUNT_SCOPE = "mig03b-journal-account"
_JOURNAL_STRATEGY_SCOPE = "mig03b-journal-strategy"
_JOURNAL_LEGACY_SCOPE = "mig03b-journal-legacy"
_JOURNAL_CLOCK_SECONDS = 1_800_000_000.0
_REGISTRY_LEASE_SECONDS = 60.0
_REGISTRY_CLOCK_INITIAL = 1_800_000_000.0
_METADATA_TIME_NS = 1_800_000_000_000_000_000
_RISK_CLOCK_SECONDS = 1_800_000_000.0
_EXECUTION_SCOPE = ExecutionScope("PAPER", "simulation", _ACCOUNT, _STRATEGY, "20261016")
_WIRE_SCOPE = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
_ACCOUNT_SCOPE = AccountScope("PAPER", _ACCOUNT, "simulation")
_RISK_POLICY = RiskPolicy(
    "mig03b-drill-risk-v1",
    Decimal("150000"),
    10,
    permit_ttl_seconds=3600.0,
    notional_unit="USD",
)


class _DurableTimeoutProvider:
    """Synthetic leaf that durably counts a dispatch, then loses its reply."""

    def __init__(self, journal_path: str) -> None:
        self._db = sqlite3.connect(journal_path, timeout=5.0, isolation_level=None)
        self.cancel_calls = 0
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS provider_dispatches "
            "(sequence INTEGER PRIMARY KEY AUTOINCREMENT, intent_id TEXT NOT NULL)"
        )

    def submit(self, intent: Any) -> None:
        self._db.execute(
            "INSERT INTO provider_dispatches(intent_id) VALUES(?)", (intent.intent_id,)
        )
        raise TimeoutError("synthetic provider timed out after dispatch")

    def cancel(self, intent: Any) -> None:
        self.cancel_calls += 1
        raise AssertionError("synthetic provider cancellation is outside this drill")

    def close(self) -> None:
        self._db.close()


class _RefusingDispatch:
    """A refused second owner must never reach any provider dispatch."""

    def submit(self, _intent: Any) -> None:
        raise AssertionError("a refused second execution owner dispatched an order")

    def cancel(self, _intent: Any) -> None:
        raise AssertionError("a refused second execution owner dispatched a cancellation")


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": _LISTENER_PROFILE,
        "registration_policy_ref": _REGISTRATION_POLICY_REF,
        "service_requirements_ref": _SERVICE_REQUIREMENTS_REF,
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _GW_INSTANCE,
                    "bind_ports": ["service.account_read"],
                    "options": {
                        "role": "account_session_worker",
                        "account_session_ref": "mig03b-session-v1",
                    },
                },
            },
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _EXEC_INSTANCE,
                    "bind_ports": ["service.order_entry", "service.order_query"],
                    "options": {"role": "account_owner"},
                },
            },
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _LISTENER_PROFILE: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SERVER_SECURITY_REF,
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": 65_536,
                    },
                }
            },
        },
    }


def _competitor_service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": _LISTENER_PROFILE,
        "registration_policy_ref": _REGISTRATION_POLICY_REF,
        "service_requirements_ref": _SERVICE_REQUIREMENTS_REF,
        "components": {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _COMPETITOR_INSTANCE,
                    "bind_ports": ["service.order_entry", "service.order_query"],
                    "options": {"role": "account_owner"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _LISTENER_PROFILE: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SERVER_SECURITY_REF,
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": 65_536,
                    },
                }
            },
        },
    }


def _client_config(endpoint: str):
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": _STRATEGY},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "broker.orders",
                                "service_id": _SERVICE_ID,
                                "instance_id": _EXEC_INSTANCE,
                                "scope_ref": _ORDER_SCOPE_REF,
                                "transport_ref": _CLIENT_CONNECT_PROFILE,
                                "account_ref": _ACCOUNT,
                            },
                            {
                                "mount": "broker.order_query",
                                "service_id": _SERVICE_ID,
                                "instance_id": _EXEC_INSTANCE,
                                "scope_ref": _QUERY_SCOPE_REF,
                                "transport_ref": _CLIENT_CONNECT_PROFILE,
                                "account_ref": _ACCOUNT,
                            },
                        ],
                        "options": {"request_timeout_ms": 2000},
                    },
                },
                "gateway": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "store.account",
                                "service_id": _SERVICE_ID,
                                "instance_id": _GW_INSTANCE,
                                "scope_ref": _GATEWAY_SCOPE_REF,
                                "transport_ref": _CLIENT_CONNECT_PROFILE,
                                "account_ref": _ACCOUNT,
                            },
                        ],
                        "options": {"request_timeout_ms": 2000},
                    },
                },
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    _CLIENT_CONNECT_PROFILE: {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": _CLIENT_SECURITY_REF,
                        "options": {
                            "request_timeout_ms": 2000,
                            "high_water_mark": 32,
                            "max_message_bytes": 65_536,
                        },
                    }
                },
            },
        }
    )


def _metadata() -> InstrumentRiskMetadata:
    return InstrumentRiskMetadata(
        instrument="BTC-USDT",
        metadata_version="mig03b-metadata-v1",
        as_of_ns=_METADATA_TIME_NS,
        expires_at_ns=_METADATA_TIME_NS + 60_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("500000"),
        taker_fee_bps=Decimal("0"),
        fixed_fee=Decimal("0"),
        max_slippage_bps=Decimal("0"),
        quantity_unit="BTC",
        valuation_unit="USD",
    )


def _submit_arguments(
    intent_id: str, signal_id: str, *, quantity: str, price: str
) -> dict[str, Any]:
    return {
        "intent_id": intent_id,
        "signal_id": signal_id,
        "instrument": "BTC-USDT",
        "side": "BUY",
        "position_effect": "OPEN",
        "order_type": "LIMIT",
        "quantity": quantity,
        "price": price,
        "time_in_force": "GTC",
        "metadata_version": "mig03b-metadata-v1",
        "tags": {INSTRUMENT_METADATA_DIGEST_TAG: _metadata().digest},
    }


def _risk_rows(path: str, intent_id: str) -> dict[str, tuple[tuple[Any, ...], ...]]:
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        connection.execute("PRAGMA query_only = ON")
        return {
            "reservations": tuple(
                connection.execute(
                    "SELECT intent_id, permit_id, status, reason, notional "
                    "FROM risk_reservations WHERE intent_id=? ORDER BY intent_id",
                    (intent_id,),
                ).fetchall()
            ),
            "dispatch_claims": tuple(
                connection.execute(
                    "SELECT intent_id, permit_id, cause_id FROM risk_dispatch_claims "
                    "WHERE intent_id=? ORDER BY intent_id",
                    (intent_id,),
                ).fetchall()
            ),
            "freezes": tuple(
                connection.execute(
                    "SELECT cause_id, reason, active FROM risk_freezes "
                    "WHERE cause_id=? ORDER BY cause_id",
                    (f"dispatch-inflight:{intent_id}",),
                ).fetchall()
            ),
        }
    finally:
        connection.close()


def _execution_rows(path: str, intent_id: str) -> tuple[tuple[Any, ...], ...]:
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        connection.execute("PRAGMA query_only = ON")
        return tuple(
            connection.execute(
                "SELECT scope_key, intent_id, state, permit_reference, dispatch_attempts, "
                "unknown_reason FROM execution_records WHERE intent_id=? ORDER BY scope_key",
                (intent_id,),
            ).fetchall()
        )
    finally:
        connection.close()


def _provider_count(path: str, intent_id: str) -> int:
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        connection.execute("PRAGMA query_only = ON")
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM provider_dispatches WHERE intent_id=?", (intent_id,)
            ).fetchone()[0]
        )
    finally:
        connection.close()


def _sqlite_rows(path: str) -> dict[str, tuple[tuple[object, ...], ...]]:
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        connection.execute("PRAGMA query_only = ON")
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        result: dict[str, tuple[tuple[object, ...], ...]] = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            # The only interpolated identifier came from sqlite_master and is
            # double-quoted above, including escaped embedded quotes.
            rows = [tuple(row) for row in connection.execute(f"SELECT * FROM {quoted}")]  # noqa: S608
            result[table] = tuple(sorted(rows, key=repr))
        return result
    finally:
        connection.close()


def _journal_projection(path: str) -> dict[str, tuple[tuple[Any, ...], ...]]:
    """Targeted journal facts: events, consumer stream epochs, consumer cursors."""

    connection = sqlite3.connect(path, timeout=5.0)
    try:
        connection.execute("PRAGMA query_only = ON")
        return {
            "events": tuple(
                connection.execute(
                    "SELECT account_scope, strategy_scope, sequence, event_id, kind "
                    "FROM gateway_events ORDER BY account_scope, strategy_scope, sequence"
                ).fetchall()
            ),
            "streams": tuple(
                connection.execute(
                    "SELECT account_scope, strategy_scope, stream_epoch "
                    "FROM gateway_consumer_streams ORDER BY account_scope, strategy_scope"
                ).fetchall()
            ),
            "progress": tuple(
                connection.execute(
                    "SELECT principal_id, account_scope, strategy_scope, stream_epoch, "
                    "offered_cursor, acknowledged_cursor FROM gateway_consumer_progress "
                    "ORDER BY account_scope, strategy_scope, principal_id"
                ).fetchall()
            ),
        }
    finally:
        connection.close()


def _seed_journal(path: str) -> None:
    journal = GatewayEventJournal(path, clock=lambda: _JOURNAL_CLOCK_SECONDS)
    journal.append(
        _JOURNAL_ACCOUNT_SCOPE,
        _JOURNAL_STRATEGY_SCOPE,
        "mig03b-event-1",
        "position_update",
        {"symbol": "SYNTH-USD", "quantity": "2"},
        observed_at=_JOURNAL_CLOCK_SECONDS,
    )
    journal.append(
        _JOURNAL_ACCOUNT_SCOPE,
        _JOURNAL_STRATEGY_SCOPE,
        "mig03b-event-2",
        "position_update",
        {"symbol": "SYNTH-USD", "quantity": "3"},
        observed_at=_JOURNAL_CLOCK_SECONDS,
    )
    # One event attributable only to an unmapped legacy strategy scope: it must
    # survive every migration phase in place, never re-attributed or delivered.
    journal.append(
        _JOURNAL_ACCOUNT_SCOPE,
        _JOURNAL_LEGACY_SCOPE,
        "mig03b-legacy-event-1",
        "position_update",
        {"symbol": "OLD-SYNTH", "quantity": "9"},
        observed_at=_JOURNAL_CLOCK_SECONDS,
    )


def _append_followup_journal_events(path: str) -> None:
    journal = GatewayEventJournal(path, clock=lambda: _JOURNAL_CLOCK_SECONDS)
    journal.append(
        _JOURNAL_ACCOUNT_SCOPE,
        _JOURNAL_STRATEGY_SCOPE,
        "mig03b-event-3",
        "position_update",
        {"symbol": "SYNTH-USD", "quantity": "5"},
        observed_at=_JOURNAL_CLOCK_SECONDS,
    )
    journal.append(
        _JOURNAL_ACCOUNT_SCOPE,
        _JOURNAL_STRATEGY_SCOPE,
        "mig03b-event-4",
        "position_update",
        {"symbol": "SYNTH-USD", "quantity": "6"},
        observed_at=_JOURNAL_CLOCK_SECONDS,
    )


def _gateway_principal() -> GatewayPrincipal:
    return GatewayPrincipal(
        principal_id=_STRATEGY_PRINCIPAL,
        account_scopes=frozenset({_JOURNAL_ACCOUNT_SCOPE}),
        strategy_scopes=frozenset({_JOURNAL_STRATEGY_SCOPE}),
        allowed_kinds=frozenset({GatewayCommandKind.READ}),
    )


def _consumer_ref() -> str:
    """Reimplement the server's documented consumer-ref derivation offline."""

    identity_claim = {
        "role": _STRATEGY_IDENTITY.role.value,
        "principal_ref": _STRATEGY_IDENTITY.principal_ref,
        "actor_ref": _STRATEGY_IDENTITY.actor_ref,
        "delegate_ref": _STRATEGY_IDENTITY.delegate_ref,
    }
    scope_claim = {
        "execution_domain_ref": _WIRE_SCOPE.execution_domain_ref,
        "account_ref": _WIRE_SCOPE.account_ref,
        "strategy_ref": _WIRE_SCOPE.strategy_ref,
    }
    canonical = json.dumps(
        {"identity": identity_claim, "scope": scope_claim},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(canonical).hexdigest()


def _service_authority(
    config: Any,
    keys: dict[str, bytes],
    *,
    execution_instance: str,
    gateway_instance: str | None,
) -> TrustedServiceAuthority:
    grants = [
        ResourceGrant(
            execution_instance,
            "service.order_entry",
            _ORDER_SCOPE_REF,
            _WIRE_SCOPE,
            frozenset({"orders.submit", "orders.cancel"}),
        ),
        ResourceGrant(
            execution_instance,
            "service.order_query",
            _QUERY_SCOPE_REF,
            _WIRE_SCOPE,
            frozenset({"orders.query"}),
        ),
    ]
    if gateway_instance is not None:
        grants.append(
            ResourceGrant(
                gateway_instance,
                "service.account_read",
                _GATEWAY_SCOPE_REF,
                _WIRE_SCOPE,
                frozenset({"account.read_after", "account.ack", "account.progress"}),
            )
        )
    return TrustedServiceAuthority(
        identity=_SERVICE_IDENTITY,
        scope=WireScope(_DOMAIN),
        scope_ref=_SERVICE_SCOPE_REF,
        scope_version="mig03b-service-scope-v1",
        security_ref=_SERVER_SECURITY_REF,
        security_version="curve-key-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="mig03b-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="mig03b-requirements-v1",
        curve_public_key=keys["server_public"],
        curve_secret_key=keys["server_secret"],
        principals={keys["strategy_public"]: _STRATEGY_PRINCIPAL},
        grants={
            _STRATEGY_PRINCIPAL: (
                PrincipalGrant(_STRATEGY_IDENTITY, tuple(grants)),
            )
        },
    )


def _safe_exception_facts(exc: BaseException, phase: str, host: Any) -> dict[str, Any]:
    """Project bounded error type/code/frame facts without exception messages."""

    chain = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < 3:
        seen.add(id(current))
        frames = traceback.extract_tb(current.__traceback__)
        chain.append(
            {
                "type": type(current).__name__,
                "code": getattr(current, "code", None)
                if type(getattr(current, "code", None)) is str
                and len(getattr(current, "code", "")) <= 64
                else None,
                "frames": tuple(
                    {
                        "file": Path(frame.filename).name,
                        "function": frame.name,
                        "line": frame.lineno,
                    }
                    for frame in frames[-5:]
                ),
            }
        )
        next_error = current.__cause__
        if next_error is None and not current.__suppress_context__:
            next_error = current.__context__
        current = next_error
    start_failure = getattr(host, "_start_failure", None) if host is not None else None
    if type(start_failure) is not str or len(start_failure) > 64:
        start_failure = None
    return {
        "phase": phase,
        "host_state": getattr(host, "state", None) if host is not None else None,
        "host_start_failure": start_failure,
        "exception_chain": tuple(chain),
    }


def _host_process(
    control,
    raw_config: dict[str, Any],
    keys: dict[str, bytes],
    declared_epoch: str | None,
    registry_path: str,
    execution_path: str,
    risk_path: str,
    provider_journal_path: str,
    gateway_path: str,
) -> None:
    """Run one real ServiceHost with only its registry clock controllable."""

    original_registry_type = service_host_module.ServiceRegistry
    host = None
    provider = None
    store = None
    gate = None
    registry_replaced = False
    phase = "source_origin"
    try:
        source_origins = _assert_fixed_source_origins()
        phase = "service_configuration"
        config = parse_service_config(raw_config)
        server_clock = [_REGISTRY_CLOCK_INITIAL]
        service_host_module.ServiceRegistry = functools.partial(
            ServiceRegistry,
            lease_seconds=_REGISTRY_LEASE_SECONDS,
            clock=lambda: server_clock[0],
        )
        registry_replaced = True
        provider = _DurableTimeoutProvider(provider_journal_path)
        store = SqliteExecutionStore(execution_path)
        gate = DurableRiskGate(
            risk_path,
            _RISK_POLICY,
            clock=lambda: _RISK_CLOCK_SECONDS,
        )
        mapper = InstrumentRiskAdmissionMapper(
            _ACCOUNT_SCOPE,
            InstrumentRiskRegistry((_metadata(),)),
            clock_ns=lambda: _METADATA_TIME_NS + 1,
        )
        admission = DurableRiskAdmissionAdapter(gate, mapper)

        def resource_provider(component, _instance_id, _options, _scope):
            if component == "execution":
                return ComponentResources.borrowed(
                    {
                        "store": store,
                        "targets": {_WIRE_SCOPE: _EXECUTION_SCOPE},
                        "dispatch": provider,
                        "admission": {"order_gate": admission, "cancel_gate": None},
                    },
                    ready=lambda: True,
                )
            if component == "gateway":
                journal = GatewayEventJournal(
                    gateway_path, clock=lambda: _JOURNAL_CLOCK_SECONDS
                )
                account = GatewayServiceAccountScope(
                    account_ref=_ACCOUNT,
                    execution_domain_ref=_DOMAIN,
                    journal_account_scope=_JOURNAL_ACCOUNT_SCOPE,
                    strategy_scope_map={_STRATEGY: _JOURNAL_STRATEGY_SCOPE},
                )
                principal = GatewayPrincipal(
                    principal_id=_STRATEGY_PRINCIPAL,
                    account_scopes=frozenset({_JOURNAL_ACCOUNT_SCOPE}),
                    strategy_scopes=frozenset({_JOURNAL_STRATEGY_SCOPE}),
                    allowed_kinds=frozenset({GatewayCommandKind.READ}),
                )
                return ComponentResources.borrowed(
                    {
                        "journal": journal,
                        "accounts": {_ACCOUNT: account},
                        "principals": {_STRATEGY_PRINCIPAL: principal},
                    },
                    ready=lambda: True,
                )
            raise AssertionError("unexpected MIG03b drill component")

        authority = _service_authority(
            config, keys, execution_instance=_EXEC_INSTANCE, gateway_instance=_GW_INSTANCE
        )

        def capture_facts() -> dict[str, Any]:
            snapshot = gate.snapshot(_ACCOUNT_SCOPE)
            return {
                "execution_rows_intent_1": _execution_rows(execution_path, _INTENT_ID),
                "execution_rows_intent_2": _execution_rows(execution_path, _NEW_INTENT_ID),
                "risk_rows_intent_1": _risk_rows(risk_path, _INTENT_ID),
                "risk_rows_intent_2": _risk_rows(risk_path, _NEW_INTENT_ID),
                "provider_dispatch_counts": {
                    "intent_1": _provider_count(provider_journal_path, _INTENT_ID),
                    "intent_2": _provider_count(provider_journal_path, _NEW_INTENT_ID),
                },
                "provider_cancel_count": provider.cancel_calls,
                "risk_snapshot": {
                    "increase_count": snapshot["increase_count"],
                    "increase_notional": snapshot["increase_notional"],
                    "active_freeze_reasons": tuple(snapshot["active_freeze_reasons"]),
                },
                "active_registration_ids": {
                    _EXEC_INSTANCE: tuple(
                        sorted(
                            host._registry.active_registration_ids(
                                instance_id=_EXEC_INSTANCE
                            )
                        )
                    ),
                    _GW_INSTANCE: tuple(
                        sorted(
                            host._registry.active_registration_ids(instance_id=_GW_INSTANCE)
                        )
                    ),
                },
                "service_epoch": host._registry.service_epoch,
            }

        phase = "host_start"
        host = ServiceHost(
            config,
            authority,
            registry_path,
            resource_provider,
            migration_deployment_epoch=declared_epoch,
        )
        host.start()
        control.send(
            ("ready", host.endpoint, host._registry.service_epoch, source_origins, declared_epoch)
        )
        phase = "serve"
        while True:
            host.serve_once(timeout_ms=20)
            if not control.poll():
                continue
            command = control.recv()
            if command == ("facts",):
                phase = "capture_facts"
                control.send(("facts", capture_facts()))
                phase = "serve"
                continue
            if command == ("close",):
                phase = "normal_close"
                epoch = host._registry.service_epoch
                host.close()
                gate.close()
                store.close()
                provider.close()
                service_host_module.ServiceRegistry = original_registry_type
                registry_replaced = False
                control.send(("closed", host.state, epoch))
                return
            raise AssertionError("unknown MIG03b host command")
    except BaseException as exc:
        failure = _safe_exception_facts(exc, phase, host)
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        for resource in (gate, store, provider):
            if resource is not None:
                with suppress(BaseException):
                    resource.close()
        if registry_replaced:
            service_host_module.ServiceRegistry = original_registry_type
        with suppress(Exception):
            control.send(("error", failure))
        raise
    finally:
        control.close()


def _refusal_process(
    control,
    raw_config: dict[str, Any],
    keys: dict[str, bytes],
    registry_path: str,
    declared_epoch: str,
) -> None:
    """One old/new deployment attempt against a sealed cutover marker."""

    host = None
    provider_calls: list[str] = []
    phase = "source_origin"
    try:
        _assert_fixed_source_origins()
        config = parse_service_config(raw_config)

        def resource_provider(component, _instance_id, _options, _scope):
            provider_calls.append(component)
            raise AssertionError("a refused deployment must not resolve component resources")

        authority = _service_authority(
            config, keys, execution_instance=_EXEC_INSTANCE, gateway_instance=_GW_INSTANCE
        )
        host = ServiceHost(
            config,
            authority,
            registry_path,
            resource_provider,
            migration_deployment_epoch=declared_epoch,
        )
        phase = "host_start"
        try:
            host.start()
        except ServiceHostError as exc:
            control.send(
                ("refused", exc.code, host.state, host._start_failure, tuple(provider_calls))
            )
            return
        control.send(("started", host._registry.service_epoch, tuple(provider_calls)))
    except BaseException as exc:
        failure = _safe_exception_facts(exc, phase, host)
        with suppress(Exception):
            control.send(("error", failure))
        raise
    finally:
        if host is not None and host.state not in {"closed", "failed", "close_failed"}:
            with suppress(BaseException):
                host.close()
        control.close()


def _new_runtime(
    endpoint: str,
    keys: dict[str, bytes],
    *,
    session_id: str,
) -> ServiceClientRuntime:
    config = _client_config(endpoint)
    return ServiceClientRuntime(
        config,
        identity=_STRATEGY_IDENTITY,
        scope_resolver=lambda _scope_ref: _WIRE_SCOPE,
        peer_resolver=lambda _service_id: _SERVICE_IDENTITY,
        transport_factory=ZmqTransportFactory(
            {
                _CLIENT_SECURITY_REF: ClientCurveKeys(
                    keys["server_public"],
                    keys["strategy_public"],
                    keys["strategy_secret"],
                )
            }
        ),
        requested_operations={
            "broker.orders": frozenset({"orders.submit", "orders.cancel"}),
            "broker.order_query": frozenset({"orders.query"}),
            "store.account": frozenset({"account.read_after", "account.ack", "account.progress"}),
        },
        client_session_id=session_id,
        run_id=_RUN_ID,
    )


def _execution_client(runtime: ServiceClientRuntime) -> ServiceExecutionClient:
    return ServiceExecutionClient(
        runtime,
        "broker.orders",
        scope_ref=_ORDER_SCOPE_REF,
        query_mount="broker.order_query",
        query_scope_ref=_QUERY_SCOPE_REF,
    )


def _binding_of(runtime: ServiceClientRuntime, mount: str):
    matches = [binding for binding in runtime.bindings if binding.mount == mount]
    assert len(matches) == 1, (mount, tuple(binding.mount for binding in runtime.bindings))
    return matches[0]


def _start_host_process(context, target, args: tuple, *, label: str) -> dict[str, Any]:
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=target, args=(child, *args), name=label)
    record = {"process": process, "pipe": parent, "label": label, "closed": False}
    try:
        process.start()
    except BaseException:
        parent.close()
        child.close()
        process.close()
        record["closed"] = True
        raise
    child.close()
    record["pid"] = process.pid
    return record


def _receive_host_message(record: dict[str, Any], timeout: float = 15.0):
    assert record["pipe"].poll(timeout), f"{record['label']} missed its response bound"
    try:
        message = record["pipe"].recv()
    except EOFError:
        record["child_pipe_eof"] = True
        raise AssertionError(f"{record['label']} closed its control pipe before replying") from None
    if message[0] == "error":
        record["child_failure"] = message[1]
        raise AssertionError((record["label"], "child_error", message[1]))
    if message[0] == "ready" and len(message) == 5:
        record["ready_source_origins"] = message[3]
    return message


def _host_command(record: dict[str, Any], *command):
    process = record["process"]
    assert process.is_alive(), f"{record['label']} exited before its command"
    record["pipe"].send(command)
    return _receive_host_message(record)


def _close_host_normally(record: dict[str, Any]) -> tuple[Any, ...]:
    process = record["process"]
    assert process.is_alive(), f"{record['label']} exited before normal close"
    try:
        record["pipe"].send(("close",))
    except BrokenPipeError as exc:
        record["close_send_broken_pipe"] = True
        process.join(5)
        record["terminal_exit_code"] = process.exitcode
        raise AssertionError(f"{record['label']} control pipe closed before normal close") from exc
    message = _receive_host_message(record)
    assert message[0] == "closed" and message[1] == "closed", message
    process.join(15)
    assert not process.is_alive(), f"{record['label']} did not exit normally"
    assert process.exitcode == 0, f"{record['label']} exited with {process.exitcode}"
    record["normal_exit_code"] = process.exitcode
    record["normal_close_ack"] = True
    record["pipe"].close()
    process.close()
    record["closed"] = True
    return message


def _bounded_reap_owned_host(
    record: dict[str, Any], forced: list[str], failures: list[str]
) -> None:
    """Bound cleanup for this test's own spawned processes only."""

    if record["closed"]:
        return
    process, pipe, label = record["process"], record["pipe"], record["label"]
    try:
        alive = process.is_alive()
    except Exception as exc:
        failures.append(f"{label}:is_alive:{type(exc).__name__}")
        alive = False
    child_has_reported_failure = record.get("child_failure") is not None
    child_pipe_eof = record.get("child_pipe_eof") is True
    if alive and not child_has_reported_failure and not child_pipe_eof:
        try:
            pipe.send(("close",))
        except BrokenPipeError:
            record["close_send_broken_pipe"] = True
        except Exception as exc:
            failures.append(f"{label}:close_send:{type(exc).__name__}")
    try:
        process.join(5)
    except Exception as exc:
        failures.append(f"{label}:close_join:{type(exc).__name__}")
    try:
        alive = process.is_alive()
    except Exception as exc:
        failures.append(f"{label}:state_after_close:{type(exc).__name__}")
        alive = False
    if alive:
        forced.append(label)
        try:
            process.terminate()
        except Exception as exc:
            failures.append(f"{label}:terminate:{type(exc).__name__}")
        try:
            process.join(5)
        except Exception as exc:
            failures.append(f"{label}:terminate_join:{type(exc).__name__}")
    try:
        alive = process.is_alive()
    except Exception as exc:
        failures.append(f"{label}:state_after_terminate:{type(exc).__name__}")
        alive = False
    if alive:
        try:
            process.kill()
        except Exception as exc:
            failures.append(f"{label}:kill:{type(exc).__name__}")
        try:
            process.join(5)
        except Exception as exc:
            failures.append(f"{label}:kill_join:{type(exc).__name__}")
    try:
        alive = process.is_alive()
    except Exception as exc:
        failures.append(f"{label}:final_state:{type(exc).__name__}")
        alive = False
    try:
        pipe.close()
    except Exception as exc:
        failures.append(f"{label}:pipe_close:{type(exc).__name__}")
    if alive:
        failures.append(f"{label}:still_alive")
        return
    record["terminal_exit_code"] = process.exitcode
    try:
        process.close()
    except Exception as exc:
        failures.append(f"{label}:process_close:{type(exc).__name__}")
    record["closed"] = True


def _host_observation(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": record["label"],
        "pid": record.get("pid"),
        "normal_close_ack": record.get("normal_close_ack", False),
        "normal_exit_code": record.get("normal_exit_code"),
        "terminal_exit_code": record.get("terminal_exit_code"),
        "closed": record["closed"],
    }


def _assert_no_fallback_cleanup(forced: list[str], failures: list[str]) -> None:
    assert not forced and not failures, (tuple(forced), tuple(failures))


def _business_projection(facts: dict[str, Any]) -> tuple[Any, ...]:
    return (
        facts["execution_rows_intent_1"],
        facts["execution_rows_intent_2"],
        facts["risk_rows_intent_1"],
        facts["risk_rows_intent_2"],
        facts["provider_dispatch_counts"],
        facts["provider_cancel_count"],
        facts["risk_snapshot"],
    )


def _registration_ids(runtime: ServiceClientRuntime) -> dict[str, tuple[str, str]]:
    return {
        binding.mount: (binding.registration_id, binding.service_epoch)
        for binding in runtime.bindings
    }


def _assert_fixed_source_origins() -> dict[str, str]:
    source_root = os.environ.get("BTAPI_MIG03B_FIXED_SOURCE_ROOT")
    if source_root is None:
        repository = Path(__file__).resolve().parents[2]
    else:
        assert source_root, "fixed SDK source export root must not be empty"
        repository = Path(source_root).resolve()
    symbols = (
        (ServiceHost, repository / "bt_api_py/service_host.py"),
        (ServiceRegistry, repository / "bt_api_py/service_registry.py"),
        (ServiceClientRuntime, repository / "bt_api_py/service_runtime.py"),
        (ServiceExecutionClient, repository / "bt_api_py/service_execution.py"),
        (
            WireScope,
            repository / "bt_api/bt_api_base/src/bt_api_base/service_protocol.py",
        ),
        (
            DurableRiskAdmissionAdapter,
            repository / "bt_api/bt_api_risk/src/bt_api_risk/order_admission.py",
        ),
        (DurableRiskGate, repository / "bt_api/bt_api_risk/src/bt_api_risk/core/admission.py"),
        (
            SqliteExecutionStore,
            repository / "bt_api/bt_api_execution/src/bt_api_execution/store.py",
        ),
        (
            GatewayEventJournal,
            repository / "bt_api/bt_api_gateway/src/bt_api_gateway/event_journal.py",
        ),
    )
    actual_origins = {}
    for symbol, expected in symbols:
        actual = Path(inspect.getsourcefile(symbol)).resolve()
        assert actual == expected.resolve()
        actual_origins[symbol.__name__] = str(actual)
    return actual_origins


def _direct_request(
    transport,
    *,
    identity: WireIdentity,
    scope: WireScope,
    operation: str,
    binding,
    arguments: dict[str, Any],
) -> Any:
    request = WireEnvelope(
        WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=uuid4().hex,
            correlation_id=None,
            identity=identity,
            scope=scope,
            operation=operation,
            schema_id="sdk.service.call",
            schema_version=1,
            sent_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        ),
        {
            "registration_id": binding.registration_id,
            "service_epoch": binding.service_epoch,
            "instance_id": binding.instance_id,
            "arguments": arguments,
        },
    )
    return transport.request(request, timeout=2.0)


def _attempt_second_execution_owner(
    tmp_path,
    keys: dict[str, bytes],
    execution_path: str,
    risk_path: str,
) -> dict[str, Any]:
    """A second complete execution owner on the same facts must be refused."""

    endpoint = _endpoint()
    config = parse_service_config(_competitor_service_config(endpoint))
    competitor_registry_path = str(tmp_path / "mig03b-competitor-registry.sqlite3")
    history: dict[str, Any] = {
        "provider_calls": 0,
        "code": None,
        "state": None,
        "start_failure": None,
        "registry_created": None,
    }

    def resource_provider(component, _instance_id, _options, _scope):
        if component != "execution":
            raise AssertionError("unexpected competitor component")
        history["provider_calls"] += 1
        store = SqliteExecutionStore(execution_path)
        gate = DurableRiskGate(risk_path, _RISK_POLICY, clock=lambda: _RISK_CLOCK_SECONDS)
        provider = _RefusingDispatch()
        mapper = InstrumentRiskAdmissionMapper(
            _ACCOUNT_SCOPE,
            InstrumentRiskRegistry((_metadata(),)),
            clock_ns=lambda: _METADATA_TIME_NS + 1,
        )
        admission = DurableRiskAdmissionAdapter(gate, mapper)

        def close_resources() -> None:
            gate.close()
            store.close()

        return ComponentResources.owned(
            {
                "store": store,
                "targets": {_WIRE_SCOPE: _EXECUTION_SCOPE},
                "dispatch": provider,
                "admission": {"order_gate": admission, "cancel_gate": None},
            },
            ready=lambda: True,
            close=close_resources,
        )

    authority = _service_authority(
        config, keys, execution_instance=_COMPETITOR_INSTANCE, gateway_instance=None
    )
    host = ServiceHost(config, authority, competitor_registry_path, resource_provider)
    try:
        try:
            host.start()
        except ServiceHostError as exc:
            history["code"] = exc.code
        if host.state == "started":
            with suppress(BaseException):
                host.close()
        history["state"] = host.state
        history["start_failure"] = host._start_failure
        history["registry_created"] = Path(competitor_registry_path).exists()
        return history
    finally:
        if host.state == "close_failed":
            with suppress(BaseException):
                host.close()


def test_two_epoch_controlled_migration_drill_preserves_facts_and_refuses_old_epoch_after_first_new_write(
    tmp_path,
) -> None:
    parent_source_origins = _assert_fixed_source_origins()
    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
        "strategy_secret": strategy_secret,
    }
    config = _service_config(endpoint)
    registry_path = str(tmp_path / "mig03b-registry.sqlite3")
    execution_path = str(tmp_path / "mig03b-execution.sqlite3")
    risk_path = str(tmp_path / "mig03b-risk.sqlite3")
    provider_journal_path = str(tmp_path / "mig03b-provider-journal.sqlite3")
    gateway_path = str(tmp_path / "mig03b-gateway.sqlite3")
    _seed_journal(gateway_path)
    hosts: list[dict[str, Any]] = []
    refusal_records: list[dict[str, Any]] = []
    runtimes: list[ServiceClientRuntime] = []
    forced: list[str] = []
    cleanup_failures: list[str] = []
    observation: dict[str, Any] = {
        "status": "RUNNING",
        "node": "two_epoch_controlled_migration_drill",
        "fixed_source_root": os.environ.get("BTAPI_MIG03B_FIXED_SOURCE_ROOT"),
        "parent_source_origins": parent_source_origins,
        "hosts": [],
        "business_facts": {},
    }
    try:
        # ---- P1: the old epoch serves one full business slice -------------
        host1 = _start_host_process(
            context,
            _host_process,
            (
                config,
                keys,
                None,
                registry_path,
                execution_path,
                risk_path,
                provider_journal_path,
                gateway_path,
            ),
            label="mig03b-host-a",
        )
        hosts.append(host1)
        ready1 = _receive_host_message(host1)
        assert ready1[0] == "ready" and ready1[1] == endpoint, ready1
        assert ready1[3] == parent_source_origins
        assert ready1[4] is None
        epoch1 = ready1[2]
        observation["epoch_old"] = epoch1

        runtime1 = _new_runtime(endpoint, keys, session_id="mig03b-client-session-original")
        runtimes.append(runtime1)
        assert runtime1.start().degraded_count == 0
        runtime1_ids = _registration_ids(runtime1)
        assert set(runtime1_ids) == {"broker.orders", "broker.order_query", "store.account"}
        assert {item[1] for item in runtime1_ids.values()} == {epoch1}

        # Same session, run, scopes and request digest are an idempotent retry.
        runtime_duplicate = _new_runtime(
            endpoint, keys, session_id="mig03b-client-session-original"
        )
        runtimes.append(runtime_duplicate)
        assert runtime_duplicate.start().degraded_count == 0
        assert _registration_ids(runtime_duplicate) == runtime1_ids

        first_order = _execution_client(runtime1).submit(
            **_submit_arguments(_INTENT_ID, "mig03b-signal-001", quantity="2", price="50000")
        )
        assert first_order.state == "UNKNOWN"
        assert first_order.dispatch_attempts == 1
        assert first_order.permit_reference
        queried_first = _execution_client(runtime1).query(_INTENT_ID)
        assert queried_first is not None and queried_first.state == "UNKNOWN"

        first_read = runtime1.call(
            "store.account",
            "account.read_after",
            {"cursor": 0, "limit": 10, "track_delivery": True},
        )
        assert [event["event_id"] for event in first_read["events"]] == [
            "mig03b-event-1",
            "mig03b-event-2",
        ]
        first_progress = first_read["consumer_progress"]
        journal_epoch = first_progress["stream_epoch"]
        assert first_progress["offered_cursor"] == 2
        assert first_progress["acknowledged_cursor"] == 0
        assert journal_epoch != epoch1
        acked = runtime1.call(
            "store.account",
            "account.ack",
            {"stream_epoch": journal_epoch, "cursor": 2},
        )
        assert acked["consumer_progress"] == {
            "stream_epoch": journal_epoch,
            "offered_cursor": 2,
            "acknowledged_cursor": 2,
        }
        observation["cursor_epoch"] = journal_epoch

        facts_old_epoch = _host_command(host1, "facts")[1]
        assert facts_old_epoch["service_epoch"] == epoch1
        assert len(facts_old_epoch["execution_rows_intent_1"]) == 1
        old_row = facts_old_epoch["execution_rows_intent_1"][0]
        assert old_row[1] == _INTENT_ID
        assert old_row[2] == "UNKNOWN"
        assert old_row[3] == first_order.permit_reference
        assert old_row[4] == 1
        assert facts_old_epoch["provider_dispatch_counts"] == {"intent_1": 1, "intent_2": 0}
        assert facts_old_epoch["provider_cancel_count"] == 0
        reservations = facts_old_epoch["risk_rows_intent_1"]["reservations"]
        assert len(reservations) == 1
        reservation = reservations[0]
        assert type(reservation) is tuple and len(reservation) == 5
        assert reservation[:4] == (_INTENT_ID, first_order.permit_reference, "active", None)
        assert type(reservation[4]) is str
        assert Decimal(reservation[4]) == Decimal("100000")
        assert facts_old_epoch["risk_rows_intent_1"]["dispatch_claims"] == (
            (_INTENT_ID, first_order.permit_reference, f"dispatch-inflight:{_INTENT_ID}"),
        )
        assert facts_old_epoch["risk_rows_intent_1"]["freezes"] == (
            (f"dispatch-inflight:{_INTENT_ID}", f"dispatch-inflight:{_INTENT_ID}", 1),
        )
        assert facts_old_epoch["risk_snapshot"]["increase_count"] == 1
        assert facts_old_epoch["risk_snapshot"]["increase_notional"] == Decimal("100000")
        assert (
            f"dispatch-inflight:{_INTENT_ID}"
            in facts_old_epoch["risk_snapshot"]["active_freeze_reasons"]
        )
        active_old = facts_old_epoch["active_registration_ids"]
        assert set(active_old[_EXEC_INSTANCE]) == {
            runtime1_ids["broker.orders"][0],
            runtime1_ids["broker.order_query"][0],
        }
        assert set(active_old[_GW_INSTANCE]) == {runtime1_ids["store.account"][0]}
        observation["business_facts"]["old_epoch_first_slice"] = facts_old_epoch

        # ---- P2: freeze the old client and drain the old writer -----------
        runtime1.close()
        assert runtime1.cleanup_failures == 0
        with pytest.raises(ServiceExecutionError) as not_registered:
            _execution_client(runtime_duplicate).query(_INTENT_ID)
        assert not_registered.value.code == "NOT_REGISTERED"
        facts_after_drain = _host_command(host1, "facts")[1]
        assert facts_after_drain["active_registration_ids"] == {
            _EXEC_INSTANCE: (),
            _GW_INSTANCE: (),
        }
        assert _business_projection(facts_after_drain) == _business_projection(facts_old_epoch)
        observation["business_facts"]["after_old_client_drain"] = facts_after_drain
        assert read_migration_marker(registry_path) is None
        closed1 = _close_host_normally(host1)
        assert closed1[1] == "closed" and closed1[2] == epoch1
        journal_after_old_epoch = _journal_projection(gateway_path)
        mapped_events = tuple(
            row for row in journal_after_old_epoch["events"] if row[1] == _JOURNAL_STRATEGY_SCOPE
        )
        legacy_events = tuple(
            row for row in journal_after_old_epoch["events"] if row[1] == _JOURNAL_LEGACY_SCOPE
        )
        assert [row[3] for row in mapped_events] == ["mig03b-event-1", "mig03b-event-2"]
        assert [row[3] for row in legacy_events] == ["mig03b-legacy-event-1"]
        assert journal_after_old_epoch["streams"] == (
            (_JOURNAL_ACCOUNT_SCOPE, _JOURNAL_STRATEGY_SCOPE, journal_epoch),
        )
        assert journal_after_old_epoch["progress"] == (
            (_STRATEGY_PRINCIPAL, _JOURNAL_ACCOUNT_SCOPE, _JOURNAL_STRATEGY_SCOPE, journal_epoch, 2, 2),
        )

        # ---- P3: the new epoch takes over the same durable facts ----------
        host2 = _start_host_process(
            context,
            _host_process,
            (
                config,
                keys,
                None,
                registry_path,
                execution_path,
                risk_path,
                provider_journal_path,
                gateway_path,
            ),
            label="mig03b-host-b",
        )
        hosts.append(host2)
        ready2 = _receive_host_message(host2)
        assert ready2[0] == "ready" and ready2[1] == endpoint, ready2
        assert ready2[3] == parent_source_origins
        epoch2 = ready2[2]
        assert epoch2 != epoch1
        observation["epoch_new"] = epoch2
        facts_new_owner = _host_command(host2, "facts")[1]
        assert facts_new_owner["service_epoch"] == epoch2
        assert facts_new_owner["active_registration_ids"] == {
            _EXEC_INSTANCE: (),
            _GW_INSTANCE: (),
        }
        assert _business_projection(facts_new_owner) == _business_projection(facts_old_epoch)
        assert _journal_projection(gateway_path) == journal_after_old_epoch
        observation["business_facts"]["new_owner_takeover"] = facts_new_owner

        with pytest.raises(ServiceExecutionError) as stale_epoch:
            _execution_client(runtime_duplicate).query(_INTENT_ID)
        assert stale_epoch.value.code == "STALE_EPOCH"
        assert _business_projection(_host_command(host2, "facts")[1]) == _business_projection(
            facts_old_epoch
        )

        marker = arm_migration_cutover(registry_path, source_epoch=epoch1, new_epoch=epoch2)
        assert marker.status == "COMMITTED"
        armed_bytes = marker_path(registry_path).read_bytes()
        observation["cutover"] = {
            "migration_id": marker.migration_id,
            "source_epoch": epoch1,
            "destination_epoch": epoch2,
        }

        # Double-owner negative: a second complete execution owner on the same
        # store must die on the real cross-process owner lock, before any
        # registry/business side effect and without growing any fact.
        competitor_history = _attempt_second_execution_owner(
            tmp_path, keys, execution_path, risk_path
        )
        assert competitor_history["code"] == "ORDER_OWNER_ALREADY_ACTIVE", competitor_history
        assert competitor_history["state"] == "failed"
        assert competitor_history["start_failure"] == "ORDER_OWNER_ALREADY_ACTIVE"
        assert competitor_history["provider_calls"] == 1
        assert competitor_history["registry_created"] is False
        observation["double_owner_negative"] = competitor_history
        after_double_owner = _host_command(host2, "facts")[1]
        assert after_double_owner["service_epoch"] == epoch2
        assert _business_projection(after_double_owner) == _business_projection(facts_old_epoch)

        # ---- P4: offline per-ID reconciliation and cursor-epoch negatives --
        assert _execution_rows(execution_path, _INTENT_ID) == facts_old_epoch[
            "execution_rows_intent_1"
        ]
        assert _risk_rows(risk_path, _INTENT_ID) == facts_old_epoch["risk_rows_intent_1"]
        assert _provider_count(provider_journal_path, _INTENT_ID) == 1
        offline_journal = GatewayEventJournal(gateway_path, clock=lambda: _JOURNAL_CLOCK_SECONDS)
        wrong_epoch_read = None
        with pytest.raises(GatewayEventJournalError) as wrong_epoch:
            offline_journal.read_for_consumer(
                _gateway_principal(),
                _JOURNAL_ACCOUNT_SCOPE,
                _JOURNAL_STRATEGY_SCOPE,
                _consumer_ref(),
                cursor=2,
                stream_epoch="0" * 32,
            )
        assert wrong_epoch.value.code == "STREAM_EPOCH_MISMATCH"
        with pytest.raises(GatewayEventJournalError) as epoch_required:
            offline_journal.read_for_consumer(
                _gateway_principal(),
                _JOURNAL_ACCOUNT_SCOPE,
                _JOURNAL_STRATEGY_SCOPE,
                _consumer_ref(),
                cursor=2,
            )
        assert epoch_required.value.code == "STREAM_EPOCH_REQUIRED"
        observation["cursor_epoch_negatives"] = [
            wrong_epoch_read,
            "STREAM_EPOCH_MISMATCH",
            "STREAM_EPOCH_REQUIRED",
        ]

        # New epoch + explicit re-registration; the migrated facts are readable
        # and the old registrations are left behind.
        runtime_duplicate.recover()
        new_epoch_ids = _registration_ids(runtime_duplicate)
        assert {item[1] for item in new_epoch_ids.values()} == {epoch2}
        assert all(
            new_epoch_ids[mount][0] != prior[0] for mount, prior in runtime1_ids.items()
        )
        migrated_order = _execution_client(runtime_duplicate).query(_INTENT_ID)
        assert migrated_order is not None
        assert migrated_order.state == "UNKNOWN"
        assert migrated_order.dispatch_attempts == 1
        assert migrated_order.permit_reference == first_order.permit_reference
        facts_after_reregister = _host_command(host2, "facts")[1]
        active_new = facts_after_reregister["active_registration_ids"]
        assert set(active_new[_EXEC_INSTANCE]) == {
            new_epoch_ids["broker.orders"][0],
            new_epoch_ids["broker.order_query"][0],
        }
        assert set(active_new[_GW_INSTANCE]) == {new_epoch_ids["store.account"][0]}

        # ---- P5: idempotency, cursor continuity and renamed-scope denials --
        client2 = _execution_client(runtime_duplicate)
        replay = client2.submit(
            **_submit_arguments(_INTENT_ID, "mig03b-signal-001", quantity="2", price="50000")
        )
        assert replay.state == "UNKNOWN"
        assert replay.permit_reference == first_order.permit_reference
        assert replay.scope_key == first_order.scope_key
        assert replay.dispatch_attempts == 1
        facts_after_replay = _host_command(host2, "facts")[1]
        assert facts_after_replay["provider_dispatch_counts"] == {"intent_1": 1, "intent_2": 0}
        assert _business_projection(facts_after_replay) == _business_projection(facts_old_epoch)

        with pytest.raises(ServiceExecutionError) as import_conflict:
            client2.submit(
                **_submit_arguments(_INTENT_ID, "mig03b-signal-001", quantity="7", price="50000")
            )
        assert import_conflict.value.code == "INTENT_CONFLICT"
        conflicting_record = import_conflict.value.record
        assert conflicting_record is not None
        assert conflicting_record.intent_id == _INTENT_ID
        assert conflicting_record.state == "UNKNOWN"
        assert conflicting_record.permit_reference == first_order.permit_reference
        facts_after_conflict = _host_command(host2, "facts")[1]
        assert facts_after_conflict["provider_dispatch_counts"] == {"intent_1": 1, "intent_2": 0}
        assert _business_projection(facts_after_conflict) == _business_projection(facts_old_epoch)

        settled = runtime_duplicate.call(
            "store.account",
            "account.read_after",
            {
                "cursor": 2,
                "limit": 10,
                "track_delivery": True,
                "stream_epoch": journal_epoch,
            },
        )
        assert settled["events"] == ()
        assert settled["consumer_progress"] == {
            "stream_epoch": journal_epoch,
            "offered_cursor": 2,
            "acknowledged_cursor": 2,
        }
        _append_followup_journal_events(gateway_path)
        delivered = runtime_duplicate.call(
            "store.account",
            "account.read_after",
            {
                "cursor": 2,
                "limit": 10,
                "track_delivery": True,
                "stream_epoch": journal_epoch,
            },
        )
        assert [event["event_id"] for event in delivered["events"]] == [
            "mig03b-event-3",
            "mig03b-event-4",
        ]
        assert delivered["consumer_progress"] == {
            "stream_epoch": journal_epoch,
            "offered_cursor": 4,
            "acknowledged_cursor": 2,
        }
        acked_after = runtime_duplicate.call(
            "store.account",
            "account.ack",
            {"stream_epoch": journal_epoch, "cursor": 4},
        )
        assert acked_after["consumer_progress"] == {
            "stream_epoch": journal_epoch,
            "offered_cursor": 4,
            "acknowledged_cursor": 4,
        }
        reread = runtime_duplicate.call(
            "store.account",
            "account.read_after",
            {
                "cursor": 4,
                "limit": 10,
                "track_delivery": True,
                "stream_epoch": journal_epoch,
            },
        )
        assert reread["events"] == ()
        progress_result = runtime_duplicate.call("store.account", "account.progress", {})
        assert progress_result["consumer_progress"] == {
            "stream_epoch": journal_epoch,
            "offered_cursor": 4,
            "acknowledged_cursor": 4,
        }

        # A renamed wire scope (REG04 old-ID-mapping obligation) is denied with
        # no second order partition and no duplicate quota.
        direct_factory = ZmqTransportFactory(
            {
                _CLIENT_SECURITY_REF: ClientCurveKeys(
                    keys["server_public"],
                    keys["strategy_public"],
                    keys["strategy_secret"],
                )
            }
        )
        profile = _client_config(endpoint).communication["profiles"][_CLIENT_CONNECT_PROFILE]
        direct_transport = direct_factory(profile, _SERVICE_IDENTITY)
        try:
            execution_binding = _binding_of(runtime_duplicate, "broker.orders")
            renamed_scope = replace(
                execution_binding.scope, strategy_ref="mig03b-renamed-strategy"
            )
            renamed_denial = _direct_request(
                direct_transport,
                identity=_STRATEGY_IDENTITY,
                scope=renamed_scope,
                operation="orders.submit",
                binding=execution_binding,
                arguments=_submit_arguments(
                    _RENAMED_SCOPE_INTENT_ID, "mig03b-signal-renamed", quantity="1", price="50000"
                ),
            )
            assert renamed_denial.payload["ok"] is False
            assert renamed_denial.payload["code"] == "SCOPE_DENIED"
            account_binding = _binding_of(runtime_duplicate, "store.account")
            renamed_account = replace(
                account_binding.scope, account_ref="mig03b-renamed-account"
            )
            account_denial = _direct_request(
                direct_transport,
                identity=_STRATEGY_IDENTITY,
                scope=renamed_account,
                operation="account.read_after",
                binding=account_binding,
                arguments={"cursor": 0, "limit": 5, "track_delivery": True},
            )
            assert account_denial.payload["ok"] is False
            assert account_denial.payload["code"] == "SCOPE_DENIED"
        finally:
            direct_transport.close()
        facts_after_renamed = _host_command(host2, "facts")[1]
        assert facts_after_renamed["provider_dispatch_counts"] == {"intent_1": 1, "intent_2": 0}
        assert facts_after_renamed["risk_rows_intent_1"] == facts_old_epoch["risk_rows_intent_1"]
        assert _business_projection(facts_after_renamed) == _business_projection(facts_old_epoch)
        assert len(_sqlite_rows(execution_path)["execution_records"]) == 1
        observation["renamed_scope_denials"] = ["SCOPE_DENIED", "SCOPE_DENIED"]

        # ---- P6: the first new business write seals the cutover -----------
        # The migrated dispatch-inflight freeze and reservation stay effective
        # in the new epoch: the new risk-increasing intent is safely rejected
        # by the durable gate (no second reservation, no double quota, no
        # provider dispatch), and its order record is the first new durable
        # write of the destination epoch.
        second_order = client2.submit(
            **_submit_arguments(_NEW_INTENT_ID, "mig03b-signal-002", quantity="1", price="50000")
        )
        assert second_order.state == "REJECTED"
        assert second_order.dispatch_attempts == 0
        assert second_order.permit_reference is None
        facts_first_new_write = _host_command(host2, "facts")[1]
        assert facts_first_new_write["provider_dispatch_counts"] == {
            "intent_1": 1,
            "intent_2": 0,
        }
        assert len(facts_first_new_write["execution_rows_intent_2"]) == 1
        new_row = facts_first_new_write["execution_rows_intent_2"][0]
        assert new_row[1] == _NEW_INTENT_ID
        assert new_row[2] == "REJECTED"
        assert new_row[3] is None
        assert new_row[4] == 0
        assert facts_first_new_write["risk_rows_intent_2"] == {
            "reservations": (),
            "dispatch_claims": (),
            "freezes": (),
        }
        assert facts_first_new_write["risk_snapshot"]["increase_count"] == 1
        assert facts_first_new_write["risk_snapshot"]["increase_notional"] == Decimal("100000")
        assert (
            f"dispatch-inflight:{_INTENT_ID}"
            in facts_first_new_write["risk_snapshot"]["active_freeze_reasons"]
        )
        observation["business_facts"]["first_new_write"] = facts_first_new_write

        sealed_now = record_migration_first_write(registry_path, fact_kind="order")
        assert sealed_now is True
        assert record_migration_first_write(registry_path, fact_kind="order") is False
        sealed_marker = read_migration_marker(registry_path)
        assert sealed_marker is not None and sealed_marker.sealed
        assert sealed_marker.first_write_fact_kind == "order"
        assert sealed_marker.source_epoch == epoch1
        assert sealed_marker.destination_epoch == epoch2
        sealed_bytes = marker_path(registry_path).read_bytes()
        assert sealed_bytes != armed_bytes
        observation["first_write_seal"] = {
            "fact_kind": sealed_marker.first_write_fact_kind,
            "recorded_at": sealed_marker.first_write_recorded_at,
        }

        # ---- P7: sealed state refuses the old deployment and rollback -----
        refused_record = _start_host_process(
            context,
            _refusal_process,
            (config, keys, registry_path, epoch1),
            label="mig03b-old-deployment-refused",
        )
        refusal_records.append(refused_record)
        refusal_message = _receive_host_message(refused_record)
        assert refusal_message[0] == "refused", refusal_message
        assert refusal_message[1] == "REVERSE_MIGRATION_REFUSED"
        refused_record["process"].join(15)
        assert not refused_record["process"].is_alive()
        assert refused_record["process"].exitcode == 0
        refusal_facts = {
            "code": refusal_message[1],
            "host_state": refusal_message[2],
            "start_failure": refusal_message[3],
            "provider_calls": refusal_message[4],
            "exit_code": refused_record["process"].exitcode,
        }
        assert refusal_facts["host_state"] == "failed"
        assert refusal_facts["start_failure"] == "REVERSE_MIGRATION_REFUSED"
        assert refusal_facts["provider_calls"] == ()
        assert marker_path(registry_path).read_bytes() == sealed_bytes
        with pytest.raises(MigrationGateError) as rollback_refused:
            rollback_migration_cutover(registry_path, deployment_epoch=epoch1)
        assert rollback_refused.value.code == REFUSAL_REVERSE_MIGRATION_REFUSED
        with pytest.raises(MigrationGateError) as undeclared_refused:
            enforce_migration_start(registry_path)
        assert undeclared_refused.value.code == REFUSAL_REVERSE_MIGRATION_REFUSED
        with pytest.raises(MigrationGateError) as foreign_epoch_refused:
            enforce_migration_start(registry_path, deployment_epoch="1f" * 16)
        assert foreign_epoch_refused.value.code == REFUSAL_REVERSE_MIGRATION_REFUSED
        assert marker_path(registry_path).read_bytes() == sealed_bytes
        observation["post_seal_refusals"] = [
            refusal_facts,
            "rollback_migration_cutover:REVERSE_MIGRATION_REFUSED",
            "undeclared_start:REVERSE_MIGRATION_REFUSED",
            "foreign_epoch_start:REVERSE_MIGRATION_REFUSED",
        ]
        still_serving = _host_command(host2, "facts")[1]
        assert still_serving["service_epoch"] == epoch2
        surviving = client2.query(_INTENT_ID)
        assert surviving is not None and surviving.state == "UNKNOWN"

        # ---- P8: a destination-epoch restart remains permitted ------------
        facts_before_restart = _host_command(host2, "facts")[1]
        _close_host_normally(host2)
        host2b = _start_host_process(
            context,
            _host_process,
            (
                config,
                keys,
                epoch2,
                registry_path,
                execution_path,
                risk_path,
                provider_journal_path,
                gateway_path,
            ),
            label="mig03b-host-b2",
        )
        hosts.append(host2b)
        ready3 = _receive_host_message(host2b)
        assert ready3[0] == "ready" and ready3[1] == endpoint, ready3
        assert ready3[3] == parent_source_origins
        assert ready3[4] == epoch2
        epoch3 = ready3[2]
        assert epoch3 not in {epoch1, epoch2}
        with pytest.raises(ServiceExecutionError) as stale_epoch_two:
            client2.query(_INTENT_ID)
        assert stale_epoch_two.value.code == "STALE_EPOCH"
        runtime_duplicate.recover()
        final_ids = _registration_ids(runtime_duplicate)
        assert {item[1] for item in final_ids.values()} == {epoch3}
        final_client = _execution_client(runtime_duplicate)
        for intent_id, view, expected_state, expected_attempts in (
            (_INTENT_ID, first_order, "UNKNOWN", 1),
            (_NEW_INTENT_ID, second_order, "REJECTED", 0),
        ):
            record = final_client.query(intent_id)
            assert record is not None and record.state == expected_state
            assert record.dispatch_attempts == expected_attempts
            assert record.permit_reference == view.permit_reference
        final_progress = runtime_duplicate.call("store.account", "account.progress", {})
        assert final_progress["consumer_progress"] == {
            "stream_epoch": journal_epoch,
            "offered_cursor": 4,
            "acknowledged_cursor": 4,
        }
        facts_restart = _host_command(host2b, "facts")[1]
        assert facts_restart["service_epoch"] == epoch3
        assert _business_projection(facts_restart) == _business_projection(
            facts_before_restart
        )
        observation["business_facts"]["destination_epoch_restart"] = facts_restart
        runtime_duplicate.close()
        assert runtime_duplicate.cleanup_failures == 0
        runtimes.remove(runtime_duplicate)
        _close_host_normally(host2b)

        # ---- final offline facts ------------------------------------------
        final_journal = _journal_projection(gateway_path)
        assert tuple(
            row[3] for row in final_journal["events"] if row[1] == _JOURNAL_STRATEGY_SCOPE
        ) == ("mig03b-event-1", "mig03b-event-2", "mig03b-event-3", "mig03b-event-4")
        assert tuple(
            row[3] for row in final_journal["events"] if row[1] == _JOURNAL_LEGACY_SCOPE
        ) == ("mig03b-legacy-event-1",)
        assert final_journal["streams"] == journal_after_old_epoch["streams"]
        assert final_journal["progress"] == (
            (_STRATEGY_PRINCIPAL, _JOURNAL_ACCOUNT_SCOPE, _JOURNAL_STRATEGY_SCOPE, journal_epoch, 4, 4),
        )
        assert _execution_rows(execution_path, _INTENT_ID) == facts_old_epoch[
            "execution_rows_intent_1"
        ]
        assert _execution_rows(execution_path, _NEW_INTENT_ID) == facts_first_new_write[
            "execution_rows_intent_2"
        ]
        assert _risk_rows(risk_path, _INTENT_ID) == facts_old_epoch["risk_rows_intent_1"]
        assert _provider_count(provider_journal_path, _INTENT_ID) == 1
        assert _provider_count(provider_journal_path, _NEW_INTENT_ID) == 0
        sealed_final = read_migration_marker(registry_path)
        assert sealed_final is not None and sealed_final.sealed
        assert sealed_final.destination_epoch == epoch2
    finally:
        active_exception = sys.exc_info()[1]
        for runtime in reversed(runtimes):
            with suppress(BaseException):
                runtime.close()
        for host_record in reversed(hosts):
            _bounded_reap_owned_host(host_record, forced, cleanup_failures)
        for refusal_record in reversed(refusal_records):
            _bounded_reap_owned_host(refusal_record, forced, cleanup_failures)
        observation["hosts"] = [
            _host_observation(record) for record in (*hosts, *refusal_records)
        ]
        observation["cleanup"] = {
            "forced_cleanup": tuple(forced),
            "failures": tuple(cleanup_failures),
            "runtime_cleanup_failures": tuple(
                {
                    "client_session_id": runtime.client_session_id,
                    "count": runtime.cleanup_failures,
                }
                for runtime in runtimes
                if runtime.cleanup_failures
            ),
        }
        runtime_cleanup_failures = observation["cleanup"]["runtime_cleanup_failures"]
        observation["status"] = (
            "FAIL"
            if active_exception or forced or cleanup_failures or runtime_cleanup_failures
            else "PASS"
        )
        if active_exception is not None:
            observation["test_failure"] = _safe_exception_facts(
                active_exception, "test_assertions_or_body", None
            )
        (tmp_path / "mig03b-drill-observation.json").write_text(
            json.dumps(observation, default=str, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        _assert_no_fallback_cleanup(forced, cleanup_failures)
        assert not runtime_cleanup_failures, runtime_cleanup_failures


def test_controlled_migration_rollback_window_and_import_failure_negatives(
    tmp_path,
) -> None:
    parent_source_origins = _assert_fixed_source_origins()
    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
        "strategy_secret": strategy_secret,
    }
    config = _service_config(endpoint)
    registry_path = str(tmp_path / "mig03b-registry.sqlite3")
    execution_path = str(tmp_path / "mig03b-execution.sqlite3")
    risk_path = str(tmp_path / "mig03b-risk.sqlite3")
    provider_journal_path = str(tmp_path / "mig03b-provider-journal.sqlite3")
    gateway_path = str(tmp_path / "mig03b-gateway.sqlite3")
    _seed_journal(gateway_path)
    hosts: list[dict[str, Any]] = []
    runtimes: list[ServiceClientRuntime] = []
    forced: list[str] = []
    cleanup_failures: list[str] = []
    observation: dict[str, Any] = {
        "status": "RUNNING",
        "node": "rollback_window_and_import_failure_negatives",
        "fixed_source_root": os.environ.get("BTAPI_MIG03B_FIXED_SOURCE_ROOT"),
        "parent_source_origins": parent_source_origins,
        "hosts": [],
        "business_facts": {},
    }
    try:
        # ---- P1: old epoch slice, then freeze ----------------------------
        host1 = _start_host_process(
            context,
            _host_process,
            (
                config,
                keys,
                None,
                registry_path,
                execution_path,
                risk_path,
                provider_journal_path,
                gateway_path,
            ),
            label="mig03b-rollback-host-a",
        )
        hosts.append(host1)
        ready1 = _receive_host_message(host1)
        assert ready1[0] == "ready" and ready1[1] == endpoint, ready1
        assert ready1[3] == parent_source_origins
        epoch1 = ready1[2]

        runtime1 = _new_runtime(endpoint, keys, session_id="mig03b-rollback-session")
        runtimes.append(runtime1)
        assert runtime1.start().degraded_count == 0
        # A second live runtime on the same session keeps the epoch-1 bindings
        # reachable after runtime1 is frozen, mirroring the drill in test one.
        runtime_duplicate = _new_runtime(
            endpoint, keys, session_id="mig03b-rollback-session"
        )
        runtimes.append(runtime_duplicate)
        assert runtime_duplicate.start().degraded_count == 0
        assert _registration_ids(runtime_duplicate) == _registration_ids(runtime1)
        first_order = _execution_client(runtime1).submit(
            **_submit_arguments(_INTENT_ID, "mig03b-signal-001", quantity="2", price="50000")
        )
        assert first_order.state == "UNKNOWN"
        assert first_order.dispatch_attempts == 1
        facts_old_epoch = _host_command(host1, "facts")[1]
        assert facts_old_epoch["service_epoch"] == epoch1
        assert facts_old_epoch["provider_dispatch_counts"] == {"intent_1": 1, "intent_2": 0}
        observation["business_facts"]["old_epoch_first_slice"] = facts_old_epoch

        runtime1.close()
        assert runtime1.cleanup_failures == 0
        with pytest.raises(ServiceExecutionError) as not_registered:
            _execution_client(runtime_duplicate).query(_INTENT_ID)
        assert not_registered.value.code == "NOT_REGISTERED"
        closed1 = _close_host_normally(host1)
        assert closed1[1] == "closed" and closed1[2] == epoch1
        journal_after_old_epoch = _journal_projection(gateway_path)
        assert read_migration_marker(registry_path) is None

        # ---- P2: new owner, armed marker, import failure -----------------
        host2 = _start_host_process(
            context,
            _host_process,
            (
                config,
                keys,
                None,
                registry_path,
                execution_path,
                risk_path,
                provider_journal_path,
                gateway_path,
            ),
            label="mig03b-rollback-host-b",
        )
        hosts.append(host2)
        ready2 = _receive_host_message(host2)
        assert ready2[0] == "ready" and ready2[1] == endpoint, ready2
        epoch2 = ready2[2]
        assert epoch2 != epoch1
        assert _business_projection(_host_command(host2, "facts")[1]) == _business_projection(
            facts_old_epoch
        )
        with pytest.raises(ServiceExecutionError) as stale_epoch:
            _execution_client(runtime_duplicate).query(_INTENT_ID)
        assert stale_epoch.value.code == "STALE_EPOCH"

        arm_migration_cutover(registry_path, source_epoch=epoch1, new_epoch=epoch2)
        armed_bytes = marker_path(registry_path).read_bytes()
        with pytest.raises(MigrationGateError) as conflicting_arm:
            arm_migration_cutover(registry_path, source_epoch=epoch1, new_epoch="ab" * 16)
        assert conflicting_arm.value.code == "CUTOVER_MARKER_CONFLICT"
        assert marker_path(registry_path).read_bytes() == armed_bytes
        observation["conflicting_rearm_negative"] = "CUTOVER_MARKER_CONFLICT"

        runtime_duplicate.recover()
        recovered_ids = _registration_ids(runtime_duplicate)
        assert {item[1] for item in recovered_ids.values()} == {epoch2}
        client2 = _execution_client(runtime_duplicate)

        # Import idempotency: the identical per-ID fact re-import returns the
        # same durable record without a second dispatch.
        replay = client2.submit(
            **_submit_arguments(_INTENT_ID, "mig03b-signal-001", quantity="2", price="50000")
        )
        assert replay.state == "UNKNOWN"
        assert replay.permit_reference == first_order.permit_reference
        assert replay.dispatch_attempts == 1
        # Import failure: the same ID with different content is refused and
        # carries the existing record; nothing is zero-filled or overwritten.
        with pytest.raises(ServiceExecutionError) as import_failure:
            client2.submit(
                **_submit_arguments(_INTENT_ID, "mig03b-signal-001", quantity="9", price="50000")
            )
        assert import_failure.value.code == "INTENT_CONFLICT"
        assert import_failure.value.record is not None
        assert import_failure.value.record.permit_reference == first_order.permit_reference
        facts_after_import_failure = _host_command(host2, "facts")[1]
        assert facts_after_import_failure["provider_dispatch_counts"] == {
            "intent_1": 1,
            "intent_2": 0,
        }
        assert _business_projection(facts_after_import_failure) == _business_projection(
            facts_old_epoch
        )
        observation["business_facts"]["after_import_failure"] = facts_after_import_failure

        # Pre-seal rollback window: the old deployment may still return.
        enforce_migration_start(registry_path, deployment_epoch=epoch1)
        observation["pre_seal_window"] = "old_deployment_start_permitted"
        _close_host_normally(host2)

        # ---- P3: controlled rollback before the first new write ----------
        assert rollback_migration_cutover(registry_path, deployment_epoch=epoch1) is True
        assert not marker_path(registry_path).exists()
        observation["rollback"] = "marker_removed_before_seal"

        # ---- P4: the complete old deployment returns unchanged -----------
        host3 = _start_host_process(
            context,
            _host_process,
            (
                config,
                keys,
                epoch1,
                registry_path,
                execution_path,
                risk_path,
                provider_journal_path,
                gateway_path,
            ),
            label="mig03b-rollback-host-a2",
        )
        hosts.append(host3)
        ready3 = _receive_host_message(host3)
        assert ready3[0] == "ready" and ready3[1] == endpoint, ready3
        assert ready3[4] == epoch1
        epoch3 = ready3[2]
        assert epoch3 not in {epoch1, epoch2}
        with pytest.raises(ServiceExecutionError) as stale_epoch_two:
            _execution_client(runtime_duplicate).query(_INTENT_ID)
        assert stale_epoch_two.value.code == "STALE_EPOCH"
        runtime_duplicate.recover()
        final_ids = _registration_ids(runtime_duplicate)
        assert {item[1] for item in final_ids.values()} == {epoch3}
        final_order = _execution_client(runtime_duplicate).query(_INTENT_ID)
        assert final_order is not None and final_order.state == "UNKNOWN"
        assert final_order.dispatch_attempts == 1
        assert final_order.permit_reference == first_order.permit_reference
        facts_restored = _host_command(host3, "facts")[1]
        assert facts_restored["service_epoch"] == epoch3
        assert _business_projection(facts_restored) == _business_projection(facts_old_epoch)
        observation["business_facts"]["after_rollback_restart"] = facts_restored
        runtime_duplicate.close()
        assert runtime_duplicate.cleanup_failures == 0
        runtimes.remove(runtime_duplicate)
        _close_host_normally(host3)

        # ---- P5: final offline facts -------------------------------------
        assert _journal_projection(gateway_path) == journal_after_old_epoch
        assert _execution_rows(execution_path, _INTENT_ID) == facts_old_epoch[
            "execution_rows_intent_1"
        ]
        assert _risk_rows(risk_path, _INTENT_ID) == facts_old_epoch["risk_rows_intent_1"]
        assert _provider_count(provider_journal_path, _INTENT_ID) == 1
        assert read_migration_marker(registry_path) is None
        assert rollback_migration_cutover(registry_path, deployment_epoch=epoch1) is False
        enforce_migration_start(registry_path)
    finally:
        active_exception = sys.exc_info()[1]
        for runtime in reversed(runtimes):
            with suppress(BaseException):
                runtime.close()
        for host_record in reversed(hosts):
            _bounded_reap_owned_host(host_record, forced, cleanup_failures)
        observation["hosts"] = [_host_observation(record) for record in hosts]
        observation["cleanup"] = {
            "forced_cleanup": tuple(forced),
            "failures": tuple(cleanup_failures),
            "runtime_cleanup_failures": tuple(
                {
                    "client_session_id": runtime.client_session_id,
                    "count": runtime.cleanup_failures,
                }
                for runtime in runtimes
                if runtime.cleanup_failures
            ),
        }
        runtime_cleanup_failures = observation["cleanup"]["runtime_cleanup_failures"]
        observation["status"] = (
            "FAIL"
            if active_exception or forced or cleanup_failures or runtime_cleanup_failures
            else "PASS"
        )
        if active_exception is not None:
            observation["test_failure"] = _safe_exception_facts(
                active_exception, "test_assertions_or_body", None
            )
        (tmp_path / "mig03b-rollback-observation.json").write_text(
            json.dumps(observation, default=str, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        _assert_no_fallback_cleanup(forced, cleanup_failures)
        assert not runtime_cleanup_failures, runtime_cleanup_failures
