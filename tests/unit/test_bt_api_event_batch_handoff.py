"""Synthetic tests for the opt-in SDK event batch handoff."""

from __future__ import annotations

import queue
import threading

import pytest

from bt_api_py import bt_api as bt_api_module
from bt_api_py._contracts.errors import CapabilityNotSupportedError, NormalizedApiError
from bt_api_py._ctp_ingress import _CtpMarketIngressEnvelope
from bt_api_py.bt_api import BtApi
from bt_api_py.event_batch_handoff import (
    EventBatch,
    EventBatchHandoffError,
    EventIngressQueue,
    PendingEventInput,
)

EXCHANGE = "BINANCE___SPOT"


class _FakeFeed:
    def disconnect(self) -> None:
        return None


def _new_api(monkeypatch) -> BtApi:
    monkeypatch.setattr(bt_api_module, "_plugins_loaded", True)
    api = BtApi(exchange_kwargs={}, debug=False)
    api.log = lambda *_args, **_kwargs: None
    api._validate_required_environment = lambda *_args, **_kwargs: None
    producer_queues = []

    def create_feed(_name, producer_queue, **_kwargs):
        producer_queues.append(producer_queue)
        return _FakeFeed()

    monkeypatch.setattr(
        bt_api_module.ExchangeRegistry,
        "create_feed",
        create_feed,
    )
    api.add_exchange(EXCHANGE, {})
    assert isinstance(api.data_queues[EXCHANGE], EventIngressQueue)
    assert producer_queues == [api.data_queues[EXCHANGE]]
    _normalizer(
        monkeypatch,
        lambda item, exchange_name, **_kwargs: {
            "kind": item.get("kind", "synthetic"),
            "symbol": item.get("symbol"),
            "exchange_name": exchange_name,
        },
    )
    return api


def _normalizer(monkeypatch, function) -> None:
    monkeypatch.setattr(
        bt_api_module.importlib.import_module("bt_api_py._normalization"),
        "normalize_event",
        function,
    )


def test_add_exchange_uses_real_queue_ingress_and_replays_backlog(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    source = api.data_queues[EXCHANGE]
    source.put({"kind": "tick", "symbol": "before-claim"})
    token = api.register_event_consumer(EXCHANGE)

    batch = api.poll_event_batch(EXCHANGE, token)
    assert batch is not None
    assert batch.event_epoch == source.event_epoch
    assert batch.batch_sequence == 1
    assert batch.events[0]["symbol"] == "before-claim"
    api.ack_event_batch(EXCHANGE, token, batch)
    assert api.poll_event_batch(EXCHANGE, token) is None


def test_unclaimed_event_ingress_queue_preserves_task_done_accounting() -> None:
    source = EventIngressQueue()
    item = {"kind": "tick", "symbol": "legacy-queue-contract"}

    assert not source.consumer_claimed
    source.put(item)
    assert source.unfinished_tasks == 1
    assert source.get_nowait() is item
    assert source.unfinished_tasks == 1

    source.task_done()
    assert source.unfinished_tasks == 0
    with pytest.raises(ValueError):
        source.task_done()


def test_exact_batch_redelivery_ack_and_no_direct_queue_bypass(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    source = api.data_queues[EXCHANGE]
    source.put({"kind": "tick", "symbol": "one"})
    token = api.register_event_consumer(EXCHANGE)

    first = api.poll_event_batch(EXCHANGE, token)
    assert first is not None
    assert api.poll_event_batch(EXCHANGE, token) is first
    assert not hasattr(source, "get_for_consumer")
    assert first.events[0]["symbol"] == "one"
    detached = first.events[0]
    detached["symbol"] = "mutated-copy"
    assert first.events[0]["symbol"] == "one"
    with pytest.raises(EventBatchHandoffError, match="event_batch_consumer_owns_queue"):
        source.get_nowait()
    with pytest.raises(EventBatchHandoffError, match="event_batch_consumer_owns_queue"):
        api.poll_events(EXCHANGE, max_raw_items=0)
    with pytest.raises(EventBatchHandoffError, match="event_batch_ack_not_current"):
        api.ack_event_batch(
            EXCHANGE,
            token,
            EventBatch(
                EXCHANGE, first.event_epoch, first.batch_sequence, first.batch_id, first._events
            ),
        )
    with pytest.raises(EventBatchHandoffError, match="event_consumer_token_invalid"):
        api.ack_event_batch(EXCHANGE, object(), first)

    api.ack_event_batch(EXCHANGE, token, first)
    source.put({"kind": "tick", "symbol": "two"})
    second = api.poll_event_batch(EXCHANGE, token)
    assert second is not None and second.batch_sequence == 2
    assert second.events[0]["symbol"] == "two"
    api.ack_event_batch(EXCHANGE, token, second)


def test_legacy_poll_attempt_and_direct_get_attempt_block_late_claim(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    source = api.data_queues[EXCHANGE]
    with pytest.raises(queue.Empty):
        source.get_nowait()
    with pytest.raises(EventBatchHandoffError, match="event_consumer_late_registration"):
        api.register_event_consumer(EXCHANGE)

    other = _new_api(monkeypatch)
    assert other.poll_events(EXCHANGE, max_raw_items=0) == []
    with pytest.raises(EventBatchHandoffError, match="event_consumer_late_registration"):
        other.register_event_consumer(EXCHANGE)


def test_legacy_poll_events_keeps_default_destructive_contract(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    api.data_queues[EXCHANGE].put({"kind": "tick", "symbol": "legacy"})

    events = api.poll_events(EXCHANGE)

    assert [event["symbol"] for event in events] == ["legacy"]
    assert api.data_queues[EXCHANGE].empty()
    with pytest.raises(EventBatchHandoffError, match="event_consumer_late_registration"):
        api.register_event_consumer(EXCHANGE)


def test_legacy_queue_get_returns_payload_shape_before_any_claim(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    source = api.data_queues[EXCHANGE]
    item = {"kind": "tick", "symbol": "direct-get"}
    source.put(item)

    assert api.get_data_queue(EXCHANGE).get_nowait() is item
    with pytest.raises(EventBatchHandoffError, match="event_consumer_late_registration"):
        api.register_event_consumer(EXCHANGE)


def test_direct_get_and_claim_share_one_queue_linearization_point(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    source = api.data_queues[EXCHANGE]
    direct_queue = api.get_data_queue(EXCHANGE)
    read_attempted = threading.Event()
    original_guard = source._require_legacy_read_allowed

    def signal_read_attempt() -> None:
        # EventIngressQueue.get sets _legacy_read_started under Queue.mutex
        # immediately before this guard, then waits on that same mutex.
        read_attempted.set()
        original_guard()

    source._require_legacy_read_allowed = signal_read_attempt
    received = []
    read_errors = []

    def blocking_direct_read() -> None:
        try:
            received.append(direct_queue.get())
        except Exception as exc:  # captured for assertion in the test thread
            read_errors.append(exc)

    reader = threading.Thread(target=blocking_direct_read, daemon=True)
    reader.start()
    assert read_attempted.wait(timeout=2)

    claim_error = None
    item = {"kind": "tick", "symbol": "reader-won"}
    try:
        api.register_event_consumer(EXCHANGE)
    except EventBatchHandoffError as exc:
        claim_error = exc
    finally:
        # Release the already-started legacy reader even if a regression makes
        # the registration unexpectedly succeed.
        source.put(item)
    reader.join(timeout=2)

    assert not reader.is_alive()
    assert not read_errors
    assert received == [item]
    assert isinstance(claim_error, EventBatchHandoffError)
    assert claim_error.code == "event_consumer_late_registration"

    # In the opposite ordering, claim wins the same queue mutex first and a
    # later public Queue.get fails without removing the pre-existing backlog.
    other = _new_api(monkeypatch)
    other_source = other.data_queues[EXCHANGE]
    queued = {"kind": "tick", "symbol": "claim-won"}
    other_source.put(queued)
    token = other.register_event_consumer(EXCHANGE)
    bypass_errors = []
    bypass = threading.Thread(
        target=lambda: _capture_queue_get_error(other.get_data_queue(EXCHANGE), bypass_errors),
        daemon=True,
    )
    bypass.start()
    bypass.join(timeout=2)
    assert not bypass.is_alive()
    assert len(bypass_errors) == 1
    assert isinstance(bypass_errors[0], EventBatchHandoffError)
    assert bypass_errors[0].code == "event_batch_consumer_owns_queue"
    assert other_source.qsize() == 1
    batch = other.poll_event_batch(EXCHANGE, token)
    assert batch is not None and batch.events[0]["symbol"] == "claim-won"
    other.ack_event_batch(EXCHANGE, token, batch)


def _capture_queue_get_error(data_queue, errors) -> None:
    try:
        data_queue.get_nowait()
    except Exception as exc:
        errors.append(exc)


def test_get_data_expansion_stays_in_original_order_when_raw_budget_expires(monkeypatch) -> None:
    api = _new_api(monkeypatch)

    class Container:
        def get_data(self):
            return [
                {"kind": "tick", "symbol": "expanded-1"},
                {"kind": "tick", "symbol": "expanded-2"},
            ]

    source = api.data_queues[EXCHANGE]
    source.put(Container())
    source.put({"kind": "tick", "symbol": "next-raw"})
    token = api.register_event_consumer(EXCHANGE)

    first = api.poll_event_batch(EXCHANGE, token, max_raw_items=1)
    assert first is not None
    assert [event["symbol"] for event in first.events] == ["expanded-1", "expanded-2"]
    assert source.qsize() == 1
    api.ack_event_batch(EXCHANGE, token, first)
    second = api.poll_event_batch(EXCHANGE, token)
    assert second is not None and second.events[0]["symbol"] == "next-raw"
    api.ack_event_batch(EXCHANGE, token, second)


def test_dispatch_exception_leaves_exact_batch_available_for_replay(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    source = api.data_queues[EXCHANGE]
    source.put({"kind": "tick", "symbol": "applied-before-failure"})
    source.put({"kind": "tick", "symbol": "throws-in-dispatch"})
    token = api.register_event_consumer(EXCHANGE)
    batch = api.poll_event_batch(EXCHANGE, token)
    assert batch is not None and len(batch.events) == 2

    applied = []
    with pytest.raises(RuntimeError, match="synthetic BT dispatch failure"):
        for event in batch.events:
            if event["symbol"] == "throws-in-dispatch":
                raise RuntimeError("synthetic BT dispatch failure")
            applied.append(event["symbol"])

    # The first side effect may be repeated: the contract is retained
    # at-least-once handoff, not exactly-once dispatch.
    assert applied == ["applied-before-failure"]
    assert api.poll_event_batch(EXCHANGE, token) is batch
    assert [event["symbol"] for event in batch.events] == [
        "applied-before-failure",
        "throws-in-dispatch",
    ]
    api.ack_event_batch(EXCHANGE, token, batch)


def test_normalizer_failure_retains_raw_input_for_retry(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    source = api.data_queues[EXCHANGE]
    raw = {"kind": "tick", "symbol": "retry-me"}
    source.put(raw)
    token = api.register_event_consumer(EXCHANGE)
    _normalizer(monkeypatch, lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError()))

    with pytest.raises(NormalizedApiError):
        api.poll_event_batch(EXCHANGE, token)
    assert source.empty()
    retained = api._normalized_event_pending[EXCHANGE][0]
    assert type(retained) is PendingEventInput
    assert retained.item is raw
    assert retained.ingress is not None and retained.ingress.sequence == 1

    _normalizer(
        monkeypatch,
        lambda item, exchange_name, **_kwargs: {
            "kind": item["kind"],
            "symbol": item["symbol"],
            "exchange_name": exchange_name,
        },
    )
    batch = api.poll_event_batch(EXCHANGE, token)
    assert batch is not None and batch.events[0]["symbol"] == "retry-me"
    api.ack_event_batch(EXCHANGE, token, batch)


def test_valid_prefix_is_leased_before_failed_input(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    source = api.data_queues[EXCHANGE]
    source.put({"kind": "tick", "symbol": "good"})
    source.put({"kind": "tick", "symbol": "bad"})
    token = api.register_event_consumer(EXCHANGE)

    def normalizer(item, exchange_name, **_kwargs):
        if item["symbol"] == "bad":
            raise ValueError("synthetic failure")
        return {"kind": item["kind"], "symbol": item["symbol"], "exchange_name": exchange_name}

    _normalizer(monkeypatch, normalizer)
    prefix = api.poll_event_batch(EXCHANGE, token)
    assert prefix is not None
    assert prefix.events[0]["symbol"] == "good"
    assert prefix.blocked_error_code
    assert api.poll_event_batch(EXCHANGE, token) is prefix
    api.ack_event_batch(EXCHANGE, token, prefix)

    _normalizer(
        monkeypatch,
        lambda item, exchange_name, **_kwargs: {
            "kind": item["kind"],
            "symbol": item["symbol"],
            "exchange_name": exchange_name,
        },
    )
    remainder = api.poll_event_batch(EXCHANGE, token)
    assert remainder is not None and remainder.events[0]["symbol"] == "bad"
    api.ack_event_batch(EXCHANGE, token, remainder)


def test_ctp_one_shot_ingress_is_not_consumed_twice_after_normalizer_error(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    exchange = "CTP___SIMNOW"
    source = EventIngressQueue()
    api.data_queues[exchange] = source
    api._ctp_market_ingress_seal = seal = object()
    raw = {"kind": "tick", "symbol": "IF2612"}
    source.put(_CtpMarketIngressEnvelope(seal, raw, {"kind": "tick", "symbol": "IF2612"}))
    token = api.register_event_consumer(exchange)
    consume = api._consume_ctp_market_ingress
    calls = []

    def count_consume(exchange_name, item):
        calls.append(item)
        return consume(exchange_name, item)

    monkeypatch.setattr(api, "_consume_ctp_market_ingress", count_consume)
    _normalizer(monkeypatch, lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(NormalizedApiError):
        api.poll_event_batch(exchange, token)
    assert len(calls) == 1

    _normalizer(
        monkeypatch,
        lambda item, name, **_kwargs: {
            "kind": item["kind"],
            "symbol": item["symbol"],
            "exchange_name": name,
        },
    )
    batch = api.poll_event_batch(exchange, token)
    assert batch is not None and batch.events[0]["symbol"] == "IF2612"
    assert len(calls) == 1
    api.ack_event_batch(exchange, token, batch)


def test_execution_session_is_rejected_before_queue_claim_and_after_claim(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    source = api.data_queues[EXCHANGE]
    source.put({"kind": "tick", "symbol": "held"})
    api._execution_session = object()
    with pytest.raises(CapabilityNotSupportedError, match="unavailable with an execution session"):
        api.register_event_consumer(EXCHANGE)
    assert source.qsize() == 1

    api._execution_session = None
    token = api.register_event_consumer(EXCHANGE)
    with pytest.raises(CapabilityNotSupportedError, match="after event batch ownership"):
        api.configure_execution({})
    assert source.qsize() == 1
    assert api.poll_event_batch(EXCHANGE, token) is not None


def test_configuration_in_progress_and_consumer_claim_are_mutually_exclusive(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    source = api.data_queues[EXCHANGE]
    source.put({"kind": "tick", "symbol": "held-during-config"})
    configuration_entered = threading.Event()
    continue_configuration = threading.Event()
    configuration_errors = []
    session_module = bt_api_module.importlib.import_module("bt_api_py._execution_session")

    def paused_session_config(_config):
        configuration_entered.set()
        if not continue_configuration.wait(timeout=2):
            raise RuntimeError("configuration barrier timed out")
        raise RuntimeError("synthetic failure before session installation")

    monkeypatch.setattr(session_module, "session_config", paused_session_config)

    def configure() -> None:
        try:
            api.configure_execution({})
        except Exception as exc:
            configuration_errors.append(exc)

    configurator = threading.Thread(target=configure, daemon=True)
    configurator.start()
    assert configuration_entered.wait(timeout=2)
    assert api._execution_configuration_in_progress is True

    registration_error = None
    try:
        api.register_event_consumer(EXCHANGE)
    except Exception as exc:
        registration_error = exc
    finally:
        continue_configuration.set()
    configurator.join(timeout=2)

    assert not configurator.is_alive()
    assert len(configuration_errors) == 1
    assert isinstance(configuration_errors[0], RuntimeError)
    assert isinstance(registration_error, CapabilityNotSupportedError)
    assert "execution configuration is in progress" in str(registration_error)
    assert api._execution_configuration_in_progress is False
    assert api._execution_session is None
    assert source.qsize() == 1

    token = api.register_event_consumer(EXCHANGE)
    batch = api.poll_event_batch(EXCHANGE, token)
    assert batch is not None and batch.events[0]["symbol"] == "held-during-config"
    api.ack_event_batch(EXCHANGE, token, batch)


@pytest.mark.parametrize("close_fails", [False, True])
def test_close_does_not_discard_outstanding_batch(monkeypatch, close_fails) -> None:
    api = _new_api(monkeypatch)
    api.data_queues[EXCHANGE].put({"kind": "tick", "symbol": "held-through-close"})
    token = api.register_event_consumer(EXCHANGE)
    batch = api.poll_event_batch(EXCHANGE, token)
    assert batch is not None

    def close_transports():
        if close_fails:
            raise RuntimeError("synthetic close failure")

    monkeypatch.setattr(api, "_close_transports", close_transports)
    if close_fails:
        with pytest.raises(RuntimeError, match="synthetic close failure"):
            api.close()
    else:
        api.close()
    assert api.poll_event_batch(EXCHANGE, token) is batch
    api.ack_event_batch(EXCHANGE, token, batch)


def test_unregister_requires_successful_close_and_drained_lanes(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    token = api.register_event_consumer(EXCHANGE)
    with pytest.raises(EventBatchHandoffError, match="event_consumer_backlog_pending"):
        api.unregister_event_consumer(EXCHANGE, token)
    monkeypatch.setattr(api, "_close_transports", lambda: None)
    api.close()
    api.unregister_event_consumer(EXCHANGE, token)
    with pytest.raises(EventBatchHandoffError, match="event_consumer_retired"):
        api.poll_event_batch(EXCHANGE, token)


def test_unregister_racing_normalizer_failure_keeps_pending_input_recoverable(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    source = api.data_queues[EXCHANGE]
    source.put({"kind": "tick", "symbol": "normalizer-race"})
    token = api.register_event_consumer(EXCHANGE)
    api.close()

    normalizer_entered = threading.Event()
    release_normalizer = threading.Event()
    retire_entered = threading.Event()
    poll_errors = []
    unregister_errors = []

    def paused_failure(_item, _exchange_name, **_kwargs):
        normalizer_entered.set()
        if not release_normalizer.wait(timeout=2):
            raise RuntimeError("normalizer barrier timed out")
        raise ValueError("synthetic normalize failure")

    _normalizer(monkeypatch, paused_failure)

    def poll() -> None:
        try:
            api.poll_event_batch(EXCHANGE, token)
        except Exception as exc:
            poll_errors.append(exc)

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()
    assert normalizer_entered.wait(timeout=2)
    assert source.qsize() == 0
    assert not api._normalized_event_pending[EXCHANGE]

    handoff = api._event_batch_handoffs[EXCHANGE]
    original_retire = handoff.retire

    def observe_retire(consumer_token, **kwargs):
        # This is reached only after unregister's earlier checks. The poller
        # still owns handoff._lock while blocked inside the normalizer.
        retire_entered.set()
        return original_retire(consumer_token, **kwargs)

    handoff.retire = observe_retire

    def unregister() -> None:
        try:
            api.unregister_event_consumer(EXCHANGE, token)
        except Exception as exc:
            unregister_errors.append(exc)

    unregister_thread = threading.Thread(target=unregister, daemon=True)
    unregister_thread.start()
    assert retire_entered.wait(timeout=2)
    release_normalizer.set()
    poller.join(timeout=2)
    unregister_thread.join(timeout=2)

    assert not poller.is_alive() and not unregister_thread.is_alive()
    assert len(poll_errors) == 1 and isinstance(poll_errors[0], NormalizedApiError)
    assert len(unregister_errors) == 1
    assert isinstance(unregister_errors[0], EventBatchHandoffError)
    assert unregister_errors[0].code == "event_consumer_backlog_pending"
    assert not handoff.retired
    retained = api._normalized_event_pending[EXCHANGE][0]
    assert type(retained) is PendingEventInput
    assert retained.item["symbol"] == "normalizer-race"

    _normalizer(
        monkeypatch,
        lambda item, exchange_name, **_kwargs: {
            "kind": item["kind"],
            "symbol": item["symbol"],
            "exchange_name": exchange_name,
        },
    )
    batch = api.poll_event_batch(EXCHANGE, token)
    assert batch is not None and batch.events[0]["symbol"] == "normalizer-race"
    api.ack_event_batch(EXCHANGE, token, batch)


def test_ingress_put_racing_retirement_is_never_stranded(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    token = api.register_event_consumer(EXCHANGE)
    api.close()
    source = api.data_queues[EXCHANGE]
    handoff = api._event_batch_handoffs[EXCHANGE]
    original_retire_ingress = source._retire_consumer
    retire_ingress_entered = threading.Event()
    release_retire_ingress = threading.Event()
    unregister_errors = []

    def pause_ingress_retire(consumer_token, **kwargs):
        retire_ingress_entered.set()
        if not release_retire_ingress.wait(timeout=2):
            raise RuntimeError("retire barrier timed out")
        return original_retire_ingress(consumer_token, **kwargs)

    source._retire_consumer = pause_ingress_retire

    def unregister() -> None:
        try:
            api.unregister_event_consumer(EXCHANGE, token)
        except Exception as exc:
            unregister_errors.append(exc)

    unregister_thread = threading.Thread(target=unregister, daemon=True)
    unregister_thread.start()
    assert retire_ingress_entered.wait(timeout=2)
    item = {"kind": "tick", "symbol": "producer-race"}
    source.put(item)
    release_retire_ingress.set()
    unregister_thread.join(timeout=2)

    assert not unregister_thread.is_alive()
    assert len(unregister_errors) == 1
    assert isinstance(unregister_errors[0], EventBatchHandoffError)
    assert unregister_errors[0].code == "event_consumer_backlog_pending"
    assert source.qsize() == 1
    batch = api.poll_event_batch(EXCHANGE, token)
    assert batch is not None and batch.events[0]["symbol"] == "producer-race"
    api.ack_event_batch(EXCHANGE, token, batch)

    other = _new_api(monkeypatch)
    other_token = other.register_event_consumer(EXCHANGE)
    other.close()
    other.unregister_event_consumer(EXCHANGE, other_token)
    emitted = []
    monkeypatch.setattr(other.event_bus, "emit", lambda *args: emitted.append(args))
    with pytest.raises(EventBatchHandoffError, match="event_ingress_retired"):
        other.put_ticker({"kind": "tick", "symbol": "late-ticker"}, EXCHANGE)
    assert emitted == []
    with pytest.raises(EventBatchHandoffError, match="event_ingress_retired"):
        other.data_queues[EXCHANGE].put({"kind": "tick", "symbol": "late-producer"})
    assert other.data_queues[EXCHANGE].qsize() == 0
    with pytest.raises(EventBatchHandoffError, match="event_ingress_retired"):
        other._append_normalized_event_pending(
            EXCHANGE, {"kind": "tick", "symbol": "late-normalized-producer"}
        )
    assert not other._normalized_event_pending[EXCHANGE]


def test_normalized_private_producer_is_serialized_with_retirement(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    token = api.register_event_consumer(EXCHANGE)
    api.close()
    handoff = api._event_batch_handoffs[EXCHANGE]
    original_append = handoff._append_pending
    append_entered = threading.Event()
    release_append = threading.Event()
    producer_errors = []

    def pause_append(pending, item):
        append_entered.set()
        if not release_append.wait(timeout=2):
            raise RuntimeError("normalized producer barrier timed out")
        return original_append(pending, item)

    handoff._append_pending = pause_append

    def publish() -> None:
        try:
            api._publish_private_event(
                EXCHANGE,
                {"kind": "tick", "symbol": "normalized-after-retire"},
                normalized=True,
            )
        except Exception as exc:
            producer_errors.append(exc)

    producer = threading.Thread(target=publish, daemon=True)
    producer.start()
    assert append_entered.wait(timeout=2)
    # The producer has selected the real non-CTP normalized path but has not
    # acquired handoff._lock. Retirement can win and must make its later append
    # fail visibly instead of landing in a permanently unread pending deque.
    api.unregister_event_consumer(EXCHANGE, token)
    release_append.set()
    producer.join(timeout=2)

    assert not producer.is_alive()
    assert len(producer_errors) == 1
    assert isinstance(producer_errors[0], EventBatchHandoffError)
    assert producer_errors[0].code == "event_ingress_retired"
    assert not api._normalized_event_pending[EXCHANGE]

    # If normalized publication wins the handoff lock first, retirement must
    # observe the pending event and reject until it is delivered and ACKed.
    other = _new_api(monkeypatch)
    other_token = other.register_event_consumer(EXCHANGE)
    other.close()
    other._publish_private_event(
        EXCHANGE,
        {"kind": "tick", "symbol": "normalized-before-retire"},
        normalized=True,
    )
    with pytest.raises(EventBatchHandoffError, match="event_consumer_backlog_pending"):
        other.unregister_event_consumer(EXCHANGE, other_token)
    batch = other.poll_event_batch(EXCHANGE, other_token)
    assert batch is not None and batch.events[0]["symbol"] == "normalized-before-retire"
    other.ack_event_batch(EXCHANGE, other_token, batch)
    other.unregister_event_consumer(EXCHANGE, other_token)


def test_put_ticker_reservation_is_serialized_with_retirement(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    token = api.register_event_consumer(EXCHANGE)
    api.close()
    source = api.data_queues[EXCHANGE]
    original_retire_ingress = source._retire_consumer
    retire_ingress_entered = threading.Event()
    release_retire_ingress = threading.Event()
    unregister_errors = []
    bus_events = []
    monkeypatch.setattr(api.event_bus, "emit", lambda *args: bus_events.append(args))

    def pause_ingress_retire(consumer_token, **kwargs):
        retire_ingress_entered.set()
        if not release_retire_ingress.wait(timeout=2):
            raise RuntimeError("put_ticker retire barrier timed out")
        return original_retire_ingress(consumer_token, **kwargs)

    source._retire_consumer = pause_ingress_retire

    def unregister() -> None:
        try:
            api.unregister_event_consumer(EXCHANGE, token)
        except Exception as exc:
            unregister_errors.append(exc)

    unregister_thread = threading.Thread(target=unregister, daemon=True)
    unregister_thread.start()
    assert retire_ingress_entered.wait(timeout=2)
    ticker = {"kind": "tick", "symbol": "public-put-ticker"}
    assert api.put_ticker(ticker, EXCHANGE) is ticker
    release_retire_ingress.set()
    unregister_thread.join(timeout=2)

    assert not unregister_thread.is_alive()
    assert len(unregister_errors) == 1
    assert isinstance(unregister_errors[0], EventBatchHandoffError)
    assert unregister_errors[0].code == "event_consumer_backlog_pending"
    assert bus_events == [("ticker", ticker)]
    assert source.qsize() == 1
    batch = api.poll_event_batch(EXCHANGE, token)
    assert batch is not None and batch.events[0]["symbol"] == "public-put-ticker"
    api.ack_event_batch(EXCHANGE, token, batch)
