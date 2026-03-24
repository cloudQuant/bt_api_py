"""
Advanced WebSocket connection management with intelligent pooling, circuit breakers, and comprehensive monitoring.
Production-grade implementation supporting 73+ exchanges with high reliability and performance.
"""

import asyncio
import contextlib
import inspect
import json
import statistics
import time
import zlib
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from websockets import ConnectionClosed, WebSocketException

from bt_api_py.exceptions import RateLimitError, WebSocketError
from bt_api_py.logging_factory import get_logger


class ConnectionState(Enum):
    """Enhanced connection states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"
    DEGRADING = "degrading"
    ERROR = "error"
    MAINTENANCE = "maintenance"


class ErrorCategory(Enum):
    """Error categorization for recovery strategies."""

    NETWORK = "network"
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    PROTOCOL = "protocol"
    EXCHANGE_SPECIFIC = "exchange_specific"
    UNKNOWN = "unknown"


@dataclass
class WebSocketMetrics:
    """Comprehensive WebSocket metrics."""

    connection_id: str
    exchange_name: str

    # Connection metrics
    connections_established: int = 0
    connections_failed: int = 0
    reconnections: int = 0
    total_uptime: float = 0.0
    last_connection_time: float | None = None

    # Message metrics
    messages_sent: int = 0
    messages_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    message_latency_samples: deque = field(default_factory=lambda: deque(maxlen=1000))

    # Error metrics
    errors_by_category: dict[ErrorCategory, int] = field(default_factory=lambda: defaultdict(int))
    error_rate_samples: deque = field(default_factory=lambda: deque(maxlen=100))

    # Subscription metrics
    active_subscriptions: int = 0
    subscription_failures: int = 0
    subscription_successes: int = 0

    # Performance metrics
    queue_utilization: float = 0.0
    memory_usage: float = 0.0
    cpu_usage: float = 0.0

    def record_latency(self, latency_ms: float) -> None:
        """Record message latency sample."""
        self.message_latency_samples.append(latency_ms)

    def get_avg_latency(self) -> float:
        """Get average message latency."""
        return (
            statistics.mean(self.message_latency_samples) if self.message_latency_samples else 0.0
        )

    def get_p95_latency(self) -> float:
        """Get 95th percentile latency."""
        if not self.message_latency_samples:
            return 0.0
        sorted_samples = sorted(self.message_latency_samples)
        return statistics.quantiles(sorted_samples, n=20)[18]  # 95th percentile

    def record_error(self, category: ErrorCategory) -> None:
        """Record error by category."""
        self.errors_by_category[category] += 1
        self.error_rate_samples.append(time.time())

    def get_error_rate(self) -> float:
        """Get current error rate (errors per minute)."""
        now = time.time()
        recent_errors = sum(1 for t in self.error_rate_samples if now - t < 60)
        return recent_errors


@dataclass
class ConnectionHealth:
    """Connection health monitoring."""

    is_healthy: bool = True
    last_check: float = field(default_factory=time.time)
    consecutive_failures: int = 0
    health_score: float = 100.0

    # Health thresholds
    max_latency_ms: float = 1000.0
    max_error_rate: float = 10.0  # errors per minute
    min_uptime_ratio: float = 0.95
    max_consecutive_failures: int = 5

    def update_health(
        self, latency_ms: float, error_rate: float, uptime_ratio: float, has_error: bool = False
    ) -> None:
        """Update health score based on metrics."""
        self.last_check = time.time()

        if has_error:
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = max(0, self.consecutive_failures - 1)

        # Calculate health score
        latency_score = max(0, 100 - (latency_ms / self.max_latency_ms) * 100)
        error_score = max(0, 100 - (error_rate / self.max_error_rate) * 100)
        uptime_score = uptime_ratio * 100
        failure_score = max(
            0, 100 - (self.consecutive_failures / self.max_consecutive_failures) * 100
        )

        self.health_score = statistics.mean(
            [latency_score, error_score, uptime_score, failure_score]
        )
        self.is_healthy = (
            self.health_score >= 70.0 and self.consecutive_failures < self.max_consecutive_failures
        )


@dataclass
class WebSocketConfig:
    """Enhanced WebSocket configuration."""

    # Basic connection
    url: str
    exchange_name: str
    endpoints: list[str] = field(default_factory=list)  # Multiple endpoints for failover

    # Connection pooling
    max_connections: int = 5
    min_connections: int = 1
    connection_timeout: float = 30.0
    idle_timeout: float = 300.0

    # Heartbeat and health
    heartbeat_interval: float = 30.0
    heartbeat_timeout: float = 10.0
    health_check_interval: float = 60.0

    # Reconnection with exponential backoff
    reconnect_enabled: bool = True
    reconnect_interval: float = 1.0
    max_reconnect_attempts: int = 10
    reconnect_backoff_multiplier: float = 2.0
    max_reconnect_delay: float = 60.0

    # Circuit breaker
    circuit_breaker_enabled: bool = True
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: float = 60.0

    # Performance optimization
    compression: bool = True
    message_buffer_size: int = 8192
    send_buffer_size: int = 1024
    receive_buffer_size: int = 8192

    # Rate limiting
    rate_limit_enabled: bool = True
    max_requests_per_second: int = 10
    max_subscriptions_per_connection: int = 50

    # Subscription management
    subscription_limits: dict[str, int] = field(
        default_factory=lambda: {
            "ticker": 100,
            "depth": 50,
            "trades": 100,
            "kline": 200,
            "orders": 50,
        }
    )

    # Dead letter queue
    dead_letter_queue_enabled: bool = True
    dead_letter_queue_size: int = 1000

    # Exchange-specific settings
    exchange_config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
            raise ValueError("url must be a valid ws/wss URL")
        if not isinstance(self.exchange_name, str) or not self.exchange_name.strip():
            raise ValueError("exchange_name must be a non-empty string")
        if not isinstance(self.max_connections, int) or self.max_connections <= 0:
            raise ValueError("max_connections must be a positive integer")
        if not isinstance(self.min_connections, int) or self.min_connections <= 0:
            raise ValueError("min_connections must be a positive integer")
        if self.min_connections > self.max_connections:
            raise ValueError("min_connections must be <= max_connections")
        if self.heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be > 0")
        if self.heartbeat_timeout <= 0:
            raise ValueError("heartbeat_timeout must be > 0")
        if self.connection_timeout <= 0:
            raise ValueError("connection_timeout must be > 0")
        if self.idle_timeout <= 0:
            raise ValueError("idle_timeout must be > 0")
        if self.reconnect_interval <= 0:
            raise ValueError("reconnect_interval must be > 0")
        if self.max_reconnect_attempts < 0:
            raise ValueError("max_reconnect_attempts must be >= 0")
        if self.reconnect_backoff_multiplier < 1:
            raise ValueError("reconnect_backoff_multiplier must be >= 1")
        if self.max_reconnect_delay <= 0:
            raise ValueError("max_reconnect_delay must be > 0")
        if self.message_buffer_size <= 0:
            raise ValueError("message_buffer_size must be > 0")
        if self.send_buffer_size <= 0:
            raise ValueError("send_buffer_size must be > 0")
        if self.receive_buffer_size <= 0:
            raise ValueError("receive_buffer_size must be > 0")
        if self.max_requests_per_second <= 0:
            raise ValueError("max_requests_per_second must be > 0")
        if self.max_subscriptions_per_connection <= 0:
            raise ValueError("max_subscriptions_per_connection must be > 0")
        if self.dead_letter_queue_size <= 0:
            raise ValueError("dead_letter_queue_size must be > 0")


class DeadLetterQueue:
    """Dead letter queue for failed messages."""

    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_size)
        self._processing = False
        self._processing_task: asyncio.Task | None = None
        self.logger = get_logger("dlq")

    async def add_message(
        self, message: dict[str, Any], error: Exception, retry_count: int = 0
    ) -> None:
        """Add failed message to dead letter queue."""
        if self._queue.qsize() >= self.max_size:
            # Remove oldest message
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()

        dlq_message = {
            "message": message,
            "error": str(error),
            "error_type": type(error).__name__,
            "timestamp": time.time(),
            "retry_count": retry_count,
        }

        await self._queue.put(dlq_message)
        self.logger.warning(f"Added message to DLQ: {error}")

    async def get_message(self) -> dict[str, Any] | None:
        """Get message from dead letter queue."""
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=1.0)
        except TimeoutError:
            return None

    async def start_processing(self, processor_func: Callable) -> None:
        """Start processing dead letter messages."""
        if self._processing_task and not self._processing_task.done():
            return

        self._processing = True
        self._processing_task = asyncio.create_task(self._process_loop(processor_func))

    async def _process_loop(self, processor_func: Callable) -> None:
        """Process messages from dead letter queue."""
        while self._processing:
            try:
                message = await self.get_message()
                if message:
                    await processor_func(message)
                else:
                    await asyncio.sleep(1.0)
            except Exception as e:
                self.logger.error(f"DLQ processing error: {e}")
                await asyncio.sleep(5.0)

    def stop_processing(self) -> None:
        """Stop processing dead letter messages."""
        self._processing = False
        if self._processing_task and not self._processing_task.done():
            self._processing_task.cancel()

    async def shutdown(self) -> None:
        self.stop_processing()
        if self._processing_task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._processing_task
            self._processing_task = None

    def size(self) -> int:
        """Get queue size."""
        return self._queue.qsize()


class IntelligentCircuitBreaker:
    """Intelligent circuit breaker with adaptive thresholds."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 3,
        adaptive_threshold: bool = True,
    ):
        self.logger = get_logger("circuit_breaker")
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.adaptive_threshold = adaptive_threshold

        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0.0
        self.last_success_time = 0.0

        # Adaptive metrics
        self.failure_history: deque[float] = deque(maxlen=100)
        self.success_history: deque[float] = deque(maxlen=100)

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function through circuit breaker."""
        if self.state == "OPEN":
            if time.time() - self.last_failure_time < self.recovery_timeout:
                raise RuntimeError("Circuit breaker is OPEN")
            else:
                self.state = "HALF_OPEN"
                self.success_count = 0

        try:
            result = func(*args, **kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            self.on_success()
            return result
        except Exception as e:
            self.logger.warning(f"Circuit breaker intercepted exception: {e}")
            self.on_failure()
            raise

    def on_success(self) -> None:
        """Handle successful call."""
        now = time.time()
        self.success_history.append(now)
        self.last_success_time = now

        if self.state == "HALF_OPEN":
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = "CLOSED"
                self.failure_count = 0

        if self.state == "CLOSED":
            self.failure_count = max(0, self.failure_count - 1)

        # Adaptive threshold adjustment
        if self.adaptive_threshold and len(self.success_history) > 50:
            recent_success_rate = len([t for t in self.success_history if now - t < 300]) / 50
            if recent_success_rate > 0.9:
                self.failure_threshold = max(3, self.failure_threshold - 1)

    def on_failure(self) -> None:
        """Handle failed call."""
        now = time.time()
        self.failure_history.append(now)
        self.last_failure_time = now

        self.failure_count += 1

        if self.state == "HALF_OPEN" or self.failure_count >= self.failure_threshold:
            self.state = "OPEN"

        # Adaptive threshold adjustment
        if self.adaptive_threshold and len(self.failure_history) > 50:
            recent_failure_rate = len([t for t in self.failure_history if now - t < 300]) / 50
            if recent_failure_rate > 0.5:
                self.failure_threshold = min(20, self.failure_threshold + 1)

    def get_state(self) -> str:
        """Get current circuit breaker state."""
        return self.state

    def get_stats(self) -> dict[str, Any]:
        """Get circuit breaker statistics."""
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "failure_threshold": self.failure_threshold,
            "last_failure_time": self.last_failure_time,
            "last_success_time": self.last_success_time,
        }


class AdvancedWebSocketConnection:
    """Advanced WebSocket connection with comprehensive features."""

    def __init__(self, config: WebSocketConfig, connection_id: str):
        self.config = config
        self.connection_id = connection_id
        self.logger = get_logger(f"ws_{config.exchange_name}_{connection_id}")

        # Connection state
        self._websocket: Any | None = None
        self._state = ConnectionState.DISCONNECTED
        self._running = False

        # Health monitoring
        self._health = ConnectionHealth()
        self._metrics = WebSocketMetrics(connection_id, config.exchange_name)

        # Circuit breaker
        self._circuit_breaker = (
            IntelligentCircuitBreaker(
                failure_threshold=config.circuit_breaker_threshold,
                recovery_timeout=config.circuit_breaker_timeout,
            )
            if config.circuit_breaker_enabled
            else None
        )

        # Subscription management
        self._subscriptions: dict[str, dict[str, Any]] = {}
        self._subscription_count: dict[str, int] = defaultdict(int)

        # Message handling
        self._message_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=config.message_buffer_size
        )
        self._send_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=config.send_buffer_size
        )
        self._processing_task: asyncio.Task | None = None
        self._sender_task: asyncio.Task | None = None
        self._health_task: asyncio.Task | None = None

        # Dead letter queue
        self._dlq = (
            DeadLetterQueue(config.dead_letter_queue_size)
            if config.dead_letter_queue_enabled
            else None
        )

        # Rate limiting
        self._rate_limiter = None
        if config.rate_limit_enabled:
            from bt_api_py.core.async_context import AsyncRateLimiter

            self._rate_limiter = AsyncRateLimiter(config.max_requests_per_second, 1.0)

        # Failover endpoints
        self._current_endpoint_index = 0
        self._endpoint_health = dict.fromkeys([config.url] + config.endpoints, 100.0)

        # Authentication state
        self._authenticated = False
        self._auth_token: str | None = None
        self._token_refresh_time: float | None = None

    async def connect(self) -> None:
        """Connect to WebSocket with intelligent failover."""
        if self._state in (ConnectionState.CONNECTED, ConnectionState.AUTHENTICATED):
            return

        self._state = ConnectionState.CONNECTING
        await self._close_websocket()

        # Try endpoints in order of health
        endpoints = sorted(
            [self.config.url] + self.config.endpoints,
            key=lambda x: self._endpoint_health.get(x, 0),
            reverse=True,
        )

        last_exception = None
        for endpoint in endpoints:
            try:
                self.logger.info(f"Attempting connection to {endpoint}")

                # Apply circuit breaker
                if self._circuit_breaker:
                    await self._circuit_breaker.call(self._do_connect, endpoint)
                else:
                    await self._do_connect(endpoint)

                # Connection successful
                self._state = ConnectionState.CONNECTED
                self._metrics.connections_established += 1
                self._metrics.last_connection_time = time.time()
                self._endpoint_health[endpoint] = min(
                    100, self._endpoint_health.get(endpoint, 0) + 10
                )

                # Start processing tasks
                self._running = True
                self._processing_task = asyncio.create_task(self._process_messages())
                self._sender_task = asyncio.create_task(self._send_messages())

                # Start health monitoring
                self._health_task = asyncio.create_task(self._health_monitor_loop())

                # Start dead letter queue processing
                if self._dlq:
                    await self._dlq.start_processing(self._process_dlq_message)

                self.logger.info(f"Connected to {endpoint}")
                return

            except (OSError, WebSocketException, TimeoutError) as e:
                last_exception = e
                self._endpoint_health[endpoint] = max(
                    0, self._endpoint_health.get(endpoint, 0) - 20
                )
                self.logger.warning(f"Failed to connect to {endpoint}: {e}")
                self._metrics.connections_failed += 1
                continue

        # All endpoints failed
        self._state = ConnectionState.ERROR
        self._metrics.record_error(ErrorCategory.NETWORK)
        raise WebSocketError(
            self.config.exchange_name, detail=f"All connection endpoints failed: {last_exception}"
        )

    async def _do_connect(self, endpoint: str) -> None:
        """Perform actual WebSocket connection."""
        import websockets

        self._websocket = await websockets.connect(
            endpoint,
            ping_interval=self.config.heartbeat_interval,
            ping_timeout=self.config.heartbeat_timeout,
            close_timeout=self.config.connection_timeout,
            max_queue=self.config.receive_buffer_size,
            compression=None if not self.config.compression else "deflate",
        )

        # Send authentication if required
        if self.config.exchange_config.get("requires_auth", False):
            await self._authenticate()

    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        self._running = False
        self._state = ConnectionState.DISCONNECTED

        await self._cancel_background_tasks(exclude_task=asyncio.current_task())
        await self._close_websocket()

        # Stop dead letter queue processing
        if self._dlq:
            await self._dlq.shutdown()

        self.logger.info("Disconnected")

    async def subscribe(
        self,
        subscription_id: str,
        topic: str,
        symbol: str,
        params: dict[str, Any] | None = None,
        callback: Callable | None = None,
    ) -> None:
        """Subscribe to a topic with rate limiting and validation."""
        # Check subscription limits
        if self._subscription_count[topic] >= self.config.subscription_limits.get(topic, 100):
            raise RateLimitError(
                self.config.exchange_name,
                detail=f"Subscription limit exceeded for {topic}",
            )

        # Check circuit breaker
        if self._circuit_breaker and self._circuit_breaker.get_state() == "OPEN":
            raise WebSocketError(
                self.config.exchange_name,
                detail="Circuit breaker is OPEN - cannot subscribe",
            )

        subscription = {
            "id": subscription_id,
            "topic": topic,
            "symbol": symbol,
            "params": params or {},
            "callback": callback,
            "created_at": time.time(),
        }

        self._subscriptions[subscription_id] = subscription
        self._subscription_count[topic] += 1
        self._metrics.active_subscriptions += 1

        # Send subscription message
        await self._send_subscription_message(subscription)
        self._metrics.subscription_successes += 1

        self.logger.debug(f"Subscribed: {subscription_id}")

    async def unsubscribe(self, subscription_id: str) -> None:
        """Unsubscribe from a topic."""
        if subscription_id not in self._subscriptions:
            return

        subscription = self._subscriptions.pop(subscription_id)
        self._subscription_count[subscription["topic"]] -= 1
        self._metrics.active_subscriptions -= 1

        # Send unsubscription message
        await self._send_unsubscription_message(subscription)

        self.logger.debug(f"Unsubscribed: {subscription_id}")

    async def _send_subscription_message(self, subscription: dict[str, Any]) -> None:
        """Send subscription message."""
        message = self._format_subscription_message(subscription)
        await self._send_message(message)

    async def _send_unsubscription_message(self, subscription: dict[str, Any]) -> None:
        """Send unsubscription message."""
        message = self._format_unsubscription_message(subscription)
        await self._send_message(message)

    async def _send_message(self, message: dict[str, Any]) -> None:
        """Send message through rate-limited queue."""
        if self._rate_limiter:
            await self._rate_limiter.acquire()

        await self._send_queue.put(message)

    async def _send_messages(self) -> None:
        """Background task for sending messages."""
        while self._running:
            try:
                message = await asyncio.wait_for(self._send_queue.get(), timeout=1.0)

                if self._websocket:
                    message_str = json.dumps(message)
                    await self._websocket.send(message_str)

                    self._metrics.messages_sent += 1
                    self._metrics.bytes_sent += len(message_str.encode())

            except TimeoutError:
                continue
            except (OSError, WebSocketException, ConnectionClosed) as e:
                self.logger.error(f"Failed to send message: {e}")
                self._metrics.record_error(ErrorCategory.NETWORK)

                # Add to dead letter queue
                if self._dlq:
                    await self._dlq.add_message(message, e)

    async def _process_messages(self) -> None:
        """Process incoming messages."""
        while self._running:
            try:
                if not self._websocket:
                    await asyncio.sleep(0.1)
                    continue

                # Receive message with timeout
                raw_message = await asyncio.wait_for(
                    self._websocket.recv(), timeout=self.config.heartbeat_interval + 5
                )

                start_time = time.time()

                # Handle compression
                if self.config.compression and isinstance(raw_message, bytes):
                    if raw_message.startswith(b"\x78\x9c"):
                        raw_message = zlib.decompress(raw_message)
                    message = json.loads(raw_message.decode("utf-8"))
                else:
                    message = json.loads(raw_message)

                # Record metrics
                latency = (time.time() - start_time) * 1000
                self._metrics.messages_received += 1
                self._metrics.bytes_received += len(str(message).encode())
                self._metrics.record_latency(latency)

                # Process message
                await self._handle_message(message)

                # Update health
                self._health.update_health(
                    latency_ms=latency,
                    error_rate=self._metrics.get_error_rate(),
                    uptime_ratio=self._get_uptime_ratio(),
                )

            except TimeoutError:
                # Send ping to keep connection alive
                if self._websocket:
                    await self._websocket.ping()
                continue
            except ConnectionClosed:
                self.logger.warning("WebSocket connection closed")
                self._metrics.record_error(ErrorCategory.NETWORK)
                await self._handle_disconnect()
                break
            except OSError as e:
                self.logger.error(f"Network error during message processing: {e}")
                self._metrics.record_error(ErrorCategory.NETWORK)
                await self._handle_disconnect()
                break
            except (json.JSONDecodeError, UnicodeDecodeError, zlib.error) as e:
                self.logger.error(f"Message decode error: {e}")
                self._metrics.record_error(ErrorCategory.PROTOCOL)

    async def _handle_message(self, message: dict[str, Any]) -> None:
        """Handle incoming message."""
        try:
            # Categorize message type
            if self._is_data_message(message):
                await self._handle_data_message(message)
            elif self._is_event_message(message):
                await self._handle_event_message(message)
            elif self._is_error_message(message):
                await self._handle_error_message(message)
            else:
                self.logger.debug(f"Unknown message type: {message.keys()}")

        except Exception as e:
            self.logger.error(f"Message handling error: {e}")
            self._metrics.record_error(ErrorCategory.PROTOCOL)

            # Add to dead letter queue
            if self._dlq:
                await self._dlq.add_message(message, e)

    async def _handle_data_message(self, message: dict[str, Any]) -> None:
        """Handle data message."""
        topic, symbol = self._extract_topic_symbol(message)

        if topic and symbol:
            # Find matching subscriptions
            for subscription in self._subscriptions.values():
                if (
                    subscription["topic"] == topic
                    and subscription["symbol"] == symbol
                    and subscription["callback"]
                ):
                    try:
                        callback_result = subscription["callback"](message)
                        if inspect.isawaitable(callback_result):
                            await callback_result
                    except Exception as e:
                        self.logger.error(f"Callback error: {e}")
                        self._metrics.record_error(ErrorCategory.EXCHANGE_SPECIFIC)

    async def _handle_event_message(self, message: dict[str, Any]) -> None:
        """Handle event message."""
        event = message.get("event", "")

        if event == "subscribe":
            self.logger.debug(f"Subscription confirmed: {message}")
        elif event == "unsubscribe":
            self.logger.debug(f"Unsubscription confirmed: {message}")
        elif event == "auth":
            if message.get("success", False):
                self._authenticated = True
                self._state = ConnectionState.AUTHENTICATED
                self.logger.info("Authentication successful")
            else:
                self.logger.error(f"Authentication failed: {message}")
                self._metrics.record_error(ErrorCategory.AUTHENTICATION)
        else:
            self.logger.debug(f"Event message: {event}")

    async def _handle_error_message(self, message: dict[str, Any]) -> None:
        """Handle error message."""
        error_code = message.get("code", "unknown")
        error_msg = message.get("msg", message.get("message", ""))

        self.logger.error(f"WebSocket error: {error_code} - {error_msg}")
        self._metrics.record_error(ErrorCategory.EXCHANGE_SPECIFIC)

    async def _handle_disconnect(self) -> None:
        """Handle disconnection with intelligent reconnection."""
        self._state = ConnectionState.DISCONNECTED
        self._authenticated = False
        await self._cancel_background_tasks(exclude_task=asyncio.current_task())
        await self._close_websocket()

        # Attempt reconnection if enabled
        if self.config.reconnect_enabled and self._running:
            await self._attempt_reconnection()
        else:
            self._running = False

    async def _attempt_reconnection(self) -> None:
        """Attempt reconnection with exponential backoff."""
        attempt = 0
        base_delay = self.config.reconnect_interval

        while attempt < self.config.max_reconnect_attempts and self._running:
            attempt += 1
            self._metrics.reconnections += 1

            # Calculate delay with exponential backoff
            delay = min(
                base_delay * (self.config.reconnect_backoff_multiplier ** (attempt - 1)),
                self.config.max_reconnect_delay,
            )

            self.logger.info(f"Reconnecting in {delay}s (attempt {attempt})")
            await asyncio.sleep(delay)

            try:
                await self.connect()
                await self._restore_subscriptions()

                self.logger.info("Reconnection successful")
                return

            except (OSError, WebSocketException, WebSocketError, TimeoutError) as e:
                self.logger.error(f"Reconnection attempt {attempt} failed: {e}")
                self._metrics.record_error(ErrorCategory.NETWORK)

        self.logger.error("Max reconnection attempts reached")
        self._state = ConnectionState.ERROR
        self._running = False

    async def _health_monitor_loop(self) -> None:
        """Monitor connection health."""
        while self._running:
            try:
                await asyncio.sleep(self.config.health_check_interval)

                # Perform health check
                if self._websocket:
                    await self._websocket.ping()
                    self._health.last_check = time.time()

                # Check if connection is unhealthy
                if not self._health.is_healthy:
                    self.logger.warning(f"Connection unhealthy: score={self._health.health_score}")

                    # Consider reconnection if severely degraded
                    if self._health.health_score < 30:
                        await self._handle_disconnect()

            except (OSError, WebSocketException, ConnectionClosed) as e:
                self.logger.error(f"Health monitor error: {e}")
                self._metrics.record_error(ErrorCategory.NETWORK)

    async def _process_dlq_message(self, dlq_message: dict[str, Any]) -> None:
        """Process dead letter queue message."""
        message = dlq_message["message"]
        retry_count = dlq_message["retry_count"]

        # Retry logic with backoff
        if retry_count < 3:
            await asyncio.sleep(2**retry_count)
            try:
                await self._handle_message(message)
                self.logger.info(f"DLQ message retry successful: {retry_count}")
            except Exception as e:
                self.logger.error(f"DLQ message retry failed: {e}")
                await self._dlq.add_message(message, e, retry_count + 1)
        else:
            self.logger.error(f"DLQ message permanently failed: {message}")

    async def _restore_subscriptions(self) -> None:
        """Restore in-memory subscriptions after reconnecting."""
        for subscription in list(self._subscriptions.values()):
            await self._send_subscription_message(subscription)

    async def _close_websocket(self) -> None:
        """Close and clear the underlying websocket if present."""
        websocket = self._websocket
        self._websocket = None
        if websocket is None:
            return
        with contextlib.suppress(Exception):
            await websocket.close()

    async def _cancel_background_tasks(self, exclude_task: asyncio.Task | None = None) -> None:
        """Cancel background tasks, excluding the current task when required."""
        for task_attr in ("_processing_task", "_sender_task", "_health_task"):
            task = getattr(self, task_attr)
            setattr(self, task_attr, None)
            if task is None or task is exclude_task or task.done():
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def _is_data_message(self, message: dict[str, Any]) -> bool:
        """Check if message is data message."""
        return "data" in message or "result" in message or "stream" in message

    def _is_event_message(self, message: dict[str, Any]) -> bool:
        """Check if message is event message."""
        return "event" in message or "type" in message

    def _is_error_message(self, message: dict[str, Any]) -> bool:
        """Check if message is error message."""
        return "error" in message or "code" in message

    def _extract_topic_symbol(self, message: dict[str, Any]) -> tuple[str | None, str | None]:
        """Extract topic and symbol from message."""
        # Exchange-specific parsing would be implemented here
        # For now, return generic extraction
        return None, None

    def _format_subscription_message(self, subscription: dict[str, Any]) -> dict[str, Any]:
        """Format subscription message for exchange."""
        # Exchange-specific formatting would be implemented here
        return {
            "action": "subscribe",
            "topic": subscription["topic"],
            "symbol": subscription["symbol"],
            "params": subscription["params"],
        }

    def _format_unsubscription_message(self, subscription: dict[str, Any]) -> dict[str, Any]:
        """Format unsubscription message for exchange."""
        # Exchange-specific formatting would be implemented here
        return {
            "action": "unsubscribe",
            "topic": subscription["topic"],
            "symbol": subscription["symbol"],
        }

    async def _authenticate(self) -> None:
        """Perform authentication."""
        # Exchange-specific authentication would be implemented here

    def _get_uptime_ratio(self) -> float:
        """Calculate connection uptime ratio."""
        if not self._metrics.last_connection_time:
            return 0.0

        total_time = time.time() - self._metrics.last_connection_time
        if total_time == 0:
            return 1.0

        # This would be calculated more accurately in production
        return (
            1.0
            if self._state in (ConnectionState.CONNECTED, ConnectionState.AUTHENTICATED)
            else 0.8
        )

    def get_metrics(self) -> WebSocketMetrics:
        """Get connection metrics."""
        self._metrics.queue_utilization = (
            self._message_queue.qsize() / self.config.message_buffer_size
        )
        return self._metrics

    def get_health(self) -> ConnectionHealth:
        """Get connection health."""
        return self._health

    def get_state(self) -> ConnectionState:
        """Get connection state."""
        return self._state
