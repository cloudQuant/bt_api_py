"""Iteration 68.03 config registration for the read-only observation query.

The parser must be able to select the new Monitor role and the bounded
telemetry options it added, while refusing unknown keys, control ports, and
invalid or partial bounds exactly as the runtime validators do.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bt_api_base.service_protocol import WireScope
from bt_api_monitor.durable import DurableOutbox
from bt_api_monitor.service_component import MonitorReadTarget
from bt_api_monitor.service_telemetry import MonitorTelemetryServiceError

from bt_api_py.service_components import (
    ComponentResources,
    build_component,
    component_specs,
    validate_components,
)
from bt_api_py.service_config import ServiceConfigError, parse_client_config, parse_service_config

_SCOPE = WireScope("paper:demo", account_ref="account-a", strategy_ref="strategy-a")
_OTHER_SCOPE = WireScope("paper:other", account_ref="account-b", strategy_ref="strategy-b")


def _service_config(
    *,
    role: str,
    ports: list[str],
    options: dict,
    instance_id: str = "monitor-observation",
) -> dict:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "monitor-service",
        "scope_ref": "monitor-scope",
        "transport_ref": "listener",
        "registration_policy_ref": "monitor-policy-v1",
        "service_requirements_ref": "monitor-requirements-v1",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": instance_id,
                    "bind_ports": ports,
                    "options": options,
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "listener": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:7699"},
                    "security_profile_ref": "monitor-security-v1",
                }
            },
        },
    }


def _observation_options(**overrides) -> dict:
    options = {"role": "observation_query", "epoch": 3, "retention_floor_sequence": 5}
    options.update(overrides)
    return options


def _parse_error(code: str, raw: dict) -> None:
    with pytest.raises(ServiceConfigError) as captured:
        parse_service_config(raw)
    assert captured.value.code == code


def _client_config(options: dict) -> dict:
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": "strategy-a"},
        "clients": {
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "runtime.telemetry",
                            "service_id": "monitor-service",
                            "instance_id": "monitor-ingress",
                            "scope_ref": "strategy-a-v1",
                            "transport_ref": "telemetry",
                            "required": False,
                        }
                    ],
                    "options": options,
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "telemetry": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": "tcp://127.0.0.1:7781"},
                    "security_profile_ref": "telemetry-keys",
                }
            },
        },
    }


def test_observation_query_config_builds_the_read_only_component(tmp_path: Path) -> None:
    config = parse_service_config(
        _service_config(
            role="observation_query",
            ports=["service.monitor_query"],
            options=_observation_options(cursor_ttl_seconds=60),
        )
    )
    assert config.to_public_dict()["components"]["monitor"]["settings"]["options"] == {
        "role": "observation_query",
        "epoch": 3,
        "retention_floor_sequence": 5,
        "cursor_ttl_seconds": 60,
    }
    prepared = validate_components(component_specs(config.components))[0]
    assert prepared.operations_by_port == {
        "service.monitor_query": frozenset({"observation.query"})
    }
    assert all(not operations for operations in prepared.write_operations_by_port.values())

    path = tmp_path / "observation.sqlite"
    DurableOutbox(path)
    resources = ComponentResources.borrowed(
        {
            "outbox": DurableOutbox(path, read_only=True),
            "targets": {_SCOPE: MonitorReadTarget("monitor:strategy-a", ("order.status",))},
            "principals": frozenset({"application:monitor"}),
            "hmac_key": b"local-fake-observation-key-32-byte",
        },
        ready=lambda: True,
    )
    component = build_component(
        prepared,
        scope=_SCOPE,
        resource_provider=lambda *_args: resources,
    )
    try:
        assert list(component.handlers) == ["observation.query"]
        assert component.internal_ports == {}
        assert "monitor.control" not in component.handlers
        assert set(component.handlers) == {"observation.query"}
    finally:
        component.close()


def test_observation_query_refuses_unknown_options_and_any_control_port() -> None:
    _parse_error(
        "UNKNOWN_FIELD",
        _service_config(
            role="observation_query",
            ports=["service.monitor_query"],
            options=_observation_options(session_ref="client-supplied"),
        ),
    )
    for port in ("service.monitor_control", "service.owner_control"):
        _parse_error(
            "PORT_ROLE_MISMATCH",
            _service_config(
                role="observation_query",
                ports=[port],
                options=_observation_options(),
            ),
        )
    # The read-only role cannot opt into a control capability either.
    _parse_error(
        "PORT_ROLE_MISMATCH",
        _service_config(
            role="observation_query",
            ports=["service.monitor_query", "service.monitor_control"],
            options=_observation_options(),
        ),
    )


def test_observation_query_requires_the_exact_constructor_bounds() -> None:
    missing_epoch = _observation_options()
    missing_epoch.pop("epoch")
    _parse_error(
        "MISSING_FIELD",
        _service_config(
            role="observation_query", ports=["service.monitor_query"], options=missing_epoch
        ),
    )
    missing_floor = _observation_options()
    missing_floor.pop("retention_floor_sequence")
    _parse_error(
        "MISSING_FIELD",
        _service_config(
            role="observation_query", ports=["service.monitor_query"], options=missing_floor
        ),
    )
    for epoch in (0, -1, (1 << 31)):
        _parse_error(
            "INVALID_VALUE",
            _service_config(
                role="observation_query",
                ports=["service.monitor_query"],
                options=_observation_options(epoch=epoch),
            ),
        )
    for floor in (-1, (1 << 63)):
        _parse_error(
            "INVALID_VALUE",
            _service_config(
                role="observation_query",
                ports=["service.monitor_query"],
                options=_observation_options(retention_floor_sequence=floor),
            ),
        )
    for ttl in (0, 3601):
        _parse_error(
            "INVALID_VALUE",
            _service_config(
                role="observation_query",
                ports=["service.monitor_query"],
                options=_observation_options(cursor_ttl_seconds=ttl),
            ),
        )
    _parse_error(
        "INVALID_TYPE",
        _service_config(
            role="observation_query",
            ports=["service.monitor_query"],
            options=_observation_options(cursor_ttl_seconds=True),
        ),
    )


def test_telemetry_ingress_registers_new_bounds_and_refuses_invalid_ones() -> None:
    config = parse_service_config(
        _service_config(
            role="telemetry_ingress",
            ports=["service.telemetry_ingress"],
            options={
                "role": "telemetry_ingress",
                "max_event_bytes": 2048,
                "max_events_per_second": 2.5,
            },
        )
    )
    options = config.to_public_dict()["components"]["monitor"]["settings"]["options"]
    assert options["max_event_bytes"] == 2048
    assert options["max_events_per_second"] == 2.5
    prepared = validate_components(component_specs(config.components))[0]
    assert prepared.options["max_event_bytes"] == 2048
    assert prepared.options["max_events_per_second"] == 2.5

@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"max_event_bytes": 0}, "INVALID_VALUE"),
        ({"max_event_bytes": 8193}, "INVALID_VALUE"),
        ({"max_event_bytes": 1.5}, "INVALID_TYPE"),
        ({"max_events_per_second": 0}, "INVALID_VALUE"),
        ({"max_events_per_second": 10_001}, "INVALID_VALUE"),
        ({"max_events_per_second": "fast"}, "INVALID_TYPE"),
        ({"retention_seconds": 86_401}, "INVALID_VALUE"),
        ({"max_streams": 0}, "INVALID_VALUE"),
        ({"unknown_bound": 1}, "UNKNOWN_FIELD"),
    ],
)
def test_telemetry_ingress_refuses_invalid_bounds(overrides: dict, code: str) -> None:
    _parse_error(
        code,
        _service_config(
            role="telemetry_ingress",
            ports=["service.telemetry_ingress"],
            options={"role": "telemetry_ingress", **overrides},
        ),
    )


def test_telemetry_ingress_producer_principals_rows_are_bounded() -> None:
    row = {
        "scope": {"execution_domain_ref": "paper:demo", "strategy_ref": "strategy-a"},
        "principals": ["strategy:demo"],
    }
    config = parse_service_config(
        _service_config(
            role="telemetry_ingress",
            ports=["service.telemetry_ingress"],
            options={"role": "telemetry_ingress", "producer_principals": [row]},
        )
    )
    prepared = validate_components(component_specs(config.components))[0]
    assert set(prepared.options["producer_principals"]) == {
        WireScope("paper:demo", strategy_ref="strategy-a")
    }
    assert prepared.options["producer_principals"][
        WireScope("paper:demo", strategy_ref="strategy-a")
    ] == frozenset({"strategy:demo"})

@pytest.mark.parametrize(
    ("value", "code"),
    [
        ([], "INVALID_VALUE"),
        ([{"scope": {"execution_domain_ref": "paper:demo"}, "principals": []}], "INVALID_VALUE"),
        ([{"scope": {"strategy_ref": "strategy-a"}, "principals": ["p"]}], "MISSING_FIELD"),
        (
            [{"scope": {"execution_domain_ref": "paper:demo", "group_id": "g"}, "principals": ["p"]}],
            "UNKNOWN_FIELD",
        ),
        (
            [
                {
                    "scope": {"execution_domain_ref": "paper:demo"},
                    "principals": ["p"],
                },
                {
                    "scope": {"execution_domain_ref": "paper:demo"},
                    "principals": ["q"],
                },
            ],
            "INVALID_VALUE",
        ),
        (
            [{"scope": {"execution_domain_ref": "paper:demo"}, "principals": ["p", "p"]}],
            "INVALID_VALUE",
        ),
        ("not-a-list", "INVALID_TYPE"),
    ],
)
def test_telemetry_ingress_refuses_invalid_producer_principals(value: object, code: str) -> None:
    _parse_error(
        code,
        _service_config(
            role="telemetry_ingress",
            ports=["service.telemetry_ingress"],
            options={"role": "telemetry_ingress", "producer_principals": value},
        ),
    )


def test_partial_producer_principal_coverage_is_refused_at_build() -> None:
    config = parse_service_config(
        _service_config(
            role="telemetry_ingress",
            ports=["service.telemetry_ingress"],
            options={
                "role": "telemetry_ingress",
                "producer_principals": [
                    {
                        "scope": {
                            "execution_domain_ref": "paper:demo",
                            "account_ref": "account-a",
                            "strategy_ref": "strategy-a",
                        },
                        "principals": ["strategy:demo"],
                    }
                ],
            },
        )
    )
    prepared = validate_components(component_specs(config.components))[0]
    resources = ComponentResources.borrowed(
        {"targets": frozenset({_SCOPE, _OTHER_SCOPE})},
        ready=lambda: True,
    )
    with pytest.raises(MonitorTelemetryServiceError) as captured:
        build_component(
            prepared,
            scope=_SCOPE,
            resource_provider=lambda *_args: resources,
        )
    assert captured.value.code == "INVALID_PRODUCER_PRINCIPALS"

    # The same allowlist with full target coverage constructs normally.
    full = ComponentResources.borrowed(
        {"targets": frozenset({_SCOPE})},
        ready=lambda: True,
    )
    component = build_component(prepared, scope=_SCOPE, resource_provider=lambda *_args: full)
    try:
        assert isinstance(component.handlers["telemetry.append"], object)
    finally:
        component.close()


def test_monitor_client_registers_producer_bound_keys() -> None:
    config = parse_client_config(
        _client_config(
            {
                "telemetry_queue_max_event_bytes": 2048,
                "telemetry_queue_max_events_per_second": 1.5,
                "telemetry_retention_seconds": 60,
            }
        )
    )
    options = config.clients["monitor"]["settings"]["options"]
    assert options["telemetry_queue_max_event_bytes"] == 2048
    assert options["telemetry_queue_max_events_per_second"] == 1.5
    assert options["telemetry_retention_seconds"] == 60

    for overrides in (
        {"telemetry_queue_max_event_bytes": 0},
        {"telemetry_queue_max_event_bytes": 8193},
        {"telemetry_queue_max_events_per_second": -1},
        {"telemetry_queue_max_events_per_second": 10_001},
        {"telemetry_retention_seconds": 0},
        {"telemetry_retention_seconds": 86_401},
        {"telemetry_queue_unknown": 1},
    ):
        with pytest.raises(ServiceConfigError) as captured:
            parse_client_config(_client_config(overrides))
        assert captured.value.code in {"INVALID_VALUE", "UNKNOWN_FIELD"}
