from __future__ import annotations

import copy
from dataclasses import replace

import pytest
import yaml

from bt_api_py.service_config import (
    ClientConfig,
    ServiceConfig,
    ServiceConfigError,
    parse_client_config,
    parse_service_config,
)

STRATEGY_CLIENT_YAML = """
config_schema_version: 6
process_role: strategy_client
registration:
  strategy_ref: strategy-s001
clients:
  gateway:
    config_schema_version: 1
    settings:
      bindings:
        - mount: store.market
          service_id: market-main
          instance_id: market-gateway
          scope_ref: public-market-access
          transport_ref: market-zmq
        - mount: store.account
          account_ref: account-a01
          service_id: account-a01
          instance_id: account-gateway
          scope_ref: account-a01
          transport_ref: account-zmq
  monitor:
    config_schema_version: 1
    settings:
      bindings:
        - mount: runtime.telemetry
          service_id: monitor-main
          instance_id: operations-monitor
          scope_ref: current-strategy
          transport_ref: monitor-zmq
          required: false
      options:
        heartbeat_interval_seconds: 5
communication:
  config_schema_version: 1
  profiles:
    market-zmq:
      backend: zmq
      config_schema_version: 1
      endpoints:
        rpc_connect: tcp://127.0.0.1:7600
        market_connect: tcp://127.0.0.1:7602
      security_profile_ref: local-market-access
    account-zmq:
      backend: zmq
      config_schema_version: 1
      endpoints:
        rpc_connect: tcp://127.0.0.1:7601
      security_profile_ref: local-account-a01
    monitor-zmq:
      backend: zmq
      config_schema_version: 1
      endpoints:
        rpc_connect: tcp://127.0.0.1:7603
      security_profile_ref: local-monitor-access
"""


ACCOUNT_SERVICE_YAML = """
service_config_schema_version: 1
process_role: service
service_id: account-a01
scope_ref: account-a01
transport_ref: account-listener
registration_policy_ref: account-a01-strategy-grants
service_requirements_ref: account-a01-readiness
components:
  gateway:
    config_schema_version: 2
    settings:
      instance_id: account-gateway
      bind_ports: [service.account_read, service.provider_session]
      options:
        role: account_session_worker
        account_session_ref: a01-session
  execution:
    config_schema_version: 2
    settings:
      instance_id: account-execution
      bind_ports: [service.order_entry]
      options:
        role: account_owner
        provider_session_ref: account-gateway
  risk:
    config_schema_version: 2
    settings:
      instance_id: account-risk
      bind_ports: [service.order_admission, service.risk_query]
      options:
        role: account_admission
        account_policy_ref: a01-risk-policy-v1
communication:
  config_schema_version: 1
  profiles:
    account-listener:
      backend: zmq
      config_schema_version: 1
      endpoints:
        rpc_bind: tcp://127.0.0.1:7601
      security_profile_ref: account-a01-server
"""


APPLICATION_CLIENT_YAML = """
client_config_schema_version: 1
process_role: application_client
registration:
  application_ref: ai-for-investor
clients:
  risk:
    config_schema_version: 1
    settings:
      bindings:
        - mount: application.risk_analysis
          service_id: portfolio-analysis
          instance_id: analysis-risk
          portfolio_ref: research-p01
          scope_ref: research-p01-analysis
          transport_ref: analysis-zmq
          required: true
communication:
  config_schema_version: 1
  profiles:
    analysis-zmq:
      backend: zmq
      config_schema_version: 1
      endpoints:
        rpc_connect: tcp://127.0.0.1:7604
      security_profile_ref: afi-analysis-access
"""


ANALYTICS_SERVICE_YAML = """
service_config_schema_version: 1
process_role: service
service_id: portfolio-analysis
scope_ref: authorized-analysis-portfolios
transport_ref: analysis-listener
registration_policy_ref: analysis-grants
service_requirements_ref: analysis-readiness
components:
  risk:
    config_schema_version: 2
    settings:
      instance_id: analysis-risk
      bind_ports: [service.risk_analysis]
      options:
        role: portfolio_analytics
        model_profile_ref: standard-analysis-v1
        allowed_source_modes: [research, imported, paper]
communication:
  config_schema_version: 1
  profiles:
    analysis-listener:
      backend: zmq
      config_schema_version: 1
      endpoints:
        rpc_bind: tcp://127.0.0.1:7604
      security_profile_ref: analysis-server
"""


def _yaml(source: str) -> dict[str, object]:
    result = yaml.safe_load(source)
    assert type(result) is dict
    return result


def _read_profile(endpoint_key: str, endpoint: str) -> dict[str, object]:
    return {
        "backend": "zmq",
        "config_schema_version": 1,
        "endpoints": {endpoint_key: endpoint},
        "security_profile_ref": "test-security",
    }


def _client_binding(
    mount: str,
    *,
    account_ref: str | None = None,
    portfolio_ref: str | None = None,
    scope_ref: str = "scope-a",
    service_id: str = "service-a",
    instance_id: str = "instance-a",
    transport_ref: str = "rpc",
    required: bool | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "mount": mount,
        "service_id": service_id,
        "instance_id": instance_id,
        "scope_ref": scope_ref,
        "transport_ref": transport_ref,
    }
    if account_ref is not None:
        result["account_ref"] = account_ref
    if portfolio_ref is not None:
        result["portfolio_ref"] = portfolio_ref
    if required is not None:
        result["required"] = required
    return result


def _strategy_config(
    bindings: list[dict[str, object]], *, role: str = "strategy_client"
) -> dict[str, object]:
    aliases = {
        "store.market": "gateway",
        "store.account": "gateway",
        "broker.orders": "execution",
        "broker.order_query": "execution",
        "broker.order_groups": "execution",
        "risk.read_model": "risk",
        "runtime.telemetry": "monitor",
        "application.market": "gateway",
        "application.account": "gateway",
        "application.orders": "execution",
        "application.order_query": "execution",
        "application.order_groups": "execution",
        "application.risk_read": "risk",
        "application.risk_analysis": "risk",
        "application.risk_policy": "risk",
        "application.monitor": "monitor",
        "application.owner_control": "gateway",
    }
    grouped: dict[str, list[dict[str, object]]] = {}
    for binding in bindings:
        alias = aliases.get(str(binding.get("mount")), "gateway")
        grouped.setdefault(alias, []).append(binding)
    return {
        "client_config_schema_version": 1,
        "process_role": role,
        "registration": (
            {"strategy_ref": "strategy-a"}
            if role == "strategy_client"
            else {"application_ref": "application-a"}
        ),
        "clients": {
            alias: {
                "config_schema_version": 1,
                "settings": {"bindings": client_bindings},
            }
            for alias, client_bindings in grouped.items()
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {"rpc": _read_profile("rpc_connect", "tcp://127.0.0.1:7101")},
        },
    }


def _service_config() -> dict[str, object]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "service-a",
        "scope_ref": "account-a",
        "transport_ref": "listener",
        "registration_policy_ref": "account-a-grants",
        "service_requirements_ref": "account-a-ready",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "gateway-a",
                    "bind_ports": ["service.account_read", "service.provider_session"],
                    "options": {
                        "role": "account_session_worker",
                        "account_session_ref": "account-a-session",
                    },
                },
            },
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "execution-a",
                    "bind_ports": ["service.order_entry"],
                    "options": {
                        "role": "account_owner",
                        "provider_session_ref": "gateway-a",
                    },
                },
            },
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "risk-a",
                    "bind_ports": ["service.order_admission", "service.risk_query"],
                    "options": {
                        "role": "account_admission",
                        "account_policy_ref": "account-a-policy-v1",
                    },
                },
            },
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {"listener": _read_profile("rpc_bind", "tcp://127.0.0.1:7101")},
        },
    }


@pytest.mark.parametrize(
    ("source", "parser", "expected_type"),
    [
        (STRATEGY_CLIENT_YAML, parse_client_config, ClientConfig),
        (ACCOUNT_SERVICE_YAML, parse_service_config, ServiceConfig),
        (APPLICATION_CLIENT_YAML, parse_client_config, ClientConfig),
        (ANALYTICS_SERVICE_YAML, parse_service_config, ServiceConfig),
    ],
)
def test_plan_whole_yaml_examples_parse(
    source: str, parser: object, expected_type: type[object]
) -> None:
    result = parser(_yaml(source))  # type: ignore[operator]
    assert isinstance(result, expected_type)


def test_service_outbound_client_merges_with_listener_profile() -> None:
    config = _yaml(ANALYTICS_SERVICE_YAML)
    config["clients"] = {
        "gateway": {
            "config_schema_version": 1,
            "settings": {
                "bindings": [
                    _client_binding(
                        "upstream.account",
                        account_ref="a01",
                        scope_ref="a01-analysis-read",
                        service_id="account-a01",
                        instance_id="account-gateway",
                        transport_ref="account-read-zmq",
                    )
                ]
            },
        }
    }
    communication = config["communication"]
    assert type(communication) is dict
    profiles = communication["profiles"]
    assert type(profiles) is dict
    profiles["account-read-zmq"] = _read_profile("rpc_connect", "tcp://127.0.0.1:7601")

    parsed = parse_service_config(config)
    public = parsed.to_public_dict()
    assert "analysis-listener" in public["communication"]["profiles"]
    assert "account-read-zmq" in public["communication"]["profiles"]
    assert public["clients"]["gateway"]["settings"]["bindings"][0]["mount"] == "upstream.account"


def test_service_rejects_outbound_binding_back_to_itself() -> None:
    config = _service_config()
    communication = config["communication"]
    assert type(communication) is dict
    profiles = communication["profiles"]
    assert type(profiles) is dict
    profiles["account-outbound"] = _read_profile("rpc_connect", "tcp://127.0.0.1:7102")
    config["clients"] = {
        "gateway": {
            "config_schema_version": 1,
            "settings": {
                "bindings": [
                    _client_binding(
                        "upstream.account",
                        account_ref="account-a",
                        service_id="service-a",
                        instance_id="remote-account",
                        transport_ref="account-outbound",
                    )
                ]
            },
        }
    }
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(config)
    assert exc_info.value.code == "SELF_REFERENCE"
    assert exc_info.value.path.endswith(".service_id")


def test_account_scope_binding_conflict_crosses_mounts() -> None:
    bindings = [
        _client_binding(
            "store.account",
            account_ref="account-a",
            scope_ref="shared-scope",
            service_id="account-service",
        ),
        _client_binding(
            "broker.orders",
            account_ref="account-b",
            scope_ref="shared-scope",
            service_id="account-service",
        ),
    ]
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_client_config(_strategy_config(bindings))
    assert exc_info.value.code == "SCOPE_CONFLICT"


@pytest.mark.parametrize("raw", [{}, {"clients": {}}])
def test_empty_client_configuration_is_zero_capability(raw: dict[str, object]) -> None:
    parsed = parse_client_config(raw)
    assert parsed.process_role is None
    assert parsed.clients == {}
    assert parsed.to_public_dict() == raw


@pytest.mark.parametrize(
    ("raw", "code", "path"),
    [
        ({"config_schema_version": True}, "MISSING_FIELD", "$.process_role"),
        (
            {"client_config_schema_version": True, "process_role": "strategy_client"},
            "INVALID_VERSION",
            "$.client_config_schema_version",
        ),
        (
            {"client_config_schema_version": 1, "process_role": "strategy_client", "enabled": True},
            "UNKNOWN_FIELD",
            "$",
        ),
        (
            {"client_config_schema_version": 1, "process_role": "invalid-role"},
            "UNKNOWN_ROLE",
            "$.process_role",
        ),
        (
            {"client_config_schema_version": 1, "process_role": "service"},
            "UNKNOWN_ROLE",
            "$.process_role",
        ),
        (
            {
                "client_config_schema_version": 1,
                "process_role": "strategy_client",
                "components": {},
            },
            "UNKNOWN_FIELD",
            "$",
        ),
        (
            {
                "client_config_schema_version": 1,
                "process_role": "strategy_client",
                "placement": "embedded",
            },
            "UNKNOWN_FIELD",
            "$",
        ),
    ],
)
def test_client_role_versions_and_unknown_fields_fail_closed(
    raw: dict[str, object], code: str, path: str
) -> None:
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_client_config(raw)
    assert exc_info.value.code == code
    assert exc_info.value.path == path


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (object(), "INVALID_NODE"),
        (
            {
                "client_config_schema_version": 1,
                "process_role": "strategy_client",
                "x": float("inf"),
            },
            "INVALID_NODE",
        ),
        (set(), "INVALID_NODE"),
    ],
)
def test_only_plain_finite_input_tree_is_accepted(value: object, code: str) -> None:
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_client_config(value)
    assert exc_info.value.code == code
    assert exc_info.value.path == "$"


def test_cyclic_input_tree_is_rejected() -> None:
    raw: dict[str, object] = {}
    raw["loop"] = raw
    with pytest.raises(ServiceConfigError, match=r"INVALID_NODE at \$"):
        parse_client_config(raw)


def test_client_schema_v1_supports_strategy_role_and_empty_clients() -> None:
    raw = {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": "strategy-a"},
        "clients": {},
    }
    parsed = parse_client_config(raw)
    assert parsed.process_role == "strategy_client"
    assert parsed.clients == {}


def test_strategy_v6_projection_is_supported() -> None:
    raw = _yaml(STRATEGY_CLIENT_YAML)
    parsed = parse_client_config(raw)
    assert parsed.process_role == "strategy_client"
    assert parsed.schema_version == 6


def test_nonempty_client_requires_role_identity_and_communication() -> None:
    raw = {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {"bindings": [_client_binding("application.market")]},
            }
        },
    }
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_client_config(raw)
    assert exc_info.value.code == "MISSING_FIELD"
    assert exc_info.value.path == "$.registration"

    raw["registration"] = {"application_ref": "app-a"}
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_client_config(raw)
    assert exc_info.value.path == "$.communication"


def test_unknown_field_error_does_not_echo_user_supplied_key() -> None:
    secret_like_key = "private-token-should-not-appear"
    raw = {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        secret_like_key: "secret-like-value",
    }
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_client_config(raw)
    assert exc_info.value.code == "UNKNOWN_FIELD"
    assert secret_like_key not in str(exc_info.value)
    assert "secret-like-value" not in str(exc_info.value)


def test_mount_registry_and_service_root_upstream_only() -> None:
    raw = _strategy_config([_client_binding("application.market")])
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_client_config(raw)
    assert exc_info.value.code == "UNKNOWN_MOUNT"

    service = _service_config()
    service["clients"] = {
        "gateway": {
            "config_schema_version": 1,
            "settings": {
                "bindings": [
                    _client_binding("store.market", scope_ref="market", transport_ref="listener")
                ]
            },
        }
    }
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(service)
    assert exc_info.value.code == "UNKNOWN_MOUNT"


@pytest.mark.parametrize("mount", ["broker.orders", "application.orders", "upstream.orders"])
def test_required_false_is_rejected_for_write_mounts(mount: str) -> None:
    role = (
        "service"
        if mount.startswith("upstream.")
        else "application_client"
        if mount.startswith("application.")
        else "strategy_client"
    )
    binding = _client_binding(
        mount,
        account_ref="account-a",
        scope_ref="account-a",
        required=False,
    )
    raw = _strategy_config([binding], role=role) if role != "service" else None
    if role == "service":
        service = _service_config()
        service["clients"] = {
            "execution": {
                "config_schema_version": 1,
                "settings": {"bindings": [binding]},
            }
        }
        service["communication"]["profiles"]["rpc"] = _read_profile(
            "rpc_connect", "tcp://127.0.0.1:7200"
        )
        binding["transport_ref"] = "rpc"
        raw = service
        parser = parse_service_config
    else:
        parser = parse_client_config
    assert raw is not None
    with pytest.raises(ServiceConfigError) as exc_info:
        parser(raw)
    assert exc_info.value.code == "REQUIRED_CANNOT_BE_FALSE"


def test_readonly_optional_binding_and_typed_references_are_checked() -> None:
    binding = _client_binding(
        "risk.read_model",
        account_ref="account-a",
        scope_ref="account-a-risk-read",
        required=False,
    )
    parsed = parse_client_config(_strategy_config([binding]))
    assert parsed.clients["risk"]["settings"]["bindings"][0]["required"] is False

    service = _service_config()
    service["components"]["risk"]["settings"]["options"]["account_policy_ref"] = "unversioned"
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(service)
    assert exc_info.value.code == "VERSIONED_REFERENCE_REQUIRED"


@pytest.mark.parametrize(
    ("binding", "expected_code"),
    [
        (_client_binding("store.market", transport_ref="missing"), "UNKNOWN_REFERENCE"),
        (
            _client_binding("store.market", transport_ref="rpc"),
            "MISSING_ENDPOINT",
        ),
        (
            _client_binding("store.account", account_ref="account-a", required=1),
            "INVALID_TYPE",
        ),
    ],
)
def test_binding_references_endpoints_and_required_types(
    binding: dict[str, object], expected_code: str
) -> None:
    raw = _strategy_config([binding])
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_client_config(raw)
    assert exc_info.value.code == expected_code


@pytest.mark.parametrize(
    "endpoints",
    [
        {"rpc_connect": "tcp://127.0.0.1:7101"},
        {"market_connect": "tcp://127.0.0.1:7102"},
    ],
    ids=("missing-market-stream", "missing-rpc-control"),
)
@pytest.mark.parametrize(
    ("mount", "role"),
    [
        ("store.market", "strategy_client"),
        ("application.market", "application_client"),
    ],
)
def test_market_mount_requires_rpc_and_market_endpoints(
    endpoints: dict[str, str], mount: str, role: str
) -> None:
    raw = _strategy_config([_client_binding(mount)], role=role)
    raw["communication"]["profiles"]["rpc"]["endpoints"] = endpoints

    with pytest.raises(ServiceConfigError) as exc_info:
        parse_client_config(raw)

    assert exc_info.value.code == "MISSING_ENDPOINT"
    assert exc_info.value.path == "$.clients.bindings[0].transport_ref"


def test_service_upstream_market_mount_requires_rpc_and_market_endpoints() -> None:
    service = _service_config()
    service["clients"] = {
        "gateway": {
            "config_schema_version": 1,
            "settings": {
                "bindings": [
                    _client_binding(
                        "upstream.market",
                        service_id="market-upstream",
                        transport_ref="outbound",
                    )
                ]
            },
        }
    }
    service["communication"]["profiles"]["outbound"] = _read_profile(
        "market_connect", "tcp://127.0.0.1:7202"
    )

    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(service)

    assert exc_info.value.code == "MISSING_ENDPOINT"
    assert exc_info.value.path == "$.clients.bindings[0].transport_ref"


def test_duplicate_account_scope_and_writer_conflicts_are_rejected() -> None:
    first = _client_binding("store.account", account_ref="account-a", scope_ref="scope-a")
    second = _client_binding(
        "store.account", account_ref="account-a", scope_ref="scope-b", instance_id="other"
    )
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_client_config(_strategy_config([first, second]))
    assert exc_info.value.code == "SCOPE_CONFLICT"

    first_writer = _client_binding("broker.orders", account_ref="account-a", scope_ref="scope-a")
    other_writer = _client_binding("broker.orders", account_ref="account-a", scope_ref="scope-a")
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_client_config(_strategy_config([first_writer, other_writer]))
    assert exc_info.value.code == "DUPLICATE_BINDING"


@pytest.mark.parametrize("mode", ["execution_risk", "execution_risk_monitor"])
def test_only_explicit_paper_composition_can_share_execution_risk_order_entry_port(
    mode: str,
) -> None:
    service = _service_config()
    execution_options = service["components"]["execution"]["settings"]["options"]
    execution_options["paper_composition"] = mode
    risk_settings = service["components"]["risk"]["settings"]
    risk_settings["bind_ports"] = ["service.order_entry"]
    risk_settings["options"] = {
        "role": "account_owner",
        "account_policy_ref": "account-a-policy-v1",
    }

    parsed = parse_service_config(service)
    assert parsed.components["execution"]["settings"]["options"]["paper_composition"] == mode
    assert parsed.components["risk"]["settings"]["options"]["account_policy_ref"] == (
        "account-a-policy-v1"
    )

    execution_options.pop("paper_composition")
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(service)
    assert exc_info.value.code == "PORT_CONFLICT"


def test_paper_execution_risk_port_sharing_rejects_unversioned_policy() -> None:
    service = _service_config()
    service["components"]["execution"]["settings"]["options"][
        "paper_composition"
    ] = "execution_risk"
    risk_settings = service["components"]["risk"]["settings"]
    risk_settings["bind_ports"] = ["service.order_entry"]
    risk_settings["options"] = {
        "role": "account_owner",
        "account_policy_ref": "mutable-latest",
    }

    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(service)
    assert exc_info.value.code == "VERSIONED_REFERENCE_REQUIRED"


def test_component_registry_options_and_local_instance_refs_are_closed() -> None:
    service = _service_config()
    service["components"]["execution"]["settings"]["options"]["provider_session_ref"] = (
        "not-present"
    )
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(service)
    assert exc_info.value.code == "UNKNOWN_REFERENCE"

    service = _service_config()
    service["components"]["gateway"]["settings"]["options"]["role"] = "unknown-role"
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(service)
    assert exc_info.value.code == "UNKNOWN_COMPONENT_ROLE"

    service = _service_config()
    service["components"]["gateway"]["settings"]["bind_ports"] = ["service.order_entry"]
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(service)
    assert exc_info.value.code == "PORT_ROLE_MISMATCH"


def test_group_policy_uses_internal_port_and_versioned_reference() -> None:
    service = _service_config()
    risk_settings = service["components"]["risk"]["settings"]
    risk_settings["bind_ports"] = ["service.group_admission"]
    risk_settings["options"] = {
        "role": "group_policy",
        "group_policy_ref": "group-policy-v3",
    }
    parsed = parse_service_config(service)
    assert parsed.to_public_dict()["components"]["risk"]["settings"]["bind_ports"] == [
        "service.group_admission"
    ]

    risk_settings["options"]["group_policy_ref"] = "mutable-latest"
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(service)
    assert exc_info.value.code == "VERSIONED_REFERENCE_REQUIRED"


@pytest.mark.parametrize(
    ("endpoint", "expected_code"),
    [
        ("tcp://user:secret@127.0.0.1:7000", "INVALID_ENDPOINT"),
        ("tcp://*:7000", "INVALID_ENDPOINT"),
        ("ipc:///tmp/test.sock", "INVALID_ENDPOINT"),
        ("tcp://127.0.0.1:70000", "INVALID_ENDPOINT"),
    ],
)
def test_endpoint_validation_is_fixed_side_and_secret_free(
    endpoint: str, expected_code: str
) -> None:
    raw = _strategy_config([_client_binding("store.market")])
    raw["communication"]["profiles"]["rpc"]["endpoints"]["market_connect"] = endpoint
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_client_config(raw)
    assert exc_info.value.code == expected_code
    assert "secret" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("endpoint_key", "code"),
    [("rpc_bind", "INVALID_ENDPOINT_SIDE"), ("market_bind", "INVALID_ENDPOINT_SIDE")],
)
def test_client_rejects_service_bind_endpoints(endpoint_key: str, code: str) -> None:
    raw = _strategy_config([_client_binding("store.account", account_ref="account-a")])
    raw["communication"]["profiles"]["rpc"]["endpoints"] = {endpoint_key: "tcp://127.0.0.1:7000"}
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_client_config(raw)
    assert exc_info.value.code == code


def test_service_requires_component_and_valid_listener_transport() -> None:
    service = _service_config()
    service["components"] = {}
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(service)
    assert exc_info.value.code == "EMPTY_COMPONENTS"

    service = _service_config()
    service["transport_ref"] = "not-present"
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(service)
    assert exc_info.value.code == "UNKNOWN_REFERENCE"


def test_equivalent_ipv6_listener_addresses_conflict_after_canonicalization() -> None:
    service = _service_config()
    profiles = service["communication"]["profiles"]
    assert type(profiles) is dict
    profiles["listener"]["endpoints"]["rpc_bind"] = "tcp://[::1]:7101"
    profiles["second-listener"] = _read_profile("rpc_bind", "tcp://[0:0:0:0:0:0:0:1]:7101")
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(service)
    assert exc_info.value.code == "DUPLICATE_BIND_ENDPOINT"


def test_parse_is_detached_and_config_models_are_deeply_immutable() -> None:
    raw = _yaml(STRATEGY_CLIENT_YAML)
    original = copy.deepcopy(raw)
    parsed = parse_client_config(raw)
    assert raw == original
    raw["clients"]["gateway"]["settings"]["bindings"].clear()
    assert parsed.to_public_dict()["clients"]["gateway"]["settings"]["bindings"]
    assert original["clients"]["gateway"]["settings"]["bindings"]
    with pytest.raises(TypeError):
        parsed.clients["gateway"]["settings"]["options"]["request_timeout_ms"] = 3

    public = parsed.to_public_dict()
    public["clients"]["gateway"]["settings"]["bindings"].clear()
    assert parsed.to_public_dict()["clients"]["gateway"]["settings"]["bindings"]


@pytest.mark.parametrize(
    ("parser", "config_type", "raw"),
    [
        (parse_client_config, ClientConfig, _strategy_config([])),
        (parse_service_config, ServiceConfig, _service_config()),
    ],
)
def test_config_models_only_construct_through_validating_parser(
    parser: object, config_type: type[object], raw: dict[str, object]
) -> None:
    parsed = parser(raw)  # type: ignore[operator]
    assert isinstance(parsed, config_type)
    with pytest.raises(TypeError, match="parse_"):
        config_type()
    with pytest.raises(TypeError, match="parse_"):
        replace(parsed)
    assert isinstance(parsed.to_public_dict(), dict)
    assert len(parsed.fingerprint()) == 64


def test_fingerprint_is_canonical_stable_and_normalized() -> None:
    first = parse_client_config(
        _strategy_config([_client_binding("store.account", account_ref="account-a")])
    )
    second_raw = _strategy_config([_client_binding("store.account", account_ref="account-a")])
    second_raw = dict(reversed(list(second_raw.items())))
    second = parse_client_config(second_raw)
    assert first.fingerprint() == second.fingerprint()
    assert len(first.fingerprint()) == 64


def test_service_parse_never_resolves_policy_or_security_references() -> None:
    service = _service_config()
    service["registration_policy_ref"] = "unregistered-but-well-formed-policy"
    service["communication"]["profiles"]["listener"]["security_profile_ref"] = "not-resolved-here"
    parsed = parse_service_config(service)
    assert parsed.registration_policy_ref == "unregistered-but-well-formed-policy"


def test_analytics_quotas_round_trip_without_changing_option_names() -> None:
    service = _service_config()
    risk_settings = service["components"]["risk"]["settings"]
    risk_settings["bind_ports"] = ["service.risk_analysis"]
    risk_settings["options"] = {
        "role": "portfolio_analytics",
        "model_profile_ref": "model-profile-v1",
        "allowed_source_modes": ["research", "imported"],
        "max_concurrent_tasks": 4,
        "max_input_bytes": 1_048_576,
    }

    parsed = parse_service_config(service)
    public = parsed.to_public_dict()
    options = public["components"]["risk"]["settings"]["options"]
    assert options["max_concurrent_tasks"] == 4
    assert options["max_input_bytes"] == 1_048_576
    reparsed = parse_service_config(public)
    assert reparsed.to_public_dict() == public
    assert reparsed.fingerprint() == parsed.fingerprint()


@pytest.mark.parametrize(
    ("option", "value"),
    [("max_concurrent_tasks", 65), ("max_input_bytes", 67_108_865)],
)
def test_analytics_quotas_have_fixed_bounds(option: str, value: int) -> None:
    service = _service_config()
    risk_settings = service["components"]["risk"]["settings"]
    risk_settings["bind_ports"] = ["service.risk_analysis"]
    risk_settings["options"] = {
        "role": "portfolio_analytics",
        "model_profile_ref": "model-profile-v1",
        "allowed_source_modes": ["research"],
        option: value,
    }
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(service)
    assert exc_info.value.code == "INVALID_VALUE"


def test_error_constructor_has_only_safe_code_and_path() -> None:
    error = ServiceConfigError("UNKNOWN_FIELD", "$.settings")
    assert error.code == "UNKNOWN_FIELD"
    assert error.path == "$.settings"
    assert str(error) == "UNKNOWN_FIELD at $.settings"
