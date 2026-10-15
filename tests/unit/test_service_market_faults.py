"""Real loopback PUB/SUB fault injection for market cursor recovery."""

from __future__ import annotations

import time
from collections import deque
from datetime import UTC, datetime
from itertools import count
from typing import Any

import pytest
import zmq
from bt_api_base.market_protocol import MarketSubscriptionKey
from bt_api_gateway.market_hub import GatewayMarketHub
from bt_api_transport_zmq.market_v2 import ZmqMarketPublisher, ZmqMarketSubscriber
from bt_api_transport_zmq.server import CurveClientCredentials, CurveServerCredentials

from bt_api_py.service_market import ServiceMarketError
from tests.unit.test_service_market import (
    _CLIENT,
    _KEY,
    _PEER,
    _PUBLIC_SCOPE,
    _close_all,
    _make_client,
    _plain_envelope,
)


class _FaultInjectingSubscriber:
    """Apply finite receive faults after the real loopback socket decodes frames."""

    def __init__(self, subscriber: ZmqMarketSubscriber) -> None:
        self._subscriber = subscriber
        self.drop_once: set[int] = set()
        self.duplicate_once: set[int] = set()
        self.reorder_pair: tuple[int, int] | None = None
        self._reorder_done = False
        self._pending: deque[Any] = deque()
        self.wire_sequences: list[int] = []
        self.offered_sequences: list[int] = []
        self.dropped_sequences: list[int] = []
        self.duplicated_sequences: list[int] = []
        self.reordered_pairs: list[tuple[int, int]] = []

    def receive(self, *, timeout: float | None = None):
        if self._pending:
            return self._offer(self._pending.popleft())

        event = self._subscriber.receive(timeout=timeout)
        if event is None:
            return None
        sequence = event.header.sequence
        self.wire_sequences.append(sequence)
        if sequence in self.drop_once:
            self.drop_once.remove(sequence)
            self.dropped_sequences.append(sequence)
            return None

        if (
            self.reorder_pair is not None
            and not self._reorder_done
            and sequence == self.reorder_pair[0]
        ):
            first, second = event, None
            deadline = time.monotonic() + 0.5
            while second is None and time.monotonic() < deadline:
                remaining = max(0.0, deadline - time.monotonic())
                candidate = self._subscriber.receive(timeout=min(0.05, remaining))
                if candidate is None:
                    continue
                self.wire_sequences.append(candidate.header.sequence)
                if candidate.header.sequence == self.reorder_pair[1]:
                    second = candidate
                else:
                    self._pending.append(candidate)
            if second is not None:
                self._reorder_done = True
                self.reordered_pairs.append((first.header.sequence, second.header.sequence))
                self._pending.append(first)
                return self._offer(second)

        return self._offer(event)

    def _offer(self, event):
        sequence = event.header.sequence
        self.offered_sequences.append(sequence)
        if sequence in self.duplicate_once:
            self.duplicate_once.remove(sequence)
            self.duplicated_sequences.append(sequence)
            self._pending.append(event)
        return event

    def disconnect(self) -> None:
        self._subscriber.close()

    def close(self) -> None:
        self._subscriber.close()

    def clear_observations(self) -> None:
        self.wire_sequences.clear()
        self.offered_sequences.clear()
        self.dropped_sequences.clear()
        self.duplicated_sequences.clear()
        self.reordered_pairs.clear()


class _SyntheticProvider:
    def __init__(self) -> None:
        self.hub: GatewayMarketHub | None = None
        self.generations: dict[str, str] = {}
        self.starts = 0
        self.stops = 0

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        self.generations[key.stream_id] = generation
        self.starts += 1
        return ("synthetic-handle", key.stream_id, generation)

    def unsubscribe(self, handle: object) -> bool:
        if type(handle) is not tuple or len(handle) != 3:
            return False
        self.generations.pop(handle[1], None)
        self.stops += 1
        return True

    def publish(self, key: MarketSubscriptionKey, marker: str):
        if self.hub is None:
            raise AssertionError("synthetic provider is not connected to the market hub")
        generation = self.generations[key.stream_id]
        observed_at = datetime.now(UTC).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
        return self.hub.publish(
            key,
            {"marker": marker},
            observed_at,
            generation=generation,
        )


class _LoopbackFixture:
    def __init__(self, tmp_path) -> None:
        server_public, server_secret = zmq.curve_keypair()
        client_public, client_secret = zmq.curve_keypair()
        self.publisher = ZmqMarketPublisher(
            "tcp://127.0.0.1:*",
            _PEER,
            _PUBLIC_SCOPE,
            {client_public: "synthetic-market-client"},
            credentials=CurveServerCredentials(server_public, server_secret),
            high_water_mark=128,
            max_message_bytes=262_144,
        )
        try:
            self.client, self.runtime, self.registry, local_market, self.transport, _ = (
                _make_client(tmp_path)
            )
        except BaseException:
            self.publisher.close()
            raise

        self._client_credentials = CurveClientCredentials(
            server_public, client_public, client_secret
        )
        self.provider = _SyntheticProvider()
        self.send_acceptance: list[tuple[int, bool]] = []
        epoch_ids = count(1)
        self.hub = GatewayMarketHub(
            self.provider,
            key_universe=frozenset({_KEY}),
            principal_allowed_keys={_CLIENT.principal_ref: frozenset({_KEY})},
            service_identity=_PEER,
            service_scope=_PUBLIC_SCOPE,
            max_replay_events=64,
            max_replay_bytes=8 * 1024 * 1024,
            publisher=self._publish_wire,
            epoch_factory=lambda: f"loopback-epoch-{next(epoch_ids)}",
        )
        self.provider.hub = self.hub
        self._install_gateway_control_handler(local_market)
        self.subscribers: list[_FaultInjectingSubscriber] = []
        self._install_loopback_subscriber()
        self.ready_head: dict[str, Any] | None = None

    def _publish_wire(self, event) -> bool:
        accepted = self.publisher.try_publish(event)
        self.send_acceptance.append((event.header.sequence, accepted))
        return accepted

    def _install_loopback_subscriber(self) -> None:
        def create(_binding, scope):
            subscriber = ZmqMarketSubscriber(
                self.publisher.endpoint,
                _PEER,
                scope,
                credentials=self._client_credentials,
                high_water_mark=128,
                max_message_bytes=262_144,
            )
            wrapped = _FaultInjectingSubscriber(subscriber)
            self.subscribers.append(wrapped)
            return wrapped

        # Keep the public SDK lifecycle and poll path while replacing only the
        # endpoint selected by this local synthetic test fixture.
        self.client._subscriber_factory = create

    def _install_gateway_control_handler(self, local_market) -> None:
        def handle(context, arguments, operation):
            key = MarketSubscriptionKey.from_dict(arguments["key"])
            registration_id = context.registration_id
            principal_ref = _CLIENT.principal_ref
            if operation == "market.subscribe":
                return dict(self.hub.subscribe(principal_ref, key, registration_id))
            if operation == "market.unsubscribe":
                return dict(self.hub.unsubscribe(principal_ref, key, registration_id))
            if not self.hub.registration_active(key, registration_id):
                raise AssertionError("market read arrived without an active registration")
            if operation == "market.head":
                return dict(self.hub.head(key))
            if operation == "market.snapshot":
                result = dict(self.hub.snapshot(key))
                event = result["event"]
                result["event"] = None if event is None else _plain_envelope(event)
                return result
            if operation == "market.read_after":
                result = dict(
                    self.hub.read_after(
                        key,
                        epoch=arguments["epoch"],
                        after=arguments["after"],
                        limit=arguments["limit"],
                    )
                )
                result["events"] = [
                    _plain_envelope(event) for event in result["events"]
                ]
                return result
            raise AssertionError(f"unexpected market operation: {operation}")

        # The existing runtime/registry fixture supplies the control calls;
        # every market state, cursor and replay answer now comes from the real
        # GatewayMarketHub implementation exercised over a real PUB/SUB link.
        local_market.handle = handle

    def publish(self, marker: str):
        return self.provider.publish(_KEY, marker)

    def reset_epoch(self, marker: str):
        self.hub.reset_stream_epoch(_KEY)
        return self.publish(marker)

    def head(self) -> dict[str, Any]:
        return self.runtime.call(
            "store.market", "market.head", {"key": _KEY.to_dict()}
        )

    def poll_until(self, predicate, *, timeout: float = 3.0):
        delivered = []
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            event = self.client.poll(50)
            if event is not None:
                delivered.append(event)
        return delivered

    def close(self) -> None:
        try:
            _close_all(self.client, self.runtime, self.registry)
        finally:
            try:
                self.hub.close()
            finally:
                self.publisher.close()


def _new_fixture(tmp_path) -> _LoopbackFixture:
    return _LoopbackFixture(tmp_path)


def _start_ready_client(fixture: _LoopbackFixture) -> None:
    bootstrap = fixture.client.start()
    assert bootstrap.snapshot_sequence == 0
    assert bootstrap.snapshot_complete is False
    assert bootstrap.full_baseline_available is False
    initial_head = fixture.head()
    assert initial_head["head"] == 0
    assert initial_head["epoch"] == bootstrap.cursor_epoch

    # PUB has no subscription handshake. Keep sending a bounded synthetic
    # readiness probe until a frame has crossed the actual loopback socket;
    # only then use the authoritative head/replay path to set the SDK cursor.
    subscriber = fixture.subscribers[0]
    wire_event = None
    for attempt in range(8):
        fixture.publish(f"subscription-ready-probe-{attempt}")
        wire_event = subscriber.receive(timeout=0.1)
        if wire_event is not None:
            break
    assert wire_event is not None
    head = fixture.head()
    assert wire_event.header.stream_epoch == head["epoch"]
    assert 1 <= wire_event.header.sequence <= head["head"] <= 8

    delivered = []
    while fixture.client._cursor_sequence < head["head"]:
        event = fixture.client.poll(0)
        assert event is not None
        delivered.append(event)
    assert [event.header.sequence for event in delivered] == list(
        range(1, head["head"] + 1)
    )
    assert all(event.header.stream_epoch == head["epoch"] for event in delivered)
    assert fixture.client.healthy is True
    subscriber.clear_observations()
    fixture.ready_head = head


def test_first_loopback_subscription_delivers_against_observed_head(tmp_path):
    fixture = _new_fixture(tmp_path)
    try:
        _start_ready_client(fixture)
        head = fixture.head()
        assert head == fixture.ready_head
        assert head["head"] >= 1
        assert len(fixture.send_acceptance) == head["head"]
        assert all(accepted for _sequence, accepted in fixture.send_acceptance)
        assert fixture.client._cursor_sequence == head["head"]
        assert fixture.provider.starts == 1
    finally:
        fixture.close()


def test_slow_loopback_consumer_recovers_injected_gap_and_lost_tail(tmp_path):
    fixture = _new_fixture(tmp_path)
    try:
        _start_ready_client(fixture)
        base = fixture.ready_head["head"]

        subscriber = fixture.subscribers[0]
        missing_sequence = base + 2
        subscriber.drop_once.add(missing_sequence)
        # Hold the SDK poller while a slow consumer accumulates a burst.
        burst = list(range(base + 1, base + 4))
        sent = [fixture.publish(f"slow-burst-{sequence}") for sequence in burst]
        head = fixture.head()
        assert head["head"] == burst[-1]
        assert [event.header.sequence for event in sent] == burst
        assert dict(fixture.send_acceptance)[burst[-1]] is True

        delivered = fixture.poll_until(
            lambda: missing_sequence in subscriber.dropped_sequences
        )
        assert subscriber.dropped_sequences == [missing_sequence]
        delivered.extend(
            fixture.poll_until(lambda: fixture.client._cursor_sequence == head["head"])
        )
        assert [event.header.sequence for event in delivered] == burst
        assert [event.payload["data"]["marker"] for event in delivered] == [
            f"slow-burst-{sequence}" for sequence in burst
        ]
        assert fixture.client._cursor_sequence == head["head"] == burst[-1]
        assert fixture.client.healthy is True

        # Drop the only remaining tail frame. No later PUB event can reveal the
        # loss, so advance the deterministic clock to force the SDK's head check.
        tail_sequence = burst[-1] + 1
        subscriber.drop_once.add(tail_sequence)
        tail = fixture.publish("lost-final-tail")
        tail_head = fixture.head()
        assert tail_head["head"] == tail.header.sequence == tail_sequence
        assert fixture.client._clock.value < fixture.client._next_head_check
        tail_delivery = []
        deadline = time.monotonic() + 3.0
        while tail_sequence not in subscriber.dropped_sequences and time.monotonic() < deadline:
            event = fixture.client.poll(50)
            if event is not None:
                tail_delivery.append(event)
        assert tail_sequence in subscriber.dropped_sequences
        assert tail_delivery == []

        fixture.client._clock.value = fixture.client._next_head_check
        replayed_tail = fixture.client.poll(0)
        assert replayed_tail is not None
        assert replayed_tail.header.sequence == tail_head["head"] == tail_sequence
        assert replayed_tail.header.stream_epoch == tail_head["epoch"]
        assert replayed_tail.payload["data"]["marker"] == "lost-final-tail"
        assert fixture.client._cursor_sequence == tail_head["head"]
    finally:
        fixture.close()


def test_loopback_out_of_order_and_duplicate_frames_keep_one_cursor_sequence(tmp_path):
    fixture = _new_fixture(tmp_path)
    try:
        _start_ready_client(fixture)
        base = fixture.ready_head["head"]

        subscriber = fixture.subscribers[0]
        burst = list(range(base + 1, base + 4))
        subscriber.reorder_pair = (burst[0], burst[1])
        subscriber.duplicate_once.add(burst[1])
        sent = [fixture.publish(f"reordered-burst-{sequence}") for sequence in burst]
        head = fixture.head()
        assert head["head"] == burst[-1]
        assert [event.header.sequence for event in sent] == burst

        delivered = fixture.poll_until(
            lambda: (
                subscriber.reordered_pairs == [(burst[0], burst[1])]
                and subscriber.wire_sequences.count(burst[-1]) == 1
                and not subscriber._pending
                and fixture.client.queue_count == 0
                and fixture.client._cursor_sequence == head["head"]
            )
        )
        # The server-head replay repairs the first out-of-order frame. Later
        # buffered/duplicate PUB frames must not advance or duplicate the cursor.
        assert [event.header.sequence for event in delivered] == burst
        assert subscriber.reordered_pairs == [(burst[0], burst[1])]
        assert subscriber.duplicated_sequences == [burst[1]]
        assert subscriber.offered_sequences[:1] == [burst[1]]
        assert subscriber.offered_sequences.count(burst[1]) == 2
        assert subscriber.wire_sequences == burst
        assert fixture.client._cursor_sequence == head["head"] == burst[-1]
        assert fixture.client.queue_count == 0
        assert fixture.client.healthy is True
    finally:
        fixture.close()


def test_loopback_disconnect_replays_tail_and_epoch_change_requires_resync(tmp_path):
    fixture = _new_fixture(tmp_path)
    try:
        _start_ready_client(fixture)
        old_head = fixture.ready_head
        first_event = fixture.publish("first-after-subscription")
        first_head = fixture.head()
        assert first_head["head"] == first_event.header.sequence == old_head["head"] + 1
        first = fixture.poll_until(
            lambda: fixture.client._cursor_sequence == first_head["head"]
        )
        assert [event.header.sequence for event in first] == [first_head["head"]]

        original_subscriber = fixture.subscribers[0]
        original_subscriber.disconnect()
        missed = fixture.publish("during-real-subscriber-disconnect")
        missed_head = fixture.head()
        assert missed_head["head"] == missed.header.sequence == first_head["head"] + 1

        recovered = fixture.client.recover()
        assert recovered.resync_required is False
        assert recovered.cursor_epoch == first_head["epoch"]
        assert recovered.cursor_sequence == first_head["head"]
        assert len(fixture.subscribers) == 2
        replayed = fixture.client.poll(0)
        assert replayed is not None
        assert replayed.header.stream_epoch == missed_head["epoch"]
        assert replayed.header.sequence == missed_head["head"]
        assert replayed.payload["data"]["marker"] == "during-real-subscriber-disconnect"
        assert fixture.client._cursor_sequence == missed_head["head"]

        changed_epoch_event = fixture.reset_epoch("new-epoch-event")
        new_head = fixture.head()
        assert changed_epoch_event.header.stream_epoch == new_head["epoch"]
        assert new_head["epoch"] != missed_head["epoch"]
        assert new_head["head"] == changed_epoch_event.header.sequence == 1
        fixture.client._clock.value = fixture.client._next_head_check
        with pytest.raises(ServiceMarketError) as error:
            fixture.client.poll(0)
        assert getattr(error.value, "code", None) == "RESYNC_REQUIRED"

        pending = fixture.client.last_bootstrap
        assert pending is not None and pending.resync_required is True
        assert pending.snapshot_epoch == new_head["epoch"]
        assert pending.snapshot_sequence == new_head["head"]
        assert pending.cursor_epoch == missed_head["epoch"]
        assert pending.cursor_sequence == missed_head["head"]
        accepted = fixture.client.accept_resync(acknowledge_data_loss=True)
        assert accepted.resync_required is False
        assert accepted.snapshot_complete is False
        assert accepted.full_baseline_available is False
        assert accepted.cursor_epoch == new_head["epoch"]
        assert accepted.cursor_sequence == new_head["head"]
        assert fixture.client.healthy is True
        assert fixture.provider.starts == 1
        assert fixture.provider.stops == 0
    finally:
        fixture.close()
