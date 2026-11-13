"""Synthetic checks for retirement of new legacy execution-session assembly."""
from __future__ import annotations

import pytest

import bt_api_py.service_config as config_module
import bt_api_py.service_execution_assembly as assembly_module


CODE = "LEGACY_SERVICE_EXECUTION_RETIRED"


def _client_config():
    def binding(mount):
        return {
            "mount": mount, "service_id": "service", "instance_id": "owner",
            "scope_ref": mount, "transport_ref": "rpc", "account_ref": "account",
        }
    return {
        "client_config_schema_version": 1, "process_role": "strategy_client",
        "registration": {"strategy_ref": "strategy"},
        "clients": {"execution": {"config_schema_version": 1, "settings": {
            "bindings": [binding("broker.orders"), binding("broker.order_query")],
        }}},
        "communication": {"config_schema_version": 1, "profiles": {"rpc": {
            "backend": "zmq", "config_schema_version": 1,
            "endpoints": {"rpc_connect": "tcp://127.0.0.1:1"},
            "security_profile_ref": "synthetic",
        }}},
    }


class _Cerebro:
    def __init__(self):
        self.calls = []
        self.result = object()

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


@pytest.mark.parametrize("client_config", [None, {}, _client_config()])
def test_assembly_rejects_before_parser_or_runtime_factory(client_config, monkeypatch):
    calls = []
    monkeypatch.setattr(config_module, "parse_client_config", lambda value: calls.append("parse"))
    monkeypatch.setattr(assembly_module, "parse_client_config", lambda value: calls.append("parse"), raising=False)
    with pytest.raises(assembly_module.ServiceExecutionAssemblyError) as exc:
        assembly_module.assemble_service_execution(
            client_config, runtime_factory=lambda config: calls.append("factory")
        )
    assert exc.value.code == CODE
    assert calls == []


def test_configured_cerebro_fails_closed_without_resources_or_fallback(monkeypatch):
    session_module = pytest.importorskip("backtrader.service.service_execution_session")
    calls = []
    cerebro = _Cerebro()
    client_config = _client_config()
    assert config_module.parse_client_config(client_config).process_role == "strategy_client"
    monkeypatch.setattr(config_module, "parse_client_config", lambda value: calls.append("parse"))
    monkeypatch.setattr(assembly_module, "parse_client_config", lambda value: calls.append("parse"), raising=False)
    monkeypatch.setattr(session_module, "ServiceExecutionSession", lambda *a, **kw: calls.append("session"))
    with pytest.raises(session_module.ServiceExecutionSessionConfigError) as exc:
        session_module.run_configured_cerebro(
            cerebro, client_config,
            runtime_factory=lambda config: calls.append("factory"), runonce=False,
        )
    assert str(exc.value) == CODE
    assert calls == []
    assert cerebro.calls == []


@pytest.mark.parametrize("form", ["omitted", "positional", "keyword"])
def test_unconfigured_cerebro_runs_normally_without_service_setup(form, monkeypatch):
    session_module = pytest.importorskip("backtrader.service.service_execution_session")
    calls = []
    cerebro = _Cerebro()
    monkeypatch.setattr(assembly_module, "assemble_service_execution", lambda *a, **kw: calls.append("assembly"))
    args = [cerebro]
    kwargs = {"runtime_factory": lambda config: calls.append("factory"), "runonce": False}
    if form == "positional":
        args.append(None)
    elif form == "keyword":
        kwargs["client_config"] = None
    assert session_module.run_configured_cerebro(*args, **kwargs) is cerebro.result
    assert cerebro.calls == [{"runonce": False}]
    assert calls == []
