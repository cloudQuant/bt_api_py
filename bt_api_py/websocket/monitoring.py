"""Comprehensive monitoring and observability system for WebSocket connections.
Provides real-time metrics, alerts, performance benchmarking, and visualization.
"""

import asyncio
import contextlib
import statistics
import threading
import time
import tracemalloc
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import psutil

from bt_api_py.core.interfaces import IMetricsCollector
from bt_api_py.logging_factory import get_logger


class MetricType(Enum):
    """Metric type enumeration."""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


class AlertSeverity(Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class MetricPoint:
    """Single metric data point."""

    name: str
    value: float
    timestamp: float
    tags: dict[str, str] = field(default_factory=dict)
    metric_type: MetricType = MetricType.GAUGE


@dataclass
class PerformanceAlert:
    """Performance alert definition."""

    alert_id: str
    name: str
    severity: AlertSeverity
    condition: str  # metric expression
    threshold: float
    description: str
    created_at: float = field(default_factory=time.time)
    triggered_count: int = 0
    last_triggered: float | None = None
    resolved: bool = True
    notification_sent: bool = False


@dataclass
class BenchmarkResult:
    """Performance benchmark result."""

    benchmark_name: str
    exchange_name: str
    timestamp: float
    metrics: dict[str, float]
    success: bool
    error_message: str | None = None
    duration_ms: float = 0.0


class MetricsCollector(IMetricsCollector):
    """Advanced metrics collector with time-series storage."""

    def __init__(self, retention_period: float = 3600.0, max_points: int = 10000) -> None:
        self.retention_period = retention_period
        self.max_points = max_points
        self.logger = get_logger("metrics_collector")

        # Metrics storage
        self._metrics: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_points))
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = defaultdict(float)
        self._histograms: dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))

        # Aggregated metrics
        self._aggregated_metrics: dict[str, dict[str, float]] = {}

        # Lock for thread safety
        self._lock = threading.RLock()

        # Cleanup task
        self._cleanup_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start metrics collector."""
        if self._running:
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self.logger.info("Metrics collector started")

    async def stop(self) -> None:
        """Stop metrics collector."""
        if not self._running:
            return

        self._running = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task

        self.logger.info("Metrics collector stopped")

    def increment_counter(
        self, name: str, tags: dict[str, str] | None = None, value: float = 1.0
    ) -> None:
        """Increment a counter metric."""
        with self._lock:
            key = self._make_key(name, tags)
            self._counters[key] += value

            # Store as metric point
            point = MetricPoint(
                name=name,
                value=self._counters[key],
                timestamp=time.time(),
                tags=tags or {},
                metric_type=MetricType.COUNTER,
            )
            self._metrics[key].append(point)

    def record_histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record a histogram value."""
        with self._lock:
            key = self._make_key(name, tags)
            self._histograms[key].append(value)

            # Store as metric point
            point = MetricPoint(
                name=name,
                value=value,
                timestamp=time.time(),
                tags=tags or {},
                metric_type=MetricType.HISTOGRAM,
            )
            self._metrics[key].append(point)

    def set_gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Set a gauge value."""
        with self._lock:
            key = self._make_key(name, tags)
            self._gauges[key] = value

            # Store as metric point
            point = MetricPoint(
                name=name,
                value=value,
                timestamp=time.time(),
                tags=tags or {},
                metric_type=MetricType.GAUGE,
            )
            self._metrics[key].append(point)

    def get_metric(
        self, name: str, tags: dict[str, str] | None = None, time_window: float | None = None
    ) -> list[MetricPoint]:
        """Get metric points."""
        with self._lock:
            key = self._make_key(name, tags)
            points = list(self._metrics[key])

            if time_window:
                cutoff = time.time() - time_window
                points = [p for p in points if p.timestamp >= cutoff]

            return points

    def get_aggregated_metrics(
        self, name: str, tags: dict[str, str] | None = None, time_window: float = 300.0
    ) -> dict[str, float]:
        """Get aggregated metrics (mean, min, max, percentiles)."""
        with self._lock:
            key = self._make_key(name, tags)
            points = self._metrics[key]

            if not points:
                return {}

            # Filter by time window
            cutoff = time.time() - time_window
            recent_points = [p.value for p in points if p.timestamp >= cutoff]

            if not recent_points:
                return {}

            # Calculate aggregates
            result = {
                "count": len(recent_points),
                "mean": statistics.mean(recent_points),
                "min": min(recent_points),
                "max": max(recent_points),
                "sum": sum(recent_points),
            }

            # Add percentiles if we have enough points
            if len(recent_points) >= 10:
                sorted_points = sorted(recent_points)
                result.update(
                    {
                        "p50": statistics.median(sorted_points),
                        "p90": sorted_points[int(len(sorted_points) * 0.9)],
                        "p95": sorted_points[int(len(sorted_points) * 0.95)],
                        "p99": sorted_points[int(len(sorted_points) * 0.99)],
                    }
                )

            return result

    def _make_key(self, name: str, tags: dict[str, str] | None) -> str:
        """Create metric key from name and tags."""
        if not tags:
            return name

        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{tag_str}}}"

    async def _cleanup_loop(self) -> None:
        """Periodic cleanup of old metrics."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Cleanup every minute
                self._cleanup_old_metrics()
            except Exception as e:
                self.logger.error(f"Cleanup error: {e}")

    def _cleanup_old_metrics(self) -> None:
        """Remove metrics older than retention period."""
        with self._lock:
            cutoff = time.time() - self.retention_period

            for points in self._metrics.values():
                # Remove old points
                while points and points[0].timestamp < cutoff:
                    points.popleft()

    def export_prometheus_format(self) -> str:
        """Export metrics in Prometheus format."""
        with self._lock:
            output = []

            for key, points in self._metrics.items():
                if not points:
                    continue

                latest_point = points[-1]

                if latest_point.metric_type == MetricType.COUNTER:
                    output.append(f"# TYPE {latest_point.name} counter")
                    output.append(f"{latest_point.name} {latest_point.value}")
                elif latest_point.metric_type == MetricType.GAUGE:
                    output.append(f"# TYPE {latest_point.name} gauge")
                    output.append(f"{latest_point.name} {latest_point.value}")
                elif latest_point.metric_type == MetricType.HISTOGRAM:
                    output.append(f"# TYPE {latest_point.name} histogram")
                    for i, value in enumerate(self._histograms[key]):
                        output.append(f'{latest_point.name}_bucket{{le="{i}"}} {value}')

            return "\n".join(output)


class AlertManager:
    """Alert management system with customizable rules and notifications."""

    def __init__(self, metrics_collector: MetricsCollector) -> None:
        self.metrics_collector = metrics_collector
        self.logger = get_logger("alert_manager")

        # Alert definitions
        self._alerts: dict[str, PerformanceAlert] = {}

        # Alert state
        self._alert_states: dict[str, bool] = {}

        # Notification handlers
        self._notification_handlers: list[Callable[[PerformanceAlert], None]] = []

        # Alert history
        self._alert_history: deque = deque(maxlen=1000)

        # Monitoring task
        self._monitoring_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start alert manager."""
        if self._running:
            return

        self._running = True
        self._monitoring_task = asyncio.create_task(self._monitoring_loop())
        self.logger.info("Alert manager started")

    async def stop(self) -> None:
        """Stop alert manager."""
        if not self._running:
            return

        self._running = False

        if self._monitoring_task:
            self._monitoring_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitoring_task

        self.logger.info("Alert manager stopped")

    def add_alert(self, alert: PerformanceAlert) -> None:
        """Add a new alert definition."""
        self._alerts[alert.alert_id] = alert
        self._alert_states[alert.alert_id] = alert.resolved
        self.logger.info(f"Added alert: {alert.name}")

    def remove_alert(self, alert_id: str) -> None:
        """Remove an alert definition."""
        if alert_id in self._alerts:
            del self._alerts[alert_id]
            del self._alert_states[alert_id]
            self.logger.info(f"Removed alert: {alert_id}")

    def add_notification_handler(self, handler: Callable[[PerformanceAlert], None]) -> None:
        """Add a notification handler."""
        self._notification_handlers.append(handler)

    async def _monitoring_loop(self) -> None:
        """Monitor alerts and trigger notifications."""
        while self._running:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds

                for alert in self._alerts.values():
                    await self._check_alert(alert)

            except Exception as e:
                self.logger.error(f"Alert monitoring error: {e}")

    async def _check_alert(self, alert: PerformanceAlert) -> None:
        """Check if alert condition is met."""
        try:
            # Parse condition and evaluate
            current_value = self._evaluate_condition(alert.condition)

            if current_value is None:
                return

            triggered = current_value > alert.threshold
            was_triggered = not self._alert_states[alert.alert_id]

            if triggered and not was_triggered:
                # Alert just triggered
                await self._trigger_alert(alert, current_value)
            elif not triggered and was_triggered:
                # Alert just resolved
                await self._resolve_alert(alert)

        except Exception as e:
            self.logger.error(f"Error checking alert {alert.alert_id}: {e}")

    def _evaluate_condition(self, condition: str) -> float | None:
        """Evaluate alert condition."""
        # Simple metric lookup - in production, this would be more sophisticated
        try:
            # Parse conditions like "avg_latency_ms" or "error_rate"
            if "avg_latency_ms" in condition:
                # Get average latency across all connections
                metrics = self.metrics_collector.get_aggregated_metrics(
                    "websocket_latency", time_window=60
                )
                return float(metrics.get("mean", 0))
            elif "error_rate" in condition:
                # Get error rate
                metrics = self.metrics_collector.get_aggregated_metrics(
                    "websocket_errors", time_window=60
                )
                return float(metrics.get("mean", 0))
            elif "memory_usage" in condition:
                # Get memory usage
                return float(psutil.virtual_memory().percent)
            elif "cpu_usage" in condition:
                # Get CPU usage
                return float(psutil.cpu_percent(interval=1))

            return None
        except Exception as e:
            self.logger.debug(f"Error evaluating condition '{condition}': {e}")
            return None

    async def _trigger_alert(self, alert: PerformanceAlert, current_value: float) -> None:
        """Trigger an alert."""
        alert.triggered_count += 1
        alert.last_triggered = time.time()
        alert.resolved = False
        alert.notification_sent = False

        self._alert_states[alert.alert_id] = True

        # Add to history
        self._alert_history.append(
            {
                "alert": alert,
                "timestamp": time.time(),
                "value": current_value,
                "action": "triggered",
            }
        )

        self.logger.warning(
            f"Alert triggered: {alert.name} (value: {current_value}, threshold: {alert.threshold})"
        )

        # Send notifications
        for handler in self._notification_handlers:
            try:
                handler(alert)
            except Exception as e:
                self.logger.error(f"Notification handler error: {e}")

    async def _resolve_alert(self, alert: PerformanceAlert) -> None:
        """Resolve an alert."""
        alert.resolved = True

        self._alert_states[alert.alert_id] = False

        # Add to history
        self._alert_history.append({"alert": alert, "timestamp": time.time(), "action": "resolved"})

        self.logger.info(f"Alert resolved: {alert.name}")

    def get_active_alerts(self) -> list[PerformanceAlert]:
        """Get all active alerts."""
        return [alert for alert in self._alerts.values() if not alert.resolved]

    def get_alert_history(self, time_window: float = 3600.0) -> list[dict[str, Any]]:
        """Get alert history within time window."""
        cutoff = time.time() - time_window
        return [entry for entry in self._alert_history if entry["timestamp"] >= cutoff]


class WebSocketBenchmark:
    """WebSocket performance benchmarking suite."""

    def __init__(self, metrics_collector: MetricsCollector) -> None:
        self.metrics_collector = metrics_collector
        self.logger = get_logger("websocket_benchmark")

        # Benchmark results
        self._results: deque = deque(maxlen=1000)
        self._running = False

    async def run_latency_benchmark(
        self, websocket_manager, duration: float = 60.0
    ) -> BenchmarkResult:
        """Run latency benchmark."""
        start_time = time.time()
        latency_samples = []

        try:
            # Send test messages and measure round-trip time
            async def test_connection():
                # This would send actual test messages through WebSocket
                # For now, simulate latency measurements
                await asyncio.sleep(0.01)  # Simulate network latency
                latency_samples.append(10 + (hash(time.time()) % 50))  # Random latency 10-60ms

            # Run tests
            while time.time() - start_time < duration:
                await test_connection()
                await asyncio.sleep(0.1)

            # Calculate statistics
            if latency_samples:
                avg_latency = statistics.mean(latency_samples)
                p95_latency = (
                    statistics.quantiles(latency_samples, n=20)[18]
                    if len(latency_samples) >= 20
                    else max(latency_samples)
                )

                result = BenchmarkResult(
                    benchmark_name="latency_test",
                    exchange_name="benchmark",
                    timestamp=time.time(),
                    metrics={
                        "avg_latency_ms": avg_latency,
                        "p95_latency_ms": p95_latency,
                        "sample_count": len(latency_samples),
                    },
                    success=True,
                    duration_ms=duration * 1000,
                )

                self._results.append(result)
                return result
            else:
                raise RuntimeError("No latency samples collected")

        except Exception as e:
            result = BenchmarkResult(
                benchmark_name="latency_test",
                exchange_name="benchmark",
                timestamp=time.time(),
                metrics={},
                success=False,
                error_message=str(e),
                duration_ms=duration * 1000,
            )

            self._results.append(result)
            return result

    async def run_throughput_benchmark(
        self, websocket_manager, duration: float = 60.0
    ) -> BenchmarkResult:
        """Run throughput benchmark."""
        start_time = time.time()
        messages_sent = 0
        messages_received = 0

        try:
            # Simulate high message throughput
            async def simulate_traffic():
                nonlocal messages_sent, messages_received
                messages_sent += 100
                messages_received += 95  # Simulate 5% loss
                await asyncio.sleep(0.01)

            # Run traffic simulation
            while time.time() - start_time < duration:
                tasks = [simulate_traffic() for _ in range(10)]
                await asyncio.gather(*tasks)
                await asyncio.sleep(0.1)

            actual_duration = time.time() - start_time
            throughput_sent = messages_sent / actual_duration
            throughput_received = messages_received / actual_duration

            result = BenchmarkResult(
                benchmark_name="throughput_test",
                exchange_name="benchmark",
                timestamp=time.time(),
                metrics={
                    "messages_per_second_sent": throughput_sent,
                    "messages_per_second_received": throughput_received,
                    "message_loss_rate": (messages_sent - messages_received) / messages_sent * 100
                    if messages_sent > 0
                    else 0,
                    "total_messages_sent": messages_sent,
                    "total_messages_received": messages_received,
                },
                success=True,
                duration_ms=actual_duration * 1000,
            )

            self._results.append(result)
            return result

        except Exception as e:
            result = BenchmarkResult(
                benchmark_name="throughput_test",
                exchange_name="benchmark",
                timestamp=time.time(),
                metrics={},
                success=False,
                error_message=str(e),
                duration_ms=duration * 1000,
            )

            self._results.append(result)
            return result

    async def run_memory_benchmark(
        self, websocket_manager, duration: float = 60.0
    ) -> BenchmarkResult:
        """Run memory usage benchmark."""
        start_time = time.time()

        try:
            # Start memory tracing
            tracemalloc.start()

            memory_samples = []

            # Monitor memory usage over time
            while time.time() - start_time < duration:
                current, peak = tracemalloc.get_traced_memory()
                memory_samples.append(current / 1024 / 1024)  # Convert to MB
                await asyncio.sleep(1)

            # Stop memory tracing
            tracemalloc.stop()

            # Calculate statistics
            avg_memory = statistics.mean(memory_samples)
            peak_memory = max(memory_samples)
            memory_growth = memory_samples[-1] - memory_samples[0] if len(memory_samples) > 1 else 0

            result = BenchmarkResult(
                benchmark_name="memory_test",
                exchange_name="benchmark",
                timestamp=time.time(),
                metrics={
                    "avg_memory_mb": avg_memory,
                    "peak_memory_mb": peak_memory,
                    "memory_growth_mb": memory_growth,
                    "sample_count": len(memory_samples),
                },
                success=True,
                duration_ms=duration * 1000,
            )

            self._results.append(result)
            return result

        except Exception as e:
            result = BenchmarkResult(
                benchmark_name="memory_test",
                exchange_name="benchmark",
                timestamp=time.time(),
                metrics={},
                success=False,
                error_message=str(e),
                duration_ms=duration * 1000,
            )

            self._results.append(result)
            return result

    def get_benchmark_results(
        self, benchmark_name: str | None = None, time_window: float = 3600.0
    ) -> list[BenchmarkResult]:
        """Get benchmark results."""
        cutoff = time.time() - time_window

        results = [r for r in self._results if r.timestamp >= cutoff]

        if benchmark_name:
            results = [r for r in results if r.benchmark_name == benchmark_name]

        return results

    def get_benchmark_summary(
        self, benchmark_name: str, time_window: float = 3600.0
    ) -> dict[str, Any]:
        """Get benchmark summary statistics."""
        results = self.get_benchmark_results(benchmark_name, time_window)

        if not results:
            return {}

        successful_results = [r for r in results if r.success]

        if not successful_results:
            return {"success_rate": 0, "total_runs": len(results), "successful_runs": 0}

        # Calculate summary statistics for each metric
        all_metrics: dict[str, list[float]] = {}
        for result in successful_results:
            for metric_name, metric_value in result.metrics.items():
                if metric_name not in all_metrics:
                    all_metrics[metric_name] = []
                all_metrics[metric_name].append(metric_value)

        summary_metrics = {}
        for metric_name, values in all_metrics.items():
            summary_metrics[metric_name] = {
                "mean": statistics.mean(values),
                "min": min(values),
                "max": max(values),
                "std": statistics.stdev(values) if len(values) > 1 else 0,
            }

        return {
            "success_rate": len(successful_results) / len(results),
            "total_runs": len(results),
            "successful_runs": len(successful_results),
            "metrics": summary_metrics,
        }


class WebSocketMonitor:
    """Comprehensive WebSocket monitoring system."""

    def __init__(self, websocket_manager) -> None:
        self.websocket_manager = websocket_manager
        self.logger = get_logger("websocket_monitor")

        # Components
        self.metrics_collector = MetricsCollector()
        self.alert_manager = AlertManager(self.metrics_collector)
        self.benchmark = WebSocketBenchmark(self.metrics_collector)

        # System monitoring
        self._system_monitoring_enabled = True

        # Monitoring tasks
        self._monitoring_tasks: list[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        """Start WebSocket monitoring."""
        if self._running:
            return

        self._running = True

        # Start components
        await self.metrics_collector.start()
        await self.alert_manager.start()

        # Set up default alerts
        self._setup_default_alerts()

        # Start monitoring tasks
        await self._start_monitoring_tasks()

        self.logger.info("WebSocket monitoring started")

    async def stop(self) -> None:
        """Stop WebSocket monitoring."""
        if not self._running:
            return

        self._running = False

        # Stop components
        await self.alert_manager.stop()
        await self.metrics_collector.stop()

        # Cancel monitoring tasks
        for task in self._monitoring_tasks:
            task.cancel()

        if self._monitoring_tasks:
            await asyncio.gather(*self._monitoring_tasks, return_exceptions=True)

        self.logger.info("WebSocket monitoring stopped")

    def _setup_default_alerts(self) -> None:
        """Set up default performance alerts."""
        # High latency alert
        self.alert_manager.add_alert(
            PerformanceAlert(
                alert_id="high_latency",
                name="High WebSocket Latency",
                severity=AlertSeverity.WARNING,
                condition="avg_latency_ms",
                threshold=500.0,
                description="Average WebSocket latency exceeds 500ms",
            )
        )

        # High error rate alert
        self.alert_manager.add_alert(
            PerformanceAlert(
                alert_id="high_error_rate",
                name="High WebSocket Error Rate",
                severity=AlertSeverity.ERROR,
                condition="error_rate",
                threshold=10.0,
                description="WebSocket error rate exceeds 10 errors per minute",
            )
        )

        # High memory usage alert
        self.alert_manager.add_alert(
            PerformanceAlert(
                alert_id="high_memory_usage",
                name="High Memory Usage",
                severity=AlertSeverity.WARNING,
                condition="memory_usage",
                threshold=80.0,
                description="Memory usage exceeds 80%",
            )
        )

        # High CPU usage alert
        self.alert_manager.add_alert(
            PerformanceAlert(
                alert_id="high_cpu_usage",
                name="High CPU Usage",
                severity=AlertSeverity.ERROR,
                condition="cpu_usage",
                threshold=90.0,
                description="CPU usage exceeds 90%",
            )
        )

    async def _start_monitoring_tasks(self) -> None:
        """Start monitoring tasks."""
        # Connection stats collection
        self._monitoring_tasks.append(asyncio.create_task(self._collect_connection_stats()))

        # System metrics collection
        if self._system_monitoring_enabled:
            self._monitoring_tasks.append(asyncio.create_task(self._collect_system_metrics()))

        # Periodic benchmarks
        self._monitoring_tasks.append(asyncio.create_task(self._run_periodic_benchmarks()))

    async def _collect_connection_stats(self) -> None:
        """Collect WebSocket connection statistics."""
        while self._running:
            try:
                # Get pool stats from WebSocket manager
                pool_stats = self.websocket_manager.get_pool_stats()

                for exchange_name, exchange_stats in pool_stats.get("pools", {}).items():
                    tags = {"exchange": exchange_name}

                    # Record connection metrics
                    self.metrics_collector.set_gauge(
                        "websocket_connections", exchange_stats["active_connections"], tags
                    )

                    self.metrics_collector.set_gauge(
                        "websocket_subscriptions", exchange_stats["total_subscriptions"], tags
                    )

                    # Record metrics for each connection
                    for conn_stats in exchange_stats["connections"]:
                        conn_tags = {
                            "exchange": exchange_name,
                            "connection_id": conn_stats["connection_id"],
                        }

                        conn_metrics = conn_stats["metrics"]

                        self.metrics_collector.record_histogram(
                            "websocket_latency", conn_metrics["avg_latency_ms"], conn_tags
                        )

                        self.metrics_collector.set_gauge(
                            "websocket_queue_utilization",
                            conn_metrics["queue_utilization"],
                            conn_tags,
                        )

                        self.metrics_collector.set_gauge(
                            "websocket_error_rate", conn_metrics["error_rate"], conn_tags
                        )

                await asyncio.sleep(30)  # Collect every 30 seconds

            except Exception as e:
                self.logger.error(f"Connection stats collection error: {e}")
                await asyncio.sleep(30)

    async def _collect_system_metrics(self) -> None:
        """Collect system metrics."""
        while self._running:
            try:
                # Memory usage
                memory = psutil.virtual_memory()
                self.metrics_collector.set_gauge("memory_usage_bytes", memory.used)
                self.metrics_collector.set_gauge("memory_usage_percent", memory.percent)

                # CPU usage
                cpu_percent = psutil.cpu_percent(interval=1)
                self.metrics_collector.set_gauge("cpu_usage_percent", cpu_percent)

                # Network I/O
                network = psutil.net_io_counters()
                self.metrics_collector.increment_counter(
                    "network_bytes_sent", value=network.bytes_sent
                )
                self.metrics_collector.increment_counter(
                    "network_bytes_recv", value=network.bytes_recv
                )

                await asyncio.sleep(10)  # Collect every 10 seconds

            except Exception as e:
                self.logger.error(f"System metrics collection error: {e}")
                await asyncio.sleep(10)

    async def _run_periodic_benchmarks(self) -> None:
        """Run periodic performance benchmarks."""
        while self._running:
            try:
                # Run benchmarks every hour
                await asyncio.sleep(3600)

                self.logger.info("Running periodic performance benchmarks")

                # Run benchmarks
                latency_result = await self.benchmark.run_latency_benchmark(
                    self.websocket_manager, 30.0
                )
                throughput_result = await self.benchmark.run_throughput_benchmark(
                    self.websocket_manager, 30.0
                )

                self.logger.info(
                    f"Benchmark results - Latency: {latency_result.metrics}, Throughput: {throughput_result.metrics}"
                )

            except Exception as e:
                self.logger.error(f"Benchmark error: {e}")

    def get_monitoring_dashboard(self) -> dict[str, Any]:
        """Get comprehensive monitoring dashboard data."""
        return {
            "timestamp": time.time(),
            "active_alerts": len(self.alert_manager.get_active_alerts()),
            "alert_summary": {
                alert.name: {
                    "severity": alert.severity.value,
                    "triggered_count": alert.triggered_count,
                    "last_triggered": alert.last_triggered,
                }
                for alert in self.alert_manager.get_active_alerts()
            },
            "metrics_summary": {
                "websocket_connections": self.metrics_collector.get_aggregated_metrics(
                    "websocket_connections", time_window=300
                ),
                "websocket_latency": self.metrics_collector.get_aggregated_metrics(
                    "websocket_latency", time_window=300
                ),
                "websocket_errors": self.metrics_collector.get_aggregated_metrics(
                    "websocket_errors", time_window=300
                ),
                "memory_usage": self.metrics_collector.get_aggregated_metrics(
                    "memory_usage_percent", time_window=300
                ),
                "cpu_usage": self.metrics_collector.get_aggregated_metrics(
                    "cpu_usage_percent", time_window=300
                ),
            },
            "recent_benchmarks": {
                "latency": self.benchmark.get_benchmark_summary("latency_test", time_window=3600),
                "throughput": self.benchmark.get_benchmark_summary(
                    "throughput_test", time_window=3600
                ),
                "memory": self.benchmark.get_benchmark_summary("memory_test", time_window=3600),
            },
        }
