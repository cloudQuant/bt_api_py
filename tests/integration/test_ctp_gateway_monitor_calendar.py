"""Explicit Monitor simulation calendar access through the local CURVE host."""

import json
from datetime import date

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.ctp_gateway_local_profile import CtpLocalProfileError, load_local_ctp_resources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime
from tests.calendar_test_store import FixtureCalendarStore
from tests.integration.test_ctp_gateway_operator_calendar import application, configured
from tests.unit.test_trading_calendar_binding import FakeGate, content, decision, publish

pytestmark = pytest.mark.skipif(not zmq.has("curve"), reason="CURVE unavailable")
OP = "calendar.monitor.windows.v1"


def setup_monitor(tmp_path, monkeypatch):
    doc, file, config, _, _ = configured(tmp_path, monkeypatch)
    public, secret = zmq.curve_keypair()
    strategy = doc["clients"][0]
    strategy_scope = strategy["resources"][0]["scope"]
    monitor_scope = dict(strategy_scope, strategy_ref=None, run_id=None)
    identity = {
        "role": "service",
        "principal_ref": "monitor-p",
        "actor_ref": "monitor-a",
        "delegate_ref": None,
    }
    resource = {
        "instance_id": "gateway-a",
        "port": "service.calendar_read",
        "scope_ref": "scope-a",
        "scope": monitor_scope,
        "operations": [OP],
    }
    doc["calendars"]["monitor_clients"] = [
        {"identity": identity, "curve_public_key": public.decode(), "resources": [resource]}
    ]
    grant = dict(doc["calendars"]["grants"][0])
    grant.pop("strategy_ref")
    grant.pop("run_id")
    grant.update(principal_ref="monitor-p", actor_ref="monitor-a")
    doc["calendars"]["monitor_grants"] = [grant]
    file.write_text(json.dumps(doc), encoding="utf-8")
    return doc, file, config, public, secret


def monitor_client(doc, config, public, secret, requested=frozenset({OP})):
    identity = doc["calendars"]["monitor_clients"][0]["identity"]
    scope = doc["calendars"]["monitor_clients"][0]["resources"][0]["scope"]
    server = doc["authority"]["identity"]
    endpoint = config.communication["profiles"]["server-transport"]["endpoints"]["rpc_bind"]
    raw = {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": identity["actor_ref"],
        "scope_ref": "monitor-root",
        "transport_ref": "unused",
        "registration_policy_ref": "policy",
        "service_requirements_ref": "requirements",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "unused",
                    "bind_ports": ["service.telemetry_ingress"],
                    "options": {"role": "gateway_heartbeat"},
                },
            }
        },
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "upstream.calendar",
                            "service_id": server["actor_ref"],
                            "instance_id": "gateway-a",
                            "scope_ref": "scope-a",
                            "account_ref": scope["account_ref"],
                            "transport_ref": "remote",
                        }
                    ]
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "unused": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:1"},
                    "security_profile_ref": "unused",
                },
                "remote": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": "client",
                },
            },
        },
    }
    return ThreadedServiceClientRuntime(
        lambda: ServiceClientRuntime(
            parse_service_config(raw),
            identity=WireIdentity(
                WireRole.SERVICE, identity["principal_ref"], identity["actor_ref"]
            ),
            scope_resolver=lambda _: WireScope(
                scope["execution_domain_ref"], account_ref=scope["account_ref"]
            ),
            peer_resolver=lambda _: WireIdentity(
                WireRole.SERVICE, server["principal_ref"], server["actor_ref"]
            ),
            transport_factory=ZmqTransportFactory(
                {
                    "client": ClientCurveKeys(
                        doc["authority"]["curve_public_key"].encode(), public, secret
                    )
                }
            ),
            requested_operations={} if requested is None else {"upstream.calendar": requested},
        )
    )


def query(runtime, **overrides):
    args = {
        "mode": "SIMULATION_ONLY",
        "route": "CTP___FUTURE",
        "venue": "SIM",
        "kind": "future",
        "product": "x",
        "contract": "x2601",
        "start_utc": "2026-01-01T01:30:00Z",
        "end_utc": "2026-01-01T02:00:00Z",
    }
    args.update(overrides)
    return runtime.call("upstream.calendar", OP, args, timeout_seconds=5)


def test_operator_monitor_curve_pins_version_and_restarts(tmp_path, monkeypatch):
    from bt_api_ctp.calendar_snapshot import CalendarSnapshot

    import bt_api_py.ctp_gateway_calendar_config as calendar_config
    from bt_api_py.gateway_calendar_service import ReceiptCheckedCalendarStore
    from bt_api_py.trading_calendar_binding import SimulationCalendarBinding
    from bt_api_py.trading_calendar_store import TradingCalendarStore

    doc, file, config, public, secret = setup_monitor(tmp_path, monkeypatch)
    store = FixtureCalendarStore(file.parent / "calendars")
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    original = calendar_config.create_calendar_service
    bindings = []

    def create(settings):
        service = original(settings)
        bound = SimulationCalendarBinding(
            mode="SIMULATION_ONLY",
            store=ReceiptCheckedCalendarStore(TradingCalendarStore(settings.root)),
            snapshot_factory=CalendarSnapshot,
            gate=FakeGate(),
            route="CTP___FUTURE",
            source_generation="test-generation",
            trading_day=date(2026, 1, 1),
            last_sequence=1,
        )
        service.rebind(bound)
        bindings.append(bound)
        return service

    monkeypatch.setattr(calendar_config, "create_calendar_service", create)
    app, client = (
        application(config, tmp_path / "launch.yaml"),
        monitor_client(doc, config, public, secret),
    )
    try:
        app.start(timeout_seconds=10)
        client.start(timeout_seconds=10)
        first = query(client)
        assert first["status"] == "COMPLETE" and first["binding"]["calendar_ref"] == "v1"
        assert first["registration_id"] and first["service_epoch"]
        assert first["binding"]["data_origin"] in {"SYNTHETIC_MOCK", "UNSPECIFIED"}
        assert first["binding"]["bound_trading_day"] == "2026-01-01"
        assert first["binding"]["source_generation"] == "test-generation"
        assert first["scope"]["mode"] == "SIMULATION_ONLY"
        assert (
            first["source_authentication"] == "NOT_PROVEN"
            and first["trade_authorization"] == "NONE"
        )
        assert first["policy"]["declaration"] == "DEPLOYMENT_DECLARED_UNVERIFIED"
        assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
        assert query(client)["binding"]["calendar_ref"] == "v1"
        with pytest.raises(ServiceRuntimeError):
            client.call("upstream.calendar", "calendar.windows.v1", {}, timeout_seconds=5)
        assert (
            bindings[-1]
            .activate(decision(route="CTP___FUTURE", generation="test-generation"))
            .outcome
            == "ACTIVATED"
        )
        after = query(client, start_utc="2026-01-02T02:00:00Z", end_utc="2026-01-02T02:30:00Z")
        assert after["status"] == "COMPLETE" and after["binding"]["calendar_ref"] == "v2"
        with pytest.raises(ServiceRuntimeError):
            query(client, contract="other")
    finally:
        client.close(timeout_seconds=10)
        app.stop(timeout_seconds=10)
    app, client = (
        application(config, tmp_path / "launch.yaml"),
        monitor_client(doc, config, public, secret),
    )
    try:
        app.start(timeout_seconds=10)
        client.start(timeout_seconds=10)
        assert query(client)["binding"]["calendar_ref"] == "v2"
    finally:
        client.close(timeout_seconds=10)
        app.stop(timeout_seconds=10)


def test_unchanged_operator_config_cannot_register_monitor_role(tmp_path, monkeypatch):
    doc, file, config, public, secret = configured(tmp_path, monkeypatch)
    assert (
        publish(FixtureCalendarStore(file.parent / "calendars"), "v1", content()).visibility
        == "COMMITTED"
    )
    strategy = doc["clients"][0]
    scope = dict(strategy["resources"][0]["scope"], strategy_ref=None, run_id=None)
    # The credential exists, but its registered identity remains a strategy.
    # Only the local client descriptor is changed; the protected bundle is untouched.
    doc["calendars"]["monitor_clients"] = [
        {
            "identity": {
                "role": "service",
                "principal_ref": strategy["identity"]["principal_ref"],
                "actor_ref": "ungranted-monitor",
                "delegate_ref": None,
            },
            "curve_public_key": public.decode(),
            "resources": [{"scope": scope}],
        }
    ]
    app = application(config, tmp_path / "launch.yaml")
    client = monitor_client(doc, config, public, secret)
    try:
        app.start(timeout_seconds=10)
        try:
            client.start(timeout_seconds=10)
            with pytest.raises((ServiceRuntimeError, ThreadedRuntimeError)):
                query(client)
        except (ServiceRuntimeError, ThreadedRuntimeError):
            pass
    finally:
        client.close(timeout_seconds=10)
        app.stop(timeout_seconds=10)


def test_monitor_operation_is_not_requested_implicitly(tmp_path, monkeypatch):
    doc, file, config, public, secret = setup_monitor(tmp_path, monkeypatch)
    assert (
        publish(FixtureCalendarStore(file.parent / "calendars"), "v1", content()).visibility
        == "COMMITTED"
    )
    app = application(config, tmp_path / "launch.yaml")
    client = monitor_client(doc, config, public, secret, requested=None)
    try:
        app.start(timeout_seconds=10)
        try:
            client.start(timeout_seconds=10)
            with pytest.raises((ServiceRuntimeError, ThreadedRuntimeError)):
                query(client)
        except (ServiceRuntimeError, ThreadedRuntimeError):
            pass
    finally:
        client.close(timeout_seconds=10)
        app.stop(timeout_seconds=10)


@pytest.mark.parametrize(
    "case",
    [
        "missing_grant",
        "missing_client",
        "principal",
        "actor",
        "domain",
        "account",
        "route",
        "venue",
        "kind",
        "product",
        "contract",
        "extra_scope",
        "delegate",
        "duplicate_identity",
        "duplicate_key",
        "duplicate_actor",
        "duplicate_monitor",
        "other_operation",
        "direct_port",
    ],
)
def test_monitor_configuration_denied_before_provider_start(tmp_path, monkeypatch, case):
    doc, file, config, _, _ = setup_monitor(tmp_path, monkeypatch)
    cal = doc["calendars"]
    client = cal["monitor_clients"][0]
    grant = cal["monitor_grants"][0]
    if case == "missing_grant":
        del cal["monitor_grants"]
    elif case == "missing_client":
        del cal["monitor_clients"]
    elif case in {
        "principal",
        "actor",
        "domain",
        "account",
        "route",
        "venue",
        "kind",
        "product",
        "contract",
    }:
        field = {
            "principal": "principal_ref",
            "actor": "actor_ref",
            "domain": "execution_domain_ref",
            "account": "account_ref",
        }.get(case, case)
        grant[field] = "other" if case != "kind" else "option"
    elif case == "extra_scope":
        client["resources"][0]["scope"]["strategy_ref"] = "extra"
    elif case == "delegate":
        client["identity"]["delegate_ref"] = "other"
    elif case == "duplicate_identity":
        client["identity"]["principal_ref"] = doc["clients"][0]["identity"]["principal_ref"]
    elif case == "duplicate_key":
        client["curve_public_key"] = doc["clients"][0]["curve_public_key"]
    elif case == "duplicate_actor":
        actor = doc["clients"][0]["identity"]["actor_ref"]
        client["identity"]["actor_ref"] = grant["actor_ref"] = actor
    elif case == "duplicate_monitor":
        cal["monitor_clients"].append(dict(client))
    elif case == "other_operation":
        client["resources"][0]["operations"].append("calendar.windows.v1")
    elif case == "direct_port":
        client["resources"][0]["port"] = "service.direct_write"
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError):
        load_local_ctp_resources(config)
