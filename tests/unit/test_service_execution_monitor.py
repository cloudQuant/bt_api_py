"""Focused tests for the explicit execution-journal-to-Monitor relay."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_execution import (
    CancelIntent,
    CancelObservation,
    ExecutionScope,
    ManagedCancellationFacade,
    ManagedExecutionFacade,
    OrderIntent,
    ProviderObservation,
    Side,
    SqliteExecutionStore,
)
from bt_api_execution.journal_reader import ExecutionJournalReader

from bt_api_py import service_execution_monitor as relay_module
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_execution_monitor import (
    ExecutionMonitorCheckpoint,
    ServiceExecutionMonitorRelay,
    ServiceExecutionMonitorRelayError,
    execution_monitor_relay_configured,
)
from bt_api_py.service_runtime import ServiceClientRuntime

_PRODUCER = WireIdentity(WireRole.SERVICE, "relay-principal", "execution-monitor")
_PEER = WireIdentity(WireRole.SERVICE, "ingress-principal", "monitor-ingress")
_SCOPE_REF = "monitor-ingress-v1"
_SERVICE_ID = "monitor-ingress"
_INSTANCE_ID = "monitor-ingress-v1"
_REQUESTED = frozenset({"events.append", "telemetry.append"})


class _RemoteOutbox:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.append_attempts: list[dict[str, Any]] = []

    def append(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.append_attempts.append(arguments)
        source_event_id = arguments["event_id"]
        if source_event_id not in self.events:
            digest = hashlib.sha256(source_event_id.encode("utf-8")).hexdigest()
            self.events[source_event_id] = {
                "event_id": "service-event:" + digest,
                "source_event_id": source_event_id,
                "sequence": len(self.events) + 1,
            }
        return dict(self.events[source_event_id])


class _RegistrationIds:
    def __init__(self) -> None:
        self.value = 0

    def next(self) -> int:
        self.value += 1
        return self.value


class _RuntimeRpcLeaf:
    """Fake only the transport leaf; ServiceClientRuntime remains real."""

    def __init__(self, remote: _RemoteOutbox, registration_ids: _RegistrationIds) -> None:
        self.remote = remote
        self.registration_ids = registration_ids
        self.requests = []
        self.closed = False
        self.fail_before_append = False
        self.fail_after_append = False

    def request(self, request, *, timeout):
        assert timeout > 0
        self.requests.append(request)
        operation = request.header.operation
        payload = request.payload
        if operation == "service.discover":
            result = {
                "protocol_version": 2,
                "service_id": _SERVICE_ID,
                "ready": True,
                "capabilities": [
                    {
                        "instance_id": _INSTANCE_ID,
                        "port": "service.telemetry_ingress",
                        "operations": ["events.append", "telemetry.append"],
                    }
                ],
            }
        elif operation == "service.register":
            registration = self.registration_ids.next()
            result = {
                "registration_id": f"registration-{registration}",
                "service_epoch": f"epoch-{registration}",
                "policy_version": "policy-v1",
                "requirements_version": "requirements-v1",
                "operations": payload["operations"],
                "expires_at": time.time() + 300,
                "lease_remaining_seconds": 300.0,
            }
        elif operation == "service.renew":
            result = {
                "registration_id": payload["registration_id"],
                "service_epoch": payload["service_epoch"],
                "policy_version": "policy-v1",
                "requirements_version": "requirements-v1",
                "operations": ["events.append", "telemetry.append"],
                "expires_at": time.time() + 300,
                "lease_remaining_seconds": 300.0,
            }
        elif operation == "service.unregister":
            result = {"unregistered": True, "registration_id": payload["registration_id"]}
        elif operation == "events.append":
            if self.fail_before_append:
                self.fail_before_append = False
                raise TimeoutError("synthetic request interruption")
            result = self.remote.append(dict(payload["arguments"]))
            if self.fail_after_append:
                self.fail_after_append = False
                raise TimeoutError("synthetic response loss")
        else:
            raise AssertionError("unexpected operation")

        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=_PEER,
            ),
            {"ok": True, "result": result},
        )

    def close(self) -> None:
        self.closed = True


def _config(*, include_monitor: bool = True):
    raw: dict[str, Any] = {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "execution-producer",
        "scope_ref": "producer-root-v1",
        "transport_ref": "listener-v1",
        "registration_policy_ref": "producer-policy-v1",
        "service_requirements_ref": "producer-requirements-v1",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "producer-monitor-v1",
                    "bind_ports": ["service.monitor_query"],
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:7700"},
                    "security_profile_ref": "listener-security-v1",
                },
                "upstream-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": "tcp://127.0.0.1:7701"},
                    "security_profile_ref": "upstream-security-v1",
                },
            },
        },
    }
    if include_monitor:
        raw["clients"] = {
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "upstream.telemetry",
                            "service_id": _SERVICE_ID,
                            "instance_id": _INSTANCE_ID,
                            "scope_ref": _SCOPE_REF,
                            "transport_ref": "upstream-v1",
                        }
                    ]
                },
            }
        }
    return parse_service_config(raw)


def _runtime(remote: _RemoteOutbox, *, wire_scope: WireScope | None = None):
    source_scope = wire_scope or WireScope(
        "paper:execution",
        account_ref="acct-a",
        strategy_ref="strategy-a",
    )
    registrations = _RegistrationIds()
    transports: list[_RuntimeRpcLeaf] = []

    def transport_factory(*_args):
        leaf = _RuntimeRpcLeaf(remote, registrations)
        transports.append(leaf)
        return leaf

    runtime = ServiceClientRuntime(
        _config(),
        identity=_PRODUCER,
        scope_resolver=lambda _scope_ref: source_scope,
        peer_resolver=lambda _service_id: _PEER,
        transport_factory=transport_factory,
        run_id="runtime-run",
        requested_operations={"upstream.telemetry": _REQUESTED},
    )
    runtime.start()
    return runtime, transports


def _execution_scope(account: str = "acct-a", strategy: str = "strategy-a") -> ExecutionScope:
    return ExecutionScope("FAKE", "simulation", account, strategy, "20261015")


def _intent(scope: ExecutionScope, intent_id: str) -> OrderIntent:
    return OrderIntent.limit(
        intent_id=intent_id,
        scope=scope,
        signal_id=f"signal.{intent_id}",
        instrument="FAKE-PERP",
        side=Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        metadata_version="metadata.1",
    )


def _build_journal(database_path, *, orders: int = 2, cancellations: int = 1) -> ExecutionScope:
    scope = _execution_scope()
    store = SqliteExecutionStore(database_path)
    try:
        execution = ManagedExecutionFacade(
            store,
            scope,
            writer_id="writer.a",
            allow_unprotected=True,
        )
        for index in range(orders):
            intent = _intent(scope, f"order.{index}")
            execution.submit(
                intent,
                lambda submitted: ProviderObservation.accepted(
                    submitted.intent_id,
                    f"provider.{submitted.intent_id}",
                ),
            )

        cancellation = ManagedCancellationFacade(
            store,
            scope,
            acquire_writer_lease=execution.acquire_writer_lease,
            allow_unprotected=True,
        )
        for index in range(cancellations):
            cancel = CancelIntent(
                cancel_id=f"cancel.{index}",
                scope=scope,
                target_intent_id=f"order.{index}",
                provider_order_id=f"provider.order.{index}",
                metadata_version="metadata.1",
            )
            cancellation.cancel(
                cancel,
                lambda submitted: CancelObservation.cancelled(
                    submitted.cancel_id,
                    submitted.target_intent_id,
                    submitted.provider_order_id,
                ),
            )
    finally:
        store.close()
    return scope


def _make_relay(
    tmp_path, *, remote: _RemoteOutbox | None = None, source_name: str = "source.sqlite"
):
    database_path = tmp_path / source_name
    scope = _build_journal(database_path)
    reader = ExecutionJournalReader(database_path, scope=scope)
    remote = remote or _RemoteOutbox()
    runtime, transports = _runtime(remote)
    relay = ServiceExecutionMonitorRelay(
        runtime,
        reader,
        scope_ref=_SCOPE_REF,
        checkpoint_path=tmp_path / "relay-cursor.sqlite",
    )
    return scope, reader, remote, runtime, transports, relay


def _close(reader, runtime, relay) -> None:
    relay.close()
    reader.close()
    runtime.close()


@pytest.mark.unit
def test_poll_projects_both_streams_to_redacted_lifecycle_metadata(tmp_path) -> None:
    _scope, reader, remote, runtime, _transports, relay = _make_relay(tmp_path)
    try:
        result = relay.poll(limit=8)

        assert result.remote_delivered == result.checkpointed
        assert result.execution_delivered > 0
        assert result.cancellation_delivered > 0
        assert result.total_delivered == result.remote_delivered
        assert relay.checkpoint("execution").sequence > 0
        assert relay.checkpoint("cancellation").sequence > 0
        for attempt in remote.append_attempts:
            assert attempt["event_type"] in {
                "execution.lifecycle",
                "execution.cancellation",
            }
            assert set(attempt["data"]) == {
                "schema",
                "journal_generation",
                "stream",
                "source_sequence",
                "source_event_id",
                "source_created_at_ns",
                "source_scope_key",
                "source_scope",
                "business_event_type",
                "state",
                "intent_id",
                "cancel_id",
                "target_intent_id",
            }
            assert "provider_order_id" not in attempt["data"]
            assert attempt["data"]["schema"] == "sdk.execution_monitor_projection.v2"
    finally:
        _close(reader, runtime, relay)


@pytest.mark.unit
def test_poll_alternates_streams_within_one_total_rpc_budget(tmp_path) -> None:
    database_path = tmp_path / "source.sqlite"
    scope = _build_journal(database_path, orders=3, cancellations=3)
    reader = ExecutionJournalReader(database_path, scope=scope)
    remote = _RemoteOutbox()
    runtime, _transports = _runtime(remote)
    relay = ServiceExecutionMonitorRelay(
        runtime,
        reader,
        scope_ref=_SCOPE_REF,
        checkpoint_path=tmp_path / "relay.sqlite",
    )
    try:
        result = relay.poll(limit=4)
        streams = [attempt["data"]["stream"] for attempt in remote.append_attempts]
        assert result.remote_delivered == 4
        assert streams == ["execution", "cancellation", "execution", "cancellation"]
    finally:
        _close(reader, runtime, relay)


@pytest.mark.unit
def test_poll_rotates_single_rpc_polls_even_after_delivery_failure(tmp_path) -> None:
    database_path = tmp_path / "source.sqlite"
    scope = _build_journal(database_path, orders=3, cancellations=3)
    reader = ExecutionJournalReader(database_path, scope=scope)
    remote = _RemoteOutbox()
    runtime, transports = _runtime(remote)
    relay = ServiceExecutionMonitorRelay(
        runtime,
        reader,
        scope_ref=_SCOPE_REF,
        checkpoint_path=tmp_path / "relay.sqlite",
    )
    transports[0].fail_before_append = True
    try:
        with pytest.raises(ServiceExecutionMonitorRelayError) as error:
            relay.poll(limit=1)
        assert error.value.code == "REMOTE_DELIVERY_FAILED"
        assert error.value.last_poll.remote_delivered == 0

        relay.poll(limit=1)
        relay.poll(limit=1)
        relay.poll(limit=1)
        streams = [
            request.payload["arguments"]["data"]["stream"]
            for request in transports[0].requests
            if request.header.operation == "events.append"
        ]
        assert streams[:4] == ["execution", "cancellation", "execution", "cancellation"]
        assert [attempt["data"]["stream"] for attempt in remote.append_attempts] == [
            "cancellation",
            "execution",
            "cancellation",
        ]
    finally:
        _close(reader, runtime, relay)


@pytest.mark.unit
def test_empty_source_does_not_spend_an_ingress_rpc(tmp_path) -> None:
    database_path = tmp_path / "source.sqlite"
    scope = _build_journal(database_path, orders=0, cancellations=0)
    reader = ExecutionJournalReader(database_path, scope=scope)
    remote = _RemoteOutbox()
    runtime, transports = _runtime(remote)
    relay = ServiceExecutionMonitorRelay(
        runtime,
        reader,
        scope_ref=_SCOPE_REF,
        checkpoint_path=tmp_path / "relay.sqlite",
    )
    try:
        result = relay.poll()
        assert result.remote_delivered == result.checkpointed == 0
        assert not remote.append_attempts
        assert not [
            request
            for request in transports[0].requests
            if request.header.operation == "events.append"
        ]
    finally:
        _close(reader, runtime, relay)


@pytest.mark.unit
def test_remote_interruption_leaves_cursor_unacknowledged(tmp_path) -> None:
    _scope, reader, remote, runtime, transports, relay = _make_relay(tmp_path)
    transports[0].fail_before_append = True
    try:
        with pytest.raises(ServiceExecutionMonitorRelayError) as error:
            relay.poll(limit=1)

        assert error.value.code == "REMOTE_DELIVERY_FAILED"
        assert error.value.last_poll.remote_delivered == 0
        assert error.value.last_poll.checkpointed == 0
        assert relay.checkpoint("execution").sequence == 0
        assert not remote.events
    finally:
        _close(reader, runtime, relay)


@pytest.mark.unit
def test_valid_remote_receipt_then_local_checkpoint_failure_replays_idempotently(
    tmp_path,
    monkeypatch,
) -> None:
    scope, reader, remote, runtime, _transports, relay = _make_relay(tmp_path)
    original_advance = relay_module._advance_checkpoint
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic local disk failure")
        return original_advance(*args, **kwargs)

    monkeypatch.setattr(relay_module, "_advance_checkpoint", fail_once)
    checkpoint_path = tmp_path / "relay-cursor.sqlite"
    source_path = tmp_path / "source.sqlite"
    try:
        with pytest.raises(ServiceExecutionMonitorRelayError) as error:
            relay.poll(limit=1)
        assert error.value.code == "LOCAL_CHECKPOINT_FAILED"
        assert error.value.last_poll.remote_delivered == 1
        assert error.value.last_poll.checkpointed == 0
        assert relay.checkpoint("execution").sequence == 0
        assert len(remote.events) == 1
        first_id = next(iter(remote.events))
    finally:
        relay.close()
        reader.close()

    reopened_reader = ExecutionJournalReader(source_path, scope=scope)
    reopened_relay = ServiceExecutionMonitorRelay(
        runtime,
        reopened_reader,
        scope_ref=_SCOPE_REF,
        checkpoint_path=checkpoint_path,
    )
    monkeypatch.setattr(relay_module, "_advance_checkpoint", original_advance)
    try:
        result = reopened_relay.poll(limit=1)
        assert result.remote_delivered == result.checkpointed == 1
        assert len(remote.events) == 1
        assert [attempt["event_id"] for attempt in remote.append_attempts] == [first_id, first_id]
        assert reopened_relay.checkpoint("execution").sequence > 0
    finally:
        _close(reopened_reader, runtime, reopened_relay)


@pytest.mark.unit
def test_source_row_change_after_remote_receipt_is_not_acknowledged(tmp_path) -> None:
    _scope, reader, remote, runtime, _transports, relay = _make_relay(tmp_path)
    original_sink = relay._sink

    def deliver_then_delete(item) -> None:
        original_sink(item)
        with sqlite3.connect(reader.database_path) as connection:
            connection.execute(
                "DELETE FROM execution_outbox WHERE sequence = ?",
                (item.sequence,),
            )

    relay._sink = deliver_then_delete
    try:
        with pytest.raises(ServiceExecutionMonitorRelayError) as error:
            relay.poll(limit=1)

        assert error.value.code == "SOURCE_CHECKPOINT_DIVERGED"
        assert error.value.last_poll.remote_delivered == 1
        assert error.value.last_poll.checkpointed == 0
        assert relay.checkpoint("execution").sequence == 0
        assert len(remote.events) == 1
    finally:
        _close(reader, runtime, relay)


@pytest.mark.unit
def test_post_commit_response_loss_keeps_unknown_event_pending_for_dedupe(tmp_path) -> None:
    _scope, reader, remote, runtime, transports, relay = _make_relay(tmp_path)
    transports[0].fail_after_append = True
    try:
        with pytest.raises(ServiceExecutionMonitorRelayError) as error:
            relay.poll(limit=1)
        assert error.value.code == "REMOTE_DELIVERY_FAILED"
        assert error.value.last_poll.remote_delivered == 0
        assert relay.checkpoint("execution").sequence == 0
        assert len(remote.events) == 1
        first_event_id = remote.append_attempts[0]["event_id"]
        first_receipt = remote.events[first_event_id]

        cancellation = relay.poll(limit=1)
        assert cancellation.cancellation_delivered == cancellation.checkpointed == 1
        assert relay.checkpoint("execution").sequence == 0
        assert len(remote.events) == 2

        result = relay.poll(limit=1)
        assert result.remote_delivered == result.checkpointed == 1
        assert len(remote.events) == 2
        assert len(remote.append_attempts) == 3
        assert [attempt["event_id"] for attempt in remote.append_attempts] == [
            first_event_id,
            remote.append_attempts[1]["event_id"],
            first_event_id,
        ]
        assert remote.events[first_event_id] == first_receipt
    finally:
        _close(reader, runtime, relay)


@pytest.mark.unit
def test_restart_keeps_cursor_and_new_generation_gets_a_fresh_namespace(tmp_path) -> None:
    scope, reader, remote, runtime, _transports, relay = _make_relay(tmp_path)
    first_generation = reader.generation
    try:
        relay.poll(limit=1)
        first_cursor = relay.checkpoint("execution")
        relay.close()
        reader.close()
    except BaseException:
        runtime.close()
        raise

    restarted_reader = ExecutionJournalReader(tmp_path / "source.sqlite", scope=scope)
    restarted = ServiceExecutionMonitorRelay(
        runtime,
        restarted_reader,
        scope_ref=_SCOPE_REF,
        checkpoint_path=tmp_path / "relay-cursor.sqlite",
    )
    try:
        assert restarted_reader.generation == first_generation
        assert restarted.checkpoint("execution") == first_cursor
        assert restarted.poll(limit=1).remote_delivered == 1

        second_source = tmp_path / "second-source.sqlite"
        second_scope = _build_journal(second_source, orders=1, cancellations=0)
        second_reader = ExecutionJournalReader(second_source, scope=second_scope)
        second_relay = ServiceExecutionMonitorRelay(
            runtime,
            second_reader,
            scope_ref=_SCOPE_REF,
            checkpoint_path=tmp_path / "relay-cursor.sqlite",
        )
        try:
            assert second_reader.generation != first_generation
            assert second_relay.checkpoint("execution").sequence == 0
            assert second_relay.poll(limit=1).remote_delivered == 1
            assert second_relay.consumer_id != restarted.consumer_id
        finally:
            _close(second_reader, runtime, second_relay)
    finally:
        _close(restarted_reader, runtime, restarted)


@pytest.mark.unit
def test_checkpoint_anchor_detects_same_generation_source_mutation(tmp_path) -> None:
    _scope, reader, remote, runtime, _transports, relay = _make_relay(tmp_path)
    try:
        relay.poll(limit=1)
        cursor = relay.checkpoint("execution")
        attempts = len(remote.append_attempts)
        with sqlite3.connect(reader.database_path) as connection:
            connection.execute(
                "UPDATE execution_outbox SET state = 'REJECTED' WHERE sequence = ?",
                (cursor.sequence,),
            )
            connection.commit()

        with pytest.raises(ServiceExecutionMonitorRelayError) as error:
            relay.poll(limit=1)
        assert error.value.code == "SOURCE_CHECKPOINT_DIVERGED"
        assert len(remote.append_attempts) == attempts
        assert relay.checkpoint("execution") == cursor
    finally:
        _close(reader, runtime, relay)


@pytest.mark.unit
def test_checkpoint_cas_accepts_exact_duplicate_and_rejects_stale_or_conflicting_ack(
    tmp_path,
) -> None:
    _scope, reader, remote, runtime, _transports, relay = _make_relay(tmp_path)
    try:
        events = reader.read_page("execution", limit=100).events
        first, second = events[:2]
        namespace = relay._namespaces["execution"]
        path = relay._checkpoint_path
        first_fingerprint = relay_module._event_fingerprint(first)
        second_fingerprint = relay_module._event_fingerprint(second)

        assert (
            relay_module._advance_checkpoint(
                path,
                namespace,
                expected_sequence=0,
                sequence=first.sequence,
                source_event_id=first.event_id,
                fingerprint=first_fingerprint,
            )
            == "advanced"
        )
        assert (
            relay_module._advance_checkpoint(
                path,
                namespace,
                expected_sequence=0,
                sequence=first.sequence,
                source_event_id=first.event_id,
                fingerprint=first_fingerprint,
            )
            == "duplicate"
        )
        assert (
            relay_module._advance_checkpoint(
                path,
                namespace,
                expected_sequence=first.sequence,
                sequence=second.sequence,
                source_event_id=second.event_id,
                fingerprint=second_fingerprint,
            )
            == "advanced"
        )

        with pytest.raises(relay_module._CheckpointFailureError) as error:
            relay_module._advance_checkpoint(
                path,
                namespace,
                expected_sequence=0,
                sequence=first.sequence,
                source_event_id=first.event_id,
                fingerprint=first_fingerprint,
            )
        assert error.value.code == "CHECKPOINT_CONFLICT"

        conflict_event_id = "0" * 32
        if conflict_event_id == second.event_id:
            conflict_event_id = "1" * 32
        with pytest.raises(relay_module._CheckpointFailureError) as error:
            relay_module._advance_checkpoint(
                path,
                namespace,
                expected_sequence=first.sequence,
                sequence=second.sequence,
                source_event_id=conflict_event_id,
                fingerprint=relay_module._sha256("different projection"),
            )
        assert error.value.code == "CHECKPOINT_CONFLICT"
        assert relay.checkpoint("execution") == ExecutionMonitorCheckpoint(
            second.sequence,
            second.event_id,
            second_fingerprint,
        )
        assert not remote.append_attempts
    finally:
        _close(reader, runtime, relay)


@pytest.mark.unit
def test_nonconsecutive_global_sequences_are_valid_for_each_stream(tmp_path) -> None:
    database_path = tmp_path / "source.sqlite"
    scope = _execution_scope()
    other_scope = _execution_scope(strategy="strategy-b")
    store = SqliteExecutionStore(database_path)
    try:
        execution = ManagedExecutionFacade(
            store,
            scope,
            writer_id="writer.a",
            allow_unprotected=True,
        )
        first_intent = _intent(scope, "order.first")
        execution.submit(
            first_intent,
            lambda submitted: ProviderObservation.accepted(
                submitted.intent_id,
                f"provider.{submitted.intent_id}",
            ),
        )
        other_execution = ManagedExecutionFacade(
            store,
            other_scope,
            writer_id="writer.a",
            allow_unprotected=True,
        )
        other_intent = _intent(other_scope, "order.other")
        other_execution.submit(
            other_intent,
            lambda submitted: ProviderObservation.accepted(
                submitted.intent_id,
                f"provider.{submitted.intent_id}",
            ),
        )
        cancellation = ManagedCancellationFacade(
            store,
            scope,
            acquire_writer_lease=execution.acquire_writer_lease,
            allow_unprotected=True,
        )
        cancellation.cancel(
            CancelIntent(
                cancel_id="cancel.first",
                scope=scope,
                target_intent_id=first_intent.intent_id,
                provider_order_id="provider.order.first",
                metadata_version="metadata.1",
            ),
            lambda submitted: CancelObservation.cancelled(
                submitted.cancel_id,
                submitted.target_intent_id,
                submitted.provider_order_id,
            ),
        )
        second_intent = _intent(scope, "order.second")
        execution.submit(
            second_intent,
            lambda submitted: ProviderObservation.accepted(
                submitted.intent_id,
                f"provider.{submitted.intent_id}",
            ),
        )
    finally:
        store.close()

    reader = ExecutionJournalReader(database_path, scope=scope)
    remote = _RemoteOutbox()
    runtime, _transports = _runtime(remote)
    relay = ServiceExecutionMonitorRelay(
        runtime,
        reader,
        scope_ref=_SCOPE_REF,
        checkpoint_path=tmp_path / "relay-cursor.sqlite",
    )
    try:
        execution_events = reader.read_page("execution", limit=100).events
        assert len(execution_events) >= 2
        assert any(
            current.sequence - prior.sequence > 1
            for prior, current in zip(execution_events, execution_events[1:], strict=False)
        )

        result = relay.poll(limit=2)
        assert result.remote_delivered == 2
        assert relay.checkpoint("execution").sequence == execution_events[0].sequence
        assert relay.checkpoint("cancellation").sequence > 0
        assert len(remote.events) == 2
    finally:
        _close(reader, runtime, relay)


@pytest.mark.unit
def test_wrong_thread_and_closed_relay_do_not_issue_rpc(tmp_path) -> None:
    _scope, reader, remote, runtime, transports, relay = _make_relay(tmp_path)
    calls_before = len(transports[0].requests)
    try:
        result = []

        def poll_foreign_thread() -> None:
            try:
                relay.poll()
            except ServiceExecutionMonitorRelayError as error:
                result.append(error.code)

        thread = threading.Thread(target=poll_foreign_thread)
        thread.start()
        thread.join()
        assert result == ["WRONG_OWNER_THREAD"]
        assert len(transports[0].requests) == calls_before

        relay.close()
        with pytest.raises(ServiceExecutionMonitorRelayError) as error:
            relay.poll()
        assert error.value.code == "RELAY_CLOSED"
        assert runtime.state == "registered"
        assert reader.generation
        assert not remote.append_attempts
        assert len(transports[0].requests) == calls_before
    finally:
        reader.close()
        runtime.close()


@pytest.mark.unit
def test_rebind_after_runtime_recovery_keeps_the_same_destination(tmp_path) -> None:
    _scope, reader, remote, runtime, transports, relay = _make_relay(tmp_path)
    original_consumer = relay.consumer_id
    try:
        runtime.recover()
        assert len(transports) == 2
        relay.rebind()
        assert relay.consumer_id == original_consumer
        assert not remote.append_attempts
        assert relay.poll(limit=1).remote_delivered == 1
    finally:
        _close(reader, runtime, relay)


@pytest.mark.unit
def test_projection_keeps_strategy_identity_when_intent_ids_repeat(tmp_path) -> None:
    database_path = tmp_path / "shared-source.sqlite"
    scope_a = _execution_scope(strategy="strategy-a")
    scope_b = _execution_scope(strategy="strategy-b")
    store = SqliteExecutionStore(database_path)
    try:
        for scope in (scope_a, scope_b):
            facade = ManagedExecutionFacade(
                store,
                scope,
                writer_id="writer.shared",
                allow_unprotected=True,
            )
            intent = _intent(scope, "order.shared")
            facade.submit(
                intent,
                lambda submitted: ProviderObservation.accepted(
                    submitted.intent_id,
                    f"provider.{submitted.intent_id}",
                ),
            )
    finally:
        store.close()

    reader_a = ExecutionJournalReader(database_path, scope=scope_a)
    reader_b = ExecutionJournalReader(database_path, scope=scope_b)
    remote = _RemoteOutbox()
    account_scope = WireScope("paper:execution", account_ref="acct-a")
    runtime, _transports = _runtime(remote, wire_scope=account_scope)
    relay_a = ServiceExecutionMonitorRelay(
        runtime,
        reader_a,
        scope_ref=_SCOPE_REF,
        checkpoint_path=tmp_path / "shared-relay.sqlite",
    )
    relay_b = ServiceExecutionMonitorRelay(
        runtime,
        reader_b,
        scope_ref=_SCOPE_REF,
        checkpoint_path=tmp_path / "shared-relay.sqlite",
    )
    try:
        relay_a.poll(limit=1)
        relay_b.poll(limit=1)
        projected = [attempt["data"] for attempt in remote.append_attempts]
        assert {item["source_scope"]["strategy_id"] for item in projected} == {
            "strategy-a",
            "strategy-b",
        }
        assert {item["source_scope"]["account_ref"] for item in projected} == {"acct-a"}
        assert {item["intent_id"] for item in projected} == {"order.shared"}
        assert len({item["source_scope_key"] for item in projected}) == 2
        assert len({attempt["event_id"] for attempt in remote.append_attempts}) == 2
    finally:
        relay_a.close()
        relay_b.close()
        reader_a.close()
        reader_b.close()
        runtime.close()


@pytest.mark.unit
def test_scope_binding_and_checkpoint_database_separation_fail_closed(tmp_path) -> None:
    source_path = tmp_path / "source.sqlite"
    scope = _build_journal(source_path)
    reader = ExecutionJournalReader(source_path, scope=scope)
    remote = _RemoteOutbox()
    mismatched_scope = WireScope(
        "paper:execution",
        account_ref="acct-other",
        strategy_ref="strategy-a",
    )
    runtime, _transports = _runtime(remote, wire_scope=mismatched_scope)
    matching_runtime, _matching_transports = _runtime(_RemoteOutbox())
    try:
        with pytest.raises(ServiceExecutionMonitorRelayError) as error:
            ServiceExecutionMonitorRelay(
                runtime,
                reader,
                scope_ref=_SCOPE_REF,
                checkpoint_path=tmp_path / "should-not-exist.sqlite",
            )
        assert error.value.code == "SCOPE_MISMATCH"
        assert not (tmp_path / "should-not-exist.sqlite").exists()

        with pytest.raises(ServiceExecutionMonitorRelayError) as error:
            ServiceExecutionMonitorRelay(
                matching_runtime,
                reader,
                scope_ref=_SCOPE_REF,
                checkpoint_path=source_path,
            )
        assert error.value.code == "CHECKPOINT_MUST_BE_SEPARATE"
    finally:
        reader.close()
        runtime.close()
        matching_runtime.close()


@pytest.mark.parametrize(
    "wire_scope",
    (
        WireScope("paper:execution", account_refs=("acct-a",)),
        WireScope("paper:execution", account_ref="acct-a", portfolio_ref="portfolio-a"),
        WireScope(
            "paper:execution",
            account_refs=("acct-a",),
            portfolio_ref="portfolio-a",
            group_id="group-a",
        ),
        WireScope(
            "paper:execution",
            account_ref="acct-a",
            portfolio_ref="portfolio-a",
            group_id="group-a",
            leg_id="leg-a",
        ),
        WireScope("paper:execution", account_ref="acct-a", manual_book_ref="book-a"),
    ),
    ids=("account-refs", "portfolio", "group", "leg", "manual-book"),
)
def test_unsupported_ingress_scope_filters_fail_before_checkpoint_io(tmp_path, wire_scope) -> None:
    source_path = tmp_path / "source.sqlite"
    scope = _build_journal(source_path)
    reader = ExecutionJournalReader(source_path, scope=scope)
    remote = _RemoteOutbox()
    runtime, transports = _runtime(remote, wire_scope=wire_scope)
    checkpoint_path = tmp_path / "should-not-exist.sqlite"
    try:
        with pytest.raises(ServiceExecutionMonitorRelayError) as error:
            ServiceExecutionMonitorRelay(
                runtime,
                reader,
                scope_ref=_SCOPE_REF,
                checkpoint_path=checkpoint_path,
            )
        assert error.value.code == "UNSUPPORTED_INGRESS_SCOPE"
        assert not checkpoint_path.exists()
        assert not remote.append_attempts
        assert not [
            request
            for request in transports[0].requests
            if request.header.operation == "events.append"
        ]
    finally:
        reader.close()
        runtime.close()


@pytest.mark.unit
def test_invalid_or_corrupt_checkpoint_is_never_reset(tmp_path) -> None:
    source_path = tmp_path / "source.sqlite"
    scope = _build_journal(source_path)
    reader = ExecutionJournalReader(source_path, scope=scope)
    remote = _RemoteOutbox()
    runtime, _transports = _runtime(remote)
    checkpoint_path = tmp_path / "corrupt.sqlite"
    with sqlite3.connect(checkpoint_path) as connection:
        connection.execute("CREATE TABLE owner_data(value TEXT NOT NULL)")
        connection.execute("INSERT INTO owner_data(value) VALUES ('preserve')")
    original_checkpoint_bytes = checkpoint_path.read_bytes()
    try:
        with pytest.raises(ServiceExecutionMonitorRelayError) as error:
            ServiceExecutionMonitorRelay(
                runtime,
                reader,
                scope_ref=_SCOPE_REF,
                checkpoint_path=checkpoint_path,
            )
        assert error.value.code == "CHECKPOINT_CORRUPT"
        assert checkpoint_path.read_bytes() == original_checkpoint_bytes
        with sqlite3.connect(checkpoint_path) as connection:
            assert connection.execute("SELECT value FROM owner_data").fetchone()[0] == "preserve"
    finally:
        reader.close()
        runtime.close()


@pytest.mark.unit
def test_config_helper_is_pure_and_requires_exact_existing_binding(tmp_path) -> None:
    assert execution_monitor_relay_configured(_config(), scope_ref=_SCOPE_REF) is True
    assert execution_monitor_relay_configured(_config(), scope_ref="other-scope") is False
    assert (
        execution_monitor_relay_configured(
            _config(include_monitor=False),
            scope_ref=_SCOPE_REF,
        )
        is False
    )
    assert not (tmp_path / "never-created.sqlite").exists()


@pytest.mark.unit
def test_repeated_checkpoint_value_is_a_stable_immutable_projection(tmp_path) -> None:
    _scope, reader, _remote, runtime, _transports, relay = _make_relay(tmp_path)
    try:
        assert relay.checkpoint("execution") == ExecutionMonitorCheckpoint()
        assert relay.checkpoint("execution") == ExecutionMonitorCheckpoint(0, None, None)
        with pytest.raises(ServiceExecutionMonitorRelayError) as error:
            relay.checkpoint("unknown")
        assert error.value.code == "INVALID_STREAM"
    finally:
        _close(reader, runtime, relay)
