"""Registry wiring of the Gateway control port and its explicit Runtime client (63.12).

The control surface is served on ``service.direct_write`` by the Gateway
``trading_owner`` role; a Runtime client can be configured explicitly with the
``runtime`` alias and only the direct-write mount. Configuration absence is
covered in ``test_iter63_gateway_runtime_control_port`` (``CONTROL_UNAVAILABLE``).
"""
from __future__ import annotations

import pytest
from bt_api_gateway.control_wire import (
    CONTROL_OPERATIONS,
    CONTROL_READ_OPERATIONS,
    CONTROL_WRITE_OPERATIONS,
)

from bt_api_py.service_config import (
    _CLIENT_ALIAS_MOUNTS,
    _COMPONENT_PORTS,
    _SERVICE_PORTS,
    ServiceConfigError,
    parse_client_config,
)
from bt_api_py.service_registry import (
    _GATEWAY_CONTROL_OPERATIONS,
    _GATEWAY_CONTROL_READ_OPERATIONS,
    _GATEWAY_CONTROL_WRITE_OPERATIONS,
)


def binding(mount: str) -> dict[str, object]:
    result = {
        "mount": mount, "service_id": "gateway-service", "instance_id": "gateway-a",
        "scope_ref": "scope-a", "transport_ref": "rpc",
    }
    if mount in {"application.direct_write", "application.risk_read"}:
        result["account_ref"] = "account-a"
    return result


def runtime_client(mount: str) -> dict[str, object]:
    return {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        "registration": {"application_ref": "runtime-a"},
        "clients": {"runtime": {"config_schema_version": 1,
                                "settings": {"bindings": [binding(mount)]}}},
        "communication": {
            "config_schema_version": 1,
            "profiles": {"rpc": {
                "backend": "zmq", "config_schema_version": 1,
                "endpoints": {"rpc_connect": "tcp://127.0.0.1:7101"},
                "security_profile_ref": "test-security",
            }},
        },
    }


def test_the_gateway_trading_owner_serves_the_control_port():
    assert _COMPONENT_PORTS["gateway"]["trading_owner"] == frozenset(
        {"service.direct_write", "service.account_read", "service.market_source", "service.calendar_read"})
    assert "service.direct_write" in _SERVICE_PORTS
    assert CONTROL_OPERATIONS <= _GATEWAY_CONTROL_OPERATIONS


def test_control_operations_match_the_registry_write_and_read_partition():
    assert CONTROL_WRITE_OPERATIONS == _GATEWAY_CONTROL_WRITE_OPERATIONS
    assert CONTROL_READ_OPERATIONS == _GATEWAY_CONTROL_READ_OPERATIONS
    assert CONTROL_OPERATIONS == _GATEWAY_CONTROL_OPERATIONS


def test_a_runtime_client_can_bind_only_the_control_mount():
    parsed = parse_client_config(runtime_client("application.direct_write"))
    assert parsed.clients["runtime"]["settings"]["bindings"][0]["mount"] == "application.direct_write"
    assert _CLIENT_ALIAS_MOUNTS["runtime"] == frozenset({"application.direct_write"})


def test_a_runtime_client_cannot_bind_a_trading_mount():
    # application.risk_read is a valid application-client mount but is not on
    # the runtime alias; the alias check names the mismatch.
    with pytest.raises(ServiceConfigError) as refused:
        parse_client_config(runtime_client("application.risk_read"))
    assert refused.value.code == "CLIENT_ALIAS_MOUNT_MISMATCH"
