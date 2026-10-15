"""Focused CFG05 regressions for the explicit service configuration boundary."""

from __future__ import annotations

import builtins
import json
import socket
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from bt_api_py.service_config import ServiceConfigError
from bt_api_py.service_config_io import load_client_config, load_service_config, main


def _valid_service() -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "service-synthetic-a",
        "scope_ref": "scope-synthetic-a",
        "transport_ref": "listener",
        "registration_policy_ref": "policy-synthetic-v1",
        "service_requirements_ref": "readiness-synthetic-v1",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "monitor-synthetic-a",
                    "bind_ports": ["service.telemetry_ingress"],
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "listener": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:7609"},
                    "security_profile_ref": "security-synthetic-v1",
                }
            },
        },
    }


def _write_json(path: Path, raw: dict[str, Any]) -> bytes:
    data = (json.dumps(raw, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(data)
    return data


@pytest.mark.parametrize(
    ("field", "location", "expected_path"),
    [
        ("enabled", "root", "$"),
        ("register_to", "root", "$"),
        ("enabled", "component_settings", "$.components.monitor.settings"),
    ],
)
def test_service_schema_rejects_retired_switch_and_registration_fields(
    tmp_path: Path, field: str, location: str, expected_path: str
) -> None:
    raw = _valid_service()
    if location == "root":
        raw[field] = True if field == "enabled" else "strategy-synthetic-a"
    else:
        raw["components"]["monitor"]["settings"][field] = True
    path = tmp_path / "service.json"
    original = _write_json(path, raw)

    with pytest.raises(ServiceConfigError) as caught:
        load_service_config(path)

    assert caught.value.code == "UNKNOWN_FIELD"
    assert caught.value.path == expected_path
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "missing_field",
    (
        "service_id",
        "scope_ref",
        "transport_ref",
        "registration_policy_ref",
        "service_requirements_ref",
    ),
)
def test_service_schema_requires_identity_and_policy_references(
    tmp_path: Path, missing_field: str
) -> None:
    raw = _valid_service()
    del raw[missing_field]
    path = tmp_path / "service.json"
    original = _write_json(path, raw)

    with pytest.raises(ServiceConfigError) as caught:
        load_service_config(path)

    assert caught.value.code == "MISSING_FIELD"
    assert caught.value.path == f"$.{missing_field}"
    assert path.read_bytes() == original


def test_strategy_schema_requires_identity_for_declared_client(tmp_path: Path) -> None:
    raw = {
        "config_schema_version": 6,
        "process_role": "strategy_client",
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "store.market",
                            "service_id": "market-synthetic-a",
                            "instance_id": "market-gateway-synthetic-a",
                            "scope_ref": "scope-synthetic-a",
                            "transport_ref": "market-profile",
                        }
                    ]
                },
            }
        },
    }
    path = tmp_path / "client.json"
    original = _write_json(path, raw)

    with pytest.raises(ServiceConfigError) as caught:
        load_client_config(path)

    assert caught.value.code == "MISSING_FIELD"
    assert caught.value.path == "$.registration"
    assert path.read_bytes() == original


def test_strategy_schema_rejects_retired_register_to_field(tmp_path: Path) -> None:
    raw = {
        "config_schema_version": 6,
        "process_role": "strategy_client",
        "register_to": "strategy-synthetic-a",
    }
    path = tmp_path / "client.json"
    original = _write_json(path, raw)

    with pytest.raises(ServiceConfigError) as caught:
        load_client_config(path)

    assert caught.value.code == "UNKNOWN_FIELD"
    assert caught.value.path == "$"
    assert path.read_bytes() == original


def test_legacy_remote_config_is_rejected_by_new_service_entry_without_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Old forwarding-shaped input must be explicitly migrated before service startup."""
    legacy = {
        "config_schema_version": 5,
        "strategy": {"id": "legacy-synthetic-a"},
        "runtime": {"mode": "live", "preset": "managed_live_gateway"},
        "gateway": {
            "backend": "zmq",
            "command_endpoint": "tcp://127.0.0.1:7610",
            "market_endpoint": "tcp://127.0.0.1:7611",
            "enable_trading": True,
        },
        "register_to": "strategy-synthetic-a",
    }
    path = tmp_path / "legacy-remote.json"
    original = _write_json(path, legacy)
    effects: list[str] = []
    optional_imports: list[str] = []

    def forbidden_effect(name: str):
        def fail(*_args: object, **_kwargs: object) -> None:
            effects.append(name)
            raise AssertionError(f"configuration validation attempted {name}")

        return fail

    monkeypatch.setattr(socket, "socket", forbidden_effect("socket"))
    monkeypatch.setattr(subprocess, "Popen", forbidden_effect("process"))
    monkeypatch.setattr(threading.Thread, "start", forbidden_effect("thread"))
    real_import = builtins.__import__
    forbidden_modules = (
        "bt_api_py.forwarding",
        "bt_api_py.gateway",
        "bt_api_gateway",
        "bt_api_execution",
        "bt_api_risk",
        "bt_api_monitor",
        "bt_api_transport_zmq",
    )

    def imports_without_optional_services(name: str, *args: object, **kwargs: object) -> object:
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden_modules):
            optional_imports.append(name)
            raise ImportError("optional service package is not part of offline validation")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", imports_without_optional_services)

    result = main(["--role", "service", str(path)])
    output = json.loads(capsys.readouterr().out)

    assert result == 2
    assert output == {"status": "invalid", "code": "UNKNOWN_FIELD", "path": "$"}
    assert "activated" not in output
    assert effects == []
    assert optional_imports == []
    assert path.read_bytes() == original
