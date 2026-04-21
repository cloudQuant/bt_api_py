"""
Tests for the monitoring system.

Covers metrics collection, logging, health monitoring, and integration components.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
import pytest_asyncio

pytest.importorskip("psutil")

from bt_api_py.monitoring import (
    BusinessMetricsCollector,
    Counter,
    ExchangeHealthMonitor,
    Gauge,
    HealthCheckFactory,
    Histogram,
    MetricRegistry,
    MetricsCollector,
    PerformanceMetrics,
    SystemMetricsCollector,
    get_registry,
    monitor_calls,
    monitor_execution_time,
    monitor_performance,
    timer,
)


class TestMetrics:
    """Test metric collection and registration."""

    def test_counter(self) -> None:
        """Test counter functionality."""
        counter = Counter("test_counter", "Test counter")

        assert counter.get() == 0.0

        counter.inc()
        assert counter.get() == 1.0

        counter.inc(5.0)
        assert counter.get() == 6.0

        with pytest.raises(ValueError):
            counter.inc(-1.0)

        counter.reset()
        assert counter.get() == 0.0

    def test_gauge(self) -> None:
        """Test gauge functionality."""
        gauge = Gauge("test_gauge", "Test gauge")

        assert gauge.get() == 0.0

        gauge.set(10.0)
        assert gauge.get() == 10.0

        gauge.inc(5.0)
        assert gauge.get() == 15.0

        gauge.dec(3.0)
        assert gauge.get() == 12.0

    def test_histogram(self) -> None:
        """Test histogram functionality."""
        histogram = Histogram("test_histogram", "Test histogram")

        assert histogram.get_count() == 0
        assert histogram.get_sum() == 0.0

        histogram.observe(0.1)
        histogram.observe(0.2)
        histogram.observe(0.05)

        assert histogram.get_count() == 3
        assert histogram.get_sum() == 0.35
        assert histogram.get_average() == pytest.approx(0.1167, rel=1e-3)

    def test_metric_registry(self) -> None:
        """Test metric registry."""
        registry = MetricRegistry()

        counter = Counter("test_counter", "Test counter")
        gauge = Gauge("test_gauge", "Test gauge")

        registry.register(counter)
        registry.register(gauge)

        # Test retrieval
        assert registry.get_metric("test_counter") == counter
        assert registry.get_metric("test_gauge") == gauge
        assert registry.get_metric("nonexistent") is None

        # Test collection
        metrics = registry.collect_all()
        assert "test_counter" in metrics
        assert "test_gauge" in metrics
        assert metrics["test_counter"] == 0.0
        assert metrics["test_gauge"] == 0.0

        # Test duplicate registration
        with pytest.raises(ValueError):
            registry.register(counter)

        # Test unregistration
        registry.unregister("test_counter")
        assert registry.get_metric("test_counter") is None


class TestDecorators:
    """Test performance monitoring decorators."""

    def test_monitor_performance(self) -> None:
        """Test performance monitoring decorator."""
        call_count = 0

        @monitor_performance("test_function")
        def test_function(x: int, y: int) -> int:
            nonlocal call_count
            call_count += 1
            time.sleep(0.01)
            return x + y

        # Test function execution
        result = test_function(2, 3)
        assert result == 5
        assert call_count == 1

        # Test metrics were created
        registry = get_registry()

        # Check that metrics were registered
        metrics = registry.collect_all()
        duration_metric = None
        calls_metric = None

        for name in metrics:
            if "test_function_duration_seconds" in name:
                duration_metric = name
            elif "test_function_calls_total" in name:
                calls_metric = name

        assert duration_metric is not None
        assert calls_metric is not None

    def test_monitor_execution_time(self) -> None:
        """Test execution time monitoring decorator."""
        call_count = 0

        @monitor_execution_time("timing_test")
        def timing_test() -> None:
            nonlocal call_count
            call_count += 1
            time.sleep(0.005)

        timing_test()

        assert call_count == 1

        # Check histogram was created
        registry = get_registry()
        metrics = registry.collect_all()

        histogram_found = any("timing_test_duration_seconds" in name for name in metrics)
        assert histogram_found

    def test_monitor_calls(self) -> None:
        """Test call monitoring decorator."""
        call_count = 0

        @monitor_calls("calls_test", track_success=True)
        def calls_test(should_fail: bool = False) -> str:
            nonlocal call_count
            call_count += 1
            if should_fail:
                raise ValueError("Test error")
            return "success"

        # Test successful call
        result = calls_test(False)
        assert result == "success"

        # Test failed call
        with pytest.raises(ValueError):
            calls_test(True)

        assert call_count == 2

    def test_timer_context_manager(self) -> None:
        """Test timer context manager."""
        histogram = Histogram("timer_test", "Timer test")

        with timer(histogram):
            time.sleep(0.01)

        assert histogram.get_count() == 1
        assert histogram.get_sum() > 0.01


class TestExchangeHealthMonitor:
    """Test exchange health monitoring."""

    @pytest_asyncio.fixture
    async def health_monitor(self):
        """Create a test health monitor."""
        monitor = ExchangeHealthMonitor("TEST_EXCHANGE")
        yield monitor
        await monitor.stop_monitoring()

    @pytest_asyncio.fixture
    async def health_monitor_unique(self):
        """Create a unique test health monitor for each test."""
        import uuid

        unique_name = f"TEST_EXCHANGE_{uuid.uuid4().hex[:8]}"
        monitor = ExchangeHealthMonitor(unique_name)
        yield monitor
        await monitor.stop_monitoring()

    @pytest.mark.asyncio
    async def test_health_check_registration(
        self, health_monitor_unique: ExchangeHealthMonitor
    ) -> None:
        """Test health check registration."""

        # Create mock check
        async def mock_check() -> bool:
            return True

        from bt_api_py.monitoring.exchange_health import HealthCheck

        check = HealthCheck("test_check", mock_check)

        health_monitor_unique.add_check(check)

        # Run check
        result = await health_monitor_unique.run_check(check)

        assert result.name == "test_check"
        assert result.status.value == "healthy"
        assert result.message == "Check passed"
        assert result.response_time is not None

    @pytest.mark.asyncio
    async def test_health_check_failure(self, health_monitor_unique: ExchangeHealthMonitor) -> None:
        """Test health check failure handling."""

        async def failing_check() -> bool:
            raise ConnectionError("Connection failed")

        from bt_api_py.monitoring.exchange_health import HealthCheck

        check = HealthCheck("failing_check", failing_check, timeout=1.0)

        health_monitor_unique.add_check(check)

        # Run check
        result = await health_monitor_unique.run_check(check)

        assert result.name == "failing_check"
        assert result.status.value == "unhealthy"
        assert "Connection failed" in result.message
        assert isinstance(result.error, ConnectionError)

    @pytest.mark.asyncio
    async def test_overall_status_calculation(
        self, health_monitor_unique: ExchangeHealthMonitor
    ) -> None:
        """Test overall health status calculation."""
        from bt_api_py.monitoring.exchange_health import HealthCheck, HealthStatus

        # Add passing check
        async def passing_check() -> bool:
            return True

        passing_health_check = HealthCheck("passing_check", passing_check)
        health_monitor_unique.add_check(passing_health_check)

        # Add failing check
        async def failing_check() -> bool:
            return False

        failing_health_check = HealthCheck("failing_check", failing_check, critical=False)
        health_monitor_unique.add_check(failing_health_check)

        # Run checks
        await health_monitor_unique.run_all_checks()

        # Should be degraded due to non-critical failure
        status = health_monitor_unique.get_overall_status()
        assert status == HealthStatus.DEGRADED

    def test_health_check_factory(self) -> None:
        """Test health check factory methods."""
        # Test API ping check creation
        mock_api_client = Mock()
        mock_api_client.get = AsyncMock(return_value=Mock(status_code=200))

        ping_check = HealthCheckFactory.api_ping_check(mock_api_client, "/ping")

        assert ping_check.name == "api_ping"
        assert ping_check.timeout == 5.0
        assert ping_check.interval == 30.0
        assert ping_check.critical is True


class TestSystemMetrics:
    """Test system metrics collection."""

    def test_system_metrics_collector_initialization(self) -> None:
        """Test system metrics collector initialization."""
        collector = SystemMetricsCollector(collection_interval=1.0)

        assert collector.collection_interval == 1.0
        assert not collector._running

        # Check metrics were created
        assert collector.cpu_usage_gauge is not None
        assert collector.memory_usage_gauge is not None
        assert collector.process_memory_gauge is not None

    @patch("psutil.cpu_percent")
    @patch("psutil.virtual_memory")
    @patch("psutil.Process")
    def test_system_metrics_collection(
        self, mock_process_class: Mock, mock_memory: Mock, mock_cpu: Mock
    ) -> None:
        """Test system metrics collection."""
        # Setup mocks
        mock_cpu.return_value = 50.0

        mock_memory_instance = Mock()
        mock_memory_instance.percent = 75.0
        mock_memory_instance.used = 8 * 1024 * 1024 * 1024  # 8GB
        mock_memory_instance.available = 4 * 1024 * 1024 * 1024  # 4GB
        mock_memory.return_value = mock_memory_instance

        mock_process = Mock()
        mock_process.cpu_percent.return_value = 25.0
        mock_process.memory_info.return_value = Mock(rss=512 * 1024 * 1024)  # 512MB
        mock_process.num_threads.return_value = 8
        mock_process.num_fds.return_value = 100
        mock_process_class.return_value = mock_process

        collector = SystemMetricsCollector()
        metrics = collector.collect_metrics()

        assert metrics.cpu_percent == 50.0
        assert metrics.memory_percent == 75.0
        assert metrics.memory_mb == pytest.approx(512.0, rel=1e-1)
        assert metrics.thread_count == 8

    def test_business_metrics_collector(self) -> None:
        """Test business metrics collector."""
        collector = BusinessMetricsCollector()

        # Test order recording
        collector.record_order_placed(success=True, latency=0.025)
        collector.record_order_placed(success=False, latency=0.150)

        # Test API request recording
        collector.record_api_request(success=True, latency=0.045)

        # Test market data update
        collector.record_market_data_update()

        # Test connection tracking
        collector.set_active_connections(5)

        # Check metrics were recorded
        assert collector.orders_total.get() == 2
        assert collector.orders_success.get() == 1
        assert collector.orders_failed.get() == 1
        assert collector.api_requests.get() == 1
        assert collector.market_data_updates.get() == 1
        assert collector.active_connections.get() == 5


class TestMetricsCollector:
    """Test main metrics collector."""

    @pytest.mark.asyncio
    async def test_collector_initialization(self) -> None:
        """Test metrics collector initialization."""
        collector = MetricsCollector(collection_interval=1.0)

        assert collector.collection_interval == 1.0
        assert not collector._running
        assert collector.system_collector is not None
        assert collector.business_collector is not None

    @pytest.mark.asyncio
    async def test_custom_metrics(self) -> None:
        """Test custom metrics handling."""
        collector = MetricsCollector()

        # Add custom metrics
        collector.add_custom_metric("custom_counter", 42)
        collector.add_custom_metric("custom_gauge", 3.14)

        # Check metrics are stored
        assert collector._custom_metrics["custom_counter"] == 42
        assert collector._custom_metrics["custom_gauge"] == 3.14

        # Remove custom metric
        collector.remove_custom_metric("custom_counter")
        assert "custom_counter" not in collector._custom_metrics
        assert "custom_gauge" in collector._custom_metrics

    @pytest.mark.asyncio
    async def test_comprehensive_metrics_collection(self) -> None:
        """Test comprehensive metrics collection."""
        collector = MetricsCollector()

        # Add some custom metrics
        collector.add_custom_metric("test_metric", 123)

        # Collect metrics
        metrics = await collector.collect_comprehensive_metrics()

        assert isinstance(metrics, PerformanceMetrics)
        assert metrics.custom_metrics["test_metric"] == 123
        assert metrics.timestamp > 0

    @pytest.mark.asyncio
    async def test_metrics_history(self) -> None:
        """Test metrics history tracking."""
        collector = MetricsCollector()

        # Clear any existing history
        collector.clear_history()

        # Collect some metrics
        metrics1 = await collector.collect_comprehensive_metrics()
        collector._metric_history.append(metrics1)

        await asyncio.sleep(0.1)
        metrics2 = await collector.collect_comprehensive_metrics()
        collector._metric_history.append(metrics2)

        # Check history
        history = collector.get_metric_history()
        assert len(history) == 2
        assert history[0].timestamp < history[1].timestamp

        # Check limited history
        limited_history = collector.get_metric_history(limit=1)
        assert len(limited_history) == 1
        assert limited_history[0] == metrics2


class TestIntegration:
    """Test integration scenarios."""

    @pytest.mark.asyncio
    async def test_trading_bot_monitoring_integration(self) -> None:
        """Test monitoring integration with trading bot scenario."""
        # Create monitoring components
        from bt_api_py.monitoring import get_business_collector, get_logger

        business_metrics = get_business_collector()
        logger = get_logger("test_trading_bot")

        # Simulate trading operations
        business_metrics.record_order_placed(success=True, latency=0.025)
        business_metrics.record_api_request(success=True, latency=0.045)
        business_metrics.set_active_connections(3)

        # Verify metrics were recorded
        assert business_metrics.orders_total.get() == 1
        assert business_metrics.orders_success.get() == 1
        assert business_metrics.api_requests.get() == 1
        assert business_metrics.active_connections.get() == 3

    def test_metric_export_json(self) -> None:
        """Test JSON export of metrics."""
        collector = MetricsCollector()

        # Add custom metrics
        collector.add_custom_metric("test_string", "value")
        collector.add_custom_metric("test_number", 42)

        # Export metrics
        json_data = collector.export_metrics_json()

        # Verify JSON structure
        assert isinstance(json_data, dict)
        assert "timestamp" in json_data
        assert "system" in json_data
        assert "business" in json_data
        assert "custom_metrics" in json_data
        assert json_data["custom_metrics"]["test_string"] == "value"
        assert json_data["custom_metrics"]["test_number"] == 42

        # Verify it's valid JSON
        json_str = json.dumps(json_data)
        parsed = json.loads(json_str)
        assert parsed == json_data


if __name__ == "__main__":
    pytest.main([__file__])
