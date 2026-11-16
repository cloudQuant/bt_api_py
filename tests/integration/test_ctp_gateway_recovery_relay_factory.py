"""Real local Monitor/owner/recorder/relay; CTP source is synthetic."""

import inspect
import os
import time
from threading import Event, get_ident

import pytest
from bt_api_base.market_recovery_observation import RecoveryObservationPage
from bt_api_ctp.gateway_session import CtpGatewaySession
from bt_api_ctp.market_provider import CtpQuoteV2MarketProvider
from bt_api_monitor.durable import DurableOutbox

import bt_api_py.ctp_gateway_trading as trading
from bt_api_py.ctp_gateway_recovery_relay_config import build_recovery_monitor_settings
from tests.integration.test_gateway_recovery_observation_factory import options
from tests.integration.test_monitor_service_process import client, deployment, start, stop
from tests.test_iter63_ctp_gateway_trading import FakeProvider, FakeSession, _resources, _wait
from tests.unit.test_ctp_gateway_recovery_relay_config import relay_config
from tests.unit.test_gateway_recovery_qualified_capture import observation
from tests.unit.test_iter63_ctp_gateway_market_profile import setup


def test_factory_relay_to_real_local_curve_monitor(tmp_path, monkeypatch):
    import yaml

    build, _, settings, authority, _ = setup(tmp_path, monkeypatch)
    config = inspect.getclosurevars(build).nonlocals["config"]
    path, doc, keys = deployment(tmp_path)
    identity = authority.identity
    doc["schema"] = "bt_api_monitor.service.v2"
    doc["scope"] = {
        "execution_domain_ref": authority.scope.execution_domain_ref,
        "account_ref": settings.account_scope,
    }
    doc["storage"]["event_types"] = ["gateway.recovery.journal.v1"]
    doc["ingress"]["principals"][0].update(
        principal_ref=identity.principal_ref, actor_ref=identity.actor_ref
    )
    doc["heartbeat"]["principals"][0].update(
        principal_ref=identity.principal_ref,
        actor_ref=identity.actor_ref,
        public_key_file="producer.public",
    )
    scope = {
        "execution_domain_ref": authority.scope.execution_domain_ref,
        "account_ref": settings.account_scope,
        "account_refs": [],
        "strategy_ref": None,
        "run_id": None,
        "manual_book_ref": None,
        "portfolio_ref": None,
        "group_id": None,
        "leg_id": None,
    }
    doc["producer_bindings"] = [
        {
            "binding_ref": "gateway-source-binding",
            "source_ref": settings.market_source_ref,
            "identity": {"principal_ref": identity.principal_ref, "actor_ref": identity.actor_ref},
            "scope": scope,
            "outbox_scope": "events",
            "event_types": ["gateway.recovery.journal.v1"],
        }
    ]
    path.write_text(yaml.safe_dump(doc))
    records, owners = [], []

    class Session(FakeSession):
        def __init__(self, key, feed, stream, **kwargs):
            super().__init__(key, feed, stream, **kwargs)
            self.real = CtpGatewaySession(key, feed, stream, **kwargs)

        @property
        def market_provider(self):
            assert get_ident() == self.owner
            return self.real._market

    def source(provider, *, after_sequence, limit):
        assert type(provider) is CtpQuoteV2MarketProvider
        owners.append(get_ident())
        return RecoveryObservationPage(
            "b" * 32,
            os.getpid(),
            after_sequence,
            1,
            len(records),
            tuple(records[after_sequence : after_sequence + limit]),
            False,
        )

    monkeypatch.setattr(trading, "_load_ctp_types", lambda: (Session, FakeProvider))
    monkeypatch.setattr(CtpQuoteV2MarketProvider, "read_recovery_observations", source)
    source_settings = options(
        tmp_path,
        settings,
        max_events=512,
        qualification_mode="local_clock_v1",
        clock_max_interval_ns=1_000_000_000,
    )
    raw = relay_config(tmp_path)
    raw.update(
        monitor_identity={
            "role": "service",
            "principal_ref": doc["ingress"]["principal_ref"],
            "actor_ref": doc["ingress"]["service_id"],
            "delegate_ref": None,
        },
        instance_id=doc["ingress"]["instance_id"],
        scope_ref=doc["ingress"]["scope_ref"],
        rpc_connect=doc["ingress"]["rpc_bind"],
        outbox_scope="events",
        server_curve_public_key=keys["events"][0].decode(),
        client_curve_public_key=keys["producer"][0].decode(),
        client_curve_secret_key=keys["producer"][1].decode(),
    )
    relay_settings = build_recovery_monitor_settings(
        raw,
        config=config,
        authority=authority,
        account_scope=settings.account_scope,
        observation=source_settings,
    )
    from bt_api_py.service_monitor_authenticated import AuthenticatedMonitorV2Sink

    entered, release = Event(), Event()
    original_append = AuthenticatedMonitorV2Sink.append

    def blocked_append(sink, envelope):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("synthetic relay block timed out")
        return original_append(sink, envelope)

    monkeypatch.setattr(AuthenticatedMonitorV2Sink, "append", blocked_append)
    process = start(path)
    resources = None
    try:
        resources = _resources(
            build(
                recovery_observation_settings=source_settings,
                recovery_monitor_relay=relay_settings,
            ),
            authority,
        )
        binding = resources.values["binding"]
        worker = binding.recovery_monitor_relay_status.__self__
        assert entered.wait(2)
        assert binding._owner.submit_service_call(lambda: 17).result(timeout_seconds=1) == 17
        heartbeat = client(
            doc["heartbeat"],
            keys["heartbeat"][0],
            keys["producer"],
            identity.principal_ref,
            "monitor.heartbeat",
            actor=identity.actor_ref,
        )
        heartbeat._scope_resolver = lambda _: authority.scope.__class__(
            authority.scope.execution_domain_ref, account_ref=settings.account_scope
        )
        heartbeat.start()
        request = {
            "schema_version": 1,
            "relay_epoch": heartbeat.bindings[0].registration_id,
            "heartbeat_id": "heartbeat-1",
            "sent_at_ns": 1,
        }
        ack = heartbeat.call("upstream.telemetry", "monitor.heartbeat", request)
        assert ack["heartbeat_id"] == "heartbeat-1"
        heartbeat.close()
        release.set()
        observation_base = time.monotonic_ns() - 10_000_000
        records.append(observation(1, observation_base))
        outbox = DurableOutbox(tmp_path / "data/events.sqlite", read_only=True)

        def delivered():
            rows, _ = outbox.read_page_types("events", ("gateway.recovery.journal.v1",), 0, 50)
            return any(
                row.event.data["payload"]["event"].get("kind") == "observation" for row in rows
            )

        try:
            _wait(delivered)
        except AssertionError:
            rows, _ = outbox.read_page_types("events", ("gateway.recovery.journal.v1",), 0, 50)
            pytest.fail(
                f"relay={worker.status()} recorder={binding.recovery_observation_status()} rows={len(rows)}"
            )
        assert owners and set(owners) == {FakeSession.instances[-1].owner}
        original_binding = worker._runtime.bindings[0]
        _wait(lambda: worker._runtime.bindings[0] is not original_binding)
        renewed_binding = worker._runtime.bindings[0]
        _wait(lambda: worker._runtime.bindings[0] is not renewed_binding)
        assert worker.status()["state"] == "running", worker.status()
        records.append(observation(2, observation_base, state="RECOVERED", start=300, through=400))

        def delivered_after_renewals():
            rows, _ = outbox.read_page_types("events", ("gateway.recovery.journal.v1",), 0, 50)
            return (
                sum(row.event.data["payload"]["event"].get("kind") == "observation" for row in rows)
                >= 2
            )

        _wait(delivered_after_renewals)
        snapshot = binding._owner._lifecycle_snapshot()
        recorder = binding.recovery_observation_status.__self__
        real_recorder_close = type(recorder).close
        real_close = type(worker).close
        calls = []

        def tracked_relay_close(self):
            calls.append(self)
            return real_close(self)

        monkeypatch.setattr(type(recorder), "close", lambda self: False)
        monkeypatch.setattr(type(worker), "close", tracked_relay_close)
        with pytest.raises(
            trading.CtpGatewayTradingError, match="RECOVERY_OBSERVATION_CLOSE_UNCONFIRMED"
        ):
            resources.close()
        assert calls == [] and binding.recovery_monitor_relay_status.__self__ is worker
        assert not binding._owner._lifecycle_is_current(snapshot)
        monkeypatch.setattr(type(recorder), "close", real_recorder_close)

        def broken_close(_self):
            raise RuntimeError("private relay error")

        monkeypatch.setattr(type(worker), "close", broken_close)
        with pytest.raises(
            trading.CtpGatewayTradingError, match="^RECOVERY_MONITOR_RELAY_CLOSE_UNCONFIRMED$"
        ):
            resources.close()
        monkeypatch.setattr(type(worker), "close", lambda self: False)
        with pytest.raises(
            trading.CtpGatewayTradingError, match="RECOVERY_MONITOR_RELAY_CLOSE_UNCONFIRMED"
        ):
            resources.close()
        assert binding.recovery_monitor_relay_status.__self__ is worker
        assert not binding._owner._lifecycle_is_current(snapshot)
        monkeypatch.setattr(type(worker), "close", real_close)
        resources.close()
        resources = None
        assert worker.status()["state"] == "closed"
        assert worker.status()["resource_state"] == "COMPLETE"
    finally:
        release.set()
        if resources is not None:
            resources.close()
        stop(process)


def test_failed_relay_start_retains_the_original_worker_for_partial_cleanup(tmp_path, monkeypatch):
    from bt_api_py.gateway_recovery_monitor_worker import GatewayRecoveryMonitorWorker
    from bt_api_py.service_components import ServiceResourceConstructionError

    build, _, settings, authority, _ = setup(tmp_path, monkeypatch)
    config = inspect.getclosurevars(build).nonlocals["config"]

    class Session(FakeSession):
        def __init__(self, key, feed, stream, **kwargs):
            super().__init__(key, feed, stream, **kwargs)
            self.real = CtpGatewaySession(key, feed, stream, **kwargs)

        @property
        def market_provider(self):
            return self.real._market

    monkeypatch.setattr(trading, "_load_ctp_types", lambda: (Session, FakeProvider))
    source_settings = options(
        tmp_path, settings, qualification_mode="local_clock_v1", clock_max_interval_ns=1_000_000_000
    )
    relay_settings = build_recovery_monitor_settings(
        relay_config(tmp_path),
        config=config,
        authority=authority,
        account_scope=settings.account_scope,
        observation=source_settings,
    )
    seen = []

    def fail_start(worker):
        seen.append(worker)
        raise RuntimeError("private detail")

    monkeypatch.setattr(GatewayRecoveryMonitorWorker, "start", fail_start)
    with pytest.raises(ServiceResourceConstructionError) as failure:
        _resources(
            build(
                recovery_observation_settings=source_settings, recovery_monitor_relay=relay_settings
            ),
            authority,
            cleanup_on_error=False,
        )
    assert "private detail" not in str(failure.value)
    assert failure.value.code == "RECOVERY_MONITOR_RELAY_START_FAILED"
    retained = inspect.getclosurevars(failure.value.resources.close_resource).nonlocals["holder"]
    assert retained["recovery_monitor_relay"] is seen[0]
    failure.value.resources.close()
    assert seen[0].status()["state"] == "closed"
    assert FakeSession.instances[-1].events[-1][0] == "close"


def test_factory_rejects_relay_aliases_and_missing_source_before_owner(tmp_path, monkeypatch):
    from dataclasses import replace

    build, _, settings, authority, factories = setup(tmp_path, monkeypatch)
    config = inspect.getclosurevars(build).nonlocals["config"]
    source_settings = options(
        tmp_path, settings, qualification_mode="local_clock_v1", clock_max_interval_ns=1_000_000_000
    )
    relay = build_recovery_monitor_settings(
        relay_config(tmp_path),
        config=config,
        authority=authority,
        account_scope=settings.account_scope,
        observation=source_settings,
    )
    bad = (
        settings.journal_path,
        tmp_path / "registry-market.sqlite",
        source_settings.journal_path,
        settings.guard_root / "relay",
        settings.trusted_flow_root / "relay",
    )
    for path in bad:
        with pytest.raises(
            trading.CtpGatewayTradingError, match="INVALID_RECOVERY_MONITOR_RELAY_SETTINGS"
        ):
            build(
                recovery_observation_settings=source_settings,
                recovery_monitor_relay=replace(relay, checkpoint_path=path),
            )
    with pytest.raises(
        trading.CtpGatewayTradingError, match="INVALID_RECOVERY_MONITOR_RELAY_SETTINGS"
    ):
        build(recovery_monitor_relay=relay)
    assert not factories and not FakeSession.instances
