"""Regression tests for websocket monitoring lifecycle and alert state handling."""

import pytest

pytest.importorskip("psutil")

from bt_api_py.websocket.monitoring import (
    AlertManager,
    AlertSeverity,
    MetricsCollector,
    PerformanceAlert,
    WebSocketMonitor,
)


class _DummyWebSocketManager:
    def get_pool_stats(self) -> dict[str, object]:
        return {"pools": {}}


@pytest.mark.asyncio
async def test_metrics_collector_stop_clears_cleanup_task_reference() -> None:
    collector = MetricsCollector()

    await collector.start()
    assert collector._cleanup_task is not None

    await collector.stop()

    assert collector._cleanup_task is None
    assert collector._running is False


@pytest.mark.asyncio
async def test_alert_manager_triggers_once_until_resolved() -> None:
    collector = MetricsCollector()
    manager = AlertManager(collector)
    alert = PerformanceAlert(
        alert_id="high_latency",
        name="High Latency",
        severity=AlertSeverity.WARNING,
        condition="avg_latency_ms",
        threshold=100.0,
        description="Test alert",
    )
    manager.add_alert(alert)

    await manager._trigger_alert(alert, 150.0)
    first_trigger_count = alert.triggered_count
    manager._evaluate_condition = lambda condition: 150.0  # type: ignore[method-assign]
    await manager._check_alert(alert)

    assert alert.triggered_count == first_trigger_count
    assert len(manager.get_alert_history()) == 1

    manager._evaluate_condition = lambda condition: 50.0  # type: ignore[method-assign]
    await manager._check_alert(alert)

    assert alert.resolved is True
    assert len(manager.get_alert_history()) == 2


@pytest.mark.asyncio
async def test_alert_manager_stop_clears_monitoring_task_reference() -> None:
    collector = MetricsCollector()
    manager = AlertManager(collector)

    await manager.start()
    assert manager._monitoring_task is not None

    await manager.stop()

    assert manager._monitoring_task is None
    assert manager._running is False


@pytest.mark.asyncio
async def test_websocket_monitor_restart_replaces_task_list() -> None:
    monitor = WebSocketMonitor(_DummyWebSocketManager())

    await monitor.start()
    first_tasks = list(monitor._monitoring_tasks)
    assert len(first_tasks) == 3

    await monitor.stop()
    assert monitor._monitoring_tasks == []

    await monitor.start()
    second_tasks = list(monitor._monitoring_tasks)
    assert len(second_tasks) == 3
    assert second_tasks != first_tasks

    await monitor.stop()


@pytest.mark.asyncio
async def test_websocket_monitor_stop_cleans_up_tasks() -> None:
    monitor = WebSocketMonitor(_DummyWebSocketManager())

    await monitor.start()
    tasks = list(monitor._monitoring_tasks)

    await monitor.stop()

    assert monitor._running is False
    assert monitor._monitoring_tasks == []
    assert all(task.cancelled() or task.done() for task in tasks)
