"""
WebSocket module initialization and integration.
Provides unified API for advanced WebSocket functionality with monitoring and optimizations.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from .advanced_connection_manager import (
    AdvancedWebSocketConnection,
    ConnectionState,
    ErrorCategory,
    WebSocketConfig,
)
from .advanced_websocket_manager import AdvancedWebSocketManager, PoolConfiguration
from .exchange_adapters import (
    AuthenticationType,
    BinanceWebSocketAdapter,
    ExchangeCredentials,
    ExchangeType,
    ExchangeWebSocketAdapter,
    OKXWebSocketAdapter,
    WebSocketAdapterFactory,
)
from .monitoring import (
    AlertManager,
    AlertSeverity,
    MetricsCollector,
    PerformanceAlert,
    WebSocketBenchmark,
    WebSocketMonitor,
)


class WebSocketSystem:
    """Unified WebSocket system with all components integrated."""

    def __init__(self) -> None:
        """Initialize WebSocket system."""
        self.manager: AdvancedWebSocketManager | None = None
        self.monitor: WebSocketMonitor | None = None
        self._initialized: bool = False

    async def initialize(self) -> None:
        """Initialize WebSocket system."""
        if self._initialized:
            return

        self.manager = AdvancedWebSocketManager()
        self.monitor = WebSocketMonitor(self.manager)

        await self.manager.start()
        await self.monitor.start()

        self._initialized = True

    async def shutdown(self) -> None:
        """Shutdown WebSocket system."""
        if not self._initialized:
            return

        if self.monitor:
            await self.monitor.stop()

        if self.manager:
            await self.manager.stop()

        self.manager = None
        self.monitor = None
        self._initialized = False

    def get_manager(self) -> AdvancedWebSocketManager:
        """Get WebSocket manager.

        Returns:
            WebSocket manager instance.

        Raises:
            RuntimeError: If WebSocket system not initialized.
        """
        if not self.manager:
            raise RuntimeError("WebSocket system not initialized")
        return self.manager

    def get_monitor(self) -> WebSocketMonitor:
        """Get WebSocket monitor.

        Returns:
            WebSocket monitor instance.

        Raises:
            RuntimeError: If WebSocket system not initialized.
        """
        if not self.monitor:
            raise RuntimeError("WebSocket system not initialized")
        return self.monitor


async def _ensure_exchange_registered(
    manager: AdvancedWebSocketManager,
    exchange_name: str,
    pool_config: PoolConfiguration | None,
) -> None:
    """Register an exchange with default adapter-derived websocket config if missing."""
    if exchange_name in manager._pools:
        return

    adapter = WebSocketAdapterFactory.create_adapter(exchange_name)
    endpoints = adapter.get_endpoints(f"wss://stream.{exchange_name.lower()}.com")

    config = WebSocketConfig(
        url=endpoints[0],
        exchange_name=exchange_name,
        endpoints=endpoints[1:],
        subscription_limits=adapter.get_subscription_limits(),
    )

    await manager.add_exchange(config, pool_config)


# Global instance
_websocket_system = WebSocketSystem()


async def get_websocket_manager() -> AdvancedWebSocketManager:
    """Get global WebSocket manager."""
    await _websocket_system.initialize()
    return _websocket_system.get_manager()


async def get_websocket_monitor() -> WebSocketMonitor:
    """Get global WebSocket monitor."""
    await _websocket_system.initialize()
    return _websocket_system.get_monitor()


# Convenience functions for common operations
async def subscribe_to_ticker(
    exchange_name: str,
    symbol: str,
    callback: Callable[..., Any] | Callable[..., Awaitable[Any]],
    pool_config: PoolConfiguration | None = None,
) -> str:
    """Subscribe to ticker data for a symbol.

    Args:
        exchange_name: Exchange identifier.
        symbol: Trading symbol.
        callback: Callback function for ticker updates.
        pool_config: Optional pool configuration.

    Returns:
        Subscription ID.
    """
    manager = await get_websocket_manager()
    await _ensure_exchange_registered(manager, exchange_name, pool_config)

    return await manager.subscribe(exchange_name, "ticker", symbol, callback)


async def subscribe_to_depth(
    exchange_name: str,
    symbol: str,
    callback: Callable[..., Any] | Callable[..., Awaitable[Any]],
    level: int = 20,
    pool_config: PoolConfiguration | None = None,
) -> str:
    """Subscribe to order book depth for a symbol.

    Args:
        exchange_name: Exchange identifier.
        symbol: Trading symbol.
        callback: Callback function for depth updates.
        level: Order book depth level. Defaults to 20.
        pool_config: Optional pool configuration.

    Returns:
        Subscription ID.
    """
    manager = await get_websocket_manager()
    await _ensure_exchange_registered(manager, exchange_name, pool_config)

    params = {"level": str(level)} if level != 20 else {}
    return await manager.subscribe(exchange_name, "depth", symbol, callback, params)


async def subscribe_to_trades(
    exchange_name: str,
    symbol: str,
    callback: Callable[..., Any] | Callable[..., Awaitable[Any]],
    pool_config: PoolConfiguration | None = None,
) -> str:
    """Subscribe to trade data for a symbol.

    Args:
        exchange_name: Exchange identifier.
        symbol: Trading symbol.
        callback: Callback function for trade updates.
        pool_config: Optional pool configuration.

    Returns:
        Subscription ID.
    """
    manager = await get_websocket_manager()
    await _ensure_exchange_registered(manager, exchange_name, pool_config)

    return await manager.subscribe(exchange_name, "trades", symbol, callback)


async def subscribe_to_klines(
    exchange_name: str,
    symbol: str,
    interval: str,
    callback: Callable[..., Any] | Callable[..., Awaitable[Any]],
    pool_config: PoolConfiguration | None = None,
) -> str:
    """Subscribe to candlestick data for a symbol.

    Args:
        exchange_name: Exchange identifier.
        symbol: Trading symbol.
        interval: Candlestick interval (e.g., "1m", "5m", "1h").
        callback: Callback function for kline updates.
        pool_config: Optional pool configuration.

    Returns:
        Subscription ID.
    """
    manager = await get_websocket_manager()
    await _ensure_exchange_registered(manager, exchange_name, pool_config)

    params = {"interval": interval}
    return await manager.subscribe(exchange_name, "kline", symbol, callback, params)


async def unsubscribe(exchange_name: str, subscription_id: str) -> None:
    """Unsubscribe from a WebSocket topic.

    Args:
        exchange_name: Exchange identifier.
        subscription_id: Subscription ID to unsubscribe.
    """
    manager = await get_websocket_manager()
    await manager.unsubscribe(exchange_name, subscription_id)


async def get_websocket_stats() -> dict[str, Any]:
    """Get comprehensive WebSocket statistics.

    Returns:
        Dictionary containing pool statistics.
    """
    manager = await get_websocket_manager()
    return manager.get_pool_stats()


async def get_monitoring_dashboard() -> dict[str, Any]:
    """Get monitoring dashboard data.

    Returns:
        Dictionary containing monitoring dashboard data.
    """
    monitor = await get_websocket_monitor()
    return monitor.get_monitoring_dashboard()


# Export main classes and functions
__all__ = [
    # Core classes
    "AdvancedWebSocketConnection",
    "WebSocketConfig",
    "ConnectionState",
    "ErrorCategory",
    "AdvancedWebSocketManager",
    "PoolConfiguration",
    # Exchange adapters
    "ExchangeWebSocketAdapter",
    "WebSocketAdapterFactory",
    "ExchangeCredentials",
    "AuthenticationType",
    "ExchangeType",
    "BinanceWebSocketAdapter",
    "OKXWebSocketAdapter",
    # Monitoring
    "WebSocketMonitor",
    "MetricsCollector",
    "AlertManager",
    "WebSocketBenchmark",
    "PerformanceAlert",
    "AlertSeverity",
    # System integration
    "WebSocketSystem",
    "get_websocket_manager",
    "get_websocket_monitor",
    # Convenience functions
    "subscribe_to_ticker",
    "subscribe_to_depth",
    "subscribe_to_trades",
    "subscribe_to_klines",
    "unsubscribe",
    "get_websocket_stats",
    "get_monitoring_dashboard",
]
