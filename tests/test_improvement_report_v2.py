"""Regression tests for improvement-report-v2 fixes."""

import threading
import time
from unittest.mock import patch

from bt_api_py import auth_config, cache, config_loader, event_bus, logging_factory, rate_limiter
from bt_api_py.containers.instrument import AssetType, Instrument
from bt_api_py.core.dependency_injection import Container
from bt_api_py.feeds import registry as legacy_feed_registry
from bt_api_py.instrument_manager import InstrumentManager
from bt_api_py.registry import ExchangeRegistry


class _LegacyFeed:
    def __init__(self, data_queue=None, **kwargs):
        self.data_queue = data_queue
        self.kwargs = kwargs


def test_event_bus_handler_list_is_stable_during_emit():
    bus = event_bus.EventBus()
    received: list[str] = []

    def removing_handler(data):
        received.append(f"first:{data}")
        bus.off("tick", removing_handler)

    def second_handler(data):
        received.append(f"second:{data}")

    bus.on("tick", removing_handler)
    bus.on("tick", second_handler)

    bus.emit("tick", "payload")

    assert received == ["first:payload", "second:payload"]


def test_simple_cache_tracks_stats():
    simple_cache = cache.SimpleCache(default_ttl=1.0)

    assert simple_cache.get("missing") is None
    simple_cache.set("answer", 42)
    assert simple_cache.get("answer") == 42

    stats = simple_cache.get_stats()
    assert stats["hits"] == 1.0
    assert stats["misses"] == 1.0
    assert stats["hit_rate"] == 0.5


def test_simple_cache_handles_concurrent_access():
    simple_cache = cache.SimpleCache(default_ttl=2.0)
    errors: list[Exception] = []

    def worker(i: int):
        try:
            key = f"k{i}"
            simple_cache.set(key, i)
            assert simple_cache.get(key) == i
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert simple_cache.size() == 50


def test_logging_factory_caches_loggers_across_threads():
    created_loggers: list[object] = []
    create_lock = threading.Lock()
    cache_key = ("report_v2_logger", False)
    logging_factory._logger_cache.pop(cache_key, None)

    def fake_create_logger(self):
        logger = object()
        with create_lock:
            created_loggers.append(logger)
        time.sleep(0.01)
        return logger

    with patch.object(logging_factory.SpdLogManager, "create_logger", fake_create_logger):
        loggers: list[object] = []

        def worker():
            loggers.append(logging_factory.get_logger("report_v2_logger"))

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert len({id(logger) for logger in loggers}) == 1
    assert len(created_loggers) == 1


def test_instrument_manager_is_safe_for_concurrent_registration():
    manager = InstrumentManager()
    errors: list[Exception] = []

    def worker(i: int):
        try:
            manager.register(
                Instrument(
                    internal=f"BTC-USDT-{i}",
                    venue="BINANCE___SWAP",
                    venue_symbol=f"BTCUSDT{i}",
                    asset_type=AssetType.SWAP,
                )
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert manager.count() == 40


def test_legacy_feed_registry_proxies_to_exchange_registry():
    saved_default = ExchangeRegistry._default
    ExchangeRegistry._default = None

    try:

        @legacy_feed_registry.register("LEGACY___SPOT")
        class LegacySpotFeed(_LegacyFeed):
            pass

        assert legacy_feed_registry.is_registered("LEGACY___SPOT")
        assert legacy_feed_registry.get_feed("LEGACY___SPOT") is LegacySpotFeed
        assert ExchangeRegistry.get_feed_class("LEGACY___SPOT") is LegacySpotFeed

        legacy_feed_registry.unregister("LEGACY___SPOT")
        assert not legacy_feed_registry.is_registered("LEGACY___SPOT")
    finally:
        ExchangeRegistry._default = saved_default


def test_core_modules_expose_public_api_via_all():
    assert "AuthConfig" in auth_config.__all__
    assert "SimpleCache" in cache.__all__
    assert "EventBus" in event_bus.__all__
    assert "get_logger" in logging_factory.__all__
    assert "ConnectionType" in config_loader.__all__
    assert "RateLimiter" in rate_limiter.__all__


def test_container_singleton_is_consistent_across_threads():
    instances: list[Container] = []

    def worker():
        instances.append(Container())

    threads = [threading.Thread(target=worker) for _ in range(30)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert instances
    assert all(instance is instances[0] for instance in instances)
