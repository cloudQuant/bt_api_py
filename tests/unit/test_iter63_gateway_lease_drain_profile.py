"""Actual Gateway ServiceHost wiring with synthetic CTP owner resources."""

from dataclasses import replace

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.ctp_gateway_trading import build_ctp_gateway_trading_profile
from bt_api_py.gateway_direct_service import GatewayControlGrant
from bt_api_py.gateway_lease_drain import GatewayLeaseDrainSettings
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.test_iter63_ctp_gateway_trading import CLIENT_A, make
from tests.unit.test_service_registry import _register


def test_ctp_host_drains_only_when_explicit_strategy_lease_expires(tmp_path, monkeypatch):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    opts = raw['components']['gateway']['settings']['options']
    opts.update(control_enabled=True, cancel_enabled=True)
    config = parse_service_config(raw)
    operator = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'operator-actor')
    operator_scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
    operations = frozenset({'direct.control.status'})
    resource = ResourceGrant('gateway-a', 'service.direct_write', config.scope_ref, operator_scope, operations)
    public, _ = zmq.curve_keypair()
    authority = replace(authority, principals={**authority.principals, public:'operator'},
        grants={**authority.grants, 'operator':(PrincipalGrant(operator,(resource,)),)})
    profile = build_ctp_gateway_trading_profile(config, authority, tmp_path/'registry.sqlite',
        profile_ref='lease-profile', settings=settings, grants=grants,
        request_feed_factory=lambda:None, market_stream_factory=lambda *_:None,
        assert_admitted=lambda _:True, control_grants={'operator':GatewayControlGrant(
            'operator-actor',True,frozenset(),operations)}, upgrade_controls_from_v9=True,
        lease_drain_settings=GatewayLeaseDrainSettings(frozenset({CLIENT_A.actor_ref}), 0))
    deployment = profile.deployment_factory()
    deployment.start()
    try:
        host = deployment._host
        registry = host._registry
        now = [100.0]
        registry._clock = lambda:now[0]
        scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope,
                          strategy_ref=CLIENT_A.actor_ref, run_id='run')
        registered = _register(registry, principal=CLIENT_A.principal_ref,identity=CLIENT_A,scope=scope,
            instance_id='gateway-a',port='service.direct_write',scope_ref=config.scope_ref,
            operations=('direct.reserve','direct.dispatch'))
        assert registered['ok'], registered
        now[0] = 131.0
        host.serve_once(0)
        manager, = host._gateway_lease_drains
        causes = manager.owner.submit_service_call(lambda:manager.control.active_system_causes(manager.origin)).result(timeout_seconds=2)
        assert len(causes) == 1 and causes[0]['mode'] == 'DRAIN' and causes[0]['scope_value'] == CLIENT_A.actor_ref
        assert not registry.pending_gateway_lease_expirations(instance_id='gateway-a',account_ref=settings.account_scope)
    finally:
        deployment.close()


def test_ctp_profile_installs_and_binds_the_lease_recovery_source(tmp_path, monkeypatch):
    """I63-E128: without an installed source no lease cause could ever be released."""
    import threading

    from bt_api_py import ctp_gateway_trading as module
    from bt_api_py.ctp_gateway_lease_recovery import CtpGatewayLeaseRecoverySource
    from tests.test_iter63_ctp_gateway_trading import FakeSession, _read_profile

    _, settings, config, authority, grants, read = _read_profile(tmp_path, monkeypatch)
    settings = replace(settings, write_enabled=True)
    raw = config.to_public_dict()
    raw['components']['gateway']['settings']['options'].update(control_enabled=True, cancel_enabled=True)
    config = parse_service_config(raw)
    operator = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'operator-actor')
    operator_scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
    operations = frozenset({'direct.control.status'})
    resource = ResourceGrant('gateway-a', 'service.direct_write', config.scope_ref, operator_scope, operations)
    public, _ = zmq.curve_keypair()
    authority = replace(authority, principals={**authority.principals, public:'operator'},
        grants={**authority.grants, 'operator':(PrincipalGrant(operator,(resource,)),)})
    declared = GatewayLeaseDrainSettings(frozenset({CLIENT_A.actor_ref}), 45)
    assert declared.recovery_source is None
    profile = build_ctp_gateway_trading_profile(config, authority, tmp_path/'registry.sqlite',
        profile_ref='lease-source', settings=settings, grants=grants,
        request_feed_factory=lambda:None, market_stream_factory=lambda *_:None,
        assert_admitted=lambda _:True, account_read_settings=read,
        account_read_principals=frozenset({CLIENT_A.principal_ref}),
        return_settings=module.CtpGatewayReturnSettings(read.observation_instruments),
        control_grants={'operator':GatewayControlGrant('operator-actor',True,frozenset(),operations)},
        upgrade_controls_from_v9=True, lease_drain_settings=declared)
    resources = profile.deployment_factory()._provide_resources(
        'gateway', 'gateway-a', dict(raw['components']['gateway']['settings']['options']), authority.scope)
    try:
        manager = resources.values['lease_drain']
        source = manager.settings.recovery_source
        assert type(source) is CtpGatewayLeaseRecoverySource
        assert (manager.settings.strategies, manager.settings.expiry_seconds, manager.settings.automatic_resume) == (
            declared.strategies, 45, True)
        assert source._account == settings.account_scope
        assert source._symbols == {grant.strategy_ref: grant.symbols for grant in grants.values()}
        session, provider, returns, journal, thread = source._bound
        assert session is FakeSession.instances[0] and provider is session.provider
        assert type(returns).__name__ == 'FakeReturnMaintenance'
        assert journal.account_scope == settings.account_scope
        assert thread is manager.owner._thread and thread is not threading.current_thread()
    finally:
        resources.close()
