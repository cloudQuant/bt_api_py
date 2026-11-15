"""Isolated synthetic Registry, journals and transport; no network or account."""
from dataclasses import replace
import os
import sqlite3
import threading
import time

import pytest

from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_py.execution_management_read_grant import ExecutionManagementReadGrant, PARENT_READ_V1
from bt_api_py.execution_management_read_service import ExecutionManagementReadService, PORT
from bt_api_py.execution_parent_grant import SUBMIT
from tests.unit.test_service_registry import _next_request, _register
from test_iter65_atomic_submission_intake import atomic
from test_iter65_fresh_submission_intake import KEY, OTHER


@pytest.fixture
def case(tmp_path, atomic):
    rig, store, _ = atomic
    original = rig.parent()
    assert rig.call(SUBMIT, original)['ok']
    wire_scope = replace(rig.scope, run_id=None)
    app = WireIdentity(WireRole.APPLICATION_CLIENT, 'management-principal', 'management-actor')
    grant = ExecutionManagementReadGrant('management-instance', app, wire_scope, 'policy-v1',
                                          frozenset({KEY}), frozenset({PARENT_READ_V1}))
    audit = tmp_path / 'management-audit'
    audit.mkdir(mode=0o700)
    service = ExecutionManagementReadService(host=rig.host, local_scope=rig.target, grant=grant,
        scope_ref='management-scope', service_id='management-service',
        authenticated_principal=app.principal_ref, audit_root=audit, lease_seconds=10)
    response = _register(service, identity=app, scope=wire_scope, principal=app.principal_ref,
        instance_id=grant.instance_id, port=PORT, scope_ref='management-scope', operations=(PARENT_READ_V1,))
    assert response['ok'], response
    yield service, grant, response['result'], original, audit, rig, store
    service.close()


def read(service, grant, registration, parent='parent-1', query='query-1', *, principal=None, revision=None):
    args = {'schema_version': 1, 'parent_ref': parent, 'query_id': query,
            'grant_revision': grant.revision if revision is None else revision}
    request = _next_request(PARENT_READ_V1, {
        'registration_id': registration['registration_id'], 'service_epoch': registration['service_epoch'],
        'instance_id': grant.instance_id, 'arguments': args}, identity=grant.identity, scope=grant.scope)
    return service.handle(grant.identity.principal_ref if principal is None else principal, request)


def attempts(path):
    with sqlite3.connect(str(path / 'execution-management-read.sqlite3')) as db:
        return db.execute('SELECT outcome,query_id FROM audit_attempts ORDER BY sequence').fetchall()


def test_read_real_local_journal_and_repeated_attempts(case):
    service, grant, reg, original, audit, rig, store = case
    parent = original['spec']['parent_ref']
    before = store._connection.total_changes
    for _ in range(2):
        response = read(service, grant, reg, parent)
        assert response['ok'], response
        result = response['result']['snapshot']
        assert result['status'] == 'OBSERVED' and result['source'] == 'LOCAL_JOURNAL'
        assert result['parent_ref'] == parent and result['intake_local_state'] == 'RECEIVED'
        assert (result['risk_local_state'], result['gateway_local_state'], result['cancel_local_state']) == (
            'UNKNOWN', 'UNKNOWN', 'UNKNOWN')
        assert 'run_id' not in result and 'identity' not in result
    assert attempts(audit) == [('OBSERVED', 'query-1'), ('OBSERVED', 'query-1')]
    assert store._connection.total_changes == before


def test_same_query_id_reads_latest_local_snapshot_and_audits_each_attempt(case):
    service, grant, reg, original, audit, _, store = case
    parent = original['spec']['parent_ref']
    first = read(service, grant, reg, parent)['result']['snapshot']
    with store._transaction() as cursor:
        cursor.execute('UPDATE execution_parent_records SET updated_at_ns=updated_at_ns+1 WHERE parent_id=?',
                       (parent,))
    second = read(service, grant, reg, parent)['result']['snapshot']
    assert first['status'] == second['status'] == 'OBSERVED'
    assert second['updated_at_ns'] == first['updated_at_ns'] + 1
    assert attempts(audit) == [('OBSERVED', 'query-1'), ('OBSERVED', 'query-1')]


def test_missing_corrupt_and_unauthorized_instrument_are_indistinguishable(case):
    service, grant, reg, original, audit, rig, store = case
    parent = original['spec']['parent_ref']
    missing = read(service, grant, reg, 'missing-parent', 'missing')['result']['snapshot']
    assert missing['status'] == 'UNKNOWN'
    narrower = replace(grant, revision='policy-v2', instruments=frozenset({OTHER}))
    assert service.replace_grant(grant, narrower)
    denied = read(service, narrower, reg, parent, 'denied')['result']['snapshot']
    assert denied == missing
    assert attempts(audit) == [('UNKNOWN', 'missing'), ('UNKNOWN', 'denied')]


def test_scope_mapping_cannot_disclose_parent_from_another_wire_domain(tmp_path, atomic):
    rig, _, _ = atomic
    original = rig.parent()
    assert rig.call(SUBMIT, original)['ok']
    wire_scope = replace(rig.scope, run_id=None, execution_domain_ref='different-domain')
    app = WireIdentity(WireRole.APPLICATION_CLIENT, 'another-principal', 'another-actor')
    grant = ExecutionManagementReadGrant('management-instance', app, wire_scope, 'policy-v1',
                                          frozenset({KEY}), frozenset({PARENT_READ_V1}))
    audit = tmp_path / 'different-domain-audit'
    audit.mkdir(mode=0o700)
    service = ExecutionManagementReadService(host=rig.host, local_scope=rig.target, grant=grant,
        scope_ref='management-scope', service_id='management-service',
        authenticated_principal=app.principal_ref, audit_root=audit)
    try:
        response = _register(service, identity=app, scope=wire_scope, principal=app.principal_ref,
            instance_id=grant.instance_id, port=PORT, scope_ref='management-scope', operations=(PARENT_READ_V1,))
        assert response['ok'], response
        observed = read(service, grant, response['result'], original['spec']['parent_ref'])
        assert observed['ok'] and observed['result']['snapshot']['status'] == 'UNKNOWN'
    finally:
        service.close()


def test_conflicting_query_id_and_revoked_policy_do_not_disclose(case):
    service, grant, reg, original, audit, _, _ = case
    parent = original['spec']['parent_ref']
    assert read(service, grant, reg, parent)['ok']
    assert not read(service, grant, reg, 'different-parent')['ok']
    assert attempts(audit) == [('OBSERVED', 'query-1')]
    assert service.revoke_grant(grant)
    assert not read(service, grant, reg, parent, 'new-id')['ok']
    assert attempts(audit) == [('OBSERVED', 'query-1')]


def test_transport_principal_and_grant_revision_cannot_be_forged(case):
    service, grant, reg, original, audit, _, _ = case
    parent = original['spec']['parent_ref']
    assert not read(service, grant, reg, parent, principal='wire-self-claimed')['ok']
    assert not read(service, grant, reg, parent, revision='wrong-revision')['ok']
    assert attempts(audit) == []


def test_audit_unavailable_never_discloses(case, monkeypatch):
    service, grant, reg, original, audit, _, _ = case
    local_audit = service._ExecutionManagementReadService__audit
    monkeypatch.setattr(local_audit, 'disclose_after_audit', lambda **_: (_ for _ in ()).throw(OSError()))
    result = read(service, grant, reg, original['spec']['parent_ref'])
    assert not result['ok'] and not result['result']
    assert attempts(audit) == []


def test_cross_thread_handle_is_denied_and_original_owner_can_close(case):
    service, grant, reg, original, _, _, _ = case
    results = []
    thread = threading.Thread(target=lambda: results.append(read(service, grant, reg, original['spec']['parent_ref'])))
    thread.start()
    thread.join(2)
    assert not thread.is_alive() and results and not results[0]['ok']
    assert read(service, grant, reg, original['spec']['parent_ref'])['ok']


def test_expired_monotonic_lease_cannot_be_revived_by_wallclock(case, monkeypatch):
    service, grant, reg, original, _, _, _ = case
    leases = service._ExecutionManagementReadService__leases
    epoch, expiry, _ = leases[reg['registration_id']]
    leases[reg['registration_id']] = (epoch, expiry, 0.0)
    result = read(service, grant, reg, original['spec']['parent_ref'])
    assert not result['ok'] and result['code'] == 'NOT_REGISTERED'


def test_trading_registry_unready_does_not_enable_or_block_management(case):
    service, grant, reg, original, _, rig, _ = case
    rig.registry.set_ready(False)
    response = read(service, grant, reg, original['spec']['parent_ref'])
    assert response['ok'] and response['result']['snapshot']['status'] == 'OBSERVED'
    assert rig.registry._ready is False


def test_corrupt_parent_is_same_unknown_as_absent(case):
    service, grant, reg, original, audit, rig, store = case
    parent = original['spec']['parent_ref']
    absent = read(service, grant, reg, 'missing-parent', 'absent')['result']['snapshot']
    with store._transaction() as cursor:
        cursor.execute('UPDATE execution_parent_records SET request_sha256=? WHERE parent_id=?', ('0' * 64, parent))
    damaged = read(service, grant, reg, parent, 'damaged')['result']['snapshot']
    assert damaged == absent and attempts(audit) == [('UNKNOWN', 'absent'), ('UNKNOWN', 'damaged')]


def test_revocation_waits_for_read_and_prevents_following_read(case, monkeypatch):
    service, grant, reg, original, audit, _, _ = case
    journal = service._ExecutionManagementReadService__audit
    real_audit = journal.disclose_after_audit
    entered, revoked = threading.Event(), threading.Event()
    result = []

    def revoke():
        assert entered.wait(2)
        result.append(service.revoke_grant(grant))
        revoked.set()

    thread = threading.Thread(target=revoke)
    thread.start()

    def during_audit(**kwargs):
        entered.set()
        assert not revoked.wait(.05)
        return real_audit(**kwargs)

    monkeypatch.setattr(journal, 'disclose_after_audit', during_audit)
    response = read(service, grant, reg, original['spec']['parent_ref'])
    thread.join(2)
    assert response['ok'] and not thread.is_alive() and result == [True]
    assert not read(service, grant, reg, original['spec']['parent_ref'], 'after-revoke')['ok']
    assert len(attempts(audit)) == 1


def test_failed_reader_close_retains_hold_for_same_owner_retry(case, monkeypatch):
    service, grant, reg, original, _, rig, _ = case
    reader = service._ExecutionManagementReadService__reader
    original_close = reader.close
    monkeypatch.setattr(reader, 'close', lambda: (_ for _ in ()).throw(OSError('synthetic')))
    with pytest.raises(OSError):
        service.close()
    assert not read(service, grant, reg, original['spec']['parent_ref'])['ok']
    with pytest.raises(Exception):
        rig.host.close()
    monkeypatch.setattr(reader, 'close', original_close)
    service.close()
