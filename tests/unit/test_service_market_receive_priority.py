"""A due head check should not block an already-ready market SUB frame."""

from dataclasses import replace

import pytest

from bt_api_py.service_market import ServiceMarketError
from bt_api_py.service_runtime import ServiceRuntimeError
from tests.unit.test_service_market import (
    _KEY,
    _OTHER_KEY,
    _close_all,
    _make_client,
)


def _started_client(tmp_path, *, queue_capacity=16):
    values = _make_client(tmp_path, queue_capacity=queue_capacity)
    values[0].start()
    return values


def _make_head_due(client) -> None:
    client._next_head_check = client._now()


def test_due_head_check_yields_to_ready_contiguous_sub_event(tmp_path):
    client, runtime, registry, market, transport, subscribers = _started_client(tmp_path)
    try:
        subscriber = subscribers.created[0]
        event = market.publish(_KEY, "ready")
        subscriber.events.append(event)
        _make_head_due(client)
        heads_before = transport.operations.count("market.head")
        reads_before = transport.operations.count("market.read_after")

        received = client.poll(0)

        assert received is event
        assert transport.operations.count("market.head") == heads_before
        assert transport.operations.count("market.read_after") == reads_before
        assert client.queue_count == 0
        assert client._next_head_check == client._now()
    finally:
        _close_all(client, runtime, registry)


def test_due_idle_head_recovers_a_missing_tail_after_sub_progress(tmp_path):
    client, runtime, registry, market, transport, subscribers = _started_client(tmp_path)
    try:
        subscriber = subscribers.created[0]
        first = market.publish(_KEY, "first")
        subscriber.events.append(first)
        _make_head_due(client)
        heads_before = transport.operations.count("market.head")
        assert client.poll(0) is first
        assert transport.operations.count("market.head") == heads_before

        missing_tail = market.publish(_KEY, "tail-without-sub-frame")
        heads_before = transport.operations.count("market.head")
        reads_before = transport.operations.count("market.read_after")

        recovered = client.poll(0)

        assert recovered is not None
        assert recovered.header.sequence == missing_tail.header.sequence
        assert recovered.payload["data"]["marker"] == "tail-without-sub-frame"
        assert transport.operations.count("market.head") == heads_before + 1
        assert transport.operations.count("market.read_after") == reads_before + 1
    finally:
        _close_all(client, runtime, registry)


@pytest.mark.parametrize("frame_kind", ["empty", "other_key", "duplicate"])
def test_due_head_still_runs_when_receive_does_not_queue_a_frame(tmp_path, frame_kind):
    client, runtime, registry, market, transport, subscribers = _started_client(tmp_path)
    try:
        subscriber = subscribers.created[0]
        if frame_kind == "other_key":
            subscriber.events.append(market.publish(_OTHER_KEY, "ignored"))
        elif frame_kind == "duplicate":
            event = market.publish(_KEY, "first")
            subscriber.events.append(event)
            _make_head_due(client)
            assert client.poll(0) is event
            subscriber.events.append(event)

        _make_head_due(client)
        heads_before = transport.operations.count("market.head")

        assert client.poll(0) is None

        assert transport.operations.count("market.head") == heads_before + 1
        assert client.queue_count == 0
        assert client._pending_poll_replay is None
    finally:
        _close_all(client, runtime, registry)


def test_due_gap_replay_remains_single_page_per_poll_and_ordered(tmp_path):
    client, runtime, registry, market, transport, subscribers = _started_client(tmp_path)
    try:
        subscriber = subscribers.created[0]
        events = [market.publish(_KEY, f"event-{index}") for index in range(1, 11)]
        subscriber.events.append(events[-1])
        _make_head_due(client)

        delivered = []
        for expected_sequence in range(1, 11):
            reads_before = transport.operations.count("market.read_after")
            event = client.poll(0)
            assert event is not None
            assert event.header.sequence == expected_sequence
            page_reads = transport.operations.count("market.read_after") - reads_before
            assert page_reads <= 1
            if expected_sequence in {1, 5, 9}:
                assert page_reads == 1
            else:
                assert page_reads == 0
            assert client.recovery_required is False
            assert client.resync_required is False
            delivered.append(event)
            if expected_sequence < 9:
                assert client._pending_poll_replay is not None
            else:
                assert client._pending_poll_replay is None

        assert [event.header.sequence for event in delivered] == list(range(1, 11))
        assert client._pending_poll_replay is None
        assert client._pending_poll_gap_event is None
        assert client.queue_count == 0
    finally:
        _close_all(client, runtime, registry)


def test_due_gap_head_failure_is_not_retried_or_returned_same_poll(tmp_path):
    client, runtime, registry, market, transport, subscribers = _started_client(tmp_path)
    try:
        subscriber = subscribers.created[0]
        market.publish(_KEY, "sequence-1")
        gap_event = market.publish(_KEY, "sequence-2")
        subscriber.events.append(gap_event)
        _make_head_due(client)
        heads_before = transport.operations.count("market.head")
        reads_before = transport.operations.count("market.read_after")
        transport.fail_after_dispatch.add("market.head")

        with pytest.raises(ServiceMarketError) as error:
            client.poll(0)

        assert error.value.code == "RECOVERY_REQUIRED"
        assert client.recovery_required is True
        assert client.queue_count == 0
        assert client._pending_poll_replay is None
        assert subscriber.events == []
        assert transport.operations.count("market.head") == heads_before + 1
        assert transport.operations.count("market.read_after") == reads_before
        assert "market.head" not in transport.fail_after_dispatch
    finally:
        _close_all(client, runtime, registry)


def test_expired_lease_maintenance_rejects_ready_sub_frame_before_receive(
    tmp_path, monkeypatch
):
    client, runtime, registry, market, transport, subscribers = _started_client(tmp_path)
    try:
        subscriber = subscribers.created[0]
        ready = market.publish(_KEY, "ready-but-expired")
        subscriber.events.append(ready)
        _make_head_due(client)
        heads_before = transport.operations.count("market.head")
        original_maintain = runtime.maintain

        def fail_expired_lease():
            raise ServiceRuntimeError("REQUIRED_LEASE_EXPIRED")

        monkeypatch.setattr(runtime, "maintain", fail_expired_lease)
        with pytest.raises(ServiceMarketError) as error:
            client.poll(0)

        assert error.value.code == "RUNTIME_MAINTENANCE_FAILED"
        assert error.value.cause_code == "REQUIRED_LEASE_EXPIRED"
        assert subscriber.events == [ready]
        assert transport.operations.count("market.head") == heads_before
        monkeypatch.setattr(runtime, "maintain", original_maintain)
    finally:
        _close_all(client, runtime, registry)


def test_changed_binding_rejects_ready_sub_frame_before_receive(tmp_path):
    client, runtime, registry, market, transport, subscribers = _started_client(tmp_path)
    original_binding = runtime._bindings[0]
    try:
        subscriber = subscribers.created[0]
        ready = market.publish(_KEY, "ready-but-binding-changed")
        subscriber.events.append(ready)
        _make_head_due(client)
        heads_before = transport.operations.count("market.head")
        runtime._bindings[0] = replace(
            original_binding, service_epoch=original_binding.service_epoch + "-changed"
        )

        with pytest.raises(ServiceMarketError) as error:
            client.poll(0)

        assert error.value.code == "RUNTIME_BINDING_CHANGED"
        assert subscriber.events == [ready]
        assert transport.operations.count("market.head") == heads_before
    finally:
        runtime._bindings[0] = original_binding
        _close_all(client, runtime, registry)
