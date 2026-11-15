"""Synthetic local lookup: no account access or order dispatch."""
from dataclasses import replace
import hashlib
import json
import sqlite3

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_py.execution_management_read_grant import (
    ExecutionManagementReadGrant, PARENT_READ_V1, PARENT_PLAN_READ_V1, PARENT_BY_REQUEST_READ_V1,
)
from bt_api_py.execution_management_read_service import ExecutionManagementReadService, PORT
from bt_api_py.execution_parent_grant import SUBMIT
from tests.unit.test_service_registry import _next_request, _register
from test_iter65_atomic_submission_intake import atomic  # noqa: F401
from test_iter65_fresh_submission_intake import KEY, OTHER


@pytest.fixture
def lookup(tmp_path, atomic):
    rig, store, _ = atomic
    original = rig.parent()
    assert rig.call(SUBMIT, original)['ok']
    app = WireIdentity(WireRole.APPLICATION_CLIENT, 'management-principal', 'management-actor')
    scope = replace(rig.scope, run_id=None)
    ops = frozenset({PARENT_READ_V1, PARENT_PLAN_READ_V1, PARENT_BY_REQUEST_READ_V1})
    grant = ExecutionManagementReadGrant('management-instance', app, scope, 'policy-v1', frozenset({KEY}), ops)
    audit = tmp_path / 'audit'
    audit.mkdir(mode=0o700)
    service = ExecutionManagementReadService(host=rig.host, local_scope=rig.target, grant=grant,
        scope_ref='scope', service_id='management-service', authenticated_principal=app.principal_ref,
        audit_root=audit, lease_seconds=10)
    reg = _register(service, identity=app, scope=scope, principal=app.principal_ref,
        instance_id=grant.instance_id, port=PORT, scope_ref='scope', operations=tuple(ops))
    assert reg['ok'], reg
    yield service, grant, reg['result'], original, audit, rig, store
    service.close()


def read(service, grant, reg, *, run='run-a', request='request-1', query='q', extra=None):
    args = {'schema_version': 1, 'original_run_id': run, 'request_id': request,
            'query_id': query, 'grant_revision': grant.revision}
    if extra:
        args.update(extra)
    return service.handle(grant.identity.principal_ref, _next_request(PARENT_BY_REQUEST_READ_V1, {
        'registration_id': reg['registration_id'], 'service_epoch': reg['service_epoch'],
        'instance_id': grant.instance_id, 'arguments': args}, identity=grant.identity, scope=grant.scope))


def parent_read(service, grant, reg, operation, query):
    return service.handle(grant.identity.principal_ref, _next_request(operation, {
        'registration_id': reg['registration_id'], 'service_epoch': reg['service_epoch'],
        'instance_id': grant.instance_id, 'arguments': {'schema_version': 1, 'parent_ref': 'parent-1',
        'query_id': query, 'grant_revision': grant.revision}}, identity=grant.identity, scope=grant.scope))


def attempts(audit):
    with sqlite3.connect(audit / 'execution-management-read.sqlite3') as db:
        return db.execute('SELECT operation_version, query_id, outcome FROM audit_attempts ORDER BY sequence').fetchall()


def test_source_and_scope(lookup):
    service, grant, reg, original, audit, rig, store = lookup
    before = store._connection.total_changes
    result = read(service, grant, reg)['result']
    found = result['snapshot']
    from bt_api_base.service_protocol import encode_wire_value
    assert result['request_sha256'] == hashlib.sha256(encode_wire_value(original)).hexdigest()
    with sqlite3.connect(audit / 'execution-management-read.sqlite3') as db:
        digest = db.execute('SELECT result_digest FROM audit_attempts').fetchone()[0]
    encoded = json.dumps({'snapshot': found, 'request_sha256': result['request_sha256']},
        sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    assert digest == hashlib.sha256(encoded).hexdigest()
    assert found['status'] == 'OBSERVED' and found['parent_ref'] == original['spec']['parent_ref']
    assert not {'request_sha256', 'request_id', 'run_id', 'plan_json'} & set(found)
    assert read(service, grant, reg)['result']['snapshot'] == found
    wrong_run = read(service, grant, reg, run='wrong', query='wrong-run')['result']['snapshot']
    wrong_request = read(service, grant, reg, request='wrong', query='wrong-request')['result']['snapshot']
    assert wrong_run == wrong_request and wrong_run['status'] == 'UNKNOWN'
    assert read(service, grant, reg, request='wrong', query='wrong-request')['result']['request_sha256'] is None
    denied_grant = replace(grant, revision='v2', instruments=frozenset({OTHER}))
    assert service.replace_grant(grant, denied_grant)
    denied = read(service, denied_grant, reg, query='denied')['result']['snapshot']
    assert denied == wrong_run and store._connection.total_changes == before
    assert attempts(audit) == [(3, 'q', 'OBSERVED'), (3, 'q', 'OBSERVED'),
        (3, 'wrong-run', 'UNKNOWN'), (3, 'wrong-request', 'UNKNOWN'),
        (3, 'wrong-request', 'UNKNOWN'), (3, 'denied', 'UNKNOWN')]


def test_id_conflicts_across_all_operations_and_keys(lookup):
    service, grant, reg, _, audit, _, _ = lookup
    assert read(service, grant, reg, query='by-request')['ok']
    assert not read(service, grant, reg, run='other', query='by-request')['ok']
    assert not read(service, grant, reg, request='other', query='by-request')['ok']
    for operation, key in ((PARENT_READ_V1, 'parent'), (PARENT_PLAN_READ_V1, 'plan')):
        assert not parent_read(service, grant, reg, operation, 'by-request')['ok']
        assert parent_read(service, grant, reg, operation, key)['ok']
        assert not read(service, grant, reg, query=key)['ok']
    assert attempts(audit) == [(3, 'by-request', 'OBSERVED'), (1, 'parent', 'OBSERVED'), (2, 'plan', 'OBSERVED')]


def test_revoke_failure_and_strict_arguments(lookup, monkeypatch):
    service, grant, reg, _, audit, _, _ = lookup
    for extra in ({'schema_version': True}, {'extra': 1}, {'request_id': ''},
                  {'original_run_id': 1}, {'query_id': ' x'}, {'grant_revision': 'bad'}):
        assert not read(service, grant, reg, extra=extra)['ok']
    journal = service._ExecutionManagementReadService__audit
    monkeypatch.setattr(journal, 'disclose_after_audit', lambda **_: (_ for _ in ()).throw(OSError()))
    assert not read(service, grant, reg)['ok']
    assert attempts(audit) == []
    assert service.revoke_grant(grant)
    assert not read(service, grant, reg)['ok']


def test_corrupt_and_bad_reader_dto_do_not_disclose(lookup, monkeypatch):
    service, grant, reg, _, _, _, store = lookup
    absent = read(service, grant, reg, request='none', query='absent')['result']['snapshot']
    with store._transaction() as cursor:
        cursor.execute('UPDATE execution_parent_records SET request_sha256=? WHERE parent_id=?', ('0' * 64, 'parent-1'))
    assert read(service, grant, reg, query='damaged')['result']['snapshot'] == absent
    from bt_api_execution.local_parent_snapshot import LocalParentRequestSnapshot
    reader = service._ExecutionManagementReadService__reader
    monkeypatch.setattr(reader, 'query_by_request', lambda **_: LocalParentRequestSnapshot('OBSERVED', None, 'a' * 64))
    assert read(service, grant, reg, query='bad-dto')['result']['snapshot'] == absent


def test_policy_rejects_request_operation_type_mismatch(lookup):
    from bt_api_py.execution_management_read_grant import (ExecutionManagementReadRequest,
        ExecutionManagementByRequestReadRequest, allows_request)
    _, grant, _, _, _, _, _ = lookup
    legacy = ExecutionManagementReadRequest(grant.instance_id, grant.identity, grant.scope, grant.revision,
                                            PARENT_BY_REQUEST_READ_V1, 'parent-1', 'q')
    assert not allows_request(grant, legacy)
    with pytest.raises(ValueError):
        ExecutionManagementByRequestReadRequest(grant.instance_id, grant.identity, grant.scope, grant.revision,
                                                PARENT_READ_V1, 'run-a', 'request-1', 'q')


def test_permission_is_independent(tmp_path, atomic):
    rig, _, _ = atomic
    assert rig.call(SUBMIT, rig.parent())['ok']
    app = WireIdentity(WireRole.APPLICATION_CLIENT, 'management-principal', 'management-actor')
    grant = ExecutionManagementReadGrant('management-instance', app, replace(rig.scope, run_id=None),
        'v1', frozenset({KEY}), frozenset({PARENT_READ_V1}))
    audit = tmp_path / 'audit'; audit.mkdir(mode=0o700)
    service = ExecutionManagementReadService(host=rig.host, local_scope=rig.target, grant=grant,
        scope_ref='scope', service_id='service', authenticated_principal=app.principal_ref, audit_root=audit)
    try:
        allowed = _register(service, identity=app, scope=grant.scope, principal=app.principal_ref,
            instance_id=grant.instance_id, port=PORT, scope_ref='scope', operations=(PARENT_READ_V1,))
        denied = _register(service, identity=app, scope=grant.scope, principal=app.principal_ref,
            instance_id=grant.instance_id, port=PORT, scope_ref='scope', key='denied',
            operations=(PARENT_BY_REQUEST_READ_V1,))
        assert allowed['ok'] and not denied['ok']
        assert not read(service, grant, allowed['result'])['ok']
        assert attempts(audit) == []
    finally:
        service.close()


def test_audit_key_survives_service_restart_and_revision(lookup):
    service, grant, reg, _, audit, rig, _ = lookup
    assert read(service, grant, reg, query='stable')['ok']
    service.close()
    revised = replace(grant, revision='v2')
    replacement = ExecutionManagementReadService(host=rig.host, local_scope=rig.target, grant=revised,
        scope_ref='scope', service_id='management-service', authenticated_principal=grant.identity.principal_ref,
        audit_root=audit, lease_seconds=10)
    try:
        assert not read(replacement, revised, reg, query='stable')['ok']
        registered = _register(replacement, identity=revised.identity, scope=revised.scope,
            principal=revised.identity.principal_ref, instance_id=revised.instance_id, port=PORT,
            scope_ref='scope', operations=tuple(revised.operations))
        assert registered['ok']
        assert read(replacement, revised, registered['result'], query='stable')['ok']
        assert not read(replacement, revised, registered['result'], request='other', query='stable')['ok']
        assert attempts(audit) == [(3, 'stable', 'OBSERVED'), (3, 'stable', 'OBSERVED')]
    finally:
        replacement.close()


def test_client_strict_response_and_bound_operation(lookup):
    from copy import deepcopy
    from dataclasses import dataclass
    from bt_api_py.execution_management_read_client import ExecutionManagementReadClient
    from bt_api_py.service_runtime import (_validate_bound_target_operation, PORT_OPERATIONS,
                                            ServiceRuntimeRequestNotSentError, ServiceClientRuntime)
    from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
    service, grant, reg, _, _, _, _ = lookup
    good = read(service, grant, reg)['result']

    @dataclass
    class Target:
        instance_id: str
        identity: object
        scope: object
        registration_id: str = ''
        service_epoch: str = ''

    class Runtime:
        def call_bound(self, target, operation, arguments, **_):
            assert operation == PARENT_BY_REQUEST_READ_V1
            assert arguments == {'schema_version': 1, 'original_run_id': 'run-a', 'request_id': 'request-1',
                                 'query_id': 'client', 'grant_revision': grant.revision}
            return self.response

    client = object.__new__(ExecutionManagementReadClient)
    client._target = Target(grant.instance_id, grant.identity, grant.scope)
    client._runtime = Runtime()
    client._revision, client._timeout = grant.revision, 1
    client._registration = lambda op: ('registered', 'epoch')
    client._runtime.response = good
    assert client.query_by_request('run-a', 'request-1', query_id='client').request_sha256 == good['request_sha256']
    for mutation in ('extra', 'digest', 'none', 'unknown_with_digest', 'plan'):
        bad = deepcopy(good)
        if mutation == 'extra': bad['extra'] = 1
        elif mutation == 'digest': bad['request_sha256'] = 'A' * 64
        elif mutation == 'none': bad['request_sha256'] = None
        elif mutation == 'plan': bad['snapshot']['plan_json'] = 'secret'
        else:
            from dataclasses import asdict
            from bt_api_execution.local_parent_snapshot import LocalParentSnapshot
            bad['snapshot'] = asdict(LocalParentSnapshot('UNKNOWN'))
        client._runtime.response = bad
        result = client.query_by_request('run-a', 'request-1', query_id='client')
        assert result.status == 'UNKNOWN' and result.snapshot is result.request_sha256 is None
    assert PARENT_BY_REQUEST_READ_V1 in PORT_OPERATIONS[PORT]
    with pytest.raises(ValueError):
        _validate_bound_target_operation(object(), PARENT_BY_REQUEST_READ_V1)
    runtime = object.__new__(ServiceClientRuntime)
    runtime._assert_owner_thread = lambda: None
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.call('mount', PARENT_BY_REQUEST_READ_V1, {})
    threaded = object.__new__(ThreadedServiceClientRuntime)
    threaded._check_reentry = lambda: None
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        threaded.call('mount', PARENT_BY_REQUEST_READ_V1, {})
