"""Iteration 60 WP6 (G2 follow-up): the SDK market client reacts to revocation.

A ``MARKET_KEY_DENIED`` read (definitive ``SCOPE_DENIED`` on the wire) or an
``authorization_floor`` replay gap interrupts the subscription explicitly:
buffered events are discarded, the receiver is told to drop frames of the old
grant, ``poll``/``drain``/``recover`` refuse, and ``healthy`` is False so a
projection or BT feed cannot keep serving the stream as live. After the
re-grant, ``resync_after_regrant`` re-subscribes and resumes from the hub's
``latest_event`` cut (never below the grant floor), arming the receiver cut.
UNIT/FAKE: in-process registry, strict local control transport, fake hub.
"""

from __future__ import annotations

import pytest

from bt_api_py.service_market import ServiceMarketError
from bt_api_py.service_registry import ServiceOperationDeniedError
from tests.unit import test_service_market as unit

_READS = {"market.head", "market.snapshot", "market.read_after", "market.read_after_bounded"}


class _RevocableSubscriber(unit.FakeSubscriber):
    """Fake receiver with the transport's revoke/regrant contract."""

    def __init__(self) -> None:
        super().__init__()
        self.revoked: str | None = None
        self.regrants: list[tuple[int, str, int]] = []
        self.dropped_revoked = 0
        self.cut: tuple[str, int] | None = None

    def receive(self, *, timeout=None):
        event = super().receive(timeout=timeout)
        if event is None:
            return None
        if self.revoked is not None:
            self.dropped_revoked += 1
            return None
        if (
            self.cut is not None
            and event.header.stream_epoch == self.cut[0]
            and event.header.sequence <= self.cut[1]
        ):
            return None
        return event

    def revoke(self, reason: str) -> None:
        self.revoked = reason

    def regrant(self, *, generation: int, epoch: str, after_sequence: int) -> None:
        self.regrants.append((generation, epoch, after_sequence))
        self.cut = (epoch, after_sequence)
        self.revoked = None


class _Factory(unit.FakeSubscriberFactory):
    def __call__(self, _binding, _scope):
        subscriber = _RevocableSubscriber()
        self.created.append(subscriber)
        return subscriber


def _client(tmp_path):
    client, runtime, registry, market, transport, _ = unit._make_client(tmp_path)
    factory = _Factory()
    client._subscriber_factory = factory
    state = {"denied": False, "floor": None}
    original = market.handle

    def handle(context, arguments, operation):
        if state["denied"] and (operation in _READS or operation == "market.subscribe"):
            # The Gateway service wiring maps MARKET_KEY_DENIED to this exact
            # definitive denial; the registry answers SCOPE_DENIED.
            raise ServiceOperationDeniedError()
        reply = original(context, arguments, operation)
        floor = state["floor"]
        if floor is not None and operation == "market.snapshot":
            event = reply["event"]
            if event is not None and reply["epoch"] == floor[0] and reply["head"] <= floor[1]:
                reply = dict(reply, event=None)
        if (
            floor is not None
            and operation in {"market.read_after", "market.read_after_bounded"}
            and not reply["gap"]
            and arguments["epoch"] == floor[0]
            and arguments["after"] < floor[1]
        ):
            reply = dict(
                reply,
                gap=True,
                gap_reason="authorization_floor",
                events=[],
                next_after=arguments["after"],
                has_more=False,
            )
        return reply

    market.handle = handle
    return client, runtime, registry, market, transport, factory, state


def _tick(client):
    client._clock.value += 11.0


def test_denied_read_interrupts_explicitly_and_regrant_resyncs_from_the_cut(tmp_path):
    client, runtime, registry, market, _transport, factory, state = _client(tmp_path)
    try:
        client.start()
        first = market.publish(unit._KEY, "authorized-1")
        subscriber = factory.created[0]
        subscriber.events.append(first)
        assert client.poll(0).header.sequence == 1
        assert client.healthy is True

        state["denied"] = True
        late = market.publish(unit._KEY, "published-after-revocation")
        _tick(client)
        with pytest.raises(ServiceMarketError) as error:
            client.poll(0)
        assert error.value.code == "MARKET_AUTHORIZATION_INTERRUPTED"
        assert error.value.cause_code == "MARKET_KEY_DENIED"
        assert client.authorization_interrupted == "MARKET_KEY_DENIED"
        assert client.healthy is False and client.recovery_required is True
        assert client.queue_count == 0
        assert subscriber.revoked == "MARKET_KEY_DENIED"
        assert client.source_descriptor is None
        # A frame still queued in the SUB socket from the old grant is dropped.
        subscriber.events.append(late)
        assert subscriber.receive() is None and subscriber.dropped_revoked == 1
        for call in (lambda: client.poll(0), lambda: client.drain(4), client.recover):
            with pytest.raises(ServiceMarketError, match="MARKET_AUTHORIZATION_INTERRUPTED"):
                call()
        with pytest.raises(ServiceMarketError, match="MARKET_AUTHORIZATION_INTERRUPTED"):
            client.accept_resync(acknowledge_data_loss=True)

        # Still denied: the resync is refused and the interruption stays.
        with pytest.raises(ServiceMarketError) as denied:
            client.resync_after_regrant()
        assert denied.value.code == "MARKET_AUTHORIZATION_INTERRUPTED"
        assert client.authorization_interrupted == "MARKET_KEY_DENIED"

        # Re-grant at the stream head; the hub hides the latest event.
        head = market.events[unit._KEY.stream_id][-1].header
        state["denied"] = False
        state["floor"] = (head.stream_epoch, head.sequence)
        bootstrap = client.resync_after_regrant()
        assert bootstrap.resync_required is True
        assert bootstrap.snapshot is None  # published while not authorized
        assert (bootstrap.cursor_epoch, bootstrap.cursor_sequence) == (
            head.stream_epoch,
            head.sequence,
        )
        assert subscriber.regrants == [(1, head.stream_epoch, head.sequence)]
        assert client.authorization_generation == 1
        assert client.authorization_interrupted is None
        assert client.healthy is True

        # A stale frame of the old grant is dropped; new frames flow again.
        subscriber.events.append(late)
        fresh = market.publish(unit._KEY, "after-regrant")
        subscriber.events.append(fresh)
        delivered = [client.poll(0), client.poll(0)]
        assert [event.header.sequence for event in delivered if event is not None] == [3]
    finally:
        state["denied"] = False
        unit._close_all(client, runtime, registry)


def test_authorization_floor_gap_interrupts_and_never_replays_below_the_floor(tmp_path):
    client, runtime, registry, market, _transport, factory, state = _client(tmp_path)
    try:
        client.start()
        subscriber = factory.created[0]
        for marker in ("a", "b", "c", "d"):
            market.publish(unit._KEY, marker)
        epoch = market.epochs[unit._KEY.stream_id]
        # Revoked and re-granted between two head checks: floor at sequence 3.
        state["floor"] = (epoch, 3)
        _tick(client)
        with pytest.raises(ServiceMarketError) as error:
            client.poll(0)
        assert error.value.code == "MARKET_AUTHORIZATION_INTERRUPTED"
        assert error.value.cause_code == "AUTHORIZATION_FLOOR"
        assert client.queue_count == 0
        assert subscriber.revoked == "AUTHORIZATION_FLOOR"

        bootstrap = client.resync_after_regrant()
        assert bootstrap.resync_required is True
        assert bootstrap.snapshot is not None and bootstrap.snapshot.header.sequence == 4
        assert bootstrap.cursor_sequence == 4
        assert subscriber.regrants == [(1, epoch, 4)]
        nxt = market.publish(unit._KEY, "e")
        subscriber.events.append(nxt)
        assert client.poll(0).header.sequence == 5
    finally:
        unit._close_all(client, runtime, registry)


def test_resync_requires_an_interruption_and_empty_snapshots_stay_invalid(tmp_path):
    client, runtime, registry, market, _transport, _factory, state = _client(tmp_path)
    try:
        client.start()
        with pytest.raises(ServiceMarketError, match="NO_AUTHORIZATION_INTERRUPTION"):
            client.resync_after_regrant()
        market.publish(unit._KEY, "a")
        epoch = market.epochs[unit._KEY.stream_id]
        # Outside a re-grant resync an empty snapshot at a non-zero head is
        # still a protocol violation, not an authorization artifact.
        state["floor"] = (epoch, 1)
        with pytest.raises(ServiceMarketError, match="INVALID_EMPTY_SNAPSHOT"):
            client._get_snapshot()
    finally:
        state["floor"] = None
        unit._close_all(client, runtime, registry)
