"""Core interfaces for the modernized bt_api_py architecture."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, Protocol, runtime_checkable

P = ParamSpec("P")


@runtime_checkable
class IExchangeAdapter(Protocol):
    """Synchronous exchange adapter interface."""

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to exchange."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection to exchange."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to exchange."""
        ...

    @abstractmethod
    def get_tick(self, symbol: str, **kwargs: Any) -> Any:
        """Get ticker data."""
        ...

    @abstractmethod
    def get_depth(self, symbol: str, count: int = 20, **kwargs: Any) -> Any:
        """Get order book depth."""
        ...

    @abstractmethod
    def get_kline(self, symbol: str, period: str, count: int = 20, **kwargs: Any) -> Any:
        """Get candlestick data."""
        ...

    @abstractmethod
    def make_order(
        self, symbol: str, volume: float, price: float, order_type: str, **kwargs: Any
    ) -> Any:
        """Place an order."""
        ...

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str, **kwargs: Any) -> Any:
        """Cancel an order."""
        ...

    @abstractmethod
    def get_balance(self, symbol: str | None = None, **kwargs: Any) -> Any:
        """Get account balance."""
        ...

    @abstractmethod
    def get_position(self, symbol: str | None = None, **kwargs: Any) -> Any:
        """Get account positions."""
        ...


@runtime_checkable
class IAsyncExchangeAdapter(Protocol):
    """Asynchronous exchange adapter interface."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to exchange."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to exchange."""
        ...

    @abstractmethod
    async def is_connected(self) -> bool:
        """Check if connected to exchange."""
        ...

    @abstractmethod
    async def get_tick(self, symbol: str, **kwargs: Any) -> Any:
        """Get ticker data."""
        ...

    @abstractmethod
    async def get_depth(self, symbol: str, count: int = 20, **kwargs: Any) -> Any:
        """Get order book depth."""
        ...

    @abstractmethod
    async def get_kline(self, symbol: str, period: str, count: int = 20, **kwargs: Any) -> Any:
        """Get candlestick data."""
        ...

    @abstractmethod
    async def make_order(
        self, symbol: str, volume: float, price: float, order_type: str, **kwargs: Any
    ) -> Any:
        """Place an order."""
        ...

    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str, **kwargs: Any) -> Any:
        """Cancel an order."""
        ...

    @abstractmethod
    async def get_balance(self, symbol: str | None = None, **kwargs: Any) -> Any:
        """Get account balance."""
        ...

    @abstractmethod
    async def get_position(self, symbol: str | None = None, **kwargs: Any) -> Any:
        """Get account positions."""
        ...


class IConnectionManager(ABC):
    """Connection pool and management interface."""

    @abstractmethod
    async def get_connection(self, exchange_name: str) -> Any:
        """Get or create a connection."""
        ...

    @abstractmethod
    async def release_connection(self, exchange_name: str, connection: Any) -> None:
        """Release a connection back to pool."""
        ...

    @abstractmethod
    async def close_all(self) -> None:
        """Close all connections."""
        ...

    @abstractmethod
    def get_connection_stats(self) -> dict[str, Any]:
        """Get connection statistics."""
        ...


class IEventBus(ABC):
    """Event bus interface for pub/sub messaging."""

    @abstractmethod
    def publish(self, event_type: str, data: Any) -> None:
        """Publish an event."""
        ...

    @abstractmethod
    async def publish_async(self, event_type: str, data: Any) -> None:
        """Publish an event asynchronously."""
        ...

    @abstractmethod
    def subscribe(
        self, event_type: str, handler: Callable[[Any], Any] | Callable[[Any], Awaitable[Any]]
    ) -> None:
        """Subscribe to an event type."""
        ...

    @abstractmethod
    def unsubscribe(
        self, event_type: str, handler: Callable[[Any], Any] | Callable[[Any], Awaitable[Any]]
    ) -> None:
        """Unsubscribe from an event type."""
        ...


class ICache(ABC):
    """Caching interface."""

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """Get value by key."""
        ...

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Set value with optional TTL."""
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete key."""
        ...

    @abstractmethod
    async def clear(self) -> None:
        """Clear all cache."""
        ...


class IRateLimiter(ABC):
    """Rate limiting interface."""

    def configure_limit(self, resource: str, max_requests: int, time_window: float) -> None:  # noqa: B027
        """Configure rate limit for a resource. Default no-op."""

    @abstractmethod
    async def acquire(self, resource: str, tokens: int = 1) -> None:
        """Acquire tokens for a resource."""
        ...

    @abstractmethod
    def get_remaining_tokens(self, resource: str) -> int:
        """Get remaining tokens for a resource."""
        ...

    @abstractmethod
    def get_reset_time(self, resource: str) -> float | None:
        """Get reset time for a resource."""
        ...


class ICircuitBreaker(ABC):
    """Circuit breaker interface for fault tolerance."""

    @abstractmethod
    async def call(self, func: Callable[P, Any], *args: P.args, **kwargs: P.kwargs) -> Any:
        """Execute function through circuit breaker."""
        ...

    @abstractmethod
    def get_state(self) -> str:
        """Get current circuit state (CLOSED, OPEN, HALF_OPEN)."""
        ...

    @abstractmethod
    def get_failure_count(self) -> int:
        """Get current failure count."""
        ...


class IMetricsCollector(ABC):
    """Metrics collection interface."""

    @abstractmethod
    def increment_counter(self, name: str, tags: dict[str, str] | None = None) -> None:
        """Increment a counter metric."""
        ...

    @abstractmethod
    def record_histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record a histogram value."""
        ...

    @abstractmethod
    def set_gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Set a gauge value."""
        ...


class IExchangeFactory(ABC):
    """Exchange adapter factory interface."""

    @abstractmethod
    def create_sync_adapter(self, exchange_name: str, config: dict[str, Any]) -> IExchangeAdapter:
        """Create synchronous exchange adapter."""
        ...

    @abstractmethod
    def create_async_adapter(
        self, exchange_name: str, config: dict[str, Any]
    ) -> IAsyncExchangeAdapter:
        """Create asynchronous exchange adapter."""
        ...

    @abstractmethod
    def get_supported_exchanges(self) -> list[str]:
        """Get list of supported exchanges."""
        ...


class IRequestBatcher(ABC):
    """Request batching interface."""

    @abstractmethod
    async def add_request(self, request: dict[str, Any]) -> Any:
        """Add request to batch and return future."""
        ...

    @abstractmethod
    async def flush(self) -> None:
        """Flush all pending requests."""
        ...

    @abstractmethod
    def get_batch_size(self) -> int:
        """Get current batch size."""
        ...


class IDataNormalizer(ABC):
    """Data normalization interface."""

    @abstractmethod
    def normalize_ticker(self, raw_data: Any, exchange_name: str) -> dict[str, Any]:
        """Normalize ticker data to standard format."""
        ...

    @abstractmethod
    def normalize_orderbook(self, raw_data: Any, exchange_name: str) -> dict[str, Any]:
        """Normalize orderbook data to standard format."""
        ...

    @abstractmethod
    def normalize_trade(self, raw_data: Any, exchange_name: str) -> dict[str, Any]:
        """Normalize trade data to standard format."""
        ...

    @abstractmethod
    def normalize_order(self, raw_data: Any, exchange_name: str) -> dict[str, Any]:
        """Normalize order data to standard format."""
        ...
