"""Synthetic strategy read RPC, bound to Registry identity and owner state."""

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.unit.test_service_registry import _next_request, _register
from test_iter63_strategy_ledger_owner import KEY, baseline, install, open_fill, order


def test_position_rpc_uses_authenticated_strategy_and_complete_instrument(tmp_path):
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
        ops = frozenset({"direct.strategy_position", "direct.strategy_close_capacity"})
        binding = GatewayDirectServiceBinding(holder["journal"],
            {"p": GatewayDirectGrant("s1", frozenset({"rb2601"}))}, owner=owner)
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, "p", "s1")
        scope = WireScope("domain", account_ref="acct", strategy_ref="s1", run_id="run1")
        capability = ServiceCapability("gateway", "service.direct_write", ops, frozenset())
        grant_scope = WireScope("domain", account_ref="acct", strategy_ref="s1")
        grant = ResourceGrant("gateway", "service.direct_write", "scope", grant_scope, ops)
        registry = ServiceRegistry(":memory:", service_id="gateway-service", capabilities=(capability,),
            grants={"p": (PrincipalGrant(identity, (grant,)),)}, policy_version="p", requirements_version="r")
        for op in ops:
            registry.register_handler("gateway", op, lambda ctx, args, op=op: binding.handle(op, context=ctx, arguments=args))
        registration = _register(registry, principal="p", identity=identity, scope=scope,
            instance_id="gateway", port="service.direct_write", scope_ref="scope", operations=tuple(ops))
        assert registration["ok"], registration
        rec = registration["result"]
        def call(op, arguments):
            return registry.handle("p", _next_request(op, {"registration_id": rec["registration_id"],
                "service_epoch": rec["service_epoch"], "instance_id": "gateway", "arguments": arguments},
                identity=identity, scope=scope))
        payload = {"symbol": "rb2601", "exchange_id": "SHFE", "quantity_unit": "lots"}
        result = call("direct.strategy_position", {"instrument": KEY.to_dict(), "payload": payload})
        assert result["ok"] and result["result"]["strategy_position"]["long_today"] == "1"
        assert result["result"]["provider_close_capacity"] == "UNKNOWN"
        capacity = call("direct.strategy_close_capacity", {"instrument": KEY.to_dict(), "payload": order("sell", "close_today")})
        assert capacity["ok"] and capacity["result"]["strategy_position"]["strategy_close_available"] == "1"
        forged = {**KEY.to_dict(), "venue": "DCE"}
        assert call("direct.strategy_position", {"instrument": forged, "payload": payload})["result"]["code"] == "SCOPE_DENIED"
        assert call("direct.strategy_position", {"instrument": KEY.to_dict(), "payload": {**payload, "strategy": "s2"}})["result"]["code"] == "INVALID_REQUEST"
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)
