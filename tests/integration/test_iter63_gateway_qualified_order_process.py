"""Synthetic CTP Gateway, configured real BT Feed/Broker, and qualified write.

The subprocess patches synthetic TD/MD and SNTP only. No account/network venue I/O.
"""
from __future__ import annotations

import importlib.metadata
import itertools
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

import backtrader as bt
from backtrader.feeds.btapifeed import BtApiFeed
from backtrader.live_state.gateway_pending import GatewayPendingHost
from backtrader.live_state.state_dir import StrategyStateDir
from backtrader.metabase import OwnerContext
from backtrader.stores.btapistore import BtApiStore
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_py.gateway_execution_stream_client import gateway_execution_consumer_ref
from bt_api_py.service_runtime import ClientCurveKeys
from tests.integration import test_iter63_async_market_bootstrap_service as fixtures
from tests.integration.test_iter63_ctp_gateway_joint_service import (
    CLIENT_A, DOMAIN, MARKET_OPS, SCOPE, SERVICE, _key,
)
from tests.integration.test_iter63_gateway_direct_service import _client_config


def _synthetic_sntp(_host, _timeout):
    return 0, 2_000_000


def _qualified_gateway(*args):
    """Install only synthetic feed, clock and operator resources in child."""
    import sys
    import threading
    from contextlib import contextmanager
    import bt_api_py.clock_verification as clock
    import bt_api_py.ctp_gateway_trading as trading
    import tests.integration.test_iter63_ctp_gateway_joint_service as joint
    from bt_api_py.ctp_gateway_operator_profile import (
        CtpGatewayTrustedResources, create_ctp_gateway_profile_factory,
    )
    from bt_api_py.gateway_operator_application import (
        GatewayOperatorProfileRegistry, load_gateway_operator_launch_config,
    )
    from bt_api_py.gateway_direct_owner import GatewayDirectOwner
    from bt_api_py.ctp_gateway_return_maintenance import CtpGatewayReturnMaintenance
    from bt_api_py.gateway_direct_service import GatewayReservationAdmissionDenied
    from bt_api_py.gateway_market_dispatch_gate import GatewayMarketDispatchGate

    original_check_permit = GatewayMarketDispatchGate._check_permit
    def observe_permit_check(self, permit, row):
        try:
            return original_check_permit(self, permit, row)
        except Exception as error:
            args[7].put(("synthetic-gate-rejected", getattr(error, "code", None)))
            raise
    GatewayMarketDispatchGate._check_permit = observe_permit_check

    original_reservation_fence = CtpGatewayReturnMaintenance.reservation_fence
    @contextmanager
    def observe_reservation_fence(self, context, order):
        try:
            with original_reservation_fence(self, context, order):
                yield
        except GatewayReservationAdmissionDenied:
            args[7].put(("synthetic-reservation-denied", self.status()))
            raise
    CtpGatewayReturnMaintenance.reservation_fence = observe_reservation_fence

    original_enqueue = GatewayDirectOwner.enqueue
    enqueued = 0
    def enqueue_with_expiring_second_command(self, command, **kwargs):
        nonlocal enqueued
        enqueued += 1
        if enqueued == 2:
            entered = threading.Event()
            def block_owner():
                entered.set()
                time.sleep(2.5)  # Exceeds the real gate's two-second maximum.
            self.submit_service_call(block_owner)
            if not entered.wait(2):
                raise RuntimeError("synthetic owner blocker did not start")
            state = original_enqueue(self, command, **kwargs)
            args[7].put(("synthetic-owner-queued", state))
            def observe_done():
                key = ("place", command.strategy, command.request_key)
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    with self._condition:
                        if key not in self._pending:
                            args[7].put(("synthetic-owner-finished", command.request_key))
                            return
                    time.sleep(.01)
            threading.Thread(target=observe_done, daemon=True).start()
            return state
        return original_enqueue(self, command, **kwargs)
    GatewayDirectOwner.enqueue = enqueue_with_expiring_second_command

    clock._sntp_query = _synthetic_sntp
    joint.MARKET_OPS = MARKET_OPS | {"market.clock_observation"}
    sys.path.insert(0, str(Path(joint.__file__).resolve().parents[2] / "bt_api/bt_api_ctp/tests"))
    from test_market_provider import _SyntheticMdClient
    from test_iter62_settlement_readback_on_write import _TraderSpi
    original_login = _TraderSpi.OnRspUserLogin
    original_trade = _TraderSpi.OnRtnTrade
    original_emit = _SyntheticMdClient.emit_tick

    def login(self, info, *other):
        info.FFEXTime = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S")
        return original_login(self, info, *other)

    def trade(self, info, *other):
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        info.TradeDate = now.strftime("%Y%m%d")
        info.TradeTime = now.strftime("%H:%M:%S")
        return original_trade(self, info, *other)

    def emit(self, **fields):
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        fields.update(ActionDay=now.strftime("%Y%m%d"), TradingDay="20261106",
                      UpdateTime=now.strftime("%H:%M:%S"), UpdateMillisec=now.microsecond // 1000,
                      BidPrice1=4004.0, AskPrice1=4006.0, BidVolume1=3, AskVolume1=4,
                      LowerLimitPrice=3500.0, UpperLimitPrice=4500.0,
                      OpenInterest=1000.0, Turnover=10000.0)
        return original_emit(self, **fields)

    _TraderSpi.OnRspUserLogin = login
    _TraderSpi.OnRtnTrade = trade
    _SyntheticMdClient.emit_tick = emit

    def configured(config, authority, registry_path, **kw):
        path = Path(args[0]) / "synthetic-operator-v2.yaml"
        path.write_text(yaml.safe_dump({"gateway_operator_config_schema_version": 2,
            "profile_ref": kw["profile_ref"], "service_config": config.to_public_dict()}), encoding="utf-8")
        loader = lambda _config: CtpGatewayTrustedResources(
            authority=authority, registry_path=registry_path, settings=kw["settings"], grants=kw["grants"],
            request_feed_factory=kw["request_feed_factory"], market_stream_factory=kw["market_stream_factory"],
            assert_admitted=kw["assert_admitted"], return_settings=kw["return_settings"],
            account_read_settings=kw["account_read_settings"],
            account_read_principals=kw["account_read_principals"], market_settings=kw["market_settings"],
            clock_verifier_factory=lambda: clock.ClockVerifier(servers=("synthetic",)),
            clock_allowed_servers=frozenset({"synthetic"}))
        factory = create_ctp_gateway_profile_factory(kw["profile_ref"], resource_loader=loader)
        class Entry:
            name = kw["profile_ref"]
            def load(self): return factory
        class Entries:
            def select(self, *, group):
                return (Entry(),) if group == "bt_api_py.gateway_operator_profile_factories" else ()
        importlib.metadata.entry_points = lambda: Entries()
        launch = load_gateway_operator_launch_config(path)
        return GatewayOperatorProfileRegistry.from_configured_entry_points(launch).resolve(kw["profile_ref"])

    trading.build_ctp_gateway_trading_profile = configured
    return joint._gateway(*args)


@pytest.fixture
def qualified_gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(fixtures, "_gateway", _qualified_gateway)
    yield from fixtures.async_gateway.__wrapped__(tmp_path)


def _strategy_config(state):
    template = Path(__file__).resolve().parents[2] / "examples/services/gateway-strategy-client.example.yaml"
    raw = yaml.safe_load(template.read_text(encoding="utf-8"))
    raw["client_config"] = _client_config(state["rpc"], CLIENT_A).to_public_dict()
    bindings = raw["client_config"]["clients"]["gateway"]["settings"]["bindings"]
    bindings[0]["account_ref"] = SCOPE
    market = dict(bindings[0], mount="store.market")
    del market["account_ref"]
    bindings.append(market)
    profile = raw["client_config"]["communication"]["profiles"]["client-transport"]
    profile["endpoints"]["market_connect"] = state["market"]
    profile.setdefault("options", {})["market_wire_version"] = 2
    raw["binding"] = {"journal_id": state["journal_id"], "account_scope": SCOPE,
        "strategy_scope": CLIENT_A.actor_ref, "principal": CLIENT_A.principal_ref}
    raw["route"].update(service_id=SERVICE.actor_ref, instance_id="gateway-a", scope_ref="scope-a")
    raw["route"]["scope"].update(execution_domain_ref=DOMAIN, account_ref=SCOPE,
        strategy_ref=CLIENT_A.actor_ref, run_id="run-" + CLIENT_A.actor_ref)
    raw["route"]["identity"].update(principal_ref=CLIENT_A.principal_ref, actor_ref=CLIENT_A.actor_ref)
    raw["peer"].update(principal_ref=SERVICE.principal_ref, actor_ref=SERVICE.actor_ref)
    from tests.integration.test_iter63_ctp_gateway_joint_service import DIRECT_OPS
    raw["requested_operations"] = sorted(DIRECT_OPS)
    raw["instruments_by_ref"] = {"IF2612": _key("IF2612").instrument.to_dict()}
    raw["market_settings"] = {"subscriptions": {"ticks": {"key": _key("IF2612").to_dict(),
        "require_source_descriptor": True}}, "requested_operations": sorted(MARKET_OPS | {"market.clock_observation"})}
    return raw


class _Strategy(bt.Strategy):
    def notify_order(self, order):
        self.events.append((order.status, order.executed.size))


class _CerebroAutoStrategy(bt.Strategy):
    def __init__(self):
        self.order = None
        self.executions = []
        self.auto_bound = False
        self.diagnostic = None

    def next(self):
        binding = self.broker._gateway_stream_binding
        if binding is not None:
            self.auto_bound = binding.strategy is self
        if self.executions:
            self.env.runstop()
            return
        if self.order is None:
            verdict = self.data.gateway_trade_qualification(expected_store=self.broker.store,
                expected_instrument=_key("IF2612").instrument)
            if verdict.ok:
                self.order = self.buy(size=2, price=10, exectype=bt.Order.Limit,
                    offset="open", quantity_unit="lots", time_in_force="GFD")
                key = self.order.info.get("gateway_request_key")
                record = self.broker.store._gateway_host.load_record(key)
                after = self.data.gateway_trade_qualification(expected_store=self.broker.store,
                    expected_instrument=_key("IF2612").instrument)
                self.diagnostic = (key, self.broker._gateway_stream_binding.state,
                    getattr(record, "state", None),
                    after.ok, after.reasons, after.receive_age_seconds)

    def notify_order(self, order):
        if order.status in (order.Partial, order.Completed) and order.executed.size:
            self.executions.append(order.executed.size)
            self.env.runstop()


def test_cerebro_auto_binding_with_configured_rules_and_real_pub(qualified_gateway, tmp_path, monkeypatch):
    import bt_api_py.clock_verification as clock
    monkeypatch.setattr(clock, "_sntp_query", _synthetic_sntp)
    client_clock = clock.GatewayClockVerifier(clock.ClockVerifier(servers=("synthetic",)))
    assert client_clock.refresh() is not None
    state = qualified_gateway
    state["release"].set()
    root = tmp_path / "cerebro-strategy"
    root.mkdir()
    state_dir = StrategyStateDir.initialize(root)
    binding = GatewayCommandBinding(state["journal_id"], SCOPE, CLIENT_A.actor_ref, CLIENT_A.principal_ref)
    GatewayPendingHost.upgrade_for_gateway(root, state_dir.strategy_uuid, binding)
    GatewayPendingHost.upgrade_for_cancellation(root, state_dir.strategy_uuid, binding)
    consumer = gateway_execution_consumer_ref(principal_ref=CLIENT_A.principal_ref,
        actor_ref=CLIENT_A.actor_ref, execution_domain_ref=DOMAIN, account_scope=SCOPE,
        strategy_scope=CLIENT_A.actor_ref)
    GatewayPendingHost.upgrade_for_execution(root, state_dir.strategy_uuid, binding, consumer_ref=consumer)
    host = GatewayPendingHost.upgrade_for_order_state(root, state_dir.strategy_uuid, binding)
    config = {"gateway_strategy": _strategy_config(state),
              "contracts": {"IF2612": {"quantity_unit": "lots", "time_in_force": "GFD"}}}
    store = BtApiStore(config=config, gateway_host=host, gateway_security_profiles={
        "security-a": ClientCurveKeys(state["server_keys"][0], *state["client_keys"][0])})
    assert store.contract_metadata["IF2612"]["gateway_rule_source"] == "local_configuration"
    cerebro = bt.Cerebro(stdstats=False, runonce=False, preload=False)
    cerebro.setbroker(bt.brokers.BtApiBroker(store=store))
    cerebro.adddata(BtApiFeed(dataname="IF2612", store=store, timeframe=bt.TimeFrame.Ticks,
        backfill_start=False, price_tick=1.0, qcheck=.05, gateway_clock_verification=client_clock))
    cerebro.addstrategy(_CerebroAutoStrategy)
    finished = threading.Event()
    producer_errors = []
    def produce_ticks():
        while not finished.is_set():
            try:
                state["controls"].put("stats")
                event = state["events"].get(timeout=2)
                if event[0] == "stats" and "IF2612" in event[5]:
                    break
            except Exception as error:
                producer_errors.append(type(error).__name__)
                return
            finished.wait(.05)
        while not finished.is_set():
            try:
                state["controls"].put(("tick", "IF2612"))
                event = state["events"].get(timeout=2)
                if event != ("tick", "IF2612"):
                    producer_errors.append(event)
                    return
            except Exception as error:
                producer_errors.append(type(error).__name__)
                return
            finished.wait(.1)
    producer = threading.Thread(target=produce_ticks, daemon=True)
    timeout = threading.Timer(15, cerebro.runstop)
    producer.start()
    timeout.start()
    try:
        strategies = cerebro.run()
    finally:
        finished.set()
        producer.join(3)
        timeout.cancel()
    assert not producer_errors
    assert not producer.is_alive()
    strategy, = strategies
    assert strategy.auto_bound and strategy.order is not None
    assert strategy.executions == [1], (strategy.diagnostic, strategy.order.getstatusname(), producer_errors,
        state["child"].is_alive(), state["child"].exitcode)
    assert host.load_execution_checkpoint().acknowledged_sequence > 0


def test_qualified_configured_order_and_clock_failure(qualified_gateway, tmp_path, monkeypatch):
    import bt_api_py.clock_verification as clock
    monkeypatch.setattr(clock, "_sntp_query", _synthetic_sntp)
    client_clock = clock.GatewayClockVerifier(clock.ClockVerifier(servers=("synthetic",)))
    assert client_clock.refresh() is not None
    state = qualified_gateway
    state["release"].set()
    root = tmp_path / "bt-strategy"
    root.mkdir()
    state_dir = StrategyStateDir.initialize(root)
    binding = GatewayCommandBinding(state["journal_id"], SCOPE, CLIENT_A.actor_ref, CLIENT_A.principal_ref)
    GatewayPendingHost.upgrade_for_gateway(root, state_dir.strategy_uuid, binding)
    GatewayPendingHost.upgrade_for_cancellation(root, state_dir.strategy_uuid, binding)
    consumer = gateway_execution_consumer_ref(principal_ref=CLIENT_A.principal_ref,
        actor_ref=CLIENT_A.actor_ref, execution_domain_ref=DOMAIN, account_scope=SCOPE,
        strategy_scope=CLIENT_A.actor_ref)
    GatewayPendingHost.upgrade_for_execution(root, state_dir.strategy_uuid, binding, consumer_ref=consumer)
    host = GatewayPendingHost.upgrade_for_order_state(root, state_dir.strategy_uuid, binding)
    strategy_path = tmp_path / "synthetic-strategy.yaml"
    strategy_path.write_text(yaml.safe_dump({"gateway_strategy": _strategy_config(state),
        "contracts": {"IF2612": {"quantity_unit": "lots", "time_in_force": "GFD"}}}), encoding="utf-8")
    store = BtApiStore(config=yaml.safe_load(strategy_path.read_text(encoding="utf-8")),
        gateway_host=host, gateway_security_profiles={"security-a": ClientCurveKeys(
            state["server_keys"][0], *state["client_keys"][0])})
    broker = bt.brokers.BtApiBroker(store=store)
    data = BtApiFeed(dataname="IF2612", store=store, timeframe=bt.TimeFrame.Ticks,
                     backfill_start=False, price_tick=1.0, qcheck=0.0,
                     gateway_clock_verification=client_clock)
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.setbroker(broker)
    cerebro.stcount = itertools.count()
    cerebro.datas, cerebro.datasbyname = [data], {"IF2612": data}
    with OwnerContext.set_owner(cerebro):
        strategy = _Strategy.__new__(_Strategy)
    strategy.datas, strategy.data = [data], data
    strategy._tradehistoryon, strategy._quicknotify, strategy.events = False, False, []
    try:
        broker.start()
        data._start()
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            state["controls"].put(("tick", "IF2612"))
            assert state["events"].get(timeout=5) == ("tick", "IF2612")
            for _ in range(10):
                loaded = data.load()
                verdict = data.gateway_trade_qualification(expected_store=store,
                    expected_instrument=_key("IF2612").instrument)
                if verdict.ok and loaded and len(data) > 0:
                    break
                time.sleep(.03)
            if verdict.ok and len(data) > 0:
                break
        else:
            adapter = data._gateway_market_adapter
            pair = adapter.latest_realtime()
            direct = None if pair is None else adapter._qualifier.qualify(*pair)
            try:
                obs = adapter._projector._client.clock_observation(timeout_seconds=1).observation
                event = pair[0].data
                raw_error = {"provider": obs["provider"], "venue": obs["venue"], "event_venue": event.instrument.venue, "service_gen_type": type(obs["service_generation"]).__name__, "td_gen_type": type(obs["td_connection_generation"]).__name__, "trading_day": obs["trading_day"], "event_day": event.trading_date, "provenance": event.event_time_provenance, "continuity": event.continuity_status, "calibration": obs["source_calibration"], "age_ns": obs["age_ns"]}
            except Exception as exc:
                raw_error = (type(exc).__name__, getattr(exc, "code", None), str(exc))
            pytest.fail(f"no qualified tick: {verdict!r}; pair={pair is not None}; reason={adapter.recovery_reason()}; direct={direct!r}; raw={raw_error!r}")
        store.bind_gateway_strategy(strategy, data_by_ref={"IF2612": data})
        reserve_calls = []
        command_trace = []
        original_reserve = store.gateway_api.reserve_order
        def traced_reserve(*args, **kwargs):
            reserve_calls.append(1)
            result = original_reserve(*args, **kwargs)
            command_trace.append(("reserve", result.outcome, result.code,
                getattr(result.record, "state", None)))
            return result
        monkeypatch.setattr(store.gateway_api, "reserve_order", traced_reserve)
        original_dispatch = store.gateway_api.dispatch_order
        def traced_dispatch(*args, **kwargs):
            result = original_dispatch(*args, **kwargs)
            command_trace.append(("dispatch", result.outcome, result.code,
                getattr(result.record, "state", None)))
            return result
        monkeypatch.setattr(store.gateway_api, "dispatch_order", traced_dispatch)
        # A genuine replay delivered by the SDK must not qualify as a live quote.
        adapter = data._gateway_market_adapter
        state["controls"].put(("tick", "IF2612"))
        assert state["events"].get(timeout=5) == ("tick", "IF2612")
        time.sleep(.15)
        adapter._projector.recover()
        replay_deadline = time.monotonic() + 5
        while time.monotonic() < replay_deadline:
            adapter.poll()
            receipt = adapter._last_receipt
            if receipt is not None and receipt.source == "replay":
                break
            time.sleep(.02)
        else:
            pytest.fail("SDK did not deliver the expected genuine replay")
        assert adapter.latest_realtime() is None
        assert not data.gateway_trade_qualification(expected_store=store,
            expected_instrument=_key("IF2612").instrument).ok
        replay_blocked = bt.BuyOrder(owner=strategy, data=data, size=1, price=10, exectype=bt.Order.Limit)
        replay_blocked.addinfo(offset="open", quantity_unit="lots", time_in_force="GFD")
        with pytest.raises(RuntimeError, match="FEED_NOT_TRADABLE"):
            broker.submit(replay_blocked)
        assert reserve_calls == []
        # Restore a real PUB event, then invalidate synthetic client calibration.
        freshness_deadline = time.monotonic() + 8
        while time.monotonic() < freshness_deadline:
            state["controls"].put(("tick", "IF2612"))
            assert state["events"].get(timeout=5) == ("tick", "IF2612")
            for _ in range(8):
                data.load()
                if data.gateway_trade_qualification(expected_store=store,
                        expected_instrument=_key("IF2612").instrument).ok:
                    break
                time.sleep(.03)
            if data.gateway_trade_qualification(expected_store=store,
                    expected_instrument=_key("IF2612").instrument).ok:
                break
        else:
            pytest.fail("fresh PUB tick did not restore qualification after replay")
        # Invalidate synthetic client calibration; no additional intent or RPC is allowed.
        with client_clock._lock:
            saved_sample = client_clock._sample
            client_clock._sample = None
        runtime = store.gateway_api._runtime
        rpc_calls = []
        from bt_api_py.service_runtime import ServiceClientRuntime
        old_call, old_submit, old_wire_call = runtime.call, runtime.submit_owner, ServiceClientRuntime.call
        def traced_call(*args, **kwargs):
            rpc_calls.append(("call", args[1] if len(args) > 1 else None))
            return old_call(*args, **kwargs)
        def traced_submit(*args, **kwargs):
            rpc_calls.append(("owner", args[0] if args else None))
            return old_submit(*args, **kwargs)
        def traced_wire_call(self, *args, **kwargs):
            rpc_calls.append(("wire", args[1] if len(args) > 1 else None))
            return old_wire_call(self, *args, **kwargs)
        monkeypatch.setattr(runtime, "call", traced_call)
        monkeypatch.setattr(runtime, "submit_owner", traced_submit)
        monkeypatch.setattr(ServiceClientRuntime, "call", traced_wire_call)
        before = state_dir.sequence.local_seq
        blocked = bt.BuyOrder(owner=strategy, data=data, size=1, price=10, exectype=bt.Order.Limit)
        blocked.addinfo(offset="open", quantity_unit="lots", time_in_force="GFD")
        with pytest.raises(RuntimeError, match="FEED_NOT_TRADABLE"):
            broker.submit(blocked)
        assert state_dir.sequence.local_seq == before
        assert reserve_calls == []
        assert all(kind == "owner" and operation == "market.ticks.status"
                   for kind, operation in rpc_calls), rpc_calls
        with client_clock._lock:
            client_clock._sample = saved_sample
        monkeypatch.setattr(runtime, "call", old_call)
        monkeypatch.setattr(runtime, "submit_owner", old_submit)
        monkeypatch.setattr(ServiceClientRuntime, "call", old_wire_call)
        # Invalid-calibration checks above can consume a quote's short lifetime;
        # submit only after receiving and qualifying a new live PUB tick.
        freshness_deadline = time.monotonic() + 8
        while time.monotonic() < freshness_deadline:
            state["controls"].put(("tick", "IF2612"))
            assert state["events"].get(timeout=5) == ("tick", "IF2612")
            for _ in range(8):
                data.load()
                if data.gateway_trade_qualification(expected_store=store,
                        expected_instrument=_key("IF2612").instrument).ok:
                    break
                time.sleep(.03)
            if data.gateway_trade_qualification(expected_store=store,
                    expected_instrument=_key("IF2612").instrument).ok:
                break
        else:
            pytest.fail("fresh PUB tick unavailable immediately before the order")
        order = bt.BuyOrder(owner=strategy, data=data, size=2, price=10, exectype=bt.Order.Limit)
        order.addinfo(offset="open", quantity_unit="lots", time_in_force="GFD")
        broker.submit(order)
        strategy._addnotification(broker.get_notification())
        strategy._notify()
        strategy.clear()
        until = time.monotonic() + 12
        while time.monotonic() < until:
            broker.next()
            notice = broker.get_notification()
            if notice is not None:
                strategy._addnotification(notice)
                strategy._notify()
                strategy.clear()
            if order.executed.size == 1 and host.load_execution_checkpoint().acknowledged_sequence:
                break
            time.sleep(.05)
        if order.executed.size != 1:
            observed = []
            while True:
                try:
                    observed.append(state["events"].get_nowait())
                except queue.Empty:
                    break
            state["controls"].put("stats")
            observed.append(state["events"].get(timeout=5))
            intent_states = []
            for intent in host.list_intents():
                try:
                    response = store.gateway_api.command_status("CTP___FUTURE", intent.request_key)
                    intent_states.append((intent.request_key, response.outcome, response.code,
                        getattr(response.record, "state", None)))
                except Exception as error:
                    intent_states.append((type(error).__name__, getattr(error, "code", None)))
            pytest.fail(f"synthetic order unfilled: status={order.getstatusname()}, "
                f"binding={broker._gateway_stream_binding.state!r}, trace={command_trace!r}, "
                f"notices={strategy.events!r}, intent_states={intent_states!r}, child_events={observed!r}, "
                f"child_alive={state['child'].is_alive()}, child_exit={state['child'].exitcode}")
        assert host.load_execution_checkpoint().acknowledged_sequence > 0
        assert any(status in (bt.Order.Partial, bt.Order.Completed) and size == 1
                   for status, size in strategy.events)

        # Queue a second real qualified request behind a synthetic slow owner
        # task. The source permit expires while the request is actually queued.
        state["controls"].put("stats")
        before_stats = state["events"].get(timeout=5)
        assert before_stats[0] == "stats" and before_stats[7] == 1
        state["controls"].put(("tick", "IF2612"))
        assert state["events"].get(timeout=5) == ("tick", "IF2612")
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            data.load()
            verdict = data.gateway_trade_qualification(expected_store=store,
                expected_instrument=_key("IF2612").instrument)
            if verdict.ok:
                break
            time.sleep(.02)
        assert verdict.ok
        expired = bt.BuyOrder(owner=strategy, data=data, size=1, price=10, exectype=bt.Order.Limit)
        expired.addinfo(offset="open", quantity_unit="lots", time_in_force="GFD")
        broker.submit(expired)
        try:
            queued = state["events"].get(timeout=5)
        except queue.Empty:
            request_key = expired.info.get("gateway_request_key")
            status = store.gateway_api.command_status("CTP___FUTURE", request_key)
            qualification = data.gateway_trade_qualification(expected_store=store,
                expected_instrument=_key("IF2612").instrument)
            state["controls"].put("stats")
            stats = state["events"].get(timeout=5)
            pytest.fail(f"second order not queued: key={request_key!r}, "
                f"binding={broker._gateway_stream_binding.state!r}, trace={command_trace!r}, "
                f"status={(status.outcome, status.code, getattr(status.record, 'state', None))!r}, "
                f"qualification={(qualification.ok, qualification.reasons, qualification.receive_age_seconds)!r}, "
                f"child_alive={state['child'].is_alive()}, child_exit={state['child'].exitcode}, stats={stats!r}")
        assert queued == ("synthetic-owner-queued", "QUEUED"), (queued, command_trace)
        assert state["events"].get(timeout=8) == ("synthetic-gate-rejected", "MARKET_QUOTE_EXPIRED")
        finished = state["events"].get(timeout=8)
        assert finished[0] == "synthetic-owner-finished"
        state["controls"].put("stats")
        after_stats = state["events"].get(timeout=5)
        assert after_stats[0] == "stats" and after_stats[7] == before_stats[7]
    finally:
        data.stop()
        store.stop(timeout=5)
