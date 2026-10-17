"""Poll replay is page-bounded while bootstrap and recovery stay complete."""

from __future__ import annotations

import bt_api_py.service_market as service_market
import pytest
from bt_api_py.service_market import ServiceMarketClient, ServiceMarketError
from tests.unit.test_service_market import _KEY, _close_all, _make_client


def _read_after_calls(transport) -> int:
    return transport.operations.count("market.read_after")


def _start_and_settle_head(client) -> None:
    client.start()
    assert client.poll(0) is None


def test_poll_replay_reads_one_page_then_resumes_before_due_head(tmp_path, monkeypatch):
    monkeypatch.setattr(service_market, "_MAX_READ_AFTER_LIMIT", 2)
    client, runtime, registry, market, transport, _subscribers = _make_client(
        tmp_path, queue_capacity=8
    )
    try:
        _start_and_settle_head(client)
        events = [market.publish(_KEY, f"poll-{index}") for index in range(5)]
        client._clock.value = client._next_head_check

        reads_before = _read_after_calls(transport)
        first = client.poll(0)
        assert first is not None and first.header.sequence == events[0].header.sequence
        assert _read_after_calls(transport) == reads_before + 1
        assert client._pending_poll_replay == (client._queued_epoch, 5)
        assert client._cursor_sequence == 1
        assert client._queued_sequence == 2
        assert client.recovery_required is False

        second = client.poll(0)
        assert second is not None and second.header.sequence == events[1].header.sequence
        assert _read_after_calls(transport) == reads_before + 1
        assert client._cursor_sequence == 2
        assert client.queue_count == 0

        # A due head timer cannot delay the pending continuation or add a
        # second market.read_after call to this poll.
        client._clock.value = client._next_head_check
        heads_before = transport.operations.count("market.head")
        third = client.poll(0)
        assert third is not None and third.header.sequence == events[2].header.sequence
        assert _read_after_calls(transport) == reads_before + 2
        assert transport.operations.count("market.head") == heads_before
        assert client._cursor_sequence == 3
        assert client._queued_sequence == 4

        fourth = client.poll(0)
        assert fourth is not None and fourth.header.sequence == events[3].header.sequence
        assert _read_after_calls(transport) == reads_before + 2

        fifth = client.poll(0)
        assert fifth is not None and fifth.header.sequence == events[4].header.sequence
        assert _read_after_calls(transport) == reads_before + 3
        assert client._pending_poll_replay is None
        assert client._cursor_sequence == client._queued_sequence == 5
        assert client.recovery_required is False
    finally:
        _close_all(client, runtime, registry)


def test_gap_frame_is_retained_until_replay_covers_its_sequence(tmp_path, monkeypatch):
    monkeypatch.setattr(service_market, "_MAX_READ_AFTER_LIMIT", 2)
    client, runtime, registry, market, transport, subscribers = _make_client(
        tmp_path, queue_capacity=8
    )
    try:
        _start_and_settle_head(client)
        events = [market.publish(_KEY, f"gap-{index}") for index in range(5)]
        late_frame = events[-1]
        subscribers.created[0].events.append(late_frame)
        reads_before = _read_after_calls(transport)

        delivered = [client.poll(0)]
        assert delivered[0] is not None
        assert delivered[0].header.sequence == events[0].header.sequence
        assert _read_after_calls(transport) == reads_before + 1
        assert client._pending_poll_gap_event is late_frame
        assert client._pending_poll_replay == (client._queued_epoch, 5)
        assert client.recovery_required is False

        while client._pending_poll_replay is not None or client.queue_count:
            previous_reads = _read_after_calls(transport)
            next_event = client.poll(0)
            assert next_event is not None
            assert _read_after_calls(transport) - previous_reads <= 1
            delivered.append(next_event)

        assert [event.header.sequence for event in delivered] == [1, 2, 3, 4, 5]
        assert delivered[-1] is not late_frame
        assert client._pending_poll_gap_event is None
        assert client._cursor_sequence == client._queued_sequence == 5
        assert client.recovery_required is False
    finally:
        _close_all(client, runtime, registry)


def test_gap_frame_after_stale_head_is_enqueued_after_pending_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(service_market, "_MAX_READ_AFTER_LIMIT", 2)
    client, runtime, registry, market, transport, subscribers = _make_client(
        tmp_path, queue_capacity=8
    )
    try:
        _start_and_settle_head(client)
        events = [market.publish(_KEY, f"stale-head-{index}") for index in range(5)]
        late_frame = events[-1]
        subscribers.created[0].events.append(late_frame)
        original_handle = market.handle

        def head_horizon_four(context, arguments, operation):
            reply = original_handle(context, arguments, operation)
            if operation != "market.head":
                return reply
            assert reply["head"] == 5
            return {
                **reply,
                "head": 4,
                "latest_observed_at_utc": events[3].header.sent_at_utc,
            }

        market.handle = head_horizon_four
        reads_before = _read_after_calls(transport)
        heads_before = transport.operations.count("market.head")

        delivered = []
        previous_reads = _read_after_calls(transport)
        first = client.poll(0)
        assert first is not None and first.header.sequence == 1
        assert _read_after_calls(transport) - previous_reads == 1
        delivered.append(first)
        assert client._pending_poll_replay == (client._queued_epoch, 4)
        assert client._pending_poll_gap_event is late_frame
        assert client.recovery_required is False

        previous_reads = _read_after_calls(transport)
        second = client.poll(0)
        assert second is not None and second.header.sequence == 2
        assert _read_after_calls(transport) == previous_reads
        delivered.append(second)

        previous_reads = _read_after_calls(transport)
        third = client.poll(0)
        assert third is not None and third.header.sequence == 3
        assert _read_after_calls(transport) - previous_reads == 1
        delivered.append(third)
        assert client._pending_poll_replay is None
        assert client._pending_poll_gap_event is None
        assert client._queued_sequence == 5
        assert client.queue_count == 2
        assert client.recovery_required is False

        for expected in (4, 5):
            previous_reads = _read_after_calls(transport)
            event = client.poll(0)
            assert event is not None and event.header.sequence == expected
            assert _read_after_calls(transport) == previous_reads
            delivered.append(event)

        assert [event.header.sequence for event in delivered] == [1, 2, 3, 4, 5]
        assert delivered[-1] is late_frame
        assert _read_after_calls(transport) == reads_before + 2
        assert transport.operations.count("market.head") == heads_before + 1
        assert client._cursor_sequence == client._queued_sequence == 5
        assert client._pending_poll_replay is None
        assert client.recovery_required is False
    finally:
        _close_all(client, runtime, registry)


def test_page_budget_does_not_mask_real_queue_capacity_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(service_market, "_MAX_READ_AFTER_LIMIT", 2)
    client, runtime, registry, market, _transport, _subscribers = _make_client(
        tmp_path, queue_capacity=1
    )
    try:
        _start_and_settle_head(client)
        events = [market.publish(_KEY, f"capacity-{index}") for index in range(2)]
        client._clock.value = client._next_head_check

        first = client.poll(0)
        assert first is not None and first.header.sequence == events[0].header.sequence
        assert client.recovery_required is True
        assert client._pending_poll_replay is None
        with pytest.raises(ServiceMarketError) as error:
            client.poll(0)
        assert error.value.code == "RECOVERY_REQUIRED"
    finally:
        _close_all(client, runtime, registry)


def test_start_bootstrap_replay_is_not_limited_by_poll_page_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(service_market, "_MAX_READ_AFTER_LIMIT", 2)
    client, runtime, registry, market, transport, _subscribers = _make_client(
        tmp_path, queue_capacity=8
    )
    original_handle = market.handle
    injected = False

    def publish_after_snapshot(context, arguments, operation):
        nonlocal injected
        if operation == "market.head" and not injected:
            injected = True
            for index in range(5):
                market.publish(_KEY, f"bootstrap-{index}")
        return original_handle(context, arguments, operation)

    market.handle = publish_after_snapshot
    try:
        bootstrap = client.start()
        assert bootstrap.resync_required is False
        assert _read_after_calls(transport) == 3
        assert client.queue_count == 5
        assert client._pending_poll_replay is None
        assert client.recovery_required is False
    finally:
        _close_all(client, runtime, registry)


def test_start_many_bootstrap_replay_is_not_limited_by_poll_page_budget(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(service_market, "_MAX_READ_AFTER_LIMIT", 2)
    client, runtime, registry, market, transport, _subscribers = _make_client(
        tmp_path, queue_capacity=8
    )
    original_handle = market.handle
    injected = False

    def publish_after_batch(context, arguments, operation):
        nonlocal injected
        result = original_handle(context, arguments, operation)
        if operation == "market.bootstrap_many" and not injected:
            injected = True
            for index in range(5):
                market.publish(_KEY, f"batch-{index}")
            events = market.events[_KEY.stream_id]
            result["items"][0]["head"] = {
                "stream_id": _KEY.stream_id,
                "epoch": market.epochs[_KEY.stream_id],
                "head": events[-1].header.sequence,
                "oldest_sequence": events[0].header.sequence,
                "latest_observed_at_utc": events[-1].header.sent_at_utc,
            }
        return result

    market.handle = publish_after_batch
    try:
        bootstraps = ServiceMarketClient.start_many((client,))
        assert len(bootstraps) == 1
        assert bootstraps[0].snapshot_sequence == 0
        assert _read_after_calls(transport) == 3
        assert client.queue_count == 5
        assert client._pending_poll_replay is None
        assert client.recovery_required is False
    finally:
        _close_all(client, runtime, registry)


def test_recover_clears_pending_poll_replay_and_replays_full_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(service_market, "_MAX_READ_AFTER_LIMIT", 2)
    client, runtime, registry, market, transport, _subscribers = _make_client(
        tmp_path, queue_capacity=8
    )
    try:
        _start_and_settle_head(client)
        events = [market.publish(_KEY, f"recover-{index}") for index in range(5)]
        client._clock.value = client._next_head_check
        first = client.poll(0)
        assert first is not None and first.header.sequence == events[0].header.sequence
        second = client.poll(0)
        assert second is not None and second.header.sequence == events[1].header.sequence
        assert client.queue_count == 0
        assert client._pending_poll_replay == (client._queued_epoch, 5)

        reads_before = _read_after_calls(transport)
        recovered = client.recover()
        assert recovered.resync_required is False
        assert _read_after_calls(transport) == reads_before + 2
        assert client._pending_poll_replay is None
        assert client._pending_poll_gap_event is None
        assert client.queue_count == 3
        assert client.recovery_required is False
        replayed = client.poll(0)
        assert replayed is not None and replayed.header.sequence == events[2].header.sequence
    finally:
        _close_all(client, runtime, registry)


def _epoch_reset_gap_reply(client):
    return {
        "stream_id": _KEY.stream_id,
        "epoch": f"{client._queued_epoch}-next",
        "head": 1,
        "oldest_sequence": 1,
        "after": 2,
        "next_after": 2,
        "limit": 2,
        "has_more": False,
        "gap": True,
        "gap_reason": "epoch_mismatch",
        "events": [],
    }


def test_read_after_accepts_epoch_reset_gap_beyond_new_head(tmp_path):
    client, runtime, registry, _market, _transport, _subscribers = _make_client(
        tmp_path
    )
    try:
        _start_and_settle_head(client)
        reply = _epoch_reset_gap_reply(client)

        client._validate_read_after_reply(reply, after=2, limit=2)
    finally:
        _close_all(client, runtime, registry)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("events", [{"not": "empty"}]),
        ("next_after", 3),
        ("has_more", True),
    ],
    ids=["nonempty-events", "advanced-cursor", "has-more"],
)
def test_read_after_rejects_malformed_epoch_reset_gap(
    tmp_path, field, invalid_value
):
    client, runtime, registry, _market, _transport, _subscribers = _make_client(
        tmp_path
    )
    try:
        _start_and_settle_head(client)
        reply = _epoch_reset_gap_reply(client)
        reply[field] = invalid_value

        with pytest.raises(ServiceMarketError) as error:
            client._validate_read_after_reply(reply, after=2, limit=2)
        assert error.value.code == "INVALID_READ_AFTER_REPLY"
    finally:
        _close_all(client, runtime, registry)


def test_epoch_change_clears_pending_poll_replay_and_remains_fail_closed(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(service_market, "_MAX_READ_AFTER_LIMIT", 2)
    client, runtime, registry, market, _transport, _subscribers = _make_client(
        tmp_path, queue_capacity=8
    )
    try:
        _start_and_settle_head(client)
        for index in range(4):
            market.publish(_KEY, f"old-epoch-{index}")
        client._clock.value = client._next_head_check
        first = client.poll(0)
        assert first is not None and first.header.sequence == 1
        second = client.poll(0)
        assert second is not None and second.header.sequence == 2
        assert client._pending_poll_replay is not None

        market.reset_epoch(_KEY)
        with pytest.raises(ServiceMarketError) as error:
            client.poll(0)
        assert error.value.code == "RESYNC_REQUIRED"
        assert client._pending_poll_replay is None
        assert client._pending_poll_gap_event is None
        assert client.resync_required is True

        original_handle = market.handle
        injected = False

        def publish_after_resync_snapshot(context, arguments, operation):
            nonlocal injected
            result = original_handle(context, arguments, operation)
            if operation == "market.snapshot" and not injected:
                injected = True
                for index in range(2, 6):
                    market.publish(_KEY, f"accepted-resync-{index}")
            return result

        market.handle = publish_after_resync_snapshot
        reads_before_accept = _read_after_calls(_transport)
        accepted = client.accept_resync(acknowledge_data_loss=True)
        assert accepted.resync_required is False
        assert _read_after_calls(_transport) == reads_before_accept + 2
        assert client.queue_count == 4
        assert client._pending_poll_replay is None
        assert client._pending_poll_gap_event is None
        assert client.recovery_required is False
    finally:
        _close_all(client, runtime, registry)


def test_close_clears_pending_poll_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(service_market, "_MAX_READ_AFTER_LIMIT", 2)
    client, runtime, registry, market, _transport, _subscribers = _make_client(
        tmp_path, queue_capacity=8
    )
    try:
        _start_and_settle_head(client)
        for index in range(4):
            market.publish(_KEY, f"close-{index}")
        client._clock.value = client._next_head_check
        first = client.poll(0)
        assert first is not None and first.header.sequence == 1
        assert client.poll(0) is not None
        assert client.queue_count == 0
        assert client._pending_poll_replay is not None

        client.close()
        assert client._state == "closed"
        assert client._pending_poll_replay is None
        assert client._pending_poll_gap_event is None
    finally:
        _close_all(client, runtime, registry)
