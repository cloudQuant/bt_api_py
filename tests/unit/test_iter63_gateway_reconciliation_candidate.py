"""Synthetic source only; these checks do not establish CTP comparability."""
from contextlib import contextmanager
from dataclasses import replace
import shutil
import sqlite3
import threading
import os

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_gateway.strategy_position_ledger import StrategyInstrumentBinding
from bt_api_py.gateway_reconciliation_candidate import (
    ActivityObservation,
    GatewayReconciliationCandidate,
    PositionObservation,
    ReconciliationScope,
    TradeObservation,
    TradeObservationBatch,
)


@pytest.fixture
def harness(tmp_path):
    guard_dir = tmp_path / "guards"
    guard_dir.mkdir()
    guard = LocalAccountSessionGuard(guard_dir, "account", "owner")
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal.create(tmp_path / "journal", "account", generation=generation)
    key = MarketInstrumentKey("ctp", "CFFEX", "simnow", "future", "IF2612", "IF2612")
    binding = StrategyInstrumentBinding(key, "route", "lots", False)
    scope = ReconciliationScope("account", generation, "session-1", "20300101", binding)

    class Source:
        def __init__(self):
            self.calls = []
            self.valid = True
            self.applied = {}
            self.position = PositionObservation(scope, "position", "positions-covered", "5", True)
            self.trade_a = TradeObservation(scope, "a", "fact-a", "buy", "2", "IN_POSITION")
            self.trade_b = TradeObservation(scope, "b", "fact-b", "sell", "1", "AFTER_POSITION")
            self.batch = TradeObservationBatch(scope, "trades", "trades-covered", True,
                                               (self.trade_a, self.trade_b))
            self.activity = ActivityObservation(scope, "activity", "activity-covered", True, 0, 0, 0)

        @contextmanager
        def hold(self, expected):
            assert expected == scope
            yield lambda: self.valid

        def read_position(self, expected):
            self.calls.append("position")
            return self.position

        def read_trades(self, expected):
            self.calls.append("trades")
            return self.batch

        def read_activity(self, expected):
            self.calls.append("activity")
            return self.activity

        def verify(self, position, batch, activity):
            return (self.valid and position is self.position and batch is self.batch
                    and activity is self.activity)

    source = Source()
    def is_applied(conn, trade):
        value = source.applied.get(trade.trade_key)
        if value is None:
            return False
        return True if value == trade.fact_ref else None
    candidate = GatewayReconciliationCandidate(scope, source, journal=journal, guard=guard, is_applied=is_applied)
    try:
        with journal._transaction() as db:
            yield scope, source, candidate, db
    finally:
        guard.release()


def test_verified_inclusion_dedup_and_no_comparison_before_ingestion(harness):
    scope, source, candidate, db = harness
    source.batch = replace(source.batch, trades=(source.trade_a, source.trade_b, source.trade_b))
    assert candidate.refresh() == "CANDIDATE"
    assert source.calls == ["position", "trades", "activity"]
    # Diagnostic access also requires the actual source fence.
    assert candidate.pending_trades(db, scope.account, scope.binding.instrument) is None
    assert candidate.preview(db, scope.account, scope.binding.instrument) is None
    assert candidate.read_account_cut(db, scope.account, scope.binding.instrument) is None
    with candidate.source_fence():
        assert candidate.pending_trades(db, scope.account, scope.binding.instrument) == (source.trade_a, source.trade_b)
        assert candidate.read_account_cut(db, scope.account, scope.binding.instrument) is None
        # Simulate facts already verified and ingested by the separate owner.
        source.applied.update({"a": "fact-a", "b": "fact-b"})
        preview = candidate.preview(db, scope.account, scope.binding.instrument)
        assert preview.status == "NOT_COMPARABLE"
        assert preview.reason == "OWNER_TRANSACTION_UNVERIFIED"
        assert preview.adjusted_net == "4"  # Included buy is not counted twice; later sell is counted once.
        assert candidate.read_account_cut(db, scope.account, scope.binding.instrument) is None
        assert not candidate.verify_account_cut(db, scope.account, scope.binding,
                                                scope.trading_day, preview.cut_ref, "4")


def test_conflicting_trade_identity_is_not_comparable(harness):
    _scope, source, candidate, _db = harness
    source.batch = replace(source.batch, trades=(source.trade_a, replace(source.trade_a, quantity="3")))
    assert candidate.refresh() == "NOT_COMPARABLE"


@pytest.mark.parametrize("field", ["active_orders", "pending_dispatches", "pending_cancels"])
def test_active_or_inflight_activity_blocks_comparison(harness, field):
    _scope, source, candidate, _db = harness
    source.activity = replace(source.activity, **{field: 1})
    assert candidate.refresh() == "NOT_COMPARABLE"


def test_unknown_or_missing_evidence_is_not_converted_to_zero(harness):
    scope, source, candidate, db = harness
    source.valid = False
    assert candidate.refresh() == "NOT_COMPARABLE"
    source.valid = True
    with pytest.raises(ValueError):
        replace(source.trade_a, position_relation="UNKNOWN")
    with pytest.raises(ValueError):
        replace(source.position, net=None)
    with pytest.raises(ValueError):
        replace(source.batch, complete=False)
    source.position = replace(source.position, scope=replace(scope, session_generation="old"))
    assert candidate.refresh() == "NOT_COMPARABLE"
    with candidate.source_fence():
        assert candidate.pending_trades(db, scope.account, scope.binding.instrument) is None


def test_late_fact_or_source_replacement_invalidates_existing_cut(harness):
    scope, source, candidate, db = harness
    assert candidate.refresh() == "CANDIDATE"
    source.applied.update({"a": "fact-a", "b": "fact-b"})
    with candidate.source_fence():
        assert candidate.preview(db, scope.account, scope.binding.instrument) is not None
        # Real source must invalidate the verified batch when a late event appears.
        source.batch = replace(source.batch, snapshot_ref="later-batch")
        assert candidate.preview(db, scope.account, scope.binding.instrument) is None


def test_changed_source_at_fence_exit_clears_candidate(harness):
    scope, source, candidate, db = harness
    assert candidate.refresh() == "CANDIDATE"
    source.applied.update({"a": "fact-a", "b": "fact-b"})
    with candidate.source_fence():
        assert candidate.preview(db, scope.account, scope.binding.instrument) is not None
    original = source.verify
    checks = []
    def verify_then_invalidate(*args):
        result = original(*args)
        checks.append(result)
        # Both verify calls succeed; invalidate only after the last one, so
        # refresh has assigned the candidate when the fence exit rechecks.
        if len(checks) == 2 and result is True:
            source.valid = False
        return result
    source.verify = verify_then_invalidate
    assert candidate.refresh() == "NOT_COMPARABLE"
    assert checks == [True, True]
    source.valid = True
    source.verify = original
    with candidate.source_fence():
        # The source is healthy again; the earlier failed refresh must have
        # actually discarded the candidate rather than merely hiding it.
        assert candidate.pending_trades(db, scope.account, scope.binding.instrument) is None
        assert candidate.preview(db, scope.account, scope.binding.instrument) is None


def test_conflicting_local_trade_fact_is_unavailable_not_missing(harness):
    scope, source, candidate, db = harness
    assert candidate.refresh() == "CANDIDATE"
    source.applied["a"] = "different-fact"
    with candidate.source_fence():
        assert candidate.pending_trades(db, scope.account, scope.binding.instrument) is None
        assert candidate.preview(db, scope.account, scope.binding.instrument) is None
        assert candidate.read_account_cut(db, scope.account, scope.binding.instrument) is None


def test_slow_collection_cannot_extend_candidate_lifetime(harness, monkeypatch):
    import bt_api_py.gateway_reconciliation_candidate as module
    _scope, source, _candidate, _db = harness
    clock = [1_000_000_000]
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: clock[0])
    candidate = GatewayReconciliationCandidate(source.position.scope, source,
                                                journal=_candidate._journal, guard=_candidate._guard,
                                                is_applied=lambda *_: True,
                                                max_age_seconds=1)
    read_position = source.read_position
    def slow_position(scope):
        result = read_position(scope)
        clock[0] += 1_000_000_000
        return result
    source.read_position = slow_position
    assert candidate.refresh() == "NOT_COMPARABLE"


def test_read_fails_for_wrong_account_closed_transaction_or_other_thread(harness):
    scope, source, candidate, db = harness
    assert candidate.refresh() == "CANDIDATE"
    source.applied.update({"a": "fact-a", "b": "fact-b"})
    other_instrument = replace(scope.binding.instrument, instrument_id="OTHER", native_symbol="OTHER")
    with candidate.source_fence():
        assert candidate.pending_trades(db, scope.account, scope.binding.instrument) == ()
        assert candidate.preview(db, scope.account, scope.binding.instrument) is not None
        assert candidate.pending_trades(db, "other", scope.binding.instrument) is None
        assert candidate.preview(db, "other", scope.binding.instrument) is None
        assert candidate.pending_trades(db, scope.account, other_instrument) is None
        assert candidate.preview(db, scope.account, other_instrument) is None
        with candidate._journal._connection() as no_transaction:
            assert candidate.pending_trades(no_transaction, scope.account, scope.binding.instrument) is None
            assert candidate.preview(no_transaction, scope.account, scope.binding.instrument) is None
    outcomes = []
    def other_thread():
        try:
            candidate.refresh()
        except ValueError as exc:
            outcomes.append(str(exc))
    thread = threading.Thread(target=other_thread)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert outcomes == ["RECONCILIATION_OWNER_CHANGED"]


def test_wrong_owner_generation_is_rejected_before_collecting(harness):
    scope, source, candidate, _db = harness
    wrong = replace(scope, owner_generation="unrelated-owner")
    with pytest.raises(ValueError, match="INVALID_RECONCILIATION_SOURCE"):
        GatewayReconciliationCandidate(wrong, source, journal=candidate._journal,
                                       guard=candidate._guard, is_applied=lambda *_: True)
    assert source.calls == []


def test_copied_journal_anchor_does_not_authorize_another_database(harness, tmp_path):
    scope, source, candidate, _db = harness
    assert candidate.refresh() == "CANDIDATE"
    source.applied.update({"a": "fact-a", "b": "fact-b"})
    copied_path = tmp_path / "copied-journal"
    shutil.copyfile(candidate._journal_path, copied_path)
    other = sqlite3.connect(copied_path, isolation_level=None)
    try:
        other.execute("BEGIN IMMEDIATE")
        # The copied database has an identical persisted anchor; path/identity
        # binding is what rejects the other transaction.
        assert candidate._journal._assert_anchor(other) >= 0
        with candidate.source_fence():
            assert candidate.read_account_cut(other, scope.account, scope.binding.instrument) is None
            assert candidate.pending_trades(other, scope.account, scope.binding.instrument) is None
    finally:
        if other.in_transaction:
            other.rollback()
        other.close()


def test_new_guard_and_source_cannot_replace_the_bound_fence(harness, tmp_path):
    scope, source, candidate, db = harness
    assert candidate.refresh() == "CANDIDATE"
    source.applied.update({"a": "fact-a", "b": "fact-b"})
    new_dir = tmp_path / "another-guard"
    new_dir.mkdir()
    new_guard = LocalAccountSessionGuard(new_dir, scope.account, "replacement")
    new_guard.acquire()
    try:
        old_guard = candidate._guard
        candidate._guard = new_guard
        with pytest.raises(ValueError, match="RECONCILIATION_OWNER_CHANGED"):
            with candidate.source_fence():
                pass
        candidate._guard = old_guard
        old_source = candidate._source
        candidate._source = object()
        with pytest.raises(ValueError, match="RECONCILIATION_OWNER_CHANGED"):
            with candidate.source_fence():
                pass
        candidate._source = old_source
        with candidate.source_fence():
            assert candidate.preview(db, scope.account, scope.binding.instrument) is not None
    finally:
        new_guard.release()


def test_stale_guard_and_new_generation_invalidate_old_candidate(harness):
    _scope, _source, candidate, _db = harness
    assert candidate.refresh() == "CANDIDATE"
    old_generation = candidate._guard.acquire()
    candidate._guard.release()
    with pytest.raises(ValueError, match="RECONCILIATION_OWNER_CHANGED"):
        candidate.refresh()
    assert candidate._guard.acquire() != old_generation
    with pytest.raises(ValueError, match="RECONCILIATION_OWNER_CHANGED"):
        candidate.refresh()


def test_guard_loss_during_source_fence_rejects_candidate(harness):
    _scope, _source, candidate, _db = harness
    assert candidate.refresh() == "CANDIDATE"
    with pytest.raises(ValueError, match="RECONCILIATION_OWNER_CHANGED"):
        with candidate.source_fence():
            candidate._guard.release()


def test_same_path_second_transaction_never_becomes_comparable(harness):
    scope, source, candidate, _db = harness
    assert candidate.refresh() == "CANDIDATE"
    source.applied.update({"a": "fact-a", "b": "fact-b"})
    other = sqlite3.connect(candidate._journal_path, isolation_level=None)
    try:
        other.execute("BEGIN")
        with candidate.source_fence():
            # A different connection on the same file passes path checks, but
            # is not an owner-issued transaction capability.
            preview = candidate.preview(other, scope.account, scope.binding.instrument)
            assert preview.status == "NOT_COMPARABLE"
            assert preview.reason == "OWNER_TRANSACTION_UNVERIFIED"
            assert candidate.read_account_cut(other, scope.account, scope.binding.instrument) is None
            assert not candidate.verify_account_cut(other, scope.account, scope.binding,
                                                    scope.trading_day, preview.cut_ref, preview.adjusted_net)
    finally:
        if other.in_transaction:
            other.rollback()
        other.close()


def test_transient_clone_connection_never_becomes_comparable(harness, tmp_path):
    scope, source, candidate, _db = harness
    assert candidate.refresh() == "CANDIDATE"
    source.applied.update({"a": "fact-a", "b": "fact-b"})
    actual_path = candidate._journal_path
    cloned_path = tmp_path / "cloned-journal"
    saved_path = tmp_path / "saved-original"
    shutil.copyfile(actual_path, cloned_path)
    other = None
    moved_original = False
    try:
        # The open connection keeps the clone even after the path is restored.
        os.replace(actual_path, saved_path)
        moved_original = True
        os.replace(cloned_path, actual_path)
        other = sqlite3.connect(actual_path, isolation_level=None)
        os.replace(actual_path, cloned_path)
        os.replace(saved_path, actual_path)
        moved_original = False
        other.execute("BEGIN")
        with candidate.source_fence():
            # PRAGMA reports the restored path, and the cloned anchor matches.
            candidate._check_transaction(other)
            preview = candidate.preview(other, scope.account, scope.binding.instrument)
            assert preview.status == "NOT_COMPARABLE"
            assert preview.reason == "OWNER_TRANSACTION_UNVERIFIED"
            assert candidate.read_account_cut(other, scope.account, scope.binding.instrument) is None
            assert not candidate.verify_account_cut(other, scope.account, scope.binding,
                                                    scope.trading_day, preview.cut_ref, preview.adjusted_net)
    finally:
        if other is not None:
            if other.in_transaction:
                other.rollback()
            other.close()
        if moved_original:
            os.replace(saved_path, actual_path)
