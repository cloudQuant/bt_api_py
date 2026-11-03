"""Iteration 60 F4: the provider cut capability drives the bootstrap end to end.

The capability below is a local FAKE provider: it numbers its own pushes and
names the last sequence each snapshot contains. Its verifier only checks the
claims this fake issued; it authenticates no real source (UNIT/FAKE).
"""

from __future__ import annotations

import threading
import time
from decimal import Decimal

import pytest
from bt_api_base.account_protocol import AccountKey, AccountPosition, AccountResourceKey
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.position_bootstrap_protocol import (
    PositionStreamBinding,
    PositionStreamEvent,
    PositionStreamSnapshot,
)

from bt_api_py.position_stream_bootstrap import (
    PROVIDER_CUT_UNAVAILABLE,
    PositionStreamCutCapability,
    VerifiedPositionStreamCut,
    resolve_position_stream_cut,
)
from bt_api_py.strategy_ledger import (
    InstrumentTerms,
    LedgerIntent,
    SqliteStrategyLedger,
    StrategyBudget,
)

ACCOUNT_KEY = AccountKey("fake", "fakevenue", "paper", "local-account")


def _row(instrument, quantity):
    return AccountPosition(
        AccountResourceKey("future", "default"),
        MarketInstrumentKey("fake", "fakevenue", "paper", "future", instrument, instrument),
        "net",
        str(quantity),
        "contracts",
        None,
        None,
        "2026-11-03T00:00:00Z",
    )


class FakeCutProvider:
    """FAKE provider with a per-subscription sequence and a sequenced snapshot."""

    def __init__(self, now):
        self.now = now
        self.positions: dict[str, int] = {}
        self.sequence = 0
        self.epoch = 0
        self.claims: list = []
        self.listener = None  # driver.on_event
        self.during_read = None
        self.during_open = None
        self.binding = None

    # -- provider side ------------------------------------------------------

    def change(self, instrument, quantity, *, deliver=True):
        self.positions[instrument] = quantity
        self.sequence += 1
        raw = {
            "kind": "position",
            "subscription_epoch": self.epoch,
            "sequence": self.sequence,
            "symbol": instrument,
            "quantity": quantity,
        }
        if deliver and self.listener is not None:
            self.listener(raw)
        return raw

    # -- capability ---------------------------------------------------------

    def open_position_stream(self, account_ref):
        self.epoch += 1
        self.sequence = 0
        self.binding = PositionStreamBinding(
            ACCOUNT_KEY, account_ref, "fake-route", "session-1", 1, self.epoch,
            f"ready-{self.epoch}", self.now[0] + 10_000,
        )
        if self.during_open is not None:
            self.during_open()
        return self.binding

    def position_stream_verifier(self, binding):
        provider = self

        class _Verifier:
            def verify_binding(self, claimed):
                return claimed == provider.binding

            def verify_snapshot(self, claimed, snapshot):
                return claimed == provider.binding and snapshot in provider.claims

            def verify_event(self, claimed, event):
                return claimed == provider.binding and event in provider.claims

            def verify_live(self, claimed, now_ns):
                return claimed == provider.binding

        return _Verifier()

    def read_position_cut(self, binding):
        cut = self.sequence
        rows = tuple(_row(name, qty) for name, qty in sorted(self.positions.items()) if qty)
        if self.during_read is not None:
            self.during_read()
        snapshot = PositionStreamSnapshot(binding, cut, self.now[0], f"cut-{cut}", rows)
        self.claims.append(snapshot)
        return snapshot

    def position_stream_event(self, binding, raw):
        epoch = raw.get("subscription_epoch")
        if raw.get("kind") != "position" or epoch != binding.subscription_epoch:
            return None
        event = PositionStreamEvent(
            binding, raw["sequence"], self.now[0], f"push-{raw['sequence']}",
            _row(raw["symbol"], raw["quantity"]),
        )
        self.claims.append(event)
        return event


def _driver(provider, now, **kwargs):
    driver = VerifiedPositionStreamCut(
        provider, account_ref="account-a", source="fake:cut", clock_ns=lambda: now[0], **kwargs
    )
    provider.listener = driver.on_event
    return driver


def test_fake_provider_declares_the_capability():
    assert isinstance(FakeCutProvider([1]), PositionStreamCutCapability)


def test_push_during_snapshot_read_is_replayed_not_overwritten():
    """F4 counterexample: push X=1 during the read of an empty snapshot."""
    now = [100]
    provider = FakeCutProvider(now)
    driver = _driver(provider, now)
    provider.during_read = lambda: provider.change("X", 1)
    assert driver.start() is True
    report = driver.reporter().report()
    assert report.complete is True
    assert report.positions == {"X": Decimal("1")}
    assert report.valid_until_ns == provider.binding.valid_until_ns


def test_pushes_already_in_the_snapshot_are_not_applied_twice_and_later_ones_apply():
    now = [100]
    provider = FakeCutProvider(now)
    driver = _driver(provider, now)
    provider.during_open = lambda: provider.change("X", 2)  # pre-binding push
    assert driver.start() is True
    assert driver.reporter().report().positions == {"X": Decimal("2")}
    provider.change("X", 0)
    provider.change("Y", 3)
    report = driver.reporter().report()
    assert report.complete is True and report.positions == {"Y": Decimal("3")}


def test_empty_account_cut_is_complete_and_empty():
    now = [100]
    driver = _driver(FakeCutProvider(now), now)
    assert driver.start() is True
    report = driver.reporter().report()
    assert report.complete is True and report.positions == {}


def test_sequence_gap_interrupts_and_restart_opens_a_new_epoch_rate_limited():
    now = [100]
    provider = FakeCutProvider(now)
    driver = _driver(provider, now, retry_interval_ns=50)
    assert driver.start() is True
    provider.change("X", 1, deliver=False)  # lost push
    provider.change("X", 4)
    report = driver.reporter().report()
    assert report.complete is False
    assert driver.interruption == "EVENT_SEQUENCE_GAP"
    assert driver.restart_if_due() is False  # within the retry interval
    now[0] += 60
    assert driver.restart_if_due() is True
    assert provider.epoch == 2
    report = driver.reporter().report()
    assert report.complete is True and report.positions == {"X": Decimal("4")}
    # A late push of the old epoch is ignored by the new subscription.
    driver.on_event({"kind": "position", "subscription_epoch": 1, "sequence": 9,
                     "symbol": "Z", "quantity": 5})
    assert driver.reporter().report().positions == {"X": Decimal("4")}


def test_disconnect_while_opening_or_after_install_fails_closed():
    now = [100]
    provider = FakeCutProvider(now)
    driver = _driver(provider, now)
    provider.during_open = driver.on_disconnect
    assert driver.start() is False
    assert driver.reporter().report().complete is False
    provider.during_open = None
    assert driver.start() is True
    driver.on_disconnect()
    assert driver.reporter().report().complete is False
    assert driver.interruption == "STREAM_DISCONNECTED"


def test_lease_expiry_makes_the_report_incomplete():
    now = [100]
    provider = FakeCutProvider(now)
    driver = _driver(provider, now)
    assert driver.start() is True
    now[0] = provider.binding.valid_until_ns
    assert driver.reporter().report().complete is False


def test_unverifiable_snapshot_is_refused():
    now = [100]
    provider = FakeCutProvider(now)
    driver = _driver(provider, now)
    original = provider.read_position_cut

    def forged(binding):
        snapshot = original(binding)
        provider.claims.remove(snapshot)
        return snapshot

    provider.read_position_cut = forged
    assert driver.start() is False
    assert driver.interruption == "VERIFIER_REJECTED"


def test_concurrent_push_during_blocked_read_is_replayed():
    now = [100]
    provider = FakeCutProvider(now)
    driver = _driver(provider, now)
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(5)

    provider.during_read = block
    worker = threading.Thread(target=driver.start)
    worker.start()
    try:
        assert entered.wait(5)
        provider.change("X", 7)
    finally:
        release.set()
        worker.join(5)
    assert driver.reporter().report().positions == {"X": Decimal("7")}


def test_verified_cut_reconciles_the_ledger_balanced_and_residual(tmp_path):
    now = [time.time_ns()]
    provider = FakeCutProvider(now)
    driver = _driver(provider, now)
    provider.during_read = lambda: provider.change("X", 2)
    assert driver.start() is True
    ledger = SqliteStrategyLedger(
        tmp_path / "ledger.sqlite3",
        account_ref="account-a",
        instruments={"X": InstrumentTerms(Decimal("1"), "USD", "spot")},
        budgets={"A": StrategyBudget(Decimal("1000000"), "USD")},
        clock_ns=lambda: now[0],
    )
    try:
        ledger.reserve(LedgerIntent("a", "A", "X", "BUY", Decimal("2"), Decimal("10")))
        ledger.apply_execution("a", cumulative_quantity=2, average_price=10)
        assert ledger.reconcile(driver.reporter().report()).state == "BALANCED"
        provider.change("X", 5)
        assert ledger.reconcile(driver.reporter().report()).state in {"RESIDUAL", "IN_FLIGHT"}
        driver.on_disconnect()
        outcome = ledger.reconcile(driver.reporter().report())
        assert (outcome.state, outcome.reason) == ("UNVERIFIED", "REPORT_INCOMPLETE")
    finally:
        ledger.close()


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("OKX___SWAP", "POSITION_PUSH_HAS_NO_SEQUENCE"),
        ("BINANCE___SWAP", "POSITION_PUSH_HAS_NO_UPDATE_ID"),
        ("CTP___FUTURE", "NO_POSITION_PUSH"),
        ("other", "PROVIDER_CUT_NOT_DECLARED"),
    ],
)
def test_builtin_providers_stay_fail_closed_with_the_missing_fact(name, reason):
    assert resolve_position_stream_cut(object(), name) == (None, reason)
    assert set(PROVIDER_CUT_UNAVAILABLE) == {"okx", "binance", "ctp"}


def test_declared_capability_is_resolved_and_invalid_declarations_refused():
    provider = FakeCutProvider([1])

    class _Api:
        position_stream_cut_capability = provider

    assert resolve_position_stream_cut(_Api(), "okx") == (provider, None)

    class _Callable:
        def position_stream_cut_capability(self):
            return provider

    assert resolve_position_stream_cut(_Callable(), None) == (provider, None)

    class _Bogus:
        position_stream_cut_capability = object()

    assert resolve_position_stream_cut(_Bogus(), None) == (None, "CAPABILITY_INVALID")

    class _Raises:
        def position_stream_cut_capability(self):
            raise RuntimeError

    assert resolve_position_stream_cut(_Raises(), None) == (
        None, "CAPABILITY_DECLARATION_FAILED"
    )
