"""Real spawned CURVE delivery of a durable outbox across ingress restart.

The second local acknowledgement failure is injected at the SQLite outbox API
boundary after the real remote append succeeds. This exercises at-least-once
replay and server deduplication; it does not simulate an OS crash or a lost
network reply.
"""

from __future__ import annotations

import multiprocessing
from contextlib import suppress

import zmq
from bt_api_monitor.durable import (
    DurableOutbox,
    DurableOutboxConsumer,
    OutboxDeliveryError,
    OutboxEvent,
)

from bt_api_py.service_monitor import ServiceMonitorOutboxSink
from tests.unit.test_service_account_integration import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _close_normally,
    _receive,
    _start_process,
)
from tests.unit.test_service_monitor_ingress_integration import (
    _EVENT_TYPE,
    _INGRESS_SCOPE_REF,
    _OUTBOX_SCOPE,
    _endpoint,
    _event,
    _host_process,
    _producer_runtime,
    _server_config,
)

_PRODUCER_ID = "producer-a"
_CONSUMER_SCOPE = "producer-local:synthetic-account"
_LOCAL_EVENT_IDS = ("local-observation-1", "local-observation-2")


def _local_event(event_id: str, value: str) -> OutboxEvent:
    request = _event(event_id, value)
    return OutboxEvent(
        event_id=request["event_id"],
        scope=_CONSUMER_SCOPE,
        event_type=request["event_type"],
        data=request["data"],
        occurred_at=request["occurred_at"],
    )


def _producer_process(control, endpoint, keys, local_path, phase: str) -> None:
    runtime = None
    try:
        runtime = _producer_runtime(endpoint, keys, _PRODUCER_ID)
        identity = runtime.identity
        identity_facts = (
            identity.role.value,
            identity.principal_ref,
            identity.actor_ref,
            identity.delegate_ref,
        )
        client_session_id = runtime.client_session_id
        run_id = runtime.run_id
        outbox = DurableOutbox(local_path)
        sink = ServiceMonitorOutboxSink(
            runtime,
            scope_ref=_INGRESS_SCOPE_REF,
            outbox_scope=_CONSUMER_SCOPE,
        )
        rpc_receipts = []
        actual_call = runtime.call

        def capture_real_receipt(*args, **kwargs):
            reply = actual_call(*args, **kwargs)
            if len(args) >= 2 and args[1] == "events.append":
                rpc_receipts.append(
                    (
                        reply["source_event_id"],
                        reply["event_id"],
                        reply["sequence"],
                    )
                )
            return reply

        # Observe the actual RPC reply while delegating every call unchanged.
        runtime.call = capture_real_receipt
        consumer = DurableOutboxConsumer(
            outbox,
            consumer_id=sink.consumer_id,
            scope=_CONSUMER_SCOPE,
            deliver=sink,
        )

        if phase == "first":
            assert (
                outbox.append_bounded(
                    _local_event(_LOCAL_EVENT_IDS[0], "100.00"),
                    max_events=2,
                    max_bytes=8192,
                ).sequence
                == 1
            )
            assert (
                outbox.append_bounded(
                    _local_event(_LOCAL_EVENT_IDS[1], "200.00"),
                    max_events=2,
                    max_bytes=8192,
                ).sequence
                == 2
            )
            first = consumer.consume(limit=1)
            assert [item.sequence for item in first] == [1]
            assert [receipt[0] for receipt in rpc_receipts] == list(_LOCAL_EVENT_IDS[:1])
            assert rpc_receipts[0][2] == 1
            assert consumer.checkpoint() == 1

            def fail_local_ack(_consumer_id: str, _scope: str, _sequence: int) -> None:
                raise OSError("injected local acknowledgement failure")

            outbox.acknowledge = fail_local_ack
            try:
                try:
                    consumer.consume(limit=1)
                except OutboxDeliveryError as exc:
                    assert type(exc.__cause__) is OSError
                else:
                    raise AssertionError("injected acknowledgement failure was not observed")
            finally:
                del outbox.acknowledge

            assert [receipt[0] for receipt in rpc_receipts] == list(_LOCAL_EVENT_IDS)
            assert rpc_receipts[1][2] == 2
            assert consumer.checkpoint() == 1
            pending = outbox.read_pending(consumer.consumer_id, _CONSUMER_SCOPE, limit=10)
            assert [item.sequence for item in pending] == [2]
        elif phase == "replay":
            assert consumer.checkpoint() == 1
            replayed = consumer.consume(limit=1)
            assert [item.sequence for item in replayed] == [2]
            assert len(rpc_receipts) == 1
            assert rpc_receipts[0][0] == _LOCAL_EVENT_IDS[1]
            assert rpc_receipts[0][2] == 2
            assert consumer.checkpoint() == 2
            assert consumer.consume(limit=1) == []
        else:
            raise AssertionError("unknown producer phase")

        consumer_id = consumer.consumer_id
        runtime.close()
        assert runtime.state == "closed"
        assert runtime.cleanup_failures == 0
        assert runtime.unregister_uncertainties == 0
        runtime = None
        control.send(
            (
                "done",
                phase,
                consumer_id,
                identity_facts,
                client_session_id,
                run_id,
                tuple(rpc_receipts),
            )
        )
        while True:
            if not control.poll(15.0):
                raise AssertionError("producer missed its bounded normal-close command")
            if control.recv() == ("close",):
                control.send(("closed", phase))
                return
            raise AssertionError("unknown producer command")
    except BaseException as exc:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(("error", phase, getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _read_events(path: str):
    outbox = DurableOutbox(path, read_only=True)
    events, has_more = outbox.read_page_types(_OUTBOX_SCOPE, (_EVENT_TYPE,), 0, 10)
    assert has_more is False
    return events


def _assert_remote_events(path: str) -> tuple[str, str]:
    events = _read_events(path)
    assert [item.sequence for item in events] == [1, 2]
    by_source = {item.event.data["source_event_id"]: item for item in events}
    assert set(by_source) == set(_LOCAL_EVENT_IDS)
    assert by_source[_LOCAL_EVENT_IDS[0]].event.event_type == _EVENT_TYPE
    assert by_source[_LOCAL_EVENT_IDS[1]].event.event_type == _EVENT_TYPE
    assert by_source[_LOCAL_EVENT_IDS[0]].event.data["payload"] == {
        "fact": "synthetic-balance",
        "value": "100.00",
    }
    assert by_source[_LOCAL_EVENT_IDS[1]].event.data["payload"] == {
        "fact": "synthetic-balance",
        "value": "200.00",
    }
    producer = by_source[_LOCAL_EVENT_IDS[0]].event.data["producer"]
    assert producer == {
        "role": "service",
        "principal_ref": "synthetic-producer-a",
        "actor_ref": "producer-a-service",
        "delegate_ref": None,
    }
    assert by_source[_LOCAL_EVENT_IDS[1]].event.data["producer"] == producer
    return (
        by_source[_LOCAL_EVENT_IDS[0]].event.event_id,
        by_source[_LOCAL_EVENT_IDS[1]].event.event_id,
    )


def test_spawned_outbox_sink_replays_after_local_ack_failure_and_ingress_restart(
    tmp_path,
) -> None:
    context = multiprocessing.get_context("spawn")
    remote_outbox = str(tmp_path / "monitor-ingress.sqlite3")
    local_outbox = str(tmp_path / "producer-outbox.sqlite3")
    registry_path = str(tmp_path / "ingress-registry.sqlite3")
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    producer_a_public, producer_a_secret = zmq.curve_keypair()
    producer_b_public, producer_b_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "producer_a_public": producer_a_public,
        "producer_a_secret": producer_a_secret,
        "producer_b_public": producer_b_public,
        "producer_b_secret": producer_b_secret,
    }

    host_record = producer_first = producer_replay = None
    forced: list[str] = []
    cleanup_failures: list[str] = []
    consumer_id = None
    first_session = first_run = None
    try:
        host_record = _start_process(
            context,
            _host_process,
            (
                _server_config(ingress=True, endpoint=endpoint),
                registry_path,
                remote_outbox,
                keys,
                True,
            ),
            "monitor-ingress-host-first",
        )
        ready = _receive(host_record["pipe"])
        assert ready[0] == "ready", ready
        endpoint = ready[1]

        producer_first = _start_process(
            context,
            _producer_process,
            (endpoint, keys, local_outbox, "first"),
            "monitor-outbox-producer-first",
        )
        first = _receive(producer_first["pipe"])
        assert first[0:2] == ("done", "first"), first
        _, _, consumer_id, first_identity, first_session, first_run, first_receipts = first
        assert first_identity == (
            "service",
            "synthetic-producer-a",
            "producer-a-service",
            None,
        )
        assert [receipt[0] for receipt in first_receipts] == list(_LOCAL_EVENT_IDS)
        assert [receipt[2] for receipt in first_receipts] == [1, 2]
        assert first_receipts[1][1].startswith("service-event:")
        local = DurableOutbox(local_outbox, read_only=True)
        assert local.checkpoint(consumer_id, _CONSUMER_SCOPE) == 1
        assert [event.sequence for event in local.read_pending(consumer_id, _CONSUMER_SCOPE)] == [2]
        remote_ids_after_first = _assert_remote_events(remote_outbox)
        assert tuple(receipt[1] for receipt in first_receipts) == remote_ids_after_first
        _close_normally(producer_first, expected_tag="closed")
        producer_first = None

        _close_normally(host_record, expected_tag="closed")
        host_record = None

        endpoint = _endpoint()
        host_record = _start_process(
            context,
            _host_process,
            (
                _server_config(ingress=True, endpoint=endpoint),
                registry_path,
                remote_outbox,
                keys,
                True,
            ),
            "monitor-ingress-host-restarted",
        )
        restarted = _receive(host_record["pipe"])
        assert restarted[0] == "ready", restarted
        endpoint = restarted[1]

        producer_replay = _start_process(
            context,
            _producer_process,
            (endpoint, keys, local_outbox, "replay"),
            "monitor-outbox-producer-replay",
        )
        replay = _receive(producer_replay["pipe"])
        assert replay[0:2] == ("done", "replay"), replay
        _, _, replay_consumer_id, replay_identity, replay_session, replay_run, replay_receipts = (
            replay
        )
        assert replay_consumer_id == consumer_id
        assert replay_identity == first_identity
        assert replay_session != first_session
        assert replay_run != first_run
        assert replay_session and replay_run
        assert replay_receipts == (first_receipts[1],)
        _close_normally(producer_replay, expected_tag="closed")
        producer_replay = None

        local = DurableOutbox(local_outbox, read_only=True)
        assert local.checkpoint(consumer_id, _CONSUMER_SCOPE) == 2
        assert local.read_pending(consumer_id, _CONSUMER_SCOPE) == []
        assert _assert_remote_events(remote_outbox) == remote_ids_after_first
        _close_normally(host_record, expected_tag="closed")
        host_record = None
    finally:
        for record in (producer_replay, producer_first, host_record):
            if record is not None:
                _bounded_reap_spawned_process(record, forced, cleanup_failures)
        _assert_no_fallback_cleanup(forced, cleanup_failures)
