"""CFG03 service-combination and fail-closed configuration matrix.

These tests exercise the public parser only. They do not start services or
resolve credentials, policies, provider sessions, or communication backends.
"""

from __future__ import annotations

import builtins
import copy
import multiprocessing
import socket
import sqlite3
import subprocess
import threading
from typing import Any

import pytest

from bt_api_py.service_config import (
    ServiceConfigError,
    parse_client_config,
    parse_service_config,
)

_PACKAGES_WITH_NO_CONFIG_TIME_IMPORT = (
    "bt_api_py.forwarding",
    "bt_api_py.gateway",
    "bt_api_gateway",
    "bt_api_execution",
    "bt_api_risk",
    "bt_api_monitor",
    "bt_api_transport_zmq",
)


def _component(
    name: str,
    *,
    include_gateway: bool,
    duplicate_owner_control: bool = False,
) -> dict[str, Any]:
    if name == "gateway":
        ports = ["service.account_read", "service.provider_session"]
        if duplicate_owner_control:
            ports.append("service.owner_control")
        return {
            "config_schema_version": 2,
            "settings": {
                "instance_id": "account-gateway",
                "bind_ports": ports,
                "options": {
                    "role": "account_session_worker",
                    "account_session_ref": "paper-account-session",
                },
            },
        }
    if name == "execution":
        ports = ["service.order_entry", "service.order_query"]
        if duplicate_owner_control:
            ports.append("service.owner_control")
        options: dict[str, str] = {"role": "account_owner"}
        if include_gateway:
            options["provider_session_ref"] = "account-gateway"
        return {
            "config_schema_version": 2,
            "settings": {
                "instance_id": "account-execution",
                "bind_ports": ports,
                "options": options,
            },
        }
    if name == "risk":
        return {
            "config_schema_version": 2,
            "settings": {
                "instance_id": "account-risk",
                "bind_ports": ["service.order_admission", "service.risk_query"],
                "options": {
                    "role": "account_admission",
                    "account_policy_ref": "paper-account-policy-v1",
                },
            },
        }
    if name == "monitor":
        return {
            "config_schema_version": 2,
            "settings": {
                "instance_id": "operations-monitor",
                "bind_ports": ["service.telemetry_ingress"],
                "options": {"role": "telemetry_ingress"},
            },
        }
    raise AssertionError(f"unrecognized test component: {name}")


def _service_config(components: tuple[str, ...]) -> dict[str, Any]:
    selected = set(components)
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "cfg03-paper-service",
        "scope_ref": "cfg03-paper-scope",
        "transport_ref": "cfg03-listener",
        "registration_policy_ref": "cfg03-registration-v1",
        "service_requirements_ref": "cfg03-requirements-v1",
        "components": {
            name: _component(name, include_gateway="gateway" in selected)
            for name in components
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "cfg03-listener": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:7811"},
                    "security_profile_ref": "cfg03-server-security-v1",
                }
            },
        },
    }


def _install_no_side_effect_guards(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    effects: list[str] = []

    def forbidden(name: str):
        def fail(*_args: object, **_kwargs: object) -> None:
            effects.append(name)
            raise AssertionError(f"configuration parsing attempted {name}")

        return fail

    monkeypatch.setattr(socket, "socket", forbidden("socket"))
    monkeypatch.setattr(socket, "create_connection", forbidden("connect"))
    monkeypatch.setattr(socket, "getaddrinfo", forbidden("name-resolution"))
    monkeypatch.setattr(subprocess, "Popen", forbidden("process"))
    monkeypatch.setattr(threading.Thread, "start", forbidden("thread"))
    monkeypatch.setattr(multiprocessing.Process, "start", forbidden("child-process"))
    monkeypatch.setattr(sqlite3, "connect", forbidden("database"))

    real_import = builtins.__import__

    def import_without_service_packages(name: str, *args: object, **kwargs: object) -> object:
        forbidden_import = any(
            name == prefix or name.startswith(prefix + ".")
            for prefix in _PACKAGES_WITH_NO_CONFIG_TIME_IMPORT
        )
        if forbidden_import:
            effects.append(f"import:{name}")
            raise AssertionError("configuration parsing imported an optional service package")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_service_packages)
    return effects


@pytest.mark.unit
@pytest.mark.parametrize(
    ("case", "components"),
    [
        ("all-four", ("gateway", "execution", "risk", "monitor")),
        ("gateway-execution", ("gateway", "execution")),
        ("execution-risk", ("execution", "risk")),
        ("gateway-monitor", ("gateway", "monitor")),
        ("execution-monitor", ("execution", "monitor")),
        ("risk-monitor", ("risk", "monitor")),
    ],
)
def test_cfg03_supported_service_combinations_parse_without_activation(
    case: str,
    components: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del case  # pytest id makes the selected deployment combination explicit.
    raw = _service_config(components)
    original = copy.deepcopy(raw)
    effects = _install_no_side_effect_guards(monkeypatch)

    parsed = parse_service_config(raw)
    public = parsed.to_public_dict()

    assert set(public["components"]) == set(components)
    assert raw == original
    assert effects == []


def _client_config(bindings: list[tuple[str, str, str, str, str]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, str]]] = {}
    aliases = {"store.account": "gateway", "broker.orders": "execution"}
    for mount, account_ref, scope_ref, instance_id, service_id in bindings:
        grouped.setdefault(aliases[mount], []).append(
            {
                "mount": mount,
                "account_ref": account_ref,
                "scope_ref": scope_ref,
                "instance_id": instance_id,
                "service_id": service_id,
                "transport_ref": "cfg03-rpc",
            }
        )
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": "cfg03-strategy"},
        "clients": {
            alias: {
                "config_schema_version": 1,
                "settings": {"bindings": items},
            }
            for alias, items in grouped.items()
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "cfg03-rpc": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": "tcp://127.0.0.1:7811"},
                    "security_profile_ref": "cfg03-client-security-v1",
                }
            },
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("case", "build_invalid", "expected_code", "expected_path"),
    [
        (
            "wrong-account-reuses-scope",
            lambda: _client_config(
                [
                    ("store.account", "paper-account-a", "shared-scope", "gateway-a", "account-service"),
                    ("broker.orders", "paper-account-b", "shared-scope", "execution-a", "account-service"),
                ]
            ),
            "SCOPE_CONFLICT",
            "$.clients[1]",
        ),
        (
            "duplicate-client-mount",
            lambda: _client_config(
                [
                    ("broker.orders", "paper-account-a", "scope-a", "execution-a", "account-service"),
                    ("broker.orders", "paper-account-a", "scope-a", "execution-a", "account-service"),
                ]
            ),
            "DUPLICATE_BINDING",
            "$.clients[0].settings.bindings[1]",
        ),
        (
            "conflicting-service-port",
            lambda: _service_config(("gateway", "execution"))
            | {
                "components": {
                    "gateway": _component(
                        "gateway", include_gateway=True, duplicate_owner_control=True
                    ),
                    "execution": _component(
                        "execution", include_gateway=True, duplicate_owner_control=True
                    ),
                }
            },
            "PORT_CONFLICT",
            "$.components.execution.settings.bind_ports",
        ),
        (
            "missing-backend",
            lambda: _service_config(("execution",))
            | {
                "communication": {
                    "config_schema_version": 1,
                    "profiles": {
                        "cfg03-listener": {
                            "config_schema_version": 1,
                            "endpoints": {"rpc_bind": "tcp://127.0.0.1:7811"},
                            "security_profile_ref": "cfg03-server-security-v1",
                        }
                    },
                }
            },
            "UNKNOWN_BACKEND",
            "$.communication.profiles[0].backend",
        ),
        (
            "misspelled-risk-option",
            lambda: _service_config(("risk",))
            | {
                "components": {
                    "risk": {
                        "config_schema_version": 2,
                        "settings": {
                            "instance_id": "account-risk",
                            "bind_ports": ["service.order_admission", "service.risk_query"],
                            "options": {
                                "role": "account_admission",
                                "account_policy_reff": "paper-account-policy-v1",
                            },
                        },
                    }
                }
            },
            "UNKNOWN_FIELD",
            "$.components.risk.settings.options",
        ),
    ],
    ids=[
        "wrong-account-reuses-scope",
        "duplicate-client-mount",
        "conflicting-service-port",
        "missing-backend",
        "misspelled-risk-option",
    ],
)
def test_cfg03_invalid_matrix_entries_reject_without_side_effects(
    case: str,
    build_invalid,
    expected_code: str,
    expected_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del case
    raw = build_invalid()
    original = copy.deepcopy(raw)
    effects = _install_no_side_effect_guards(monkeypatch)

    with pytest.raises(ServiceConfigError) as caught:
        (parse_client_config if "clients" in raw else parse_service_config)(raw)

    assert caught.value.code == expected_code
    assert caught.value.path == expected_path
    assert raw == original
    assert effects == []
