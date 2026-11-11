"""Configured CURVE Gateway computing strategy cash itself, read through the real BT getters.

The Gateway process gets budgets, an adjustment and rates the way the operator's
bundle supplies them (D63-53, D63-54, D63-55); nothing in the test produces a
snapshot by hand.
"""
from __future__ import annotations

import itertools
import time
from pathlib import Path

import backtrader as bt
import pytest
from backtrader.feeds.btapifeed import BtApiFeed
from backtrader.live_state.gateway_pending import GatewayPendingHost
from backtrader.live_state.state_dir import StrategyStateDir
from backtrader.metabase import OwnerContext
from backtrader.stores.btapistore import BtApiStore
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding

from bt_api_py.gateway_execution_stream_client import gateway_execution_consumer_ref
from bt_api_py.service_runtime import ClientCurveKeys
from tests.integration import test_iter63_async_market_bootstrap_service as fixtures
from tests.integration import test_iter63_gateway_qualified_order_process as qualified
from tests.integration.test_iter63_ctp_gateway_joint_service import CLIENT_A, DOMAIN, SCOPE, _key

OP = "direct.strategy_financial"
RATES = b"""schema_version: 1
currency: CNY
products:
  IF:
    multiplier: 300
    margin_ratio: {long: "0.12", short: "0.12"}
    commission:
      open: {per_lot: "0", ratio: "0.000023"}
      close: {per_lot: "0", ratio: "0.000023"}
      close_today: {per_lot: "0", ratio: "0.00023"}
"""


def _cash_gateway(*args):
    from dataclasses import replace
    from decimal import Decimal

    from bt_api_py import ctp_gateway_operator_profile as operator
    from bt_api_py import ctp_gateway_strategy_financial as financial
    from bt_api_py.ctp_gateway_strategy_financial import CtpStrategyFinancialSettings
    from bt_api_py.gateway_direct_service import GatewayControlGrant
    from bt_api_py.service_config import parse_service_config
    from bt_api_py.strategy_rates import parse_strategy_rates
    from tests.integration import test_iter63_ctp_gateway_joint_service as joint

    original_service_config = joint._service_config

    def service_config(endpoint):
        raw = original_service_config(endpoint).to_public_dict()
        raw["components"]["gateway"]["settings"]["options"].update(
            dispatch_enabled=True, cancel_enabled=True, control_enabled=True,
            strategy_ledger_enabled=True, account_extensions_enabled=True,
            account_facts_financial_enabled=True)
        return parse_service_config(raw)

    joint._service_config = service_config
    joint.DIRECT_OPS = joint.DIRECT_OPS | {OP}
    original_factory = operator.create_ctp_gateway_profile_factory
    settings = CtpStrategyFinancialSettings(
        "CNY", parse_strategy_rates(RATES), {CLIENT_A.actor_ref: Decimal("500000.00")},
        (("2026-11-12-a", CLIENT_A.actor_ref, Decimal("-100000.00")),))

    def factory(profile_ref, *, resource_loader):
        def loader(config):
            return replace(
                resource_loader(config), strategy_financial_settings=settings,
                control_grants={CLIENT_A.principal_ref: GatewayControlGrant(
                    CLIENT_A.actor_ref, True, frozenset(), frozenset({"direct.control.status"}))},
                upgrade_controls_from_v9=True, upgrade_strategy_ledger_from_v10=True,
                upgrade_account_extensions_from_v11=True, upgrade_parent_authority_from_v12=True,
                upgrade_account_facts_financial_from_v13=True)
        return original_factory(profile_ref, resource_loader=loader)

    operator.create_ctp_gateway_profile_factory = factory
    # Report to the test process which price board the producer reads and which one the
    # Gateway's own market connection writes; the two must be the same object.
    root = Path(args[0])
    original_init, original_observe = financial.CtpGatewayStrategyFinancial.__init__, financial.OwnerLastPrices.observe

    def init(self, *init_args, **kwargs):
        original_init(self, *init_args, **kwargs)
        # The producer is created before the return stream exists: it must see it as incomplete.
        root.joinpath("producer-price-board").write_text(
            f"{id(kwargs['last_price'].__self__)} {kwargs['returns_continuous']()!r}", encoding="utf-8")

    def observe(self, events):
        original_observe(self, events)
        seen = self.last("IF2612")
        if seen is not None:
            root.joinpath("market-price-board").write_text(f"{id(self)} {seen[0]}", encoding="utf-8")

    financial.CtpGatewayStrategyFinancial.__init__ = init
    financial.OwnerLastPrices.observe = observe
    return qualified._qualified_gateway(*args)


@pytest.fixture
def cash_gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(fixtures, "_gateway", _cash_gateway)
    yield from fixtures.async_gateway.__wrapped__(tmp_path)


class _Strategy(bt.Strategy):
    pass


def test_gateway_computes_strategy_cash_from_its_own_configuration(cash_gateway, tmp_path, monkeypatch):
    import bt_api_py.clock_verification as clock

    monkeypatch.setattr(clock, "_sntp_query", qualified._synthetic_sntp)
    client_clock = clock.GatewayClockVerifier(clock.ClockVerifier(servers=("synthetic",)))
    assert client_clock.refresh() is not None
    state = cash_gateway
    state["release"].set()
    root = Path(tmp_path) / "bt-strategy"
    root.mkdir()
    state_dir = StrategyStateDir.initialize(root)
    binding = GatewayCommandBinding(state["journal_id"], SCOPE, CLIENT_A.actor_ref, CLIENT_A.principal_ref)
    GatewayPendingHost.upgrade_for_gateway(root, state_dir.strategy_uuid, binding)
    GatewayPendingHost.upgrade_for_cancellation(root, state_dir.strategy_uuid, binding)
    consumer = gateway_execution_consumer_ref(
        principal_ref=CLIENT_A.principal_ref, actor_ref=CLIENT_A.actor_ref, execution_domain_ref=DOMAIN,
        account_scope=SCOPE, strategy_scope=CLIENT_A.actor_ref)
    GatewayPendingHost.upgrade_for_execution(root, state_dir.strategy_uuid, binding, consumer_ref=consumer)
    host = GatewayPendingHost.upgrade_for_order_state(root, state_dir.strategy_uuid, binding)
    config = qualified._strategy_config(state)
    config["requested_operations"] = sorted(set(config["requested_operations"]) | {OP})
    config["strategy_finance_currency"] = "CNY"
    store = BtApiStore(
        config={"gateway_strategy": config}, gateway_host=host,
        gateway_security_profiles={"security-a": ClientCurveKeys(state["server_keys"][0], *state["client_keys"][0])},
        contract_metadata={"IF2612": {"instrument_key": _key("IF2612").instrument,
                                      "exchange_name": "CTP___FUTURE", "quantity_unit": "lots",
                                      "time_in_force": "GFD"}})
    broker = bt.brokers.BtApiBroker(store=store)
    data = BtApiFeed(dataname="IF2612", store=store, timeframe=bt.TimeFrame.Ticks, backfill_start=False,
                     price_tick=1.0, qcheck=0.0, gateway_clock_verification=client_clock)
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.setbroker(broker)
    cerebro.stcount = itertools.count()
    cerebro.datas, cerebro.datasbyname = [data], {"IF2612": data}
    with OwnerContext.set_owner(cerebro):
        strategy = _Strategy.__new__(_Strategy)
    strategy.datas, strategy.data = [data], data
    strategy._tradehistoryon, strategy._quicknotify = False, False
    try:
        broker.start()
        data._start()
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            state["controls"].put(("tick", "IF2612"))
            assert state["events"].get(timeout=5) == ("tick", "IF2612")
            for _ in range(10):
                data.load()
                if len(data):
                    break
                time.sleep(0.03)
            if len(data):
                break
        else:
            pytest.fail("no synthetic market tick")
        store.bind_gateway_strategy(strategy, data_by_ref={"IF2612": data})
        # The first snapshot exists once the Gateway's return stream is complete.
        deadline, cash = time.monotonic() + 15, None
        while cash is None:
            try:
                cash = broker.get_strategy_cash(strategy)
            except RuntimeError as error:
                assert str(error) == "GATEWAY_STRATEGY_CASH_UNAVAILABLE"
                assert time.monotonic() < deadline, "strategy cash never became available"
                time.sleep(0.2)
        # A flat strategy is worth its budget and the adjustment: 500000 - 100000.
        assert cash == 400000.0 and broker.get_strategy_value(strategy) == 400000.0
        # The tick this strategy received also reached the price board the producer reads.
        board, price = (Path(tmp_path) / "market-price-board").read_text(encoding="utf-8").split()
        assert (Path(tmp_path) / "producer-price-board").read_text(encoding="utf-8") == board + " False"
        assert float(price) == data.close[0] > 0
    finally:
        data.stop()
        store.stop(timeout=5)
