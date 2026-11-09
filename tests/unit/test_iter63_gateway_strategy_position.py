"""Gateway strategy position observations are scoped, bounded and fail closed."""
from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType
import threading

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey

from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_runtime_thread import BindingSnapshot, RuntimeSnapshot
from tests.unit.test_iter63_gateway_strategy_client import parts

ROUTE = "CTP___FUTURE"
KEY = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")
OPS = frozenset({"direct.strategy_position", "direct.strategy_close_capacity"})


def setup(monkeypatch, operations=OPS):
    kwargs = parts()
    kwargs["requested_operations"] = operations
    session = GatewayStrategyClientSession(**kwargs)
    api = GatewayBtApi(session, exchange_name=ROUTE)
    route = session.route
    registration = BindingSnapshot("registration", "service-epoch", True,
        route.mount, route.service_id, route.instance_id, route.scope_ref,
        route.scope, operations, True)
    snapshot = RuntimeSnapshot("running", True, True, None, (registration,), 0, 0,
                               route.identity, route.scope.run_id)
    monkeypatch.setattr(session.runtime, "status", lambda: snapshot)
    return session, api, registration, snapshot


def response(*, close=False):
    values = {"trading_day": "20261109", "long_today": "3", "long_yesterday": "2",
              "short_today": "0", "short_yesterday": "0", "projected_buckets": False}
    if close:
        values.update(strategy_close_available="4", strategy_close_reserved="1")
    return {"status": "OBSERVED", "schema_version": 1, "instrument": KEY.to_dict(),
            "owner_generation": "owner-generation", "strategy_position": values,
            "provider_close_capacity": "UNKNOWN"}


def test_scoped_position_and_close_observations(monkeypatch):
    session, api, _, _ = setup(monkeypatch)
    calls = []
    def call(mount, operation, arguments, **kwargs):
        calls.append((mount, operation, arguments, kwargs))
        body = response(close=operation.endswith("close_capacity"))
        return MappingProxyType({**body, "strategy_position": MappingProxyType(body["strategy_position"])})
    monkeypatch.setattr(session.runtime, "call", call)
    pos = api.get_strategy_position(ROUTE, KEY, quantity_unit="lots")
    cap = api.get_strategy_close_capacity(ROUTE, KEY, quantity_unit="lots", side="sell",
                                          offset="close_today", quantity=Decimal("1"), position_side="long")
    assert pos.outcome == cap.outcome == "OBSERVED"
    assert pos.observation.long_today == Decimal("3")
    assert pos.observation.provider_close_capacity == "UNKNOWN"
    assert cap.observation.strategy_close_available == Decimal("4")
    assert cap.observation.strategy_close_reserved == Decimal("1")
    assert calls[0][2] == {"instrument": KEY.to_dict(), "payload":
        {"symbol": "rb", "exchange_id": "SHFE", "quantity_unit": "lots"}}
    assert calls[1][2]["payload"] == {"symbol": "rb", "exchange_id": "SHFE", "quantity_unit": "lots",
        "side": "sell", "offset": "close_today", "quantity": "1", "position_side": "long"}
    assert calls[0][3]["account_ref"] == "account" and calls[0][3]["scope_ref"] == "scope"
    assert api.strategy_position_is_current(ROUTE, cap.observation, max_age_seconds=3)
    assert not api.strategy_position_is_current(ROUTE, replace(cap.observation), max_age_seconds=3)
    assert not api.strategy_position_is_current(ROUTE, cap.observation, max_age_seconds=1e-12)


def test_unavailable_denied_scope_or_untrusted_response(monkeypatch):
    session, api, registration, snapshot = setup(monkeypatch)
    monkeypatch.setattr(session.runtime, "call", lambda *_a, **_k: response())
    previous = api.get_strategy_position(ROUTE, KEY, quantity_unit="lots").observation
    monkeypatch.setattr(session.runtime, "call", lambda *_a, **_k:
        {"status": "REQUEST_REJECTED", "code": "STRATEGY_LEDGER_UNAVAILABLE"})
    result = api.get_strategy_position(ROUTE, KEY, quantity_unit="lots")
    assert result.outcome == "UNAVAILABLE" and result.code == "STRATEGY_LEDGER_UNAVAILABLE"
    assert not api.strategy_position_is_current(ROUTE, previous, max_age_seconds=5)
    for mutation in (
        {"provider_close_capacity": "10"}, {"owner_generation": ""},
        {"instrument": {**KEY.to_dict(), "venue": "CFFEX"}}, {"schema_version": True},
        {"strategy_position": {**response()["strategy_position"], "long_today": "NaN"}},
        {"strategy_position": {**response()["strategy_position"], "extra": "1"}},
    ):
        monkeypatch.setattr(session.runtime, "call", lambda *_a, _m=mutation, **_k: {**response(), **_m})
        assert api.get_strategy_position(ROUTE, KEY, quantity_unit="lots").outcome == "UNAVAILABLE"
    monkeypatch.setattr(session.runtime, "status", lambda: replace(snapshot, bindings=(replace(registration, operations=frozenset()),)))
    monkeypatch.setattr(session.runtime, "call", lambda *_a, **_k: pytest.fail("unauthorized call"))
    assert api.get_strategy_position(ROUTE, KEY, quantity_unit="lots").code == "ROUTE_UNAVAILABLE"


def test_registration_or_owner_generation_change_invalidates_results(monkeypatch):
    session, api, registration, snapshot = setup(monkeypatch)
    current = [snapshot]
    monkeypatch.setattr(session.runtime, "status", lambda: current[0])
    monkeypatch.setattr(session.runtime, "call", lambda *_a, **_k: response())
    first = api.get_strategy_position(ROUTE, KEY, quantity_unit="lots").observation
    current[0] = replace(snapshot, bindings=(replace(registration, registration_id="other"),))
    assert not api.strategy_position_is_current(ROUTE, first, max_age_seconds=5)
    current[0] = snapshot
    monkeypatch.setattr(session.runtime, "call", lambda *_a, **_k: {**response(), "owner_generation": "other"})
    assert api.get_strategy_position(ROUTE, KEY, quantity_unit="lots").outcome == "UNAVAILABLE"
    assert not api.strategy_position_is_current(ROUTE, first, max_age_seconds=5)


def test_changed_registration_during_rpc_and_wrong_process_or_thread(monkeypatch):
    session, api, registration, snapshot = setup(monkeypatch)
    current = [snapshot]
    monkeypatch.setattr(session.runtime, "status", lambda: current[0])
    def call(*_a, **_k):
        current[0] = replace(snapshot, bindings=(replace(registration, service_epoch="new-epoch"),))
        return response()
    monkeypatch.setattr(session.runtime, "call", call)
    assert api.get_strategy_position(ROUTE, KEY, quantity_unit="lots").outcome == "UNAVAILABLE"
    errors = []
    def run():
        try:
            api.get_strategy_position(ROUTE, KEY, quantity_unit="lots")
        except RuntimeError as exc:
            errors.append(str(exc))
    t = threading.Thread(target=run)
    t.start(); t.join()
    assert errors == ["GATEWAY_POSITION_WRONG_OWNER"]
    with pytest.raises(ValueError, match="GATEWAY_ROUTE_MISMATCH"):
        api.get_strategy_position("wrong", KEY, quantity_unit="lots")


def test_untrusted_capacity_exceeding_strategy_position_refused(monkeypatch):
    session, api, _, _ = setup(monkeypatch)
    invalid = response(close=True)
    invalid["strategy_position"]["strategy_close_available"] = "999"
    monkeypatch.setattr(session.runtime, "call", lambda *_a, **_k: invalid)
    result = api.get_strategy_close_capacity(
        ROUTE, KEY, quantity_unit="lots", side="sell", offset="close",
        quantity=Decimal("1"), position_side="long"
    )
    assert result.outcome == "UNAVAILABLE"


@pytest.mark.parametrize("kwargs", [
    {"side":"buy", "offset":"open", "quantity":Decimal("1")},
    {"side":"buy", "offset":"close", "quantity":Decimal("NaN")},
    {"side":"sell", "offset":"close", "quantity":Decimal("1"), "position_side":"short"},
])
def test_invalid_close_request_refused_before_network(monkeypatch, kwargs):
    session, api, _, _ = setup(monkeypatch)
    monkeypatch.setattr(session.runtime, "call", lambda *_a, **_k: pytest.fail("invalid RPC"))
    with pytest.raises(ValueError, match="GATEWAY_POSITION_INVALID_REQUEST"):
        api.get_strategy_close_capacity(ROUTE, KEY, quantity_unit="lots", **kwargs)


def test_typed_client_against_real_registry_and_single_gateway_owner(tmp_path, monkeypatch):
    """Synthetic facts exercise the actual owner, journal and authenticated registry."""
    from pathlib import Path
    from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
    from bt_api_gateway.session_guard import LocalAccountSessionGuard
    from bt_api_py.gateway_direct_client import GatewayClientRoute
    from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
    from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
    from bt_api_py.gateway_strategy_position import GatewayStrategyPositionClient
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
    from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
    from tests.unit.test_service_registry import _next_request, _register

    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "bt_api/bt_api_gateway/tests"))
    from test_iter63_strategy_ledger_owner import KEY as ledger_key, baseline, install, open_fill

    directory = tmp_path / "guards"
    directory.mkdir()
    holder = {}

    def factory():
        guard = LocalAccountSessionGuard(directory, "acct", "owner")
        generation = guard.acquire()
        journal = GatewayDirectCommandJournal.create(tmp_path / "db", "acct", generation=generation)
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        baseline(journal, guard)
        install(journal, guard)
        open_fill(journal, guard)
        dispatcher = GatewayDirectDispatcher(journal, "CTP", guard, lambda *_: None,
                                             lambda _: ("anchor",), lambda _: True)
        holder["journal"] = journal
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release)

    owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=1, busy_timeout_seconds=1)
    assert owner.start(2)
    registry = None
    try:
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, "p", "s1")
        scope = WireScope("domain", account_ref="acct", strategy_ref="s1", run_id="run1")
        grant_scope = WireScope("domain", account_ref="acct", strategy_ref="s1")
        capability = ServiceCapability("gateway", "service.direct_write", OPS, frozenset())
        grant = ResourceGrant("gateway", "service.direct_write", "scope", grant_scope, OPS)
        registry = ServiceRegistry(":memory:", service_id="gateway-service", capabilities=(capability,),
            grants={"p": (PrincipalGrant(identity, (grant,)),)}, policy_version="p", requirements_version="r")
        binding = GatewayDirectServiceBinding(holder["journal"],
            {"p": GatewayDirectGrant("s1", frozenset({"rb2601"}))}, owner=owner)
        for op in OPS:
            registry.register_handler("gateway", op,
                lambda ctx, args, op=op: binding.handle(op, context=ctx, arguments=args))
        registered = _register(registry, principal="p", identity=identity, scope=scope,
            instance_id="gateway", port="service.direct_write", scope_ref="scope", operations=tuple(OPS))
        assert registered["ok"]
        rec = registered["result"]
        route = GatewayClientRoute("broker.direct_write", "gateway-service", "gateway", "scope", scope, identity)
        runtime = object.__new__(ThreadedServiceClientRuntime)
        reg = BindingSnapshot(rec["registration_id"], rec["service_epoch"], True, route.mount,
                              route.service_id, route.instance_id, route.scope_ref, scope, OPS, True)
        snap = RuntimeSnapshot("running", True, True, None, (reg,), 0, 0, identity, "run1")
        monkeypatch.setattr(runtime, "status", lambda: snap)
        def call(_mount, op, arguments, **_kwargs):
            reply = registry.handle("p", _next_request(op, {"registration_id": rec["registration_id"],
                "service_epoch": rec["service_epoch"], "instance_id": "gateway", "arguments": arguments},
                identity=identity, scope=scope))
            assert reply["ok"]
            return reply["result"]
        monkeypatch.setattr(runtime, "call", call)
        client = GatewayStrategyPositionClient(runtime,
            binding=GatewayCommandBinding("journal", "acct", "s1", "p"), route=route)
        position = client.position(ledger_key, quantity_unit="lots")
        assert position.outcome == "OBSERVED" and position.observation.long_today == Decimal("1")
        capacity = client.close_capacity(ledger_key, quantity_unit="lots", side="sell",
                                         offset="close_today", quantity=Decimal("1"))
        assert capacity.outcome == "OBSERVED" and capacity.observation.strategy_close_available == Decimal("1")
        assert capacity.observation.provider_close_capacity == "UNKNOWN"
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)
