import asyncio
import queue
from typing import Optional

import pytest

import bt_api_py.forwarding.router as router_module
from bt_api_py.brokers.errors import BrokerError, BrokerErrorCode
from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.brokers.types import OrderRequest
from bt_api_py.forwarding import (
    BtApiForwardingAdapter,
    CommandAck,
    ForwardingClient,
    ForwardingError,
    ForwardingRuntime,
    InMemoryForwardingBus,
    MarketDataHub,
    OrderCommand,
    OrderRouter,
    PrivateEvent,
    RiskRuleSet,
    SQLiteStateStore,
)


class FakeBtApi:
    def __init__(self) -> None:
        self.queue = queue.Queue()
        self.subscriptions = []

    def add_exchange(self, *args, **kwargs):
        return {"args": args, "kwargs": kwargs}

    def subscribe(self, dataname, topics):
        self.subscriptions.append((dataname, topics))

    def get_data_queue(self, exchange_name):
        return self.queue


def test_market_data_hub_fans_out_to_multiple_consumers_and_replays() -> None:
    bus = InMemoryForwardingBus(replay_size=4)
    hub = MarketDataHub(bus)
    first = bus.subscribe_market("md.SIM.SPOT.RB2510.")
    second = bus.subscribe_market("md.SIM.SPOT.RB2510.")

    event = hub.publish_tick(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        price=3500.0,
        volume=2.0,
    )
    replay = bus.subscribe_market("md.SIM.SPOT.RB2510.", replay=1)

    assert event.sequence_id == 1
    assert first.poll().payload["price"] == 3500.0
    assert second.poll().payload["volume"] == 2.0
    assert replay.poll().sequence_id == 1


def test_market_data_hub_and_bus_stats_report_runtime_state() -> None:
    bus = InMemoryForwardingBus(replay_size=4)
    hub = MarketDataHub(bus)
    market_subscription = bus.subscribe_market("md.SIM.SPOT.RB2510.")
    private_subscription = bus.subscribe_private("strategy.s1.")

    hub.subscribe("SIM", "SPOT", "RB2510")
    hub.subscribe("SIM", "SPOT", "RB2510")
    hub.publish_tick(exchange="SIM", market_type="SPOT", symbol="RB2510", price=3500.0)

    stats = hub.stats()

    assert stats["active_subscription_count"] == 1
    assert stats["subscription_refcounts"]["SIM.SPOT.RB2510.tick"] == 2
    assert stats["bus"]["replay_size"] == 4
    assert stats["bus"]["market_subscription_count"] == 1
    assert stats["bus"]["private_subscription_count"] == 1
    assert stats["bus"]["market_replay_topic_count"] == 1
    assert stats["bus"]["market_sequence_topic_count"] == 1
    assert stats["bus"]["command_handler_registered"] is False
    assert market_subscription.poll().payload["price"] == 3500.0

    market_subscription.close()
    private_subscription.close()


def test_market_data_hub_normalizes_subscription_symbol_like_topics() -> None:
    hub = MarketDataHub(InMemoryForwardingBus())

    hub.subscribe("sim", "spot", "BTC/USDT", "tick")
    hub.unsubscribe("SIM", "SPOT", "BTC-USDT", "tick")

    assert hub.stats()["subscription_refcounts"] == {}


def test_market_data_hub_explicit_market_fields_override_payload() -> None:
    hub = MarketDataHub(InMemoryForwardingBus())

    tick = hub.publish_tick(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        price=3500.0,
        volume=2.0,
        direction="sell",
        payload={"price": 1.0, "volume": 0.0, "direction": "buy", "raw": "kept"},
    )
    orderbook = hub.publish_orderbook(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        bids=[(3499.0, 3.0)],
        asks=[(3501.0, 4.0)],
        payload={"bids": [], "asks": [], "raw": "kept"},
    )

    assert tick.payload == {
        "price": 3500.0,
        "volume": 2.0,
        "direction": "sell",
        "raw": "kept",
    }
    assert orderbook.payload == {
        "bids": [(3499.0, 3.0)],
        "asks": [(3501.0, 4.0)],
        "raw": "kept",
    }


def test_market_data_hub_publishes_bars_for_forwarding_client() -> None:
    bus = InMemoryForwardingBus()
    hub = MarketDataHub(bus)
    client = ForwardingClient(bus=bus, exchange="SIM", market_type="SPOT")
    client.connect()
    client.subscribe("RB2510")

    event = hub.publish_bar(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        open_price=3490.0,
        high=3510.0,
        low=3480.0,
        close=3500.0,
        volume=12.0,
        openinterest=3.0,
        payload={"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "raw": "kept"},
    )
    bar = client.poll_bar("RB2510")

    assert event.event_type == "bar"
    assert event.payload["raw"] == "kept"
    assert bar == {
        "datetime": event.event_time / 1000.0,
        "open": 3490.0,
        "high": 3510.0,
        "low": 3480.0,
        "close": 3500.0,
        "volume": 12.0,
        "openinterest": 3.0,
    }


def test_in_memory_bus_rejects_negative_replay_size() -> None:
    with pytest.raises(ValueError, match="replay_size must be non-negative"):
        InMemoryForwardingBus(replay_size=-1)


def test_in_memory_bus_rejects_negative_subscription_replay() -> None:
    bus = InMemoryForwardingBus()

    with pytest.raises(ValueError, match="replay must be non-negative"):
        bus.subscribe_market("md.SIM.", replay=-1)

    with pytest.raises(ValueError, match="replay must be non-negative"):
        bus.subscribe_private("strategy.s1.", replay=-1)


def test_btapi_forwarding_adapter_bridges_existing_data_queue() -> None:
    bus = InMemoryForwardingBus()
    hub = MarketDataHub(bus)
    api = FakeBtApi()
    adapter = BtApiForwardingAdapter(api, hub, default_market_type="SPOT")
    subscription = bus.subscribe_market("md.SIM.SPOT.RB2510.")
    api.queue.put(
        {
            "event": "TickerEvent",
            "exchange": "SIM",
            "market_type": "SPOT",
            "symbol": "RB2510",
            "price": 3500.0,
        }
    )

    adapter.subscribe("SIM___SPOT___RB2510", ["ticker"])
    forwarded = adapter.forward_once("SIM")
    event = subscription.poll()

    assert api.subscriptions == [("SIM___SPOT___RB2510", ["ticker"])]
    assert forwarded == 1
    assert event.event_type == "tick"
    assert event.payload["price"] == 3500.0


def test_btapi_forwarding_adapter_max_items_boundaries() -> None:
    bus = InMemoryForwardingBus()
    hub = MarketDataHub(bus)
    api = FakeBtApi()
    adapter = BtApiForwardingAdapter(api, hub, default_market_type="SPOT")
    api.queue.put(
        {
            "event": "TickerEvent",
            "exchange": "SIM",
            "market_type": "SPOT",
            "symbol": "RB2510",
            "price": 3500.0,
        }
    )

    assert adapter.forward_once("SIM", max_items=0) == 0
    with pytest.raises(ValueError, match="max_items must be non-negative"):
        adapter.forward_once("SIM", max_items=-1)


@pytest.mark.asyncio
async def test_in_memory_bus_awaits_future_command_handler() -> None:
    bus = InMemoryForwardingBus()
    command = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        idempotency_key="future-handler",
    )

    def handler(item: OrderCommand) -> asyncio.Future[CommandAck]:
        future: asyncio.Future[CommandAck] = asyncio.get_running_loop().create_future()
        future.set_result(
            CommandAck(
                command_id=item.command_id,
                idempotency_key=str(item.idempotency_key),
                accepted=True,
                status="accepted",
                account_id=item.account_id,
                strategy_id=item.strategy_id,
                order_id="future-order",
            )
        )
        return future

    bus.set_command_handler(handler)

    ack = await bus.send_command(command)

    assert ack.accepted is True
    assert ack.order_id == "future-order"


@pytest.mark.asyncio
async def test_in_memory_bus_sync_command_times_out_inside_running_loop() -> None:
    bus = InMemoryForwardingBus()
    command = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        idempotency_key="sync-timeout",
    )

    async def handler(item: OrderCommand) -> CommandAck:
        await asyncio.Event().wait()
        return CommandAck(
            command_id=item.command_id,
            idempotency_key=str(item.idempotency_key),
            accepted=True,
            status="accepted",
        )

    bus.set_command_handler(handler)

    with pytest.raises(TimeoutError, match="timed out after 0.01s"):
        bus.send_command_sync(command, timeout=0.01)


def test_in_memory_bus_rejects_negative_sync_command_timeout() -> None:
    bus = InMemoryForwardingBus()
    command = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
    )

    with pytest.raises(ValueError, match="timeout must be a non-negative finite number"):
        bus.send_command_sync(command, timeout=-0.01)


@pytest.mark.asyncio
async def test_order_router_enforces_idempotency_and_publishes_private_events() -> None:
    bus = InMemoryForwardingBus()
    router = OrderRouter(MockBrokerAdapter(), bus=bus)
    await router.connect()
    strategy_events = bus.subscribe_private("strategy.s1.")
    command = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        order_type="limit",
        price=3500.0,
        idempotency_key="s1-order-1",
    )

    first = await router.handle_command(command)
    second = await router.handle_command(command)

    assert first.accepted is True
    assert second.order_id == first.order_id
    assert len(router.adapter.orders) == 1
    updates = []
    while True:
        event = strategy_events.poll()
        if event is None:
            break
        updates.append(event.payload["kind"])
    assert "order" in updates
    assert "trade" in updates
    assert "account" in updates
    assert "position" in updates


@pytest.mark.asyncio
async def test_order_router_logs_account_state_refresh_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AccountStateFailingAdapter(MockBrokerAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.get_account_calls = 0

        async def get_account(self, account_id: str):
            self.get_account_calls += 1
            if self.get_account_calls == 1:
                return await super().get_account(account_id)
            raise RuntimeError(f"account unavailable: {account_id}")

    warnings: list[str] = []
    monkeypatch.setattr(router_module.logger, "warning", lambda message: warnings.append(message))
    router = OrderRouter(AccountStateFailingAdapter(), bus=InMemoryForwardingBus())
    await router.connect()

    ack = await router.handle_command(
        OrderCommand(
            strategy_id="s1",
            account_id="paper",
            symbol="RB2510",
            side="buy",
            size=1,
            order_type="limit",
            price=3500.0,
            idempotency_key="state-refresh-fails",
        )
    )

    assert ack.accepted is True
    assert warnings == [
        "Failed to publish account state after order command: "
        "account_id=paper, strategy_id=s1, "
        "error_type=RuntimeError, error=account unavailable: paper"
    ]


@pytest.mark.asyncio
async def test_order_router_rejects_disallowed_symbol_before_adapter_call() -> None:
    bus = InMemoryForwardingBus()
    adapter = MockBrokerAdapter()
    router = OrderRouter(
        adapter,
        bus=bus,
        risk_rules=RiskRuleSet(allowed_symbols={"IF2510"}, max_order_size=10),
    )
    await router.connect()

    ack = await router.handle_command(
        OrderCommand(
            strategy_id="s1",
            account_id="paper",
            symbol="RB2510",
            side="buy",
            size=1,
            idempotency_key="blocked",
        )
    )

    assert ack.accepted is False
    assert "symbol is not allowed" in ack.reason
    assert adapter.orders == {}


@pytest.mark.asyncio
async def test_order_router_rejects_invalid_side_instead_of_market_buy() -> None:
    adapter = MockBrokerAdapter()
    placed: list[OrderRequest] = []
    original = adapter.place_order

    async def spy(request: OrderRequest):
        placed.append(request)
        return await original(request)

    adapter.place_order = spy  # type: ignore[method-assign]
    router = OrderRouter(adapter)
    cmd = OrderCommand(
        strategy_id="s",
        account_id="a",
        exchange="E",
        market_type="SPOT",
        symbol="BTCUSDT",
        side="sdie",
        size=1.0,
        order_type="limit",
        price=10.0,
        client_order_id="c1",
        idempotency_key="k-invalid-side",
    )
    ack = await router.handle_command(cmd)
    assert ack.accepted is False
    assert "invalid side" in str(ack.reason).lower()
    assert placed == []  # never reaches the adapter


@pytest.mark.asyncio
async def test_order_router_rejects_invalid_order_type_instead_of_market() -> None:
    adapter = MockBrokerAdapter()
    placed: list[OrderRequest] = []
    original = adapter.place_order

    async def spy(request: OrderRequest):
        placed.append(request)
        return await original(request)

    adapter.place_order = spy  # type: ignore[method-assign]
    router = OrderRouter(adapter)
    cmd = OrderCommand(
        strategy_id="s",
        account_id="a",
        exchange="E",
        market_type="SPOT",
        symbol="BTCUSDT",
        side="buy",
        size=1.0,
        order_type="LIMT",
        price=10.0,
        client_order_id="c1",
        idempotency_key="k-invalid-order-type",
    )
    ack = await router.handle_command(cmd)
    assert ack.accepted is False
    assert "invalid order_type" in str(ack.reason).lower()
    assert placed == []  # never reaches the adapter


@pytest.mark.asyncio
async def test_order_router_health_reports_adapter_risk_and_state_store(tmp_path) -> None:
    state_store = SQLiteStateStore(tmp_path / "forwarding.sqlite3")
    bus = InMemoryForwardingBus()
    router = OrderRouter(
        MockBrokerAdapter(),
        bus=bus,
        risk_rules=RiskRuleSet(
            allowed_accounts={"paper"},
            allowed_symbols={"RB2510"},
            max_order_size=2,
        ),
        state_store=state_store,
    )
    await router.connect()

    try:
        command = OrderCommand(
            strategy_id="s1",
            account_id="paper",
            symbol="RB2510",
            side="buy",
            size=1,
            order_type="limit",
            price=3500.0,
            idempotency_key="health-order",
        )
        ack = await router.handle_command(command)
        health = await router.health()

        assert ack.accepted is True
        assert health["adapter"]["connected"] is True
        assert health["adapter"]["adapter"] == "mock"
        assert health["cached_ack_count"] == 1
        assert health["state_store_enabled"] is True
        assert health["bus_attached"] is True
        assert health["risk"]["allowed_account_count"] == 1
        assert health["risk"]["allowed_symbol_count"] == 1
        assert health["risk"]["max_order_size"] == 2
        assert health["risk"]["kill_switch"] is False
        assert bus.stats()["command_handler_registered"] is True
    finally:
        await router.disconnect()
        state_store.close()


@pytest.mark.asyncio
async def test_order_router_recovers_idempotent_ack_from_sqlite_state(tmp_path) -> None:
    db_path = tmp_path / "forwarding.sqlite3"
    state_store = SQLiteStateStore(db_path)
    first_adapter = MockBrokerAdapter()
    first_router = OrderRouter(first_adapter, state_store=state_store)
    command = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        order_type="limit",
        price=3500.0,
        idempotency_key="persisted-order",
    )

    first_ack = await first_router.handle_command(command)
    state_store.close()

    second_state_store = SQLiteStateStore(db_path)
    second_adapter = MockBrokerAdapter()
    second_router = OrderRouter(second_adapter, state_store=second_state_store)
    second_ack = await second_router.handle_command(command)
    events = second_state_store.list_private_events("strategy.s1.")
    second_state_store.close()

    assert first_ack.accepted is True
    assert second_ack.order_id == first_ack.order_id
    assert second_adapter.orders == {}
    assert [event.payload["kind"] for event in events] == ["order", "trade", "account", "position"]


def test_sqlite_state_store_treats_topic_prefix_as_literal(tmp_path) -> None:
    state_store = SQLiteStateStore(tmp_path / "forwarding.sqlite3")
    try:
        exact_event = PrivateEvent(
            event_type="orders",
            account_id="paper",
            strategy_id="s_1",
            payload={"kind": "exact"},
        )
        wildcard_match_event = PrivateEvent(
            event_type="orders",
            account_id="paper",
            strategy_id="sa1",
            payload={"kind": "wildcard-match"},
        )

        state_store.save_private_event(exact_event)
        state_store.save_private_event(wildcard_match_event)

        events = state_store.list_private_events("strategy.s_1.")
    finally:
        state_store.close()

    assert [event.payload["kind"] for event in events] == ["exact"]


def test_sqlite_state_store_creates_parent_directory(tmp_path) -> None:
    db_path = tmp_path / "nested" / "state" / "forwarding.sqlite3"
    state_store = SQLiteStateStore(db_path)
    try:
        event = PrivateEvent(
            event_type="orders",
            account_id="paper",
            strategy_id="s1",
            payload={"kind": "order"},
        )
        state_store.save_private_event(event)

        events = state_store.list_private_events("strategy.s1.")
    finally:
        state_store.close()

    assert db_path.parent.is_dir()
    assert [event.payload["kind"] for event in events] == ["order"]


def test_sqlite_state_store_context_manager_closes_connection(tmp_path) -> None:
    db_path = tmp_path / "forwarding.sqlite3"
    event = PrivateEvent(
        event_type="orders",
        account_id="paper",
        strategy_id="s1",
        payload={"kind": "order"},
    )

    with SQLiteStateStore(db_path) as state_store:
        state_store.save_private_event(event)
        assert [
            item.payload["kind"] for item in state_store.list_private_events("strategy.s1.")
        ] == ["order"]

    with pytest.raises(ForwardingError, match="SQLiteStateStore is closed"):
        state_store.list_private_events("strategy.s1.")


def test_sqlite_state_store_close_is_idempotent(tmp_path) -> None:
    state_store = SQLiteStateStore(tmp_path / "forwarding.sqlite3")

    state_store.close()
    state_store.close()

    with pytest.raises(ForwardingError, match="SQLiteStateStore is closed"):
        state_store.list_private_events()


def test_sqlite_state_store_private_event_limit_boundaries(tmp_path) -> None:
    with SQLiteStateStore(tmp_path / "forwarding.sqlite3") as state_store:
        state_store.save_private_event(
            PrivateEvent(
                event_type="orders",
                account_id="paper",
                strategy_id="s1",
                payload={"kind": "order"},
            )
        )

        assert state_store.list_private_events("strategy.s1.", limit=0) == []
        with pytest.raises(ValueError, match="limit must be non-negative"):
            state_store.list_private_events("strategy.s1.", limit=-1)


@pytest.mark.asyncio
async def test_forwarding_runtime_health_includes_market_and_router_state() -> None:
    runtime = ForwardingRuntime(MockBrokerAdapter())
    await runtime.start()

    try:
        runtime.market_data.subscribe("SIM", "SPOT", "RB2510")
        health = await runtime.health()

        assert health["runtime"] == "ForwardingRuntime"
        assert health["market_data"]["active_subscription_count"] == 1
        assert health["market_data"]["bus"]["command_handler_registered"] is True
        assert health["order_router"]["adapter"]["connected"] is True
        assert health["order_router"]["bus_attached"] is True
    finally:
        await runtime.stop()


def test_forwarding_client_exposes_backtrader_style_market_and_order_api() -> None:
    bus = InMemoryForwardingBus()
    hub = MarketDataHub(bus)
    router = OrderRouter(MockBrokerAdapter(), bus=bus)
    client = ForwardingClient(
        bus=bus,
        exchange="SIM",
        market_type="SPOT",
        account_id="paper",
        strategy_id="s1",
    )
    client.connect()
    client.subscribe("RB2510")

    hub.publish_tick(exchange="SIM", market_type="SPOT", symbol="RB2510", price=3500.0)
    hub.publish_orderbook(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        bids=[(3499.0, 3.0)],
        asks=[(3501.0, 4.0)],
    )

    tick = client.poll_tick("RB2510")
    orderbook = client.poll_orderbook("RB2510")
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
    broker_updates = []
    while True:
        update = client.poll_broker_update()
        if update is None:
            break
        broker_updates.append(update["kind"])

    assert router is not None
    assert tick.price == 3500.0
    assert orderbook.bids[0][0] == 3499.0
    assert response["order_id"]
    assert "order" in broker_updates
    assert "trade" in broker_updates
    assert client.get_balance()["account_id"] == "paper"
    assert client.get_positions()[0]["symbol"] == "RB2510"


def test_forwarding_client_requires_explicit_side_and_order_type() -> None:
    bus = InMemoryForwardingBus()
    hub = MarketDataHub(bus)
    router = OrderRouter(MockBrokerAdapter(), bus=bus)
    client = ForwardingClient(
        bus=bus,
        exchange="SIM",
        market_type="SPOT",
        account_id="paper",
        strategy_id="s1",
    )
    client.connect()

    with pytest.raises(ValueError, match="side is required"):
        client.submit_order({"symbol": "RB2510", "size": 1.0, "order_type": "market"})
    with pytest.raises(ValueError, match="order_type is required"):
        client.submit_order({"symbol": "RB2510", "side": "buy", "size": 1.0})


def test_forwarding_client_normalizes_market_symbol_keys_like_topics() -> None:
    bus = InMemoryForwardingBus()
    hub = MarketDataHub(bus)
    client = ForwardingClient(
        bus=bus,
        exchange="SIM",
        market_type="SPOT",
        account_id="paper",
        strategy_id="s1",
    )
    client.connect()

    client.subscribe("BTC/USDT")
    client.subscribe("BTC-USDT")
    hub.publish_tick(exchange="SIM", market_type="SPOT", symbol="BTC/USDT", price=65000.0)

    assert client.supports_live_ticks("BTC-USDT") is True
    assert client.supports_live_orderbook("BTC/USDT") is True
    assert bus.stats()["market_subscription_count"] == 1
    assert client.has_pending_tick("BTC-USDT") is True
    assert client.poll_tick("BTC-USDT").price == 65000.0


def test_forwarding_client_disconnect_clears_event_caches_but_keeps_query_snapshots() -> None:
    bus = InMemoryForwardingBus()
    hub = MarketDataHub(bus)
    client = ForwardingClient(bus=bus, exchange="SIM", market_type="SPOT")
    client.connect()
    client.subscribe("RB2510")
    client._account_cache = {"cash": 100.0, "value": 120.0}
    client._broker_updates.append({"kind": "order", "order_id": "stale"})

    hub.publish_tick(exchange="SIM", market_type="SPOT", symbol="RB2510", price=3500.0)
    hub.publish_orderbook(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        bids=[(3499.0, 3.0)],
        asks=[(3501.0, 4.0)],
    )
    hub.publish_bar(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        open_price=3490.0,
        high=3510.0,
        low=3480.0,
        close=3500.0,
    )

    assert client.has_pending_tick("RB2510") is True
    assert client.has_pending_orderbook("RB2510") is True
    assert client.poll_bar("RB2510") is not None

    client.disconnect()

    assert client.poll_tick("RB2510") is None
    assert client.poll_orderbook("RB2510") is None
    assert client.poll_bar("RB2510") is None
    assert client.poll_broker_update() is None
    assert client.get_balance() == {"cash": 100.0, "value": 120.0}


def test_forwarding_client_bounds_realtime_event_caches_for_slow_consumers() -> None:
    bus = InMemoryForwardingBus()
    hub = MarketDataHub(bus)
    client = ForwardingClient(
        bus=bus,
        exchange="SIM",
        market_type="SPOT",
        account_id="paper",
        strategy_id="s1",
        event_cache_size=1,
    )
    client.connect()
    client.subscribe("RB2510")

    hub.publish_tick(exchange="SIM", market_type="SPOT", symbol="RB2510", price=3499.0)
    hub.publish_tick(exchange="SIM", market_type="SPOT", symbol="RB2510", price=3500.0)

    assert client.poll_tick("RB2510").price == 3500.0
    assert client.poll_tick("RB2510") is None

    hub.publish_orderbook(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        bids=[(3499.0, 1.0)],
        asks=[(3501.0, 1.0)],
    )
    hub.publish_orderbook(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        bids=[(3500.0, 2.0)],
        asks=[(3502.0, 2.0)],
    )

    assert client.poll_orderbook("RB2510").bids == [(3500.0, 2.0)]
    assert client.poll_orderbook("RB2510") is None

    hub.publish_bar(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        open_price=3490.0,
        high=3500.0,
        low=3480.0,
        close=3495.0,
    )
    hub.publish_bar(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        open_price=3495.0,
        high=3510.0,
        low=3490.0,
        close=3505.0,
    )

    assert client.poll_bar("RB2510")["close"] == 3505.0
    assert client.poll_bar("RB2510") is None

    bus.publish_private(
        PrivateEvent(
            event_type="orders",
            account_id="paper",
            strategy_id="s1",
            payload={"kind": "order", "order_id": "old"},
        )
    )
    bus.publish_private(
        PrivateEvent(
            event_type="orders",
            account_id="paper",
            strategy_id="s1",
            payload={"kind": "order", "order_id": "new"},
        )
    )

    assert client.poll_broker_update()["order_id"] == "new"
    assert client.poll_broker_update() is None


def test_forwarding_client_stats_reports_event_cache_pressure() -> None:
    bus = InMemoryForwardingBus()
    hub = MarketDataHub(bus)
    client = ForwardingClient(
        bus=bus,
        exchange="SIM",
        market_type="SPOT",
        account_id="paper",
        strategy_id="s1",
        event_cache_size=1,
    )
    client.connect()
    client.subscribe("RB2510")

    hub.publish_tick(exchange="SIM", market_type="SPOT", symbol="RB2510", price=3499.0)
    hub.publish_tick(exchange="SIM", market_type="SPOT", symbol="RB2510", price=3500.0)
    hub.publish_orderbook(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        bids=[(3499.0, 1.0)],
        asks=[(3501.0, 1.0)],
    )
    hub.publish_orderbook(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        bids=[(3500.0, 2.0)],
        asks=[(3502.0, 2.0)],
    )
    hub.publish_bar(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        open_price=3490.0,
        high=3500.0,
        low=3480.0,
        close=3495.0,
    )
    hub.publish_bar(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        open_price=3495.0,
        high=3510.0,
        low=3490.0,
        close=3505.0,
    )
    bus.publish_private(
        PrivateEvent(
            event_type="orders",
            account_id="paper",
            strategy_id="s1",
            payload={"kind": "order", "order_id": "old"},
        )
    )
    bus.publish_private(
        PrivateEvent(
            event_type="orders",
            account_id="paper",
            strategy_id="s1",
            payload={"kind": "order", "order_id": "new"},
        )
    )

    assert client.stats(refresh=False) == {
        "connected": True,
        "event_cache_size": 1,
        "market_subscription_count": 1,
        "private_subscription_count": 2,
        "pending_event_counts": {
            "tick": 0,
            "orderbook": 0,
            "bar": 0,
            "broker_update": 0,
        },
        "dropped_event_counts": {
            "tick": 0,
            "orderbook": 0,
            "bar": 0,
            "broker_update": 0,
        },
    }
    assert client.stats() == {
        "connected": True,
        "event_cache_size": 1,
        "market_subscription_count": 1,
        "private_subscription_count": 2,
        "pending_event_counts": {
            "tick": 1,
            "orderbook": 1,
            "bar": 1,
            "broker_update": 1,
        },
        "dropped_event_counts": {
            "tick": 1,
            "orderbook": 1,
            "bar": 1,
            "broker_update": 1,
        },
    }


def test_forwarding_client_passes_configured_command_timeout() -> None:
    class RecordingBus(InMemoryForwardingBus):
        def __init__(self) -> None:
            super().__init__()
            self.recorded_timeout: Optional[float] = None

        def send_command_sync(
            self, command: OrderCommand, *, timeout: Optional[float] = None
        ) -> CommandAck:
            self.recorded_timeout = timeout
            return CommandAck(
                command_id=command.command_id,
                idempotency_key=str(command.idempotency_key),
                accepted=True,
                status="ok",
                account_id=command.account_id,
                strategy_id=command.strategy_id,
                payload={"account_id": command.account_id, "cash": 100.0},
            )

    bus = RecordingBus()
    client = ForwardingClient(bus=bus, command_timeout=0.25)

    balance = client.get_balance()

    assert balance["account_id"] == "paper"
    assert bus.recorded_timeout == 0.25


def test_forwarding_client_rejects_negative_command_timeout() -> None:
    with pytest.raises(
        ValueError,
        match="command_timeout must be a non-negative finite number",
    ):
        ForwardingClient(command_timeout=-0.01)


def test_forwarding_client_rejects_negative_replay() -> None:
    with pytest.raises(ValueError, match="replay must be non-negative"):
        ForwardingClient(replay=-1)


def test_forwarding_client_rejects_negative_event_cache_size() -> None:
    with pytest.raises(ValueError, match="event_cache_size must be non-negative"):
        ForwardingClient(event_cache_size=-1)


def test_forwarding_client_returns_cached_query_snapshots_when_command_times_out() -> None:
    class TimeoutBus(InMemoryForwardingBus):
        def send_command_sync(
            self, command: OrderCommand, *, timeout: Optional[float] = None
        ) -> CommandAck:
            raise TimeoutError("query timed out")

    client = ForwardingClient(bus=TimeoutBus())
    client._account_cache = {"cash": 100.0, "value": 120.0}
    client._positions_cache = [{"symbol": "RB2510", "size": 1}]
    client._orders_cache = [{"order_id": "order-1", "status": "submitted"}]

    assert client.get_balance() == {"cash": 100.0, "value": 120.0}
    assert client.get_positions() == [{"symbol": "RB2510", "size": 1}]
    assert client.fetch_open_orders() == [{"order_id": "order-1", "status": "submitted"}]


@pytest.mark.asyncio
async def test_router_does_not_cache_retryable_errors() -> None:
    calls = 0

    class FlakyAdapter(MockBrokerAdapter):
        async def place_order(self, request: OrderRequest):
            nonlocal calls
            calls += 1
            raise BrokerError(
                BrokerErrorCode.NETWORK_ERROR, "timeout", retryable=True,
            )

    router = OrderRouter(FlakyAdapter())
    cmd = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        order_type="limit",
        price=3500.0,
        idempotency_key="k-retry",
    )
    ack1 = await router.handle_command(cmd)
    ack2 = await router.handle_command(cmd)
    assert ack1.accepted is False and ack2.accepted is False
    assert calls == 2  # retry must reach the adapter again, not return the stale cached rejection


@pytest.mark.asyncio
async def test_router_caches_terminal_rejects() -> None:
    calls = 0

    class TerminalAdapter(MockBrokerAdapter):
        async def place_order(self, request: OrderRequest):
            nonlocal calls
            calls += 1
            raise BrokerError(
                BrokerErrorCode.INSUFFICIENT_FUNDS, "no money", retryable=False,
            )

    router = OrderRouter(TerminalAdapter())
    cmd = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        order_type="limit",
        price=3500.0,
        idempotency_key="k-terminal",
    )
    await router.handle_command(cmd)
    await router.handle_command(cmd)
    assert calls == 1  # terminal rejection hits the cache
