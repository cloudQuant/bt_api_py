"""Status service and Web projection with fake ports; no service or network."""

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from bt_api_base.risk_service_protocol import RISK_SERVICE_SCHEMA
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_risk.pretrade.engine import PretradeEngine
import bt_api_risk.web.app as web
from fastapi.routing import APIRoute
from starlette.requests import Request

from bt_api_py.risk_service_component import RiskOrderService


class FakeLedger:
    def __init__(self):
        self.reads = 0

    @contextmanager
    def transaction(self):
        self.reads += 1
        yield self

    def cursor(self):
        return {"continuity": "VERIFIED", "epoch": 1, "last_sequence": 4,
                "verified_from_zero": True, "gap_reason": None}

    def bypass_count(self): return 0
    def permit_count(self): return 0
    def permits_in(self, states): return []
    def active_latches(self): return []
    def control_intents(self, states): return []
    def cancel_intents(self, states): return []
    def counters_for_day(self, day):
        assert day == "20261115"
        return [{"instrument_id": "synthetic-instrument", "kind": "order", "value": "7"}]


def context(principal="operator", actor="operator-actor", *, account="private-account-sentinel",
            domain="private-domain-sentinel", role=WireRole.APPLICATION_CLIENT,
            strategy=None, run=None):
    return SimpleNamespace(principal_ref=principal, identity=WireIdentity(role, principal, actor),
                           scope=WireScope(domain, account_ref=account,
                                           strategy_ref=strategy, run_id=run))


@pytest.fixture
def setup():
    ledger = FakeLedger()
    engine = object.__new__(PretradeEngine)
    engine._ledger = ledger
    engine._store = SimpleNamespace(load_error=None)
    engine._active = None
    engine._fact = lambda name, expected: "20261115" if name == "trading_day" else None
    principals = {
        "operator": SimpleNamespace(principal_ref="operator", actor_ref="operator-actor", role="operator",
                                    strategy_refs=frozenset(), operations=frozenset({"risk.admin.status"})),
        "ungranted": SimpleNamespace(principal_ref="ungranted", actor_ref="ungranted-actor", role="operator",
                                     strategy_refs=frozenset(), operations=frozenset()),
        "strategy": SimpleNamespace(principal_ref="strategy", actor_ref="strategy-a", role="strategy",
                                    strategy_refs=frozenset({"strategy-a"}), operations=frozenset({"risk.status"})),
    }
    service = RiskOrderService(engine, ledger, account_scope="private-account-sentinel", route="fake-route",
                               execution_domain_ref="private-domain-sentinel", principals=principals,
                               instrument_for=lambda *_: None, ready=lambda: True)
    return service, ledger


def assert_no_sensitive_output(reply):
    encoded = json.dumps(reply, sort_keys=True)
    for value in ("private-account-sentinel", "private-domain-sentinel", "operator-actor",
                  "api_key", "password", "credential", "token", "/private/"):
        assert value not in encoded


def test_authorized_admin_handle_returns_explicit_unavailable(setup):
    service, ledger = setup
    reply = service.handle("risk.admin.status", context=context(), arguments={})
    assert reply["status"] == "OBSERVED"
    utilization = reply["summary"]["threshold_utilization"]
    assert utilization["available"] is False
    assert utilization["status"] == "INCOMPLETE"
    assert utilization["scope"] == "local_ledger"
    # Each reason is a finite (code, status) pair derived from actual checks.
    assert utilization["reasons"] == [
        {"code": "policy_snapshot_unavailable", "status": "UNAVAILABLE"},
        {"code": "trading_day_binding_unverified", "status": "UNVERIFIED"},
        {"code": "account_source_coverage_unverified", "status": "UNVERIFIED"},
        {"code": "instrument_identity_incomplete", "status": "UNVERIFIED"},
    ]
    assert all(reason["status"] in ("UNAVAILABLE", "UNVERIFIED")
               for reason in utilization["reasons"])
    assert reply["summary"]["counts_coverage"] == {"scope": "local_ledger", "status": "INCOMPLETE"}
    assert reply["summary"]["operator_refusal_counts"] == {}
    assert ledger.reads == 1
    assert_no_sensitive_output(reply)


@pytest.mark.parametrize("ctx", [
    context(account="other-account"), context(domain="other-domain"),
    context("ungranted", "ungranted-actor"),
    context("strategy", "strategy-a", role=WireRole.STRATEGY_CLIENT, strategy="strategy-a", run="run-a"),
])
def test_unauthorized_admin_handle_never_reads_status(setup, ctx):
    service, ledger = setup
    reply = service.handle("risk.admin.status", context=ctx, arguments={})
    assert reply == {"status": "REQUEST_REJECTED", "code": "risk_unauthorized"}
    assert ledger.reads == 0
    assert_no_sensitive_output(reply)


def test_strategy_status_keeps_original_whitelist(setup):
    service, ledger = setup
    ctx = context("strategy", "strategy-a", role=WireRole.STRATEGY_CLIENT, strategy="strategy-a", run="run-a")
    reply = service.handle("risk.status", context=ctx, arguments={})
    assert reply == {"schema": RISK_SERVICE_SCHEMA, "schema_version": 1, "available": False,
                     "policy_version": None, "refusal_counts": {}}
    assert ledger.reads == 1
    assert_no_sensitive_output(reply)


def test_web_status_serializes_authorized_handle_without_sensitive_values(setup, monkeypatch):
    service, ledger = setup
    async def no_thread(function, *args):
        return function(*args)
    monkeypatch.setattr(web, "run_in_threadpool", no_thread)
    session = web.AuthenticatedRiskSession("operator", "operator-actor", "c" * 64,
                                           frozenset({"risk.admin.status"}))
    app = web.create_app(origin="http://127.0.0.1:8710", authenticate=lambda _: session,
                         admin_caller=lambda operation, args, timeout: service.handle(
                             operation, context=context(), arguments=args))
    path = web.API_PREFIX + "/status"
    route = next(route for route in app.routes if isinstance(route, APIRoute) and route.path == path)
    scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"", "headers": [],
             "server": ("127.0.0.1", 8710), "client": ("127.0.0.1", 1234), "root_path": "",
             "path_params": {}}
    response = asyncio.run(route.endpoint(Request(scope)))
    assert response.status_code == 200
    reply = json.loads(response.body)
    assert reply["summary"]["threshold_utilization"]["available"] is False
    assert reply["summary"]["counts_coverage"]["status"] == "INCOMPLETE"
    assert ledger.reads == 1
    assert_no_sensitive_output(reply)
