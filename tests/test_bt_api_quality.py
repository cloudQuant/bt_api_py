from datetime import UTC, datetime
import queue

import pytest

from bt_api_py.bt_api import BtApi
from bt_api_py.exceptions import InvalidOrderError, SubscribeError


class _DummyFeed:
    def make_order(self, *args, **kwargs):
        return {"args": args, "kwargs": kwargs}


def test_add_exchange_copies_params_before_storing_and_building(monkeypatch):
    captured: dict[str, object] = {}

    def fake_create_feed(exchange_name, data_queue, **kwargs):
        captured["exchange_name"] = exchange_name
        captured["data_queue"] = data_queue
        captured["kwargs"] = kwargs
        return _DummyFeed()

    monkeypatch.setattr("bt_api_py.bt_api.ExchangeRegistry.create_feed", fake_create_feed)

    api = BtApi(None, debug=False)
    params = {"credentials": {"api_key": "secret"}, "timeout": 5}

    api.add_exchange("TEST___SPOT", params)
    params["credentials"]["api_key"] = "mutated"

    assert api.exchange_kwargs["TEST___SPOT"]["credentials"]["api_key"] == "secret"
    assert captured["kwargs"] == {"credentials": {"api_key": "secret"}, "timeout": 5}
    assert isinstance(captured["data_queue"], queue.Queue)


def test_add_exchange_rolls_back_state_when_feed_creation_fails(monkeypatch):
    monkeypatch.setattr(
        "bt_api_py.bt_api.ExchangeRegistry.create_feed",
        lambda exchange_name, data_queue, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    api = BtApi(None, debug=False)

    with pytest.raises(RuntimeError, match="boom"):
        api.add_exchange("FAIL___SPOT", {"nested": {"x": 1}})

    assert "FAIL___SPOT" not in api.exchange_kwargs
    assert "FAIL___SPOT" not in api.data_queues
    assert "FAIL___SPOT" not in api.exchange_feeds


def test_subscribe_passes_copied_params_and_topics_to_handler(monkeypatch):
    captured: dict[str, object] = {}

    def handler(data_queue, exchange_params, topics, api):
        captured["data_queue"] = data_queue
        captured["exchange_params"] = exchange_params
        captured["topics"] = topics
        exchange_params["nested"]["value"] = "changed"
        topics[0]["meta"]["flag"] = "changed"

    monkeypatch.setattr("bt_api_py.bt_api.ExchangeRegistry.get_stream_class", lambda *args: handler)

    api = BtApi(None, debug=False)
    api.exchange_kwargs["TEST___SPOT"] = {"nested": {"value": "original"}}
    api.data_queues["TEST___SPOT"] = queue.Queue()
    original_topics = [{"topic": "kline", "meta": {"flag": "original"}}]

    api.subscribe("TEST___SPOT___BTCUSDT", original_topics)

    assert api.subscribe_bar_num == 1
    assert captured["data_queue"] is api.data_queues["TEST___SPOT"]
    assert api.exchange_kwargs["TEST___SPOT"]["nested"]["value"] == "original"
    assert original_topics[0]["meta"]["flag"] == "original"


@pytest.mark.parametrize(
    "topics",
    [
        "not-a-list",
        ["not-a-dict"],
        [{}],
        [{"topic": ""}],
    ],
)
def test_subscribe_rejects_invalid_topic_shapes(topics):
    api = BtApi(None, debug=False)

    with pytest.raises(SubscribeError, match="topic"):
        api.subscribe("TEST___SPOT___BTCUSDT", topics)


def test_make_order_rejects_non_string_order_type():
    api = BtApi(None, debug=False)
    api.exchange_feeds["TEST___SPOT"] = _DummyFeed()

    with pytest.raises(InvalidOrderError, match="order_type"):
        api.make_order("TEST___SPOT", "BTCUSDT", 1, 1, None)


def test_calculate_aligned_stop_time_uses_full_daily_delta(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 3, 20, 10, 30, 45, tzinfo=UTC)

    monkeypatch.setattr("bt_api_py.bt_api.datetime", FrozenDateTime)

    api = BtApi(None, debug=False)
    aligned = api._calculate_aligned_stop_time("1D")

    assert aligned == datetime(2026, 3, 20, 0, 0, 0, tzinfo=UTC)
