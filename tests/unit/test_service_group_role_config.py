from __future__ import annotations

import ast
import builtins
import multiprocessing
import socket
import threading
from pathlib import Path
from typing import Any

import pytest

import bt_api_py.service_config as service_config_module
from bt_api_py.service_config import ServiceConfigError, parse_service_config


def _service_config(
    component: str,
    role: str,
    bind_ports: list[str],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "group-role-service",
        "scope_ref": "group-role-root-scope",
        "transport_ref": "listener",
        "registration_policy_ref": "group-role-registration-policy",
        "service_requirements_ref": "group-role-requirements",
        "components": {
            component: {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "group-role-instance",
                    "bind_ports": bind_ports,
                    "options": options if options is not None else {"role": role},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "listener": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:7201"},
                    "security_profile_ref": "group-role-security-profile",
                }
            },
        },
    }


@pytest.mark.parametrize(
    ("component", "role", "bind_ports", "options"),
    [
        (
            "execution",
            "group_account_owner",
            ["service.order_entry", "service.order_query"],
            {"role": "group_account_owner"},
        ),
        (
            "execution",
            "group_account_owner",
            ["service.order_entry"],
            {"role": "group_account_owner"},
        ),
        (
            "execution",
            "group_account_owner",
            ["service.order_query"],
            {"role": "group_account_owner"},
        ),
        (
            "risk",
            "group_reservations",
            ["service.group_reservations"],
            {"role": "group_reservations"},
        ),
        (
            "execution",
            "group_coordinator",
            ["service.order_group_entry"],
            {"role": "group_coordinator", "portfolio_ref": "portfolio-group-a"},
        ),
    ],
)
def test_dedicated_group_roles_parse_with_fixed_ports_and_options(
    component: str,
    role: str,
    bind_ports: list[str],
    options: dict[str, Any],
) -> None:
    parsed = parse_service_config(_service_config(component, role, bind_ports, options))

    settings = parsed.to_public_dict()["components"][component]["settings"]
    assert settings["bind_ports"] == bind_ports
    assert settings["options"] == options


@pytest.mark.parametrize(
    ("component", "role", "wrong_port"),
    [
        ("execution", "group_account_owner", "service.order_group_entry"),
        ("execution", "group_account_owner", "service.owner_control"),
        ("risk", "group_reservations", "service.risk_query"),
        ("risk", "group_reservations", "service.order_admission"),
    ],
)
def test_group_role_rejects_ports_outside_its_fixed_capability(
    component: str, role: str, wrong_port: str
) -> None:
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(_service_config(component, role, [wrong_port]))

    assert exc_info.value.code == "PORT_ROLE_MISMATCH"


@pytest.mark.parametrize(
    ("component", "role", "port"),
    [
        ("execution", "group_account_owner", "service.order_entry"),
        ("risk", "group_reservations", "service.group_reservations"),
    ],
)
@pytest.mark.parametrize(
    "extra_option",
    [
        {"authorization_bypass": True},
        {"allow_any_actor": True},
    ],
)
def test_group_roles_reject_yaml_authorization_fields(
    component: str,
    role: str,
    port: str,
    extra_option: dict[str, bool],
) -> None:
    options: dict[str, Any] = {"role": role, **extra_option}

    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(_service_config(component, role, [port], options))

    assert exc_info.value.code == "UNKNOWN_FIELD"
    assert exc_info.value.path == f"$.components.{component}.settings.options"


def test_unknown_group_role_is_rejected() -> None:
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(
            _service_config("execution", "group_account_owner_v2", ["service.order_entry"])
        )

    assert exc_info.value.code == "UNKNOWN_COMPONENT_ROLE"
    assert exc_info.value.path == "$.components.execution.settings.options.role"


def test_group_coordinator_still_requires_its_portfolio_reference() -> None:
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(
            _service_config("execution", "group_coordinator", ["service.order_group_entry"])
        )

    assert exc_info.value.code == "MISSING_FIELD"
    assert exc_info.value.path == "$.components.execution.settings.options.portfolio_ref"


def test_existing_ordinary_account_roles_keep_their_current_configuration() -> None:
    execution = parse_service_config(
        _service_config(
            "execution",
            "account_owner",
            ["service.order_entry", "service.order_query", "service.owner_control"],
            {"role": "account_owner"},
        )
    )
    risk = parse_service_config(
        _service_config(
            "risk",
            "account_admission",
            [
                "service.order_admission",
                "service.risk_query",
                "service.group_reservations",
                "service.owner_control",
            ],
            {"role": "account_admission", "account_policy_ref": "account-policy-v1"},
        )
    )

    assert execution.components["execution"]["settings"]["options"] == {"role": "account_owner"}
    assert risk.components["risk"]["settings"]["options"] == {
        "role": "account_admission",
        "account_policy_ref": "account-policy-v1",
    }


def test_group_role_parsing_does_not_import_domains_or_start_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbidden_import_roots = {
        "bt_api_gateway",
        "bt_api_execution",
        "bt_api_risk",
        "bt_api_monitor",
        "bt_api_transport_zmq",
        "zmq",
        "socket",
        "threading",
        "multiprocessing",
    }
    tree = ast.parse(Path(service_config_module.__file__).read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    assert imported_roots.isdisjoint(forbidden_import_roots)

    original_import = builtins.__import__

    def _guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.split(".", 1)[0] in forbidden_import_roots:
            raise AssertionError("group-role parser attempted a runtime or domain import")
        return original_import(name, *args, **kwargs)

    def _resource_start_forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("group-role parsing attempted to start a resource")

    monkeypatch.setattr(builtins, "__import__", _guarded_import)
    monkeypatch.setattr(socket, "socket", _resource_start_forbidden)
    monkeypatch.setattr(threading, "Thread", _resource_start_forbidden)
    monkeypatch.setattr(multiprocessing, "Process", _resource_start_forbidden)

    for component, role, port in (
        ("execution", "group_account_owner", "service.order_query"),
        ("risk", "group_reservations", "service.group_reservations"),
    ):
        parsed = parse_service_config(_service_config(component, role, [port]))
        assert parsed.components[component]["settings"]["options"]["role"] == role
