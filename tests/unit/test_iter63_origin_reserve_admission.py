"""A reservation refused by the trading fence is a plain refusal, also on the strategy-origin path (I63-E141).

Found on SimNow on 2026-11-11: after a failed return query the return stream is closed to new
reservations for a while. On journals with strategy origins (schema 16 and later) the Gateway
answered such a reservation with ``{"status": "UNKNOWN"}``; the strategy could not tell a refusal
from a lost reply, waited about 20 seconds and then rejected its own order.
"""
from contextlib import contextmanager
from decimal import Decimal

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    GatewayDirectCommandJournal,
)
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_gateway.strategy_position_ledger import (
    StrategyBaselineEvidence,
    StrategyInstrumentBinding,
)

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import (
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
    GatewayReservationAdmissionDenied,
)
from bt_api_py.gateway_strategy_origin_source import GatewayStrategyOriginSource
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from tests.unit.test_service_registry import _next_request, _register

INSTRUMENT = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")


@pytest.fixture
def gateway(tmp_path):
    """A real owner, journal (schema 17, strategy origins on) and registry; the trading fence is ours."""
    directory = tmp_path / "guards"
    directory.mkdir()
    holder, state = {}, {"open": True}

    def factory():
        guard = LocalAccountSessionGuard(directory, "acct", "owner")
        generation = guard.acquire()
        journal = GatewayDirectCommandJournal.create(tmp_path / "db", "acct", generation=generation)
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        evidence = StrategyBaselineEvidence("acct", journal.journal_id, "20300101", "CTP", generation, 1,
                                            "source", "positions", "orders", "cut", ("resource",), 100, 200,
                                            True, True, True, True)

        @contextmanager
        def baseline_fence():
            yield

        journal.initialize_empty_strategy_ledger(evidence, guard=guard, baseline_fence=baseline_fence,
                                                 verify_current_empty=lambda value: value == evidence)
        journal.install_strategy_ledger_ports(
            guard=guard, resolve_binding=lambda _: StrategyInstrumentBinding(INSTRUMENT, "CTP", "lots", True),
            current_trading_day=lambda: "20300101", verify_day_transition=lambda *_: False,
            verify_current_continuity=lambda *_: True)
        for step in ("enable_account_extensions", "enable_parent_authority", "enable_account_facts_financial",
                     "enable_actor_stream", "enable_strategy_origin", "enable_control_cancel"):
            getattr(journal, step)(guard)
        source = GatewayStrategyOriginSource(resolve=lambda *_: INSTRUMENT, recheck=lambda _: True)
        holder.update(journal=journal, source=source)
        dispatcher = GatewayDirectDispatcher(journal, "CTP", guard, lambda *_: None, lambda _: ("anchor",),
                                             lambda _: True)

        def close():
            source.close()
            guard.release()

        return GatewayDirectOwnerResources(dispatcher, journal, close)

    owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=1, busy_timeout_seconds=1)
    assert owner.start(2)

    @contextmanager
    def trading_fence(_context, _order):
        # What the CTP return maintenance does while its return stream is not healthy.
        if not state["open"]:
            raise GatewayReservationAdmissionDenied()
        yield

    binding = GatewayDirectServiceBinding(holder["journal"], {"p": GatewayDirectGrant("s1", frozenset({"rb"}))},
                                          owner=owner, reservation_fence=trading_fence,
                                          strategy_origin_source=holder["source"])
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, "p", "s1")
    scope = WireScope("domain", account_ref="acct", strategy_ref="s1", run_id="run")
    operations = frozenset({"direct.reserve"})
    registry = ServiceRegistry(
        ":memory:", service_id="gateway-service",
        capabilities=(ServiceCapability("gateway", "service.direct_write", operations, operations),),
        grants={"p": (PrincipalGrant(identity, (ResourceGrant(
            "gateway", "service.direct_write", "scope", WireScope("domain", account_ref="acct", strategy_ref="s1"),
            operations),)),)}, policy_version="p", requirements_version="r")
    registry.register_handler("gateway", "direct.reserve",
                              lambda ctx, args: binding.handle("direct.reserve", context=ctx, arguments=args))
    registered = _register(registry, principal="p", identity=identity, scope=scope, instance_id="gateway",
                           port="service.direct_write", scope_ref="scope", operations=tuple(operations))
    assert registered["ok"], registered
    record = registered["result"]

    def reserve(key):
        payload = OrderRequest("rb", Side.BUY, OrderType.LIMIT, Decimal("1"), "acct", "client", price=Decimal("10"),
                               idempotency_key=key, exchange_id="SHFE", offset="open", quantity_unit="lots").to_dict()
        return registry.handle("p", _next_request("direct.reserve", {
            "registration_id": record["registration_id"], "service_epoch": record["service_epoch"],
            "instance_id": "gateway", "arguments": {"request_key": key, "payload": payload, "ttl_seconds": 10}},
            identity=identity, scope=scope))["result"]

    try:
        yield reserve, state, holder["journal"]
    finally:
        registry.close()
        assert owner.close(2)


def test_a_reservation_the_trading_fence_refuses_is_answered_as_a_refusal_and_nothing_is_stored(gateway):
    reserve, state, journal = gateway
    assert reserve("one")["record"]["state"] == "RESERVED"          # the path works while the fence is open
    state["open"] = False
    assert reserve("two") == {"status": "REQUEST_REJECTED", "code": "ADMISSION_DENIED"}
    with pytest.raises(DirectCommandJournalError, match="^NOT_FOUND$"):
        journal.get("s1", "two", "p")                               # rolled back: no command, no origin
    with journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM direct_strategy_origins").fetchone()[0] == 1
    # A repeat of the reservation that exists is still observed while the fence is closed.
    assert reserve("one")["record"]["state"] == "RESERVED"
    state["open"] = True
    assert reserve("two")["record"]["state"] == "RESERVED"          # and the same key goes through afterwards
