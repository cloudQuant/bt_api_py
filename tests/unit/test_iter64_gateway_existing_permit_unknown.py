"""Gateway facade projections for an existing Risk permit with unavailable facts.

UNIT/FAKE only: most cases use fake wire responses; the final two parameter
cases also use a synthetic legacy record in a temporary Risk SQLite ledger.
None proves parent corruption, a Gateway process, or an actual account.
"""

import pytest
from bt_api_base.risk_service_protocol import RiskOrderResult

from bt_api_py.service_runtime_thread import ThreadedRuntimeError
from tests.unit.test_iter63_gateway_api import order
from tests.unit.test_iter64_risk_service_client import DIGEST, answer, facade
from tests.unit.test_iter64_risk_service_component import service  # noqa: F401

ROUTE = "CTP___FUTURE"
KEY = "same-key"


def unknown_reserved():
    return answer(
        "UNKNOWN",
        state="RESERVED",
        permit_id="permit-1",
        reservation_id="res-1",
        view_digest=DIGEST,
        code="risk_parent_unavailable",
    )


def test_submitted_intent_ticket_is_consumed_but_v1_first_reserve_stays_unknown(monkeypatch):
    from bt_api_py.gateway_direct_client import build_gateway_order_intent

    api, host, wire, direct_gateway = facade(monkeypatch, replies=[answer("RESERVED")])
    intent = build_gateway_order_intent(api._binding, api._route, order())
    ticket = host.create_risk_reserve_intent(intent)
    assert ticket is not None and host.load_intent(KEY) == intent
    consumed = []
    consume = host.consume_risk_reserve_ticket

    def record_consume(candidate, capability):
        result = consume(candidate, capability)
        consumed.append((candidate.request_key, result))
        return result

    monkeypatch.setattr(host, "consume_risk_reserve_ticket", record_consume)
    # A caller lacking the ticket cannot steal or poison the first submission.
    assert api.reserve_order(ROUTE, order()).outcome == "UNKNOWN"
    assert api.reserve_order(ROUTE, order(), first_send_ticket=object()).outcome == "UNKNOWN"
    assert wire.calls == []
    result = api.reserve_order(ROUTE, order(), first_send_ticket=ticket)
    assert result.outcome == "UNKNOWN" and result.record is None
    assert consumed[-1] == (KEY, True) and host._risk_tickets == {}
    assert KEY in api._risk_keys and KEY in api._risk_uncertain_keys
    assert KEY not in api._risk_permits and host.load_intent(KEY) is intent
    assert api.reserve_order(ROUTE, order(), first_send_ticket=ticket).outcome == "UNKNOWN"
    assert wire.calls == [] and len(wire.replies) == 1
    assert direct_gateway == []


def test_host_without_first_send_capability_never_sends(monkeypatch):
    api, host, wire, direct_gateway = facade(monkeypatch)
    monkeypatch.setattr(host, "create_risk_reserve_intent", None)
    assert api.reserve_order(ROUTE, order()).outcome == "UNKNOWN"
    assert host.load_intent(KEY) is None and wire.calls == [] and direct_gateway == []


def test_synthetic_lost_client_reply_and_facade_retry_keep_unknown(monkeypatch):
    # Direct fake client models a historical lost reply; this is not a
    # reachable facade first reserve or proof of an actual old reservation.
    simulated_gateway_reserves = []

    def create_then_lose_reply():
        simulated_gateway_reserves.append(KEY)
        raise ThreadedRuntimeError("TIMEOUT_AFTER_START")

    api, host, wire, direct_gateway = facade(
        monkeypatch, replies=[create_then_lose_reply, unknown_reserved()]
    )
    first = api._risk.reserve(order(), request_key=KEY)
    assert first.outcome == "UNKNOWN"
    first = api.reserve_order(ROUTE, order())
    retried = api.reserve_order(ROUTE, order())

    assert first.outcome == retried.outcome == "UNKNOWN"
    assert retried.risk_denied is None and retried.record is None
    assert retried.risk_answer is None
    assert host.load_intent(KEY).request_key == KEY
    assert KEY in api._risk_keys and KEY not in api._risk_permits
    assert simulated_gateway_reserves == [KEY]
    assert [call[1] for call in wire.calls] == ["risk.order.reserve"]
    assert direct_gateway == []
    # Do not dispatch or release an uncertain key; status remains read-only.
    assert api.dispatch_order(ROUTE, KEY).outcome == "UNKNOWN"
    release = api.release_order(ROUTE, KEY, operation_id="release-1")
    assert release.outcome == "UNKNOWN"
    assert api.risk_order_status(ROUTE, KEY).answer.outcome == "UNKNOWN"
    assert [call[1] for call in wire.calls] == [
        "risk.order.reserve",
        "risk.order.status",
    ]
    assert direct_gateway == [] and simulated_gateway_reserves == [KEY]


def test_synthetic_known_permit_duplicate_preserves_receipt_reads_and_cancel(monkeypatch):
    # A locally injected permit covers downstream routing only; the v1 gate
    # cannot acquire it and the injection supplies no account authority.
    from bt_api_py.gateway_direct_client import build_gateway_order_intent
    from bt_api_py.gateway_strategy_delegated import GatewayDelegatedCommandResult
    from tests.unit.test_iter64_risk_service_client import delegated_observation

    api, host, wire, direct_gateway = facade(monkeypatch, replies=[answer("RESERVED")])
    host.save_intent(build_gateway_order_intent(api._binding, api._route, order()))
    api._risk_keys.add(KEY)
    api._risk_permits[KEY] = RiskOrderResult.from_mapping(answer("RESERVED"))
    original_permit = api._risk_permits[KEY]
    original_intent = host.load_intent(KEY)
    assert api.reserve_order(ROUTE, order()).outcome == "UNKNOWN"
    assert KEY not in api._risk_uncertain_keys
    assert api._risk_permits[KEY] is original_permit and host.load_intent(KEY) is original_intent
    assert api.risk_order_status(ROUTE, KEY).answer.outcome == "RESERVED"

    observation = delegated_observation("SENT")
    monkeypatch.setattr(
        api._delegated,
        "status",
        lambda key, **kw: GatewayDelegatedCommandResult("OBSERVED", observation),
    )
    calls = []
    monkeypatch.setattr(
        api._delegated, "cancel", lambda obs, **kw: calls.append(obs) or "cancelled"
    )
    assert api.cancel_order(ROUTE, KEY, operation_id="cancel-1") == "cancelled"
    assert calls == [observation]
    assert [call[1] for call in wire.calls] == ["risk.order.status"]
    assert direct_gateway == []


def test_recreated_facade_with_same_host_keeps_legacy_intent_unknown(monkeypatch):
    from bt_api_py.gateway_api import GatewayBtApi

    original, host, wire, direct_gateway = facade(monkeypatch, replies=[answer("RESERVED")])
    assert original.reserve_order(ROUTE, order()).outcome == "UNKNOWN"
    original_intent = host.load_intent(KEY)
    restarted = GatewayBtApi(original._session, exchange_name=ROUTE, risk_client=original._risk)
    assert restarted.reserve_order(ROUTE, order()).outcome == "UNKNOWN"
    assert restarted.dispatch_order(ROUTE, KEY).outcome == "UNKNOWN"
    # A read-back permit is not authority to dispatch or release an older key.
    assert restarted.risk_order_status(ROUTE, KEY).answer.outcome == "RESERVED"
    assert restarted.release_order(ROUTE, KEY, operation_id="release-1").outcome == "UNKNOWN"
    assert host.load_intent(KEY) is original_intent
    assert [call[1] for call in wire.calls] == ["risk.order.status"]
    assert direct_gateway == []


def test_synthetic_old_reservation_status_cannot_authorize_new_local_host(monkeypatch):
    api, _, wire, direct_gateway = facade(monkeypatch, replies=[unknown_reserved()])
    reply = api.reserve_order(ROUTE, order())
    assert reply.outcome == "UNKNOWN" and reply.risk_denied is None
    assert reply.risk_answer is None and wire.calls == []
    status = api.risk_order_status(ROUTE, KEY)
    assert status.answer.outcome == "UNKNOWN" and status.answer.state == "RESERVED"
    assert (status.answer.permit_id, status.answer.reservation_id) == (
        "permit-1",
        "res-1",
    )
    assert [call[1] for call in wire.calls] == ["risk.order.status"]
    assert api.dispatch_order(ROUTE, KEY).outcome == "UNKNOWN"
    assert api.release_order(ROUTE, KEY, operation_id="release-1").outcome == "UNKNOWN"
    assert [call[1] for call in wire.calls] == ["risk.order.status"]
    assert direct_gateway == []


@pytest.mark.parametrize("timeout", [0, -1, float("nan")])
def test_invalid_timeout_on_a_new_local_key_does_not_disprove_an_old_host(monkeypatch, timeout):
    api, host, wire, direct_gateway = facade(monkeypatch)
    result = api.reserve_order(ROUTE, order(), timeout_seconds=timeout)
    assert (result.outcome, result.code) == ("UNKNOWN", "RISK_OUTCOME_UNKNOWN")
    assert host.load_intent(KEY) is not None
    assert KEY in api._risk_keys and wire.calls == [] and direct_gateway == []


@pytest.mark.parametrize("timeout", [0, -1])
def test_old_local_intent_never_resends_when_timeout_is_invalid(monkeypatch, timeout):
    api, host, wire, direct_gateway = facade(monkeypatch, replies=[unknown_reserved()])
    assert api.reserve_order(ROUTE, order()).outcome == "UNKNOWN"
    first_intent = host.load_intent(KEY)
    assert api.reserve_order(ROUTE, order(), timeout_seconds=timeout).outcome == "UNKNOWN"
    assert host.load_intent(KEY) is first_intent
    assert wire.calls == [] and len(wire.replies) == 1
    assert direct_gateway == []


@pytest.mark.parametrize("followup", ["dispatch", "release"])
def test_concurrent_same_facade_reserve_never_bypasses_v1_gate(monkeypatch, followup):
    """Synchronized starts do not force interleaving or bound a deadlocked executor."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2, timeout=3)
    api, host, wire, direct_gateway = facade(monkeypatch, replies=[answer("RESERVED")])

    def submit():
        barrier.wait()
        return api.reserve_order(ROUTE, order())

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(submit)
        second = executor.submit(submit)
        assert first.result(timeout=5).outcome == "UNKNOWN"
        assert second.result(timeout=5).outcome == "UNKNOWN"
    assert wire.calls == [] and len(wire.replies) == 1
    assert host.load_intent(KEY) is not None and host._risk_tickets == {}
    assert KEY in api._risk_uncertain_keys and KEY not in api._risk_permits
    assert direct_gateway == []
    if followup == "dispatch":
        assert api.dispatch_order(ROUTE, KEY).outcome == "UNKNOWN"
    else:
        assert api.release_order(ROUTE, KEY, operation_id="release-1").outcome == "UNKNOWN"
    assert wire.calls == [] and direct_gateway == []


def test_synthetic_known_permit_unknown_dispatch_and_release_preserve_identity(monkeypatch):
    # Explicitly inject a hypothetical permit to exercise downstream mapping.
    # The v1 facade itself never establishes this permit.
    api, _, wire, direct_gateway = facade(
        monkeypatch, replies=[unknown_reserved(), unknown_reserved()]
    )
    api._risk_keys.add(KEY)
    api._risk_permits[KEY] = RiskOrderResult.from_mapping(answer("RESERVED"))

    dispatched = api.dispatch_order(ROUTE, KEY)
    released = api.release_order(ROUTE, KEY, operation_id="release-1")
    assert dispatched.outcome == released.outcome == "UNKNOWN"
    assert dispatched.risk_denied is None and released.risk_denied is None
    assert dispatched.risk_answer.state == released.risk_answer.state == "RESERVED"
    assert api._risk_permits[KEY].permit_id == "permit-1"
    assert api._risk_permits[KEY].reservation_id == "res-1"
    assert [call[1] for call in wire.calls] == [
        "risk.order.dispatch",
        "risk.order.release",
    ]
    assert direct_gateway == []


@pytest.mark.parametrize("new_run", ["old-run", "new-run"])
def test_new_host_synthetic_legacy_key_stays_unknown_through_local_service(request, monkeypatch, new_run):
    from decimal import Decimal

    from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

    from bt_api_py._contracts import OrderRequest, OrderType, Side
    from bt_api_py.gateway_api import GatewayBtApi
    from bt_api_py.gateway_direct_client import GatewayClientRoute
    from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
    from bt_api_py.risk_service_client import RiskClientRoute, RiskServiceClient
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
    from tests.unit.test_iter63_gateway_strategy_client import config_data, parts
    from tests.unit.test_iter64_risk_quarantine_sdk import seed_synthetic_legacy_order
    from tests.unit.test_iter64_risk_service_client import Host, Wire
    from tests.unit.test_iter64_risk_service_component import ACCOUNT, DOMAIN, ctx

    risk_service = request.getfixturevalue("service")
    # A row inserted into temporary SQLite represents historical local data,
    # not authority or evidence that an actual Gateway reserve occurred.
    key = "k1"
    old = seed_synthetic_legacy_order(risk_service)
    assert old is not None and old.state == "RESERVED" and old.occupancy > 0
    original_gateway_calls = list(risk_service.gateway.calls)
    data = parts()
    config = config_data()
    config["registration"]["strategy_ref"] = "strategy-a"
    config["clients"]["gateway"]["settings"]["bindings"][0]["account_ref"] = ACCOUNT
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-a", "strategy-a")
    scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref="strategy-a", run_id=new_run)
    data.update(
        config=parse_client_config(config),
        binding=GatewayCommandBinding("journal", ACCOUNT, "strategy-a", "principal-a"),
        route=GatewayClientRoute(
            "broker.direct_write", "gateway", "instance", "scope", scope, identity
        ),
        host=Host(),
        cancel_host=Host(),
    )
    session = GatewayStrategyClientSession(**data)
    route = RiskClientRoute("broker.risk_order", "risk", "risk-a", "risk-scope", scope, identity)
    client = RiskServiceClient(
        ThreadedServiceClientRuntime(lambda: None, require_unregister_ack=True), route=route
    )
    wire = Wire(client, monkeypatch)

    def call(mount, operation, arguments, **kwargs):
        wire.calls.append((mount, operation, arguments, kwargs))
        return risk_service.handle(
            operation, context=ctx("principal-a", run=new_run), arguments=arguments
        )

    monkeypatch.setattr(client._runtime, "call", call)
    api = GatewayBtApi(session, exchange_name=ROUTE, risk_client=client)
    request = OrderRequest(
        symbol="rb",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        price=Decimal("10"),
        account_id=ACCOUNT,
        client_order_id="c-" + key,
        idempotency_key=key,
        offset="open",
        quantity_unit="lots",
        exchange_id="SHFE",
    )
    result = api.reserve_order(ROUTE, request)
    assert result.outcome == "UNKNOWN" and result.risk_denied is None
    assert result.risk_answer is None and wire.calls == []
    assert key not in api._risk_permits and key in api._risk_uncertain_keys
    status = api.risk_order_status(ROUTE, key)
    assert status.outcome == "ANSWERED" and status.answer.outcome == "UNKNOWN"
    assert status.answer.permit_id is None and status.answer.reservation_id is None
    assert api.dispatch_order(ROUTE, key).outcome == "UNKNOWN"
    assert api.release_order(ROUTE, key, operation_id="release-1").outcome == "UNKNOWN"
    assert risk_service.gateway.calls == original_gateway_calls
    assert [item[1] for item in wire.calls] == ["risk.order.status"]
    assert api.reserve_order(ROUTE, request).outcome == "UNKNOWN"
    assert len(wire.calls) == 1 and risk_service.gateway.calls == original_gateway_calls
    with risk_service._ledger.transaction() as txn:
        assert txn.permit_by_key("strategy-a", key) == old
