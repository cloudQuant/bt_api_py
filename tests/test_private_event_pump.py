"""PrivateEventPump tests (Task 3.2)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from bt_api_py.brokers.types import BrokerEvent
from bt_api_py.forwarding.memory import InMemoryForwardingBus
from bt_api_py.forwarding.private_event_pump import PrivateEventPump


class _FakeAdapter:
    def __init__(self, events: list[tuple[BrokerEvent, dict]]) -> None:
        self._events = events

    async def stream_events(self) -> AsyncIterator[tuple[BrokerEvent, dict]]:
        for event in self._events:
            yield event


def _drain(subscription) -> list:
    events = []
    while True:
        event = subscription.poll()
        if event is None:
            break
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_pump_converts_broker_events_to_private_events() -> None:
    bus = InMemoryForwardingBus()
    subscription = bus.subscribe_private("acct.paper.")
    adapter = _FakeAdapter(
        [
            (BrokerEvent.ORDER_UPDATED, {"order_id": "o1", "status": "filled"}),
            (BrokerEvent.POSITION_UPDATED, {"symbol": "BTCUSDT", "quantity": 0.5}),
            (BrokerEvent.ACCOUNT_UPDATED, {"cash": 100.0}),
        ]
    )
    pump = PrivateEventPump(adapter, bus, account_id="paper")

    await pump.run_once()

    events = _drain(subscription)
    assert len(events) == 3


@pytest.mark.asyncio
async def test_pump_maps_connection_lost_event() -> None:
    bus = InMemoryForwardingBus()
    subscription = bus.subscribe_private("acct.paper.")
    adapter = _FakeAdapter([(BrokerEvent.CONNECTION_LOST, {"reason": "timeout"})])
    pump = PrivateEventPump(adapter, bus, account_id="paper")

    await pump.run_once()

    events = _drain(subscription)
    assert len(events) == 1
    assert events[0].event_type == "connection_lost"


@pytest.mark.asyncio
async def test_pump_preserves_account_id_and_payload() -> None:
    bus = InMemoryForwardingBus()
    subscription = bus.subscribe_private("acct.paper-1.")
    adapter = _FakeAdapter([(BrokerEvent.ACCOUNT_UPDATED, {"cash": 100.0})])
    pump = PrivateEventPump(adapter, bus, account_id="paper-1")

    await pump.run_once()

    event = _drain(subscription)[0]
    assert event.account_id == "paper-1"
    assert event.payload["cash"] == 100.0
