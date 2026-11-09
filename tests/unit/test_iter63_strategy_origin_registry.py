"""Local Registry and actual Gateway owner, with synthetic source callbacks."""
from contextlib import contextmanager
from decimal import Decimal
import sqlite3

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import DirectCommandJournalError, GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_gateway.strategy_position_ledger import StrategyBaselineEvidence, StrategyInstrumentBinding

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.gateway_strategy_origin_source import GatewayStrategyOriginSource
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.unit.test_service_registry import _next_request, _register


@pytest.mark.parametrize("with_source", [True, False])
@pytest.mark.parametrize("schema_version", [16, 17, 18, 19])
def test_actual_registry_owner_origin_source_and_recheck(tmp_path, with_source, schema_version):
    directory = tmp_path / "guards"
    directory.mkdir()
    state = {"current": True}
    holder = {}
    instrument = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")

    def factory():
        guard = LocalAccountSessionGuard(directory, "acct", "owner")
        generation = guard.acquire()
        journal = GatewayDirectCommandJournal.create(tmp_path / "db", "acct", generation=generation)
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        evidence = StrategyBaselineEvidence("acct", journal.journal_id, "20300101", "CTP", generation, 1,
            "source", "positions", "orders", "cut", ("resource",), 100, 200, True, True, True, True)
        @contextmanager
        def baseline_fence():
            yield
        journal.initialize_empty_strategy_ledger(evidence, guard=guard, baseline_fence=baseline_fence,
                                                verify_current_empty=lambda value: value == evidence)
        journal.install_strategy_ledger_ports(guard=guard,
            resolve_binding=lambda _: StrategyInstrumentBinding(instrument, "CTP", "lots", True),
            current_trading_day=lambda: "20300101", verify_day_transition=lambda *_: False,
            verify_current_continuity=lambda *_: True)
        journal.enable_account_extensions(guard)
        journal.enable_parent_authority(guard)
        journal.enable_account_facts_financial(guard)
        journal.enable_actor_stream(guard)
        journal.enable_strategy_origin(guard)
        if schema_version >= 17:
            journal.enable_control_cancel(guard)
        if schema_version >= 18:
            journal.enable_account_fact_recovery(guard)
        if schema_version >= 19:
            journal.enable_account_event_recovery(guard)
        source = GatewayStrategyOriginSource(resolve=lambda *_: instrument,
                                             recheck=lambda _: state["current"]) if with_source else None
        holder.update(journal=journal, source=source)
        dispatcher = GatewayDirectDispatcher(journal, "CTP", guard, lambda *_: None,
                                             lambda _: ("anchor",), lambda _: True)
        def close():
            if source is not None:
                source.close()
            guard.release()
        return GatewayDirectOwnerResources(dispatcher, journal, close)

    owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=1, busy_timeout_seconds=1)
    assert owner.start(2)
    registry = None
    committed_under_fence = []
    try:
        @contextmanager
        def admission(_context, _order):
            assert registry._lock._is_owned()
            try:
                yield
            finally:
                assert registry._lock._is_owned()
                with sqlite3.connect(tmp_path / "db") as db:
                    committed_under_fence.append(db.execute(
                        "SELECT count(*) FROM direct_strategy_origins").fetchone()[0])
        binding = GatewayDirectServiceBinding(holder["journal"], {"p": GatewayDirectGrant("s1", frozenset({"rb"}))},
            owner=owner, reservation_fence=admission, strategy_origin_source=holder["source"])
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, "p", "s1")
        scope = WireScope("domain", account_ref="acct", strategy_ref="s1", run_id="run")
        ops = frozenset({"direct.reserve"})
        capability = ServiceCapability("gateway", "service.direct_write", ops, ops)
        grant = ResourceGrant("gateway", "service.direct_write", "scope",
                              WireScope("domain", account_ref="acct", strategy_ref="s1"), ops)
        registry = ServiceRegistry(":memory:", service_id="gateway-service", capabilities=(capability,),
            grants={"p": (PrincipalGrant(identity, (grant,)),)}, policy_version="p", requirements_version="r")
        registry.register_handler("gateway", "direct.reserve", lambda ctx, args: binding.handle("direct.reserve", context=ctx, arguments=args))
        reg = _register(registry, principal="p", identity=identity, scope=scope,
            instance_id="gateway", port="service.direct_write", scope_ref="scope", operations=tuple(ops))
        assert reg["ok"], reg
        rec = reg["result"]

        def call(key):
            payload = OrderRequest("rb", Side.BUY, OrderType.LIMIT, Decimal("1"), "acct", "client",
                price=Decimal("10"), idempotency_key=key, exchange_id="SHFE", offset="open", quantity_unit="lots").to_dict()
            request = _next_request("direct.reserve", {"registration_id": rec["registration_id"],
                "service_epoch": rec["service_epoch"], "instance_id": "gateway", "arguments":
                {"request_key": key, "payload": payload, "ttl_seconds": 10}}, identity=identity, scope=scope)
            return registry.handle("p", request)

        first = call("one")
        if not with_source:
            assert first["result"]["code"] == "STRATEGY_ORIGIN_UNAVAILABLE"
        else:
            assert first["ok"] and first["result"]["status"] == "OK", first
            assert first["result"]["record"]["state"] == "RESERVED", first
            assert committed_under_fence == [1]
            with holder["journal"]._transaction() as db:
                assert db.execute("SELECT count(*) FROM direct_strategy_origins").fetchone()[0] == 1
            state["current"] = False
            retry = call("one")
            assert retry["result"]["record"]["reservation_id"] == first["result"]["record"]["reservation_id"]
            rejected = call("two")
            assert rejected["result"]["code"] == "STRATEGY_ORIGIN_UNAVAILABLE", rejected
        with pytest.raises(DirectCommandJournalError, match="NOT_FOUND"):
            holder["journal"].get("s1", "two" if with_source else "one", "p")
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)
