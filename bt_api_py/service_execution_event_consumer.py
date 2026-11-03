"""Exactly-once-effect consumer of a Gateway execution event stream (iteration 60, R2-C).

The Gateway account owner publishes typed terminal-order and per-fill
observations into one event stream per strategy (``stream="execution"`` on
``account.read_after``/``account.ack``/``account.progress``). This consumer
reads that stream with tracked delivery and hands each page to a caller
*sink* that applies it durably (Backtrader: the Broker order/fill projection
and its notification journal). The order of commits is:

1. ``read()``: confirm any durable pending ACK, then read the page after the
   local durable cursor; every event is validated (account/strategy scope,
   contiguous sequence, no snapshot gap, Base fact id and digest re-derived,
   expected account key) before the sink sees it;
2. the sink applies the page and returns only after its effects are durable;
3. ``commit(page)``: persist the page's cursor locally as *pending ACK*;
4. ``acknowledge()``: ACK that cursor to the Gateway, then mark it ACKed.

A crash before (3) re-reads the page after a restart (the sink must dedupe by
the stable ``event_id``); a crash between (3) and (4) repeats only the ACK.
So the remote ACK never runs ahead of the sink's durable effects. The cursor
store is a dedicated SQLite file bound to the stream identity; a Gateway
stream epoch change is refused with ``STREAM_EPOCH_MISMATCH`` (resync).

The consumer neither starts nor closes the borrowed runtime. Observations are
Gateway declarations, not provider proof or intent bindings.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bt_api_base.account_protocol import AccountKey
from bt_api_base.execution_row_observation_protocol import (
    EXECUTION_EVENT_KINDS,
    ExecutionObservation,
    execution_observation_from_event,
)

from bt_api_py.service_account_consumer import (
    _MAX_BATCH_SIZE,
    _MAX_SQLITE_CURSOR,
    _read_progress,
    _rpc_binding_signature,
    _stable_binding,
    _valid_text,
)
from bt_api_py.service_deployment import MOUNT_PORTS
from bt_api_py.service_runtime import ServiceClientRuntime, ServiceRuntimeError

_ACCOUNT_READ_PORT = "service.account_read"
_REQUIRED_OPERATIONS = frozenset({"account.read_after", "account.ack", "account.progress"})
_STREAM = "execution"
_BINDING_SCHEMA = "sdk.service_execution_event_consumer.binding.v1"
_EVENT_FIELDS = frozenset(
    {"account_scope", "strategy_scope", "sequence", "event_id", "kind", "payload", "observed_at"}
)


class ServiceExecutionEventConsumerError(RuntimeError):
    """Finite consumer diagnostics that never include remote values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ExecutionStreamEvent:
    """One validated execution event: ``cursor`` is the stream sequence."""

    stream_epoch: str
    cursor: int
    event_id: str
    kind: str
    observation: ExecutionObservation


@dataclass(frozen=True)
class ExecutionStreamPage:
    """One read page; ``commit`` it after the sink made its effects durable."""

    stream_epoch: str
    requested_cursor: int
    next_cursor: int
    events: tuple[ExecutionStreamEvent, ...]


@dataclass(frozen=True)
class ExecutionStreamCheckpoint:
    """The durable local cursor (``pending_ack``: committed, ACK not confirmed)."""

    stream_epoch: str | None
    cursor: int
    pending_ack: bool


class _CursorStore:
    """One dedicated SQLite row: binding, epoch, cursor, pending ACK."""

    def __init__(self, path: str | Path, binding: Mapping[str, Any]) -> None:
        self._binding = json.dumps(binding, sort_keys=True, separators=(",", ":"))
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS execution_cursor (id INTEGER PRIMARY KEY CHECK (id = 1), "
            "binding TEXT NOT NULL, stream_epoch TEXT, cursor INTEGER NOT NULL, "
            "pending_ack INTEGER NOT NULL CHECK (pending_ack IN (0, 1)))"
        )
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute("SELECT binding FROM execution_cursor WHERE id=1").fetchone()
            if row is None:
                self._db.execute(
                    "INSERT INTO execution_cursor VALUES (1, ?, NULL, 0, 0)", (self._binding,)
                )
            elif row[0] != self._binding:
                raise ServiceExecutionEventConsumerError("CURSOR_STORE_BINDING_MISMATCH")
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            self._db.close()
            raise

    def checkpoint(self) -> ExecutionStreamCheckpoint:
        epoch, cursor, pending = self._db.execute(
            "SELECT stream_epoch, cursor, pending_ack FROM execution_cursor WHERE id=1"
        ).fetchone()
        if type(cursor) is not int or cursor < 0 or (epoch is None and (cursor or pending)):
            raise ServiceExecutionEventConsumerError("CURSOR_STORE_CORRUPT")
        return ExecutionStreamCheckpoint(epoch, cursor, bool(pending))

    def write(self, epoch: str, cursor: int, pending: bool) -> ExecutionStreamCheckpoint:
        self._db.execute(
            "UPDATE execution_cursor SET stream_epoch=?, cursor=?, pending_ack=? WHERE id=1",
            (epoch, cursor, int(pending)),
        )
        return self.checkpoint()

    def close(self) -> None:
        self._db.close()


class ServiceExecutionEventConsumer:
    """Read one strategy's Gateway execution stream; ACK only committed cursors.

    Construct with a registered ``ServiceClientRuntime`` (the binding needs the
    tracked read, ACK and progress grants), or with ``over_call`` for a trusted
    co-located caller of the same Gateway read service. Owner-thread only.
    """

    def __init__(
        self,
        runtime: ServiceClientRuntime,
        mount: str,
        *,
        account_ref: str,
        path: str | Path,
        account_key: AccountKey,
        expected_account_scope: str,
        expected_strategy_scope: str,
        limit: int = 100,
    ) -> None:
        if not isinstance(runtime, ServiceClientRuntime):
            raise ServiceExecutionEventConsumerError("INVALID_RUNTIME")
        if (
            type(mount) is not str
            or mount not in MOUNT_PORTS
            or _ACCOUNT_READ_PORT not in MOUNT_PORTS[mount]
        ):
            raise ServiceExecutionEventConsumerError("INVALID_ACCOUNT_MOUNT")
        if not _valid_text(account_ref):
            raise ServiceExecutionEventConsumerError("INVALID_ACCOUNT_REF")
        try:
            binding = runtime.account_binding_view(
                mount, account_ref, required_operations=_REQUIRED_OPERATIONS
            )
            stable = _stable_binding(
                binding,
                runtime.identity,
                expected_account_scope=expected_account_scope,
                expected_strategy_scope=expected_strategy_scope,
                projection_version=_BINDING_SCHEMA,
                initial_state_sha256="0" * 64,
            )
            signature = _rpc_binding_signature(binding, runtime.identity)
        except Exception:
            raise ServiceExecutionEventConsumerError("ACCOUNT_BINDING_UNAVAILABLE") from None

        def call(operation: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
            current = runtime.account_binding_view(
                mount, account_ref, required_operations=_REQUIRED_OPERATIONS
            )
            if _rpc_binding_signature(current, runtime.identity) != signature:
                raise ServiceExecutionEventConsumerError("ACCOUNT_RPC_BINDING_CHANGED")
            try:
                return runtime.call(
                    mount,
                    operation,
                    arguments,
                    account_ref=account_ref,
                    scope_ref=binding.scope_ref,
                    max_reply_message_bytes=signature[-1],
                )
            except ServiceRuntimeError as error:
                if error.code == "REPLY_TOO_LARGE":
                    raise ServiceExecutionEventConsumerError("REPLY_TOO_LARGE") from None
                raise ServiceExecutionEventConsumerError("ACCOUNT_SERVICE_CALL_FAILED") from None

        self._setup(
            call,
            binding=stable,
            path=path,
            account_key=account_key,
            expected_account_scope=expected_account_scope,
            expected_strategy_scope=expected_strategy_scope,
            limit=limit,
        )

    @classmethod
    def over_call(
        cls,
        call: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
        *,
        binding_ref: str,
        path: str | Path,
        account_key: AccountKey,
        expected_account_scope: str,
        expected_strategy_scope: str,
        limit: int = 100,
    ) -> ServiceExecutionEventConsumer:
        """Use a trusted in-process ``call(operation, arguments)`` (co-located service)."""

        if not callable(call) or not _valid_text(binding_ref):
            raise ServiceExecutionEventConsumerError("INVALID_CALL_PORT")
        consumer = cls.__new__(cls)
        consumer._setup(
            call,
            binding={"schema": _BINDING_SCHEMA, "binding_ref": binding_ref},
            path=path,
            account_key=account_key,
            expected_account_scope=expected_account_scope,
            expected_strategy_scope=expected_strategy_scope,
            limit=limit,
        )
        return consumer

    def _setup(
        self,
        call,
        *,
        binding,
        path,
        account_key,
        expected_account_scope,
        expected_strategy_scope,
        limit,
    ) -> None:
        if type(account_key) is not AccountKey:
            raise ServiceExecutionEventConsumerError("INVALID_ACCOUNT_KEY")
        if not _valid_text(expected_account_scope) or not _valid_text(expected_strategy_scope):
            raise ServiceExecutionEventConsumerError("INVALID_EXPECTED_SCOPE")
        if type(limit) is not int or not 1 <= limit <= _MAX_BATCH_SIZE:
            raise ServiceExecutionEventConsumerError("INVALID_BATCH_LIMIT")
        self._call_port = call
        self._account_key = account_key
        self._account_scope = expected_account_scope
        self._strategy_scope = expected_strategy_scope
        self._limit = limit
        self._owner = threading.get_ident()
        self._closed = False
        bound = dict(binding)
        bound.update(
            stream=_STREAM,
            account_key=account_key.to_dict(),
            expected_account_scope=expected_account_scope,
            expected_strategy_scope=expected_strategy_scope,
        )
        try:
            self._store = _CursorStore(path, bound)
        except ServiceExecutionEventConsumerError:
            raise
        except Exception:
            raise ServiceExecutionEventConsumerError("CURSOR_STORE_OPEN_FAILED") from None
        self.binding_digest = hashlib.sha256(self._store._binding.encode()).hexdigest()

    # -- public ------------------------------------------------------------------

    def checkpoint(self) -> ExecutionStreamCheckpoint:
        self._assert_owner()
        return self._store.checkpoint()

    def read(self) -> ExecutionStreamPage:
        """Confirm a pending ACK, then read and validate the page after the cursor."""

        self._assert_owner()
        checkpoint = self._store.checkpoint()
        if checkpoint.pending_ack:
            checkpoint = self.acknowledge()
        requested = checkpoint.cursor if checkpoint.stream_epoch is not None else None
        arguments: dict[str, Any] = {
            "stream": _STREAM,
            "cursor": requested,
            "limit": self._limit,
            "track_delivery": True,
        }
        if requested is not None:
            arguments["stream_epoch"] = checkpoint.stream_epoch
        reply = self._call("account.read_after", arguments)
        if reply.get("requested_cursor", _MISSING) != requested:
            raise ServiceExecutionEventConsumerError("REQUEST_CURSOR_MISMATCH")
        progress = _read_progress(reply)
        epoch = progress["stream_epoch"]
        if not _valid_text(epoch):
            raise ServiceExecutionEventConsumerError("INVALID_ACCOUNT_PROGRESS")
        if checkpoint.stream_epoch is not None and epoch != checkpoint.stream_epoch:
            raise ServiceExecutionEventConsumerError("STREAM_EPOCH_MISMATCH")
        if reply.get("snapshot") is not None or reply.get("recovered_from_snapshot") is True:
            # Execution events cannot be rebuilt from a snapshot: a gap.
            raise ServiceExecutionEventConsumerError("EXECUTION_STREAM_GAP")
        start = checkpoint.cursor if requested is not None else 0
        raw_events = reply.get("events")
        if not isinstance(raw_events, (list, tuple)) or len(raw_events) > self._limit:
            raise ServiceExecutionEventConsumerError("INVALID_ACCOUNT_REPLY")
        events = []
        expected = start
        for raw in raw_events:
            expected += 1
            events.append(self._event(raw, epoch, expected))
        next_cursor = reply.get("next_cursor")
        latest = reply.get("latest_cursor")
        offered = progress["offered_cursor"]
        if (
            type(next_cursor) is not int
            or next_cursor != expected
            or type(latest) is not int
            or not next_cursor <= latest <= _MAX_SQLITE_CURSOR
            or type(offered) is not int
            or offered < next_cursor
        ):
            raise ServiceExecutionEventConsumerError("INVALID_ACCOUNT_PROGRESS")
        return ExecutionStreamPage(epoch, start, next_cursor, tuple(events))

    def commit(self, page: ExecutionStreamPage) -> ExecutionStreamCheckpoint:
        """Persist ``page``'s cursor as pending ACK (after the sink's effects are durable)."""

        self._assert_owner()
        if type(page) is not ExecutionStreamPage:
            raise ServiceExecutionEventConsumerError("INVALID_PAGE")
        checkpoint = self._store.checkpoint()
        if checkpoint.stream_epoch not in (None, page.stream_epoch) or (
            checkpoint.cursor != page.requested_cursor
        ):
            raise ServiceExecutionEventConsumerError("PAGE_NOT_CURRENT")
        if page.next_cursor == page.requested_cursor:
            if checkpoint.stream_epoch is not None:
                return checkpoint
            try:  # first, empty page: remember the stream epoch, nothing to ACK
                return self._store.write(page.stream_epoch, page.next_cursor, False)
            except sqlite3.Error:
                raise ServiceExecutionEventConsumerError("CURSOR_STORE_FAILED") from None
        try:
            return self._store.write(page.stream_epoch, page.next_cursor, True)
        except sqlite3.Error:
            raise ServiceExecutionEventConsumerError("CURSOR_STORE_FAILED") from None

    def acknowledge(self) -> ExecutionStreamCheckpoint:
        """ACK the committed cursor to the Gateway, then mark it ACKed locally."""

        self._assert_owner()
        checkpoint = self._store.checkpoint()
        if not checkpoint.pending_ack:
            return checkpoint
        reply = self._call(
            "account.ack",
            {
                "stream": _STREAM,
                "stream_epoch": checkpoint.stream_epoch,
                "cursor": checkpoint.cursor,
            },
        )
        progress = _read_progress(reply, wrapped_only=True)
        if (
            progress["stream_epoch"] != checkpoint.stream_epoch
            or type(progress["acknowledged_cursor"]) is not int
            or progress["acknowledged_cursor"] < checkpoint.cursor
        ):
            raise ServiceExecutionEventConsumerError("ACK_NOT_CONFIRMED")
        try:
            return self._store.write(checkpoint.stream_epoch, checkpoint.cursor, False)
        except sqlite3.Error:
            raise ServiceExecutionEventConsumerError("CURSOR_STORE_FAILED") from None

    def poll(self, sink: Callable[[tuple[ExecutionStreamEvent, ...]], object]) -> int:
        """``read`` -> ``sink(events)`` -> ``commit`` -> ``acknowledge``; return the count."""

        page = self.read()
        if page.events:
            sink(page.events)
        self.commit(page)
        self.acknowledge()
        return len(page.events)

    def close(self) -> None:
        self._assert_owner()
        if not self._closed:
            self._store.close()
            self._closed = True

    # -- internals ---------------------------------------------------------------

    def _call(self, operation: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            reply = self._call_port(operation, arguments)
        except ServiceExecutionEventConsumerError:
            raise
        except Exception:
            raise ServiceExecutionEventConsumerError("ACCOUNT_SERVICE_CALL_FAILED") from None
        if not isinstance(reply, Mapping):
            raise ServiceExecutionEventConsumerError("INVALID_ACCOUNT_REPLY")
        return reply

    def _event(self, raw: object, epoch: str, sequence: int) -> ExecutionStreamEvent:
        if not isinstance(raw, Mapping) or set(raw) != _EVENT_FIELDS:
            raise ServiceExecutionEventConsumerError("INVALID_EXECUTION_EVENT")
        if raw["account_scope"] != self._account_scope or (
            raw["strategy_scope"] != self._strategy_scope
        ):
            raise ServiceExecutionEventConsumerError("EXECUTION_EVENT_SCOPE_MISMATCH")
        if raw["sequence"] != sequence or type(raw["sequence"]) is not int:
            raise ServiceExecutionEventConsumerError("EXECUTION_STREAM_GAP")
        kind = raw["kind"]
        if kind not in EXECUTION_EVENT_KINDS:
            raise ServiceExecutionEventConsumerError("UNSUPPORTED_EXECUTION_EVENT")
        try:
            observation = execution_observation_from_event(kind, raw["event_id"], raw["payload"])
        except (TypeError, ValueError, KeyError):
            raise ServiceExecutionEventConsumerError("INVALID_EXECUTION_EVENT") from None
        if observation.account_key != self._account_key:
            raise ServiceExecutionEventConsumerError("EXECUTION_EVENT_ACCOUNT_MISMATCH")
        return ExecutionStreamEvent(epoch, sequence, raw["event_id"], kind, observation)

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner:
            raise ServiceExecutionEventConsumerError("WRONG_OWNER_THREAD")
        if self._closed:
            raise ServiceExecutionEventConsumerError("CONSUMER_CLOSED")


_MISSING = object()


__all__ = (
    "ExecutionStreamCheckpoint",
    "ExecutionStreamEvent",
    "ExecutionStreamPage",
    "ServiceExecutionEventConsumer",
    "ServiceExecutionEventConsumerError",
)
