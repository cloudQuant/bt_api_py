"""Iteration 73.09: bounded idle reconcile fast path in CtpGatewayReturnMaintenance."""

from __future__ import annotations

import os
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from bt_api_base.direct_write_protocol import (
    DirectExecutionEventKind,
    DirectOrderStatus,
    NativeOrderIdentity,
)
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.ctp_gateway_return_maintenance import CtpGatewayReturnMaintenance

INSTRUMENT = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "hc2701", "hc2701")


def _make_unmatched_order(ref: str = "src-1", key: str = "session-unmatched") -> GatewayReturnFact:
    return GatewayReturnFact(
        "acct",
        "CTP",
        INSTRUMENT,
        "20300101",
        DirectExecutionEventKind.ORDER,
        ref,
        identity=NativeOrderIdentity("CTP", "ref-a", {"trading_day": "20300101"}, 1),
        identity_keys=(key,),
        binding_keys=(key,),
        venue_key=None,
        trade_key=None,
        status=DirectOrderStatus.ACCEPTED,
        side=None,
        offset=None,
        trade_quantity=None,
        trade_price=None,
    )


@pytest.fixture
def fastpath_env(tmp_path):
    guards = tmp_path / "guards"
    guards.mkdir()
    guard = LocalAccountSessionGuard(guards, "acct", "owner")
    generation = guard.acquire()
    db_path = tmp_path / "orders.sqlite"
    journal = GatewayDirectCommandJournal.create(
        db_path, "acct", generation=generation, max_records=32, execution_max_facts=32
    )
    journal.enable_controls(guard)
    journal.enable_strategy_ledger(guard)
    journal.enable_account_extensions(guard)
    ledger = GatewayDirectExecutionLedger(journal, guard)

    clock = [1000.0]
    batch_box = [SimpleNamespace(records=())]
    ingested_batches = []
    m = CtpGatewayReturnMaintenance.__new__(CtpGatewayReturnMaintenance)
    m._lock = threading.Lock()
    m._closing = threading.Event()
    m._open, m._status = True, "HEALTHY"
    m._fact_health = {
        "status": "UNAVAILABLE",
        "unattributed": None,
        "conflict": None,
        "verified_external": None,
    }
    m._ledger = ledger
    m._generation = 1
    m._provider = SimpleNamespace(
        session_generation=lambda: 1,
        clear_instrument_status_return_recovery=lambda: None,
    )
    m._ingestor = SimpleNamespace(
        assert_session_current=lambda: None,
        ingest_callbacks=lambda batch, assert_current: ingested_batches.append(batch),
    )
    buf_status = SimpleNamespace(gap=False, record_count=0)
    m._session = SimpleNamespace(
        return_buffer=SimpleNamespace(
            status=lambda: buf_status,
            offer=lambda **_: batch_box[0],
            ack=lambda _: None,
        ),
        assert_return_record_current=lambda _record: None,
    )
    m._settings = SimpleNamespace(callback_batch_limit=64)
    m._observed_order_source = None
    m._source_start_failed = False
    m._done = {"orders", "trades"}
    m._pair_seen = True
    m._stale_seen = False
    m._last_poll_ok = False
    m._recovery_fence_generation = None
    m._last_query_failure = None
    m._pid = os.getpid()
    m._owner_thread = threading.current_thread()
    m._request = None
    m._next_kind = "orders"
    m._next_query = 0.0
    m._reconcile_cache = None
    m._reconcile_clock = lambda: clock[0]

    yield m, journal, ledger, db_path, clock, batch_box, ingested_batches
    guard.release()


def test_idle_poll_reuses_fixed_point_reconcile_and_invalidates_on_write_time_and_foreign_change(
    fastpath_env, monkeypatch
):
    m, journal, ledger, db_path, clock, batch_box, ingested_batches = fastpath_env
    ledger.ingest(_make_unmatched_order())

    connect_count = [0]
    orig_connect = sqlite3.connect

    def _counting_connect(*args, **kwargs):
        connect_count[0] += 1
        return orig_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _counting_connect)

    # First poll runs full reconcile_pending + account_fact_health and caches the fixed point.
    m.poll()
    assert m.status() == {"status": "HEALTHY", "reservation_open": True}
    assert m.fact_status() == {
        "status": "INCOMPLETE",
        "unattributed": 1,
        "conflict": 0,
        "verified_external": 0,
    }
    first_connects = connect_count[0]
    assert first_connects >= 2

    # Next 10 idle ticks within the 5s window open zero SQLite connections.
    for _ in range(10):
        clock[0] += 0.1
        m.poll()
    assert connect_count[0] == first_connects
    assert m.fact_status()["status"] == "INCOMPLETE"
    assert m.fact_status()["unattributed"] == 1

    # Advancing past the 5.0s safety window forces a fresh full reconcile + fact_health check.
    clock[0] += 5.1
    m.poll()
    after_timer = connect_count[0]
    assert after_timer > first_connects
    assert m.fact_status()["unattributed"] == 1

    # A foreign SQLite write (bypassing journal.mutation_counter) invalidates via fingerprint.
    monkeypatch.setattr(sqlite3, "connect", orig_connect)
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "UPDATE direct_journal_meta SET revision=revision+1 WHERE account=?", ("acct",)
        )
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr(sqlite3, "connect", _counting_connect)
    clock[0] += 0.1
    m.poll()
    after_foreign = connect_count[0]
    assert after_foreign > after_timer

    # An in-process write (matching command reserved & dispatched) invalidates immediately
    # and reconciles the UNATTRIBUTED fact to ATTRIBUTED on the very next poll.
    monkeypatch.setattr(sqlite3, "connect", orig_connect)
    row = journal.reserve(
        "s1",
        "req-1",
        "principal",
        {"symbol": "hc2701", "side": "buy", "offset": "open", "quantity": "1"},
    )
    native = {
        "exchange_name": "CTP",
        "client_order_id": "req-1",
        "session_generation": 1,
        "fields_json": "{}",
    }
    journal.begin_dispatch(
        "s1",
        "req-1",
        row.reservation_id,
        row.digest,
        "principal",
        1,
        native,
        ("session-unmatched",),
    )
    monkeypatch.setattr(sqlite3, "connect", _counting_connect)
    connect_before_match = connect_count[0]
    clock[0] += 0.1
    m.poll()
    assert connect_count[0] > connect_before_match
    assert m.fact_status() == {
        "status": "NO_UNRESOLVED_OBSERVED",
        "unattributed": 0,
        "conflict": 0,
        "verified_external": 0,
    }
    # Because a fact transitioned out of UNATTRIBUTED on that poll, cache stayed empty so
    # the next poll confirms a true fixed point before re-arming the fast path.
    assert m._reconcile_cache is None
    m.poll()
    assert m._reconcile_cache is not None

    # A non-empty callback batch always invalidates and runs full reconcile.
    connect_before_cb = connect_count[0]
    batch_box[0] = SimpleNamespace(records=("synthetic-record",))
    clock[0] += 0.1
    m.poll()
    assert len(ingested_batches) == 1
    assert connect_count[0] > connect_before_cb


def test_fastpath_disabled_on_reconnect_error_or_continuity_witness(fastpath_env, monkeypatch):
    m, journal, ledger, _db_path, clock, _batch_box, _ingested = fastpath_env
    m.poll()
    assert m._reconcile_cache is not None

    # pause_for_reconnect clears cache and fails closed to UNAVAILABLE.
    m.pause_for_reconnect()
    assert m._reconcile_cache is None
    assert m.fact_status()["status"] == "UNAVAILABLE"

    # Re-warm then trigger a blocked observed_order_source: must fail closed and drop cache.
    m._done = {"orders", "trades"}
    m.poll()
    assert m._reconcile_cache is not None
    m._observed_order_source = SimpleNamespace(blocked=True, invalidate=lambda: None)
    m.poll()
    assert m._reconcile_cache is None
    assert m.status()["status"] == "OBSERVED_SOURCE_GAP"
    assert m.fact_status()["status"] == "UNAVAILABLE"

    # When a continuity witness is installed on the journal, fast path is never used.
    m._observed_order_source = None
    m.poll()
    assert m._reconcile_cache is not None
    monkeypatch.setattr(journal, "_continuity_witness", object())
    assert m._reconcile_fastpath_journal() is None
    assert m._try_reuse_idle_reconcile(SimpleNamespace(records=())) is False
