"""Synthetic CTP profile control assembly; no native account is contacted."""
from dataclasses import replace

import zmq
import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError, build_ctp_gateway_trading_profile
from bt_api_py.gateway_direct_service import GatewayControlGrant
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.test_iter63_ctp_gateway_trading import make


def test_explicit_control_profile_assembles_on_existing_owner(tmp_path, monkeypatch):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    opts = raw['components']['gateway']['settings']['options']
    opts['control_enabled'] = True
    config = parse_service_config(raw)
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator-p', 'operator-a')
    scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
    ops = frozenset({'direct.control.apply', 'direct.control.resume', 'direct.control.status'})
    resource = ResourceGrant('gateway-a', 'service.direct_write', config.scope_ref, scope, ops)
    operator_public, _ = zmq.curve_keypair()
    authority = replace(authority, principals={**authority.principals, operator_public: 'operator-p'},
        grants={**authority.grants, 'operator-p': (PrincipalGrant(identity, (resource,)),)})
    grant = GatewayControlGrant('operator-a', True, frozenset(), ops)
    profile = build_ctp_gateway_trading_profile(config, authority, tmp_path/'control-registry.sqlite',
        profile_ref='control-test', settings=settings, grants=grants,
        request_feed_factory=lambda: None, market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True, control_grants={'operator-p': grant}, upgrade_controls_from_v9=True)
    deployment = profile.deployment_factory()
    resources = deployment._provide_resources('gateway', 'gateway-a', opts, authority.scope)
    try:
        binding = resources.values['binding']
        assert binding._journal._schema_version == 10
        assert binding._market_gate_required is True and binding._market_gate is None
        assert binding.supported_operations >= ops
        control = binding._control
        cause = binding._owner.submit_service_call(lambda: control.apply('test', 'FREEZE', scope_kind='account', origin='operator-p')).result(timeout_seconds=2)
        assert cause['active'] == 1
    finally:
        resources.close()


def test_explicit_v11_profile_does_not_invent_ctp_baseline(tmp_path, monkeypatch):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    opts = raw['components']['gateway']['settings']['options']
    opts.update(control_enabled=True, strategy_ledger_enabled=True)
    config = parse_service_config(raw)
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator-p', 'operator-a')
    scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
    ops = frozenset({'direct.control.status'})
    resource = ResourceGrant('gateway-a', 'service.direct_write', config.scope_ref, scope, ops)
    operator_public, _ = zmq.curve_keypair()
    authority = replace(authority, principals={**authority.principals, operator_public: 'operator-p'},
        grants={**authority.grants, 'operator-p': (PrincipalGrant(identity, (resource,)),)})
    grant = GatewayControlGrant('operator-a', True, frozenset(), ops)
    profile = build_ctp_gateway_trading_profile(config, authority, tmp_path/'control-registry.sqlite',
        profile_ref='ledger-test', settings=settings, grants=grants,
        request_feed_factory=lambda: None, market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True, control_grants={'operator-p': grant},
        upgrade_controls_from_v9=True, upgrade_strategy_ledger_from_v10=True)
    deployment = profile.deployment_factory()
    resources = deployment._provide_resources('gateway', 'gateway-a', opts, authority.scope)
    try:
        binding = resources.values['binding']
        assert binding._journal._schema_version == 11
        assert binding._journal._strategy_ledger_ports is None
        assert {'direct.strategy_position', 'direct.strategy_close_capacity'} <= binding.supported_operations
        with binding._journal._transaction() as db:
            assert db.execute('SELECT count(*) FROM strategy_position_baseline').fetchone()[0] == 0
    finally:
        resources.close()


def test_confirmed_incompatible_monitor_rejects_start_and_cleans_owner(tmp_path, monkeypatch):
    from bt_api_py.gateway_required_monitor import RequiredMonitor, RequiredMonitorSettings
    from bt_api_py.service_host import ServiceHostError

    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    raw['components']['gateway']['settings']['options']['control_enabled'] = True
    config = parse_service_config(raw)
    operator = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator-p', 'operator-a')
    scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
    ops = frozenset({'direct.control.status'})
    resource = ResourceGrant('gateway-a', 'service.direct_write', config.scope_ref, scope, ops)
    public, _ = zmq.curve_keypair()
    authority = replace(authority, principals={**authority.principals, public:'operator-p'},
        grants={**authority.grants, 'operator-p':(PrincipalGrant(operator,(resource,)),)})
    monitor_identity = WireIdentity(WireRole.SERVICE, 'monitor-p', 'monitor')
    required = RequiredMonitorSettings(lambda:None, authority.identity, monitor_identity, scope)
    monkeypatch.setattr(RequiredMonitor, 'start', lambda *_:None)
    monkeypatch.setattr(RequiredMonitor, 'startup_probe', lambda *_:'INCOMPATIBLE')
    profile = build_ctp_gateway_trading_profile(config, authority, tmp_path/'control-registry.sqlite',
        profile_ref='monitor-test', settings=settings, grants=grants,
        request_feed_factory=lambda:None, market_stream_factory=lambda *_:None,
        assert_admitted=lambda _:True, control_grants={'operator-p':GatewayControlGrant(
            'operator-a',True,frozenset(),ops)}, upgrade_controls_from_v9=True, required_monitor=required)
    deployment = profile.deployment_factory()
    with pytest.raises(ServiceHostError, match='MONITOR_INCOMPATIBLE'):
        deployment.start()
    assert deployment._host.state == 'failed'


def test_v11_trading_profile_admits_orders_only_after_a_proved_ledger_baseline(tmp_path, monkeypatch):
    """I63-E111: the write-enabled profile installs the ledger ports and baseline."""
    import time
    from types import SimpleNamespace

    from tests import test_iter63_ctp_gateway_trading as fixtures
    from tests.test_iter63_ctp_gateway_trading import _authenticated_reserve

    returns_complete = []
    reads = []
    monkeypatch.setattr(fixtures.FakeReturnMaintenance, "ledger_continuous",
                        lambda self: bool(returns_complete), raising=False)
    monkeypatch.setattr(fixtures.FakeReturnMaintenance, "query", lambda self: None, raising=False)
    monkeypatch.setattr(fixtures.FakeSession, "return_buffer", SimpleNamespace(
        status=lambda: SimpleNamespace(last_sequence=4, gap=False, gap_reason=None, record_count=0)),
        raising=False)

    def read_account(self, *, generation, timeout):
        reads.append(generation)
        return SimpleNamespace(observation_id="synthetic-read", positions=(), open_orders=(),
                               positions_complete=True, open_orders_complete=True)

    monkeypatch.setattr(fixtures.FakeSession, "read_account", read_account, raising=False)
    from tests.unit import test_iter63_gateway_direct_service as direct_fixtures

    plain_order = direct_fixtures._order
    # The ledger journal refuses an order that does not say open or close.
    monkeypatch.setattr(direct_fixtures, "_order",
                        lambda **fields: dict(plain_order(**fields), offset="open"))
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch, write_enabled=True)
    raw = config.to_public_dict()
    opts = raw['components']['gateway']['settings']['options']
    opts.update(control_enabled=True, strategy_ledger_enabled=True)
    config = parse_service_config(raw)
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator-p', 'operator-a')
    scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
    ops = frozenset({'direct.control.status'})
    resource = ResourceGrant('gateway-a', 'service.direct_write', config.scope_ref, scope, ops)
    operator_public, _ = zmq.curve_keypair()
    authority = replace(authority, principals={**authority.principals, operator_public: 'operator-p'},
        grants={**authority.grants, 'operator-p': (PrincipalGrant(identity, (resource,)),)})
    from bt_api_base.market_protocol import MarketInstrumentKey
    from bt_api_py.ctp_gateway_trading import CtpGatewayReturnSettings

    instrument = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612")
    profile = build_ctp_gateway_trading_profile(config, authority, tmp_path/'ledger-registry.sqlite',
        profile_ref='ledger-write-test', settings=settings, grants=grants,
        request_feed_factory=lambda: None, market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True, return_settings=CtpGatewayReturnSettings((instrument,)),
        control_grants={'operator-p': GatewayControlGrant('operator-a', True, frozenset(), ops)},
        upgrade_controls_from_v9=True, upgrade_strategy_ledger_from_v10=True)
    deployment = profile.deployment_factory()
    resources = deployment._provide_resources('gateway', 'gateway-a', opts, authority.scope)
    try:
        binding = resources.values['binding']
        journal = binding._journal
        assert journal._schema_version == 11 and journal._strategy_ledger_ports is not None

        def baselines():
            with journal._transaction() as db:
                return db.execute('SELECT count(*) FROM strategy_position_baseline').fetchone()[0]

        _, reserve = _authenticated_reserve(binding, settings.account_scope)
        # Returns are not complete yet: no baseline, and no order is admitted.
        time.sleep(0.2)
        assert baselines() == 0 and reads == []
        assert reserve("before-baseline") == {"status": "REQUEST_REJECTED",
                                               "code": "STRATEGY_LEDGER_UNAVAILABLE"}
        returns_complete.append(True)
        deadline = time.monotonic() + 5
        while baselines() == 0:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert len(reads) == 1
        admitted = reserve("after-baseline")
        assert (admitted["status"], admitted["record"]["state"]) == ("OK", "RESERVED")
        close = {"symbol": "rb2612", "side": "sell", "offset": "close_today", "quantity": "1",
                 "quantity_unit": "contracts", "exchange_id": "SHFE"}
        position = binding._owner.submit_service_call(lambda: journal.read_strategy_position(
            grants[next(iter(grants))].strategy_ref, close, expected_instrument=instrument)
        ).result(timeout_seconds=2)
        assert (position.trading_day, position.net) == ("20300101", 0)
    finally:
        resources.close()


def test_v12_trading_profile_compares_the_account_with_the_strategy_ledgers(tmp_path, monkeypatch):
    """D63-48 in the trading profile: schema 12, comparison ports, an evaluated cut."""
    import time
    from types import SimpleNamespace

    from tests import test_iter63_ctp_gateway_trading as fixtures
    from tests.test_iter63_ctp_gateway_trading import _authenticated_reserve

    returns_complete = []
    reads = []
    monkeypatch.setattr(fixtures.FakeReturnMaintenance, "ledger_continuous",
                        lambda self: bool(returns_complete), raising=False)
    monkeypatch.setattr(fixtures.FakeReturnMaintenance, "query", lambda self: None, raising=False)
    monkeypatch.setattr(fixtures.FakeSession, "return_buffer", SimpleNamespace(
        status=lambda: SimpleNamespace(last_sequence=4, gap=False, gap_reason=None, record_count=0)),
        raising=False)

    def read_account(self, *, generation, timeout):
        reads.append(generation)
        return SimpleNamespace(observation_id="synthetic-read", positions=(), open_orders=(),
                               positions_complete=True, open_orders_complete=True)

    monkeypatch.setattr(fixtures.FakeSession, "read_account", read_account, raising=False)
    from tests.unit import test_iter63_gateway_direct_service as direct_fixtures

    plain_order = direct_fixtures._order
    # The ledger journal refuses an order that does not say open or close.
    monkeypatch.setattr(direct_fixtures, "_order",
                        lambda **fields: dict(plain_order(**fields), offset="open"))
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch, write_enabled=True)
    raw = config.to_public_dict()
    opts = raw['components']['gateway']['settings']['options']
    opts.update(control_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True)
    config = parse_service_config(raw)
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator-p', 'operator-a')
    scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
    ops = frozenset({'direct.control.status'})
    resource = ResourceGrant('gateway-a', 'service.direct_write', config.scope_ref, scope, ops)
    operator_public, _ = zmq.curve_keypair()
    authority = replace(authority, principals={**authority.principals, operator_public: 'operator-p'},
        grants={**authority.grants, 'operator-p': (PrincipalGrant(identity, (resource,)),)})
    from bt_api_base.market_protocol import MarketInstrumentKey
    from bt_api_py.ctp_gateway_trading import CtpGatewayReturnSettings

    instrument = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612")
    profile = build_ctp_gateway_trading_profile(config, authority, tmp_path/'ledger-registry.sqlite',
        profile_ref='ledger-write-test', settings=settings, grants=grants,
        request_feed_factory=lambda: None, market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True, return_settings=CtpGatewayReturnSettings((instrument,)),
        control_grants={'operator-p': GatewayControlGrant('operator-a', True, frozenset(), ops)},
        upgrade_controls_from_v9=True, upgrade_strategy_ledger_from_v10=True,
        upgrade_account_extensions_from_v11=True, strategy_ledger_comparison_refresh_seconds=1)
    # The comparison needs the ledger and schema 12; a bare request is refused.
    for bad in (dict(strategy_ledger_comparison_refresh_seconds=0.5), dict(strategy_ledger_comparison_refresh_seconds=True),
                dict(strategy_ledger_comparison_refresh_seconds=121)):
        with pytest.raises(CtpGatewayTradingError, match="INVALID_ACCOUNT_COMPARISON_CONFIGURATION"):
            build_ctp_gateway_trading_profile(config, authority, tmp_path/'bad-registry.sqlite',
                profile_ref='ledger-write-test', settings=settings, grants=grants,
                request_feed_factory=lambda: None, market_stream_factory=lambda *_: None,
                assert_admitted=lambda _: True, return_settings=CtpGatewayReturnSettings((instrument,)),
                control_grants={'operator-p': GatewayControlGrant('operator-a', True, frozenset(), ops)},
                upgrade_controls_from_v9=True, upgrade_strategy_ledger_from_v10=True,
                upgrade_account_extensions_from_v11=True, **bad)
    deployment = profile.deployment_factory()
    resources = deployment._provide_resources('gateway', 'gateway-a', opts, authority.scope)
    try:
        binding = resources.values['binding']
        journal = binding._journal
        assert journal._schema_version == 12 and journal._strategy_ledger_ports is not None

        def baselines():
            with journal._transaction() as db:
                return db.execute('SELECT count(*) FROM strategy_position_baseline').fetchone()[0]

        _, reserve = _authenticated_reserve(binding, settings.account_scope)
        # Returns are not complete yet: no baseline, and no order is admitted.
        time.sleep(0.2)
        assert baselines() == 0 and reads == []
        assert reserve("before-baseline") == {"status": "REQUEST_REJECTED",
                                               "code": "STRATEGY_LEDGER_UNAVAILABLE"}
        returns_complete.append(True)
        deadline = time.monotonic() + 5
        while baselines() == 0:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        # The comparison ports follow the baseline; the first cut is evaluated.
        def comparison():
            return binding._owner.submit_service_call(
                lambda: journal.read_account_net_comparison(instrument)).result(timeout_seconds=2)

        deadline = time.monotonic() + 5
        while comparison().status != "MATCH":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert journal._account_comparison_ports is not None
        result = comparison()
        assert (result.strategy_net, result.external_net, result.account_net, result.difference) == (0, 0, 0, 0)
        assert len(reads) == 2  # one read for the baseline, one for the comparison cut
    finally:
        resources.close()


def test_v17_trading_profile_walks_the_journal_chain_and_records_strategy_origins(tmp_path, monkeypatch):
    """DRAIN targets (63.12): schema 17 through the installed bundle's origin source."""
    import time
    from types import SimpleNamespace

    from tests import test_iter63_ctp_gateway_trading as fixtures
    from tests.test_iter63_ctp_gateway_trading import _authenticated_reserve
    from bt_api_py.ctp_gateway_local_profile import _strategy_origin_source_factory

    returns_complete = []
    reads = []
    monkeypatch.setattr(fixtures.FakeReturnMaintenance, "ledger_continuous",
                        lambda self: bool(returns_complete), raising=False)
    monkeypatch.setattr(fixtures.FakeReturnMaintenance, "query", lambda self: None, raising=False)
    monkeypatch.setattr(fixtures.FakeSession, "return_buffer", SimpleNamespace(
        status=lambda: SimpleNamespace(last_sequence=4, gap=False, gap_reason=None, record_count=0)),
        raising=False)

    def read_account(self, *, generation, timeout):
        reads.append(generation)
        return SimpleNamespace(observation_id="synthetic-read", positions=(), open_orders=(),
                               positions_complete=True, open_orders_complete=True)

    monkeypatch.setattr(fixtures.FakeSession, "read_account", read_account, raising=False)
    from tests.unit import test_iter63_gateway_direct_service as direct_fixtures

    plain_order = direct_fixtures._order
    # The ledger journal refuses an order that does not say open or close.
    exchange = ["SHFE"]
    monkeypatch.setattr(direct_fixtures, "_order", lambda **fields: dict(
        plain_order(**fields), offset="open", exchange_id=exchange[0]))
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch, write_enabled=True)
    raw = config.to_public_dict()
    opts = raw['components']['gateway']['settings']['options']
    opts.update(control_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True,
                strategy_origin_enabled=True, control_cancel_enabled=True,
                dispatch_enabled=True, cancel_enabled=True)
    config = parse_service_config(raw)
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator-p', 'operator-a')
    scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
    ops = frozenset({'direct.control.status'})
    resource = ResourceGrant('gateway-a', 'service.direct_write', config.scope_ref, scope, ops)
    operator_public, _ = zmq.curve_keypair()
    authority = replace(authority, principals={**authority.principals, operator_public: 'operator-p'},
        grants={**authority.grants, 'operator-p': (PrincipalGrant(identity, (resource,)),)})
    from bt_api_base.market_protocol import MarketInstrumentKey
    from bt_api_py.ctp_gateway_trading import CtpGatewayReturnSettings

    instrument = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612")
    profile = build_ctp_gateway_trading_profile(config, authority, tmp_path/'ledger-registry.sqlite',
        profile_ref='ledger-write-test', settings=settings, grants=grants,
        request_feed_factory=lambda: None, market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True, return_settings=CtpGatewayReturnSettings((instrument,)),
        control_grants={'operator-p': GatewayControlGrant('operator-a', True, frozenset(), ops)},
        upgrade_controls_from_v9=True, upgrade_strategy_ledger_from_v10=True,
        upgrade_account_extensions_from_v11=True, upgrade_parent_authority_from_v12=True,
        upgrade_account_facts_financial_from_v13=True, upgrade_actor_stream_from_v14=True,
        upgrade_strategy_origin_from_v15=True, upgrade_control_cancel_from_v16=True,
        # The strategy's orders name the symbol "rb" on SHFE (the fixture's grant).
        strategy_origin_source_factory=_strategy_origin_source_factory(
            (MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb"),)))
    deployment = profile.deployment_factory()
    resources = deployment._provide_resources('gateway', 'gateway-a', opts, authority.scope)
    try:
        binding = resources.values['binding']
        journal = binding._journal
        assert journal._schema_version == 17 and journal._strategy_ledger_ports is not None

        def baselines():
            with journal._transaction() as db:
                return db.execute('SELECT count(*) FROM strategy_position_baseline').fetchone()[0]

        _, reserve = _authenticated_reserve(binding, settings.account_scope)
        # Returns are not complete yet: no baseline, and no order is admitted.
        time.sleep(0.2)
        assert baselines() == 0 and reads == []
        assert reserve("before-baseline") == {"status": "REQUEST_REJECTED",
                                               "code": "STRATEGY_LEDGER_UNAVAILABLE"}
        returns_complete.append(True)
        deadline = time.monotonic() + 5
        while baselines() == 0:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        admitted = reserve("with-origin")
        assert (admitted["status"], admitted["record"]["state"]) == ("OK", "RESERVED"), admitted
        # An order that does not name one of the operator's instruments has no origin.
        exchange[0] = "CZCE"
        assert reserve("no-origin") == {"status": "REQUEST_REJECTED", "code": "STRATEGY_ORIGIN_UNAVAILABLE"}
        exchange[0] = "SHFE"

        def origins():
            with journal._transaction() as db:
                return db.execute("SELECT count(*) FROM direct_strategy_origins").fetchone()[0]

        assert binding._owner.submit_service_call(origins).result(timeout_seconds=2) == 1
    finally:
        resources.close()
