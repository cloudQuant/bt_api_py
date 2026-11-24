"""Synthetic-only tests for the standalone read-only Monitor query host."""

from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py import _win32_security
from bt_api_py.monitor_query_service_host import (
    SCHEMA,
    load_config,
    main,
    parse_config,
)
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import (
    BoundMonitorQueryTarget,
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)

_EVENT_TYPES = ("synthetic.runtime.status",)
_OUTBOX_SCOPE = "synthetic-events"
_SCOPE = WireScope("paper:synthetic", account_ref="synthetic-account")
_SERVICE_ID = "monitor-query-service"
_SERVICE_PRINCIPAL = "monitor-query-service-principal"
_SERVICE_INSTANCE = "monitor-query-service"
_SERVICE_SCOPE_REF = "monitor-query-scope-v1"
_CLIENT_PRINCIPAL = "bt-api-web-monitor"
_CLIENT_ACTOR = "bt-api-web"


def _endpoint() -> str:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{sock.getsockname()[1]}"


def _set_owner_only(path: Path, mode: int = 0o600) -> Path:
    if _win32_security.WINDOWS:
        _win32_security._set_owner_only_acl(path)
    else:
        path.chmod(mode)
    return path


def _config(tmp_path: Path, *, endpoint: str | None = None) -> dict:
    server_public, server_secret = zmq.curve_keypair()
    return {
        "schema": SCHEMA,
        "service_config": {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": _SERVICE_ID,
            "scope_ref": _SERVICE_SCOPE_REF,
            "transport_ref": "monitor-query-listener",
            "registration_policy_ref": "monitor-query-registration-v1",
            "service_requirements_ref": "monitor-query-requirements-v1",
            "components": {
                "monitor": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": _SERVICE_INSTANCE,
                        "bind_ports": ["service.monitor_query"],
                        "options": {
                            "role": "observation_query",
                            "epoch": 1,
                            "retention_floor_sequence": 0,
                            "cursor_ttl_seconds": 300,
                        },
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "monitor-query-listener": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": endpoint or _endpoint()},
                        "security_profile_ref": "monitor-query-server",
                    }
                },
            },
        },
        "scope": {
            "execution_domain_ref": _SCOPE.execution_domain_ref,
            "account_ref": _SCOPE.account_ref,
        },
        "service_identity": {
            "principal_ref": _SERVICE_PRINCIPAL,
            "actor_ref": _SERVICE_ID,
        },
        "client_identity": {"principal_ref": _CLIENT_PRINCIPAL, "actor_ref": _CLIENT_ACTOR},
        "keys": {
            "server_public_key_file": str(tmp_path / "server.public"),
            "server_secret_key_file": str(tmp_path / "server.secret"),
            "client_public_key_file": str(tmp_path / "client.public"),
            "hmac_key_hex_file": str(tmp_path / "observation.hmac.hex"),
        },
        "storage": {
            "events_db": str(tmp_path / "events.sqlite"),
            "registry_path": str(tmp_path / "query-registry.sqlite"),
            "outbox_scope": _OUTBOX_SCOPE,
            "event_types": list(_EVENT_TYPES),
        },
        "_test_keys": (server_public, server_secret),
    }


def _write_config(path: Path, raw: dict) -> dict:
    document = {key: value for key, value in raw.items() if not key.startswith("_")}
    path.write_text(json.dumps(document), encoding="utf-8")
    return document


def _json_ready(value):
    if isinstance(value, Mapping):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def test_dry_run_reads_only_config_and_does_not_open_keys_or_databases(tmp_path, capsys):
    config_path = tmp_path / "query.json"
    raw = _config(tmp_path)
    document = _write_config(config_path, raw)

    assert main(["run", "--config", str(config_path), "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "MONITOR_OBSERVATION_QUERY_CONFIG_VALID"
    assert load_config(config_path).service.service_id == _SERVICE_ID
    assert not Path(document["storage"]["events_db"]).exists()
    assert not Path(document["storage"]["registry_path"]).exists()
    assert all(not Path(value).exists() for value in document["keys"].values())


def test_config_rejects_non_query_component_and_duplicate_event_type(tmp_path):
    raw = _config(tmp_path)
    raw.pop("_test_keys")
    broken_service = json.loads(json.dumps(raw))
    broken_service["service_config"]["components"]["monitor"]["settings"]["bind_ports"] = [
        "service.telemetry_ingress",
        "service.monitor_query",
    ]
    with pytest.raises(ValueError):
        parse_config(broken_service, path=tmp_path / "query.json")

    duplicate_types = json.loads(json.dumps(raw))
    duplicate_types["storage"]["event_types"] = [_EVENT_TYPES[0], _EVENT_TYPES[0]]
    with pytest.raises(ValueError):
        parse_config(duplicate_types, path=tmp_path / "query.json")


def _monitor_client_config(endpoint: str) -> dict:
    return {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        "registration": {"application_ref": _CLIENT_ACTOR},
        "clients": {
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "application.monitor",
                            "service_id": _SERVICE_ID,
                            "instance_id": _SERVICE_INSTANCE,
                            "scope_ref": _SERVICE_SCOPE_REF,
                            "transport_ref": "monitor-query-client",
                        }
                    ],
                    "options": {
                        "request_timeout_ms": 3000,
                        "heartbeat_interval_seconds": 5,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "monitor-query-client": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": "monitor-query-client-security",
                    "options": {"request_timeout_ms": 3000, "high_water_mark": 32},
                }
            },
        },
    }


def _read_lines(process: subprocess.Popen[str], lines: queue.Queue[str | None]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        lines.put(line.strip())
    lines.put(None)


def _next_line(lines: queue.Queue[str | None], process: subprocess.Popen[str]) -> str:
    try:
        line = lines.get(timeout=15)
    except queue.Empty:
        raise AssertionError("query host did not produce bounded process output") from None
    if line is None:
        raise AssertionError(f"query host exited before readiness (code={process.poll()})")
    return line


def _stop_query_process(process: subprocess.Popen[str], marker: Path | None = None) -> None:
    if process.poll() is not None:
        return
    if marker is not None:
        marker.write_bytes(b"")
        _set_owner_only(marker)
        process.wait(timeout=10)
        return
    if os.name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=5)


def _drain_output(lines: queue.Queue[str | None]) -> list[str]:
    collected = []
    while True:
        try:
            line = lines.get(timeout=5)
        except queue.Empty:
            raise AssertionError("query host output did not close after process exit") from None
        if line is None:
            return collected
        collected.append(line)


def test_spawned_host_serves_one_authenticated_synthetic_query_and_stops(tmp_path):
    from bt_api_monitor.durable import DurableOutbox, OutboxEvent
    from bt_api_monitor.observation_query import (
        sign_observation_query_request,
        verify_observation_query_page,
    )

    raw = _config(tmp_path)
    server_public, server_secret = raw.pop("_test_keys")
    client_public, client_secret = zmq.curve_keypair()
    raw["keys"]["client_public_key_file"] = str(tmp_path / "client.public")
    key_bytes = bytes(range(32))
    key_paths = {
        "server.public": server_public,
        "server.secret": server_secret,
        "client.public": client_public,
    }
    for name, value in key_paths.items():
        path = tmp_path / name
        path.write_bytes(value)
        _set_owner_only(path)
    hmac_path = tmp_path / "observation.hmac.hex"
    hmac_path.write_text(key_bytes.hex(), encoding="ascii")
    _set_owner_only(hmac_path)

    outbox = DurableOutbox(tmp_path / "events.sqlite")
    outbox.append(
        OutboxEvent(
            event_id="synthetic-event-1",
            scope=_OUTBOX_SCOPE,
            event_type=_EVENT_TYPES[0],
            data={"status": "ready"},
            occurred_at=time.time(),
        )
    )
    config_path = tmp_path / "query.json"
    document = _write_config(config_path, raw)
    endpoint = document["service_config"]["communication"]["profiles"][
        "monitor-query-listener"
    ]["endpoints"]["rpc_bind"]
    command = [
        sys.executable,
        "-u",
        "-m",
        "bt_api_py.monitor_query_service_host",
        "run",
        "--config",
        str(config_path),
        "--enable-owner-stop-file",
    ]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=Path(__file__).resolve().parents[2],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=creationflags,
    )
    lines: queue.Queue[str | None] = queue.Queue()
    reader = threading.Thread(target=_read_lines, args=(process, lines), daemon=True)
    reader.start()
    runtime = None
    stop_marker = None
    try:
        assert _next_line(lines, process) == "MONITOR_OBSERVATION_QUERY_READY"
        marker = re.fullmatch(
            r"MONITOR_OBSERVATION_QUERY_STOP_FILE (monitor-query-[0-9a-f]{32}\.stop)",
            _next_line(lines, process),
        )
        assert marker is not None
        stop_marker = config_path.with_name(marker[1])
        stop_marker.write_bytes(b"synthetic invalid stop request")
        _set_owner_only(stop_marker)
        service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
        client_identity = WireIdentity(WireRole.APPLICATION_CLIENT, _CLIENT_PRINCIPAL, _CLIENT_ACTOR)
        runtime = ServiceClientRuntime(
            parse_client_config(_monitor_client_config(endpoint)),
            identity=client_identity,
            scope_resolver=lambda _scope_ref: _SCOPE,
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory(
                {
                    "monitor-query-client-security": ClientCurveKeys(
                        server_public, client_public, client_secret
                    )
                }
            ),
            requested_operations={"application.monitor": frozenset({"observation.query"})},
        )
        receipt = runtime.start()
        assert receipt.degraded_count == 0
        binding = receipt.bindings[0]
        peer_proof = runtime.authenticated_peer_for_binding(binding)
        assert peer_proof is not None
        client_config = parse_client_config(_monitor_client_config(endpoint)).to_public_dict()
        client_profile = client_config["communication"]["profiles"]["monitor-query-client"]
        target = BoundMonitorQueryTarget(
            mount=binding.mount,
            port="service.monitor_query",
            service_id=binding.service_id,
            instance_id=binding.instance_id,
            scope_ref=binding.scope_ref,
            scope=binding.scope,
            identity=client_identity,
            runtime_run_id=runtime.run_id,
            transport_ref=binding.transport_ref,
            security_profile_ref=client_profile["security_profile_ref"],
            endpoint=client_profile["endpoints"]["rpc_connect"],
            expected_peer=peer_proof[0],
            server_key_fingerprint=peer_proof[1],
            registration_id=binding.registration_id,
            service_epoch=binding.service_epoch,
        )
        request = sign_observation_query_request(
            key=key_bytes,
            principal_ref=_CLIENT_PRINCIPAL,
            scope=_SCOPE,
            event_types=_EVENT_TYPES,
            epoch=1,
            cursor=None,
            page_size=10,
            request_id=uuid.uuid4().hex,
            issued_at=time.time(),
        )
        page = _json_ready(
            runtime.call_bound(
                target,
                "observation.query",
                request,
                deadline_monotonic=time.monotonic() + 3,
            )
        )
        assert verify_observation_query_page(
            page,
            key=key_bytes,
            principal_ref=_CLIENT_PRINCIPAL,
            scope=_SCOPE,
            event_types=_EVENT_TYPES,
            epoch=1,
        )
        assert [event["event_id"] for event in page["events"]] == ["synthetic-event-1"]
        assert Path(document["storage"]["events_db"]).exists()
    finally:
        if runtime is not None:
            runtime.close()
        _stop_query_process(process, stop_marker)
    output = _drain_output(lines)
    assert "MONITOR_OBSERVATION_QUERY_STOPPED" in output
    assert "MONITOR_OBSERVATION_QUERY_FAILED" not in output
    assert process.returncode == 0
