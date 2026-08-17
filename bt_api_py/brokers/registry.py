"""Module-level docstring."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bt_api_py.brokers.base import BrokerAdapter

AdapterFactory = Callable[[], "BrokerAdapter"]

_ADAPTER_FACTORIES: dict[str, AdapterFactory] = {}


def _normalize_adapter_name(name: str) -> str:
    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("adapter name must be non-empty")
    return normalized


def register_adapter(name: str, factory: AdapterFactory) -> None:
    """register_adapter function"""
    _ADAPTER_FACTORIES[_normalize_adapter_name(name)] = factory


def get_adapter_factory(name: str) -> AdapterFactory | None:
    """get_adapter_factory function"""
    return _ADAPTER_FACTORIES.get(_normalize_adapter_name(name))


def list_registered_adapters() -> list[str]:
    """list_registered_adapters function"""
    return sorted(_ADAPTER_FACTORIES)
