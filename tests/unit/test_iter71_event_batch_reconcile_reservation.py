"""Synthetic reconciliation producer reservations for SDK event batches."""
import importlib.util
import threading
from pathlib import Path

import pytest

from bt_api_py import bt_api as module
from bt_api_py.event_batch_handoff import EventBatchHandoffError

_spec = importlib.util.spec_from_file_location(
    "event_batch_fake_fixture", Path(__file__).with_name("test_bt_api_event_batch_handoff.py")
)
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)
ROUTE = fixture.EXCHANGE


def _api(monkeypatch):
    api = fixture._new_api(monkeypatch)
    monkeypatch.setattr(api, "_close_transports", lambda: None)
    fixture._normalizer(monkeypatch, lambda item, exchange_name, **_kwargs: {
        **item, "exchange_name": exchange_name,
    })
    return api


def _endpoints(api, entered, release):
    def orders(*_args, **_kwargs):
        entered.set()
        assert release.wait(3)
        return [{"symbol": "synthetic", "order_id": "one"}]
    api.get_open_orders = orders
    api.get_account = lambda *_args, **_kwargs: []
    api.get_position = lambda *_args, **_kwargs: []
    api.get_deals = lambda *_args, **_kwargs: []


def _reconnect(api):
    return api.event_bus.emit("ws.connected", {"stream_role": "account", "exchange_name": ROUTE,
                                               "connection_generation": 2})


def _drain(api, token):
    events = []
    while True:
        batch = api.poll_event_batch(ROUTE, token)
        if batch is None:
            return events
        events.extend(batch.events)
        api.ack_event_batch(ROUTE, token, batch)


def test_reconnect_worker_blocks_retirement_until_published(monkeypatch):
    api = _api(monkeypatch)
    token = api.register_event_consumer(ROUTE)
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    _endpoints(api, entered, release)
    original = api.reconcile_private_state
    errors = []
    real_thread = threading.Thread
    workers = []
    class RecordingThread(real_thread):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            workers.append(self)
    monkeypatch.setattr(module.threading, "Thread", RecordingThread)
    def wrapped(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()
    api.reconcile_private_state = wrapped
    try:
        _reconnect(api)
        assert entered.wait(2)
        assert any(event.get("status") == "required" for event in _drain(api, token))
        api.close()
        with pytest.raises(EventBatchHandoffError, match="event_consumer_backlog_pending"):
            api.unregister_event_consumer(ROUTE, token)
    finally:
        release.set()
        assert done.wait(3)
        assert len(workers) == 1
        workers[0].join(3)
    assert not workers[0].is_alive() and not errors
    events = _drain(api, token)
    assert any(event.get("kind") == "order" for event in events)
    assert any(event.get("status") == "complete" for event in events)
    api.unregister_event_consumer(ROUTE, token)


def test_direct_reconcile_blocks_retirement_while_query_is_active(monkeypatch):
    api = _api(monkeypatch)
    token = api.register_event_consumer(ROUTE)
    entered, release = threading.Event(), threading.Event()
    _endpoints(api, entered, release)
    errors = []
    def run():
        try:
            api.reconcile_private_state(ROUTE, connection_generation=2)
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert entered.wait(2)
        api.close()
        with pytest.raises(EventBatchHandoffError, match="event_consumer_backlog_pending"):
            api.unregister_event_consumer(ROUTE, token)
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and not errors
    assert any(event.get("kind") == "order" for event in _drain(api, token))
    api.unregister_event_consumer(ROUTE, token)


def test_unknown_thread_start_retains_reservation(monkeypatch):
    api = _api(monkeypatch)
    token = api.register_event_consumer(ROUTE)
    class FailedThread:
        def __init__(self, **_kwargs):
            pass
        def start(self):
            raise RuntimeError("synthetic thread-start failure")
    monkeypatch.setattr(module.threading, "Thread", FailedThread)
    errors = _reconnect(api)
    assert len(errors) == 1 and str(errors[0]) == "synthetic thread-start failure"
    _drain(api, token)
    api.close()
    with pytest.raises(EventBatchHandoffError, match="event_consumer_backlog_pending"):
        api.unregister_event_consumer(ROUTE, token)


def test_scheduled_but_not_started_worker_retains_required_event_custody(monkeypatch):
    api = _api(monkeypatch)
    token = api.register_event_consumer(ROUTE)
    scheduled = []
    class DeferredThread:
        def __init__(self, *, target, args, kwargs, **_options):
            self.target, self.args, self.kwargs = target, args, kwargs
            scheduled.append(self)
        def start(self):
            return None
    monkeypatch.setattr(module.threading, "Thread", DeferredThread)
    api.get_open_orders = lambda *_args, **_kwargs: [{"symbol": "synthetic"}]
    api.get_account = api.get_position = api.get_deals = lambda *_args, **_kwargs: []
    assert _reconnect(api) == [] and len(scheduled) == 1
    assert any(row.get("status") == "required" for row in _drain(api, token))
    api.close()
    with pytest.raises(EventBatchHandoffError, match="event_consumer_backlog_pending"):
        api.unregister_event_consumer(ROUTE, token)
    scheduled[0].target(*scheduled[0].args, **scheduled[0].kwargs)
    assert any(row.get("kind") == "order" for row in _drain(api, token))
    api.unregister_event_consumer(ROUTE, token)


def test_thread_start_after_launch_exception_retains_until_worker_finishes(monkeypatch):
    api = _api(monkeypatch)
    token = api.register_event_consumer(ROUTE)
    entered, release = threading.Event(), threading.Event()
    _endpoints(api, entered, release)
    real_thread = threading.Thread
    workers = []
    class StartThenRaise:
        def __init__(self, **kwargs):
            self.thread = real_thread(**kwargs)
            workers.append(self.thread)
        def start(self):
            self.thread.start()
            raise RuntimeError("synthetic after-launch failure")
    monkeypatch.setattr(module.threading, "Thread", StartThenRaise)
    try:
        errors = _reconnect(api)
        assert len(errors) == 1 and str(errors[0]) == "synthetic after-launch failure"
        assert entered.wait(2)
        _drain(api, token)
        api.close()
        with pytest.raises(EventBatchHandoffError, match="event_consumer_backlog_pending"):
            api.unregister_event_consumer(ROUTE, token)
    finally:
        release.set()
        for worker in workers:
            worker.join(3)
    assert len(workers) == 1 and not workers[0].is_alive()
    assert any(row.get("kind") == "order" for row in _drain(api, token))
    api.unregister_event_consumer(ROUTE, token)


def test_reconcile_control_exception_retains_original_and_blocks_retire(monkeypatch):
    api = _api(monkeypatch)
    token = api.register_event_consumer(ROUTE)
    original = KeyboardInterrupt("synthetic worker failure")
    def fail(*_args, **_kwargs):
        raise original
    api.get_open_orders = fail
    class ImmediateThread:
        def __init__(self, *, target, args, kwargs, **_options):
            self.target, self.args, self.kwargs = target, args, kwargs
        def start(self):
            self.target(*self.args, **self.kwargs)
    monkeypatch.setattr(module.threading, "Thread", ImmediateThread)
    with pytest.raises(KeyboardInterrupt) as caught:
        api._on_websocket_connected({"stream_role": "account", "exchange_name": ROUTE,
                                     "connection_generation": 2})
    assert caught.value is original
    assert any(row.get("status") == "required" for row in _drain(api, token))
    api.close()
    with pytest.raises(EventBatchHandoffError, match="event_consumer_backlog_pending"):
        api.unregister_event_consumer(ROUTE, token)
    holders = [holder for holder in module._EVENT_RECONCILE_CUSTODY if holder.api is api]
    assert holders and any(holder.failure is original for holder in holders)


def test_reentrant_waker_cannot_retire_during_required_event(monkeypatch):
    api = _api(monkeypatch)
    token = api.register_event_consumer(ROUTE)
    enabled = [False]
    errors = []
    def wake():
        if not enabled[0]:
            return
        enabled[0] = False
        _drain(api, token)
        try:
            api.unregister_event_consumer(ROUTE, token)
        except EventBatchHandoffError as exc:
            errors.append(exc.code)
    api.register_event_waker(wake)
    api.close()
    enabled[0] = True
    api.get_open_orders = lambda *_args, **_kwargs: [{"symbol": "synthetic"}]
    api.get_account = api.get_position = api.get_deals = lambda *_args, **_kwargs: []
    class ImmediateThread:
        def __init__(self, *, target, args, kwargs, **_options):
            self.target, self.args, self.kwargs = target, args, kwargs
        def start(self):
            self.target(*self.args, **self.kwargs)
    monkeypatch.setattr(module.threading, "Thread", ImmediateThread)
    api._on_websocket_connected({"stream_role": "account", "exchange_name": ROUTE,
                                 "connection_generation": 2})
    assert errors == ["event_consumer_backlog_pending"]
    assert any(row.get("kind") == "order" for row in _drain(api, token))
    api.unregister_event_consumer(ROUTE, token)


def test_reconcile_source_replacement_retains_original_queue_reservation(monkeypatch):
    from bt_api_py.event_batch_handoff import EventIngressQueue
    api = _api(monkeypatch)
    token = api.register_event_consumer(ROUTE)
    original_source = api.data_queues[ROUTE]
    entered, release = threading.Event(), threading.Event()
    _endpoints(api, entered, release)
    errors = []
    def run():
        try:
            api.reconcile_private_state(ROUTE, connection_generation=2)
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert entered.wait(2)
        api.data_queues[ROUTE] = EventIngressQueue()
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and len(errors) == 1
    assert isinstance(errors[0], EventBatchHandoffError)
    assert errors[0].code == "event_reconcile_source_changed"
    api.data_queues[ROUTE] = original_source
    api.close()
    _drain(api, token)
    with pytest.raises(EventBatchHandoffError, match="event_consumer_backlog_pending"):
        api.unregister_event_consumer(ROUTE, token)


def test_retired_queue_refuses_reconcile_before_any_query(monkeypatch):
    api = _api(monkeypatch)
    token = api.register_event_consumer(ROUTE)
    api.close()
    api.unregister_event_consumer(ROUTE, token)
    queried = []
    api.get_open_orders = lambda *_args, **_kwargs: queried.append(True)
    with pytest.raises(EventBatchHandoffError, match="event_ingress_retired"):
        api.reconcile_private_state(ROUTE, connection_generation=2)
    assert queried == []


def test_required_event_waker_control_exception_retains_reservation(monkeypatch):
    api = _api(monkeypatch)
    token = api.register_event_consumer(ROUTE)
    original = KeyboardInterrupt("synthetic waker failure")
    def fail():
        raise original
    waker = api.register_event_waker(fail)
    with pytest.raises(KeyboardInterrupt) as caught:
        api._on_websocket_connected({"stream_role": "account", "exchange_name": ROUTE,
                                     "connection_generation": 2})
    assert caught.value is original
    assert api.unregister_event_waker(waker)
    assert any(row.get("status") == "required" for row in _drain(api, token))
    api.close()
    with pytest.raises(EventBatchHandoffError, match="event_consumer_backlog_pending"):
        api.unregister_event_consumer(ROUTE, token)
    holders = [holder for holder in module._EVENT_RECONCILE_CUSTODY if holder.api is api]
    assert len(holders) == 1 and holders[0].failure is original


def test_failed_normalized_publication_keeps_partial_result_and_exact_event(monkeypatch):
    api = _api(monkeypatch)
    token = api.register_event_consumer(ROUTE)
    row = {"symbol": "synthetic", "order_id": "failed-row"}
    api.get_open_orders = lambda *_args, **_kwargs: [row]
    api.get_account = api.get_position = api.get_deals = lambda *_args, **_kwargs: []
    failure = RuntimeError("synthetic publish failure")
    held = []
    original = api._publish_private_event
    def publish(exchange_name, item, *, normalized=False):
        if normalized and item.get("kind") == "order":
            held.append(item)
            raise failure
        return original(exchange_name, item, normalized=normalized)
    monkeypatch.setattr(api, "_publish_private_event", publish)
    result = api.reconcile_private_state(ROUTE, connection_generation=2)
    assert result["status"] == "partial_failed" and len(held) == 1
    assert any(event.get("status") == "partial_failed" for event in _drain(api, token))
    api.close()
    with pytest.raises(EventBatchHandoffError, match="event_consumer_backlog_pending"):
        api.unregister_event_consumer(ROUTE, token)
    owners = [holder for holder in module._EVENT_RECONCILE_CUSTODY if holder.api is api]
    assert any(any(event is held[0] and error is failure for event, error in owner.failed_events)
               for owner in owners)


def test_source_disappears_inside_publish_retains_exact_generated_event(monkeypatch):
    api = _api(monkeypatch)
    token = api.register_event_consumer(ROUTE)
    original_source = api.data_queues[ROUTE]
    api.get_open_orders = lambda *_args, **_kwargs: [{"symbol": "synthetic", "order_id": "held"}]
    api.get_account = api.get_position = api.get_deals = lambda *_args, **_kwargs: []
    original_publish = api._publish_private_event
    generated = []
    def remove_source(exchange_name, event, *, normalized=False):
        if normalized and event.get("kind") == "order":
            generated.append(event)
            api.data_queues.pop(exchange_name)
        return original_publish(exchange_name, event, normalized=normalized)
    monkeypatch.setattr(api, "_publish_private_event", remove_source)
    with pytest.raises(EventBatchHandoffError, match="event_reconcile_source_changed"):
        api.reconcile_private_state(ROUTE, connection_generation=2)
    assert len(generated) == 1
    holders = [holder for holder in module._EVENT_RECONCILE_CUSTODY if holder.api is api]
    assert any(any(event is generated[0] for event, _error in holder.failed_events)
               for holder in holders)
    api.data_queues[ROUTE] = original_source
    api.close()
    _drain(api, token)
    with pytest.raises(EventBatchHandoffError, match="event_consumer_backlog_pending"):
        api.unregister_event_consumer(ROUTE, token)


def test_missing_batch_source_refuses_before_query(monkeypatch):
    api = _api(monkeypatch)
    api.register_event_consumer(ROUTE)
    api.data_queues.pop(ROUTE)
    queried = []
    api.get_open_orders = lambda *_args, **_kwargs: queried.append(True)
    with pytest.raises(EventBatchHandoffError, match="event_reconcile_source_changed"):
        api.reconcile_private_state(ROUTE, connection_generation=2)
    assert queried == []


def test_plain_legacy_queue_keeps_reconciliation_behavior(monkeypatch):
    import queue
    api = _api(monkeypatch)
    source = queue.Queue()
    api.data_queues[ROUTE] = source
    api.get_open_orders = api.get_account = api.get_position = api.get_deals = lambda *_args, **_kwargs: []
    result = api.reconcile_private_state(ROUTE, connection_generation=2)
    assert result["status"] == "complete"
    assert source.get_nowait() is result
    assert not any(holder.api is api for holder in module._EVENT_RECONCILE_CUSTODY)


def test_concurrent_same_generation_admits_one_reconcile_worker(monkeypatch):
    api = _api(monkeypatch)
    token = api.register_event_consumer(ROUTE)
    api.get_open_orders = api.get_account = api.get_position = api.get_deals = lambda *_args, **_kwargs: []
    first_entered, second_entered, release = threading.Event(), threading.Event(), threading.Event()
    count_lock = threading.Lock()
    calls = []
    original = api._begin_event_reconcile_producer
    def begin(exchange_name):
        with count_lock:
            calls.append(threading.get_ident())
            first = len(calls) == 1
        if first:
            first_entered.set()
            assert release.wait(3)
        else:
            second_entered.set()
        return original(exchange_name)
    monkeypatch.setattr(api, "_begin_event_reconcile_producer", begin)
    payload = {"stream_role": "account", "exchange_name": ROUTE, "connection_generation": 2}
    errors = []
    real_thread = threading.Thread
    workers = []
    class RecordingThread(real_thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if kwargs.get("name", "").startswith("btapi-reconcile-"):
                workers.append(self)
    monkeypatch.setattr(module.threading, "Thread", RecordingThread)
    def run():
        try:
            api._on_websocket_connected(payload)
        except BaseException as exc:
            errors.append(exc)
    first = threading.Thread(target=run, daemon=True)
    second = threading.Thread(target=run, daemon=True)
    first.start()
    try:
        assert first_entered.wait(2)
        second.start()
        second_entered.wait(0.2)
    finally:
        release.set()
        first.join(3)
        second.join(3)
    assert not first.is_alive() and not second.is_alive() and not errors
    assert len(workers) == 1
    workers[0].join(3)
    assert not workers[0].is_alive()
    assert api.get_event_metrics()["private_reconcile_triggers"] == 1
    assert sum(row.get("status") == "required" for row in _drain(api, token)) == 1


@pytest.mark.parametrize("failure_mode", ["publish", "source_change"])
@pytest.mark.parametrize("failure_index", [0, 1])
def test_failed_multirow_publication_retains_original_unpublished_suffix(failure_mode, failure_index, monkeypatch):
    api = _api(monkeypatch)
    token = api.register_event_consumer(ROUTE)
    original_source = api.data_queues[ROUTE]
    rows = [{"symbol": "synthetic", "order_id": str(index)} for index in range(3)]
    api.get_open_orders = lambda *_args, **_kwargs: rows
    api.get_account = api.get_position = api.get_deals = lambda *_args, **_kwargs: []
    original_publish = api._publish_private_event
    failed_events = []
    failure = RuntimeError("synthetic multirow publish failure")
    def publish(exchange_name, event, *, normalized=False):
        if normalized and event.get("kind") == "order":
            failed_events.append(event)
            if len(failed_events) - 1 == failure_index:
                if failure_mode == "publish":
                    raise failure
                api.data_queues.pop(exchange_name)
        return original_publish(exchange_name, event, normalized=normalized)
    monkeypatch.setattr(api, "_publish_private_event", publish)
    if failure_mode == "publish":
        result = api.reconcile_private_state(ROUTE, connection_generation=2)
        assert result["status"] == "partial_failed"
    else:
        with pytest.raises(EventBatchHandoffError, match="event_reconcile_source_changed"):
            api.reconcile_private_state(ROUTE, connection_generation=2)
        api.data_queues[ROUTE] = original_source
    assert len(failed_events) == failure_index + 1
    holders = [holder for holder in module._EVENT_RECONCILE_CUSTODY if holder.api is api]
    assert any(any(event is failed_events[-1] for event, _error in holder.failed_events)
               for holder in holders)
    expected_suffix = rows[failure_index:]
    assert any(any(scope == "orders" and first == failure_index
                   and len(held_rows[first:]) == len(expected_suffix)
                   and all(got is want for got, want in zip(held_rows[first:], expected_suffix))
                   for scope, held_rows, first, _error in holder.failed_rows)
               for holder in holders)
    api.close()
    _drain(api, token)
    with pytest.raises(EventBatchHandoffError, match="event_consumer_backlog_pending"):
        api.unregister_event_consumer(ROUTE, token)


def test_failed_returned_row_extraction_retains_exact_provider_result(monkeypatch):
    api = _api(monkeypatch)
    token = api.register_event_consumer(ROUTE)
    failure = RuntimeError("synthetic row extraction failure")
    class InterruptedRows(list):
        def __iter__(self):
            yield self[0]
            raise failure
    raw = InterruptedRows([{"symbol": "first"}, {"symbol": "unvisited"}])
    api.get_open_orders = lambda *_args, **_kwargs: raw
    api.get_account = api.get_position = api.get_deals = lambda *_args, **_kwargs: []
    result = api.reconcile_private_state(ROUTE, connection_generation=2)
    assert result["status"] == "partial_failed"
    _drain(api, token)
    api.close()
    with pytest.raises(EventBatchHandoffError, match="event_consumer_backlog_pending"):
        api.unregister_event_consumer(ROUTE, token)
    holders = [holder for holder in module._EVENT_RECONCILE_CUSTODY if holder.api is api]
    assert any(any(scope == "orders" and value is raw and error is failure
                   for scope, value, error in holder.failed_results) for holder in holders)
