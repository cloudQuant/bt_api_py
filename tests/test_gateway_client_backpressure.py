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


def test_event_queue_is_bounded_and_drops_oldest() -> None:
    client = GatewayClient(gateway_max_events=2)

    for seq in range(4):
        client._event_queue.append({"seq": seq})

    assert client._event_queue.maxlen == 2
    assert [item["seq"] for item in client._event_queue] == [2, 3]


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
