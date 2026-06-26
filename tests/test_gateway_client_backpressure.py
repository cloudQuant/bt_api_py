from __future__ import annotations

import json

import pytest

from bt_api_py.gateway.client import GatewayClient


def test_tick_queue_is_bounded_and_drops_oldest() -> None:
    client = GatewayClient(gateway_max_ticks_per_symbol=3)

    for seq in range(5):
        client._store_tick({"symbol": "EURUSD", "seq": seq})

    queue = client._tick_queues["EURUSD"]
    assert queue.maxlen == 3
    assert [item["seq"] for item in queue] == [2, 3, 4]


def test_subscribe_tracks_symbols_after_successful_command() -> None:
    client = GatewayClient()
    calls = []

    def send_command(command, payload):
        calls.append((command, payload))
        return {"ok": True}

    client._send_command = send_command  # type: ignore[method-assign]

    assert client.subscribe(["EURUSD", "", "USDJPY"]) == {"ok": True}

    assert calls == [("subscribe", {"symbols": ["EURUSD", "USDJPY"]})]
    assert client.subscribed == {"EURUSD", "USDJPY"}


def test_cancel_order_payload_includes_canonical_symbol_aliases() -> None:
    client = GatewayClient()
    calls = []

    def send_command(command, payload):
        calls.append((command, payload))
        return {"ok": True}

    client._send_command = send_command  # type: ignore[method-assign]

    assert client.cancel_order("remote-1", dataname=" IF2609 ") == {"ok": True}

    assert calls == [
        (
            "cancel_order",
            {
                "order_ref": "remote-1",
                "dataname": "IF2609",
                "data_name": "IF2609",
                "symbol": "IF2609",
                "instrument": "IF2609",
            },
        )
    ]


def test_payload_with_strategy_preserves_explicit_strategy_id() -> None:
    client = GatewayClient(strategy_id="strategy-a")

    assert client._payload_with_strategy({"symbol": "EURUSD"}) == {
        "symbol": "EURUSD",
        "strategy_id": "strategy-a",
    }
    assert client._payload_with_strategy({"strategy_id": "strategy-b"}) == {
        "strategy_id": "strategy-b"
    }


def test_store_tick_filters_unsubscribed_symbols() -> None:
    client = GatewayClient(gateway_max_ticks_per_symbol=3)
    client.subscribed.add("EURUSD")

    client._store_tick({"symbol": "USDJPY", "seq": 1})
    client._store_tick({"symbol": "GBPUSD", "instrument_id": "EURUSD", "seq": 2})
    client._store_tick({"symbol": "EURUSD", "seq": 3})

    assert "USDJPY" not in client._tick_queues
    assert "GBPUSD" not in client._tick_queues
    assert [item["seq"] for item in client._tick_queues["EURUSD"]] == [2, 3]


def test_store_tick_without_subscriptions_keeps_legacy_all_symbol_behavior() -> None:
    client = GatewayClient(gateway_max_ticks_per_symbol=3)

    client._store_tick({"symbol": "GBPUSD", "instrument_id": "GBPUSD.m", "seq": 1})

    assert [item["seq"] for item in client._tick_queues["GBPUSD"]] == [1]
    assert [item["seq"] for item in client._tick_queues["GBPUSD.m"]] == [1]


def test_store_tick_accepts_gateway_symbol_aliases() -> None:
    client = GatewayClient(gateway_max_ticks_per_symbol=3)

    client._store_tick({"InstrumentID": "IF2609", "LastPrice": 5001.0})
    client._store_tick({"instId": "BTC-USDT-SWAP", "last": "60100"})
    client._store_tick({"data_name": "XAUUSD", "last_price": 2331.0})

    assert list(client._tick_queues) == ["IF2609", "BTC-USDT-SWAP", "XAUUSD"]
    assert client._tick_queues["IF2609"][0]["LastPrice"] == 5001.0
    assert client._tick_queues["BTC-USDT-SWAP"][0]["last"] == "60100"
    assert client._tick_queues["XAUUSD"][0]["last_price"] == 2331.0


def test_event_queue_is_bounded_and_drops_oldest() -> None:
    client = GatewayClient(gateway_max_events=2)

    for seq in range(4):
        client._event_queue.append({"seq": seq})

    assert client._event_queue.maxlen == 2
    assert [item["seq"] for item in client._event_queue] == [2, 3]


def test_store_event_filters_other_strategy_updates() -> None:
    client = GatewayClient(strategy_id="strategy-a")

    client._store_event({"kind": "trade", "strategy_id": "strategy-b", "order_ref": "1"})
    client._store_event({"kind": "trade", "strategy_id": "strategy-a", "order_ref": "2"})

    assert list(client._event_queue) == [
        {"kind": "trade", "strategy_id": "strategy-a", "order_ref": "2"}
    ]


def test_store_event_drops_unowned_order_identity_by_default() -> None:
    client = GatewayClient(strategy_id="strategy-a")

    client._store_event({"kind": "order", "order_ref": "unknown-owner"})
    client._store_event({"kind": "position", "position_id": "pos-1"})

    assert list(client._event_queue) == [{"kind": "position", "position_id": "pos-1"}]


def test_store_event_drops_unowned_private_identity_aliases() -> None:
    client = GatewayClient(strategy_id="strategy-a")

    client._store_event({"kind": "order", "orderId": "remote-1"})
    client._store_event({"kind": "trade", "details": {"OrderRef": "ctp-1"}})
    client._store_event({"kind": "trade", "tradeId": "fill-1"})
    client._store_event({"kind": "error", "details": {"requestId": "req-1"}})
    client._store_event({"kind": "order", "ordId": "okx-order-1"})
    client._store_event({"kind": "trade", "details": {"clOrdId": "okx-client-1"}})
    client._store_event({"kind": "error", "details": {"origClOrdId": "okx-client-2"}})
    client._store_event({"kind": "order", "newClientOrderId": "binance-client-1"})
    client._store_event({"kind": "trade", "details": {"origClientOrderId": "binance-client-2"}})
    client._store_event({"kind": "trade", "orderLinkId": "bybit-client-1"})
    client._store_event({"kind": "error", "details": {"origOrderLinkId": "bybit-client-2"}})
    client._store_event({"kind": "trade", "execID": "bybit-exec-1"})
    client._store_event({"kind": "position", "position_id": "pos-1"})

    assert list(client._event_queue) == [{"kind": "position", "position_id": "pos-1"}]


def test_store_event_accepts_owned_private_identity_without_strategy_id() -> None:
    client = GatewayClient(strategy_id="strategy-a")
    client._remember_owned_event_ids(
        {
            "client_order_id": "client-1",
            "data": {"orderId": "venue-1"},
        }
    )

    client._store_event({"kind": "trade", "orderId": "other-order", "lastQty": 1})
    client._store_event({"kind": "trade", "details": {"orderId": "venue-1"}, "lastQty": 2})
    client._store_event({"kind": "order", "clientOrderId": "client-1", "status": "filled"})

    assert list(client._event_queue) == [
        {"kind": "trade", "details": {"orderId": "venue-1"}, "lastQty": 2},
        {"kind": "order", "clientOrderId": "client-1", "status": "filled"},
    ]


def test_store_event_normalizes_private_event_type_aliases() -> None:
    client = GatewayClient(strategy_id="strategy-a")
    client._remember_owned_event_ids(
        {
            "client_order_id": "client-1",
            "data": {"orderId": "venue-1"},
        }
    )

    client._store_event({"type": "order_update", "clientOrderId": "client-1", "status": "filled"})
    client._store_event({"event_type": "fill_update", "orderId": "venue-1", "lastQty": 2})
    client._store_event({"event": "cancel_reject", "clientOrderId": "client-1", "message": "late"})

    assert list(client._event_queue) == [
        {
            "type": "order_update",
            "clientOrderId": "client-1",
            "status": "filled",
            "kind": "order",
        },
        {
            "event_type": "fill_update",
            "orderId": "venue-1",
            "lastQty": 2,
            "kind": "trade",
        },
        {
            "event": "cancel_reject",
            "clientOrderId": "client-1",
            "message": "late",
            "kind": "error",
        },
    ]


def test_store_event_drops_unowned_private_event_type_aliases() -> None:
    client = GatewayClient(strategy_id="strategy-a")

    client._store_event({"type": "order_update", "orderId": "remote-1"})
    client._store_event({"event_type": "fill_update", "tradeId": "fill-1"})
    client._store_event({"event": "cancel_reject", "clientOrderId": "client-2"})
    client._store_event({"type": "position_update", "id": "pos-1"})

    assert list(client._event_queue) == [{"type": "position_update", "id": "pos-1"}]


def test_store_event_normalizes_raw_binance_private_events() -> None:
    client = GatewayClient(strategy_id="strategy-a")
    client._remember_owned_event_ids(
        {
            "client_order_id": "client-1",
            "data": {"orderId": "venue-1"},
        }
    )

    client._store_event({"e": "executionReport", "c": "remote-client", "i": "remote-order", "x": "NEW"})
    client._store_event({"e": "executionReport", "c": "client-1", "i": "venue-1", "x": "NEW"})
    client._store_event({"e": "executionReport", "c": "client-1", "i": "venue-1", "x": "TRADE", "t": "99"})
    client._store_event(
        {
            "e": "ORDER_TRADE_UPDATE",
            "o": {"c": "client-1", "i": "venue-1", "x": "TRADE", "t": "100"},
        }
    )

    assert list(client._event_queue) == [
        {"e": "executionReport", "c": "client-1", "i": "venue-1", "x": "NEW", "kind": "order"},
        {
            "e": "executionReport",
            "c": "client-1",
            "i": "venue-1",
            "x": "TRADE",
            "t": "99",
            "kind": "trade",
        },
        {
            "e": "ORDER_TRADE_UPDATE",
            "o": {"c": "client-1", "i": "venue-1", "x": "TRADE", "t": "100"},
            "kind": "trade",
        },
    ]


def test_store_event_normalizes_raw_okx_private_channels() -> None:
    client = GatewayClient(strategy_id="strategy-a")
    client._remember_owned_event_ids(
        {
            "client_order_id": "client-1",
            "data": {"ordId": "venue-1"},
        }
    )

    client._store_event(
        {
            "arg": {"channel": "orders"},
            "data": [{"clOrdId": "remote-client", "ordId": "remote-order"}],
        }
    )
    client._store_event(
        {
            "arg": {"channel": "orders"},
            "data": [{"clOrdId": "client-1", "ordId": "venue-1"}],
        }
    )
    client._store_event(
        {
            "arg": {"channel": "orders"},
            "data": [{"clOrdId": "client-1", "ordId": "venue-1", "fillSz": "1", "fillPx": "50000"}],
        }
    )
    client._store_event(
        {
            "arg": {"channel": "fills"},
            "data": [{"clOrdId": "client-1", "tradeId": "fill-1"}],
        }
    )

    assert list(client._event_queue) == [
        {
            "arg": {"channel": "orders"},
            "data": [{"clOrdId": "client-1", "ordId": "venue-1"}],
            "kind": "order",
        },
        {
            "arg": {"channel": "orders"},
            "data": [{"clOrdId": "client-1", "ordId": "venue-1", "fillSz": "1", "fillPx": "50000"}],
            "kind": "trade",
        },
        {
            "arg": {"channel": "fills"},
            "data": [{"clOrdId": "client-1", "tradeId": "fill-1"}],
            "kind": "trade",
        },
    ]


def test_store_event_normalizes_raw_bybit_execution_topic() -> None:
    client = GatewayClient(strategy_id="strategy-a")
    client._remember_owned_event_ids(
        {
            "orderLinkId": "bybit-client-1",
            "orderId": "bybit-order-1",
        }
    )

    client._store_event(
        {
            "topic": "execution",
            "data": [
                {
                    "orderLinkId": "other-client",
                    "orderId": "other-order",
                    "execId": "other-exec",
                    "execQty": "1",
                    "execPrice": "50000",
                }
            ],
        }
    )
    client._store_event(
        {
            "topic": "execution",
            "data": [
                {
                    "orderLinkId": "bybit-client-1",
                    "orderId": "bybit-order-1",
                    "execId": "bybit-exec-1",
                    "execQty": "1",
                    "execPrice": "50000",
                    "execFee": "2.75",
                }
            ],
        }
    )
    client._store_event(
        {
            "topic": "execution.linear",
            "data": [
                {
                    "orderLinkId": "bybit-client-1",
                    "orderId": "bybit-order-1",
                    "execID": "bybit-exec-2",
                    "execQty": "1",
                    "execPrice": "50100",
                    "execFee": "2.80",
                }
            ],
        }
    )

    assert list(client._event_queue) == [
        {
            "topic": "execution",
            "data": [
                {
                    "orderLinkId": "bybit-client-1",
                    "orderId": "bybit-order-1",
                    "execId": "bybit-exec-1",
                    "execQty": "1",
                    "execPrice": "50000",
                    "execFee": "2.75",
                }
            ],
            "kind": "trade",
        },
        {
            "topic": "execution.linear",
            "data": [
                {
                    "orderLinkId": "bybit-client-1",
                    "orderId": "bybit-order-1",
                    "execID": "bybit-exec-2",
                    "execQty": "1",
                    "execPrice": "50100",
                    "execFee": "2.80",
                }
            ],
            "kind": "trade",
        },
    ]


def test_owned_private_identity_cache_is_bounded() -> None:
    client = GatewayClient(strategy_id="strategy-a", gateway_max_owned_event_ids=2)

    client._remember_owned_event_ids({"order_id": "one"})
    client._remember_owned_event_ids({"order_id": "two"})
    client._remember_owned_event_ids({"order_id": "three"})

    assert client._owned_event_ids == {"two", "three"}
    client._store_event({"kind": "order", "order_id": "one"})
    client._store_event({"kind": "order", "order_id": "three"})

    assert list(client._event_queue) == [{"kind": "order", "order_id": "three"}]


def test_store_event_accepts_private_update_with_nested_strategy_id() -> None:
    client = GatewayClient(strategy_id="strategy-a")

    owned_update = {
        "kind": "trade",
        "details": {
            "strategy_id": "strategy-a",
            "OrderRef": "ctp-1",
        },
    }
    client._store_event({"kind": "trade", "details": {"strategy_id": "strategy-b", "OrderRef": "x"}})
    client._store_event(owned_update)

    assert list(client._event_queue) == [owned_update]


def test_store_event_can_keep_legacy_unowned_order_events() -> None:
    client = GatewayClient(strategy_id="strategy-a", gateway_drop_unowned_events=False)

    client._store_event({"kind": "order", "order_ref": "legacy"})

    assert list(client._event_queue) == [{"kind": "order", "order_ref": "legacy"}]


def test_poll_tick_consumes_existing_queue_before_draining() -> None:
    client = GatewayClient(gateway_max_ticks_per_symbol=3)
    drain_calls = 0

    def drain_market() -> None:
        nonlocal drain_calls
        drain_calls += 1
        client._store_tick({"symbol": "EURUSD", "seq": 99})

    client._drain_market = drain_market  # type: ignore[method-assign]
    client._tick_queues["EURUSD"].append({"symbol": "EURUSD", "seq": 1})

    assert client.poll_tick("EURUSD") == {"symbol": "EURUSD", "seq": 1}
    assert drain_calls == 0
    assert client.poll_tick("EURUSD") == {"symbol": "EURUSD", "seq": 99}
    assert drain_calls == 1


def test_poll_broker_update_consumes_existing_queue_before_draining() -> None:
    client = GatewayClient(gateway_max_events=3)
    drain_calls = 0

    def drain_events() -> None:
        nonlocal drain_calls
        drain_calls += 1
        client._event_queue.append({"seq": 99})

    client._drain_events = drain_events  # type: ignore[method-assign]
    client._event_queue.append({"seq": 1})

    assert client.poll_broker_update() == {"seq": 1}
    assert drain_calls == 0
    assert client.poll_broker_update() == {"seq": 99}
    assert drain_calls == 1


def test_drain_socket_respects_message_limit() -> None:
    zmq = pytest.importorskip("zmq")

    class FakeSocket:
        def __init__(self) -> None:
            self.payloads = [
                json.dumps({"seq": seq}).encode("utf-8")
                for seq in range(5)
            ]

        def recv(self, flags: int = 0) -> bytes:
            assert flags == zmq.NOBLOCK
            if not self.payloads:
                raise zmq.Again()
            return self.payloads.pop(0)

    drained: list[dict[str, int]] = []
    GatewayClient._drain_socket(
        FakeSocket(),
        drained.append,
        max_messages=2,
    )

    assert drained == [{"seq": 0}, {"seq": 1}]


def test_drain_socket_skips_malformed_messages_and_continues() -> None:
    zmq = pytest.importorskip("zmq")

    class FakeSocket:
        def __init__(self) -> None:
            self.payloads = [
                b"not-json",
                json.dumps({"seq": 1}).encode("utf-8"),
            ]

        def recv(self, flags: int = 0) -> bytes:
            assert flags == zmq.NOBLOCK
            if not self.payloads:
                raise zmq.Again()
            return self.payloads.pop(0)

    drained: list[dict[str, int]] = []
    GatewayClient._drain_socket(
        FakeSocket(),
        drained.append,
        max_messages=10,
    )

    assert drained == [{"seq": 1}]
