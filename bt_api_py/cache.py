"""
Caching utilities for performance optimization.

Provides in-memory caching for frequently accessed data like exchange info,
trading pairs, and market data.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from functools import wraps
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])
_CACHE_MISSING = object()

__all__ = [
    "SimpleCache",
    "ExchangeInfoCache",
    "MarketDataCache",
    "cached",
    "get_exchange_info_cache",
    "get_market_data_cache",
]


class SimpleCache:
    """
    Simple in-memory cache with TTL (Time To Live) support.

    Features:
    - Key-value storage with expiration
    - Automatic cleanup of expired entries
    - Thread-safe operations (basic)
    """

    def __init__(self, default_ttl: float = 300.0, max_size: int | None = None) -> None:
        """
        Initialize cache.

        Args:
            default_ttl: Default time-to-live in seconds (default: 5 minutes)
        """
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    def _get_or_default(self, key: str, default: Any) -> Any:
        with self._lock:
            cached_entry = self._cache.get(key)
            if cached_entry is None:
                self._misses += 1
                return default

            value, expiry = cached_entry
            if time.time() > expiry:
                self._cache.pop(key, None)
                self._misses += 1
                return default

            self._hits += 1
            self._cache.move_to_end(key)
            return value

    def get(self, key: str) -> Any | None:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/expired
        """
        return self._get_or_default(key, None)

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (uses default if None)
        """
        if ttl is None:
            ttl = self._default_ttl

        expiry = time.time() + ttl
        with self._lock:
            if key in self._cache:
                self._cache.pop(key)
            self._cache[key] = (value, expiry)
            if self._max_size is not None and self._max_size > 0:
                while len(self._cache) > self._max_size:
                    self._cache.popitem(last=False)

    def delete(self, key: str) -> None:
        """
        Delete key from cache.

        Args:
            key: Cache key to delete
        """
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """
        Clear all cached data.

        Removes all entries from the cache regardless of expiration.
        """
        with self._lock:
            self._cache.clear()

    def cleanup(self) -> int:
        """
        Remove expired entries from cache.

        Iterates through all cached entries and removes those that have
        exceeded their time-to-live (TTL).

        Returns:
            Number of entries removed from cache.
        """
        with self._lock:
            now = time.time()
            active_cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
            removed = 0
            for key, entry in self._cache.items():
                if now > entry[1]:
                    removed += 1
                else:
                    active_cache[key] = entry
            self._cache = active_cache
            return removed

    def size(self) -> int:
        """
        Get number of cached entries.

        Returns:
            Number of entries currently in cache (including expired ones).
        """
        with self._lock:
            return len(self._cache)

    def __iter__(self) -> Iterator[str]:
        """Iterate over cache keys (for 'for key in cache' support)."""
        return iter(self.keys())

    def keys(self) -> list[str]:
        """
        Return a snapshot of cache keys.

        Returns:
            List of all cache keys (including expired entries).
        """
        with self._lock:
            return list(self._cache.keys())

    def get_stats(self) -> dict[str, float]:
        """
        Return cache hit/miss statistics.

        Returns:
            Dictionary with cache statistics including:
            - size: Number of cached entries
            - hits: Number of cache hits
            - misses: Number of cache misses
            - hit_rate: Cache hit rate (0.0 to 1.0)
        """
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total else 0.0
            return {
                "size": float(len(self._cache)),
                "hits": float(self._hits),
                "misses": float(self._misses),
                "hit_rate": hit_rate,
            }


class ExchangeInfoCache:
    """
    Specialized cache for exchange information.

    Caches:
    - Trading pairs
    - Exchange limits
    - Fee structures
    - Market status
    """

    def __init__(self, ttl: float = 3600.0) -> None:
        """
        Initialize exchange info cache.

        Args:
            ttl: Time-to-live in seconds (default: 1 hour)
        """
        self._cache = SimpleCache(default_ttl=ttl)

    def get_trading_pairs(self, exchange: str) -> list[str] | None:
        """
        Get cached trading pairs for exchange.

        Args:
            exchange: Exchange identifier (e.g., "BINANCE", "OKX")

        Returns:
            List of trading pair symbols or None if not cached.
        """
        return self._cache.get(f"{exchange}:trading_pairs")

    def set_trading_pairs(self, exchange: str, pairs: list[str]) -> None:
        """
        Cache trading pairs for exchange.

        Args:
            exchange: Exchange identifier (e.g., "BINANCE", "OKX")
            pairs: List of trading pair symbols to cache
        """
        self._cache.set(f"{exchange}:trading_pairs", pairs)

    def get_exchange_info(self, exchange: str, symbol: str) -> dict[str, Any] | None:
        """
        Get cached exchange info for symbol.

        Args:
            exchange: Exchange identifier (e.g., "BINANCE", "OKX")
            symbol: Trading pair symbol (e.g., "BTCUSDT")

        Returns:
            Dictionary with exchange info or None if not cached.
        """
        return self._cache.get(f"{exchange}:{symbol}:info")

    def set_exchange_info(self, exchange: str, symbol: str, info: dict[str, Any]) -> None:
        """
        Cache exchange info for symbol.

        Args:
            exchange: Exchange identifier (e.g., "BINANCE", "OKX")
            symbol: Trading pair symbol (e.g., "BTCUSDT")
            info: Dictionary with exchange information to cache
        """
        self._cache.set(f"{exchange}:{symbol}:info", info)

    def clear_exchange(self, exchange: str) -> None:
        """
        Clear all cached data for an exchange.

        Args:
            exchange: Exchange identifier to clear all cached data for
        """
        keys_to_delete = [key for key in self._cache if key.startswith(f"{exchange}:")]
        for key in keys_to_delete:
            self._cache.delete(key)


class MarketDataCache:
    """
    Cache for market data with shorter TTL.

    Caches:
    - Recent tickers
    - Order book snapshots
    - Recent trades
    """

    def __init__(self, ttl: float = 5.0) -> None:
        """
        Initialize market data cache.

        Args:
            ttl: Time-to-live in seconds (default: 5 seconds)
        """
        self._cache = SimpleCache(default_ttl=ttl)

    def get_ticker(self, exchange: str, symbol: str) -> Any | None:
        """
        Get cached ticker data.

        Args:
            exchange: Exchange identifier (e.g., "BINANCE", "OKX")
            symbol: Trading pair symbol (e.g., "BTCUSDT")

        Returns:
            Ticker data or None if not cached.
        """
        return self._cache.get(f"{exchange}:{symbol}:ticker")

    def set_ticker(self, exchange: str, symbol: str, ticker: Any) -> None:
        """
        Cache ticker data.

        Args:
            exchange: Exchange identifier (e.g., "BINANCE", "OKX")
            symbol: Trading pair symbol (e.g., "BTCUSDT")
            ticker: Ticker data to cache
        """
        self._cache.set(f"{exchange}:{symbol}:ticker", ticker)

    def get_orderbook(self, exchange: str, symbol: str) -> Any | None:
        """
        Get cached order book data.

        Args:
            exchange: Exchange identifier (e.g., "BINANCE", "OKX")
            symbol: Trading pair symbol (e.g., "BTCUSDT")

        Returns:
            Order book data or None if not cached.
        """
        return self._cache.get(f"{exchange}:{symbol}:orderbook")

    def set_orderbook(self, exchange: str, symbol: str, orderbook: Any) -> None:
        """
        Cache order book data.

        Args:
            exchange: Exchange identifier (e.g., "BINANCE", "OKX")
            symbol: Trading pair symbol (e.g., "BTCUSDT")
            orderbook: Order book data to cache
        """
        self._cache.set(f"{exchange}:{symbol}:orderbook", orderbook)


def cached(
    ttl: float = 300.0,
    cache_instance: SimpleCache | None = None,
    maxsize: int | None = None,
) -> Callable[[F], F]:
    """
    Decorator for caching function results.

    Automatically caches function results based on function name and arguments.
    Useful for expensive API calls or computations.

    Args:
        ttl: Time-to-live in seconds for cached results
        cache_instance: Cache instance to use (creates new if None)
        maxsize: Optional max number of cached entries (LRU eviction)

    Returns:
        Decorator function that wraps the original function with caching

    Example:
        @cached(ttl=60.0)
        def get_exchange_info(exchange: str):
            # Expensive API call
            return fetch_info(exchange)
    """
    if cache_instance is None:
        cache_instance = SimpleCache(default_ttl=ttl, max_size=maxsize)

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            """
            Wrapper function that implements caching logic.

            Args:
                *args: Positional arguments passed to the decorated function
                **kwargs: Keyword arguments passed to the decorated function

            Returns:
                Cached result or fresh result from function execution
            """
            # Create cache key from function name and arguments
            key_parts = [func.__name__]
            key_parts.extend(str(arg) for arg in args)
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            cache_key = ":".join(key_parts)

            # Try to get from cache
            cached_value = cache_instance._get_or_default(cache_key, _CACHE_MISSING)
            if cached_value is not _CACHE_MISSING:
                return cached_value

            # Call function and cache result
            result = func(*args, **kwargs)
            cache_instance.set(cache_key, result, ttl)
            return result

        # Add cache control methods
        wrapper.cache = cache_instance  # type: ignore
        wrapper.clear_cache = cache_instance.clear  # type: ignore
        wrapper.cache_stats = cache_instance.get_stats  # type: ignore

        return wrapper  # type: ignore

    return decorator


# Global cache instances
_exchange_info_cache = ExchangeInfoCache()
_market_data_cache = MarketDataCache()


def get_exchange_info_cache() -> ExchangeInfoCache:
    """
    Get global exchange info cache instance.

    Returns:
        Global ExchangeInfoCache instance for caching exchange information
    """
    return _exchange_info_cache


def get_market_data_cache() -> MarketDataCache:
    """
    Get global market data cache instance.

    Returns:
        Global MarketDataCache instance for caching market data
    """
    return _market_data_cache
