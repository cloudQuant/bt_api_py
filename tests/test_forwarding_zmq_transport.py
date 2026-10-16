import socket
import time
from collections.abc import Generator
from typing import Any

import pytest

import bt_api_py.forwarding.transport as transport_module
from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.forwarding import MarketEvent, OrderCommand
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


def test_zmq_command_endpoints_are_retired_before_socket_creation(monkeypatch) -> None:
    from bt_api_py._contracts.errors import LegacyForwardingRetiredError

    context_calls: list[str] = []

    class ForbiddenContext:
        @staticmethod
        def instance():
            context_calls.append("instance")
            raise AssertionError("retired command endpoint must fail before context creation")

    monkeypatch.setattr(transport_module.zmq, "Context", ForbiddenContext)
    with pytest.raises(LegacyForwardingRetiredError, match="ServiceExecutionSession"):
        ZmqCommandServer("inproc://retired", lambda _command: None)
    with pytest.raises(LegacyForwardingRetiredError, match="ServiceExecutionSession"):
        ZmqCommandClient("inproc://retired")

    assert context_calls == []


def test_zmq_command_client_legacy_timeout_path_is_retired() -> None:
    from bt_api_py._contracts.errors import LegacyForwardingRetiredError

    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        ZmqCommandClient("inproc://retired")


def test_zmq_command_client_send_is_retired_even_for_uninitialized_instance() -> None:
    from bt_api_py._contracts.errors import LegacyForwardingRetiredError

    client = object.__new__(ZmqCommandClient)
    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        client.send(
            OrderCommand(
                strategy_id="s1",
                account_id="paper",
                symbol="RB2510",
                side="buy",
                size=1,
            )
        )


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


def test_zmq_command_server_start_is_retired_even_for_uninitialized_instance() -> None:
    from bt_api_py._contracts.errors import LegacyForwardingRetiredError

    server = object.__new__(ZmqCommandServer)
    with pytest.raises(LegacyForwardingRetiredError, match="ServiceExecutionSession"):
        server.start()


def test_zmq_forwarding_runtime_is_retired_before_startup() -> None:
    from bt_api_py._contracts.errors import LegacyForwardingRetiredError
    from bt_api_py.forwarding.client import ZmqForwardingClient

    adapter = MockBrokerAdapter()
    with pytest.raises(LegacyForwardingRetiredError, match="ServiceExecutionSession"):
        ZmqForwardingRuntime(
            adapter,
            market_endpoint="tcp://127.0.0.1:7001",
            command_endpoint="tcp://127.0.0.1:7002",
            private_endpoint="tcp://127.0.0.1:7003",
        )
    assert adapter.connected is False

    with pytest.raises(LegacyForwardingRetiredError, match="ServiceClientRuntime"):
        ZmqForwardingClient(
            market_endpoint="tcp://127.0.0.1:7001",
            command_endpoint="tcp://127.0.0.1:7002",
        )
