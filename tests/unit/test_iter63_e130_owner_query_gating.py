"""I63-E130: no other provider query of the owner starts behind an outstanding return query.

Synthetic CTP profile (fake session and provider, real owner thread, journal
and strategy ledger); no native account is contacted.
"""

import threading
from dataclasses import replace

import zmq
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

import bt_api_py.ctp_gateway_return_maintenance as return_module
from bt_api_py import ctp_gateway_trading as module
from bt_api_py.ctp_gateway_strategy_ledger import CtpGatewayStrategyLedger
from bt_api_py.ctp_gateway_trading import build_ctp_gateway_trading_profile
from bt_api_py.gateway_direct_service import GatewayControlGrant
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.test_iter63_ctp_gateway_trading import FakeReturnMaintenance, _wait, make


def test_settlement_and_ledger_reads_wait_until_the_return_query_ended(tmp_path, monkeypatch):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch, write_enabled=True)
    settings = replace(settings, settlement_refresh_seconds=0)
    events, lock = [], threading.Lock()

    def record(entry):
        with lock:
            events.append(entry)

    class SlowReturns(FakeReturnMaintenance):
        """Each query stays outstanding for three ticks, as on a slow venue."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.remaining = 0
            self.queries = 0

        def due(self, now):
            return self.remaining == 0 and self.queries < 3

        def start_query(self):
            self.queries += 1
            self.remaining = 3
            record(("start", True))

        def advance_query(self):
            if self.remaining:
                self.remaining -= 1
                record(("advance", True))
            return self.remaining > 0

        def query_pending(self):
            return self.remaining > 0

        def ledger_continuous(self):
            return False

    owner_returns = []

    def returns_factory(*args, **kwargs):
        owner_returns.append(SlowReturns(*args, **kwargs))
        return owner_returns[-1]

    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", returns_factory)

    def tick(controller):
        record(("settlement", owner_returns[0].query_pending()))
        return controller.status

    def ledger_maintain(ledger):
        record(("ledger", owner_returns[0].query_pending()))

    monkeypatch.setattr(module.GatewaySettlementController, "tick", tick)
    monkeypatch.setattr(CtpGatewayStrategyLedger, "maintain", ledger_maintain)
    raw = config.to_public_dict()
    opts = raw["components"]["gateway"]["settings"]["options"]
    opts.update(control_enabled=True, strategy_ledger_enabled=True)
    config = parse_service_config(raw)
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, "operator-p", "operator-a")
    scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
    ops = frozenset({"direct.control.status"})
    resource = ResourceGrant("gateway-a", "service.direct_write", config.scope_ref, scope, ops)
    operator_public, _ = zmq.curve_keypair()
    authority = replace(authority, principals={**authority.principals, operator_public: "operator-p"},
                        grants={**authority.grants,
                                "operator-p": (PrincipalGrant(identity, (resource,)),)})
    grant = GatewayControlGrant("operator-a", True, frozenset(), ops)
    profile = build_ctp_gateway_trading_profile(
        config, authority, tmp_path / "gating-registry.sqlite", profile_ref="gating",
        settings=settings, grants=grants, request_feed_factory=lambda: None,
        market_stream_factory=lambda *_: None, assert_admitted=lambda _: True,
        control_grants={"operator-p": grant}, upgrade_controls_from_v9=True,
        upgrade_strategy_ledger_from_v10=True,
        return_settings=module.CtpGatewayReturnSettings(
            (MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612"),)),
    )
    deployment = profile.deployment_factory()
    resources = deployment._provide_resources("gateway", "gateway-a", opts, authority.scope)
    try:
        _wait(lambda: owner_returns and owner_returns[0].queries == 3
              and not owner_returns[0].query_pending()
              and sum(what == "ledger" for what, _ in events[-3:]) >= 1)
    finally:
        resources.close()
    with lock:
        seen = list(events)
    starts = [index for index, (what, _) in enumerate(seen) if what == "start"]
    assert len(starts) == 3
    # While a query was outstanding neither started; both ran between queries.
    assert [entry for entry in seen if entry[0] in {"settlement", "ledger"} and entry[1]] == []
    for first, second in zip(starts, starts[1:], strict=False):
        between = {what for what, _ in seen[first:second]}
        assert {"settlement", "ledger"} <= between
