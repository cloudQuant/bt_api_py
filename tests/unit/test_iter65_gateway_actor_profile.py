"""Offline CTP owner composition and capability publication for actor v2."""
from dataclasses import replace

import pytest
import zmq

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_delegation import GatewayDelegateGrant
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.ctp_gateway_trading import build_ctp_gateway_trading_profile
from bt_api_py.gateway_delegation_service import GatewayDelegationSettings
from bt_api_py.gateway_direct_service import GatewayControlGrant
from bt_api_py.service_components import ServiceResourceConstructionError
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
import tests.test_iter63_ctp_gateway_trading as fixtures


def profile_for(tmp_path, monkeypatch, *, v2_enabled=True):
    # Do not probe a port; none of these tests constructs a network listener.
    monkeypatch.setattr(fixtures, '_free_endpoint', lambda: 'tcp://127.0.0.1:17359')
    _, settings, config, authority, grants, _ = fixtures.make(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    raw['components']['gateway']['settings']['options'].update(
        cancel_enabled=True, control_enabled=True, strategy_ledger_enabled=True,
        account_extensions_enabled=True, delegation_enabled=True)
    if v2_enabled:
        raw['components']['gateway']['settings']['options']['actor_source_v2_enabled'] = True
    config = parse_service_config(raw)
    domain = authority.scope.execution_domain_ref
    account = WireScope(domain, account_ref=settings.account_scope)
    template = WireScope(domain, account_ref=settings.account_scope, strategy_ref=fixtures.CLIENT_A.actor_ref)
    operator = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'operator-actor')
    risk = WireIdentity(WireRole.SERVICE, 'risk-principal', 'risk-actor')
    control_ops = frozenset({'direct.control.status'})
    delegate_ops = frozenset({'direct.delegated_reserve', 'direct.delegated_dispatch',
                              'direct.delegate_actor_status'})
    if v2_enabled:
        delegate_ops |= frozenset({'direct.delegate_actor_status_v2', 'direct.delegate_actor_release_status_v2'})
    op_key, _ = zmq.curve_keypair()
    risk_key, _ = zmq.curve_keypair()
    authority = replace(authority, principals={**authority.principals, op_key: 'operator', risk_key: 'risk-principal'},
        grants={**authority.grants,
            'operator': (PrincipalGrant(operator, (ResourceGrant('gateway-a', 'service.direct_write',
                config.scope_ref, account, control_ops),)),),
            'risk-principal': (PrincipalGrant(risk, (ResourceGrant('gateway-a', 'service.direct_write',
                config.scope_ref, template, delegate_ops),)),)})
    key = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
    grant = GatewayDelegateGrant(risk, settings.account_scope, fixtures.CLIENT_A.actor_ref, domain,
        'risk', 'rev1', frozenset({key}), frozenset({'direct.reserve', 'direct.dispatch', 'direct.command_status'}))
    profile = build_ctp_gateway_trading_profile(config, authority, tmp_path / 'registry.sqlite',
        profile_ref='actor-source-profile', settings=settings, grants=grants,
        request_feed_factory=lambda: None, market_stream_factory=lambda *_: None, assert_admitted=lambda _: True,
        control_grants={'operator': GatewayControlGrant('operator-actor', True, frozenset(), control_ops)},
        upgrade_controls_from_v9=True, upgrade_strategy_ledger_from_v10=True,
        upgrade_account_extensions_from_v11=True,
        delegation_settings=GatewayDelegationSettings((grant,), lambda instrument: instrument == key))
    return profile, settings, config, authority


def resources_for(profile, config, authority):
    deployment = profile.deployment_factory()
    return deployment._provide_resources('gateway', 'gateway-a',
        dict(config.components['gateway']['settings']['options']), authority.scope)


def test_ctp_owner_factory_binds_the_actual_guard(tmp_path, monkeypatch):
    profile, _, config, authority = profile_for(tmp_path, monkeypatch)
    resources = resources_for(profile, config, authority)
    try:
        actor = resources.values['delegate_actor_service']
        owner, journal, guard = actor.owner, actor.journal, actor._actor_source_guard
        assert actor.supports_actor_source_v2
        def check():
            return (owner.is_bound_to(journal) and owner._dispatcher.is_bound_to(journal, guard, 'CTP___FUTURE')
                    and journal._actor_source_guard is guard)
        assert owner.submit_service_call(check).result(timeout_seconds=2)
    finally:
        resources.close()
    assert fixtures.FakeSession.instances[0].events[-1][0] == 'close'


@pytest.mark.parametrize('failure', ['dispatcher_mismatch', 'install_failure', 'install_unavailable'])
def test_ctp_source_binding_failure_cleans_up(tmp_path, monkeypatch, failure):
    profile, settings, config, authority = profile_for(tmp_path, monkeypatch)
    if failure == 'dispatcher_mismatch':
        monkeypatch.setattr(GatewayDirectDispatcher, 'is_bound_to', lambda *_: False)
    elif failure == 'install_failure':
        def fail_install(*_, **__):
            raise RuntimeError('synthetic install failure')
        monkeypatch.setattr(GatewayDirectCommandJournal, 'install_actor_source_guard', fail_install)
    else:
        monkeypatch.setattr(GatewayDirectCommandJournal, 'install_actor_source_guard', None)
    with pytest.raises(ServiceResourceConstructionError) as caught:
        resources_for(profile, config, authority)
    caught.value.resources.close()
    assert fixtures.FakeSession.instances[0].events[-1][0] == 'close'
    probe = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, 'next-owner')
    probe.acquire()
    probe.release()


def test_build_component_advertises_v2_only_with_bound_guard(tmp_path, monkeypatch):
    from bt_api_py.gateway_delegate_actor_service import GatewayDelegateActorService, OPERATIONS, V2_OPERATIONS
    from bt_api_py.service_components import ComponentResources, build_component, component_specs, validate_components

    profile, _, config, authority = profile_for(tmp_path, monkeypatch)
    resources = resources_for(profile, config, authority)
    try:
        prepared, = validate_components(component_specs(config.components))
        values = dict(resources.values)
        original = values['delegate_actor_service']
        component = build_component(prepared, scope=authority.scope,
            resource_provider=lambda *_: ComponentResources.borrowed(values, ready=lambda: True))
        capability, = component.capabilities
        assert OPERATIONS | V2_OPERATIONS <= capability.operations
        assert not V2_OPERATIONS & capability.write_operations
        assert frozenset({'direct.delegated_reserve', 'direct.delegated_dispatch'}) <= capability.operations
        values['delegate_actor_service'] = GatewayDelegateActorService(original._delegation)
        from bt_api_py.service_components import ServiceComponentError
        with pytest.raises(ServiceComponentError, match='ACTOR_SOURCE_V2_UNAVAILABLE'):
            build_component(prepared, scope=authority.scope,
                resource_provider=lambda *_: ComponentResources.borrowed(values, ready=lambda: True))
        raw = config.to_public_dict()
        del raw['components']['gateway']['settings']['options']['actor_source_v2_enabled']
        old_prepared, = validate_components(component_specs(parse_service_config(raw).components))
        for actor in (values['delegate_actor_service'], original):
            values['delegate_actor_service'] = actor
            component = build_component(old_prepared, scope=authority.scope,
                resource_provider=lambda *_: ComponentResources.borrowed(values, ready=lambda: True))
            capability, = component.capabilities
            assert OPERATIONS <= capability.operations and not V2_OPERATIONS & capability.operations
    finally:
        resources.close()


def test_service_host_bound_capability_without_listener(tmp_path, monkeypatch):
    from bt_api_py.gateway_delegate_actor_service import OPERATIONS, V2_OPERATIONS
    from tests.unit.test_service_registry import _register
    import bt_api_transport_zmq.service_v2 as transport

    profile, _, config, authority = profile_for(tmp_path, monkeypatch)
    class NoNetworkServer:
        def __init__(self, endpoint, *_args, **_kwargs):
            self.endpoint = endpoint
        def close(self):
            pass
    monkeypatch.setattr(transport, 'ZmqServiceServer', NoNetworkServer)
    deployment = profile.deployment_factory()
    try:
        deployment.start()
        capability = deployment._host._registry._capabilities[('gateway-a', 'service.direct_write')]
        assert OPERATIONS | V2_OPERATIONS <= capability.operations
        assert not V2_OPERATIONS & capability.write_operations
        assert frozenset({'direct.delegated_reserve', 'direct.delegated_dispatch'}) <= capability.operations
        registry = deployment._host._registry
        strategy_entry = authority.grants[fixtures.CLIENT_A.principal_ref][0]
        scope = replace(strategy_entry.resources[0].scope, run_id='run')
        def register(identity, operations, key):
            return _register(registry, principal=identity.principal_ref, identity=identity, scope=scope,
                key=key, instance_id='gateway-a', port='service.direct_write', scope_ref=config.scope_ref,
                operations=tuple(operations))
        assert register(fixtures.CLIENT_A, {'direct.reserve'}, 'strategy')['ok']
        risk = authority.grants['risk-principal'][0].identity
        assert register(risk, V2_OPERATIONS | {'direct.delegate_actor_status'}, 'risk')['ok']
        assert not register(risk, {'direct.delegate_actor_release'}, 'risk-unauthorized')['ok']
    finally:
        deployment.close()


def test_service_host_unbound_does_not_advertise_v2(tmp_path, monkeypatch):
    from bt_api_py.gateway_delegate_actor_service import GatewayDelegateActorService, OPERATIONS, V2_OPERATIONS
    from bt_api_py.service_components import ComponentResources
    from bt_api_py.service_host import ServiceHost
    from tests.unit.test_service_registry import _register
    import bt_api_transport_zmq.service_v2 as transport

    profile, _, config, authority = profile_for(tmp_path, monkeypatch, v2_enabled=False)
    risk_entry = authority.grants['risk-principal'][0]
    risk_resource = risk_entry.resources[0]
    risk_entry = replace(risk_entry, resources=(replace(risk_resource,
        operations=risk_resource.operations - V2_OPERATIONS),))
    authority = replace(authority, grants={**authority.grants, 'risk-principal': (risk_entry,)})
    class NoNetworkServer:
        def __init__(self, endpoint, *_args, **_kwargs):
            self.endpoint = endpoint
        def close(self):
            pass
    monkeypatch.setattr(transport, 'ZmqServiceServer', NoNetworkServer)
    deployment = profile.deployment_factory()
    def provide(*args):
        resources = deployment._provide_resources(*args)
        values = dict(resources.values)
        bound = values['delegate_actor_service']
        values['delegate_actor_service'] = GatewayDelegateActorService(bound._delegation)
        return ComponentResources.owned(values, ready=resources.ready, close=resources.close)
    host = ServiceHost(config, authority, tmp_path / 'unbound-registry.sqlite', provide)
    try:
        host.start()
        capability = host._registry._capabilities[('gateway-a', 'service.direct_write')]
        assert OPERATIONS <= capability.operations
        assert not V2_OPERATIONS & capability.operations
        assert frozenset({'direct.delegated_reserve', 'direct.delegated_dispatch'}) <= capability.operations
        strategy = authority.grants[fixtures.CLIENT_A.principal_ref][0]
        scope = replace(strategy.resources[0].scope, run_id='run')
        def register(identity, operations, key):
            return _register(host._registry, principal=identity.principal_ref, identity=identity, scope=scope,
                key=key, instance_id='gateway-a', port='service.direct_write', scope_ref=config.scope_ref,
                operations=tuple(operations))
        assert register(fixtures.CLIENT_A, {'direct.reserve'}, 'strategy')['ok']
        risk = authority.grants['risk-principal'][0].identity
        assert register(risk, {'direct.delegate_actor_status'}, 'risk-v1')['ok']
        assert not register(risk, V2_OPERATIONS, 'risk-v2')['ok']
    finally:
        host.close()


@pytest.mark.parametrize('value,delegation,cancel,valid', [
    (True, True, True, True), (False, False, False, True),
    (True, False, True, False), (True, True, False, False),
    (1, True, True, False), ('true', True, True, False), (None, True, True, False),
])
def test_actor_source_v2_config_is_explicit_and_strict(tmp_path, monkeypatch, value, delegation, cancel, valid):
    from bt_api_py.service_config import ServiceConfigError
    from bt_api_py.gateway_direct_service import validate_service_options
    from bt_api_py.service_components import component_specs, validate_components
    monkeypatch.setattr(fixtures, '_free_endpoint', lambda: 'tcp://127.0.0.1:17359')
    _, _, config, _, _, _ = fixtures.make(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    opts = raw['components']['gateway']['settings']['options']
    opts.update(cancel_enabled=cancel, control_enabled=True, strategy_ledger_enabled=True,
        account_extensions_enabled=True, delegation_enabled=delegation, actor_source_v2_enabled=value)
    if valid:
        parsed = parse_service_config(raw)
        parsed_opts = parsed.components['gateway']['settings']['options']
        assert parsed_opts['actor_source_v2_enabled'] is value
        assert validate_service_options(parsed_opts, ('service.direct_write',))['actor_source_v2_enabled'] is value
        prepared, = validate_components(component_specs(parsed.components))
        assert ('direct.delegate_actor_status_v2' in prepared.operations_by_port['service.direct_write']) is value
    else:
        with pytest.raises(ServiceConfigError):
            parse_service_config(raw)
        with pytest.raises(ValueError):
            validate_service_options(opts, ('service.direct_write',))


def test_service_host_enabled_unbound_fails_closed(tmp_path, monkeypatch):
    from bt_api_py.gateway_delegate_actor_service import GatewayDelegateActorService
    from bt_api_py.service_components import ComponentResources
    from bt_api_py.service_host import ServiceHost, ServiceHostError
    import bt_api_transport_zmq.service_v2 as transport
    profile, _, config, authority = profile_for(tmp_path, monkeypatch)
    class NoNetworkServer:
        def __init__(self, *_args, **_kwargs):
            pytest.fail('unbound guard must fail before listener construction')
    monkeypatch.setattr(transport, 'ZmqServiceServer', NoNetworkServer)
    deployment = profile.deployment_factory()
    def provide(*args):
        resources = deployment._provide_resources(*args)
        values = dict(resources.values)
        values['delegate_actor_service'] = GatewayDelegateActorService(values['delegation_service'])
        return ComponentResources.owned(values, ready=resources.ready, close=resources.close)
    host = ServiceHost(config, authority, tmp_path / 'unbound-required.sqlite', provide)
    with pytest.raises(ServiceHostError, match='ACTOR_SOURCE_V2_UNAVAILABLE'):
        host.start()
    assert fixtures.FakeSession.instances[0].events[-1][0] == 'close'
