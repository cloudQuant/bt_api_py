"""Synthetic source installed into the real Gateway comparison journal."""
from contextlib import contextmanager

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_gateway.strategy_position_ledger import StrategyBaselineEvidence, StrategyInstrumentBinding
from bt_api_py.gateway_reconciliation_candidate import (
    ActivityObservation,
    GatewayReconciliationCandidate,
    PositionObservation,
    ReconciliationScope,
    TradeObservationBatch,
)


def test_verified_local_candidate_cannot_enable_unattested_gateway_comparison(tmp_path):
    guard_dir = tmp_path / "guards"
    guard_dir.mkdir()
    guard = LocalAccountSessionGuard(guard_dir, "account", "owner")
    generation = guard.acquire()
    try:
        journal = GatewayDirectCommandJournal.create(tmp_path / "journal", "account", generation=generation)
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        journal.enable_account_extensions(guard)
        instrument = MarketInstrumentKey("ctp", "CFFEX", "simnow", "future", "IF2612", "IF2612")
        binding = StrategyInstrumentBinding(instrument, "CTP___FUTURE", "lots", True)

        @contextmanager
        def baseline_fence():
            yield

        baseline = StrategyBaselineEvidence("account", journal.journal_id, "20300101", binding.route,
            journal.generation, 1, "synthetic-source", "positions", "orders", "baseline",
            ("synthetic-coverage",), 100, 200, True, True, True, True)
        journal.initialize_empty_strategy_ledger(baseline, guard=guard, baseline_fence=baseline_fence,
                                                 verify_current_empty=lambda _: True)
        scope = ReconciliationScope("account", journal.generation, "synthetic-session", "20300101", binding)

        class Source:
            valid = True
            position = PositionObservation(scope, "position-snapshot", "position-evidence", "0", True)
            trades = TradeObservationBatch(scope, "trade-snapshot", "trade-evidence", True, ())
            activity = ActivityObservation(scope, "activity-snapshot", "activity-evidence", True, 0, 0, 0)
            @contextmanager
            def hold(self, actual):
                assert actual == scope
                yield lambda: self.valid
            def read_position(self, _scope):
                return self.position
            def read_trades(self, _scope):
                return self.trades
            def read_activity(self, _scope):
                return self.activity
            def verify(self, position, trades, activity):
                return (self.valid and position is self.position and trades is self.trades
                        and activity is self.activity)

        source = Source()
        candidate = GatewayReconciliationCandidate(scope, source, journal=journal, guard=guard,
                                                     is_applied=lambda *_: True)
        assert candidate.refresh() == "CANDIDATE"
        journal.install_strategy_ledger_ports(guard=guard, resolve_binding=lambda _: binding,
            current_trading_day=lambda: scope.trading_day, verify_day_transition=lambda *_: False,
            verify_current_continuity=lambda *_: source.valid, verify_external_current=lambda *_: source.valid)
        journal.install_account_comparison_ports(guard=guard, source_fence=candidate.source_fence,
            read_account_cut=candidate.read_account_cut, verify_account_cut=candidate.verify_account_cut,
            verify_external_current=lambda *_: source.valid, verify_external_cut_current=lambda *_: source.valid)
        with candidate.source_fence(), journal._transaction() as db:
            preview = candidate.preview(db, scope.account, instrument)
            assert preview.status == "NOT_COMPARABLE"
            assert preview.reason == "OWNER_TRANSACTION_UNVERIFIED"
            assert preview.adjusted_net == "0"
        # Even the actual owner path cannot claim a comparable cut until the
        # journal exposes and verifies an owner-issued transaction capability.
        assert journal.read_account_net_comparison(instrument).status == "UNAVAILABLE"
        source.valid = False
        assert journal.read_account_net_comparison(instrument).status == "UNAVAILABLE"
    finally:
        guard.release()
