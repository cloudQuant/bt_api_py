"""Synthetic local management plan disclosure; no live account or transport."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
import sqlite3

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_execution.contracts import canonical_json
from bt_api_execution.frozen_algorithm_plan import algorithm_plan_digest
from bt_api_py.execution_management_plan_contract import (
    MAX_PLAN_AUDIT_RESULT_BYTES, MAX_PLAN_BYTES, validate_plan_json,
)
from bt_api_py.execution_management_read_grant import ExecutionManagementReadGrant, PARENT_READ_V1, PARENT_PLAN_READ_V1
from bt_api_py.execution_management_read_service import ExecutionManagementReadService, ExecutionManagementReadServiceError, PORT
from bt_api_py.execution_parent_grant import SUBMIT
from tests.unit.test_service_registry import _next_request, _register
from test_iter65_atomic_submission_intake import atomic  # noqa: F401
from test_iter65_fresh_submission_intake import KEY, OTHER


@pytest.fixture
def plan_case(tmp_path, atomic):
    rig, store, _ = atomic
    request = rig.parent()
    assert rig.call(SUBMIT, request)['ok']
    app = WireIdentity(WireRole.APPLICATION_CLIENT, 'management-principal', 'management-actor')
    scope = replace(rig.scope, run_id=None)
    grant = ExecutionManagementReadGrant('management-instance', app, scope, 'policy-v1',
        frozenset({KEY}), frozenset({PARENT_READ_V1, PARENT_PLAN_READ_V1}))
    audit = tmp_path / 'audit'
    audit.mkdir(mode=0o700)
    service = ExecutionManagementReadService(host=rig.host, local_scope=rig.target, grant=grant,
        scope_ref='scope', service_id='management-service', authenticated_principal=app.principal_ref,
        audit_root=audit, lease_seconds=10)
    result = _register(service, identity=app, scope=scope, principal=app.principal_ref,
        instance_id=grant.instance_id, port=PORT, scope_ref='scope',
        operations=(PARENT_READ_V1, PARENT_PLAN_READ_V1))
    assert result['ok'], result
    yield service, grant, result['result'], request, audit, rig, store
    service.close()


def call(service, grant, registration, operation=PARENT_PLAN_READ_V1, *, query_id='q', parent='parent-1'):
    return service.handle(grant.identity.principal_ref, _next_request(operation, {
        'registration_id': registration['registration_id'], 'service_epoch': registration['service_epoch'],
        'instance_id': grant.instance_id, 'arguments': {'schema_version': 1, 'parent_ref': parent,
        'query_id': query_id, 'grant_revision': grant.revision}}, identity=grant.identity, scope=grant.scope))


def attempts(path):
    with sqlite3.connect(path / 'execution-management-read.sqlite3') as db:
        return db.execute('SELECT operation_version,query_id,outcome FROM audit_attempts ORDER BY sequence').fetchall()


def test_original_plan_and_instrument_are_audited_without_source_identity(plan_case, monkeypatch):
    service, grant, reg, request, audit, rig, store = plan_case
    from bt_api_execution.algorithm_config import ScheduleRegistry
    monkeypatch.setattr(ScheduleRegistry, 'create', lambda *_a, **_k: pytest.fail('extension executed'))
    before = store._connection.total_changes
    reply = call(service, grant, reg)
    assert reply['ok'], reply
    result = reply['result']['snapshot']
    assert result['status'] == 'OBSERVED' and result['source'] == 'LOCAL_JOURNAL'
    assert json.loads(result['plan_json']) == request['algorithm_plan']
    assert result['instrument'] == request['spec']['instrument']
    assert result['declared_build_id'] == request['algorithm_plan']['build_id']
    assert 'identity' not in result and 'request_sha256' not in result and 'request_hex' not in result
    assert attempts(audit) == [(2, 'q', 'OBSERVED')]
    assert store._connection.total_changes == before


def test_plan_permission_is_independent_of_parent_read(tmp_path, atomic):
    rig, _, _ = atomic
    assert rig.call(SUBMIT, rig.parent())['ok']
    app = WireIdentity(WireRole.APPLICATION_CLIENT, 'management-principal', 'management-actor')
    scope = replace(rig.scope, run_id=None)
    grant = ExecutionManagementReadGrant('management-instance', app, scope, 'policy-v1',
        frozenset({KEY}), frozenset({PARENT_READ_V1}))
    audit = tmp_path / 'audit'; audit.mkdir(mode=0o700)
    service = ExecutionManagementReadService(host=rig.host, local_scope=rig.target, grant=grant,
        scope_ref='scope', service_id='management-service', authenticated_principal=app.principal_ref, audit_root=audit)
    try:
        reg = _register(service, identity=app, scope=scope, principal=app.principal_ref,
            instance_id=grant.instance_id, port=PORT, scope_ref='scope', operations=(PARENT_READ_V1,))
        assert reg['ok']
        denied = _register(service, identity=app, scope=scope, principal=app.principal_ref, key='plan-key',
            instance_id=grant.instance_id, port=PORT, scope_ref='scope', operations=(PARENT_PLAN_READ_V1,))
        assert not denied['ok']
        assert not call(service, grant, reg['result'])['ok']
        assert attempts(audit) == []
        with pytest.raises(ExecutionManagementReadServiceError, match='INVALID_CONFIGURATION'):
            service.replace_grant(grant, replace(grant, operations=frozenset({PARENT_READ_V1, PARENT_PLAN_READ_V1})))
        assert call(service, grant, reg['result'], PARENT_READ_V1)['ok']
    finally:
        service.close()


def test_plan_query_id_cannot_cross_operations(plan_case):
    service, grant, reg, _, audit, _, _ = plan_case
    assert call(service, grant, reg, PARENT_READ_V1, query_id='same')['ok']
    assert not call(service, grant, reg, PARENT_PLAN_READ_V1, query_id='same')['ok']
    assert call(service, grant, reg, PARENT_PLAN_READ_V1, query_id='plan')['ok']
    assert not call(service, grant, reg, PARENT_READ_V1, query_id='plan')['ok']
    assert attempts(audit) == [(1, 'same', 'OBSERVED'), (2, 'plan', 'OBSERVED')]


def test_unauthorized_instrument_missing_and_bad_plan_are_same_unknown(plan_case, monkeypatch):
    service, grant, reg, _, audit, rig, _ = plan_case
    missing = call(service, grant, reg, query_id='missing', parent='missing')['result']['snapshot']
    assert missing['status'] == 'UNKNOWN'
    narrower = replace(grant, revision='policy-v2', instruments=frozenset({OTHER}))
    assert service.replace_grant(grant, narrower)
    denied = call(service, narrower, reg, query_id='denied')['result']['snapshot']
    assert denied == missing
    assert attempts(audit) == [(2, 'missing', 'UNKNOWN'), (2, 'denied', 'UNKNOWN')]


@pytest.mark.parametrize('when', ['after_snapshot', 'after_audit'])
def test_revocation_before_disclosure_never_returns_plan(plan_case, monkeypatch, when):
    service, grant, reg, _, audit, _, _ = plan_case
    if when == 'after_snapshot':
        reader = service._ExecutionManagementReadService__reader
        original = reader.query_plan
        def revoke_after_read(*args):
            value = original(*args)
            assert service.revoke_grant(grant)
            return value
        monkeypatch.setattr(reader, 'query_plan', revoke_after_read)
    else:
        journal = service._ExecutionManagementReadService__audit
        original = journal.disclose_after_audit
        def revoke_after_commit(**kwargs):
            value = original(**kwargs)
            assert service.revoke_grant(grant)
            return value
        monkeypatch.setattr(journal, 'disclose_after_audit', revoke_after_commit)
    result = call(service, grant, reg)
    assert not result['ok'] and not result['result']
    assert len(attempts(audit)) == (1 if when == 'after_audit' else 0)


def test_audit_failure_never_discloses_plan(plan_case, monkeypatch):
    service, grant, reg, _, audit, _, _ = plan_case
    journal = service._ExecutionManagementReadService__audit
    monkeypatch.setattr(journal, 'disclose_after_audit', lambda **_: (_ for _ in ()).throw(OSError()))
    result = call(service, grant, reg)
    assert not result['ok'] and not result['result'] and attempts(audit) == []


@pytest.mark.parametrize('name,params', [('PASS', {}), ('SLICE', {'maximum': '3'}),
    ('TWAP', {'slots': [{'start_ns': 1, 'end_ns': 2, 'quantity': '2'}]})])
def test_exact_builtin_validator_accepts_valid_plan(name, params, plan_case):
    _, _, _, request, _, _, _ = plan_case
    plan = deepcopy(request['algorithm_plan'])
    plan['schedule']['class'], plan['schedule']['params'] = name, params
    data = canonical_json(plan).encode('ascii')
    assert validate_plan_json(data, digest=algorithm_plan_digest(plan), version=plan['plan_version'],
                              build_id=plan['build_id']) == data.decode('ascii')


@pytest.mark.parametrize('damage', ['class', 'version', 'extra', 'params', 'retry', 'grid', 'slots'])
def test_validator_rejects_unknown_or_malformed_without_extension(damage, plan_case):
    _, _, _, request, _, _, _ = plan_case
    plan = deepcopy(request['algorithm_plan'])
    schedule = plan['schedule']
    if damage == 'class': schedule['class'] = 'EXTENSION'
    elif damage == 'version': schedule['version'] = 2
    elif damage == 'extra': schedule['unexpected'] = 'x'
    elif damage == 'params': schedule['params'] = {'unexpected': 1}
    elif damage == 'retry': schedule['binding']['retry_limit'] = True
    elif damage == 'grid': schedule['binding']['price_step'] = '3'
    else: schedule.update({'class': 'TWAP', 'params': {'slots': [{'start_ns': 2, 'end_ns': 1, 'quantity': '1'}]}})
    data = canonical_json(plan).encode('ascii')
    with pytest.raises((ValueError, TypeError)):
        validate_plan_json(data, digest=algorithm_plan_digest(plan), version=plan['plan_version'], build_id=plan['build_id'])


def test_large_plan_is_complete_under_separate_audit_budget(plan_case):
    service, grant, reg, request, audit, rig, store = plan_case
    from test_iter65_local_parent_plan_snapshot import replace_synthetic_plan
    plan = deepcopy(request['algorithm_plan'])
    plan['schedule']['class'] = 'TWAP'
    plan['schedule']['params'] = {'slots': [
        {'start_ns': i * 2, 'end_ns': i * 2 + 1, 'quantity': '1'} for i in range(1200)]}
    data = canonical_json(plan).encode('ascii')
    assert 32768 < len(data) < MAX_PLAN_BYTES and 2 * len(data) < MAX_PLAN_AUDIT_RESULT_BYTES
    # Explicit test-only coherent rewrite does not claim original intake admission.
    replace_synthetic_plan(store, rig, request, plan)
    reply = call(service, grant, reg)
    assert reply['ok'], reply
    assert reply['result']['snapshot']['plan_json'].encode('ascii') == data
    assert attempts(audit) == [(2, 'q', 'OBSERVED')]


def test_operation_change_requires_rebuild_and_old_session_is_not_reused(plan_case):
    service, grant, old, _, audit, rig, _ = plan_case
    narrower = replace(grant, revision='policy-v2', operations=frozenset({PARENT_READ_V1}))
    with pytest.raises(ExecutionManagementReadServiceError, match='INVALID_CONFIGURATION'):
        service.replace_grant(grant, narrower)
    assert call(service, grant, old, query_id='before-rebuild')['ok']
    service.close()
    replacement = ExecutionManagementReadService(host=rig.host, local_scope=rig.target, grant=narrower,
        scope_ref='scope', service_id='management-service', authenticated_principal=grant.identity.principal_ref,
        audit_root=audit, lease_seconds=10)
    try:
        assert not call(replacement, narrower, old, PARENT_READ_V1, query_id='old-session')['ok']
        rejected = _register(replacement, identity=narrower.identity, scope=narrower.scope,
            principal=narrower.identity.principal_ref, key='new-plan', instance_id=narrower.instance_id,
            port=PORT, scope_ref='scope', operations=(PARENT_PLAN_READ_V1,))
        assert not rejected['ok']
        new = _register(replacement, identity=narrower.identity, scope=narrower.scope,
            principal=narrower.identity.principal_ref, key='new-parent', instance_id=narrower.instance_id,
            port=PORT, scope_ref='scope', operations=(PARENT_READ_V1,))
        assert new['ok'] and call(replacement, narrower, new['result'], PARENT_READ_V1, query_id='new')['ok']
    finally:
        replacement.close()


def test_near_maximum_valid_plan_is_complete_and_oversized_rejected(plan_case):
    service, grant, reg, request, audit, rig, store = plan_case
    from test_iter65_local_parent_plan_snapshot import replace_synthetic_plan
    plan = deepcopy(request['algorithm_plan'])
    plan['plan_version'] = '版本' * 20
    plan['build_id'] = '构建' * 20
    plan['schedule']['class'] = 'TWAP'
    slots = []
    plan['schedule']['params'] = {'slots': slots}
    for i in range(4096):
        slots.append({'start_ns': i * 2, 'end_ns': i * 2 + 1, 'quantity': '1'})
        if len(canonical_json(plan).encode('ascii')) > MAX_PLAN_BYTES:
            slots.pop()
            break
    data = canonical_json(plan).encode('ascii')
    assert MAX_PLAN_BYTES - 128 <= len(data) <= MAX_PLAN_BYTES
    assert len(slots) < 4096 and b'\\u' in data
    adjusted_request = deepcopy(request)
    adjusted_request['spec']['plan_version'] = plan['plan_version']
    adjusted_request['spec']['plan_digest'] = algorithm_plan_digest(plan)
    adjusted_request['algorithm_plan'] = plan
    from bt_api_base.service_protocol import encode_wire_value
    assert len(encode_wire_value(adjusted_request)) <= 131072
    replace_synthetic_plan(store, rig, adjusted_request, plan)
    result = call(service, grant, reg)
    assert result['ok'], result
    assert result['result']['snapshot']['plan_json'].encode('ascii') == data
    assert attempts(audit) == [(2, 'q', 'OBSERVED')]
    # The enclosing audit JSON escapes the already canonical JSON a second time.
    encoded = json.dumps(result['result']['snapshot'], ensure_ascii=True, sort_keys=True,
                         separators=(',', ':')).encode('ascii')
    assert len(data) < len(encoded) < MAX_PLAN_AUDIT_RESULT_BYTES
    slots.append({'start_ns': len(slots) * 2, 'end_ns': len(slots) * 2 + 1, 'quantity': '1'})
    oversized = canonical_json(plan).encode('ascii')
    assert len(oversized) > MAX_PLAN_BYTES
    with pytest.raises(ValueError):
        validate_plan_json(oversized, digest=algorithm_plan_digest(plan), version=plan['plan_version'],
                           build_id=plan['build_id'])


def test_client_parser_does_not_import_optional_execution_package():
    import subprocess
    import sys
    script = r'''
import builtins, hashlib, json
original_import = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == 'bt_api_execution' or name.startswith('bt_api_execution.'):
        raise AssertionError('optional Execution imported by client: ' + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = blocked
from bt_api_py.execution_management_read_client import _parse_plan_snapshot, ExecutionManagementReadClient
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
instrument = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
plan = {'schema_version': 1, 'plan_version': 'v1', 'build_id': 'build', 'schedule': {
    'class': 'PASS', 'version': 1, 'binding': {'deadline_ns': 5, 'quantity_step': '1',
        'parent_limit': '1', 'price_step': '1', 'retry_limit': 0}, 'params': {}}}
data = json.dumps(plan, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')
from dataclasses import asdict
raw = {'status': 'OBSERVED', 'source': 'LOCAL_JOURNAL', 'journal_incarnation': 'a' * 32,
    'parent_ref': 'p', 'instrument': asdict(instrument), 'plan_version': 'v1',
    'plan_digest': hashlib.sha256(data).hexdigest(), 'declared_build_id': 'build',
    'plan_json': data.decode('ascii')}
assert _parse_plan_snapshot(raw, 'p').plan_json == data
# Synthetic runtime response exercises query_plan's error-to-UNKNOWN boundary.
from dataclasses import dataclass
@dataclass
class Target:
    instance_id: str
    identity: object
    scope: object
    registration_id: str = ''
    service_epoch: str = ''
class Runtime:
    def call_bound(self, target, operation, arguments, **kwargs):
        return {'schema_version': 1, 'snapshot': raw}
client = object.__new__(ExecutionManagementReadClient)
client._target = Target('instance', WireIdentity(WireRole.APPLICATION_CLIENT, 'principal', 'actor'),
    WireScope('domain', account_ref='account', strategy_ref='strategy'))
client._runtime = Runtime()
client._revision, client._timeout = 'v1', 1
client._registration = lambda operation: ('registration', 'epoch')
assert client.query_plan('p', query_id='query').snapshot.plan_json == data
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_unknown_extension_is_audited_without_execution(plan_case):
    service, grant, reg, request, audit, rig, store = plan_case
    from test_iter65_local_parent_plan_snapshot import replace_synthetic_plan
    plan = deepcopy(request['algorithm_plan'])
    plan['schedule']['class'] = 'CUSTOM_EXTENSION'
    replace_synthetic_plan(store, rig, request, plan)
    result = call(service, grant, reg)
    assert result['ok'] and result['result']['snapshot']['status'] == 'UNKNOWN'
    assert attempts(audit) == [(2, 'q', 'UNKNOWN')]


def test_lost_reply_after_audit_commit_never_discloses(plan_case, monkeypatch):
    service, grant, reg, _, audit, _, _ = plan_case
    journal = service._ExecutionManagementReadService__audit
    original = journal.disclose_after_audit
    def commit_then_lose_reply(**kwargs):
        original(**kwargs)
        raise OSError('synthetic lost reply')
    monkeypatch.setattr(journal, 'disclose_after_audit', commit_then_lose_reply)
    result = call(service, grant, reg)
    assert not result['ok'] and not result['result']
    assert attempts(audit) == [(2, 'q', 'OBSERVED')]
