"""One real ordinary U0 strategy process against the LOAD01 local services.

This is a bounded integration smoke, not a 100-strategy LOAD01 acceptance run.
It keeps the production U0 profile and worker unchanged, starts one real market
host, one real account/execution owner and the real Monitor hosts, and uses the
existing worker's submit/cancel cycle and normal shutdown.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt"
    or not all(
        os.environ.get(name)
        for name in (
            "BT_API_LOAD01_SOURCE_ROOT",
            "BT_API_LOAD01_EXPORT_PINS",
            "BT_API_LOAD01_RUNNER_OVERLAY_SHA256",
        )
    ),
    reason="requires Windows spawn and the fixed-source LOAD01 integration environment",
)

import zmq

from tests.unit import test_load01_hundred_strategy_30min as runner
from tests.unit._load01_profiles import load01_profile
from tests.unit.test_load01_n5_spawn_smoke import (
    _CHILD_CLOSE_TIMEOUT_SECONDS,
    _ONE_MESSAGE_TIMEOUT_SECONDS,
    _close_spawned,
    _monitor_ready,
    _monitor_thread_target,
    _receive_tag,
    _spawn,
)

_TERMINAL_WATERMARK_TIMEOUT_SECONDS = 10.0


def _receive_strategy_progress(connection, timeout: float) -> tuple[Any, ...]:
    deadline = time.monotonic() + timeout
    seen: list[tuple[Any, ...]] = []
    while time.monotonic() < deadline:
        if not connection.poll(min(0.1, max(0.0, deadline - time.monotonic()))):
            continue
        message = connection.recv()
        if type(message) is not tuple or not message:
            raise AssertionError(f"U0 strategy sent malformed control data: {message!r}")
        if message[0] in {"progress", "rpc-diagnostic"}:
            seen.append(message)
            if (
                message[0] == "progress"
                and message[1] == 0
                and type(message[2]) is dict
                and message[2].get("order_cycles", 0) >= 1
                and message[2].get("market_events", 0) > 0
            ):
                return message
            continue
        if message[0] == "error":
            raise AssertionError(f"U0 strategy failed: {message!r}; prior={seen!r}")
        raise AssertionError(f"U0 strategy sent unexpected {message[0]!r}; prior={seen!r}")
    raise TimeoutError(f"U0 strategy did not report an order and market event: {seen!r}")


def _receive_strategy_closed(connection, timeout: float) -> tuple[Any, ...]:
    deadline = time.monotonic() + timeout
    seen: list[tuple[Any, ...]] = []
    while time.monotonic() < deadline:
        if not connection.poll(min(0.1, max(0.0, deadline - time.monotonic()))):
            continue
        message = connection.recv()
        if type(message) is not tuple or not message:
            raise AssertionError(f"U0 strategy sent malformed control data: {message!r}")
        if message[0] in {"progress", "rpc-diagnostic"}:
            seen.append(message)
            continue
        if message[0] == "error":
            raise AssertionError(f"U0 strategy failed during close: {message!r}; prior={seen!r}")
        if message[0] != "closed":
            raise AssertionError(f"U0 strategy sent unexpected {message[0]!r}; prior={seen!r}")
        return message
    raise TimeoutError(f"U0 strategy did not close normally: {seen!r}")


def _capture_terminal_market_watermark(
    market_record: dict[str, Any], expected_stream_ids: tuple[str, ...], timeout: float
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    if len(expected_stream_ids) != 20 or len(set(expected_stream_ids)) != 20:
        raise AssertionError("U0 terminal watermark did not cover exactly 20 unique topics")
    token = time.monotonic_ns()
    market_record["pipe"].send(("terminal-watermark", token))
    message = _receive_tag(
        market_record["pipe"],
        "terminal-watermark",
        timeout,
        label="U0 market terminal watermark",
    )
    status, watermark = runner._validate_load01_terminal_market_message(
        message,
        expected_token=token,
        expected_stream_ids=expected_stream_ids,
    )
    captured_ns = watermark["captured_monotonic_ns"]
    if captured_ns < token or captured_ns > time.monotonic_ns():
        raise AssertionError("U0 terminal watermark capture time was outside the request window")
    if status["source_publishing_stopped"] is not True:
        raise AssertionError("U0 market publisher was not stopped before strategy close")
    if set(status["source_sequences"]) != set(expected_stream_ids):
        raise AssertionError("U0 terminal provider watermark omitted a topic")
    if set(watermark["watermarks"]) != set(expected_stream_ids):
        raise AssertionError("U0 terminal hub watermark omitted a topic")
    return token, status, watermark


def _receive_startup_maintenance(
    connection, baseline: dict[str, Any], expected_pid: int
) -> dict[str, Any]:
    """Use the worker's existing common-load-barrier maintenance path."""

    deadline = time.monotonic() + 25.0
    wave = 0
    while time.monotonic() < deadline:
        wave += 1
        connection.send(("startup-barrier-snapshot", wave))
        message = _receive_tag(
            connection,
            "startup-barrier-snapshot",
            _ONE_MESSAGE_TIMEOUT_SECONDS,
            label="U0 strategy startup maintenance",
        )
        if message[1:4] != (0, expected_pid, wave):
            raise AssertionError(f"U0 startup maintenance identity mismatch: {message!r}")
        maintenance = message[5]
        if (
            type(maintenance) is dict
            and maintenance.get("maintenance_delay_checks", 0)
            > baseline.get("maintenance_delay_checks", 0)
        ):
            return maintenance
        time.sleep(0.05)
    raise TimeoutError("U0 worker did not execute its ordinary lease-maintenance check")


def test_u0_single_strategy_real_spawn_submit_cancel_normal_close(tmp_path: Path, monkeypatch) -> None:
    """Run one unmodified U0 worker through a real submit/cancel cycle and close."""

    source_root = runner._fixed_source_root()
    runner_path = Path(runner.__file__).resolve()
    assert runner_path.is_relative_to(source_root)
    runner_relative = runner_path.relative_to(source_root).as_posix()
    runner_overlay_sha256 = os.environ["BT_API_LOAD01_RUNNER_OVERLAY_SHA256"]
    assert runner_overlay_sha256 == runner._sha256(runner_path)
    allowed_overlays = {
        runner_relative: runner_overlay_sha256,
        "tests/unit/test_load01_runner_metrics.py": (
            "5a8f902fc8873266527d7092313ff399424bf4343c8fb8d063dc8ab8ccdd023a"
        ),
        "tests/unit/_load01_profiles.py": runner._N6_PROFILE_OVERLAY_SHA256,
        "tests/unit/test_load01_profiles.py": runner._N6_PROFILE_TEST_OVERLAY_SHA256,
    }
    source_manifest_path = runner._write_source_run_manifest(
        source_root,
        tmp_path,
        profile_id="U0",
        allowed_overlays=allowed_overlays,
    )
    monkeypatch.setenv("BT_API_LOAD01_SOURCE_ROOT", str(source_root))
    monkeypatch.setenv("BT_API_LOAD01_SOURCE_MANIFEST", str(source_manifest_path))
    checked = runner._check_fixed_source_manifest(source_manifest_path, profile_id="U0")
    assert checked["load_runner"]["sha256"] == runner._sha256(runner_path)
    manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    assert manifest["parent_commit"] == runner._FIXED_PARENT
    assert {item["path"]: item["commit"] for item in manifest["package_gitlinks"]} == runner._FIXED_PACKAGE_GITLINKS
    assert manifest["files"][runner_relative] == runner_overlay_sha256

    profile = load01_profile("U0")
    assert profile.accounts_for_strategy(0) == (0,)
    assert profile.expected_bindings_per_strategy(0) > 0
    context = multiprocessing.get_context("spawn")
    active_before = {child.pid for child in multiprocessing.active_children()}
    account_ref = runner._ACCOUNT_REFS[0]
    strategy_ref = runner._strategy_ref(0)
    root = tmp_path / "load01-u0-one-strategy"
    paths = {
        name: root / name
        for name in (
            "guard",
            "registries",
            "journals",
            "checkpoints",
            "monitor",
            "market",
        )
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)

    (
        account_endpoint,
        execution_endpoint,
        market_rpc_endpoint,
        market_pub_endpoint,
        durable_endpoint,
        telemetry_endpoint,
        unused_relay_endpoint,
    ) = runner._free_endpoints(7)
    account_server_keys = zmq.curve_keypair()
    execution_server_keys = zmq.curve_keypair()
    market_server_keys = zmq.curve_keypair()
    durable_server_keys = zmq.curve_keypair()
    telemetry_server_keys = zmq.curve_keypair()
    relay_keys = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()

    account_service = runner._account_service_info(0, account_endpoint, account_server_keys[0])
    execution_service = runner._execution_service_info(
        0, execution_endpoint, execution_server_keys[0]
    )
    account_config_raw = runner._account_service_config(account_service, account_endpoint)
    execution_config_raw = runner._execution_service_config(execution_service, group_owner=False)
    owner_strategy_audience = (
        {
            "index": 0,
            "public_key": strategy_public,
            "principal_ref": runner._principal_ref(0),
            "strategy_ref": strategy_ref,
        },
    )
    market_keys = runner._market_keys("U0")
    market_audience = (
        (strategy_public, runner._principal_ref(0), strategy_ref, account_ref),
    )
    market_config = runner._market_service_config(market_rpc_endpoint, market_pub_endpoint)
    market_stop_event = context.Event()
    load_event = context.Event()
    load_start_value = context.Value("d", 0.0)
    registration_event = context.Event()
    strategy_stop_event = context.Event()
    abort_event = context.Event()

    market_record = None
    owner_record = None
    strategy_record = None
    monitor_parent = None
    monitor_thread = None
    monitor_errors: list[BaseException] = []
    strategy_closed: tuple[Any, ...] | None = None
    market_ready: tuple[Any, ...] | None = None
    owner_ready: tuple[Any, ...] | None = None
    monitor_ready: tuple[Any, ...] | None = None
    owner_close: tuple[Any, ...] | None = None
    market_close: tuple[Any, ...] | None = None
    monitor_close: tuple[Any, ...] | None = None
    owner_connected = False
    terminal_watermark_request_sent = False
    terminal_market_status: dict[str, Any] | None = None
    terminal_watermark: dict[str, Any] | None = None
    primary_error: BaseException | None = None
    cleanup_errors: list[str] = []

    try:
        market_record = _spawn(
            context,
            runner._market_host_process,
            (
                market_config,
                str(paths["market"] / "registry.sqlite3"),
                market_server_keys,
                market_audience,
                market_keys,
                load_event,
                load_start_value,
                market_stop_event,
                "U0",
            ),
            "load01-u0-market-host",
        )
        market_ready = _receive_tag(
            market_record["pipe"], "ready", 60.0, label="U0 market host"
        )
        assert market_ready[1:3] == ("market", market_record["pid"])
        assert market_ready[3] and market_ready[4]
        market_service = {
            "service_id": runner._MARKET_ID,
            "instance_id": runner._MARKET_INSTANCE,
            "scope_ref": runner._MARKET_SCOPE_REF,
            "peer": runner.WireIdentity(runner.WireRole.SERVICE, runner._MARKET_PRINCIPAL, runner._MARKET_ID),
            "endpoint": market_ready[3],
            "market_endpoint": market_ready[4],
        }

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
            ),
            "load01-u0-owner-a00",
        )
        owner_ready = _receive_tag(owner_record["pipe"], "ready", 90.0, label="U0 owner")
        assert owner_ready[1:4] == ("owner", 0, owner_record["pid"])
        assert owner_ready[6]["service_state"] == "started"
        assert owner_ready[6]["worker_ready"] is True
        assert owner_ready[6]["provider_connect_calls"] == 1
        assert owner_ready[6]["provider_read_calls"] == 1
        assert owner_ready[6]["provider_close_calls"] == 0
        account_service["endpoint"] = owner_ready[4]
        execution_service["endpoint"] = owner_ready[5]

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
        telemetry_audience = (
            (
                strategy_public,
                runner._principal_ref(0),
                strategy_ref,
                account_ref,
                runner._strategy_telemetry_scope_ref("U0", 0, 0),
            ),
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
            name="load01-u0-monitor-hosts",
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
        relay_ready = _receive_tag(
            owner_record["pipe"], "relay-ready", 45.0, label="U0 owner Monitor relay"
        )
        assert relay_ready[1:4] == (0, owner_record["pid"], len(owner_ready[7]))
        owner_connected = True

        telemetry_service = {
            "service_id": runner._TELEMETRY_ID,
            "instance_id": runner._TELEMETRY_INSTANCE,
            "scope_ref": runner._TELEMETRY_SCOPE_REF,
            "peer": runner.WireIdentity(
                runner.WireRole.SERVICE, runner._TELEMETRY_PRINCIPAL, runner._TELEMETRY_ID
            ),
            "endpoint": monitor_ready[4],
        }
        service_records = [market_service, telemetry_service, account_service, execution_service]
        raw_strategy_config = runner._strategy_config(
            0,
            {0: account_service},
            {0: execution_service},
            market_ready[3],
            market_ready[4],
            monitor_ready[4],
            account_index=0,
            profile_id="U0",
        )
        security_material = {
            runner._MARKET_SECURITY_REF: (
                market_server_keys[0],
                strategy_public,
                strategy_secret,
            ),
            runner._TELEMETRY_SECURITY_REF: (
                telemetry_server_keys[0],
                strategy_public,
                strategy_secret,
            ),
            account_service["security_ref"]: (
                account_server_keys[0],
                strategy_public,
                strategy_secret,
            ),
            execution_service["security_ref"]: (
                execution_server_keys[0],
                strategy_public,
                strategy_secret,
            ),
        }
        strategy_record = _spawn(
            context,
            runner._strategy_process,
            (
                0,
                raw_strategy_config,
                {0: account_service},
                {0: execution_service},
                service_records,
                security_material,
                market_keys,
                registration_event,
                load_event,
                load_start_value,
                strategy_stop_event,
                abort_event,
                "pilot",
                "U0",
                None,
                None,
            ),
            "load01-u0-strategy-000",
        )
        loaded = _receive_tag(
            strategy_record["pipe"], "loaded", 60.0, label="U0 strategy source guard"
        )
        assert loaded == ("loaded", 0, strategy_record["pid"])
        registration_event.set()
        ready = _receive_tag(
            strategy_record["pipe"], "ready", 90.0, label="U0 strategy runtime"
        )
        assert ready[1:5] == (0, strategy_record["pid"], strategy_ref, account_ref)
        assert ready[7] == profile.expected_bindings_per_strategy(0)
        assert ready[8] == 0
        assert ready[11]["maintenance"]["rpc_boundary_checks"] > 0
        assert ready[11]["maintenance"]["maintenance_failure_count"] == 0

        barrier_maintenance = _receive_startup_maintenance(
            strategy_record["pipe"], ready[11]["maintenance"], strategy_record["pid"]
        )
        assert barrier_maintenance["maintenance_failure_count"] == 0
        load_start_value.value = time.monotonic() + 0.2
        load_event.set()
        progress = _receive_strategy_progress(strategy_record["pipe"], 45.0)
        strategy_stats = progress[2]
        assert strategy_stats["index"] == 0
        assert strategy_stats["market_events"] > 0
        assert strategy_stats["order_cycles"] >= 1
        market_stop_event.set()
        strategy_stop_event.set()
        expected_stream_ids = tuple(key.stream_id for key in market_keys)
        terminal_watermark_request_sent = True
        _terminal_token, terminal_market_status, terminal_watermark = (
            _capture_terminal_market_watermark(
                market_record, expected_stream_ids, _TERMINAL_WATERMARK_TIMEOUT_SECONDS
            )
        )
        strategy_record["pipe"].send(("close",))
        strategy_closed = _receive_strategy_closed(
            strategy_record["pipe"], _CHILD_CLOSE_TIMEOUT_SECONDS
        )
        assert strategy_closed[1:7] == (
            0,
            strategy_record["pid"],
            True,
            0,
            0,
            0,
        )
        final_stats = strategy_closed[7]
        assert final_stats["order_cycles"] >= 1
        assert final_stats["market_events"] > 0
        assert final_stats["runtime_state"] == "closed"
        assert final_stats["runtime_cleanup_failures"] == 0
        assert final_stats["runtime_unregister_uncertainties"] == 0
        assert final_stats["active_bindings"] == 0
        child_trace = strategy_closed[8]
        assert type(child_trace) is dict and child_trace["process_role"] == "strategy"
        child_events = child_trace["events"]
        child_submit_ids = {
            event.get("correlation_id")
            for event in child_events
            if event.get("operation") == "orders.submit"
            and event.get("event") == "client_send"
        }
        child_cancel_ids = {
            event.get("correlation_id")
            for event in child_events
            if event.get("operation") == "orders.cancel"
            and event.get("event") == "client_send"
        }
        child_intent_id = f"load01-00-00-0000001"
        assert child_intent_id in child_submit_ids
        assert f"cancel-{child_intent_id}" in child_cancel_ids
        _close_spawned(strategy_record, timeout=_CHILD_CLOSE_TIMEOUT_SECONDS)
        strategy_record = None

        owner_record["pipe"].send(("status",))
        owner_status = _receive_tag(
            owner_record["pipe"], "status", 30.0, label="U0 synthetic owner status"
        )
        assert owner_status[1] == 0
        assert owner_status[3] >= final_stats["order_cycles"]
        assert owner_status[4] >= final_stats["order_cycles"]
        assert owner_status[6:9] == ("registered", 0, 0)

        owner_record["pipe"].send(("close",))
        owner_close = _receive_tag(
            owner_record["pipe"], "closed", _CHILD_CLOSE_TIMEOUT_SECONDS, label="U0 owner close"
        )
        assert owner_close[1:3] == (0, owner_record["pid"])
        assert owner_close[3:10] == ("closed", (), "closed", (), "closed", 0, 0)
        assert owner_close[10] >= final_stats["order_cycles"]
        assert owner_close[11] >= final_stats["order_cycles"]
        assert owner_close[12] > 0
        assert owner_close[13]["provider_close_calls"] == 1
        assert owner_close[17]["normal_drain_completed"] is True
        assert owner_close[17]["relay_runtime"]["binding_count"] == 0
        _close_spawned(owner_record, timeout=_CHILD_CLOSE_TIMEOUT_SECONDS)
        owner_record = None

        market_record["pipe"].send(("close",))
        market_close = _receive_tag(
            market_record["pipe"], "closed", _CHILD_CLOSE_TIMEOUT_SECONDS, label="U0 market close"
        )
        assert market_close[1:3] == (market_record["pid"], "closed")
        assert type(market_close[3]) is tuple and market_close[3] == ()
        assert market_close[4]["source_publishing_stopped"] is True
        assert any(value > 0 for value in market_close[4]["source_sequences"].values())
        assert terminal_market_status is not None and terminal_watermark is not None
        final_market_watermark = runner._validate_load01_terminal_market_status(
            market_close[4], expected_stream_ids
        )
        assert final_market_watermark["watermarks"] == terminal_watermark["watermarks"]
        assert market_close[4]["source_sequences"] == terminal_market_status["source_sequences"]
        assert final_market_watermark["captured_monotonic_ns"] >= terminal_watermark[
            "captured_monotonic_ns"
        ]
        _close_spawned(market_record, timeout=_CHILD_CLOSE_TIMEOUT_SECONDS)
        market_record = None

        monitor_parent.send(("status",))
        monitor_status = _receive_tag(
            monitor_parent, "status", 30.0, label="U0 Monitor durable status"
        )
        assert monitor_status[1]["durable_event_rows"] > 0
        assert monitor_status[1]["durable_scope_count"] == 1

        monitor_parent.send(("close",))
        monitor_close = _receive_tag(
            monitor_parent,
            "closed",
            _CHILD_CLOSE_TIMEOUT_SECONDS,
            label="U0 Monitor ServiceHosts close",
        )
        assert monitor_close[1] == os.getpid()
        assert monitor_close[2:6] == ("closed", (), "closed", ())
        assert monitor_close[6]["durable_event_rows"] > 0
        monitor_thread.join(_CHILD_CLOSE_TIMEOUT_SECONDS)
        assert not monitor_thread.is_alive()
        assert not monitor_errors, f"Monitor thread errors: {monitor_errors!r}"
        monitor_parent.close()
        monitor_parent = None

        assert {child.pid for child in multiprocessing.active_children()} == active_before

    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        market_stop_event.set()
        strategy_stop_event.set()
        abort_event.set()
        registration_event.set()
        load_event.set()

        def attempt(label: str, cleanup) -> None:
            if cleanup is None:
                return
            try:
                cleanup()
            except BaseException as exc:
                cleanup_errors.append(f"{label}:{type(exc).__name__}:{exc}")

        if (
            market_record is not None
            and market_ready is not None
            and not terminal_watermark_request_sent
            and market_close is None
        ):
            terminal_watermark_request_sent = True
            try:
                expected_stream_ids = tuple(key.stream_id for key in market_keys)
                _token, terminal_market_status, terminal_watermark = (
                    _capture_terminal_market_watermark(
                        market_record, expected_stream_ids, _TERMINAL_WATERMARK_TIMEOUT_SECONDS
                    )
                )
            except BaseException as exc:
                cleanup_errors.append(f"market-terminal-watermark:{type(exc).__name__}:{exc}")

        if strategy_record is not None:
            try:
                strategy_record["pipe"].send(("close",))
                if strategy_closed is None:
                    strategy_closed = _receive_strategy_closed(
                        strategy_record["pipe"], _CHILD_CLOSE_TIMEOUT_SECONDS
                    )
            except BaseException as exc:
                cleanup_errors.append(f"strategy-close:{type(exc).__name__}:{exc}")
            attempt(
                "strategy-reap",
                lambda: _close_spawned(
                    strategy_record, timeout=_CHILD_CLOSE_TIMEOUT_SECONDS
                ),
            )
        if market_record is not None:
            market_stop_event.set()
            try:
                market_record["pipe"].send(("close",))
                if market_close is None:
                    market_close = _receive_tag(
                        market_record["pipe"],
                        "closed",
                        _CHILD_CLOSE_TIMEOUT_SECONDS,
                        label="U0 market cleanup",
                    )
            except BaseException as exc:
                cleanup_errors.append(f"market-close:{type(exc).__name__}:{exc}")
            attempt(
                "market-reap",
                lambda: _close_spawned(market_record, timeout=_CHILD_CLOSE_TIMEOUT_SECONDS),
            )
        if owner_record is not None:
            try:
                if not owner_connected and monitor_ready is not None:
                    relay_identity = (
                        f"{runner._RELAY_PRINCIPAL_PREFIX}00",
                        f"{runner._RELAY_CLIENT_PREFIX}00",
                        relay_keys[0],
                        relay_keys[1],
                    )
                    owner_record["pipe"].send(
                        ("connect-monitor", durable_endpoint, durable_server_keys[0], relay_identity)
                    )
                    _receive_tag(
                        owner_record["pipe"],
                        "relay-ready",
                        45.0,
                        label="U0 owner cleanup relay",
                    )
                    owner_connected = True
                owner_record["pipe"].send(("close",))
                if owner_close is None:
                    owner_close = _receive_tag(
                        owner_record["pipe"],
                        "closed",
                        _CHILD_CLOSE_TIMEOUT_SECONDS,
                        label="U0 owner cleanup",
                    )
            except BaseException as exc:
                cleanup_errors.append(f"owner-close:{type(exc).__name__}:{exc}")
            attempt(
                "owner-reap",
                lambda: _close_spawned(owner_record, timeout=_CHILD_CLOSE_TIMEOUT_SECONDS),
            )
        if monitor_thread is not None and monitor_parent is not None:
            try:
                if monitor_close is None:
                    monitor_parent.send(("close",))
                    monitor_close = _receive_tag(
                        monitor_parent,
                        "closed",
                        _CHILD_CLOSE_TIMEOUT_SECONDS,
                        label="U0 Monitor cleanup",
                    )
                monitor_thread.join(_CHILD_CLOSE_TIMEOUT_SECONDS)
                if monitor_thread.is_alive():
                    cleanup_errors.append("monitor-cleanup_uncertain:thread_remained_alive")
                if monitor_errors:
                    cleanup_errors.append(f"monitor-thread-errors:{monitor_errors!r}")
            except BaseException as exc:
                cleanup_errors.append(f"monitor-close:{type(exc).__name__}:{exc}")
            attempt("monitor-parent-close", monitor_parent.close)
        if cleanup_errors:
            if primary_error is not None:
                for item in cleanup_errors:
                    primary_error.add_note(f"cleanup also failed: {item}")
            else:
                raise AssertionError("U0 single-strategy cleanup failed: " + "; ".join(cleanup_errors))
