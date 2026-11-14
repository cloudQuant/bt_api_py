"""Synthetic Registry subject pinning; no network or real accounts."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import subprocess
import sys
from threading import Barrier

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution.contracts import ExecutionScope
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.unit.test_service_registry import _business, _next_request, _register

A = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal-a', 'strategy')
B = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal-b', 'strategy')
SCOPE = WireScope('domain-a', account_ref='account', strategy_ref='strategy', run_id='run-a')
ALIAS = replace(SCOPE, execution_domain_ref='domain-b')
DIRECT = replace(SCOPE, strategy_ref='ordinary')
UNKNOWN = replace(SCOPE, strategy_ref='unknown')
OTHER = replace(SCOPE, account_ref='unprotected')
TARGET = ExecutionScope('FAKE', 'test', 'canonical-account', 'canonical-strategy')
OPS = frozenset({'orders.submit', 'account.read'})


def make_registry(path, *, identities=(A, B), clock=lambda: 100.0):
    grants = {}
    for identity in identities:
        resources = tuple(ResourceGrant('instance', 'service.account_read', 'scope',
                                        replace(scope, run_id=None), OPS)
                          for scope in (SCOPE, ALIAS, DIRECT, UNKNOWN, OTHER))
        grants[identity.principal_ref] = (PrincipalGrant(identity, resources),)
    registry = ServiceRegistry(path, service_id='service', capabilities=(
        ServiceCapability('instance', 'service.account_read', OPS, frozenset({'orders.submit'})),),
        grants=grants, policy_version='p', requirements_version='r', lease_seconds=10, clock=clock)
    registry.register_handler('instance', 'orders.submit', lambda context, args: {'ok': True})
    return registry


def configure(registry):
    registry.configure_execution_subject_scopes(protected_accounts=frozenset({'account'}), aliases={
        replace(SCOPE, run_id=None): TARGET, replace(ALIAS, run_id=None): TARGET,
        replace(DIRECT, run_id=None): None,
    })


def register(registry, identity=A, scope=SCOPE, key='key', operations=('orders.submit',)):
    return _register(registry, principal=identity.principal_ref, identity=identity, scope=scope,
        key=key, client_session_id=key, instance_id='instance', port='service.account_read',
        scope_ref='scope', operations=operations)


def release(registry, identity, scope, receipt):
    return registry.handle(identity.principal_ref, _next_request('service.unregister',
        {'registration_id': receipt['registration_id'], 'service_epoch': receipt['service_epoch']},
        identity=identity, scope=scope))


def test_subject_pin_survives_release_and_restart_without_disclosing_run(tmp_path):
    path = tmp_path / 'registry.sqlite'
    registry = make_registry(path)
    configure(registry)
    receipt = register(registry)['result']
    assert not register(registry, B, ALIAS, 'b-active')['ok']
    assert release(registry, A, SCOPE, receipt)['ok']
    denied = register(registry, B, ALIAS, 'b-after')
    assert not denied['ok'] and 'strategy_write_run_id' not in str(denied)
    denied = register(registry, B, replace(ALIAS, run_id='run-b'), 'b-other')
    assert not denied['ok'] and 'strategy_write_run_id' not in str(denied)
    suggestion = register(registry, A, replace(ALIAS, run_id='run-new'), 'a-new')
    assert suggestion['result']['strategy_write_run_id'] == 'run-a'
    assert register(registry, A, ALIAS, 'a-reconnect')['ok']
    registry.close()
    registry = make_registry(path)
    try:
        configure(registry)
        denied = register(registry, B, ALIAS, 'b-restart')
        assert not denied['ok'] and 'strategy_write_run_id' not in str(denied)
        assert register(registry, A, SCOPE, 'a-restart')['ok']
    finally:
        registry.close()


def test_competing_subjects_and_expiration_do_not_transfer_pin(tmp_path):
    now = [100.0]
    registry = make_registry(tmp_path / 'registry.sqlite', clock=lambda: now[0])
    try:
        configure(registry)
        barrier = Barrier(2)
        def compete(item):
            identity, wire = item
            barrier.wait(timeout=5)
            return register(registry, identity, wire, identity.principal_ref)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(compete, ((A, SCOPE), (B, ALIAS))))
        assert sum(result['ok'] for result in results) == 1
        winner = A if results[0]['ok'] else B
        loser = B if winner is A else A
        now[0] += 11
        denial = register(registry, loser, SCOPE, 'after-expiry')
        assert not denial['ok'] and 'strategy_write_run_id' not in str(denial)
        assert register(registry, winner, SCOPE, 'winner-after-expiry')['ok']
    finally:
        registry.close()


def test_unknown_historical_subject_and_unknown_alias_fail_closed(tmp_path):
    path = tmp_path / 'registry.sqlite'
    registry = make_registry(path)
    assert register(registry)['ok']
    registry.close()
    registry = make_registry(path)
    try:
        configure(registry)
        assert register(registry, A, ALIAS, 'historic')['code'] == 'POLICY_REQUIREMENT_UNMET'
        assert register(registry, A, UNKNOWN, 'unknown')['code'] == 'POLICY_REQUIREMENT_UNMET'
        assert register(registry, A, DIRECT, 'direct')['ok']
        assert register(registry, A, OTHER, 'unprotected')['ok']
        assert registry._db.execute('SELECT count(*) FROM execution_subject_pins').fetchone()[0] == 0
    finally:
        registry.close()


def test_unclassified_domain_history_remains_unknown_after_restart_and_row_deletion(tmp_path):
    path = tmp_path / 'registry.sqlite'
    registry = make_registry(path)
    assert register(registry, A, SCOPE, 'legacy')['ok']
    registry.close()
    registry = make_registry(path)
    try:
        # domain-a is deliberately absent; it must not be treated as empty
        # history when domain-b is later selected.
        registry.configure_execution_subject_scopes(protected_accounts=frozenset({'account'}), aliases={
            replace(ALIAS, run_id=None): TARGET, replace(DIRECT, run_id=None): None})
        assert register(registry, B, ALIAS, 'takeover')['code'] == 'POLICY_REQUIREMENT_UNMET'
        assert registry._db.execute('SELECT count(*) FROM execution_subject_pins').fetchone()[0] == 0
        with registry._transaction():
            registry._db.execute("DELETE FROM strategy_write_claims WHERE execution_domain_ref='domain-a'")
        assert register(registry, B, ALIAS, 'after-delete')['code'] == 'POLICY_REQUIREMENT_UNMET'
        assert register(registry, A, DIRECT, 'ordinary')['ok']
    finally:
        registry.close()
    registry = make_registry(path)
    try:
        assert register(registry, B, ALIAS, 'after-restart')['code'] == 'POLICY_REQUIREMENT_UNMET'
        assert registry._db.execute('SELECT count(*) FROM execution_subject_pins').fetchone()[0] == 0
    finally:
        registry.close()


@pytest.mark.parametrize('deleted', ['pin', 'claim'])
def test_single_sided_subject_record_deletion_cannot_reseed(tmp_path, deleted):
    path = tmp_path / 'registry.sqlite'
    registry = make_registry(path)
    configure(registry)
    assert register(registry)['ok']
    registry.close()
    registry = make_registry(path)
    try:
        with registry._transaction():
            registry._db.execute('DELETE FROM ' + ('execution_subject_pins' if deleted == 'pin'
                                                   else 'strategy_write_claims'))
        assert not register(registry, B, ALIAS, 'b')['ok']
        assert not register(registry, A, SCOPE, 'a')['ok']
    finally:
        registry.close()


def test_later_unclassified_domain_claim_blocks_existing_pin(tmp_path):
    path = tmp_path / 'registry.sqlite'
    registry = make_registry(path)
    configure(registry)
    assert register(registry)['ok']
    registry.close()
    registry = make_registry(path)
    try:
        with registry._transaction():
            registry._db.execute('INSERT INTO strategy_write_claims '
                '(execution_domain_ref,strategy_ref,account_ref,run_id,created_at) VALUES(?,?,?,?,?)',
                ('unclassified-domain', 'strategy', 'account', 'run-a', 100.0))
        assert register(registry, A, SCOPE, 'resume')['code'] == 'POLICY_REQUIREMENT_UNMET'
        assert not register(registry, B, ALIAS, 'takeover')['ok']
    finally:
        registry.close()


def test_revoked_identity_cannot_register_after_restart(tmp_path):
    path = tmp_path / 'registry.sqlite'
    registry = make_registry(path)
    configure(registry)
    assert register(registry)['ok']
    registry.close()
    registry = make_registry(path, identities=(B,))
    try:
        assert not register(registry, A, SCOPE, 'revoked')['ok']
        assert not register(registry, B, SCOPE, 'replacement')['ok']
    finally:
        registry.close()


@pytest.mark.parametrize('revoked', ['resource', 'handler'])
def test_revoke_between_preflight_and_transaction_creates_no_pin(tmp_path, monkeypatch, revoked):
    registry = make_registry(tmp_path / 'registry.sqlite')
    try:
        configure(registry)
        original = registry._authorized_resource
        calls = []
        def revoke_after_first_check(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(True)
            if len(calls) == 1:
                if revoked == 'resource':
                    grant = registry._grants[A.principal_ref][0]
                    restricted = tuple(replace(resource, operations=frozenset({'account.read'}))
                                       for resource in grant.resources)
                    registry._grants = dict(registry._grants)
                    registry._grants[A.principal_ref] = (PrincipalGrant(A, restricted),)
                else:
                    del registry._handlers[('instance', 'orders.submit')]
            return result
        monkeypatch.setattr(registry, '_authorized_resource', revoke_after_first_check)
        assert not register(registry)['ok']
        assert len(calls) == 2
        assert registry._db.execute('SELECT count(*) FROM execution_subject_pins').fetchone()[0] == 0
        assert registry._db.execute('SELECT count(*) FROM strategy_write_claims').fetchone()[0] == 0
    finally:
        registry.close()


def test_configuration_is_exact_and_immutable(tmp_path):
    registry = make_registry(tmp_path / 'registry.sqlite')
    try:
        configure(registry)
        configure(registry)
        with pytest.raises(ValueError, match='changed'):
            registry.configure_execution_subject_scopes(protected_accounts=frozenset({'account'}),
                aliases={replace(SCOPE, run_id=None): TARGET})
    finally:
        registry.close()


@pytest.mark.parametrize('second', [None, replace(TARGET, strategy_id='other-canonical')])
def test_cross_domain_same_strategy_inconsistent_classification_rejected(tmp_path, second):
    registry = make_registry(tmp_path / 'registry.sqlite')
    try:
        with pytest.raises(ValueError, match='cross-domain'):
            registry.configure_execution_subject_scopes(protected_accounts=frozenset({'account'}), aliases={
                replace(SCOPE, run_id=None): TARGET, replace(ALIAS, run_id=None): second,
                replace(DIRECT, run_id=None): None})
        assert registry._db.execute('SELECT count(*) FROM execution_subject_aliases').fetchone()[0] == 0
    finally:
        registry.close()


def test_damaged_cross_domain_classification_refuses_both_selected_and_direct(tmp_path):
    registry = make_registry(tmp_path / 'registry.sqlite')
    try:
        configure(registry)
        with registry._transaction():
            registry._db.execute("UPDATE execution_subject_aliases SET canonical_account_key=NULL, "
                "canonical_strategy_id=NULL WHERE execution_domain_ref='domain-b' AND strategy_ref='strategy'")
        assert register(registry, A, SCOPE, 'selected')['code'] == 'POLICY_REQUIREMENT_UNMET'
        assert register(registry, B, ALIAS, 'bypass')['code'] == 'POLICY_REQUIREMENT_UNMET'
        assert register(registry, A, DIRECT, 'ordinary')['ok']
        assert registry._db.execute('SELECT count(*) FROM execution_subject_pins').fetchone()[0] == 0
    finally:
        registry.close()


@pytest.mark.parametrize('order', ['before', 'after'])
def test_canonical_mapping_cannot_reclassify_protected_alias(tmp_path, order):
    registry = make_registry(tmp_path / 'registry.sqlite')
    try:
        alias = replace(ALIAS, run_id=None)
        conflicting = replace(TARGET, strategy_id='different')
        if order == 'before':
            registry.configure_strategy_canonical_targets(targets={alias: conflicting})
            with pytest.raises(ValueError, match='conflicting canonical'):
                configure(registry)
        else:
            configure(registry)
            with pytest.raises(ValueError, match='conflicting canonical'):
                registry.configure_strategy_canonical_targets(targets={alias: conflicting})
            registry.configure_strategy_canonical_targets(targets={alias: TARGET})
            with pytest.raises(ValueError, match='conflicting canonical'):
                registry.configure_strategy_canonical_targets(targets={replace(UNKNOWN, run_id=None): TARGET})
    finally:
        registry.close()


def test_quarantined_alias_and_forged_transport_cannot_pin(tmp_path):
    registry = make_registry(tmp_path / 'registry.sqlite')
    try:
        registry.configure_strategy_canonical_targets(targets={replace(SCOPE, run_id=None): TARGET})
        configure(registry)
        with registry._transaction():
            registry._db.execute('UPDATE strategy_canonical_bindings SET quarantined=1')
        assert register(registry)['code'] == 'POLICY_REQUIREMENT_UNMET'
        with registry._transaction():
            registry._db.execute('UPDATE strategy_canonical_bindings SET quarantined=0')
        forged = _register(registry, principal=B.principal_ref, identity=A, scope=SCOPE, key='forged',
            client_session_id='forged', instance_id='instance', port='service.account_read',
            scope_ref='scope', operations=('orders.submit',))
        assert not forged['ok']
        assert registry._db.execute('SELECT count(*) FROM execution_subject_pins').fetchone()[0] == 0
    finally:
        registry.close()


def test_subprocess_owner_exclusion_and_pin_after_reopen(tmp_path):
    path = tmp_path / 'registry.sqlite'
    registry = make_registry(path)
    configure(registry)
    assert register(registry)['ok']
    script = '''
import sys
from pathlib import Path
from tests.unit.test_iter65_execution_subject_pin import make_registry, register, B, SCOPE
from bt_api_py.service_registry import ServiceRegistryBusyError
try:
    registry = make_registry(Path(sys.argv[1]))
except ServiceRegistryBusyError:
    print("BUSY")
else:
    try:
        answer = register(registry, B, SCOPE, "child")
        print("DENIED" if not answer["ok"] and "strategy_write_run_id" not in str(answer) else "UNEXPECTED")
    finally:
        registry.close()
'''
    try:
        blocked = subprocess.run([sys.executable, '-c', script, str(path)], text=True,
                                 capture_output=True, timeout=10, check=True)
        assert blocked.stdout.strip() == 'BUSY'
    finally:
        registry.close()
    reopened = subprocess.run([sys.executable, '-c', script, str(path)], text=True,
                              capture_output=True, timeout=10, check=True)
    assert reopened.stdout.strip() == 'DENIED'


def test_gateway_direct_writes_and_operator_cancel_stay_separately_authorized(tmp_path):
    operator = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'operator')
    account_scope = WireScope('domain-a', account_ref='account')
    gateway_ops = frozenset({'direct.reserve', 'direct.cancel', 'direct.control.cancel_scope',
                             'direct.control.status'})
    strategy_ops = frozenset({'direct.reserve', 'direct.cancel'})
    control_ops = frozenset({'direct.control.cancel_scope', 'direct.control.status'})
    grants = {}
    for identity in (A, B):
        grants[identity.principal_ref] = (PrincipalGrant(identity, tuple(
            ResourceGrant('gateway', 'service.direct_write', 'scope', replace(wire, run_id=None), strategy_ops)
            for wire in (SCOPE, DIRECT))),)
    grants[operator.principal_ref] = (PrincipalGrant(operator, (ResourceGrant(
        'gateway', 'service.direct_write', 'operator-scope', account_scope, control_ops),)),)
    registry = ServiceRegistry(tmp_path / 'gateway.sqlite', service_id='gateway', capabilities=(
        ServiceCapability('gateway', 'service.direct_write', gateway_ops,
                          gateway_ops - {'direct.control.status'}),), grants=grants,
        policy_version='p', requirements_version='r', lease_seconds=10, clock=lambda: 100.0)
    for operation in gateway_ops:
        registry.register_handler('gateway', operation, lambda context, args: {'accepted': True})
    def gateway_register(identity, wire, key, operations, scope_ref='scope'):
        return _register(registry, principal=identity.principal_ref, identity=identity, scope=wire, key=key,
            client_session_id=key, instance_id='gateway', port='service.direct_write', scope_ref=scope_ref,
            operations=operations)
    def invoke(identity, wire, receipt, operation):
        return registry.handle(identity.principal_ref, _next_request(operation, {
            'registration_id': receipt['registration_id'], 'service_epoch': receipt['service_epoch'],
            'instance_id': 'gateway', 'arguments': {}}, identity=identity, scope=wire))
    try:
        configure(registry)
        subject = gateway_register(A, SCOPE, 'subject', ('direct.reserve', 'direct.cancel'))
        assert subject['ok']
        assert invoke(A, SCOPE, subject['result'], 'direct.reserve')['ok']
        assert invoke(A, SCOPE, subject['result'], 'direct.cancel')['ok']
        assert not gateway_register(B, SCOPE, 'other', ('direct.reserve',))['ok']
        assert gateway_register(B, DIRECT, 'ordinary', ('direct.reserve',))['ok']
        control = gateway_register(operator, account_scope, 'operator', tuple(control_ops), 'operator-scope')
        assert control['ok']
        assert invoke(operator, account_scope, control['result'], 'direct.control.cancel_scope')['ok']
        assert invoke(operator, account_scope, control['result'], 'direct.control.status')['ok']
    finally:
        registry.close()


def test_active_session_rechecks_pin_on_retry_and_dispatch(tmp_path):
    registry = make_registry(tmp_path / 'registry.sqlite')
    try:
        configure(registry)
        receipt = register(registry)['result']
        with registry._transaction():
            registry._db.execute('DELETE FROM execution_subject_pins')
        assert not register(registry)['ok']
        result = _business(registry, receipt, identity=A, scope=SCOPE, principal=A.principal_ref,
                           instance_id='instance')
        assert not result['ok']
    finally:
        registry.close()


@pytest.mark.parametrize('historical', ['before_config', 'after_config'])
def test_history_quarantines_every_alias_of_same_canonical(tmp_path, historical):
    path = tmp_path / 'registry.sqlite'
    y_scope = replace(SCOPE, account_ref='account-y', strategy_ref='strategy-y')
    y_identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal-y', 'strategy-y')
    def allow_y(registry):
        resource = ResourceGrant('instance', 'service.account_read', 'scope', replace(y_scope, run_id=None), OPS)
        registry._grants = dict(registry._grants)
        registry._grants[y_identity.principal_ref] = (PrincipalGrant(y_identity, (resource,)),)
    registry = make_registry(path)
    if historical == 'before_config':
        assert register(registry, A, SCOPE, 'legacy')['ok']
        registry.close()
        registry = make_registry(path)
    try:
        allow_y(registry)
        registry.configure_execution_subject_scopes(protected_accounts=frozenset({'account', 'account-y'}),
            aliases={replace(SCOPE, run_id=None): TARGET, replace(y_scope, run_id=None): TARGET})
        if historical == 'after_config':
            with registry._transaction():
                registry._db.execute('INSERT INTO strategy_write_claims '
                    '(execution_domain_ref,strategy_ref,account_ref,run_id,created_at) VALUES(?,?,?,?,?)',
                    ('unknown-domain', 'strategy', 'account', 'run-a', 100.0))
        assert register(registry, y_identity, y_scope, 'y')['code'] == 'POLICY_REQUIREMENT_UNMET'
        if historical == 'before_config':
            with registry._transaction():
                registry._db.execute('DELETE FROM strategy_write_claims')
            assert register(registry, y_identity, y_scope, 'after-delete')['code'] == 'POLICY_REQUIREMENT_UNMET'
    finally:
        registry.close()
    if historical == 'before_config':
        registry = make_registry(path)
        try:
            allow_y(registry)
            assert register(registry, y_identity, y_scope, 'after-restart')['code'] == 'POLICY_REQUIREMENT_UNMET'
        finally:
            registry.close()
