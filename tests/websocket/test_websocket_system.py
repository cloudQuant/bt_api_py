"""Regression tests for websocket system initialization and manager cleanup."""

import pytest

import bt_api_py.websocket as websocket_module
from bt_api_py.websocket.advanced_websocket_manager import (
    AdvancedWebSocketManager,
    ConnectionWrapper,
)


class _DummyConnection:
    def __init__(self, connection_id: str = "dummy_0") -> None:
        self.connection_id = connection_id
        self.disconnect_calls = 0

    async def disconnect(self) -> None:
        self.disconnect_calls += 1


class _FakeMonitor:
    def __init__(self, manager: AdvancedWebSocketManager) -> None:
        self.manager = manager
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_advanced_websocket_manager_instantiates_without_event_bus() -> None:
    manager = AdvancedWebSocketManager()

    assert manager.event_bus is not None
    assert manager.get_pool_stats()["pools"] == {}

    await manager.stop()


@pytest.mark.asyncio
async def test_close_all_clears_pools_and_resets_connection_metrics() -> None:
    manager = AdvancedWebSocketManager()
    connection = _DummyConnection()
    manager._pools["BINANCE___SPOT"] = [ConnectionWrapper(connection=connection)]
    manager._global_metrics["total_connections"] = 1
    manager._global_metrics["active_connections"] = 1
    manager._global_metrics["total_subscriptions"] = 3

    await manager.close_all()

    assert connection.disconnect_calls == 1
    assert manager._pools["BINANCE___SPOT"] == []
    assert manager._global_metrics["total_connections"] == 0
    assert manager._global_metrics["active_connections"] == 0
    assert manager._global_metrics["total_subscriptions"] == 0


@pytest.mark.asyncio
async def test_websocket_system_initialize_and_shutdown_reset_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(websocket_module, "WebSocketMonitor", _FakeMonitor)
    system = websocket_module.WebSocketSystem()

    await system.initialize()

    assert isinstance(system.manager, AdvancedWebSocketManager)
    assert isinstance(system.monitor, _FakeMonitor)
    assert system.monitor.started is True
    assert system._initialized is True

    await system.shutdown()

    assert system.manager is None
    assert system.monitor is None
    assert system._initialized is False
