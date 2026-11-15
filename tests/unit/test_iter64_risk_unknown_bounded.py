"""Whole-result unknown-list bounds with synthetic identity and temporary storage."""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_risk.pretrade import RiskAdminError
from bt_api_risk.web.app import AuthenticatedRiskSession, create_app
from bt_api_transport_zmq.service_v2 import service_reply_fits
from fastapi.testclient import TestClient

import bt_api_py.risk_service_component as component
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.unit.test_iter64_risk_audit_contract import ORIGIN, SERVICE_ID, live_adapter
from tests.unit.test_iter64_risk_service_component import ACCOUNT, DOMAIN, ctx, operator_ctx, service
from tests.unit.test_service_registry import _next_request, _register


def row(key="key", quantity=Decimal("1.25")):
    return {"request_key": key, "strategy_ref": "strategy-a", "permit_id": "permit-" + key,
            "reservation_id": None, "instrument_id": "rb", "side": "BUY", "offset": "OPEN",
            "quantity": quantity, "created_ns": 1_762_905_600_000_000_000}


def call(service, checker=lambda result: True, caller=None):
    context = operator_ctx() if caller is None else caller
    if checker is not None:
        context.result_fits = checker
    return service.handle("risk.unknown.list", context=context, arguments={})


def fake(service, rows):
    service._engine = SimpleNamespace(list_unknown_bounded=lambda: rows)


def reject(code):
    return {"status": "REQUEST_REJECTED", "code": code}


def test_small_result_preserves_v1_fields_and_order(service):
    fake(service, [row("first"), row("second", Decimal("0.0100"))])
    result = call(service)
    assert result == {"status": "OBSERVED", "permits": [
        {"request_key": "first", "strategy_ref": "strategy-a", "permit_id": "permit-first",
         "reservation_id": None, "state": "UNKNOWN", "instrument": "rb", "side": "BUY",
         "offset": "OPEN", "quantity": "1.25", "since": "2025-11-12T00:00:00.000000Z"},
        {"request_key": "second", "strategy_ref": "strategy-a", "permit_id": "permit-second",
         "reservation_id": None, "state": "UNKNOWN", "instrument": "rb", "side": "BUY",
         "offset": "OPEN", "quantity": "0.0100", "since": "2025-11-12T00:00:00.000000Z"}]}
    assert list(result["permits"][0]) == ["request_key", "strategy_ref", "permit_id", "reservation_id",
                                           "state", "instrument", "side", "offset", "quantity", "since"]


def test_authorization_and_missing_capabilities_do_not_read(service):
    calls = []
    service._engine = SimpleNamespace(list_unknown=lambda: calls.append("legacy"),
                                      list_unknown_bounded=lambda: calls.append("bounded"))
    assert call(service, caller=ctx("principal-a"))["code"] == "risk_unauthorized"
    assert call(service, checker=None) == reject("risk_unknown_list_unavailable")
    service._engine = SimpleNamespace(list_unknown=lambda: calls.append("legacy"))
    assert call(service) == reject("risk_unknown_list_unavailable")
    assert calls == []


@pytest.mark.parametrize("quantity,code", [
    (Decimal("1E+999999"), "risk_unknown_list_too_large"),
    (Decimal("0E-999999"), "risk_unknown_list_too_large"),
    (Decimal("NaN"), "risk_unknown_list_corrupt"),
    (Decimal("Infinity"), "risk_unknown_list_corrupt"),
    (Decimal("-2"), "risk_unknown_list_corrupt"),
    (Decimal("0"), "risk_unknown_list_corrupt"),
    ([Decimal("0E-999999")], "risk_unknown_list_corrupt"),
])
def test_huge_or_invalid_quantity_refused_without_details(service, quantity, code):
    fake(service, [row("secret-test-key", quantity)])
    assert call(service) == reject(code)


def test_nonpositive_zero_and_other_decimal_semantics(service):
    fake(service, [row("a", Decimal("0E+999999"))])
    assert call(service) == reject("risk_unknown_list_corrupt")
    assert component.json_safe(Decimal("0.0100")) == "0.0100"


@pytest.mark.parametrize("key", ["", " whitespace", "a" * 257])
def test_malformed_request_key_refuses_all_rows(service, key):
    fake(service, [row("valid"), row(key)])
    assert call(service) == reject("risk_unknown_list_corrupt")


@pytest.mark.parametrize("duplicate", ["permit_id", "request"])
def test_duplicate_ledger_identity_refuses_complete_result(service, duplicate):
    first, second = row("a"), row("b")
    if duplicate == "permit_id":
        second["permit_id"] = first["permit_id"]
    else:
        second["request_key"] = first["request_key"]
    fake(service, [first, second])
    assert call(service) == reject("risk_unknown_list_corrupt")


def test_request_keys_can_repeat_across_strategies(service):
    first, second = row("same"), row("same")
    second.update(strategy_ref="strategy-b", permit_id="another-permit")
    fake(service, [first, second])
    assert len(call(service)["permits"]) == 2


@pytest.mark.parametrize("field,value", [
    ("strategy_ref", ""), ("permit_id", ""), ("instrument_id", ""),
    ("side", "SELL?!"), ("offset", "anything"),
    ("created_ns", -1), ("created_ns", None), ("created_ns", True),
    ("created_ns", 10**40),
])
def test_malformed_public_fields_refuse_complete_result(service, field, value):
    invalid = row("private-test-key")
    invalid[field] = value
    fake(service, [row("valid"), invalid])
    assert call(service) == reject("risk_unknown_list_corrupt")


@pytest.mark.parametrize("side,offset", [
    ("BUY", "OPEN"), ("SELL", "CLOSE"), ("BUY", "CLOSE_TODAY"),
    ("SELL", "CLOSE_YESTERDAY"),
])
def test_valid_side_offset_and_epoch_timestamp_keep_wire_shape(service, side, offset):
    valid = row()
    valid.update(side=side, offset=offset, created_ns=0)
    fake(service, [valid])
    permit = call(service)["permits"][0]
    assert (permit["side"], permit["offset"], permit["since"]) == (
        side, offset, "1970-01-01T00:00:00.000000Z")


@pytest.mark.parametrize("mode", ["raise", "nonbool", "mutate", "nondeterministic"])
def test_checker_fails_closed_without_details(service, mode):
    fake(service, [row("secret-test-key")])
    counter = [0]
    def checker(reply):
        counter[0] += 1
        if mode == "raise":
            raise RuntimeError("secret-test-key")
        if mode == "nonbool":
            return 1
        if mode == "mutate":
            reply["permits"].clear()
            return True
        return counter[0] == 1
    assert call(service, checker) == reject("risk_unknown_list_unavailable")


def test_full_result_refuses_instead_of_truncating(service, monkeypatch):
    fake(service, [row("a"), row("b")])
    assert call(service, lambda reply: len(reply["permits"]) <= 1) == reject("risk_unknown_list_too_large")
    monkeypatch.setattr(component, "_AUDIT_REPLY_NODE_LIMIT", 15)
    assert call(service) == reject("risk_unknown_list_too_large")
    monkeypatch.setattr(component, "_AUDIT_REPLY_NODE_LIMIT", 200_000)
    assert len(call(service)["permits"]) == 2


def test_web_node_budget_is_the_exact_shared_limit():
    assert component._audit_nodes_fit({"items": [None] * 199_998})
    assert not component._audit_nodes_fit({"items": [None] * 199_999})


def test_temporary_sqlite_bounded_reader_and_legacy_shape(service):
    from tests.unit.test_iter64_risk_arithmetic_observer_ack import _seed_historical_permit

    _seed_historical_permit(service)
    result = call(service)
    assert result == {"status": "OBSERVED", "permits": [{
        "request_key": "held", "strategy_ref": "strategy-a", "permit_id": "held",
        "reservation_id": None, "state": "UNKNOWN", "instrument": "rb", "side": "BUY",
        "offset": "OPEN", "quantity": "1", "since": "2030-03-17T17:46:40.000000Z"}]}


@pytest.mark.parametrize("code", ["risk_unknown_list_too_large", "risk_unknown_list_corrupt",
                                  "risk_unknown_list_unavailable", "untrusted-secret"])
def test_bounded_reader_errors_are_finite_and_do_not_leak(service, code):
    def fail():
        raise RiskAdminError(code)
    service._engine = SimpleNamespace(list_unknown_bounded=fail)
    expected = code if code.startswith("risk_unknown_list_") else "risk_unknown_list_unavailable"
    assert call(service) == reject(expected)


def test_real_registry_and_web_small_frame_are_complete_or_refused(live_adapter, tmp_path, monkeypatch):
    service, run = live_adapter
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, "operator-1", "ops")
    scope = WireScope(DOMAIN, account_ref=ACCOUNT)
    operations = frozenset({"risk.unknown.list"})
    server_limit = [8192]

    def make_registry(with_checker=True):
        def checker(request, payload, hint):
            limit = server_limit[0] if hint is None else min(server_limit[0], hint)
            return service_reply_fits(SERVICE_ID, request, payload, max_message_bytes=limit)
        registry = ServiceRegistry(
            tmp_path / ("registry.sqlite" if with_checker else "bare.sqlite"), service_id="risk-service",
            capabilities=(ServiceCapability("risk-instance", "service.risk_admin", operations),),
            grants={"operator-1": (PrincipalGrant(identity, (ResourceGrant(
                "risk-instance", "service.risk_admin", "risk-scope", scope, operations),)),)},
            policy_version="unit-policy", requirements_version="unit-requirements",
            reply_size_checker=checker if with_checker else None)
        registry.register_handler("risk-instance", "risk.unknown.list", lambda context, arguments:
                                  service.handle("risk.unknown.list", context=context, arguments=arguments))
        registered = _register(registry, identity=identity, scope=scope, principal="operator-1",
                               instance_id="risk-instance", port="service.risk_admin",
                               scope_ref="risk-scope", operations=("risk.unknown.list",))
        assert registered["ok"]
        return registry, registered["result"]

    def request(registry, registration, hint=None):
        payload = {"registration_id": registration["registration_id"],
                   "service_epoch": registration["service_epoch"], "instance_id": "risk-instance",
                   "arguments": {}}
        if hint is not None:
            payload["max_reply_message_bytes"] = hint
        envelope = _next_request("risk.unknown.list", payload, identity=identity, scope=scope)
        return envelope, dict(registry.handle("operator-1", envelope))

    first, second = row("a"), row("b")
    first["instrument_id"], second["instrument_id"] = "a" * 200, "b" * 200
    first["strategy_ref"], second["strategy_ref"] = "c" * 200, "d" * 200
    first["permit_id"], second["permit_id"] = "e" * 200, "f" * 200
    run(fake, service, [first, second])
    registry, registration = run(make_registry)
    try:
        envelope, small = run(request, registry, registration, 8192)
        assert small["ok"] and len(small["result"]["permits"]) == 2
        assert service_reply_fits(SERVICE_ID, envelope, small, max_message_bytes=8192)
        envelope, refused = run(request, registry, registration, 1024)
        assert refused["result"] == reject("risk_unknown_list_too_large")
        assert service_reply_fits(SERVICE_ID, envelope, refused, max_message_bytes=1024)
        # The field names and per-value framing add to the raw text budget.
        large = [row("large-" + str(index)) for index in range(6000)]
        for item in large:
            item["instrument_id"] = "x" * 70
        run(fake, service, large)
        server_limit[0] = 1024 * 1024
        _, oversized = run(request, registry, registration)
        assert oversized["result"] == reject("risk_unknown_list_too_large"), oversized
        run(fake, service, [first, second])
        server_limit[0] = 8192
        session = AuthenticatedRiskSession("operator-1", "ops", "x" * 64, operations)
        def admin(operation, arguments, timeout_seconds):
            assert operation == "risk.unknown.list" and arguments == {} and timeout_seconds > 0
            return run(request, registry, registration, server_limit[0])[1]["result"]
        app = create_app(origin=ORIGIN, authenticate=lambda request: session, admin_caller=admin)
        with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50000)) as client:
            assert len(client.get("/api/v1/risk/unknown").json()["permits"]) == 2
            server_limit[0] = 1024
            refused = client.get("/api/v1/risk/unknown")
            assert refused.status_code == 409 and refused.json() == reject("risk_unknown_list_too_large")
            server_limit[0] = 8192
            monkeypatch.setattr(component, "_AUDIT_REPLY_NODE_LIMIT", 15)
            refused = client.get("/api/v1/risk/unknown")
            assert refused.status_code == 409 and refused.json() == reject("risk_unknown_list_too_large")
    finally:
        run(registry.close)
    bare, registration = run(make_registry, False)
    try:
        _, hinted = run(request, bare, registration, 4096)
        assert hinted["ok"] is False and hinted["code"] == "CAPABILITY_UNAVAILABLE"
        envelope, unchecked = run(request, bare, registration)
        assert unchecked["ok"] and not service_reply_fits(SERVICE_ID, envelope, unchecked, max_message_bytes=128)
    finally:
        run(bare.close)
