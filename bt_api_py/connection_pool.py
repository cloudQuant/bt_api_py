"""
Connection pool management for WebSocket and HTTP connections.

Provides efficient connection reuse and management to improve performance
and reduce resource usage.
"""

import asyncio
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Generic, TypeVar

T = TypeVar("T")

_logger = logging.getLogger(__name__)


class ConnectionPool(Generic[T]):
    """
    Generic connection pool for managing reusable connections.

    Features:
    - Connection reuse
    - Automatic cleanup of idle connections
    - Thread-safe operations
    - Health checking
    """

    def __init__(
        self,
        factory: Callable[[], T],
        max_size: int = 10,
        min_size: int = 2,
        max_idle_time: float = 300.0,
        health_check: Callable[[T], bool] | None = None,
    ) -> None:
        """
        Initialize connection pool.

        Args:
            factory: Function to create new connections.
            max_size: Maximum number of connections.
            min_size: Minimum number of connections to maintain.
            max_idle_time: Maximum idle time before connection is closed (seconds).
            health_check: Optional function to check connection health.

        Returns:
            None
        """
        self._factory = factory
        self._max_size = max_size
        self._min_size = min_size
        self._max_idle_time = max_idle_time
        self._health_check = health_check

        self._pool: deque[tuple[T, float]] = deque()
        self._in_use: set[T] = set()
        self._lock = threading.Lock()
        self._cleanup_thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        """
        Start the connection pool and cleanup thread.

        Returns:
            None
        """
        if self._running:
            return

        self._running = True

        # Pre-create minimum connections
        with self._lock:
            for _ in range(self._min_size):
                conn = self._factory()
                self._pool.append((conn, time.time()))

        # Start cleanup thread
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def stop(self) -> None:
        """
        Stop the connection pool and close all connections.

        Returns:
            None
        """
        self._running = False

        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=5.0)

        with self._lock:
            # Close all pooled connections
            while self._pool:
                conn, _ = self._pool.popleft()
                self._close_connection(conn)

            # Close in-use connections
            for conn in self._in_use:
                self._close_connection(conn)
            self._in_use.clear()

    def acquire(self) -> T:
        """
        Acquire a connection from the pool.

        Returns:
            A connection instance.

        Raises:
            RuntimeError: If connection pool is exhausted.
        """
        with self._lock:
            # Try to get from pool
            while self._pool:
                conn, _ = self._pool.popleft()

                # Check health
                if self._health_check and not self._health_check(conn):
                    self._close_connection(conn)
                    continue

                self._in_use.add(conn)
                return conn

            # Create new connection if under max size
            if len(self._in_use) < self._max_size:
                conn = self._factory()
                self._in_use.add(conn)
                return conn

            # Pool exhausted, wait and retry
            raise RuntimeError("Connection pool exhausted")

    def release(self, conn: T) -> None:
        """
        Release a connection back to the pool.

        Args:
            conn: Connection to release.

        Returns:
            None
        """
        with self._lock:
            if conn not in self._in_use:
                return

            self._in_use.remove(conn)

            # Check health before returning to pool
            if self._health_check and not self._health_check(conn):
                self._close_connection(conn)
                return

            # Add back to pool with current timestamp
            self._pool.append((conn, time.time()))

    def _cleanup_loop(self) -> None:
        """Background thread to cleanup idle connections."""
        while self._running:
            time.sleep(60)  # Check every minute
            self._cleanup_idle_connections()

    def _cleanup_idle_connections(self) -> None:
        """Remove idle connections exceeding max_idle_time."""
        with self._lock:
            now = time.time()
            new_pool: deque[tuple[T, float]] = deque()

            for conn, last_used in self._pool:
                if now - last_used > self._max_idle_time:
                    # Connection idle too long, close it
                    self._close_connection(conn)
                else:
                    new_pool.append((conn, last_used))

            self._pool = new_pool

            # Ensure minimum connections
            while len(self._pool) < self._min_size:
                conn = self._factory()
                self._pool.append((conn, now))

    def _close_connection(self, conn: T) -> None:
        """Close a connection (override for custom cleanup)."""
        if hasattr(conn, "close"):
            try:
                conn.close()
            except (OSError, ConnectionError) as e:
                _logger.debug(f"Error closing connection: {e}")

    def size(self) -> tuple[int, int]:
        """
        Get pool statistics.

        Returns:
            Tuple of (available, in_use) connection counts.
        """
        with self._lock:
            return len(self._pool), len(self._in_use)


class AsyncConnectionPool(Generic[T]):
    """
    Async version of connection pool for asyncio applications.
    """

    def __init__(
        self,
        factory: Callable[[], Any],
        max_size: int = 10,
        min_size: int = 2,
        max_idle_time: float = 300.0,
    ) -> None:
        """
        Initialize async connection pool.

        Args:
            factory: Function to create new connections.
            max_size: Maximum number of connections.
            min_size: Minimum number of connections to maintain.
            max_idle_time: Maximum idle time before connection is closed (seconds).

        Returns:
            None
        """
        self._factory = factory
        self._max_size = max_size
        self._min_size = min_size
        self._max_idle_time = max_idle_time

        self._pool: deque[tuple[T, float]] = deque()
        self._in_use: set[T] = set()
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_size)

    async def acquire(self) -> T:
        """
        Acquire a connection asynchronously.

        Returns:
            A connection instance.
        """
        async with self._semaphore, self._lock:
            # Try to get from pool
            while self._pool:
                conn, _ = self._pool.popleft()
                self._in_use.add(conn)
                return conn

            # Create new connection
            if asyncio.iscoroutinefunction(self._factory):
                conn = await self._factory()
            else:
                conn = self._factory()

            self._in_use.add(conn)
            return conn

    async def release(self, conn: T) -> None:
        """
        Release a connection back to the pool.

        Args:
            conn: Connection to release.

        Returns:
            None
        """
        async with self._lock:
            if conn in self._in_use:
                self._in_use.remove(conn)
                self._pool.append((conn, time.time()))


class PooledConnection(Generic[T]):
    """
    Context manager for pooled connections.

    Example:
        pool = ConnectionPool(factory=create_connection)
        pool.start()

        with PooledConnection(pool) as conn:
            conn.execute("SELECT 1")
    """

    def __init__(self, pool: ConnectionPool[T]) -> None:
        """
        Initialize with connection pool.

        Args:
            pool: Connection pool instance.

        Returns:
            None
        """
        self._pool = pool
        self._conn: T | None = None

    def __enter__(self) -> T:
        """Acquire connection."""
        conn = self._pool.acquire()
        self._conn = conn
        return conn

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Release connection."""
        if self._conn:
            self._pool.release(self._conn)
