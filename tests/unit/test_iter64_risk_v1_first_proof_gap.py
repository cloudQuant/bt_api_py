"""V1 observations cannot establish first creation across restored ledgers.

Local fake transport only; no account or live Gateway is contacted.
"""

import pytest

from bt_api_py.gateway_api import GatewayBtApi
from tests.unit.test_iter63_gateway_api import order
from tests.unit.test_iter64_risk_service_client import answer, delegated_observation, facade
from tests.unit.test_iter64_risk_service_component import service as _service_fixture
from tests.unit.test_iter64_risk_service_component import V2_POLICY_BLOCKED

ROUTE = "CTP___FUTURE"
KEY = "same-key"


@pytest.fixture(name="service")
def _proof_service(tmp_path):
    yield from _service_fixture.__wrapped__(tmp_path)


def _matching_facade(service, monkeypatch):
    """A fake client for the temporary service's account and strategy scope."""
    from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

    from bt_api_py.gateway_direct_client import GatewayClientRoute
    from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
    from bt_api_py.risk_service_client import RiskClientRoute, RiskServiceClient
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
    from tests.unit.test_iter63_gateway_strategy_client import config_data, parts
    from tests.unit.test_iter64_risk_service_client import Host, Wire
    from tests.unit.test_iter64_risk_service_component import ACCOUNT, DOMAIN, ctx

    data, config = parts(), config_data()
    config["registration"]["strategy_ref"] = "strategy-a"
    config["clients"]["gateway"]["settings"]["bindings"][0]["account_ref"] = ACCOUNT
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-a", "strategy-a")
    scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref="strategy-a", run_id="run-1")
    data.update(config=parse_client_config(config),
                binding=GatewayCommandBinding("journal", ACCOUNT, "strategy-a", "principal-a"),
                route=GatewayClientRoute("broker.direct_write", "gateway", "instance", "scope",
                                         scope, identity), host=Host(), cancel_host=Host())
    session = GatewayStrategyClientSession(**data)
    route = RiskClientRoute("broker.risk_order", "risk", "risk-a", "risk-scope", scope, identity)
    client = RiskServiceClient(ThreadedServiceClientRuntime(lambda: None, require_unregister_ack=True),
                               route=route)
    wire = Wire(client, monkeypatch)

    def call(mount, operation, arguments, **kwargs):
        wire.calls.append((mount, operation, arguments, kwargs))
        return service.handle(operation, context=ctx("principal-a"), arguments=arguments)

    monkeypatch.setattr(client._runtime, "call", call)
    api = GatewayBtApi(session, exchange_name=ROUTE, risk_client=client)
    direct_gateway = []

    def forbid_direct(*args, **kwargs):
        direct_gateway.append((args, kwargs))
        raise AssertionError("selected Risk must not fall back to direct Gateway writes")

    for method in ("reserve", "dispatch", "release"):
        monkeypatch.setattr(api._command, method, forbid_direct)
    return api, data["host"], wire, direct_gateway


@pytest.mark.parametrize("reply", [
    answer("RESERVED"),
    answer("DENIED", state="DENIED", code="risk_order_quantity_exceeds_cap"),
])
def test_v1_reserve_is_not_sent_without_first_proof(monkeypatch, reply):
    api, _, wire, gateway_calls = facade(monkeypatch, replies=[reply])
    result = api.reserve_order(ROUTE, order())
    assert result.outcome == "UNKNOWN" and result.record is None
    assert result.risk_denied is None
    assert result.risk_answer is None
    assert KEY not in api._risk_permits
    assert api.dispatch_order(ROUTE, KEY).outcome == "UNKNOWN"
    assert api.release_order(ROUTE, KEY, operation_id="release-1").outcome == "UNKNOWN"
    assert wire.calls == []
    assert gateway_calls == []


def test_restarted_facade_does_not_turn_status_into_write_authority(monkeypatch):
    """A fake status read is an observation, not a restored write permit."""
    api, _, wire, gateway_calls = facade(monkeypatch, replies=[answer("RESERVED")])
    assert api.reserve_order(ROUTE, order()).outcome == "UNKNOWN"
    restarted = GatewayBtApi(api._session, exchange_name=ROUTE, risk_client=api._risk)
    status = restarted.risk_order_status(ROUTE, KEY)
    assert status.outcome == "ANSWERED" and status.answer.outcome == "RESERVED"
    assert KEY in restarted._risk_keys and KEY not in restarted._risk_permits
    assert restarted.dispatch_order(ROUTE, KEY).outcome == "UNKNOWN"
    assert restarted.release_order(ROUTE, KEY, operation_id="release-1").outcome == "UNKNOWN"
    assert [call[1] for call in wire.calls] == ["risk.order.status"]
    assert gateway_calls == []


def test_known_delegated_observation_can_still_be_cancelled(monkeypatch):
    from bt_api_py.gateway_strategy_delegated import GatewayDelegatedCommandResult

    api, _, wire, gateway_calls = facade(monkeypatch)
    assert api.reserve_order(ROUTE, order()).outcome == "UNKNOWN"
    observation = delegated_observation("SENT")
    calls = []

    def status(key, **_kwargs):
        api._delegated._observations[key] = observation
        return GatewayDelegatedCommandResult("OBSERVED", observation)

    monkeypatch.setattr(api._delegated, "status", status)
    monkeypatch.setattr(api._delegated, "cancel", lambda obs, **kw: calls.append((obs, kw)) or "cancelled")
    assert api.cancel_order(ROUTE, KEY, operation_id="cancel-1") == "cancelled"
    assert len(calls) == 1 and calls[0][0] is observation
    assert wire.calls == [] and gateway_calls == []


@V2_POLICY_BLOCKED
@pytest.mark.parametrize("quantity", ["1", "3"])
def test_new_risk_ledger_with_old_gateway_key_stays_unknown(
    service, monkeypatch, quantity
):
    from decimal import Decimal

    from bt_api_base.gateway_direct_protocol import canonical_gateway_payload

    from bt_api_py._contracts import OrderRequest, OrderType, Side
    from tests.unit.test_iter64_risk_service_component import ACCOUNT, ctx, sha
    from tests.unit.test_iter64_risk_service_v1_write_gate import _rows

    request = OrderRequest(
        symbol="rb", side=Side.BUY, order_type=OrderType.LIMIT,
        quantity=Decimal(quantity), price=Decimal("10"), account_id=ACCOUNT,
        client_order_id="c-" + KEY, idempotency_key=KEY, offset="open",
        quantity_unit="lots", exchange_id="SHFE",
    )
    # The Risk ledger fixture starts empty, but the synthetic Gateway already
    # has an older same-key reservation with the same payload.
    prior_digest = sha(canonical_gateway_payload(request.to_dict()))
    service.gateway.commands[KEY] = prior_digest

    def reserve_existing(**kw):
        service.gateway.calls.append(("reserve", kw))
        assert sha(kw["canonical_payload"]) == service.gateway.commands[KEY]
        return service.gateway.reply(KEY, "RESERVED")

    monkeypatch.setattr(service.gateway, "reserve", reserve_existing)
    api, host, wire, direct_gateway = _matching_facade(service, monkeypatch)
    # The service gate runs before policy evaluation or Gateway I/O; these
    # quantities do not prove either a reservation or a policy denial.
    import json
    before = _rows(service)
    assert before["permits"] == []
    raw = service.handle("risk.order.reserve", context=ctx("principal-a"), arguments={
        "request_key": KEY, "payload": json.loads(canonical_gateway_payload(request.to_dict())),
        "parent_ref": None, "child_id": None,
    })
    assert raw["outcome"] == "UNKNOWN" and raw["state"] is None
    assert _rows(service) == before and service.gateway.calls == []
    result = api.reserve_order(ROUTE, request)
    assert result.risk_answer is None
    assert result.outcome == "UNKNOWN" and result.record is None and result.risk_denied is None
    assert KEY in api._risk_uncertain_keys and host.load_intent(KEY) is not None
    assert host._risk_tickets == {}
    intent = host.load_intent(KEY)
    retried = api.reserve_order(ROUTE, request)
    assert retried.outcome == "UNKNOWN" and retried.record is None
    assert host.load_intent(KEY) is intent and host._risk_tickets == {}
    assert api.dispatch_order(ROUTE, KEY).outcome == "UNKNOWN"
    assert api.release_order(ROUTE, KEY, operation_id="release-1").outcome == "UNKNOWN"
    assert service.gateway.commands == {KEY: prior_digest}
    assert _rows(service) == before and service.gateway.calls == []
    assert wire.calls == [] and direct_gateway == []


@V2_POLICY_BLOCKED
def test_synthetic_historical_risk_permit_survives_new_run_without_writes(service, monkeypatch):
    """A synthetic old SQLite row is not evidence of Gateway/account authority."""
    from tests.unit.test_iter64_risk_quarantine_sdk import seed_synthetic_legacy_order
    from tests.unit.test_iter64_risk_service_component import ctx, payload
    from tests.unit.test_iter64_risk_service_v1_write_gate import _rows

    old = seed_synthetic_legacy_order(service)
    before = _rows(service)
    assert old is not None and old.state == "RESERVED" and old.occupancy > 0
    assert old.run_id == "old-run" and len(before["permits"]) == 1
    gateway_before, runs_before = list(service.gateway.calls), list(service.runs)
    status = service.handle("risk.order.status", context=ctx("principal-a", run="new-run"),
                            arguments={"request_key": "k1"})
    assert status["outcome"] == "UNKNOWN" and status["permit_id"] is None
    arguments = {
        "risk.order.reserve": {"request_key": "k1", "payload": payload("k1"),
                               "parent_ref": None, "child_id": None},
        "risk.order.dispatch": {"request_key": "k1", "permit_id": old.permit_id,
                                "reservation_id": old.reservation_id,
                                "view_digest": old.view_digest},
        "risk.order.release": {"request_key": "k1", "permit_id": old.permit_id},
    }
    for operation, values in arguments.items():
        result = service.handle(operation, context=ctx("principal-a", run="new-run"),
                                arguments=values)
        assert result["outcome"] == "UNKNOWN" and result["permit_id"] is None

    # A fresh facade likewise cannot turn its own local ticket into authority
    # over this synthetic historical key; no fake Risk/Gateway RPC is sent.
    from decimal import Decimal

    from bt_api_py._contracts import OrderRequest, OrderType, Side
    from tests.unit.test_iter64_risk_service_component import ACCOUNT

    api, host, wire, direct_gateway = _matching_facade(service, monkeypatch)
    request = OrderRequest(symbol="rb", side=Side.BUY, order_type=OrderType.LIMIT,
                           quantity=Decimal("1"), price=Decimal("10"), account_id=ACCOUNT,
                           client_order_id="c-k1", idempotency_key="k1", offset="open",
                           quantity_unit="lots", exchange_id="SHFE")
    assert request.to_dict() == payload("k1")
    result = api.reserve_order(ROUTE, request)
    assert result.outcome == "UNKNOWN" and result.risk_answer is None
    assert "k1" in api._risk_uncertain_keys and host.load_intent("k1") is not None
    assert api.dispatch_order(ROUTE, "k1").outcome == "UNKNOWN"
    assert api.release_order(ROUTE, "k1", operation_id="release-1").outcome == "UNKNOWN"
    assert host._risk_tickets == {} and wire.calls == [] and direct_gateway == []
    assert _rows(service) == before and service.gateway.calls == gateway_before
    assert service.runs == runs_before
