"""
Comprehensive tests for the advanced WebSocket system.
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import random
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bt_api_py.websocket.advanced_websocket_manager as advanced_ws_manager_module
from bt_api_py.exceptions import AuthenticationError, RateLimitError, WebSocketError
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
    AdvancedWebSocketManager,
    ConnectionWrapper,
    LoadBalancer,
    PoolConfiguration,
)
from bt_api_py.websocket.exchange_adapters import (
    AuthenticationType,
    BinanceWebSocketAdapter,
    ExchangeCredentials,
    ExchangeType,
    GenericWebSocketAdapter,
    OKXWebSocketAdapter,
    WebSocketAdapterFactory,
)
from bt_api_py.websocket.monitoring import (
    AlertManager,
    AlertSeverity,
    BenchmarkResult,
    MetricsCollector,
    PerformanceAlert,
    WebSocketBenchmark,
)


class _DummyWebSocket:
    def __init__(self) -> None:
        self.closed = False
        self.sent: list[str] = []
        self.ping_count = 0

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True

    async def ping(self) -> None:
        self.ping_count += 1


class _DummyEventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def publish(self, event_type: str, data: Any) -> None:
        self.events.append((event_type, data))

    async def publish_async(self, event_type: str, data: Any) -> None:
        self.events.append((event_type, data))

    def subscribe(self, event_type: str, handler: Any) -> None:
        return None

    def unsubscribe(self, event_type: str, handler: Any) -> None:
        return None


class _StubAdvancedConnection:
    def __init__(
        self,
        connection_id: str,
        *,
        state: ConnectionState = ConnectionState.CONNECTED,
        health_score: float = 100.0,
    ) -> None:
        self.connection_id = connection_id
        self._state = state
        self._health = ConnectionHealth(
            is_healthy=health_score >= 70.0,
            health_score=health_score,
            consecutive_failures=0,
        )
        self._metrics = WebSocketMetrics(connection_id, "TEST")
        self._subscriptions: dict[str, Any] = {}
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.subscribe_error: Exception | None = None

    async def connect(self) -> None:
        self.connect_calls += 1
        self._state = ConnectionState.CONNECTED

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._state = ConnectionState.DISCONNECTED

    async def subscribe(
        self,
        subscription_id: str,
        topic: str,
        symbol: str,
        params: dict[str, Any] | None = None,
        callback: Any = None,
    ) -> None:
        if self.subscribe_error is not None:
            raise self.subscribe_error
        self._subscriptions[subscription_id] = {
            "topic": topic,
            "symbol": symbol,
            "params": params or {},
            "callback": callback,
        }

    async def unsubscribe(self, subscription_id: str) -> None:
        self._subscriptions.pop(subscription_id, None)

    def get_state(self) -> ConnectionState:
        return self._state

    def get_health(self) -> ConnectionHealth:
        return self._health

    def get_metrics(self) -> WebSocketMetrics:
        return self._metrics


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
            (
                {"url": "wss://test.com", "exchange_name": "TEST", "max_connections": 0},
                "max_connections",
            ),
            (
                {"url": "wss://test.com", "exchange_name": "TEST", "heartbeat_interval": 0},
                "heartbeat_interval",
            ),
            (
                {
                    "url": "wss://test.com",
                    "exchange_name": "TEST",
                    "min_connections": 2,
                    "max_connections": 1,
                },
                "min_connections",
            ),
            (
                {"url": "wss://test.com", "exchange_name": "TEST", "reconnect_backoff_multiplier": 0.5},
                "reconnect_backoff_multiplier",
            ),
            (
                {"url": "wss://test.com", "exchange_name": "TEST", "dead_letter_queue_size": 0},
                "dead_letter_queue_size",
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

    def test_latency_and_error_helpers_handle_empty_and_recent_windows(self):
        metrics = WebSocketMetrics("test_conn", "TEST_EXCHANGE")

        assert metrics.get_p95_latency() == 0.0
        assert metrics.get_error_rate() == 0.0

        with patch("time.time", return_value=100.0):
            metrics.record_error(ErrorCategory.NETWORK)
        with patch("time.time", return_value=170.0):
            assert metrics.get_error_rate() == 0.0


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


@pytest.mark.asyncio
class TestAdvancedWebSocketConnection:
    """Targeted regression tests for AdvancedWebSocketConnection."""

    async def test_helper_methods_cover_message_classification_and_uptime(self):
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")
        connection = AdvancedWebSocketConnection(config, "helper_conn")

        assert connection._is_data_message({"data": {}}) is True
        assert connection._is_data_message({"stream": "btcusdt@ticker"}) is True
        assert connection._is_event_message({"event": "subscribe"}) is True
        assert connection._is_event_message({"type": "notice"}) is True
        assert connection._is_error_message({"error": "boom"}) is True
        assert connection._is_error_message({"code": 500}) is True
        assert connection._extract_topic_symbol({"topic": "ticker"}) == (None, None)
        assert connection._format_subscription_message(
            {"topic": "ticker", "symbol": "BTCUSDT", "params": {"limit": 5}}
        ) == {
            "action": "subscribe",
            "topic": "ticker",
            "symbol": "BTCUSDT",
            "params": {"limit": 5},
        }
        assert connection._format_unsubscription_message(
            {"topic": "ticker", "symbol": "BTCUSDT"}
        ) == {
            "action": "unsubscribe",
            "topic": "ticker",
            "symbol": "BTCUSDT",
        }

        assert connection._get_uptime_ratio() == 0.0
        connection._state = ConnectionState.CONNECTED
        connection._metrics.last_connection_time = time.time()
        assert connection._get_uptime_ratio() == 1.0
        connection._state = ConnectionState.DISCONNECTED
        connection._metrics.last_connection_time = time.time() - 5
        assert connection._get_uptime_ratio() == 0.8

    async def test_get_metrics_updates_queue_utilization(self):
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST", message_buffer_size=10)
        connection = AdvancedWebSocketConnection(config, "metric_conn")

        for _ in range(3):
            await connection._message_queue.put({"payload": True})

        metrics = connection.get_metrics()

        assert metrics.queue_utilization == 0.3

    async def test_send_message_uses_rate_limiter_and_queue(self):
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")
        connection = AdvancedWebSocketConnection(config, "send_conn")
        limiter = MagicMock()
        limiter.acquire = AsyncMock()
        connection._rate_limiter = limiter

        await connection._send_message({"action": "ping"})

        limiter.acquire.assert_awaited_once()
        queued = await connection._send_queue.get()
        assert queued == {"action": "ping"}

    async def test_unsubscribe_noops_for_missing_subscription(self):
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")
        connection = AdvancedWebSocketConnection(config, "unsubscribe_conn")

        await connection.unsubscribe("missing")

        assert connection._metrics.active_subscriptions == 0

    async def test_handle_message_routes_error_and_dlq_paths(self):
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")
        connection = AdvancedWebSocketConnection(config, "router_conn")
        connection._dlq = AsyncMock()
        connection._handle_data_message = AsyncMock(side_effect=RuntimeError("bad payload"))

        await connection._handle_message({"data": {"foo": "bar"}})
        await connection._handle_message({"code": 400, "msg": "exchange boom"})
        await connection._handle_message({"mystery": True})

        connection._dlq.add_message.assert_awaited()
        assert connection._metrics.errors_by_category[ErrorCategory.PROTOCOL] >= 1
        assert connection._metrics.errors_by_category[ErrorCategory.EXCHANGE_SPECIFIC] >= 1

    async def test_handle_event_message_auth_paths(self):
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")
        connection = AdvancedWebSocketConnection(config, "auth_event_conn")

        await connection._handle_event_message({"event": "auth", "success": True})
        assert connection.get_state() == ConnectionState.AUTHENTICATED

        await connection._handle_event_message({"event": "auth", "success": False, "msg": "denied"})
        assert connection._metrics.errors_by_category[ErrorCategory.AUTHENTICATION] == 1

    async def test_handle_data_message_records_callback_errors(self):
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")
        connection = AdvancedWebSocketConnection(config, "callback_conn")

        def bad_callback(message: dict[str, Any]) -> None:
            raise RuntimeError(f"bad callback: {message}")

        connection._subscriptions = {
            "bad": {
                "id": "bad",
                "topic": "ticker",
                "symbol": "BTCUSDT",
                "params": {},
                "callback": bad_callback,
                "created_at": time.time(),
            }
        }
        connection._extract_topic_symbol = MagicMock(return_value=("ticker", "BTCUSDT"))

        await connection._handle_data_message({"stream": "btcusdt@ticker", "data": {"price": "1"}})

        assert connection._metrics.errors_by_category[ErrorCategory.EXCHANGE_SPECIFIC] == 1

    async def test_process_dlq_message_retries_and_requeues(self, monkeypatch: pytest.MonkeyPatch):
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")
        connection = AdvancedWebSocketConnection(config, "dlq_conn")
        connection._dlq = AsyncMock()
        attempts = 0

        async def fake_sleep(_: float) -> None:
            return None

        async def flaky_handle(message: dict[str, Any]) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("retry me")

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(connection, "_handle_message", flaky_handle)

        await connection._process_dlq_message({"message": {"payload": 1}, "retry_count": 0})
        connection._dlq.add_message.assert_awaited_once()

        connection._dlq.reset_mock()
        monkeypatch.setattr(connection, "_handle_message", AsyncMock(return_value=None))
        await connection._process_dlq_message({"message": {"payload": 2}, "retry_count": 3})
        connection._dlq.add_message.assert_not_awaited()

    async def test_attempt_reconnection_stops_after_max_attempts(self, monkeypatch: pytest.MonkeyPatch):
        config = WebSocketConfig(
            url="wss://test.com",
            exchange_name="TEST",
            reconnect_interval=0.01,
            max_reconnect_attempts=2,
            max_reconnect_delay=0.01,
        )
        connection = AdvancedWebSocketConnection(config, "reconnect_conn")
        connection._running = True

        async def fake_sleep(_: float) -> None:
            return None

        async def always_fail() -> None:
            raise RuntimeError("still down")

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(connection, "connect", always_fail)

        await connection._attempt_reconnection()

        assert connection._metrics.reconnections == 2
        assert connection.get_state() == ConnectionState.ERROR
        assert connection._running is False

    async def test_restore_subscriptions_resends_all(self):
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")
        connection = AdvancedWebSocketConnection(config, "restore_conn")
        sent: list[dict[str, Any]] = []

        async def fake_send_subscription(subscription: dict[str, Any]) -> None:
            sent.append(subscription)

        connection._subscriptions = {
            "sub1": {"id": "sub1", "topic": "ticker", "symbol": "BTCUSDT", "params": {}},
            "sub2": {"id": "sub2", "topic": "depth", "symbol": "ETHUSDT", "params": {}},
        }
        connection._send_subscription_message = fake_send_subscription  # type: ignore[method-assign]

        await connection._restore_subscriptions()

        assert [item["id"] for item in sent] == ["sub1", "sub2"]

    async def test_handle_data_message_supports_sync_and_async_callbacks(self):
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")
        connection = AdvancedWebSocketConnection(config, "test_conn")

        received: list[tuple[str, dict[str, Any]]] = []

        def sync_callback(message: dict[str, Any]) -> None:
            received.append(("sync", message))

        async def async_callback(message: dict[str, Any]) -> None:
            received.append(("async", message))

        connection._subscriptions = {
            "sync": {
                "id": "sync",
                "topic": "ticker",
                "symbol": "BTCUSDT",
                "params": {},
                "callback": sync_callback,
                "created_at": time.time(),
            },
            "async": {
                "id": "async",
                "topic": "ticker",
                "symbol": "BTCUSDT",
                "params": {},
                "callback": async_callback,
                "created_at": time.time(),
            },
        }
        connection._extract_topic_symbol = MagicMock(return_value=("ticker", "BTCUSDT"))
        message = {"stream": "btcusdt@ticker", "data": {"price": "100"}}

        await connection._handle_data_message(message)

        assert received == [("sync", message), ("async", message)]

    async def test_subscribe_raises_contract_compliant_rate_limit_error(self):
        config = WebSocketConfig(
            url="wss://test.com",
            exchange_name="TEST",
            subscription_limits={"ticker": 1},
        )
        connection = AdvancedWebSocketConnection(config, "test_conn")
        connection._subscription_count["ticker"] = 1

        with pytest.raises(RateLimitError, match="TEST rate limit exceeded") as exc_info:
            await connection.subscribe("sub2", "ticker", "BTCUSDT")

        assert exc_info.value.exchange_name == "TEST"

    async def test_subscribe_raises_contract_compliant_websocket_error_for_open_circuit_breaker(
        self,
    ):
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")
        connection = AdvancedWebSocketConnection(config, "test_conn")
        connection._circuit_breaker = MagicMock()
        connection._circuit_breaker.get_state.return_value = "OPEN"

        with pytest.raises(WebSocketError, match="TEST WebSocket error") as exc_info:
            await connection.subscribe("sub3", "ticker", "BTCUSDT")

        assert exc_info.value.exchange_name == "TEST"

    async def test_connect_failure_raises_contract_compliant_websocket_error(self, monkeypatch):
        config = WebSocketConfig(
            url="wss://primary.test.com",
            exchange_name="TEST",
            endpoints=["wss://backup.test.com"],
        )
        connection = AdvancedWebSocketConnection(config, "test_conn")

        async def always_fail(endpoint: str) -> None:
            raise RuntimeError(f"cannot connect to {endpoint}")

        monkeypatch.setattr(connection, "_do_connect", always_fail)

        with pytest.raises(WebSocketError, match="TEST WebSocket error") as exc_info:
            await connection.connect()

        assert "All connection endpoints failed" in str(exc_info.value)
        assert connection.get_state() == ConnectionState.ERROR

    async def test_disconnect_clears_websocket_and_background_tasks(self):
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")
        connection = AdvancedWebSocketConnection(config, "test_conn")
        websocket = _DummyWebSocket()
        connection._websocket = websocket
        connection._state = ConnectionState.CONNECTED
        connection._running = True
        connection._processing_task = asyncio.create_task(asyncio.sleep(3600))
        connection._sender_task = asyncio.create_task(asyncio.sleep(3600))
        connection._health_task = asyncio.create_task(asyncio.sleep(3600))

        await connection.disconnect()

        assert websocket.closed is True
        assert connection._websocket is None
        assert connection._processing_task is None
        assert connection._sender_task is None
        assert connection._health_task is None
        assert connection.get_state() == ConnectionState.DISCONNECTED

    async def test_handle_disconnect_closes_stale_websocket_before_reconnect(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        config = WebSocketConfig(
            url="wss://test.com",
            exchange_name="TEST",
            reconnect_enabled=True,
            reconnect_interval=0.01,
            max_reconnect_attempts=1,
        )
        connection = AdvancedWebSocketConnection(config, "test_conn")
        stale_websocket = _DummyWebSocket()
        replacement_websocket = _DummyWebSocket()
        connection._websocket = stale_websocket
        connection._state = ConnectionState.CONNECTED
        connection._running = True
        connection._sender_task = asyncio.create_task(asyncio.sleep(3600))
        connection._health_task = asyncio.create_task(asyncio.sleep(3600))

        attempts = 0

        async def fake_sleep(_: float) -> None:
            return None

        async def fake_connect() -> None:
            nonlocal attempts
            attempts += 1
            connection._state = ConnectionState.CONNECTED
            connection._websocket = replacement_websocket

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(connection, "connect", fake_connect)

        await connection._handle_disconnect()

        assert stale_websocket.closed is True
        assert attempts == 1
        assert connection._websocket is replacement_websocket
        assert connection._sender_task is None
        assert connection._health_task is None
        assert connection.get_state() == ConnectionState.CONNECTED


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

    def test_get_endpoints_supports_futures_and_swap(self):
        futures_adapter = BinanceWebSocketAdapter(exchange_type=ExchangeType.FUTURES)
        swap_adapter = BinanceWebSocketAdapter(exchange_type=ExchangeType.SWAP)

        futures_endpoints = futures_adapter.get_endpoints("ignored")
        swap_endpoints = swap_adapter.get_endpoints("ignored")

        assert futures_endpoints[0] == "wss://fstream.binance.com"
        assert swap_endpoints[0] == "wss://dstream.binance.com"

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

    @pytest.mark.asyncio
    async def test_authenticate_subscribes_with_listen_key(self, monkeypatch: pytest.MonkeyPatch):
        credentials = ExchangeCredentials(
            exchange_name="BINANCE",
            auth_type=AuthenticationType.API_KEY_SECRET,
            api_key="key",
            api_secret="secret",
        )
        adapter = BinanceWebSocketAdapter(credentials=credentials)
        websocket = _DummyWebSocket()

        async def fake_get_listen_key() -> None:
            adapter._listen_key = "listen-key-123"

        monkeypatch.setattr(adapter, "_get_listen_key", fake_get_listen_key)

        await adapter.authenticate(websocket)

        assert json.loads(websocket.sent[0]) == {
            "method": "SUBSCRIBE",
            "params": ["listen-key-123"],
            "id": json.loads(websocket.sent[0])["id"],
        }

    @pytest.mark.parametrize(
        ("topic", "params", "expected"),
        [
            ("depth", {"level": "5"}, "btcusdt@depth5"),
            ("trades", {}, "btcusdt@trade"),
            ("kline", {"interval": "5m"}, "btcusdt@kline_5m"),
            ("aggTrades", {}, "btcusdt@aggTrade"),
            ("markPrice", {}, "btcusdt@markPrice@1s"),
            ("custom", {}, "btcusdt@custom"),
        ],
    )
    def test_get_stream_name_covers_branch_topics(
        self, topic: str, params: dict[str, Any], expected: str
    ) -> None:
        adapter = BinanceWebSocketAdapter()

        assert adapter._get_stream_name(topic, "BTCUSDT", params) == expected

    def test_normalize_message_supports_depth_trade_and_kline(self):
        adapter = BinanceWebSocketAdapter()

        depth = adapter.normalize_message(
            {
                "stream": "btcusdt@depth",
                "data": {
                    "bids": [["50000", "1.5"]],
                    "asks": [["50010", "2.0"]],
                    "lastUpdateId": 123,
                },
            }
        )
        trade = adapter.normalize_message(
            {
                "stream": "btcusdt@trade",
                "data": {"p": "50000", "q": "0.1", "T": 123456, "m": True},
            }
        )
        kline = adapter.normalize_message(
            {
                "stream": "btcusdt@kline_1m",
                "data": {
                    "k": {
                        "t": 1,
                        "T": 2,
                        "o": "10",
                        "h": "12",
                        "l": "9",
                        "c": "11",
                        "v": "100",
                        "x": True,
                    }
                },
            }
        )

        assert depth["bids"] == [[50000.0, 1.5]]
        assert depth["asks"] == [[50010.0, 2.0]]
        assert depth["last_update_id"] == 123
        assert trade["price"] == 50000.0
        assert trade["quantity"] == 0.1
        assert trade["is_buyer_maker"] is True
        assert kline["open_time"] == 1
        assert kline["close_time"] == 2
        assert kline["close"] == 11.0
        assert kline["is_closed"] is True


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

    def test_generate_signature_requires_contract_compliant_credentials_error(self):
        adapter = OKXWebSocketAdapter()

        with pytest.raises(AuthenticationError, match="Connection failed: OKX") as exc_info:
            adapter._generate_signature("1700000000", "GET", "/users/self/verify")

        assert exc_info.value.exchange_name == "OKX"

    def test_generate_signature_with_credentials(self):
        credentials = ExchangeCredentials(
            exchange_name="OKX",
            auth_type=AuthenticationType.API_KEY_SECRET,
            api_key="key",
            api_secret="secret",
            passphrase="pass",
        )
        adapter = OKXWebSocketAdapter(credentials=credentials)

        signature = adapter._generate_signature("1700000000", "GET", "/users/self/verify")
        expected = hmac.new(
            b"secret", b"1700000000GET/users/self/verify", hashlib.sha256
        ).digest()

        assert signature == __import__("base64").b64encode(expected).decode()

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

    @pytest.mark.asyncio
    async def test_authenticate_sends_login_message(self, monkeypatch: pytest.MonkeyPatch):
        credentials = ExchangeCredentials(
            exchange_name="OKX",
            auth_type=AuthenticationType.API_KEY_SECRET,
            api_key="key",
            api_secret="secret",
            passphrase="pass",
        )
        adapter = OKXWebSocketAdapter(credentials=credentials)
        websocket = _DummyWebSocket()

        monkeypatch.setattr(time, "time", lambda: 1700000000.0)
        monkeypatch.setattr(adapter, "_generate_signature", lambda *args: "signed")

        await adapter.authenticate(websocket)

        assert json.loads(websocket.sent[0]) == {
            "op": "login",
            "args": [
                {
                    "apiKey": "key",
                    "passphrase": "pass",
                    "timestamp": "1700000000",
                    "sign": "signed",
                }
            ],
        }

    def test_okx_channel_mapping_and_reverse_mapping(self):
        adapter = OKXWebSocketAdapter()

        assert adapter._get_okx_channel("ticker", {}) == "tickers"
        assert adapter._get_okx_channel("depth", {}) == "books"
        assert adapter._get_okx_channel("kline", {"interval": "5m"}) == "candle5m"
        assert adapter._get_okx_channel("custom", {}) == "custom"
        assert adapter._convert_okx_channel_to_generic("tickers") == "ticker"
        assert adapter._convert_okx_channel_to_generic("books") == "depth"
        assert adapter._convert_okx_channel_to_generic("candle1m") == "kline"
        assert adapter._convert_okx_channel_to_generic("account") == "account"
        assert adapter._convert_okx_channel_to_generic("custom") == "custom"

    def test_normalize_message_supports_depth_trades_and_kline(self):
        adapter = OKXWebSocketAdapter()

        depth = adapter.normalize_message(
            {
                "arg": {"channel": "books", "instId": "BTC-USDT"},
                "data": [{"bids": [["1", "2"]], "asks": [["3", "4"]], "checksum": 9}],
            }
        )
        trades = adapter.normalize_message(
            {
                "arg": {"channel": "trades", "instId": "BTC-USDT"},
                "data": [{"px": "50000", "sz": "0.2", "ts": "123", "side": "buy"}],
            }
        )
        kline = adapter.normalize_message(
            {
                "arg": {"channel": "candle1m", "instId": "BTC-USDT"},
                "data": [{"ts": "123", "o": "10", "h": "12", "l": "9", "c": "11", "vol": "15"}],
            }
        )

        assert depth["bids"] == [[1.0, 2.0]]
        assert depth["asks"] == [[3.0, 4.0]]
        assert depth["checksum"] == 9
        assert trades["price"] == 50000.0
        assert trades["quantity"] == 0.2
        assert trades["trade_time"] == 123
        assert trades["side"] == "buy"
        assert kline["open_time"] == 123
        assert kline["open"] == 10.0
        assert kline["close"] == 11.0
        assert kline["volume"] == 15.0


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
        assert isinstance(adapter, GenericWebSocketAdapter)

    def test_register_adapter_overrides_factory_mapping(self):
        WebSocketAdapterFactory.register_adapter("CUSTOM_GENERIC", GenericWebSocketAdapter)

        adapter = WebSocketAdapterFactory.create_adapter("CUSTOM_GENERIC")

        assert isinstance(adapter, GenericWebSocketAdapter)

    def test_generic_adapter_formats_and_normalizes_messages(self):
        adapter = GenericWebSocketAdapter("CUSTOM")

        subscribe_message = adapter.format_subscription_message(
            "sub1", "ticker", "BTCUSDT", {"depth": 5}
        )
        unsubscribe_message = adapter.format_unsubscription_message("sub1", "ticker", "BTCUSDT")
        normalized = adapter.normalize_message(
            {"symbol": "BTCUSDT", "topic": "ticker", "data": {"price": 1}, "timestamp": 123}
        )

        assert subscribe_message == {
            "action": "subscribe",
            "topic": "ticker",
            "symbol": "BTCUSDT",
            "params": {"depth": 5},
            "id": "sub1",
        }
        assert unsubscribe_message == {
            "action": "unsubscribe",
            "topic": "ticker",
            "symbol": "BTCUSDT",
            "id": "sub1",
        }
        assert adapter.extract_topic_symbol({"topic": "ticker", "symbol": "BTCUSDT"}) == (
            "ticker",
            "BTCUSDT",
        )
        assert normalized == {
            "exchange": "CUSTOM",
            "symbol": "BTCUSDT",
            "topic": "ticker",
            "data": {"price": 1},
            "timestamp": 123,
        }

    def test_subscription_count_helpers_do_not_go_negative(self):
        adapter = GenericWebSocketAdapter("CUSTOM")

        adapter.increment_subscription_count("ticker")
        adapter.decrement_subscription_count("ticker")
        adapter.decrement_subscription_count("ticker")

        assert adapter._subscription_counts["ticker"] == 0

    @pytest.mark.asyncio
    async def test_check_rate_limits_raises_contract_compliant_error(self):
        adapter = BinanceWebSocketAdapter()
        adapter._subscription_counts["ticker"] = adapter.get_subscription_limits()["ticker"]

        with pytest.raises(RateLimitError, match="BINANCE rate limit exceeded") as exc_info:
            await adapter.check_rate_limits("ticker")

        assert exc_info.value.exchange_name == "BINANCE"


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

    def test_random_selection_uses_random_choice(self, monkeypatch: pytest.MonkeyPatch):
        balancer = LoadBalancer("random")
        connections = [
            ConnectionWrapper(_StubAdvancedConnection("c1")),
            ConnectionWrapper(_StubAdvancedConnection("c2")),
        ]

        monkeypatch.setattr(random, "choice", lambda items: items[1])

        assert balancer.select_connection(connections) is connections[1]

    def test_weighted_selection_supports_zero_weight_and_threshold_choice(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        balancer = LoadBalancer("weighted")
        zero_weight_connections = [
            ConnectionWrapper(_StubAdvancedConnection("z1"), health_score=0.0),
            ConnectionWrapper(_StubAdvancedConnection("z2"), health_score=0.0),
        ]

        assert balancer.select_connection(zero_weight_connections) is zero_weight_connections[0]

        weighted_connections = [
            ConnectionWrapper(_StubAdvancedConnection("w1"), health_score=10.0),
            ConnectionWrapper(_StubAdvancedConnection("w2"), health_score=90.0),
        ]
        monkeypatch.setattr(random, "uniform", lambda a, b: 50.0)

        assert balancer.select_connection(weighted_connections) is weighted_connections[1]

    def test_selection_falls_back_to_all_connections_and_unknown_strategy_defaults(self):
        balancer = LoadBalancer("unknown")
        connections = [
            ConnectionWrapper(
                _StubAdvancedConnection("u1", state=ConnectionState.DISCONNECTED), health_score=10.0
            ),
            ConnectionWrapper(
                _StubAdvancedConnection("u2", state=ConnectionState.ERROR), health_score=20.0
            ),
        ]

        assert balancer.select_connection(connections) is connections[0]


@pytest.mark.asyncio
class TestAdvancedWebSocketManager:
    async def test_start_and_stop_are_idempotent(self):
        manager = AdvancedWebSocketManager(event_bus=_DummyEventBus())

        await manager.start()
        cleanup_task = manager._cleanup_task
        await manager.start()

        assert manager._running is True
        assert manager._cleanup_task is cleanup_task

        await manager.stop()
        await manager.stop()

        assert manager._running is False
        assert manager._cleanup_task is None

    async def test_get_connection_creates_wrapper_and_updates_metrics(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        manager = AdvancedWebSocketManager(event_bus=_DummyEventBus())
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")
        await manager.add_exchange(config, PoolConfiguration(max_connections=1))
        created_connections: list[_StubAdvancedConnection] = []

        def fake_connection_factory(config: WebSocketConfig, connection_id: str) -> _StubAdvancedConnection:
            connection = _StubAdvancedConnection(connection_id)
            created_connections.append(connection)
            return connection

        async def fake_create_task(coro: Any) -> asyncio.Task[None]:
            await coro
            return asyncio.create_task(asyncio.sleep(0))

        monkeypatch.setattr(
            advanced_ws_manager_module,
            "AdvancedWebSocketConnection",
            fake_connection_factory,
        )
        monkeypatch.setattr(manager._task_group, "create_task", fake_create_task)

        connection = await manager.get_connection("TEST")

        assert connection is created_connections[0]
        assert created_connections[0].connect_calls == 1
        assert manager._global_metrics["total_connections"] == 1
        assert manager._pools["TEST"][0].usage_count == 1
        assert manager._pools["TEST"][0].in_use is True

    async def test_get_connection_falls_back_to_available_or_least_used(self):
        manager = AdvancedWebSocketManager(event_bus=_DummyEventBus())
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")
        await manager.add_exchange(config, PoolConfiguration(max_connections=2))

        available = ConnectionWrapper(_StubAdvancedConnection("available"), usage_count=3, in_use=False)
        busy = ConnectionWrapper(_StubAdvancedConnection("busy"), usage_count=1, in_use=True)
        manager._pools["TEST"] = [available, busy]
        manager._load_balancers["TEST"] = MagicMock(select_connection=MagicMock(return_value=None))

        chosen_available = await manager.get_connection("TEST")

        assert chosen_available is available.connection
        assert available.in_use is True

        all_busy_low = ConnectionWrapper(_StubAdvancedConnection("low"), usage_count=1, in_use=True)
        all_busy_high = ConnectionWrapper(_StubAdvancedConnection("high"), usage_count=5, in_use=True)
        manager._pools["TEST"] = [all_busy_high, all_busy_low]

        chosen_least_used = await manager.get_connection("TEST")

        assert chosen_least_used is all_busy_low.connection

    async def test_release_connection_and_subscribe_failure_rolls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        manager = AdvancedWebSocketManager(event_bus=_DummyEventBus())
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")
        await manager.add_exchange(config, PoolConfiguration(max_connections=1))
        connection = _StubAdvancedConnection("sub_fail")
        connection.subscribe_error = RuntimeError("subscribe failed")
        wrapper = ConnectionWrapper(connection, in_use=True)
        manager._pools["TEST"] = [wrapper]
        released: list[tuple[str, _StubAdvancedConnection]] = []

        async def fake_get_connection(exchange_name: str) -> _StubAdvancedConnection:
            assert exchange_name == "TEST"
            return connection

        async def fake_release_connection(
            exchange_name: str, released_connection: _StubAdvancedConnection
        ) -> None:
            released.append((exchange_name, released_connection))

        monkeypatch.setattr(manager, "get_connection", fake_get_connection)
        monkeypatch.setattr(manager, "release_connection", fake_release_connection)

        with pytest.raises(RuntimeError, match="subscribe failed"):
            await manager.subscribe("TEST", "ticker", "BTCUSDT", lambda *_: None)

        assert released == [("TEST", connection)]

        await AdvancedWebSocketManager.release_connection(manager, "TEST", connection)

        assert wrapper.in_use is False

    async def test_close_all_and_get_connection_health(self):
        event_bus = _DummyEventBus()
        manager = AdvancedWebSocketManager(event_bus=event_bus)
        config = WebSocketConfig(url="wss://test.com", exchange_name="TEST")
        await manager.add_exchange(config, PoolConfiguration(max_connections=2))

        connection = _StubAdvancedConnection("health_conn", health_score=88.0)
        connection._metrics.messages_sent = 5
        connection._metrics.messages_received = 7
        connection._metrics.bytes_sent = 100
        connection._metrics.bytes_received = 200
        connection._metrics.active_subscriptions = 1
        connection._metrics.queue_utilization = 0.25
        connection._subscriptions["sub1"] = {"topic": "ticker"}
        wrapper = ConnectionWrapper(connection, usage_count=2, health_score=88.0, in_use=True)
        manager._pools["TEST"] = [wrapper]
        manager._global_metrics["total_connections"] = 1
        manager._global_metrics["active_connections"] = 1
        manager._global_metrics["total_subscriptions"] = 1

        health = manager.get_connection_health("TEST", "health_conn")

        assert health is not None
        assert health["connection_id"] == "health_conn"
        assert health["health"]["health_score"] == 88.0
        assert health["metrics"]["messages_sent"] == 5
        assert manager.get_connection_health("MISSING", "health_conn") is None

        manager._handle_performance_alert({"severity": "warning"})

        assert event_bus.events[-1] == ("performance_alert", {"severity": "warning"})

        await manager.close_all()

        assert manager._pools["TEST"] == []
        assert manager._global_metrics["total_connections"] == 0
        assert manager._global_metrics["active_connections"] == 0
        assert manager._global_metrics["total_subscriptions"] == 0


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

    def test_get_metric_supports_time_window_and_empty_aggregates(self, metrics_collector):
        with patch("time.time", return_value=1000.0):
            metrics_collector.increment_counter("window_counter", value=1)
        with patch("time.time", return_value=1030.0):
            metrics_collector.increment_counter("window_counter", value=2)

        with patch("time.time", return_value=1035.0):
            recent_points = metrics_collector.get_metric("window_counter", time_window=10)
            old_window_points = metrics_collector.get_metric("window_counter", time_window=2)

        assert len(recent_points) == 1
        assert recent_points[0].value == 3.0
        assert old_window_points == []
        assert metrics_collector.get_aggregated_metrics("missing_metric") == {}

    def test_get_aggregated_metrics_includes_percentiles(self, metrics_collector):
        for value in range(1, 11):
            metrics_collector.record_histogram("percentile_histogram", float(value))

        aggregated = metrics_collector.get_aggregated_metrics("percentile_histogram")

        assert aggregated["count"] == 10
        assert aggregated["p50"] == 5.5
        assert aggregated["p90"] == 10.0
        assert aggregated["p95"] == 10.0
        assert aggregated["p99"] == 10.0

    def test_export_prometheus_format_includes_counter_gauge_and_histogram(self, metrics_collector):
        metrics_collector.increment_counter("requests_total")
        metrics_collector.set_gauge("memory_usage", 42.0)
        metrics_collector.record_histogram("latency_ms", 12.0)

        exported = metrics_collector.export_prometheus_format()

        assert "# TYPE requests_total counter" in exported
        assert "requests_total 1.0" in exported
        assert "# TYPE memory_usage gauge" in exported
        assert "memory_usage 42.0" in exported
        assert "# TYPE latency_ms histogram" in exported
        assert 'latency_ms_bucket{le="0"} 12.0' in exported


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

    async def test_evaluate_condition_supports_metric_memory_and_cpu_paths(self, alert_manager):
        alert_manager.metrics_collector.set_gauge("websocket_latency", 12.5)
        alert_manager.metrics_collector.set_gauge("websocket_errors", 3.0)

        with patch("bt_api_py.websocket.monitoring.psutil.virtual_memory") as mock_vm, patch(
            "bt_api_py.websocket.monitoring.psutil.cpu_percent"
        ) as mock_cpu:
            mock_vm.return_value = MagicMock(percent=65.0)
            mock_cpu.return_value = 35.0

            assert alert_manager._evaluate_condition("avg_latency_ms") == 12.5
            assert alert_manager._evaluate_condition("error_rate") == 3.0
            assert alert_manager._evaluate_condition("memory_usage") == 65.0
            assert alert_manager._evaluate_condition("cpu_usage") == 35.0
            assert alert_manager._evaluate_condition("unknown_condition") is None

    async def test_trigger_alert_notifies_handlers_and_tracks_active_and_history(self, alert_manager):
        alert = PerformanceAlert(
            alert_id="notify_alert",
            name="Notify Alert",
            severity=AlertSeverity.ERROR,
            condition="avg_latency_ms",
            threshold=10.0,
            description="notification test",
        )
        seen: list[str] = []

        def good_handler(target_alert: PerformanceAlert) -> None:
            seen.append(target_alert.alert_id)

        def failing_handler(_: PerformanceAlert) -> None:
            raise RuntimeError("handler failed")

        alert_manager.add_alert(alert)
        alert_manager.add_notification_handler(good_handler)
        alert_manager.add_notification_handler(failing_handler)

        await alert_manager._trigger_alert(alert, 42.0)

        assert seen == ["notify_alert"]
        assert alert_manager.get_active_alerts() == [alert]
        assert alert_manager.get_alert_history()

        await alert_manager._resolve_alert(alert)

        assert alert_manager.get_active_alerts() == []

    async def test_get_alert_history_filters_by_time_window(self, alert_manager):
        alert_manager._alert_history.extend(
            [
                {"timestamp": 100.0, "action": "triggered"},
                {"timestamp": 200.0, "action": "resolved"},
            ]
        )

        with patch("time.time", return_value=250.0):
            history = alert_manager.get_alert_history(time_window=60.0)

        assert history == [{"timestamp": 200.0, "action": "resolved"}]


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

    async def test_get_benchmark_results_and_summary(self, ws_benchmark):
        ws_benchmark._results.extend(
            [
                BenchmarkResult(
                    benchmark_name="latency_test",
                    exchange_name="benchmark",
                    timestamp=100.0,
                    metrics={"avg_latency_ms": 10.0},
                    success=True,
                    duration_ms=1.0,
                ),
                BenchmarkResult(
                    benchmark_name="latency_test",
                    exchange_name="benchmark",
                    timestamp=110.0,
                    metrics={"avg_latency_ms": 20.0},
                    success=True,
                    duration_ms=1.0,
                ),
                BenchmarkResult(
                    benchmark_name="latency_test",
                    exchange_name="benchmark",
                    timestamp=120.0,
                    metrics={},
                    success=False,
                    error_message="boom",
                    duration_ms=1.0,
                ),
            ]
        )

        with patch("time.time", return_value=130.0):
            results = ws_benchmark.get_benchmark_results("latency_test", time_window=40.0)
            summary = ws_benchmark.get_benchmark_summary("latency_test", time_window=40.0)

        assert len(results) == 3
        assert summary["success_rate"] == 2 / 3
        assert summary["total_runs"] == 3
        assert summary["successful_runs"] == 2
        assert summary["metrics"]["avg_latency_ms"]["mean"] == 15.0
        assert summary["metrics"]["avg_latency_ms"]["min"] == 10.0
        assert summary["metrics"]["avg_latency_ms"]["max"] == 20.0

    async def test_get_benchmark_summary_handles_empty_and_failed_only_results(self, ws_benchmark):
        assert ws_benchmark.get_benchmark_summary("missing") == {}

        ws_benchmark._results.append(
            BenchmarkResult(
                benchmark_name="throughput_test",
                exchange_name="benchmark",
                timestamp=time.time(),
                metrics={},
                success=False,
                error_message="failure",
                duration_ms=1.0,
            )
        )

        summary = ws_benchmark.get_benchmark_summary("throughput_test")

        assert summary == {"success_rate": 0, "total_runs": 1, "successful_runs": 0}


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
            assert (
                connection._dlq._processing_task is None or connection._dlq._processing_task.done()
            )

    async def test_pool_load_balancing(self):
        """Test connection pool load balancing."""
        # This would test the actual pool behavior

    async def test_monitoring_integration(self):
        """Test monitoring system integration."""
        # This would test the monitoring components working together
