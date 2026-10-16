"""Owner-thread market replay, recovery and loss signaling tests."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from bt_api_base.market_protocol import (
    MARKET_EVENT_OPERATION,
    MARKET_EVENT_SCHEMA,
    MARKET_EVENT_SCHEMA_VERSION,
    MarketInstrumentKey,
    MarketSourceDescriptor,
    MarketSubscriptionKey,
)
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
    encode_envelope,
)

from bt_api_py.service_config import parse_client_config
from bt_api_py.service_market import ServiceMarketClient, ServiceMarketError
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import (
    ServiceClientRuntime,
    ServiceRuntimeRequestNotSentError,
)

_CLIENT = WireIdentity(WireRole.STRATEGY_CLIENT, "market-client", "strategy-market")
_PEER = WireIdentity(WireRole.SERVICE, "market-service-principal", "market-service")
_DOMAIN = "paper:market-unit"
_PUBLIC_SCOPE = WireScope(_DOMAIN)
_CLIENT_SCOPE = WireScope(_DOMAIN, strategy_ref="strategy-market")
_OPERATIONS = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)
_KEY = MarketSubscriptionKey(
    source_ref="unit-source",
    entitlement_ref="unit-public-entitlement",
    instrument=MarketInstrumentKey(
        provider="synthetic",
        venue="unit",
        environment="paper",
        product_type="spot",
        instrument_id="asset-a",
        native_symbol="ASSET-A",
    ),
    channel="trades",
)
_OTHER_KEY = MarketSubscriptionKey(
    source_ref="unit-source",
    entitlement_ref="unit-public-entitlement",
    instrument=MarketInstrumentKey(
        provider="synthetic",
        venue="unit",
        environment="paper",
        product_type="spot",
        instrument_id="asset-b",
        native_symbol="ASSET-B",
    ),
    channel="trades",
)


class ManualClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def _plain_envelope(event: WireEnvelope) -> dict[str, object]:
    return json.loads(encode_envelope(event))


class FakeMarketService:
    """Service handlers with exact market RPC shapes and counted upstream refs."""

    def __init__(self) -> None:
        self.epochs: dict[str, str] = {}
        self.events: dict[str, list[WireEnvelope]] = {}
        self.registrations: dict[str, set[str]] = {}
        self.starts: dict[str, int] = {}
        self.stops: dict[str, int] = {}
        self.subscribe_arguments: list[dict[str, object]] = []
        self.source_descriptor: MarketSourceDescriptor | None = None

    def handle(self, context, arguments, operation: str):
        key = MarketSubscriptionKey.from_dict(arguments["key"])
        stream_id = key.stream_id
        epoch = self.epochs.setdefault(stream_id, "market-epoch-one")
        events = self.events.setdefault(stream_id, [])
        registrations = self.registrations.setdefault(stream_id, set())
        if operation == "market.subscribe":
            self.subscribe_arguments.append(dict(arguments))
            already_subscribed = context.registration_id in registrations
            if not registrations:
                self.starts[stream_id] = self.starts.get(stream_id, 0) + 1
            registrations.add(context.registration_id)
            reply = {
                "stream_id": stream_id,
                "epoch": epoch,
                "head": events[-1].header.sequence if events else 0,
                "oldest_sequence": events[0].header.sequence if events else 1,
                "already_subscribed": already_subscribed,
            }
            if arguments.get("require_source_descriptor") is True and self.source_descriptor is not None:
                reply["source_descriptor"] = self.source_descriptor.to_dict()
            return reply
        if operation == "market.unsubscribe":
            registrations.discard(context.registration_id)
            if not registrations:
                self.stops[stream_id] = self.stops.get(stream_id, 0) + 1
            return {
                "stream_id": stream_id,
                "epoch": epoch,
                "head": events[-1].header.sequence if events else 0,
                "upstream_released": not registrations,
            }
        if context.registration_id not in registrations:
            raise AssertionError("read arrived without a subscription")
        head = events[-1].header.sequence if events else 0
        oldest = events[0].header.sequence if events else head + 1
        if operation == "market.head":
            latest = events[-1].header.sent_at_utc if events else None
            return {
                "stream_id": stream_id,
                "epoch": epoch,
                "head": head,
                "oldest_sequence": oldest,
                "latest_observed_at_utc": latest,
            }
        if operation == "market.snapshot":
            return {
                "snapshot_kind": "latest_event",
                "complete": False,
                "stream_id": stream_id,
                "epoch": epoch,
                "head": head,
                "event": _plain_envelope(events[-1]) if events else None,
            }
        if operation == "market.read_after":
            after = arguments["after"]
            limit = arguments["limit"]
            requested_epoch = arguments["epoch"]
            gap = requested_epoch != epoch or after < oldest - 1
            reason = None
            selected: list[WireEnvelope] = []
            if gap:
                reason = "epoch_mismatch" if requested_epoch != epoch else "cursor_too_old"
            else:
                selected = [event for event in events if event.header.sequence > after][:limit]
            next_after = selected[-1].header.sequence if selected else after
            return {
                "stream_id": stream_id,
                "epoch": epoch,
                "head": head,
                "oldest_sequence": oldest,
                "after": after,
                "next_after": next_after,
                "limit": limit,
                "has_more": next_after < head,
                "gap": gap,
                "gap_reason": reason,
                "events": [_plain_envelope(event) for event in selected],
            }
        raise AssertionError(f"unexpected market operation: {operation}")

    def publish(self, key: MarketSubscriptionKey, marker: str) -> WireEnvelope:
        stream_id = key.stream_id
        epoch = self.epochs.setdefault(stream_id, "market-epoch-one")
        events = self.events.setdefault(stream_id, [])
        sequence = len(events) + 1
        event = self.make_event(key, marker, epoch=epoch, sequence=sequence)
        events.append(event)
        return event

    def make_event(
        self,
        key: MarketSubscriptionKey,
        marker: str,
        *,
        epoch: str,
        sequence: int,
    ) -> WireEnvelope:
        event = WireEnvelope(
            WireHeader(
                protocol_version=2,
                kind=WireMessageKind.EVENT,
                message_id=uuid4().hex,
                correlation_id=None,
                identity=_PEER,
                scope=_PUBLIC_SCOPE,
                operation=MARKET_EVENT_OPERATION,
                schema_id=MARKET_EVENT_SCHEMA,
                schema_version=MARKET_EVENT_SCHEMA_VERSION,
                sent_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                stream_id=key.stream_id,
                stream_epoch=epoch,
                sequence=sequence,
            ),
            {"key": key.to_dict(), "data": {"marker": marker}},
        )
        return event

    def reset_epoch(self, key: MarketSubscriptionKey) -> WireEnvelope:
        self.epochs[key.stream_id] = "market-epoch-two"
        self.events[key.stream_id] = []
        return self.publish(key, "new-epoch")


class LocalControlTransport:
    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry
        self.fail_after_dispatch: set[str] = set()
        self.not_sent: set[str] = set()

    def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
        del timeout
        operation = request.header.operation
        if operation in self.not_sent:
            self.not_sent.remove(operation)
            raise ServiceRuntimeRequestNotSentError()
        result = self.registry.handle(_CLIENT.principal_ref, request)
        if operation in self.fail_after_dispatch:
            self.fail_after_dispatch.remove(operation)
            raise TimeoutError("injected lost service reply")
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=_PEER,
            ),
            result,
        )

    def close(self) -> None:
        pass


class FakeSubscriber:
    def __init__(self) -> None:
        self.events: list[WireEnvelope] = []
        self.fail_receive = False
        self.fail_close_once = False
        self.closed = False

    def receive(self, *, timeout: float | None = None) -> WireEnvelope | None:
        del timeout
        if self.fail_receive:
            self.fail_receive = False
            raise OSError("synthetic subscriber failure")
        if not self.events:
            return None
        return self.events.pop(0)

    def close(self) -> None:
        if self.fail_close_once:
            self.fail_close_once = False
            raise OSError("synthetic local close failure")
        self.closed = True


class FakeSubscriberFactory:
    def __init__(self) -> None:
        self.created: list[FakeSubscriber] = []

    def __call__(self, _binding, _scope) -> FakeSubscriber:
        subscriber = FakeSubscriber()
        self.created.append(subscriber)
        return subscriber


def _make_client(
    tmp_path,
    *,
    key: MarketSubscriptionKey = _KEY,
    queue_capacity=16,
    queue_max_bytes=1_048_576,
    require_source_descriptor=False,
):
    capability = ServiceCapability("market-instance", "service.market_source", _OPERATIONS)
    grant = ResourceGrant(
        "market-instance",
        "service.market_source",
        "market-scope-v1",
        _CLIENT_SCOPE,
        _OPERATIONS,
    )
    registry = ServiceRegistry(
        tmp_path / f"market-{uuid4().hex}.sqlite3",
        service_id="market-service",
        capabilities=(capability,),
        grants={_CLIENT.principal_ref: (PrincipalGrant(_CLIENT, (grant,)),)},
        policy_version="market-policy-v1",
        requirements_version="market-requirements-v1",
        lease_seconds=30,
    )
    market = FakeMarketService()
    for operation in _OPERATIONS:
        registry.register_handler(
            "market-instance",
            operation,
            lambda context, arguments, op=operation: market.handle(context, arguments, op),
        )
    registry.set_ready(True)
    transport = LocalControlTransport(registry)
    raw = {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": _CLIENT.actor_ref},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "store.market",
                            "service_id": "market-service",
                            "instance_id": "market-instance",
                            "scope_ref": "market-scope-v1",
                            "transport_ref": "market-client-profile",
                        }
                    ],
                    "options": {"request_timeout_ms": 1_000},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "market-client-profile": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {
                        "rpc_connect": "tcp://127.0.0.1:7711",
                        "market_connect": "tcp://127.0.0.1:7712",
                    },
                    "security_profile_ref": "market-keys",
                    "options": {
                        "request_timeout_ms": 1_000,
                        "high_water_mark": 128,
                        "max_message_bytes": 262_144,
                    },
                }
            },
        },
    }
    config = parse_client_config(raw)
    clock = ManualClock()
    runtime = ServiceClientRuntime(
        config,
        identity=_CLIENT,
        scope_resolver=lambda _scope_ref: _CLIENT_SCOPE,
        peer_resolver=lambda _service_id: _PEER,
        transport_factory=lambda _profile, _peer: transport,
        run_id="market-run-1",
        requested_operations={"store.market": _OPERATIONS},
        monotonic_clock=clock,
    )
    runtime.start()
    subscribers = FakeSubscriberFactory()
    client = ServiceMarketClient(
        runtime,
        "store.market",
        key,
        subscriber_factory=subscribers,
        stream_scope=_PUBLIC_SCOPE,
        queue_capacity=queue_capacity,
        queue_max_bytes=queue_max_bytes,
        head_check_interval_seconds=10.0,
        monotonic_clock=clock,
        require_source_descriptor=require_source_descriptor,
    )
    return client, runtime, registry, market, transport, subscribers


def _close_all(client, runtime, registry) -> None:
    try:
        if client._state not in {"new", "closed"}:
            client.close()
    finally:
        if runtime.state != "closed":
            runtime.close()
        registry.close()


def test_recover_reuses_registration_and_replays_lost_pub_tail(tmp_path):
    client, runtime, registry, market, _transport, subscribers = _make_client(tmp_path)
    try:
        initial = client.start()
        assert initial.snapshot_complete is False
        assert initial.full_baseline_available is False
        assert market.starts[_KEY.stream_id] == 1
        assert market.stops.get(_KEY.stream_id, 0) == 0

        # Complete the initial tail check before simulating a dropped PUB frame.
        assert client.poll(0) is None
        missed = market.publish(_KEY, "missed-during-pub-outage")
        first_subscriber = subscribers.created[0]
        first_subscriber.fail_receive = True
        with pytest.raises(ServiceMarketError) as error:
            client.poll(0)
        assert error.value.code == "RECEIVE_FAILED"

        recovered = client.recover()
        assert recovered.resync_required is False
        assert recovered.cursor_epoch == initial.cursor_epoch
        assert recovered.cursor_sequence == 0
        assert market.starts[_KEY.stream_id] == 1
        assert market.stops.get(_KEY.stream_id, 0) == 0
        assert len(subscribers.created) == 2
        assert first_subscriber.closed is True

        replayed = client.poll(0)
        assert replayed is not None
        assert replayed.header.stream_epoch == missed.header.stream_epoch
        assert replayed.header.sequence == missed.header.sequence == 1
        assert replayed.payload["data"]["marker"] == "missed-during-pub-outage"
        assert client.healthy is True
    finally:
        _close_all(client, runtime, registry)


def test_pub_filters_other_keys_and_own_epoch_change_requires_resync(tmp_path):
    client, runtime, registry, market, _transport, subscribers = _make_client(tmp_path)
    try:
        initial = client.start()
        assert client.poll(0) is None
        subscriber = subscribers.created[0]
        subscriber.events.append(market.publish(_OTHER_KEY, "other-key"))
        assert client.poll(0) is None
        assert client.healthy is True
        assert client.resync_required is False

        changed_epoch_event = market.reset_epoch(_KEY)
        subscriber.events.append(changed_epoch_event)
        with pytest.raises(ServiceMarketError) as error:
            client.poll(0)
        assert error.value.code == "RESYNC_REQUIRED"
        assert client.resync_required is True
        pending = client.last_bootstrap
        assert pending is not None and pending.resync_required is True
        assert pending.snapshot_epoch == changed_epoch_event.header.stream_epoch
        assert pending.cursor_epoch == initial.cursor_epoch
        assert pending.cursor_sequence == 0
        assert pending.snapshot_complete is False
        assert pending.full_baseline_available is False

        with pytest.raises(ServiceMarketError) as error:
            client.accept_resync(acknowledge_data_loss=False)
        assert error.value.code == "RESYNC_ACKNOWLEDGEMENT_REQUIRED"
        accepted = client.accept_resync(acknowledge_data_loss=True)
        assert accepted.resync_required is False
        assert accepted.snapshot_complete is False
        assert accepted.full_baseline_available is False
        assert client.healthy is True
    finally:
        _close_all(client, runtime, registry)


def test_late_old_epoch_pub_after_resync_is_ignored(tmp_path):
    client, runtime, registry, market, _transport, subscribers = _make_client(tmp_path)
    try:
        initial = client.start()
        assert client.poll(0) is None
        subscriber = subscribers.created[0]

        changed_epoch_event = market.reset_epoch(_KEY)
        subscriber.events.append(changed_epoch_event)
        with pytest.raises(ServiceMarketError) as error:
            client.poll(0)
        assert error.value.code == "RESYNC_REQUIRED"

        accepted = client.accept_resync(acknowledge_data_loss=True)
        assert accepted.resync_required is False
        assert accepted.cursor_epoch == changed_epoch_event.header.stream_epoch
        assert accepted.cursor_sequence == 1
        assert client.healthy is True

        old_epoch_late = market.make_event(
            _KEY,
            "late-old-epoch",
            epoch=initial.cursor_epoch,
            sequence=2,
        )
        subscriber.events.append(old_epoch_late)
        assert client.poll(0) is None
        assert client.healthy is True
        assert client.resync_required is False

        next_new_epoch = market.publish(_KEY, "next-new-epoch")
        subscriber.events.append(next_new_epoch)
        received = client.poll(0)
        assert received is not None
        assert received.header.stream_epoch == changed_epoch_event.header.stream_epoch
        assert received.header.sequence == 2
        assert received.payload["data"]["marker"] == "next-new-epoch"
        assert client.healthy is True
    finally:
        _close_all(client, runtime, registry)


def test_bounded_queue_preserves_prefix_and_recovery_replays_remainder(tmp_path):
    client, runtime, registry, market, _transport, _subscribers = _make_client(
        tmp_path, queue_capacity=1
    )
    try:
        client.start()
        first = market.publish(_KEY, "first")
        second = market.publish(_KEY, "second")

        delivered = client.poll(0)
        assert delivered is not None and delivered.header.sequence == first.header.sequence
        assert delivered.payload["data"]["marker"] == "first"
        assert client.queue_count == 0
        assert client.recovery_required is True

        recovered = client.recover()
        assert recovered.resync_required is False
        replayed = client.poll(0)
        assert replayed is not None and replayed.header.sequence == second.header.sequence
        assert replayed.payload["data"]["marker"] == "second"
        assert client.healthy is True
    finally:
        _close_all(client, runtime, registry)


def test_byte_limit_bounds_replay_even_when_event_count_fits(tmp_path):
    byte_budget = 300_000
    client, runtime, registry, market, _transport, _subscribers = _make_client(
        tmp_path, queue_capacity=8, queue_max_bytes=byte_budget
    )
    try:
        client.start()
        first = market.publish(_KEY, "a" * 190_000)
        second = market.publish(_KEY, "b" * 190_000)

        delivered = client.poll(0)
        assert delivered is not None and delivered.header.sequence == first.header.sequence
        assert client.queue_count == 0
        assert client.queue_bytes == 0
        assert client.recovery_required is True

        client.recover()
        assert client.queue_count == 1
        assert 0 < client.queue_bytes <= byte_budget
        replayed = client.poll(0)
        assert replayed is not None and replayed.header.sequence == second.header.sequence
        assert replayed.payload["data"]["marker"] == "b" * 190_000
        assert client.queue_bytes == 0
    finally:
        _close_all(client, runtime, registry)


def test_timeout_after_unsubscribe_keeps_unknown_remote_outcome_observable(tmp_path):
    client, runtime, registry, market, transport, _subscribers = _make_client(tmp_path)
    client.start()
    transport.fail_after_dispatch.add("market.unsubscribe")
    with pytest.raises(ServiceMarketError) as error:
        client.close()
    assert error.value.code == "UNSUBSCRIPTION_UNCERTAIN"
    # The fake service applied the request, but no reply reached the client.
    assert market.registrations[_KEY.stream_id] == set()
    assert client.unsubscription_uncertain is True
    assert client._state == "closed"
    runtime.close()
    registry.close()


def test_not_sent_subscribe_does_not_create_remote_registration(tmp_path):
    client, runtime, registry, market, transport, _subscribers = _make_client(tmp_path)
    try:
        transport.not_sent.add("market.subscribe")
        with pytest.raises(ServiceMarketError) as error:
            client.start()
        assert error.value.code == "CONTROL_REQUEST_NOT_SENT"
        assert market.registrations.get(_KEY.stream_id, set()) == set()
        assert client.unsubscription_uncertain is False
    finally:
        _close_all(client, runtime, registry)


def test_failed_local_close_retains_subscriber_for_explicit_retry(tmp_path):
    client, runtime, registry, _market, _transport, subscribers = _make_client(tmp_path)
    client.start()
    subscriber = subscribers.created[0]
    subscriber.fail_close_once = True
    with pytest.raises(ServiceMarketError) as error:
        client.close()
    assert error.value.code == "LOCAL_CLOSE_FAILED"
    assert client._state == "close_failed"
    assert client._subscriber is subscriber
    client.close()
    assert client._state == "closed"
    assert client._subscriber is None
    runtime.close()
    registry.close()


def _test_source_descriptor(epoch: str = "market-epoch-one") -> MarketSourceDescriptor:
    return MarketSourceDescriptor(
        key=_KEY,
        stream_id=_KEY.stream_id,
        epoch=epoch,
        provider=_KEY.instrument.provider,
        source="synthetic.native.market",
        connection_generation=7,
        clock_domain_id="synthetic-clock-domain",
        clock_source_session_id="synthetic-clock-session",
        clock_source="synthetic.monotonic",
    )


def test_source_descriptor_requires_explicit_opt_in_and_preserves_legacy_request_shape(
    tmp_path,
):
    client, runtime, registry, market, _transport, _subscribers = _make_client(tmp_path)
    try:
        bootstrap = client.start()
        assert market.subscribe_arguments == [{"key": _KEY.to_dict()}]
        assert bootstrap.source_descriptor is None
        assert client.source_descriptor is None
    finally:
        _close_all(client, runtime, registry)


def test_required_source_descriptor_is_typed_and_bound_to_subscribe_epoch(tmp_path):
    client, runtime, registry, market, _transport, _subscribers = _make_client(
        tmp_path, require_source_descriptor=True
    )
    descriptor = _test_source_descriptor()
    market.source_descriptor = descriptor
    try:
        bootstrap = client.start()
        assert market.subscribe_arguments == [
            {"key": _KEY.to_dict(), "require_source_descriptor": True}
        ]
        assert type(bootstrap.source_descriptor) is MarketSourceDescriptor
        assert bootstrap.source_descriptor == descriptor
        assert client.source_descriptor == descriptor
    finally:
        _close_all(client, runtime, registry)


def test_required_source_descriptor_missing_from_legacy_reply_fails_closed(tmp_path):
    client, runtime, registry, market, _transport, _subscribers = _make_client(
        tmp_path, require_source_descriptor=True
    )
    try:
        with pytest.raises(ServiceMarketError) as error:
            client.start()
        assert error.value.code == "SOURCE_DESCRIPTOR_UNAVAILABLE"
        assert client.source_descriptor is None
        assert market.registrations.get(_KEY.stream_id, set()) == set()
    finally:
        _close_all(client, runtime, registry)


def test_unrequested_source_descriptor_is_rejected_to_preserve_legacy_reply_shape(tmp_path):
    client, runtime, registry, _market, _transport, _subscribers = _make_client(tmp_path)
    descriptor = _test_source_descriptor()
    reply = {
        "stream_id": _KEY.stream_id,
        "epoch": descriptor.epoch,
        "head": 0,
        "oldest_sequence": 1,
        "already_subscribed": False,
        "source_descriptor": descriptor.to_dict(),
    }
    try:
        with pytest.raises(ServiceMarketError) as error:
            client._validate_subscription_reply(reply)
        assert error.value.code == "INVALID_SUBSCRIBE_REPLY"
    finally:
        _close_all(client, runtime, registry)


def test_source_descriptor_is_cleared_before_failed_recovery_resubscribe(tmp_path):
    client, runtime, registry, market, _transport, _subscribers = _make_client(
        tmp_path, require_source_descriptor=True
    )
    market.source_descriptor = _test_source_descriptor()
    client.start()
    assert client.source_descriptor == market.source_descriptor
    market.source_descriptor = None
    try:
        with pytest.raises(ServiceMarketError) as error:
            client.recover()
        assert error.value.code == "SOURCE_DESCRIPTOR_UNAVAILABLE"
        assert client.source_descriptor is None
    finally:
        _close_all(client, runtime, registry)
