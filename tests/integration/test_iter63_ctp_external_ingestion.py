"""Synthetic sealed CTP query through real owner and external journal."""
from contextlib import contextmanager

from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.external_position_ledger import ExternalFactEvidence
from bt_api_gateway.strategy_position_ledger import StrategyBaselineEvidence, StrategyInstrumentBinding
from bt_api_py.ctp_gateway_return_ingestion import CtpGatewayReturnIngestor, TrustedExternalClassification

from tests.integration.test_iter63_ctp_gateway_return_ingestion import (
    ACCOUNT, ACCOUNT_KEY, DAY, INSTRUMENT, _reader, _trade, owner_guard,  # noqa: F401
)
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal


@contextmanager
def fence():
    yield


def test_sealed_external_classification_uses_real_journal_and_requires_proof(tmp_path, owner_guard):  # noqa: F811
    reader, _, native = _reader()
    guard = owner_guard
    journal = GatewayDirectCommandJournal.create(tmp_path / "journal", ACCOUNT, generation=guard.acquire())
    journal.enable_controls(guard)
    journal.enable_strategy_ledger(guard)
    journal.enable_account_extensions(guard)
    baseline = StrategyBaselineEvidence(ACCOUNT, journal.journal_id, DAY, "CTP___FUTURE", journal.generation,
        1, "source", "positions", "orders", "baseline-cut", ("coverage",), 100, 200, True, True, True, True)
    journal.initialize_empty_strategy_ledger(baseline, guard=guard, baseline_fence=fence,
                                             verify_current_empty=lambda _: True)
    ledger = GatewayDirectExecutionLedger(journal, guard)
    native.rows["trades"] = [_trade()]
    # A sealed CTP query alone does not establish external provenance or a cut.
    ingestor = CtpGatewayReturnIngestor(reader, ledger, account_key=ACCOUNT_KEY, instruments=(INSTRUMENT,))
    assert ingestor.read_and_ingest("trades", timeout=1).unattributed_count == 1
    with journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM external_position_events").fetchone()[0] == 0

    def incomplete(source, fact):
        evidence = ExternalFactEvidence(ACCOUNT, journal.journal_id, journal.generation,
            source.connection_generation, fact.route, fact.trading_day, fact.fact_digest(), fact.source_ref,
            "unproved-cut", "unproved-exclusion")
        return TrustedExternalClassification(evidence, fence, lambda *_: False)
    unproved = CtpGatewayReturnIngestor(reader, ledger, account_key=ACCOUNT_KEY,
                                       instruments=(INSTRUMENT,), external_classifier=incomplete)
    unproved.read_and_ingest("trades", timeout=1)
    with journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM external_position_events").fetchone()[0] == 0

    calls = []
    def classifier(source, fact):
        evidence = ExternalFactEvidence(ACCOUNT, journal.journal_id, journal.generation,
            source.connection_generation, fact.route, fact.trading_day, fact.fact_digest(), fact.source_ref,
            "trusted-synthetic-cut", "all-system-orders-excluded")
        def verify(db, actual, received):
            calls.append((actual, received))
            return actual == fact and received == evidence
        return TrustedExternalClassification(evidence, fence, verify)
    trusted = CtpGatewayReturnIngestor(reader, ledger, account_key=ACCOUNT_KEY,
                                      instruments=(INSTRUMENT,), external_classifier=classifier)
    # Only an installed trusted source can classify the already-durable fact.
    trusted.read_and_ingest("trades", timeout=1)
    with journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM external_position_events").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM external_position_trades").fetchone()[0] == 1
    assert len(calls) == 2
    trusted.read_and_ingest("trades", timeout=1)
    with journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM external_position_trades").fetchone()[0] == 1
    binding = StrategyInstrumentBinding(INSTRUMENT, "CTP___FUTURE", "lots", True)
    current = {"trusted": True}
    verify_external = lambda *_: current["trusted"]
    journal.install_strategy_ledger_ports(guard=guard, resolve_binding=lambda _: binding,
        current_trading_day=lambda: DAY, verify_day_transition=lambda *_: False,
        verify_current_continuity=lambda *_: True, verify_external_current=verify_external)
    journal.install_account_comparison_ports(guard=guard, source_fence=fence,
        read_account_cut=lambda *_: (binding, DAY, "current-cut", "1"),
        verify_account_cut=lambda *_: current["trusted"], verify_external_current=verify_external,
        verify_external_cut_current=lambda *_: current["trusted"])
    comparison = journal.read_account_net_comparison(INSTRUMENT)
    assert comparison.status == "MATCH" and comparison.external_net == 1 and comparison.account_net == 1
    current["trusted"] = False
    assert journal.read_account_net_comparison(INSTRUMENT).status == "UNAVAILABLE"
