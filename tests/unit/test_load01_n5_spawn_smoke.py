"""One-account N5 spawn smoke through the actual LOAD01 service targets.

This exercises two spawned synthetic service children (account/execution owner
and shared group coordinator), real Curve ZMQ/Registry traffic, and the actual
runner owner/coordinator entry points. The Monitor ServiceHosts run on a parent
thread. This is a small local integration check, not LOAD01 acceptance.
"""

from __future__ import annotations

import importlib
import json
import multiprocessing
import os
import threading
import time
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

# This integration test requires an explicitly prepared, pinned LOAD01 export.
pytestmark = pytest.mark.skipif(
    not all(
        os.environ.get(name)
        for name in (
            "BT_API_LOAD01_SOURCE_ROOT",
            "BT_API_LOAD01_EXPORT_PINS",
            "BT_API_LOAD01_RUNNER_OVERLAY_SHA256",
        )
    ),
    reason="requires the fixed-source LOAD01 integration environment",
)
import zmq

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    ExecutionScope,
    ExecutionState,
    GroupOrderSnapshot,
    GroupOrderState,
    OrderIntent,
    PositionEffect,
    Side,
    TimeInForce,
    group_leg_intent_id,
)
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from tests.unit import _load01_group_coordinator as coordinator
from tests.unit import test_load01_hundred_strategy_30min as runner
from tests.unit._load01_group_workload import (
    N5_LEG_IDS,
    N5_LEG_SPECS,
    build_n5_group_request,
    query_n5_group,
    submit_n5_group,
)
from tests.unit._load01_profiles import load01_profile


_ONE_MESSAGE_TIMEOUT_SECONDS = 45.0
_CHILD_CLOSE_TIMEOUT_SECONDS = 90.0


def _receive_tag(connection, expected: str, timeout: float, *, label: str) -> tuple[Any, ...]:
    deadline = time.monotonic() + timeout
    observed: list[tuple[Any, ...]] = []
    while time.monotonic() < deadline:
        wait = min(0.1, max(0.0, deadline - time.monotonic()))
        if not connection.poll(wait):
            continue
        message = connection.recv()
        if not isinstance(message, tuple) or not message:
            raise AssertionError(f"{label} sent an invalid control message: {message!r}")
        tag = message[0]
        if tag in {"progress", "rpc-diagnostic"}:
            observed.append(message)
            continue
        if tag == "error":
            raise AssertionError(f"{label} failed: {message!r}; prior={observed!r}")
        if tag != expected:
            raise AssertionError(
                f"{label} sent {tag!r}, expected {expected!r}; prior={observed!r}"
            )
        return message
    raise TimeoutError(f"{label} timed out waiting for {expected!r}; prior={observed!r}")


def _spawn(context, target, args: tuple[Any, ...], name: str):
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=target, args=(child, *args), name=name)
    try:
        process.start()
    except BaseException:
        parent.close()
        child.close()
        process.close()
        raise
    child.close()
    return {"process": process, "pipe": parent, "name": name, "pid": process.pid}


def _close_spawned(record: dict[str, Any], *, timeout: float) -> int:
    process = record["process"]
    process.join(timeout)
    if process.is_alive():
        # Closing the control pipe is a cooperative EOF signal to the child.
        # Never terminate a service process from this smoke.
        record["pipe"].close()
        process.join(timeout)
    alive = process.is_alive()
    exit_code = process.exitcode
    if alive:
        raise RuntimeError(f"cleanup_uncertain: {record['name']} remained alive after bounded join")
    record["pipe"].close()
    process.close()
    assert exit_code == 0, f"{record['name']} exited with {exit_code}"
    return exit_code


def _monitor_thread_target(error_box: list[BaseException], target, args: tuple[Any, ...]) -> None:
    try:
        target(*args)
    except BaseException as exc:
        error_box.append(exc)


def _monitor_ready(connection, thread: threading.Thread, error_box: list[BaseException]):
    deadline = time.monotonic() + _ONE_MESSAGE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if error_box:
            raise AssertionError("Monitor ServiceHost thread failed before ready") from error_box[0]
        if connection.poll(0.1):
            message = connection.recv()
            assert type(message) is tuple and message[:2] == ("ready", "monitor"), message
            return message
        assert thread.is_alive(), "Monitor ServiceHost thread exited before ready"
    raise TimeoutError("Monitor ServiceHosts did not become ready")


def _runtime_config(
    *,
    owner_service: Mapping[str, Any],
    coordinator_service: Mapping[str, Any],
    account_ref: str,
    strategy_ref: str,
) -> dict[str, Any]:
    coordinator_route = coordinator_service["strategy_bindings"][0]
    owner_transport = "load01-n5-smoke-owner-client"
    coordinator_transport = "load01-n5-smoke-coordinator-client"
    bindings = [
        {
            "mount": "broker.orders",
            "service_id": owner_service["service_id"],
            "instance_id": owner_service["instance_id"],
            "scope_ref": owner_service["order_scope_ref"],
            "account_ref": account_ref,
            "transport_ref": owner_transport,
        },
        {
            "mount": "broker.order_query",
            "service_id": owner_service["service_id"],
            "instance_id": owner_service["instance_id"],
            "scope_ref": owner_service["query_scope_ref"],
            "account_ref": account_ref,
            "transport_ref": owner_transport,
        },
        {
            "mount": "broker.order_groups",
            "service_id": coordinator_service["service_id"],
            "instance_id": coordinator_service["instance_id"],
            "scope_ref": coordinator_route["scope_ref"],
            "portfolio_ref": coordinator_route["portfolio_ref"],
            "transport_ref": coordinator_transport,
        },
    ]
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": strategy_ref},
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": bindings,
                    "options": {"request_timeout_ms": runner._RPC_TIMEOUT_MS},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                owner_transport: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": owner_service["endpoint"]},
                    "security_profile_ref": owner_service["security_ref"],
                    "options": {"request_timeout_ms": runner._RPC_TIMEOUT_MS},
                },
                coordinator_transport: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": coordinator_service["endpoint"]},
                    "security_profile_ref": coordinator_service["security_ref"],
                    "options": {"request_timeout_ms": runner._RPC_TIMEOUT_MS},
                },
            },
        },
    }


def test_n5_one_owner_spawn_and_shared_coordinator_end_to_end(tmp_path: Path) -> None:
    """Run one ordinary order and four balanced group cycles over real local ZMQ."""

    if os.name != "nt":
        pytest.skip("the LOAD01 process smoke is specifically for Windows spawn")

    source_root = runner._fixed_source_root()
    assert Path(runner.__file__).resolve().is_relative_to(source_root)
    assert Path(coordinator.__file__).resolve().is_relative_to(source_root)
    runner_path = Path(runner.__file__).resolve()
    runner_relative = runner_path.relative_to(source_root).as_posix()
    runner_overlay_sha256 = os.environ.get("BT_API_LOAD01_RUNNER_OVERLAY_SHA256")
    assert runner_overlay_sha256 == runner._sha256(runner_path)
    profile_relative = "tests/unit/_load01_profiles.py"
    profile_test_relative = "tests/unit/test_load01_profiles.py"
    allowed_overlays = {
        runner_relative: runner_overlay_sha256,
        "tests/unit/test_load01_runner_metrics.py": "8c34b5a78ae7e69168301878127a690850e4856c2229deb9f0acf468b9557862",
        profile_relative: runner._N6_PROFILE_OVERLAY_SHA256,
        profile_test_relative: runner._N6_PROFILE_TEST_OVERLAY_SHA256,
    }
    source_manifest_path = runner._write_source_run_manifest(
        source_root,
        tmp_path,
        profile_id="N5",
        allowed_overlays=allowed_overlays,
    )
    os.environ["BT_API_LOAD01_SOURCE_ROOT"] = str(source_root)
    os.environ["BT_API_LOAD01_SOURCE_MANIFEST"] = str(source_manifest_path)
    checked = runner._check_fixed_source_manifest(
        source_manifest_path, profile_id="N5"
    )
    assert checked["load_runner"]["sha256"] == runner._sha256(runner_path)
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_pins = source_manifest["files"]
    n5_modules = {
        "tests.unit.test_load01_hundred_strategy_30min": runner,
        "tests.unit._load01_group_coordinator": coordinator,
        "tests.unit._load01_group_workload": importlib.import_module(
            "tests.unit._load01_group_workload"
        ),
        "tests.unit._load01_group_topology": importlib.import_module(
            "tests.unit._load01_group_topology"
        ),
        "tests.unit._load01_n5_owner_resources": importlib.import_module(
            "tests.unit._load01_n5_owner_resources"
        ),
        "tests.unit._load01_profiles": importlib.import_module("tests.unit._load01_profiles"),
        "tests.unit._service_group_provider_fixtures": importlib.import_module(
            "tests.unit._service_group_provider_fixtures"
        ),
        "tests.integration._dim09_three_leg_fixtures": importlib.import_module(
            "tests.integration._dim09_three_leg_fixtures"
        ),
    }
    for module_name, module in n5_modules.items():
        module_path = Path(module.__file__).resolve()
        assert module_path.is_relative_to(source_root), module_name
        relative_path = module_path.relative_to(source_root).as_posix()
        assert source_pins.get(relative_path) == runner._sha256(module_path), (
            module_name,
            relative_path,
        )

    context = multiprocessing.get_context("spawn")
    active_before = {item.pid for item in multiprocessing.active_children()}
    profile = load01_profile("N5")
    assert 0 in profile.expected_group_capable_strategies
    assert profile.accounts_for_strategy(0) == (0,)
    expected_registry_sessions = profile.expected_registry_sessions
    assert expected_registry_sessions["execution-00"] == 13
    assert expected_registry_sessions["group-coordinator"] == 2
    account_ref = runner._ACCOUNT_REFS[0]
    strategy_ref = runner._strategy_ref(0)
    origin_run_id = "load01-n5-owner-run-a00-v1"
    reservation_scope_ref = "load01-n5-reservation-scope-a00-v1"

    root = tmp_path / "load01-n5-spawn-smoke"
    paths = {
        "guard": root / "session-guards",
        "registries": root / "registries",
        "journals": root / "journals",
        "checkpoints": root / "relay-checkpoints",
        "monitor": root / "monitor",
        "coordinator": root / "coordinator",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)

    account_endpoint, execution_endpoint, coordinator_endpoint, durable_endpoint, telemetry_endpoint, unused_relay_endpoint = runner._free_endpoints(6)
    account_server_keys = zmq.curve_keypair()
    execution_server_keys = zmq.curve_keypair()
    coordinator_server_keys = zmq.curve_keypair()
    durable_server_keys = zmq.curve_keypair()
    telemetry_server_keys = zmq.curve_keypair()
    relay_keys = zmq.curve_keypair()
    strategy_keys = {index: zmq.curve_keypair() for index in range(runner._PER_ACCOUNT)}

    account_service = runner._account_service_info(0, account_endpoint, account_server_keys[0])
    execution_service = runner._execution_service_info(0, execution_endpoint, execution_server_keys[0])
    account_config_raw = runner._account_service_config(account_service, account_endpoint)
    execution_config_raw = runner._execution_service_config(execution_service, group_owner=True)
    owner_strategy_audience = tuple(
        {
            "index": index,
            "public_key": strategy_keys[index][0],
            "principal_ref": runner._principal_ref(index),
            "strategy_ref": runner._strategy_ref(index),
        }
        for index in range(runner._PER_ACCOUNT)
    )
    metadata = coordinator.build_n5_group_metadata()
    coordinator_info = coordinator.n5_coordinator_service_info(
        coordinator_endpoint, coordinator_server_keys[0], target_count=1
    )

    owner_record = None
    coordinator_record = None
    monitor_parent = None
    monitor_thread = None
    monitor_errors: list[BaseException] = []
    strategy_runtime: ServiceClientRuntime | None = None
    owner_ready: tuple[Any, ...] | None = None
    coordinator_ready: tuple[Any, ...] | None = None
    monitor_close_message: tuple[Any, ...] | None = None
    coordinator_close_message: tuple[Any, ...] | None = None
    owner_close_message: tuple[Any, ...] | None = None
    primary_error: BaseException | None = None
    cleanup_errors: list[str] = []
    expected_group_ids: list[str] = []
    expected_child_intents: set[str] = set()

    try:
        owner_record = _spawn(
            context,
            runner._owner_process,
            (
                0,
                account_config_raw,
                execution_config_raw,
                str(paths["registries"] / "account.sqlite3"),
                str(paths["registries"] / "execution.sqlite3"),
                str(paths["journals"] / "gateway.sqlite3"),
                str(paths["guard"]),
                str(paths["journals"] / "execution.sqlite3"),
                str(paths["checkpoints"]),
                account_server_keys,
                execution_server_keys,
                owner_strategy_audience,
                unused_relay_endpoint,
                metadata,
                reservation_scope_ref,
                (runner._N5_COORDINATOR_PRINCIPAL, runner._N5_COORDINATOR_SERVICE_ID),
                origin_run_id,
                coordinator_server_keys[0],
            ),
            "load01-n5-owner-a00",
        )
        owner_ready = _receive_tag(owner_record["pipe"], "ready", 90.0, label="N5 owner")
        assert owner_ready[1:4] == ("owner", 0, owner_record["pid"]), owner_ready[:4]
        assert owner_ready[6]["service_state"] == "started"
        assert owner_ready[6]["worker_ready"] is True
        assert owner_ready[6]["provider_connect_calls"] == 1
        assert owner_ready[6]["provider_read_calls"] == 1
        assert len(owner_ready) == 9 and isinstance(owner_ready[8], Mapping)
        route = dict(owner_ready[8])
        assert route["account_ref"] == account_ref
        assert route["strategy_index"] == 0
        assert route["strategy_ref"] == strategy_ref
        assert route["owner_endpoint"] == owner_ready[5]

        owner_services = {
            0: {
                **execution_service,
                "service_config": execution_config_raw,
                "endpoint": owner_ready[5],
                "server_public": execution_server_keys[0],
                "n5_group_route": route,
            }
        }
        coordinator_config_raw = coordinator.build_n5_coordinator_service_config(
            endpoint=coordinator_endpoint,
            server_security_ref=coordinator_info["security_ref"],
            owner_services=owner_services,
            target_count=1,
        )
        outbound_diagnostic_path = paths["coordinator"] / "outbound-call-diagnostic.json"
        coordinator_record = _spawn(
            context,
            coordinator.n5_coordinator_host_process,
            (
                coordinator_config_raw,
                owner_services,
                {0: route},
                metadata,
                str(paths["registries"] / "coordinator.sqlite3"),
                str(paths["coordinator"] / "groups.sqlite3"),
                str(paths["coordinator"] / "origin-runs.sqlite3"),
                coordinator_server_keys,
                {0: strategy_keys[0][0]},
                1,
                str(outbound_diagnostic_path),
            ),
            "load01-n5-coordinator",
        )
        coordinator_ready = _receive_tag(
            coordinator_record["pipe"], "ready", 90.0, label="N5 coordinator"
        )
        assert coordinator_ready[1] == coordinator_endpoint
        assert coordinator_ready[3] == coordinator_record["pid"]
        active_upstream = coordinator_ready[2]
        assert len(active_upstream) == 3
        assert {item[0] for item in active_upstream} == {
            "upstream.orders",
            "upstream.order_query",
            "upstream.account_reservations",
        }
        assert all(item[1] == account_ref for item in active_upstream)

        owner_record["pipe"].send(("arm-group",))
        armed = _receive_tag(owner_record["pipe"], "group-armed", 45.0, label="N5 owner arm")
        assert armed[1:3] == (0, owner_record["pid"])
        assert armed[3]["active"] is True
        assert armed[3]["account_ref"] == account_ref
        assert armed[3]["strategy_id"] == strategy_ref
        assert armed[3]["portfolio_ref"] == coordinator_info["strategy_bindings"][0]["portfolio_ref"]
        assert armed[3]["origin_strategy_run_id"] == origin_run_id
        assert armed[3]["entry_scope_ref"] == route["order_scope_ref"]
        assert isinstance(armed[3]["registration_id"], str) and armed[3]["registration_id"]
        assert isinstance(armed[3]["query_registration_id"], str) and armed[3]["query_registration_id"]
        assert isinstance(armed[3]["reservation_registration_id"], str) and armed[3]["reservation_registration_id"]

        durable_config = runner._monitor_service_config(
            service_id=runner._DURABLE_ID,
            scope_ref=runner._DURABLE_ROOT_SCOPE_REF,
            instance_id=runner._DURABLE_INSTANCE,
            listener_ref=runner._DURABLE_LISTENER_REF,
            endpoint=durable_endpoint,
            security_ref=runner._DURABLE_SECURITY_REF,
            role="durable_ingress",
        )
        telemetry_config = runner._monitor_service_config(
            service_id=runner._TELEMETRY_ID,
            scope_ref=runner._TELEMETRY_SCOPE_REF,
            instance_id=runner._TELEMETRY_INSTANCE,
            listener_ref=runner._TELEMETRY_LISTENER_REF,
            endpoint=telemetry_endpoint,
            security_ref=runner._TELEMETRY_SECURITY_REF,
            role="telemetry_ingress",
        )
        owner_monitor_audience = (
            {
                "public_key": relay_keys[0],
                "principal_ref": f"{runner._RELAY_PRINCIPAL_PREFIX}00",
                "actor_ref": f"{runner._RELAY_CLIENT_PREFIX}00",
                "scopes": owner_ready[7],
            },
        )
        telemetry_audience = tuple(
            (
                strategy_keys[index][0],
                runner._principal_ref(index),
                runner._strategy_ref(index),
                account_ref,
                runner._strategy_telemetry_scope_ref("N5", index, 0),
            )
            for index in range(runner._PER_ACCOUNT)
        )
        monitor_parent, monitor_worker = context.Pipe(duplex=True)
        monitor_thread = threading.Thread(
            target=_monitor_thread_target,
            args=(
                monitor_errors,
                runner._monitor_process,
                (
                    monitor_worker,
                    durable_config,
                    telemetry_config,
                    str(paths["monitor"] / "durable-registry.sqlite3"),
                    str(paths["monitor"] / "telemetry-registry.sqlite3"),
                    str(paths["monitor"] / "outbox.sqlite3"),
                    durable_server_keys,
                    telemetry_server_keys,
                    owner_monitor_audience,
                    telemetry_audience,
                ),
            ),
            name="load01-n5-monitor-hosts",
            daemon=False,
        )
        monitor_thread.start()
        monitor_ready = _monitor_ready(monitor_parent, monitor_thread, monitor_errors)
        assert monitor_ready[2] == os.getpid()
        assert monitor_ready[5:] == ("started", "started")

        relay_identity = (
            owner_monitor_audience[0]["principal_ref"],
            owner_monitor_audience[0]["actor_ref"],
            relay_keys[0],
            relay_keys[1],
        )
        owner_record["pipe"].send(
            ("connect-monitor", durable_endpoint, durable_server_keys[0], relay_identity)
        )
        relay_ready = _receive_tag(owner_record["pipe"], "relay-ready", 45.0, label="N5 owner relay")
        assert relay_ready[1:4] == (0, owner_record["pid"], len(owner_ready[7]))

        coordinator_route = coordinator_info["strategy_bindings"][0]
        strategy_scope = WireScope(
            runner._DOMAIN,
            account_refs=(account_ref,),
            strategy_ref=strategy_ref,
            portfolio_ref=coordinator_route["portfolio_ref"],
        )
        ordinary_scope = WireScope(
            runner._DOMAIN,
            account_ref=account_ref,
            strategy_ref=strategy_ref,
        )
        scope_by_ref = {
            execution_service["order_scope_ref"]: ordinary_scope,
            execution_service["query_scope_ref"]: ordinary_scope,
            coordinator_route["scope_ref"]: strategy_scope,
        }
        peer_by_service = {
            execution_service["service_id"]: execution_service["peer"],
            coordinator_info["service_id"]: coordinator_info["peer"],
        }
        client_public, client_secret = strategy_keys[0]
        raw_client_config = _runtime_config(
            owner_service=execution_service,
            coordinator_service=coordinator_info,
            account_ref=account_ref,
            strategy_ref=strategy_ref,
        )
        strategy_runtime = ServiceClientRuntime(
            parse_client_config(raw_client_config),
            identity=WireIdentity(
                WireRole.STRATEGY_CLIENT, runner._principal_ref(0), strategy_ref
            ),
            scope_resolver=lambda scope_ref: scope_by_ref[scope_ref],
            peer_resolver=lambda service_id: peer_by_service[service_id],
            transport_factory=ZmqTransportFactory(
                {
                    execution_service["security_ref"]: ClientCurveKeys(
                        execution_server_keys[0], client_public, client_secret
                    ),
                    coordinator_info["security_ref"]: ClientCurveKeys(
                        coordinator_server_keys[0], client_public, client_secret
                    ),
                }
            ),
            client_session_id="load01-n5-spawn-smoke-session",
            run_id=origin_run_id,
            requested_operations={
                "broker.orders": frozenset({"orders.submit"}),
                "broker.order_query": frozenset({"orders.query"}),
                "broker.order_groups": frozenset({"groups.submit", "groups.query"}),
            },
        )
        started = strategy_runtime.start()
        assert started.degraded_count == 0
        assert len(started.bindings) == 3
        assert all(binding.active for binding in strategy_runtime.bindings)
        owner_client_bindings = tuple(
            binding
            for binding in strategy_runtime.bindings
            if binding.service_id == execution_service["service_id"]
        )
        coordinator_client_bindings = tuple(
            binding
            for binding in strategy_runtime.bindings
            if binding.service_id == coordinator_info["service_id"]
        )
        assert {binding.mount for binding in owner_client_bindings} == {
            "broker.orders",
            "broker.order_query",
        }
        assert len(owner_client_bindings) == 2
        assert len(coordinator_client_bindings) == 1
        # This one-client smoke observes 2 ordinary + 3 activated group
        # contexts. The 23-row profile expectation is for the complete N5
        # account (20 ordinary registrations plus three group-owner routes).
        activated_registration_ids = {
            armed[3]["registration_id"],
            armed[3]["query_registration_id"],
            armed[3]["reservation_registration_id"],
        }
        assert len(activated_registration_ids) == 3
        assert len(owner_client_bindings) + len(activated_registration_ids) == 5

        ordinary_id = "load01-n5-spawn-smoke-ordinary-0001"
        ordinary_spec = N5_LEG_SPECS[0]
        ordinary_intent = OrderIntent.limit(
            intent_id=ordinary_id,
            scope=ExecutionScope(
                "fixture", "simulation", account_ref, strategy_ref, runner._EXECUTION_DAY
            ),
            signal_id=f"{ordinary_id}-signal",
            instrument=ordinary_spec.instrument,
            side=ordinary_spec.side,
            quantity=ordinary_spec.quantity,
            price=ordinary_spec.price,
            time_in_force=TimeInForce.GTC,
            metadata_version=next(
                row.metadata_version for row in metadata if row.instrument == ordinary_spec.instrument
            ),
            deadline_ns=time.time_ns() + 60_000_000_000,
            tags={},
        )
        ordinary_payload = ordinary_intent.to_payload()
        ordinary_payload.pop("scope")
        ordinary_submit = strategy_runtime.call(
            "broker.orders",
            "orders.submit",
            ordinary_payload,
            account_ref=account_ref,
            scope_ref=execution_service["order_scope_ref"],
        )
        assert ordinary_submit["ok"] is True and ordinary_submit["code"] == "OK", ordinary_submit
        assert ordinary_submit["result"]["found"] is True
        ordinary_query = strategy_runtime.call(
            "broker.order_query",
            "orders.query",
            {"intent_id": ordinary_id},
            account_ref=account_ref,
            scope_ref=execution_service["query_scope_ref"],
        )
        assert ordinary_query["ok"] is True and ordinary_query["code"] == "OK", ordinary_query
        assert ordinary_query["result"]["found"] is True
        assert ordinary_query["result"]["record"]["intent_id"] == ordinary_id
        assert ordinary_query["result"]["record"]["state"] == ExecutionState.ACKED.value

        for cycle in (1, 3, 5, 7):
            group_id = f"load01-n5-group-a00-s000-c{cycle:07d}"
            body = build_n5_group_request(
                group_id=group_id,
                account_ref=account_ref,
                scope=ExecutionScope(
                    "fixture", "simulation", account_ref, strategy_ref, runner._EXECUTION_DAY
                ),
                metadata=metadata,
                deadline_ns=time.time_ns() + 60_000_000_000,
            )
            submitted = submit_n5_group(
                strategy_runtime,
                profile,
                strategy_index=0,
                cycle=cycle,
                request=body,
                account_ref=account_ref,
                portfolio_ref=coordinator_route["portfolio_ref"],
                scope_ref=coordinator_route["scope_ref"],
            )
            assert submitted["ok"] is True and submitted["code"] == "OK", submitted
            queried = query_n5_group(
                strategy_runtime,
                profile,
                strategy_index=0,
                cycle=cycle,
                group_id=group_id,
                portfolio_ref=coordinator_route["portfolio_ref"],
                scope_ref=coordinator_route["scope_ref"],
            )
            assert queried["ok"] is True and queried["code"] == "OK", queried
            snapshot = GroupOrderSnapshot.from_payload(
                    runner._plain_rpc_json(queried["result"]["snapshot"])
                )
            assert snapshot.state is GroupOrderState.PAIRED
            assert snapshot.residual_vector == (Decimal("0"),) * 3
            assert snapshot.residual_base_units == Decimal("0")
            assert [leg.leg_id for leg in snapshot.legs] == list(N5_LEG_IDS)
            assert len(snapshot.legs) == 3
            assert all(leg.child_status is not None for leg in snapshot.legs)
            assert all(leg.child_status.state is ExecutionState.FILLED for leg in snapshot.legs)
            expected_group_ids.append(group_id)
            expected_child_intents.update(
                group_leg_intent_id(group_id, leg_id) for leg_id in N5_LEG_IDS
            )

        assert len(expected_group_ids) == 4
        assert len(expected_child_intents) == 12
        assert strategy_runtime.healthy is True

    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        def attempt(label: str, cleanup) -> None:
            if cleanup is None:
                return
            try:
                cleanup()
            except BaseException as exc:
                cleanup_errors.append(f"{label}:{type(exc).__name__}:{exc}")

        attempt("strategy-runtime-close", strategy_runtime.close if strategy_runtime else None)

        if coordinator_record is not None:
            try:
                if coordinator_close_message is None:
                    coordinator_record["pipe"].send(("close",))
                    coordinator_close_message = _receive_tag(
                        coordinator_record["pipe"], "closed", _CHILD_CLOSE_TIMEOUT_SECONDS,
                        label="N5 coordinator close",
                    )
                assert coordinator_close_message[1] == coordinator_record["pid"]
                assert coordinator_close_message[2:] == ("closed", "closed")
            except BaseException as exc:
                cleanup_errors.append(f"coordinator-close:{type(exc).__name__}:{exc}")
            try:
                _close_spawned(coordinator_record, timeout=_CHILD_CLOSE_TIMEOUT_SECONDS)
            except BaseException as exc:
                cleanup_errors.append(f"coordinator-reap:{type(exc).__name__}:{exc}")

        if owner_record is not None:
            try:
                if owner_close_message is None:
                    owner_record["pipe"].send(("close",))
                    owner_close_message = _receive_tag(
                        owner_record["pipe"], "closed", _CHILD_CLOSE_TIMEOUT_SECONDS,
                        label="N5 owner close",
                    )
                if owner_close_message is not None:
                    assert owner_close_message[1:3] == (0, owner_record["pid"])
                    assert owner_close_message[3:10] == (
                        "closed", (), "closed", (), "closed", 0, 0
                    ), owner_close_message[3:10]
            except BaseException as exc:
                cleanup_errors.append(f"owner-close:{type(exc).__name__}:{exc}")
            try:
                _close_spawned(owner_record, timeout=_CHILD_CLOSE_TIMEOUT_SECONDS)
            except BaseException as exc:
                cleanup_errors.append(f"owner-reap:{type(exc).__name__}:{exc}")

        if monitor_thread is not None and monitor_parent is not None:
            try:
                monitor_parent.send(("close",))
                monitor_close_message = _receive_tag(
                    monitor_parent, "closed", _CHILD_CLOSE_TIMEOUT_SECONDS,
                    label="Monitor ServiceHosts close",
                )
                assert monitor_close_message[1] == os.getpid()
                assert monitor_close_message[2:6] == ("closed", (), "closed", ())
                monitor_thread.join(_CHILD_CLOSE_TIMEOUT_SECONDS)
                assert not monitor_thread.is_alive(), "Monitor ServiceHost thread remained alive"
                assert not monitor_errors, f"Monitor ServiceHost thread errors: {monitor_errors!r}"
            except BaseException as exc:
                cleanup_errors.append(f"monitor-close:{type(exc).__name__}:{exc}")
                try:
                    monitor_parent.close()
                except BaseException as close_exc:
                    cleanup_errors.append(
                        f"monitor-control-close:{type(close_exc).__name__}:{close_exc}"
                    )
                try:
                    monitor_thread.join(_CHILD_CLOSE_TIMEOUT_SECONDS)
                    if monitor_thread.is_alive():
                        cleanup_errors.append("monitor-cleanup_uncertain:thread_remained_alive")
                    elif monitor_errors:
                        cleanup_errors.append(f"monitor-thread-errors:{monitor_errors!r}")
                except BaseException as join_exc:
                    cleanup_errors.append(f"monitor-reap:{type(join_exc).__name__}:{join_exc}")
            finally:
                try:
                    monitor_parent.close()
                except BaseException as exc:
                    cleanup_errors.append(f"monitor-parent-close:{type(exc).__name__}:{exc}")

        if cleanup_errors:
            if primary_error is not None:
                for item in cleanup_errors:
                    primary_error.add_note(f"cleanup also failed: {item}")
            else:
                raise AssertionError("N5 spawn smoke normal cleanup failed: " + "; ".join(cleanup_errors))

    assert strategy_runtime is not None and strategy_runtime.state == "closed"
    assert owner_close_message is not None and coordinator_close_message is not None
    owner_facts = owner_close_message[17]
    assert owner_facts["normal_drain_completed"] is True
    assert owner_facts["execution_host"]["state"] == "closed"
    assert owner_facts["execution_host"]["cleanup_failures"] == []
    assert owner_facts["account_host"]["state"] == "closed"
    assert owner_facts["account_host"]["cleanup_failures"] == []
    assert owner_facts["account_worker"]["state"] == "closed"
    assert owner_facts["account_worker"]["provider_close_calls"] == 1
    assert owner_facts["relay_runtime"]["state"] == "closed"
    assert owner_facts["relay_runtime"]["cleanup_failures"] == 0
    assert owner_facts["n5_group_runtime"]["state"] == "closed"
    assert owner_close_message[10] == 1

    group_runtime = owner_facts["n5_group_runtime"]
    assert group_runtime["account_ref"] == account_ref
    assert group_runtime["strategy_ref"] == strategy_ref
    assert group_runtime["strategy_index"] == 0
    assert group_runtime["activation_receipt"]["registration_id"] in {
        armed[3]["registration_id"],
        armed[3]["query_registration_id"],
        armed[3]["reservation_registration_id"],
    }
    provider_ledger = group_runtime["provider_ledger"]
    assert provider_ledger["source"] == "synthetic-feed-sqlite-provider-ledger"
    assert provider_ledger["make_order_write_count"] == 12
    assert provider_ledger["provider_order_count"] == 12
    assert provider_ledger["unique_client_order_id_count"] == 12
    assert provider_ledger["duplicate_client_order_id_count"] == 0
    assert provider_ledger["write_without_order_record_count"] == 0
    assert provider_ledger["order_record_without_write_count"] == 0
    assert provider_ledger["order_status_counts"] == {"filled": 12}
    assert provider_ledger["unjoined_write_count"] == 0
    assert provider_ledger["unjoined_query_count"] == 0
    joins = group_runtime["intent_order_join"]
    assert len(joins) == 12 and group_runtime["joined_intent_order_count"] == 12
    assert {row["intent_id"] for row in joins} == expected_child_intents
    assert {row["group_id"] for row in joins} == set(expected_group_ids)
    assert all(row["join_state"] == "joined" for row in joins)
    assert all(row["provider_write_count"] == 1 for row in joins)
    assert all(row["provider_order_row_present"] is True for row in joins)
    assert all(row["order_status"] == "filled" for row in joins)
    assert all(row["order_query_count"] >= 1 for row in joins)
    assert all(row["fill_query_count"] >= 1 for row in joins)
    client_order_ids = {row["client_order_id"] for row in joins}
    assert len(client_order_ids) == 12
    writes = provider_ledger["writes_by_client_order_id"]
    assert len(writes) == 12
    assert all(row["write_count"] == 1 for row in writes)
    assert all(row["order_status"] == "filled" for row in writes)

    risk = group_runtime["risk_reservations"]
    assert risk["group_count"] == 4
    assert risk["leg_count"] == 12
    assert risk["unique_intent_id_count"] == 12
    assert risk["duplicate_intent_id_count"] == 0
    assert risk["claimed_leg_count"] == 12
    assert risk["resolved_leg_count"] == 12
    assert risk["leg_state_counts"] == {"settled": 12}
    assert {row["group_id"] for row in risk["groups"]} == set(expected_group_ids)
    assert all(
        leg["state"] == "settled" and leg["claimed"] and leg["resolved"]
        for group in risk["groups"]
        for leg in group["legs"]
    )

    current_children = {item.pid for item in multiprocessing.active_children()}
    assert current_children - active_before == set(), current_children - active_before
