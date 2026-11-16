"""R08 evaluation to passive audit reads with explicit in-memory substitutes.

No order is submitted and no actual audit writer, identity provider, account,
transport, storage, or notification is exercised. The fake audit producer
copies evaluation warnings using the engine's documented audit detail shape.
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_risk.pretrade.controls import Exposure, FactSnapshot, OrderShape, check_margin
from bt_api_risk.pretrade.engine import PretradeEngine
from bt_api_risk.pretrade.policy import validate_policy_document
from bt_api_py.risk_service_component import RiskOrderService
from bt_api_py.risk_service_config import RiskPrincipal
from tests.unit.test_iter64_risk_service_component import V2_POLICY_BLOCKED

# ``_inputs`` validates a v1 policy; the product is v2-only and v2 activation
# is blocked by RULE_R04_V2_UNAVAILABLE pending the iteration-64 R-04/R-11
# decision, so no policy reaches the engine yet.  Strict xfail keeps the cases
# collected and flags an XPASS when the activation lands.
pytestmark = V2_POLICY_BLOCKED

D = Decimal
DAY = "20261114"
ACCOUNT, DOMAIN = "fake-account", "fake-domain"
OPERATION = "risk.audit.read"


def _inputs():
    policy = validate_policy_document({
        "schema": "bt_api_risk.policy.v1", "policy_id": "fake-policy",
        "account": {"route": "fake-route", "account_scope": ACCOUNT},
        "per_order": {"max_quantity": {"s": {"i": "3"}},
                      "max_notional": {"s": {"i": "100"}}, "max_reference_age_ms": 3},
        "margin_precheck": {"mode": "warn", "max_reference_age_ms": 1},
        "self_trade": {"enabled": False}, "closable_check": {"enabled": False},
    })
    order = OrderShape("s", "i", "v", "BUY", "OPEN", "INCREASE", "LONG", "OPEN", D("2"), None)
    now = 10_000_000
    facts = FactSnapshot(now, DAY,
        SimpleNamespace(last_price=D("20"), quality_ok=True, source_time_ns=now, received_time_ns=now),
        SimpleNamespace(multiplier=D("2")),
        SimpleNamespace(complete=True, trading_day=DAY, as_of_ns=1, available=D("1")), None,
        SimpleNamespace(complete=True, long_rate=D("0.1"), short_rate=D("0.2")))
    return policy, order, facts


def _txn():
    return SimpleNamespace(cursor=lambda: {"continuity": "VERIFIED"},
        permits_in=lambda *args: [], terminal_with_pending_fills=lambda *args: [],
        holding_parents=lambda *args: [], working_orders=lambda *args: [],
        trades_after=lambda *args: [], children=lambda *args: [],
        counter=lambda *args: D(0), position=lambda *args: D(0))


def _evaluations(monkeypatch):
    policy, order, facts = _inputs()
    engine = PretradeEngine.__new__(PretradeEngine)
    engine._active = SimpleNamespace(version=1, policy=policy)
    for name, result in (("_holding_parents_consistent", True), ("_account_counter_error", None),
                         ("_loss_breaches", None), ("_latch_code", None),
                         ("_custom_rules", None), ("_duplicate", None),
                         ("_day_counter_coverage_verified", False),
                         ("_exchange_rule_scope_verified", False)):
        monkeypatch.setattr(engine, name, lambda *args, value=result: value)
    txn = _txn()
    view = SimpleNamespace(trading_day=DAY, request_key="fake-key")
    permit = SimpleNamespace(policy_version=1, permit_id="fake-permit", expires_ns=None)
    initial = engine._evaluate(txn, engine._active, order, view, None, facts, exclude=None, caller=None)
    claim = engine._evaluate_claim(txn, permit, view, order, facts, None)
    return engine, txn, policy, order, facts, initial, claim


class FakeAudit:
    def __init__(self, evaluations):
        # This is a substitute producer, not an invocation of engine.reserve/claim audit.
        self.rows = [{"seq": i, "kind": kind, "detail": {"warnings": sorted(set(ev.warnings))}}
                     for i, (kind, ev) in enumerate(zip(("reserve", "claim"), evaluations), 1)]
        self.reads = []

    def read_audit_page(self, caller, after, limit, through, snapshot):
        self.reads.append((after, limit))
        if through is None:
            through, snapshot = max([after, *(row["seq"] for row in self.rows)]), 3
        records = [row for row in self.rows if after < row["seq"] <= through][:limit]
        next_after = records[-1]["seq"] if records else after
        return {"records": records, "next_after": next_after, "through": through,
                "snapshot": snapshot, "has_more": len(records) == limit and next_after < through}

    def audit_read_rejection(self, caller):
        pass  # Explicit substitute; this fixture does not claim a durable audit write.


def _service(audit):
    operator = RiskPrincipal("fake-operator", "operator", "fake-ops", frozenset(),
                             frozenset({OPERATION}), b"a" * 40)
    denied = RiskPrincipal("fake-no-grant", "operator", "fake-ops", frozenset(),
                           frozenset(), b"b" * 40)
    return RiskOrderService(audit, None, account_scope=ACCOUNT, route="fake-route",
        execution_domain_ref=DOMAIN, principals={p.principal_ref: p for p in (operator, denied)},
        instrument_for=lambda *args: None, ready=lambda: True)


def _context(principal="fake-operator", account=ACCOUNT, fits=lambda page: True):
    return SimpleNamespace(identity=WireIdentity(WireRole.APPLICATION_CLIENT, principal, "fake-ops"),
        scope=WireScope(DOMAIN, account_ref=account), principal_ref=principal, result_fits=fits)


def _read(service, *, context=None, after=0, limit=100, through=None, snapshot=None):
    return service.handle(OPERATION, context=context or _context(), arguments={"after": after, "limit": limit,
                                                                               "through": through, "snapshot": snapshot})


def test_r08_real_engine_warn_and_synthetic_controls_insufficient(monkeypatch):
    engine, txn, policy, order, facts, initial, claim = _evaluations(monkeypatch)
    # An insufficient synthetic funds comparison is possible only if uncovered
    # occupancy is supplied as known. Real _exposure currently refuses that premise.
    synthetic = Exposure(True, True, D(0), D(0), D(0), D(0), uncovered_margin=D(0))
    assert check_margin(policy, order, facts, synthetic) == "risk_margin_insufficient"
    assert engine._exposure(txn, order, facts, None, D(0)).uncovered_margin is None
    for evaluation in (initial, claim):
        assert evaluation.code is None
        assert evaluation.warnings == ["risk_margin_facts_unavailable"]


def test_sdk_audit_reads_fake_warnings_with_scope_and_budget_checks(monkeypatch):
    *_, initial, claim = _evaluations(monkeypatch)
    audit = FakeAudit((initial, claim))
    service = _service(audit)
    page = _read(service)
    assert page == {"status": "OBSERVED", "records": audit.rows, "next_after": 2,
                    "through": 2, "snapshot": 3, "has_more": False}
    assert _read(service, context=_context("fake-no-grant"))["code"] == "risk_unauthorized"
    assert _read(service, context=_context(account="other-account"))["code"] == "risk_unauthorized"
    assert audit.reads == [(0, 100)]
    assert _read(service, limit=1001) == {"status": "REQUEST_REJECTED", "code": "risk_request_invalid"}
    refused = _read(service, context=_context(fits=lambda page: len(page["records"]) <= 1), limit=2)
    assert refused == {"status": "REQUEST_REJECTED", "code": "risk_audit_page_too_large"}
    first = _read(service, after=0, limit=1)
    second = _read(service, after=first["next_after"], limit=1,
                   through=first["through"], snapshot=first["snapshot"])
    assert [first["records"][0]["seq"], second["records"][0]["seq"]] == [1, 2]
    assert second["next_after"] == 2


def test_web_audit_handler_reads_and_refuses_fake_principals(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from bt_api_risk.web.app import AuthenticatedRiskSession, create_app

    *_, initial, claim = _evaluations(monkeypatch)
    audit = FakeAudit((initial, claim))
    service = _service(audit)
    session = [AuthenticatedRiskSession("fake-operator", "fake-ops", "x" * 64, frozenset({OPERATION}))]
    context = [_context()]
    calls = []

    def call(operation, arguments, timeout):
        calls.append((operation, arguments))
        return service.handle(operation, context=context[0], arguments=arguments)

    app = create_app(origin="http://127.0.0.1:8710", authenticate=lambda request: session[0], admin_caller=call)
    with TestClient(app, base_url="http://127.0.0.1:8710", client=("127.0.0.1", 50000)) as client:
        response = client.get("/api/v1/risk/audit?after=0&limit=2")
        assert response.status_code == 200
        assert response.json()["records"] == audit.rows
        assert response.json()["next_after"] == 2
        context[0] = _context(account="other-account")
        response = client.get("/api/v1/risk/audit?after=0&limit=2")
        assert response.status_code == 409 and response.json()["code"] == "risk_unauthorized"
        session[0] = AuthenticatedRiskSession("fake-no-grant", "fake-ops", "x" * 64, frozenset())
        before = len(calls)
        response = client.get("/api/v1/risk/audit?after=0&limit=2")
        assert response.status_code == 403 and response.json()["error"] == "OPERATION_NOT_GRANTED"
        assert len(calls) == before
        session[0] = AuthenticatedRiskSession("fake-operator", "fake-ops", "x" * 64, frozenset({OPERATION}))
        context[0] = _context(fits=lambda page: len(page["records"]) <= 1)
        response = client.get("/api/v1/risk/audit?after=0&limit=2")
        assert response.status_code == 409 and response.json()["code"] == "risk_audit_page_too_large"
        response = client.get("/api/v1/risk/audit?after=0&limit=1")
        assert response.status_code == 200 and response.json()["records"][0]["seq"] == 1
        before = len(calls)
        assert client.get("/api/v1/risk/audit?after=0&limit=1001").status_code == 422
        assert len(calls) == before
