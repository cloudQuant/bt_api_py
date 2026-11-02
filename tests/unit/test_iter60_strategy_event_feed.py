"""Iteration 60, D60-16: strategy event delivery across crash windows.

Crash points injected around one event:
  1. before the handler ran            -> redelivered after restart;
  2. after the handler, before ACK     -> redelivered with the same event_id
                                          (at least once, never lost);
  3. after the cursor was saved         -> not redelivered.
A side effect guarded by DeliveredEventFilter runs exactly once in all cases.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytest.importorskip("bt_api_execution")

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    CancelObservation,
    ExecutionScope,
    ExecutionState,
    ProviderObservation,
    SqliteExecutionStore,
    StrategyCloseCapacityGate,
)
from bt_api_execution.service_order_entry import (
    SUBMIT_OPERATION,
    ExecutionOrderEntryService,
)

from bt_api_py.strategy_event_feed import (
    DeliveredEventFilter,
    SqliteEventCursorStore,
    StrategyEventFeed,
    StrategyEventFeedError,
)

_ACCOUNT = "account-events"


class _Provider:
    def submit(self, intent):
        return ProviderObservation.accepted(intent.intent_id, f"provider-{intent.intent_id}")

    def cancel(self, intent):
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


class _CrashError(Exception):
    pass


@pytest.fixture
def ledger(tmp_path):
    store = SqliteExecutionStore(tmp_path / "ledger.sqlite3")
    scope = ExecutionScope("PAPER", "simulation", _ACCOUNT, "strategy-a", "20261102")
    other = ExecutionScope("PAPER", "simulation", _ACCOUNT, "strategy-b", "20261102")
    wires = {
        scope: WireScope("paper:events", account_ref=_ACCOUNT, strategy_ref="strategy-a"),
        other: WireScope("paper:events", account_ref=_ACCOUNT, strategy_ref="strategy-b"),
    }
    service = ExecutionOrderEntryService(
        store,
        {wire: execution for execution, wire in wires.items()},
        _Provider(),
        admission_gate=StrategyCloseCapacityGate(store),
    )

    def trade(execution, intent_id):
        reply = service.handle(
            SUBMIT_OPERATION,
            principal_ref="client",
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, "client", execution.strategy_id),
            scope=wires[execution],
            arguments={
                "intent_id": intent_id,
                "signal_id": f"s-{intent_id}",
                "instrument": "PAPER:SIM:SA2701",
                "side": "BUY",
                "position_effect": "OPEN",
                "order_type": "LIMIT",
                "quantity": "1",
                "price": "1500",
                "time_in_force": "GTC",
                "reduce_only": False,
                "metadata_version": "m",
                "deadline_ns": None,
                "tags": {},
            },
        )
        assert reply["ok"] is True
        service._facade_pair(execution)[0].reconcile(
            ProviderObservation(
                intent_id,
                ExecutionState.FILLED,
                provider_order_id=f"provider-{intent_id}",
                filled_quantity=Decimal("1"),
                average_price=Decimal("1500"),
            )
        )

    trade(scope, "a-1")
    trade(other, "b-1")
    yield store, scope, tmp_path, trade
    for execution in wires:
        service._facade_pair(execution)[0].close()
    store.close()


def _event_ids(store, scope, cursor_path, consumer="bt-strategy-a"):
    cursor = SqliteEventCursorStore(cursor_path, consumer)
    seen = []
    StrategyEventFeed(store, (scope,), cursor).poll(lambda event: seen.append(event.event_id))
    cursor.close()
    return seen


def test_feed_delivers_only_the_strategys_own_events(ledger):
    store, scope, tmp_path, _trade = ledger
    cursor = SqliteEventCursorStore(tmp_path / "c.sqlite3", "bt-strategy-a")
    events = []
    StrategyEventFeed(store, (scope,), cursor).poll(events.append)
    assert events and {event.scope_key for event in events} == {scope.key}
    assert [e.sequence for e in events] == sorted(e.sequence for e in events)


def test_crash_before_handler_redelivers(ledger):
    store, scope, tmp_path, _trade = ledger
    path = tmp_path / "c.sqlite3"
    cursor = SqliteEventCursorStore(path, "bt-strategy-a")

    def crash(_event):
        raise _CrashError

    with pytest.raises(_CrashError):
        StrategyEventFeed(store, (scope,), cursor).poll(crash)
    cursor.close()
    assert _event_ids(store, scope, path)  # nothing was lost


def test_crash_after_handler_before_ack_redelivers_same_event(ledger):
    store, scope, tmp_path, _trade = ledger
    path = tmp_path / "c.sqlite3"
    handled = []

    class _CrashingCursor(SqliteEventCursorStore):
        def save(self, journal_generation, sequence):
            raise _CrashError  # the process dies before persisting the ACK

    cursor = _CrashingCursor(path, "bt-strategy-a")
    with pytest.raises(_CrashError):
        StrategyEventFeed(store, (scope,), cursor).poll(lambda e: handled.append(e.event_id))
    cursor.close()
    redelivered = _event_ids(store, scope, path)
    assert handled and redelivered[0] == handled[0]  # same identity, delivered again


def test_after_ack_nothing_is_redelivered_and_new_events_follow(ledger):
    store, scope, tmp_path, trade = ledger
    path = tmp_path / "c.sqlite3"
    first = _event_ids(store, scope, path)
    assert first
    assert _event_ids(store, scope, path) == []
    trade(scope, "a-2")
    later = _event_ids(store, scope, path)
    assert later and not set(later) & set(first)


def test_side_effect_runs_exactly_once_across_redelivery(ledger):
    store, scope, tmp_path, _trade = ledger
    path = tmp_path / "c.sqlite3"

    def notify(cursor):
        guard = DeliveredEventFilter(cursor, "bt-strategy-a")

        def effect(connection):
            connection.execute("CREATE TABLE IF NOT EXISTS notified (event_id TEXT)")

        def handler(event):
            guard.run_once(
                event.event_id,
                lambda c: (effect(c), c.execute("INSERT INTO notified VALUES (?)", (event.event_id,))),
            )

        return handler

    class _CrashingCursor(SqliteEventCursorStore):
        def save(self, journal_generation, sequence):
            raise _CrashError

    crashing = _CrashingCursor(path, "bt-strategy-a")
    with pytest.raises(_CrashError):
        StrategyEventFeed(store, (scope,), crashing).poll(notify(crashing))
    crashing.close()
    cursor = SqliteEventCursorStore(path, "bt-strategy-a")
    StrategyEventFeed(store, (scope,), cursor).poll(notify(cursor))
    rows = cursor.connection.execute("SELECT event_id FROM notified").fetchall()
    assert rows and len(rows) == len({row[0] for row in rows})  # no duplicate side effects
    cursor.close()


def test_changed_journal_generation_requires_resync(ledger):
    store, scope, tmp_path, _trade = ledger
    path = tmp_path / "c.sqlite3"
    cursor = SqliteEventCursorStore(path, "bt-strategy-a")
    cursor.save("some-other-journal", 1)
    with pytest.raises(StrategyEventFeedError) as info:
        StrategyEventFeed(store, (scope,), cursor).poll(lambda e: None)
    assert info.value.code == "RESYNC_REQUIRED"
    cursor.close()
