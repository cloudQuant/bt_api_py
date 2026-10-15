from __future__ import annotations

from typing import Any

import pytest

from bt_api_py.service_components import (
    ComponentSpec,
    ServiceComponentError,
    component_specs,
    validate_components,
)
from bt_api_py.service_config import ServiceConfigError, parse_service_config


_GROUP_COORDINATOR_OPTIONS = {
    "role": "group_coordinator",
    "portfolio_ref": "portfolio-group-a",
}


def _service_config(options: dict[str, Any]) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "group-coordinator-service",
        "scope_ref": "group-coordinator-root-scope",
        "transport_ref": "listener",
        "registration_policy_ref": "group-coordinator-access-v1",
        "service_requirements_ref": "group-coordinator-requirements-v1",
        "components": {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "group-coordinator-a",
                    "bind_ports": ["service.order_group_entry"],
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
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:7210"},
                    "security_profile_ref": "group-coordinator-security-v1",
                }
            },
        },
    }


def test_group_coordinator_parser_and_component_validation_preserve_portfolio_binding() -> None:
    parsed = parse_service_config(_service_config(dict(_GROUP_COORDINATOR_OPTIONS)))
    prepared = validate_components(component_specs(parsed.components))

    assert len(prepared) == 1
    assert prepared[0].spec.component == "execution"
    assert prepared[0].spec.bind_ports == ("service.order_group_entry",)
    assert dict(prepared[0].options) == _GROUP_COORDINATOR_OPTIONS


@pytest.mark.parametrize(
    ("options", "code", "path"),
    [
        (
            {**_GROUP_COORDINATOR_OPTIONS, "accept_any_portfolio": True},
            "UNKNOWN_FIELD",
            "$.components.execution.settings.options",
        ),
        (
            {"role": "group_coordinator", "portfolio_ref": "invalid portfolio"},
            "INVALID_REFERENCE",
            "$.components.execution.settings.options.portfolio_ref",
        ),
    ],
    ids=("reject-unknown-option", "reject-invalid-portfolio-reference"),
)
def test_group_coordinator_parser_rejects_unknown_fields_and_invalid_references(
    options: dict[str, Any], code: str, path: str
) -> None:
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(_service_config(options))

    assert exc_info.value.code == code
    assert exc_info.value.path == path


@pytest.mark.parametrize(
    "options",
    [
        {**_GROUP_COORDINATOR_OPTIONS, "accept_any_portfolio": True},
        {"role": "group_coordinator", "portfolio_ref": "invalid portfolio"},
    ],
    ids=("reject-bypassed-unknown-option", "reject-bypassed-invalid-reference"),
)
def test_group_component_validation_rechecks_options_when_parser_is_bypassed(
    options: dict[str, Any],
) -> None:
    spec = ComponentSpec(
        "execution",
        "group-coordinator-a",
        ("service.order_group_entry",),
        options,
    )

    with pytest.raises(ServiceComponentError) as exc_info:
        validate_components((spec,))

    assert exc_info.value.code == "COMPONENT_OPTIONS_INVALID"
