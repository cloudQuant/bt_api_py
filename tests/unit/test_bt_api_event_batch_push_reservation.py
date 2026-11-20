from __future__ import annotations

import queue

import pytest

from bt_api_py import bt_api as bt_api_module
from bt_api_py.bt_api import BtApi
from bt_api_py.event_batch_handoff import EventBatchHandoffError, EventIngressQueue

EXCHANGE = "BINANCE___SPOT"


class _FakeFeed:
    def disconnect(self) -> None:
        return None


def _new_api(monkeypatch) -> BtApi:
    monkeypatch.setattr(bt_api_module, "_plugins_loaded", True)
    api = BtApi(exchange_kwargs={}, debug=False)
    api.log = lambda *_args, **_kwargs: None
    api._validate_required_environment = lambda *_args, **_kwargs: None
    monkeypatch.setattr(
        bt_api_module.ExchangeRegistry,
        "create_feed",
        lambda *_args, **_kwargs: _FakeFeed(),
    )
    api.add_exchange(EXCHANGE, {})
    assert isinstance(api.data_queues[EXCHANGE], EventIngressQueue)
    return api


class _Bars:
    def __init__(self, rows):
        self.rows = rows

    def get_data(self):
        return list(self.rows)


class _SingleIteration:
    def __init__(self, rows):
        self._rows = iter(rows)
        self.iter_calls = 0

    def __iter__(self):
        self.iter_calls += 1
        if self.iter_calls > 1:
            raise RuntimeError("bar iterator cannot be iterated twice")
        return self

    def __next__(self):
        return next(self._rows)


class _SingleIterationBars:
    def __init__(self, rows):
        self.data = _SingleIteration(rows)

    def get_data(self):
        return self.data


def test_push_bar_data_reservation_prevents_mid_batch_consumer_retirement(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    source = api.data_queues[EXCHANGE]
    consumer_token = api.register_event_consumer(EXCHANGE)
    consumed = []
    retirement_errors = []

    def consume_one_and_try_retire() -> None:
        if source.empty():
            return
        consumed.append(source._get_for_consumer(consumer_token).item)
        try:
            api.unregister_event_consumer(EXCHANGE, consumer_token)
        except EventBatchHandoffError as exc:
            retirement_errors.append(exc.code)

    waker_token = api.register_event_waker(consume_one_and_try_retire)
    api._close_transports = lambda: None
    api.close()

    rows = ["bar-1", "bar-2", "bar-3"]
    bars = _SingleIterationBars(rows)
    api.push_bar_data_to_queue(EXCHANGE, bars)

    assert consumed == rows
    assert bars.data.iter_calls == 1
    assert retirement_errors == ["event_consumer_backlog_pending"] * len(rows)
    assert source.last_ingress_sequence == len(rows)
    assert source.empty()
    assert source.event_waker_callback_errors == 0

    # The reservation ends after the full producer call; the same idle queue
    # can then be retired normally and its retained callback explicitly removed.
    api.unregister_event_consumer(EXCHANGE, consumer_token)
    assert api.unregister_event_waker(waker_token)
    assert not source.event_waker_bound


def test_empty_bar_batch_does_not_reserve_or_fail_on_retired_queue(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    source = api.data_queues[EXCHANGE]
    consumer_token = api.register_event_consumer(EXCHANGE)
    api._close_transports = lambda: None
    api.close()
    api.unregister_event_consumer(EXCHANGE, consumer_token)

    api.push_bar_data_to_queue(EXCHANGE, _Bars([]))
    with pytest.raises(EventBatchHandoffError, match="event_ingress_retired"):
        api.push_bar_data_to_queue(EXCHANGE, _Bars(["bar-1"]))
    assert source.last_ingress_sequence == 0
    assert source.empty()


def test_generator_failure_keeps_prefix_and_releases_reservation(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    source = api.data_queues[EXCHANGE]
    consumer_token = api.register_event_consumer(EXCHANGE)
    failure = RuntimeError("synthetic generator failure after one bar")
    wakes = []
    waker_token = api.register_event_waker(lambda: wakes.append(source.qsize()))

    class _FailingGeneratorBars:
        def get_data(self):
            yield "bar-1"
            raise failure

    with pytest.raises(RuntimeError) as exc_info:
        api.push_bar_data_to_queue(EXCHANGE, _FailingGeneratorBars())

    assert exc_info.value is failure
    assert source.last_ingress_sequence == 1
    assert wakes == [1]
    assert source._get_for_consumer(consumer_token).item == "bar-1"

    api._close_transports = lambda: None
    api.close()
    api.unregister_event_consumer(EXCHANGE, consumer_token)
    assert api.unregister_event_waker(waker_token)
    assert not source.event_waker_bound


def test_push_bar_data_preserves_plain_queue_prefix_and_exception(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    failure = RuntimeError("synthetic second put failure")

    class _FailingQueue(queue.Queue):
        def __init__(self) -> None:
            super().__init__()
            self.put_attempts = 0

        def put(self, item, block=True, timeout=None) -> None:
            self.put_attempts += 1
            if self.put_attempts == 2:
                raise failure
            super().put(item, block=block, timeout=timeout)

    source = _FailingQueue()
    api.data_queues[EXCHANGE] = source
    wakes = []
    waker_token = api.register_event_waker(lambda: wakes.append(source.qsize()))

    with pytest.raises(RuntimeError) as exc_info:
        api.push_bar_data_to_queue(EXCHANGE, _Bars(["bar-1", "bar-2", "bar-3"]))

    assert exc_info.value is failure
    assert source.put_attempts == 2
    assert source.get_nowait() == "bar-1"
    assert source.empty()
    assert wakes == [1]
    assert api.unregister_event_waker(waker_token)

    api._close_transports = lambda: None
    api.close()
