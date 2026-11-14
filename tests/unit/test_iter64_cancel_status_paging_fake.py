"""Pagination of synthetic cancel observations; no Gateway or account is created."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.risk_service_component import RiskOrderService
from bt_api_py.risk_service_config import RiskPrincipal
from bt_api_py.risk_service_gateway import GatewayControlAdapter

OP = "cancel-op"
STATES = ("QUEUED", "SENT", "NOT_SENT", "UNKNOWN", "TERMINAL")


def item(n, state="QUEUED", cancel=None):
    return {"reservation_id": f"res-{n:04d}", "strategy": "s", "request_key": f"r-{n}",
            "state": state, "cancel": cancel}


def page(targets, *, after="", limit=256, remaining=None, **extra):
    targets = list(targets)
    counts = dict.fromkeys(STATES, 0)
    counts["QUEUED"] = 260
    if remaining is None:
        remaining = len(targets) if after else 260
    return {"status": "OBSERVED", "operation_id": OP, "scope_kind": "account",
            "scope_value": "", "origin": "risk:a", "targets": targets, "counts": counts,
            "after": after, "limit": limit, "remaining_at_cursor": remaining, **extra}


class Runtime:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def status(self):
        binding = SimpleNamespace(active=True, mount="app", service_id="gateway", instance_id="gw",
            scope_ref="scope", operations=frozenset({"direct.control.cancel_scope_status"}))
        return SimpleNamespace(state="running", healthy=True, bindings=[binding])

    def call(self, mount, operation, arguments, **kwargs):
        self.calls.append((operation, arguments))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def adapter(*replies):
    runtime = Runtime(replies)
    return GatewayControlAdapter(runtime, mount="app", service_id="gateway", instance_id="gw",
        scope_ref="scope", account_ref="acct", timeout_seconds=1), runtime


def test_more_than_256_and_empty_tail_are_independent_read_only_calls():
    first = page(item(i) for i in range(256))
    last = page((item(i) for i in range(256, 260)), after="res-0255", limit=5)
    empty = page([], after="res-0259", limit=5)
    control, runtime = adapter(first, last, empty)
    assert control.cancel_scope_status(OP) == first
    assert control.cancel_scope_status(OP, after="res-0255", limit=5) == last
    assert control.cancel_scope_status(OP, after="res-0259", limit=5) == empty
    assert [x[1] for x in runtime.calls] == [
        {"operation_id": OP, "after": "", "limit": 256},
        {"operation_id": OP, "after": "res-0255", "limit": 5},
        {"operation_id": OP, "after": "res-0259", "limit": 5},
    ]
    assert {x[0] for x in runtime.calls} == {"direct.control.cancel_scope_status"}


@pytest.mark.parametrize("kwargs", [
    {"after": " x"}, {"after": "\n"}, {"after": 1}, {"limit": True},
    {"limit": 0}, {"limit": 257}, {"limit": "2"},
])
def test_invalid_paging_is_rejected_before_gateway(kwargs):
    control, runtime = adapter()
    control.last_cancel_status[OP] = {"old": True}
    assert control.cancel_scope_status(OP, **kwargs) is None
    assert runtime.calls == []
    assert OP not in control.last_cancel_status


@pytest.mark.parametrize("response", [
    page([item(3), item(2)], after="res-0002", limit=2),
    page([item(2), item(2)], after="res-0002", limit=2),
    page([item(1)], after="res-0002", limit=2),
    page([item(3), item(4), item(5)], after="res-0002", limit=2),
    page([item(3)], after="res-0002", limit=2, operation_id="other"),
    page([item(3)], after="res-0002", limit=2, scope_kind="account", scope_value="bad"),
    page([], after="res-0002", limit=2, remaining=1),
    page([item(3)], after="res-0002", limit=2, remaining=2),
    page([item(3)], after="different", limit=2),
    page([item(3)], after="res-0002", limit=1),
    page([item(3)], after="res-0002", limit=2, remaining=True),
    page([item(3)], after="res-0002", limit=2, remaining=261),
    {key: value for key, value in page([item(3)], after="res-0002", limit=2).items()
     if key != "remaining_at_cursor"},
    {"status": "NOT_FOUND", "operation_id": "other"},
    RuntimeError("private"),
])
def test_bad_page_does_not_survive_as_observation(response):
    control, _ = adapter(response)
    assert control.cancel_scope_status(OP, after="res-0002", limit=2) is None
    assert OP not in control.last_cancel_status


def test_counts_are_current_observations_not_a_cross_page_snapshot():
    first = page([item(0)], limit=1, counts={**dict.fromkeys(STATES, 0), "QUEUED": 260})
    second = page([item(1, "SENT", "SENT")], after="res-0000", limit=1,
                  counts={**dict.fromkeys(STATES, 0), "QUEUED": 259, "SENT": 1})
    control, _ = adapter(first, second)
    assert control.cancel_scope_status(OP, limit=1) == first
    assert control.cancel_scope_status(OP, after="res-0000", limit=1) == second


class Ledger:
    @contextmanager
    def transaction(self):
        yield SimpleNamespace(cancel_intent=lambda _: {
            "operation_id": OP, "state": "PENDING", "scope_kind": "account", "scope_value": ""})


def service_with(control):
    principal = RiskPrincipal("operator", "operator", "actor", frozenset(),
        frozenset({"risk.cancel.status"}), b"x" * 40)
    service = RiskOrderService(None, Ledger(), account_scope="acct", route="ctp", execution_domain_ref="domain",
        principals={"operator": principal}, instrument_for=lambda *_: None, ready=lambda: True,
        cancel_status=control.cancel_scope_status)
    ctx = SimpleNamespace(identity=WireIdentity(WireRole.APPLICATION_CLIENT, "operator", "actor"),
                          scope=WireScope("domain", account_ref="acct"), principal_ref="operator")
    return service, ctx


def test_service_forwards_page_and_rejects_mismatched_scope():
    good = page([item(3)], after="res-0002", limit=1)
    wrong = page([item(3)], after="res-0002", limit=1,
                 scope_kind="instrument", scope_value="SHFE:rb2701")
    control, runtime = adapter(good, wrong)
    service, context = service_with(control)
    args = {"operation_id": OP, "after": "res-0002", "limit": 1}
    assert service.handle("risk.cancel.status", context=context, arguments=args)["gateway"] == good
    assert service.handle("risk.cancel.status", context=context, arguments=args)["gateway"] is None
    assert runtime.calls[-1][1] == args
    assert service.handle("risk.cancel.status", context=context,
        arguments={"operation_id": OP, "limit": True}) == {"status": "REQUEST_REJECTED", "code": "risk_request_invalid"}
    assert len(runtime.calls) == 2


def test_web_through_service_to_gateway_fake():
    from fastapi.testclient import TestClient
    from bt_api_risk.web.app import API_PREFIX, AuthenticatedRiskSession, create_app

    expected = page([item(257), item(258)], after="res-0256", limit=2)
    control, runtime = adapter(expected)
    service, context = service_with(control)
    app = create_app(origin="http://127.0.0.1:8710",
        authenticate=lambda _: AuthenticatedRiskSession("operator", "actor", "c" * 64,
                                                       frozenset({"risk.cancel.status"})),
        admin_caller=lambda operation, arguments, timeout: service.handle(
            operation, context=context, arguments=arguments))
    client = TestClient(app, base_url="http://127.0.0.1:8710", client=("127.0.0.1", 50000))
    result = client.get(API_PREFIX + "/cancel/cancel-op?after=res-0256&limit=2")
    assert result.status_code == 200 and result.json()["gateway"] == expected
    assert runtime.calls == [("direct.control.cancel_scope_status",
                              {"operation_id": OP, "after": "res-0256", "limit": 2})]


@pytest.mark.parametrize("reply", [
    page([item(257)], after="res-0256", limit=2, scope_kind="instrument", scope_value="SHFE:rb2701"),
    page([item(258), item(257)], after="res-0256", limit=2), TimeoutError("private"),
])
def test_web_through_service_handles_invalid_or_unavailable_gateway_page(reply):
    from fastapi.testclient import TestClient
    from bt_api_risk.web.app import API_PREFIX, AuthenticatedRiskSession, create_app

    control, runtime = adapter(reply)
    service, context = service_with(control)
    app = create_app(origin="http://127.0.0.1:8710",
        authenticate=lambda _: AuthenticatedRiskSession("operator", "actor", "c" * 64,
                                                       frozenset({"risk.cancel.status"})),
        admin_caller=lambda operation, arguments, timeout: service.handle(
            operation, context=context, arguments=arguments))
    client = TestClient(app, base_url="http://127.0.0.1:8710", client=("127.0.0.1", 50000))
    result = client.get(API_PREFIX + "/cancel/cancel-op?after=res-0256&limit=2")
    assert result.status_code == 200 and result.json()["gateway"] is None
    assert [operation for operation, _ in runtime.calls] == ["direct.control.cancel_scope_status"]
