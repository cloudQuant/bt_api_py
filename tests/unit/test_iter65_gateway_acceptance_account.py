"""Local Host/Registry/owner coverage with no transport listener or provider."""
from dataclasses import replace
import threading
import time

import pytest
import zmq
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_control import GatewayDirectControl
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_parent_authority import GatewayParentAuthority
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayControlGrant, GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.gateway_parent_service import GatewayParentGrant, GatewayParentService, GatewayParentSettings
from bt_api_py.gateway_parent_acceptance_service import GatewayParentAcceptanceGrant, GatewayParentAcceptanceService, OPERATION
from bt_api_py.service_components import (ComponentResources, ServiceComponentError, build_component,
                                          component_specs, validate_components)
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.integration.test_iter63_gateway_direct_service import _service_config
from tests.unit.test_iter63_gateway_delegate_service_client import _synthetic_owner_factory
from tests.unit.test_service_registry import _next_request, _register

KEY = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
IDENTITY = WireIdentity(WireRole.SERVICE, 'execution', 'actor')
RISK = WireIdentity(WireRole.SERVICE, 'risk', 'risk-actor')
APPLICATION = WireIdentity(WireRole.APPLICATION_CLIENT, 'application', 'app')
STRATEGY = WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-client', 'strategy')
SCOPE = WireScope('domain', account_ref='account')
OPTS = {'role': 'trading_owner', 'dispatch_enabled': True, 'control_enabled': True,
        'strategy_ledger_enabled': True, 'account_extensions_enabled': True,
        'parent_authority_enabled': True, 'parent_acceptance_order_enabled': True}


def parent_spec():
    return dict(account_scope='account', strategy_scope='strategy', parent_ref='parent',
        registration_operation_id='register', execution_domain_ref='domain', instrument=KEY.to_dict(),
        quantity='1', quantity_unit='lots', side='buy', offset='open', limit_price='100',
        creation_trading_day='20300101', allowed_trading_days=['20300101'], expires_at_ns=1000,
        grant_revision='old', target_position_ref=None, plan_digest='a' * 64,
        plan_version='v1', time_in_force='DAY')


@pytest.fixture
def host_env(tmp_path, monkeypatch):
    import bt_api_transport_zmq.service_v2 as transport
    class Listener:
        def __init__(self, endpoint, *_args, **_kwargs):
            self.endpoint = endpoint
        def close(self):
            pass
    monkeypatch.setattr(transport, 'ZmqServiceServer', Listener)
    directory = tmp_path / 'guard'
    directory.mkdir()
    parts, errors = {}, []
    def build(guard, generation):
        journal = GatewayDirectCommandJournal.create(tmp_path / 'journal', 'account', generation=generation)
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        journal.enable_account_extensions(guard)
        journal.enable_parent_authority(guard)
        authority = GatewayParentAuthority(journal, guard, now_ns=lambda: 100, trading_day=lambda: '20300101')
        from contextlib import nullcontext
        authority.register_parent(dict(parent_spec(), parent_ref='old', registration_operation_id='old'),
                                  principal='execution', actor_ref='actor', fence=nullcontext, recheck=lambda: True)
        journal.enable_parent_acceptance_order(guard)
        authority.register_parent(parent_spec(), principal='execution', actor_ref='actor',
                                  fence=nullcontext, recheck=lambda: True)
        authority.register_parent(dict(parent_spec(), parent_ref='foreign', registration_operation_id='foreign'),
                                  principal='foreign', actor_ref='actor', fence=nullcontext, recheck=lambda: True)
        authority.register_parent(dict(parent_spec(), parent_ref='domain', registration_operation_id='domain',
                                       execution_domain_ref='other'), principal='execution', actor_ref='actor',
                                  fence=nullcontext, recheck=lambda: True)
        control = GatewayDirectControl(journal, guard)
        dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, lambda *_: None,
                                             lambda _: ('key',), lambda _: True)
        parts.update(journal=journal, authority=authority, control=control)
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release)
    owner = GatewayDirectOwner(_synthetic_owner_factory(directory, build, errors),
                               queue_size=4, max_queue_wait_seconds=2, busy_timeout_seconds=2)
    assert owner.start(2), errors
    account_grant = GatewayParentAcceptanceGrant('gateway-a', 'scope-a', IDENTITY, 'account', 'domain',
                                                  'fifo-v1', frozenset({'strategy'}), frozenset({KEY}))
    parent_grant = GatewayParentGrant(IDENTITY, 'account', 'strategy', 'domain', 'execution', 'parent-v1',
                                      frozenset({KEY}), frozenset({'direct.parent.query'}))
    parent = GatewayParentService(GatewayParentSettings((parent_grant,)), owner=owner, journal=parts['journal'],
                                  authority=parts['authority'], instance_id='gateway-a', scope_ref='scope-a')
    acceptance = GatewayParentAcceptanceService((account_grant,), owner=owner, journal=parts['journal'],
        authority=parts['authority'], instance_id='gateway-a', scope_ref='scope-a')
    binding = GatewayDirectServiceBinding(parts['journal'], {'strategy': GatewayDirectGrant('strategy', frozenset({'rb'}))},
        owner=owner, control=parts['control'], control_grants={'operator': GatewayControlGrant(
            'operator', True, frozenset(), frozenset({'direct.control.status'}))})
    raw = _service_config('tcp://127.0.0.1:12345').to_public_dict()
    raw['components']['gateway']['settings']['options'] = OPTS
    config = parse_service_config(raw)
    server_public, server_secret = zmq.curve_keypair()
    client_public, _ = zmq.curve_keypair()
    risk_public, _ = zmq.curve_keypair()
    app_public, _ = zmq.curve_keypair()
    strategy_public, _ = zmq.curve_keypair()
    resource = ResourceGrant('gateway-a', 'service.direct_write', 'scope-a', SCOPE, frozenset({OPERATION}))
    strategy_resource = ResourceGrant('gateway-a', 'service.direct_write', 'scope-a',
        replace(SCOPE, strategy_ref='strategy'), frozenset({OPERATION}))
    authority = TrustedServiceAuthority(identity=WireIdentity(WireRole.SERVICE, 'gateway', 'gateway-service'),
        scope=WireScope('domain'), scope_ref='scope-a', scope_version='v1', security_ref='security-a',
        security_version='v1', registration_policy_ref='direct-policy-v1', registration_policy_version='v1',
        service_requirements_ref='direct-requirements-v1', service_requirements_version='v1',
        curve_public_key=server_public, curve_secret_key=server_secret,
        principals={client_public: 'execution', risk_public: 'risk', app_public: 'application',
                    strategy_public: 'strategy-client'},
        grants={'execution': (PrincipalGrant(IDENTITY, (resource,)),),
                'risk': (PrincipalGrant(RISK, (resource,)),),
                'application': (PrincipalGrant(APPLICATION, (resource,)),),
                'strategy-client': (PrincipalGrant(STRATEGY, (strategy_resource,)),)})
    host = ServiceHost(config, authority, tmp_path / 'registry', lambda *_: ComponentResources.borrowed(
        {'binding': binding, 'parent_service': parent, 'parent_acceptance_service': acceptance}, ready=lambda: True))
    try:
        host.start()
        registry = host._registry
        registration = _register(registry, identity=IDENTITY, scope=SCOPE, principal='execution', key='fifo',
            instance_id='gateway-a', port='service.direct_write', scope_ref='scope-a', operations=(OPERATION,))
        assert registration['ok'], registration
        issued = registration['result']
        def request(**changes):
            journal = parts['journal']
            arguments = dict(account_scope='account', strategy_scope='strategy', parent_ref='parent',
                registration_operation_id='register', expected_source=dict(account_scope='account',
                journal_id=journal.journal_id, gateway_generation=journal.generation, minimum_revision=0))
            arguments.update(changes)
            return registry.handle('execution', _next_request(OPERATION, dict(registration_id=issued['registration_id'],
                service_epoch=issued['service_epoch'], instance_id='gateway-a', arguments=arguments),
                identity=IDENTITY, scope=SCOPE))
        yield host, owner, account_grant, request
    finally:
        host.close()
        assert owner.close(2)


def test_plan_disabled_default_and_strict_boolean():
    raw = _service_config('tcp://127.0.0.1:12345').to_public_dict()
    raw['components']['gateway']['settings']['options'] = {k: v for k, v in OPTS.items() if k != 'parent_acceptance_order_enabled'}
    disabled = validate_components(component_specs(parse_service_config(raw).components))[0]
    assert OPERATION not in disabled.operations_by_port['service.direct_write']
    raw['components']['gateway']['settings']['options'] = dict(OPTS)
    enabled = validate_components(component_specs(parse_service_config(raw).components))[0]
    assert OPERATION in enabled.operations_by_port['service.direct_write']
    assert OPERATION not in enabled.write_operations_by_port['service.direct_write']
    raw['components']['gateway']['settings']['options']['parent_acceptance_order_enabled'] = 1
    with pytest.raises(ValueError):
        parse_service_config(raw)


def test_host_registry_observation_and_denials(host_env):
    host, _, grant, request = host_env
    result = request()
    assert result['ok'] and result['result']['status'] == 'OBSERVED', result
    assert result['result']['result']['sequence'] == 1
    assert request(parent_ref='old', registration_operation_id='old')['result']['result']['sequence'] is None
    for change in ({'account_scope': 'other'}, {'strategy_scope': 'other'}, {'parent_ref': 'other'},
                   {'registration_operation_id': 'other'}, {'expected_source': {'bad': 'source'}},
                   {'parent_ref': 'foreign', 'registration_operation_id': 'foreign'},
                   {'parent_ref': 'domain', 'registration_operation_id': 'domain'}):
        result = request(**change)
        assert not result['ok'] or result['result']['status'] != 'OBSERVED'
    assert host._registry.revoke_gateway_parent_acceptance(grant)
    assert not request()['ok']


def test_only_exact_service_scope_and_explicit_operation_register(host_env):
    host, _, grant, _ = host_env
    registry = host._registry
    def register(identity, scope, operations, key):
        return _register(registry, identity=identity, scope=scope, principal=identity.principal_ref, key=key,
            instance_id='gateway-a', port='service.direct_write', scope_ref='scope-a', operations=operations)
    for identity, scope in ((RISK, SCOPE), (APPLICATION, SCOPE),
                            (STRATEGY, replace(SCOPE, strategy_ref='strategy', run_id='run')),
                            (IDENTITY, replace(SCOPE, account_ref='other')),
                            (IDENTITY, replace(SCOPE, execution_domain_ref='other'))):
        assert not register(identity, scope, (OPERATION,), identity.principal_ref + scope.execution_domain_ref)['ok']
    assert not register(IDENTITY, SCOPE, (OPERATION, 'direct.parent.query'), 'mixed')['ok']
    with pytest.raises(ValueError):
        replace(grant, identity=APPLICATION)


def test_enabled_component_requires_bound_service_and_actual_extension(host_env):
    host, owner, grant, _ = host_env
    prepared = validate_components(component_specs(host._config.components))[0]
    resources = host._resource_provider(None, None, None, None).values
    with pytest.raises(ServiceComponentError, match='INVALID_PARENT_ACCEPTANCE_BINDING'):
        build_component(prepared, scope=WireScope('domain'), resource_provider=lambda *_:
            ComponentResources.borrowed({k: v for k, v in resources.items() if k != 'parent_acceptance_service'},
                                        ready=lambda: True))
    service = resources['parent_acceptance_service']
    with pytest.raises(ValueError, match='INVALID_ACCEPTANCE_SERVICE'):
        GatewayParentAcceptanceService((), owner=owner, journal=service.journal, authority=service.authority,
                                       instance_id='gateway-a', scope_ref='scope-a')
    service.journal._parent_acceptance_order_enabled = False
    try:
        with pytest.raises(ValueError, match='INVALID_ACCEPTANCE_SERVICE'):
            GatewayParentAcceptanceService((grant,), owner=owner, journal=service.journal,
                                           authority=service.authority, instance_id='gateway-a', scope_ref='scope-a')
    finally:
        service.journal._parent_acceptance_order_enabled = True


@pytest.mark.parametrize('field,value', [('account_scope', 'other'), ('journal_id', 'other'),
                                         ('gateway_generation', 'other'), ('minimum_revision', 2**62)])
def test_host_rejects_source_mismatch(host_env, field, value):
    _, _, _, request = host_env
    correct = request()['result']['source']
    expected = dict(account_scope=correct['account_scope'], journal_id=correct['journal_id'],
                    gateway_generation=correct['gateway_generation'], minimum_revision=0)
    expected[field] = value
    result = request(expected_source=expected)
    assert not result['ok'] or result['result']['status'] != 'OBSERVED'


def test_queued_revoke_reinstall_same_object_does_not_revive_admission(host_env):
    host, owner, grant, request = host_env
    entered, release, results = threading.Event(), threading.Event(), []
    def block():
        entered.set()
        assert release.wait(2)
    blocker = owner.submit_service_call(block)
    assert entered.wait(2)
    worker = threading.Thread(target=lambda: results.append(request()))
    worker.start()
    try:
        deadline = time.monotonic() + 0.8
        while owner.status().service_queue_depth != 1 and time.monotonic() < deadline:
            time.sleep(0.002)
        assert owner.status().service_queue_depth == 1
        assert host._registry.revoke_gateway_parent_acceptance(grant)
        host._registry.configure_gateway_parent_acceptance(grants=(grant,))
    finally:
        release.set()
        blocker.result(timeout_seconds=2)
        worker.join(2)
    assert results and (not results[0]['ok'] or results[0]['result']['status'] != 'OBSERVED')


@pytest.mark.parametrize('invalidate', ['principal', 'lease'])
def test_queued_lease_or_acl_invalidation_cannot_disclose(host_env, invalidate):
    host, owner, _, request = host_env
    entered, release, results = threading.Event(), threading.Event(), []
    def block():
        entered.set()
        assert release.wait(2)
    blocker = owner.submit_service_call(block)
    assert entered.wait(2)
    worker = threading.Thread(target=lambda: results.append(request()))
    worker.start()
    try:
        deadline = time.monotonic() + 0.8
        while owner.status().service_queue_depth != 1 and time.monotonic() < deadline:
            time.sleep(0.002)
        assert owner.status().service_queue_depth == 1
        if invalidate == 'principal':
            assert host._registry.revoke_principal('execution')
        else:
            with host._registry._lock:
                host._registry._db.execute("UPDATE sessions SET expires_at=0 WHERE principal='execution'")
    finally:
        release.set()
        blocker.result(timeout_seconds=2)
        worker.join(2)
    assert results and (not results[0]['ok'] or results[0]['result']['status'] != 'OBSERVED')


def test_ctp_profile_explicit_account_grant_and_acl_are_both_required(tmp_path, monkeypatch):
    import json
    import tests.test_iter63_ctp_gateway_trading as ctp_tests
    from tests.unit.test_iter63_ctp_gateway_local_profile import (
        _fixture, _composing_config, _composing_doc, _execution_delegate,
    )
    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources
    from bt_api_py.ctp_gateway_trading import build_ctp_gateway_trading_profile, CtpGatewayTradingError
    from bt_api_py.ctp_gateway_operator_profile import create_ctp_gateway_profile_factory
    monkeypatch.setattr(ctp_tests, '_free_endpoint', lambda: 'tcp://127.0.0.1:23456')
    doc, file, config = _fixture(tmp_path, monkeypatch)
    _, config = _composing_config(config, delegation_enabled=True, parent_authority_enabled=True,
                                  parent_acceptance_order_enabled=True)
    _composing_doc(doc)
    strategy = doc['clients'][0]['identity']['actor_ref']
    instrument = doc['returns']['instruments'][0]
    doc['delegation'] = {'delegates': [_execution_delegate(doc, strategy, instrument)]}
    file.write_text(json.dumps(doc), encoding='utf-8')
    resources = load_simnow_trading_resources(config)
    parent_grant = resources.parent_settings.grants[0]
    grant = GatewayParentAcceptanceGrant('gateway-a', 'scope-a', parent_grant.identity,
        resources.settings.account_scope, resources.authority.scope.execution_domain_ref, 'fifo-rev',
        frozenset({strategy}), frozenset({MarketInstrumentKey.from_dict(instrument)}))
    principal = grant.identity.principal_ref
    entry, = resources.authority.grants[principal]
    account_scope = WireScope(grant.execution_domain_ref, account_ref=grant.account_scope)
    account_resource = ResourceGrant('gateway-a', 'service.direct_write', 'scope-a', account_scope,
                                     frozenset({OPERATION}))
    all_grants = dict(resources.authority.grants)
    all_grants[principal] = (replace(entry, resources=entry.resources + (account_resource,)),)
    authority = replace(resources.authority, grants=all_grants)
    def profile(auth, account_grants):
        return build_ctp_gateway_trading_profile(config, auth, resources.registry_path,
            profile_ref='test-fifo', settings=resources.settings, grants=resources.grants,
            request_feed_factory=resources.request_feed_factory, market_stream_factory=resources.market_stream_factory,
            assert_admitted=resources.assert_admitted, return_settings=resources.return_settings,
            control_grants=resources.control_grants, delegation_settings=resources.delegation_settings,
            parent_settings=resources.parent_settings, parent_authority_factory=resources.parent_authority_factory,
            acceptance_grants=account_grants)
    assert profile(authority, (grant,)) is not None
    with pytest.raises(CtpGatewayTradingError):
        profile(authority, ())
    # An account grant alone cannot replace its dedicated ResourceGrant.
    with pytest.raises(CtpGatewayTradingError):
        profile(resources.authority, (grant,))
    trusted_resources = replace(resources, authority=authority, acceptance_grants=(grant,))
    factory = create_ctp_gateway_profile_factory('test-fifo', resource_loader=lambda _config: trusted_resources)
    assert factory.create_deployment(config).state == 'new'
    for invalid in (replace(trusted_resources, acceptance_grants=()),
                    replace(trusted_resources, authority=resources.authority)):
        factory = create_ctp_gateway_profile_factory('test-fifo', resource_loader=lambda _config, v=invalid: v)
        with pytest.raises(CtpGatewayTradingError):
            factory.create_deployment(config)
    with pytest.raises(ValueError):
        replace(trusted_resources, upgrade_parent_acceptance_order=1)
    unsupported_upgrade = replace(trusted_resources, upgrade_parent_acceptance_order=True)
    factory = create_ctp_gateway_profile_factory('test-fifo', resource_loader=lambda _config: unsupported_upgrade)
    with pytest.raises(CtpGatewayTradingError, match='PARENT_ACCEPTANCE_MIGRATION_PROOF_REQUIRED'):
        factory.create_deployment(config)
