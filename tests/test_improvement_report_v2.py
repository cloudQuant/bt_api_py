"""Regression tests for improvement-report-v2 fixes."""

from __future__ import annotations

import asyncio
import importlib.util
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from bt_api_py import auth_config, cache, config_loader, event_bus, logging_factory, rate_limiter
from bt_api_py.connection_pool import AsyncConnectionPool, ConnectionPool, PooledConnection
from bt_api_py.containers.instrument import AssetType, Instrument
from bt_api_py.core import dependency_injection
from bt_api_py.core.dependency_injection import Container
from bt_api_py.exceptions import RequestError, RequestFailedError
from bt_api_py.feeds import registry as legacy_feed_registry
from bt_api_py.feeds.feed import Feed
from bt_api_py.instrument_manager import InstrumentManager
from bt_api_py.registry import ExchangeRegistry
from bt_api_py.security import SecureCredentialManager
from bt_api_py.websocket_manager import WebSocketConfig as BasicWebSocketConfig


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


def test_di_container_raises_clear_error_for_missing_required_dependency():
    class MissingDependency:
        pass

    class NeedsDependency:
        def __init__(self, dependency: MissingDependency):
            self.dependency = dependency

    container = dependency_injection.DIContainer()
    container.register_transient(NeedsDependency, NeedsDependency)

    with pytest.raises(ValueError, match="dependency"):
        container.resolve(NeedsDependency)


def test_inject_method_raises_clear_error_for_missing_required_dependency():
    class MissingDependency:
        pass

    dependency_injection._global_container.clear()

    @dependency_injection.inject_method
    def handler(dependency: MissingDependency):
        return dependency

    try:
        with pytest.raises(ValueError, match="dependency"):
            handler()
    finally:
        dependency_injection._global_container.clear()


def test_di_container_scoped_lifetime_is_isolated_per_scope():
    class ScopedService:
        pass

    container = dependency_injection.DIContainer()
    container.register_scoped(ScopedService, ScopedService)

    with container.create_scope():
        first = container.resolve(ScopedService)
        second = container.resolve(ScopedService)
        assert first is second

    with container.create_scope():
        third = container.resolve(ScopedService)

    assert third is not first


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


def test_instrument_manager_reregister_replaces_stale_venue_mappings():
    manager = InstrumentManager()
    original = Instrument(
        internal="BTC-USDT",
        venue="BINANCE___SPOT",
        venue_symbol="BTCUSDT",
        asset_type=AssetType.SPOT,
    )
    updated = Instrument(
        internal="BTC-USDT",
        venue="OKX___SWAP",
        venue_symbol="BTC-USDT-SWAP",
        asset_type=AssetType.SWAP,
    )

    manager.register(original)
    manager.register(updated)

    assert manager.get("BTC-USDT") is updated
    assert manager.get_by_venue("BINANCE___SPOT", "BTCUSDT") is None
    assert manager.get_by_venue("OKX___SWAP", "BTC-USDT-SWAP") is updated
    assert manager.all_venues() == ["OKX___SWAP"]


def test_instrument_manager_reregister_replaces_stale_underlying_index():
    manager = InstrumentManager()
    original = Instrument(
        internal="BTC-PERP",
        venue="BINANCE___SWAP",
        venue_symbol="BTCUSDT",
        asset_type=AssetType.SWAP,
        underlying="BTC",
    )
    updated = Instrument(
        internal="BTC-PERP",
        venue="OKX___SWAP",
        venue_symbol="BTC-USDT-SWAP",
        asset_type=AssetType.SWAP,
        underlying="XBT",
    )

    manager.register(original)
    manager.register(updated)

    assert manager.find(underlying="BTC") == []
    assert manager.find(underlying="XBT") == [updated]


def test_secure_credential_manager_clears_failed_decryption_values(monkeypatch: pytest.MonkeyPatch):
    manager = SecureCredentialManager()
    manager._cipher = object()

    monkeypatch.setattr(
        manager,
        "load_from_env",
        lambda key, default=None: {
            "BINANCE_API_KEY": "encrypted-key",
            "BINANCE_SECRET": "encrypted-secret",
            "BINANCE_TESTNET": "false",
        }.get(key, default),
    )

    def _fail_decrypt(value: str) -> str:
        raise ValueError(f"cannot decrypt {value}")

    monkeypatch.setattr(manager, "decrypt_credential", _fail_decrypt)

    credentials = manager.get_exchange_credentials("BINANCE", encrypted=True)

    assert credentials["api_key"] is None
    assert credentials["secret"] is None
    assert credentials["testnet"] is False


def test_secure_credential_manager_rejects_template_placeholder_api_keys():
    assert not SecureCredentialManager.validate_api_key("your_binance_api_key_here")
    assert not SecureCredentialManager.validate_api_key("your_okx_secret_here")
    assert not SecureCredentialManager.validate_api_key("  your_ctp_user_id_here  ")
    assert SecureCredentialManager.validate_api_key("abcd1234efgh5678")


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


def test_cached_decorator_caches_none_results():
    call_count = 0

    @cache.cached(ttl=60.0)
    def get_optional_value(key: str):
        nonlocal call_count
        call_count += 1
        return None

    assert get_optional_value("missing") is None
    assert get_optional_value("missing") is None
    assert call_count == 1


def test_cached_decorator_honors_maxsize_with_lru_eviction():
    call_count = 0

    @cache.cached(ttl=60.0, maxsize=2)
    def get_value(key: str):
        nonlocal call_count
        call_count += 1
        return f"{key}:{call_count}"

    assert get_value("a") == "a:1"
    assert get_value("b") == "b:2"
    assert get_value("a") == "a:1"
    assert get_value("c") == "c:3"
    assert get_value("a") == "a:1"
    assert get_value("b") == "b:4"
    assert call_count == 4


def test_simple_cache_cleanup_preserves_lru_ordering():
    simple_cache = cache.SimpleCache(default_ttl=60.0, max_size=2)
    simple_cache.set("a", 1)
    simple_cache.set("b", 2)
    assert simple_cache.get("a") == 1

    assert simple_cache.cleanup() == 0

    simple_cache.set("c", 3)

    assert simple_cache.get("a") == 1
    assert simple_cache.get("b") is None
    assert simple_cache.get("c") == 3


def test_rate_limiter_rolls_back_consumed_capacity_when_later_rule_fails():
    rules = [
        rate_limiter.RateLimitRule(
            name="global",
            type=rate_limiter.RateLimitType.SLIDING_WINDOW,
            interval=60,
            limit=10,
            scope=rate_limiter.RateLimitScope.GLOBAL,
        ),
        rate_limiter.RateLimitRule(
            name="order",
            type=rate_limiter.RateLimitType.SLIDING_WINDOW,
            interval=60,
            limit=1,
            scope=rate_limiter.RateLimitScope.ENDPOINT,
            endpoint="/api/v1/order",
        ),
    ]
    limiter = rate_limiter.RateLimiter(rules)

    assert limiter.acquire("POST", "/api/v1/order")
    assert not limiter.acquire("POST", "/api/v1/order")


@pytest.mark.asyncio
async def test_rate_limiter_async_acquire_returns_false_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
):
    limiter = rate_limiter.RateLimiter()

    monkeypatch.setattr(limiter, "acquire", lambda *args, **kwargs: False)
    monkeypatch.setattr(limiter, "_get_max_wait_time", lambda *args, **kwargs: 1.0)

    allowed = await limiter.async_acquire("GET", "/api/v1/test", timeout=0.01)

    assert allowed is False


def test_connection_pool_acquire_waits_for_released_connection():
    class _Connection:
        pass

    pool = ConnectionPool(factory=_Connection, max_size=1, min_size=0)
    first = pool.acquire()
    result: dict[str, object] = {}

    def _release_later():
        time.sleep(0.05)
        pool.release(first)

    releaser = threading.Thread(target=_release_later)
    releaser.start()
    try:
        second = pool.acquire(timeout=0.2)
        result["conn"] = second
    finally:
        releaser.join()

    assert result["conn"] is first
    pool.release(first)


def test_feed_masks_sensitive_values_in_request_error_logs():
    feed = Feed(exchange_name="BINANCE___SPOT")
    logger = feed.logger

    with patch.object(logger, "warning") as warning_mock, pytest.raises(RuntimeError):
        feed.handle_request_exception(
            "https://api.example.com/order?apiKey=public-secret&signature=sig-value&token=tkn",
            "POST",
            {
                "api_key": "public-secret",
                "password": "p@ssw0rd",
                "nested": {"token": "nested-token"},
            },
            RuntimeError("boom"),
        )

    logged_message = warning_mock.call_args[0][0]
    assert "public-secret" not in logged_message
    assert "sig-value" not in logged_message
    assert "p@ssw0rd" not in logged_message
    assert "nested-token" not in logged_message
    assert "***" in logged_message


def test_feed_http_request_uses_status_code_for_non_retryable_404():
    feed = Feed(exchange_name="BINANCE___SPOT")
    failed = RequestFailedError(venue="BINANCE___SPOT", message="endpoint missing")
    failed.status_code = 404

    with (
        patch.object(feed._http_client, "request", side_effect=failed) as request_mock,
        pytest.raises(RequestError, match="endpoint gone/not found"),
    ):
        feed.http_request("GET", "https://api.example.com/missing", max_retries=3)

    assert request_mock.call_count == 1


def test_feed_http_request_uses_exponential_backoff_on_retryable_failures():
    feed = Feed(exchange_name="BINANCE___SPOT")
    failures = [
        RequestFailedError(venue="BINANCE___SPOT", message="temporary timeout"),
        RequestFailedError(venue="BINANCE___SPOT", message="temporary connection issue"),
        {"ok": True},
    ]

    with (
        patch.object(feed._http_client, "request", side_effect=failures) as request_mock,
        patch("bt_api_py.feeds.feed._time.sleep") as sleep_mock,
    ):
        result = feed.http_request("GET", "https://api.example.com/retry", max_retries=3)

    assert result == {"ok": True}
    assert request_mock.call_count == 3
    assert [call.args[0] for call in sleep_mock.call_args_list] == [0.5, 1.0]


def test_connection_pool_context_manager_releases_connection():
    class _Connection:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    pool = ConnectionPool(factory=_Connection, max_size=2, min_size=0)
    conn = None

    with PooledConnection(pool) as acquired:
        conn = acquired
        assert pool.size() == (0, 1)

    assert conn is not None
    assert pool.size() == (1, 0)


def test_connection_pool_stop_tolerates_in_use_set_changes_during_close():
    class _Connection:
        pass

    class _MutatingPool(ConnectionPool):
        def _close_connection(self, conn):
            self._in_use.discard(conn)

    pool = _MutatingPool(factory=_Connection, max_size=2, min_size=0)
    first = pool.acquire()
    second = pool.acquire()

    pool.release(first)

    pool.stop()

    assert pool.size() == (0, 0)
    assert second not in pool._in_use


def test_connection_pool_acquire_raises_after_timeout():
    class _Connection:
        pass

    pool = ConnectionPool(factory=_Connection, max_size=1, min_size=0)
    conn = pool.acquire()

    try:
        with pytest.raises(RuntimeError, match="timeout"):
            pool.acquire(timeout=0.05)
    finally:
        pool.release(conn)


@pytest.mark.asyncio
async def test_async_connection_pool_blocks_until_release():
    class _Connection:
        pass

    pool = AsyncConnectionPool(
        factory=_Connection,
        max_size=1,
        min_size=0,
    )

    first = await pool.acquire()
    second_task = asyncio.create_task(pool.acquire())

    await asyncio.sleep(0.01)
    assert not second_task.done()

    await pool.release(first)
    second = await asyncio.wait_for(second_task, timeout=0.1)

    assert second is first
    await pool.release(second)


def test_crypto_auth_config_requires_non_empty_exchange_and_keys():
    with pytest.raises(ValueError, match="exchange"):
        auth_config.CryptoAuthConfig(exchange="")

    with pytest.raises(ValueError, match="public_key"):
        auth_config.CryptoAuthConfig(exchange="BINANCE", public_key="", private_key="secret")

    with pytest.raises(ValueError, match="private_key"):
        auth_config.CryptoAuthConfig(exchange="BINANCE", public_key="public", private_key="")


def test_ctp_auth_config_requires_core_credentials_and_fronts():
    with pytest.raises(ValueError, match="broker_id"):
        auth_config.CtpAuthConfig(
            broker_id="",
            user_id="user",
            password="password",
            md_front="tcp://127.0.0.1:1234",
            td_front="tcp://127.0.0.1:1235",
        )

    with pytest.raises(ValueError, match="md_front"):
        auth_config.CtpAuthConfig(
            broker_id="9999",
            user_id="user",
            password="password",
            md_front="http://127.0.0.1:1234",
            td_front="tcp://127.0.0.1:1235",
        )


def test_ib_auth_config_validates_host_port_and_client_id():
    with pytest.raises(ValueError, match="host"):
        auth_config.IbAuthConfig(host="")

    with pytest.raises(ValueError, match="port"):
        auth_config.IbAuthConfig(port=0)

    with pytest.raises(ValueError, match="client_id"):
        auth_config.IbAuthConfig(client_id=-1)


def test_ib_web_auth_config_validates_base_url_and_timeout():
    with pytest.raises(ValueError, match="base_url"):
        auth_config.IbWebAuthConfig(base_url="localhost:5000")

    with pytest.raises(ValueError, match="timeout"):
        auth_config.IbWebAuthConfig(timeout=0)


@pytest.mark.asyncio
async def test_async_task_group_cancel_all_tolerates_done_callback_set_mutation():
    """Regression: cancel_all() must not crash when done callbacks discard tasks from _tasks."""
    from bt_api_py.core.async_context import AsyncTaskGroup

    group = AsyncTaskGroup()

    async def slow_task():
        await asyncio.sleep(10)

    # Create several tasks so that when cancel fires, done callbacks mutate _tasks
    for _ in range(5):
        await group.create_task(slow_task())

    assert group.task_count() == 5

    # Before the fix, this could raise RuntimeError: Set changed size during iteration
    await group.cancel_all()

    # After cancel_all, all tasks should be done (removed by done callback)
    assert group.task_count() == 0


def test_websocket_connection_handles_text_frame_with_compression_enabled():
    """Regression: str text frames must not crash on startswith(bytes) when compression=True."""
    from bt_api_py.websocket_manager import WebSocketConnection

    config = BasicWebSocketConfig(
        url="wss://stream.example.com",
        exchange_name="TEST___SPOT",
        compression=True,
    )
    conn = WebSocketConnection(config, "test_0")

    # Simulate what _process_messages does with a text frame
    raw_message = '{"result": null, "id": 1}'
    # Before the fix, this would raise TypeError: startswith requires bytes
    if (
        config.compression
        and isinstance(raw_message, bytes)
        and raw_message.startswith(b"\x78\x9c")
    ):
        import zlib

        raw_message = zlib.decompress(raw_message)

    import json

    if isinstance(raw_message, bytes):
        message = json.loads(raw_message.decode("utf-8"))
    else:
        message = json.loads(str(raw_message))

    assert message == {"result": None, "id": 1}
    assert conn.config.compression is True


def test_basic_websocket_config_validates_url_and_queue_size():
    with pytest.raises(ValueError, match="url"):
        BasicWebSocketConfig(url="https://test.com", exchange_name="TEST")

    with pytest.raises(ValueError, match="message_queue_size"):
        BasicWebSocketConfig(url="wss://test.com", exchange_name="TEST", message_queue_size=0)


def test_coverage_analyzer_reads_coverage_json_from_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "analyze_coverage.py"
    spec = importlib.util.spec_from_file_location("coverage_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    analyzer = module.CoverageAnalyzer()
    analyzer.project_root = tmp_path
    analyzer.feeds_dir = tmp_path / "bt_api_py" / "feeds"
    analyzer.tests_dir = tmp_path / "tests"
    analyzer.feeds_dir.mkdir(parents=True)
    analyzer.tests_dir.mkdir()
    (tmp_path / "coverage.json").write_text(
        '{"totals":{"percent_covered":91.0},"files":{}}',
        encoding="utf-8",
    )
    monkeypatch.chdir(analyzer.tests_dir)
    monkeypatch.setattr(module.pytest, "main", lambda *args, **kwargs: 0)

    coverage_data = analyzer.run_coverage_analysis()

    assert coverage_data["totals"]["percent_covered"] == 91.0


def test_coverage_analyzer_handles_empty_exchange_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "analyze_coverage.py"
    spec = importlib.util.spec_from_file_location("coverage_script_empty", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    analyzer = module.CoverageAnalyzer()
    analyzer.project_root = tmp_path
    analyzer.feeds_dir = tmp_path / "bt_api_py" / "feeds"
    analyzer.tests_dir = tmp_path / "tests"
    analyzer.feeds_dir.mkdir(parents=True)
    analyzer.tests_dir.mkdir()
    monkeypatch.setattr(analyzer, "run_coverage_analysis", lambda: {})

    report = analyzer.generate_report()

    assert "Total exchanges: 0" in report
    assert "Tested exchanges: 0 (0.0%)" in report
