"""Pure SDK R04 selection tests; no service, key file or account is opened."""

from __future__ import annotations

import copy
import builtins
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import bt_api_py.risk_service_config as config_module
import bt_api_py.risk_service_host as host_module


def selection(**changes):
    value = {
        "mode": "v2",
        "implementation": "trusted.rules:CombinedR04",
        "implementation_version": 1,
        "claim_contract_version": 2,
        "observation_contract_version": 2,
    }
    value.update(changes)
    return value


def document():
    return {
        "schema": "bt_api_risk.service.v1",
        "service": {"service_id": "risk-service", "principal_ref": "risk-host",
                    "instance_id": "risk-a", "scope_ref": "risk-scope",
                    "rpc_bind": "tcp://example.invalid:1", "public_key_file": "server.pub",
                    "secret_key_file": "server.sec"},
        "account": {"route": "route", "provider": "ctp", "environment": "demo",
                    "account_scope": "synthetic-account", "execution_domain_ref": "paper:demo",
                    "instruments": {"rb": {"venue": "SHFE", "native_symbol": "rb",
                                           "product_type": "future"}}},
        "storage": {"directory": "state"}, "policy": {"file": "policy.yaml"},
        "principals": [
            {"principal_ref": "strategy", "role": "strategy", "actor_ref": "s",
             "strategy_refs": ["s"], "operations": ["risk.status"], "public_key_file": "s.pub"},
            {"principal_ref": "operator", "role": "operator", "actor_ref": "ops",
             "strategy_refs": [], "operations": ["risk.admin.status"], "public_key_file": "op.pub"},
        ],
        "gateway": {"service_id": "gateway", "principal_ref": "gateway-principal",
                    "instance_id": "g", "scope_ref": "scope", "rpc_endpoint": "tcp://example.invalid:2",
                    "server_public_key_file": "gateway.pub",
                    "delegate": {"principal_ref": "delegate", "actor_ref": "delegate-actor",
                                 "public_key_file": "delegate.pub", "secret_key_file": "delegate.sec",
                                 "grant_revisions": {"s": "revision"}},
                    "control": {"principal_ref": "control", "actor_ref": "control-actor",
                                "public_key_file": "control.pub", "secret_key_file": "control.sec"}},
        "deadlines": {"call_timeout_seconds": 5, "reservation_ttl_seconds": "10",
                      "dispatch_recheck_seconds": 5},
        "refresh": {"loop_timeout_ms": 20, "observation_poll_seconds": "0.2",
                    "maintain_seconds": 1, "dispatch_recheck_limit": 8},
    }


class ForbiddenPath:
    def __fspath__(self):
        raise AssertionError("v2 must be refused before resolving a path")


@pytest.fixture
def no_risk_or_plugin_import(monkeypatch):
    original_import = builtins.__import__

    def checked(name, *args, **kwargs):
        if name == "bt_api_risk" or name.startswith("bt_api_risk.") or name.startswith("trusted"):
            pytest.fail("risk package or selected plugin imported")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checked)


def test_v2_refused_before_paths_keys_or_plugin_import(monkeypatch, no_risk_or_plugin_import):
    monkeypatch.setattr(config_module, "read_key_file", lambda *a, **k: pytest.fail("key read"))
    config = {"rules": {"r04": selection()}}
    with pytest.raises(config_module.RiskServiceConfigError) as error:
        config_module.parse_risk_service_config(config, path=ForbiddenPath())
    assert error.value.code == "RISK_SERVICE_R04_V2_UNAVAILABLE"


@pytest.mark.parametrize("field,value", [
    ("mode", "v3"), ("mode", True), ("mode", []),
    ("implementation", ""), ("implementation", "module"),
    ("implementation", "module:Class:Extra"), ("implementation", "中:Class"),
    ("implementation", "x" * 257), ("implementation", None),
    ("implementation_version", True), ("implementation_version", 0),
    ("implementation_version", 2**31), ("implementation_version", "1"),
    ("claim_contract_version", True), ("claim_contract_version", 1),
    ("observation_contract_version", True), ("observation_contract_version", 1),
])
def test_invalid_selection_rejected_without_resources(field, value):
    with pytest.raises(config_module.RiskServiceConfigError):
        config_module.parse_risk_service_config(
            {"rules": {"r04": selection(**{field: value})}}, path=ForbiddenPath()
        )


@pytest.mark.parametrize("field", ["mode", "implementation", "implementation_version",
                                    "claim_contract_version", "observation_contract_version"])
def test_missing_v2_field_is_rejected_without_resources(field):
    value = selection()
    del value[field]
    with pytest.raises(config_module.RiskServiceConfigError):
        config_module.parse_risk_service_config({"rules": {"r04": value}}, path=ForbiddenPath())


@pytest.mark.parametrize("value", [
    None, [], "v2", {"mode": "v1", "implementation": "x:Y"},
    {**selection(), "unknown": 1},
])
def test_invalid_shape_or_extra_field_rejected(value):
    with pytest.raises(config_module.RiskServiceConfigError):
        config_module.parse_risk_service_config({"rules": {"r04": value}}, path=ForbiddenPath())


def test_default_and_explicit_v1_have_identical_config(monkeypatch):
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: self)
    monkeypatch.setattr(config_module, "read_key_file", lambda path, *, secret: path.name.encode())
    first = document()
    second = copy.deepcopy(first)
    second["rules"] = {"r04": {"mode": "v1"}}
    left = config_module.parse_risk_service_config(first, path=Path("/synthetic/service.yaml"))
    right = config_module.parse_risk_service_config(second, path=Path("/synthetic/service.yaml"))
    assert left == right and left.r04_selection is None and right.r04_selection is None


def claimed_v2():
    return config_module.RiskR04Selection("trusted.rules:CombinedR04", 1, 2, 2)


def test_manually_constructed_v2_config_cannot_start(monkeypatch, no_risk_or_plugin_import):
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: self)
    monkeypatch.setattr(config_module, "read_key_file", lambda path, *, secret: path.name.encode())
    parsed = config_module.parse_risk_service_config(document(), path=Path("/synthetic/service.yaml"))
    fake_host = SimpleNamespace(config=replace(parsed, r04_selection=claimed_v2()))
    with pytest.raises(host_module.RiskServiceStartError) as error:
        host_module.RiskServiceHost.start(fake_host)
    assert error.value.code == "RISK_SERVICE_R04_V2_UNAVAILABLE"


def test_constructor_refuses_v2_before_any_initialization(monkeypatch, no_risk_or_plugin_import):
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: self)
    monkeypatch.setattr(config_module, "read_key_file", lambda path, *, secret: path.name.encode())
    parsed = config_module.parse_risk_service_config(document(), path=Path("/synthetic/service.yaml"))
    v2 = replace(parsed, r04_selection=claimed_v2())

    def forbidden(*_args, **_kwargs):
        pytest.fail("constructor accessed a resource")

    monkeypatch.setattr(host_module, "ExitStack", forbidden)
    monkeypatch.setattr(host_module, "threading", SimpleNamespace(Event=forbidden))
    monkeypatch.setattr(host_module, "os", SimpleNamespace(getpid=forbidden))
    fake_self = SimpleNamespace()
    with pytest.raises(host_module.RiskServiceStartError) as error:
        host_module.RiskServiceHost.__init__(fake_self, v2)
    assert error.value.code == "RISK_SERVICE_R04_V2_UNAVAILABLE"
    assert vars(fake_self) == {}
    with pytest.raises(TypeError, match="RISK_SERVICE_CONFIG_REQUIRED"):
        host_module.RiskServiceHost.__init__(fake_self, SimpleNamespace(r04_selection=claimed_v2()))
    assert vars(fake_self) == {}


def test_constructor_keeps_v1_initialization_order(monkeypatch):
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: self)
    monkeypatch.setattr(config_module, "read_key_file", lambda path, *, secret: path.name.encode())
    parsed = config_module.parse_risk_service_config(document(), path=Path("/synthetic/service.yaml"))
    stack, event = object(), object()
    monkeypatch.setattr(host_module, "ExitStack", lambda: stack)
    monkeypatch.setattr(host_module, "threading", SimpleNamespace(Event=lambda: event))
    monkeypatch.setattr(host_module, "os", SimpleNamespace(getpid=lambda: 123))
    fake_self = SimpleNamespace()
    host_module.RiskServiceHost.__init__(fake_self, parsed)
    assert fake_self.config is parsed and fake_self._creator_pid == 123
    assert fake_self._stack is stack and fake_self.stop_event is event


@pytest.mark.parametrize("method,args", [
    ("start", ()), ("run", ()), ("run_once", ()), ("open_storage", ()), ("_open_storage_owned", ()),
    ("build_engine", ()), ("start_gateway", ()), ("start_service_host", ()),
    ("_service_runtime", ("mount", object(), frozenset())),
    ("_application_runtime", ({},)), ("_control_runtime", ()),
    ("_connect_delegate", (object(),)), ("_connect_delegate_owned", (object(),)),
    ("_curve", (object(),)), ("_peer", ()),
    ("_construct_risk_raw_runtime", (lambda: pytest.fail("factory called"),)),
    ("_reconnect_observer", ()), ("_start_strategy_financial", ()), ("_start_market", ()),
])
def test_host_entry_points_refuse_v2_before_accessing_host_or_resources(method, args, no_risk_or_plugin_import):
    # Unbound methods run against a fake object with no lifecycle methods or resources.
    fake_host = SimpleNamespace(config=SimpleNamespace(r04_selection=claimed_v2()))
    with pytest.raises(host_module.RiskServiceStartError) as error:
        getattr(host_module.RiskServiceHost, method)(fake_host, *args)
    assert error.value.code == "RISK_SERVICE_R04_V2_UNAVAILABLE"


@pytest.mark.parametrize("function,args", [
    ("service_config_document", ()), ("_gateway_client_config", ("mount",)),
    ("_control_client_config", ([],)), ("build_authority", ()),
])
def test_config_factories_refuse_v2_before_imports(function, args, no_risk_or_plugin_import):
    config = SimpleNamespace(r04_selection=claimed_v2())
    with pytest.raises(host_module.RiskServiceStartError) as error:
        getattr(host_module, function)(config, *args)
    assert error.value.code == "RISK_SERVICE_R04_V2_UNAVAILABLE"


def test_bootstrap_refuses_before_risk_import_or_policy_read(monkeypatch, no_risk_or_plugin_import):
    monkeypatch.setattr(host_module, "read_policy_files", lambda *a: pytest.fail("policy read"))
    with pytest.raises(host_module.RiskServiceStartError) as error:
        host_module.bootstrap_policy(None, None, SimpleNamespace(r04_selection=claimed_v2()), None)
    assert error.value.code == "RISK_SERVICE_R04_V2_UNAVAILABLE"


@pytest.mark.parametrize("command", ["_command_run", "_command_status", "_command_validate_policy"])
def test_cli_refuses_v2_before_host_policy_or_resource_calls(command, monkeypatch, capsys, no_risk_or_plugin_import):
    monkeypatch.setattr(host_module, "load_risk_service_config",
                        lambda _: SimpleNamespace(r04_selection=claimed_v2()))
    monkeypatch.setattr(host_module, "RiskServiceHost", lambda *a, **k: pytest.fail("host created"))
    monkeypatch.setattr(host_module, "read_policy_files", lambda *a: pytest.fail("policy read"))
    result = getattr(host_module, command)(SimpleNamespace(config="synthetic", policy="synthetic"))
    captured = capsys.readouterr()
    assert result == 2
    assert "RISK_SERVICE_R04_V2_UNAVAILABLE" in (captured.out + captured.err)
    if command != "_command_run":
        assert json.loads(captured.out)["code"] == "RISK_SERVICE_R04_V2_UNAVAILABLE"


def test_absent_selection_preserves_v1_host_gate():
    assert host_module._require_supported_r04(SimpleNamespace(r04_selection=None)) is None


def test_validate_policy_does_not_claim_v2_is_supported_without_config(monkeypatch, capsys):
    package = ModuleType("bt_api_risk")
    package.__path__ = []
    pretrade = ModuleType("bt_api_risk.pretrade")
    pretrade.__path__ = []
    policy = ModuleType("bt_api_risk.pretrade.policy")

    class Unsupported(Exception):
        code = "policy_schema_unsupported"

    def validate(text, **_kwargs):
        assert json.loads(text)["schema"] == "bt_api_risk.policy.v2"
        raise Unsupported()

    pretrade.RiskPolicyError = Unsupported
    policy.validate_policy_document = validate
    for name, module in (("bt_api_risk", package), ("bt_api_risk.pretrade", pretrade),
                         ("bt_api_risk.pretrade.policy", policy)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(host_module, "read_policy_files",
                        lambda _: ('{"schema":"bt_api_risk.policy.v2"}', None))
    assert host_module._command_validate_policy(SimpleNamespace(config=None, policy="synthetic")) == 1
    assert json.loads(capsys.readouterr().out) == {"code": "policy_schema_unsupported", "valid": False}
