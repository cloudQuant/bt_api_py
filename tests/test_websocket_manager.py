"""Regression tests for the basic websocket manager."""

import asyncio
from typing import Any

import pytest

from bt_api_py.exceptions import WebSocketError
from bt_api_py.websocket_manager import (
    Subscription,
    WebSocketConfig,
    WebSocketConnection,
    WebSocketManager,
)


class _DummyEventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def publish(self, event_type: str, data: Any) -> None:
        self.events.append((event_type, data))

    async def publish_async(self, event_type: str, data: Any) -> None:
        self.events.append((event_type, data))

    def subscribe(self, event_type: str, handler: Any) -> None:
        return None

    def unsubscribe(self, event_type: str, handler: Any) -> None:
        return None


class _DummyWebSocket:
    def __init__(self) -> None:
        self.closed = False
        self.sent: list[str] = []
        self.ping_count = 0

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True

    async def ping(self) -> None:
        self.ping_count += 1

    async def recv(self) -> str:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_websocket_manager_is_instantiable_and_exposes_connection_stats() -> None:
    manager = WebSocketManager(event_bus=_DummyEventBus())

    assert manager.get_connection_stats() == {}
    assert manager.get_pool_stats() == {}


@pytest.mark.asyncio
async def test_handle_data_message_supports_sync_and_async_callbacks() -> None:
    config = WebSocketConfig(url="wss://example.com/ws", exchange_name="BINANCE___SPOT")
    connection = WebSocketConnection(config, "test_0")

    received: list[tuple[str, dict[str, Any]]] = []

    def sync_callback(message: dict[str, Any]) -> None:
        received.append(("sync", message))

    async def async_callback(message: dict[str, Any]) -> None:
        received.append(("async", message))

    connection._subscriptions = {
        "sync": Subscription(
            id="sync",
            topic="ticker",
            symbol="BTCUSDT",
            params={},
            callback=sync_callback,
        ),
        "async": Subscription(
            id="async",
            topic="ticker",
            symbol="BTCUSDT",
            params={},
            callback=async_callback,
        ),
    }

    message = {"stream": "btcusdt@ticker", "data": {"price": "100"}}
    await connection._handle_data_message(message)

    assert received == [("sync", message), ("async", message)]


@pytest.mark.asyncio
async def test_disconnect_clears_processing_task_and_websocket() -> None:
    config = WebSocketConfig(url="wss://example.com/ws", exchange_name="TEST___SPOT")
    connection = WebSocketConnection(config, "test_1")
    websocket = _DummyWebSocket()
    connection._websocket = websocket
    connection._connected = True
    connection._running = True
    connection._processing_task = asyncio.create_task(asyncio.sleep(3600))

    await connection.disconnect()

    assert connection._processing_task is None
    assert connection._websocket is None
    assert websocket.closed is True
    assert connection.connected is False
    assert connection.running is False


@pytest.mark.asyncio
async def test_handle_disconnect_closes_stale_websocket_when_retries_exhausted() -> None:
    config = WebSocketConfig(
        url="wss://example.com/ws",
        exchange_name="TEST___SPOT",
        max_reconnect_attempts=0,
    )
    connection = WebSocketConnection(config, "test_2")
    websocket = _DummyWebSocket()
    connection._websocket = websocket
    connection._connected = True
    connection._running = True

    await connection._handle_disconnect()

    assert websocket.closed is True
    assert connection._websocket is None
    assert connection.connected is False
    assert connection.running is False


@pytest.mark.asyncio
async def test_handle_disconnect_retries_after_websocket_error_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = WebSocketConfig(
        url="wss://example.com/ws",
        exchange_name="TEST___SPOT",
        reconnect_interval=0.01,
        max_reconnect_attempts=2,
    )
    connection = WebSocketConnection(config, "test_3")
    websocket = _DummyWebSocket()
    connection._websocket = websocket
    connection._connected = True
    connection._running = True

    attempts = 0

    async def fake_sleep(_: float) -> None:
        return None

    async def fake_connect() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise WebSocketError(config.exchange_name, detail="temporary failure")
        connection._connected = True
        connection._websocket = _DummyWebSocket()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(connection, "connect", fake_connect)

    await connection._handle_disconnect()

    assert websocket.closed is True
    assert attempts == 2
    assert connection.connected is True
    assert connection.running is True
    assert connection._reconnect_attempts == 2


@pytest.mark.asyncio
async def test_handle_disconnect_stops_after_websocket_error_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = WebSocketConfig(
        url="wss://example.com/ws",
        exchange_name="TEST___SPOT",
        reconnect_interval=0.01,
        max_reconnect_attempts=2,
    )
    connection = WebSocketConnection(config, "test_4")
    websocket = _DummyWebSocket()
    connection._websocket = websocket
    connection._connected = True
    connection._running = True

    attempts = 0

    async def fake_sleep(_: float) -> None:
        return None

    async def fake_connect() -> None:
        nonlocal attempts
        attempts += 1
        raise WebSocketError(config.exchange_name, detail="still down")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(connection, "connect", fake_connect)

    await connection._handle_disconnect()

    assert websocket.closed is True
    assert attempts == 2
    assert connection.connected is False
    assert connection.running is False
    assert connection._websocket is None
