"""Crash recovery for a tracked, authenticated account projection consumer.

This test intentionally exits one spawned consumer after its real SQLite
projection transaction commits but before the SDK can acknowledge that batch.
The gateway host and journal remain alive while a fresh runtime resumes from
the same projection file.
"""

from __future__ import annotations

import multiprocessing
import os
from contextlib import suppress
from types import MethodType
from typing import Any

import zmq

from bt_api_py.service_account_consumer import ServiceAccountConsumer
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime
from tests.unit.test_service_account_consumer_integration import (
    _ACCOUNT_REF,
    _ACTOR_A,
    _JOURNAL_ACCOUNT_SCOPE,
    _SHARED_FACT_SCOPE,
    _available_tcp_endpoint,
    _await_host,
    _cleanup_host,
    _close_host_normally,
    _close_runtime,
    _new_runtime,
    _progress,
    _seed_journal,
    _spawn_host,
)

_PROJECTION_VERSION = "account-consumer-crash-recovery-v1"
_INITIAL_STATE = {"applied_count": 0, "event_ids": [], "last_sequence": 0}
_EXPECTED_CRASH_EXIT = 73


def _snapshot_reducer(initial_state: dict[str, Any], _snapshot: dict[str, Any]) -> dict[str, Any]:
    """Start a synthetic event projection from the caller's frozen initial state."""

    return {
        "applied_count": int(initial_state["applied_count"]),
        "event_ids": list(initial_state["event_ids"]),
        "last_sequence": int(initial_state["last_sequence"]),
    }


def _event_reducer(state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Record each synthetic event exactly once in the durable projection."""

    payload = event["payload"]
    return {
        "applied_count": int(state["applied_count"]) + 1,
        "event_ids": [*state["event_ids"], event["event_id"]],
        "last_sequence": int(payload["sequence"]),
    }


def _new_consumer(runtime: ServiceClientRuntime, path: str) -> ServiceAccountConsumer:
    return ServiceAccountConsumer(
        runtime,
        "store.account",
        account_ref=_ACCOUNT_REF,
        path=path,
        projection_version=_PROJECTION_VERSION,
        initial_state=_INITIAL_STATE,
        expected_account_scope=_JOURNAL_ACCOUNT_SCOPE,
        expected_strategy_scope=_SHARED_FACT_SCOPE,
        snapshot_reducer=_snapshot_reducer,
        event_reducer=_event_reducer,
        limit=1,
    )


def _crash_after_projection_apply(
    control,
    endpoint: str,
    client_keys: ClientCurveKeys,
    projection_path: str,
) -> None:
    """Run one real poll and crash only after the underlying SQLite commit."""

    runtime = None
    consumer = None
    store_type = None
    original_apply = None
    try:
        runtime = _new_runtime(endpoint, client_keys, _ACTOR_A)
        runtime.start()
        consumer = _new_consumer(runtime, projection_path)
        store_type = type(consumer._store)
        original_apply = store_type.apply

        def apply_then_exit(store, *args, **kwargs):
            original_apply(store, *args, **kwargs)
            os._exit(_EXPECTED_CRASH_EXIT)

        # This patches only the real store class in this spawned child. The
        # original method performs and commits SQLite work before os._exit.
        store_type.apply = apply_then_exit
        control.send(("polling", runtime.state, runtime.bindings[0].scope.run_id))
        consumer.poll()
        if original_apply is not None:
            store_type.apply = original_apply
        control.send(("unexpected_poll_return", None))
    except BaseException as exc:
        code = getattr(exc, "code", type(exc).__name__)
        with suppress(Exception):
            control.send(("error", code))
        raise
    finally:
        if consumer is not None:
            with suppress(BaseException):
                consumer.close()
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.close()


def _checkpoint_mapping(checkpoint) -> dict[str, Any]:
    return {
        "applied_count": checkpoint.projection["applied_count"],
        "event_ids": list(checkpoint.projection["event_ids"]),
        "last_sequence": checkpoint.projection["last_sequence"],
    }


def _close_crash_child(record, failures: list[str]) -> None:
    if record is None or record["closed"]:
        return
    process = record["process"]
    control = record["pipe"]
    if process.is_alive():
        process.join(15)
    if process.is_alive():
        failures.append("consumer_crash_child_required_terminate_fallback")
        process.terminate()
        process.join(5)
    if process.is_alive() and hasattr(process, "kill"):
        failures.append("consumer_crash_child_required_kill_fallback")
        process.kill()
        process.join(5)
    if process.exitcode is None:
        failures.append("consumer_crash_child_remained_alive")
    else:
        process.close()
    with suppress(Exception):
        control.close()
    record["closed"] = True


def test_spawned_curve_consumer_recovers_commit_before_ack(tmp_path) -> None:
    context = multiprocessing.get_context("spawn")
    endpoint = _available_tcp_endpoint()
    registry_path = str(tmp_path / "service-registry.sqlite3")
    journal_path = str(tmp_path / "account-journal.sqlite3")
    projection_path = str(tmp_path / "account-projection.sqlite3")
    _seed_journal(journal_path)

    server_keys = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    client_keys = ClientCurveKeys(server_keys[0], client_public, client_secret)
    host_records = []
    runtimes = []
    crash_record = None
    consumer = None
    cleanup_failures: list[str] = []
    try:
        host = _spawn_host(
            context, endpoint, registry_path, journal_path, server_keys, client_public
        )
        host_records.append(host)
        assert _await_host(host) == endpoint

        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=_crash_after_projection_apply,
            args=(child, endpoint, client_keys, projection_path),
            name="synthetic-account-consumer-crash",
        )
        process.start()
        child.close()
        crash_record = {"process": process, "pipe": parent, "closed": False}
        assert parent.poll(20), "spawned projection consumer did not start its real poll"
        crash_start = parent.recv()
        assert crash_start[:2] == ("polling", "registered")
        crash_run_id = crash_start[2]
        process.join(20)
        assert not process.is_alive(), "projection consumer failed to reach its bounded crash point"
        assert process.exitcode == _EXPECTED_CRASH_EXIT
        process.close()
        parent.close()
        crash_record["closed"] = True

        # Reopen through the real SDK consumer/store, not a copied in-memory
        # representation, and verify the committed projection is still pending.
        runtime = _new_runtime(endpoint, client_keys, _ACTOR_A)
        runtimes.append(runtime)
        runtime.start()
        binding = runtime.bindings[0]
        assert binding.scope.run_id != crash_run_id
        consumer = _new_consumer(runtime, projection_path)
        crashed_checkpoint = consumer.checkpoint()
        assert crashed_checkpoint.stream_epoch
        assert crashed_checkpoint.cursor == 1
        assert crashed_checkpoint.pending_ack is True
        assert _checkpoint_mapping(crashed_checkpoint) == {
            "applied_count": 1,
            "event_ids": ["consumer-event-1"],
            "last_sequence": 1,
        }

        pending_server_progress = _progress(runtime.call("store.account", "account.progress", {}))
        assert pending_server_progress == {
            "stream_epoch": crashed_checkpoint.stream_epoch,
            "offered_cursor": 1,
            "acknowledged_cursor": 0,
        }

        calls = []
        original_call = runtime.call

        def tracked_call(self, mount, operation, arguments, **kwargs):
            if operation in {"account.ack", "account.read_after"}:
                calls.append((operation, dict(arguments)))
            return original_call(mount, operation, arguments, **kwargs)

        runtime.call = MethodType(tracked_call, runtime)
        resumed_checkpoint = consumer.poll()
        assert resumed_checkpoint.stream_epoch == crashed_checkpoint.stream_epoch
        assert resumed_checkpoint.cursor == 2
        assert resumed_checkpoint.pending_ack is False
        assert _checkpoint_mapping(resumed_checkpoint) == {
            "applied_count": 2,
            "event_ids": ["consumer-event-1", "consumer-event-2"],
            "last_sequence": 2,
        }
        assert [operation for operation, _arguments in calls] == [
            "account.ack",
            "account.read_after",
            "account.ack",
        ]
        assert calls[0][1] == {
            "stream_epoch": crashed_checkpoint.stream_epoch,
            "cursor": 1,
        }
        assert calls[1][1]["cursor"] == 1
        assert calls[1][1]["stream_epoch"] == crashed_checkpoint.stream_epoch
        assert calls[1][1]["track_delivery"] is True
        assert calls[2][1] == {
            "stream_epoch": crashed_checkpoint.stream_epoch,
            "cursor": 2,
        }

        final_progress = _progress(runtime.call("store.account", "account.progress", {}))
        assert final_progress == {
            "stream_epoch": crashed_checkpoint.stream_epoch,
            "offered_cursor": 2,
            "acknowledged_cursor": 2,
        }
        retained = runtime.call("store.account", "account.read_after", {"cursor": 0, "limit": 10})
        assert [event["event_id"] for event in retained["events"]] == [
            "consumer-event-1",
            "consumer-event-2",
        ]
        assert host["process"].is_alive()
        consumer.close()
        consumer = None
        runtime.close()
        assert runtime.state == "closed"
        assert runtime.cleanup_failures == 0
        assert runtime.unregister_uncertainties == 0
        _close_host_normally(host)
    finally:
        if consumer is not None:
            with suppress(Exception):
                consumer.close()
        _close_crash_child(crash_record, cleanup_failures)
        for runtime in runtimes:
            _close_runtime(runtime, cleanup_failures)
        for record in host_records:
            _cleanup_host(record, cleanup_failures)
        if cleanup_failures:
            raise AssertionError(
                f"local crash integration cleanup failed: {tuple(cleanup_failures)}"
            )
