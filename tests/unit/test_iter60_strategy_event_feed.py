"""Iteration 60, D60-16 / acceptance report P1-4: direct-mode event delivery.

The single source is the SDK strategy ledger (no bt_api_execution). Crash
points around one event:
  1. before the handler ran            -> redelivered after restart;
  2. after the handler, before ACK     -> redelivered with the same event_id;
  3. after the cursor was saved         -> not redelivered.
A side effect guarded by DeliveredEventFilter runs exactly once in all cases,
also when the consuming process is killed between commit and ACK.
"""

from __future__ import annotations

import multiprocessing
import os
from decimal import Decimal

import pytest

from bt_api_py.strategy_event_feed import (
    DeliveredEventFilter,
    SqliteEventCursorStore,
    StrategyEventFeed,
    StrategyEventFeedError,
)
from bt_api_py.strategy_ledger import LedgerIntent, SqliteStrategyLedger

_ACCOUNT = "account-events"
_X = "PAPER:SIM:SA2701"


class _CrashError(Exception):
    pass


def _trade(ledger, strategy_ref, intent_id):
    ledger.reserve(LedgerIntent(intent_id, strategy_ref, _X, "BUY", Decimal("1"), Decimal("1500")))
    ledger.mark_submitted(intent_id)
    ledger.apply_execution(intent_id, cumulative_quantity=1, average_price=1500)


@pytest.fixture
def ledger(tmp_path):
    item = SqliteStrategyLedger(tmp_path / "ledger.sqlite3", account_ref=_ACCOUNT, require_budget=False)
    _trade(item, "strategy-a", "a-1")
    _trade(item, "strategy-b", "b-1")
    try:
        yield item, tmp_path
    finally:
        item.close()


def _event_ids(ledger, cursor_path, consumer="bt-strategy-a"):
    cursor = SqliteEventCursorStore(cursor_path, consumer)
    seen = []
    StrategyEventFeed(ledger, "strategy-a", cursor).poll(lambda event: seen.append(event.event_id))
    cursor.close()
    return seen


def test_feed_delivers_only_the_strategys_own_events_in_order(ledger):
    store, tmp_path = ledger
    cursor = SqliteEventCursorStore(tmp_path / "c.sqlite3", "bt-strategy-a")
    events = []
    StrategyEventFeed(store, "strategy-a", cursor).poll(events.append)
    assert [e.action for e in events] == ["RESERVE", "SUBMITTED", "EXECUTION"]
    assert {e.strategy_ref for e in events} == {"strategy-a"}
    assert {e.intent_id for e in events} == {"a-1"}
    assert [e.cursor for e in events] == sorted(e.cursor for e in events)


def test_crash_before_handler_redelivers(ledger):
    store, tmp_path = ledger
    path = tmp_path / "c.sqlite3"
    cursor = SqliteEventCursorStore(path, "bt-strategy-a")

    def crash(_event):
        raise _CrashError

    with pytest.raises(_CrashError):
        StrategyEventFeed(store, "strategy-a", cursor).poll(crash)
    cursor.close()
    assert _event_ids(store, path)  # nothing was lost


def test_crash_after_handler_before_ack_redelivers_same_event(ledger):
    store, tmp_path = ledger
    path = tmp_path / "c.sqlite3"
    handled = []

    class _CrashingCursor(SqliteEventCursorStore):
        def save(self, journal_generation, sequence):
            raise _CrashError  # the process dies before persisting the ACK

    cursor = _CrashingCursor(path, "bt-strategy-a")
    with pytest.raises(_CrashError):
        StrategyEventFeed(store, "strategy-a", cursor).poll(lambda e: handled.append(e.event_id))
    cursor.close()
    redelivered = _event_ids(store, path)
    assert handled and redelivered[0] == handled[0]  # same identity, delivered again


def test_after_ack_nothing_is_redelivered_and_new_events_follow(ledger):
    store, tmp_path = ledger
    path = tmp_path / "c.sqlite3"
    first = _event_ids(store, path)
    assert first
    assert _event_ids(store, path) == []
    _trade(store, "strategy-b", "b-2")  # other strategies' events do not appear
    assert _event_ids(store, path) == []
    store.reserve(LedgerIntent("a-2", "strategy-a", _X, "SELL", Decimal("1"), Decimal("1500")))
    later = _event_ids(store, path)
    assert later and not set(later) & set(first)


def test_side_effect_runs_exactly_once_across_redelivery(ledger):
    store, tmp_path = ledger
    path = tmp_path / "c.sqlite3"

    def notify(cursor):
        guard = DeliveredEventFilter(cursor, "bt-strategy-a")

        def handler(event):
            guard.run_once(
                event.event_id,
                lambda c: (
                    c.execute("CREATE TABLE IF NOT EXISTS notified (event_id TEXT)"),
                    c.execute("INSERT INTO notified VALUES (?)", (event.event_id,)),
                ),
            )

        return handler

    class _CrashingCursor(SqliteEventCursorStore):
        def save(self, journal_generation, sequence):
            raise _CrashError

    crashing = _CrashingCursor(path, "bt-strategy-a")
    with pytest.raises(_CrashError):
        StrategyEventFeed(store, "strategy-a", crashing).poll(notify(crashing))
    crashing.close()
    cursor = SqliteEventCursorStore(path, "bt-strategy-a")
    StrategyEventFeed(store, "strategy-a", cursor).poll(notify(cursor))
    rows = cursor.connection.execute("SELECT event_id FROM notified").fetchall()
    assert rows and len(rows) == len({row[0] for row in rows})  # no duplicate side effects
    cursor.close()


def test_a_rebuilt_ledger_requires_resync(ledger, tmp_path):
    store, base = ledger
    path = base / "c.sqlite3"
    assert _event_ids(store, path)
    rebuilt = SqliteStrategyLedger(
        tmp_path / "rebuilt.sqlite3", account_ref=_ACCOUNT, require_budget=False
    )
    try:
        _trade(rebuilt, "strategy-a", "a-new")
        cursor = SqliteEventCursorStore(path, "bt-strategy-a")
        with pytest.raises(StrategyEventFeedError) as info:
            StrategyEventFeed(rebuilt, "strategy-a", cursor).poll(lambda e: None)
        assert info.value.code == "RESYNC_REQUIRED"
        cursor.close()
    finally:
        rebuilt.close()


def _consume_then_die(ledger_path, cursor_path, out_path):
    """Child process: commit the side effect, then die before saving the ACK."""
    ledger = SqliteStrategyLedger(
        ledger_path, account_ref=_ACCOUNT, exclusive_owner=False, require_budget=False
    )
    cursor = SqliteEventCursorStore(cursor_path, "bt-strategy-a")
    guard = DeliveredEventFilter(cursor, "bt-strategy-a")

    def handler(event):
        guard.run_once(
            event.event_id,
            lambda c: (
                c.execute("CREATE TABLE IF NOT EXISTS notified (event_id TEXT)"),
                c.execute("INSERT INTO notified VALUES (?)", (event.event_id,)),
            ),
        )
        os._exit(73)  # killed after the committed side effect, before the ACK

    StrategyEventFeed(ledger, "strategy-a", cursor).poll(handler)


def test_process_killed_between_commit_and_ack_resumes_without_duplicates(ledger):
    store, tmp_path = ledger
    cursor_path = tmp_path / "c.sqlite3"
    context = multiprocessing.get_context("spawn")
    child = context.Process(
        target=_consume_then_die,
        args=(str(tmp_path / "ledger.sqlite3"), str(cursor_path), str(tmp_path / "out")),
    )
    child.start()
    child.join(timeout=60)
    assert child.exitcode == 73
    cursor = SqliteEventCursorStore(cursor_path, "bt-strategy-a")
    guard = DeliveredEventFilter(cursor, "bt-strategy-a")
    delivered = []

    def handler(event):
        delivered.append(event.event_id)
        guard.run_once(
            event.event_id,
            lambda c: c.execute("INSERT INTO notified VALUES (?)", (event.event_id,)),
        )

    StrategyEventFeed(store, "strategy-a", cursor).poll(handler)
    rows = [row[0] for row in cursor.connection.execute("SELECT event_id FROM notified")]
    cursor.close()
    assert delivered  # the first event was redelivered after the crash ...
    assert len(rows) == len(set(rows)) == len(delivered)  # ... but applied once
