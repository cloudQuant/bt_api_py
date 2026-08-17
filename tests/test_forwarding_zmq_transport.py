import asyncio
import socket
import time
from collections.abc import Callable, Generator
from typing import Any, Optional

import pytest

import bt_api_py.forwarding.service as service_module
import bt_api_py.forwarding.transport as transport_module
from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.forwarding import MarketEvent, OrderCommand, PrivateEvent
from bt_api_py.forwarding.client import ZmqForwardingClient
from bt_api_py.forwarding.schema import CommandAck
from bt_api_py.forwarding.service import ZmqForwardingRuntime
from bt_api_py.forwarding.transport import (
    ZmqCommandClient,
    ZmqCommandServer,
    ZmqMarketPublisher,
    ZmqMarketSubscriber,
    _run_handler,
    serialize_message,
)


def _free_tcp_endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{sock.getsockname()[1]}"


def _wait_for_dropped_event(
    client: ZmqForwardingClient,
    event_kind: str,
    publish_events: Callable[[], None],
    *,
    attempts: int = 30,
    interval: float = 0.05,
) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for _ in range(attempts):
        publish_events()
        time.sleep(interval)
        stats = client.stats()
        if stats["dropped_event_counts"][event_kind] >= 1:
            break
    return stats


class CountingMockBrokerAdapter(MockBrokerAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.connect_count = 0
        self.disconnect_count = 0

    async def connect(self) -> bool:
        self.connect_count += 1
        return await super().connect()

    async def disconnect(self) -> bool:
        self.disconnect_count += 1
        return await super().disconnect()


def test_zmq_market_pub_sub_transports_market_events() -> None:
    endpoint = _free_tcp_endpoint()
    publisher = ZmqMarketPublisher(endpoint)
    subscriber = ZmqMarketSubscriber(endpoint, "md.SIM.SPOT.RB2510.")
    event = MarketEvent(
        event_type="tick",
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        payload={"price": 3500.0},
    )

    received = None
    try:
        for _ in range(10):
            time.sleep(0.05)
            publisher.publish(event)
            received = subscriber.poll(100)
            if received is not None:
                break
    finally:
        subscriber.close()
        publisher.close()

    assert received is not None
    assert received.topic == event.topic
    assert received.payload["price"] == 3500.0


def test_zmq_router_dealer_transports_order_ack() -> None:
    endpoint = _free_tcp_endpoint()

    def handler(command: OrderCommand) -> CommandAck:
        return CommandAck(
            command_id=command.command_id,
            idempotency_key=str(command.idempotency_key),
            accepted=True,
            status="accepted",
            account_id=command.account_id,
            strategy_id=command.strategy_id,
            order_id="order-1",
        )

    server = ZmqCommandServer(endpoint, handler)
    client = ZmqCommandClient(endpoint)
    server.start()
    time.sleep(0.05)
    try:
        ack = client.send(
            OrderCommand(
                strategy_id="s1",
                account_id="paper",
                symbol="RB2510",
                side="buy",
                size=1,
                idempotency_key="idem",
            )
        )
    finally:
        client.close()
        server.stop()

    assert ack.accepted is True
    assert ack.order_id == "order-1"


def test_zmq_command_client_rejects_negative_timeout_before_send() -> None:
    client = ZmqCommandClient(_free_tcp_endpoint())
    try:
        with pytest.raises(ValueError, match="timeout_ms must be non-negative"):
            client.send(
                OrderCommand(
                    strategy_id="s1",
                    account_id="paper",
                    symbol="RB2510",
                    side="buy",
                    size=1,
                ),
                timeout_ms=-1,
            )
    finally:
        client.close()


def test_zmq_client_drains_stale_ack_after_send_timeout() -> None:
    """After a send timeout, stale replies are drained so the next send gets its own ack."""
    cmd1 = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        idempotency_key="cmd1",
    )
    cmd2 = OrderCommand(
        strategy_id="s2",
        account_id="paper",
        symbol="RB2510",
        side="sell",
        size=2,
        idempotency_key="cmd2",
    )

    stale_ack = CommandAck(
        command_id="stale-id",
        idempotency_key="stale-key",
        accepted=True,
        status="accepted",
        order_id="stale-order",
    )
    stale_ack_bytes = serialize_message(stale_ack)

    correct_ack = CommandAck(
        command_id=cmd2.command_id,
        idempotency_key=str(cmd2.idempotency_key),
        accepted=True,
        status="accepted",
        order_id="correct-order",
    )
    correct_ack_bytes = serialize_message(correct_ack)

    class FakeSocket:
        def __init__(self) -> None:
            self.sent: list[bytes] = []
            self._poll_zero_count = 0
            self._recv_count = 0
            self._poll_timeout_count = 0

        def send(self, data: bytes) -> None:
            self.sent.append(data)

        def poll(self, timeout_ms: int) -> int:
            if timeout_ms > 0:
                self._poll_timeout_count += 1
                return 0 if self._poll_timeout_count == 1 else 1
            else:
                # timeout_ms == 0: non-blocking poll for drain loop
                self._poll_zero_count += 1
                return 1 if self._poll_zero_count == 1 else 0

        def recv(self) -> bytes:
            self._recv_count += 1
            if self._recv_count == 1:
                return stale_ack_bytes
            return correct_ack_bytes

        def close(self, linger: int = 0) -> None:
            pass

    fake_socket = FakeSocket()
    client = ZmqCommandClient("tcp://127.0.0.1:9999")
    client.socket = fake_socket

    # First send: should time out (fake poll returns 0)
    with pytest.raises(TimeoutError, match="timed out"):
        client.send(cmd1, timeout_ms=100)

    # Drain loop was triggered: poll(0) called at least twice (once 1, once 0)
    assert fake_socket._poll_zero_count >= 2
    # Stale ack was drained: recv() called once
    assert fake_socket._recv_count == 1

    # Second send: should get the correct ack for cmd2, not the stale one
    reply = client.send(cmd2, timeout_ms=100)
    assert reply.command_id == cmd2.command_id
    assert reply.order_id == "correct-order"
    assert fake_socket._recv_count == 2


def test_zmq_command_handler_accepts_generic_awaitable_ack() -> None:
    command = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        idempotency_key="awaitable-handler",
    )

    class AckAwaitable:
        def __init__(self, ack: CommandAck) -> None:
            self.ack = ack

        def __await__(self) -> Generator[Any, None, CommandAck]:
            async def resolve() -> CommandAck:
                return self.ack

            return resolve().__await__()

    def handler(item: OrderCommand) -> AckAwaitable:
        return AckAwaitable(
            CommandAck(
                command_id=item.command_id,
                idempotency_key=str(item.idempotency_key),
                accepted=True,
                status="accepted",
                account_id=item.account_id,
                strategy_id=item.strategy_id,
                order_id="awaitable-order",
            )
        )

    ack = _run_handler(handler, command)

    assert ack.accepted is True
    assert ack.order_id == "awaitable-order"


def test_zmq_router_dealer_error_ack_preserves_command_identity(
    monkeypatch,
) -> None:
    endpoint = _free_tcp_endpoint()
    warnings: list[str] = []
    monkeypatch.setattr(
        transport_module.logger, "warning", lambda message: warnings.append(message)
    )

    def handler(command: OrderCommand) -> CommandAck:
        raise RuntimeError(f"boom: {command.command_id}")

    server = ZmqCommandServer(endpoint, handler)
    client = ZmqCommandClient(endpoint)
    command = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        idempotency_key="idem-error",
    )

    server.start()
    time.sleep(0.05)
    try:
        ack = client.send(command)
    finally:
        client.close()
        server.stop()

    assert ack.accepted is False
    assert ack.status == "rejected"
    assert ack.command_id == command.command_id
    assert ack.idempotency_key == command.idempotency_key
    assert ack.account_id == "paper"
    assert ack.strategy_id == "s1"
    assert "boom" in ack.reason
    assert warnings == [
        "ZMQ command handler failed: "
        f"command_id={command.command_id}, "
        f"idempotency_key={command.idempotency_key}, "
        "error_type=RuntimeError, "
        f"error=boom: {command.command_id}"
    ]


def test_zmq_forwarding_runtime_serves_market_and_order_clients() -> None:
    market_endpoint = _free_tcp_endpoint()
    command_endpoint = _free_tcp_endpoint()
    private_endpoint = _free_tcp_endpoint()
    runtime = ZmqForwardingRuntime(
        MockBrokerAdapter(),
        market_endpoint=market_endpoint,
        command_endpoint=command_endpoint,
        private_endpoint=private_endpoint,
    )
    client = ZmqForwardingClient(
        market_endpoint=market_endpoint,
        command_endpoint=command_endpoint,
        private_endpoint=private_endpoint,
        exchange="SIM",
        market_type="SPOT",
        account_id="paper",
        strategy_id="s1",
    )

    runtime.start_sync()
    try:
        client.connect()
        client.subscribe("RB2510")
        time.sleep(0.1)
        received_tick = None
        for _ in range(20):
            runtime.market_data.publish_tick(
                exchange="SIM",
                market_type="SPOT",
                symbol="RB2510",
                price=3500.0,
            )
            time.sleep(0.05)
            received_tick = client.poll_tick("RB2510")
            if received_tick is not None:
                break

        response = client.submit_order(
            {
                "bt_order_ref": 1,
                "symbol": "RB2510",
                "side": "buy",
                "size": 1,
                "order_type": "limit",
                "price": 3500.0,
            }
        )
        updates = []
        for _ in range(20):
            update = client.poll_broker_update()
            if update is not None:
                updates.append(update["kind"])
            if "trade" in updates:
                break
            time.sleep(0.05)
    finally:
        client.disconnect()
        runtime.stop_sync()

    assert received_tick is not None
    assert received_tick.price == 3500.0
    assert response["order_id"]
    assert "order" in updates
    assert "trade" in updates


def test_zmq_forwarding_client_stats_refreshes_market_events() -> None:
    market_endpoint = _free_tcp_endpoint()
    command_endpoint = _free_tcp_endpoint()
    private_endpoint = _free_tcp_endpoint()
    runtime = ZmqForwardingRuntime(
        MockBrokerAdapter(),
        market_endpoint=market_endpoint,
        command_endpoint=command_endpoint,
        private_endpoint=private_endpoint,
    )
    client = ZmqForwardingClient(
        market_endpoint=market_endpoint,
        command_endpoint=command_endpoint,
        private_endpoint=private_endpoint,
        exchange="SIM",
        market_type="SPOT",
        account_id="paper",
        strategy_id="s1",
        event_cache_size=1,
    )

    runtime.start_sync()
    try:
        client.connect()
        client.subscribe("RB2510")

        def publish_ticks() -> None:
            runtime.market_data.publish_tick(
                exchange="SIM",
                market_type="SPOT",
                symbol="RB2510",
                price=3499.0,
            )
            runtime.market_data.publish_tick(
                exchange="SIM",
                market_type="SPOT",
                symbol="RB2510",
                price=3500.0,
            )

        stats = _wait_for_dropped_event(client, "tick", publish_ticks)
    finally:
        client.disconnect()
        runtime.stop_sync()

    assert stats["event_cache_size"] == 1
    assert stats["pending_event_counts"]["tick"] == 1
    assert stats["dropped_event_counts"]["tick"] >= 1


def test_zmq_forwarding_client_stats_refreshes_private_events() -> None:
    market_endpoint = _free_tcp_endpoint()
    command_endpoint = _free_tcp_endpoint()
    private_endpoint = _free_tcp_endpoint()
    runtime = ZmqForwardingRuntime(
        MockBrokerAdapter(),
        market_endpoint=market_endpoint,
        command_endpoint=command_endpoint,
        private_endpoint=private_endpoint,
    )
    client = ZmqForwardingClient(
        market_endpoint=market_endpoint,
        command_endpoint=command_endpoint,
        private_endpoint=private_endpoint,
        exchange="SIM",
        market_type="SPOT",
        account_id="paper",
        strategy_id="s1",
        event_cache_size=1,
    )

    runtime.start_sync()
    try:
        client.connect()

        def publish_private_events() -> None:
            runtime.bus.publish_private(
                PrivateEvent(
                    event_type="orders",
                    account_id="paper",
                    strategy_id="s1",
                    payload={"kind": "order", "order_id": "old"},
                )
            )
            runtime.bus.publish_private(
                PrivateEvent(
                    event_type="orders",
                    account_id="paper",
                    strategy_id="s1",
                    payload={"kind": "order", "order_id": "new"},
                )
            )

        stats = _wait_for_dropped_event(client, "broker_update", publish_private_events)
    finally:
        client.disconnect()
        runtime.stop_sync()

    assert stats["event_cache_size"] == 1
    assert stats["pending_event_counts"]["broker_update"] == 1
    assert stats["dropped_event_counts"]["broker_update"] >= 1


def test_zmq_forwarding_client_rejects_negative_command_timeout() -> None:
    with pytest.raises(ValueError, match="command_timeout_ms must be non-negative"):
        ZmqForwardingClient(
            market_endpoint="tcp://127.0.0.1:7001",
            command_endpoint="tcp://127.0.0.1:7002",
            command_timeout_ms=-1,
        )


def test_zmq_forwarding_runtime_health_reports_endpoints_and_running_state() -> None:
    market_endpoint = _free_tcp_endpoint()
    command_endpoint = _free_tcp_endpoint()
    private_endpoint = _free_tcp_endpoint()
    runtime = ZmqForwardingRuntime(
        MockBrokerAdapter(),
        market_endpoint=market_endpoint,
        command_endpoint=command_endpoint,
        private_endpoint=private_endpoint,
    )

    runtime.start_sync()
    time.sleep(0.05)
    try:
        health = asyncio.run(runtime.health())
    finally:
        runtime.stop_sync()

    assert health["runtime"] == "ZmqForwardingRuntime"
    assert health["order_router"]["adapter"]["connected"] is True
    assert health["zmq"]["market_endpoint"] == market_endpoint
    assert health["zmq"]["command_endpoint"] == command_endpoint
    assert health["zmq"]["private_endpoint"] == private_endpoint
    assert health["zmq"]["running"] is True
    assert health["zmq"]["forwarder_thread_count"] >= 1
    assert health["zmq"]["market_publisher_active"] is True
    assert health["zmq"]["private_publisher_active"] is True


def test_zmq_forwarding_runtime_is_running_tracks_lifecycle() -> None:
    market_endpoint = _free_tcp_endpoint()
    command_endpoint = _free_tcp_endpoint()
    private_endpoint = _free_tcp_endpoint()
    runtime = ZmqForwardingRuntime(
        MockBrokerAdapter(),
        market_endpoint=market_endpoint,
        command_endpoint=command_endpoint,
        private_endpoint=private_endpoint,
    )

    assert runtime.is_running is False

    runtime.start_sync()
    try:
        assert runtime.is_running is True
    finally:
        runtime.stop_sync()

    assert runtime.is_running is False


def test_zmq_forwarding_runtime_start_sync_is_idempotent() -> None:
    market_endpoint = _free_tcp_endpoint()
    command_endpoint = _free_tcp_endpoint()
    private_endpoint = _free_tcp_endpoint()
    runtime = ZmqForwardingRuntime(
        MockBrokerAdapter(),
        market_endpoint=market_endpoint,
        command_endpoint=command_endpoint,
        private_endpoint=private_endpoint,
    )

    runtime.start_sync()
    try:
        first_health = asyncio.run(runtime.health())
        runtime.start_sync()
        second_health = asyncio.run(runtime.health())
    finally:
        runtime.stop_sync()

    assert first_health["zmq"]["running"] is True
    assert second_health["zmq"]["running"] is True
    assert (
        second_health["zmq"]["forwarder_thread_count"]
        == first_health["zmq"]["forwarder_thread_count"]
    )


def test_zmq_forwarding_runtime_stop_sync_is_idempotent() -> None:
    market_endpoint = _free_tcp_endpoint()
    command_endpoint = _free_tcp_endpoint()
    private_endpoint = _free_tcp_endpoint()
    adapter = CountingMockBrokerAdapter()
    runtime = ZmqForwardingRuntime(
        adapter,
        market_endpoint=market_endpoint,
        command_endpoint=command_endpoint,
        private_endpoint=private_endpoint,
    )

    runtime.stop_sync()
    assert adapter.disconnect_count == 0

    runtime.start_sync()
    runtime.stop_sync()
    runtime.stop_sync()

    assert adapter.disconnect_count == 1


def test_zmq_forwarding_runtime_start_sync_cleans_up_after_publisher_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    market_endpoint = _free_tcp_endpoint()
    command_endpoint = _free_tcp_endpoint()
    private_endpoint = _free_tcp_endpoint()
    created_publishers: list[Any] = []

    class FailingPublisher:
        def __init__(self, endpoint: str) -> None:
            self.endpoint = endpoint
            self.closed = False
            created_publishers.append(self)
            if endpoint == private_endpoint:
                raise RuntimeError("publisher failed")

        def publish(self, event: object) -> None:
            raise AssertionError("publisher should not publish during failed startup")

        def close(self) -> None:
            self.closed = True

    adapter = CountingMockBrokerAdapter()
    runtime = ZmqForwardingRuntime(
        adapter,
        market_endpoint=market_endpoint,
        command_endpoint=command_endpoint,
        private_endpoint=private_endpoint,
    )
    monkeypatch.setattr(service_module, "ZmqEventPublisher", FailingPublisher)

    with pytest.raises(RuntimeError, match="publisher failed"):
        runtime.start_sync()

    assert adapter.connect_count == 1
    assert adapter.disconnect_count == 1
    assert adapter.connected is False
    assert created_publishers[0].endpoint == market_endpoint
    assert created_publishers[0].closed is True
    assert runtime._market_publisher is None
    assert runtime._private_publisher is None
    assert runtime._command_server is None
    assert runtime._threads == []


def test_zmq_forwarding_runtime_start_sync_cleans_up_after_command_server_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    market_endpoint = _free_tcp_endpoint()
    command_endpoint = _free_tcp_endpoint()
    private_endpoint = _free_tcp_endpoint()
    created_publishers: list[Any] = []

    class TrackingPublisher:
        def __init__(self, endpoint: str) -> None:
            self.endpoint = endpoint
            self.closed = False
            created_publishers.append(self)

        def publish(self, event: object) -> None:
            raise AssertionError("publisher should not publish during failed startup")

        def close(self) -> None:
            self.closed = True

    class FailingCommandServer:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("command server failed")

    adapter = CountingMockBrokerAdapter()
    runtime = ZmqForwardingRuntime(
        adapter,
        market_endpoint=market_endpoint,
        command_endpoint=command_endpoint,
        private_endpoint=private_endpoint,
    )
    monkeypatch.setattr(service_module, "ZmqEventPublisher", TrackingPublisher)
    monkeypatch.setattr(service_module, "ZmqCommandServer", FailingCommandServer)

    with pytest.raises(RuntimeError, match="command server failed"):
        runtime.start_sync()

    assert adapter.connect_count == 1
    assert adapter.disconnect_count == 1
    assert adapter.connected is False
    assert [publisher.endpoint for publisher in created_publishers] == [
        market_endpoint,
        private_endpoint,
    ]
    assert all(publisher.closed for publisher in created_publishers)
    assert runtime._market_publisher is None
    assert runtime._private_publisher is None
    assert runtime._command_server is None
    assert runtime._threads == []


def test_zmq_forwarding_runtime_start_sync_cleans_up_after_thread_start_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    market_endpoint = _free_tcp_endpoint()
    command_endpoint = _free_tcp_endpoint()
    private_endpoint = _free_tcp_endpoint()
    created_threads: list[Any] = []

    class FakeThread:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.ident: Optional[int] = None
            self.join_count = 0
            self.index = len(created_threads)
            created_threads.append(self)

        def start(self) -> None:
            if self.index == 2:
                raise RuntimeError("thread start failed")
            self.ident = self.index + 1

        def join(self, timeout: Optional[float] = None) -> None:
            self.join_count += 1

        def is_alive(self) -> bool:
            return self.ident is not None

    adapter = CountingMockBrokerAdapter()
    runtime = ZmqForwardingRuntime(
        adapter,
        market_endpoint=market_endpoint,
        command_endpoint=command_endpoint,
        private_endpoint=private_endpoint,
    )
    monkeypatch.setattr(service_module.threading, "Thread", FakeThread)

    with pytest.raises(RuntimeError, match="thread start failed"):
        runtime.start_sync()

    assert adapter.connect_count == 1
    assert adapter.disconnect_count == 1
    assert adapter.connected is False
    assert [thread.join_count for thread in created_threads] == [1, 1, 0]
    assert runtime._market_publisher is None
    assert runtime._private_publisher is None
    assert runtime._command_server is None
    assert runtime._threads == []
