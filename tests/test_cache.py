"""Tests for cache module."""

from __future__ import annotations

import time

import pytest

from bt_api_py.cache import (
    ExchangeInfoCache,
    MarketDataCache,
    SimpleCache,
    cached,
    get_exchange_info_cache,
    get_market_data_cache,
)


class TestSimpleCache:
    """Tests for SimpleCache class."""

    def test_init_defaults(self):
        """Test default initialization."""
        cache = SimpleCache()

        assert cache._default_ttl == 300.0
        assert cache._max_size == 10000

    def test_init_custom(self):
        """Test custom initialization."""
        cache = SimpleCache(default_ttl=60.0, max_size=100)

        assert cache._default_ttl == 60.0
        assert cache._max_size == 100

    def test_set_and_get(self):
        """Test set and get operations."""
        cache = SimpleCache()

        cache.set("key1", "value1")
        result = cache.get("key1")

        assert result == "value1"

    def test_get_missing_key(self):
        """Test getting missing key."""
        cache = SimpleCache()

        result = cache.get("missing")

        assert result is None

    def test_set_with_custom_ttl(self):
        """Test set with custom TTL."""
        cache = SimpleCache(default_ttl=300.0)

        cache.set("key1", "value1", ttl=0.01)
        time.sleep(0.02)
        result = cache.get("key1")

        assert result is None  # Expired

    def test_delete(self):
        """Test delete operation."""
        cache = SimpleCache()

        cache.set("key1", "value1")
        cache.delete("key1")
        result = cache.get("key1")

        assert result is None

    def test_clear(self):
        """Test clear operation."""
        cache = SimpleCache()

        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.clear()

        assert cache.size() == 0

    def test_cleanup_expired(self):
        """Test cleanup of expired entries."""
        cache = SimpleCache()

        cache.set("key1", "value1", ttl=0.01)
        cache.set("key2", "value2", ttl=300.0)
        time.sleep(0.02)

        removed = cache.cleanup()

        assert removed == 1
        assert cache.get("key2") == "value2"

    def test_size(self):
        """Test size operation."""
        cache = SimpleCache()

        cache.set("key1", "value1")
        cache.set("key2", "value2")

        assert cache.size() == 2

    def test_keys(self):
        """Test keys operation."""
        cache = SimpleCache()

        cache.set("key1", "value1")
        cache.set("key2", "value2")

        keys = cache.keys()

        assert set(keys) == {"key1", "key2"}

    def test_iter(self):
        """Test iteration over keys."""
        cache = SimpleCache()

        cache.set("key1", "value1")
        cache.set("key2", "value2")

        keys = list(cache)

        assert set(keys) == {"key1", "key2"}

    def test_get_stats(self):
        """Test statistics."""
        cache = SimpleCache()

        cache.set("key1", "value1")
        cache.get("key1")  # Hit
        cache.get("key1")  # Hit
        cache.get("missing")  # Miss

        stats = cache.get_stats()

        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 2 / 3

    def test_max_size_eviction(self):
        """Test LRU eviction when max size reached."""
        cache = SimpleCache(max_size=3)

        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")
        cache.set("key4", "value4")  # Should evict key1

        assert cache.size() == 3
        assert cache.get("key1") is None
        assert cache.get("key4") == "value4"

    def test_update_existing_key(self):
        """Test updating existing key."""
        cache = SimpleCache()

        cache.set("key1", "value1")
        cache.set("key1", "value2")

        assert cache.get("key1") == "value2"
        assert cache.size() == 1


class TestExchangeInfoCache:
    """Tests for ExchangeInfoCache class."""

    def test_trading_pairs(self):
        """Test trading pairs caching."""
        cache = ExchangeInfoCache()

        cache.set_trading_pairs("BINANCE", ["BTCUSDT", "ETHUSDT"])
        result = cache.get_trading_pairs("BINANCE")

        assert result == ["BTCUSDT", "ETHUSDT"]

    def test_trading_pairs_missing(self):
        """Test getting missing trading pairs."""
        cache = ExchangeInfoCache()

        result = cache.get_trading_pairs("UNKNOWN")

        assert result is None

    def test_exchange_info(self):
        """Test exchange info caching."""
        cache = ExchangeInfoCache()

        info = {"minQty": 0.001, "maxQty": 1000}
        cache.set_exchange_info("BINANCE", "BTCUSDT", info)
        result = cache.get_exchange_info("BINANCE", "BTCUSDT")

        assert result == info

    def test_clear_exchange(self):
        """Test clearing exchange data."""
        cache = ExchangeInfoCache()

        cache.set_trading_pairs("BINANCE", ["BTCUSDT"])
        cache.set_exchange_info("BINANCE", "BTCUSDT", {"minQty": 0.001})
        cache.set_trading_pairs("OKX", ["BTC-USDT"])

        cache.clear_exchange("BINANCE")

        assert cache.get_trading_pairs("BINANCE") is None
        assert cache.get_exchange_info("BINANCE", "BTCUSDT") is None
        assert cache.get_trading_pairs("OKX") == ["BTC-USDT"]


class TestMarketDataCache:
    """Tests for MarketDataCache class."""

    def test_ticker(self):
        """Test ticker caching."""
        cache = MarketDataCache()

        ticker = {"last": 50000.0, "bid": 49999.0, "ask": 50001.0}
        cache.set_ticker("BINANCE", "BTCUSDT", ticker)
        result = cache.get_ticker("BINANCE", "BTCUSDT")

        assert result == ticker

    def test_orderbook(self):
        """Test orderbook caching."""
        cache = MarketDataCache()

        orderbook = {"bids": [[50000, 1.0]], "asks": [[50001, 1.0]]}
        cache.set_orderbook("BINANCE", "BTCUSDT", orderbook)
        result = cache.get_orderbook("BINANCE", "BTCUSDT")

        assert result == orderbook


class TestCachedDecorator:
    """Tests for cached decorator."""

    def test_cached_basic(self):
        """Test basic caching."""

        call_count = 0

        @cached(ttl=60.0)
        def expensive_function(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        result1 = expensive_function(5)
        result2 = expensive_function(5)

        assert result1 == 10
        assert result2 == 10
        assert call_count == 1  # Only called once

    def test_cached_different_args(self):
        """Test caching with different arguments."""

        @cached(ttl=60.0)
        def add(a, b):
            return a + b

        result1 = add(1, 2)
        result2 = add(2, 3)

        assert result1 == 3
        assert result2 == 5

    def test_cached_with_kwargs(self):
        """Test caching with keyword arguments."""

        @cached(ttl=60.0)
        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        result1 = greet("World")
        result2 = greet("World", greeting="Hi")

        assert result1 == "Hello, World!"
        assert result2 == "Hi, World!"

    def test_cached_clear_cache(self):
        """Test clearing cache via decorator."""

        call_count = 0

        @cached(ttl=60.0)
        def func(x):
            nonlocal call_count
            call_count += 1
            return x

        func(1)
        func(1)
        func.clear_cache()
        func(1)

        assert call_count == 2  # Called twice (before and after clear)

    def test_cached_stats(self):
        """Test getting cache stats via decorator."""

        @cached(ttl=60.0)
        def func(x):
            return x

        func(1)
        func(1)
        func(2)

        stats = func.cache_stats()

        assert stats["hits"] == 1
        assert stats["misses"] == 2


class TestGlobalCacheInstances:
    """Tests for global cache instances."""

    def test_get_exchange_info_cache(self):
        """Test getting global exchange info cache."""
        cache = get_exchange_info_cache()

        assert isinstance(cache, ExchangeInfoCache)

    def test_get_market_data_cache(self):
        """Test getting global market data cache."""
        cache = get_market_data_cache()

        assert isinstance(cache, MarketDataCache)

    def test_global_caches_are_singletons(self):
        """Test that global caches are singleton instances."""
        cache1 = get_exchange_info_cache()
        cache2 = get_exchange_info_cache()

        assert cache1 is cache2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
