"""Synthetic CTP profile control assembly; no native account is contacted."""
from dataclasses import replace

import zmq
import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.ctp_gateway_trading import build_ctp_gateway_trading_profile
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
