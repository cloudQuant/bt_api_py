from __future__ import annotations

from collections import deque
from typing import Any

from bt_api_base.gateway.registrar import GatewayRuntimeRegistrar
from bt_api_base.registry import ExchangeRegistry


class QueueStub:
    def __init__(self) -> None:
        self._items: deque[Any] = deque()

    def put(self, item: Any) -> None:
        self._items.append(item)

    def get_nowait(self) -> Any:
        return self._items.popleft()

    def empty(self) -> bool:
        return not self._items


class EventBusStub:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def emit(self, event_name: str, payload: Any) -> None:
        self.events.append((event_name, payload))


def create_isolated_exchange_registry() -> ExchangeRegistry:
    return ExchangeRegistry.create_isolated()


def reset_gateway_runtime_registrar() -> None:
    GatewayRuntimeRegistrar.clear()
