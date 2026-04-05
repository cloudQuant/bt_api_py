"""
System metrics collector for monitoring BT API performance.

Collects system-level metrics including CPU, memory, network, and custom business metrics.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import time
from dataclasses import dataclass, field
from typing import Any

import psutil

from bt_api_py.monitoring.metrics import Counter, Gauge, Histogram, Metric, MetricRegistry


@dataclass
class SystemMetrics:
    """Container for system metrics."""

    timestamp: float = field(default_factory=time.time)
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_mb: float = 0.0
    disk_usage_percent: float = 0.0
    network_bytes_sent: int = 0
    network_bytes_recv: int = 0
    process_count: int = 0
    thread_count: int = 0
    gc_count: list[int] = field(default_factory=lambda: [0, 0, 0])


class SystemMetricsCollector:
    """Collects system-level metrics."""

    def __init__(
        self, collection_interval: float = 5.0, registry: MetricRegistry | None = None
    ) -> None:
        self.collection_interval = collection_interval
        self._running = False
        self._task: asyncio.Task | None = None
        self._process = psutil.Process()

        # Metrics
        self.registry = registry or MetricRegistry()
        self.setup_metrics()

        # Network baseline
        self._network_baseline = self._get_network_stats()

    def setup_metrics(self) -> None:
        """Setup system metrics."""
        # CPU metrics
        self.cpu_usage_gauge = Gauge("btapi_system_cpu_usage_percent", "CPU usage percentage")
        self.cpu_user_gauge = Gauge(
            "btapi_system_cpu_user_seconds_total", "CPU user time seconds total"
        )
        self.cpu_system_gauge = Gauge(
            "btapi_system_cpu_system_seconds_total", "CPU system time seconds total"
        )

        # Memory metrics
        self.memory_usage_gauge = Gauge(
            "btapi_system_memory_usage_percent", "Memory usage percentage"
        )
        self.memory_bytes_gauge = Gauge("btapi_system_memory_usage_bytes", "Memory usage in bytes")
        self.memory_available_gauge = Gauge(
            "btapi_system_memory_available_bytes", "Available memory in bytes"
        )

        # Process metrics
        self.process_cpu_gauge = Gauge(
            "btapi_process_cpu_usage_percent", "Process CPU usage percentage"
        )
        self.process_memory_gauge = Gauge(
            "btapi_process_memory_usage_bytes", "Process memory usage in bytes"
        )
        self.process_memory_rss_gauge = Gauge(
            "btapi_process_memory_rss_bytes", "Process RSS memory in bytes"
        )
        self.process_threads_gauge = Gauge(
            "btapi_process_threads_count", "Number of process threads"
        )

        # File descriptors
        self.fd_count_gauge = Gauge(
            "btapi_process_file_descriptors_count", "Number of open file descriptors"
        )

        # GC metrics
        self.gc_count_gauge = Gauge("btapi_gc_collections_total", "Number of garbage collections")

        # Network metrics
        self.network_bytes_sent_gauge = Gauge(
            "btapi_network_bytes_sent_total", "Total network bytes sent"
        )
        self.network_bytes_recv_gauge = Gauge(
            "btapi_network_bytes_recv_total", "Total network bytes received"
        )

        # Disk metrics
        self.disk_usage_gauge = Gauge("btapi_disk_usage_percent", "Disk usage percentage")

        # Register all metrics
        metrics = [
            self.cpu_usage_gauge,
            self.cpu_user_gauge,
            self.cpu_system_gauge,
            self.memory_usage_gauge,
            self.memory_bytes_gauge,
            self.memory_available_gauge,
            self.process_cpu_gauge,
            self.process_memory_gauge,
            self.process_memory_rss_gauge,
            self.process_threads_gauge,
            self.fd_count_gauge,
            self.gc_count_gauge,
            self.network_bytes_sent_gauge,
            self.network_bytes_recv_gauge,
            self.disk_usage_gauge,
        ]

        for metric in metrics:
            self.registry.register(metric)

    def _get_network_stats(self) -> dict[str, int]:
        """Get current network statistics."""
        try:
            net_io = psutil.net_io_counters()
            return {"bytes_sent": net_io.bytes_sent, "bytes_recv": net_io.bytes_recv}
        except (OSError, psutil.AccessDenied):
            return {"bytes_sent": 0, "bytes_recv": 0}

    def collect_metrics(self) -> SystemMetrics:
        """Collect current system metrics."""
        try:
            # CPU metrics
            cpu_percent = psutil.cpu_percent(interval=None)
            cpu_times = psutil.cpu_times()

            # Memory metrics
            memory = psutil.virtual_memory()

            # Process metrics (cpu_percent needs interval=None for non-blocking; first call may return 0)
            process_cpu_percent = self._process.cpu_percent(interval=None)
            process_memory = self._process.memory_info()
            process_threads = self._process.num_threads()

            # File descriptors (Unix only)
            try:
                fd_count = self._process.num_fds() if hasattr(self._process, "num_fds") else 0
            except (OSError, psutil.AccessDenied):
                fd_count = 0

            # GC metrics
            gc.get_stats() if hasattr(gc, "get_stats") else []
            gc_counts = gc.collect() if hasattr(gc, "collect") else [0, 0, 0]

            # Network metrics
            network_stats = self._get_network_stats()

            # Disk metrics
            disk_usage = psutil.disk_usage("/")

            metrics = SystemMetrics(
                cpu_percent=cpu_percent,
                memory_percent=memory.percent,
                memory_mb=process_memory.rss / 1024 / 1024,
                disk_usage_percent=disk_usage.percent,
                network_bytes_sent=network_stats["bytes_sent"]
                - self._network_baseline["bytes_sent"],
                network_bytes_recv=network_stats["bytes_recv"]
                - self._network_baseline["bytes_recv"],
                process_count=len(psutil.pids()),
                thread_count=process_threads,
                gc_count=gc_counts if isinstance(gc_counts, list) else [gc_counts, 0, 0],
            )

            # Update gauge metrics
            self.update_gauges(
                metrics, cpu_times, memory, process_memory, fd_count, process_cpu_percent
            )

            return metrics

        except (OSError, psutil.AccessDenied, psutil.NoSuchProcess):
            # Return default metrics if access denied
            return SystemMetrics()

    def update_gauges(
        self,
        metrics: SystemMetrics,
        cpu_times: Any,
        memory: Any,
        process_memory: Any,
        fd_count: int,
        process_cpu_percent: float = 0.0,
    ) -> None:
        """Update all gauge metrics."""
        # System metrics
        self.cpu_usage_gauge.set(metrics.cpu_percent)
        self.cpu_user_gauge.set(cpu_times.user)
        self.cpu_system_gauge.set(cpu_times.system)

        self.memory_usage_gauge.set(metrics.memory_percent)
        self.memory_bytes_gauge.set(memory.used)
        self.memory_available_gauge.set(memory.available)

        # Process metrics (process_cpu_percent = current process CPU usage %)
        self.process_cpu_gauge.set(process_cpu_percent)
        self.process_memory_gauge.set(process_memory.rss)
        self.process_memory_rss_gauge.set(process_memory.rss)
        self.process_threads_gauge.set(metrics.thread_count)

        self.fd_count_gauge.set(fd_count)

        # GC metrics
        for _i, count in enumerate(metrics.gc_count):
            self.gc_count_gauge.set(count)

        # Network metrics
        self.network_bytes_sent_gauge.set(metrics.network_bytes_sent)
        self.network_bytes_recv_gauge.set(metrics.network_bytes_recv)

        # Disk metrics
        self.disk_usage_gauge.set(metrics.disk_usage_percent)

    async def start_collection(self) -> None:
        """Start periodic metrics collection."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._collection_loop())

    async def stop_collection(self) -> None:
        """Stop metrics collection."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _collection_loop(self) -> None:
        """Main collection loop."""
        while self._running:
            try:
                self.collect_metrics()
                await asyncio.sleep(self.collection_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                # Continue collection even if there's an error
                await asyncio.sleep(self.collection_interval)

    def get_current_metrics(self) -> SystemMetrics:
        """Get current metrics without waiting."""
        return self.collect_metrics()


class BusinessMetricsCollector:
    """Collects business-specific metrics for trading systems."""

    def __init__(self, registry: MetricRegistry | None = None) -> None:
        self.registry = registry or MetricRegistry()
        self.setup_business_metrics()

    def setup_business_metrics(self) -> None:
        """Setup business metrics."""
        # Order metrics
        self.orders_total = Counter("btapi_orders_total", "Total number of orders placed")
        self.orders_success = Counter(
            "btapi_orders_success_total", "Total number of successful orders"
        )
        self.orders_failed = Counter("btapi_orders_failed_total", "Total number of failed orders")
        self.order_latency_histogram = Histogram(
            "btapi_order_latency_seconds",
            "Order placement latency",
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
        )

        # Data metrics
        self.market_data_updates = Counter(
            "btapi_market_data_updates_total", "Total market data updates received"
        )
        self.websocket_messages = Counter(
            "btapi_websocket_messages_total", "Total WebSocket messages received"
        )

        # API metrics
        self.api_requests = Counter("btapi_api_requests_total", "Total API requests made")
        self.api_errors = Counter("btapi_api_errors_total", "Total API request errors")
        self.api_latency_histogram = Histogram(
            "btapi_api_latency_seconds",
            "API request latency",
            buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
        )
        self.api_latency = self.api_latency_histogram

        # Connection metrics
        self.active_connections = Gauge("btapi_active_connections", "Number of active connections")
        self.connection_errors = Counter("btapi_connection_errors_total", "Total connection errors")

        # Register all metrics
        metrics: list[Metric] = [
            self.orders_total,
            self.orders_success,
            self.orders_failed,
            self.order_latency_histogram,
            self.market_data_updates,
            self.websocket_messages,
            self.api_requests,
            self.api_errors,
            self.api_latency_histogram,
            self.active_connections,
            self.connection_errors,
        ]

        for metric in metrics:
            self.registry.register(metric)

    def record_order_placed(self, success: bool = False, latency: float | None = None) -> None:
        """Record an order placement."""
        self.orders_total.inc()
        if success:
            self.orders_success.inc()
        else:
            self.orders_failed.inc()
        if latency:
            self.order_latency_histogram.observe(latency)

    def record_market_data_update(self) -> None:
        """Record a market data update."""
        self.market_data_updates.inc()

    def record_websocket_message(self) -> None:
        """Record a WebSocket message."""
        self.websocket_messages.inc()

    def record_api_request(self, success: bool = True, latency: float | None = None) -> None:
        """Record an API request."""
        self.api_requests.inc()
        if not success:
            self.api_errors.inc()
        if latency:
            self.api_latency_histogram.observe(latency)

    def set_active_connections(self, count: int) -> None:
        """Set the number of active connections."""
        self.active_connections.set(count)

    def record_connection_error(self) -> None:
        """Record a connection error."""
        self.connection_errors.inc()


# Global collectors
_system_collector: SystemMetricsCollector | None = None
_business_collector: BusinessMetricsCollector | None = None


def get_system_collector(collection_interval: float = 5.0) -> SystemMetricsCollector:
    """Get or create the system metrics collector."""
    global _system_collector
    if _system_collector is None:
        _system_collector = SystemMetricsCollector(collection_interval)
    return _system_collector


def get_business_collector() -> BusinessMetricsCollector:
    """Get or create the business metrics collector."""
    global _business_collector
    if _business_collector is None:
        _business_collector = BusinessMetricsCollector()
    return _business_collector
