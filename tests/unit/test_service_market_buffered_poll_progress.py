"""Buffered market events should make progress before another head check."""

from dataclasses import replace

import pytest

from bt_api_py.service_market import ServiceMarketError
from bt_api_py.service_runtime import ServiceRuntimeError
from tests.unit.test_service_market import _KEY, _close_all, _make_client


def _prime_replay_queue(tmp_path, *, event_count: int = 3):
    client, runtime, registry, market, transport, subscribers = _make_client(tmp_path)
    client.start()
    assert client.poll(0) is None  # Establish the next periodic head deadline.

    events = [market.publish(_KEY, f"buffered-{index}") for index in range(event_count)]
    clock = runtime._monotonic_clock
    clock.value = client._next_head_check
    first = client.poll(0)  # Replay fills the SDK queue and returns its first event.
    assert first is not None and first.header.sequence == events[0].header.sequence
    assert client.queue_count == event_count - 1
    return client, runtime, registry, transport, clock, events


def test_poll_dequeues_buffered_replay_before_due_head_then_resumes_head_after_drain(
    tmp_path,
):
    client, runtime, registry, transport, clock, events = _prime_replay_queue(tmp_path)
    try:
        head_checks_before = transport.operations.count("market.head")
        clock.value = client._next_head_check

        second = client.poll(0)
        assert second is not None and second.header.sequence == events[1].header.sequence
        assert transport.operations.count("market.head") == head_checks_before

        third = client.poll(0)
        assert third is not None and third.header.sequence == events[2].header.sequence
        assert client.queue_count == 0
        assert transport.operations.count("market.head") == head_checks_before

        assert client.poll(0) is None
        assert transport.operations.count("market.head") == head_checks_before + 1
    finally:
        _close_all(client, runtime, registry)


def test_poll_keeps_runtime_lease_and_binding_checks_before_buffered_dequeue(tmp_path):
    client, runtime, registry, _transport, _clock, _events = _prime_replay_queue(
        tmp_path, event_count=2
    )
    try:
        queued_before = client.queue_count
        original_maintain = runtime.maintain

        def expired_lease():
            raise ServiceRuntimeError("REQUIRED_LEASE_EXPIRED")

        runtime.maintain = expired_lease
        with pytest.raises(ServiceMarketError) as error:
            client.poll(0)
        assert error.value.code == "RUNTIME_MAINTENANCE_FAILED"
        assert error.value.cause_code == "REQUIRED_LEASE_EXPIRED"
        assert client.queue_count == queued_before
        runtime.maintain = original_maintain

        original_binding_view = runtime.market_binding_view

        def changed_binding_view(mount, *, required_operations):
            binding = original_binding_view(
                mount, required_operations=required_operations
            )
            return replace(binding, service_epoch=binding.service_epoch + "-changed")

        runtime.market_binding_view = changed_binding_view
        with pytest.raises(ServiceMarketError) as error:
            client.poll(0)
        assert error.value.code == "RUNTIME_BINDING_CHANGED"
        assert client.queue_count == queued_before
        runtime.market_binding_view = original_binding_view
    finally:
        _close_all(client, runtime, registry)


def test_poll_returns_newly_ingested_event_before_head_due_at_receive_boundary(tmp_path):
    client, runtime, registry, _market, transport, subscribers = _make_client(tmp_path)
    try:
        client.start()
        assert client.poll(0) is None
        clock = runtime._monotonic_clock
        clock.value = client._next_head_check - 0.001
        event = _market.publish(_KEY, "received-at-head-deadline")
        subscriber = subscribers.created[0]
        subscriber.events.append(event)
        receive = subscriber.receive
        first_receive = True

        def receive_across_head_deadline(*, timeout=None):
            nonlocal first_receive
            received = receive(timeout=timeout)
            if first_receive:
                clock.value = client._next_head_check
                first_receive = False
            return received

        subscriber.receive = receive_across_head_deadline
        head_checks_before = transport.operations.count("market.head")

        received = client.poll(0)
        assert received == event
        assert transport.operations.count("market.head") == head_checks_before

        assert client.poll(0) is None
        assert transport.operations.count("market.head") == head_checks_before + 1
    finally:
        _close_all(client, runtime, registry)
