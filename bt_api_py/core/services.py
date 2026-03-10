"""
Core services for modern bt_api_py architecture.
"""

import asyncio
import contextlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from bt_api_py.logging_factory import get_logger

from .async_context import (
    AsyncRateLimiter,
    AsyncSemaphore,
    async_circuit_breaker,
    async_retry,
    async_timeout,
)
from .dependency_injection import inject, singleton
from .interfaces import (
    ICache,
    IConnectionManager,
    IEventBus,
    IRateLimiter,
)


@dataclass
class ConnectionConfig:
    """Connection configuration."""

    exchange_name: str
    base_url: str
    timeout: float = 30.0
    max_connections: int = 10
    retry_attempts: int = 3
    retry_delay: float = 1.0


@singleton(IConnectionManager)
class ConnectionService(IConnectionManager):
    """Manages connection pools for multiple exchanges."""

    def __init__(self, logger=None) -> Any | None:
        self.logger = logger or get_logger("connection_service")
        self._pools: dict[str, dict[str, Any]] = {}
        self._semaphores: dict[str, AsyncSemaphore] = {}
        self._lock = asyncio.Lock()
        self._stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "active_connections": 0,
                "total_requests": 0,
                "failed_requests": 0,
                "avg_response_time": 0.0,
            }
        )

    async def get_connection(self, exchange_name: str) -> Any:
        """Get or create a connection for the exchange."""
        async with self._lock:
            if exchange_name not in self._pools:
                await self._create_pool(exchange_name)

            pool = self._pools[exchange_name]
            semaphore = self._semaphores[exchange_name]

            # Acquire semaphore to limit concurrent connections
            await semaphore.acquire()

            try:
                # For HTTP exchanges, we return the client session
                if "session" in pool:
                    self._stats[exchange_name]["active_connections"] += 1
                    return pool["session"]
                else:
                    # For WebSocket exchanges, return the connection
                    self._stats[exchange_name]["active_connections"] += 1
                    return pool["connection"]
            except Exception:
                semaphore.release()
                raise

    async def release_connection(self, exchange_name: str, connection: Any) -> None:
        """Release a connection back to the pool."""
        if exchange_name in self._semaphores:
            self._semaphores[exchange_name].release()
            self._stats[exchange_name]["active_connections"] = max(
                0, self._stats[exchange_name]["active_connections"] - 1
            )

    async def close_all(self) -> None:
        """Close all connections."""
        async with self._lock:
            for exchange_name, pool in self._pools.items():
                try:
                    if "session" in pool:
                        await pool["session"].close()
                    elif "connection" in pool:
                        await pool["connection"].close()
                    self.logger.info(f"Closed connections for {exchange_name}")
                except Exception as e:
                    self.logger.error(f"Error closing {exchange_name}: {e}")

            self._pools.clear()
            self._semaphores.clear()

    async def _create_pool(self, exchange_name: str) -> None:
        """Create a connection pool for an exchange."""
        # This would be configured based on exchange-specific requirements
        # For now, create a basic structure
        self._pools[exchange_name] = {}
        self._semaphores[exchange_name] = AsyncSemaphore(max_concurrent=10)

    def get_connection_stats(self) -> dict[str, Any]:
        """Get connection statistics."""
        return dict(self._stats)


@singleton(IEventBus)
class EventService(IEventBus):
    """Enhanced event bus with async support and persistence."""

    def __init__(self, logger=None) -> Any | None:
        self.logger = logger or get_logger("event_service")
        self._handlers: dict[str, list[callable]] = defaultdict(list)
        self._async_handlers: dict[str, list[callable]] = defaultdict(list)
        self._event_queue: asyncio.Queue | None = None
        self._running = False
        self._stats: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the event processing loop."""
        async with self._lock:
            if not self._running:
                self._event_queue = asyncio.Queue()
                self._running = True
                asyncio.create_task(self._process_events())
                self.logger.info("Event service started")

    async def stop(self) -> None:
        """Stop the event processing loop."""
        async with self._lock:
            self._running = False
            if self._event_queue:
                # Signal stop with None
                await self._event_queue.put(None)
            self.logger.info("Event service stopped")

    def publish(self, event_type: str, data: Any) -> None:
        """Publish an event (sync for backward compatibility)."""
        if self._event_queue and self._running:
            # Add to async queue if available
            asyncio.create_task(self._event_queue.put((event_type, data)))
        else:
            # Direct call if not running
            self._emit_sync(event_type, data)

        self._stats[f"events_{event_type}_published"] += 1

    async def publish_async(self, event_type: str, data: Any) -> None:
        """Publish an event asynchronously."""
        if self._event_queue and self._running:
            await self._event_queue.put((event_type, data))
        else:
            self._emit_sync(event_type, data)

        self._stats[f"events_{event_type}_published"] += 1

    def subscribe(self, event_type: str, handler: callable) -> None:
        """Subscribe to an event type."""
        if asyncio.iscoroutinefunction(handler):
            self._async_handlers[event_type].append(handler)
        else:
            self._handlers[event_type].append(handler)
        self.logger.debug(f"Subscribed {handler} to {event_type}")

    def unsubscribe(self, event_type: str, handler: callable) -> None:
        """Unsubscribe from an event type."""
        with contextlib.suppress(ValueError):
            self._handlers[event_type].remove(handler)

        with contextlib.suppress(ValueError):
            self._async_handlers[event_type].remove(handler)

    async def _process_events(self) -> None:
        """Process events from the queue."""
        while self._running:
            try:
                event_data = await self._event_queue.get()
                if event_data is None:  # Stop signal
                    break

                event_type, data = event_data
                await self._emit_async(event_type, data)

            except Exception as e:
                self.logger.error(f"Error processing event: {e}")

    def _emit_sync(self, event_type: str, data: Any) -> None:
        """Emit event to sync handlers."""
        for handler in self._handlers[event_type]:
            try:
                handler(data)
            except Exception as e:
                self.logger.error(f"Sync handler error: {e}")

    async def _emit_async(self, event_type: str, data: Any) -> None:
        """Emit event to async handlers."""
        # Process sync handlers
        self._emit_sync(event_type, data)

        # Process async handlers
        tasks = []
        for handler in self._async_handlers[event_type]:
            tasks.append(asyncio.create_task(self._safe_call_handler(handler, data)))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_call_handler(self, handler: callable, data: Any) -> None:
        """Safely call async handler."""
        try:
            await handler(data)
        except Exception as e:
            self.logger.error(f"Async handler error: {e}")

    def get_stats(self) -> dict[str, Any]:
        """Get event statistics."""
        return dict(self._stats)


@singleton(ICache)
class CacheService(ICache):
    """Distributed cache service with Redis support."""

    def __init__(self, redis_url: str | None = None, logger=None) -> Any | None:
        self.logger = logger or get_logger("cache_service")
        self._redis_client = None
        self._local_cache: dict[str, Any] = {}
        self._local_ttl: dict[str, float] = {}
        self._lock = asyncio.Lock()

        if redis_url:
            self._init_redis(redis_url)

    def _init_redis(self, redis_url: str) -> None:
        """Initialize Redis client."""
        try:
            import redis.asyncio as redis

            self._redis_client = redis.from_url(redis_url)
            self.logger.info("Redis cache initialized")
        except ImportError:
            self.logger.warning("Redis not available, using local cache only")

    async def get(self, key: str) -> Any | None:
        """Get value by key."""
        if self._redis_client:
            try:
                value = await self._redis_client.get(key)
                if value:
                    return json.loads(value)
            except Exception as e:
                self.logger.error(f"Redis get error: {e}")

        # Fallback to local cache
        async with self._lock:
            if key in self._local_ttl and time.time() < self._local_ttl[key]:
                return self._local_cache.get(key)
            else:
                self._local_cache.pop(key, None)
                self._local_ttl.pop(key, None)
                return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Set value with optional TTL."""
        serialized = json.dumps(value, default=str)

        if self._redis_client:
            try:
                if ttl:
                    await self._redis_client.setex(key, ttl, serialized)
                else:
                    await self._redis_client.set(key, serialized)
            except Exception as e:
                self.logger.error(f"Redis set error: {e}")

        # Always update local cache as fallback
        async with self._lock:
            self._local_cache[key] = value
            if ttl:
                self._local_ttl[key] = time.time() + ttl
            else:
                self._local_ttl[key] = float("inf")

    async def delete(self, key: str) -> None:
        """Delete key."""
        if self._redis_client:
            try:
                await self._redis_client.delete(key)
            except Exception as e:
                self.logger.error(f"Redis delete error: {e}")

        async with self._lock:
            self._local_cache.pop(key, None)
            self._local_ttl.pop(key, None)

    async def clear(self) -> None:
        """Clear all cache."""
        if self._redis_client:
            try:
                await self._redis_client.flushdb()
            except Exception as e:
                self.logger.error(f"Redis clear error: {e}")

        async with self._lock:
            self._local_cache.clear()
            self._local_ttl.clear()


@singleton(IRateLimiter)
class RateLimitService(IRateLimiter):
    """Advanced rate limiting with multiple strategies."""

    def __init__(self, logger=None) -> Any | None:
        self.logger = logger or get_logger("rate_limit_service")
        self._limiters: dict[str, AsyncRateLimiter] = {}
        self._limits: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def configure_limit(self, resource: str, max_requests: int, time_window: float) -> None:
        """Configure rate limit for a resource."""
        self._limits[resource] = {"max_requests": max_requests, "time_window": time_window}

    async def acquire(self, resource: str, tokens: int = 1) -> None:
        """Acquire tokens for a resource."""
        async with self._lock:
            if resource not in self._limiters:
                if resource in self._limits:
                    limit = self._limits[resource]
                    self._limiters[resource] = AsyncRateLimiter(
                        limit["max_requests"], limit["time_window"]
                    )
                else:
                    # Default limiter
                    self._limiters[resource] = AsyncRateLimiter(10, 1.0)

        await self._limiters[resource].acquire()

    def get_remaining_tokens(self, resource: str) -> int:
        """Get remaining tokens for a resource."""
        limiter = self._limiters.get(resource)
        if limiter:
            return limiter.max_requests - len(limiter.requests)
        return 0

    def get_reset_time(self, resource: str) -> float | None:
        """Get reset time for a resource."""
        limiter = self._limiters.get(resource)
        if limiter and limiter.requests:
            return limiter.requests[0] + limiter.time_window
        return None


class MarketDataService:
    """Service for market data operations with caching and batching."""

    def __init__(
        self,
        connection_manager: IConnectionManager = inject(IConnectionManager),
        cache_service: ICache = inject(ICache),
        rate_limiter: IRateLimiter = inject(IRateLimiter),
        event_bus: IEventBus = inject(IEventBus),
    ):
        self.connection_manager = connection_manager
        self.cache_service = cache_service
        self.rate_limiter = rate_limiter
        self.event_bus = event_bus
        self.logger = get_logger("market_data_service")

        # Configure rate limits
        self.rate_limiter.configure_limit("ticker", 100, 60)  # 100 requests per minute
        self.rate_limiter.configure_limit("depth", 50, 60)  # 50 requests per minute
        self.rate_limiter.configure_limit("kline", 30, 60)  # 30 requests per minute

    @async_retry(max_attempts=3, delay=0.5)
    @async_timeout(10.0)
    async def get_ticker(
        self, exchange_name: str, symbol: str, use_cache: bool = True
    ) -> dict[str, Any]:
        """Get ticker with caching."""
        cache_key = f"ticker:{exchange_name}:{symbol}"

        # Try cache first
        if use_cache:
            cached_data = await self.cache_service.get(cache_key)
            if cached_data:
                return cached_data

        # Rate limit
        await self.rate_limiter.acquire("ticker")

        # Get connection and fetch data
        connection = await self.connection_manager.get_connection(exchange_name)
        try:
            # This would use the actual exchange adapter
            # For now, return mock data
            ticker_data = {
                "symbol": symbol,
                "price": 50000.0,
                "timestamp": time.time(),
                "exchange": exchange_name,
            }

            # Cache the result
            if use_cache:
                await self.cache_service.set(cache_key, ticker_data, ttl=5)  # 5 seconds TTL

            # Publish event
            await self.event_bus.publish_async(
                "ticker_received",
                {"exchange": exchange_name, "symbol": symbol, "data": ticker_data},
            )

            return ticker_data

        finally:
            await self.connection_manager.release_connection(exchange_name, connection)

    async def get_multiple_tickers(
        self, requests: list[dict[str, str]]
    ) -> dict[str, dict[str, Any]]:
        """Batch ticker requests."""
        tasks = []
        for req in requests:
            task = self.get_ticker(req["exchange"], req["symbol"])
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = {}
        for i, req in enumerate(requests):
            key = f"{req['exchange']}:{req['symbol']}"
            if not isinstance(results[i], Exception):
                output[key] = results[i]
            else:
                self.logger.error(f"Error getting ticker for {key}: {results[i]}")

        return output


class TradingService:
    """Service for trading operations with risk management."""

    def __init__(
        self,
        connection_manager: IConnectionManager = inject(IConnectionManager),
        event_bus: IEventBus = inject(IEventBus),
        rate_limiter: IRateLimiter = inject(IRateLimiter),
    ):
        self.connection_manager = connection_manager
        self.event_bus = event_bus
        self.rate_limiter = rate_limiter
        self.logger = get_logger("trading_service")

        # Rate limits for trading
        self.rate_limiter.configure_limit("order", 20, 60)  # 20 orders per minute
        self.rate_limiter.configure_limit("cancel", 40, 60)  # 40 cancels per minute

    @async_circuit_breaker(failure_threshold=5, recovery_timeout=60.0)
    @async_retry(max_attempts=3, delay=1.0)
    async def place_order(
        self,
        exchange_name: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Place an order with circuit breaker and retry."""
        await self.rate_limiter.acquire("order")

        connection = await self.connection_manager.get_connection(exchange_name)
        try:
            # Mock order placement
            order_data = {
                "order_id": f"order_{int(time.time())}_{hash(symbol)}",
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "quantity": quantity,
                "price": price,
                "status": "submitted",
                "exchange": exchange_name,
                "timestamp": time.time(),
            }

            # Publish event
            await self.event_bus.publish_async("order_placed", order_data)

            return order_data

        finally:
            await self.connection_manager.release_connection(exchange_name, connection)


class AccountService:
    """Service for account operations."""

    def __init__(
        self,
        connection_manager: IConnectionManager = inject(IConnectionManager),
        cache_service: ICache = inject(ICache),
        event_bus: IEventBus = inject(IEventBus),
    ):
        self.connection_manager = connection_manager
        self.cache_service = cache_service
        self.event_bus = event_bus
        self.logger = get_logger("account_service")

    async def get_balance(self, exchange_name: str, use_cache: bool = True) -> dict[str, Any]:
        """Get account balance."""
        cache_key = f"balance:{exchange_name}"

        if use_cache:
            cached_data = await self.cache_service.get(cache_key)
            if cached_data:
                return cached_data

        connection = await self.connection_manager.get_connection(exchange_name)
        try:
            # Mock balance data
            balance_data = {
                "exchange": exchange_name,
                "balances": {
                    "BTC": {"free": 1.5, "locked": 0.5},
                    "USDT": {"free": 10000.0, "locked": 2000.0},
                },
                "timestamp": time.time(),
            }

            await self.cache_service.set(cache_key, balance_data, ttl=30)  # 30 seconds TTL
            await self.event_bus.publish_async("balance_updated", balance_data)

            return balance_data

        finally:
            await self.connection_manager.release_connection(exchange_name, connection)
