"""Typed Monitor calendar reads with local CURVE and a synthetic Gateway profile."""

from dataclasses import replace

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_monitor_calendar_client import (
    GatewayMonitorCalendarClient,
    MonitorCalendarClientError,
)
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from tests.calendar_test_store import FixtureCalendarStore
from tests.integration.test_ctp_gateway_monitor_calendar import setup_monitor
from tests.integration.test_ctp_gateway_operator_calendar import application
from tests.integration.test_monitor_service_process import client as telemetry_client
from tests.unit.test_trading_calendar_binding import content, publish

pytestmark = pytest.mark.skipif(not zmq.has("curve"), reason="CURVE unavailable")


def test_monitor_calendar_client_authentication_and_two_real_renewals(tmp_path, monkeypatch):
    doc, file, config, public, secret = setup_monitor(tmp_path, monkeypatch)
    assert (
        publish(FixtureCalendarStore(file.parent / "calendars"), "v1", content()).visibility
        == "COMMITTED"
    )
    server_identity = doc["authority"]["identity"]
    peer = WireIdentity(
        WireRole.SERVICE, server_identity["principal_ref"], server_identity["actor_ref"]
    )
    identity = WireIdentity(WireRole.SERVICE, "monitor-p", "monitor-a")
    scope_raw = doc["calendars"]["monitor_clients"][0]["resources"][0]["scope"]
    scope = WireScope(scope_raw["execution_domain_ref"], account_ref=scope_raw["account_ref"])
    endpoint = config.communication["profiles"]["server-transport"]["endpoints"]["rpc_bind"]
    listener = {
        "principal_ref": peer.principal_ref,
        "service_id": peer.actor_ref,
        "instance_id": "gateway-a",
        "scope_ref": "scope-a",
        "rpc_bind": endpoint,
    }
    app = application(config, tmp_path / "launch.yaml")
    runtime = None
    try:
        app.start(timeout_seconds=10)
        template = telemetry_client(
            listener,
            doc["authority"]["curve_public_key"].encode(),
            (public, secret),
            identity.principal_ref,
            "events.append",
            actor=identity.actor_ref,
        )
        raw = template._config
        raw["clients"] = {"gateway": raw["clients"].pop("monitor")}
        raw["clients"]["gateway"]["settings"]["bindings"][0].update(
            mount="upstream.calendar", account_ref=scope.account_ref
        )
        runtime = ServiceClientRuntime(
            parse_service_config(raw),
            identity=identity,
            scope_resolver=lambda _: scope,
            peer_resolver=lambda _: peer,
            transport_factory=ZmqTransportFactory(
                {
                    "client": ClientCurveKeys(
                        doc["authority"]["curve_public_key"].encode(), public, secret
                    )
                }
            ),
            requested_operations={"upstream.calendar": frozenset({"calendar.monitor.windows.v1"})},
        )
        runtime.start()
        client = GatewayMonitorCalendarClient(
            runtime,
            identity=identity,
            scope=scope,
            peer=peer,
            service_id=peer.actor_ref,
            instance_id="gateway-a",
            scope_ref="scope-a",
        )

        def read():
            return client.windows(
                route="CTP___FUTURE",
                venue="SIM",
                kind="future",
                product="x",
                contract="x2601",
                start_utc="2026-01-01T01:30:00Z",
                end_utc="2026-01-01T02:00:00Z",
            )

        first = read()
        assert first.status == "COMPLETE" and first.binding["calendar_ref"] == "v1"
        assert (
            first.scope["mode"] == "SIMULATION_ONLY" and first.source_authentication == "NOT_PROVEN"
        )
        original = runtime.bindings[0]
        runtime._maintain(force=True)
        first_renewal = runtime.bindings[0]
        runtime._maintain(force=True)
        second_renewal = runtime.bindings[0]
        assert original is not first_renewal and first_renewal is not second_renewal
        assert read().binding["calendar_ref"] == "v1"
        original_call = runtime.call

        def renew_after_reply(*args, **kwargs):
            response = original_call(*args, **kwargs)
            runtime._maintain(force=True)
            return response

        runtime.call = renew_after_reply
        with pytest.raises(MonitorCalendarClientError, match="CALENDAR_UNAVAILABLE"):
            read()
        third_renewal = runtime.bindings[0]
        assert third_renewal is not second_renewal
        assert third_renewal.registration_id == second_renewal.registration_id
        assert third_renewal.service_epoch == second_renewal.service_epoch
        runtime.call = original_call
        assert read().status == "COMPLETE"
        second_renewal = third_renewal
        forged = replace(second_renewal)
        runtime._bindings = [forged]
        with pytest.raises(MonitorCalendarClientError, match="CALENDAR_UNAVAILABLE"):
            read()
        runtime._bindings = [second_renewal]
        assert read().status == "COMPLETE"
        with pytest.raises(MonitorCalendarClientError, match="BINDING_UNAVAILABLE"):
            GatewayMonitorCalendarClient(
                runtime,
                identity=identity,
                scope=scope,
                peer=replace(peer, actor_ref="other"),
                service_id=peer.actor_ref,
                instance_id="gateway-a",
                scope_ref="scope-a",
            )
        with pytest.raises(MonitorCalendarClientError, match="BINDING_UNAVAILABLE"):
            GatewayMonitorCalendarClient(
                runtime,
                identity=identity,
                scope=replace(scope, account_ref="other"),
                peer=peer,
                service_id=peer.actor_ref,
                instance_id="gateway-a",
                scope_ref="scope-a",
            )
        with pytest.raises(MonitorCalendarClientError, match="CALENDAR_UNAVAILABLE"):
            client.windows(
                route="CTP___FUTURE",
                venue="SIM",
                kind="future",
                product="x",
                contract="other",
                start_utc="2026-01-01T01:30:00Z",
                end_utc="2026-01-01T02:00:00Z",
            )
        client.close()
        assert runtime.state == "registered"
    finally:
        if runtime is not None:
            runtime.close()
        app.stop(timeout_seconds=10)
