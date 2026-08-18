"""BtApi forwarding bridge tests (Task 3.1).

The bridge must convert topic strings into ``[{"topic": ...}]`` dicts before
calling ``BtApi.subscribe`` (U-02), and must raise a clear error when the
upstream data queue is missing.
"""

from __future__ import annotations

import queue

import pytest

from bt_api_py.forwarding.btapi_bridge import BtApiForwardingBridge
from bt_api_py.forwarding.hub import MarketDataHub
from bt_api_py.forwarding.memory import InMemoryForwardingBus


class _SpyBtApi:
    def __init__(self) -> None:
        self.queue: queue.Queue = queue.Queue()
        self.subscriptions: list[tuple] = []

    def subscribe(self, dataname: str, topics: list) -> None:
        self.subscriptions.append((dataname, topics))

    def get_data_queue(self, exchange_name: str):
        return self.queue


class _NoQueueBtApi:
    def subscribe(self, dataname: str, topics: list) -> None:
        pass

    def get_data_queue(self, exchange_name: str):
        return None


def _bridge(api) -> BtApiForwardingBridge:
    hub = MarketDataHub(InMemoryForwardingBus())
    return BtApiForwardingBridge(api, hub, default_market_type="SPOT")


def test_bridge_converts_topic_strings_to_dicts() -> None:
    api = _SpyBtApi()
    bridge = _bridge(api)

    bridge.subscribe("SIM___SPOT___RB2510", ["ticker"])

    assert api.subscriptions == [("SIM___SPOT___RB2510", [{"topic": "ticker"}])]


def test_bridge_passes_dict_topics_through() -> None:
    api = _SpyBtApi()
    bridge = _bridge(api)

    bridge.subscribe("SIM___SPOT___RB2510", [{"topic": "ticker", "symbol": "RB2510"}])

    assert api.subscriptions == [("SIM___SPOT___RB2510", [{"topic": "ticker", "symbol": "RB2510"}])]


def test_bridge_raises_when_data_queue_missing() -> None:
    api = _NoQueueBtApi()
    bridge = _bridge(api)

    with pytest.raises(RuntimeError, match="data queue"):
        bridge.forward_once("SIM___SPOT")
