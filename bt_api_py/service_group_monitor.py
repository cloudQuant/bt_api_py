"""Explicit, bounded relay from a group coordination journal to service Monitor.

The relay is deliberately opt-in. It borrows an already registered service
runtime, owns only its local cursor database, and performs RPCs only when
:meth:`poll` is called by its owner thread. It never runs from
``ServiceHost.maintain`` or the order path.

Group residual facts (``GROUP_RECOVERY_REQUIRED`` rows of the execution group
journal) are already committed with their business row in the coordinator's
SQLite transaction. This module opens that journal read-only, projects only
the trusted residual/unhedged lifecycle metadata (plus the immutable group
request's account/group/strategy scope), and appends it to Monitor's durable
ingress. It never reads an order payload, provider economic fact, or mutable
snapshot field: the projected document is a pure function of one immutable
``order_group_events`` row and its immutable request row, so replays after a
crash produce the identical Monitor event ID and payload.

The journal reader derives its ``generation`` from the journal file identity
(device/inode) captured at construction because the group journal stores no
journal-incarnation identifier of its own. A replaced or rewritten journal
fails closed instead of skipping facts.

The relay's fixed Monitor ingress wire scope must be at least as narrow as
the facts it carries: a configured ``portfolio_ref``, ``strategy_ref``, or
``account_ref`` constrains every delivered event, and a journal row outside
that scope fails the poll closed (``SCOPE_MISMATCH``) rather than being
silently dropped. A coordinator journal holding several portfolios therefore
needs one relay per matching Monitor scope.
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
from decimal import Decimal, InvalidOperation
from pathlib import Path

from bt_api_base.service_protocol import WireScope

from bt_api_py.service_config import ServiceConfig
from bt_api_py.service_monitor import (
    ServiceMonitorOutboxSink,
    ServiceMonitorOutboxSinkError,
)
from bt_api_py.service_runtime import RegisteredBinding, ServiceClientRuntime

_PROJECTION_VERSION = 1
_PROJECTION_SCHEMA = "sdk.group_monitor_projection.v1"
_SCOPE_SCHEMA = "sdk.group_monitor_scope.v1"
_MAX_POLL_LIMIT = 128
_PAGE_SIZE = 100
_MAX_CHECKPOINT_RECORDS = 4096
_MAX_CHECKPOINT_BINDING_BYTES = 2048
_MAX_DATABASE_PAGES = 2048  # 8 MiB with SQLite's default 4 KiB page size.
_MAX_TEXT_BYTES = 256
_MAX_PAGE_LIMIT = 1_000
_MAX_PAGE_FIELD_BYTES = 2 * 1024 * 1024
_SEQUENCE_LIMIT = 1 << 63
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DECIMAL_RE = re.compile(r"[-+]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_CHECKPOINT_APPLICATION_ID = 0x53474D52  # "SGMR"
_CHECKPOINT_USER_VERSION = 1
_STREAM = "group_residual"
_SOURCE_EVENT_TYPE = "GROUP_RECOVERY_REQUIRED"
_SOURCE_STATE = "RECOVERY_REQUIRED"
_MONITOR_EVENT_TYPE = "group.residual"
_PAIR_RESIDUAL_REASON = "hedge_residual_unresolved"
_SUPPORTED_JOURNAL_SCHEMAS = frozenset({1, 2})
_PAIR_KEYS = (
    "first_leg_id",
    "second_leg_id",
    "first_filled_quantity",
    "second_filled_quantity",
    "residual_base_units",
)
_ALLOWED_SOURCE_PAYLOAD_KEYS = frozenset(_PAIR_KEYS) | {
    "code",
    "budget_state",
    "budget_transition",
}

_RESIDUAL_PAGE_SQL = """
SELECT
    e.sequence,
    e.portfolio_ref,
    e.group_id,
    e.revision,
    e.event_type,
    e.payload_json,
    e.event_sha256,
    e.created_at_ns,
    s.request_json,
    s.request_fingerprint
FROM order_group_events AS e
JOIN order_group_snapshots AS s
    ON s.portfolio_ref = e.portfolio_ref AND s.group_id = e.group_id
WHERE e.event_type = ? AND e.sequence > ?
ORDER BY e.sequence ASC
LIMIT ?
"""


class GroupMonitorReaderError(RuntimeError):
    """A read-only group journal read failed with a finite reason code."""

    _CODES = frozenset(
        {
            "invalid_database_path",
            "database_missing",
            "database_unavailable",
            "journal_unavailable",
            "malformed_journal",
            "malformed_event",
            "invalid_cursor",
            "invalid_limit",
            "reader_closed",
            "source_changed",
            "generation_changed",
            "page_too_large",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("unsupported group monitor reader error code")
        self.code = code
        super().__init__(code)


class ServiceGroupMonitorRelayError(RuntimeError):
    """Finite relay diagnostic with only confirmed delivery/checkpoint counts."""

    def __init__(
        self,
        code: str,
        *,
        last_poll: GroupMonitorPollResult | None = None,
    ) -> None:
        self.code = code
        self.last_poll = last_poll or GroupMonitorPollResult()
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class GroupMonitorPollResult:
    """Counts for the most recent explicit poll.

    ``remote_delivered`` counts only events for which the sink returned a
    validated ingress receipt. ``checkpointed`` counts local cursor advances
    or an exact idempotent acknowledgement found after a competing relay won
    the same cursor CAS.
    """

    residual_delivered: int = 0
    remote_delivered: int = 0
    checkpointed: int = 0

    @property
    def total_delivered(self) -> int:
        """Return the number of remotely confirmed event deliveries."""

        return self.remote_delivered


@dataclass(frozen=True, slots=True)
class GroupMonitorCheckpoint:
    """The latest durable local cursor for the residual stream."""

    sequence: int = 0
    source_event_id: str | None = None
    fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class GroupResidualEvent:
    """One immutable group residual fact from the coordination journal.

    ``state`` is fixed to the only state ``GROUP_RECOVERY_REQUIRED`` rows can
    carry. ``reason`` is the coordinator's payload ``code`` when present, or
    the pair-residual reason when the payload carries filled quantities.
    """

    sequence: int
    event_sha256: str
    revision: int
    portfolio_ref: str
    group_id: str
    strategy_id: str
    account_refs: tuple[str, ...]
    state: str
    reason: str
    residual_base_units: str | None
    first_leg_id: str | None
    second_leg_id: str | None
    first_filled_quantity: str | None
    second_filled_quantity: str | None
    created_at_ns: int
    journal_generation: str


@dataclass(frozen=True, slots=True)
class GroupResidualPage:
    """A page from one group journal incarnation."""

    generation: str
    events: tuple[GroupResidualEvent, ...]


@dataclass(frozen=True, slots=True)
class _CursorNamespace:
    namespace_id: str
    binding_json: str


class _CheckpointFailureError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SqliteGroupResidualReader:
    """Read ``GROUP_RECOVERY_REQUIRED`` facts from an existing group journal.

    This reader opens the already-migrated coordinator journal through a
    read-only URI and never constructs ``SqliteOrderGroupWriter`` state of
    its own. Each row is verified against the journal's own stored digests
    before any fact is exposed: the event digest (over group ID, event type,
    payload, and timestamp), the immutable request digest, and per-field
    shape/allow-list checks. Pages are bounded and no cursor is persisted
    here; the relay owns its separate local checkpoint database.
    """

    def __init__(self, database_path: str | os.PathLike[str]) -> None:
        # Domain contracts are imported lazily so ordinary SDK imports stay
        # independent of the optional Execution package.
        try:
            from bt_api_execution.contracts import (
                canonical_json as contract_canonical_json,
            )
            from bt_api_execution.contracts import payload_sha256
            from bt_api_execution.group_order_contracts import GroupOrderIntent
        except ImportError:
            raise GroupMonitorReaderError("journal_unavailable") from None
        self._canonical_json_contract = contract_canonical_json
        self._payload_sha256 = payload_sha256
        self._intent_type = GroupOrderIntent

        try:
            raw_path = os.fspath(database_path)
        except (TypeError, ValueError) as error:
            raise GroupMonitorReaderError("invalid_database_path") from error
        if type(raw_path) is not str or not raw_path:
            raise GroupMonitorReaderError("invalid_database_path")
        try:
            resolved_path = Path(raw_path).resolve(strict=True)
        except FileNotFoundError as error:
            raise GroupMonitorReaderError("database_missing") from error
        except (OSError, RuntimeError, ValueError) as error:
            raise GroupMonitorReaderError("invalid_database_path") from error
        try:
            initial_stat = resolved_path.stat()
        except FileNotFoundError as error:
            raise GroupMonitorReaderError("database_missing") from error
        except OSError as error:
            raise GroupMonitorReaderError("database_unavailable") from error
        if not resolved_path.is_file():
            raise GroupMonitorReaderError("database_unavailable")

        self._database_path = resolved_path
        self._source_identity = (
            int(initial_stat.st_dev),
            int(initial_stat.st_ino),
        )
        self._closed = False
        self._uri = f"{resolved_path.as_uri()}?mode=ro"
        self._schema_version = self._load_initial_schema_version()
        self._generation = _sha256(
            _canonical_json(
                {
                    "schema": _SCOPE_SCHEMA,
                    "database_identity": [self._source_identity[0], self._source_identity[1]],
                    "journal_schema_version": self._schema_version,
                }
            )
        )

    @property
    def database_path(self) -> Path:
        """Return the resolved path of the existing journal file."""

        return self._database_path

    @property
    def source_identity(self) -> tuple[int, int]:
        """Return the local ``(st_dev, st_ino)`` identity captured at construction."""

        return self._source_identity

    @property
    def generation(self) -> str:
        """Return the journal incarnation digest captured at construction."""

        return self._generation

    @property
    def schema_version(self) -> int:
        """Return the verified group journal schema version."""

        return self._schema_version

    def close(self) -> None:
        """Close the reader logically; connections are short-lived per page."""

        self._closed = True

    def read_residual_page(
        self,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> GroupResidualPage:
        """Return one bounded page of residual facts without advancing a cursor."""

        if self._closed:
            raise GroupMonitorReaderError("reader_closed")
        if type(after_sequence) is not int or not 0 <= after_sequence < _SEQUENCE_LIMIT:
            raise GroupMonitorReaderError("invalid_cursor")
        if type(limit) is not int or not 1 <= limit <= _MAX_PAGE_LIMIT:
            raise GroupMonitorReaderError("invalid_limit")
        if self._current_source_identity() != self._source_identity:
            raise GroupMonitorReaderError("source_changed")

        try:
            connection = self._connect()
        except sqlite3.Error as error:
            raise GroupMonitorReaderError("database_unavailable") from error

        events: tuple[GroupResidualEvent, ...]
        try:
            connection.execute("BEGIN")
            if self._read_schema_version(connection) != self._schema_version:
                raise GroupMonitorReaderError("generation_changed")
            rows = connection.execute(
                _RESIDUAL_PAGE_SQL,
                (_SOURCE_EVENT_TYPE, after_sequence, limit),
            ).fetchall()
            events = tuple(self._event_from_row(row) for row in rows)
            if self._read_schema_version(connection) != self._schema_version:
                raise GroupMonitorReaderError("generation_changed")
            if self._page_field_bytes(events) > _MAX_PAGE_FIELD_BYTES:
                raise GroupMonitorReaderError("page_too_large")
            connection.execute("COMMIT")
        except GroupMonitorReaderError:
            raise
        except (sqlite3.Error, UnicodeError, json.JSONDecodeError) as error:
            raise GroupMonitorReaderError("journal_unavailable") from error
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

        if self._current_source_identity() != self._source_identity:
            raise GroupMonitorReaderError("source_changed")
        return GroupResidualPage(generation=self._generation, events=events)

    def _load_initial_schema_version(self) -> int:
        if self._current_source_identity() != self._source_identity:
            raise GroupMonitorReaderError("source_changed")
        try:
            connection = self._connect()
        except sqlite3.Error as error:
            raise GroupMonitorReaderError("journal_unavailable") from error
        try:
            connection.execute("BEGIN")
            schema_version = self._read_schema_version(connection)
            connection.execute("COMMIT")
        except GroupMonitorReaderError:
            raise
        except (sqlite3.Error, UnicodeError) as error:
            raise GroupMonitorReaderError("journal_unavailable") from error
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
        if self._current_source_identity() != self._source_identity:
            raise GroupMonitorReaderError("source_changed")
        return schema_version

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._uri, uri=True, isolation_level=None, timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            query_only = connection.execute("PRAGMA query_only").fetchone()
            if query_only is None or type(query_only[0]) is not int or query_only[0] != 1:
                raise sqlite3.OperationalError("query_only is unavailable")
            return connection
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def _read_schema_version(connection: sqlite3.Connection) -> int:
        try:
            row = connection.execute(
                """
                SELECT CASE WHEN typeof(schema_version) = 'integer'
                            THEN schema_version END AS schema_version
                FROM execution_group_meta
                WHERE singleton = 1
                """
            ).fetchone()
        except sqlite3.Error as error:
            raise GroupMonitorReaderError("journal_unavailable") from error
        if row is None:
            raise GroupMonitorReaderError("journal_unavailable")
        schema_version = row["schema_version"]
        if type(schema_version) is not int or schema_version not in _SUPPORTED_JOURNAL_SCHEMAS:
            raise GroupMonitorReaderError("malformed_journal")
        return schema_version

    def _current_source_identity(self) -> tuple[int, int]:
        try:
            stat_result = self._database_path.stat()
        except FileNotFoundError as error:
            raise GroupMonitorReaderError("source_changed") from error
        except OSError as error:
            raise GroupMonitorReaderError("database_unavailable") from error
        if not self._database_path.is_file():
            raise GroupMonitorReaderError("source_changed")
        return (int(stat_result.st_dev), int(stat_result.st_ino))

    def _event_from_row(self, row: sqlite3.Row) -> GroupResidualEvent:
        sequence = row["sequence"]
        portfolio_ref = row["portfolio_ref"]
        group_id = row["group_id"]
        revision = row["revision"]
        event_type = row["event_type"]
        payload_json = row["payload_json"]
        event_sha256 = row["event_sha256"]
        created_at_ns = row["created_at_ns"]
        request_json = row["request_json"]
        request_fingerprint = row["request_fingerprint"]
        if (
            type(sequence) is not int
            or not 1 <= sequence < _SEQUENCE_LIMIT
            or not _valid_text(portfolio_ref, _MAX_TEXT_BYTES)
            or not _valid_text(group_id, _MAX_TEXT_BYTES)
            or type(revision) is not int
            or not 0 <= revision < _SEQUENCE_LIMIT
            or event_type != _SOURCE_EVENT_TYPE
            or type(payload_json) is not str
            or type(event_sha256) is not str
            or _SHA256_RE.fullmatch(event_sha256) is None
            or type(created_at_ns) is not int
            or created_at_ns <= 0
            or type(request_json) is not str
            or type(request_fingerprint) is not str
            or _SHA256_RE.fullmatch(request_fingerprint) is None
        ):
            raise GroupMonitorReaderError("malformed_event")

        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as error:
            raise GroupMonitorReaderError("malformed_event") from error
        if (
            not isinstance(payload, Mapping)
            or any(type(key) is not str for key in payload)
            or self._canonical_json_contract(dict(payload)) != payload_json
        ):
            raise GroupMonitorReaderError("malformed_event")
        digest = self._payload_sha256(
            {
                "group_id": group_id,
                "event_type": _SOURCE_EVENT_TYPE,
                "payload": dict(payload),
                "created_at_ns": created_at_ns,
            }
        )
        if digest != event_sha256:
            raise GroupMonitorReaderError("malformed_event")

        try:
            request_payload = json.loads(request_json)
        except json.JSONDecodeError as error:
            raise GroupMonitorReaderError("malformed_event") from error
        if (
            not isinstance(request_payload, Mapping)
            or self._canonical_json_contract(dict(request_payload)) != request_json
        ):
            raise GroupMonitorReaderError("malformed_event")
        try:
            request = self._intent_type.from_payload(request_payload)
        except Exception:
            raise GroupMonitorReaderError("malformed_event") from None
        if request.fingerprint != request_fingerprint or request.portfolio_ref != portfolio_ref:
            raise GroupMonitorReaderError("malformed_event")

        keys = frozenset(payload)
        if keys - _ALLOWED_SOURCE_PAYLOAD_KEYS:
            raise GroupMonitorReaderError("malformed_event")
        has_pair = "residual_base_units" in payload
        has_code = "code" in payload
        if has_pair == has_code:
            raise GroupMonitorReaderError("malformed_event")
        if has_pair:
            if not frozenset(_PAIR_KEYS) <= keys:
                raise GroupMonitorReaderError("malformed_event")
            first_leg_id = payload["first_leg_id"]
            second_leg_id = payload["second_leg_id"]
            first_filled_quantity = payload["first_filled_quantity"]
            second_filled_quantity = payload["second_filled_quantity"]
            residual_base_units = payload["residual_base_units"]
            if (
                not _valid_identifier(first_leg_id)
                or not _valid_identifier(second_leg_id)
                or not _valid_decimal_text(first_filled_quantity)
                or not _valid_decimal_text(second_filled_quantity)
                or not _valid_decimal_text(residual_base_units)
            ):
                raise GroupMonitorReaderError("malformed_event")
            reason = _PAIR_RESIDUAL_REASON
        else:
            code = payload["code"]
            if not _valid_identifier(code):
                raise GroupMonitorReaderError("malformed_event")
            reason = code
            residual_base_units = None
            first_leg_id = None
            second_leg_id = None
            first_filled_quantity = None
            second_filled_quantity = None

        return GroupResidualEvent(
            sequence=sequence,
            event_sha256=event_sha256,
            revision=revision,
            portfolio_ref=portfolio_ref,
            group_id=group_id,
            strategy_id=request.strategy_id,
            account_refs=request.account_refs,
            state=_SOURCE_STATE,
            reason=reason,
            residual_base_units=residual_base_units,
            first_leg_id=first_leg_id,
            second_leg_id=second_leg_id,
            first_filled_quantity=first_filled_quantity,
            second_filled_quantity=second_filled_quantity,
            created_at_ns=created_at_ns,
            journal_generation=self._generation,
        )

    @staticmethod
    def _page_field_bytes(events: tuple[GroupResidualEvent, ...]) -> int:
        total = 0
        for event in events:
            values = (
                event.event_sha256,
                event.portfolio_ref,
                event.group_id,
                event.strategy_id,
                event.reason,
                event.residual_base_units,
                event.first_leg_id,
                event.second_leg_id,
                event.first_filled_quantity,
                event.second_filled_quantity,
                event.journal_generation,
                *event.account_refs,
            )
            total += sum(len(value.encode("utf-8")) for value in values if value is not None)
        return total


class ServiceGroupMonitorRelay:
    """Relay retained group residual facts over Monitor's durable ingress.

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
            raise ServiceGroupMonitorRelayError("INVALID_RUNTIME")
        if type(scope_ref) is not str or not _valid_text(scope_ref, 128):
            raise ServiceGroupMonitorRelayError("INVALID_SCOPE_REF")
        if not isinstance(checkpoint_path, (str, Path)) or not str(checkpoint_path):
            raise ServiceGroupMonitorRelayError("INVALID_CHECKPOINT_PATH")
        if type(reader) is not SqliteGroupResidualReader:
            raise ServiceGroupMonitorRelayError("INVALID_JOURNAL_READER")
        generation = getattr(reader, "generation", None)
        identity = getattr(reader, "source_identity", None)
        if (
            not _valid_text(generation, 128)
            or type(identity) is not tuple
            or len(identity) != 2
            or any(type(value) is not int or value < 0 for value in identity)
        ):
            raise ServiceGroupMonitorRelayError("INVALID_JOURNAL_NAMESPACE")

        try:
            ingress_scope = self._validate_ingress_scope(runtime, scope_ref)
            outbox_scope = _outbox_scope(generation)
            sink = ServiceMonitorOutboxSink(
                runtime,
                scope_ref=scope_ref,
                outbox_scope=outbox_scope,
            )
            source_path, source_identity = _source_identity(reader)
            checkpoint = Path(checkpoint_path).expanduser().resolve(strict=False)
            _reject_same_database(source_path, source_identity, checkpoint)
            _initialize_or_validate_checkpoint(checkpoint)
            namespace = _cursor_namespace(sink.consumer_id, generation, _STREAM)
        except ServiceGroupMonitorRelayError:
            raise
        except ServiceMonitorOutboxSinkError as error:
            raise ServiceGroupMonitorRelayError(error.code) from None
        except _CheckpointFailureError as error:
            raise ServiceGroupMonitorRelayError(error.code) from None
        except Exception:
            raise ServiceGroupMonitorRelayError("RELAY_CONFIGURATION_INVALID") from None

        self._runtime = runtime
        self._reader = reader
        self._scope_ref = scope_ref
        self._ingress_scope = ingress_scope
        self._source_path = source_path
        self._source_identity_value = source_identity
        self._generation = generation
        self._checkpoint_path = checkpoint
        self._outbox_scope = outbox_scope
        self._sink = sink
        self._consumer_id = sink.consumer_id
        self._namespace = namespace
        self._owner_thread_id = owner_thread_id
        self._closed = False
        self._last_poll = GroupMonitorPollResult()

    @property
    def last_poll(self) -> GroupMonitorPollResult:
        """Return the latest completed or partially completed poll counts."""

        return self._last_poll

    @property
    def consumer_id(self) -> str:
        """Return the stable Monitor sink consumer identity."""

        return self._consumer_id

    @property
    def outbox_scope(self) -> str:
        """Return the destination outbox scope derived from the journal identity."""

        return self._outbox_scope

    def checkpoint(self) -> GroupMonitorCheckpoint:
        """Read the durable cursor for the residual stream."""

        self._assert_usable()
        try:
            return _read_checkpoint(self._checkpoint_path, self._namespace)
        except _CheckpointFailureError as error:
            raise ServiceGroupMonitorRelayError(
                error.code, last_poll=self._last_poll
            ) from None

    def rebind(self) -> None:
        """Recreate the borrowed-runtime sink after caller-managed recovery.

        Recovery may renew registration IDs, but the destination identity must
        remain stable. A changed destination cannot inherit this relay's
        checkpoint namespace.
        """

        self._assert_usable()
        try:
            self._validate_reader_identity()
            ingress_scope = self._validate_ingress_scope(self._runtime, self._scope_ref)
            sink = ServiceMonitorOutboxSink(
                self._runtime,
                scope_ref=self._scope_ref,
                outbox_scope=self._outbox_scope,
            )
        except ServiceMonitorOutboxSinkError as error:
            raise ServiceGroupMonitorRelayError(
                error.code, last_poll=self._last_poll
            ) from None
        except ServiceGroupMonitorRelayError:
            raise
        except Exception:
            raise ServiceGroupMonitorRelayError(
                "INGRESS_BINDING_UNAVAILABLE", last_poll=self._last_poll
            ) from None
        if sink.consumer_id != self._consumer_id:
            raise ServiceGroupMonitorRelayError(
                "INGRESS_BINDING_CHANGED", last_poll=self._last_poll
            )
        self._sink = sink
        self._ingress_scope = ingress_scope

    def poll(self, limit: int = 32) -> GroupMonitorPollResult:
        """Poll the residual stream and spend at most ``limit`` ingress RPCs."""

        self._assert_usable()
        if type(limit) is not int or not 0 <= limit <= _MAX_POLL_LIMIT:
            raise ServiceGroupMonitorRelayError("INVALID_POLL_LIMIT")

        counts = {
            "residual_delivered": 0,
            "remote_delivered": 0,
            "checkpointed": 0,
        }
        self._last_poll = GroupMonitorPollResult()
        try:
            self._validate_reader_identity()
            cursor = _read_checkpoint(self._checkpoint_path, self._namespace)
            # The source is not retained away in this round. Verify the
            # existing cursor's anchor before delivering anything, so a
            # replaced, truncated, or rewritten source cannot skip history.
            if cursor.sequence:
                self._verify_anchor(cursor)

            buffer: list[GroupResidualEvent] = []
            page_cursor = cursor.sequence
            while counts["remote_delivered"] < limit:
                if not buffer:
                    buffer = self._read_events(after_sequence=page_cursor, limit=_PAGE_SIZE)
                    if not buffer:
                        break
                selected_event = buffer.pop(0)
                page_cursor = selected_event.sequence

                event = self._project(selected_event)
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
                counts["residual_delivered"] += 1
                self._snapshot_counts(counts)

                # The reader can be backed up or replaced while the RPC is in
                # flight. Recheck the exact just-delivered source row before
                # committing its local acknowledgement.
                self._verify_event_anchor(selected_event)

                fingerprint = _event_fingerprint(selected_event)
                try:
                    outcome = _advance_checkpoint(
                        self._checkpoint_path,
                        self._namespace,
                        expected_sequence=cursor.sequence,
                        sequence=selected_event.sequence,
                        source_event_id=selected_event.event_sha256,
                        fingerprint=fingerprint,
                    )
                except _CheckpointFailureError as error:
                    self._raise_poll_error(error.code, counts)
                except Exception:
                    self._raise_poll_error("LOCAL_CHECKPOINT_FAILED", counts)

                if outcome == "advanced" or outcome == "duplicate":
                    cursor = GroupMonitorCheckpoint(
                        selected_event.sequence,
                        selected_event.event_sha256,
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
        except ServiceGroupMonitorRelayError:
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
            raise ServiceGroupMonitorRelayError(
                "SOURCE_GENERATION_CHANGED", last_poll=self._last_poll
            )
        try:
            current_path, current_identity = _source_identity(self._reader)
        except Exception:
            raise ServiceGroupMonitorRelayError(
                "SOURCE_IDENTITY_UNAVAILABLE", last_poll=self._last_poll
            ) from None
        if current_path != self._source_path or current_identity != self._source_identity_value:
            raise ServiceGroupMonitorRelayError(
                "SOURCE_IDENTITY_CHANGED", last_poll=self._last_poll
            )

    @staticmethod
    def _validate_ingress_scope(
        runtime: ServiceClientRuntime,
        scope_ref: str,
    ) -> WireScope:
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
            raise ServiceGroupMonitorRelayError("INGRESS_BINDING_UNAVAILABLE")
        wire_scope = candidates[0].scope
        if type(wire_scope) is not WireScope:
            raise ServiceGroupMonitorRelayError("INGRESS_BINDING_UNAVAILABLE")
        if (
            wire_scope.account_refs
            or wire_scope.group_id is not None
            or wire_scope.leg_id is not None
            or wire_scope.manual_book_ref is not None
            or wire_scope.run_id is not None
        ):
            # Monitor's own ingress rejects account_refs/group_id/leg_id, and a
            # run-scoped grant cannot describe a cross-run group fact.
            raise ServiceGroupMonitorRelayError("UNSUPPORTED_INGRESS_SCOPE")
        return wire_scope

    def _verify_anchor(self, cursor: GroupMonitorCheckpoint) -> None:
        events = self._read_events(after_sequence=cursor.sequence - 1, limit=1)
        if not events or events[0].sequence != cursor.sequence:
            raise ServiceGroupMonitorRelayError(
                "SOURCE_CHECKPOINT_DIVERGED", last_poll=self._last_poll
            )
        event = events[0]
        if (
            event.event_sha256 != cursor.source_event_id
            or _event_fingerprint(event) != cursor.fingerprint
        ):
            raise ServiceGroupMonitorRelayError(
                "SOURCE_CHECKPOINT_DIVERGED", last_poll=self._last_poll
            )

    def _verify_event_anchor(self, expected_event: GroupResidualEvent) -> None:
        events = self._read_events(after_sequence=expected_event.sequence - 1, limit=1)
        if not events or events[0].sequence != expected_event.sequence:
            raise ServiceGroupMonitorRelayError(
                "SOURCE_CHECKPOINT_DIVERGED", last_poll=self._last_poll
            )
        if (
            events[0].event_sha256 != expected_event.event_sha256
            or _event_fingerprint(events[0]) != _event_fingerprint(expected_event)
        ):
            raise ServiceGroupMonitorRelayError(
                "SOURCE_CHECKPOINT_DIVERGED", last_poll=self._last_poll
            )

    def _read_events(self, *, after_sequence: int, limit: int) -> list[GroupResidualEvent]:
        try:
            page = self._reader.read_residual_page(
                after_sequence=after_sequence,
                limit=limit,
            )
        except Exception:
            raise ServiceGroupMonitorRelayError(
                "SOURCE_READ_FAILED", last_poll=self._last_poll
            ) from None
        if type(page) is not GroupResidualPage or page.generation != self._generation:
            raise ServiceGroupMonitorRelayError(
                "SOURCE_GENERATION_CHANGED", last_poll=self._last_poll
            )
        if type(page.events) is not tuple:
            raise ServiceGroupMonitorRelayError(
                "SOURCE_PAGE_INVALID", last_poll=self._last_poll
            )
        events: list[GroupResidualEvent] = []
        prior = after_sequence
        for event in page.events:
            self._validate_event(event)
            if event.sequence <= prior:
                raise ServiceGroupMonitorRelayError(
                    "SOURCE_PAGE_INVALID", last_poll=self._last_poll
                )
            prior = event.sequence
            events.append(event)
        if len(events) > limit:
            raise ServiceGroupMonitorRelayError(
                "SOURCE_PAGE_INVALID", last_poll=self._last_poll
            )
        return events

    def _validate_event(self, event: object) -> None:
        if type(event) is not GroupResidualEvent:
            raise ServiceGroupMonitorRelayError(
                "SOURCE_EVENT_INVALID", last_poll=self._last_poll
            )
        if (
            type(event.sequence) is not int
            or not 1 <= event.sequence < _SEQUENCE_LIMIT
            or type(event.event_sha256) is not str
            or _SHA256_RE.fullmatch(event.event_sha256) is None
            or type(event.revision) is not int
            or not 0 <= event.revision < _SEQUENCE_LIMIT
            or not _valid_text(event.portfolio_ref, _MAX_TEXT_BYTES)
            or not _valid_text(event.group_id, _MAX_TEXT_BYTES)
            or not _valid_text(event.strategy_id, _MAX_TEXT_BYTES)
            or type(event.account_refs) is not tuple
            or not event.account_refs
            or len(set(event.account_refs)) != len(event.account_refs)
            or any(not _valid_text(item, _MAX_TEXT_BYTES) for item in event.account_refs)
            or event.state != _SOURCE_STATE
            or not _valid_identifier(event.reason)
            or type(event.created_at_ns) is not int
            or event.created_at_ns <= 0
            or event.journal_generation != self._generation
        ):
            raise ServiceGroupMonitorRelayError(
                "SOURCE_EVENT_INVALID", last_poll=self._last_poll
            )
        pair_values = (
            event.first_leg_id,
            event.second_leg_id,
            event.first_filled_quantity,
            event.second_filled_quantity,
        )
        if event.residual_base_units is None:
            if any(value is not None for value in pair_values) or event.reason == _PAIR_RESIDUAL_REASON:
                raise ServiceGroupMonitorRelayError(
                    "SOURCE_EVENT_INVALID", last_poll=self._last_poll
                )
        else:
            if not _valid_decimal_text(event.residual_base_units):
                raise ServiceGroupMonitorRelayError(
                    "SOURCE_EVENT_INVALID", last_poll=self._last_poll
                )
            if (
                not _valid_identifier(event.first_leg_id)
                or not _valid_identifier(event.second_leg_id)
                or not _valid_decimal_text(event.first_filled_quantity)
                or not _valid_decimal_text(event.second_filled_quantity)
                or event.reason != _PAIR_RESIDUAL_REASON
            ):
                raise ServiceGroupMonitorRelayError(
                    "SOURCE_EVENT_INVALID", last_poll=self._last_poll
                )
        wire_scope = self._ingress_scope
        if (
            wire_scope.portfolio_ref is not None
            and wire_scope.portfolio_ref != event.portfolio_ref
        ):
            raise ServiceGroupMonitorRelayError("SCOPE_MISMATCH", last_poll=self._last_poll)
        if (
            wire_scope.strategy_ref is not None
            and wire_scope.strategy_ref != event.strategy_id
        ):
            raise ServiceGroupMonitorRelayError("SCOPE_MISMATCH", last_poll=self._last_poll)
        if wire_scope.account_ref is not None and wire_scope.account_ref not in event.account_refs:
            raise ServiceGroupMonitorRelayError("SCOPE_MISMATCH", last_poll=self._last_poll)

    def _project(self, source_event: GroupResidualEvent) -> object:
        self._validate_event(source_event)
        try:
            from bt_api_monitor.durable import OutboxEvent
        except ImportError:
            raise ServiceGroupMonitorRelayError(
                "MONITOR_OUTBOX_UNAVAILABLE", last_poll=self._last_poll
            ) from None
        event_id = _projected_event_id(
            self._generation,
            source_event.portfolio_ref,
            source_event.group_id,
            source_event.event_sha256,
        )
        data = {
            "schema": _PROJECTION_SCHEMA,
            "journal_generation": self._generation,
            "source_sequence": source_event.sequence,
            "source_event_sha256": source_event.event_sha256,
            "source_revision": source_event.revision,
            "source_created_at_ns": source_event.created_at_ns,
            "portfolio_ref": source_event.portfolio_ref,
            "group_id": source_event.group_id,
            "strategy_id": source_event.strategy_id,
            "account_refs": list(source_event.account_refs),
            "state": source_event.state,
            "reason": source_event.reason,
            "residual_base_units": source_event.residual_base_units,
            "first_leg_id": source_event.first_leg_id,
            "second_leg_id": source_event.second_leg_id,
            "first_filled_quantity": source_event.first_filled_quantity,
            "second_filled_quantity": source_event.second_filled_quantity,
        }
        return OutboxEvent(
            event_id=event_id,
            scope=self._outbox_scope,
            event_type=_MONITOR_EVENT_TYPE,
            data=data,
            occurred_at=source_event.created_at_ns / 1_000_000_000,
        )

    def _assert_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceGroupMonitorRelayError(
                "WRONG_OWNER_THREAD", last_poll=self._last_poll
            )

    def _assert_usable(self) -> None:
        self._assert_owner_thread()
        if self._closed:
            raise ServiceGroupMonitorRelayError("RELAY_CLOSED", last_poll=self._last_poll)

    def _snapshot_counts(self, counts: Mapping[str, int]) -> GroupMonitorPollResult:
        result = GroupMonitorPollResult(**dict(counts))
        self._last_poll = result
        return result

    def _raise_poll_error(self, code: str, counts: Mapping[str, int]) -> None:
        result = self._snapshot_counts(counts)
        raise ServiceGroupMonitorRelayError(code, last_poll=result) from None


def group_monitor_relay_configured(
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


def _outbox_scope(generation: str) -> str:
    digest = _sha256(
        _canonical_json(
            {
                "schema": _SCOPE_SCHEMA,
                "stream": _STREAM,
                "generation": generation,
            }
        )
    )
    return "sdk-group-monitor-v1:" + digest


def _projected_event_id(
    generation: str,
    portfolio_ref: str,
    group_id: str,
    source_event_sha256: str,
) -> str:
    digest = _sha256(
        _canonical_json(
            {
                "schema": _PROJECTION_SCHEMA,
                "generation": generation,
                "portfolio_ref": portfolio_ref,
                "group_id": group_id,
                "source_event_sha256": source_event_sha256,
            }
        )
    )
    return "service-event:" + digest


def _cursor_namespace(
    consumer_id: str,
    generation: str,
    stream: str,
) -> _CursorNamespace:
    binding = {
        "schema": _SCOPE_SCHEMA,
        "consumer_id": consumer_id,
        "generation": generation,
        "stream": stream,
        "projection_version": _PROJECTION_VERSION,
    }
    binding_json = _canonical_json(binding)
    if len(binding_json.encode("utf-8")) > _MAX_CHECKPOINT_BINDING_BYTES:
        raise ServiceGroupMonitorRelayError("CHECKPOINT_NAMESPACE_TOO_LARGE")
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
        raise ServiceGroupMonitorRelayError("CHECKPOINT_MUST_BE_SEPARATE")
    try:
        checkpoint_info = checkpoint_path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(checkpoint_info.st_mode):
        raise ServiceGroupMonitorRelayError("INVALID_CHECKPOINT_FILE")
    if (checkpoint_info.st_dev, checkpoint_info.st_ino) == source_identity:
        raise ServiceGroupMonitorRelayError("CHECKPOINT_MUST_BE_SEPARATE")
    try:
        if os.path.samefile(source_path, checkpoint_path):
            raise ServiceGroupMonitorRelayError("CHECKPOINT_MUST_BE_SEPARATE")
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


def _read_checkpoint(path: Path, namespace: _CursorNamespace) -> GroupMonitorCheckpoint:
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
        return GroupMonitorCheckpoint()
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
    return GroupMonitorCheckpoint(sequence, source_event_id, fingerprint)


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


def _event_fingerprint(event: GroupResidualEvent) -> str:
    return _sha256(
        _canonical_json(
            {
                "sequence": event.sequence,
                "event_sha256": event.event_sha256,
                "revision": event.revision,
                "portfolio_ref": event.portfolio_ref,
                "group_id": event.group_id,
                "strategy_id": event.strategy_id,
                "account_refs": list(event.account_refs),
                "state": event.state,
                "reason": event.reason,
                "residual_base_units": event.residual_base_units,
                "first_leg_id": event.first_leg_id,
                "second_leg_id": event.second_leg_id,
                "first_filled_quantity": event.first_filled_quantity,
                "second_filled_quantity": event.second_filled_quantity,
                "created_at_ns": event.created_at_ns,
                "journal_generation": event.journal_generation,
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


def _valid_identifier(value: object) -> bool:
    return type(value) is str and _IDENTIFIER_RE.fullmatch(value) is not None


def _valid_optional_text(value: object, max_bytes: int) -> bool:
    return value is None or _valid_text(value, max_bytes)


def _valid_decimal_text(value: object) -> bool:
    if type(value) is not str or _DECIMAL_RE.fullmatch(value) is None:
        return False
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return False
    return parsed.is_finite()


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
    "GroupMonitorCheckpoint",
    "GroupMonitorPollResult",
    "GroupMonitorReaderError",
    "GroupResidualEvent",
    "GroupResidualPage",
    "ServiceGroupMonitorRelay",
    "ServiceGroupMonitorRelayError",
    "SqliteGroupResidualReader",
    "group_monitor_relay_configured",
]
