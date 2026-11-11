"""D63-35 option b: Monitor-driven Risk control is retired (iteration 64).

``MonitorRiskControlOwner`` used to apply Monitor FREEZE/STOP_NEW/RESUME
commands to a Risk gate and the Registry. It has been removed; Monitor stays an
observer. Any configuration that still selects the Monitor ``control`` role is
refused with the finite code ``MONITOR_CONTROL_RETIRED`` at component
validation, before a trusted resource provider runs, and is never silently
ignored. Evidence level: UNIT (no listener, provider or account).
"""

from __future__ import annotations

import importlib

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.service_components import (
    ServiceComponentError,
    component_specs,
    validate_components,
)
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

_SERVICE_ID = "monitor-control-retired-service"
_SERVICE_INSTANCE = "monitor"
_SCOPE_REF = "monitor-control-retired-route-v1"
_CLIENT_PRINCIPAL = "monitor-control-retired-client"
_CLIENT = WireIdentity(
    WireRole.APPLICATION_CLIENT,
    _CLIENT_PRINCIPAL,
    "monitor-control-retired-app",
    delegate_ref="operator-delegation:retired-desk",
)
_WIRE_SCOPE = WireScope(
    execution_domain_ref="paper:monitor-control-retired",
    account_ref="retired-account",
)


def _service_config(*, role: str | None, bind_ports: list[str]):
    settings: dict[str, object] = {"instance_id": _SERVICE_INSTANCE, "bind_ports": bind_ports}
    if role is not None:
        settings["options"] = {"role": role}
    return parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": _SERVICE_ID,
            "scope_ref": "monitor-control-retired-root",
            "transport_ref": "monitor-listener",
            "registration_policy_ref": "monitor-registration-v1",
            "service_requirements_ref": "monitor-requirements-v1",
            "components": {"monitor": {"config_schema_version": 2, "settings": settings}},
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "monitor-listener": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": "tcp://127.0.0.1:7612"},
                        "security_profile_ref": "monitor-server-key",
                    }
                },
            },
        }
    )


def test_monitor_risk_control_owner_is_removed() -> None:
    module = importlib.import_module("bt_api_py.service_monitor_control")

    assert not hasattr(module, "MonitorRiskControlOwner")
    assert "MonitorRiskControlOwner" not in module.__all__
    with pytest.raises(ImportError):
        from bt_api_py.service_monitor_control import MonitorRiskControlOwner  # noqa: F401
    assert module.MONITOR_CONTROL_RETIRED == "MONITOR_CONTROL_RETIRED"


def test_runtime_construction_is_refused_with_the_finite_code() -> None:
    from bt_api_py.service_monitor_control import MonitorControlRuntime

    for args, kwargs in (((), {}), ((object(), {}), {}), ((None,), {"owners": {}})):
        with pytest.raises(ServiceComponentError) as refused:
            MonitorControlRuntime(*args, **kwargs)
        assert refused.value.code == "MONITOR_CONTROL_RETIRED"


def test_component_validation_refuses_the_control_role() -> None:
    config = _service_config(role="control", bind_ports=["service.monitor_control"])

    with pytest.raises(ServiceComponentError) as refused:
        validate_components(component_specs(config.components))

    assert refused.value.code == "MONITOR_CONTROL_RETIRED"


def test_other_monitor_roles_still_validate() -> None:
    config = _service_config(role=None, bind_ports=["service.monitor_query"])

    (prepared,) = validate_components(component_specs(config.components))

    assert set(prepared.operations_by_port) == {"service.monitor_query"}


def test_host_start_is_refused_before_any_resource_provider_call(tmp_path) -> None:
    server_public, server_secret = zmq.curve_keypair()
    client_public, _client_secret = zmq.curve_keypair()
    config = _service_config(role="control", bind_ports=["service.monitor_control"])
    provider_calls: list[str] = []

    def provider(component, _instance_id, _options, _scope):
        provider_calls.append(component)
        raise AssertionError("a retired control role must not resolve resources")

    resource = ResourceGrant(
        _SERVICE_INSTANCE,
        "service.monitor_control",
        _SCOPE_REF,
        _WIRE_SCOPE,
        frozenset({"monitor.control"}),
    )
    host = ServiceHost(
        config,
        TrustedServiceAuthority(
            identity=WireIdentity(WireRole.SERVICE, "monitor-retired-service", _SERVICE_ID),
            scope=_WIRE_SCOPE,
            scope_ref=config.scope_ref,
            scope_version="monitor-retired-scope-v1",
            security_ref="monitor-server-key",
            security_version="monitor-retired-curve-v1",
            registration_policy_ref=config.registration_policy_ref,
            registration_policy_version="monitor-retired-registration-v1",
            service_requirements_ref=config.service_requirements_ref,
            service_requirements_version="monitor-retired-requirements-v1",
            curve_public_key=server_public,
            curve_secret_key=server_secret,
            principals={client_public: _CLIENT_PRINCIPAL},
            grants={_CLIENT_PRINCIPAL: (PrincipalGrant(_CLIENT, (resource,)),)},
        ),
        str(tmp_path / "monitor-retired-registry.sqlite3"),
        provider,
    )
    try:
        with pytest.raises(ServiceHostError) as refused:
            host.start()
        assert refused.value.code == "MONITOR_CONTROL_RETIRED"
        assert provider_calls == []
        assert host.state == "failed"
    finally:
        host.close()
