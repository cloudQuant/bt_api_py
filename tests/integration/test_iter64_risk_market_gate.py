"""G64-10: an order placed through the Risk service passes the Gateway market gate (64.09).

INTEGRATION_SIM evidence. A spawned CTP Gateway built by
``build_ctp_gateway_trading_profile`` with the real CTP Python provider, market
hub, clock verification and market dispatch gate; only native TD/MD and SNTP
are synthetic (as in ``test_iter63_gateway_qualified_order_process``). The
delegated dispatch requires the market gate (the profile always sets
``market_gate_required`` for delegation). The Risk principal holds its own
account-level ``service.market_source`` registration and key, the Risk host
(CLI, second process) subscribes on ``upstream.market``, and its dispatch
carries Risk's own ``market_ref``. No provider session, no account.

Composition note: the 64.09 loopback fixture has no market hub, and the gate
binds only to a hub whose provider is the trading owner's
``GatewayOwnerMarketProvider`` with a native clock source, so this test
composes the CTP trading profile the 63 qualified-order test uses.

The Gateway account key carries a sub-account; the Risk configuration names
the account only by its hashed reference (``account_scope``) and still reads
the operator account snapshot (plan 64 section 6.1).
"""

from __future__ import annotations

import json
import multiprocessing
import os
import queue
import sqlite3
import sys
import time
import traceback
from pathlib import Path

import pytest
from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from tests.integration import _iter64_recovery_support as support

ACCOUNT = AccountKey("ctp", "simnow", "simnow", "account", "9999")  # with a sub-account
SCOPE = ACCOUNT.account_key_id
DOMAIN = "paper:demo"
SOURCE = "ctp-native-market-r1"
ENTITLEMENT = "ctp-quote-v2-entitlement-r1"
RESOURCE = AccountResourceKey("future", "futures")
GATEWAY = WireIdentity(WireRole.SERVICE, "gateway-principal", "gateway-service")
STRATEGY = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-a", "strategy-a")
RISK = support.RISK
CONTROL = support.CONTROL
RISK_HOST = support.RISK_HOST
RUN = "strategy-run"
SYMBOLS = ("IF2612", "IH2612")
MARKET_OPS = frozenset({"market.subscribe", "market.unsubscribe", "market.snapshot", "market.head",
                        "market.read_after", "market.bootstrap_begin", "market.bootstrap_status",
                        "market.bootstrap_cancel", "market.clock_observation"})


def _instrument(symbol):
    return MarketInstrumentKey("ctp", "CFFEX", "simnow", "future", symbol, symbol)


def _key(symbol):
    return MarketSubscriptionKey(SOURCE, ENTITLEMENT, _instrument(symbol), "tick")


def _gateway(root, rpc, market, server_keys, publics, journal_id, issuer_id, wire_version,
             events, controls):
    """CTP trading profile with synthetic native TD/MD and SNTP, in its own process."""
    root = Path(root)
    deployment = None
    try:
        ctp_tests = Path(__file__).resolve().parents[2] / "bt_api" / "bt_api_ctp" / "tests"
        sys.path.insert(0, str(ctp_tests))
        from datetime import datetime
        from types import SimpleNamespace
        from zoneinfo import ZoneInfo

        from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
        from bt_api_ctp.feeds import live_ctp_feed
        from bt_api_ctp.feeds.live_ctp_feed import CtpMarketStream, CtpRequestDataFuture
        from bt_api_ctp.trusted_flow import flow_binding, trusted_flow
        from bt_api_gateway.direct_delegation import GatewayDelegateGrant
        from test_iter62_settlement_readback_on_write import _login, _NativeApi, _TraderSpi
        from test_market_provider import _SyntheticMdClient

        import bt_api_py.clock_verification as clock
        from bt_api_py.ctp_gateway_trading import (
            CtpGatewayAccountReadSettings,
            CtpGatewayMarketSettings,
            CtpGatewayReturnSettings,
            CtpGatewayTradingSettings,
            build_ctp_gateway_trading_profile,
        )
        from bt_api_py.gateway_account_observer import ACCOUNT_OBSERVER_OPERATIONS
        from bt_api_py.gateway_delegate_actor_service import OPERATIONS as ACTOR_OPERATIONS
        from bt_api_py.gateway_delegation_service import GatewayDelegationSettings
        from bt_api_py.gateway_direct_service import GatewayControlGrant, GatewayDirectGrant
        from bt_api_py.service_config import parse_service_config
        from bt_api_py.service_host import TrustedServiceAuthority
        from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
        from tests.integration.test_iter63_gateway_direct_service import _service_config

        clock._sntp_query = lambda _host, _timeout: (0, 2_000_000)
        original_login, original_trade = _TraderSpi.OnRspUserLogin, _TraderSpi.OnRtnTrade
        original_emit = _SyntheticMdClient.emit_tick

        def login(self, info, *other):
            info.FFEXTime = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S")
            return original_login(self, info, *other)

        def trade(self, info, *other):
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            info.TradeDate, info.TradeTime = now.strftime("%Y%m%d"), now.strftime("%H:%M:%S")
            return original_trade(self, info, *other)

        def emit(self, **fields):
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            fields.update(ActionDay=now.strftime("%Y%m%d"), TradingDay="20261106",
                          UpdateTime=now.strftime("%H:%M:%S"),
                          UpdateMillisec=now.microsecond // 1000,
                          BidPrice1=4004.0, AskPrice1=4006.0, BidVolume1=3, AskVolume1=4,
                          LowerLimitPrice=3500.0, UpperLimitPrice=4500.0,
                          OpenInterest=1000.0, Turnover=10000.0)
            return original_emit(self, **fields)

        _TraderSpi.OnRspUserLogin, _TraderSpi.OnRtnTrade = login, trade
        _SyntheticMdClient.emit_tick = emit
        natives, settled = [], []
        from bt_api_py.gateway_settlement import GatewaySettlementController

        original_tick = GatewaySettlementController.tick

        def traced_tick(self):
            result = original_tick(self)
            if result.observed_ready:
                settled[:] = [True]
            return result

        GatewaySettlementController.tick = traced_tick

        class Native(_NativeApi):
            def ReqOrderInsert(self, field, request_id):
                result = super().ReqOrderInsert(field, request_id)
                suffix = str(field.OrderRef)
                common = dict(BrokerID="9999", InvestorID="account", TradingDay="20261106",
                              ExchangeID="CFFEX", InstrumentID=field.InstrumentID,
                              OrderRef=field.OrderRef, OrderSysID="sys-" + suffix, Direction="0")
                self.client._spi.OnRtnOrder(SimpleNamespace(
                    **common, FrontID=7, SessionID=19, CombOffsetFlag="0", OrderStatus="3",
                    OrderSubmitStatus="3", OrderPriceType="2", LimitPrice=10.0,
                    VolumeTotalOriginal=2, VolumeTraded=0, VolumeTotal=2))
                return result

            def _reply(self, kind, rows, request_id):
                for row in rows:
                    self.client._handle_query_callback(kind, row, None, request_id, False)
                self.client._handle_query_callback(kind, None, None, request_id, True)
                return 0

            def ReqQryTradingAccount(self, field, request_id):
                return self._reply("account", [dict(AccountID="account", BrokerID="9999",
                                   CurrencyID="CNY", Balance=1000, Available=800)], request_id)

            def ReqQryInvestorPosition(self, field, request_id):
                return self._reply("positions", [], request_id)

            def ReqQryOrder(self, field, request_id):
                return self._reply("orders", [], request_id)

            def ReqQryTrade(self, field, request_id):
                self.calls.append("return_query")
                return self._reply("trades", [], request_id)

        def start(trader, **_kwargs):
            native = Native()
            native.client = trader
            trader._api = native
            _login(trader, native, session_id=19)
            natives.append(native)

        def stop(trader, *, timeout):
            return CtpNativeStopReceipt(trader.connection_generation, False, True, True, False,
                                        False)

        class FakeMd(_SyntheticMdClient):
            instances = []
            _gateway_md_dispatch_coordinator = None

            def _bind_gateway_md_dispatch_coordinator(self, coordinator, *, account_scope,
                                                      service_generation):
                if (not coordinator.matches_owner(account_scope, service_generation)
                        or self._gateway_md_dispatch_coordinator is not None):
                    raise ValueError("ctp_gateway_dispatch_owner_mismatch")
                self._gateway_md_dispatch_coordinator = coordinator
                parent = getattr(super(), "_bind_gateway_md_dispatch_coordinator", None)
                if callable(parent):
                    parent(coordinator, account_scope=account_scope,
                           service_generation=service_generation)

            def __init__(self, *args, **kwargs):
                self.trusted_flow_binding = flow_binding(
                    trusted_flow(kwargs["trusted_flow_root"], kwargs["flow_account_scope"]))
                self.last_stop_receipt = None
                super().__init__(*args)

        TraderClient.start, TraderClient.stop_and_wait = start, stop
        live_ctp_feed.ctp_client.MdClient = FakeMd
        params = dict(broker_id="9999", user_id="account", password="synthetic",
                      td_front="tcp://offline-neutral-td.invalid:30001",
                      md_front="tcp://offline-neutral-md.invalid:30011",
                      ctp_env_profile="config_front_pair", ctp_environment="simnow",
                      trusted_flow_root=str(root / "flow"), flow_account_scope=SCOPE)

        def feed():
            result = CtpRequestDataFuture(None, auto_settlement_confirm=False, **params)
            result._connect_timeout = 0.05
            return result

        def stream(key, q):
            del key
            return CtpMarketStream(q, **params)

        raw = _service_config(rpc).to_public_dict()
        raw["service_id"] = GATEWAY.actor_ref
        gateway = raw["components"]["gateway"]["settings"]
        gateway["bind_ports"] += ["service.account_read", "service.market_source"]
        gateway["options"].update(dispatch_enabled=True, cancel_enabled=True, control_enabled=True,
                                  strategy_ledger_enabled=True, account_extensions_enabled=True,
                                  delegation_enabled=True)
        transport = raw["communication"]["profiles"]["server-transport"]
        transport["endpoints"]["market_bind"] = market
        transport.setdefault("options", {})["market_wire_version"] = wire_version
        account_scope = WireScope(DOMAIN, account_ref=SCOPE)
        template = WireScope(DOMAIN, account_ref=SCOPE, strategy_ref=STRATEGY.actor_ref)
        control_ops = support.CONTROL_OPS
        delegate_ops = frozenset({"direct.delegated_reserve", "direct.delegated_dispatch"}) | ACTOR_OPERATIONS

        def grant(port, scope, operations):
            return ResourceGrant("gateway-a", port, "scope-a", scope, operations)

        grants = {
            STRATEGY.principal_ref: (PrincipalGrant(STRATEGY, (
                grant("service.direct_write", template,
                      frozenset({"direct.reserve", "direct.dispatch", "direct.command_status"})),)),),
            RISK.principal_ref: (PrincipalGrant(RISK, (
                grant("service.direct_write", template, delegate_ops),
                grant("service.account_read", account_scope, ACCOUNT_OBSERVER_OPERATIONS),
                grant("service.market_source", account_scope, MARKET_OPS))),),
            CONTROL.principal_ref: (PrincipalGrant(CONTROL, (
                grant("service.direct_write", account_scope, control_ops),
                grant("service.account_read", account_scope,
                      frozenset({"account.snapshot.typed"})))),),
        }
        authority = TrustedServiceAuthority(
            identity=GATEWAY, scope=WireScope(DOMAIN), scope_ref="scope-a",
            scope_version="scope-v1", security_ref="security-a", security_version="security-v1",
            registration_policy_ref="direct-policy-v1",
            registration_policy_version="direct-policy-v1",
            service_requirements_ref="direct-requirements-v1",
            service_requirements_version="direct-requirements-v1",
            curve_public_key=server_keys[0], curve_secret_key=server_keys[1],
            principals=publics, grants=grants)
        settings = CtpGatewayTradingSettings(
            ACCOUNT, SCOPE, root / "guard", "risk-gate-owner", root / "orders.sqlite", journal_id,
            0, root / "flow", SOURCE, ENTITLEMENT, write_enabled=True,
            maintenance_interval_seconds=0.05, connect_timeout_seconds=2, close_timeout_seconds=5)
        keys = tuple(_key(symbol) for symbol in SYMBOLS)
        read = CtpGatewayAccountReadSettings(
            root / "account.sqlite", "account-state", (RESOURCE,), root / "identity.sqlite",
            issuer_id, tuple(key.instrument for key in keys), refresh_interval_seconds=0.2,
            max_staleness_seconds=3, request_timeout_seconds=2)
        logical = frozenset({"direct.reserve", "direct.dispatch", "direct.command_status",
                             "direct.cancel", "direct.cancel_status", "direct.release"})
        profile = build_ctp_gateway_trading_profile(
            parse_service_config(raw), authority, root / "registry.sqlite",
            profile_ref="risk-market-gate", settings=settings,
            grants={STRATEGY.principal_ref: GatewayDirectGrant(STRATEGY.actor_ref, frozenset(SYMBOLS))},
            request_feed_factory=feed, market_stream_factory=stream, assert_admitted=lambda _: True,
            return_settings=CtpGatewayReturnSettings(tuple(key.instrument for key in keys),
                                                     query_interval_seconds=1),
            account_read_settings=read,
            account_read_principals=frozenset({RISK.principal_ref, CONTROL.principal_ref}),
            market_settings=CtpGatewayMarketSettings(frozenset(keys),
                                                     {RISK.principal_ref: frozenset(keys)}),
            control_grants={CONTROL.principal_ref: GatewayControlGrant(
                CONTROL.actor_ref, True, frozenset(), control_ops, kind="risk",
                modes=frozenset({"STOP_NEW", "FREEZE"}), instrument_scope=True)},
            upgrade_controls_from_v9=True, upgrade_strategy_ledger_from_v10=True,
            upgrade_account_extensions_from_v11=True,
            clock_verifier_factory=lambda: clock.ClockVerifier(servers=("synthetic",)),
            clock_allowed_servers=frozenset({"synthetic"}),
            delegation_settings=GatewayDelegationSettings(
                (GatewayDelegateGrant(RISK, SCOPE, STRATEGY.actor_ref, DOMAIN, "risk", "risk-v1",
                                      frozenset(key.instrument for key in keys), logical),),
                lambda _: True),
        )
        deployment = profile.deployment_factory()
        deployment.start()
        events.put(("ready", os.getpid()))
        while True:
            try:
                action = controls.get_nowait()
            except queue.Empty:
                action = None
            if action == "stop":
                break
            if action == "stats":
                events.put(("stats", 0 if not natives else natives[0].calls.count("insert"),
                            bool(settled)))
            if isinstance(action, tuple) and action[0] == "tick":
                FakeMd.instances[0].emit_tick(
                    InstrumentID=action[1], ExchangeID="CFFEX", LastPrice=4005.0, Volume=2)
                events.put(("tick", action[1], time.time_ns()))
            deployment.serve_once(10)
    except BaseException:
        events.put(("error", traceback.format_exc()))
    finally:
        try:
            if deployment is not None and deployment.state in {"started", "close_failed"}:
                deployment.close()
            events.put(("closed", 1))
        except BaseException:
            events.put(("cleanup_error", traceback.format_exc()))


def _risk_files(root, keys, rpc, market, risk_endpoint):
    key_dir = root / "keys"
    key_dir.mkdir()
    for name, (public, secret) in keys.items():
        (key_dir / (name + ".public")).write_bytes(public + b"\n")
        secret_path = key_dir / (name + ".secret")
        secret_path.write_bytes(secret + b"\n")
        os.chmod(secret_path, 0o600)
    (root / "risk_policy.yaml").write_text(
        "schema: bt_api_risk.policy.v1\npolicy_id: it-64-market-gate\n"
        "account: {route: CTP___FUTURE, account_scope: " + SCOPE + "}\n"
        "per_order:\n  max_quantity: {strategy-a: {IF2612: \"2\", IH2612: \"2\"}}\n"
        "self_trade: {enabled: false, on_unverifiable: deny}\n",
        encoding="utf-8")
    instruments = {symbol: {"venue": "CFFEX", "native_symbol": symbol, "product_type": "future",
                            "multiplier": "300", "tick_size": "0.2"} for symbol in SYMBOLS}
    config = {
        "schema": "bt_api_risk.service.v1",
        "service": {"service_id": RISK_HOST.actor_ref, "principal_ref": RISK_HOST.principal_ref,
                    "instance_id": "risk-a", "scope_ref": "risk-scope", "rpc_bind": risk_endpoint,
                    "public_key_file": "keys/risk_server.public",
                    "secret_key_file": "keys/risk_server.secret"},
        # Only the hashed account reference: no account or sub-account number (section 6.1).
        "account": {"route": "CTP___FUTURE", "provider": "ctp", "environment": "simnow",
                    "account_scope": SCOPE, "execution_domain_ref": DOMAIN,
                    "instruments": instruments},
        "storage": {"directory": "state"},
        "policy": {"file": "risk_policy.yaml"},
        "principals": [
            {"principal_ref": STRATEGY.principal_ref, "role": "strategy",
             "actor_ref": STRATEGY.actor_ref, "strategy_refs": [STRATEGY.actor_ref],
             "operations": list(support.RISK_ORDER_OPS),
             "public_key_file": "keys/strategy-0.public"},
            {"principal_ref": support.OPERATOR.principal_ref, "role": "operator",
             "actor_ref": support.OPERATOR.actor_ref, "strategy_refs": [],
             "operations": list(support.OPERATOR_OPS), "public_key_file": "keys/operator.public"},
        ],
        "gateway": {
            "service_id": GATEWAY.actor_ref, "principal_ref": GATEWAY.principal_ref,
            "instance_id": "gateway-a", "scope_ref": "scope-a", "rpc_endpoint": rpc,
            "market_endpoint": market,
            "server_public_key_file": "keys/gateway_server.public",
            "delegate": {"principal_ref": RISK.principal_ref, "actor_ref": RISK.actor_ref,
                         "public_key_file": "keys/risk_delegate.public",
                         "secret_key_file": "keys/risk_delegate.secret",
                         "grant_revisions": {STRATEGY.actor_ref: "risk-v1"}},
            "control": {"principal_ref": CONTROL.principal_ref, "actor_ref": CONTROL.actor_ref,
                        "public_key_file": "keys/risk_control.public",
                        "secret_key_file": "keys/risk_control.secret"},
            "market": {"source_ref": SOURCE, "entitlement_ref": ENTITLEMENT, "channel": "tick",
                       "instruments": list(SYMBOLS)},
            "account_snapshot": {"resources": [{"product_type": "future",
                                                "margin_pool": "futures"}]},
        },
        "deadlines": {"call_timeout_seconds": 5, "reservation_ttl_seconds": 20,
                      "dispatch_recheck_seconds": 1},
        "refresh": {"loop_timeout_ms": 5, "observation_poll_seconds": "0.1",
                    "maintain_seconds": 1, "dispatch_recheck_limit": 16,
                    "market_poll_seconds": "0.05", "account_snapshot_seconds": 1,
                    "account_snapshot_max_age_seconds": 30},
    }
    assert "9999" not in json.dumps(config) and "account\"" not in json.dumps(config["account"])
    path = root / "risk_service.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _risk_client(risk_endpoint, keys):
    from bt_api_py.risk_service_client import RiskClientRoute, RiskServiceClient
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys

    config = parse_client_config({
        "client_config_schema_version": 1, "process_role": "strategy_client",
        "registration": {"strategy_ref": STRATEGY.actor_ref},
        "clients": {"risk": {"config_schema_version": 1, "settings": {
            "bindings": [{"mount": "broker.risk_order", "service_id": RISK_HOST.actor_ref,
                          "instance_id": "risk-a", "scope_ref": "risk-scope", "account_ref": SCOPE,
                          "transport_ref": "risk-transport", "required": True}],
            "options": {"request_timeout_ms": 20000}}}},
        "communication": {"config_schema_version": 1, "profiles": {"risk-transport": {
            "backend": "zmq", "config_schema_version": 1,
            "endpoints": {"rpc_connect": risk_endpoint}, "security_profile_ref": "risk-curve"}}},
    })
    scope = WireScope(DOMAIN, account_ref=SCOPE, strategy_ref=STRATEGY.actor_ref, run_id=RUN)
    return RiskServiceClient.compose(
        config, route=RiskClientRoute("broker.risk_order", RISK_HOST.actor_ref, "risk-a",
                                      "risk-scope", scope, STRATEGY),
        peer=RISK_HOST,
        security_profiles={"risk-curve": ClientCurveKeys(keys["risk_server"][0], *keys["strategy-0"])},
        requested_operations=frozenset(support.RISK_ORDER_OPS))


def _order(key, symbol, *, offset="open", side="BUY"):
    from decimal import Decimal

    from bt_api_py._contracts.models import OrderRequest, OrderType, Side

    return OrderRequest(symbol=symbol, side=Side[side], order_type=OrderType.LIMIT,
                        quantity=Decimal("2"), price=Decimal("4005"), account_id=SCOPE,
                        client_order_id="c-" + key, idempotency_key=key, offset=offset,
                        quantity_unit="contracts", exchange_id="CFFEX", time_in_force="GFD")


def _command(root, request_key):
    with sqlite3.connect("file:" + str(root / "orders.sqlite") + "?mode=ro", uri=True) as db:
        return db.execute("SELECT status FROM direct_journal_commands WHERE strategy=? AND "
                          "request_key=?", (STRATEGY.actor_ref, request_key)).fetchall()


def _next(events, kind, timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        item = events.get(timeout=max(0.01, deadline - time.monotonic()))
        if item[0] in ("error", "cleanup_error"):
            raise AssertionError(item[1])
        if item[0] == kind:
            return item


@pytest.fixture
def market_gateway(tmp_path, request):
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

    from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer
    from tests.integration.test_service_account_instrument_metadata import _free_endpoint

    gateway_root = tmp_path / "gateway"
    (gateway_root / "guard").mkdir(parents=True)
    (gateway_root / "flow").mkdir(mode=0o700)
    journal = GatewayDirectCommandJournal.create(gateway_root / "orders.sqlite", SCOPE,
                                                 generation="provisioned")
    issuer = DurableOrderCorrelationIssuer(gateway_root / "identity.sqlite")
    issuer_id = issuer.issuer_id
    issuer.close()
    keys = support.keypairs(1)
    publics = {keys["strategy-0"][0]: STRATEGY.principal_ref,
               keys["risk_delegate"][0]: RISK.principal_ref,
               keys["risk_control"][0]: CONTROL.principal_ref}
    rpc, market, risk_endpoint = _free_endpoint(), _free_endpoint(), _free_endpoint()
    context = multiprocessing.get_context("spawn")
    events, controls = context.Queue(), context.Queue()
    child = context.Process(target=_gateway, args=(
        str(gateway_root), rpc, market, keys["gateway_server"], publics, journal.journal_id,
        issuer_id, getattr(request, "param", 1), events, controls))
    child.start()
    state = dict(root=gateway_root, risk_root=tmp_path / "risk", keys=keys, rpc=rpc,
                 market=market, risk_endpoint=risk_endpoint, events=events, controls=controls,
                 journal_id=journal.journal_id)
    try:
        assert _next(events, "ready", 40)[1] == child.pid
        yield state
    finally:
        controls.put("stop")
        child.join(20)
        if child.is_alive():
            child.terminate()
            child.join(5)
        remaining = []
        while True:
            try:
                remaining.append(events.get_nowait())
            except queue.Empty:
                break
        assert not any(item[0] in ("error", "cleanup_error") for item in remaining), remaining
    assert child.exitcode == 0


def test_risk_dispatch_passes_the_gateway_market_gate_with_its_own_market_ref(market_gateway):
    state = market_gateway
    events, controls = state["events"], state["controls"]

    def stats():
        controls.put("stats")
        return _next(events, "stats")

    # The Gateway reservation fence opens after its first account/return reads.
    support.until(stats, lambda item: item[2], seconds=20)
    state["risk_root"].mkdir()
    config = _risk_files(state["risk_root"], state["keys"], state["rpc"], state["market"],
                         state["risk_endpoint"])
    risk = client = operator = strategy = None
    try:
        # G64-11: the Gateway registers the Risk delegate only while the strategy holds a
        # live write claim for this run (a session registered with its write operations).
        strategy = support.strategy_session(
            state["rpc"], state["keys"], state["journal_id"], run_id=RUN, account=SCOPE,
            peer=GATEWAY, requested_operations=frozenset(
                {"direct.reserve", "direct.dispatch", "direct.command_status"}))
        assert strategy.start(timeout_seconds=15).healthy
        risk = support.start_risk(config, state["risk_root"] / "ready.json")
        client = _risk_client(state["risk_endpoint"], state["keys"])
        assert client.start(timeout_seconds=15).healthy
        operator = support.operator_runtime(state["risk_endpoint"], state["keys"], account=SCOPE)
        assert operator.start(timeout_seconds=15).healthy

        def admin(operation, arguments=None):
            from bt_api_py.gateway_execution_stream_client import _detach_wire

            return _detach_wire(operator.call(
                "application.risk_admin", operation, arguments or {}, account_ref=SCOPE,
                scope_ref="risk-scope", timeout_seconds=10))

        def answer(result):
            assert result.outcome == "ANSWERED", result
            return result.answer

        def place(key, symbol):
            reserved = answer(client.reserve(_order(key, symbol), request_key=key,
                                             timeout_seconds=20))
            assert reserved.outcome == "RESERVED", reserved
            return answer(client.dispatch(key, permit_id=reserved.permit_id,
                                          reservation_id=reserved.reservation_id,
                                          view_digest=reserved.view_digest, timeout_seconds=20))

        # A fresh tick of Risk's own subscription: Risk now knows the trading day and holds
        # a market reference it has had the Gateway attest (market.clock_observation).
        controls.put(("tick", "IF2612"))
        _next(events, "tick")
        status = support.until(lambda: admin("risk.admin.status"),
                               lambda r: r["summary"]["observation"]["continuity"] == "VERIFIED"
                               and r["summary"]["counts"] is not None, seconds=20)
        assert status["summary"]["observation"]["continuity"] == "VERIFIED", status

        # Fresh: the dispatch carries Risk's own market_ref and the Gateway sends it.
        controls.put(("tick", "IF2612"))
        _next(events, "tick")
        time.sleep(0.3)  # one Risk market poll and its clock attestation
        fresh = place("fresh-1", "IF2612")
        assert (fresh.outcome, fresh.state) == ("DISPATCHED", "DISPATCHED"), fresh
        sent = support.until(lambda: _command(state["root"], "fresh-1"),
                             lambda rows: rows and rows[0][0] == "SENT")
        assert sent == [("SENT",)], sent
        assert stats()[1] == 1  # one native send

        # Stale: the last Risk tick is older than the gate's 2 s quote age limit.
        time.sleep(2.5)
        stale = place("stale-1", "IF2612")
        # Refused by the gate before the owner queue: not a NOT_SENT proof (freeze 5),
        # so Risk keeps the worst-case occupancy and reports UNKNOWN, never DENIED.
        assert (stale.outcome, stale.state) == ("UNKNOWN", "UNKNOWN"), stale
        assert stale.gateway_code == "MARKET_GATE_UNAVAILABLE", stale

        # Absent: Risk never received a tick of IH2612.
        absent = place("absent-1", "IH2612")
        assert (absent.outcome, absent.state) == ("UNKNOWN", "UNKNOWN"), absent
        assert absent.gateway_code == "MARKET_GATE_UNAVAILABLE", absent

        # Neither is sent, resent or released without proof over several maintain cycles.
        time.sleep(3)
        for key in ("stale-1", "absent-1"):
            assert _command(state["root"], key) == [("RESERVED",)], key
            view = answer(client.status(key, timeout_seconds=10))
            assert (view.outcome, view.state) == ("UNKNOWN", "UNKNOWN"), view
            # The same identity asked again is not dispatched again.
            again = answer(client.dispatch(key, permit_id=view.permit_id,
                                           reservation_id=view.reservation_id,
                                           view_digest=view.view_digest, timeout_seconds=20))
            assert again.outcome == "UNKNOWN", again
        assert stats()[1] == 1
        unknown = sorted(row["request_key"] for row in admin("risk.unknown.list")["permits"])
        assert unknown == ["absent-1", "stale-1"], unknown
    finally:
        for item in (client, operator, strategy):
            if item is not None:
                item.close(timeout_seconds=5)
        exit_code = support.stop_risk(risk)
    assert exit_code == 0, risk.communicate(timeout=10) if risk is not None else None


def test_risk_host_reads_the_snapshot_of_a_subaccount_key_by_its_hashed_reference(market_gateway):
    """Plan 64 section 6.1: no account number in the Risk configuration, snapshot still read.

    The Risk host runs in this process (its owner thread is this thread) so that
    its account source can be inspected; the Gateway is the spawned CTP profile
    whose account key carries the sub-account ``9999``.
    """
    from bt_api_py.risk_service_config import load_risk_service_config
    from bt_api_py.risk_service_host import RiskServiceHost

    state = market_gateway
    state["controls"].put("stats")
    support.until(lambda: _next(state["events"], "stats"), lambda item: item[2], seconds=20)
    state["risk_root"].mkdir()
    path = _risk_files(state["risk_root"], state["keys"], state["rpc"], state["market"],
                       state["risk_endpoint"])
    text = path.read_text(encoding="utf-8")
    assert ACCOUNT.account_id not in json.loads(text)["account"].values()
    assert ACCOUNT.subaccount_id not in text
    host = RiskServiceHost(load_risk_service_config(path))
    host.start()
    try:
        assert host.account.refresh() is True
        snapshot = host.facts.account_snapshot()
        # The observation has no Risk-bound expiry, so reads cannot be used for admission.
        assert snapshot is not None and snapshot.complete is False
        assert snapshot.valid_until_ns is None
        assert snapshot.balance is not None and snapshot.positions == {}
        assert host.account.failures == 0
    finally:
        host.close()
