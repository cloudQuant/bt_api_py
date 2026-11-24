"""Parent RPC quarantine: synthetic identities and a ledger that must not be read."""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.risk_service_protocol import RISK_ORDER_OPERATIONS, RISK_SERVICE_SCHEMA, RiskParentView
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_risk.pretrade.engine import RiskParentResult

from bt_api_py.risk_service_component import RiskOrderService


OPS = frozenset(RISK_ORDER_OPERATIONS)
PARENT = RiskParentView(
    account_scope="fake-account", route="fake-route", strategy_ref="strategy-a",
    parent_ref="parent-a", instrument=MarketInstrumentKey("ctp", "SHFE", "fake", "future", "rb", "rb"),
    side="BUY", offset="OPEN", position_side="NET", total_quantity=Decimal("2"),
    quantity_unit="lots", limit_price=Decimal("10"), created_trading_day="20300101",
    valid_until_trading_day="20300101",
)


class NoReadLedger:
    def __init__(self, scenario):
        self.scenario = scenario
        self.calls = 0

    def transaction(self):
        self.calls += 1
        raise AssertionError("parent mutation must not access ledger")


class FakeEngine:
    """Emulates the real engine's default parent-contract quarantine.

    The SDK handler now delegates parent reserve/release to the engine.  Under
    the default (no parent contract, no R05 v2 coverage) the engine returns an
    unattributed UNKNOWN without touching the ledger; this fake preserves that
    contract so the ledger-must-not-be-read assertions stay meaningful.
    """

    def __init__(self):
        self.status_calls = []
        self.parent_calls = []

    def parent_status(self, caller, parent_ref):
        self.status_calls.append((caller.principal_ref, caller.run_id, parent_ref))
        return RiskParentResult(parent_ref, "NOT_FOUND")

    def parent_reserve(self, caller, parent):
        self.parent_calls.append(("reserve", caller.principal_ref, parent.parent_ref))
        return RiskParentResult(parent.parent_ref, "UNKNOWN", code="risk_parent_unavailable")

    def parent_release(self, caller, parent_ref):
        self.parent_calls.append(("release", caller.principal_ref, parent_ref))
        return RiskParentResult(parent_ref, "UNKNOWN", code="risk_parent_unavailable")


def principal(ref="exec-a", role="execution", actor="actor-a", operations=OPS):
    return SimpleNamespace(principal_ref=ref, role=role, actor_ref=actor,
                           strategy_refs=frozenset({"strategy-a"}), operations=operations)


def context(ref="exec-a", actor="actor-a", run="run-a", account="fake-account",
            domain="fake-domain", strategy="strategy-a", role=WireRole.SERVICE):
    return SimpleNamespace(principal_ref=ref, identity=WireIdentity(role, ref, actor),
                           scope=WireScope(domain, account_ref=account, strategy_ref=strategy, run_id=run))


def service(scenario):
    ledger, engine = NoReadLedger(scenario), FakeEngine()
    principals = {
        "exec-a": principal(),
        "exec-b": principal("exec-b", actor="actor-b"),
        "no-access": principal("no-access", actor="actor-c", operations=frozenset()),
        "strategy": principal("strategy", role="strategy", actor="strategy-a"),
    }
    svc = RiskOrderService(engine, ledger, account_scope="fake-account", route="fake-route",
                           execution_domain_ref="fake-domain", principals=principals,
                           instrument_for=lambda *_: None, ready=lambda: True)
    return svc, ledger, engine


def assert_unknown(reply, parent_ref="parent-a"):
    assert reply == {"schema": RISK_SERVICE_SCHEMA, "schema_version": 1,
                     "parent_ref": parent_ref, "outcome": "UNKNOWN", "state": None,
                     "code": "risk_parent_unavailable", "total_quantity": None,
                     "allocated_quantity": None, "unallocated_quantity": None,
                     "policy_version": None}


@pytest.mark.parametrize("scenario,ctx,parent", [
    ("matching historical row", context(), PARENT),
    ("missing row", context(), PARENT),
    ("different principal", context("exec-b", "actor-b"), PARENT),
    ("different run", context(run="run-b"), PARENT),
    ("same key different digest", context(), replace(PARENT, total_quantity=Decimal("3"))),
])
def test_parent_reserve_never_reads_legacy_ledger(scenario, ctx, parent):
    svc, ledger, engine = service(scenario)
    assert_unknown(svc.handle("risk.parent.reserve", context=ctx, arguments={"parent": parent.to_mapping()}))
    assert ledger.calls == 0 and engine.status_calls == []
    assert len(engine.parent_calls) == 1 and engine.parent_calls[0][0] == "reserve"


@pytest.mark.parametrize("scenario", ["matching historical row", "missing row", "different principal/run"])
def test_parent_release_never_reads_legacy_ledger(scenario):
    svc, ledger, engine = service(scenario)
    assert_unknown(svc.handle("risk.parent.release", context=context(), arguments={"parent_ref": "parent-a"}))
    assert ledger.calls == 0 and engine.status_calls == []
    assert len(engine.parent_calls) == 1 and engine.parent_calls[0][0] == "release"


@pytest.mark.parametrize("operation,ctx,arguments,code", [
    ("risk.parent.reserve", context("no-access", "actor-c"), {"parent": PARENT.to_mapping()}, "risk_unauthorized"),
    ("risk.parent.release", context(account="other"), {"parent_ref": "parent-a"}, "risk_unauthorized"),
    ("risk.parent.release", context(domain="other"), {"parent_ref": "parent-a"}, "risk_unauthorized"),
    ("risk.parent.reserve", context(), {"parent": replace(PARENT, strategy_ref="other").to_mapping()}, "risk_unauthorized"),
    ("risk.parent.reserve", context(), {"parent": replace(PARENT, account_scope="other").to_mapping()}, "risk_unauthorized"),
    ("risk.parent.reserve", context(), {"parent": replace(PARENT, route="other").to_mapping()}, "risk_unauthorized"),
    ("risk.parent.release", context("strategy", "strategy-a", role=WireRole.STRATEGY_CLIENT), {"parent_ref": "parent-a"}, "risk_unauthorized"),
    ("risk.parent.reserve", context(), {"parent": {}}, "risk_request_invalid"),
    ("risk.parent.release", context(), {"parent_ref": " bad "}, "risk_request_invalid"),
    ("risk.parent.release", context(), {"parent_ref": "parent-a", "extra": 1}, "risk_request_invalid"),
])
def test_invalid_or_unauthorized_still_rejected(operation, ctx, arguments, code):
    svc, ledger, engine = service("unreadable")
    assert svc.handle(operation, context=ctx, arguments=arguments) == {"status": "REQUEST_REJECTED", "code": code}
    assert ledger.calls == 0 and engine.status_calls == []


def test_parent_status_delegation_unchanged():
    svc, ledger, engine = service("status fake")
    reply = svc.handle("risk.parent.status", context=context(), arguments={"parent_ref": "parent-a"})
    assert reply["outcome"] == "NOT_FOUND" and reply["parent_ref"] == "parent-a"
    assert engine.status_calls == [("exec-a", "run-a", "parent-a")]
    assert ledger.calls == 0
