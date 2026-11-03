"""At-least-once strategy event delivery with a durable cursor (iteration 60, D60-16).

Direct mode (acceptance report P1-4): the single source of a strategy's
order/fill events is the SDK strategy ledger's event table
(``SqliteStrategyLedger.read_events``); no bt_api_execution is involved. In
Gateway mode the single source is the Gateway event journal, consumed with a
durable checkpoint and network ACK by ``bt_api_py.service_account_consumer``.

Delivery semantics of this feed:

* **At least once, never lost.** The cursor advances only after the handler
  returned for an event. A crash before or during the handler, or after it but
  before the cursor was saved, redelivers the same event after restart.
* **Stable identity.** Every event carries ``event_id`` (``generation:cursor``)
  and ``cursor``; a consumer that must not repeat a side effect keeps the
  delivered ids in the same durable transaction as that side effect (see
  ``DeliveredEventFilter``) or makes the side effect idempotent. Side effects
  outside that transaction get at-least-once, not exactly-once.
* **One ledger generation.** The saved cursor is bound to the ledger
  generation it was read from. A replaced or rebuilt ledger refuses to
  continue (``RESYNC_REQUIRED``) instead of mapping old cursors onto new events.
* **Own scope only.** A feed reads one strategy's events; other strategies'
  events on the same account are never delivered.

Strategy-level Backtrader state (indicators, user variables) is not recovered
by this feed; only the event stream is.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = [
    "DeliveredEventFilter",
    "SqliteEventCursorStore",
    "StrategyEventFeed",
    "StrategyEventFeedError",
]


class StrategyEventFeedError(RuntimeError):
    """Finite, value-free feed failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SqliteEventCursorStore:
    """Durable ``(journal_generation, sequence)`` cursor for one consumer."""

    def __init__(self, path: str | Path, consumer_id: str) -> None:
        if type(consumer_id) is not str or not consumer_id:
            raise StrategyEventFeedError("CONSUMER_ID_INVALID")
        self._connection = sqlite3.connect(str(path), isolation_level=None)
        self._consumer_id = consumer_id
        self._lock = threading.Lock()
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS event_cursor ("
            " consumer_id TEXT PRIMARY KEY, journal_generation TEXT NOT NULL,"
            " sequence INTEGER NOT NULL CHECK (sequence >= 0))"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS delivered_event ("
            " consumer_id TEXT NOT NULL, event_id TEXT NOT NULL,"
            " PRIMARY KEY (consumer_id, event_id))"
        )

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def load(self) -> tuple[str, int] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT journal_generation, sequence FROM event_cursor WHERE consumer_id = ?",
                (self._consumer_id,),
            ).fetchone()
        return None if row is None else (str(row[0]), int(row[1]))

    def save(self, journal_generation: str, sequence: int) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO event_cursor(consumer_id, journal_generation, sequence)"
                " VALUES (?, ?, ?) ON CONFLICT(consumer_id) DO UPDATE SET"
                " journal_generation = excluded.journal_generation,"
                " sequence = excluded.sequence"
                " WHERE event_cursor.sequence <= excluded.sequence"
                " OR event_cursor.journal_generation != excluded.journal_generation",
                (self._consumer_id, journal_generation, sequence),
            )

    def close(self) -> None:
        self._connection.close()


class DeliveredEventFilter:
    """Exactly-once side effects on top of at-least-once delivery.

    ``run_once(event_id, effect)`` records ``event_id`` and runs ``effect``
    inside one SQLite transaction on the cursor store's connection: either
    both are committed or neither. A redelivered event whose id is recorded is
    skipped. ``effect`` must write its durable state through the same
    connection (or be idempotent itself).
    """

    def __init__(self, cursor_store: SqliteEventCursorStore, consumer_id: str) -> None:
        self._connection = cursor_store.connection
        self._consumer_id = consumer_id
        self._lock = threading.Lock()

    def run_once(self, event_id: str, effect: Callable[[sqlite3.Connection], Any]) -> bool:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                seen = self._connection.execute(
                    "SELECT 1 FROM delivered_event WHERE consumer_id = ? AND event_id = ?",
                    (self._consumer_id, event_id),
                ).fetchone()
                if seen is not None:
                    self._connection.execute("ROLLBACK")
                    return False
                effect(self._connection)
                self._connection.execute(
                    "INSERT INTO delivered_event(consumer_id, event_id) VALUES (?, ?)",
                    (self._consumer_id, event_id),
                )
                self._connection.execute("COMMIT")
                return True
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise


class StrategyEventFeed:
    """Read one strategy's durable ledger events after a saved cursor."""

    def __init__(self, ledger: Any, strategy_ref: str, cursor_store: Any) -> None:
        if not callable(getattr(ledger, "read_events", None)):
            raise StrategyEventFeedError("EVENT_STORE_INVALID")
        if type(strategy_ref) is not str or not strategy_ref:
            raise StrategyEventFeedError("EVENT_SCOPES_REQUIRED")
        self._ledger = ledger
        self._strategy_ref = strategy_ref
        self._cursor_store = cursor_store

    def poll(self, handler: Callable[[Any], None], *, limit: int = 100) -> int:
        """Deliver every pending event in order; return how many were delivered."""

        delivered = 0
        while True:
            saved = self._cursor_store.load()
            after = 0 if saved is None else saved[1]
            page = self._ledger.read_events(self._strategy_ref, after=after, limit=limit)
            generation = page.generation
            if saved is not None and saved[0] != generation:
                raise StrategyEventFeedError("RESYNC_REQUIRED")
            for event in page.events:
                handler(event)
                self._cursor_store.save(generation, event.cursor)
                delivered += 1
            if not page.has_more:
                if page.scanned_through > after and (
                    not page.events or page.scanned_through > page.events[-1].cursor
                ):
                    self._cursor_store.save(generation, page.scanned_through)
                return delivered
