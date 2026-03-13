"""
Comprehensive tests for the advanced WebSocket system.
"""

import asyncio
import contextlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bt_api_py.websocket.advanced_connection_manager import (
    AdvancedWebSocketConnection,
    ConnectionHealth,
    ConnectionState,
    DeadLetterQueue,
    ErrorCategory,
    IntelligentCircuitBreaker,
    WebSocketConfig,
    WebSocketMetrics,
)
from bt_api_py.websocket.advanced_websocket_manager import (
    ConnectionWrapper,
    LoadBalancer,
)
from bt_api_py.websocket.exchange_adapters import (
    BinanceWebSocketAdapter,
    ExchangeType,
    OKXWebSocketAdapter,
    WebSocketAdapterFactory,
)
from bt_api_py.websocket.monitoring import (
    AlertManager,
    AlertSeverity,
    MetricsCollector,
    PerformanceAlert,
    WebSocketBenchmark,
)


class TestWebSocketConfig:
    """Test WebSocket configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")

        assert config.url == "wss://test.com"
        assert config.exchange_name == "TEST"
        assert config.max_connections == 5
        assert config.heartbeat_interval == 30.0
        assert config.compression is True
        assert config.reconnect_enabled is True

    def test_custom_config(self):
        """Test custom configuration values."""
        config = WebSocketConfig(
            url="wss://custom.com",
            exchange_name="CUSTOM",
            max_connections=10,
            heartbeat_interval=60.0,
            compression=False,
        )

        assert config.max_connections == 10
        assert config.heartbeat_interval == 60.0
        assert config.compression is False

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"url": "https://test.com", "exchange_name": "TEST"}, "url"),
            ({"url": "wss://test.com", "exchange_name": ""}, "exchange_name"),
            ({"url": "wss://test.com", "exchange_name": "TEST", "max_connections": 0}, "max_connections"),
            (
                {"url": "wss://test.com", "exchange_name": "TEST", "heartbeat_interval": 0},
                "heartbeat_interval",
            ),
        ],
    )
    def test_invalid_config_values_raise_value_error(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            WebSocketConfig(**kwargs)


class TestWebSocketMetrics:
    """Test WebSocket metrics collection."""

    def test_metrics_initialization(self):
        """Test metrics initialization."""
        metrics = WebSocketMetrics("test_conn", "TEST_EXCHANGE")

        assert metrics.connection_id == "test_conn"
        assert metrics.exchange_name == "TEST_EXCHANGE"
        assert metrics.connections_established == 0
        assert metrics.messages_sent == 0
        assert metrics.active_subscriptions == 0

    def test_latency_recording(self):
        """Test latency recording and calculation."""
        metrics = WebSocketMetrics("test_conn", "TEST_EXCHANGE")

        # Record some latency samples
        metrics.record_latency(10.0)
        metrics.record_latency(20.0)
        metrics.record_latency(30.0)

        assert metrics.get_avg_latency() == 20.0
        assert len(metrics.message_latency_samples) == 3

    def test_error_recording(self):
        """Test error recording and rate calculation."""
        metrics = WebSocketMetrics("test_conn", "TEST_EXCHANGE")

        # Record some errors
        time.time()
        metrics.record_error(ErrorCategory.NETWORK)
        metrics.record_error(ErrorCategory.PROTOCOL)

        assert metrics.errors_by_category[ErrorCategory.NETWORK] == 1
        assert metrics.errors_by_category[ErrorCategory.PROTOCOL] == 1
        assert len(metrics.error_rate_samples) == 2


class TestConnectionHealth:
    """Test connection health monitoring."""

    def test_health_initialization(self):
        """Test health initialization."""
        health = ConnectionHealth()

        assert health.is_healthy is True
        assert health.health_score == 100.0
        assert health.consecutive_failures == 0

    def test_health_update(self):
        """Test health score updates."""
        health = ConnectionHealth()

        # Update with good metrics
        health.update_health(latency_ms=50.0, error_rate=2.0, uptime_ratio=0.98)

        assert health.health_score > 80.0
        assert health.is_healthy is True

        # Update with bad metrics
        health.update_health(latency_ms=2000.0, error_rate=50.0, uptime_ratio=0.5, has_error=True)

        assert health.health_score < 50.0
        assert health.consecutive_failures == 1


class TestDeadLetterQueue:
    """Test dead letter queue functionality."""

    @pytest.mark.asyncio
    async def test_dlq_add_get_message(self):
        """Test adding and getting messages from DLQ."""
        dlq = DeadLetterQueue(max_size=10)

        message = {"test": "data"}
        error = Exception("Test error")

        await dlq.add_message(message, error)

        retrieved = await dlq.get_message()

        assert retrieved is not None
        assert retrieved["message"] == message
        assert retrieved["error"] == "Test error"
        assert retrieved["retry_count"] == 0

    @pytest.mark.asyncio
    async def test_dlq_overflow(self):
        """Test DLQ overflow behavior."""
        dlq = DeadLetterQueue(max_size=2)

        # Add messages beyond capacity
        for i in range(5):
            await dlq.add_message({"msg": i}, Exception(f"Error {i}"))

        assert dlq.size() == 2

        # Should keep most recent messages
        retrieved = await dlq.get_message()
        assert retrieved["message"]["msg"] == 3  # Last message


class TestIntelligentCircuitBreaker:
    """Test intelligent circuit breaker."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_states(self):
        """Test circuit breaker state transitions."""
        cb = IntelligentCircuitBreaker(failure_threshold=3, recovery_timeout=1.0)

        # Initially closed
        assert cb.get_state() == "CLOSED"

        # Record failures
        for _i in range(3):
            with contextlib.suppress(Exception):
                await cb.call(lambda: 1 / 0)  # Will raise exception

        # Should be open now
        assert cb.get_state() == "OPEN"

        # Wait for recovery timeout
        await asyncio.sleep(1.1)

        # Next call should put it in half-open state
        with contextlib.suppress(Exception):
            await cb.call(lambda: "success")

        assert cb.get_state() in ["HALF_OPEN", "CLOSED"]

    @pytest.mark.asyncio
    async def test_circuit_breaker_success(self):
        """Test successful calls through circuit breaker."""
        cb = IntelligentCircuitBreaker()

        result = await cb.call(lambda: "test_result")
        assert result == "test_result"
        assert cb.get_state() == "CLOSED"


class TestBinanceWebSocketAdapter:
    """Test Binance WebSocket adapter."""

    def test_adapter_initialization(self):
        """Test adapter initialization."""
        adapter = BinanceWebSocketAdapter()

        assert adapter.exchange_name == "BINANCE"
        assert adapter.exchange_type == ExchangeType.SPOT

    def test_get_endpoints(self):
        """Test endpoint generation."""
        adapter = BinanceWebSocketAdapter()
        endpoints = adapter.get_endpoints("wss://stream.binance.com")

        assert len(endpoints) > 1
        assert "wss://stream.binance.com" in endpoints[0]

    def test_format_subscription_message(self):
        """Test subscription message formatting."""
        adapter = BinanceWebSocketAdapter()

        message = adapter.format_subscription_message("sub1", "ticker", "BTCUSDT", {})

        assert message["method"] == "SUBSCRIBE"
        assert "btcusdt@ticker" in message["params"]
        assert "id" in message

    def test_format_unsubscription_message(self):
        """Test unsubscription message formatting."""
        adapter = BinanceWebSocketAdapter()

        message = adapter.format_unsubscription_message("sub1", "ticker", "BTCUSDT")

        assert message["method"] == "UNSUBSCRIBE"
        assert "btcusdt@ticker" in message["params"]

    def test_extract_topic_symbol(self):
        """Test topic/symbol extraction."""
        adapter = BinanceWebSocketAdapter()

        message = {"stream": "btcusdt@ticker", "data": {"c": "50000.00"}}

        topic, symbol = adapter.extract_topic_symbol(message)

        assert topic == "ticker"
        assert symbol == "BTCUSDT"

    def test_normalize_message(self):
        """Test message normalization."""
        adapter = BinanceWebSocketAdapter()

        message = {
            "stream": "btcusdt@ticker",
            "data": {"c": "50000.00", "v": "1000.5", "h": "51000.00", "l": "49000.00", "P": "2.5"},
        }

        normalized = adapter.normalize_message(message)

        assert normalized["exchange"] == "BINANCE"
        assert normalized["symbol"] == "BTCUSDT"
        assert normalized["topic"] == "ticker"
        assert normalized["last_price"] == 50000.00
        assert normalized["volume"] == 1000.5
        assert normalized["change_24h"] == 2.5


class TestOKXWebSocketAdapter:
    """Test OKX WebSocket adapter."""

    def test_adapter_initialization(self):
        """Test adapter initialization."""
        adapter = OKXWebSocketAdapter()

        assert adapter.exchange_name == "OKX"
        assert adapter.exchange_type == ExchangeType.SPOT

    def test_get_endpoints(self):
        """Test endpoint generation."""
        adapter = OKXWebSocketAdapter()
        endpoints = adapter.get_endpoints("wss://ws.okx.com")

        assert len(endpoints) > 1
        assert "wss://ws.okx.com" in endpoints[0]

    def test_format_subscription_message(self):
        """Test subscription message formatting."""
        adapter = OKXWebSocketAdapter()

        message = adapter.format_subscription_message("sub1", "ticker", "BTC-USDT", {})

        assert message["op"] == "subscribe"
        assert len(message["args"]) == 1
        assert message["args"][0]["channel"] == "tickers"
        assert message["args"][0]["instId"] == "BTC-USDT"

    def test_extract_topic_symbol(self):
        """Test topic/symbol extraction."""
        adapter = OKXWebSocketAdapter()

        message = {
            "arg": {"channel": "tickers", "instId": "BTC-USDT"},
            "data": [{"last": "50000", "vol24h": "1000"}],
        }

        topic, symbol = adapter.extract_topic_symbol(message)

        assert topic == "ticker"
        assert symbol == "BTC-USDT"

    def test_normalize_message(self):
        """Test message normalization."""
        adapter = OKXWebSocketAdapter()

        message = {
            "arg": {"channel": "tickers", "instId": "BTC-USDT"},
            "data": [
                {
                    "last": "50000",
                    "vol24h": "1000",
                    "high24h": "51000",
                    "low24h": "49000",
                    "chg": "2.5",
                }
            ],
        }

        normalized = adapter.normalize_message(message)

        assert normalized["exchange"] == "OKX"
        assert normalized["symbol"] == "BTC-USDT"
        assert normalized["topic"] == "ticker"
        assert normalized["last_price"] == 50000.0
        assert normalized["volume"] == 1000.0
        assert normalized["change_24h"] == 2.5


class TestWebSocketAdapterFactory:
    """Test WebSocket adapter factory."""

    def test_create_binance_adapter(self):
        """Test creating Binance adapter."""
        adapter = WebSocketAdapterFactory.create_adapter("BINANCE")

        assert isinstance(adapter, BinanceWebSocketAdapter)
        assert adapter.exchange_name == "BINANCE"

    def test_create_okx_adapter(self):
        """Test creating OKX adapter."""
        adapter = WebSocketAdapterFactory.create_adapter("OKX")

        assert isinstance(adapter, OKXWebSocketAdapter)
        assert adapter.exchange_name == "OKX"

    def test_create_generic_adapter(self):
        """Test creating generic adapter for unknown exchange."""
        adapter = WebSocketAdapterFactory.create_adapter("UNKNOWN_EXCHANGE")

        assert adapter.exchange_name == "UNKNOWN_EXCHANGE"


class TestLoadBalancer:
    """Test load balancer functionality."""

    def test_round_robin_selection(self):
        """Test round-robin connection selection."""
        balancer = LoadBalancer("round_robin")

        connections = [
            ConnectionWrapper(MagicMock()),
            ConnectionWrapper(MagicMock()),
            ConnectionWrapper(MagicMock()),
        ]

        # Test round-robin selection
        selected1 = balancer.select_connection(connections)
        selected2 = balancer.select_connection(connections)
        selected3 = balancer.select_connection(connections)
        selected4 = balancer.select_connection(connections)

        assert selected1 is not selected2
        assert selected2 is not selected3
        assert selected4 is selected1  # Should cycle back after 3

    def test_least_connections_selection(self):
        """Test least connections selection."""
        balancer = LoadBalancer("least_connections")

        connections = [
            ConnectionWrapper(MagicMock()),
            ConnectionWrapper(MagicMock()),
            ConnectionWrapper(MagicMock()),
        ]

        connections[0].usage_count = 5
        connections[1].usage_count = 2
        connections[2].usage_count = 10

        selected = balancer.select_connection(connections)

        assert selected is connections[1]  # Should select least used

    def test_empty_connections(self):
        """Test behavior with empty connections list."""
        balancer = LoadBalancer("round_robin")

        selected = balancer.select_connection([])

        assert selected is None


class TestMetricsCollector:
    """Test metrics collector functionality."""

    @pytest.fixture
    def metrics_collector(self):
        return MetricsCollector()

    def test_counter_increment(self, metrics_collector):
        """Test counter increment."""
        metrics_collector.increment_counter("test_counter", tags={"env": "test"})
        metrics_collector.increment_counter("test_counter", tags={"env": "test"}, value=5)

        points = metrics_collector.get_metric("test_counter", {"env": "test"})

        assert len(points) == 2
        assert points[-1].value == 6.0

    def test_gauge_set(self, metrics_collector):
        """Test gauge setting."""
        metrics_collector.set_gauge("test_gauge", 42.5, tags={"env": "test"})

        points = metrics_collector.get_metric("test_gauge", {"env": "test"})

        assert len(points) == 1
        assert points[0].value == 42.5

    def test_histogram_record(self, metrics_collector):
        """Test histogram recording."""
        for i in range(100):
            metrics_collector.record_histogram("test_histogram", i + 1, tags={"env": "test"})

        aggregated = metrics_collector.get_aggregated_metrics("test_histogram", {"env": "test"})

        assert aggregated["count"] == 100
        assert aggregated["mean"] == 50.5
        assert aggregated["min"] == 1.0
        assert aggregated["max"] == 100.0

    def test_cleanup(self, metrics_collector):
        """Test metrics cleanup."""
        # Add some old metrics
        with patch("time.time", return_value=1000.0):
            metrics_collector.set_gauge("old_metric", 1.0)

        # Add new metrics
        with patch("time.time", return_value=2000.0):
            metrics_collector.set_gauge("new_metric", 2.0)

        # Cleanup with retention period of 500 seconds
        # Must patch time.time so cutoff = 2000 - 500 = 1500 (old_metric@1000 < 1500, cleaned)
        metrics_collector.retention_period = 500.0
        with patch("time.time", return_value=2000.0):
            metrics_collector._cleanup_old_metrics()

        old_points = metrics_collector.get_metric("old_metric")
        new_points = metrics_collector.get_metric("new_metric")

        assert len(old_points) == 0  # Should be cleaned up
        assert len(new_points) == 1  # Should remain


@pytest.mark.asyncio
class TestAlertManager:
    """Test alert manager functionality."""

    @pytest.fixture
    def alert_manager(self):
        metrics_collector = MetricsCollector()
        return AlertManager(metrics_collector)

    async def test_add_alert(self, alert_manager):
        """Test adding alerts."""
        alert = PerformanceAlert(
            alert_id="test_alert",
            name="Test Alert",
            severity=AlertSeverity.WARNING,
            condition="test_condition",
            threshold=50.0,
            description="Test description",
        )

        alert_manager.add_alert(alert)

        assert "test_alert" in alert_manager._alerts
        assert alert_manager._alerts["test_alert"] == alert

    async def test_remove_alert(self, alert_manager):
        """Test removing alerts."""
        alert = PerformanceAlert(
            alert_id="test_alert",
            name="Test Alert",
            severity=AlertSeverity.WARNING,
            condition="test_condition",
            threshold=50.0,
            description="Test description",
        )

        alert_manager.add_alert(alert)
        alert_manager.remove_alert("test_alert")

        assert "test_alert" not in alert_manager._alerts
        assert "test_alert" not in alert_manager._alert_states

    async def test_alert_triggering(self, alert_manager):
        """Test alert triggering logic."""
        alert = PerformanceAlert(
            alert_id="test_alert",
            name="Test Alert",
            severity=AlertSeverity.WARNING,
            condition="test_condition",
            threshold=50.0,
            description="Test description",
        )

        # Mock condition evaluation to return triggered value
        with patch.object(alert_manager, "_evaluate_condition", return_value=60.0):
            alert_manager.add_alert(alert)
            await alert_manager._check_alert(alert)

        assert alert.resolved is False
        assert alert.triggered_count == 1
        assert alert.last_triggered is not None


@pytest.mark.asyncio
class TestWebSocketBenchmark:
    """Test WebSocket benchmark functionality."""

    @pytest.fixture
    def ws_benchmark(self):
        metrics_collector = MetricsCollector()
        return WebSocketBenchmark(metrics_collector)

    async def test_latency_benchmark(self, ws_benchmark):
        """Test latency benchmark."""
        # Mock WebSocket manager
        mock_manager = MagicMock()

        result = await ws_benchmark.run_latency_benchmark(mock_manager, duration=0.1)

        assert result.benchmark_name == "latency_test"
        assert result.success is True
        assert "avg_latency_ms" in result.metrics
        assert result.duration_ms >= 100  # Should run for at least 100ms

    async def test_throughput_benchmark(self, ws_benchmark):
        """Test throughput benchmark."""
        mock_manager = MagicMock()

        result = await ws_benchmark.run_throughput_benchmark(mock_manager, duration=0.1)

        assert result.benchmark_name == "throughput_test"
        assert result.success is True
        assert "messages_per_second_sent" in result.metrics
        assert "messages_per_second_received" in result.metrics

    async def test_memory_benchmark(self, ws_benchmark):
        """Test memory benchmark."""
        mock_manager = MagicMock()

        result = await ws_benchmark.run_memory_benchmark(mock_manager, duration=0.1)

        assert result.benchmark_name == "memory_test"
        assert result.success is True
        assert "avg_memory_mb" in result.metrics
        assert "peak_memory_mb" in result.metrics


# Integration tests
@pytest.mark.asyncio
@pytest.mark.integration
class TestWebSocketIntegration:
    """Integration tests for WebSocket system."""

    async def test_end_to_end_subscription(self):
        """Test end-to-end subscription flow."""
        # This would be a more comprehensive integration test
        # that tests the entire WebSocket system

        # Mock the actual WebSocket connection
        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws

            # Create configuration
            config = WebSocketConfig(url="wss://test.com", exchange_name="TEST", max_connections=1)

            # Create connection
            connection = AdvancedWebSocketConnection(config, "test_conn")

            # Test connection
            await connection.connect()
            assert connection.get_state() == ConnectionState.CONNECTED

            # Test subscription
            callback = AsyncMock()
            await connection.subscribe("sub1", "ticker", "BTCUSDT", callback=callback)

            assert "sub1" in connection._subscriptions

            # Test disconnection
            await connection.disconnect()
            assert connection.get_state() == ConnectionState.DISCONNECTED
            assert connection._dlq is not None
            assert connection._dlq._processing_task is None or connection._dlq._processing_task.done()

    async def test_pool_load_balancing(self):
        """Test connection pool load balancing."""
        # This would test the actual pool behavior

    async def test_monitoring_integration(self):
        """Test monitoring system integration."""
        # This would test the monitoring components working together
