"""Regression tests for the basic websocket manager."""

import asyncio
import json
from typing import Any

import pytest
from websockets import WebSocketException

from bt_api_py.exceptions import RateLimitError, WebSocketError
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


class _FailingSendWebSocket:
    async def send(self, payload: str) -> None:
        raise OSError(f"send failed: {payload}")


class _StubConnection:
    def __init__(self, *, connection_id: str, connected: bool, subscription_count: int = 0) -> None:
        self.connection_id = connection_id
        self.connected = connected
        self.subscription_count = subscription_count
        self.unsubscribe_calls: list[str] = []
        self._subscription_ids: set[str] = set()

    async def disconnect(self) -> None:
        self.connected = False

    async def unsubscribe(self, subscription_id: str) -> None:
        self.unsubscribe_calls.append(subscription_id)
        self._subscription_ids.discard(subscription_id)

    def has_subscription(self, subscription_id: str) -> bool:
        return subscription_id in self._subscription_ids

    def get_stats(self) -> dict[str, Any]:
        return {
            "connection_id": self.connection_id,
            "connected": self.connected,
            "subscriptions": self.subscription_count,
            "reconnect_attempts": 0,
        }


def test_websocket_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="url must be a valid ws/wss URL"):
        WebSocketConfig(url="https://example.com", exchange_name="TEST")

    with pytest.raises(ValueError, match="exchange_name must be a non-empty string"):
        WebSocketConfig(url="wss://example.com/ws", exchange_name="")

    with pytest.raises(ValueError, match="max_connections must be a positive integer"):
        WebSocketConfig(url="wss://example.com/ws", exchange_name="TEST", max_connections=0)


@pytest.mark.asyncio
async def test_send_message_requires_active_connection() -> None:
    connection = WebSocketConnection(
        WebSocketConfig(url="wss://example.com/ws", exchange_name="TEST___SPOT"),
        "send_guard",
    )

    with pytest.raises(WebSocketError, match="Not connected"):
        await connection._send_message({"action": "ping"})


@pytest.mark.asyncio
async def test_send_message_wraps_transport_errors() -> None:
    connection = WebSocketConnection(
        WebSocketConfig(url="wss://example.com/ws", exchange_name="TEST___SPOT"),
        "send_error",
    )
    connection._connected = True
    connection._websocket = _FailingSendWebSocket()

    with pytest.raises(WebSocketError, match="send failed"):
        await connection._send_message({"action": "ping"})


@pytest.mark.asyncio
async def test_subscribe_enforces_subscription_limit() -> None:
    config = WebSocketConfig(
        url="wss://example.com/ws",
        exchange_name="TEST___SPOT",
        subscription_limits={"ticker": 1},
    )
    connection = WebSocketConnection(config, "limit_case")
    connection._subscription_count["ticker"] = 1

    with pytest.raises(RateLimitError, match="Subscription limit exceeded for ticker"):
        await connection.subscribe(Subscription("sub-1", "ticker", "BTCUSDT", {}))


@pytest.mark.asyncio
async def test_unsubscribe_noop_for_missing_subscription() -> None:
    connection = WebSocketConnection(
        WebSocketConfig(url="wss://example.com/ws", exchange_name="TEST___SPOT"),
        "unsubscribe_noop",
    )

    await connection.unsubscribe("missing")

    assert connection._stats["subscriptions_removed"] == 0


@pytest.mark.asyncio
async def test_unsubscribe_removes_subscription_and_sends_message() -> None:
    connection = WebSocketConnection(
        WebSocketConfig(url="wss://example.com/ws", exchange_name="BINANCE___SPOT"),
        "unsubscribe_live",
    )
    websocket = _DummyWebSocket()
    connection._connected = True
    connection._websocket = websocket
    subscription = Subscription("sub-binance", "ticker", "BTCUSDT", {})
    connection._subscriptions[subscription.id] = subscription
    connection._subscription_count[subscription.topic] = 1

    await connection.unsubscribe(subscription.id)

    assert subscription.id not in connection._subscriptions
    assert connection._subscription_count[subscription.topic] == 0
    assert connection._stats["subscriptions_removed"] == 1
    assert json.loads(websocket.sent[0]) == {
        "method": "UNSUBSCRIBE",
        "params": ["btcusdt@ticker"],
        "id": "sub-binance",
    }


@pytest.mark.asyncio
async def test_send_subscription_uses_okx_format() -> None:
    connection = WebSocketConnection(
        WebSocketConfig(url="wss://example.com/ws", exchange_name="OKX___SWAP"),
        "okx_sub",
    )
    websocket = _DummyWebSocket()
    connection._connected = True
    connection._websocket = websocket

    await connection._send_subscription(Subscription("sub-okx", "books", "BTC-USDT-SWAP", {}))

    assert json.loads(websocket.sent[0]) == {
        "op": "subscribe",
        "args": [{"channel": "books", "instId": "BTC-USDT-SWAP"}],
    }


@pytest.mark.asyncio
async def test_send_unsubscription_uses_generic_format() -> None:
    connection = WebSocketConnection(
        WebSocketConfig(url="wss://example.com/ws", exchange_name="CUSTOM___SPOT"),
        "generic_unsub",
    )
    websocket = _DummyWebSocket()
    connection._connected = True
    connection._websocket = websocket

    await connection._send_unsubscription(Subscription("sub-generic", "ticker", "ETHUSDT", {}))

    assert json.loads(websocket.sent[0]) == {
        "action": "unsubscribe",
        "topic": "ticker",
        "symbol": "ETHUSDT",
    }


def test_extract_topic_symbol_supports_okx_and_unknown_messages() -> None:
    okx_connection = WebSocketConnection(
        WebSocketConfig(url="wss://example.com/ws", exchange_name="OKX___SWAP"),
        "okx_extract",
    )
    assert okx_connection._extract_topic_symbol(
        {"arg": {"channel": "books", "instId": "BTC-USDT-SWAP"}, "data": [{}]}
    ) == ("books", "BTC-USDT-SWAP")

    generic_connection = WebSocketConnection(
        WebSocketConfig(url="wss://example.com/ws", exchange_name="CUSTOM___SPOT"),
        "generic_extract",
    )
    assert generic_connection._extract_topic_symbol({"payload": "noop"}) == (None, None)


@pytest.mark.asyncio
async def test_handle_message_routes_event_and_unknown_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = WebSocketConnection(
        WebSocketConfig(url="wss://example.com/ws", exchange_name="TEST___SPOT"),
        "handle_paths",
    )
    seen_events: list[dict[str, Any]] = []

    async def fake_event_handler(message: dict[str, Any]) -> None:
        seen_events.append(message)

    monkeypatch.setattr(connection, "_handle_event_message", fake_event_handler)

    await connection._handle_message({"event": "subscribe", "id": 1})
    await connection._handle_message({"unexpected": True})

    assert seen_events == [{"event": "subscribe", "id": 1}]


@pytest.mark.asyncio
async def test_manager_get_connection_rejects_unknown_exchange() -> None:
    manager = WebSocketManager(event_bus=_DummyEventBus())

    with pytest.raises(ValueError, match="Exchange MISSING not configured"):
        await manager.get_connection("MISSING")


@pytest.mark.asyncio
async def test_manager_get_connection_uses_round_robin_when_pool_is_full() -> None:
    manager = WebSocketManager(event_bus=_DummyEventBus())
    config = WebSocketConfig(url="wss://example.com/ws", exchange_name="TEST___SPOT", max_connections=2)
    first = _StubConnection(connection_id="c1", connected=True, subscription_count=50)
    second = _StubConnection(connection_id="c2", connected=True, subscription_count=50)
    manager._pools[config.exchange_name] = [first, second]
    manager._pool_configs[config.exchange_name] = config
    manager._pool_locks[config.exchange_name] = asyncio.Lock()

    chosen_first = await manager.get_connection(config.exchange_name)
    chosen_second = await manager.get_connection(config.exchange_name)

    assert chosen_first is first
    assert chosen_second is second


@pytest.mark.asyncio
async def test_manager_subscribe_and_unsubscribe_publish_events(monkeypatch: pytest.MonkeyPatch) -> None:
    event_bus = _DummyEventBus()
    manager = WebSocketManager(event_bus=event_bus)
    config = WebSocketConfig(url="wss://example.com/ws", exchange_name="TEST___SPOT")
    connection = _StubConnection(connection_id="managed", connected=True)
    subscribed: list[Subscription] = []

    async def fake_get_connection(exchange_name: str) -> _StubConnection:
        assert exchange_name == config.exchange_name
        return connection

    async def fake_subscribe(subscription: Subscription) -> None:
        subscribed.append(subscription)
        connection._subscription_ids.add(subscription.id)

    monkeypatch.setattr(manager, "get_connection", fake_get_connection)
    connection.subscribe = fake_subscribe  # type: ignore[attr-defined]
    manager._pools[config.exchange_name] = [connection]  # type: ignore[list-item]
    manager._pool_configs[config.exchange_name] = config
    manager._pool_locks[config.exchange_name] = asyncio.Lock()

    subscription_id = await manager.subscribe(config.exchange_name, "ticker", "BTCUSDT", lambda _: None)
    await manager.unsubscribe(config.exchange_name, subscription_id)

    assert len(subscribed) == 1
    assert subscribed[0].id == subscription_id
    assert event_bus.events == [
        (
            "websocket_subscribed",
            {
                "exchange": config.exchange_name,
                "topic": "ticker",
                "symbol": "BTCUSDT",
                "subscription_id": subscription_id,
            },
        ),
        (
            "websocket_unsubscribed",
            {"exchange": config.exchange_name, "subscription_id": subscription_id},
        ),
    ]


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


@pytest.mark.asyncio
async def test_close_all_clears_pools_and_resets_round_robin() -> None:
    manager = WebSocketManager(event_bus=_DummyEventBus())
    config = WebSocketConfig(url="wss://example.com/ws", exchange_name="TEST___SPOT")
    connection = WebSocketConnection(config, "test_5")
    websocket = _DummyWebSocket()
    connection._websocket = websocket
    connection._connected = True
    connection._running = True
    manager._pools[config.exchange_name] = [connection]
    manager._pool_configs[config.exchange_name] = config
    manager._pool_locks[config.exchange_name] = asyncio.Lock()
    manager._round_robin[config.exchange_name] = 7

    await manager.close_all()

    assert websocket.closed is True
    assert manager._pools[config.exchange_name] == []
    assert manager._round_robin[config.exchange_name] == 0
    assert manager.get_pool_stats()[config.exchange_name]["total_connections"] == 0


@pytest.mark.asyncio
async def test_get_connection_creates_fresh_connection_after_close_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = WebSocketManager(event_bus=_DummyEventBus())
    config = WebSocketConfig(
        url="wss://example.com/ws",
        exchange_name="TEST___SPOT",
        max_connections=1,
    )
    await manager.add_exchange(config)

    async def fake_connect(self: WebSocketConnection) -> None:
        self._connected = True
        self._running = True
        self._websocket = _DummyWebSocket()

    async def fake_disconnect(self: WebSocketConnection) -> None:
        self._running = False
        await self._close_websocket()
        self._connected = False

    monkeypatch.setattr(WebSocketConnection, "connect", fake_connect)
    monkeypatch.setattr(WebSocketConnection, "disconnect", fake_disconnect)

    try:
        first = await manager.get_connection(config.exchange_name)
        await manager.close_all()
        second = await manager.get_connection(config.exchange_name)

        assert first is not second
        assert second.connected is True
    finally:
        await manager.close_all()


@pytest.mark.asyncio
async def test_stop_closes_all_connections_and_resets_task_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = WebSocketManager(event_bus=_DummyEventBus())
    config = WebSocketConfig(
        url="wss://example.com/ws",
        exchange_name="TEST___SPOT",
        max_connections=1,
    )
    await manager.add_exchange(config)

    async def fake_connect(self: WebSocketConnection) -> None:
        self._connected = True
        self._running = True
        self._websocket = _DummyWebSocket()

    async def fake_disconnect(self: WebSocketConnection) -> None:
        self._running = False
        await self._close_websocket()
        self._connected = False

    monkeypatch.setattr(WebSocketConnection, "connect", fake_connect)
    monkeypatch.setattr(WebSocketConnection, "disconnect", fake_disconnect)

    await manager.get_connection(config.exchange_name)
    original_task_group = manager._task_group

    await manager.stop()

    assert manager.get_pool_stats()[config.exchange_name]["total_connections"] == 0
    assert manager._task_group is not original_task_group
    assert manager._task_group.task_count() == 0


@pytest.mark.asyncio
async def test_monitor_connection_handles_top_level_websocket_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = WebSocketManager(event_bus=_DummyEventBus())
    config = WebSocketConfig(url="wss://example.com/ws", exchange_name="TEST___SPOT")
    connection = WebSocketConnection(config, "test_6")

    class _FailingPingWebSocket:
        async def ping(self) -> None:
            raise WebSocketException("ping failed")

    async def fake_sleep(_: float) -> None:
        return None

    connection._connected = True
    connection._running = True
    connection._websocket = _FailingPingWebSocket()
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await manager._monitor_connection(connection)

    assert connection._stats["last_heartbeat"] == 0.0
