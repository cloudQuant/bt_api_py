"""Focused tests for the durable read-only account consumer projection."""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from contextlib import suppress
from pathlib import Path
from typing import Any

from bt_api_py.service_account_projection import (
    AccountProjectionError,
    AccountProjectionStore,
)

_EPOCH = "stream-epoch-a"
_BINDING = {
    "expected_account_scope": "account:synthetic:one",
    "expected_strategy_scope": "strategy:alpha",
    "logical_route": "strategy.alpha.account",
    "projector": "account-view-v1",
}
_INITIAL = {"values": ["initial"]}


def _event(sequence: int, value: str, *, event_id: str | None = None) -> dict[str, Any]:
    return {
        "account_scope": _BINDING["expected_account_scope"],
        "strategy_scope": _BINDING["expected_strategy_scope"],
        "sequence": sequence,
        "event_id": event_id or f"event-{sequence}",
        "kind": "account.fact",
        "payload": {"value": value},
        "observed_at": 1_800_000_000.0 + sequence,
    }


def _snapshot(cursor: int, values: list[str]) -> dict[str, Any]:
    return {
        "account_scope": _BINDING["expected_account_scope"],
        "strategy_scope": _BINDING["expected_strategy_scope"],
        "snapshot_id": f"snapshot-{cursor}",
        "cursor": cursor,
        "payload": {"values": values},
        "captured_at": 1_800_000_000.0 + cursor,
    }


def _reply(
    requested_cursor: int | None,
    *,
    events: list[dict[str, Any]] | None = None,
    snapshot: dict[str, Any] | None = None,
    latest_cursor: int | None = None,
    offered_cursor: int | None = None,
    acknowledged_cursor: int = 0,
    stream_epoch: str = _EPOCH,
    recovered_from_snapshot: bool | None = None,
    state_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = [] if events is None else events
    base = snapshot["cursor"] if snapshot is not None else (requested_cursor or 0)
    next_cursor = rows[-1]["sequence"] if rows else base
    latest = next_cursor if latest_cursor is None else latest_cursor
    offered = latest if offered_cursor is None else offered_cursor
    result: dict[str, Any] = {
        "requested_cursor": requested_cursor,
        "snapshot": snapshot,
        "events": rows,
        "next_cursor": next_cursor,
        "latest_cursor": latest,
        "recovered_from_snapshot": (
            snapshot is not None
            and requested_cursor is not None
            and snapshot["cursor"] > requested_cursor
            if recovered_from_snapshot is None
            else recovered_from_snapshot
        ),
        "consumer_progress": {
            "stream_epoch": stream_epoch,
            "offered_cursor": offered,
            "acknowledged_cursor": acknowledged_cursor,
        },
    }
    if state_status is not None:
        result["state_status"] = state_status
    return result


def _snapshot_reducer(_initial: Any, snapshot: Any) -> dict[str, Any]:
    return {"values": list(snapshot["payload"]["values"])}


def _event_reducer(state: Any, event: Any) -> dict[str, Any]:
    return {"values": [*state["values"], event["payload"]["value"]]}


class AccountProjectionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.path = Path(self._temporary.name) / "consumer.sqlite3"
        self.store = self._new_store()
        self.addCleanup(self.store.close)

    def _new_store(
        self,
        *,
        binding: dict[str, Any] | None = None,
        initial_state: dict[str, Any] | None = None,
        path: Path | None = None,
        max_payload_bytes: int = 1_048_576,
    ) -> AccountProjectionStore:
        return AccountProjectionStore(
            self.path if path is None else path,
            binding=_BINDING if binding is None else binding,
            initial_state=_INITIAL if initial_state is None else initial_state,
            max_payload_bytes=max_payload_bytes,
        )

    def _apply(self, reply: dict[str, Any], requested: int | None = None):
        return self.store.apply(
            reply,
            requested_cursor=requested,
            snapshot_reducer=_snapshot_reducer,
            event_reducer=_event_reducer,
        )

    def test_snapshot_and_events_commit_as_one_immutable_checkpoint(self) -> None:
        result = self._apply(
            _reply(
                None,
                snapshot=_snapshot(4, ["baseline"]),
                events=[_event(5, "tail")],
                state_status={
                    "session_generation": "generation-1",
                    "ready": True,
                    "fresh_for_seconds": 2.5,
                },
            )
        )

        self.assertEqual(
            (result.stream_epoch, result.cursor, result.pending_ack), (_EPOCH, 5, True)
        )
        self.assertEqual(result.projection["values"], ("baseline", "tail"))
        with self.assertRaises(TypeError):
            result.projection["new"] = "mutation"
        with self.assertRaises(TypeError):
            result.projection["values"][0] = "mutation"
        self.assertEqual(self.store.checkpoint(), result)

    def test_compaction_snapshot_replaces_projection_from_initial_state(self) -> None:
        self._apply(_reply(None, events=[_event(1, "old-a"), _event(2, "old-b")]))
        self.store.mark_acknowledged(_EPOCH, 2)
        result = self._apply(
            _reply(
                2,
                snapshot=_snapshot(5, ["compacted-baseline"]),
                recovered_from_snapshot=True,
            ),
            requested=2,
        )

        self.assertEqual(result.cursor, 5)
        self.assertEqual(result.projection["values"], ("compacted-baseline",))
        self.assertTrue(result.pending_ack)

    def test_snapshot_only_page_commits_baseline_before_contiguous_tail(self) -> None:
        baseline = self._apply(
            _reply(
                None,
                snapshot=_snapshot(5, ["bounded-baseline"]),
                latest_cursor=7,
                offered_cursor=5,
            )
        )
        self.assertEqual(baseline.cursor, 5)
        self.assertEqual(baseline.projection["values"], ("bounded-baseline",))
        self.assertTrue(baseline.pending_ack)
        tail = _reply(
            5,
            events=[_event(6, "tail-6"), _event(7, "tail-7")],
            acknowledged_cursor=5,
        )
        with self.assertRaisesRegex(AccountProjectionError, "ACK_PENDING"):
            self._apply(tail, requested=5)
        self.store.mark_acknowledged(_EPOCH, 5)
        with self.assertRaisesRegex(AccountProjectionError, "CURSOR_GAP"):
            self._apply(_reply(5, latest_cursor=7), requested=5)
        final = self._apply(tail, requested=5)
        self.assertEqual(final.cursor, 7)
        self.assertEqual(final.projection["values"], ("bounded-baseline", "tail-6", "tail-7"))

    def test_exact_duplicate_is_idempotent_and_excludes_mutable_remote_status(self) -> None:
        calls = 0

        def count_events(state: Any, event: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return _event_reducer(state, event)

        first = _reply(
            None,
            events=[_event(1, "once")],
            state_status={
                "session_generation": "generation-1",
                "ready": True,
                "fresh_for_seconds": 2.0,
            },
        )
        applied = self.store.apply(
            first,
            requested_cursor=None,
            snapshot_reducer=_snapshot_reducer,
            event_reducer=count_events,
        )
        duplicate = _reply(
            None,
            events=[_event(1, "once")],
            latest_cursor=2,
            offered_cursor=2,
            acknowledged_cursor=1,
            state_status={
                "session_generation": "generation-2",
                "ready": False,
                "fresh_for_seconds": 0.0,
            },
        )

        self.assertEqual(
            self.store.apply(
                duplicate,
                requested_cursor=None,
                snapshot_reducer=_snapshot_reducer,
                event_reducer=count_events,
            ),
            applied,
        )
        self.assertEqual(calls, 1)

    def test_duplicate_from_a_different_stream_epoch_is_rejected(self) -> None:
        first = _reply(None, events=[_event(1, "once")])
        self._apply(first)
        other_epoch = _reply(None, events=[_event(1, "once")], stream_epoch="stream-b")

        with self.assertRaisesRegex(AccountProjectionError, "STREAM_EPOCH_MISMATCH"):
            self._apply(other_epoch)
        self.assertEqual(self.store.checkpoint().cursor, 1)

    def test_changed_duplicate_and_new_page_wait_for_ack(self) -> None:
        self._apply(_reply(None, events=[_event(1, "first")]))
        with self.assertRaisesRegex(AccountProjectionError, "DUPLICATE_BATCH_CONFLICT"):
            self._apply(_reply(None, events=[_event(1, "changed")]))
        with self.assertRaisesRegex(AccountProjectionError, "ACK_PENDING"):
            self._apply(_reply(1, events=[_event(2, "second")]), requested=1)

    def test_ack_is_exact_epoch_and_local_cursor_and_is_idempotent(self) -> None:
        self._apply(_reply(None, events=[_event(1, "one")]))
        with self.assertRaisesRegex(AccountProjectionError, "STREAM_EPOCH_MISMATCH"):
            self.store.mark_acknowledged("other-epoch", 1)
        with self.assertRaisesRegex(AccountProjectionError, "ACK_CURSOR_MISMATCH"):
            self.store.mark_acknowledged(_EPOCH, 2)
        with self.assertRaisesRegex(AccountProjectionError, "INVALID_ACK_CURSOR"):
            self.store.mark_acknowledged(_EPOCH, True)
        with self.assertRaisesRegex(AccountProjectionError, "INVALID_ACK_CURSOR"):
            self.store.mark_acknowledged(_EPOCH, None)  # type: ignore[arg-type]

        acknowledged = self.store.mark_acknowledged(_EPOCH, 1)
        self.assertFalse(acknowledged.pending_ack)
        self.assertEqual(self.store.mark_acknowledged(_EPOCH, 1), acknowledged)

    def test_bad_initial_cursor_echo_and_boolean_cursor_are_rejected(self) -> None:
        with self.assertRaisesRegex(AccountProjectionError, "INVALID_REQUEST_CURSOR"):
            self._apply(_reply(0), requested=0)
        bad_echo = _reply(None)
        bad_echo["requested_cursor"] = False
        with self.assertRaisesRegex(AccountProjectionError, "INVALID_REQUEST_CURSOR"):
            self._apply(bad_echo)
        bad_method_cursor = _reply(1)
        with self.assertRaisesRegex(AccountProjectionError, "INVALID_REQUEST_CURSOR"):
            self._apply(bad_method_cursor, requested=True)  # type: ignore[arg-type]

    def test_sequence_and_silent_empty_page_gaps_are_rejected(self) -> None:
        with self.assertRaisesRegex(AccountProjectionError, "CURSOR_GAP"):
            self._apply(_reply(None, events=[_event(2, "starts-late")]))
        with self.assertRaisesRegex(AccountProjectionError, "CURSOR_GAP"):
            self._apply(_reply(None, latest_cursor=1))
        self.assertEqual(self.store.checkpoint().cursor, 0)

    def test_scope_and_duplicate_event_ids_are_rejected(self) -> None:
        wrong_scope = _event(1, "wrong")
        wrong_scope["account_scope"] = "account:other"
        with self.assertRaisesRegex(AccountProjectionError, "INVALID_SCOPE"):
            self._apply(_reply(None, events=[wrong_scope]))
        duplicate_ids = [
            _event(1, "first", event_id="same-id"),
            _event(2, "second", event_id="same-id"),
        ]
        with self.assertRaisesRegex(AccountProjectionError, "INVALID_BATCH"):
            self._apply(_reply(None, events=duplicate_ids))

    def test_reducer_failure_and_invalid_output_roll_back_cursor_and_projection(self) -> None:
        reply = _reply(None, events=[_event(1, "one")])

        def fail(_state: Any, _event_value: Any) -> dict[str, Any]:
            raise RuntimeError("private reducer detail")

        with self.assertRaisesRegex(AccountProjectionError, "REDUCER_FAILED"):
            self.store.apply(
                reply,
                requested_cursor=None,
                snapshot_reducer=_snapshot_reducer,
                event_reducer=fail,
            )
        self.assertEqual(self.store.checkpoint().cursor, 0)
        self.assertEqual(self.store.checkpoint().projection, {"values": ("initial",)})

        def invalid(_state: Any, _event_value: Any) -> Any:
            return ["not-a-mapping"]

        with self.assertRaisesRegex(AccountProjectionError, "INVALID_REDUCER_OUTPUT"):
            self.store.apply(
                reply,
                requested_cursor=None,
                snapshot_reducer=_snapshot_reducer,
                event_reducer=invalid,
            )
        self.assertEqual(self.store.checkpoint().cursor, 0)

    def test_reducer_arguments_are_frozen_and_reentry_is_rejected(self) -> None:
        def reenter(_state: Any, _event_value: Any) -> dict[str, Any]:
            with suppress(AccountProjectionError):
                self.store.checkpoint()
            return {"values": []}

        with self.assertRaisesRegex(AccountProjectionError, "REENTRANT_CALL"):
            self.store.apply(
                _reply(None, events=[_event(1, "one")]),
                requested_cursor=None,
                snapshot_reducer=_snapshot_reducer,
                event_reducer=reenter,
            )
        self.assertEqual(self.store.checkpoint().cursor, 0)

    def test_payload_limit_is_checked_before_reducer_calls(self) -> None:
        limited = self._new_store(
            path=Path(self._temporary.name) / "small.sqlite3", max_payload_bytes=256
        )
        self.addCleanup(limited.close)
        calls = 0

        def count(_state: Any, _event_value: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {"values": []}

        oversized = _reply(None, snapshot=_snapshot(0, ["x" * 512]))
        with self.assertRaises(AccountProjectionError):
            limited.apply(
                oversized,
                requested_cursor=None,
                snapshot_reducer=count,
                event_reducer=count,
            )
        self.assertEqual(calls, 0)
        self.assertEqual(limited.checkpoint().cursor, 0)

    def test_second_handle_reads_committed_state_and_binding_mismatch_fails_closed(self) -> None:
        second = self._new_store()
        self.addCleanup(second.close)
        page = _reply(None, events=[_event(1, "committed")])
        self._apply(page)
        self.assertEqual(second.checkpoint().cursor, 1)
        self.assertEqual(second.checkpoint().projection["values"], ("initial", "committed"))

        def no_replay(_state: Any, _event_value: Any) -> dict[str, Any]:
            self.fail("a second handle reran a committed reducer")

        duplicate = second.apply(
            page,
            requested_cursor=None,
            snapshot_reducer=_snapshot_reducer,
            event_reducer=no_replay,
        )
        self.assertEqual(duplicate.cursor, 1)
        self.assertTrue(duplicate.pending_ack)

        altered_binding = {**_BINDING, "projector": "different-projector"}
        with self.assertRaisesRegex(AccountProjectionError, "STORE_BINDING_MISMATCH"):
            self._new_store(binding=altered_binding)
        with self.assertRaisesRegex(AccountProjectionError, "INITIAL_STATE_MISMATCH"):
            self._new_store(initial_state={"values": ["different"]})
        self.assertEqual(second.checkpoint().cursor, 1)

    def test_checkpoint_and_pending_ack_survive_reopening_sqlite(self) -> None:
        applied = self._apply(_reply(None, events=[_event(1, "durable")]))
        self.store.close()
        reopened = self._new_store()
        self.addCleanup(reopened.close)

        self.assertEqual(reopened.checkpoint(), applied)
        self.assertTrue(reopened.checkpoint().pending_ack)
        acknowledged = reopened.mark_acknowledged(_EPOCH, 1)
        self.assertEqual(acknowledged.cursor, 1)
        self.assertFalse(acknowledged.pending_ack)

    def test_store_rejects_memory_and_unrelated_sqlite_databases(self) -> None:
        with self.assertRaisesRegex(AccountProjectionError, "DURABLE_PATH_REQUIRED"):
            self._new_store(path=Path(":memory:"))
        foreign_path = Path(self._temporary.name) / "foreign.sqlite3"
        connection = sqlite3.connect(foreign_path)
        try:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(AccountProjectionError, "FOREIGN_DATABASE_CONTENT"):
            self._new_store(path=foreign_path)

        view_only_path = Path(self._temporary.name) / "view-only.sqlite3"
        connection = sqlite3.connect(view_only_path)
        try:
            connection.execute("CREATE VIEW unrelated_view AS SELECT 1 AS value")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(AccountProjectionError, "FOREIGN_DATABASE_CONTENT"):
            self._new_store(path=view_only_path)

        empty_existing_path = Path(self._temporary.name) / "empty-existing.sqlite3"
        empty_existing_path.touch()
        with self.assertRaisesRegex(AccountProjectionError, "STORE_CORRUPT"):
            self._new_store(path=empty_existing_path)

    def test_missing_checkpoint_table_and_schema_mismatch_never_reset_state(self) -> None:
        self._apply(_reply(None, events=[_event(1, "persisted")]))
        self.store.close()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("DROP TABLE account_projection_checkpoint")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(AccountProjectionError, "STORE_CORRUPT"):
            self._new_store()

        version_path = Path(self._temporary.name) / "version.sqlite3"
        versioned = self._new_store(path=version_path)
        versioned.close()
        connection = sqlite3.connect(version_path)
        try:
            connection.execute("UPDATE account_projection_checkpoint SET schema_version = 2")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(AccountProjectionError, "STORE_VERSION_MISMATCH"):
            self._new_store(path=version_path)

    def test_thread_ownership_and_close(self) -> None:
        outcomes: list[str] = []

        def wrong_thread() -> None:
            try:
                self.store.checkpoint()
            except AccountProjectionError as error:
                outcomes.append(error.code)

        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join()
        self.assertEqual(outcomes, ["WRONG_THREAD"])
        self.store.close()
        self.store.close()
        with self.assertRaisesRegex(AccountProjectionError, "STORE_CLOSED"):
            self.store.checkpoint()


if __name__ == "__main__":
    unittest.main()
