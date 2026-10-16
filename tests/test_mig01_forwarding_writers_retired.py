"""Public entry points into the legacy forwarding writer fail closed."""

from __future__ import annotations

import asyncio

import pytest

import bt_api_py.forwarding.transport as transport_module
from bt_api_py._contracts.errors import LegacyForwardingRetiredError
from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.forwarding.client import ForwardingClient
from bt_api_py.forwarding.memory import InMemoryForwardingBus
from bt_api_py.forwarding.router import OrderRouter
from bt_api_py.forwarding.schema import MarketEvent, OrderCommand, PrivateEvent
from bt_api_py.forwarding.transport import ZmqCommandClient, ZmqCommandServer


class _RecordingBus(InMemoryForwardingBus):
    def __init__(self) -> None:
        super().__init__()
        self.command_calls = 0

    def send_command_sync(self, command: OrderCommand, *, timeout: float | None = None):
        self.command_calls += 1
        raise AssertionError("retired forwarding command reached the bus")


def _command(command_type: str = "place_order") -> OrderCommand:
    return OrderCommand(
        strategy_id="strategy-1",
        account_id="account-1",
        exchange="SIM",
        market_type="SPOT",
        symbol="BTC-USDT",
        side="buy",
        size=1,
        order_type="limit",
        price=100,
        command_type=command_type,
        idempotency_key="legacy-writer-test",
    )


def test_forwarding_client_order_entrypoints_fail_before_dispatch() -> None:
    bus = _RecordingBus()
    client = ForwardingClient(bus=bus, account_id="account-1", strategy_id="strategy-1")
    payload = {
        "symbol": "BTC-USDT",
        "side": "buy",
        "size": 1,
        "order_type": "limit",
        "price": 100,
    }

    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        client.submit_order(payload)
    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        client.cancel_order(order_ref="order-1")
    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        client.cancel_all("BTC-USDT")
    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        client._send_command_sync(_command())

    assert bus.command_calls == 0
    assert client.pending_commands() == []


def test_forwarding_client_query_commands_fail_before_dispatch() -> None:
    bus = _RecordingBus()
    client = ForwardingClient(bus=bus, account_id="account-1", strategy_id="strategy-1")
    queries = (
        (client.get_balance, "service.account_read"),
        (client.get_positions, "service.account_read"),
        (client.fetch_open_orders, "service.account_read"),
        (client.get_deals, "service.account_read"),
        (lambda: client.query_order("order-1", "BTC-USDT"), "ServiceClientRuntime"),
        (lambda: client.get_command_status("command-1"), "ServiceClientRuntime"),
    )

    for query, migration in queries:
        with pytest.raises(LegacyForwardingRetiredError, match=migration):
            query()

    assert bus.command_calls == 0
    assert client.pending_commands() == []


def test_connected_client_private_reads_fail_without_polling_or_caching() -> None:
    bus = _RecordingBus()
    client = ForwardingClient(bus=bus, account_id="account-1", strategy_id="strategy-1")
    client.connect()

    # connect() remains useful for market reads, but it must not attach the
    # retired private-event channel.
    assert bus.stats()["private_subscription_count"] == 0
    assert client.stats(refresh=False)["private_subscription_count"] == 0

    # Seed a pre-existing legacy subscription to prove that every public
    # account/private read fails before polling even if old state is attached.
    subscription = bus.subscribe_private("strategy.strategy-1.account")
    client._private_subscriptions.append(subscription)
    poll_calls = 0
    original_poll = subscription.poll

    def record_poll():
        nonlocal poll_calls
        poll_calls += 1
        return original_poll()

    subscription.poll = record_poll
    event = bus.publish_private(
        PrivateEvent(
            event_type="account",
            account_id="account-1",
            strategy_id="strategy-1",
            payload={"kind": "account", "cash": 50.0, "equity": 75.0},
        )
    )
    assert event.topic == "strategy.strategy-1.account"
    assert list(subscription.queue) == [event]

    private_reads = (
        client.get_balance,
        client.get_positions,
        client.fetch_open_orders,
        client.get_deals,
        client.latest_account_event,
        client.latest_position_events,
        client.latest_order_events,
        client.latest_fill_events,
        client.poll_broker_update,
    )
    for read in private_reads:
        with pytest.raises(LegacyForwardingRetiredError, match="service.account_read"):
            read()

    # Market-only statistics may refresh market queues but never consume the
    # queued private event or populate the retired snapshot caches.
    client.stats()
    assert poll_calls == 0
    assert list(subscription.queue) == [event]
    assert client._account_cache is None
    assert client._positions_cache is None
    assert client._orders_cache is None
    assert client._deals_cache is None
    assert client._latest_account_event is None
    assert client._latest_position_events == {}
    assert client._latest_order_events == {}
    assert client._latest_fill_events == {}
    assert list(client._broker_updates) == []
    assert bus.command_calls == 0
    assert subscription.poll() is event
    assert poll_calls == 1
    subscription.close()


def test_market_only_client_keeps_initialization_and_tick_normalization_helpers() -> None:
    bus = InMemoryForwardingBus()
    client = ForwardingClient(
        bus=bus,
        exchange="SIM",
        market_type="SPOT",
        replay=1,
        command_timeout=0.25,
        event_cache_size=2,
    )
    client.subscribe("BTC/USDT")
    event = bus.publish_market(
        MarketEvent(
            event_type="tick",
            exchange="SIM",
            market_type="SPOT",
            symbol="BTC/USDT",
            payload={"price": 101.25, "timestamp": 1_700_000_000},
            event_time=1_700_000_000_000,
            receive_time=1_700_000_000_000,
        )
    )

    observed = client.wait_for_next_market_event(
        "BTC-USDT", "tick", after_sequence_id=0, timeout_ms=0
    )
    tick = client.poll_tick("BTC-USDT")

    assert observed is event
    assert tick.price == 101.25
    assert tick.timestamp == 1_700_000_000_000
    assert client.stats(refresh=False)["event_cache_size"] == 2
    assert bus.stats()["private_subscription_count"] == 0


def test_order_router_constructor_and_direct_command_handlers_fail_closed() -> None:
    adapter = MockBrokerAdapter()
    bus = InMemoryForwardingBus()
    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        OrderRouter(
            adapter,
            bus=bus,
            write_enabled=True,
            write_scope=("SIM", "SPOT", "account-1"),
        )
    assert adapter.connected is False
    assert bus.stats()["command_handler_registered"] is False

    router = object.__new__(OrderRouter)
    command = _command()
    for lifecycle_entrypoint in (router.connect, router.disconnect):
        with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
            asyncio.run(lifecycle_entrypoint())
    for entrypoint in (
        router.handle_command,
        router.place_order,
        router.cancel_order,
        router.cancel_all,
        router.query_order,
    ):
        with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
            asyncio.run(entrypoint(command))


def test_zmq_command_transport_fails_before_context_or_thread_creation(monkeypatch) -> None:
    context_calls: list[str] = []

    class ForbiddenContext:
        @staticmethod
        def instance():
            context_calls.append("instance")
            raise AssertionError("retired transport must not create a ZMQ context")

    monkeypatch.setattr(transport_module.zmq, "Context", ForbiddenContext)
    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        ZmqCommandServer("inproc://retired", lambda _command: None)
    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        ZmqCommandClient("inproc://retired")

    server = object.__new__(ZmqCommandServer)
    client = object.__new__(ZmqCommandClient)
    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        server.start()
    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        server._run()
    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        client.send(_command())

    assert context_calls == []
