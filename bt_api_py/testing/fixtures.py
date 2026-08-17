"""Module documentation"""
from __future__ import annotations

from collections import deque
from typing import Any

from bt_api_base.gateway.registrar import GatewayRuntimeRegistrar
from bt_api_base.registry import ExchangeRegistry

from bt_api_py.brokers.types import OrderRequest

DEFAULT_CONTRACT_SYMBOL = "RB2510"
DEFAULT_CONTRACT_PRICE = 3500.0
DEFAULT_CONTRACT_QUANTITY = 1.0


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


def reset_gateway_runtime_registrar() -> None:
    """reset_gateway_runtime_registrar function"""
    GatewayRuntimeRegistrar.clear()


def make_contract_order_request(account_id: str) -> OrderRequest:
    """make_contract_order_request function"""
    return OrderRequest(
        account_id=account_id,
        symbol=DEFAULT_CONTRACT_SYMBOL,
        side="buy",
        quantity=DEFAULT_CONTRACT_QUANTITY,
        price=DEFAULT_CONTRACT_PRICE,
    )
