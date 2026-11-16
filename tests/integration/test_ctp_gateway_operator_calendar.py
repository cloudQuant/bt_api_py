"""Protected synthetic CTP operator and simulation calendar over real CURVE."""
import json
from copy import deepcopy

import pytest
import yaml
import zmq

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.ctp_gateway_local_profile import CtpLocalProfileError, load_local_ctp_resources
from bt_api_py.gateway_operator_application import (GatewayOperatorApplication, GatewayOperatorProfileRegistry,
    load_gateway_operator_launch_config)
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime import ServiceCallOutcomeUnknownError
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime
from tests.calendar_test_store import FixtureCalendarStore
from tests.test_iter63_ctp_gateway_trading import FakeSession
from tests.unit.test_iter63_ctp_gateway_local_profile import _fixture
from tests.unit.test_trading_calendar_binding import content, publish

OPS = frozenset({"calendar.query.v1", "calendar.windows.v1"})
pytestmark = pytest.mark.skipif(not zmq.has("curve"), reason="CURVE unavailable")


def configured(tmp_path, monkeypatch, *, bootstrap=True, fixed_run=None):
    doc, file, original = _fixture(tmp_path, monkeypatch)
    client_public, client_secret = zmq.curve_keypair()
    client = doc["clients"][0]
    client["curve_public_key"] = client_public.decode("ascii")
    scope = client["resources"][0]["scope"]
    calendar_resource = deepcopy(client["resources"][0])
    calendar_resource.update(port="service.calendar_read", operations=sorted(OPS))
    calendar_resource["scope"]["run_id"] = fixed_run
    client["resources"].append(calendar_resource)
    raw = original.to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.calendar_read")
    config = parse_service_config(raw)
    grant = {"principal_ref": client["identity"]["principal_ref"],
             "execution_domain_ref": scope["execution_domain_ref"], "account_ref": scope["account_ref"],
             "strategy_ref": scope["strategy_ref"], "run_id": "run-a", "route": "CTP___FUTURE",
             "venue": "SIM", "kind": "future", "product": "x", "contract": "x2601"}
    policy = {"policy_id": "policy", **{k: grant[k] for k in ("route", "venue", "kind", "product", "contract")},
              "max_advance_days": 0, "max_spill_days": 0}
    doc["calendars"] = {"root": "calendars", "mode": "SIMULATION_ONLY", "route": "CTP___FUTURE",
                        "grants": [grant], "range_policies": [policy],
                        "simulation_bootstrap": {"trading_day": "2026-01-01"} if bootstrap else None}
    file.write_text(json.dumps(doc), encoding="utf-8")
    return doc, file, config, client_public, client_secret


def client_for(doc, config, public, secret, *, run_id="run-a"):
    identity = doc["clients"][0]["identity"]
    scope = doc["clients"][0]["resources"][0]["scope"]
    server = doc["authority"]["identity"]
    endpoint = config.communication["profiles"]["server-transport"]["endpoints"]["rpc_bind"]
    raw = {"client_config_schema_version": 1, "process_role": "strategy_client",
           "registration": {"strategy_ref": identity["actor_ref"]}, "clients": {"gateway": {
               "config_schema_version": 1, "settings": {"bindings": [{"mount": "store.calendar",
                   "service_id": server["actor_ref"], "instance_id": "gateway-a", "scope_ref": "scope-a",
                   "account_ref": scope["account_ref"], "transport_ref": "transport"}]}}},
           "communication": {"config_schema_version": 1, "profiles": {"transport": {
               "backend": "zmq", "config_schema_version": 1, "endpoints": {"rpc_connect": endpoint},
               "security_profile_ref": "security"}}}}
    client_config = parse_client_config(raw)
    return ThreadedServiceClientRuntime(lambda: ServiceClientRuntime(
        client_config, identity=WireIdentity(WireRole.STRATEGY_CLIENT, identity["principal_ref"], identity["actor_ref"]),
        scope_resolver=lambda _: WireScope(scope["execution_domain_ref"], account_ref=scope["account_ref"],
                                           strategy_ref=scope["strategy_ref"]),
        peer_resolver=lambda _: WireIdentity(WireRole.SERVICE, server["principal_ref"], server["actor_ref"]),
        transport_factory=ZmqTransportFactory({"security": ClientCurveKeys(
            doc["authority"]["curve_public_key"].encode("ascii"), public, secret)}),
        run_id=run_id, requested_operations={"store.calendar": OPS}))


def application(config, launch_file):
    launch_file.write_text(yaml.safe_dump({"gateway_operator_config_schema_version": 2,
        "profile_ref": "ctp-local-readonly", "service_config": config.to_public_dict()}), encoding="utf-8")
    launch = load_gateway_operator_launch_config(launch_file)
    registry = GatewayOperatorProfileRegistry.from_configured_entry_points(launch)
    return GatewayOperatorApplication.from_launch_config(launch, registry, poll_interval_ms=5)


def query(runtime, windows=False, contract="x2601"):
    args = dict(mode="SIMULATION_ONLY", route="CTP___FUTURE", venue="SIM", kind="future", product="x", contract=contract)
    if windows:
        args.update(start_utc="2026-01-01T01:30:00Z", end_utc="2026-01-01T02:00:00Z")
    else:
        args["trading_day"] = "2026-01-01"
    return runtime.call("store.calendar", "calendar.windows.v1" if windows else "calendar.query.v1", args, timeout_seconds=5)


@pytest.mark.parametrize("bootstrap", [True, False])
def test_operator_curve_fixed_view_and_restart(tmp_path, monkeypatch, bootstrap):
    import bt_api_py.ctp_gateway_calendar_config as calendar

    doc, file, config, public, secret = configured(tmp_path, monkeypatch, bootstrap=bootstrap)
    root = file.parent / "calendars"
    store = FixtureCalendarStore(root)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    created = []
    original = calendar.create_calendar_service

    def capture(settings):
        service = original(settings)
        created.append(service)
        return service

    monkeypatch.setattr(calendar, "create_calendar_service", capture)
    app, runtime = application(config, tmp_path / "launch.yaml"), client_for(doc, config, public, secret)
    try:
        app.start(timeout_seconds=10)
        runtime.start(timeout_seconds=10)
        result = query(runtime)
        if not bootstrap:
            assert result["status"] == "UNKNOWN" and result["reason"] == "SOURCE_UNAVAILABLE"
            return
        assert result["status"] == "COMPLETE" and result["binding"]["calendar_ref"] == "v1"
        assert result["binding"]["bound_trading_day"] == "2026-01-01"
        assert result["binding"]["source_generation"].startswith("operator-simulation-")
        assert result["source_authentication"] == "NOT_PROVEN" and result["trade_authorization"] == "NONE"
        interval = query(runtime, True)
        assert interval["status"] == "COMPLETE" and interval["binding"]["calendar_ref"] == "v1"
        assert created[-1]._binding.activate(object()).outcome == "GATE_REJECTED"
        assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
        assert query(runtime)["binding"]["calendar_ref"] == "v1"
        generation = result["binding"]["source_generation"]
    finally:
        runtime.close(timeout_seconds=10)
        app.stop(timeout_seconds=10)
        assert created and created[-1]._closed
    assert app.status["state"] == "closed"
    app, runtime = application(config, tmp_path / "launch.yaml"), client_for(doc, config, public, secret)
    try:
        app.start(timeout_seconds=10)
        runtime.start(timeout_seconds=10)
        result = query(runtime)
        assert result["binding"]["calendar_ref"] == "v2"
        assert result["binding"]["source_generation"] != generation
        assert result["binding"]["bound_trading_day"] == "2026-01-01"
    finally:
        runtime.close(timeout_seconds=10)
        app.stop(timeout_seconds=10)
        assert created[-1]._closed
    assert app.status["state"] == "closed"


@pytest.mark.parametrize("mutation", ["mode", "root", "grant", "scope", "resource_scope", "instance", "operations", "policy", "port", "missing", "date", "run"])
def test_invalid_bundle_rejected_before_provider_start(tmp_path, monkeypatch, mutation):
    doc, file, config, _, _ = configured(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    if mutation == "mode": doc["calendars"]["mode"] = "LIVE"
    if mutation == "root": doc["calendars"]["root"] = ""
    if mutation == "grant": doc["calendars"]["grants"][0]["principal_ref"] = "unknown"
    if mutation == "scope": doc["calendars"]["grants"][0]["account_ref"] = "other"
    if mutation == "resource_scope": doc["clients"][0]["resources"][-1]["scope"]["strategy_ref"] = "other"
    if mutation == "instance": doc["clients"][0]["resources"][-1]["instance_id"] = "other"
    if mutation == "operations": doc["clients"][0]["resources"][-1]["operations"].append("direct.dispatch")
    if mutation == "policy": doc["calendars"]["range_policies"][0]["contract"] = "other"
    if mutation == "port": raw["components"]["gateway"]["settings"]["bind_ports"].remove("service.calendar_read")
    if mutation == "missing": del doc["calendars"]
    if mutation == "date": doc["calendars"]["simulation_bootstrap"]["trading_day"] = "20260101"
    if mutation == "run": doc["clients"][0]["resources"][-1]["scope"]["run_id"] = "other"
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError):
        load_local_ctp_resources(parse_service_config(raw))
    assert FakeSession.instances == []


def test_corrupt_snapshot_is_unknown(tmp_path, monkeypatch):
    doc, file, config, public, secret = configured(tmp_path, monkeypatch)
    root = file.parent / "calendars"
    store = FixtureCalendarStore(root)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    (root / "CURRENT.json").write_bytes(b"{}")
    app, runtime = application(config, tmp_path / "launch.yaml"), client_for(doc, config, public, secret)
    try:
        app.start(timeout_seconds=10)
        runtime.start(timeout_seconds=10)
        result = query(runtime)
        assert result["status"] == "UNKNOWN" and result["reason"] == "SOURCE_UNAVAILABLE"
    finally:
        runtime.close(timeout_seconds=10)
        app.stop(timeout_seconds=10)
    assert app.status["state"] == "closed"


@pytest.mark.parametrize("fixed_run", [None, "run-a"])
def test_curve_rejects_wrong_run_and_contract(tmp_path, monkeypatch, fixed_run):
    doc, file, config, public, secret = configured(tmp_path, monkeypatch, fixed_run=fixed_run)
    assert publish(FixtureCalendarStore(file.parent / "calendars"), "v1", content()).visibility == "COMMITTED"
    app = application(config, tmp_path / "launch.yaml")
    correct = client_for(doc, config, public, secret)
    wrong = client_for(doc, config, public, secret, run_id="run-b")
    try:
        app.start(timeout_seconds=10)
        correct.start(timeout_seconds=10)
        assert query(correct)["status"] == "COMPLETE"
        with pytest.raises(ServiceCallOutcomeUnknownError):
            query(correct, contract="other")
        if fixed_run is not None:
            with pytest.raises(ThreadedRuntimeError, match="START_FAILED"):
                wrong.start(timeout_seconds=10)
        else:
            wrong.start(timeout_seconds=10)
            with pytest.raises(ServiceCallOutcomeUnknownError):
                query(wrong)
    finally:
        wrong.close(timeout_seconds=10)
        correct.close(timeout_seconds=10)
        app.stop(timeout_seconds=10)


def test_cli_dry_run_discovers_real_configured_entrypoint(tmp_path, monkeypatch, capsys):
    from bt_api_py.gateway_operator_cli import main

    _, _, config, _, _ = configured(tmp_path, monkeypatch)
    path = tmp_path / "launch.yaml"
    application(config, path)
    assert main(["--config", str(path), "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "mode": "offline_config_validation", "profile": "ctp-local-readonly", "status": "PASS"}
