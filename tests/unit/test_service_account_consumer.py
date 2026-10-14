from __future__ import annotations

from collections import deque
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_transport_zmq.service_v2 import service_reply_fits

from bt_api_py.service_account_consumer import (
    ServiceAccountConsumer,
    ServiceAccountConsumerError,
)
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import ServiceClientRuntime

_CLIENT = WireIdentity(WireRole.STRATEGY_CLIENT, "consumer-principal", "strategy-a")
_PEER = WireIdentity(WireRole.SERVICE, "consumer-service-principal", "service-a")
_SCOPE = WireScope("paper:consumer", account_ref="account-a", strategy_ref="strategy-a")
_OPERATIONS = frozenset({"account.read_after", "account.ack", "account.progress"})
_EPOCH = "consumer-stream-epoch-a"


class _ConsumerHarness:
    def __init__(self, replies=(), *, fail_ack_count: int = 0):
        self.replies = deque(replies)
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.fail_ack_count = fail_ack_count
        self.stream_epoch = _EPOCH
        self.offered_cursor = 0
        self.acknowledged_cursor = 0
        self.registry = None

    def attach(self, path) -> None:
        self.registry = ServiceRegistry(
            path,
            service_id="service-a",
            capabilities=(ServiceCapability("gateway-a", "service.account_read", _OPERATIONS),),
            grants={
                _CLIENT.principal_ref: (
                    PrincipalGrant(
                        _CLIENT,
                        (
                            ResourceGrant(
                                "gateway-a",
                                "service.account_read",
                                "account-resource-a",
                                _SCOPE,
                                _OPERATIONS,
                            ),
                        ),
                    ),
                )
            },
            policy_version="account-consumer-policy-v1",
            requirements_version="account-consumer-requirements-v1",
            reply_size_checker=lambda request, payload, hint: service_reply_fits(
                _PEER,
                request,
                payload,
                max_message_bytes=min(1_048_576, hint or 1_048_576),
            ),
        )
        self.registry.register_handler("gateway-a", "account.read_after", self._read_after)
        self.registry.register_handler("gateway-a", "account.ack", self._ack)
        self.registry.register_handler("gateway-a", "account.progress", self._progress)

    def _read_after(self, _context, arguments):
        self.calls.append(("account.read_after", dict(arguments)))
        if not self.replies:
            raise RuntimeError("no synthetic page configured")
        reply = self.replies.popleft()
        progress = reply["consumer_progress"]
        self.stream_epoch = progress["stream_epoch"]
        self.offered_cursor = progress["offered_cursor"]
        self.acknowledged_cursor = progress["acknowledged_cursor"]
        return reply

    def _ack(self, _context, arguments):
        self.calls.append(("account.ack", dict(arguments)))
        if self.fail_ack_count:
            self.fail_ack_count -= 1
            raise RuntimeError("synthetic uncertain ack")
        if arguments["stream_epoch"] != self.stream_epoch:
            raise RuntimeError("stream mismatch")
        self.acknowledged_cursor = max(self.acknowledged_cursor, arguments["cursor"])
        return {"consumer_progress": self._progress_value()}

    def _progress(self, _context, arguments):
        self.calls.append(("account.progress", dict(arguments)))
        return {"consumer_progress": self._progress_value()}

    def _progress_value(self):
        return {
            "stream_epoch": self.stream_epoch,
            "offered_cursor": self.offered_cursor,
            "acknowledged_cursor": self.acknowledged_cursor,
        }


class _LocalTransport:
    def __init__(self, harness: _ConsumerHarness):
        self._harness = harness
        self.closed = False

    def request(self, request, *, timeout):
        del timeout
        assert self._harness.registry is not None
        result = self._harness.registry.handle(_CLIENT.principal_ref, request)
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="consumer-reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=_PEER,
            ),
            result,
        )

    def close(self):
        self.closed = True


def _runtime(harness: _ConsumerHarness, run_id: str) -> ServiceClientRuntime:
    config = parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": _CLIENT.actor_ref},
            "clients": {
                "gateway": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "store.account",
                                "service_id": "service-a",
                                "instance_id": "gateway-a",
                                "scope_ref": "account-resource-a",
                                "account_ref": "account-a",
                                "transport_ref": "account-rpc",
                            }
                        ],
                        "options": {"request_timeout_ms": 1000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "account-rpc": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7601"},
                        "security_profile_ref": "synthetic-account-keys",
                    }
                },
            },
        }
    )
    runtime = ServiceClientRuntime(
        config,
        identity=_CLIENT,
        scope_resolver=lambda _scope_ref: WireScope(
            _SCOPE.execution_domain_ref,
            account_ref=_SCOPE.account_ref,
            strategy_ref=_SCOPE.strategy_ref,
            run_id=run_id,
        ),
        peer_resolver=lambda _service_id: _PEER,
        transport_factory=lambda _profile, _peer: _LocalTransport(harness),
        run_id=run_id,
        requested_operations={"store.account": _OPERATIONS},
    )
    runtime.start()
    return runtime


def _snapshot(
    cursor: int,
    *,
    account_scope: str = "journal-account-a",
    strategy_scope: str = "journal-strategy-a",
    value: int = 0,
):
    return {
        "account_scope": account_scope,
        "strategy_scope": strategy_scope,
        "snapshot_id": f"snapshot-{cursor}",
        "cursor": cursor,
        "payload": {"value": value},
        "captured_at": 1.0,
    }


def _event(sequence: int, *, delta: int = 1, event_id: str | None = None):
    return {
        "account_scope": "journal-account-a",
        "strategy_scope": "journal-strategy-a",
        "sequence": sequence,
        "event_id": event_id or f"event-{sequence}",
        "kind": "position.changed",
        "payload": {"delta": delta},
        "observed_at": float(sequence + 1),
    }


def _batch(
    *,
    requested_cursor: int | None,
    snapshot=None,
    events=(),
    next_cursor: int,
    latest_cursor: int,
    offered_cursor: int,
    acknowledged_cursor: int,
    stream_epoch: str = _EPOCH,
    account_scope: str = "journal-account-a",
    strategy_scope: str = "journal-strategy-a",
):
    actual_snapshot = snapshot
    if snapshot is not None and account_scope != "journal-account-a":
        actual_snapshot = {**snapshot, "account_scope": account_scope}
    return {
        "requested_cursor": requested_cursor,
        "snapshot": actual_snapshot,
        "events": list(events),
        "next_cursor": next_cursor,
        "latest_cursor": latest_cursor,
        "recovered_from_snapshot": (
            requested_cursor is not None
            and actual_snapshot is not None
            and actual_snapshot["cursor"] > requested_cursor
        ),
        "consumer_progress": {
            "stream_epoch": stream_epoch,
            "offered_cursor": offered_cursor,
            "acknowledged_cursor": acknowledged_cursor,
        },
    }


def _reducers(calls):
    def snapshot_reducer(_initial, snapshot):
        calls.append(("snapshot", snapshot["snapshot_id"]))
        return {"value": snapshot["payload"]["value"], "event_ids": []}

    def event_reducer(current, event):
        calls.append(("event", event["event_id"]))
        return {
            "value": current["value"] + event["payload"]["delta"],
            "event_ids": [*current["event_ids"], event["event_id"]],
        }

    return snapshot_reducer, event_reducer


def _consumer(runtime, path, reducer_calls, *, initial_state=None):
    snapshot_reducer, event_reducer = _reducers(reducer_calls)
    return ServiceAccountConsumer(
        runtime,
        "store.account",
        account_ref="account-a",
        path=path,
        projection_version="projection-v1",
        initial_state=initial_state or {"value": 0, "event_ids": []},
        expected_account_scope="journal-account-a",
        expected_strategy_scope="journal-strategy-a",
        snapshot_reducer=snapshot_reducer,
        event_reducer=event_reducer,
        limit=10,
    )


def test_ack_failure_keeps_sqlite_commit_and_restart_acks_before_read(tmp_path):
    harness = _ConsumerHarness(
        [
            _batch(
                requested_cursor=None,
                snapshot=_snapshot(0),
                events=[_event(1, delta=7)],
                next_cursor=1,
                latest_cursor=1,
                offered_cursor=1,
                acknowledged_cursor=0,
            ),
            _batch(
                requested_cursor=1,
                events=[],
                next_cursor=1,
                latest_cursor=1,
                offered_cursor=1,
                acknowledged_cursor=1,
            ),
        ],
        fail_ack_count=1,
    )
    harness.attach(tmp_path / "registry.sqlite3")
    path = tmp_path / "projection.sqlite3"
    reducer_calls: list[tuple[str, str]] = []
    first_runtime = _runtime(harness, "run-first")
    first = _consumer(first_runtime, path, reducer_calls)
    try:
        with pytest.raises(ServiceAccountConsumerError, match="ACCOUNT_SERVICE_CALL_FAILED"):
            first.poll()
        committed = first.checkpoint()
        assert committed.stream_epoch == _EPOCH
        assert committed.cursor == 1
        assert committed.pending_ack is True
        assert committed.projection == {"value": 7, "event_ids": ("event-1",)}
        assert [operation for operation, _ in harness.calls] == [
            "account.read_after",
            "account.ack",
        ]
    finally:
        first.close()
        first_runtime.close()

    second_runtime = _runtime(harness, "run-after-restart")
    second = _consumer(second_runtime, path, reducer_calls)
    try:
        resumed = second.poll()
        assert resumed.cursor == 1
        assert resumed.pending_ack is False
        assert resumed.projection == {"value": 7, "event_ids": ("event-1",)}
        assert reducer_calls == [("snapshot", "snapshot-0"), ("event", "event-1")]
        assert [operation for operation, _ in harness.calls] == [
            "account.read_after",
            "account.ack",
            "account.ack",
            "account.read_after",
            "account.ack",
        ]
        assert harness.calls[2][1] == {"stream_epoch": _EPOCH, "cursor": 1}
        assert harness.calls[3][1] == {
            "cursor": 1,
            "limit": 10,
            "track_delivery": True,
            "stream_epoch": _EPOCH,
        }
    finally:
        second.close()
        second_runtime.close()
        harness.registry.close()


def test_remote_ack_ahead_without_local_checkpoint_still_reads_baseline(tmp_path):
    harness = _ConsumerHarness(
        [
            _batch(
                requested_cursor=None,
                snapshot=_snapshot(5, value=20),
                events=[_event(6, delta=2), _event(7, delta=3)],
                next_cursor=7,
                latest_cursor=7,
                offered_cursor=7,
                acknowledged_cursor=6,
            )
        ]
    )
    harness.attach(tmp_path / "registry.sqlite3")
    runtime = _runtime(harness, "run-empty-local-store")
    reducer_calls: list[tuple[str, str]] = []
    consumer = _consumer(runtime, tmp_path / "projection.sqlite3", reducer_calls)
    try:
        checkpoint = consumer.poll()
        assert harness.calls[0] == (
            "account.read_after",
            {"cursor": None, "limit": 10, "track_delivery": True},
        )
        assert all(operation != "account.progress" for operation, _ in harness.calls)
        assert reducer_calls == [
            ("snapshot", "snapshot-5"),
            ("event", "event-6"),
            ("event", "event-7"),
        ]
        assert checkpoint.cursor == 7
        assert checkpoint.pending_ack is False
        assert checkpoint.projection == {"value": 25, "event_ids": ("event-6", "event-7")}
        assert harness.calls[-1] == ("account.ack", {"stream_epoch": _EPOCH, "cursor": 7})
    finally:
        consumer.close()
        runtime.close()
        harness.registry.close()


def test_wrong_scope_and_stream_epoch_fail_without_advancing_local_projection(tmp_path):
    wrong_scope = _batch(
        requested_cursor=None,
        snapshot=_snapshot(0),
        events=[_event(1)],
        next_cursor=1,
        latest_cursor=1,
        offered_cursor=1,
        acknowledged_cursor=0,
        account_scope="another-account-scope",
    )
    harness = _ConsumerHarness([wrong_scope])
    harness.attach(tmp_path / "registry.sqlite3")
    runtime = _runtime(harness, "run-scope-failure")
    consumer = _consumer(runtime, tmp_path / "projection.sqlite3", [])
    try:
        with pytest.raises(ServiceAccountConsumerError, match="PROJECTION_APPLY_FAILED"):
            consumer.poll()
        checkpoint = consumer.checkpoint()
        assert checkpoint.stream_epoch is None
        assert checkpoint.cursor == 0
        assert checkpoint.pending_ack is False
        assert [operation for operation, _ in harness.calls] == ["account.read_after"]
    finally:
        consumer.close()
        runtime.close()
        harness.registry.close()

    first_page = _batch(
        requested_cursor=None,
        snapshot=_snapshot(0),
        events=[_event(1)],
        next_cursor=1,
        latest_cursor=1,
        offered_cursor=1,
        acknowledged_cursor=0,
    )
    wrong_epoch_page = _batch(
        requested_cursor=1,
        events=[],
        next_cursor=1,
        latest_cursor=1,
        offered_cursor=1,
        acknowledged_cursor=1,
        stream_epoch="different-stream-epoch",
    )
    epoch_harness = _ConsumerHarness([first_page, wrong_epoch_page])
    epoch_harness.attach(tmp_path / "epoch-registry.sqlite3")
    epoch_runtime = _runtime(epoch_harness, "run-epoch-failure")
    epoch_consumer = _consumer(epoch_runtime, tmp_path / "epoch-projection.sqlite3", [])
    try:
        assert epoch_consumer.poll().cursor == 1
        before = epoch_consumer.checkpoint()
        with pytest.raises(ServiceAccountConsumerError, match="STREAM_EPOCH_MISMATCH"):
            epoch_consumer.poll()
        after = epoch_consumer.checkpoint()
        assert after == before
        assert [operation for operation, _ in epoch_harness.calls] == [
            "account.read_after",
            "account.ack",
            "account.read_after",
        ]
    finally:
        epoch_consumer.close()
        epoch_runtime.close()
        epoch_harness.registry.close()


def test_identity_reassignment_fails_closed_before_remote_call(tmp_path):
    empty_page = _batch(
        requested_cursor=None,
        snapshot=_snapshot(0),
        events=[],
        next_cursor=0,
        latest_cursor=0,
        offered_cursor=0,
        acknowledged_cursor=0,
    )
    harness = _ConsumerHarness([empty_page])
    harness.attach(tmp_path / "registry.sqlite3")
    runtime = _runtime(harness, "run-identity-change")
    consumer = _consumer(runtime, tmp_path / "projection.sqlite3", [])
    try:
        before = len(harness.calls)
        runtime.identity = WireIdentity(WireRole.STRATEGY_CLIENT, "other-principal", "strategy-a")
        with pytest.raises(ServiceAccountConsumerError, match="ACCOUNT_BINDING_CHANGED"):
            consumer.poll()
        assert len(harness.calls) == before
        runtime.identity = _CLIENT
    finally:
        consumer.close()
        runtime.close()
        harness.registry.close()


def test_duplicate_empty_poll_is_idempotent_and_close_borrows_runtime(tmp_path):
    empty = _batch(
        requested_cursor=None,
        snapshot=_snapshot(0),
        events=[],
        next_cursor=0,
        latest_cursor=0,
        offered_cursor=0,
        acknowledged_cursor=0,
    )
    empty_after_baseline = _batch(
        requested_cursor=0,
        events=[],
        next_cursor=0,
        latest_cursor=0,
        offered_cursor=0,
        acknowledged_cursor=0,
    )
    harness = _ConsumerHarness([empty, empty_after_baseline, empty_after_baseline])
    harness.attach(tmp_path / "registry.sqlite3")
    runtime = _runtime(harness, "run-idle")
    consumer = _consumer(runtime, tmp_path / "projection.sqlite3", [])
    try:
        first = consumer.poll()
        second = consumer.poll()
        third = consumer.poll()
        assert first == second == third
        assert first.cursor == 0 and first.pending_ack is False
        assert [operation for operation, _ in harness.calls] == [
            "account.read_after",
            "account.ack",
            "account.read_after",
            "account.ack",
            "account.read_after",
        ]
        consumer.close()
        consumer.close()
        assert runtime.state == "registered"
        assert runtime.call("store.account", "account.progress", {}, account_ref="account-a") == {
            "consumer_progress": {
                "stream_epoch": _EPOCH,
                "offered_cursor": 0,
                "acknowledged_cursor": 0,
            }
        }
        assert runtime.state == "registered"
    finally:
        consumer.close()
        runtime.close()
        harness.registry.close()
