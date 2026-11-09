"""Configured CURVE Gateway and real BT getters with a synthetic trusted cut."""
from __future__ import annotations

import itertools
import time
from pathlib import Path

import backtrader as bt
import pytest
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_py.gateway_execution_stream_client import gateway_execution_consumer_ref
from bt_api_py.service_runtime import ClientCurveKeys
from backtrader.feeds.btapifeed import BtApiFeed
from backtrader.live_state.gateway_pending import GatewayPendingHost
from backtrader.live_state.state_dir import StrategyStateDir
from backtrader.metabase import OwnerContext
from backtrader.stores.btapistore import BtApiStore
from tests.integration import test_iter63_async_market_bootstrap_service as fixtures
from tests.integration import test_iter63_gateway_qualified_order_process as qualified
from tests.integration.test_iter63_ctp_gateway_joint_service import CLIENT_A, DOMAIN, SCOPE, _key

OP = "direct.strategy_financial"


def _financial_gateway(*args):
    from contextlib import contextmanager
    from dataclasses import replace
    import sys
    from bt_api_py import ctp_gateway_operator_profile as operator
    from bt_api_py import gateway_strategy_financial_source as source_module
    from bt_api_py.gateway_direct_service import GatewayControlGrant
    from bt_api_py.service_config import parse_service_config
    from tests.integration import test_iter63_ctp_gateway_joint_service as joint

    root = Path(args[0])
    sys.path.insert(0, str(Path(joint.__file__).resolve().parents[2] / "bt_api/bt_api_gateway/tests"))
    from test_iter63_strategy_financial_ledger import sample

    original_service_config = joint._service_config
    def service_config(endpoint):
        raw = original_service_config(endpoint).to_public_dict()
        raw['components']['gateway']['settings']['options'].update(
            dispatch_enabled=True, cancel_enabled=True, control_enabled=True,
            strategy_ledger_enabled=True, account_extensions_enabled=True,
            account_facts_financial_enabled=True)
        return parse_service_config(raw)
    joint._service_config = service_config
    joint.DIRECT_OPS = joint.DIRECT_OPS | {OP}
    original_factory = operator.create_ctp_gateway_profile_factory

    def source_factory(journal, guard, session):
        del session
        snapshot = sample(account=SCOPE, strategy=CLIENT_A.actor_ref, journal_id=journal.journal_id,
                          owner_generation=journal._generation)
        published = [False]
        def collect():
            if published[0]:
                return ()
            published[0] = True
            return (snapshot,)
        @contextmanager
        def fence(account, strategy, currency):
            yield lambda _db, value: (root.joinpath('source-current').exists() and value == snapshot
                and (account, strategy, currency) == (SCOPE, CLIENT_A.actor_ref, 'CNY'))
        return source_module.GatewayStrategyFinancialSource(journal=journal, guard=guard,
            source_fence=fence, collect=collect, close=lambda: None)

    def factory(profile_ref, *, resource_loader):
        def loader(config):
            resources = resource_loader(config)
            return replace(resources, strategy_financial_source_factory=source_factory,
                control_grants={CLIENT_A.principal_ref: GatewayControlGrant(CLIENT_A.actor_ref, True, frozenset(),
                    frozenset({'direct.control.status'}))},
                upgrade_controls_from_v9=True, upgrade_strategy_ledger_from_v10=True,
                upgrade_account_extensions_from_v11=True, upgrade_parent_authority_from_v12=True,
                upgrade_account_facts_financial_from_v13=True)
        return original_factory(profile_ref, resource_loader=loader)
    operator.create_ctp_gateway_profile_factory = factory
    return qualified._qualified_gateway(*args)


@pytest.fixture
def financial_gateway(tmp_path, monkeypatch):
    (tmp_path / 'source-current').touch()
    monkeypatch.setattr(fixtures, '_gateway', _financial_gateway)
    yield from fixtures.async_gateway.__wrapped__(tmp_path)


class _Strategy(bt.Strategy):
    pass


def test_financial_source_configured_curve_to_broker_and_revocation(financial_gateway, tmp_path, monkeypatch):
    import bt_api_py.clock_verification as clock
    monkeypatch.setattr(clock, '_sntp_query', qualified._synthetic_sntp)
    client_clock = clock.GatewayClockVerifier(clock.ClockVerifier(servers=('synthetic',)))
    assert client_clock.refresh() is not None
    state = financial_gateway
    state['release'].set()
    root = tmp_path / 'bt-strategy'
    root.mkdir()
    state_dir = StrategyStateDir.initialize(root)
    binding = GatewayCommandBinding(state['journal_id'], SCOPE, CLIENT_A.actor_ref, CLIENT_A.principal_ref)
    GatewayPendingHost.upgrade_for_gateway(root, state_dir.strategy_uuid, binding)
    GatewayPendingHost.upgrade_for_cancellation(root, state_dir.strategy_uuid, binding)
    consumer = gateway_execution_consumer_ref(principal_ref=CLIENT_A.principal_ref,
        actor_ref=CLIENT_A.actor_ref, execution_domain_ref=DOMAIN, account_scope=SCOPE,
        strategy_scope=CLIENT_A.actor_ref)
    GatewayPendingHost.upgrade_for_execution(root, state_dir.strategy_uuid, binding, consumer_ref=consumer)
    host = GatewayPendingHost.upgrade_for_order_state(root, state_dir.strategy_uuid, binding)
    config = qualified._strategy_config(state)
    config['requested_operations'] = sorted(set(config['requested_operations']) | {OP})
    config['strategy_finance_currency'] = 'CNY'
    store = BtApiStore(config={'gateway_strategy': config}, gateway_host=host,
        gateway_security_profiles={'security-a': ClientCurveKeys(state['server_keys'][0], *state['client_keys'][0])},
        contract_metadata={'IF2612': {'instrument_key': _key('IF2612').instrument,
            'exchange_name': 'CTP___FUTURE', 'quantity_unit': 'lots', 'time_in_force': 'GFD'}})
    broker = bt.brokers.BtApiBroker(store=store)
    data = BtApiFeed(dataname='IF2612', store=store, timeframe=bt.TimeFrame.Ticks,
        backfill_start=False, price_tick=1.0, qcheck=0.0, gateway_clock_verification=client_clock)
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.setbroker(broker)
    cerebro.stcount = itertools.count()
    cerebro.datas, cerebro.datasbyname = [data], {'IF2612': data}
    with OwnerContext.set_owner(cerebro):
        strategy = _Strategy.__new__(_Strategy)
    strategy.datas, strategy.data = [data], data
    strategy._tradehistoryon, strategy._quicknotify = False, False
    try:
        broker.start()
        data._start()
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            state['controls'].put(('tick', 'IF2612'))
            assert state['events'].get(timeout=5) == ('tick', 'IF2612')
            for _ in range(10):
                data.load()
                if len(data):
                    break
                time.sleep(.03)
            if len(data):
                break
        else:
            pytest.fail('no synthetic market tick')
        store.bind_gateway_strategy(strategy, data_by_ref={'IF2612': data})
        assert broker.get_strategy_cash(strategy) == 74900.0
        assert broker.get_strategy_value(strategy) == 104900.0
        (tmp_path / 'source-current').unlink()
        with pytest.raises(RuntimeError, match='GATEWAY_STRATEGY_CASH_UNAVAILABLE'):
            broker.get_strategy_cash(strategy)
        with pytest.raises(RuntimeError, match='GATEWAY_STRATEGY_VALUE_UNAVAILABLE'):
            broker.get_strategy_value(strategy)
    finally:
        data.stop()
        store.stop(timeout=5)
