"""Bounded, restart-safe relay from the Execution event projection to Monitor.

The relay consumes the read-only parent/child event projection published by
``bt_api_execution.parent_event_projection`` (``ProjectionPage`` with events,
a canonical token cursor, a durable head and an optional positioned gap) and
forwards only whitelisted lifecycle observations over an injected outbox sink.
It deliberately mirrors the older :mod:`bt_api_py.service_execution_monitor`
relay: an owner thread drives :meth:`ServiceExecutionProjectionRelay.poll`,
only a local checkpoint database is owned here, every source is borrowed and
read-only, and no thread, timer, retry worker or order-path hook is created.

The projection dependency is injected and duck-typed.  A port exposes
``read(after=..., limit=...)`` and may additionally expose a stable
``journal_fingerprint``/``source_fingerprint``/``epoch``/``generation``.  The
relay never imports the Execution package, never opens the journal, and never
calls anything except the injected projection and the injected sink.

Only these observation fields are forwarded: ``schema``, ``event_id``,
``cursor``, ``occurred_at_ns``, ``subject_kind``, ``subject_ref``,
``event_type``, ``state``, ``reason_code`` and a bounded ``detail`` made of a
finite key allowlist and integer/decimal values.  Accounts, prices, order
payloads, provider economics and arbitrary extra fields are never copied.

Checkpoint contract
-------------------

The exact projection token cursor is stored in a local SQLite checkpoint
(together with a local delivery counter).  The checkpoint is namespaced by the
destination (``consumer_id`` + ``outbox_scope``) and its stored binding also
carries any source fingerprint/epoch the port exposes, so a replaced source or
a tampered row is refused instead of silently skipped.  Rules:

* the cursor advances only after the sink accepted the observation;
* a crash between sink acceptance and the checkpoint write may duplicate an
  observation, but the forwarded identity is a deterministic digest of the
  projection ``event_id`` and the destination, so Monitor de-duplicates it;
* a malformed, foreign, expired or source-changed cursor is refused and never
  advanced; a page gap is reported and the relay never advances to ``head``;
* a sink failure, monitor unavailability, explicit rejection or backpressure
  is counted and boundedly backed off; it never raises into the caller.

This is an observation relay, not authority: it establishes no Gateway
acceptance, venue state, account permission or fill.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_EVENT_ID_SCHEMA = "sdk.execution_projection_relay.event_id.v1"
_BINDING_SCHEMA = "sdk.execution_projection_relay.binding.v1"
_MONITOR_EVENT_TYPE = "execution.parent_child_projection"

_MAX_PAGE_LIMIT = 128
_MAX_EVENTS_PER_POLL = 128
_DEFAULT_PAGE_LIMIT = 64
_DEFAULT_EVENTS_PER_POLL = 64
_DEFAULT_BYTE_BUDGET = 256 * 1024
_MAX_BYTE_BUDGET = 4 * 1024 * 1024
_MAX_EVENT_BYTES = 8 * 1024
_MAX_CURSOR_BYTES = 1024
_MAX_TEXT_BYTES = 256
_MAX_SCHEMA_BYTES = 128
_MAX_CONSUMER_ID_BYTES = 128
_MAX_OUTBOX_SCOPE_BYTES = 256
_MAX_DETAIL_ENTRIES = 8
_SEQUENCE_LIMIT = 1 << 63

_MAX_CHECKPOINT_RECORDS = 4096
_MAX_BINDING_BYTES = 2048
_MAX_DATABASE_PAGES = 2048  # 8 MiB with SQLite's default 4 KiB page size.
_CHECKPOINT_APPLICATION_ID = 0x53455052  # "SEPR"
_CHECKPOINT_USER_VERSION = 1

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_UPPER_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_DECIMAL_RE = re.compile(r"[0-9]{1,42}(?:\.[0-9]{1,42})?\Z")

_SUBJECT_KINDS = frozenset({"PARENT", "CHILD", "SCHEDULE", "CANCEL"})
_DETAIL_KEYS = frozenset(
    {"children_planned", "cancels_issued", "missed_windows", "filled", "remainder"}
)

# Optional attributes a projection port may expose to bind the checkpoint.
_SOURCE_BINDING_ATTRS = ("journal_fingerprint", "source_fingerprint", "epoch", "generation")
# Best-effort journal-path attributes used only to refuse a checkpoint that
# would live in the read-only source database itself.
_SOURCE_PATH_ATTRS = ("database_path", "parent_path", "schedule_path", "source_path", "path")

_CURSOR_REFUSAL_CODES = frozenset(
    {
        "INVALID_CURSOR",
        "CURSOR_AHEAD",
        "CURSOR_REGRESSED",
        "CURSOR_UNKNOWN",
        "CURSOR_DIVERGED",
        "CURSOR_JOURNAL_MISMATCH",
        "SOURCE_GAP_BEHIND_CURSOR",
    }
)
_SOURCE_UNAVAILABLE_CODES = frozenset(
    {
        "PROJECTION_UNAVAILABLE",
        "PROJECTION_CLOSED",
        "PROJECTION_SCHEMA_UNSUPPORTED",
        "PARENT_JOURNAL_UNRECOGNIZED",
        "SCHEDULE_JOURNAL_UNRECOGNIZED",
    }
)
_SINK_UNAVAILABLE_CODES = frozenset(
    {
        "MONITOR_OUTBOX_UNAVAILABLE",
        "INGRESS_BINDING_UNAVAILABLE",
        "INGRESS_BINDING_CHANGED",
        "INGRESS_RPC_BINDING_CHANGED",
        "WRONG_OWNER_THREAD",
    }
)
_SINK_REJECTED_CODES = frozenset(
    {
        "OUTBOX_SCOPE_MISMATCH",
        "INVALID_OUTBOX_EVENT",
        "INVALID_INGRESS_RECEIPT",
        "SINK_REJECTED",
    }
)
_SINK_BACKPRESSURE_CODES = frozenset(
    {
        "OUTBOX_CAPACITY",
        "INGRESS_BACKPRESSURE",
        "MONITOR_BACKPRESSURE",
        "REMOTE_BACKPRESSURE",
    }
)
_CHECKPOINT_REFUSAL_CODES = frozenset(
    {
        "CHECKPOINT_SOURCE_CHANGED",
        "CHECKPOINT_CONFLICT",
        "CHECKPOINT_CORRUPT",
        "CHECKPOINT_CAPACITY",
    }
)


class ServiceExecutionProjectionRelayError(RuntimeError):
    """Finite diagnostics for invalid configuration or an invalid caller.

    Transient projection and monitor failures never raise this; they are
    counted in :class:`ExecutionProjectionPollResult`.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ExecutionProjectionCheckpoint:
    """The durable local position of one relay destination."""

    cursor: str = ""
    delivered: int = 0
    last_event_id: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionProjectionPollResult:
    """Bounded counts and the final diagnostic of one explicit poll.

    ``delivered`` counts sink acceptances; ``checkpointed`` counts durable
    cursor advances.  They can differ for one observation when the sink
    accepted it but the local checkpoint write failed.  ``dropped`` is a
    finite aggregate of observations/pages that were not forwarded.
    """

    delivered: int = 0
    checkpointed: int = 0
    pages: int = 0
    read_events: int = 0
    bytes_forwarded: int = 0
    gaps: int = 0
    last_gap_code: str | None = None
    refused: int = 0
    unavailable: int = 0
    rejected: int = 0
    errors: int = 0
    invalid: int = 0
    oversize: int = 0
    backpressure: int = 0
    backoff: bool = False
    backoff_seconds: float = 0.0
    last_error_code: str | None = None
    disabled: bool = False

    @property
    def total_delivered(self) -> int:
        """Return the number of accepted sink deliveries."""

        return self.delivered

    @property
    def dropped(self) -> int:
        """Return a finite count of observations/pages not forwarded."""

        return (
            self.refused
            + self.unavailable
            + self.rejected
            + self.errors
            + self.invalid
            + self.oversize
            + self.backpressure
        )


@dataclass
class _MutableCounters:
    delivered: int = 0
    checkpointed: int = 0
    pages: int = 0
    read_events: int = 0
    bytes_forwarded: int = 0
    gaps: int = 0
    last_gap_code: str | None = None
    refused: int = 0
    unavailable: int = 0
    rejected: int = 0
    errors: int = 0
    invalid: int = 0
    oversize: int = 0
    backpressure: int = 0
    backoff: bool = False
    backoff_seconds: float = 0.0
    last_error_code: str | None = None
    disabled: bool = False

    def snapshot(self) -> ExecutionProjectionPollResult:
        return ExecutionProjectionPollResult(
            delivered=self.delivered,
            checkpointed=self.checkpointed,
            pages=self.pages,
            read_events=self.read_events,
            bytes_forwarded=self.bytes_forwarded,
            gaps=self.gaps,
            last_gap_code=self.last_gap_code,
            refused=self.refused,
            unavailable=self.unavailable,
            rejected=self.rejected,
            errors=self.errors,
            invalid=self.invalid,
            oversize=self.oversize,
            backpressure=self.backpressure,
            backoff=self.backoff,
            backoff_seconds=self.backoff_seconds,
            last_error_code=self.last_error_code,
            disabled=self.disabled,
        )


@dataclass(frozen=True, slots=True)
class _CheckpointNamespace:
    namespace_id: str
    binding_json: str


@dataclass(frozen=True, slots=True)
class _Observation:
    """One validated, whitelisted observation ready for the sink."""

    schema: str
    event_id: str
    cursor: str
    occurred_at_ns: int
    subject_kind: str
    subject_ref: str
    event_type: str
    state: str
    reason_code: str | None
    detail: tuple[tuple[str, object], ...]

    def data(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "event_id": self.event_id,
            "cursor": self.cursor,
            "occurred_at_ns": self.occurred_at_ns,
            "subject_kind": self.subject_kind,
            "subject_ref": self.subject_ref,
            "event_type": self.event_type,
            "state": self.state,
            "reason_code": self.reason_code,
            "detail": dict(self.detail),
        }


class _InvalidSourceError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _SourceRefusedError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _SourceFailureError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _SinkUnavailableError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _CheckpointFailureError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ServiceExecutionProjectionRelay:
    """Forward one read-only Execution projection to a Monitor outbox sink.

    ``projection`` is borrowed and only ``read`` is called.  ``sink`` is a
    callable accepting a ``bt_api_monitor.durable.SequencedOutboxEvent``; it is
    called synchronously on the owner thread.  ``sink=None`` disables the relay
    entirely (no projection read, no checkpoint file).  Both objects remain
    owned by the caller and are never closed here.
    """

    def __init__(
        self,
        projection: object | None,
        sink: object | None = None,
        *,
        checkpoint_path: str | Path | None = None,
        outbox_scope: str | None = None,
        consumer_id: str | None = None,
        page_limit: int = _DEFAULT_PAGE_LIMIT,
        max_events_per_poll: int = _DEFAULT_EVENTS_PER_POLL,
        byte_budget: int = _DEFAULT_BYTE_BUDGET,
        backoff_seconds: float = 0.5,
        max_backoff_seconds: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        owner_thread_id = threading.get_ident()
        if clock is not None and not callable(clock):
            raise ServiceExecutionProjectionRelayError("INVALID_CLOCK")
        if type(page_limit) is not int or not 1 <= page_limit <= _MAX_PAGE_LIMIT:
            raise ServiceExecutionProjectionRelayError("INVALID_PAGE_LIMIT")
        if (
            type(max_events_per_poll) is not int
            or not 1 <= max_events_per_poll <= _MAX_EVENTS_PER_POLL
        ):
            raise ServiceExecutionProjectionRelayError("INVALID_POLL_LIMIT")
        if type(byte_budget) is not int or not 1 <= byte_budget <= _MAX_BYTE_BUDGET:
            raise ServiceExecutionProjectionRelayError("INVALID_BYTE_BUDGET")
        if type(max_backoff_seconds) not in (int, float) or not 0 <= max_backoff_seconds <= 300.0:
            raise ServiceExecutionProjectionRelayError("INVALID_BACKOFF")
        if (
            type(backoff_seconds) not in (int, float)
            or not 0 <= backoff_seconds <= max_backoff_seconds
        ):
            raise ServiceExecutionProjectionRelayError("INVALID_BACKOFF")
        if sink is not None and not callable(sink):
            raise ServiceExecutionProjectionRelayError("INVALID_SINK")

        self._owner_thread_id = owner_thread_id
        self._projection = projection
        self._sink = sink
        self._enabled = sink is not None
        self._clock = clock or time.monotonic
        self._page_limit = page_limit
        self._max_events_per_poll = max_events_per_poll
        self._byte_budget = byte_budget
        self._backoff_base = float(backoff_seconds)
        self._backoff_max = float(max_backoff_seconds)
        self._backoff_until = 0.0
        self._consecutive_failures = 0
        self._closed = False
        self._last_poll = ExecutionProjectionPollResult()
        self._durable_types: tuple[type, type] | None = None

        self._checkpoint_path: Path | None = None
        self._outbox_scope = ""
        self._consumer_id = ""
        self._namespace = _CheckpointNamespace("", "")
        self._source_binding: dict[str, str] = {}
        self._source_binding_json = ""

        if not self._enabled:
            return

        if not callable(getattr(projection, "read", None)):
            raise ServiceExecutionProjectionRelayError("INVALID_PROJECTION")
        if type(outbox_scope) is not str or not _valid_text(outbox_scope, _MAX_OUTBOX_SCOPE_BYTES):
            raise ServiceExecutionProjectionRelayError("INVALID_OUTBOX_SCOPE")
        if not isinstance(checkpoint_path, (str, Path)) or not str(checkpoint_path):
            raise ServiceExecutionProjectionRelayError("INVALID_CHECKPOINT_PATH")

        try:
            source_binding = _source_binding(projection)
        except _SourceRefusedError as error:
            raise ServiceExecutionProjectionRelayError(error.code) from None
        if consumer_id is None:
            sink_consumer = getattr(sink, "consumer_id", None)
            if type(sink_consumer) is str and _valid_text(sink_consumer, _MAX_CONSUMER_ID_BYTES):
                consumer_id = sink_consumer
            else:
                consumer_id = "sdk-execution-projection-relay-v1:" + _sha256(
                    _canonical_json({"outbox_scope": outbox_scope})
                )
        if type(consumer_id) is not str or not _valid_text(consumer_id, _MAX_CONSUMER_ID_BYTES):
            raise ServiceExecutionProjectionRelayError("INVALID_CONSUMER_ID")

        binding_json = _canonical_json(
            {
                "schema": _BINDING_SCHEMA,
                "consumer_id": consumer_id,
                "outbox_scope": outbox_scope,
                "source": source_binding,
            }
        )
        if len(binding_json.encode("utf-8")) > _MAX_BINDING_BYTES:
            raise ServiceExecutionProjectionRelayError("CHECKPOINT_NAMESPACE_TOO_LARGE")

        checkpoint = Path(checkpoint_path).expanduser().resolve(strict=False)
        _reject_same_database(projection, checkpoint)
        try:
            _initialize_or_validate_checkpoint(checkpoint)
        except _CheckpointFailureError as error:
            raise ServiceExecutionProjectionRelayError(error.code) from None

        self._checkpoint_path = checkpoint
        self._outbox_scope = outbox_scope
        self._consumer_id = consumer_id
        self._source_binding = source_binding
        self._source_binding_json = _canonical_json(source_binding)
        self._namespace = _CheckpointNamespace(
            _sha256(
                _canonical_json(
                    {
                        "schema": _BINDING_SCHEMA,
                        "consumer_id": consumer_id,
                        "outbox_scope": outbox_scope,
                    }
                )
            ),
            binding_json,
        )

    @property
    def consumer_id(self) -> str:
        """Return the stable destination identity of the checkpoint."""

        return self._consumer_id

    @property
    def outbox_scope(self) -> str:
        """Return the Monitor outbox scope this relay writes to."""

        return self._outbox_scope

    @property
    def enabled(self) -> bool:
        """Return whether a sink is configured."""

        return self._enabled

    @property
    def last_poll(self) -> ExecutionProjectionPollResult:
        """Return the latest completed or partially completed poll result."""

        return self._last_poll

    @property
    def backoff_active(self) -> bool:
        """Return whether the relay is inside a bounded failure backoff."""

        return self._enabled and self._clock() < self._backoff_until

    @property
    def source_binding(self) -> Mapping[str, str]:
        """Return the optional source fingerprint/epoch binding in use."""

        return dict(self._source_binding)

    def checkpoint(self) -> ExecutionProjectionCheckpoint:
        """Read the durable local checkpoint without modifying it."""

        self._assert_usable()
        if not self._enabled:
            return ExecutionProjectionCheckpoint()
        return self._read_checkpoint()

    def poll(self, limit: int | None = None) -> ExecutionProjectionPollResult:
        """Forward at most ``limit`` observations from one projection page at a time.

        The call performs no work unless the owner thread invokes it.  It never
        raises for projection, monitor, sink or checkpoint failures; those are
        counted and boundedly backed off.
        """

        self._assert_usable()
        budget = self._max_events_per_poll if limit is None else limit
        if type(budget) is not int or not 0 <= budget <= _MAX_EVENTS_PER_POLL:
            raise ServiceExecutionProjectionRelayError("INVALID_POLL_LIMIT")

        counters = _MutableCounters()
        if not self._enabled:
            counters.disabled = True
            self._last_poll = counters.snapshot()
            return self._last_poll

        remaining = self._backoff_until - self._clock()
        if remaining > 0:
            counters.backoff = True
            counters.backoff_seconds = remaining
            self._last_poll = counters.snapshot()
            return self._last_poll

        try:
            result = self._poll(budget, counters)
            if not counters.backoff:
                self._consecutive_failures = 0
                self._backoff_until = 0.0
        except _SourceRefusedError as error:
            counters.refused += 1
            counters.last_error_code = error.code
            self._enter_backoff(counters)
            result = counters.snapshot()
        except _InvalidSourceError as error:
            counters.invalid += 1
            counters.last_error_code = error.code
            self._enter_backoff(counters)
            result = counters.snapshot()
        except _SourceFailureError as error:
            counters.last_error_code = error.code
            if error.code in _SOURCE_UNAVAILABLE_CODES:
                counters.unavailable += 1
            else:
                counters.errors += 1
            self._enter_backoff(counters)
            result = counters.snapshot()
        except _CheckpointFailureError as error:
            counters.last_error_code = error.code
            if error.code in _CHECKPOINT_REFUSAL_CODES:
                counters.refused += 1
            else:
                counters.errors += 1
            self._enter_backoff(counters)
            result = counters.snapshot()
        except Exception:
            counters.errors += 1
            counters.last_error_code = "RELAY_INTERNAL_ERROR"
            self._enter_backoff(counters)
            result = counters.snapshot()
        self._last_poll = result
        return result

    def close(self) -> None:
        """Close this relay's logical lifetime without closing borrowed objects."""

        self._assert_owner_thread()
        self._closed = True

    def _poll(self, limit: int, counters: _MutableCounters) -> ExecutionProjectionPollResult:
        checkpoint = self._read_checkpoint()
        current_binding = _canonical_json(_source_binding(self._projection))
        if current_binding != self._source_binding_json:
            raise _SourceRefusedError("SOURCE_BINDING_CHANGED")

        cursor = checkpoint.cursor
        if cursor and not _valid_cursor_token(cursor):
            raise _CheckpointFailureError("CHECKPOINT_CORRUPT")

        delivered = checkpoint.delivered
        bytes_forwarded = 0
        while counters.delivered < limit:
            page_limit = min(self._page_limit, limit - counters.delivered)
            page = self._read_page(cursor, page_limit)
            counters.pages += 1
            events = _page_events(page, page_limit)
            truncated = _page_truncated(page)
            counters.read_events += len(events)

            gap_code = _page_gap_code(page)
            if gap_code is not None:
                counters.gaps += 1
                counters.last_gap_code = gap_code

            if not events:
                break

            stop = False
            for event in events:
                observation = _validate_event(event)
                size = _observation_bytes(observation)
                if size > _MAX_EVENT_BYTES:
                    counters.oversize += 1
                    counters.last_error_code = "SOURCE_EVENT_OVERSIZE"
                    stop = True
                    break
                if counters.delivered and bytes_forwarded + size > self._byte_budget:
                    stop = True
                    break

                sequence = delivered + 1
                try:
                    item = self._sequenced_event(observation, sequence)
                    accepted = self._sink(item)
                except _SinkUnavailableError as error:
                    counters.unavailable += 1
                    counters.last_error_code = error.code
                    stop = True
                    self._enter_backoff(counters)
                    break
                except Exception as error:
                    _count_sink_failure(counters, error)
                    stop = True
                    self._enter_backoff(counters)
                    break

                if accepted is False:
                    counters.rejected += 1
                    counters.last_error_code = "SINK_REJECTED"
                    stop = True
                    self._enter_backoff(counters)
                    break

                counters.delivered += 1
                bytes_forwarded += size
                counters.bytes_forwarded += size

                try:
                    outcome = _advance_checkpoint(
                        self._checkpoint_path,
                        self._namespace,
                        expected_cursor=cursor,
                        expected_delivered=delivered,
                        cursor=observation.cursor,
                        delivered=sequence,
                        last_event_id=observation.event_id,
                        updated_at_ns=time.time_ns(),
                    )
                except _CheckpointFailureError as error:
                    counters.errors += 1
                    counters.last_error_code = error.code
                    stop = True
                    self._enter_backoff(counters)
                    break
                except Exception:
                    counters.errors += 1
                    counters.last_error_code = "LOCAL_CHECKPOINT_FAILED"
                    stop = True
                    self._enter_backoff(counters)
                    break
                if outcome == "conflict":
                    counters.errors += 1
                    counters.last_error_code = "CHECKPOINT_CONFLICT"
                    stop = True
                    self._enter_backoff(counters)
                    break

                cursor = observation.cursor
                delivered = sequence
                counters.checkpointed += 1

            if stop:
                break
            if not truncated:
                break

        return counters.snapshot()

    def _read_page(self, after: str, limit: int) -> object:
        after_value = after or None
        try:
            page = self._projection.read(after=after_value, limit=limit)
        except Exception as error:
            code = _finite_code(error)
            if code in _CURSOR_REFUSAL_CODES:
                raise _SourceRefusedError(code) from None
            raise _SourceFailureError(code) from None
        return page

    def _read_checkpoint(self) -> ExecutionProjectionCheckpoint:
        if self._checkpoint_path is None:
            raise _CheckpointFailureError("CHECKPOINT_UNAVAILABLE")
        try:
            return _read_checkpoint(self._checkpoint_path, self._namespace)
        except _CheckpointFailureError:
            raise
        except Exception:
            raise _CheckpointFailureError("CHECKPOINT_UNAVAILABLE") from None

    def _sequenced_event(self, observation: _Observation, sequence: int) -> object:
        outbox_event_type, sequenced_type = self._monitor_types()
        event = outbox_event_type(
            event_id=_monitor_event_id(self._consumer_id, self._outbox_scope, observation.event_id),
            scope=self._outbox_scope,
            event_type=_MONITOR_EVENT_TYPE,
            data=observation.data(),
            occurred_at=observation.occurred_at_ns / 1_000_000_000,
        )
        return sequenced_type(sequence, event)

    def _monitor_types(self) -> tuple[type, type]:
        if self._durable_types is not None:
            return self._durable_types
        try:
            from bt_api_monitor.durable import OutboxEvent, SequencedOutboxEvent
        except ImportError:
            raise _SinkUnavailableError("MONITOR_OUTBOX_UNAVAILABLE") from None
        self._durable_types = (OutboxEvent, SequencedOutboxEvent)
        return self._durable_types

    def _enter_backoff(self, counters: _MutableCounters) -> None:
        self._consecutive_failures += 1
        exponent = min(self._consecutive_failures - 1, 16)
        delay = min(self._backoff_base * (2**exponent), self._backoff_max)
        self._backoff_until = self._clock() + delay
        counters.backoff = True
        counters.backoff_seconds = delay

    def _assert_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceExecutionProjectionRelayError("WRONG_OWNER_THREAD")

    def _assert_usable(self) -> None:
        self._assert_owner_thread()
        if self._closed:
            raise ServiceExecutionProjectionRelayError("RELAY_CLOSED")


def _count_sink_failure(counters: _MutableCounters, error: Exception) -> None:
    """Classify one sink exception without leaking its message."""

    if isinstance(error, (BlockingIOError, TimeoutError)):
        counters.backpressure += 1
        counters.last_error_code = "BACKPRESSURE"
        return
    code = _finite_code(error)
    if code in _SINK_BACKPRESSURE_CODES:
        counters.backpressure += 1
    elif code in _SINK_REJECTED_CODES:
        counters.rejected += 1
    elif code in _SINK_UNAVAILABLE_CODES:
        counters.unavailable += 1
    else:
        counters.errors += 1
    counters.last_error_code = code


def _source_binding(projection: object) -> dict[str, str]:
    binding: dict[str, str] = {}
    for name in _SOURCE_BINDING_ATTRS:
        value = getattr(projection, name, None)
        if value is None:
            continue
        if type(value) is not str or not _valid_text(value, _MAX_CONSUMER_ID_BYTES):
            raise _SourceRefusedError("SOURCE_BINDING_INVALID")
        binding[name] = value
    return binding


def _reject_same_database(projection: object, checkpoint_path: Path) -> None:
    """Refuse a checkpoint that resolves to an exposed source database."""

    for name in _SOURCE_PATH_ATTRS:
        value = getattr(projection, name, None)
        if not isinstance(value, (str, Path)) or not str(value):
            continue
        try:
            source = Path(value).expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if source == checkpoint_path:
            raise ServiceExecutionProjectionRelayError("CHECKPOINT_MUST_BE_SEPARATE")
        try:
            source_info = source.stat()
            checkpoint_info = checkpoint_path.lstat()
        except OSError:
            continue
        if (source_info.st_dev, source_info.st_ino) == (
            checkpoint_info.st_dev,
            checkpoint_info.st_ino,
        ):
            raise ServiceExecutionProjectionRelayError("CHECKPOINT_MUST_BE_SEPARATE")


def _page_events(page: object, limit: int) -> tuple[object, ...]:
    events = _field(page, "events")
    if type(events) not in (tuple, list):
        raise _SourceFailureError("SOURCE_PAGE_INVALID")
    if len(events) > limit:
        raise _SourceFailureError("SOURCE_PAGE_INVALID")
    return tuple(events)


def _page_truncated(page: object) -> bool:
    value = _field(page, "truncated")
    if type(value) is not bool:
        raise _SourceFailureError("SOURCE_PAGE_INVALID")
    return value


def _page_gap_code(page: object) -> str | None:
    gap = _field(page, "gap")
    if gap is None:
        return None
    code = gap.get("code") if isinstance(gap, Mapping) else getattr(gap, "code", None)
    if type(code) is not str or _UPPER_TOKEN_RE.fullmatch(code) is None:
        raise _SourceFailureError("SOURCE_PAGE_INVALID")
    return code


def _field(item: object, name: str) -> Any:
    if isinstance(item, Mapping):
        if name not in item:
            raise _SourceFailureError("SOURCE_PAGE_INVALID")
        return item[name]
    try:
        return getattr(item, name)
    except AttributeError:
        raise _SourceFailureError("SOURCE_PAGE_INVALID") from None


def _event_field(event: object, name: str) -> Any:
    if isinstance(event, Mapping):
        if name not in event:
            raise _InvalidSourceError("SOURCE_EVENT_INVALID")
        return event[name]
    try:
        return getattr(event, name)
    except AttributeError:
        raise _InvalidSourceError("SOURCE_EVENT_INVALID") from None


def _validate_event(event: object) -> _Observation:
    schema = _require_text(_event_field(event, "schema"), _MAX_SCHEMA_BYTES)
    event_id = _require_text(_event_field(event, "event_id"), _MAX_TEXT_BYTES)
    cursor = _cursor_token(_event_field(event, "cursor"))
    occurred_at_ns = _event_field(event, "occurred_at_ns")
    if type(occurred_at_ns) is not int or not 1 <= occurred_at_ns < _SEQUENCE_LIMIT:
        raise _InvalidSourceError("SOURCE_EVENT_INVALID")
    subject_kind = _event_field(event, "subject_kind")
    if type(subject_kind) is not str or subject_kind not in _SUBJECT_KINDS:
        raise _InvalidSourceError("SOURCE_EVENT_INVALID")
    subject_ref = _require_text(_event_field(event, "subject_ref"), _MAX_TEXT_BYTES)
    event_type = _require_token(_event_field(event, "event_type"))
    state = _require_token(_event_field(event, "state"))
    reason_raw = _event_field(event, "reason_code")
    reason_code = None if reason_raw is None else _require_text(reason_raw, _MAX_TEXT_BYTES)
    detail = _validate_detail(_event_field(event, "detail"))
    return _Observation(
        schema=schema,
        event_id=event_id,
        cursor=cursor,
        occurred_at_ns=occurred_at_ns,
        subject_kind=subject_kind,
        subject_ref=subject_ref,
        event_type=event_type,
        state=state,
        reason_code=reason_code,
        detail=detail,
    )


def _cursor_token(value: object) -> str:
    if isinstance(value, Mapping):
        raw = value.get("token")
    elif type(value) is str:
        raw = value
    else:
        raw = getattr(value, "token", None)
        if callable(raw):
            try:
                raw = raw()
            except Exception:
                raise _InvalidSourceError("SOURCE_EVENT_INVALID") from None
    if type(raw) is not str or not _valid_cursor_token(raw):
        raise _InvalidSourceError("SOURCE_EVENT_INVALID")
    return raw


def _validate_detail(value: object) -> tuple[tuple[str, object], ...]:
    if isinstance(value, Mapping):
        items = list(value.items())
    elif type(value) in (tuple, list):
        items = []
        for pair in value:
            if type(pair) not in (tuple, list) or len(pair) != 2:
                raise _InvalidSourceError("SOURCE_EVENT_INVALID")
            items.append((pair[0], pair[1]))
    else:
        raise _InvalidSourceError("SOURCE_EVENT_INVALID")
    if len(items) > _MAX_DETAIL_ENTRIES:
        raise _InvalidSourceError("SOURCE_EVENT_INVALID")
    normalized: list[tuple[str, object]] = []
    seen: set[str] = set()
    for key, nested in items:
        if type(key) is not str or key not in _DETAIL_KEYS or key in seen:
            raise _InvalidSourceError("SOURCE_EVENT_INVALID")
        seen.add(key)
        if type(nested) is int:
            if not 0 <= nested < _SEQUENCE_LIMIT:
                raise _InvalidSourceError("SOURCE_EVENT_INVALID")
            normalized.append((key, nested))
        elif type(nested) is str and _DECIMAL_RE.fullmatch(nested) is not None:
            normalized.append((key, nested))
        else:
            raise _InvalidSourceError("SOURCE_EVENT_INVALID")
    normalized.sort(key=lambda item: item[0])
    return tuple(normalized)


def _observation_bytes(observation: _Observation) -> int:
    return len(_canonical_json(observation.data()).encode("utf-8"))


def _monitor_event_id(consumer_id: str, outbox_scope: str, projection_event_id: str) -> str:
    digest = _sha256(
        _canonical_json(
            {
                "schema": _EVENT_ID_SCHEMA,
                "consumer_id": consumer_id,
                "outbox_scope": outbox_scope,
                "projection_event_id": projection_event_id,
            }
        )
    )
    return "service-event:" + digest


def _finite_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if type(code) is str and _UPPER_TOKEN_RE.fullmatch(code) is not None:
        return code
    return "SOURCE_READ_FAILED"


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
            application_id = connection.execute("PRAGMA application_id").fetchone()[0]
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if application_id == 0 and user_version == 0:
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if existed or tables:
                    raise _CheckpointFailureError("CHECKPOINT_CORRUPT")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("""
                    CREATE TABLE projection_relay_checkpoints (
                        namespace_id TEXT PRIMARY KEY,
                        binding_json TEXT NOT NULL,
                        cursor_token TEXT NOT NULL,
                        delivered INTEGER NOT NULL CHECK(delivered >= 0),
                        last_event_id TEXT NOT NULL,
                        updated_at_ns INTEGER NOT NULL
                    ) WITHOUT ROWID
                    """)
                connection.execute(f"PRAGMA application_id={_CHECKPOINT_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={_CHECKPOINT_USER_VERSION}")
                connection.commit()
            elif (
                application_id != _CHECKPOINT_APPLICATION_ID
                or user_version != _CHECKPOINT_USER_VERSION
            ):
                raise _CheckpointFailureError("CHECKPOINT_CORRUPT")
            _validate_checkpoint_schema(connection)
            connection.execute("PRAGMA synchronous=FULL")
            mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if type(mode) is not str or mode.casefold() != "delete":
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
    rows = connection.execute("PRAGMA table_info(projection_relay_checkpoints)").fetchall()
    columns = [(row[1], row[2].upper(), row[3], row[5]) for row in rows]
    expected = [
        ("namespace_id", "TEXT", 1, 1),
        ("binding_json", "TEXT", 1, 0),
        ("cursor_token", "TEXT", 1, 0),
        ("delivered", "INTEGER", 1, 0),
        ("last_event_id", "TEXT", 1, 0),
        ("updated_at_ns", "INTEGER", 1, 0),
    ]
    if columns != expected:
        raise _CheckpointFailureError("CHECKPOINT_CORRUPT")


def _read_checkpoint(path: Path, namespace: _CheckpointNamespace) -> ExecutionProjectionCheckpoint:
    try:
        connection = _connect(path)
        try:
            _validate_database_identity(connection)
            row = connection.execute(
                """
                SELECT binding_json, cursor_token, delivered, last_event_id, updated_at_ns
                FROM projection_relay_checkpoints WHERE namespace_id = ?
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
        return ExecutionProjectionCheckpoint()
    binding_json, cursor_token, delivered, last_event_id, updated_at_ns = row
    if binding_json != namespace.binding_json:
        raise _CheckpointFailureError("CHECKPOINT_SOURCE_CHANGED")
    if (
        type(cursor_token) is not str
        or (cursor_token != "" and not _valid_cursor_token(cursor_token))
        or type(delivered) is not int
        or not 0 <= delivered < _SEQUENCE_LIMIT
        or type(last_event_id) is not str
        or (last_event_id != "" and not _valid_text(last_event_id, _MAX_TEXT_BYTES))
        or type(updated_at_ns) is not int
        or not 0 < updated_at_ns < _SEQUENCE_LIMIT
    ):
        raise _CheckpointFailureError("CHECKPOINT_CORRUPT")
    return ExecutionProjectionCheckpoint(cursor_token, delivered, last_event_id)


def _advance_checkpoint(
    path: Path,
    namespace: _CheckpointNamespace,
    *,
    expected_cursor: str,
    expected_delivered: int,
    cursor: str,
    delivered: int,
    last_event_id: str,
    updated_at_ns: int,
) -> str:
    """Atomically advance one cursor or acknowledge an exact duplicate.

    This module-level boundary is intentionally narrow so tests can inject a
    one-shot local persistence failure after a successful sink receipt.
    """

    if (
        type(expected_cursor) is not str
        or (expected_cursor != "" and not _valid_cursor_token(expected_cursor))
        or type(expected_delivered) is not int
        or not 0 <= expected_delivered < _SEQUENCE_LIMIT
        or not _valid_cursor_token(cursor)
        or type(delivered) is not int
        or not expected_delivered < delivered < _SEQUENCE_LIMIT
        or type(last_event_id) is not str
        or not _valid_text(last_event_id, _MAX_TEXT_BYTES)
        or type(updated_at_ns) is not int
        or not 0 < updated_at_ns < _SEQUENCE_LIMIT
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
                SELECT binding_json, cursor_token, delivered, last_event_id
                FROM projection_relay_checkpoints WHERE namespace_id = ?
                """,
                (namespace.namespace_id,),
            ).fetchone()
            if row is None:
                if expected_cursor != "" or expected_delivered != 0:
                    raise _CheckpointFailureError("CHECKPOINT_CONFLICT")
                count = connection.execute(
                    "SELECT count(*) FROM projection_relay_checkpoints"
                ).fetchone()[0]
                if type(count) is not int or count >= _MAX_CHECKPOINT_RECORDS:
                    raise _CheckpointFailureError("CHECKPOINT_CAPACITY")
                connection.execute(
                    """
                    INSERT INTO projection_relay_checkpoints
                        (namespace_id, binding_json, cursor_token, delivered,
                         last_event_id, updated_at_ns)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        namespace.namespace_id,
                        namespace.binding_json,
                        cursor,
                        delivered,
                        last_event_id,
                        updated_at_ns,
                    ),
                )
                connection.commit()
                return "advanced"

            binding_json, current_cursor, current_delivered, current_event_id = row
            if binding_json != namespace.binding_json:
                raise _CheckpointFailureError("CHECKPOINT_SOURCE_CHANGED")
            if (
                type(current_cursor) is not str
                or (current_cursor != "" and not _valid_cursor_token(current_cursor))
                or type(current_delivered) is not int
                or not 0 <= current_delivered < _SEQUENCE_LIMIT
                or type(current_event_id) is not str
                or (current_event_id != "" and not _valid_text(current_event_id, _MAX_TEXT_BYTES))
            ):
                raise _CheckpointFailureError("CHECKPOINT_CORRUPT")

            if (
                current_cursor == cursor
                and current_delivered == delivered
                and current_event_id == last_event_id
            ):
                connection.commit()
                return "duplicate"
            if current_cursor != expected_cursor or current_delivered != expected_delivered:
                # SELECT occurs after BEGIN IMMEDIATE: this is the fresh cursor
                # read required after a competing relay won the prior CAS.
                raise _CheckpointFailureError("CHECKPOINT_CONFLICT")
            connection.execute(
                """
                UPDATE projection_relay_checkpoints
                SET cursor_token = ?, delivered = ?, last_event_id = ?, updated_at_ns = ?
                WHERE namespace_id = ? AND cursor_token = ? AND delivered = ?
                """,
                (
                    cursor,
                    delivered,
                    last_event_id,
                    updated_at_ns,
                    namespace.namespace_id,
                    expected_cursor,
                    expected_delivered,
                ),
            )
            if connection.total_changes != 1:
                raise _CheckpointFailureError("CHECKPOINT_CONFLICT")
            connection.commit()
            return "advanced"
        except _CheckpointFailureError:
            connection.rollback()
            raise
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
        connection = sqlite3.connect(uri, uri=True, timeout=0.5, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=500")
        return connection
    except Exception:
        if connection is not None:
            connection.close()
        raise


def _validate_database_identity(connection: sqlite3.Connection) -> None:
    application_id = connection.execute("PRAGMA application_id").fetchone()[0]
    user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if application_id != _CHECKPOINT_APPLICATION_ID or user_version != _CHECKPOINT_USER_VERSION:
        raise _CheckpointFailureError("CHECKPOINT_CORRUPT")
    _validate_checkpoint_schema(connection)


def _require_text(value: object, max_bytes: int) -> str:
    if not _valid_text(value, max_bytes):
        raise _InvalidSourceError("SOURCE_EVENT_INVALID")
    return value


def _require_token(value: object) -> str:
    if type(value) is not str or _UPPER_TOKEN_RE.fullmatch(value) is None:
        raise _InvalidSourceError("SOURCE_EVENT_INVALID")
    return value


def _valid_cursor_token(value: object) -> bool:
    if type(value) is not str or not value:
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return len(encoded) <= _MAX_CURSOR_BYTES and all(
        33 <= ord(character) <= 126 for character in value
    )


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


__all__ = (
    "ExecutionProjectionCheckpoint",
    "ExecutionProjectionPollResult",
    "ServiceExecutionProjectionRelay",
    "ServiceExecutionProjectionRelayError",
)
