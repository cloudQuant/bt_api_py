"""Composition-bound, single-Registry ownership; no real account evidence."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest
from bt_api_base.service_protocol import WireScope
from bt_api_execution.contracts import ExecutionScope
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.unit.test_service_registry import _business, _identity, _next_request, _register

OPS = frozenset({'orders.submit'})


def scope(domain, account='wire-account', strategy='wire-strategy', run='run-a'):
    return WireScope(domain, account_ref=account, strategy_ref=strategy, run_id=run)


def setup_registry(path, scopes, *, migration_deployment_epoch=None):
    identities = tuple(_identity(principal_ref=f'p-{i}', actor_ref=f'actor-{i}') for i in range(len(scopes)))
    grants = {}
    for identity, wire in zip(identities, scopes):
        resource = ResourceGrant('instance', 'service.account_read', 'scope', replace(wire, run_id=None), OPS)
        grants[identity.principal_ref] = (PrincipalGrant(identity, (resource,)),)
    registry = ServiceRegistry(path, service_id='service', capabilities=(
        ServiceCapability('instance', 'service.account_read', OPS, OPS),), grants=grants,
        policy_version='p', requirements_version='r', lease_seconds=10, clock=lambda: 100.0,
        migration_deployment_epoch=migration_deployment_epoch)
    return registry, identities


def register(registry, identity, wire, key):
    return _register(registry, principal=identity.principal_ref, identity=identity, scope=wire,
        key=key, client_session_id=key, instance_id='instance', port='service.account_read',
        scope_ref='scope', operations=('orders.submit',))


def test_real_registry_serializes_authorized_cross_domain_registrations(tmp_path):
    scopes = (scope('domain-a'), scope('domain-b'))
    target = ExecutionScope('FAKE', 'test', 'actual-account', 'actual-strategy')
    registry, identities = setup_registry(tmp_path / 'registry.sqlite', scopes)
    try:
        registry.configure_strategy_canonical_targets(targets=dict.fromkeys(scopes, target))
        barrier = Barrier(2)
        def compete(i):
            barrier.wait(timeout=5)
            return register(registry, identities[i], scopes[i], f'key-{i}')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(compete, range(2)))
        assert sum(result['ok'] for result in results) == 1
        assert next(result for result in results if not result['ok'])['code'] == 'DUPLICATE_ACTIVE_RUN'
    finally:
        registry.close()


@pytest.mark.parametrize('target', [
    ExecutionScope('FAKE', 'test', 'other-account', 'actual-strategy'),
    ExecutionScope('FAKE', 'test', 'actual-account', 'other-strategy'),
])
def test_different_canonical_account_or_strategy_can_register(tmp_path, target):
    scopes = (scope('domain-a'), scope('domain-b'))
    registry, identities = setup_registry(tmp_path / 'registry.sqlite', scopes)
    try:
        registry.configure_strategy_canonical_targets(targets={scopes[0]:
            ExecutionScope('FAKE', 'test', 'actual-account', 'actual-strategy'), scopes[1]: target})
        assert register(registry, identities[0], scopes[0], 'first')['ok']
        assert register(registry, identities[1], scopes[1], 'second')['ok']
    finally:
        registry.close()


def test_legacy_claim_is_quarantined_per_canonical_identity(tmp_path):
    path = tmp_path / 'registry.sqlite'
    scopes = (scope('domain-a'), scope('domain-b'), scope('domain-c'))
    target = ExecutionScope('FAKE', 'test', 'actual-account', 'actual-strategy')
    other = replace(target, strategy_id='other')
    registry, identities = setup_registry(path, scopes)
    assert register(registry, identities[0], scopes[0], 'old')['ok']
    registry.close()
    registry, identities = setup_registry(path, scopes)
    try:
        registry.configure_strategy_canonical_targets(targets={scopes[0]: target, scopes[1]: target, scopes[2]: other})
        assert register(registry, identities[1], scopes[1], 'new')['code'] == 'POLICY_REQUIREMENT_UNMET'
        assert register(registry, identities[2], scopes[2], 'other')['ok']
        assert registry.strategy_write_run_id('domain-a', 'wire-strategy', 'wire-account') == 'run-a'
    finally:
        registry.close()


def test_persisted_mapping_cannot_be_reinterpreted_on_restart(tmp_path):
    path = tmp_path / 'registry.sqlite'
    wire = scope('domain-a')
    target = ExecutionScope('FAKE', 'test', 'actual-account', 'actual-strategy')
    registry, _ = setup_registry(path, (wire,))
    registry.configure_strategy_canonical_targets(targets={wire: target})
    registry.close()
    registry, _ = setup_registry(path, (wire,))
    try:
        with pytest.raises(ValueError, match='target changed'):
            registry.configure_strategy_canonical_targets(targets={wire: replace(target, account_ref='other')})
        registry.configure_strategy_canonical_targets(targets={wire: target})
    finally:
        registry.close()


def test_restart_retains_old_run_without_inferring_its_outcome(tmp_path):
    path = tmp_path / 'registry.sqlite'
    scopes = (scope('domain-a'), scope('domain-b'))
    target = ExecutionScope('FAKE', 'test', 'actual-account', 'actual-strategy')
    registry, identities = setup_registry(path, scopes)
    registry.configure_strategy_canonical_targets(targets=dict.fromkeys(scopes, target))
    assert register(registry, identities[0], scopes[0], 'old')['ok']
    registry.close()
    registry, identities = setup_registry(path, scopes)
    try:
        registry.configure_strategy_canonical_targets(targets=dict.fromkeys(scopes, target))
        different_run = register(registry, identities[1], replace(scopes[1], run_id='new-run'), 'new')
        assert different_run['code'] == 'DUPLICATE_ACTIVE_RUN'
        assert different_run['result']['strategy_write_run_id'] == 'run-a'
        assert register(registry, identities[1], scopes[1], 'resume')['ok']
    finally:
        registry.close()


def test_freeze_survives_domain_change(tmp_path):
    path = tmp_path / 'registry.sqlite'
    scopes = (scope('domain-a'), scope('domain-b'))
    target = ExecutionScope('FAKE', 'test', 'actual-account', 'actual-strategy')
    registry, identities = setup_registry(path, scopes)
    registry.configure_strategy_canonical_targets(targets=dict.fromkeys(scopes, target))
    assert register(registry, identities[0], scopes[0], 'first')['ok']
    registry.freeze_strategy(scopes[0], 'freeze')
    registry.close()
    registry, identities = setup_registry(path, scopes)
    try:
        registry.configure_strategy_canonical_targets(targets=dict.fromkeys(scopes, target))
        assert register(registry, identities[1], scopes[1], 'other')['code'] == 'STRATEGY_STOPPED'
    finally:
        registry.close()


def test_freeze_on_old_alias_fences_active_registration(tmp_path):
    scopes = (scope('domain-a'), scope('domain-b'))
    target = ExecutionScope('FAKE', 'test', 'actual-account', 'actual-strategy')
    registry, identities = setup_registry(tmp_path / 'registry.sqlite', scopes)
    registry.register_handler('instance', 'orders.submit', lambda context, args: {'accepted': True})
    try:
        registry.configure_strategy_canonical_targets(targets=dict.fromkeys(scopes, target))
        first = register(registry, identities[0], scopes[0], 'first')['result']
        released = registry.handle(identities[0].principal_ref, _next_request('service.unregister',
            {'registration_id': first['registration_id'], 'service_epoch': first['service_epoch']},
            identity=identities[0], scope=scopes[0]))
        assert released['ok']
        second = register(registry, identities[1], scopes[1], 'second')['result']
        registry.freeze_strategy(scopes[0], 'freeze-old-alias')
        result = _business(registry, second, principal=identities[1].principal_ref,
            identity=identities[1], scope=scopes[1], instance_id='instance')
        assert result['code'] == 'STRATEGY_STOPPED'
    finally:
        registry.close()
