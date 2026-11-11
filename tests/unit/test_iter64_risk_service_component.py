"""Risk ServiceHost built-in ``("risk", "order_service")`` (64.09). UNIT/FAKE evidence.

A real ``PretradeEngine`` and ledger on a temporary SQLite file, with in-test
Gateway, control and facts ports; callers are authenticated Registry contexts.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.risk_service_protocol import RISK_ADMIN_OPERATIONS
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.risk_service_component import RiskOrderService, validate_service_options
from bt_api_py.risk_service_config import RiskPrincipal

ACCOUNT, DOMAIN, ROUTE, DAY = "account-a", "paper:demo", "CTP___FUTURE", "20300101"
RB = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")
ORDER_OPS = frozenset({"risk.order.reserve", "risk.order.dispatch", "risk.order.release",
                       "risk.order.status", "risk.status"})
PRINCIPALS = {
    "principal-a": RiskPrincipal("principal-a", "strategy", "strategy-a",
                                 frozenset({"strategy-a"}), ORDER_OPS, b"a" * 40),
    "principal-b": RiskPrincipal("principal-b", "strategy", "strategy-b",
                                 frozenset({"strategy-b"}), ORDER_OPS, b"b" * 40),
    "exec-1": RiskPrincipal("exec-1", "execution", "exec-actor",
                            frozenset({"strategy-a"}), ORDER_OPS, b"c" * 40),
    "operator-1": RiskPrincipal("operator-1", "operator", "ops", frozenset(),
                                frozenset(RISK_ADMIN_OPERATIONS), b"d" * 40),
}


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Gateway:
    def __init__(self):
        self.calls, self.commands = [], {}

    def reply(self, key, state, outcome="OBSERVED"):
        from bt_api_risk.pretrade import GatewayReply

        command = self.commands[key]
        return GatewayReply(outcome, state=state, reservation_id="res-" + key, version=1,
                            digest=command, owner_generation="g1", reservation_generation="g1")

    def reserve(self, **kw):
        self.calls.append(("reserve", kw))
        self.commands[kw["request_key"]] = sha(kw["canonical_payload"])
        return self.reply(kw["request_key"], "RESERVED")

    def dispatch(self, **kw):
        self.calls.append(("dispatch", kw))
        return self.reply(kw["request_key"], "DISPATCHING", outcome="QUEUED")

    def status(self, **kw):
        self.calls.append(("status", kw))
        return self.reply(kw["request_key"], "RESERVED")

    def release(self, **kw):
        self.calls.append(("release", kw))
        return self.reply(kw["request_key"], "RELEASED")

    def cancel(self, **kw):
        from bt_api_risk.pretrade import GatewayReply

        return GatewayReply("UNKNOWN")


class Control:
    def apply(self, **kw):
        from bt_api_risk.pretrade import ControlReply

        return ControlReply("OBSERVED", cause_id=kw["cause_id"], active=True)

    status = apply

    def cancel_scope(self, **kw):
        from bt_api_risk.pretrade import ControlReply

        return ControlReply("OBSERVED")


class Facts:
    def reference_price(self, _i):
        return None

    def market_ref(self, _i):
        return None

    def instrument_facts(self, _i):
        return None

    def account_snapshot(self):
        return None

    def strategy_financial(self, _s):
        return None

    def margin_rate(self, _i):
        return None

    def trading_day(self):
        return DAY


@pytest.fixture
def service(tmp_path):
    from bt_api_risk.core.admission import AccountScope
    from bt_api_risk.pretrade import (
        CallerIdentity,
        PolicyStore,
        PretradeEngine,
        PretradeLedger,
        RuleRegistry,
    )
    from bt_api_risk.risk_order_journal import RiskOrderJournal

    stack = ExitStack()
    journal = RiskOrderJournal(tmp_path / "risk.sqlite", writer_id="unit")
    fence = stack.enter_context(journal.writer_fence(AccountScope("ctp", ACCOUNT, "simnow")))
    ledger = PretradeLedger(journal, fence)
    gateway = Gateway()
    clock = [1_900_000_000_000_000_000]
    engine = PretradeEngine(
        ledger, PolicyStore(ledger), RuleRegistry(), Facts(), gateway, Control(),
        account_scope=ACCOUNT, route=ROUTE, now_ns=lambda: clock[0],
        reservation_ttl_seconds=10.0, actor_principal="risk-principal",
    )
    operator = CallerIdentity("operator-1", "ops", "operator", frozenset(), None,
                              frozenset({"risk.policy.publish"}))
    engine.publish_policy(operator, {
        "schema": "bt_api_risk.policy.v1", "policy_id": "unit",
        "account": {"route": ROUTE, "account_scope": ACCOUNT},
        "per_order": {"max_quantity": {"strategy-a": {"rb": "2"}, "strategy-b": {"rb": "2"}}},
        "self_trade": {"enabled": False, "on_unverifiable": "deny"},
    }, 0, "unit bootstrap")
    runs = []
    svc = RiskOrderService(
        engine, ledger, account_scope=ACCOUNT, route=ROUTE, execution_domain_ref=DOMAIN,
        principals=PRINCIPALS,
        instrument_for=lambda venue, native: RB if (venue, native) == ("SHFE", "rb") else None,
        ready=lambda: True, note_run=lambda strategy, run: runs.append((strategy, run)),
        cancel_status=lambda op: {"status": "OBSERVED", "operation_id": op, "counts": {"SENT": 1}},
    )
    svc.gateway, svc.runs = gateway, runs
    yield svc
    stack.close()


def ctx(principal, *, role=WireRole.STRATEGY_CLIENT, actor=None, strategy="strategy-a",
        run="run-1", account=ACCOUNT, domain=DOMAIN):
    scope = WireScope(domain, account_ref=account, strategy_ref=strategy,
                      run_id=run if strategy is not None else None)
    identity = WireIdentity(role, principal, actor or PRINCIPALS[principal].actor_ref)
    return SimpleNamespace(identity=identity, scope=scope, principal_ref=principal,
                           registration_id="reg-1")


def operator_ctx():
    return ctx("operator-1", role=WireRole.APPLICATION_CLIENT, strategy=None)


def payload(key="k1", quantity="1", **changes):
    values = {"symbol": "rb", "side": Side.BUY, "order_type": OrderType.LIMIT,
              "quantity": Decimal(quantity), "price": Decimal("10"), "account_id": ACCOUNT,
              "client_order_id": "c-" + key, "idempotency_key": key, "offset": "open",
              "quantity_unit": "lots", "exchange_id": "SHFE"}
    values.update(changes)
    return json.loads(canonical_gateway_payload(OrderRequest(**values).to_dict()))


def reserve(svc, context, key="k1", **kw):
    return svc.handle("risk.order.reserve", context=context, arguments={
        "request_key": key, "payload": payload(key, **kw), "parent_ref": None, "child_id": None})


def test_options_validator_binds_exactly_the_two_ports():
    assert dict(validate_service_options({"role": "order_service"},
                                         ["service.risk_order", "service.risk_admin"])) == {
        "role": "order_service"}
    for options, ports in (({"role": "order_service", "x": 1}, ["service.risk_order", "service.risk_admin"]),
                           ({"role": "order_service"}, ["service.risk_order"])):
        with pytest.raises(ValueError):
            validate_service_options(options, ports)


def test_reserve_dispatch_with_the_authenticated_strategy_and_run(service):
    reply = reserve(service, ctx("principal-a"))
    assert reply["outcome"] == "RESERVED", reply
    assert service.runs == [("strategy-a", "run-1")]
    call = service.gateway.calls[0][1]
    assert call["strategy_ref"] == "strategy-a" and call["run_id"] == "run-1"
    # The canonical payload is forwarded unchanged.
    assert call["canonical_payload"] == canonical_gateway_payload(payload("k1"))
    dispatched = service.handle("risk.order.dispatch", context=ctx("principal-a"), arguments={
        "request_key": "k1", "permit_id": reply["permit_id"],
        "reservation_id": reply["reservation_id"], "view_digest": reply["view_digest"]})
    assert dispatched["outcome"] == "DISPATCHED", dispatched
    status = service.handle("risk.order.status", context=ctx("principal-a"),
                            arguments={"request_key": "k1"})
    assert status["state"] == "DISPATCHED"


def test_view_effect_comes_from_the_explicit_offset_never_from_the_caller(service):
    view, canonical, code = service.build_view(
        SimpleNamespace(strategy_refs=frozenset({"strategy-a"}), role="strategy"), "k9",
        payload("k9", offset="close_today", side=Side.SELL), None, None)
    assert code is None and view.offset == "CLOSE_TODAY" and view.side == "SELL"
    assert view.strategy_ref == "strategy-a" and view.origin == "strategy"
    assert view.instrument == RB and view.limit_price == Decimal("10")
    market, _, code = service.build_view(
        SimpleNamespace(strategy_refs=frozenset({"strategy-a"}), role="strategy"), "k8",
        payload("k8", order_type=OrderType.MARKET, price=None), None, None)
    assert code is None and market.order_type == "MARKET" and market.limit_price is None


@pytest.mark.parametrize("change, code", [
    ({"exchange_id": "DCE"}, "risk_limit_unconfigured"),
    ({"idempotency_key": "other"}, "risk_request_invalid"),
    ({"account_id": "account-b"}, "risk_request_invalid"),
])
def test_payload_refusals_never_reach_the_gateway(service, change, code):
    raw = payload("k1", **change)
    reply = service.handle("risk.order.reserve", context=ctx("principal-a"), arguments={
        "request_key": "k1", "payload": raw, "parent_ref": None, "child_id": None})
    assert reply["outcome"] == "DENIED" and reply["code"] == code
    assert service.gateway.calls == []


def test_r01_refusal_is_counted_for_the_callers_strategy_only(service):
    reply = reserve(service, ctx("principal-a"), "big", quantity="5")
    assert reply["code"] == "risk_order_quantity_exceeds_cap" and service.gateway.calls == []
    mine = service.handle("risk.status", context=ctx("principal-a"), arguments={})
    other = service.handle("risk.status", context=ctx("principal-b", strategy="strategy-b"),
                           arguments={})
    assert mine == {"schema": "bt_api.risk_service.v1", "schema_version": 1, "available": True,
                    "policy_version": "1",
                    "refusal_counts": {"risk_order_quantity_exceeds_cap": 1}}
    assert other["refusal_counts"] == {}


def test_a_strategy_cannot_act_for_another_strategy(service):
    # principal-a registered with strategy-b's scope: the Registry would refuse, and so does Risk.
    reply = reserve(service, ctx("principal-a", strategy="strategy-b"))
    assert reply["outcome"] == "DENIED" and reply["code"] == "risk_unauthorized"
    # A strategy principal never holds a SERVICE identity, and the actor must match.
    assert reserve(service, ctx("principal-a", role=WireRole.SERVICE))["code"] == "risk_unauthorized"
    assert reserve(service, ctx("principal-a", actor="strategy-b"))["code"] == "risk_unauthorized"
    assert reserve(service, ctx("principal-a", account="account-b"))["code"] == "risk_unauthorized"
    assert reserve(service, ctx("principal-a", run=None, strategy=None))["code"] == "risk_unauthorized"
    assert reserve(service, ctx("unknown", actor="strategy-a"))["code"] == "risk_unauthorized"
    assert service.gateway.calls == []
    # A permit of strategy-a is invisible to strategy-b under the same request key.
    first = reserve(service, ctx("principal-a"))
    other = service.handle("risk.order.status", context=ctx("principal-b", strategy="strategy-b"),
                           arguments={"request_key": "k1"})
    assert first["outcome"] == "RESERVED" and other["outcome"] == "NOT_FOUND"
    release = service.handle("risk.order.release", context=ctx("principal-b", strategy="strategy-b"),
                             arguments={"request_key": "k1", "permit_id": first["permit_id"]})
    assert release["outcome"] == "DENIED" and release["code"] == "risk_unauthorized"


def test_execution_principal_maps_to_the_execution_origin(service):
    caller, code = service.caller(ctx("exec-1", role=WireRole.SERVICE), "risk.order.reserve")
    assert code is None and caller.role == "execution" and caller.strategy_refs == {"strategy-a"}
    caller, code = service.caller(ctx("exec-1", role=WireRole.SERVICE, strategy="strategy-b"),
                                  "risk.order.reserve")
    assert caller is None and code == "risk_unauthorized"


def test_admin_operations_are_operator_only(service):
    assert service.handle("risk.admin.status", context=ctx("principal-a"), arguments={}) == {
        "status": "REQUEST_REJECTED", "code": "risk_unauthorized"}
    # An operator principal using a strategy-client identity is refused.
    wrong = ctx("operator-1", role=WireRole.STRATEGY_CLIENT, strategy="strategy-a")
    assert service.handle("risk.admin.status", context=wrong, arguments={})["code"] == "risk_unauthorized"
    # The same account-only scope under a non-application identity is refused too.
    for role in (WireRole.STRATEGY_CLIENT, WireRole.SERVICE):
        account_only = ctx("operator-1", role=role, strategy=None)
        assert service.handle("risk.admin.status", context=account_only, arguments={}) == {
            "status": "REQUEST_REJECTED", "code": "risk_unauthorized"}
    # An operator cannot place orders.
    reply = service.handle("risk.order.reserve", context=operator_ctx(), arguments={
        "request_key": "k1", "payload": payload("k1"), "parent_ref": None, "child_id": None})
    assert reply["code"] == "risk_unauthorized" and service.gateway.calls == []
    status = service.handle("risk.admin.status", context=operator_ctx(), arguments={})
    assert status["status"] == "OBSERVED" and status["summary"]["policy_version"] == 1


def test_admin_shapes(service):
    op = operator_ctx()
    assert service.handle("risk.admin.status", context=op, arguments={"x": 1}) == {
        "status": "REQUEST_REJECTED", "code": "risk_request_invalid"}
    read = service.handle("risk.policy.read", context=op, arguments={})
    assert read["status"] == "OBSERVED" and read["version"] == 1 and read["policy_id"] == "unit"
    assert read["document"]["per_order"]["max_quantity"]["strategy-a"] == {"rb": "2"}
    assert read["exchange_rules_digest"] is None
    tightened = dict(read["document"], per_order={"max_quantity": {
        "strategy-a": {"rb": "1"}, "strategy-b": {"rb": "2"}}})
    published = service.handle("risk.policy.publish", context=op, arguments={
        "expected_version": 1, "document": tightened, "reason": "", "exchange_rules_text": None})
    assert published["status"] == "OBSERVED" and published["version"] == 2, published
    assert published["classification"]["tightened"] and not published["classification"]["loosened"]
    stale = service.handle("risk.policy.publish", context=op, arguments={
        "expected_version": 1, "document": tightened, "reason": "x", "exchange_rules_text": None})
    assert stale == {"status": "REQUEST_REJECTED", "code": "policy_version_conflict"}
    assert service.handle("risk.latch.list", context=op, arguments={"active_only": True}) == {
        "status": "OBSERVED", "latches": []}
    assert service.handle("risk.unknown.list", context=op, arguments={}) == {
        "status": "OBSERVED", "permits": []}
    audit = service.handle("risk.audit.read", context=op, arguments={"after": 0, "limit": 500})
    assert audit["status"] == "OBSERVED" and audit["next_after"] == audit["records"][-1]["seq"]
    assert service.handle("risk.audit.read", context=op, arguments={"after": 0, "limit": 501})[
        "code"] == "risk_request_invalid"
    exported = service.handle("risk.counts.export", context=op,
                              arguments={"trading_day": DAY, "format": "csv"})
    assert exported["status"] == "OBSERVED" and exported["content"].startswith("trading_day,")
    requested = service.handle("risk.cancel.request", context=op, arguments={
        "operation_id": "op-1", "scope_kind": "instrument", "scope_value": "SHFE:rb"})
    assert requested == {"status": "OBSERVED", "operation_id": "op-1", "state": "ACCEPTED"}
    assert service.handle("risk.cancel.request", context=op, arguments={
        "operation_id": "op-2", "scope_kind": "strategy", "scope_value": "strategy-a"})[
        "code"] == "risk_request_invalid"
    status = service.handle("risk.cancel.status", context=op, arguments={"operation_id": "op-1"})
    assert status["state"] == "ACCEPTED" and status["gateway"]["counts"] == {"SENT": 1}
    assert service.handle("risk.cancel.status", context=op, arguments={"operation_id": "none"}) == {
        "status": "NOT_FOUND", "operation_id": "none"}


def test_latch_and_unknown_rows_use_the_frozen_wire_projection(service):
    from bt_api_py.risk_service_component import _latch, _unknown, iso_utc

    assert iso_utc(1_762_905_600_123_456_789) == "2025-11-12T00:00:00.123456Z"
    assert iso_utc(None) is None
    latch = _latch({"latch_id": "l1", "control": "counters", "scope_kind": "instrument",
                    "scope_value": "rb", "code": "risk_order_count_limit", "reason": "R-04",
                    "trading_day": DAY, "row_version": 2, "active": 0, "created_ns": 1,
                    "released_by": "operator-1", "released_ns": 1_762_905_600_000_000_000,
                    "release_reason": "checked"})
    assert latch == {"latch_id": "l1", "control": "counters", "scope_kind": "instrument",
                     "scope_value": "rb", "reason": "R-04", "trading_day": DAY, "version": 2,
                     "active": False, "released_by": "operator-1",
                     "released_at": "2025-11-12T00:00:00.000000Z"}
    permit = _unknown({"permit_id": "p", "strategy_ref": "strategy-a", "request_key": "k",
                       "instrument_id": "rb", "side": "BUY", "offset": "OPEN",
                       "quantity": Decimal("1"), "occupancy": Decimal("1"), "reservation_id": None,
                       "gateway_state": None, "gateway_code": None, "trading_day": DAY,
                       "created_ns": 1_762_905_600_000_000_000, "updated_ns": 5})
    assert permit == {"request_key": "k", "strategy_ref": "strategy-a", "permit_id": "p",
                      "reservation_id": None, "state": "UNKNOWN", "instrument": "rb",
                      "side": "BUY", "offset": "OPEN", "quantity": "1",
                      "since": "2025-11-12T00:00:00.000000Z"}


def test_unknown_or_extra_order_arguments(service):
    reply = service.handle("risk.order.status", context=ctx("principal-a"),
                           arguments={"request_key": "k1", "strategy_ref": "strategy-b"})
    assert reply["outcome"] == "DENIED" and reply["code"] == "risk_request_invalid"
    assert service.handle("risk.order.status", context=ctx("principal-a"), arguments={}) == {
        "status": "REQUEST_REJECTED", "code": "risk_request_invalid"}
    assert service.handle("risk.nothing", context=ctx("principal-a"), arguments={})[
        "code"] == "risk_request_invalid"


def test_builtin_is_registered_and_requires_the_typed_service(tmp_path):
    from bt_api_py.risk_service_host import service_config_document
    from bt_api_py.service_components import (
        ComponentResources,
        ServiceComponentError,
        build_component,
        component_specs,
        validate_components,
    )
    from bt_api_py.service_config import parse_service_config

    config = SimpleNamespace(service_id="risk-service", scope_ref="risk-scope",
                             instance_id="risk-a", rpc_bind="tcp://127.0.0.1:7999")
    parsed = parse_service_config(service_config_document(config))
    (prepared,) = validate_components(component_specs(parsed.components))
    assert prepared.write_operations_by_port["service.risk_order"] == {
        "risk.order.reserve", "risk.order.dispatch", "risk.order.release"}
    assert prepared.write_operations_by_port.get("service.risk_admin", frozenset()) == frozenset()
    with pytest.raises(ServiceComponentError) as raised:
        build_component(prepared, scope=WireScope(DOMAIN),
                        resource_provider=lambda *_: ComponentResources.borrowed(
                            {"service": object()}, ready=lambda: True))
    assert raised.value.code == "RISK_ORDER_SERVICE_INVALID"


def test_built_component_passes_the_authenticated_context(service):
    from bt_api_py.risk_service_host import service_config_document
    from bt_api_py.service_components import (
        ComponentResources,
        build_component,
        component_specs,
        validate_components,
    )
    from bt_api_py.service_config import parse_service_config

    config = SimpleNamespace(service_id="risk-service", scope_ref="risk-scope",
                             instance_id="risk-a", rpc_bind="tcp://127.0.0.1:7999")
    (prepared,) = validate_components(component_specs(parse_service_config(
        service_config_document(config)).components))
    component = build_component(prepared, scope=WireScope(DOMAIN),
                                resource_provider=lambda *_: ComponentResources.borrowed(
                                    {"service": service}, ready=lambda: True))
    reply = component.handlers["risk.status"](ctx("principal-a"), {})
    assert reply["available"] is True and reply["policy_version"] == "1"
