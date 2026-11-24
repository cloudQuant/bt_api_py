"""Synthetic bundle and real local Monitor transport; no CTP account."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
import yaml
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.monitor_heartbeat_wire import MONITOR_HEARTBEAT_OPERATIONS

from bt_api_py.ctp_gateway_local_profile import (
    CtpLocalProfileError,
    load_local_ctp_resources,
    load_simnow_trading_resources,
    simnow_trading_profile_factory,
)
from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_runtime import ServiceRuntimeError
from tests.integration.test_monitor_service_process import deployment, start, stop
from tests.unit.test_iter63_ctp_gateway_local_profile import (
    _composing_config,
    _composing_doc,
    _fixture,
    _protect_bundle,
)


def _setup(tmp_path, monkeypatch):
    bundle, bundle_file, config = _fixture(tmp_path, monkeypatch)
    _composing_doc(bundle)
    _, config = _composing_config(config)
    monitor_dir = tmp_path / "monitor"
    monitor_dir.mkdir(mode=0o700)
    _protect_bundle(monitor_dir)
    monitor_file, monitor, keys = deployment(monitor_dir)
    gateway = bundle["authority"]["identity"]
    monitor["scope"] = {
        "execution_domain_ref": bundle["authority"]["scope"]["execution_domain_ref"],
        "account_ref": bundle["settings"]["account_scope"],
    }
    monitor["heartbeat"]["principals"][0].update(
        principal_ref=gateway["principal_ref"], actor_ref=gateway["actor_ref"]
    )
    monitor_file.write_text(yaml.safe_dump(monitor))
    server = monitor["heartbeat"]
    bundle["required_monitor"] = {
        "monitor_identity": {
            "role": "service",
            "principal_ref": server["principal_ref"],
            "actor_ref": server["service_id"],
            "delegate_ref": None,
        },
        "instance_id": server["instance_id"],
        "scope_ref": server["scope_ref"],
        "rpc_connect": server["rpc_bind"],
        "server_curve_public_key": keys["heartbeat"][0].decode(),
        "client_curve_public_key": keys["gateway"][0].decode(),
        "client_curve_secret_key": keys["gateway"][1].decode(),
        "interval_seconds": 0.2,
        "timeout_seconds": 2,
        "automatic_resume": True,
    }
    bundle_file.write_text(json.dumps(bundle))
    return bundle, bundle_file, config, monitor_file, monitor, keys


def test_bundle_produces_separate_authenticated_heartbeat_runtime(tmp_path, monkeypatch):
    bundle, _, config, monitor_file, _, _ = _setup(tmp_path, monkeypatch)
    before = config.to_public_dict()
    resources = load_simnow_trading_resources(config)
    settings = resources.required_monitor
    assert settings.gateway_identity == resources.authority.identity
    assert settings.scope == WireScope(
        resources.authority.scope.execution_domain_ref, account_ref=resources.settings.account_scope
    )
    assert "monitor" not in config.clients and config.to_public_dict() == before
    assert bundle["required_monitor"]["client_curve_secret_key"] not in repr(resources)
    assert bundle["required_monitor"]["client_curve_secret_key"] not in repr(settings)
    assert simnow_trading_profile_factory.create_deployment(config).state == "new"
    process = start(monitor_file)
    try:
        runtime = settings.runtime_factory()
        assert runtime._requested_operations == {"upstream.telemetry": MONITOR_HEARTBEAT_OPERATIONS}
        assert runtime.start().degraded_count == 0
        (binding,) = runtime.bindings
        request = {
            "schema_version": 1,
            "relay_epoch": binding.registration_id,
            "heartbeat_id": "synthetic",
            "sent_at_ns": 17,
        }
        assert dict(runtime.call("upstream.telemetry", "monitor.heartbeat", request)) == {
            **request,
            "monitor_principal": settings.monitor_identity.principal_ref,
        }
        runtime.close()
        assert not runtime.unregister_uncertainties
    finally:
        stop(process)


def test_factory_discards_existing_public_clients_without_mutating_gateway_config(
    tmp_path, monkeypatch
):
    _, _, config, _, _, _ = _setup(tmp_path, monkeypatch)
    resources = load_simnow_trading_resources(config)
    generated = resources.required_monitor.runtime_factory()._config
    public = config.to_public_dict()
    public["clients"] = deepcopy(generated["clients"])
    binding = public["clients"]["monitor"]["settings"]["bindings"][0]
    old_ref = binding["transport_ref"]
    public["communication"]["profiles"][old_ref] = deepcopy(
        generated["communication"]["profiles"][old_ref]
    )
    config_with_client = parse_service_config(public)
    original = config_with_client.to_public_dict()
    settings = load_simnow_trading_resources(config_with_client).required_monitor
    private = settings.runtime_factory()._config
    assert config_with_client.to_public_dict() == original
    bindings = private["clients"]["monitor"]["settings"]["bindings"]
    assert len(bindings) == 1 and bindings[0]["transport_ref"] != old_ref
    assert private["clients"].keys() == {"monitor"}


def test_wrong_remote_identity_or_scope_cannot_register(tmp_path, monkeypatch):
    bundle, bundle_file, config, monitor_file, monitor, _ = _setup(tmp_path, monkeypatch)
    process = start(monitor_file)
    try:
        wrong = deepcopy(bundle)
        wrong["required_monitor"]["monitor_identity"]["principal_ref"] = "wrong-principal"
        bundle_file.write_text(json.dumps(wrong))
        settings = load_simnow_trading_resources(config).required_monitor
        runtime = settings.runtime_factory()
        with pytest.raises(ServiceRuntimeError):
            runtime.start()
        runtime.close()
        wrong = deepcopy(bundle)
        wrong["required_monitor"]["scope_ref"] = "not-granted-scope"
        bundle_file.write_text(json.dumps(wrong))
        runtime = load_simnow_trading_resources(config).required_monitor.runtime_factory()
        with pytest.raises(ServiceRuntimeError):
            runtime.start()
        runtime.close()
    finally:
        stop(process)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(extra="bad"),
        lambda d: d.update(rpc_connect="tcp://0.0.0.0:9876"),
        lambda d: d.update(rpc_connect="tcp://8.8.8.8:9876"),
        lambda d: d.update(rpc_connect="tcp://localhost:9876"),
        lambda d: d.update(client_curve_secret_key=d["server_curve_public_key"]),
        lambda d: d.update(server_curve_public_key="~" * 40),
        lambda d: d.update(interval_seconds=2, timeout_seconds=2),
        lambda d: d.update(interval_seconds=0.0001, timeout_seconds=0.0005),
        lambda d: d.update(timeout_seconds=301),
        lambda d: d.update(automatic_resume=1),
        lambda d: d["monitor_identity"].update(role="application_client"),
        lambda d: d["monitor_identity"].update(delegate_ref="untrusted"),
        lambda d: d["monitor_identity"].update(extra="bad"),
    ],
)
def test_invalid_required_monitor_declarations_fail_without_secret_output(
    tmp_path, monkeypatch, change
):
    bundle, bundle_file, config, _, _, _ = _setup(tmp_path, monkeypatch)
    change(bundle["required_monitor"])
    bundle_file.write_text(json.dumps(bundle))
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$") as caught:
        load_simnow_trading_resources(config)
    assert bundle["required_monitor"]["client_curve_secret_key"] not in str(caught.value)


def test_readonly_rejects_required_monitor_and_trading_requires_control(tmp_path, monkeypatch):
    bundle, bundle_file, config, _, _, _ = _setup(tmp_path, monkeypatch)
    with pytest.raises(CtpLocalProfileError):
        load_local_ctp_resources(config)
    del bundle["controls"]
    bundle_file.write_text(json.dumps(bundle))
    with pytest.raises(CtpGatewayTradingError, match="INVALID_REQUIRED_MONITOR"):
        simnow_trading_profile_factory.create_deployment(config)
