"""R21 offline CTP profile over a spawned CURVE Gateway.

Only native TD/MD are synthetic. This is not evidence of a venue connection,
account execution, or Backtrader Broker/Store integration.
"""
from __future__ import annotations

import multiprocessing
import os
import queue
import sys
import time
import traceback
from pathlib import Path

import pytest
import zmq
from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_py._contracts.models import OrderRequest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_strategy_account import GatewayAccountClientSettings
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.gateway_strategy_market import GatewayMarketClientSettings, GatewayMarketSubscriptionSettings
from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_runtime import ClientCurveKeys
from tests.integration.test_iter63_gateway_direct_service import (
    CLIENT_A, CLIENT_B, DOMAIN, OPS, SERVICE, _client_config, _service_config,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_direct_client import MemoryHost
from tests.integration.test_iter63_gateway_cancel_rpc import CancelHost, OPS as WRITE_OPS
from bt_api_py.gateway_execution_stream_service import STREAM_OPERATIONS
DIRECT_OPS = WRITE_OPS | STREAM_OPERATIONS
from tests.unit.test_iter63_gateway_direct_service import _order

CLIENT_C = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-c", "strategy-c")
ACCOUNT = AccountKey("ctp", "simnow", "simnow", "account", "9999")
SCOPE = ACCOUNT.account_key_id
SOURCE = "ctp-native-market-r1"
ENTITLEMENT = "ctp-quote-v2-entitlement-r1"
RESOURCE = AccountResourceKey("future", "futures")
MARKET_OPS = frozenset({"market.subscribe", "market.unsubscribe", "market.snapshot", "market.head", "market.read_after", "market.bootstrap_begin", "market.bootstrap_status", "market.bootstrap_cancel"})
ACCOUNT_OPS = frozenset({"account.snapshot.typed"})


def _key(symbol):
    return MarketSubscriptionKey(SOURCE, ENTITLEMENT, MarketInstrumentKey(
        "ctp", "CFFEX", "simnow", "future", symbol, symbol), "tick")


def _gateway(root, rpc, market, server_keys, client_keys, journal_id, issuer_id, events, controls, blocked_start=None, blocked_poll=None, blocked_stop=None):
    """Runs the actual profile and CTP Python providers in the spawned process."""
    root = Path(root)
    deployment = None
    try:
        ctp_tests = Path(__file__).resolve().parents[2] / "bt_api" / "bt_api_ctp" / "tests"
        sys.path.insert(0, str(ctp_tests))
        from test_iter62_settlement_readback_on_write import _NativeApi, _login
        from test_market_provider import _SyntheticMdClient
        from bt_api_ctp.ctp.client import TraderClient, CtpNativeStopReceipt
        from bt_api_ctp.feeds import live_ctp_feed
        from bt_api_ctp.feeds.live_ctp_feed import CtpMarketStream, CtpRequestDataFuture
        from bt_api_ctp.trusted_flow import flow_binding, trusted_flow
        from bt_api_ctp.market_provider import CtpQuoteV2MarketProvider
        if blocked_poll is not None:
            armed, poll_entered, poll_release, dequeued, maintenance_entered = blocked_poll
            from bt_api_py.gateway_owner_market import GatewayOwnerMarketProvider
            original_reset_check = GatewayOwnerMarketProvider.source_epoch_reset_required
            def signal_maintenance(self, handle):
                if poll_entered.is_set() and not poll_release.is_set():
                    maintenance_entered.set()
                return original_reset_check(self, handle)
            GatewayOwnerMarketProvider.source_epoch_reset_required = signal_maintenance
            original_poll = CtpQuoteV2MarketProvider.poll
            def hold_dequeued(self, handle, *, limit):
                result = original_poll(self, handle, limit=limit)
                if result and armed.is_set():
                    armed.clear()
                    with dequeued.get_lock():
                        dequeued.value += len(result)
                    poll_entered.set()
                    if not poll_release.wait(8):
                        raise TimeoutError("synthetic MD poll was not released")
                return result
            CtpQuoteV2MarketProvider.poll = hold_dequeued
        from bt_api_py.ctp_gateway_trading import (CtpGatewayTradingSettings,
            CtpGatewayAccountReadSettings, CtpGatewayMarketSettings, CtpGatewayReturnSettings,
            build_ctp_gateway_trading_profile)
        from bt_api_py.gateway_direct_service import GatewayDirectGrant
        from bt_api_py.service_host import TrustedServiceAuthority
        from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

        natives, feeds, streams, stops, settled, return_owners = [], [], [], [], [], []
        from bt_api_py.ctp_gateway_return_maintenance import CtpGatewayReturnMaintenance
        original_return_init = CtpGatewayReturnMaintenance.__init__
        def trace_return_init(self, *args, **kwargs):
            original_return_init(self, *args, **kwargs)
            return_owners.append(self)
        CtpGatewayReturnMaintenance.__init__ = trace_return_init
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
                from types import SimpleNamespace
                result = super().ReqOrderInsert(field, request_id)
                suffix = str(field.OrderRef)
                common = dict(BrokerID="9999", InvestorID="account", TradingDay="20261106",
                    ExchangeID="CFFEX", InstrumentID=field.InstrumentID, OrderRef=field.OrderRef,
                    OrderSysID="sys-" + suffix, Direction="0")
                order = SimpleNamespace(**common, FrontID=7, SessionID=19, CombOffsetFlag="0",
                    OrderStatus="1", OrderSubmitStatus="3", OrderPriceType="2", LimitPrice=10.0,
                    VolumeTotalOriginal=2, VolumeTraded=1, VolumeTotal=1)
                trade = SimpleNamespace(**common, TradeID="trade-" + suffix, OffsetFlag="0",
                    Volume=1, Price=10.0, TradeTime="09:30:00")
                self.client._spi.OnRtnOrder(order)
                self.client._spi.OnRtnTrade(trade)
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
            stops.append(1)
            return CtpNativeStopReceipt(trader.connection_generation, False, True, True, False, False)

        class FakeMd(_SyntheticMdClient):
            instances = []
            _gateway_md_dispatch_coordinator = None

            def _bind_gateway_md_dispatch_coordinator(self, coordinator, *, account_scope,
                                                      service_generation):
                # Same contract as the real MdClient: bind once, exact owner only.
                if (not coordinator.matches_owner(account_scope, service_generation)
                        or self._gateway_md_dispatch_coordinator is not None):
                    raise ValueError("ctp_gateway_dispatch_owner_mismatch")
                self._gateway_md_dispatch_coordinator = coordinator
                # Keep observers that patch the synthetic parent hook (MD claim
                # binding test) in the chain instead of shadowing them.
                parent = getattr(super(), "_bind_gateway_md_dispatch_coordinator", None)
                if callable(parent):
                    parent(coordinator, account_scope=account_scope,
                           service_generation=service_generation)

            def __init__(self, *args, **kwargs):
                self.trusted_flow_binding = flow_binding(trusted_flow(kwargs["trusted_flow_root"], kwargs["flow_account_scope"]))
                self.last_stop_receipt = None
                super().__init__(*args)

            def start(self, *, block=False, join_observer=True):
                if blocked_start is not None:
                    entered, release = blocked_start
                    entered.set()
                    if not release.wait(8):
                        raise TimeoutError("synthetic MD start was not released")
                return super().start(block=block, join_observer=join_observer)

            def stop_and_wait(self, *, timeout):
                if blocked_stop is not None and blocked_stop[0].is_set():
                    blocked_stop[0].clear()
                    blocked_stop[1].set()
                    if not blocked_stop[2].wait(8):
                        raise TimeoutError("synthetic MD close was not released")
                self.last_stop_receipt = super().stop_and_wait(timeout=timeout)
                return self.last_stop_receipt

        TraderClient.start, TraderClient.stop_and_wait = start, stop
        live_ctp_feed.ctp_client.MdClient = FakeMd
        params = dict(broker_id="9999", user_id="account", password="synthetic",
            td_front="tcp://offline-neutral-td.invalid:30001", md_front="tcp://offline-neutral-md.invalid:30011",
            ctp_env_profile="config_front_pair", ctp_environment="simnow",
            trusted_flow_root=str(root / "flow"), flow_account_scope=SCOPE)

        def feed():
            result = CtpRequestDataFuture(None, auto_settlement_confirm=False, **params)
            result._connect_timeout = 0.05
            feeds.append(result)
            return result

        def stream(key, q):
            del key
            result = CtpMarketStream(q, **params)
            streams.append(result)
            return result

        raw = _service_config(rpc).to_public_dict()
        gateway = raw["components"]["gateway"]["settings"]
        gateway["bind_ports"] += ["service.account_read", "service.market_source"]
        gateway["options"].update(dispatch_enabled=True, cancel_enabled=True)
        raw["communication"]["profiles"]["server-transport"]["endpoints"]["market_bind"] = market
        raw["communication"]["profiles"]["server-transport"].setdefault("options", {})["market_wire_version"] = 2
        identities = (CLIENT_A, CLIENT_B, CLIENT_C)
        grants = {}
        for identity in identities:
            wire = WireScope(DOMAIN, account_ref=SCOPE, strategy_ref=identity.actor_ref)
            selected = [("service.direct_write", DIRECT_OPS if identity != CLIENT_C else OPS)]
            if identity != CLIENT_C:
                selected += [("service.account_read", ACCOUNT_OPS), ("service.market_source", MARKET_OPS)]
            resources = tuple(ResourceGrant("gateway-a", port, "scope-a", wire, ops) for port, ops in selected)
            grants[identity.principal_ref] = (PrincipalGrant(identity, resources),)
        authority = TrustedServiceAuthority(
            identity=SERVICE, scope=WireScope(DOMAIN), scope_ref="scope-a", scope_version="scope-v1",
            security_ref="security-a", security_version="security-v1",
            registration_policy_ref="direct-policy-v1", registration_policy_version="direct-policy-v1",
            service_requirements_ref="direct-requirements-v1", service_requirements_version="direct-requirements-v1",
            curve_public_key=server_keys[0], curve_secret_key=server_keys[1],
            principals={pair[0]: identity.principal_ref for pair, identity in zip(client_keys, identities)}, grants=grants)
        settings = CtpGatewayTradingSettings(ACCOUNT, SCOPE, root / "guard", "joint-owner",
            root / "orders.sqlite", journal_id, 0, root / "flow", SOURCE, ENTITLEMENT,
            write_enabled=True, maintenance_interval_seconds=0.05,
            # A test that holds the native MD start must outlast the provider's
            # authentication wait, or the provider abandons and stops that start.
            connect_timeout_seconds=2 if blocked_start is None else 10, close_timeout_seconds=5)
        keys = (_key("IF2612"), _key("IH2612"))
        read = CtpGatewayAccountReadSettings(root / "account.sqlite", "account-state", (RESOURCE,),
            root / "identity.sqlite", issuer_id, tuple(key.instrument for key in keys),
            refresh_interval_seconds=0.2, max_staleness_seconds=3, request_timeout_seconds=2)
        profile = build_ctp_gateway_trading_profile(parse_service_config(raw), authority, root / "registry.sqlite",
            profile_ref="offline-joint", settings=settings,
            grants={identity.principal_ref: GatewayDirectGrant(identity.actor_ref, frozenset({"IF2612", "IH2612"})) for identity in identities},
            request_feed_factory=feed, market_stream_factory=stream, assert_admitted=lambda _: True,
            return_settings=CtpGatewayReturnSettings(tuple(key.instrument for key in keys), query_interval_seconds=1),
            account_read_settings=read, account_read_principals=frozenset(i.principal_ref for i in (CLIENT_A, CLIENT_B)),
            market_settings=CtpGatewayMarketSettings(frozenset(keys), {
                CLIENT_A.principal_ref: frozenset({keys[0]}), CLIENT_B.principal_ref: frozenset({keys[1]})}))
        assert not feeds and not natives and not streams
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
                md = FakeMd.instances[0] if FakeMd.instances else None
                events.put(("stats", len(natives), len(feeds), len(streams), len(FakeMd.instances),
                    () if md is None else tuple(md.subscribed), 0 if md is None else md.stop_wait_calls,
                    0 if not natives else natives[0].calls.count("insert"),
                    0 if not natives else natives[0].calls.count("cancel"),
                    bool(settled), 0 if not natives else natives[0].calls.count("return_query"),
                    {} if not return_owners else return_owners[0].status(),
                    bool(md is not None and md.last_stop_receipt is not None
                        and md.last_stop_receipt.complete and md.last_stop_receipt.native_released)))
            if isinstance(action, tuple) and action[0] == "tick":
                symbol = action[1]
                FakeMd.instances[0].emit_tick(InstrumentID=symbol, ExchangeID="CFFEX", LastPrice=4005.0,
                    Volume=2, TradingDay="20261020", ActionDay="20261020", UpdateTime="09:30:02", UpdateMillisec=0)
                events.put(("tick", symbol))
            deployment.serve_once(10)
    except BaseException:
        events.put(("error", traceback.format_exc()))
    finally:
        try:
            if deployment is not None and deployment.state in {"started", "close_failed"}:
                deployment.close()
            events.put(("closed", len(locals().get("stops", []))))
        except BaseException:
            events.put(("cleanup_error", traceback.format_exc()))


def _session(identity, keys, server_public, rpc, market, journal_id, symbol, root, run_suffix="", heartbeat_interval_seconds=None):
    raw = _client_config(rpc, identity).to_public_dict()
    if heartbeat_interval_seconds is not None:
        raw["clients"]["gateway"]["settings"]["options"]["heartbeat_interval_seconds"] = heartbeat_interval_seconds
    bindings = raw["clients"]["gateway"]["settings"]["bindings"]
    bindings[0]["account_ref"] = SCOPE
    account = dict(bindings[0], mount="store.account")
    market_binding = dict(bindings[0], mount="store.market")
    del market_binding["account_ref"]
    bindings += [account, market_binding]
    raw["communication"]["profiles"]["client-transport"]["endpoints"]["market_connect"] = market
    raw["communication"]["profiles"]["client-transport"].setdefault("options", {})["market_wire_version"] = 2
    scope = WireScope(DOMAIN, account_ref=SCOPE, strategy_ref=identity.actor_ref, run_id="run-" + identity.actor_ref + run_suffix)
    subscriptions = {"ticks": GatewayMarketSubscriptionSettings(_key(symbol), require_source_descriptor=True)}
    if identity == CLIENT_A:
        subscriptions["forbidden"] = GatewayMarketSubscriptionSettings(_key("IH2612"))
    return GatewayStrategyClientSession(parse_client_config(raw),
        binding=GatewayCommandBinding(journal_id, SCOPE, identity.actor_ref, identity.principal_ref),
        route=GatewayClientRoute("broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a", scope, identity),
        peer=SERVICE, security_profiles={"security-a": ClientCurveKeys(server_public, *keys)},
        host=MemoryHost(), cancel_host=CancelHost(root / (identity.actor_ref + "-cancel.sqlite")), requested_operations=DIRECT_OPS,
        account_settings=GatewayAccountClientSettings(ACCOUNT, (RESOURCE,), ACCOUNT_OPS),
        market_settings=GatewayMarketClientSettings(subscriptions, MARKET_OPS))


def _until(fn, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        try:
            result = fn()
            if result:
                return result
        except Exception:
            if time.monotonic() >= deadline:
                raise
        assert time.monotonic() < deadline
        time.sleep(0.05)


def test_spawned_real_ctp_session_shares_one_td_and_lazy_md(tmp_path):
    (tmp_path / "guard").mkdir()
    (tmp_path / "flow").mkdir(mode=0o700)
    journal = GatewayDirectCommandJournal.create(tmp_path / "orders.sqlite", SCOPE, generation="provisioned")
    issuer = DurableOrderCorrelationIssuer(tmp_path / "identity.sqlite")
    issuer_id = issuer.issuer_id
    issuer.close()
    rpc, market = _free_endpoint(), _free_endpoint()
    server_keys = zmq.curve_keypair()
    client_keys = (zmq.curve_keypair(), zmq.curve_keypair(), zmq.curve_keypair())
    ctx = multiprocessing.get_context("spawn")
    events, controls = ctx.Queue(), ctx.Queue()
    child = ctx.Process(target=_gateway, args=(str(tmp_path), rpc, market, server_keys, client_keys,
        journal.journal_id, issuer_id, events, controls))
    child.start()
    sessions = []
    context = zmq.Context()
    raw_sub = context.socket(zmq.SUB)
    raw_sub.linger = 0
    from zmq.utils.monitor import recv_monitor_message
    monitor = raw_sub.get_monitor_socket(events=zmq.EVENT_HANDSHAKE_SUCCEEDED | zmq.EVENT_HANDSHAKE_FAILED_AUTH)
    raw_sub.curve_serverkey, raw_sub.curve_publickey, raw_sub.curve_secretkey = server_keys[0], *client_keys[2]
    raw_sub.setsockopt(zmq.SUBSCRIBE, b"")
    raw_sub.connect(market)
    try:
        ready = events.get(timeout=20)
        assert ready == ("ready", child.pid) and child.pid != os.getpid(), ready
        for identity, keys, symbol in zip((CLIENT_A, CLIENT_B), client_keys, ("IF2612", "IH2612")):
            session = _session(identity, keys, server_keys[0], rpc, market, journal.journal_id, symbol, tmp_path)
            assert session.status().state == "new"
            sessions.append(session)
            session.start(timeout_seconds=10)
        controls.put("stats")
        assert events.get(timeout=5)[:7] == ("stats", 1, 1, 0, 0, (), 0)
        for session in sessions:
            view = _until(lambda: session.account_client.refresh(timeout_seconds=5))
            assert view.observation.account_key == ACCOUNT
        markets = [session.market_client.subscription("ticks") for session in sessions]
        descriptors = {}
        for symbol, subscription in zip(("IF2612", "IH2612"), markets):
            bootstrap = subscription.start(timeout_seconds=10)
            descriptor = bootstrap.source_descriptor
            assert descriptor.key == _key(symbol) and descriptor.provider == "ctp"
            assert descriptor.connection_generation == 1 and descriptor.epoch == bootstrap.snapshot_epoch
            descriptors[symbol] = descriptor
        controls.put("stats")
        stats = events.get(timeout=5)
        assert stats[:5] == ("stats", 1, 1, 1, 1), stats
        assert set(stats[5]) == {"IF2612", "IH2612"}
        assert monitor.poll(5000)
        assert recv_monitor_message(monitor)["event"] == zmq.EVENT_HANDSHAKE_FAILED_AUTH
        raw = _client_config(rpc, CLIENT_C).to_public_dict()
        raw["clients"]["gateway"]["settings"]["bindings"][0]["account_ref"] = SCOPE
        c_scope = WireScope(DOMAIN, account_ref=SCOPE, strategy_ref=CLIENT_C.actor_ref, run_id="run-c")
        direct_only = GatewayStrategyClientSession(parse_client_config(raw),
            binding=GatewayCommandBinding(journal.journal_id, SCOPE, CLIENT_C.actor_ref, CLIENT_C.principal_ref),
            route=GatewayClientRoute("broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a", c_scope, CLIENT_C),
            peer=SERVICE, security_profiles={"security-a": ClientCurveKeys(server_keys[0], *client_keys[2])},
            host=MemoryHost(), requested_operations=OPS)
        try:
            direct_only.start(timeout_seconds=10)
            c_payload = _order(key="direct-only", symbol="IF2612", account=SCOPE)
            assert _until(lambda: (result if (result := direct_only.command_client.reserve(
                OrderRequest.from_dict(c_payload), timeout_seconds=5)).outcome == "OBSERVED" else None))
        finally:
            direct_closed = direct_only.close(timeout_seconds=10)
            assert direct_closed.state == "closed" and not direct_closed.thread_alive
        for symbol, subscription in zip(("IF2612", "IH2612"), markets):
            controls.put(("tick", symbol))
            assert events.get(timeout=5) == ("tick", symbol)
            event = _until(lambda: (event if (event := subscription.poll(timeout_seconds=5)) and
                event.payload["data"]["last_price"] == 4005.0 else None))
            assert event.header.scope == WireScope(DOMAIN)
            assert event.header.stream_epoch == descriptors[symbol].epoch
            assert event.payload["data"]["instrument"] == _key(symbol).instrument.to_dict()
            assert event.payload["data"]["provider_connection_generation"] == descriptors[symbol].connection_generation
            assert not raw_sub.poll(300), "direct-only principal received a market frame"
        def owner_ready():
            controls.put("stats")
            status = events.get(timeout=5)
            return status[9] and status[10] > 0 and status[11]["reservation_open"]
        _until(owner_ready)
        for index, (session, symbol) in enumerate(zip(sessions, ("IF2612", "IH2612"))):
            payload = _order(key="joint-order-" + str(index), symbol=symbol, quantity="2", account=SCOPE)
            payload.update(exchange_id="CFFEX", offset="open", time_in_force="GFD", quantity_unit="contracts")
            def reserve():
                result = session.command_client.reserve(OrderRequest.from_dict(payload), timeout_seconds=10)
                assert result.outcome in {"OBSERVED", "REQUEST_DENIED"}, result
                return result if result.outcome == "OBSERVED" else None
            reserved = _until(reserve)
            assert reserved.outcome == "OBSERVED"
            # Decision B: this Gateway has market settings but no clock verifier,
            # so a dispatch without a qualified market reference is refused
            # before the owner queue or a marker; the reservation remains.
            # Qualified dispatch, fills and ACK through this composition:
            # test_iter63_gateway_qualified_order_process; cancel: the cancel RPC suite.
            refused = session.command_client.dispatch("joint-order-" + str(index), timeout_seconds=10)
            assert (refused.outcome, refused.code) == ("REQUEST_DENIED", "MARKET_GATE_UNAVAILABLE")
            current = session.command_client.status("joint-order-" + str(index))
            assert current.record is not None and current.record.state == "RESERVED"
        controls.put("stats")
        assert events.get(timeout=5)[7:9] == (0, 0)  # no native send, no cancel
        from bt_api_py.service_runtime_thread import OwnerOperationError
        from bt_api_py.service_runtime import ServiceRuntimeError
        with pytest.raises(ServiceRuntimeError):
            sessions[0].runtime.call("store.market", "market.subscribe", {"key": _key("IF2612").to_dict()},
                account_ref="wrong-account", timeout_seconds=5)
        with pytest.raises(OwnerOperationError):
            sessions[0].market_client.subscription("forbidden").start(timeout_seconds=5)
        closed_a = sessions[0].close(timeout_seconds=10)
        assert closed_a.state == "closed" and not closed_a.thread_alive
        def first_release():
            controls.put("stats")
            status = events.get(timeout=5)
            assert status[:5] == ("stats", 1, 1, 1, 1), status
            # The first strategy's symbol stays natively subscribed while the
            # other strategy keeps the MD session alive (bt_api_ctp parks a
            # released symbol so that its consumer can come back); the session
            # itself is neither stopped nor restarted.
            return status if {"IF2612", "IH2612"} <= set(status[5]) and status[6] == 0 else None
        _until(first_release)
        controls.put(("tick", "IH2612"))
        assert events.get(timeout=5) == ("tick", "IH2612")
        assert _until(lambda: markets[1].poll(timeout_seconds=5)).header.scope == WireScope(DOMAIN)
        closed_b = sessions[1].close(timeout_seconds=10)
        assert closed_b.state == "closed" and not closed_b.thread_alive
        def last_release():
            controls.put("stats")
            status = events.get(timeout=5)
            assert status[:5] == ("stats", 1, 1, 1, 1), status
            return status if status[6] >= 1 and status[12] else None
        _until(last_release)
    finally:
        raw_sub.close()
        monitor.close()
        context.term()
        close_results = []
        for session in sessions:
            try:
                closed = session.close(timeout_seconds=10)
                close_results.append((closed.state, closed.thread_alive))
            except Exception as exc:
                close_results.append((type(exc).__name__, True))
        controls.put("stop")
        child.join(15)
        if child.is_alive():
            child.terminate()
            child.join(5)
            pytest.fail("synthetic Gateway process failed bounded shutdown")
        remaining = []
        while True:
            try:
                remaining.append(events.get_nowait())
            except queue.Empty:
                break
        assert child.exitcode == 0 and not any(item[0] in {"error", "cleanup_error"} for item in remaining), remaining
        assert ("closed", 1) in remaining, remaining
        assert all(state == "closed" and alive is False for state, alive in close_results), close_results


def test_blocked_native_md_start_retains_one_acquisition(tmp_path):
    from bt_api_py.service_runtime_thread import ThreadedRuntimeError

    (tmp_path / "guard").mkdir()
    (tmp_path / "flow").mkdir(mode=0o700)
    journal = GatewayDirectCommandJournal.create(tmp_path / "orders.sqlite", SCOPE, generation="provisioned")
    issuer = DurableOrderCorrelationIssuer(tmp_path / "identity.sqlite")
    issuer_id = issuer.issuer_id
    issuer.close()
    rpc, market = _free_endpoint(), _free_endpoint()
    server_keys = zmq.curve_keypair()
    client_keys = (zmq.curve_keypair(), zmq.curve_keypair(), zmq.curve_keypair())
    ctx = multiprocessing.get_context("spawn")
    entered, release = ctx.Event(), ctx.Event()
    poll_armed, poll_entered, poll_release, dequeued = ctx.Event(), ctx.Event(), ctx.Event(), ctx.Value("i", 0)
    maintenance_entered = ctx.Event()
    stop_armed, stop_entered, stop_release = ctx.Event(), ctx.Event(), ctx.Event()
    events, controls = ctx.Queue(), ctx.Queue()
    child = ctx.Process(target=_gateway, args=(str(tmp_path), rpc, market, server_keys, client_keys,
        journal.journal_id, issuer_id, events, controls, (entered, release),
        (poll_armed, poll_entered, poll_release, dequeued, maintenance_entered), (stop_armed, stop_entered, stop_release)))
    child.start()
    session = None
    direct_only = None
    try:
        ready = events.get(timeout=20)
        assert ready == ("ready", child.pid), ready
        session = _session(CLIENT_A, client_keys[0], server_keys[0], rpc, market,
            journal.journal_id, "IF2612", tmp_path)
        session.start(timeout_seconds=10)
        raw = _client_config(rpc, CLIENT_C).to_public_dict()
        raw["clients"]["gateway"]["settings"]["bindings"][0]["account_ref"] = SCOPE
        c_scope = WireScope(DOMAIN, account_ref=SCOPE, strategy_ref=CLIENT_C.actor_ref, run_id="run-c")
        direct_only = GatewayStrategyClientSession(parse_client_config(raw),
            binding=GatewayCommandBinding(journal.journal_id, SCOPE, CLIENT_C.actor_ref, CLIENT_C.principal_ref),
            route=GatewayClientRoute("broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a", c_scope, CLIENT_C),
            peer=SERVICE, security_profiles={"security-a": ClientCurveKeys(server_keys[0], *client_keys[2])},
            host=MemoryHost(), requested_operations=OPS)
        direct_only.start(timeout_seconds=10)
        payload = _order(key="liveness", symbol="IF2612", account=SCOPE)
        assert _until(lambda: (result if (result := direct_only.command_client.reserve(
            OrderRequest.from_dict(payload), timeout_seconds=5)).outcome == "OBSERVED" else None))
        subscription = session.market_client.subscription("ticks")
        with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
            subscription.start(timeout_seconds=0.3)
        assert entered.wait(5)
        with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
            subscription.start(timeout_seconds=0.2)
        release.set()
        bootstrap = subscription.start(timeout_seconds=10)
        assert bootstrap.source_descriptor.connection_generation == 1
        controls.put("stats")
        stats = events.get(timeout=5)
        assert stats[:5] == ("stats", 1, 1, 1, 1), stats
        assert stats[5] == ("IF2612",)
        while subscription.poll(timeout_seconds=5) is not None:
            pass
        poll_armed.set()
        controls.put(("tick", "IF2612"))
        assert events.get(timeout=5) == ("tick", "IF2612")
        assert poll_entered.wait(5)
        # Prove a later Host maintenance pass actually entered the source
        # reset check with the same provider poll still in flight.
        assert maintenance_entered.wait(5)
        time.sleep(0.3)
        assert not poll_release.is_set()
        started = time.monotonic()
        live = direct_only.command_client.status("liveness", timeout_seconds=0.5)
        elapsed = time.monotonic() - started
        assert live.outcome == "OBSERVED" and live.record.state == "RESERVED" and elapsed < 1.0, (live, elapsed)
        try:
            held = subscription.poll(timeout_seconds=0.2)
            assert held is None or held.payload["data"]["last_price"] != 4005.0
        except ThreadedRuntimeError as error:
            assert error.code == "OWNER_TIMEOUT_RUNNING"
        poll_release.set()
        delivered = _until(lambda: (event if (event := subscription.poll(timeout_seconds=10)) and
            event.payload["data"]["last_price"] == 4005.0 else None))
        assert dequeued.value == 1 and delivered.header.scope == WireScope(DOMAIN)
        assert subscription.poll(timeout_seconds=5) is None
        stop_armed.set()
        closed = session.close(timeout_seconds=5)
        assert closed.state == "closed" and not closed.thread_alive
        # The Gateway answered for this client's own reference; the native stop
        # that is still running is the Gateway's work, not an open question of
        # the client's close.
        assert closed.owner_cleanup_uncertainties == 0
        assert stop_entered.wait(5)
        stop_release.set()
        def stop_completed():
            controls.put("stats")
            status = events.get(timeout=5)
            # Gateway-mode disconnect asks the same client for a non-blocking
            # stop receipt, then the provider waits for its typed receipt; both
            # observe one native client and one confirmed release (status[12]).
            return status if status[6] >= 1 and status[12] else None
        stats = _until(stop_completed)
        assert stats[:5] == ("stats", 1, 1, 1, 1), stats
    finally:
        release.set()
        poll_release.set()
        stop_release.set()
        close_result = None
        try:
            if session is not None:
                closed = session.close(timeout_seconds=10)
                close_result = (closed.state, closed.thread_alive)
            if direct_only is not None:
                other_closed = direct_only.close(timeout_seconds=10)
                assert other_closed.state == "closed" and not other_closed.thread_alive
        finally:
            controls.put("stop")
            child.join(15)
            if child.is_alive():
                child.terminate()
                child.join(5)
                pytest.fail("synthetic Gateway process failed bounded shutdown")
        remaining = []
        while True:
            try:
                remaining.append(events.get_nowait())
            except queue.Empty:
                break
        assert child.exitcode == 0 and not any(item[0] in {"error", "cleanup_error"} for item in remaining), remaining
        assert ("closed", 1) in remaining, remaining
        assert session is None or close_result == ("closed", False)
