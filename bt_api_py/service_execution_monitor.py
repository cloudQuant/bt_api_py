"""Explicit, bounded relay from an execution journal to service Monitor.

The relay is deliberately opt-in. It borrows an already registered service
runtime and a read-only execution journal reader, owns only its local cursor
database, and performs RPCs only when :meth:`poll` is called by its owner
thread. It never runs from ``ServiceHost.maintain`` or the order path.

Execution business events are already committed with their business rows in
the source journal transaction. This module projects only trusted lifecycle
metadata; it never reads or forwards an order payload or provider/account
economic fact. Source rows are retained by the journal in this round. A later
capacity/retention policy must be designed separately.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from bt_api_base.service_protocol import WireScope

from bt_api_py.service_config import ServiceConfig
from bt_api_py.service_monitor import (
    ServiceMonitorOutboxSink,
    ServiceMonitorOutboxSinkError,
)
from bt_api_py.service_runtime import RegisteredBinding, ServiceClientRuntime

_PROJECTION_VERSION = 2
_PROJECTION_SCHEMA = "sdk.execution_monitor_projection.v2"
_SCOPE_SCHEMA = "sdk.execution_monitor_scope.v1"
_MAX_POLL_LIMIT = 128
_PAGE_SIZE = 100
_MAX_CHECKPOINT_RECORDS = 4096
_MAX_CHECKPOINT_BINDING_BYTES = 2048
_MAX_DATABASE_PAGES = 2048  # 8 MiB with SQLite's default 4 KiB page size.
_MAX_TEXT_BYTES = 256
_SEQUENCE_LIMIT = 1 << 63
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_CHECKPOINT_APPLICATION_ID = 0x53454D52  # "SEMR"
_CHECKPOINT_USER_VERSION = 1
_STREAMS = ("execution", "cancellation")


class ServiceExecutionMonitorRelayError(RuntimeError):
    """Finite relay diagnostic with only confirmed delivery/checkpoint counts."""

    def __init__(
        self,
        code: str,
        *,
        last_poll: ExecutionMonitorPollResult | None = None,
    ) -> None:
        self.code = code
        self.last_poll = last_poll or ExecutionMonitorPollResult()
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ExecutionMonitorPollResult:
    """Counts for the most recent explicit poll.

    ``remote_delivered`` counts only events for which the sink returned a
    validated ingress receipt. ``checkpointed`` counts local cursor advances
    or an exact idempotent acknowledgement found after a competing relay won
    the same cursor CAS.
    """

    execution_delivered: int = 0
    cancellation_delivered: int = 0
    remote_delivered: int = 0
    checkpointed: int = 0

    @property
    def total_delivered(self) -> int:
        """Return the number of remotely confirmed event deliveries."""

        return self.remote_delivered


@dataclass(frozen=True, slots=True)
class ExecutionMonitorCheckpoint:
    """The latest durable local cursor for one stream namespace."""

    sequence: int = 0
    source_event_id: str | None = None
    fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class _CursorNamespace:
    namespace_id: str
    binding_json: str


class _CheckpointFailureError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ServiceExecutionMonitorRelay:
    """Relay retained execution-journal metadata over Monitor's durable ingress.

    ``runtime`` must already be registered and remain owned by this thread.
    ``reader`` is borrowed and read-only. ``close`` closes only the relay's
    logical lifetime; it never closes either borrowed object.
    """

    def __init__(
        self,
        runtime: ServiceClientRuntime,
        reader: object,
        *,
        scope_ref: str,
        checkpoint_path: str | Path,
    ) -> None:
        owner_thread_id = threading.get_ident()
        if type(runtime) is not ServiceClientRuntime:
            raise ServiceExecutionMonitorRelayError("INVALID_RUNTIME")
        if type(scope_ref) is not str or not _valid_text(scope_ref, 128):
            raise ServiceExecutionMonitorRelayError("INVALID_SCOPE_REF")
        if not isinstance(checkpoint_path, (str, Path)) or not str(checkpoint_path):
            raise ServiceExecutionMonitorRelayError("INVALID_CHECKPOINT_PATH")

        # Domain dependencies are loaded only when this explicit adapter is
        # constructed, keeping ordinary SDK imports independent of Execution.
        try:
            from bt_api_execution.contracts import ExecutionScope
            from bt_api_execution.journal_reader import (
                ExecutionJournalReader,
                JournalEvent,
                JournalPage,
            )
        except ImportError:
            raise ServiceExecutionMonitorRelayError("EXECUTION_JOURNAL_UNAVAILABLE") from None

        if type(reader) is not ExecutionJournalReader:
            raise ServiceExecutionMonitorRelayError("INVALID_JOURNAL_READER")
        source_scope = getattr(reader, "scope", None)
        if type(source_scope) is not ExecutionScope:
            raise ServiceExecutionMonitorRelayError("INVALID_EXECUTION_SCOPE")
        generation = getattr(reader, "generation", None)
        scope_key = getattr(source_scope, "key", None)
        if not _valid_text(generation, 128) or not _valid_text(scope_key, 128):
            raise ServiceExecutionMonitorRelayError("INVALID_JOURNAL_NAMESPACE")

        try:
            self._validate_ingress_scope(runtime, scope_ref, source_scope)
            outbox_scope = _outbox_scope(generation, scope_key)
            sink = ServiceMonitorOutboxSink(
                runtime,
                scope_ref=scope_ref,
                outbox_scope=outbox_scope,
            )
            source_path, source_identity = _source_identity(reader)
            checkpoint = Path(checkpoint_path).expanduser().resolve(strict=False)
            _reject_same_database(source_path, source_identity, checkpoint)
            _initialize_or_validate_checkpoint(checkpoint)
            namespaces = {
                stream: _cursor_namespace(sink.consumer_id, generation, scope_key, stream)
                for stream in _STREAMS
            }
        except ServiceExecutionMonitorRelayError:
            raise
        except ServiceMonitorOutboxSinkError as error:
            raise ServiceExecutionMonitorRelayError(error.code) from None
        except _CheckpointFailureError as error:
            raise ServiceExecutionMonitorRelayError(error.code) from None
        except Exception:
            raise ServiceExecutionMonitorRelayError("RELAY_CONFIGURATION_INVALID") from None

        self._runtime = runtime
        self._reader = reader
        self._scope_ref = scope_ref
        self._source_scope = source_scope
        self._source_path = source_path
        self._source_identity_value = source_identity
        self._generation = generation
        self._scope_key = scope_key
        self._page_type = JournalPage
        self._event_type = JournalEvent
        self._checkpoint_path = checkpoint
        self._outbox_scope = outbox_scope
        self._sink = sink
        self._consumer_id = sink.consumer_id
        self._namespaces = namespaces
        self._owner_thread_id = owner_thread_id
        self._next_stream = 0
        self._closed = False
        self._last_poll = ExecutionMonitorPollResult()

    @property
    def last_poll(self) -> ExecutionMonitorPollResult:
        """Return the latest completed or partially completed poll counts."""

        return self._last_poll

    @property
    def consumer_id(self) -> str:
        """Return the stable Monitor sink consumer identity."""

        return self._consumer_id

    def checkpoint(self, stream: str) -> ExecutionMonitorCheckpoint:
        """Read the durable cursor for ``execution`` or ``cancellation``."""

        self._assert_usable()
        namespace = self._namespace(stream)
        try:
            return _read_checkpoint(self._checkpoint_path, namespace)
        except _CheckpointFailureError as error:
            raise ServiceExecutionMonitorRelayError(error.code, last_poll=self._last_poll) from None

    def rebind(self) -> None:
        """Recreate the borrowed-runtime sink after caller-managed recovery.

        Recovery may renew registration IDs, but the destination identity must
        remain stable. A changed destination cannot inherit this relay's
        checkpoint namespace.
        """

        self._assert_usable()
        try:
            self._validate_reader_identity()
            self._validate_ingress_scope(self._runtime, self._scope_ref, self._source_scope)
            sink = ServiceMonitorOutboxSink(
                self._runtime,
                scope_ref=self._scope_ref,
                outbox_scope=self._outbox_scope,
            )
        except ServiceMonitorOutboxSinkError as error:
            raise ServiceExecutionMonitorRelayError(error.code, last_poll=self._last_poll) from None
        except ServiceExecutionMonitorRelayError:
            raise
        except Exception:
            raise ServiceExecutionMonitorRelayError(
                "INGRESS_BINDING_UNAVAILABLE", last_poll=self._last_poll
            ) from None
        if sink.consumer_id != self._consumer_id:
            raise ServiceExecutionMonitorRelayError(
                "INGRESS_BINDING_CHANGED", last_poll=self._last_poll
            )
        self._sink = sink

    def poll(self, limit: int = 32) -> ExecutionMonitorPollResult:
        """Poll both journal streams and spend at most ``limit`` ingress RPCs."""

        self._assert_usable()
        if type(limit) is not int or not 0 <= limit <= _MAX_POLL_LIMIT:
            raise ServiceExecutionMonitorRelayError("INVALID_POLL_LIMIT")

        counts = {
            "execution_delivered": 0,
            "cancellation_delivered": 0,
            "remote_delivered": 0,
            "checkpointed": 0,
        }
        self._last_poll = ExecutionMonitorPollResult()
        try:
            self._validate_reader_identity()
            cursors = {
                stream: _read_checkpoint(self._checkpoint_path, self._namespaces[stream])
                for stream in _STREAMS
            }
            # The source is not retained away in this round. Verify each
            # existing cursor's anchor before delivering anything, so a
            # replaced, truncated, or rewritten source cannot skip history.
            for stream in _STREAMS:
                cursor = cursors[stream]
                if cursor.sequence:
                    self._verify_anchor(stream, cursor)

            pages: dict[str, list[object]] = {stream: [] for stream in _STREAMS}
            page_offsets = dict.fromkeys(_STREAMS, 0)
            next_stream = self._next_stream
            idle_streams: set[str] = set()
            while counts["remote_delivered"] < limit:
                selected_stream: str | None = None
                selected_event: object | None = None
                for _ in range(len(_STREAMS)):
                    stream = _STREAMS[next_stream]
                    next_stream = (next_stream + 1) % len(_STREAMS)
                    if page_offsets[stream] >= len(pages[stream]):
                        if stream in idle_streams:
                            continue
                        events = self._read_events(
                            stream,
                            after_sequence=cursors[stream].sequence,
                            limit=_PAGE_SIZE,
                        )
                        pages[stream] = events
                        page_offsets[stream] = 0
                        if not events:
                            idle_streams.add(stream)
                            continue
                        idle_streams.discard(stream)
                    selected_stream = stream
                    selected_event = pages[stream][page_offsets[stream]]
                    page_offsets[stream] += 1
                    # Persist fairness across polls and failures. The next
                    # stream gets its opportunity even when this RPC fails.
                    self._next_stream = next_stream
                    break

                if selected_stream is None or selected_event is None:
                    break

                event = self._project(selected_event, selected_stream)
                from bt_api_monitor.durable import SequencedOutboxEvent

                try:
                    self._sink(SequencedOutboxEvent(selected_event.sequence, event))
                except ServiceMonitorOutboxSinkError as error:
                    code = _sink_error_code(error.code)
                    self._raise_poll_error(code, counts)
                except Exception:
                    # The remote outcome may be unknown. Leave the cursor
                    # untouched; stable event IDs make replay idempotent.
                    self._raise_poll_error("REMOTE_DELIVERY_FAILED", counts)

                counts["remote_delivered"] += 1
                if selected_stream == "execution":
                    counts["execution_delivered"] += 1
                else:
                    counts["cancellation_delivered"] += 1
                self._snapshot_counts(counts)

                # The reader can be backed up or replaced while the RPC is in
                # flight. Recheck the exact just-delivered source row before
                # committing its local acknowledgement.
                self._verify_event_anchor(selected_stream, selected_event)

                fingerprint = _event_fingerprint(selected_event)
                try:
                    outcome = _advance_checkpoint(
                        self._checkpoint_path,
                        self._namespaces[selected_stream],
                        expected_sequence=cursors[selected_stream].sequence,
                        sequence=selected_event.sequence,
                        source_event_id=selected_event.event_id,
                        fingerprint=fingerprint,
                    )
                except _CheckpointFailureError as error:
                    self._raise_poll_error(error.code, counts)
                except Exception:
                    self._raise_poll_error("LOCAL_CHECKPOINT_FAILED", counts)

                if outcome == "advanced" or outcome == "duplicate":
                    cursors[selected_stream] = ExecutionMonitorCheckpoint(
                        selected_event.sequence,
                        selected_event.event_id,
                        fingerprint,
                    )
                    counts["checkpointed"] += 1
                    self._snapshot_counts(counts)
                    continue

                # _advance_checkpoint rereads the row under BEGIN IMMEDIATE.
                # Any loss that is not an exact same-event duplicate fails
                # closed; this poll will not skip to the newer cursor.
                self._raise_poll_error("CHECKPOINT_CONFLICT", counts)

            result = self._snapshot_counts(counts)
            return result
        except ServiceExecutionMonitorRelayError:
            raise
        except _CheckpointFailureError as error:
            self._raise_poll_error(error.code, counts)
        except Exception:
            self._raise_poll_error("SOURCE_READ_FAILED", counts)
        raise AssertionError("unreachable")

    def close(self) -> None:
        """Close this relay's logical lifetime without closing borrowed objects."""

        self._assert_owner_thread()
        self._closed = True

    def _validate_reader_identity(self) -> None:
        if getattr(self._reader, "generation", None) != self._generation:
            raise ServiceExecutionMonitorRelayError(
                "SOURCE_GENERATION_CHANGED", last_poll=self._last_poll
            )
        if getattr(getattr(self._reader, "scope", None), "key", None) != self._scope_key:
            raise ServiceExecutionMonitorRelayError("SCOPE_MISMATCH", last_poll=self._last_poll)
        try:
            current_path, current_identity = _source_identity(self._reader)
        except Exception:
            raise ServiceExecutionMonitorRelayError(
                "SOURCE_IDENTITY_UNAVAILABLE", last_poll=self._last_poll
            ) from None
        if current_path != self._source_path or current_identity != self._source_identity_value:
            raise ServiceExecutionMonitorRelayError(
                "SOURCE_IDENTITY_CHANGED", last_poll=self._last_poll
            )

    @staticmethod
    def _validate_ingress_scope(
        runtime: ServiceClientRuntime,
        scope_ref: str,
        source_scope: object,
    ) -> None:
        bindings = runtime.bindings
        candidates = [
            binding
            for binding in bindings
            if type(binding) is RegisteredBinding
            and binding.active is True
            and binding.mount == "upstream.telemetry"
            and binding.scope_ref == scope_ref
            and type(binding.operations) is frozenset
            and "events.append" in binding.operations
        ]
        if len(candidates) != 1:
            raise ServiceExecutionMonitorRelayError("INGRESS_BINDING_UNAVAILABLE")
        wire_scope = candidates[0].scope
        if type(wire_scope) is not WireScope:
            raise ServiceExecutionMonitorRelayError("INGRESS_BINDING_UNAVAILABLE")
        if (
            wire_scope.account_refs
            or wire_scope.portfolio_ref is not None
            or wire_scope.group_id is not None
            or wire_scope.leg_id is not None
            or wire_scope.manual_book_ref is not None
        ):
            raise ServiceExecutionMonitorRelayError("UNSUPPORTED_INGRESS_SCOPE")
        if wire_scope.account_ref != source_scope.account_ref:
            raise ServiceExecutionMonitorRelayError("SCOPE_MISMATCH")
        if (
            wire_scope.strategy_ref is not None
            and wire_scope.strategy_ref != source_scope.strategy_id
        ):
            raise ServiceExecutionMonitorRelayError("SCOPE_MISMATCH")

    def _verify_anchor(self, stream: str, cursor: ExecutionMonitorCheckpoint) -> None:
        events = self._read_events(
            stream,
            after_sequence=cursor.sequence - 1,
            limit=1,
        )
        if not events or events[0].sequence != cursor.sequence:
            raise ServiceExecutionMonitorRelayError(
                "SOURCE_CHECKPOINT_DIVERGED", last_poll=self._last_poll
            )
        event = events[0]
        if (
            event.event_id != cursor.source_event_id
            or _event_fingerprint(event) != cursor.fingerprint
        ):
            raise ServiceExecutionMonitorRelayError(
                "SOURCE_CHECKPOINT_DIVERGED", last_poll=self._last_poll
            )

    def _verify_event_anchor(
        self,
        stream: str,
        expected_event: object,
        *,
        expected_sequence: int | None = None,
    ) -> None:
        sequence = expected_event.sequence if expected_sequence is None else expected_sequence
        events = self._read_events(stream, after_sequence=sequence - 1, limit=1)
        if not events or events[0].sequence != sequence:
            raise ServiceExecutionMonitorRelayError(
                "SOURCE_CHECKPOINT_DIVERGED", last_poll=self._last_poll
            )
        event = events[0]
        if event.event_id != expected_event.event_id or _event_fingerprint(
            event
        ) != _event_fingerprint(expected_event):
            raise ServiceExecutionMonitorRelayError(
                "SOURCE_CHECKPOINT_DIVERGED", last_poll=self._last_poll
            )

    def _read_events(self, stream: str, *, after_sequence: int, limit: int) -> list[object]:
        try:
            page = self._reader.read_page(
                stream,
                after_sequence=after_sequence,
                limit=limit,
            )
        except Exception:
            raise ServiceExecutionMonitorRelayError(
                "SOURCE_READ_FAILED", last_poll=self._last_poll
            ) from None
        if type(page) is not self._page_type or page.generation != self._generation:
            raise ServiceExecutionMonitorRelayError(
                "SOURCE_GENERATION_CHANGED", last_poll=self._last_poll
            )
        if type(page.events) is not tuple:
            raise ServiceExecutionMonitorRelayError(
                "SOURCE_PAGE_INVALID", last_poll=self._last_poll
            )
        events: list[object] = []
        prior = after_sequence
        for event in page.events:
            self._validate_event(event, stream)
            if event.sequence <= prior:
                raise ServiceExecutionMonitorRelayError(
                    "SOURCE_PAGE_INVALID", last_poll=self._last_poll
                )
            prior = event.sequence
            events.append(event)
        if len(events) > limit:
            raise ServiceExecutionMonitorRelayError(
                "SOURCE_PAGE_INVALID", last_poll=self._last_poll
            )
        return events

    def _validate_event(self, event: object, stream: str) -> None:
        if type(event) is not self._event_type:
            raise ServiceExecutionMonitorRelayError(
                "SOURCE_EVENT_INVALID", last_poll=self._last_poll
            )
        if (
            type(event.sequence) is not int
            or not 1 <= event.sequence < _SEQUENCE_LIMIT
            or event.stream != stream
            or event.scope_key != self._scope_key
            or event.journal_generation != self._generation
            or not _valid_text(event.event_id, _MAX_TEXT_BYTES)
            or not _valid_text(event.event_type, 128)
            or not _valid_text(event.state, 128)
            or type(event.created_at_ns) is not int
            or event.created_at_ns <= 0
            or not _valid_optional_text(event.intent_id, _MAX_TEXT_BYTES)
            or not _valid_optional_text(event.cancel_id, _MAX_TEXT_BYTES)
            or not _valid_optional_text(event.target_intent_id, _MAX_TEXT_BYTES)
        ):
            raise ServiceExecutionMonitorRelayError(
                "SOURCE_EVENT_INVALID", last_poll=self._last_poll
            )
        if stream == "execution":
            valid_refs = (
                event.intent_id is not None
                and event.cancel_id is None
                and event.target_intent_id is None
            )
        else:
            valid_refs = (
                event.intent_id is None
                and event.cancel_id is not None
                and event.target_intent_id is not None
            )
        if not valid_refs:
            raise ServiceExecutionMonitorRelayError(
                "SOURCE_EVENT_INVALID", last_poll=self._last_poll
            )

    def _project(self, source_event: object, stream: str) -> object:
        self._validate_event(source_event, stream)
        try:
            from bt_api_monitor.durable import OutboxEvent
        except ImportError:
            raise ServiceExecutionMonitorRelayError(
                "MONITOR_OUTBOX_UNAVAILABLE", last_poll=self._last_poll
            ) from None
        event_id = _projected_event_id(
            self._generation,
            stream,
            self._scope_key,
            source_event.event_id,
        )
        data = {
            "schema": _PROJECTION_SCHEMA,
            "journal_generation": self._generation,
            "stream": stream,
            "source_sequence": source_event.sequence,
            "source_event_id": source_event.event_id,
            "source_created_at_ns": source_event.created_at_ns,
            "source_scope_key": self._scope_key,
            "source_scope": dict(self._source_scope.to_payload()),
            "business_event_type": source_event.event_type,
            "state": source_event.state,
            "intent_id": source_event.intent_id,
            "cancel_id": source_event.cancel_id,
            "target_intent_id": source_event.target_intent_id,
        }
        return OutboxEvent(
            event_id=event_id,
            scope=self._outbox_scope,
            event_type=(
                "execution.lifecycle" if stream == "execution" else "execution.cancellation"
            ),
            data=data,
            occurred_at=source_event.created_at_ns / 1_000_000_000,
        )

    def _namespace(self, stream: str) -> _CursorNamespace:
        if type(stream) is not str or stream not in self._namespaces:
            raise ServiceExecutionMonitorRelayError("INVALID_STREAM", last_poll=self._last_poll)
        return self._namespaces[stream]

    def _assert_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceExecutionMonitorRelayError("WRONG_OWNER_THREAD", last_poll=self._last_poll)

    def _assert_usable(self) -> None:
        self._assert_owner_thread()
        if self._closed:
            raise ServiceExecutionMonitorRelayError("RELAY_CLOSED", last_poll=self._last_poll)

    def _snapshot_counts(self, counts: Mapping[str, int]) -> ExecutionMonitorPollResult:
        result = ExecutionMonitorPollResult(**dict(counts))
        self._last_poll = result
        return result

    def _raise_poll_error(self, code: str, counts: Mapping[str, int]) -> None:
        result = self._snapshot_counts(counts)
        raise ServiceExecutionMonitorRelayError(code, last_poll=result) from None


def execution_monitor_relay_configured(
    config: ServiceConfig,
    *,
    scope_ref: str,
) -> bool:
    """Return whether parsed service config declares the fixed Monitor upstream.

    This pure check does not import Execution/Monitor packages, construct a
    runtime or reader, or touch a checkpoint file. The registered runtime sink
    performs the independent ``events.append`` operation check against the
    active registry; this config declaration is not itself authorization.
    """

    if type(config) is not ServiceConfig or type(scope_ref) is not str or not scope_ref:
        return False
    monitor = config.clients.get("monitor")
    if not isinstance(monitor, Mapping):
        return False
    settings = monitor.get("settings")
    if not isinstance(settings, Mapping):
        return False
    bindings = settings.get("bindings")
    if type(bindings) is not tuple:
        return False
    matches = [
        binding
        for binding in bindings
        if isinstance(binding, Mapping)
        and binding.get("mount") == "upstream.telemetry"
        and binding.get("scope_ref") == scope_ref
    ]
    return len(matches) == 1


def _outbox_scope(generation: str, scope_key: str) -> str:
    digest = _sha256(
        _canonical_json(
            {
                "schema": _SCOPE_SCHEMA,
                "generation": generation,
                "scope_key": scope_key,
            }
        )
    )
    return "sdk-execution-monitor-v1:" + digest


def _projected_event_id(
    generation: str,
    stream: str,
    scope_key: str,
    source_event_id: str,
) -> str:
    digest = _sha256(
        _canonical_json(
            {
                "schema": _PROJECTION_SCHEMA,
                "generation": generation,
                "stream": stream,
                "scope_key": scope_key,
                "source_event_id": source_event_id,
            }
        )
    )
    return "service-event:" + digest


def _cursor_namespace(
    consumer_id: str,
    generation: str,
    scope_key: str,
    stream: str,
) -> _CursorNamespace:
    binding = {
        "schema": _SCOPE_SCHEMA,
        "consumer_id": consumer_id,
        "generation": generation,
        "scope_key": scope_key,
        "stream": stream,
        "projection_version": _PROJECTION_VERSION,
    }
    binding_json = _canonical_json(binding)
    if len(binding_json.encode("utf-8")) > _MAX_CHECKPOINT_BINDING_BYTES:
        raise ServiceExecutionMonitorRelayError("CHECKPOINT_NAMESPACE_TOO_LARGE")
    return _CursorNamespace(_sha256(binding_json), binding_json)


def _source_identity(reader: object) -> tuple[Path, tuple[int, int]]:
    path = getattr(reader, "database_path", None)
    identity = getattr(reader, "source_identity", None)
    if not isinstance(path, Path) or type(identity) is not tuple or len(identity) != 2:
        raise ValueError("reader source identity is invalid")
    if any(type(value) is not int or value < 0 for value in identity):
        raise ValueError("reader source identity is invalid")
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if not stat.S_ISREG(info.st_mode) or identity != (info.st_dev, info.st_ino):
        raise ValueError("reader source identity changed")
    return resolved, identity


def _reject_same_database(
    source_path: Path,
    source_identity: tuple[int, int],
    checkpoint_path: Path,
) -> None:
    if source_path == checkpoint_path:
        raise ServiceExecutionMonitorRelayError("CHECKPOINT_MUST_BE_SEPARATE")
    try:
        checkpoint_info = checkpoint_path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(checkpoint_info.st_mode):
        raise ServiceExecutionMonitorRelayError("INVALID_CHECKPOINT_FILE")
    if (checkpoint_info.st_dev, checkpoint_info.st_ino) == source_identity:
        raise ServiceExecutionMonitorRelayError("CHECKPOINT_MUST_BE_SEPARATE")
    try:
        if os.path.samefile(source_path, checkpoint_path):
            raise ServiceExecutionMonitorRelayError("CHECKPOINT_MUST_BE_SEPARATE")
    except OSError:
        # Device/inode identity already handles hardlinks on supported local
        # filesystems. A failed optional samefile check must not leak the path.
        pass


def _initialize_or_validate_checkpoint(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            info = path.lstat()
        except FileNotFoundError:
            existed = False
        else:
            existed = True
            if not stat.S_ISREG(info.st_mode):
                raise _CheckpointFailureError("CHECKPOINT_UNAVAILABLE")
        connection = _connect(path, create=not existed)
        try:
            app_id = connection.execute("PRAGMA application_id").fetchone()[0]
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if app_id == 0 and user_version == 0:
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if existed or tables:
                    raise _CheckpointFailureError("CHECKPOINT_CORRUPT")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    CREATE TABLE relay_checkpoints (
                        namespace_id TEXT PRIMARY KEY,
                        binding_json TEXT NOT NULL,
                        sequence INTEGER NOT NULL CHECK(sequence > 0),
                        source_event_id TEXT NOT NULL,
                        fingerprint TEXT NOT NULL
                    ) WITHOUT ROWID
                    """
                )
                connection.execute(f"PRAGMA application_id={_CHECKPOINT_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={_CHECKPOINT_USER_VERSION}")
                connection.commit()
            elif app_id != _CHECKPOINT_APPLICATION_ID or user_version != _CHECKPOINT_USER_VERSION:
                raise _CheckpointFailureError("CHECKPOINT_CORRUPT")
            _validate_checkpoint_schema(connection)
            # For an existing file, do not change a persistent setting until
            # its application identity and full schema have been verified.
            connection.execute("PRAGMA synchronous=FULL")
            mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if mode.casefold() != "delete":
                raise _CheckpointFailureError("CHECKPOINT_UNAVAILABLE")
            connection.execute(f"PRAGMA max_page_count={_MAX_DATABASE_PAGES}")
            check = connection.execute("PRAGMA quick_check(1)").fetchone()
            if check is None or check[0] != "ok":
                raise _CheckpointFailureError("CHECKPOINT_CORRUPT")
            page_count = connection.execute("PRAGMA page_count").fetchone()[0]
            if type(page_count) is not int or page_count > _MAX_DATABASE_PAGES:
                raise _CheckpointFailureError("CHECKPOINT_CAPACITY")
        finally:
            connection.close()
    except _CheckpointFailureError:
        raise
    except Exception:
        raise _CheckpointFailureError("CHECKPOINT_UNAVAILABLE") from None


def _validate_checkpoint_schema(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA table_info(relay_checkpoints)").fetchall()
    columns = [(row[1], row[2].upper(), row[3], row[5]) for row in rows]
    expected = [
        ("namespace_id", "TEXT", 1, 1),
        ("binding_json", "TEXT", 1, 0),
        ("sequence", "INTEGER", 1, 0),
        ("source_event_id", "TEXT", 1, 0),
        ("fingerprint", "TEXT", 1, 0),
    ]
    if columns != expected:
        raise _CheckpointFailureError("CHECKPOINT_CORRUPT")


def _read_checkpoint(path: Path, namespace: _CursorNamespace) -> ExecutionMonitorCheckpoint:
    try:
        connection = _connect(path)
        try:
            _validate_database_identity(connection)
            row = connection.execute(
                """
                SELECT binding_json, sequence, source_event_id, fingerprint
                FROM relay_checkpoints WHERE namespace_id = ?
                """,
                (namespace.namespace_id,),
            ).fetchone()
        finally:
            connection.close()
    except _CheckpointFailureError:
        raise
    except Exception:
        raise _CheckpointFailureError("CHECKPOINT_UNAVAILABLE") from None
    if row is None:
        return ExecutionMonitorCheckpoint()
    binding_json, sequence, source_event_id, fingerprint = row
    if binding_json != namespace.binding_json:
        raise _CheckpointFailureError("CHECKPOINT_CONFLICT")
    if (
        type(sequence) is not int
        or not 1 <= sequence < _SEQUENCE_LIMIT
        or not _valid_text(source_event_id, _MAX_TEXT_BYTES)
        or type(fingerprint) is not str
        or _SHA256_RE.fullmatch(fingerprint) is None
    ):
        raise _CheckpointFailureError("CHECKPOINT_CORRUPT")
    return ExecutionMonitorCheckpoint(sequence, source_event_id, fingerprint)


def _advance_checkpoint(
    path: Path,
    namespace: _CursorNamespace,
    *,
    expected_sequence: int,
    sequence: int,
    source_event_id: str,
    fingerprint: str,
) -> str:
    """Atomically advance one cursor or acknowledge an exact duplicate.

    This module-level boundary is intentionally narrow so tests can inject a
    one-shot local persistence failure after a successful sink receipt.
    """

    if (
        type(expected_sequence) is not int
        or expected_sequence < 0
        or type(sequence) is not int
        or not expected_sequence < sequence < _SEQUENCE_LIMIT
        or not _valid_text(source_event_id, _MAX_TEXT_BYTES)
        or type(fingerprint) is not str
        or _SHA256_RE.fullmatch(fingerprint) is None
    ):
        raise _CheckpointFailureError("CHECKPOINT_CONFLICT")
    try:
        connection = _connect(path)
        try:
            _validate_database_identity(connection)
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT binding_json, sequence, source_event_id, fingerprint
                FROM relay_checkpoints WHERE namespace_id = ?
                """,
                (namespace.namespace_id,),
            ).fetchone()
            if row is None:
                if expected_sequence != 0:
                    raise _CheckpointFailureError("CHECKPOINT_CONFLICT")
                count = connection.execute("SELECT count(*) FROM relay_checkpoints").fetchone()[0]
                if type(count) is not int or count >= _MAX_CHECKPOINT_RECORDS:
                    raise _CheckpointFailureError("CHECKPOINT_CAPACITY")
                connection.execute(
                    """
                    INSERT INTO relay_checkpoints
                        (namespace_id, binding_json, sequence, source_event_id, fingerprint)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        namespace.namespace_id,
                        namespace.binding_json,
                        sequence,
                        source_event_id,
                        fingerprint,
                    ),
                )
                connection.commit()
                return "advanced"

            binding_json, current_sequence, current_event_id, current_fingerprint = row
            if binding_json != namespace.binding_json:
                raise _CheckpointFailureError("CHECKPOINT_CONFLICT")
            if (
                type(current_sequence) is not int
                or not 1 <= current_sequence < _SEQUENCE_LIMIT
                or not _valid_text(current_event_id, _MAX_TEXT_BYTES)
                or type(current_fingerprint) is not str
                or _SHA256_RE.fullmatch(current_fingerprint) is None
            ):
                raise _CheckpointFailureError("CHECKPOINT_CORRUPT")

            if current_sequence == sequence:
                if current_event_id == source_event_id and current_fingerprint == fingerprint:
                    connection.commit()
                    return "duplicate"
                raise _CheckpointFailureError("CHECKPOINT_CONFLICT")
            if current_sequence != expected_sequence:
                # SELECT occurs after BEGIN IMMEDIATE: this is the fresh cursor
                # read required after a competing relay won the prior CAS.
                raise _CheckpointFailureError("CHECKPOINT_CONFLICT")
            connection.execute(
                """
                UPDATE relay_checkpoints
                SET sequence = ?, source_event_id = ?, fingerprint = ?
                WHERE namespace_id = ? AND sequence = ?
                """,
                (
                    sequence,
                    source_event_id,
                    fingerprint,
                    namespace.namespace_id,
                    expected_sequence,
                ),
            )
            if connection.total_changes != 1:
                raise _CheckpointFailureError("CHECKPOINT_CONFLICT")
            connection.commit()
            return "advanced"
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    except _CheckpointFailureError:
        raise
    except Exception:
        raise _CheckpointFailureError("LOCAL_CHECKPOINT_FAILED") from None


def _connect(path: Path, *, create: bool = False) -> sqlite3.Connection:
    mode = "rwc" if create else "rw"
    uri = path.resolve(strict=False).as_uri() + "?mode=" + mode
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            uri,
            uri=True,
            timeout=0.5,
            isolation_level=None,
        )
        connection.execute("PRAGMA busy_timeout=500")
        return connection
    except Exception:
        if connection is not None:
            connection.close()
        raise


def _validate_database_identity(connection: sqlite3.Connection) -> None:
    app_id = connection.execute("PRAGMA application_id").fetchone()[0]
    user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if app_id != _CHECKPOINT_APPLICATION_ID or user_version != _CHECKPOINT_USER_VERSION:
        raise _CheckpointFailureError("CHECKPOINT_CORRUPT")
    _validate_checkpoint_schema(connection)


def _event_fingerprint(event: object) -> str:
    return _sha256(
        _canonical_json(
            {
                "sequence": event.sequence,
                "stream": event.stream,
                "event_id": event.event_id,
                "scope_key": event.scope_key,
                "event_type": event.event_type,
                "state": event.state,
                "created_at_ns": event.created_at_ns,
                "journal_generation": event.journal_generation,
                "intent_id": event.intent_id,
                "cancel_id": event.cancel_id,
                "target_intent_id": event.target_intent_id,
            }
        )
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="strict")).hexdigest()


def _valid_text(value: object, max_bytes: int) -> bool:
    if type(value) is not str or not value or value != value.strip():
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return len(encoded) <= max_bytes and all(
        ord(character) >= 32 and ord(character) != 127 for character in value
    )


def _valid_optional_text(value: object, max_bytes: int) -> bool:
    return value is None or _valid_text(value, max_bytes)


def _sink_error_code(code: str) -> str:
    known = {
        "WRONG_OWNER_THREAD",
        "INGRESS_BINDING_UNAVAILABLE",
        "INGRESS_BINDING_CHANGED",
        "INGRESS_RPC_BINDING_CHANGED",
        "OUTBOX_SCOPE_MISMATCH",
        "INVALID_INGRESS_RECEIPT",
        "MONITOR_OUTBOX_UNAVAILABLE",
        "INVALID_OUTBOX_EVENT",
    }
    return code if code in known else "REMOTE_DELIVERY_FAILED"


__all__ = [
    "ExecutionMonitorCheckpoint",
    "ExecutionMonitorPollResult",
    "ServiceExecutionMonitorRelay",
    "ServiceExecutionMonitorRelayError",
    "execution_monitor_relay_configured",
]
