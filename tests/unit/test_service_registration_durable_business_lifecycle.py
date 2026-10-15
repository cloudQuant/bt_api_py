"""Registration lifecycle keeps UNKNOWN order and Risk facts across recovery.

This node uses the real spawned ServiceHost, CURVE RPC, SQLite registry,
Execution journal and DurableRiskGate. Only the terminal provider call is
synthetic; it records a durable attempt and raises a timeout to create an
UNKNOWN outcome without an account or network connection.
"""

from __future__ import annotations

import functools
import inspect
import json
import multiprocessing
import os
import socket
import sqlite3
import sys
import traceback
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import ExecutionScope, SqliteExecutionStore
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
from bt_api_py.service_execution import (
    ServiceExecutionClient,
    ServiceExecutionError,
)
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceRegistry
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

_DOMAIN = "paper:reg03-durable-lifecycle"
_SERVICE_ID = "reg03-synthetic-execution"
_SERVICE_PRINCIPAL = "reg03-execution-owner"
_SERVICE_INSTANCE = "reg03-account-owner"
_SERVICE_SCOPE_REF = "reg03-service-scope-v1"
_SERVER_SECURITY_REF = "reg03-server-curve-v1"
_ACCOUNT = "reg03-paper-account"
_STRATEGY = "reg03-strategy"
_STRATEGY_PRINCIPAL = "reg03-strategy-principal"
_ORDER_SCOPE_REF = "reg03-order-scope-v1"
_QUERY_SCOPE_REF = "reg03-query-scope-v1"
_CLIENT_SECURITY_REF = "reg03-client-curve-v1"
_INTENT_ID = "reg03-unknown-order-001"
_RUN_ID = "reg03-stable-run-001"
_REGISTRY_LEASE_SECONDS = 60.0
_REGISTRY_CLOCK_INITIAL = 1_800_000_000.0
_METADATA_TIME_NS = 1_800_000_000_000_000_000
_RISK_CLOCK_SECONDS = 1_800_000_000.0
_EXECUTION_SCOPE = ExecutionScope("PAPER", "simulation", _ACCOUNT, _STRATEGY, "20261015")
_WIRE_SCOPE = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
_ACCOUNT_SCOPE = AccountScope("PAPER", _ACCOUNT, "simulation")
_RISK_POLICY = RiskPolicy(
    "reg03-lifecycle-risk-v1",
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
        raise AssertionError("synthetic provider cancellation is outside this lifecycle case")

    def close(self) -> None:
        self._db.close()


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
        "transport_ref": "reg03-execution-listener-v1",
        "registration_policy_ref": "reg03-registration-policy-v1",
        "service_requirements_ref": "reg03-service-requirements-v1",
        "components": {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _SERVICE_INSTANCE,
                    "bind_ports": ["service.order_entry", "service.order_query"],
                    "options": {"role": "account_owner"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "reg03-execution-listener-v1": {
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
                                "instance_id": _SERVICE_INSTANCE,
                                "scope_ref": _ORDER_SCOPE_REF,
                                "transport_ref": "reg03-execution-connect-v1",
                                "account_ref": _ACCOUNT,
                            },
                            {
                                "mount": "broker.order_query",
                                "service_id": _SERVICE_ID,
                                "instance_id": _SERVICE_INSTANCE,
                                "scope_ref": _QUERY_SCOPE_REF,
                                "transport_ref": "reg03-execution-connect-v1",
                                "account_ref": _ACCOUNT,
                            },
                        ],
                        "options": {"request_timeout_ms": 2000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "reg03-execution-connect-v1": {
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
        metadata_version="reg03-metadata-v1",
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


def _safe_exception_facts(exc: BaseException, phase: str, host) -> dict[str, Any]:
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
    registry_path: str,
    execution_path: str,
    risk_path: str,
    provider_journal_path: str,
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
        metadata = _metadata()
        mapper = InstrumentRiskAdmissionMapper(
            _ACCOUNT_SCOPE,
            InstrumentRiskRegistry((metadata,)),
            clock_ns=lambda: _METADATA_TIME_NS + 1,
        )
        admission = DurableRiskAdmissionAdapter(gate, mapper)

        def resource_provider(component, _instance_id, _options, _scope):
            if component != "execution":
                raise AssertionError("unexpected REG03 component")
            return ComponentResources.borrowed(
                {
                    "store": store,
                    "targets": {_WIRE_SCOPE: _EXECUTION_SCOPE},
                    "dispatch": provider,
                    "admission": {"order_gate": admission, "cancel_gate": None},
                },
                ready=lambda: True,
            )

        # The authority and client share only synthetic CURVE key material passed
        # directly to this spawned process.
        authority = TrustedServiceAuthority(
            identity=WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID),
            scope=WireScope(_DOMAIN),
            scope_ref=_SERVICE_SCOPE_REF,
            scope_version="reg03-execution-scope-v1",
            security_ref=_SERVER_SECURITY_REF,
            security_version="curve-key-v1",
            registration_policy_ref=config.registration_policy_ref,
            registration_policy_version="reg03-policy-v1",
            service_requirements_ref=config.service_requirements_ref,
            service_requirements_version="reg03-requirements-v1",
            curve_public_key=keys["server_public"],
            curve_secret_key=keys["server_secret"],
            principals={keys["strategy_public"]: _STRATEGY_PRINCIPAL},
            grants={
                _STRATEGY_PRINCIPAL: (
                    PrincipalGrant(
                        WireIdentity(WireRole.STRATEGY_CLIENT, _STRATEGY_PRINCIPAL, _STRATEGY),
                        (
                            ResourceGrant(
                                _SERVICE_INSTANCE,
                                "service.order_entry",
                                _ORDER_SCOPE_REF,
                                _WIRE_SCOPE,
                                frozenset({"orders.submit", "orders.cancel"}),
                            ),
                            ResourceGrant(
                                _SERVICE_INSTANCE,
                                "service.order_query",
                                _QUERY_SCOPE_REF,
                                _WIRE_SCOPE,
                                frozenset({"orders.query"}),
                            ),
                        ),
                    ),
                )
            },
        )

        def capture_facts() -> dict[str, Any]:
            snapshot = gate.snapshot(_ACCOUNT_SCOPE)
            return {
                "execution_rows": _execution_rows(execution_path, _INTENT_ID),
                "risk_rows": _risk_rows(risk_path, _INTENT_ID),
                "provider_dispatch_count": _provider_count(provider_journal_path, _INTENT_ID),
                "provider_cancel_count": provider.cancel_calls,
                "risk_snapshot": {
                    "increase_count": snapshot["increase_count"],
                    "increase_notional": snapshot["increase_notional"],
                    "active_freeze_reasons": tuple(snapshot["active_freeze_reasons"]),
                },
                "active_registration_ids": tuple(
                    sorted(host._registry.active_registration_ids(instance_id=_SERVICE_INSTANCE))
                ),
                "service_epoch": host._registry.service_epoch,
            }

        phase = "host_start"
        host = ServiceHost(config, authority, registry_path, resource_provider)
        host.start()
        control.send(("ready", host.endpoint, host._registry.service_epoch, source_origins))
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
            if command == ("expire_registry_lease",):
                phase = "expire_registry_lease"
                before = tuple(
                    sorted(host._registry.active_registration_ids(instance_id=_SERVICE_INSTANCE))
                )
                server_clock[0] += _REGISTRY_LEASE_SECONDS + 1.0
                after = tuple(
                    sorted(host._registry.active_registration_ids(instance_id=_SERVICE_INSTANCE))
                )
                control.send(("expired", before, after))
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
            raise AssertionError("unknown REG03 host command")
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


def _new_runtime(
    endpoint: str,
    keys: dict[str, bytes],
    *,
    session_id: str,
) -> ServiceClientRuntime:
    peer = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, _STRATEGY_PRINCIPAL, _STRATEGY)
    config = _client_config(endpoint)
    return ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=lambda _scope_ref: _WIRE_SCOPE,
        peer_resolver=lambda _service_id: peer,
        transport_factory=ZmqTransportFactory(
            {
                _CLIENT_SECURITY_REF: ClientCurveKeys(
                    keys["server_public"],
                    keys["strategy_public"],
                    keys["strategy_secret"],
                )
            }
        ),
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


def _start_host_process(context, *args, label: str) -> dict[str, Any]:
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=_host_process, args=(child, *args), name=label)
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
    if message[0] == "ready" and len(message) == 4:
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
    """Bound cleanup for this test's own spawned ServiceHost only."""

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
        "ready_source_origins": record.get("ready_source_origins"),
        "child_failure": record.get("child_failure"),
        "child_pipe_eof": record.get("child_pipe_eof", False),
        "close_send_broken_pipe": record.get("close_send_broken_pipe", False),
        "normal_close_ack": record.get("normal_close_ack", False),
        "normal_exit_code": record.get("normal_exit_code"),
        "terminal_exit_code": record.get("terminal_exit_code"),
        "closed": record["closed"],
    }


def _assert_no_fallback_cleanup(forced: list[str], failures: list[str]) -> None:
    assert not forced and not failures, (tuple(forced), tuple(failures))


def _business_projection(facts: dict[str, Any]) -> tuple[Any, ...]:
    return (
        facts["execution_rows"],
        facts["risk_rows"],
        facts["provider_dispatch_count"],
        facts["provider_cancel_count"],
        facts["risk_snapshot"],
    )


def _registration_ids(runtime: ServiceClientRuntime) -> dict[str, tuple[str, str]]:
    return {
        binding.mount: (binding.registration_id, binding.service_epoch)
        for binding in runtime.bindings
    }


def _assert_fixed_source_origins() -> dict[str, str]:
    source_root = os.environ.get("BTAPI_REG03_FIXED_SOURCE_ROOT")
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
    )
    actual_origins = {}
    for symbol, expected in symbols:
        actual = Path(inspect.getsourcefile(symbol)).resolve()
        assert actual == expected.resolve()
        actual_origins[symbol.__name__] = str(actual)
    return actual_origins


def test_real_registration_lifecycle_preserves_unknown_order_and_risk_across_unregister_expiry_and_restart(
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
    registry_path = str(tmp_path / "reg03-registry.sqlite3")
    execution_path = str(tmp_path / "reg03-execution.sqlite3")
    risk_path = str(tmp_path / "reg03-risk.sqlite3")
    provider_journal_path = str(tmp_path / "reg03-provider-journal.sqlite3")
    hosts = []
    runtimes: list[ServiceClientRuntime] = []
    forced: list[str] = []
    cleanup_failures: list[str] = []
    observation: dict[str, Any] = {
        "status": "RUNNING",
        "fixed_source_root": os.environ.get("BTAPI_REG03_FIXED_SOURCE_ROOT"),
        "parent_source_origins": parent_source_origins,
        "hosts": [],
        "business_facts": {},
    }
    try:
        host1 = _start_host_process(
            context,
            config,
            keys,
            registry_path,
            execution_path,
            risk_path,
            provider_journal_path,
            label="reg03-service-host-1",
        )
        hosts.append(host1)
        ready1 = _receive_host_message(host1)
        assert ready1[0] == "ready" and ready1[1] == endpoint, ready1
        assert ready1[3] == parent_source_origins
        observation["hosts"].append(_host_observation(host1))
        epoch1 = ready1[2]

        runtime1 = _new_runtime(endpoint, keys, session_id="reg03-client-session-original")
        runtimes.append(runtime1)
        assert runtime1.start().degraded_count == 0
        runtime1_ids = _registration_ids(runtime1)
        assert set(runtime1_ids) == {"broker.orders", "broker.order_query"}
        assert {epoch for _, epoch in runtime1_ids.values()} == {epoch1}

        # Same session, run, scopes and request digest are an idempotent retry.
        runtime_duplicate = _new_runtime(endpoint, keys, session_id="reg03-client-session-original")
        runtimes.append(runtime_duplicate)
        assert runtime_duplicate.start().degraded_count == 0
        assert _registration_ids(runtime_duplicate) == runtime1_ids

        # A second live client session cannot acquire the same strategy write
        # claim, even when it presents the same logical run_id.
        competitor = _new_runtime(endpoint, keys, session_id="reg03-client-session-competitor")
        runtimes.append(competitor)
        with pytest.raises(ServiceRuntimeError) as duplicate_active:
            competitor.start()
        assert duplicate_active.value.code == "DUPLICATE_ACTIVE_RUN"
        assert competitor.cleanup_failures == 0
        active_before_submit = _host_command(host1, "facts")
        assert active_before_submit[0] == "facts"
        assert set(active_before_submit[1]["active_registration_ids"]) == {
            runtime1_ids[mount][0] for mount in runtime1_ids
        }

        client1 = _execution_client(runtime1)
        order = client1.submit(
            intent_id=_INTENT_ID,
            signal_id="reg03-signal-unknown-001",
            instrument="BTC-USDT",
            side="BUY",
            position_effect="OPEN",
            order_type="LIMIT",
            quantity="2",
            price="50000",
            time_in_force="GTC",
            metadata_version="reg03-metadata-v1",
            tags={INSTRUMENT_METADATA_DIGEST_TAG: _metadata().digest},
        )
        assert order.state == "UNKNOWN"
        order = client1.query(_INTENT_ID)
        assert order is not None
        assert order.state == "UNKNOWN"
        assert order.dispatch_attempts == 1
        assert order.permit_reference

        original_facts = _host_command(host1, "facts")[1]
        assert len(original_facts["execution_rows"]) == 1
        assert original_facts["execution_rows"][0][2:5] == (
            "UNKNOWN",
            order.permit_reference,
            1,
        )
        assert original_facts["provider_dispatch_count"] == 1
        assert original_facts["provider_cancel_count"] == 0
        observation["business_facts"]["after_unknown_dispatch"] = original_facts
        reservations = original_facts["risk_rows"]["reservations"]
        assert len(reservations) == 1
        reservation = reservations[0]
        assert type(reservation) is tuple and len(reservation) == 5
        assert reservation[:4] == (_INTENT_ID, order.permit_reference, "active", None)
        assert type(reservation[4]) is str
        assert Decimal(reservation[4]) == Decimal("100000")
        assert original_facts["risk_rows"]["dispatch_claims"] == (
            (_INTENT_ID, order.permit_reference, f"dispatch-inflight:{_INTENT_ID}"),
        )
        assert original_facts["risk_rows"]["freezes"] == (
            (
                f"dispatch-inflight:{_INTENT_ID}",
                f"dispatch-inflight:{_INTENT_ID}",
                1,
            ),
        )
        assert original_facts["risk_snapshot"]["increase_count"] == 1
        assert original_facts["risk_snapshot"]["increase_notional"] == Decimal("100000")
        assert (
            f"dispatch-inflight:{_INTENT_ID}"
            in original_facts["risk_snapshot"]["active_freeze_reasons"]
        )

        # Closing one idempotent caller unregisters the shared server lease;
        # it must not erase the business record or Risk reservation.
        runtime1.close()
        assert runtime1.cleanup_failures == 0
        closed_session_error = None
        with pytest.raises(ServiceExecutionError) as not_registered:
            _execution_client(runtime_duplicate).query(_INTENT_ID)
        closed_session_error = not_registered.value.code
        assert closed_session_error == "NOT_REGISTERED"
        after_unregister = _host_command(host1, "facts")[1]
        observation["business_facts"]["after_unregister"] = after_unregister
        assert after_unregister["active_registration_ids"] == ()
        assert _business_projection(after_unregister) == _business_projection(original_facts)

        # recover() generates a new client-session id but keeps the approved
        # run generation and only queries the existing UNKNOWN business ID.
        session_before_recover = runtime_duplicate.client_session_id
        stable_run_id = runtime_duplicate.run_id
        runtime_duplicate.recover()
        assert runtime_duplicate.client_session_id != session_before_recover
        assert runtime_duplicate.run_id == stable_run_id == _RUN_ID
        recovered = _execution_client(runtime_duplicate).query(_INTENT_ID)
        assert recovered is not None and recovered.state == "UNKNOWN"
        assert recovered.dispatch_attempts == 1
        after_unregister_recover = _host_command(host1, "facts")[1]
        observation["business_facts"]["after_unregister_recover"] = after_unregister_recover
        assert _business_projection(after_unregister_recover) == _business_projection(
            original_facts
        )

        # Expire the real registry leases using its injected server clock and
        # owner-side expiry transaction; no client-side elapsed-time guess.
        before_expiry = _registration_ids(runtime_duplicate)
        expired = _host_command(host1, "expire_registry_lease")
        assert expired[0] == "expired"
        assert set(expired[1]) == {value[0] for value in before_expiry.values()}
        assert expired[2] == ()
        with pytest.raises(ServiceExecutionError) as lease_error:
            _execution_client(runtime_duplicate).query(_INTENT_ID)
        assert lease_error.value.code == "NOT_REGISTERED"
        after_expiry = _host_command(host1, "facts")[1]
        observation["business_facts"]["after_lease_expiry"] = after_expiry
        assert after_expiry["active_registration_ids"] == ()
        assert _business_projection(after_expiry) == _business_projection(original_facts)

        session_before_expiry_recover = runtime_duplicate.client_session_id
        runtime_duplicate.recover()
        assert runtime_duplicate.client_session_id != session_before_expiry_recover
        assert runtime_duplicate.run_id == _RUN_ID
        recovered_after_expiry = _execution_client(runtime_duplicate).query(_INTENT_ID)
        assert recovered_after_expiry is not None
        assert recovered_after_expiry.state == "UNKNOWN"
        assert recovered_after_expiry.dispatch_attempts == 1
        after_expiry_recover = _host_command(host1, "facts")[1]
        observation["business_facts"]["after_expiry_recover"] = after_expiry_recover
        assert _business_projection(after_expiry_recover) == _business_projection(original_facts)

        old_epoch_ids = _registration_ids(runtime_duplicate)
        _close_host_normally(host1)
        ready2_host = _start_host_process(
            context,
            config,
            keys,
            registry_path,
            execution_path,
            risk_path,
            provider_journal_path,
            label="reg03-service-host-2",
        )
        hosts.append(ready2_host)
        ready2 = _receive_host_message(ready2_host)
        assert ready2[0] == "ready" and ready2[1] == endpoint, ready2
        assert ready2[3] == parent_source_origins
        observation["hosts"].append(_host_observation(ready2_host))
        epoch2 = ready2[2]
        assert epoch2 != epoch1

        # The old authenticated binding is rejected by epoch before any
        # business handler runs; only explicit recovery registers anew.
        with pytest.raises(ServiceExecutionError) as stale_epoch:
            _execution_client(runtime_duplicate).query(_INTENT_ID)
        assert stale_epoch.value.code == "STALE_EPOCH"
        after_restart_before_recover = _host_command(ready2_host, "facts")[1]
        observation["business_facts"]["after_restart_before_recover"] = after_restart_before_recover
        assert after_restart_before_recover["active_registration_ids"] == ()
        assert _business_projection(after_restart_before_recover) == _business_projection(
            original_facts
        )

        session_before_restart_recover = runtime_duplicate.client_session_id
        runtime_duplicate.recover()
        assert runtime_duplicate.client_session_id != session_before_restart_recover
        assert runtime_duplicate.run_id == _RUN_ID
        new_ids = _registration_ids(runtime_duplicate)
        assert {epoch for _, epoch in new_ids.values()} == {epoch2}
        assert all(new_ids[mount][0] != prior[0] for mount, prior in old_epoch_ids.items())
        final_order = _execution_client(runtime_duplicate).query(_INTENT_ID)
        assert final_order is not None
        assert final_order.state == "UNKNOWN"
        assert final_order.dispatch_attempts == 1
        assert final_order.permit_reference == order.permit_reference
        final_facts = _host_command(ready2_host, "facts")[1]
        observation["business_facts"]["after_restart_recover"] = final_facts
        assert _business_projection(final_facts) == _business_projection(original_facts)
        assert final_facts["provider_dispatch_count"] == 1

        runtime_duplicate.close()
        assert runtime_duplicate.cleanup_failures == 0
        runtimes.remove(runtime_duplicate)
        empty_after_close = _host_command(ready2_host, "facts")[1]
        observation["business_facts"]["after_client_close"] = empty_after_close
        assert empty_after_close["active_registration_ids"] == ()
        assert _business_projection(empty_after_close) == _business_projection(original_facts)
        _close_host_normally(ready2_host)

        # Reopen the persisted files outside the ServiceHost lifecycle and
        # verify the exact order, risk claim, reservation and one dispatch.
        final_execution_rows = _execution_rows(execution_path, _INTENT_ID)
        assert final_execution_rows == original_facts["execution_rows"]
        final_risk_rows = _risk_rows(risk_path, _INTENT_ID)
        assert final_risk_rows == original_facts["risk_rows"]
        assert _provider_count(provider_journal_path, _INTENT_ID) == 1
        assert tuple(row[0] for row in final_execution_rows) == (order.scope_key,)
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
        (tmp_path / "reg03-observation.json").write_text(
            json.dumps(observation, default=str, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        _assert_no_fallback_cleanup(forced, cleanup_failures)
        assert not runtime_cleanup_failures, runtime_cleanup_failures
