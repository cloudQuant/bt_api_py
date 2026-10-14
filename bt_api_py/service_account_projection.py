"""Durable local projection for one read-only account consumer.

This store applies already-authorized gateway pages to caller-supplied pure
reducers. It is a receiver checkpoint, not an event ledger or trading state
authority. A committed offer is not proof of network delivery; the caller must
apply a page, acknowledge its exact cursor remotely, then clear ``pending_ack``.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

_TABLE = "account_projection_checkpoint"
_SCHEMA_VERSION = 1
_SQLITE_MAX_INT = (1 << 63) - 1
_MAX_CONFIGURED_PAYLOAD_BYTES = 16 * 1024 * 1024
_MAX_JSON_DEPTH = 64
_MAX_JSON_NODES = 100_000
_MAX_CONTAINER_ITEMS = 20_000
_MAX_INTEGER_BITS = 256
_MAX_IDENTIFIER_BYTES = 256
_MAX_EPOCH_BYTES = 128
_MAX_EVENTS_PER_PAGE = 1000
_BATCH_FIELDS = frozenset(
    {
        "requested_cursor",
        "snapshot",
        "events",
        "next_cursor",
        "latest_cursor",
        "recovered_from_snapshot",
        "consumer_progress",
    }
)
_OPTIONAL_BATCH_FIELDS = frozenset({"state_status"})
_SNAPSHOT_FIELDS = frozenset(
    {"account_scope", "strategy_scope", "snapshot_id", "cursor", "payload", "captured_at"}
)
_EVENT_FIELDS = frozenset(
    {
        "account_scope",
        "strategy_scope",
        "sequence",
        "event_id",
        "kind",
        "payload",
        "observed_at",
    }
)
_PROGRESS_FIELDS = frozenset({"stream_epoch", "offered_cursor", "acknowledged_cursor"})
_STATUS_FIELDS = frozenset({"session_generation", "ready", "fresh_for_seconds"})
_ROW_FIELDS = frozenset(
    {
        "singleton",
        "schema_version",
        "binding_json",
        "initial_state_json",
        "stream_epoch",
        "cursor",
        "projection_json",
        "pending_ack",
        "last_requested_cursor",
        "last_next_cursor",
        "last_batch_fingerprint",
    }
)


class AccountProjectionError(RuntimeError):
    """Finite, data-free error from the local account projection store."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AccountProjectionCheckpoint:
    """Immutable local cursor and JSON projection for one receiver."""

    stream_epoch: str | None
    cursor: int
    projection: Mapping[str, Any]
    pending_ack: bool

    def __post_init__(self) -> None:
        if self.stream_epoch is not None and not _valid_identifier(
            self.stream_epoch, _MAX_EPOCH_BYTES
        ):
            raise ValueError("stream_epoch must be a bounded identifier or None")
        if type(self.cursor) is not int or not 0 <= self.cursor <= _SQLITE_MAX_INT:
            raise ValueError("cursor must be a non-negative SQLite integer")
        if type(self.pending_ack) is not bool:
            raise ValueError("pending_ack must be a bool")
        _text, normalized = _canonical_mapping(
            self.projection,
            max_bytes=_MAX_CONFIGURED_PAYLOAD_BYTES,
            error_code="INVALID_CHECKPOINT",
        )
        object.__setattr__(self, "projection", _freeze(normalized))


@dataclass(frozen=True)
class _StoreRecord:
    checkpoint: AccountProjectionCheckpoint
    last_requested_cursor: int | None
    last_next_cursor: int | None
    last_batch_fingerprint: str | None


@dataclass(frozen=True)
class _ValidatedBatch:
    requested_cursor: int | None
    snapshot: Mapping[str, Any] | None
    events: tuple[Mapping[str, Any], ...]
    next_cursor: int
    latest_cursor: int
    stream_epoch: str
    offered_cursor: int
    acknowledged_cursor: int
    recovered_from_snapshot: bool
    fingerprint: str


class AccountProjectionStore:
    """SQLite-backed projection and cursor for one bound read-only consumer.

    ``binding`` must identify the trusted logical route/projector and include
    ``expected_account_scope`` and ``expected_strategy_scope``. Binding and
    initial-state JSON are canonicalized and pinned on first creation; reopening
    with different values fails closed. No domain package, provider, transport,
    or trading ledger is imported or used here.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        binding: Mapping[str, Any],
        initial_state: Mapping[str, Any],
        max_payload_bytes: int = 1_048_576,
    ) -> None:
        if (
            type(max_payload_bytes) is not int
            or not 1 <= max_payload_bytes <= _MAX_CONFIGURED_PAYLOAD_BYTES
        ):
            raise AccountProjectionError("INVALID_MAX_PAYLOAD_BYTES")
        if not isinstance(path, (str, Path)):
            raise AccountProjectionError("INVALID_PATH")
        raw_path = str(path)
        if not raw_path or raw_path == ":memory:" or raw_path.casefold().startswith("file:"):
            raise AccountProjectionError("DURABLE_PATH_REQUIRED")
        try:
            # Resolve once so working-directory changes cannot redirect a
            # durable consumer checkpoint to a different SQLite file.
            database_path = Path(path).expanduser().resolve(strict=False)
            database_path.parent.mkdir(parents=True, exist_ok=True)
            new_database_file = not database_path.exists()
        except (OSError, TypeError, ValueError):
            raise AccountProjectionError("INVALID_PATH") from None

        binding_json, normalized_binding = _canonical_mapping(
            binding, max_bytes=max_payload_bytes, error_code="INVALID_BINDING"
        )
        account_scope = normalized_binding.get("expected_account_scope")
        strategy_scope = normalized_binding.get("expected_strategy_scope")
        if not _valid_identifier(account_scope, _MAX_IDENTIFIER_BYTES) or not _valid_identifier(
            strategy_scope, _MAX_IDENTIFIER_BYTES
        ):
            raise AccountProjectionError("INVALID_BINDING")
        initial_state_json, normalized_initial = _canonical_mapping(
            initial_state, max_bytes=max_payload_bytes, error_code="INVALID_INITIAL_STATE"
        )

        self._path = database_path
        self._new_database_file = new_database_file
        self._binding_json = binding_json
        self._initial_state_json = initial_state_json
        self._binding = _freeze(normalized_binding)
        self._initial_state = _freeze(normalized_initial)
        self._max_payload_bytes = max_payload_bytes
        self._owner_thread_id = threading.get_ident()
        self._closed = False
        self._in_callback = False
        self._reentry_violation = False
        self._initialize_database()

    def checkpoint(self) -> AccountProjectionCheckpoint:
        """Read the latest durable checkpoint, including changes by other handles."""

        self._assert_usable()
        try:
            with self._transaction(immediate=False) as connection:
                return self._load_record(connection).checkpoint
        except AccountProjectionError:
            raise
        except sqlite3.Error:
            raise AccountProjectionError("STORE_IO_ERROR") from None

    def apply(
        self,
        reply: Mapping[str, Any],
        *,
        requested_cursor: int | None,
        snapshot_reducer: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
        event_reducer: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
    ) -> AccountProjectionCheckpoint:
        """Atomically apply one exact gateway page and mark it pending ACK.

        Reducers receive recursively immutable JSON mappings and must be pure
        local transformations. A duplicate page is recognized by its last
        canonical data fingerprint and never reruns reducers.
        """

        self._assert_usable()
        if not callable(snapshot_reducer) or not callable(event_reducer):
            raise AccountProjectionError("INVALID_REDUCER")
        request_cursor = _cursor_or_none(requested_cursor, "INVALID_REQUEST_CURSOR")
        try:
            with self._transaction(immediate=True) as connection:
                record = self._load_record(connection)
                validated = self._validate_reply(reply, request_cursor)
                previous = record.checkpoint

                # Epoch binding is checked before duplicate recognition: the
                # data fingerprint intentionally excludes mutable progress, but
                # a copied page from another stream must never be accepted.
                if (
                    previous.stream_epoch is not None
                    and validated.stream_epoch != previous.stream_epoch
                ):
                    raise AccountProjectionError("STREAM_EPOCH_MISMATCH")

                same_request_and_end = (
                    record.last_batch_fingerprint is not None
                    and record.last_requested_cursor == validated.requested_cursor
                    and record.last_next_cursor == validated.next_cursor
                )
                if same_request_and_end:
                    if record.last_batch_fingerprint != validated.fingerprint:
                        raise AccountProjectionError("DUPLICATE_BATCH_CONFLICT")
                    return previous

                if previous.pending_ack:
                    raise AccountProjectionError("ACK_PENDING")
                if previous.stream_epoch is None:
                    if request_cursor is not None:
                        raise AccountProjectionError("INVALID_REQUEST_CURSOR")
                elif request_cursor != previous.cursor:
                    raise AccountProjectionError("CURSOR_MISMATCH")

                base_cursor = self._validate_batch_position(validated, previous)

                if validated.snapshot is not None:
                    projection = self._invoke_reducer(
                        snapshot_reducer, self._initial_state, validated.snapshot
                    )
                else:
                    projection = previous.projection
                for event in validated.events:
                    projection = self._invoke_reducer(event_reducer, projection, event)

                if validated.next_cursor < base_cursor:
                    raise AccountProjectionError("INVALID_BATCH")
                next_checkpoint = AccountProjectionCheckpoint(
                    stream_epoch=validated.stream_epoch,
                    cursor=validated.next_cursor,
                    projection=projection,
                    pending_ack=True,
                )
                projection_json = _canonical_mapping(
                    next_checkpoint.projection,
                    max_bytes=self._max_payload_bytes,
                    error_code="INVALID_REDUCER_OUTPUT",
                )[0]
                connection.execute(
                    """UPDATE account_projection_checkpoint
                    SET stream_epoch = ?, cursor = ?, projection_json = ?, pending_ack = 1,
                        last_requested_cursor = ?, last_next_cursor = ?,
                        last_batch_fingerprint = ?
                    WHERE singleton = 1""",
                    (
                        validated.stream_epoch,
                        validated.next_cursor,
                        projection_json,
                        validated.requested_cursor,
                        validated.next_cursor,
                        validated.fingerprint,
                    ),
                )
                return next_checkpoint
        except AccountProjectionError:
            raise
        except sqlite3.Error:
            raise AccountProjectionError("STORE_IO_ERROR") from None

    def mark_acknowledged(self, stream_epoch: str, cursor: int) -> AccountProjectionCheckpoint:
        """Clear the local ACK gate for the current epoch and exact local cursor."""

        self._assert_usable()
        if not _valid_identifier(stream_epoch, _MAX_EPOCH_BYTES):
            raise AccountProjectionError("INVALID_STREAM_EPOCH")
        ack_cursor = _cursor(cursor, "INVALID_ACK_CURSOR")
        try:
            with self._transaction(immediate=True) as connection:
                record = self._load_record(connection)
                current = record.checkpoint
                if current.stream_epoch is None or stream_epoch != current.stream_epoch:
                    raise AccountProjectionError("STREAM_EPOCH_MISMATCH")
                if ack_cursor != current.cursor:
                    raise AccountProjectionError("ACK_CURSOR_MISMATCH")
                if current.pending_ack:
                    connection.execute(
                        "UPDATE account_projection_checkpoint SET pending_ack = 0 WHERE singleton = 1"
                    )
                    return AccountProjectionCheckpoint(
                        current.stream_epoch, current.cursor, current.projection, False
                    )
                return current
        except AccountProjectionError:
            raise
        except sqlite3.Error:
            raise AccountProjectionError("STORE_IO_ERROR") from None

    def close(self) -> None:
        """Idempotently prevent further use; every SQLite connection is short-lived."""

        self._assert_owner()
        if self._in_callback:
            self._reentry_violation = True
            raise AccountProjectionError("REENTRANT_CALL")
        if self._closed:
            return
        self._closed = True

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise AccountProjectionError("WRONG_THREAD")

    def _assert_usable(self) -> None:
        self._assert_owner()
        if self._in_callback:
            self._reentry_violation = True
            raise AccountProjectionError("REENTRANT_CALL")
        if self._closed:
            raise AccountProjectionError("STORE_CLOSED")

    @contextmanager
    def _transaction(self, *, immediate: bool) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self._path), timeout=5.0, isolation_level=None, uri=False)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        try:
            with self._transaction(immediate=True) as connection:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (_TABLE,),
                ).fetchone()
                user_objects = connection.execute(
                    "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if any(row[0] != _TABLE for row in user_objects):
                    raise AccountProjectionError("FOREIGN_DATABASE_CONTENT")
                if exists is None:
                    if not self._new_database_file:
                        raise AccountProjectionError("STORE_CORRUPT")
                    connection.execute(
                        """CREATE TABLE account_projection_checkpoint (
                            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                            schema_version INTEGER NOT NULL,
                            binding_json TEXT NOT NULL,
                            initial_state_json TEXT NOT NULL,
                            stream_epoch TEXT,
                            cursor INTEGER NOT NULL CHECK (
                                cursor >= 0 AND cursor <= 9223372036854775807
                            ),
                            projection_json TEXT NOT NULL,
                            pending_ack INTEGER NOT NULL CHECK (pending_ack IN (0, 1)),
                            last_requested_cursor INTEGER,
                            last_next_cursor INTEGER,
                            last_batch_fingerprint TEXT
                        )"""
                    )
                    connection.execute(
                        """INSERT INTO account_projection_checkpoint (
                            singleton, schema_version, binding_json, initial_state_json,
                            stream_epoch, cursor, projection_json, pending_ack,
                            last_requested_cursor, last_next_cursor, last_batch_fingerprint
                        ) VALUES (1, ?, ?, ?, NULL, 0, ?, 0, NULL, NULL, NULL)""",
                        (
                            _SCHEMA_VERSION,
                            self._binding_json,
                            self._initial_state_json,
                            self._initial_state_json,
                        ),
                    )
                else:
                    self._load_record(connection)
        except AccountProjectionError:
            raise
        except sqlite3.DatabaseError:
            raise AccountProjectionError("STORE_CORRUPT") from None
        except sqlite3.Error:
            raise AccountProjectionError("STORE_IO_ERROR") from None

    def _load_record(self, connection: sqlite3.Connection) -> _StoreRecord:
        try:
            rows = connection.execute(
                "SELECT * FROM account_projection_checkpoint LIMIT 2"
            ).fetchall()
        except sqlite3.Error:
            raise AccountProjectionError("STORE_CORRUPT") from None
        if len(rows) != 1:
            raise AccountProjectionError("STORE_CORRUPT")
        row = rows[0]

        if "schema_version" not in row.keys():  # noqa: SIM118 -- sqlite3.Row membership checks values.
            raise AccountProjectionError("STORE_CORRUPT")
        version = row["schema_version"]
        if type(version) is not int:
            raise AccountProjectionError("STORE_CORRUPT")
        if version != _SCHEMA_VERSION:
            raise AccountProjectionError("STORE_VERSION_MISMATCH")
        if frozenset(row.keys()) != _ROW_FIELDS or row["singleton"] != 1:
            raise AccountProjectionError("STORE_CORRUPT")

        stored_binding = _decode_canonical_mapping(
            row["binding_json"], self._max_payload_bytes, "STORE_CORRUPT"
        )
        if stored_binding[0] != self._binding_json:
            raise AccountProjectionError("STORE_BINDING_MISMATCH")
        stored_initial = _decode_canonical_mapping(
            row["initial_state_json"], self._max_payload_bytes, "STORE_CORRUPT"
        )
        if stored_initial[0] != self._initial_state_json:
            raise AccountProjectionError("INITIAL_STATE_MISMATCH")
        projection_text, projection_map = _decode_canonical_mapping(
            row["projection_json"], self._max_payload_bytes, "STORE_CORRUPT"
        )

        epoch = row["stream_epoch"]
        if epoch is not None and not _valid_identifier(epoch, _MAX_EPOCH_BYTES):
            raise AccountProjectionError("STORE_CORRUPT")
        cursor = row["cursor"]
        if type(cursor) is not int or not 0 <= cursor <= _SQLITE_MAX_INT:
            raise AccountProjectionError("STORE_CORRUPT")
        pending_raw = row["pending_ack"]
        if type(pending_raw) is not int or pending_raw not in (0, 1):
            raise AccountProjectionError("STORE_CORRUPT")

        last_requested = row["last_requested_cursor"]
        last_next = row["last_next_cursor"]
        fingerprint = row["last_batch_fingerprint"]
        if fingerprint is None:
            if last_requested is not None or last_next is not None:
                raise AccountProjectionError("STORE_CORRUPT")
            if epoch is not None or cursor != 0 or pending_raw != 0:
                raise AccountProjectionError("STORE_CORRUPT")
            if projection_text != self._initial_state_json:
                raise AccountProjectionError("STORE_CORRUPT")
        else:
            if (
                type(fingerprint) is not str
                or len(fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in fingerprint)
                or epoch is None
                or type(last_next) is not int
                or last_next != cursor
                or (last_requested is not None and type(last_requested) is not int)
                or (last_requested is not None and not 0 <= last_requested <= cursor)
            ):
                raise AccountProjectionError("STORE_CORRUPT")

        checkpoint = AccountProjectionCheckpoint(
            stream_epoch=epoch,
            cursor=cursor,
            projection=_freeze(projection_map),
            pending_ack=bool(pending_raw),
        )
        return _StoreRecord(checkpoint, last_requested, last_next, fingerprint)

    def _validate_reply(
        self, reply: Mapping[str, Any], requested_cursor: int | None
    ) -> _ValidatedBatch:
        reply_json, normalized = _canonical_mapping(
            reply,
            max_bytes=self._max_payload_bytes,
            error_code="INVALID_REPLY",
        )
        del reply_json  # The complete page was byte-bounded before any reducer call.
        fields = frozenset(normalized)
        if not fields >= _BATCH_FIELDS or fields - _BATCH_FIELDS - _OPTIONAL_BATCH_FIELDS:
            raise AccountProjectionError("INVALID_REPLY")
        echoed_cursor = _cursor_or_none(normalized["requested_cursor"], "INVALID_REQUEST_CURSOR")
        if echoed_cursor != requested_cursor:
            raise AccountProjectionError("INVALID_REQUEST_CURSOR")
        if requested_cursor is not None and (
            type(requested_cursor) is not int or not 0 <= requested_cursor <= _SQLITE_MAX_INT
        ):
            raise AccountProjectionError("INVALID_REQUEST_CURSOR")

        expected_account = self._binding["expected_account_scope"]
        expected_strategy = self._binding["expected_strategy_scope"]
        snapshot_value = normalized["snapshot"]
        snapshot: Mapping[str, Any] | None
        snapshot_cursor: int | None = None
        if snapshot_value is None:
            snapshot = None
        else:
            if (
                not isinstance(snapshot_value, dict)
                or frozenset(snapshot_value) != _SNAPSHOT_FIELDS
            ):
                raise AccountProjectionError("INVALID_BATCH")
            snapshot = _freeze(snapshot_value)
            if (
                snapshot_value["account_scope"] != expected_account
                or snapshot_value["strategy_scope"] != expected_strategy
            ):
                raise AccountProjectionError("INVALID_SCOPE")
            if not _valid_identifier(snapshot_value["snapshot_id"], _MAX_IDENTIFIER_BYTES):
                raise AccountProjectionError("INVALID_BATCH")
            snapshot_cursor = _cursor(snapshot_value["cursor"], "INVALID_BATCH")
            _finite_timestamp(snapshot_value["captured_at"], "INVALID_BATCH")
            if not isinstance(snapshot_value["payload"], dict):
                raise AccountProjectionError("INVALID_BATCH")

        events_value = normalized["events"]
        if type(events_value) is not list or len(events_value) > _MAX_EVENTS_PER_PAGE:
            raise AccountProjectionError("INVALID_BATCH")
        events: list[Mapping[str, Any]] = []
        for event_value in events_value:
            if not isinstance(event_value, dict) or frozenset(event_value) != _EVENT_FIELDS:
                raise AccountProjectionError("INVALID_BATCH")
            if (
                event_value["account_scope"] != expected_account
                or event_value["strategy_scope"] != expected_strategy
            ):
                raise AccountProjectionError("INVALID_SCOPE")
            if not _valid_identifier(event_value["event_id"], _MAX_IDENTIFIER_BYTES):
                raise AccountProjectionError("INVALID_BATCH")
            if not _valid_identifier(event_value["kind"], _MAX_IDENTIFIER_BYTES):
                raise AccountProjectionError("INVALID_BATCH")
            sequence = _cursor(event_value["sequence"], "INVALID_BATCH")
            if sequence == 0 or not isinstance(event_value["payload"], dict):
                raise AccountProjectionError("INVALID_BATCH")
            _finite_timestamp(event_value["observed_at"], "INVALID_BATCH")
            if any(event["event_id"] == event_value["event_id"] for event in events):
                raise AccountProjectionError("INVALID_BATCH")
            events.append(_freeze(event_value))

        next_cursor = _cursor(normalized["next_cursor"], "INVALID_BATCH")
        latest_cursor = _cursor(normalized["latest_cursor"], "INVALID_BATCH")
        recovered = normalized["recovered_from_snapshot"]
        if type(recovered) is not bool or latest_cursor < next_cursor:
            raise AccountProjectionError("INVALID_BATCH")
        if requested_cursor is None:
            if recovered:
                raise AccountProjectionError("INVALID_BATCH")
        else:
            expected_recovered = snapshot_cursor is not None and snapshot_cursor > requested_cursor
            if recovered is not expected_recovered:
                raise AccountProjectionError("INVALID_BATCH")
        if snapshot_cursor is None:
            batch_base = 0 if requested_cursor is None else requested_cursor
        else:
            if requested_cursor is not None and snapshot_cursor <= requested_cursor:
                raise AccountProjectionError("INVALID_BATCH")
            batch_base = snapshot_cursor

        previous_sequence = batch_base
        for event in events:
            sequence = event["sequence"]
            if sequence != previous_sequence + 1:
                raise AccountProjectionError("CURSOR_GAP")
            previous_sequence = sequence
        expected_next = previous_sequence if events else batch_base
        if next_cursor != expected_next or expected_next > latest_cursor:
            raise AccountProjectionError("INVALID_BATCH")
        if not events and snapshot is None and latest_cursor > next_cursor:
            raise AccountProjectionError("CURSOR_GAP")

        progress = normalized["consumer_progress"]
        if not isinstance(progress, dict) or frozenset(progress) != _PROGRESS_FIELDS:
            raise AccountProjectionError("INVALID_BATCH")
        epoch = progress["stream_epoch"]
        if not _valid_identifier(epoch, _MAX_EPOCH_BYTES):
            raise AccountProjectionError("INVALID_STREAM_EPOCH")
        offered = _cursor(progress["offered_cursor"], "INVALID_BATCH")
        acknowledged = _cursor(progress["acknowledged_cursor"], "INVALID_BATCH")
        if acknowledged > offered or offered < next_cursor or offered > latest_cursor:
            raise AccountProjectionError("INVALID_BATCH")

        if "state_status" in normalized:
            status = normalized["state_status"]
            if not isinstance(status, dict) or frozenset(status) != _STATUS_FIELDS:
                raise AccountProjectionError("INVALID_REPLY")
            generation = status["session_generation"]
            if generation is not None and not _valid_identifier(generation, _MAX_IDENTIFIER_BYTES):
                raise AccountProjectionError("INVALID_REPLY")
            if type(status["ready"]) is not bool:
                raise AccountProjectionError("INVALID_REPLY")
            fresh_for = _finite_timestamp(status["fresh_for_seconds"], "INVALID_REPLY")
            if fresh_for < 0:
                raise AccountProjectionError("INVALID_REPLY")

        data_for_fingerprint = {
            "requested_cursor": requested_cursor,
            "snapshot": snapshot_value,
            "events": events_value,
            "next_cursor": next_cursor,
            "recovered_from_snapshot": recovered,
        }
        fingerprint = hashlib.sha256(
            _canonical_json(data_for_fingerprint, self._max_payload_bytes, "INVALID_REPLY")
        ).hexdigest()
        return _ValidatedBatch(
            requested_cursor=requested_cursor,
            snapshot=snapshot,
            events=tuple(events),
            next_cursor=next_cursor,
            latest_cursor=latest_cursor,
            stream_epoch=epoch,
            offered_cursor=offered,
            acknowledged_cursor=acknowledged,
            recovered_from_snapshot=recovered,
            fingerprint=fingerprint,
        )

    def _validate_batch_position(
        self, batch: _ValidatedBatch, previous: AccountProjectionCheckpoint
    ) -> int:
        if batch.latest_cursor < previous.cursor or batch.next_cursor < previous.cursor:
            raise AccountProjectionError("CURSOR_GAP")
        if batch.snapshot is not None:
            base = batch.snapshot["cursor"]
            if previous.stream_epoch is not None and base <= previous.cursor:
                raise AccountProjectionError("INVALID_BATCH")
        else:
            base = previous.cursor
        if batch.events:
            if batch.events[0]["sequence"] != base + 1:
                raise AccountProjectionError("CURSOR_GAP")
        elif batch.snapshot is None and batch.latest_cursor > batch.next_cursor:
            raise AccountProjectionError("CURSOR_GAP")
        return base

    def _invoke_reducer(
        self,
        reducer: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
        state: Mapping[str, Any],
        fact: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if self._in_callback:
            self._reentry_violation = True
            raise AccountProjectionError("REENTRANT_CALL")
        self._reentry_violation = False
        self._in_callback = True
        try:
            try:
                output = reducer(state, fact)
            except Exception:
                if self._reentry_violation:
                    raise AccountProjectionError("REENTRANT_CALL") from None
                raise AccountProjectionError("REDUCER_FAILED") from None
        finally:
            self._in_callback = False
        if self._reentry_violation:
            raise AccountProjectionError("REENTRANT_CALL")
        _text, normalized = _canonical_mapping(
            output,
            max_bytes=self._max_payload_bytes,
            error_code="INVALID_REDUCER_OUTPUT",
        )
        return _freeze(normalized)


def _cursor_or_none(value: object, code: str) -> int | None:
    if value is None:
        return None
    return _cursor(value, code)


def _cursor(value: object, code: str) -> int:
    if type(value) is not int or not 0 <= value <= _SQLITE_MAX_INT:
        raise AccountProjectionError(code)
    return value


def _finite_timestamp(value: object, code: str) -> float:
    if type(value) not in (int, float):
        raise AccountProjectionError(code)
    try:
        finite = math.isfinite(float(value))
    except (OverflowError, ValueError):
        finite = False
    if not finite:
        raise AccountProjectionError(code)
    return float(value)


def _valid_identifier(value: object, max_bytes: int) -> bool:
    if type(value) is not str or not value or value != value.strip():
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return len(encoded) <= max_bytes and not any(
        ord(character) < 32 or 0x7F <= ord(character) <= 0x9F for character in value
    )


def _canonical_mapping(
    value: object, *, max_bytes: int, error_code: str
) -> tuple[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise AccountProjectionError(error_code)
    try:
        normalized = _normalize_json(value, depth=0, count=[0], max_bytes=max_bytes)
        if not isinstance(normalized, dict):
            raise AccountProjectionError(error_code)
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except AccountProjectionError:
        raise
    except Exception:
        raise AccountProjectionError(error_code) from None
    if len(encoded) > max_bytes:
        raise AccountProjectionError(error_code)
    return encoded.decode("utf-8"), normalized


def _normalize_json(value: Any, *, depth: int, count: list[int], max_bytes: int) -> Any:
    if depth > _MAX_JSON_DEPTH:
        raise AccountProjectionError("JSON_BOUNDS_EXCEEDED")
    count[0] += 1
    if count[0] > _MAX_JSON_NODES:
        raise AccountProjectionError("JSON_BOUNDS_EXCEEDED")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if value.bit_length() > _MAX_INTEGER_BITS:
            raise AccountProjectionError("JSON_BOUNDS_EXCEEDED")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise AccountProjectionError("JSON_VALUE_INVALID")
        return value
    if type(value) is str:
        try:
            if len(value) > max_bytes or len(value.encode("utf-8", errors="strict")) > max_bytes:
                raise AccountProjectionError("JSON_BOUNDS_EXCEEDED")
        except UnicodeEncodeError:
            raise AccountProjectionError("JSON_VALUE_INVALID") from None
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_CONTAINER_ITEMS:
            raise AccountProjectionError("JSON_BOUNDS_EXCEEDED")
        copied: dict[str, Any] = {}
        for key in value:
            if type(key) is not str:
                raise AccountProjectionError("JSON_VALUE_INVALID")
            if len(key) > _MAX_IDENTIFIER_BYTES:
                raise AccountProjectionError("JSON_BOUNDS_EXCEEDED")
            try:
                key_bytes = key.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                raise AccountProjectionError("JSON_VALUE_INVALID") from None
            if len(key_bytes) > _MAX_IDENTIFIER_BYTES:
                raise AccountProjectionError("JSON_BOUNDS_EXCEEDED")
            copied[key] = _normalize_json(
                value[key], depth=depth + 1, count=count, max_bytes=max_bytes
            )
        return copied
    if type(value) in (list, tuple):
        if len(value) > _MAX_CONTAINER_ITEMS:
            raise AccountProjectionError("JSON_BOUNDS_EXCEEDED")
        return [
            _normalize_json(item, depth=depth + 1, count=count, max_bytes=max_bytes)
            for item in value
        ]
    raise AccountProjectionError("JSON_VALUE_INVALID")


def _canonical_json(value: object, max_bytes: int, error_code: str) -> bytes:
    text, _normalized = _canonical_mapping(value, max_bytes=max_bytes, error_code=error_code)
    return text.encode("utf-8")


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _decode_canonical_mapping(
    value: object, max_bytes: int, error_code: str
) -> tuple[str, dict[str, Any]]:
    if type(value) is not str:
        raise AccountProjectionError(error_code)
    try:
        encoded = value.encode("utf-8", errors="strict")
        if len(encoded) > max_bytes:
            raise AccountProjectionError(error_code)
        decoded = json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_float=_finite_json_float,
            parse_int=_bounded_json_int,
            parse_constant=_reject_json_constant,
        )
        canonical, normalized = _canonical_mapping(
            decoded, max_bytes=max_bytes, error_code=error_code
        )
    except AccountProjectionError:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeEncodeError):
        raise AccountProjectionError(error_code) from None
    if canonical != value:
        raise AccountProjectionError(error_code)
    return canonical, normalized


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _bounded_json_int(value: str) -> int:
    if len(value.lstrip("-")) > 78:
        raise ValueError("JSON integer exceeds limit")
    parsed = int(value)
    if parsed.bit_length() > _MAX_INTEGER_BITS:
        raise ValueError("JSON integer exceeds limit")
    return parsed


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid JSON constant")
