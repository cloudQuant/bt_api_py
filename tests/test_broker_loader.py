from __future__ import annotations

from bt_api_py.brokers import loader as loader_module
from bt_api_py.brokers.errors import BrokerError, BrokerErrorCode
from bt_api_py.brokers.gateway_bridge import GatewayBridgeAdapter
from bt_api_py.brokers.loader import load_adapter
from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.brokers.registry import list_registered_adapters, register_adapter


def test_load_adapter_returns_builtin_mock() -> None:
    adapter = load_adapter("mock")

    assert isinstance(adapter, MockBrokerAdapter)


def test_load_adapter_returns_builtin_gateway_bridge() -> None:
    adapter = load_adapter("gateway_bridge")

    assert isinstance(adapter, GatewayBridgeAdapter)


def test_list_builtin_adapters_contains_mock_and_gateway_bridge() -> None:
    load_adapter("mock")

    adapters = list_registered_adapters()

    assert "mock" in adapters
    assert "gateway_bridge" in adapters


def test_load_adapter_raises_structured_error_for_unknown_name() -> None:
    try:
        load_adapter("missing")
    except BrokerError as exc:
        assert exc.code == BrokerErrorCode.ADAPTER_NOT_INSTALLED
    else:
        raise AssertionError("expected BrokerError for missing adapter")


def test_register_adapter_accepts_external_factory() -> None:
    register_adapter("external_mock", MockBrokerAdapter)

    adapter = load_adapter("external_mock")

    assert isinstance(adapter, MockBrokerAdapter)


def test_load_adapter_discovers_entry_point_registration(monkeypatch) -> None:
    def register_fake() -> None:
        register_adapter("entrypoint_mock", MockBrokerAdapter)

    class FakeEntryPoint:
        def load(self):
            return register_fake

    class FakeEntryPoints(list):
        def select(self, *, group: str):
            if group == loader_module.ENTRY_POINT_GROUP:
                return self
            return []

    monkeypatch.setattr(loader_module, "_ENTRY_POINTS_LOADED", False)
    monkeypatch.setattr(
        loader_module.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )

    adapter = load_adapter("entrypoint_mock")

    assert isinstance(adapter, MockBrokerAdapter)
