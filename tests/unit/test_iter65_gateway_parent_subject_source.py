"""Authenticated subject observation under one synthetic Registry snapshot."""
import json
import threading
from dataclasses import replace

import pytest

from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_py.gateway_parent_service import SOURCE
from bt_api_py.service_registry import ServiceDispatchAdmission
from tests.unit.test_iter65_gateway_parent_bound_v2 import service
from tests.unit.test_service_registry import _next_request


def arguments(**extra):
    return dict(account_scope='account', strategy_scope='strategy', execution_domain_ref='domain',
                subject_version=1, **extra)


def test_subject_is_derived_from_active_strategy_claim_not_request(service):
    result = service['request'](SOURCE, arguments())
    assert result['ok'] and result['result']['status'] == 'OBSERVED'
    observed = result['result']
    assert observed['schema_version'] == 3 and observed['operation'] == SOURCE
    assert observed['strategy_subject']['identity'] == {
        'role': 'strategy_client', 'principal_ref': 'strategy-principal',
        'actor_ref': 'strategy', 'delegate_ref': None}
    assert observed['strategy_subject']['scope'] == service['registry']._scope_dict(service['scope'])
    assert observed['result']['schema_version'] == 1
    forged = service['request'](SOURCE, arguments(strategy_subject=observed['strategy_subject']))
    assert forged['result'] == {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}
    ordinary = service['request'](SOURCE, {'account_scope': 'account', 'strategy_scope': 'strategy',
                                            'execution_domain_ref': 'domain'})
    assert ordinary['result']['schema_version'] == 1 and 'strategy_subject' not in ordinary['result']


@pytest.mark.parametrize('bad', [True, 0, 2, '1', None])
def test_subject_version_is_explicit_and_exact(service, bad):
    response = service['request'](SOURCE, dict(arguments(), subject_version=bad))
    assert response['result'] == {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}
    both = service['request'](SOURCE, dict(arguments(), readiness_version=2))
    assert both['result'] == {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}


@pytest.mark.parametrize('change', ['claim', 'session', 'ttl', 'epoch', 'actor', 'delegate',
                                     'principal_acl', 'write_acl', 'capability', 'handler'])
def test_subject_observation_rejects_untrusted_or_inactive_strategy(service, change):
    registry = service['registry']
    with registry._lock:
        row = registry._db.execute("SELECT * FROM sessions WHERE principal='strategy-principal'").fetchone()
        assert row is not None
        if change == 'claim':
            registry._db.execute("UPDATE strategy_write_claims SET active_registration_id=NULL WHERE strategy_ref='strategy'")
        elif change == 'session':
            registry._db.execute('DELETE FROM sessions WHERE registration_id=?', (row['registration_id'],))
        elif change == 'ttl':
            registry._db.execute('UPDATE sessions SET expires_at=0 WHERE registration_id=?', (row['registration_id'],))
        elif change == 'epoch':
            registry._db.execute("UPDATE sessions SET service_epoch='wrong' WHERE registration_id=?", (row['registration_id'],))
        elif change in ('actor', 'delegate'):
            identity = json.loads(row['identity_json'])
            identity['actor_ref' if change == 'actor' else 'delegate_ref'] = 'other'
            registry._db.execute('UPDATE sessions SET identity_json=? WHERE registration_id=?',
                                 (json.dumps(identity, sort_keys=True, separators=(',', ':')), row['registration_id']))
        elif change == 'principal_acl':
            grants = dict(registry._grants)
            grants.pop('strategy-principal')
            registry._grants = grants
        elif change == 'write_acl':
            registry._db.execute("UPDATE sessions SET write_operations_json='[]' WHERE registration_id=?",
                                 (row['registration_id'],))
        elif change == 'capability':
            caps = dict(registry._capabilities)
            key = ('instance', 'service.direct_write')
            caps[key] = replace(caps[key], write_operations=caps[key].write_operations - {'direct.reserve'})
            registry._capabilities = caps
        else:
            registry._handlers.pop(('instance', 'direct.reserve'))
    reply = service['request'](SOURCE, arguments())
    assert not reply['ok'] or reply['result']['status'] != 'OBSERVED'


def test_subject_can_only_be_observed_by_authorized_service_admission(service):
    registry = service['registry']
    strategy = WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-principal', 'strategy')
    with registry._lock:
        row = registry._db.execute("SELECT registration_id,service_epoch FROM sessions "
                                   "WHERE principal='strategy-principal'").fetchone()
    request = _next_request(SOURCE, {'registration_id': row['registration_id'], 'service_epoch': row['service_epoch'],
        'instance_id': 'instance', 'arguments': arguments()}, identity=strategy, scope=service['scope'])
    response = registry.handle(strategy.principal_ref, request)
    assert not response['ok'] or response['result']['status'] != 'OBSERVED'


def test_subject_change_between_admission_rechecks_is_unknown(service, monkeypatch):
    original = ServiceDispatchAdmission.observe_gateway_parent_strategy_subject
    calls = []
    def changed(self):
        subject = original(self)
        calls.append(1)
        if len(calls) > 1:
            subject['identity']['principal_ref'] = 'other'
        return subject
    monkeypatch.setattr(ServiceDispatchAdmission, 'observe_gateway_parent_strategy_subject', changed)
    response = service['request'](SOURCE, arguments())
    assert response['result'] == {'status': 'UNKNOWN'} and len(calls) >= 2


@pytest.mark.parametrize('change', ['claim', 'ttl', 'identity', 'resource', 'gateway-grant'])
def test_authorization_changes_after_first_subject_read_are_rechecked(service, monkeypatch, change):
    registry = service['registry']
    original = ServiceDispatchAdmission.observe_gateway_parent_strategy_subject
    calls = []
    def change_after_read(self):
        subject = original(self)
        calls.append(1)
        if len(calls) == 1:
            row = registry._db.execute("SELECT * FROM sessions WHERE principal='strategy-principal'").fetchone()
            if change == 'claim':
                registry._db.execute("UPDATE strategy_write_claims SET active_registration_id=NULL "
                                     "WHERE strategy_ref='strategy'")
            elif change == 'ttl':
                registry._db.execute('UPDATE sessions SET expires_at=0 WHERE registration_id=?',
                                     (row['registration_id'],))
            elif change == 'identity':
                identity = json.loads(row['identity_json'])
                identity['actor_ref'] = 'other'
                registry._db.execute('UPDATE sessions SET identity_json=? WHERE registration_id=?',
                                     (json.dumps(identity, sort_keys=True, separators=(',', ':')), row['registration_id']))
            elif change == 'resource':
                grants = dict(registry._grants)
                grants.pop('strategy-principal')
                registry._grants = grants
            else:
                key = ('instance', 'scope', service['identity'].principal_ref, 'account', 'strategy')
                registry._gateway_parent_grants[key] = replace(service['grant'], operations=service['grant'].operations - {SOURCE})
        return subject
    monkeypatch.setattr(ServiceDispatchAdmission, 'observe_gateway_parent_strategy_subject', change_after_read)
    reply = service['request'](SOURCE, arguments())
    assert not reply['ok'] or reply['result']['status'] != 'OBSERVED'
    assert calls == [1]


def test_subject_snapshot_serializes_cooperating_strategy_revocation(service, monkeypatch):
    registry, journal = service['registry'], service['journal']
    original = journal._assert_anchor
    entered, release, revoked = threading.Event(), threading.Event(), threading.Event()
    replies = []
    def pause(db):
        revision = original(db)
        entered.set()
        assert release.wait(3)
        return revision
    def revoke():
        registry.revoke_principal('strategy-principal')
        revoked.set()
    monkeypatch.setattr(journal, '_assert_anchor', pause)
    reader = threading.Thread(target=lambda: replies.append(service['request'](SOURCE, arguments())))
    changer = threading.Thread(target=revoke)
    reader.start()
    try:
        assert entered.wait(2)
        changer.start()
        assert not revoked.wait(0.05)  # Same Registry lock still protects the observation.
    finally:
        release.set()
        reader.join(3)
        if changer.ident is not None:
            changer.join(3)
    assert not reader.is_alive() and not changer.is_alive() and revoked.is_set()
    if replies and replies[0]['ok'] and replies[0]['result']['status'] == 'OBSERVED':
        assert replies[0]['result']['strategy_subject']['identity']['principal_ref'] == 'strategy-principal'
    later = service['request'](SOURCE, arguments())
    assert not later['ok'] or later['result']['status'] != 'OBSERVED'
