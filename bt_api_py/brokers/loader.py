"""Module-level docstring."""

from __future__ import annotations

from importlib import metadata

from bt_api_py.brokers.base import BrokerAdapter
from bt_api_py.brokers.errors import BrokerError, BrokerErrorCode
from bt_api_py.brokers.gateway_bridge import GatewayBridgeAdapter
from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.brokers.registry import (
    get_adapter_factory,
    list_registered_adapters,
    register_adapter,
)

_BUILTIN_ADAPTER_FACTORIES = {
    "mock": MockBrokerAdapter,
    "gateway_bridge": GatewayBridgeAdapter,
}
ENTRY_POINT_GROUP = "bt_api.adapters"
_ENTRY_POINTS_LOADED = False


def register_builtin_adapters() -> None:
    """register_builtin_adapters function"""
    for name, factory in _BUILTIN_ADAPTER_FACTORIES.items():
        register_adapter(name, factory)


def _discover_entry_point_adapters() -> None:
    global _ENTRY_POINTS_LOADED

    if _ENTRY_POINTS_LOADED:
        return

    entry_points = metadata.entry_points()
    selected_entry_points = (
        entry_points.select(group=ENTRY_POINT_GROUP)
        if hasattr(entry_points, "select")
        else entry_points.get(ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]  # 旧版 API 兼容
    )
    for entry_point in selected_entry_points:
        register = entry_point.load()
        register()

    _ENTRY_POINTS_LOADED = True


register_builtin_adapters()


def load_adapter(name: str) -> BrokerAdapter:
    """load_adapter function"""
    _discover_entry_point_adapters()
    factory = get_adapter_factory(name)
    if factory is None:
        raise BrokerError(BrokerErrorCode.ADAPTER_NOT_INSTALLED, f"adapter not found: {name}")
    return factory()


def available_adapters() -> list[str]:
    """available_adapters function"""
    _discover_entry_point_adapters()
    return list_registered_adapters()
