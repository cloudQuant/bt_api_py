"""
WebSocket connection management with optimized pooling and backpressure.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import time
import zlib
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import urlparse

import websockets
from websockets import ConnectionClosed, WebSocketException

from bt_api_py.exceptions import RateLimitError, WebSocketError
from bt_api_py.logging_factory import get_logger

from .core.async_context import AsyncTaskGroup
from .core.dependency_injection import inject, singleton
from .core.interfaces import IConnectionManager, IEventBus


@dataclass
class WebSocketConfig:
    """WebSocket configuration."""

    url: str
    exchange_name: str
    max_connections: int = 5
    heartbeat_interval: float = 30.0
    reconnect_interval: float = 5.0
    max_reconnect_attempts: int = 10
    message_queue_size: int = 10000
    compression: bool = True
    subscription_limits: dict[str, int] = field(
        default_factory=lambda: {"ticker": 100, "depth": 50, "trades": 100, "kline": 200}
    )

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
            raise ValueError("url must be a valid ws/wss URL")
        if not isinstance(self.exchange_name, str) or not self.exchange_name.strip():
            raise ValueError("exchange_name must be a non-empty string")
        if not isinstance(self.max_connections, int) or self.max_connections <= 0:
            raise ValueError("max_connections must be a positive integer")
        if self.heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be > 0")
        if self.reconnect_interval <= 0:
            raise ValueError("reconnect_interval must be > 0")
        if not isinstance(self.max_reconnect_attempts, int) or self.max_reconnect_attempts < 0:
            raise ValueError("max_reconnect_attempts must be >= 0")
        if not isinstance(self.message_queue_size, int) or self.message_queue_size <= 0:
            raise ValueError("message_queue_size must be a positive integer")


@dataclass
class Subscription:
    """WebSocket subscription data."""

    id: str
    topic: str
    symbol: str
    params: dict[str, Any]
    callback: Callable[..., Any] | None = None
    created_at: float = field(default_factory=time.time)


class WebSocketConnection:
    """Managed WebSocket connection with auto-reconnect and backpressure."""

    def __init__(self, config: WebSocketConfig, connection_id: str):
        self.config = config
        self.connection_id = connection_id
        self.logger = get_logger(f"ws_{config.exchange_name}_{connection_id}")

        # Connection state
        self._websocket: Any | None = None
        self._connected = False
        self._running = False

        # Subscription management
        self._subscriptions: dict[str, Subscription] = {}
        self._subscription_count: dict[str, int] = defaultdict(int)

        # Message handling
        self._message_queue = asyncio.Queue(maxsize=config.message_queue_size)
        self._processing_task: asyncio.Task | None = None

        # Reconnection
        self._reconnect_attempts = 0
        self._last_disconnect = 0.0

        # Statistics
        self._stats = {
            "messages_received": 0,
            "messages_sent": 0,
            "subscriptions_added": 0,
            "subscriptions_removed": 0,
            "reconnects": 0,
            "last_heartbeat": 0.0,
        }

    async def connect(self) -> None:
        """Connect to WebSocket."""
        if self._connected:
            return

        try:
            if self._websocket is not None:
                await self._close_websocket()
            self._websocket = await websockets.connect(
                self.config.url,
                ping_interval=self.config.heartbeat_interval,
                ping_timeout=10.0,
                close_timeout=10.0,
                max_queue=1000,
            )

            self._connected = True
            self._running = True
            self._reconnect_attempts = 0

            # Start message processing
            self._processing_task = asyncio.create_task(self._process_messages())

            self.logger.info(f"Connected to {self.config.url}")

        except (OSError, WebSocketException) as e:
            self.logger.error(f"Connection failed: {e}")
            raise WebSocketError(self.config.exchange_name, detail=str(e)) from e

    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        self._running = False

        current_task = asyncio.current_task()
        processing_task = self._processing_task
        self._processing_task = None
        if processing_task and processing_task is not current_task:
            processing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await processing_task

        await self._close_websocket()

        self._connected = False
        self.logger.info("Disconnected")

    async def subscribe(self, subscription: Subscription) -> None:
        """Subscribe to a topic."""
        # Check subscription limits
        if self._subscription_count[subscription.topic] >= self.config.subscription_limits.get(
            subscription.topic, 100
        ):
            raise RateLimitError(
                self.config.exchange_name,
                detail=f"Subscription limit exceeded for {subscription.topic}",
            )

        self._subscriptions[subscription.id] = subscription
        self._subscription_count[subscription.topic] += 1
        self._stats["subscriptions_added"] += 1

        # Send subscription message
        await self._send_subscription(subscription)

        self.logger.debug(f"Subscribed: {subscription.id}")

    async def unsubscribe(self, subscription_id: str) -> None:
        """Unsubscribe from a topic."""
        if subscription_id not in self._subscriptions:
            return

        subscription = self._subscriptions.pop(subscription_id)
        self._subscription_count[subscription.topic] -= 1
        self._stats["subscriptions_removed"] += 1

        # Send unsubscription message
        await self._send_unsubscription(subscription)

        self.logger.debug(f"Unsubscribed: {subscription_id}")

    async def _send_subscription(self, subscription: Subscription) -> None:
        """Send subscription message."""
        # Exchange-specific subscription format
        if self.config.exchange_name.startswith("BINANCE"):
            message = {
                "method": "SUBSCRIBE",
                "params": [f"{subscription.symbol.lower()}@{subscription.topic}"],
                "id": subscription.id,
            }
        elif self.config.exchange_name.startswith("OKX"):
            message = {
                "op": "subscribe",
                "args": [{"channel": subscription.topic, "instId": subscription.symbol}],
            }
        else:
            # Generic format
            message = {
                "action": "subscribe",
                "topic": subscription.topic,
                "symbol": subscription.symbol,
                "params": subscription.params,
            }

        await self._send_message(message)

    async def _send_unsubscription(self, subscription: Subscription) -> None:
        """Send unsubscription message."""
        if self.config.exchange_name.startswith("BINANCE"):
            message = {
                "method": "UNSUBSCRIBE",
                "params": [f"{subscription.symbol.lower()}@{subscription.topic}"],
                "id": subscription.id,
            }
        elif self.config.exchange_name.startswith("OKX"):
            message = {
                "op": "unsubscribe",
                "args": [{"channel": subscription.topic, "instId": subscription.symbol}],
            }
        else:
            message = {
                "action": "unsubscribe",
                "topic": subscription.topic,
                "symbol": subscription.symbol,
            }

        await self._send_message(message)

    async def _send_message(self, message: dict[str, Any]) -> None:
        """Send message to WebSocket."""
        if not self._connected or not self._websocket:
            raise WebSocketError(self.config.exchange_name, detail="Not connected")

        try:
            await self._websocket.send(json.dumps(message))
            self._stats["messages_sent"] += 1
        except (OSError, WebSocketException) as e:
            self.logger.error(f"Send failed: {e}")
            raise WebSocketError(self.config.exchange_name, detail=str(e)) from e

    async def _process_messages(self) -> None:
        """Process incoming messages."""
        while self._running:
            try:
                # Get message from WebSocket
                if self._websocket:
                    raw_message = await self._websocket.recv()
                    self._stats["messages_received"] += 1

                    # Handle compression
                    if (
                        self.config.compression
                        and isinstance(raw_message, bytes)
                        and raw_message.startswith(b"\x78\x9c")
                    ):
                        raw_message = zlib.decompress(raw_message)

                    # Parse message
                    if isinstance(raw_message, bytes):
                        message = json.loads(raw_message.decode("utf-8"))
                    else:
                        message = json.loads(str(raw_message))

                    # Process message
                    await self._handle_message(message)

            except ConnectionClosed:
                self.logger.warning("WebSocket connection closed")
                await self._handle_disconnect()
                break
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                self.logger.error(f"Message processing error: {e}")
            except OSError as e:
                self.logger.error(f"Network error during message processing: {e}")
                await self._handle_disconnect()
                break

    async def _handle_message(self, message: dict[str, Any]) -> None:
        """Handle incoming message."""
        # Handle different message types
        if "result" in message or "data" in message:
            # Data message
            await self._handle_data_message(message)
        elif "event" in message:
            # Event message (subscription confirmation, etc.)
            await self._handle_event_message(message)
        else:
            # Unknown message type
            self.logger.debug(f"Unknown message type: {message.keys()}")

    async def _handle_data_message(self, message: dict[str, Any]) -> None:
        """Handle data message."""
        # Extract topic and symbol from message
        topic, symbol = self._extract_topic_symbol(message)

        if topic and symbol:
            # Find relevant subscriptions
            for subscription in self._subscriptions.values():
                if (
                    subscription.topic == topic
                    and subscription.symbol == symbol
                    and subscription.callback
                ):
                    try:
                        callback_result = subscription.callback(message)
                        if inspect.isawaitable(callback_result):
                            await callback_result
                    except Exception as e:
                        self.logger.error(
                            f"Callback error for {self.config.exchange_name} {symbol}@{topic}: {e}"
                        )

    async def _handle_event_message(self, message: dict[str, Any]) -> None:
        """Handle event message."""
        event = message.get("event", "")

        if event == "subscribe":
            self.logger.debug(f"Subscription confirmed: {message}")
        elif event == "unsubscribe":
            self.logger.debug(f"Unsubscription confirmed: {message}")
        elif event == "error":
            self.logger.error(f"WebSocket error: {message}")

    def _extract_topic_symbol(self, message: dict[str, Any]) -> tuple[str | None, str | None]:
        """Extract topic and symbol from message."""
        # Exchange-specific parsing
        if self.config.exchange_name.startswith("BINANCE") and "stream" in message:
            stream = message["stream"]
            parts = stream.split("@")
            if len(parts) == 2:
                symbol = parts[0].upper()
                topic = parts[1]
                return topic, symbol
        elif self.config.exchange_name.startswith("OKX") and "arg" in message and "data" in message:
            arg = message["arg"]
            return arg.get("channel"), arg.get("instId")

        return None, None

    async def _handle_disconnect(self) -> None:
        """Handle disconnection."""
        self._connected = False
        self._last_disconnect = time.time()
        await self._close_websocket()

        # Attempt reconnection
        while self._running and self._reconnect_attempts < self.config.max_reconnect_attempts:
            self._reconnect_attempts += 1
            self._stats["reconnects"] += 1

            wait_time = min(self.config.reconnect_interval * (2**self._reconnect_attempts), 60)
            self.logger.info(f"Reconnecting in {wait_time}s (attempt {self._reconnect_attempts})")

            await asyncio.sleep(wait_time)

            try:
                await self.connect()
                await self._restore_subscriptions()
                return
            except WebSocketError as e:
                self.logger.warning(
                    "Reconnection attempt %d/%d failed for %s: %s",
                    self._reconnect_attempts,
                    self.config.max_reconnect_attempts,
                    self.connection_id,
                    e,
                )
                self._connected = False
                await self._close_websocket()

        self._running = False
        self.logger.error("Max reconnection attempts reached")

    async def _restore_subscriptions(self) -> None:
        """Restore in-memory subscriptions after reconnecting."""
        for subscription in list(self._subscriptions.values()):
            await self._send_subscription(subscription)

    async def _close_websocket(self) -> None:
        """Close and clear the underlying websocket if present."""
        websocket = self._websocket
        self._websocket = None
        if websocket is None:
            return
        with contextlib.suppress(Exception):
            await websocket.close()

    @property
    def connected(self) -> bool:
        """Whether the connection is currently active."""
        return self._connected

    @property
    def running(self) -> bool:
        """Whether the connection loop is running."""
        return self._running

    @property
    def subscription_count(self) -> int:
        """Number of active subscriptions."""
        return len(self._subscriptions)

    def has_subscription(self, subscription_id: str) -> bool:
        """Check if a subscription exists."""
        return subscription_id in self._subscriptions

    def get_stats(self) -> dict[str, Any]:
        """Get connection statistics."""
        return {
            **self._stats,
            "connected": self._connected,
            "subscriptions": len(self._subscriptions),
            "connection_id": self.connection_id,
            "reconnect_attempts": self._reconnect_attempts,
        }


@singleton(cast("type[Any]", IConnectionManager))
class WebSocketManager(IConnectionManager):
    """WebSocket connection manager with pooling and load balancing."""

    def __init__(self, event_bus: IEventBus = inject(cast("type[Any]", IEventBus))):
        self.event_bus = event_bus
        self.logger = get_logger("websocket_manager")

        # Connection pools
        self._pools: dict[str, list[WebSocketConnection]] = {}
        self._pool_configs: dict[str, WebSocketConfig] = {}
        self._pool_locks: dict[str, asyncio.Lock] = {}

        # Connection selection
        self._round_robin: dict[str, int] = defaultdict(int)

        # Task management
        self._task_group = AsyncTaskGroup()

    async def add_exchange(self, config: WebSocketConfig) -> None:
        """Add exchange WebSocket configuration."""
        self._pool_configs[config.exchange_name] = config
        self._pool_locks[config.exchange_name] = asyncio.Lock()
        self._pools[config.exchange_name] = []

        self.logger.info(f"Added WebSocket pool for {config.exchange_name}")

    async def get_connection(self, exchange_name: str) -> WebSocketConnection:
        """Get or create a WebSocket connection."""
        if exchange_name not in self._pools:
            raise ValueError(f"Exchange {exchange_name} not configured")

        async with self._pool_locks[exchange_name]:
            pool = self._pools[exchange_name]
            config = self._pool_configs[exchange_name]

            # Find available connection
            available_connection = None
            for conn in pool:
                if (
                    conn.connected and conn.subscription_count < 50
                ):  # Limit subscriptions per connection
                    available_connection = conn
                    break

            # Create new connection if needed
            if not available_connection and len(pool) < config.max_connections:
                connection_id = f"{exchange_name}_{len(pool)}"
                available_connection = WebSocketConnection(config, connection_id)

                await available_connection.connect()
                pool.append(available_connection)

                # Monitor connection
                await self._task_group.create_task(self._monitor_connection(available_connection))

            if not available_connection:
                # Use round-robin if all connections are busy
                index = self._round_robin[exchange_name] % len(pool)
                available_connection = pool[index]
                self._round_robin[exchange_name] += 1

            return available_connection

    async def release_connection(self, exchange_name: str, connection: WebSocketConnection) -> None:
        """Release connection (no-op for WebSockets as they're persistent)."""

    async def close_all(self) -> None:
        """Close all WebSocket connections."""
        for exchange_name, pool in self._pools.items():
            for connection in pool:
                await connection.disconnect()
            self.logger.info(f"Closed {len(pool)} connections for {exchange_name}")
            pool.clear()
            self._round_robin[exchange_name] = 0

        await self._task_group.cancel_all()
        self._task_group = AsyncTaskGroup()

    async def stop(self) -> None:
        """Compatibility shutdown entrypoint for deterministic cleanup."""
        await self.close_all()

    async def subscribe(
        self,
        exchange_name: str,
        topic: str,
        symbol: str,
        callback: Callable[..., Any],
        params: dict[str, Any] | None = None,
    ) -> str:
        """Subscribe to WebSocket topic."""
        connection = await self.get_connection(exchange_name)

        subscription_id = f"{exchange_name}_{topic}_{symbol}_{int(time.time())}"
        subscription = Subscription(
            id=subscription_id, topic=topic, symbol=symbol, params=params or {}, callback=callback
        )

        await connection.subscribe(subscription)

        # Publish subscription event
        await self.event_bus.publish_async(
            "websocket_subscribed",
            {
                "exchange": exchange_name,
                "topic": topic,
                "symbol": symbol,
                "subscription_id": subscription_id,
            },
        )

        return subscription_id

    async def unsubscribe(self, exchange_name: str, subscription_id: str) -> None:
        """Unsubscribe from WebSocket topic."""
        if exchange_name not in self._pool_locks:
            raise ValueError(f"Exchange {exchange_name} not configured")

        async with self._pool_locks[exchange_name]:
            pool = self._pools[exchange_name]

            for connection in pool:
                if connection.has_subscription(subscription_id):
                    await connection.unsubscribe(subscription_id)
                    break

        # Publish unsubscription event
        await self.event_bus.publish_async(
            "websocket_unsubscribed",
            {"exchange": exchange_name, "subscription_id": subscription_id},
        )

    async def _monitor_connection(self, connection: WebSocketConnection) -> None:
        """Monitor connection health."""
        while connection.running:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds

                # Send heartbeat if needed
                if connection.connected and connection._websocket:
                    await connection._websocket.ping()
                    connection._stats["last_heartbeat"] = time.time()

            except (OSError, WebSocketException) as e:
                self.logger.error(f"Connection monitoring error: {e}")
                break

    def get_pool_stats(self) -> dict[str, Any]:
        """Get WebSocket pool statistics."""
        stats = {}
        for exchange_name, pool in self._pools.items():
            exchange_stats = {
                "total_connections": len(pool),
                "active_connections": sum(1 for conn in pool if conn.connected),
                "total_subscriptions": sum(conn.subscription_count for conn in pool),
                "connections": [],
            }

            for conn in pool:
                exchange_stats["connections"].append(conn.get_stats())

            stats[exchange_name] = exchange_stats

        return stats

    def get_connection_stats(self) -> dict[str, Any]:
        """Compatibility alias for the connection manager interface."""
        return self.get_pool_stats()
