"""Public v2 launch config with a synthetic trusted factory and real CURVE."""
from __future__ import annotations

import importlib.metadata
import multiprocessing
import threading
from pathlib import Path

import pytest
import yaml
from bt_api_base.service_protocol import WireScope

from bt_api_py.gateway_operator import GatewayOperatorDeployment
from bt_api_py.gateway_operator_application import (
    GatewayOperatorApplication,
    GatewayOperatorApplicationError,
    GatewayOperatorProfileFactory,
    GatewayOperatorProfileRegistry,
    load_gateway_operator_launch_config,
)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.integration.test_iter63_gateway_direct_service import (
    ACCOUNT, CLIENT_A, DOMAIN, SERVICE, _client_config, _service_config,
)
from tests.unit.test_iter63_gateway_direct_service import _order
from tests.unit.test_iter63_gateway_operator_trading_owner import _binding, _setup


class _Entry:
    name = "synthetic"

    def __init__(self, factory):
        self.factory = factory

    def load(self):
        return self.factory


class _Entries:
    def __init__(self, entries):
        self.entries = entries

    def select(self, *, group):
        return self.entries if group == "bt_api_py.gateway_operator_profile_factories" else ()


def _write_config(path, endpoint):
    path.write_text(yaml.safe_dump({
        "gateway_operator_config_schema_version": 2,
        "profile_ref": "synthetic",
        "service_config": _service_config(endpoint).to_public_dict(),
    }), encoding="utf-8")


def _serve(path, pipe):
    endpoint, authority, keys = _setup()
    _write_config(Path(path) / "launch.yaml", endpoint)
    owner = []
    journals = []

    def factory(config):
        owner.append(threading.get_ident())
        def provider(*_args):
            binding = _binding(Path(path))
            journals.append(binding._journal.journal_id)
            return ComponentResources.owned({"binding": binding}, ready=lambda: True, close=lambda: None)
        return GatewayOperatorDeployment(config, authority, Path(path) / "registry.sqlite", resource_provider=provider)

    entry = _Entry(GatewayOperatorProfileFactory("synthetic", "trading_owner", factory))
    importlib.metadata.entry_points = lambda: _Entries((entry,))
    config = load_gateway_operator_launch_config(Path(path) / "launch.yaml")
    registry = GatewayOperatorProfileRegistry.from_configured_entry_points(config)
    assert not owner
    app = GatewayOperatorApplication.from_launch_config(config, registry, poll_interval_ms=5)
    try:
        app.start(timeout_seconds=10)
        pipe.send((endpoint, keys, owner[0], threading.get_ident(), journals[0]))
        assert pipe.recv() == "stop"
    finally:
        app.stop(timeout_seconds=10)
        pipe.close()


def test_yaml_config_starts_separate_operator_and_real_client(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_serve, args=(str(tmp_path), child))
    process.start()
    child.close()
    runtime = None
    try:
        assert parent.poll(20), "synthetic Gateway did not start"
        endpoint, (server_public, client_public, client_secret), owner_id, main_id, _journal_id = parent.recv()
        assert owner_id != main_id
        runtime = ThreadedServiceClientRuntime(lambda: ServiceClientRuntime(
            _client_config(endpoint, CLIENT_A), identity=CLIENT_A,
            scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
            peer_resolver=lambda _: SERVICE,
            transport_factory=ZmqTransportFactory({"security-a": ClientCurveKeys(server_public, client_public, client_secret)}),
            run_id="run-a",
        ))
        runtime.start(timeout_seconds=10)
        result = runtime.call("broker.direct_write", "direct.reserve",
                              {"request_key": "key-a", "payload": _order(key="key-a"), "ttl_seconds": 10},
                              timeout_seconds=10)
        assert result["status"] == "OK"
        assert result["record"]["state"] == "RESERVED"
    finally:
        if runtime is not None:
            runtime.close(timeout_seconds=10)
        if process.is_alive():
            try:
                parent.send("stop")
            except (BrokenPipeError, EOFError):
                pass
        process.join(timeout=15)
        parent.close()
    assert process.exitcode == 0


def test_configured_factory_resolution_is_strict_and_inert(tmp_path, monkeypatch):
    endpoint, _, _ = _setup()
    path = tmp_path / "launch.yaml"
    _write_config(path, endpoint)
    config = load_gateway_operator_launch_config(path)
    called = []
    factory = GatewayOperatorProfileFactory("synthetic", "trading_owner", lambda cfg: called.append(cfg))
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda: _Entries((_Entry(factory),)))
    registry = GatewayOperatorProfileRegistry.from_configured_entry_points(config)
    profile = registry.resolve("synthetic")
    assert called == []
    profile.deployment_factory()
    assert called == [config.service_config]
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda: _Entries(()))
    with pytest.raises(GatewayOperatorApplicationError, match="PROFILE_NOT_REGISTERED"):
        GatewayOperatorProfileRegistry.from_configured_entry_points(config)
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda: _Entries((_Entry(factory), _Entry(factory))))
    with pytest.raises(GatewayOperatorApplicationError, match="PROFILE_DUPLICATE"):
        GatewayOperatorProfileRegistry.from_configured_entry_points(config)
    raw = yaml.safe_load(path.read_text())
    raw["python_module"] = "not-allowed"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(GatewayOperatorApplicationError, match="INVALID_LAUNCH_CONFIG"):
        load_gateway_operator_launch_config(path)


def test_cli_selects_configured_factory_for_v2(tmp_path, monkeypatch):
    import bt_api_py.gateway_operator_cli as cli
    from tests.test_gateway_operator_cli import _LifecycleApp, _wire_cli

    endpoint, _, _ = _setup()
    path = tmp_path / "launch.yaml"
    _write_config(path, endpoint)
    config = load_gateway_operator_launch_config(path)
    app = _LifecycleApp({}, start_state="failed")
    _wire_cli(monkeypatch, app)
    monkeypatch.setattr(cli, "load_gateway_operator_launch_config", lambda _: config)
    seen = []
    monkeypatch.setattr(cli.GatewayOperatorProfileRegistry, "from_configured_entry_points",
                        classmethod(lambda cls, supplied: seen.append(supplied) or object()))
    assert cli.main(["--config", str(path)]) == 1
    assert seen == [config]


def test_public_strategy_config_connects_to_separate_gateway(tmp_path):
    from bt_api_py.gateway_strategy_config import parse_gateway_strategy_config
    from tests.unit.test_iter63_gateway_direct_client import MemoryHost, _request

    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_serve, args=(str(tmp_path), child))
    process.start()
    child.close()
    api = None
    try:
        assert parent.poll(20), "synthetic Gateway did not start"
        endpoint, (server_public, client_public, client_secret), _, _, journal_id = parent.recv()
        template = Path(__file__).resolve().parents[2] / "examples/services/gateway-strategy-client.example.yaml"
        raw = yaml.safe_load(template.read_text(encoding="utf-8"))
        raw["client_config"] = _client_config(endpoint, CLIENT_A).to_public_dict()
        # This direct-only fixture does not advertise account or market ports.
        for name in ("account_settings", "market_settings", "instrument_settings"):
            raw.pop(name, None)
        raw["binding"] = {"journal_id": journal_id, "account_scope": ACCOUNT,
                          "strategy_scope": CLIENT_A.actor_ref, "principal": CLIENT_A.principal_ref}
        raw["route"].update(service_id=SERVICE.actor_ref, instance_id="gateway-a", scope_ref="scope-a")
        raw["route"]["scope"].update(execution_domain_ref=DOMAIN, account_ref=ACCOUNT,
                                      strategy_ref=CLIENT_A.actor_ref, run_id="run-a")
        raw["route"]["identity"].update(principal_ref=CLIENT_A.principal_ref, actor_ref=CLIENT_A.actor_ref)
        raw["peer"].update(principal_ref=SERVICE.principal_ref, actor_ref=SERVICE.actor_ref)
        raw["requested_operations"] = ["direct.reserve", "direct.command_status"]
        path = tmp_path / "strategy-public.yaml"
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        config = parse_gateway_strategy_config(yaml.safe_load(path.read_text(encoding="utf-8")))
        host = MemoryHost()
        api = config.create_api(host=host, security_profiles={
            "security-a": ClientCurveKeys(server_public, client_public, client_secret)})
        api.start(timeout_seconds=10)
        result = api.reserve_order(config.exchange_name, _request("key-a"), timeout_seconds=10)
        assert result.outcome == "OBSERVED" and result.record.state == "RESERVED"
        status = api.command_status(config.exchange_name, "key-a", timeout_seconds=10)
        assert status.outcome == "OBSERVED" and status.record.reservation_id == result.record.reservation_id
    finally:
        if api is not None:
            api.close(timeout_seconds=10)
        if process.is_alive():
            try:
                parent.send("stop")
            except (BrokenPipeError, EOFError):
                pass
        process.join(timeout=15)
        parent.close()
    assert process.exitcode == 0
