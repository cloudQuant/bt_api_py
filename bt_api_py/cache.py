"""
Caching utilities for performance optimization.

Provides in-memory caching for frequently accessed data like exchange info,
trading pairs, and market data.
"""

import time
from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class SimpleCache:
    """
    Simple in-memory cache with TTL (Time To Live) support.
    
    Features:
    - Key-value storage with expiration
    - Automatic cleanup of expired entries
    - Thread-safe operations (basic)
    """
    
    def __init__(self, default_ttl: float = 300.0):
        """
        Initialize cache.
        
        Args:
            default_ttl: Default time-to-live in seconds (default: 5 minutes)
        """
        self._cache: dict[str, tuple[Any, float]] = {}
        self._default_ttl = default_ttl
    
    def get(self, key: str) -> Any | None:
        """
        Get value from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found/expired
        """
        if key not in self._cache:
            return None
        
        value, expiry = self._cache[key]
        
        # Check if expired
        if time.time() > expiry:
            del self._cache[key]
            return None
        
        return value
    
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
        self._cache[key] = (value, expiry)
    
    def delete(self, key: str) -> None:
        """Delete key from cache."""
        self._cache.pop(key, None)
    
    def clear(self) -> None:
        """Clear all cached data."""
        self._cache.clear()
    
    def cleanup(self) -> int:
        """
        Remove expired entries.
        
        Returns:
            Number of entries removed
        """
        now = time.time()
        expired_keys = [
            key for key, (_, expiry) in self._cache.items() if now > expiry
        ]
        
        for key in expired_keys:
            del self._cache[key]
        
        return len(expired_keys)
    
    def size(self) -> int:
        """Get number of cached entries."""
        return len(self._cache)


class ExchangeInfoCache:
    """
    Specialized cache for exchange information.
    
    Caches:
    - Trading pairs
    - Exchange limits
    - Fee structures
    - Market status
    """
    
    def __init__(self, ttl: float = 3600.0):
        """
        Initialize exchange info cache.
        
        Args:
            ttl: Time-to-live in seconds (default: 1 hour)
        """
        self._cache = SimpleCache(default_ttl=ttl)
    
    def get_trading_pairs(self, exchange: str) -> list[str] | None:
        """Get cached trading pairs for exchange."""
        return self._cache.get(f"{exchange}:trading_pairs")
    
    def set_trading_pairs(self, exchange: str, pairs: list[str]) -> None:
        """Cache trading pairs for exchange."""
        self._cache.set(f"{exchange}:trading_pairs", pairs)
    
    def get_exchange_info(self, exchange: str, symbol: str) -> dict[str, Any] | None:
        """Get cached exchange info for symbol."""
        return self._cache.get(f"{exchange}:{symbol}:info")
    
    def set_exchange_info(self, exchange: str, symbol: str, info: dict[str, Any]) -> None:
        """Cache exchange info for symbol."""
        self._cache.set(f"{exchange}:{symbol}:info", info)
    
    def clear_exchange(self, exchange: str) -> None:
        """Clear all cached data for an exchange."""
        keys_to_delete = [
            key for key in self._cache._cache.keys() if key.startswith(f"{exchange}:")
        ]
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
    
    def __init__(self, ttl: float = 5.0):
        """
        Initialize market data cache.
        
        Args:
            ttl: Time-to-live in seconds (default: 5 seconds)
        """
        self._cache = SimpleCache(default_ttl=ttl)
    
    def get_ticker(self, exchange: str, symbol: str) -> Any | None:
        """Get cached ticker."""
        return self._cache.get(f"{exchange}:{symbol}:ticker")
    
    def set_ticker(self, exchange: str, symbol: str, ticker: Any) -> None:
        """Cache ticker data."""
        self._cache.set(f"{exchange}:{symbol}:ticker", ticker)
    
    def get_orderbook(self, exchange: str, symbol: str) -> Any | None:
        """Get cached order book."""
        return self._cache.get(f"{exchange}:{symbol}:orderbook")
    
    def set_orderbook(self, exchange: str, symbol: str, orderbook: Any) -> None:
        """Cache order book data."""
        self._cache.set(f"{exchange}:{symbol}:orderbook", orderbook)


def cached(ttl: float = 300.0, cache_instance: SimpleCache | None = None):
    """
    Decorator for caching function results.
    
    Args:
        ttl: Time-to-live in seconds
        cache_instance: Cache instance to use (creates new if None)
        
    Example:
        @cached(ttl=60.0)
        def get_exchange_info(exchange: str):
            # Expensive API call
            return fetch_info(exchange)
    """
    if cache_instance is None:
        cache_instance = SimpleCache(default_ttl=ttl)
    
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create cache key from function name and arguments
            key_parts = [func.__name__]
            key_parts.extend(str(arg) for arg in args)
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            cache_key = ":".join(key_parts)
            
            # Try to get from cache
            cached_value = cache_instance.get(cache_key)
            if cached_value is not None:
                return cached_value
            
            # Call function and cache result
            result = func(*args, **kwargs)
            cache_instance.set(cache_key, result, ttl)
            return result
        
        # Add cache control methods
        wrapper.cache = cache_instance  # type: ignore
        wrapper.clear_cache = cache_instance.clear  # type: ignore
        
        return wrapper  # type: ignore
    
    return decorator


# Global cache instances
_exchange_info_cache = ExchangeInfoCache()
_market_data_cache = MarketDataCache()


def get_exchange_info_cache() -> ExchangeInfoCache:
    """Get global exchange info cache instance."""
    return _exchange_info_cache


def get_market_data_cache() -> MarketDataCache:
    """Get global market data cache instance."""
    return _market_data_cache
