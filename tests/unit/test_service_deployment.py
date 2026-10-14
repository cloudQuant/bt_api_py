from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from bt_api_py.service_config import (
    ClientConfig,
    ServiceConfig,
    ServiceConfigError,
    parse_client_config,
    parse_service_config,
)
from bt_api_py.service_deployment import (
    MOUNT_PORTS,
    OPTIONAL_MOUNT_PORTS,
    ClientBindingRequest,
    DeploymentPlan,
    validate_deployment,
)


def _profile(endpoint_name: str, endpoint: str) -> dict[str, Any]:
    return {
        "backend": "zmq",
        "config_schema_version": 1,
        "endpoints": {endpoint_name: endpoint},
        "security_profile_ref": "test-security",
    }


def _component_for_port(port: str, instance_id: str) -> tuple[str, dict[str, Any]]:
    if port == "service.market_source":
        name = "gateway"
        options: dict[str, Any] = {"role": "market_hub"}
    elif port in {"service.account_read", "service.owner_control"}:
        name = "gateway"
        options = {"role": "account_session_worker", "account_session_ref": "session-a"}
    elif port in {"service.order_entry", "service.order_query", "service.owner_control"}:
        name = "execution"
        options = {"role": "account_owner"}
    elif port == "service.order_group_entry":
        name = "execution"
        options = {"role": "group_coordinator", "portfolio_ref": "portfolio-a"}
    elif port in {"service.risk_query", "service.group_reservations", "service.owner_control"}:
        name = "risk"
        options = {"role": "account_admission", "account_policy_ref": "account-policy-v1"}
    elif port == "service.risk_analysis":
        name = "risk"
        options = {
            "role": "portfolio_analytics",
            "model_profile_ref": "model-profile-v1",
            "allowed_source_modes": ["research"],
        }
    elif port == "service.risk_policy":
        name = "risk"
        options = {"role": "policy_catalog"}
    elif port in {
        "service.telemetry_ingress",
        "service.monitor_query",
        "service.monitor_control",
    }:
        name = "monitor"
        options = {}
    else:  # pragma: no cover - test helper misuse
        raise AssertionError(port)
    return name, {
        "config_schema_version": 2,
        "settings": {
            "instance_id": instance_id,
            "bind_ports": [port],
            "options": options,
        },
    }


def _service(
    service_id: str,
    port: str,
    *,
    instance_id: str | None = None,
    bind_endpoint: str = "tcp://127.0.0.1:7101",
    outbound: dict[str, Any] | None = None,
) -> ServiceConfig:
    instance = instance_id or f"{service_id}-instance"
    component_name, component = _component_for_port(port, instance)
    listener_endpoints = {"rpc_bind": bind_endpoint}
    if port == "service.market_source":
        bind_host, bind_port = bind_endpoint.rsplit(":", 1)
        listener_endpoints["market_bind"] = f"{bind_host}:{int(bind_port) + 1}"
    communication: dict[str, Any] = {
        "config_schema_version": 1,
        "profiles": {
            "listener": {
                **_profile("rpc_bind", bind_endpoint),
                "endpoints": listener_endpoints,
            },
        },
    }
    config: dict[str, Any] = {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": service_id,
        "scope_ref": f"{service_id}-root-scope",
        "transport_ref": "listener",
        "registration_policy_ref": f"{service_id}-grants",
        "service_requirements_ref": f"{service_id}-readiness",
        "components": {component_name: component},
        "communication": communication,
    }
    if outbound is not None:
        alias, binding, connect_endpoint = outbound
        config["clients"] = {
            alias: {
                "config_schema_version": 1,
                "settings": {"bindings": [binding]},
            }
        }
        communication["profiles"]["outbound"] = _profile("rpc_connect", connect_endpoint)
        binding["transport_ref"] = "outbound"
    return parse_service_config(config)


def _client(
    mount: str,
    service_id: str,
    instance_id: str,
    *,
    account_ref: str | None = None,
    scope_ref: str = "client-grant-scope",
    endpoint: str = "tcp://127.0.0.1:7101",
    rpc_endpoint: str | None = None,
    alias: str | None = None,
) -> ClientConfig:
    if alias is None:
        alias = {
            "store.market": "gateway",
            "store.account": "gateway",
            "broker.orders": "execution",
            "broker.order_query": "execution",
            "broker.order_groups": "execution",
            "risk.read_model": "risk",
            "runtime.telemetry": "monitor",
            "application.market": "gateway",
            "application.account": "gateway",
            "application.orders": "gateway",
            "application.order_query": "execution",
            "application.order_groups": "execution",
            "application.risk_read": "risk",
            "application.risk_analysis": "risk",
            "application.risk_policy": "risk",
            "application.monitor": "monitor",
            "application.owner_control": "gateway",
            "upstream.market": "gateway",
            "upstream.account": "gateway",
            "upstream.orders": "execution",
            "upstream.order_query": "execution",
            "upstream.risk_read": "risk",
            "upstream.risk_policy": "risk",
            "upstream.telemetry": "monitor",
            "upstream.owner_control": "gateway",
            "upstream.account_reservations": "risk",
        }[mount]
    binding: dict[str, Any] = {
        "mount": mount,
        "service_id": service_id,
        "instance_id": instance_id,
        "scope_ref": scope_ref,
        "transport_ref": "target",
    }
    if account_ref is not None:
        binding["account_ref"] = account_ref
    raw: dict[str, Any] = {
        "client_config_schema_version": 1,
        "process_role": "application_client"
        if mount.startswith("application.")
        else "strategy_client",
        "registration": (
            {"application_ref": "test-application"}
            if mount.startswith("application.")
            else {"strategy_ref": "test-strategy"}
        ),
        "clients": {
            alias: {
                "config_schema_version": 1,
                "settings": {"bindings": [binding]},
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "target": {
                    **_profile("rpc_connect", endpoint),
                    "endpoints": {"rpc_connect": rpc_endpoint or endpoint},
                }
            },
        },
    }
    if mount in {"store.market", "application.market"}:
        raw["communication"]["profiles"]["target"] = {
            **_profile("market_connect", endpoint),
            "endpoints": {
                "rpc_connect": rpc_endpoint or endpoint,
                "market_connect": endpoint,
            },
        }
    return parse_client_config(raw)


def _directory(*configs: ServiceConfig) -> dict[str, ServiceConfig]:
    return {config.service_id: config for config in configs}


def test_empty_deployment_is_a_valid_immutable_zero_service_plan() -> None:
    plan = validate_deployment({})

    assert plan == DeploymentPlan((), {}, (), ())
    assert plan.service_order == ()
    assert plan.dependencies == {}
    assert plan.component_activations == ()
    assert plan.client_binding_requests == ()
    assert plan.services_activated is False
    assert plan.remote_readiness_verified is False
    assert plan.automatic_failover_supported is False
    assert plan.external_fencing_supported is False


def test_plan_orders_dependencies_and_is_deterministic_from_mapping_order() -> None:
    account = _service(
        "account-a",
        "service.account_read",
        instance_id="account-reader",
        bind_endpoint="tcp://127.0.0.1:7102",
    )
    market = _service(
        "market-b",
        "service.market_source",
        instance_id="market-hub",
        bind_endpoint="tcp://[::1]:7103",
    )
    analysis = _service(
        "analysis-c",
        "service.risk_analysis",
        instance_id="analysis-worker",
        bind_endpoint="tcp://127.0.0.1:7104",
        outbound=(
            "gateway",
            {
                "mount": "upstream.account",
                "service_id": "account-a",
                "instance_id": "account-reader",
                "scope_ref": "analysis-grant",
                "account_ref": "account-a",
            },
            "tcp://127.0.0.1:7102",
        ),
    )
    client = _client(
        "store.market",
        "market-b",
        "market-hub",
        endpoint="tcp://[0:0:0:0:0:0:0:1]:7104",
        rpc_endpoint="tcp://[0:0:0:0:0:0:0:1]:7103",
    )

    first = validate_deployment(_directory(analysis, market, account), (client,))
    second = validate_deployment(_directory(account, market, analysis), (client,))

    assert first == second
    assert first.service_order == ("account-a", "analysis-c", "market-b")
    assert first.dependencies == {
        "account-a": (),
        "analysis-c": ("account-a",),
        "market-b": (),
    }
    assert first.client_binding_requests[0].requester_service_id == "analysis-c"
    assert isinstance(first.client_binding_requests[0], ClientBindingRequest)
    assert first.client_binding_requests[0].scope_ref == "analysis-grant"
    assert first.client_binding_requests[1].service_id == "market-b"
    assert first.client_binding_requests[1].connect_endpoint == "tcp://[0:0:0:0:0:0:0:1]:7104"


def test_cross_service_dependency_cycle_is_rejected() -> None:
    service_a = _service(
        "service-a",
        "service.account_read",
        outbound=(
            "gateway",
            {
                "mount": "upstream.account",
                "service_id": "service-b",
                "instance_id": "instance-b",
                "scope_ref": "opaque-grant-b",
                "account_ref": "account-b",
            },
            "tcp://127.0.0.1:7102",
        ),
    )
    service_b = _service(
        "service-b",
        "service.account_read",
        instance_id="instance-b",
        bind_endpoint="tcp://127.0.0.1:7102",
        outbound=(
            "gateway",
            {
                "mount": "upstream.account",
                "service_id": "service-a",
                "instance_id": "service-a-instance",
                "scope_ref": "opaque-grant-a",
                "account_ref": "account-a",
            },
            "tcp://127.0.0.1:7101",
        ),
    )

    with pytest.raises(ServiceConfigError) as exc_info:
        validate_deployment(_directory(service_a, service_b))

    assert exc_info.value.code == "DEPENDENCY_CYCLE"
    assert exc_info.value.path == "$.services"
    assert "service-a" not in str(exc_info.value)


def test_missing_target_service_and_instance_fail_with_finite_errors() -> None:
    target = _service("target", "service.account_read", instance_id="reader")
    missing_service = _client("store.account", "missing-secret-id", "reader", account_ref="acct")
    with pytest.raises(ServiceConfigError) as exc_info:
        validate_deployment(_directory(target), (missing_service,))
    assert exc_info.value.code == "MISSING_TARGET_SERVICE"
    assert "missing-secret-id" not in str(exc_info.value)

    missing_instance = _client("store.account", "target", "missing-instance", account_ref="acct")
    with pytest.raises(ServiceConfigError) as exc_info:
        validate_deployment(_directory(target), (missing_instance,))
    assert exc_info.value.code == "UNKNOWN_INSTANCE"
    assert exc_info.value.path.endswith(".instance_id")


def test_existing_instance_must_bind_the_mounts_registered_port() -> None:
    target = _service("target", "service.account_read", instance_id="reader")
    client = _client("broker.orders", "target", "reader", account_ref="acct")

    with pytest.raises(ServiceConfigError) as exc_info:
        validate_deployment(_directory(target), (client,))

    assert exc_info.value.code == "MOUNT_PORT_MISMATCH"
    assert exc_info.value.path.endswith(".mount")


def test_selected_bindings_cannot_create_two_writers_for_one_account() -> None:
    service_a = _service(
        "owner-a",
        "service.order_entry",
        instance_id="execution-a",
        bind_endpoint="tcp://127.0.0.1:7101",
    )
    service_b = _service(
        "owner-b",
        "service.order_entry",
        instance_id="execution-b",
        bind_endpoint="tcp://127.0.0.1:7102",
    )
    client_a = _client(
        "broker.orders",
        "owner-a",
        "execution-a",
        account_ref="same-account",
        endpoint="tcp://127.0.0.1:7101",
    )
    client_b = _client(
        "broker.orders",
        "owner-b",
        "execution-b",
        account_ref="same-account",
        endpoint="tcp://127.0.0.1:7102",
    )

    with pytest.raises(ServiceConfigError) as exc_info:
        validate_deployment(_directory(service_a, service_b), (client_a, client_b))

    assert exc_info.value.code == "WRITER_CONFLICT"
    assert "same-account" not in str(exc_info.value)


def test_monitor_requires_query_and_treats_control_as_optional() -> None:
    monitor = _service("monitor", "service.monitor_query", instance_id="monitor-worker")
    client = _client(
        "application.monitor",
        "monitor",
        "monitor-worker",
        endpoint="tcp://127.0.0.1:7101",
    )

    plan = validate_deployment(_directory(monitor), (client,))

    assert MOUNT_PORTS["application.monitor"] == ("service.monitor_query",)
    assert OPTIONAL_MOUNT_PORTS["application.monitor"] == ("service.monitor_control",)
    assert plan.client_binding_requests[0].mount == "application.monitor"

    control_only = _service("monitor-only-control", "service.monitor_control")
    control_client = _client(
        "application.monitor",
        "monitor-only-control",
        "monitor-only-control-instance",
    )
    with pytest.raises(ServiceConfigError) as exc_info:
        validate_deployment(_directory(control_only), (control_client,))
    assert exc_info.value.code == "MOUNT_PORT_MISMATCH"


def test_literal_endpoint_mismatch_fails_but_dns_alias_and_wildcard_bind_are_allowed() -> None:
    target = _service(
        "target",
        "service.account_read",
        instance_id="reader",
        bind_endpoint="tcp://0.0.0.0:7101",
    )
    wildcard_client = _client(
        "store.account",
        "target",
        "reader",
        account_ref="account-a",
        endpoint="tcp://192.0.2.8:7101",
    )
    assert validate_deployment(_directory(target), (wildcard_client,)).client_binding_requests

    ipv6_wildcard = _service(
        "ipv6-wildcard",
        "service.account_read",
        instance_id="reader",
        bind_endpoint="tcp://[0:0:0:0:0:0:0:0]:7104",
    )
    ipv6_wildcard_client = _client(
        "store.account",
        "ipv6-wildcard",
        "reader",
        account_ref="account-v6",
        endpoint="tcp://192.0.2.9:7104",
    )
    assert validate_deployment(
        _directory(ipv6_wildcard), (ipv6_wildcard_client,)
    ).client_binding_requests

    dns_target = _service(
        "dns-target",
        "service.account_read",
        instance_id="reader",
        bind_endpoint="tcp://service.internal:7102",
    )
    dns_client = _client(
        "store.account",
        "dns-target",
        "reader",
        account_ref="account-b",
        endpoint="tcp://account-alias.internal:7102",
    )
    assert validate_deployment(_directory(dns_target), (dns_client,)).client_binding_requests

    literal_target = _service(
        "literal-target",
        "service.account_read",
        instance_id="reader",
        bind_endpoint="tcp://192.0.2.10:7103",
    )
    mismatched_client = _client(
        "store.account",
        "literal-target",
        "reader",
        account_ref="account-c",
        endpoint="tcp://192.0.2.11:7103",
    )
    with pytest.raises(ServiceConfigError) as exc_info:
        validate_deployment(_directory(literal_target), (mismatched_client,))
    assert exc_info.value.code == "ENDPOINT_MISMATCH"


@pytest.mark.parametrize(
    ("rpc_endpoint", "market_endpoint"),
    [
        ("tcp://192.0.2.21:7101", "tcp://192.0.2.20:7102"),
        ("tcp://192.0.2.20:7101", "tcp://192.0.2.21:7102"),
    ],
    ids=("rpc-control-mismatch", "market-stream-mismatch"),
)
@pytest.mark.parametrize("mount", ("store.market", "application.market"))
def test_market_binding_validates_rpc_and_market_endpoint_pairs(
    rpc_endpoint: str, market_endpoint: str, mount: str
) -> None:
    target = _service(
        "market",
        "service.market_source",
        instance_id="hub",
        bind_endpoint="tcp://192.0.2.20:7101",
    )
    client = _client(
        mount,
        "market",
        "hub",
        endpoint=market_endpoint,
        rpc_endpoint=rpc_endpoint,
    )

    with pytest.raises(ServiceConfigError) as exc_info:
        validate_deployment(_directory(target), (client,))
    assert exc_info.value.code == "ENDPOINT_MISMATCH"


def test_config_objects_must_come_from_the_public_parsers() -> None:
    with pytest.raises(TypeError):
        ServiceConfig()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        ClientConfig()  # type: ignore[call-arg]
    with pytest.raises(ServiceConfigError) as exc_info:
        validate_deployment({"service": object()})  # type: ignore[dict-item]
    assert exc_info.value.code == "INVALID_CONFIG"


def test_plan_snapshots_are_deeply_immutable() -> None:
    monitor = _service("monitor", "service.monitor_query", instance_id="monitor-worker")
    client = _client("application.monitor", "monitor", "monitor-worker")
    plan = validate_deployment(_directory(monitor), (client,))

    with pytest.raises(TypeError):
        plan.dependencies["other"] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        plan.component_activations[0].options["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        MOUNT_PORTS["application.monitor"] = ("service.monitor_control",)  # type: ignore[index]


def test_deployment_module_does_not_import_optional_domain_or_transport_packages() -> None:
    module_path = Path(__file__).parents[2] / "bt_api_py" / "service_deployment.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert "bt_api_gateway" not in imported
    assert "bt_api_execution" not in imported
    assert "bt_api_risk" not in imported
    assert "bt_api_monitor" not in imported
    assert "bt_api_transport_zmq" not in imported
