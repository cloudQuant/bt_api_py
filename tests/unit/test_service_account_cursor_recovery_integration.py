"""GW03 loopback recovery across a compacted account-event cursor."""

from __future__ import annotations

import multiprocessing
import sqlite3
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_gateway import GatewayEventJournal

from bt_api_py.service_account_consumer import ServiceAccountConsumer
from bt_api_py.service_runtime import ClientCurveKeys, ZmqTransportFactory
from tests.unit.test_service_account_consumer_integration import (
    _ACCOUNT_REF,
    _ACTOR_A,
    _CLIENT_PRINCIPAL,
    _JOURNAL_ACCOUNT_SCOPE,
    _SECURITY_REF,
    _SERVICE_ID,
    _SERVICE_PRINCIPAL,
    _SHARED_FACT_SCOPE,
    _available_tcp_endpoint,
    _await_host,
    _cleanup_host,
    _client_config,
    _close_host_normally,
    _close_runtime,
    _direct_call,
    _new_runtime,
    _progress,
    _seed_journal,
    _spawn_host,
)

_PROJECTION_VERSION = "gw03-expired-cursor-v1"
_INITIAL_STATE: dict[str, Any] = {"last_sequence": 0, "event_ids": []}


def _snapshot_reducer(_state: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    payload = snapshot.get("payload")
    if (
        not isinstance(payload, Mapping)
        or type(payload.get("last_sequence")) is not int
        or not isinstance(payload.get("event_ids"), (list, tuple))
    ):
        raise AssertionError("invalid synthetic snapshot")
    return {
        "last_sequence": payload["last_sequence"],
        "event_ids": list(payload["event_ids"]),
    }


def _event_reducer(state: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event["payload"]
    if not isinstance(payload, Mapping):
        raise AssertionError("invalid synthetic event payload")
    sequence = payload["sequence"]
    if type(sequence) is not int or sequence != state["last_sequence"] + 1:
        raise AssertionError("consumer observed a gap or out-of-order event")
    return {
        "last_sequence": sequence,
        "event_ids": [*state["event_ids"], event["event_id"]],
    }


def test_curve_consumer_rebuilds_expired_cursor_from_snapshot_and_tail(tmp_path) -> None:
    context = multiprocessing.get_context("spawn")
    endpoint = _available_tcp_endpoint()
    registry_path = str(tmp_path / "service-registry.sqlite3")
    journal_path = str(tmp_path / "account-journal.sqlite3")
    projection_path = str(tmp_path / "account-projection.sqlite3")
    _seed_journal(journal_path)
    journal = GatewayEventJournal(journal_path, clock=lambda: 1_800_000_100.0)
    journal.append(
        "journal:unrelated-account",
        "journal:unrelated-strategy",
        "unrelated-account-event",
        "position_update",
        {"sequence": 1, "source": "synthetic-decoy"},
        observed_at=1_800_000_001.0,
    )

    server_keys = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    client_keys = ClientCurveKeys(server_keys[0], client_public, client_secret)
    host_record = None
    runtime = None
    consumer = None
    direct_transport = None
    cleanup_failures: list[str] = []
    try:
        host_record = _spawn_host(
            context, endpoint, registry_path, journal_path, server_keys, client_public
        )
        assert _await_host(host_record) == endpoint

        runtime = _new_runtime(endpoint, client_keys, _ACTOR_A)
        runtime.start()
        consumer = ServiceAccountConsumer(
            runtime,
            "store.account",
            account_ref=_ACCOUNT_REF,
            path=projection_path,
            projection_version=_PROJECTION_VERSION,
            initial_state=_INITIAL_STATE,
            expected_account_scope=_JOURNAL_ACCOUNT_SCOPE,
            expected_strategy_scope=_SHARED_FACT_SCOPE,
            snapshot_reducer=_snapshot_reducer,
            event_reducer=_event_reducer,
            limit=1,
        )

        first = consumer.poll()
        assert first.stream_epoch and first.cursor == 1 and first.pending_ack is False
        assert first.projection == {
            "last_sequence": 1,
            "event_ids": ("consumer-event-1",),
        }
        first_progress = _progress(runtime.call("store.account", "account.progress", {}))
        assert first_progress == {
            "stream_epoch": first.stream_epoch,
            "offered_cursor": 1,
            "acknowledged_cursor": 1,
        }

        # Offer sequence 2 through the real authenticated service while
        # leaving the durable consumer projection at sequence 1.
        offered_second = runtime.call(
            "store.account",
            "account.read_after",
            {
                "cursor": 1,
                "stream_epoch": first.stream_epoch,
                "limit": 1,
                "track_delivery": True,
            },
        )
        assert [event["event_id"] for event in offered_second["events"]] == ["consumer-event-2"]
        offered_progress = _progress(offered_second)
        assert offered_progress == {
            "stream_epoch": first.stream_epoch,
            "offered_cursor": 2,
            "acknowledged_cursor": 1,
        }
        assert consumer.checkpoint().cursor == 1

        # The actual journal head and server-offered cursor are both 2. An
        # authenticated ACK beyond that head must be rejected without moving
        # the durable progress row.
        binding = runtime.bindings[0]
        service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
        profile = _client_config(endpoint, _ACTOR_A).communication["profiles"]["client-curve-v1"]
        direct_transport = ZmqTransportFactory({_SECURITY_REF: client_keys})(
            profile, service_identity
        )
        rejected_ack = _direct_call(
            direct_transport,
            runtime.identity,
            binding.scope,
            binding,
            "account.ack",
            {"stream_epoch": first.stream_epoch, "cursor": 3},
        )
        assert rejected_ack.payload["ok"] is False
        assert rejected_ack.payload["code"] == "HANDLER_FAILED"
        assert _progress(runtime.call("store.account", "account.progress", {})) == offered_progress

        consumer.close()
        consumer = None
        direct_transport.close()
        direct_transport = None
        _close_runtime(runtime, cleanup_failures)
        runtime = None
        assert cleanup_failures == []

        # Reopen the projection and register a fresh runtime before compaction.
        # The local cursor remains at 1 even though the server previously
        # offered sequence 2, so the next read genuinely has an expired cursor.
        runtime = _new_runtime(endpoint, client_keys, _ACTOR_A)
        runtime.start()
        consumer = ServiceAccountConsumer(
            runtime,
            "store.account",
            account_ref=_ACCOUNT_REF,
            path=projection_path,
            projection_version=_PROJECTION_VERSION,
            initial_state=_INITIAL_STATE,
            expected_account_scope=_JOURNAL_ACCOUNT_SCOPE,
            expected_strategy_scope=_SHARED_FACT_SCOPE,
            snapshot_reducer=_snapshot_reducer,
            event_reducer=_event_reducer,
            limit=1,
        )
        reopened = consumer.checkpoint()
        assert reopened.stream_epoch == first.stream_epoch
        assert reopened.cursor == 1 and reopened.pending_ack is False
        assert _progress(runtime.call("store.account", "account.progress", {})) == offered_progress

        # Compact through sequence 2 while the durable consumer cursor is 1,
        # then append sequence 3. Its next authenticated read must apply the
        # snapshot boundary and the contiguous tail in one local SQLite commit.
        snapshot = journal.capture_snapshot(
            _JOURNAL_ACCOUNT_SCOPE,
            _SHARED_FACT_SCOPE,
            "snapshot-through-two",
            expected_cursor=2,
            payload={
                "last_sequence": 2,
                "event_ids": ["consumer-event-1", "consumer-event-2"],
            },
            captured_at=1_800_000_100.0,
        )
        tail = journal.append(
            _JOURNAL_ACCOUNT_SCOPE,
            _SHARED_FACT_SCOPE,
            "consumer-event-3",
            "position_update",
            {"sequence": 3, "source": "synthetic"},
            observed_at=1_800_000_103.0,
        )
        assert snapshot.cursor == 2
        assert tail.sequence == 3

        recovered = consumer.poll()
        assert recovered.stream_epoch == first.stream_epoch
        assert recovered.cursor == 3 and recovered.pending_ack is False
        assert recovered.projection == {
            "last_sequence": 3,
            "event_ids": (
                "consumer-event-1",
                "consumer-event-2",
                "consumer-event-3",
            ),
        }
        final_progress = _progress(runtime.call("store.account", "account.progress", {}))
        assert final_progress == {
            "stream_epoch": first.stream_epoch,
            "offered_cursor": 3,
            "acknowledged_cursor": 3,
        }

        with sqlite3.connect(journal_path) as connection:
            stored_snapshot = connection.execute(
                """
                SELECT snapshot_id, cursor FROM gateway_event_snapshots
                WHERE account_scope=? AND strategy_scope=?
                """,
                (_JOURNAL_ACCOUNT_SCOPE, _SHARED_FACT_SCOPE),
            ).fetchone()
            retained_events = connection.execute(
                """
                SELECT sequence, event_id FROM gateway_events
                WHERE account_scope=? AND strategy_scope=? ORDER BY sequence
                """,
                (_JOURNAL_ACCOUNT_SCOPE, _SHARED_FACT_SCOPE),
            ).fetchall()
            unrelated_events = connection.execute(
                "SELECT event_id FROM gateway_events WHERE account_scope=? ORDER BY sequence",
                ("journal:unrelated-account",),
            ).fetchall()
            service_progress = connection.execute(
                """
                SELECT offered_cursor, acknowledged_cursor FROM gateway_consumer_progress
                WHERE principal_id=? AND account_scope=? AND strategy_scope=?
                """,
                (_CLIENT_PRINCIPAL, _JOURNAL_ACCOUNT_SCOPE, _SHARED_FACT_SCOPE),
            ).fetchone()
        assert stored_snapshot == ("snapshot-through-two", 2)
        assert retained_events == [(3, "consumer-event-3")]
        assert unrelated_events == [("unrelated-account-event",)]
        assert service_progress == (3, 3)

        consumer.close()
        consumer = None
        _close_runtime(runtime, cleanup_failures)
        runtime = None
        assert cleanup_failures == []
        _close_host_normally(host_record)
        host_record = None
    finally:
        if direct_transport is not None:
            with suppress(Exception):
                direct_transport.close()
        if consumer is not None:
            with suppress(Exception):
                consumer.close()
        if runtime is not None:
            _close_runtime(runtime, cleanup_failures)
        if host_record is not None:
            _cleanup_host(host_record, cleanup_failures)
        if cleanup_failures:
            raise AssertionError(f"integration cleanup failed: {tuple(cleanup_failures)}")
