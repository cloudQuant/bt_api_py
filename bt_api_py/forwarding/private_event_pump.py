"""Private event pump: broker event stream → PrivateEvent fan-out (Task 3.2)."""

from __future__ import annotations

from typing import Any

from bt_api_py.brokers.types import BrokerEvent
from bt_api_py.forwarding.memory import InMemoryForwardingBus
from bt_api_py.forwarding.schema import PrivateEvent

_BROKER_EVENT_TYPES = {
    BrokerEvent.ORDER_UPDATED: "order",
    BrokerEvent.POSITION_UPDATED: "position",
    BrokerEvent.ACCOUNT_UPDATED: "account",
    BrokerEvent.ERROR: "error",
    BrokerEvent.CONNECTION_LOST: "connection_lost",
    BrokerEvent.RESYNC_REQUIRED: "resync_required",
}


class PrivateEventPump:
    """Consume a broker adapter's ``stream_events`` and publish private events.

    Events are published serially per account so consumers observe a consistent
    order. Connection loss and resync signals are converted to explicit
    ``connection_lost`` / ``resync_required`` private events instead of letting
    the task silently exit (U-04).
    """

    def __init__(self, adapter: Any, bus: InMemoryForwardingBus, *, account_id: str) -> None:
        self._adapter = adapter
        self._bus = bus
        self._account_id = account_id

    async def run_once(self) -> None:
        """Drain one pass of the broker event stream into the bus."""
        async for broker_event, payload in self._adapter.stream_events():
            event = self._to_private_event(broker_event, payload)
            self._bus.publish_private(event)

    def _to_private_event(self, broker_event: BrokerEvent, payload: dict[str, Any]) -> PrivateEvent:
        event_type = _BROKER_EVENT_TYPES.get(broker_event, str(broker_event.value))
        return PrivateEvent(
            event_type=event_type,
            account_id=str(payload.get("account_id") or self._account_id),
            payload=dict(payload),
        )
