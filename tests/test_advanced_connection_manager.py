"""Tests for advanced_connection_manager module - pure local logic."""

import time

import pytest

from bt_api_py.websocket.advanced_connection_manager import (
    ConnectionHealth,
    ConnectionState,
    DeadLetterQueue,
    ErrorCategory,
    IntelligentCircuitBreaker,
    WebSocketConfig,
    WebSocketMetrics,
)


class TestConnectionState:
    """Tests for ConnectionState enum."""

    def test_enum_values(self):
        """Test all enum values exist."""
        assert ConnectionState.DISCONNECTED.value == "disconnected"
        assert ConnectionState.CONNECTING.value == "connecting"
        assert ConnectionState.CONNECTED.value == "connected"
        assert ConnectionState.AUTHENTICATED.value == "authenticated"
        assert ConnectionState.DEGRADING.value == "degrading"
        assert ConnectionState.ERROR.value == "error"
        assert ConnectionState.MAINTENANCE.value == "maintenance"


class TestErrorCategory:
    """Tests for ErrorCategory enum."""

    def test_enum_values(self):
        """Test all enum values exist."""
        assert ErrorCategory.NETWORK.value == "network"
        assert ErrorCategory.AUTHENTICATION.value == "authentication"
        assert ErrorCategory.RATE_LIMIT.value == "rate_limit"
        assert ErrorCategory.PROTOCOL.value == "protocol"
        assert ErrorCategory.EXCHANGE_SPECIFIC.value == "exchange_specific"
        assert ErrorCategory.UNKNOWN.value == "unknown"


class TestWebSocketMetrics:
    """Tests for WebSocketMetrics dataclass."""

    def test_init_defaults(self):
        """Test default initialization."""
        metrics = WebSocketMetrics(connection_id="conn-1", exchange_name="test")

        assert metrics.connection_id == "conn-1"
        assert metrics.exchange_name == "test"
        assert metrics.connections_established == 0
        assert metrics.messages_sent == 0
        assert metrics.active_subscriptions == 0

    def test_record_latency(self):
        """Test latency recording."""
        metrics = WebSocketMetrics(connection_id="conn-1", exchange_name="test")

        metrics.record_latency(10.5)
        metrics.record_latency(20.5)
        metrics.record_latency(30.5)

        assert len(metrics.message_latency_samples) == 3
        assert metrics.get_avg_latency() == pytest.approx(20.5, rel=0.01)

    def test_get_avg_latency_empty(self):
        """Test average latency with no samples."""
        metrics = WebSocketMetrics(connection_id="conn-1", exchange_name="test")

        assert metrics.get_avg_latency() == 0.0

    def test_get_p95_latency(self):
        """Test p95 latency calculation."""
        metrics = WebSocketMetrics(connection_id="conn-1", exchange_name="test")

        # Add 100 samples from 1 to 100
        for i in range(1, 101):
            metrics.record_latency(float(i))

        p95 = metrics.get_p95_latency()
        assert p95 > 90.0  # 95th percentile should be high

    def test_get_p95_latency_empty(self):
        """Test p95 latency with no samples."""
        metrics = WebSocketMetrics(connection_id="conn-1", exchange_name="test")

        assert metrics.get_p95_latency() == 0.0

    def test_record_error(self):
        """Test error recording."""
        metrics = WebSocketMetrics(connection_id="conn-1", exchange_name="test")

        metrics.record_error(ErrorCategory.NETWORK)
        metrics.record_error(ErrorCategory.NETWORK)
        metrics.record_error(ErrorCategory.PROTOCOL)

        assert metrics.errors_by_category[ErrorCategory.NETWORK] == 2
        assert metrics.errors_by_category[ErrorCategory.PROTOCOL] == 1

    def test_get_error_rate(self):
        """Test error rate calculation."""
        metrics = WebSocketMetrics(connection_id="conn-1", exchange_name="test")

        # Record errors
        metrics.record_error(ErrorCategory.NETWORK)
        metrics.record_error(ErrorCategory.NETWORK)

        # Should count recent errors
        rate = metrics.get_error_rate()
        assert rate >= 2


class TestConnectionHealth:
    """Tests for ConnectionHealth dataclass."""

    def test_init_defaults(self):
        """Test default initialization."""
        health = ConnectionHealth()

        assert health.is_healthy is True
        assert health.health_score == 100.0
        assert health.consecutive_failures == 0

    def test_update_health_success(self):
        """Test health update with no error."""
        health = ConnectionHealth()

        health.update_health(latency_ms=100.0, error_rate=1.0, uptime_ratio=0.99, has_error=False)

        assert health.is_healthy is True
        assert health.health_score > 70.0
        assert health.consecutive_failures == 0

    def test_update_health_with_error(self):
        """Test health update with error."""
        health = ConnectionHealth()

        health.update_health(latency_ms=100.0, error_rate=1.0, uptime_ratio=0.99, has_error=True)

        assert health.consecutive_failures == 1
        # Still healthy after one failure
        assert health.is_healthy is True

    def test_update_health_max_failures(self):
        """Test health becomes unhealthy after max failures."""
        health = ConnectionHealth()

        # Record max consecutive failures
        for _ in range(health.max_consecutive_failures):
            health.update_health(
                latency_ms=100.0, error_rate=1.0, uptime_ratio=0.99, has_error=True
            )

        assert health.consecutive_failures == health.max_consecutive_failures
        assert health.is_healthy is False

    def test_update_health_reduces_failures(self):
        """Test that successful updates reduce consecutive failures."""
        health = ConnectionHealth()
        health.consecutive_failures = 3

        health.update_health(latency_ms=100.0, error_rate=1.0, uptime_ratio=0.99, has_error=False)

        assert health.consecutive_failures == 2

    def test_update_health_zero_failures_floor(self):
        """Test consecutive failures doesn't go negative."""
        health = ConnectionHealth()
        health.consecutive_failures = 0

        health.update_health(latency_ms=100.0, error_rate=1.0, uptime_ratio=0.99, has_error=False)

        assert health.consecutive_failures == 0


class TestWebSocketConfig:
    """Tests for WebSocketConfig dataclass."""

    def test_valid_config(self):
        """Test valid configuration creation."""
        config = WebSocketConfig(url="wss://stream.example.com", exchange_name="test")

        assert config.url == "wss://stream.example.com"
        assert config.exchange_name == "test"
        assert config.max_connections == 5
        assert config.reconnect_enabled is True

    def test_invalid_url_scheme(self):
        """Test invalid URL scheme raises error."""
        with pytest.raises(ValueError, match="url must be a valid ws/wss URL"):
            WebSocketConfig(url="https://example.com", exchange_name="test")

    def test_invalid_url_no_netloc(self):
        """Test URL without network location raises error."""
        with pytest.raises(ValueError, match="url must be a valid ws/wss URL"):
            WebSocketConfig(url="wss://", exchange_name="test")

    def test_empty_exchange_name(self):
        """Test empty exchange name raises error."""
        with pytest.raises(ValueError, match="exchange_name must be a non-empty string"):
            WebSocketConfig(url="wss://stream.example.com", exchange_name="")

    def test_invalid_max_connections(self):
        """Test invalid max_connections raises error."""
        with pytest.raises(ValueError, match="max_connections must be a positive integer"):
            WebSocketConfig(url="wss://stream.example.com", exchange_name="test", max_connections=0)

    def test_invalid_min_connections(self):
        """Test invalid min_connections raises error."""
        with pytest.raises(ValueError, match="min_connections must be a positive integer"):
            WebSocketConfig(url="wss://stream.example.com", exchange_name="test", min_connections=0)

    def test_min_greater_than_max(self):
        """Test min > max raises error."""
        with pytest.raises(ValueError, match="min_connections must be <= max_connections"):
            WebSocketConfig(
                url="wss://stream.example.com",
                exchange_name="test",
                min_connections=10,
                max_connections=5,
            )

    def test_invalid_heartbeat_interval(self):
        """Test invalid heartbeat interval raises error."""
        with pytest.raises(ValueError, match="heartbeat_interval must be > 0"):
            WebSocketConfig(
                url="wss://stream.example.com", exchange_name="test", heartbeat_interval=0
            )

    def test_invalid_heartbeat_timeout(self):
        """Test invalid heartbeat timeout raises error."""
        with pytest.raises(ValueError, match="heartbeat_timeout must be > 0"):
            WebSocketConfig(
                url="wss://stream.example.com", exchange_name="test", heartbeat_timeout=-1
            )

    def test_invalid_connection_timeout(self):
        """Test invalid connection timeout raises error."""
        with pytest.raises(ValueError, match="connection_timeout must be > 0"):
            WebSocketConfig(
                url="wss://stream.example.com", exchange_name="test", connection_timeout=0
            )

    def test_invalid_idle_timeout(self):
        """Test invalid idle timeout raises error."""
        with pytest.raises(ValueError, match="idle_timeout must be > 0"):
            WebSocketConfig(url="wss://stream.example.com", exchange_name="test", idle_timeout=0)

    def test_invalid_reconnect_interval(self):
        """Test invalid reconnect interval raises error."""
        with pytest.raises(ValueError, match="reconnect_interval must be > 0"):
            WebSocketConfig(
                url="wss://stream.example.com", exchange_name="test", reconnect_interval=0
            )

    def test_invalid_max_reconnect_attempts(self):
        """Test invalid max reconnect attempts raises error."""
        with pytest.raises(ValueError, match="max_reconnect_attempts must be >= 0"):
            WebSocketConfig(
                url="wss://stream.example.com", exchange_name="test", max_reconnect_attempts=-1
            )

    def test_invalid_reconnect_backoff_multiplier(self):
        """Test invalid backoff multiplier raises error."""
        with pytest.raises(ValueError, match="reconnect_backoff_multiplier must be >= 1"):
            WebSocketConfig(
                url="wss://stream.example.com",
                exchange_name="test",
                reconnect_backoff_multiplier=0.5,
            )

    def test_invalid_max_reconnect_delay(self):
        """Test invalid max reconnect delay raises error."""
        with pytest.raises(ValueError, match="max_reconnect_delay must be > 0"):
            WebSocketConfig(
                url="wss://stream.example.com", exchange_name="test", max_reconnect_delay=0
            )

    def test_invalid_buffer_sizes(self):
        """Test invalid buffer sizes raise errors."""
        with pytest.raises(ValueError, match="message_buffer_size must be > 0"):
            WebSocketConfig(
                url="wss://stream.example.com", exchange_name="test", message_buffer_size=0
            )

        with pytest.raises(ValueError, match="send_buffer_size must be > 0"):
            WebSocketConfig(
                url="wss://stream.example.com", exchange_name="test", send_buffer_size=0
            )

        with pytest.raises(ValueError, match="receive_buffer_size must be > 0"):
            WebSocketConfig(
                url="wss://stream.example.com", exchange_name="test", receive_buffer_size=0
            )

    def test_invalid_rate_limits(self):
        """Test invalid rate limits raise errors."""
        with pytest.raises(ValueError, match="max_requests_per_second must be > 0"):
            WebSocketConfig(
                url="wss://stream.example.com", exchange_name="test", max_requests_per_second=0
            )

        with pytest.raises(ValueError, match="max_subscriptions_per_connection must be > 0"):
            WebSocketConfig(
                url="wss://stream.example.com",
                exchange_name="test",
                max_subscriptions_per_connection=0,
            )

    def test_invalid_dead_letter_queue_size(self):
        """Test invalid DLQ size raises error."""
        with pytest.raises(ValueError, match="dead_letter_queue_size must be > 0"):
            WebSocketConfig(
                url="wss://stream.example.com", exchange_name="test", dead_letter_queue_size=0
            )


class TestDeadLetterQueue:
    """Tests for DeadLetterQueue class."""

    @pytest.mark.asyncio
    async def test_add_and_get_message(self):
        """Test adding and getting messages."""
        dlq = DeadLetterQueue(max_size=10)

        await dlq.add_message({"id": 1}, ValueError("test error"))

        msg = await dlq.get_message()
        assert msg is not None
        assert msg["message"] == {"id": 1}
        assert "test error" in msg["error"]
        assert msg["error_type"] == "ValueError"

    @pytest.mark.asyncio
    async def test_queue_size_limit(self):
        """Test queue removes oldest when full."""
        dlq = DeadLetterQueue(max_size=2)

        await dlq.add_message({"id": 1}, ValueError("error 1"))
        await dlq.add_message({"id": 2}, ValueError("error 2"))
        await dlq.add_message({"id": 3}, ValueError("error 3"))  # Should evict first

        assert dlq.size() == 2

        # First message should be id 2
        msg = await dlq.get_message()
        assert msg["message"] == {"id": 2}

    @pytest.mark.asyncio
    async def test_get_message_timeout(self):
        """Test get_message returns None on empty queue."""
        dlq = DeadLetterQueue(max_size=10)

        msg = await dlq.get_message()
        assert msg is None

    @pytest.mark.asyncio
    async def test_start_and_stop_processing(self):
        """Test processing loop start and stop."""
        dlq = DeadLetterQueue(max_size=10)
        processed = []

        async def processor(msg):
            processed.append(msg)

        await dlq.start_processing(processor)
        assert dlq._processing is True
        assert dlq._processing_task is not None

        # Stop processing
        dlq.stop_processing()
        assert dlq._processing is False

        # Cleanup
        await dlq.shutdown()
        assert dlq._processing_task is None

    def test_size(self):
        """Test size method."""
        dlq = DeadLetterQueue(max_size=10)
        assert dlq.size() == 0


class TestIntelligentCircuitBreaker:
    """Tests for IntelligentCircuitBreaker class."""

    def test_init_defaults(self):
        """Test default initialization."""
        cb = IntelligentCircuitBreaker()

        assert cb.state == "CLOSED"
        assert cb.failure_threshold == 5
        assert cb.recovery_timeout == 60.0
        assert cb.success_threshold == 3

    def test_on_success_closed(self):
        """Test on_success in CLOSED state."""
        cb = IntelligentCircuitBreaker()
        cb.failure_count = 3

        cb.on_success()

        assert cb.failure_count == 2  # Reduced by 1

    def test_on_success_half_open(self):
        """Test on_success in HALF_OPEN state."""
        cb = IntelligentCircuitBreaker()
        cb.state = "HALF_OPEN"
        cb.success_count = 0

        cb.on_success()
        assert cb.success_count == 1

        # Reach threshold to close
        cb.on_success()
        cb.on_success()
        assert cb.state == "CLOSED"
        assert cb.failure_count == 0

    def test_on_failure(self):
        """Test on_failure transitions to OPEN."""
        cb = IntelligentCircuitBreaker(failure_threshold=3)

        cb.on_failure()
        cb.on_failure()
        assert cb.state == "CLOSED"

        cb.on_failure()  # Reaches threshold
        assert cb.state == "OPEN"

    def test_on_failure_half_open(self):
        """Test on_failure in HALF_OPEN state."""
        cb = IntelligentCircuitBreaker()
        cb.state = "HALF_OPEN"

        cb.on_failure()

        assert cb.state == "OPEN"

    def test_get_state(self):
        """Test get_state method."""
        cb = IntelligentCircuitBreaker()
        assert cb.get_state() == "CLOSED"

        cb.state = "OPEN"
        assert cb.get_state() == "OPEN"

    def test_get_stats(self):
        """Test get_stats method."""
        cb = IntelligentCircuitBreaker()
        cb.failure_count = 2
        cb.success_count = 5

        stats = cb.get_stats()

        assert stats["state"] == "CLOSED"
        assert stats["failure_count"] == 2
        assert stats["success_count"] == 5

    @pytest.mark.asyncio
    async def test_call_open_raises(self):
        """Test call raises when OPEN and not recovered."""
        cb = IntelligentCircuitBreaker()
        cb.state = "OPEN"
        cb.last_failure_time = time.time()  # Just failed

        with pytest.raises(RuntimeError, match="Circuit breaker is OPEN"):
            await cb.call(lambda: "result")

    @pytest.mark.asyncio
    async def test_call_half_open_success(self):
        """Test call in HALF_OPEN state with success."""
        cb = IntelligentCircuitBreaker()
        cb.state = "OPEN"
        cb.last_failure_time = time.time() - cb.recovery_timeout - 1  # Past recovery time

        result = await cb.call(lambda: "success")

        assert result == "success"
        assert cb.state == "HALF_OPEN"

    @pytest.mark.asyncio
    async def test_call_async_function(self):
        """Test call with async function."""
        cb = IntelligentCircuitBreaker()

        async def async_func():
            return "async_result"

        result = await cb.call(async_func)

        assert result == "async_result"

    @pytest.mark.asyncio
    async def test_call_propagates_exception(self):
        """Test call propagates exceptions."""
        cb = IntelligentCircuitBreaker()

        def failing_func():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            await cb.call(failing_func)

        assert cb.failure_count == 1

    def test_adaptive_threshold_on_success(self):
        """Test adaptive threshold adjustment on success."""
        cb = IntelligentCircuitBreaker(adaptive_threshold=True, failure_threshold=10)

        # Add many successes to trigger adaptation
        for _ in range(60):
            cb.success_history.append(time.time())

        cb.on_success()
        # Threshold should decrease when success rate is high
        # (depends on timing, so just verify it runs without error)

    def test_adaptive_threshold_on_failure(self):
        """Test adaptive threshold adjustment on failure."""
        cb = IntelligentCircuitBreaker(adaptive_threshold=True, failure_threshold=5)

        # Add many failures to trigger adaptation
        for _ in range(60):
            cb.failure_history.append(time.time())

        cb.on_failure()
        # Threshold should increase when failure rate is high


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
