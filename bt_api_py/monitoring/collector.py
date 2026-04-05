"""
Main metrics collector that coordinates all monitoring components.

Provides a unified interface for collecting performance metrics from various sources.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

from bt_api_py.monitoring.system_metrics import (
    get_business_collector,
    get_system_collector,
)


@dataclass
class PerformanceMetrics:
    """Comprehensive performance metrics container."""

    timestamp: float = field(default_factory=time.time)

    # System metrics
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_mb: float = 0.0
    disk_usage_percent: float = 0.0
    thread_count: int = 0
    fd_count: int = 0

    # Business metrics
    orders_total: int = 0
    orders_success: int = 0
    orders_failed: int = 0
    api_requests: int = 0
    api_errors: int = 0
    active_connections: int = 0

    # Performance metrics
    order_latency_avg: float = 0.0
    api_latency_avg: float = 0.0

    # Custom metrics
    custom_metrics: dict[str, Any] = field(default_factory=dict)


class MetricsCollector:
    """Main metrics collector that coordinates all monitoring."""

    def __init__(self, collection_interval: float = 5.0) -> None:
        self.collection_interval = collection_interval
        self._running = False
        self._task: asyncio.Task | None = None

        # Initialize collectors
        self.system_collector = get_system_collector(collection_interval)
        self.business_collector = get_business_collector()

        # Custom metrics registry
        self._custom_metrics: dict[str, Any] = {}
        self._metric_history: list[PerformanceMetrics] = []
        self._max_history_size = 1000

    async def start_collection(self) -> None:
        """Start all metrics collection."""
        if self._running:
            return

        self._running = True

        # Start individual collectors
        await self.system_collector.start_collection()

        # Start main collection loop
        self._task = asyncio.create_task(self._collection_loop())

    async def stop_collection(self) -> None:
        """Stop all metrics collection."""
        self._running = False

        # Stop individual collectors
        await self.system_collector.stop_collection()

        # Stop main collection loop
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _collection_loop(self) -> None:
        """Main collection loop for comprehensive metrics."""
        while self._running:
            try:
                metrics = await self.collect_comprehensive_metrics()
                self._metric_history.append(metrics)

                # Limit history size
                if len(self._metric_history) > self._max_history_size:
                    self._metric_history.pop(0)

                await asyncio.sleep(self.collection_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                # Continue collection even if there's an error
                await asyncio.sleep(self.collection_interval)

    async def collect_comprehensive_metrics(self) -> PerformanceMetrics:
        """Collect comprehensive performance metrics."""
        # Get system metrics
        system_metrics = self.system_collector.get_current_metrics()
        business_collector = self.business_collector

        # Create comprehensive metrics
        metrics = PerformanceMetrics(
            cpu_percent=system_metrics.cpu_percent,
            memory_percent=system_metrics.memory_percent,
            memory_mb=system_metrics.memory_mb,
            disk_usage_percent=system_metrics.disk_usage_percent,
            thread_count=system_metrics.thread_count,
            fd_count=getattr(system_metrics, "fd_count", 0),
            orders_total=int(business_collector.orders_total.get()),
            orders_success=int(business_collector.orders_success.get()),
            orders_failed=int(business_collector.orders_failed.get()),
            api_requests=int(business_collector.api_requests.get()),
            api_errors=int(business_collector.api_errors.get()),
            active_connections=int(business_collector.active_connections.get()),
            order_latency_avg=business_collector.order_latency_histogram.get_average(),
            api_latency_avg=business_collector.api_latency_histogram.get_average(),
            # Custom metrics
            custom_metrics=self._custom_metrics.copy(),
        )

        return metrics

    def add_custom_metric(self, name: str, value: Any) -> None:
        """Add a custom metric."""
        self._custom_metrics[name] = value

    def remove_custom_metric(self, name: str) -> None:
        """Remove a custom metric."""
        self._custom_metrics.pop(name, None)

    def get_current_metrics(self) -> PerformanceMetrics:
        """Get current comprehensive metrics."""
        return asyncio.run(self.collect_comprehensive_metrics())

    def get_metric_history(self, limit: int | None = None) -> list[PerformanceMetrics]:
        """Get historical metrics."""
        if limit:
            return self._metric_history[-limit:]
        return self._metric_history.copy()

    def get_average_metrics(self, duration_minutes: float = 5.0) -> PerformanceMetrics | None:
        """Get average metrics over the specified duration."""
        cutoff_time = time.time() - (duration_minutes * 60)
        recent_metrics = [m for m in self._metric_history if m.timestamp >= cutoff_time]

        if not recent_metrics:
            return None

        # Calculate averages
        avg_metrics = PerformanceMetrics()
        count = len(recent_metrics)

        for metrics in recent_metrics:
            avg_metrics.cpu_percent += metrics.cpu_percent
            avg_metrics.memory_percent += metrics.memory_percent
            avg_metrics.memory_mb += metrics.memory_mb
            avg_metrics.disk_usage_percent += metrics.disk_usage_percent
            avg_metrics.thread_count += metrics.thread_count
            avg_metrics.fd_count += metrics.fd_count
            avg_metrics.orders_total += metrics.orders_total
            avg_metrics.orders_success += metrics.orders_success
            avg_metrics.orders_failed += metrics.orders_failed
            avg_metrics.api_requests += metrics.api_requests
            avg_metrics.api_errors += metrics.api_errors
            avg_metrics.active_connections += metrics.active_connections
            avg_metrics.order_latency_avg += metrics.order_latency_avg
            avg_metrics.api_latency_avg += metrics.api_latency_avg

        # Divide by count
        avg_metrics.cpu_percent /= count
        avg_metrics.memory_percent /= count
        avg_metrics.memory_mb /= count
        avg_metrics.disk_usage_percent /= count
        avg_metrics.thread_count //= count
        avg_metrics.fd_count //= count
        avg_metrics.orders_total //= count
        avg_metrics.orders_success //= count
        avg_metrics.orders_failed //= count
        avg_metrics.api_requests //= count
        avg_metrics.api_errors //= count
        avg_metrics.active_connections //= count
        avg_metrics.order_latency_avg /= count
        avg_metrics.api_latency_avg /= count

        return avg_metrics

    def clear_history(self) -> None:
        """Clear metrics history."""
        self._metric_history.clear()

    def export_metrics_json(self) -> dict[str, Any]:
        """Export metrics in JSON format."""
        current = self.get_current_metrics()

        return {
            "timestamp": current.timestamp,
            "system": {
                "cpu_percent": current.cpu_percent,
                "memory_percent": current.memory_percent,
                "memory_mb": current.memory_mb,
                "disk_usage_percent": current.disk_usage_percent,
                "thread_count": current.thread_count,
                "fd_count": current.fd_count,
            },
            "business": {
                "orders_total": current.orders_total,
                "orders_success": current.orders_success,
                "orders_failed": current.orders_failed,
                "api_requests": current.api_requests,
                "api_errors": current.api_errors,
                "active_connections": current.active_connections,
            },
            "performance": {
                "order_latency_avg": current.order_latency_avg,
                "api_latency_avg": current.api_latency_avg,
            },
            "custom_metrics": current.custom_metrics,
        }


# Global metrics collector
_global_collector: MetricsCollector | None = None


def get_global_collector(collection_interval: float = 5.0) -> MetricsCollector:
    """Get or create the global metrics collector."""
    global _global_collector
    if _global_collector is None:
        _global_collector = MetricsCollector(collection_interval)
    return _global_collector


async def start_global_monitoring(collection_interval: float = 5.0) -> MetricsCollector:
    """Start global monitoring and return the collector."""
    collector = get_global_collector(collection_interval)
    await collector.start_collection()
    return collector


async def stop_global_monitoring() -> None:
    """Stop global monitoring."""
    global _global_collector
    if _global_collector:
        await _global_collector.stop_collection()
