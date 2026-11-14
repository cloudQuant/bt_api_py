"""Synthetic startup boundaries for retired execution service owner roles."""
from __future__ import annotations

import threading
from enum import Enum
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

import bt_api_py.ctp_execution_runtime as ctp_runtime
import bt_api_py.service_config as service_config
import bt_api_py.service_components as components
import bt_api_py.service_host as host_module
from bt_api_py.service_config import ServiceConfigError, parse_service_config


RETIRED = ("account_owner", "account_group_owner")
CODE = "LEGACY_EXECUTION_OWNER_RETIRED"
SCOPE = WireScope(execution_domain_ref="synthetic", account_ref="account")


@pytest.mark.parametrize("role", RETIRED)
def test_retired_owner_removed_from_builtin_role_tables(role):
    assert ("execution", role) not in components._ROLE_BUILTINS
    assert role not in service_config._COMPONENT_PORTS["execution"]


def _component(name, role, port, options=None):
    return {"config_schema_version": 2, "settings": {
        "instance_id": name, "bind_ports": [port],
        "options": options if options is not None else {"role": role},
    }}


def _config(name, role, port, options=None):
    return {
        "service_config_schema_version": 1, "process_role": "service",
        "service_id": "service", "scope_ref": "scope", "transport_ref": "listener",
        "registration_policy_ref": "registration", "service_requirements_ref": "requirements",
        "components": {name: _component(name, role, port, options)},
        "communication": {"config_schema_version": 1, "profiles": {"listener": {
            "backend": "zmq", "config_schema_version": 1,
            "endpoints": {"rpc_bind": "tcp://127.0.0.1:7201"},
            "security_profile_ref": "security",
        }}},
    }


def _spec(role, port="service.order_entry"):
    return components.ComponentSpec("execution", "owner", (port,), {"role": role})


@pytest.mark.parametrize("role", RETIRED)
@pytest.mark.parametrize("port", ("service.order_entry", "service.order_query", "service.owner_control"))
def test_config_rejects_retired_owner_for_entry_query_and_control_ports(role, port):
    with pytest.raises(ServiceConfigError) as exc:
        parse_service_config(_config("execution", role, port))
    assert exc.value.code == CODE
    assert exc.value.path == "$.components.execution.settings.options.role"


@pytest.mark.parametrize("role", RETIRED)
def test_component_validation_rejects_before_imports(role, monkeypatch):
    calls = []
    monkeypatch.setattr(components.importlib, "import_module", lambda name: calls.append(name))
    earlier = components.ComponentSpec("monitor", "monitor", ("service.monitor_query",), {})
    with pytest.raises(components.ServiceComponentError) as exc:
        components.validate_components((earlier, _spec(role)))
    assert exc.value.code == CODE
    assert calls == []


@pytest.mark.parametrize("role", RETIRED)
def test_direct_build_rejects_manually_prepared_owner_before_factory(role):
    calls = []
    spec = _spec(role)
    prepared = components.PreparedComponent(spec, spec.options, object(), {}, {})
    with pytest.raises(components.ServiceComponentError) as exc:
        components.build_component(prepared, scope=SCOPE, resource_provider=lambda *a: calls.append(a))
    assert exc.value.code == CODE
    assert calls == []


@pytest.mark.parametrize("role", RETIRED)
def test_direct_build_rejects_retired_options_disguised_by_spec(role):
    calls = []
    spec = _spec("group_account_owner")
    prepared = components.PreparedComponent(spec, {"role": role}, object(), {}, {})
    with pytest.raises(components.ServiceComponentError) as exc:
        components.build_component(prepared, scope=SCOPE, resource_provider=lambda *a: calls.append(a))
    assert exc.value.code == CODE
    assert calls == []


def test_execution_build_uses_one_stable_options_snapshot(monkeypatch):
    nested = {"reference": "synthetic"}
    original = {"role": "group_coordinator", "nested": nested}
    spec = _spec("group_coordinator")
    prepared = components.PreparedComponent(spec, original, object(), {}, {})
    observed = []

    def provider(_component, _instance, options, _scope):
        original["role"] = "account_owner"
        observed.append(("provider", options, options["role"], options["nested"]))
        return components.ComponentResources.borrowed({}, ready=lambda: True)

    def capture_builder(_resources, *, spec, options, module):
        observed.append(("builder", options, options["role"], options["nested"]))
        raise components.ServiceComponentError("TEST_HALT")

    monkeypatch.setattr(components, "_account_risk_policy_metadata", capture_builder)
    with pytest.raises(components.ServiceComponentError) as exc:
        components.build_component(prepared, scope=SCOPE, resource_provider=provider)
    assert exc.value.code == "TEST_HALT"
    assert [entry[2] for entry in observed] == ["group_coordinator", "group_coordinator"]
    assert observed[0][1] is observed[1][1]
    assert observed[0][3] is observed[1][3] is nested
    with pytest.raises(TypeError):
        observed[0][1]["role"] = "account_owner"


class _RoleString(str):
    pass


class _RoleEnum(str, Enum):
    RETIRED = "account_owner"
    ACTIVE = "group_coordinator"


@pytest.mark.parametrize("role", [_RoleString("account_owner"), _RoleEnum.RETIRED,
                                   _RoleString("group_coordinator"), _RoleEnum.ACTIVE])
@pytest.mark.parametrize("location", ["spec", "prepared"])
def test_execution_build_rejects_non_exact_string_roles(role, location):
    calls = []
    spec = _spec(role if location == "spec" else "group_coordinator")
    options = {"role": role if location == "prepared" else "group_coordinator"}
    prepared = components.PreparedComponent(spec, options, object(), {}, {})
    with pytest.raises(components.ServiceComponentError) as exc:
        components.build_component(prepared, scope=SCOPE, resource_provider=lambda *a: calls.append(a))
    assert exc.value.code == "COMPONENT_OPTIONS_INVALID"
    assert calls == []


@pytest.mark.parametrize(("spec_role", "options_role"), [
    ("group_coordinator", "group_account_owner"),
    ("group_account_owner", "group_coordinator"),
])
def test_execution_build_rejects_mismatched_active_roles_before_factory(spec_role, options_role):
    calls = []
    spec = _spec(spec_role)
    prepared = components.PreparedComponent(spec, {"role": options_role}, object(), {}, {})
    with pytest.raises(components.ServiceComponentError) as exc:
        components.build_component(prepared, scope=SCOPE, resource_provider=lambda *a: calls.append(a))
    assert exc.value.code == "COMPONENT_ROLE_MISMATCH"
    assert calls == []


def test_risk_build_keeps_original_options_object():
    options = {"role": "account_owner"}
    spec = components.ComponentSpec("risk", "risk", ("service.order_query",), options)
    prepared = components.PreparedComponent(spec, options, object(), {}, {})
    calls = []
    with pytest.raises(components.ServiceComponentError) as exc:
        components.build_component(
            prepared, scope=SCOPE, resource_provider=lambda *args: calls.append(args[2])
        )
    assert exc.value.code == "INVALID_COMPONENT_RESOURCES"
    assert calls == [options]
    assert calls[0] is options


@pytest.mark.parametrize(("role", "code"), [
    ("account_owner", CODE), ("account_group_owner", CODE),
    (_RoleString("account_owner"), "COMPONENT_OPTIONS_INVALID"),
    (_RoleEnum.RETIRED, "COMPONENT_OPTIONS_INVALID"),
])
def test_host_rejects_manual_config_before_lock_factory_or_transport(role, code, monkeypatch, tmp_path):
    parsed = parse_service_config(_config("monitor", None, "service.monitor_query", {}))
    # Simulate an already-constructed configuration bypassing the parser.
    object.__setattr__(parsed, "components", {"execution": _component("execution", role, "service.order_entry")})
    authority = host_module.TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, "principal", "service"), scope=SCOPE,
        scope_ref="scope", scope_version="v1", security_ref="security", security_version="v1",
        registration_policy_ref="registration", registration_policy_version="v1",
        service_requirements_ref="requirements", service_requirements_version="v1",
        curve_public_key=b"a" * 40, curve_secret_key=b"b" * 40,
        principals={b"c" * 40: "client"}, grants={"client": ()},
    )
    calls = []
    monkeypatch.setattr(host_module, "migration_start_guard", lambda *a, **kw: calls.append("lock"))
    monkeypatch.setattr(host_module, "build_component", lambda *a, **kw: calls.append("build"))
    monkeypatch.setattr(host_module.ServiceHost, "_listener_profile", lambda *a: calls.append("transport"))
    host = host_module.ServiceHost(parsed, authority, tmp_path / "registry", lambda *a: calls.append("factory"))
    with pytest.raises(host_module.ServiceHostError) as exc:
        host.start()
    assert exc.value.code == code
    assert calls == []
    assert host.state == "failed"


@pytest.mark.parametrize(("name", "role", "port", "options"), [
    ("execution", "group_account_owner", "service.order_query", {"role": "group_account_owner"}),
    ("execution", "group_coordinator", "service.order_group_entry", {"role": "group_coordinator", "portfolio_ref": "portfolio"}),
    ("risk", "account_owner", "service.order_query", {"role": "account_owner", "account_policy_ref": "policy-v1"}),
    ("gateway", "account_session_worker", "service.account_read", {"role": "account_session_worker", "account_session_ref": "session-v1"}),
    ("monitor", None, "service.monitor_query", {}),
])
def test_other_components_and_roles_parse(name, role, port, options):
    parsed = parse_service_config(_config(name, role, port, options))
    assert name in parsed.components


def test_group_account_owner_still_validates():
    spec = components.ComponentSpec(
        "execution", "owner", ("service.order_entry", "service.order_query"),
        {"role": "group_account_owner"},
    )
    prepared, = components.validate_components((spec,))
    assert prepared.spec == spec
    assert prepared.options["role"] == spec.options["role"]
    assert prepared.operations_by_port["service.order_query"]


def test_group_coordinator_normalized_role_matches_spec():
    spec = components.ComponentSpec(
        "execution", "coordinator", ("service.order_group_entry",),
        {"role": "group_coordinator", "portfolio_ref": "portfolio"},
    )
    prepared, = components.validate_components((spec,))
    assert prepared.options["role"] == spec.options["role"]


def test_direct_ctp_sentinel_still_reaches_runtime_bootstrap(monkeypatch):
    calls = []
    class FakeRuntime:
        def __init__(self, config, instance_id):
            calls.append((config, instance_id))
        def bootstrap(self):
            return "synthetic resources"
    monkeypatch.setattr(ctp_runtime, "CtpExecutionRuntime", FakeRuntime)
    provider = object.__new__(ctp_runtime.CtpExecutionResourceProvider)
    provider.config = SimpleNamespace(service_scope=SCOPE)
    provider.last_runtime = None
    provider._used = False
    provider._lock = threading.Lock()
    assert provider("execution", "direct", ctp_runtime.DIRECT_MANAGED_OWNER_OPTIONS, SCOPE) == "synthetic resources"
    assert len(calls) == 1
