"""
Production-grade WebSocket manager with intelligent pooling, load balancing, and comprehensive monitoring.
Supports 73+ exchanges with high reliability and performance optimization.
"""

import asyncio
import contextlib
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bt_api_py.core.async_context import AsyncTaskGroup
from bt_api_py.core.dependency_injection import inject, singleton
from bt_api_py.core.interfaces import IEventBus
from bt_api_py.logging_factory import get_logger
from bt_api_py.websocket.advanced_connection_manager import (
    AdvancedWebSocketConnection,
    ConnectionState,
    WebSocketConfig,
    WebSocketMetrics,
)


@dataclass
class PoolConfiguration:
    """WebSocket pool configuration."""

    # Pool sizing
    min_connections: int = 1
    max_connections: int = 10
    connection_timeout: float = 30.0

    # Load balancing
    load_balance_strategy: str = "round_robin"  # round_robin, least_connections, random, weighted
    health_check_interval: float = 30.0
    connection_max_age: float = 3600.0  # Max connection age in seconds

    # Performance optimization
    message_batching: bool = True
    batch_size: int = 10
    batch_timeout: float = 0.1  # seconds

    # Failover
    failover_enabled: bool = True
    failover_threshold: float = 0.5  # Health score threshold for failover
    failover_timeout: float = 60.0

    # Monitoring
    metrics_enabled: bool = True
    detailed_logging: bool = False


@dataclass
class ConnectionWrapper:
    """Wrapper for WebSocket connection with pool metadata."""

    connection: AdvancedWebSocketConnection
    created_at: float = 0.0
    last_used: float = 0.0
    usage_count: int = 0
    health_score: float = 100.0
    in_use: bool = False

    def __post_init__(self):
        now = time.time()
        if self.created_at == 0.0:
            self.created_at = now
        if self.last_used == 0.0:
            self.last_used = now


class LoadBalancer:
    """Intelligent load balancer for WebSocket connections."""

    def __init__(self, strategy: str = "round_robin"):
        self.strategy = strategy
        self._round_robin_index = 0
        self._random_state = None

    def select_connection(
        self, connections: list[ConnectionWrapper], exclude_unhealthy: bool = True
    ) -> ConnectionWrapper | None:
        """Select best connection based on strategy."""
        if not connections:
            return None

        # Filter unhealthy connections if requested
        if exclude_unhealthy:
            healthy_connections = [
                conn
                for conn in connections
                if conn.health_score >= 50
                and conn.connection.get_state() == ConnectionState.CONNECTED
            ]
            if not healthy_connections:
                # Fallback to all connections if none are healthy
                healthy_connections = connections
        else:
            healthy_connections = connections

        if not healthy_connections:
            return None

        if self.strategy == "round_robin":
            return self._select_round_robin(healthy_connections)
        elif self.strategy == "least_connections":
            return self._select_least_connections(healthy_connections)
        elif self.strategy == "random":
            return self._select_random(healthy_connections)
        elif self.strategy == "weighted":
            return self._select_weighted(healthy_connections)
        else:
            # Default to round_robin
            return self._select_round_robin(healthy_connections)

    def _select_round_robin(self, connections: list[ConnectionWrapper]) -> ConnectionWrapper:
        """Round-robin selection."""
        connection = connections[self._round_robin_index % len(connections)]
        self._round_robin_index += 1
        return connection

    def _select_least_connections(self, connections: list[ConnectionWrapper]) -> ConnectionWrapper:
        """Select connection with least usage."""
        return min(connections, key=lambda conn: conn.usage_count)

    def _select_random(self, connections: list[ConnectionWrapper]) -> ConnectionWrapper:
        """Random selection."""
        import random

        return random.choice(connections)

    def _select_weighted(self, connections: list[ConnectionWrapper]) -> ConnectionWrapper:
        """Weighted selection based on health score and usage."""
        total_weight = sum(conn.health_score for conn in connections)
        if total_weight == 0:
            return connections[0]

        import random

        r = random.uniform(0, total_weight)
        cumulative_weight = 0

        for conn in connections:
            cumulative_weight += conn.health_score
            if cumulative_weight >= r:
                return conn

        return connections[-1]


class PerformanceMonitor:
    """Real-time performance monitoring for WebSocket pools."""

    def __init__(self, pool_name: str):
        self.pool_name = pool_name
        self.logger = get_logger(f"perf_monitor_{pool_name}")

        # Metrics collection
        self._metrics_history = defaultdict(lambda: defaultdict(list))
        self._alerts = []
        self._alert_callbacks = []

        # Performance thresholds
        self.thresholds = {
            "max_latency_ms": 1000.0,
            "max_error_rate": 10.0,  # per minute
            "min_health_score": 70.0,
            "max_queue_utilization": 0.8,
            "max_memory_usage_mb": 500.0,
        }

    def record_metrics(self, connection_id: str, metrics: WebSocketMetrics) -> None:
        """Record connection metrics."""
        timestamp = time.time()

        # Store metrics history
        self._metrics_history[connection_id]["latency"].append(
            (timestamp, metrics.get_avg_latency())
        )
        self._metrics_history[connection_id]["error_rate"].append(
            (timestamp, metrics.get_error_rate())
        )
        # Health score needs to be passed separately or obtained from connection
        # For now, use a default or skip health score in metrics

        # Keep only last 1000 samples per metric
        for metric_type in self._metrics_history[connection_id]:
            if len(self._metrics_history[connection_id][metric_type]) > 1000:
                self._metrics_history[connection_id][metric_type] = self._metrics_history[
                    connection_id
                ][metric_type][-1000:]

        # Check for performance issues
        self._check_performance_alerts(connection_id, metrics)

    def _check_performance_alerts(self, connection_id: str, metrics: WebSocketMetrics) -> None:
        """Check for performance alerts."""
        alerts = []

        # Latency alert
        if metrics.get_avg_latency() > self.thresholds["max_latency_ms"]:
            alerts.append(
                {
                    "type": "high_latency",
                    "connection_id": connection_id,
                    "value": metrics.get_avg_latency(),
                    "threshold": self.thresholds["max_latency_ms"],
                    "timestamp": time.time(),
                }
            )

        # Error rate alert
        if metrics.get_error_rate() > self.thresholds["max_error_rate"]:
            alerts.append(
                {
                    "type": "high_error_rate",
                    "connection_id": connection_id,
                    "value": metrics.get_error_rate(),
                    "threshold": self.thresholds["max_error_rate"],
                    "timestamp": time.time(),
                }
            )

        # Health score alert - skip for now as health is managed separately
        # if metrics.connection.health_score < self.thresholds["min_health_score"]:
        #     alerts.append(
        #         {
        #             "type": "low_health_score",
        #             "connection_id": connection_id,
        #             "value": metrics.connection.health_score,
        #             "threshold": self.thresholds["min_health_score"],
        #             "timestamp": time.time(),
        #         }
        #     )

        # Queue utilization alert
        if metrics.queue_utilization > self.thresholds["max_queue_utilization"]:
            alerts.append(
                {
                    "type": "high_queue_utilization",
                    "connection_id": connection_id,
                    "value": metrics.queue_utilization,
                    "threshold": self.thresholds["max_queue_utilization"],
                    "timestamp": time.time(),
                }
            )

        # Trigger alert callbacks
        for alert in alerts:
            self._alerts.append(alert)
            for callback in self._alert_callbacks:
                try:
                    callback(alert)
                except Exception as e:
                    self.logger.error(f"Alert callback error: {e}")

    def add_alert_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Add callback for performance alerts."""
        self._alert_callbacks.append(callback)

    def get_performance_summary(self, time_window: float = 300.0) -> dict[str, Any]:
        """Get performance summary for the last time_window seconds."""
        now = time.time()
        cutoff_time = now - time_window

        summary = {
            "pool_name": self.pool_name,
            "time_window": time_window,
            "connection_count": len(self._metrics_history),
            "total_alerts": len([a for a in self._alerts if a["timestamp"] > cutoff_time]),
            "connections": {},
        }

        for connection_id, metrics_data in self._metrics_history.items():
            conn_summary = {
                "avg_latency": 0.0,
                "max_latency": 0.0,
                "avg_error_rate": 0.0,
                "max_error_rate": 0.0,
                "avg_health_score": 100.0,
                "min_health_score": 100.0,
            }

            # Calculate averages and maximums
            for metric_type, values in metrics_data.items():
                recent_values = [v for t, v in values if t > cutoff_time]
                if recent_values:
                    if metric_type == "latency":
                        conn_summary["avg_latency"] = sum(recent_values) / len(recent_values)
                        conn_summary["max_latency"] = max(recent_values)
                    elif metric_type == "error_rate":
                        conn_summary["avg_error_rate"] = sum(recent_values) / len(recent_values)
                        conn_summary["max_error_rate"] = max(recent_values)
                    elif metric_type == "health_score":
                        conn_summary["avg_health_score"] = sum(recent_values) / len(recent_values)
                        conn_summary["min_health_score"] = min(recent_values)

            summary["connections"][connection_id] = conn_summary

        return summary

    def get_recent_alerts(self, time_window: float = 3600.0) -> list[dict[str, Any]]:
        """Get recent alerts within time_window."""
        cutoff_time = time.time() - time_window
        return [alert for alert in self._alerts if alert["timestamp"] > cutoff_time]


@singleton
class AdvancedWebSocketManager:
    """Advanced WebSocket connection manager with intelligent pooling and monitoring."""

    def __init__(self, event_bus: IEventBus = inject(IEventBus)):
        self.event_bus = event_bus
        self.logger = get_logger("advanced_websocket_manager")

        # Connection pools
        self._pools: dict[str, list[ConnectionWrapper]] = {}
        self._pool_configs: dict[str, WebSocketConfig] = {}
        self._pool_metadata: dict[str, PoolConfiguration] = {}
        self._pool_locks: dict[str, asyncio.Lock] = {}

        # Load balancers
        self._load_balancers: dict[str, LoadBalancer] = {}

        # Performance monitors
        self._monitors: dict[str, PerformanceMonitor] = {}

        # Task management
        self._task_group = AsyncTaskGroup()
        self._running = False

        # Global metrics
        self._global_metrics = {
            "total_connections": 0,
            "active_connections": 0,
            "total_subscriptions": 0,
            "total_messages_sent": 0,
            "total_messages_received": 0,
            "total_errors": 0,
        }

        # Cleanup tasks
        self._cleanup_interval = 300.0  # 5 minutes
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the WebSocket manager."""
        if self._running:
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self.logger.info("Advanced WebSocket manager started")

    async def stop(self) -> None:
        """Stop the WebSocket manager."""
        if not self._running:
            return

        self._running = False

        # Cancel cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task

        # Close all connections
        await self.close_all()

        # Cancel all tasks
        await self._task_group.cancel_all()

        self.logger.info("Advanced WebSocket manager stopped")

    async def add_exchange(
        self, config: WebSocketConfig, pool_config: PoolConfiguration | None = None
    ) -> None:
        """Add exchange WebSocket configuration."""
        exchange_name = config.exchange_name

        # Initialize pool if not exists
        if exchange_name not in self._pools:
            self._pools[exchange_name] = []
            self._pool_locks[exchange_name] = asyncio.Lock()
            self._pool_configs[exchange_name] = config
            self._pool_metadata[exchange_name] = pool_config or PoolConfiguration()
            self._load_balancers[exchange_name] = LoadBalancer(
                self._pool_metadata[exchange_name].load_balance_strategy
            )
            self._monitors[exchange_name] = PerformanceMonitor(exchange_name)

            # Set up performance alerts
            self._monitors[exchange_name].add_alert_callback(self._handle_performance_alert)

        self.logger.info(f"Added WebSocket pool for {exchange_name}")

    async def get_connection(self, exchange_name: str) -> AdvancedWebSocketConnection:
        """Get or create a WebSocket connection with intelligent load balancing."""
        if exchange_name not in self._pools:
            raise ValueError(f"Exchange {exchange_name} not configured")

        async with self._pool_locks[exchange_name]:
            pool = self._pools[exchange_name]
            config = self._pool_configs[exchange_name]
            pool_config = self._pool_metadata[exchange_name]
            load_balancer = self._load_balancers[exchange_name]

            # Select best connection
            selected_wrapper = load_balancer.select_connection(pool)

            # Create new connection if needed
            if not selected_wrapper and len(pool) < pool_config.max_connections:
                connection_id = f"{exchange_name}_{len(pool)}"
                new_connection = AdvancedWebSocketConnection(config, connection_id)
                wrapper = ConnectionWrapper(
                    connection=new_connection, created_at=time.time(), last_used=time.time()
                )

                # Connect the new connection
                await new_connection.connect()
                pool.append(wrapper)

                # Start monitoring
                await self._task_group.create_task(self._monitor_connection(exchange_name, wrapper))

                selected_wrapper = wrapper
                self._global_metrics["total_connections"] += 1

            if not selected_wrapper:
                # Pool is full, use existing connection
                available_wrappers = [w for w in pool if not w.in_use]
                if available_wrappers:
                    selected_wrapper = available_wrappers[0]
                else:
                    # All connections in use, fall back to least used
                    selected_wrapper = min(pool, key=lambda w: w.usage_count)

            # Update wrapper usage
            selected_wrapper.last_used = time.time()
            selected_wrapper.usage_count += 1
            selected_wrapper.in_use = True
            selected_wrapper.health_score = selected_wrapper.connection.get_health().health_score

            return selected_wrapper.connection

    async def release_connection(
        self, exchange_name: str, connection: AdvancedWebSocketConnection
    ) -> None:
        """Release connection back to pool."""
        if exchange_name not in self._pools:
            return

        async with self._pool_locks[exchange_name]:
            pool = self._pools[exchange_name]
            for wrapper in pool:
                if wrapper.connection == connection:
                    wrapper.in_use = False
                    break

    async def subscribe(
        self,
        exchange_name: str,
        topic: str,
        symbol: str,
        callback: Callable,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Subscribe to WebSocket topic with intelligent connection selection."""
        connection = await self.get_connection(exchange_name)

        subscription_id = f"{exchange_name}_{topic}_{symbol}_{int(time.time())}"

        try:
            await connection.subscribe(subscription_id, topic, symbol, params, callback)
            self._global_metrics["total_subscriptions"] += 1

            # Publish subscription event
            self.event_bus.publish(
                "websocket_subscribed",
                {
                    "exchange": exchange_name,
                    "topic": topic,
                    "symbol": symbol,
                    "subscription_id": subscription_id,
                },
            )

            return subscription_id

        except Exception:
            await self.release_connection(exchange_name, connection)
            raise

    async def unsubscribe(self, exchange_name: str, subscription_id: str) -> None:
        """Unsubscribe from WebSocket topic."""
        async with self._pool_locks[exchange_name]:
            pool = self._pools[exchange_name]

            for wrapper in pool:
                if subscription_id in wrapper.connection._subscriptions:
                    await wrapper.connection.unsubscribe(subscription_id)
                    self._global_metrics["total_subscriptions"] -= 1
                    break

        # Publish unsubscription event
        self.event_bus.publish(
            "websocket_unsubscribed",
            {"exchange": exchange_name, "subscription_id": subscription_id},
        )

    async def close_all(self) -> None:
        """Close all WebSocket connections."""
        for exchange_name, pool in self._pools.items():
            for wrapper in pool:
                await wrapper.connection.disconnect()
            self.logger.info(f"Closed {len(pool)} connections for {exchange_name}")

    async def _monitor_connection(self, exchange_name: str, wrapper: ConnectionWrapper) -> None:
        """Monitor connection health and performance."""
        connection = wrapper.connection
        monitor = self._monitors[exchange_name]
        pool_config = self._pool_metadata[exchange_name]

        while self._running:
            try:
                await asyncio.sleep(pool_config.health_check_interval)

                # Update metrics
                metrics = connection.get_metrics()
                connection_state = connection.get_state()
                monitor.record_metrics(connection.connection_id, metrics)

                # Update global metrics
                self._update_global_metrics(metrics, connection_state)

                # Update wrapper health score
                connection_health = connection.get_health()
                wrapper.health_score = connection_health.health_score if connection_health else 0.0

                # Check for connection aging
                age = time.time() - wrapper.created_at
                if age > pool_config.connection_max_age:
                    self.logger.info(
                        f"Connection {connection.connection_id} exceeded max age, reconnecting"
                    )
                    await connection.disconnect()
                    await connection.connect()
                    wrapper.created_at = time.time()

                # Check for failover needs
                if (
                    pool_config.failover_enabled
                    and wrapper.health_score < pool_config.failover_threshold
                ):
                    self.logger.warning(
                        f"Connection {connection.connection_id} health degraded, considering failover"
                    )
                    await self._handle_failover(exchange_name, wrapper)

            except Exception as e:
                self.logger.error(f"Connection monitoring error: {e}")

    async def _handle_failover(
        self, exchange_name: str, degraded_wrapper: ConnectionWrapper
    ) -> None:
        """Handle connection failover."""
        pool = self._pools[exchange_name]
        config = self._pool_configs[exchange_name]

        # Try to create a new healthy connection
        if len(pool) < self._pool_metadata[exchange_name].max_connections:
            try:
                connection_id = f"{exchange_name}_{len(pool)}_failover"
                new_connection = AdvancedWebSocketConnection(config, connection_id)
                new_wrapper = ConnectionWrapper(
                    connection=new_connection, created_at=time.time(), last_used=time.time()
                )

                await new_connection.connect()
                pool.append(new_wrapper)

                # Start monitoring new connection
                await self._task_group.create_task(
                    self._monitor_connection(exchange_name, new_wrapper)
                )

                self.logger.info(f"Created failover connection {connection_id}")

            except Exception as e:
                self.logger.error(f"Failed to create failover connection: {e}")

    def _update_global_metrics(
        self, metrics: WebSocketMetrics, connection_state: ConnectionState
    ) -> None:
        """Update global metrics."""
        self._global_metrics["active_connections"] = (
            1 if connection_state == ConnectionState.CONNECTED else 0
        )
        self._global_metrics["total_messages_sent"] += metrics.messages_sent
        self._global_metrics["total_messages_received"] += metrics.messages_received
        self._global_metrics["total_errors"] += sum(metrics.errors_by_category.values())

    def _handle_performance_alert(self, alert: dict[str, Any]) -> None:
        """Handle performance alerts."""
        self.logger.warning(f"Performance alert: {alert}")

        # Publish alert event
        self.event_bus.publish("performance_alert", alert)

    async def _cleanup_loop(self) -> None:
        """Periodic cleanup of idle connections."""
        while self._running:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup_idle_connections()
            except Exception as e:
                self.logger.error(f"Cleanup error: {e}")

    async def _cleanup_idle_connections(self) -> None:
        """Clean up idle connections."""
        for exchange_name, pool in self._pools.items():
            pool_config = self._pool_metadata[exchange_name]

            async with self._pool_locks[exchange_name]:
                # Find idle connections
                idle_wrappers = [
                    wrapper
                    for wrapper in pool
                    if (
                        not wrapper.in_use
                        and len(pool) > pool_config.min_connections
                        and time.time() - wrapper.last_used > pool_config.connection_timeout
                    )
                ]

                # Remove idle connections
                for wrapper in idle_wrappers:
                    await wrapper.connection.disconnect()
                    pool.remove(wrapper)
                    self.logger.info(f"Removed idle connection {wrapper.connection.connection_id}")

    def get_pool_stats(self) -> dict[str, Any]:
        """Get comprehensive WebSocket pool statistics."""
        stats: dict[str, Any] = {
            "global_metrics": self._global_metrics.copy(),
            "pools": {},
        }

        for exchange_name, pool in self._pools.items():
            pool_config = self._pool_metadata[exchange_name]
            monitor = self._monitors[exchange_name]

            connections_list: list[dict[str, Any]] = []
            pool_stats: dict[str, Any] = {
                "total_connections": len(pool),
                "active_connections": sum(
                    1 for w in pool if w.connection.get_state() == ConnectionState.CONNECTED
                ),
                "connections_in_use": sum(1 for w in pool if w.in_use),
                "total_subscriptions": sum(len(w.connection._subscriptions) for w in pool),
                "avg_health_score": sum(w.health_score for w in pool) / len(pool) if pool else 0,
                "config": {
                    "min_connections": pool_config.min_connections,
                    "max_connections": pool_config.max_connections,
                    "load_balance_strategy": pool_config.load_balance_strategy,
                },
                "connections": connections_list,
            }

            for wrapper in pool:
                connection_metrics = wrapper.connection.get_metrics()
                connection_health = wrapper.connection.get_health()

                conn_stats = {
                    "connection_id": wrapper.connection.connection_id,
                    "state": wrapper.connection.get_state().value,
                    "created_at": wrapper.created_at,
                    "last_used": wrapper.last_used,
                    "usage_count": wrapper.usage_count,
                    "health_score": wrapper.health_score,
                    "in_use": wrapper.in_use,
                    "subscriptions": len(wrapper.connection._subscriptions),
                    "metrics": {
                        "messages_sent": connection_metrics.messages_sent,
                        "messages_received": connection_metrics.messages_received,
                        "avg_latency_ms": connection_metrics.get_avg_latency(),
                        "p95_latency_ms": connection_metrics.get_p95_latency(),
                        "error_rate": connection_metrics.get_error_rate(),
                        "queue_utilization": connection_metrics.queue_utilization,
                    },
                    "health": {
                        "is_healthy": connection_health.is_healthy,
                        "health_score": connection_health.health_score,
                        "consecutive_failures": connection_health.consecutive_failures,
                    },
                }

                connections_list.append(conn_stats)

            # Add performance summary
            pool_stats["performance_summary"] = monitor.get_performance_summary()
            pool_stats["recent_alerts"] = monitor.get_recent_alerts()

            stats["pools"][exchange_name] = pool_stats

        return stats

    def get_connection_health(
        self, exchange_name: str, connection_id: str
    ) -> dict[str, Any] | None:
        """Get detailed health information for a specific connection."""
        if exchange_name not in self._pools:
            return None

        for wrapper in self._pools[exchange_name]:
            if wrapper.connection.connection_id == connection_id:
                metrics = wrapper.connection.get_metrics()
                health = wrapper.connection.get_health()

                return {
                    "connection_id": connection_id,
                    "exchange_name": exchange_name,
                    "state": wrapper.connection.get_state().value,
                    "wrapper_info": {
                        "created_at": wrapper.created_at,
                        "last_used": wrapper.last_used,
                        "usage_count": wrapper.usage_count,
                        "health_score": wrapper.health_score,
                        "in_use": wrapper.in_use,
                    },
                    "health": {
                        "is_healthy": health.is_healthy,
                        "health_score": health.health_score,
                        "consecutive_failures": health.consecutive_failures,
                        "last_check": health.last_check,
                    },
                    "metrics": {
                        "messages_sent": metrics.messages_sent,
                        "messages_received": metrics.messages_received,
                        "bytes_sent": metrics.bytes_sent,
                        "bytes_received": metrics.bytes_received,
                        "avg_latency_ms": metrics.get_avg_latency(),
                        "p95_latency_ms": metrics.get_p95_latency(),
                        "error_rate": metrics.get_error_rate(),
                        "queue_utilization": metrics.queue_utilization,
                        "active_subscriptions": metrics.active_subscriptions,
                    },
                    "errors_by_category": dict(metrics.errors_by_category),
                }

        return None
