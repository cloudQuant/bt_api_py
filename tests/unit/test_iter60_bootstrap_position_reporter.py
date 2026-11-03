"""F4 (复验报告 2026-11-03): ledger evidence only from a verified snapshot/stream cut.

``BootstrapPositionReporter`` turns a ``PositionStreamBootstrap`` projection
into an ``AccountPositionReport``; anything short of a verified cut followed by
a contiguous stream is incomplete, so the ledger reconciles ``UNVERIFIED``.
The verifier here is a local test double, not provider evidence.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from bt_api_py.account_position_stream import (
    AccountPositionStreamError,
    BootstrapPositionReporter,
    GatewayAccountPositionStream,
)
from bt_api_py.position_stream_bootstrap import PositionBootstrapError
from bt_api_py.strategy_ledger import (
    InstrumentTerms,
    LedgerIntent,
    SqliteStrategyLedger,
    StrategyBudget,
)
from tests.unit.test_iter60_position_bootstrap_contract import (
    _build,
    _event,
    _snapshot,
    _row,
)

INSTRUMENT = "SA2701"


def _reporter(stream, now):
    return BootstrapPositionReporter(
        stream, account_ref="account-a", source="ctp:verified-cut", clock_ns=lambda: now[0]
    )


@pytest.fixture
def ledger(tmp_path):
    item = SqliteStrategyLedger(
        tmp_path / "ledger.sqlite3",
        account_ref="account-a",
        instruments={INSTRUMENT: InstrumentTerms(Decimal("1"), "CNY", "futures")},
        budgets={"A": StrategyBudget(Decimal("1000000"), "CNY")},
        clock_ns=lambda: 100,
    )
    item.reserve(LedgerIntent("a", "A", INSTRUMENT, "BUY", Decimal("2"), Decimal("10")))
    item.apply_execution("a", cumulative_quantity=2, average_price=10)
    try:
        yield item
    finally:
        item.close()


def test_no_snapshot_is_incomplete(ledger):
    binding, verifier, now, stream = _build()
    reporter = _reporter(stream, now)
    report = reporter.report()
    assert report.complete is False and reporter.interruption == "SNAPSHOT_NOT_INSTALLED"
    assert ledger.reconcile(report).state == "UNVERIFIED"


def test_push_during_snapshot_read_is_replayed_not_lost(ledger):
    """The 复验 F4 interleaving: a push that lands while the snapshot is read."""

    binding, verifier, now, stream = _build()
    stream.push(_event(binding, verifier, 11, "2"))  # after the cut, before install
    stream.install_snapshot(_snapshot(binding, verifier, cut=10, rows=()))  # empty at the cut
    report = _reporter(stream, now).report()
    assert report.complete is True
    assert report.positions == {INSTRUMENT: Decimal("2")}  # not the stale empty snapshot
    assert report.valid_until_ns == binding.valid_until_ns
    assert ledger.reconcile(report).state == "BALANCED"


def test_gap_after_the_cut_is_incomplete(ledger):
    binding, verifier, now, stream = _build()
    stream.install_snapshot(_snapshot(binding, verifier, cut=10, rows=(_row("2"),)))
    with pytest.raises(PositionBootstrapError):
        stream.push(_event(binding, verifier, 12, "3"))  # 11 lost
    reporter = _reporter(stream, now)
    assert reporter.report().complete is False and reporter.interruption == "EVENT_SEQUENCE_GAP"
    assert ledger.reconcile(reporter.report()).state == "UNVERIFIED"


def test_expired_lease_or_disconnect_is_incomplete():
    binding, verifier, now, stream = _build()
    stream.install_snapshot(_snapshot(binding, verifier, cut=10, rows=(_row("2"),)))
    reporter = _reporter(stream, now)
    assert reporter.report().complete is True
    now[0] = binding.valid_until_ns  # lease over
    assert reporter.report().complete is False and reporter.interruption == "LEASE_EXPIRED"
    _b, v2, n2, s2 = _build()
    s2.install_snapshot(_snapshot(_b, v2, cut=10, rows=()))
    s2.interrupt("STREAM_DISCONNECTED")
    assert _reporter(s2, n2).report().complete is False


def test_verifier_refusal_is_incomplete():
    binding, verifier, now, stream = _build()
    stream.install_snapshot(_snapshot(binding, verifier, cut=10, rows=()))
    verifier.live = False
    reporter = _reporter(stream, now)
    assert reporter.report().complete is False and reporter.interruption == "VERIFIER_REJECTED"


def test_reporter_is_bound_to_its_account():
    binding, verifier, now, stream = _build()
    stream.install_snapshot(_snapshot(binding, verifier, cut=10, rows=()))
    other = BootstrapPositionReporter(stream, account_ref="account-b", source="x")
    assert other.report().complete is False and other.interruption == "BINDING_ACCOUNT_MISMATCH"
    with pytest.raises(AccountPositionStreamError):
        BootstrapPositionReporter(object(), account_ref="account-a", source="x")


def test_gateway_stream_is_evidence_only_through_a_verified_cut():
    binding, verifier, now, stream = _build()
    stream.install_snapshot(_snapshot(binding, verifier, cut=10, rows=(_row("2"),)))
    plain = GatewayAccountPositionStream(lambda: None, account_ref="account-a")
    plain.positions.apply_snapshot({(INSTRUMENT, "net"): "2"})  # rows look complete
    assert plain.diagnostic_report().complete is True
    assert plain.report().complete is False  # but they are not ledger evidence
    cut = _reporter(stream, now)
    verified = GatewayAccountPositionStream(
        lambda: None, account_ref="account-a", verified_cut=cut
    )
    assert verified.report().complete is True
    with pytest.raises(AccountPositionStreamError):
        GatewayAccountPositionStream(lambda: None, account_ref="account-z", verified_cut=cut)
