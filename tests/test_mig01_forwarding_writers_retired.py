"""The retired forwarding and gateway writer surfaces are absent."""

from __future__ import annotations

import importlib.util

import pytest

import bt_api_py
import bt_api_py.runtime_plugins as runtime_plugins


def test_retired_sdk_writer_packages_and_modules_cannot_be_imported() -> None:
    names = (
        "bt_api_py.forwarding",
        "bt_api_py.gateway",
        "bt_api_py._gateway_wire",
        "bt_api_py.runtime_plugins.gateway_dispatch",
    )
    for name in names:
        assert importlib.util.find_spec(name) is None


def test_retired_gateway_names_are_not_public_sdk_exports() -> None:
    retired_sdk_names = {
        "GatewayClient",
        "ForwardingClient",
        "ForwardingRuntime",
        "BtApiForwardingAdapter",
        "InMemoryForwardingBus",
        "MarketDataHub",
        "MarketEvent",
        "OrderCommand",
        "OrderRouter",
        "PrivateEvent",
        "SQLiteStateStore",
        "ZmqForwardingClient",
        "ZmqForwardingRuntime",
        "MAX_MESSAGE_BYTES",
    }
    assert not retired_sdk_names.intersection(bt_api_py.__all__)

    for name in retired_sdk_names:
        with pytest.raises(AttributeError):
            getattr(bt_api_py, name)


def test_retired_runtime_plugin_gateway_names_are_not_public_exports() -> None:
    retired_runtime_names = {
        "GatewayExecutionAuthority",
        "GatewayManagedDispatchError",
        "GatewayManagedDispatcher",
        "GatewayManagedExecutionRuntime",
        "GatewayManagedOutcomeUnknown",
        "GatewayManagedOutcomeUnknownError",
        "compose_gateway_execution_authority",
        "compose_gateway_managed_client",
        "gateway_command_id",
    }
    assert not retired_runtime_names.intersection(runtime_plugins.__all__)
    for name in retired_runtime_names:
        with pytest.raises(AttributeError):
            getattr(runtime_plugins, name)
