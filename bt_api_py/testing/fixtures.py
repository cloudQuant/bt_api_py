"""Module documentation"""

from __future__ import annotations

from collections import deque
from typing import Any

from bt_api_base.registry import ExchangeRegistry


class QueueStub:
    """Class QueueStub"""

    def __init__(self) -> None:
        """__init__ method"""
        self._items: deque[Any] = deque()

    def put(self, item: Any) -> None:
        """put method"""
        self._items.append(item)

    def get_nowait(self) -> Any:
        """get_nowait method"""
        return self._items.popleft()

    def empty(self) -> bool:
        """empty method"""
        return not self._items


class EventBusStub:
    """Class EventBusStub"""

    def __init__(self) -> None:
        """__init__ method"""
        self.events: list[tuple[str, Any]] = []

    def emit(self, event_name: str, payload: Any) -> None:
        """emit method"""
        self.events.append((event_name, payload))


def create_isolated_exchange_registry() -> ExchangeRegistry:
    """create_isolated_exchange_registry function"""
    return ExchangeRegistry.create_isolated()


