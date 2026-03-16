"""
Tests for bt_api_py.core.services
Core service lifecycle and behavior tests
"""

import asyncio
from unittest.mock import patch

import pytest

from bt_api_py.core.services import (
    AccountService,
    ConnectionService,
    EventService,
    MarketDataService,
    TradingService,
)


class TestEventService:
    """Test EventService lifecycle"""

    @pytest.mark.asyncio
    async def test_event_service_start_stop(self):
        """Test EventService initialization and start/stop"""
        service = EventService()
        await service.start()
        assert service._running is True
        assert service._processor_task is not None
        await service.stop()
        assert service._running is False
        assert service._processor_task is None
        assert service._event_queue is None

    @pytest.mark.asyncio
    async def test_event_service_publish(self):
        """Test event publishing"""
        service = EventService()
        await service.start()

        received = []

        def callback(event):
            received.append(event)

        service.subscribe("test_topic", callback)
        service.publish("test_topic", {"data": "test"})

        await asyncio.sleep(0.1)
        assert len(received) == 1
        assert received[0] == {"data": "test"}

        await service.stop()

    @pytest.mark.asyncio
    async def test_event_service_subscribe_unsubscribe(self):
        """Test event subscription and unsubscription"""
        service = EventService()
        await service.start()

        received = []

        def callback(event):
            received.append(event)

        service.subscribe("test_topic", callback)
        service.unsubscribe("test_topic", callback)
        service.publish("test_topic", {"data": "test"})

        await asyncio.sleep(0.1)
        assert len(received) == 0

        await service.stop()

    @pytest.mark.asyncio
    async def test_event_service_can_restart_cleanly(self):
        """Stopping should release queue/task references so restart creates fresh ones."""
        service = EventService()

        await service.start()
        first_queue = service._event_queue
        first_task = service._processor_task
        await service.stop()

        await service.start()

        assert service._event_queue is not None
        assert service._event_queue is not first_queue
        assert service._processor_task is not None
        assert service._processor_task is not first_task

        await service.stop()

    def test_event_service_publish_falls_back_to_sync_without_running_loop(self):
        """Sync publish should not crash if called while marked running outside an event loop."""
        service = EventService()
        received: list[dict[str, str]] = []

        service._running = True
        service._event_queue = asyncio.Queue()
        service.subscribe("test_topic", lambda event: received.append(event))

        with patch("bt_api_py.core.services.asyncio.get_running_loop", side_effect=RuntimeError):
            service.publish("test_topic", {"data": "sync"})

        assert received == [{"data": "sync"}]


class _Closable:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class TestConnectionService:
    @pytest.mark.asyncio
    async def test_close_all_resets_active_connection_stats(self):
        service = ConnectionService()
        session = _Closable()
        service._pools["BINANCE___SPOT"] = {"session": session}
        service._stats["BINANCE___SPOT"]["active_connections"] = 3

        await service.close_all()

        assert session.closed is True
        assert service._stats["BINANCE___SPOT"]["active_connections"] == 0


class _StubConnectionManager:
    def __init__(self) -> None:
        self.connections_requested: list[str] = []
        self.released: list[tuple[str, object]] = []
        self.connection = object()

    async def get_connection(self, exchange_name: str) -> object:
        self.connections_requested.append(exchange_name)
        return self.connection

    async def release_connection(self, exchange_name: str, connection: object) -> None:
        self.released.append((exchange_name, connection))


class _StubCache:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.set_calls: list[tuple[str, object, int | None]] = []

    async def get(self, key: str) -> object | None:
        return self.data.get(key)

    async def set(self, key: str, value: object, ttl: int | None = None) -> None:
        self.data[key] = value
        self.set_calls.append((key, value, ttl))

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)

    async def clear(self) -> None:
        self.data.clear()


class _StubRateLimiter:
    def __init__(self) -> None:
        self.configured: list[tuple[str, int, float]] = []
        self.acquired: list[tuple[str, int]] = []

    def configure_limit(self, resource: str, max_requests: int, time_window: float) -> None:
        self.configured.append((resource, max_requests, time_window))

    async def acquire(self, resource: str, tokens: int = 1) -> None:
        self.acquired.append((resource, tokens))


class _StubEventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def publish(self, event_type: str, data: object) -> None:
        self.events.append((event_type, data))

    async def publish_async(self, event_type: str, data: object) -> None:
        self.events.append((event_type, data))


class TestDomainServices:
    @pytest.mark.asyncio
    async def test_market_data_service_uses_cache_before_connection(self):
        connection_manager = _StubConnectionManager()
        cache = _StubCache()
        rate_limiter = _StubRateLimiter()
        event_bus = _StubEventBus()
        cache.data["ticker:BINANCE___SPOT:BTCUSDT"] = {"symbol": "BTCUSDT", "price": 1.0}
        service = MarketDataService(
            connection_manager=connection_manager,
            cache_service=cache,
            rate_limiter=rate_limiter,
            event_bus=event_bus,
        )

        result = await service.get_ticker("BINANCE___SPOT", "BTCUSDT")

        assert result == {"symbol": "BTCUSDT", "price": 1.0}
        assert connection_manager.connections_requested == []
        assert rate_limiter.acquired == []
        assert event_bus.events == []

    @pytest.mark.asyncio
    async def test_trading_service_places_order_and_releases_connection(self):
        connection_manager = _StubConnectionManager()
        rate_limiter = _StubRateLimiter()
        event_bus = _StubEventBus()
        service = TradingService(
            connection_manager=connection_manager,
            event_bus=event_bus,
            rate_limiter=rate_limiter,
        )

        order = await service.place_order(
            exchange_name="BINANCE___SPOT",
            symbol="BTCUSDT",
            side="BUY",
            order_type="LIMIT",
            quantity=1.0,
            price=50000.0,
        )

        assert order["symbol"] == "BTCUSDT"
        assert rate_limiter.acquired == [("order", 1)]
        assert connection_manager.released == [("BINANCE___SPOT", connection_manager.connection)]
        assert event_bus.events[0][0] == "order_placed"

    @pytest.mark.asyncio
    async def test_account_service_reads_cache_before_connection(self):
        connection_manager = _StubConnectionManager()
        cache = _StubCache()
        event_bus = _StubEventBus()
        cache.data["balance:OKX___SPOT"] = {"exchange": "OKX___SPOT", "balances": {}}
        service = AccountService(
            connection_manager=connection_manager,
            cache_service=cache,
            event_bus=event_bus,
        )

        result = await service.get_balance("OKX___SPOT")

        assert result == {"exchange": "OKX___SPOT", "balances": {}}
        assert connection_manager.connections_requested == []
        assert event_bus.events == []
