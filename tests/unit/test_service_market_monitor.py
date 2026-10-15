"""Focused tests for the best-effort Gateway market-to-Monitor relay."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import replace
from typing import Any

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.market_hub import GatewayMarketHub

from bt_api_py.service_market import ServiceMarketClient, ServiceMarketError
from bt_api_py.service_market_monitor import (
    ServiceMarketMonitorError,
    ServiceMarketMonitorRelay,
)
from bt_api_py.service_runtime import (
    RegisteredBinding,
    ServiceCallOutcomeUnknownError,
    ServiceClientRuntime,
    ServiceMarketBindingView,
    ServiceRuntimeRequestNotSentError,
)


class _Provider:
    def __init__(self) -> None:
        self.generations: dict[MarketSubscriptionKey, str] = {}

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        self.generations[key] = generation
        return object()

    def unsubscribe(self, handle: object) -> bool:
        del handle
        return True


def _scope(**changes: Any) -> WireScope:
    values: dict[str, Any] = {"execution_domain_ref": "paper:demo"}
    values.update(changes)
    return WireScope(**values)


def _key() -> MarketSubscriptionKey:
    return MarketSubscriptionKey(
        source_ref="source:public",
        entitlement_ref="entitlement:public",
        instrument=MarketInstrumentKey(
            provider="synthetic",
            venue="test-venue",
            environment="paper",
            product_type="spot",
            instrument_id="instrument-1",
            native_symbol="TST-USD",
        ),
        channel="trades",
    )


def _events(count_events: int = 2):
    key = _key()
    provider = _Provider()
    published = []
    hub = GatewayMarketHub(
        provider,
        key_universe=(key,),
        principal_allowed_keys={"relay:market": (key,)},
        service_identity=WireIdentity(
            role=WireRole.SERVICE,
            principal_ref="service:gateway",
            actor_ref="gateway:market-hub",
        ),
        service_scope=_scope(),
        epoch_factory=lambda: "gateway-epoch-1",
    )
    hub.set_publisher(published.append)
    hub.subscribe("relay:market", key, "registration:market")
    for index in range(1, count_events + 1):
        hub.publish(
            key,
            {"price": 100 + index},
            f"2026-10-15T10:00:{index:02d}Z",
            generation=provider.generations[key],
        )
    return key, tuple(published)


def _make_relay(monkeypatch, frames, *, monitor_scope=None, max_pending_events=4):
    market_scope = _scope()
    monitor_scope = market_scope if monitor_scope is None else monitor_scope
    runtime = object.__new__(ServiceClientRuntime)
    runtime._owner_thread_id = threading.get_ident()
    runtime.identity = WireIdentity(
        role=WireRole.SERVICE,
        principal_ref="service:monitor-relay",
        actor_ref="monitor-relay:paper",
    )
    runtime._bindings = [
        RegisteredBinding(
            mount="upstream.telemetry",
            service_id="service:monitor-ingress",
            instance_id="instance:monitor-ingress",
            scope_ref="monitor:system",
            scope=monitor_scope,
            registration_id="registration:monitor",
            service_epoch="monitor-epoch-1",
            operations=frozenset({"events.append"}),
            expires_at=100.0,
            policy_version="policy-v1",
            requirements_version="requirements-v1",
            transport_ref="transport:monitor",
            timeout=0.5,
        )
    ]
    gateway = WireIdentity(
        role=WireRole.SERVICE,
        principal_ref="service:gateway",
        actor_ref="gateway:market-hub",
    )
    view = ServiceMarketBindingView(
        mount="upstream.market",
        service_id="service:gateway",
        instance_id="instance:gateway",
        scope_ref="gateway:public-market",
        registration_id="registration:gateway",
        service_epoch="gateway-epoch-1",
        rpc_scope=market_scope,
        operations=frozenset(
            {
                "market.subscribe",
                "market.unsubscribe",
                "market.snapshot",
                "market.head",
                "market.read_after",
            }
        ),
        transport_ref="transport:gateway",
        timeout=0.5,
        peer=gateway,
        market_endpoint="tcp://127.0.0.1:9001",
        security_profile_ref="security:gateway",
        high_water_mark=10,
        max_message_bytes=64 * 1024,
    )
    monkeypatch.setattr(
        ServiceClientRuntime,
        "market_binding_view",
        lambda self, mount, *, required_operations: view,
    )
    client = object.__new__(ServiceMarketClient)
    client._runtime = runtime
    client.mount = "upstream.market"
    client.stream_scope = market_scope
    client.key = _key()
    client._recovery_required = False
    client._resync_required = False
    frame_queue = deque(frames)
    start_calls: list[bool] = []

    def start(_self):
        start_calls.append(True)
        return object()

    def poll(_self, timeout_ms=0):
        assert 0 <= timeout_ms <= 60_000
        return frame_queue.popleft() if frame_queue else None

    monkeypatch.setattr(ServiceMarketClient, "start", start)
    monkeypatch.setattr(ServiceMarketClient, "poll", poll)
    runtime_calls: list[tuple[str, str, dict[str, Any], str | None]] = []
    relay = ServiceMarketMonitorRelay(
        client,
        runtime,
        monitor_scope_ref="monitor:system",
        system_ref="system:paper",
        process_ref="process:monitor-relay",
        max_pending_events=max_pending_events,
    )
    return relay, runtime, runtime_calls, start_calls


def test_relay_keeps_gateway_frame_and_retries_same_monitor_request_after_unknown(
    monkeypatch,
) -> None:
    _, frames = _events()
    relay, runtime, runtime_calls, start_calls = _make_relay(monkeypatch, frames)
    runtime_behavior = deque(["unknown", "success", "success"])

    def call(self, mount, operation, arguments, *, scope_ref=None):
        runtime_calls.append((mount, operation, dict(arguments), scope_ref))
        behavior = runtime_behavior.popleft()
        if behavior == "unknown":
            raise ServiceCallOutcomeUnknownError("message:unknown", "events.append")
        return {
            "event_id": f"monitor-row:{arguments['event_id']}",
            "source_event_id": arguments["event_id"],
            "sequence": len(runtime_calls),
        }

    monkeypatch.setattr(ServiceClientRuntime, "call", call)
    relay.start()
    assert len(start_calls) == 1

    first = relay.pump_once()
    assert first is frames[0]
    cut = relay.status
    assert cut.delivery_state == "UNKNOWN_RETRY_PENDING"
    assert cut.pending_events == 1
    assert cut.unknown_append_outcomes == 1
    assert cut.market_state == "STARTED"

    second = relay.pump_once()
    assert second is frames[1]
    after_second = relay.status
    assert after_second.appended_observations == 1
    assert after_second.pending_events == 1
    assert after_second.lost_observations == 0

    assert relay.pump_once() is None
    final = relay.status
    assert final.appended_observations == 2
    assert final.pending_events == final.pending_bytes == 0
    assert final.delivery_state == "APPENDED"
    assert not hasattr(final, "healthy")

    assert [entry[0:2] for entry in runtime_calls] == [
        ("upstream.telemetry", "events.append"),
        ("upstream.telemetry", "events.append"),
        ("upstream.telemetry", "events.append"),
    ]
    assert all(entry[3] == "monitor:system" for entry in runtime_calls)
    assert runtime_calls[0][2] == runtime_calls[1][2]
    assert runtime_calls[1][2]["event_id"] != runtime_calls[2][2]["event_id"]


def test_pending_capacity_is_visible_and_does_not_discard_market_frame(monkeypatch) -> None:
    _, frames = _events()
    relay, _, runtime_calls, _ = _make_relay(monkeypatch, frames, max_pending_events=1)

    def not_sent(self, mount, operation, arguments, *, scope_ref=None):
        runtime_calls.append((mount, operation, dict(arguments), scope_ref))
        raise ServiceRuntimeRequestNotSentError()

    monkeypatch.setattr(ServiceClientRuntime, "call", not_sent)
    relay.start()
    assert relay.pump_once() is frames[0]
    assert relay.pump_once() is frames[1]

    status = relay.status
    assert status.received_frames == 2
    assert status.queued_observations == 1
    assert status.pending_events == 1
    assert status.append_failures == 2
    assert status.lost_observations == 1
    assert status.last_loss_code == "PENDING_CAPACITY"
    assert status.delivery_state == "RETRY_PENDING"
    assert len(runtime_calls) == 2
    assert runtime_calls[0][2]["event_id"] == runtime_calls[1][2]["event_id"]
    assert all(call[3] == "monitor:system" for call in runtime_calls)


def test_market_disconnect_remains_visible_instead_of_becoming_healthy_none(monkeypatch) -> None:
    _, frames = _events()
    relay, _, runtime_calls, _ = _make_relay(monkeypatch, frames)

    def disconnected(client, timeout_ms=0):
        del timeout_ms
        client._recovery_required = True
        raise ServiceMarketError("RECEIVE_FAILED", cause_code="SUBSCRIBER_CLOSED")

    monkeypatch.setattr(ServiceMarketClient, "poll", disconnected)
    relay.start()
    assert relay.pump_once() is None
    status = relay.status
    assert status.market_state == "RECOVERY_REQUIRED"
    assert status.market_recovery_required is True
    assert status.last_market_error == "RECEIVE_FAILED"
    assert status.received_frames == 0
    assert runtime_calls == []
    assert not hasattr(status, "healthy")


def test_relay_rejects_a_different_runtime_identity_for_the_market_client(monkeypatch) -> None:
    _, frames = _events()
    relay, _, _, _ = _make_relay(monkeypatch, frames)
    other_runtime = object.__new__(ServiceClientRuntime)
    other_runtime.identity = WireIdentity(
        role=WireRole.SERVICE,
        principal_ref="service:other-relay",
        actor_ref="monitor-relay:other",
    )

    with pytest.raises(ServiceMarketMonitorError) as error:
        ServiceMarketMonitorRelay(
            relay._market_client,
            other_runtime,
            monitor_scope_ref="monitor:system",
            system_ref="system:paper",
            process_ref="process:monitor-relay",
        )
    assert error.value.code == "MARKET_RUNTIME_MISMATCH"


def test_relay_stops_appending_if_the_fixed_monitor_registration_changes(monkeypatch) -> None:
    _, frames = _events()
    relay, runtime, runtime_calls, _ = _make_relay(monkeypatch, frames)
    runtime._bindings[0] = replace(runtime._bindings[0], registration_id="registration:replaced")

    def call(self, mount, operation, arguments, *, scope_ref=None):
        runtime_calls.append((mount, operation, dict(arguments), scope_ref))
        return {
            "event_id": "monitor-row:unexpected",
            "source_event_id": arguments["event_id"],
            "sequence": 1,
        }

    monkeypatch.setattr(ServiceClientRuntime, "call", call)
    relay.start()
    assert relay.pump_once() is frames[0]
    status = relay.status
    assert runtime_calls == []
    assert status.pending_events == 1
    assert status.append_failures == 1
    assert status.last_delivery_error == "MONITOR_BINDING_CHANGED"
    assert status.delivery_state == "RETRY_PENDING"


@pytest.mark.parametrize(
    "scope",
    [
        _scope(account_ref="account:a"),
        _scope(account_ref="account:a", strategy_ref="strategy:a"),
    ],
)
def test_relay_rejects_account_or_strategy_scoped_monitor_binding(monkeypatch, scope) -> None:
    _, frames = _events()
    with pytest.raises(ServiceMarketMonitorError) as error:
        _make_relay(monkeypatch, frames, monitor_scope=scope)
    assert error.value.code == "SYSTEM_MONITOR_SCOPE_REQUIRED"
