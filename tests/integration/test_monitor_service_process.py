"""Synthetic-only independent Monitor process with real CURVE and SQLite."""

from __future__ import annotations

import queue
import re
import socket
import subprocess
import sys
import threading

import pytest
import yaml
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py import _win32_security
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)


def endpoint():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{s.getsockname()[1]}"


def _protect_file(path):
    if _win32_security.WINDOWS:
        _win32_security._set_owner_only_acl(path)
    else:
        path.chmod(0o600)


def deployment(tmp_path):
    keys = {}
    for name in ("events", "heartbeat", "producer", "gateway", "outsider"):
        public, secret = zmq.curve_keypair()
        keys[name] = (public, secret)
        for suffix, value in (("public", public), ("secret", secret)):
            path = tmp_path / f"{name}.{suffix}"
            path.write_bytes(value)
            _protect_file(path)

    def listener(name, principal):
        return {
            "service_id": f"{name}-service",
            "principal_ref": f"{name}-principal",
            "instance_id": f"{name}-instance",
            "scope_ref": f"{name}-scope",
            "rpc_bind": endpoint(),
            "public_key_file": f"{name}.public",
            "secret_key_file": f"{name}.secret",
            "principals": [
                {
                    "principal_ref": principal,
                    "actor_ref": f"{principal}-service",
                    "public_key_file": f"{principal}.public",
                }
            ],
        }

    document = {
        "schema": "bt_api_monitor.service.v1",
        "scope": {"execution_domain_ref": "synthetic-domain", "account_ref": "synthetic-account"},
        "storage": {
            "directory": "data",
            "outbox_scope": "events",
            "event_types": ["synthetic.observation"],
        },
        "ingress": listener("events", "producer"),
        "heartbeat": listener("heartbeat", "gateway"),
    }
    path = tmp_path / "monitor.yaml"
    path.write_text(yaml.safe_dump(document))
    return path, document, keys


def client(listener, server_key, client_keys, principal, operation, *, actor=None):
    identity = WireIdentity(WireRole.SERVICE, principal, actor or f"{principal}-service")
    peer = WireIdentity(WireRole.SERVICE, listener["principal_ref"], listener["service_id"])
    scope = WireScope("synthetic-domain", account_ref="synthetic-account")
    raw = {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": identity.actor_ref,
        "scope_ref": "producer-root",
        "transport_ref": "unused",
        "registration_policy_ref": "policy",
        "service_requirements_ref": "requirements",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "unused-instance",
                    "bind_ports": ["service.telemetry_ingress"],
                    "options": {"role": "gateway_heartbeat"},
                },
            }
        },
        "clients": {
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "upstream.telemetry",
                            "service_id": listener["service_id"],
                            "instance_id": listener["instance_id"],
                            "scope_ref": listener["scope_ref"],
                            "transport_ref": "remote",
                        }
                    ],
                    "options": {"request_timeout_ms": 1000, "heartbeat_interval_seconds": 5},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "unused": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:1"},
                    "security_profile_ref": "unused",
                },
                "remote": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": listener["rpc_bind"]},
                    "security_profile_ref": "client",
                },
            },
        },
    }
    return ServiceClientRuntime(
        parse_service_config(raw),
        identity=identity,
        scope_resolver=lambda _: scope,
        peer_resolver=lambda _: peer,
        transport_factory=ZmqTransportFactory(
            {"client": ClientCurveKeys(server_key, *client_keys)}
        ),
        requested_operations={"upstream.telemetry": frozenset({operation})},
    )


def start(path):
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "bt_api_py.monitor_service_host",
            "run",
            "--config",
            str(path),
            "--enable-owner-stop-file",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # select() only supports sockets on Windows; a reader thread with a queue
    # gives the same bounded startup wait on every platform.
    ready: queue.Queue = queue.Queue(maxsize=2)

    def _read_startup():
        ready.put(process.stdout.readline())
        ready.put(process.stdout.readline())

    reader = threading.Thread(target=_read_startup, daemon=True)
    reader.start()
    try:
        line = ready.get(timeout=20).strip()
    except queue.Empty as exc:
        process.terminate()
        process.wait(timeout=5)
        raise AssertionError("Monitor process did not report startup") from exc
    if line != "MONITOR_LISTENERS_READY":
        process.wait(timeout=5)
        raise AssertionError((line, process.stderr.read()))
    marker = ready.get(timeout=5).strip()
    match = re.fullmatch(r"MONITOR_STOP_FILE (monitor-[0-9a-f]{32}\.stop)", marker)
    assert match is not None, marker
    process.stop_marker = path.with_name(match[1])
    return process


def stop(process):
    process.stop_marker.write_bytes(b"")
    _protect_file(process.stop_marker)
    out, err = process.communicate(timeout=15)
    assert process.returncode == 0, (out, err)
    assert "MONITOR_STOPPED" in out


def test_two_authenticated_listeners_persist_and_deduplicate_after_restart(tmp_path):
    path, doc, keys = deployment(tmp_path)
    event = {
        "schema_version": 1,
        "event_id": "synthetic-1",
        "event_type": "synthetic.observation",
        "occurred_at": 1800000000.0,
        "data": {"value": 1},
    }
    process = start(path)
    first_marker = process.stop_marker
    try:
        first_marker.write_bytes(b"invalid nonempty stop request")
        _protect_file(first_marker)
        ingress = client(
            doc["ingress"], keys["events"][0], keys["producer"], "producer", "events.append"
        )
        ingress.start()
        first = dict(ingress.call("upstream.telemetry", "events.append", event))
        assert first == dict(ingress.call("upstream.telemetry", "events.append", event))
        ingress.close()
        heartbeat = client(
            doc["heartbeat"], keys["heartbeat"][0], keys["gateway"], "gateway", "monitor.heartbeat"
        )
        heartbeat.start()
        request = {
            "schema_version": 1,
            "relay_epoch": heartbeat.bindings[0].registration_id,
            "heartbeat_id": "synthetic-heartbeat",
            "sent_at_ns": 1,
        }
        assert dict(heartbeat.call("upstream.telemetry", "monitor.heartbeat", request)) == {
            **request,
            "monitor_principal": doc["heartbeat"]["principal_ref"],
        }
        heartbeat.close()
        for principal, key, actor, operation in (
            ("producer", keys["producer"], "wrong-actor", "events.append"),
            ("outsider", keys["outsider"], None, "events.append"),
            ("producer", keys["producer"], None, "monitor.heartbeat"),
        ):
            bad = client(doc["ingress"], keys["events"][0], key, principal, operation, actor=actor)
            with pytest.raises(ServiceRuntimeError):
                bad.start()
            bad.close()
    finally:
        stop(process)
    assert first_marker.is_file()
    process = start(path)
    try:
        assert process.stop_marker != first_marker
        ingress = client(
            doc["ingress"], keys["events"][0], keys["producer"], "producer", "events.append"
        )
        ingress.start()
        assert dict(ingress.call("upstream.telemetry", "events.append", event)) == first
        ingress.close()
    finally:
        stop(process)


def test_same_gateway_identity_and_key_can_use_both_explicit_grants(tmp_path):
    path, doc, keys = deployment(tmp_path)
    doc["ingress"]["principals"] = doc["heartbeat"]["principals"]
    path.write_text(yaml.safe_dump(doc))
    process = start(path)
    try:
        ingress = client(
            doc["ingress"], keys["events"][0], keys["gateway"], "gateway", "events.append"
        )
        ingress.start()
        result = ingress.call(
            "upstream.telemetry",
            "events.append",
            {
                "schema_version": 1,
                "event_id": "shared-key-event",
                "event_type": "synthetic.observation",
                "occurred_at": 1800000000.0,
                "data": {"value": 1},
            },
        )
        assert result["sequence"] > 0
        ingress.close()
        heartbeat = client(
            doc["heartbeat"], keys["heartbeat"][0], keys["gateway"], "gateway", "monitor.heartbeat"
        )
        heartbeat.start()
        request = {
            "schema_version": 1,
            "relay_epoch": heartbeat.bindings[0].registration_id,
            "heartbeat_id": "shared-key-heartbeat",
            "sent_at_ns": 1,
        }
        assert (
            heartbeat.call("upstream.telemetry", "monitor.heartbeat", request)["monitor_principal"]
            == doc["heartbeat"]["principal_ref"]
        )
        heartbeat.close()
    finally:
        stop(process)
