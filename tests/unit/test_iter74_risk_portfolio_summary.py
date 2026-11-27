"""Unit tests for Iteration 74.01 Risk read-only portfolio exposure & leverage summary."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.risk_service_protocol import RISK_ADMIN_OPERATIONS
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.risk_service_component import RiskOrderService
from bt_api_py.risk_service_config import RiskInstrumentBinding, RiskPrincipal
from bt_api_py.risk_service_host import build_risk_portfolio_summary
from bt_api_risk.pretrade.facts import AccountSnapshot, InstrumentFacts, PriceFact

RB = MarketInstrumentKey("ctp", "SHFE", "simnow", "futures", "rb2701", "rb2701")
HC = MarketInstrumentKey("ctp", "SHFE", "simnow", "futures", "hc2701", "hc2701")


class _FakeFacts:
    def __init__(self, snapshot, prices, facts, *, attested=()):
        self._snapshot = snapshot
        self._prices = prices
        self._facts = facts
        self._attested = frozenset(attested)

    def account_snapshot(self):
        return self._snapshot

    def reference_price(self, instrument):
        return self._prices.get(instrument)

    def instrument_facts(self, instrument):
        return self._facts.get(instrument)

    def has_attested_market_ref(self, instrument):
        return instrument in self._attested


def _bindings():
    return {
        "rb2701": RiskInstrumentBinding("rb2701", RB, Decimal("10"), Decimal("1")),
        "hc2701": RiskInstrumentBinding("hc2701", HC, Decimal("10"), Decimal("1")),
    }


def test_build_risk_portfolio_summary_complete_exact_decimals():
    snapshot = AccountSnapshot(
        as_of_ns=1_760_000_000_000_000_000,
        positions={
            ("rb2701", "LONG"): (Decimal("2"), Decimal("3")),   # 5 * 10 * 3500 = 175000
            ("hc2701", "SHORT"): (Decimal("1"), Decimal("1")),  # 2 * 10 * 3600 = 72000
        },
        balance=Decimal("100000"),
        available=Decimal("70000"),
        margin=Decimal("30000"),
        equity=Decimal("100000"),
        currency="CNY",
        complete=True,
    )
    facts = _FakeFacts(
        snapshot,
        prices={
            RB: PriceFact(RB, last_price=Decimal("3500"), quality_ok=True),
            HC: PriceFact(HC, last_price=Decimal("3600"), quality_ok=True),
        },
        facts={
            RB: InstrumentFacts(RB, multiplier=Decimal("10"), tick_size=Decimal("1")),
            HC: InstrumentFacts(HC, multiplier=Decimal("10"), tick_size=Decimal("1")),
        },
    )
    summary = build_risk_portfolio_summary(facts, instruments=_bindings())
    assert summary["valuation_status"] == "COMPLETE"
    assert summary["snapshot_complete"] is True
    assert summary["currency"] == "CNY"
    assert summary["equity"] == "100000"
    assert summary["margin_ratio"] == "0.3"
    assert summary["long_notional"] == "175000"
    assert summary["short_notional"] == "72000"
    assert summary["gross_notional"] == "247000"
    assert summary["net_notional"] == "103000"
    assert summary["gross_leverage"] == "2.47"
    assert summary["net_leverage"] == "1.03"
    assert summary["missing_reasons"] == []
    assert len(summary["positions"]) == 2
    assert all(p["valuation_status"] == "PRICED" for p in summary["positions"])


def test_build_risk_portfolio_summary_fails_closed_on_missing_price_or_multiplier_or_zero_equity():
    snapshot = AccountSnapshot(
        as_of_ns=1_760_000_000_000_000_000,
        positions={
            ("rb2701", "LONG"): (Decimal("2"), Decimal("0")),
            ("hc2701", "SHORT"): (Decimal("1"), Decimal("0")),
        },
        equity=Decimal("100000"),
        currency="CNY",
        complete=False,
    )
    facts = _FakeFacts(
        snapshot,
        prices={
            RB: PriceFact(RB, last_price=Decimal("3500"), quality_ok=True),
            HC: PriceFact(HC, last_price=Decimal("3600"), quality_ok=False),
        },
        facts={
            RB: InstrumentFacts(RB, multiplier=Decimal("10"), tick_size=Decimal("1")),
            HC: InstrumentFacts(HC, multiplier=Decimal("10"), tick_size=Decimal("1")),
        },
    )
    summary = build_risk_portfolio_summary(facts, instruments=_bindings())
    assert summary["valuation_status"] == "PARTIAL"
    assert summary["snapshot_complete"] is False
    # Because hc2701 price is bad quality, total notional & leverages MUST stay None.
    assert summary["gross_notional"] is None
    assert summary["net_notional"] is None
    assert summary["gross_leverage"] is None
    assert summary["net_leverage"] is None
    # Partial priced portion is still reported explicitly.
    assert summary["priced_gross_notional"] == "70000"
    assert "PRICE_QUALITY_UNVERIFIED" in summary["missing_reasons"]
    assert "ACCOUNT_SNAPSHOT_INCOMPLETE" in summary["missing_reasons"]

    # Zero equity must never divide by zero or invent 0 leverage.
    zero_eq = AccountSnapshot(
        as_of_ns=1_760_000_000_000_000_000,
        positions={("rb2701", "LONG"): (Decimal("1"), Decimal("0"))},
        equity=Decimal("0"),
        currency="CNY",
        complete=True,
    )
    summary_zero = build_risk_portfolio_summary(
        _FakeFacts(
            zero_eq,
            prices={RB: PriceFact(RB, last_price=Decimal("3500"), quality_ok=True)},
            facts={RB: InstrumentFacts(RB, multiplier=Decimal("10"))},
        ),
        instruments=_bindings(),
    )
    assert summary_zero["gross_notional"] == "35000"
    assert summary_zero["gross_leverage"] is None
    assert "EQUITY_UNAVAILABLE" in summary_zero["missing_reasons"]

    # Missing account snapshot returns UNAVAILABLE cleanly.
    none_summary = build_risk_portfolio_summary(_FakeFacts(None, {}, {}), instruments=_bindings())
    assert none_summary["valuation_status"] == "UNAVAILABLE"
    assert none_summary["missing_reasons"] == ["ACCOUNT_SNAPSHOT_UNAVAILABLE"]


def test_build_risk_portfolio_summary_computes_indicative_notional_only_for_gateway_attested_ticks():
    snapshot = AccountSnapshot(
        as_of_ns=1_760_000_000_000_000_000,
        positions={
            ("hc2701", "LONG"): (Decimal("4"), Decimal("3")),   # 7 * 10 * 3161 = 221270
            ("rb2701", "LONG"): (Decimal("4"), Decimal("3")),   # 7 * 10 * 3067 = 214690
            ("rb2701", "SHORT"): (Decimal("3"), Decimal("1")),  # 4 * 10 * 3067 = 122680
        },
        balance=Decimal("10000000"),
        available=Decimal("9950000"),
        equity=Decimal("10000000"),
        currency="CNY",
        complete=False,
    )
    facts = _FakeFacts(
        snapshot,
        prices={
            HC: PriceFact(HC, last_price=Decimal("3161"), quality_ok=False),
            RB: PriceFact(RB, last_price=Decimal("3067"), quality_ok=False),
        },
        facts={
            HC: InstrumentFacts(HC, multiplier=Decimal("10"), tick_size=Decimal("1")),
            RB: InstrumentFacts(RB, multiplier=Decimal("10"), tick_size=Decimal("1")),
        },
        attested=(HC, RB),
    )
    summary = build_risk_portfolio_summary(facts, instruments=_bindings())
    # Must stay PARTIAL (NEVER COMPLETE!) and keep PRICE_QUALITY_UNVERIFIED honestly.
    assert summary["valuation_status"] == "PARTIAL"
    assert summary["snapshot_complete"] is False
    assert "PRICE_QUALITY_UNVERIFIED" in summary["missing_reasons"]
    assert "ACCOUNT_SNAPSHOT_INCOMPLETE" in summary["missing_reasons"]
    assert "MARGIN_UNAVAILABLE" in summary["missing_reasons"]
    assert summary["long_notional"] == "435960"
    assert summary["short_notional"] == "122680"
    assert summary["gross_notional"] == "558640"
    assert summary["priced_gross_notional"] == "558640"
    assert summary["net_notional"] == "313280"
    assert summary["gross_leverage"] == "0.055864"
    assert summary["net_leverage"] == "0.031328"
    assert all(p["valuation_status"] == "INDICATIVE" and p["price_quality_ok"] is False for p in summary["positions"])

    # Even with complete=True snapshot, INDICATIVE can NEVER promote valuation_status to COMPLETE.
    complete_snap = AccountSnapshot(
        as_of_ns=1_760_000_000_000_000_000,
        positions={("rb2701", "LONG"): (Decimal("1"), Decimal("0"))},
        equity=Decimal("100000"),
        currency="CNY",
        complete=True,
    )
    ind_complete = build_risk_portfolio_summary(
        _FakeFacts(
            complete_snap,
            prices={RB: PriceFact(RB, last_price=Decimal("3067"), quality_ok=False)},
            facts={RB: InstrumentFacts(RB, multiplier=Decimal("10"))},
            attested=(RB,),
        ),
        instruments=_bindings(),
    )
    assert ind_complete["valuation_status"] == "PARTIAL"
    assert ind_complete["gross_notional"] == "30670"
    assert ind_complete["positions"][0]["valuation_status"] == "INDICATIVE"


def test_risk_order_service_admin_status_portfolio_summary_opt_in_and_fail_closed():
    class _Engine:
        def status_summary(self):
            return {"available": True, "policy_version": 2}

        def record_admin_refusal(self, *args, **kwargs):
            return None

    principal = RiskPrincipal(
        principal_ref="op-1",
        role="operator",
        actor_ref="ops-actor",
        strategy_refs=frozenset(),
        operations=RISK_ADMIN_OPERATIONS,
        public_key=b"0" * 40,
    )
    ctx = SimpleNamespace(
        principal_ref="op-1",
        identity=WireIdentity(WireRole.APPLICATION_CLIENT, "op-1", "ops-actor"),
        scope=WireScope("dom-1", account_ref="acct-1", strategy_ref=None, run_id=None),
        operations=RISK_ADMIN_OPERATIONS,
        registration_id="reg-1",
    )

    sample = build_risk_portfolio_summary(_FakeFacts(None, {}, {}), instruments={})
    svc = RiskOrderService(
        _Engine(),
        object(),
        account_scope="acct-1",
        route="ctp_simnow",
        execution_domain_ref="dom-1",
        principals={"op-1": principal},
        instrument_for=lambda *_args: None,
        ready=lambda: True,
        portfolio_summary_observe=lambda: sample,
    )
    plain = svc.handle("risk.admin.status", context=ctx, arguments={})
    assert "portfolio_summary" not in plain["summary"]

    requested = svc.handle("risk.admin.status", context=ctx, arguments={"portfolio_summary": True})
    assert requested["status"] == "OBSERVED"
    assert requested["summary"]["portfolio_summary"] == sample

    def boom():
        raise RuntimeError("sensitive-portfolio-error")

    broken_svc = RiskOrderService(
        _Engine(),
        object(),
        account_scope="acct-1",
        route="ctp_simnow",
        execution_domain_ref="dom-1",
        principals={"op-1": principal},
        instrument_for=lambda *_args: None,
        ready=lambda: True,
        portfolio_summary_observe=boom,
    )
    broken = broken_svc.handle("risk.admin.status", context=ctx, arguments={"portfolio_summary": True})
    assert broken["summary"]["portfolio_summary"]["valuation_status"] == "UNAVAILABLE"
    assert "sensitive-portfolio-error" not in repr(broken)
