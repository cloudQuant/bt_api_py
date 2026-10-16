import asyncio
import queue
from typing import Any, cast

import pytest

import bt_api_py.forwarding.client as client_module
from bt_api_py._contracts.errors import LegacyForwardingRetiredError
from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.brokers.types import OrderRequest
from bt_api_py.forwarding import (
    CommandAck,
    ForwardingError,
    MarketEvent,
    OrderCommand,
    PrivateEvent,
)
from bt_api_py.forwarding.btapi_adapter import BtApiForwardingAdapter
from bt_api_py.forwarding.client import ForwardingClient
from bt_api_py.forwarding.hub import MarketDataHub
from bt_api_py.forwarding.memory import InMemoryForwardingBus
from bt_api_py.forwarding.service import ForwardingRuntime
from bt_api_py.forwarding.state import SQLiteStateStore


class FakeBtApi:
    def __init__(self) -> None:
        self.queue: queue.Queue[Any] = queue.Queue()
        self.subscriptions: list[tuple[str, Any]] = []

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
    assert stats["bus"]["market_subscription_count"] == 3
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
        "datetime": event.event_time,
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

    assert api.subscriptions == [("SIM___SPOT___RB2510", [{"topic": "ticker"}])]
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

    with pytest.raises(TimeoutError, match="result unknown"):
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


def test_forwarding_runtime_entrypoint_is_retired() -> None:
    with pytest.raises(LegacyForwardingRetiredError, match="ServiceExecutionSession"):
        ForwardingRuntime(MockBrokerAdapter())


def test_forwarding_client_keeps_market_reads_and_retires_order_writes() -> None:
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
    client.subscribe("RB2510")

    hub.publish_tick(exchange="SIM", market_type="SPOT", symbol="RB2510", price=3500.0)
    tick = client.poll_tick("RB2510")
    assert tick.price == 3500.0

    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        client.submit_order(
            {
                "bt_order_ref": 1,
                "symbol": "RB2510",
                "side": "buy",
                "size": 1,
                "order_type": "limit",
                "price": 3500.0,
            }
        )
    assert bus.stats()["command_handler_registered"] is False




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
    with pytest.raises(LegacyForwardingRetiredError, match="service.account_read"):
        client.poll_broker_update()
    assert client._account_cache == {"cash": 100.0, "value": 120.0}


def test_forwarding_client_bounds_market_event_caches_for_slow_consumers() -> None:
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

    assert client.stats(refresh=False)["private_subscription_count"] == 0
    assert bus.stats()["private_subscription_count"] == 0


def test_forwarding_client_stats_reports_market_event_cache_pressure() -> None:
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
    assert client.stats(refresh=False) == {
        "connected": True,
        "event_cache_size": 1,
        "market_subscription_count": 1,
        "private_subscription_count": 0,
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
        "private_subscription_count": 0,
        "pending_event_counts": {
            "tick": 1,
            "orderbook": 1,
            "bar": 1,
            "broker_update": 0,
        },
        "dropped_event_counts": {
            "tick": 1,
            "orderbook": 1,
            "bar": 1,
            "broker_update": 0,
        },
    }


def test_forwarding_client_retires_query_before_command_timeout_dispatch() -> None:
    class RecordingBus(InMemoryForwardingBus):
        def __init__(self) -> None:
            super().__init__()
            self.command_calls = 0

        def send_command_sync(
            self, command: OrderCommand, *, timeout: float | None = None
        ) -> CommandAck:
            self.command_calls += 1
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

    with pytest.raises(LegacyForwardingRetiredError, match="service.account_read"):
        client.get_balance()

    assert bus.command_calls == 0


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


def test_forwarding_client_does_not_use_cached_snapshots_for_retired_queries() -> None:
    class TimeoutBus(InMemoryForwardingBus):
        def __init__(self) -> None:
            super().__init__()
            self.command_calls = 0

        def send_command_sync(
            self, command: OrderCommand, *, timeout: float | None = None
        ) -> CommandAck:
            self.command_calls += 1
            raise TimeoutError("query timed out")

    client = ForwardingClient(bus=TimeoutBus())
    client._account_cache = {"cash": 100.0, "value": 120.0}
    client._positions_cache = [{"symbol": "RB2510", "size": 1}]
    client._orders_cache = [{"order_id": "order-1", "status": "submitted"}]

    for query in (client.get_balance, client.get_positions, client.fetch_open_orders):
        with pytest.raises(LegacyForwardingRetiredError, match="service.account_read"):
            query()
    assert client._account_cache == {"cash": 100.0, "value": 120.0}
    assert client._positions_cache == [{"symbol": "RB2510", "size": 1}]
    assert client._orders_cache == [{"order_id": "order-1", "status": "submitted"}]
    assert client.bus.command_calls == 0




def test_forwarding_client_cancel_entrypoints_fail_closed_before_dispatch() -> None:
    class RecordingBus(InMemoryForwardingBus):
        def __init__(self) -> None:
            super().__init__()
            self.command_count = 0

        def send_command_sync(self, command: OrderCommand, *, timeout: float | None = None):
            self.command_count += 1
            raise AssertionError("retired cancel must not dispatch")

    bus = RecordingBus()
    client = ForwardingClient(
        bus=bus,
        exchange="SIM",
        market_type="SPOT",
        account_id="paper",
        strategy_id="s1",
    )

    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        client.cancel_order(order_ref="ref-1")
    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        client.cancel_all("RB2510")

    assert bus.command_count == 0




# ── Task 1.9: A-14 / A-15 / A-19 / A-20 ────────────────────────────────


def test_sqlite_state_store_memory_uri_uses_in_memory_db(tmp_path, monkeypatch) -> None:
    """`memory:` 内存指示符不应在磁盘创建文件，而应得到真正的内存库。"""
    monkeypatch.chdir(tmp_path)
    store = SQLiteStateStore("memory:")
    ack = CommandAck(command_id="c1", idempotency_key="k1", accepted=True, status="ok")
    store.save_command_ack(ack)
    assert store.get_command_ack("k1") is not None
    store.close()
    assert list(tmp_path.iterdir()) == []




def test_fetch_open_orders_uses_the_service_account_read_route() -> None:
    """The retired forwarding command bus cannot serve account snapshots."""

    class StubBus(InMemoryForwardingBus):
        def __init__(self) -> None:
            super().__init__()
            self.command_calls = 0

        def send_command_sync(
            self, command: OrderCommand, *, timeout: float | None = None
        ) -> CommandAck:
            self.command_calls += 1
            return CommandAck(
                command_id=command.command_id,
                idempotency_key=str(command.idempotency_key),
                accepted=True,
                status="ok",
                payload={
                    "orders": [
                        {"order_id": "o1", "status": "new"},
                        {"order_id": "o2", "status": "submitted"},
                        {"order_id": "o3", "status": "filled"},
                    ]
                },
            )

    client = ForwardingClient(bus=StubBus())
    with pytest.raises(LegacyForwardingRetiredError, match="service.account_read"):
        client.fetch_open_orders()
    assert client.bus.command_calls == 0


def test_market_event_timestamp_normalized_to_ms() -> None:
    """tick/bar timestamp 应统一为毫秒 int，秒级输入换算为 *1000。"""
    # payload 提供秒级 timestamp → 转换为毫秒
    tick = client_module._market_event_to_tick(
        MarketEvent(
            event_type="tick",
            exchange="SIM",
            market_type="SPOT",
            symbol="RB2510",
            payload={"timestamp": 1_700_000_000},
            event_time=1_700_000_000_000,
            receive_time=1_700_000_000_000,
        )
    )
    assert tick.timestamp == 1_700_000_000_000
    assert isinstance(tick.timestamp, int)

    # 无 payload timestamp → 直接使用 event_time（毫秒）
    tick2 = client_module._market_event_to_tick(
        MarketEvent(
            event_type="tick",
            exchange="SIM",
            market_type="SPOT",
            symbol="RB2510",
            event_time=1_700_000_000_123,
            receive_time=1_700_000_000_123,
        )
    )
    assert tick2.timestamp == 1_700_000_000_123
    assert isinstance(tick2.timestamp, int)


# ── Task 1.10: A-04 幽灵订单 / pending 可查询 ───────────────────────────


def test_forwarding_client_does_not_track_or_dispatch_retired_order_writes() -> None:
    class RecordingBus(InMemoryForwardingBus):
        def __init__(self) -> None:
            super().__init__()
            self.command_count = 0

        def send_command_sync(self, command: OrderCommand, *, timeout: float | None = None):
            self.command_count += 1
            raise AssertionError("retired order must not dispatch")

    bus = RecordingBus()
    client = ForwardingClient(bus=bus, command_timeout=0.01)
    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        client.submit_order(
            {
                "bt_order_ref": 7,
                "symbol": "RB2510",
                "side": "buy",
                "size": 1,
                "order_type": "limit",
                "price": 3500.0,
            }
        )
    assert bus.command_count == 0
    assert client.pending_commands() == []
