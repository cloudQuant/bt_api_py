"""Historical strategy cancellation reads, without a host or file database."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway import direct_control as direct_control_module
from bt_api_gateway.direct_control import GatewayDirectControl, cancel_scope_cause_id

from bt_api_py.service_registry import ServiceDispatchAdmission
from tests.unit.test_iter64_gateway_strategy_identity_fake import strategy_binding
from tests.unit.test_iter64_strategy_cancel_gateway_fake import call


@contextmanager
def historical_control(monkeypatch, *, stored_generation='old-generation', origin=None):
    binding, _, context = strategy_binding(monkeypatch)
    db = sqlite3.connect(':memory:')
    try:
        db.execute('PRAGMA temp_store=MEMORY')
        assert db.execute('PRAGMA temp_store').fetchone()[0] == 2
        assert db.execute('PRAGMA journal_mode=MEMORY').fetchone()[0] == 'memory'
        assert db.execute('PRAGMA database_list').fetchone()[2] == ''
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE direct_control_causes (account TEXT, cause_id TEXT, scope_kind TEXT, '
                   'scope_value TEXT, mode TEXT, origin TEXT, generation TEXT, active INTEGER, '
                   'version INTEGER, resume_id TEXT)')
        db.execute('CREATE TABLE direct_control_targets (account TEXT, cause_id TEXT, strategy TEXT, '
                   'request_key TEXT, reservation_id TEXT, operation_id TEXT, state TEXT)')
        db.execute('CREATE TABLE direct_journal_commands (account TEXT, strategy TEXT, request_key TEXT, '
                   'reservation_id TEXT, native_identity_json TEXT, payload_json TEXT, status TEXT)')
        cause = cancel_scope_cause_id('operation')
        expected_origin = 'risk:' + hashlib.sha256(
            json.dumps(['risk-principal', 'actor'], separators=(',', ':')).encode()
        ).hexdigest()
        db.execute('INSERT INTO direct_control_causes VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, NULL)',
                   ('account', cause, 'strategy', 'strategy-a', 'CANCEL_ONLY',
                    expected_origin if origin is None else origin, stored_generation))
        for suffix in ('a', 'z'):
            db.execute('INSERT INTO direct_journal_commands VALUES (?, ?, ?, ?, ?, ?, ?)',
                       ('account', 'strategy-a', 'request-' + suffix, 'reservation-' + suffix,
                        '{}', '{}', 'SENT'))
            db.execute('INSERT INTO direct_control_targets VALUES (?, ?, ?, ?, ?, ?, ?)',
                       ('account', cause, 'strategy-a', 'request-' + suffix, 'reservation-' + suffix,
                        'cancel-' + suffix, 'PENDING'))

        @contextmanager
        def transaction():
            yield db

        journal = SimpleNamespace(account_scope='account', _schema_version=17, journal_id='journal',
                                  generation='generation', _max_records=100, _transaction=transaction,
                                  _bump=lambda _: pytest.fail('historical read wrote a revision'))
        control = object.__new__(GatewayDirectControl)
        control._journal, control._generation, control._claims, control._guard = journal, 'generation', {}, object()
        binding._journal, binding._control = journal, control
        monkeypatch.setattr(GatewayDirectControl, '_owned', lambda _: None)
        monkeypatch.setattr(direct_control_module, '_terminal_observation', lambda *_: None)
        for name in ('cancel_scope', 'reconcile_targets', 'claim_target', 'dispatch_claimed_cancel'):
            monkeypatch.setattr(GatewayDirectControl, name,
                                lambda *args, **kwargs: pytest.fail('historical read attempted a write'))
        yield binding, control, context, db, cause, expected_origin
    finally:
        db.rollback()
        db.close()


def history(binding, context, **changes):
    args = {'operation_id': 'operation', 'strategy': 'strategy-a', 'after': '', 'limit': 1,
            'expected_journal_id': 'journal', 'expected_generation': 'generation',
            'history_version': 1, 'original_generation': 'old-generation'}
    args.update(changes)
    return call(binding, context, 'cancel_scope_status', **args)


def test_old_generation_can_be_read_without_mutation_and_all_pages_remain_bound(monkeypatch):
    with historical_control(monkeypatch) as (binding, _, context, db, cause, origin):
        before = db.total_changes
        first = history(binding, context)
        assert first == {'status': 'OBSERVED', 'operation_id': 'operation', 'scope_kind': 'strategy',
                         'scope_value': 'strategy-a', 'origin': origin,
                         'targets': [{'reservation_id': 'reservation-a', 'strategy': 'strategy-a',
                                      'request_key': 'request-a', 'state': 'QUEUED', 'cancel': None}],
                         'counts': {'QUEUED': 2, 'SENT': 0, 'NOT_SENT': 0, 'UNKNOWN': 0, 'TERMINAL': 0},
                         'after': '', 'limit': 1, 'remaining_at_cursor': 2, 'history_version': 1,
                         'journal_id': 'journal', 'original_generation': 'old-generation',
                         'current_generation': 'generation', 'read_only': True, 'retry_permitted': False}
        second = history(binding, context, after='reservation-a')
        assert second['status'] == 'OBSERVED' and second['targets'][0]['reservation_id'] == 'reservation-z'
        assert second['remaining_at_cursor'] == 1
        assert db.total_changes == before
        assert [row[0] for row in db.execute('SELECT state FROM direct_control_targets WHERE cause_id=?',
                                             (cause,))] == ['PENDING', 'PENDING']


def test_old_generation_missing_attempt_is_unknown_not_not_sent(monkeypatch):
    with historical_control(monkeypatch) as (binding, _, context, db, _, _):
        db.execute('UPDATE direct_control_targets SET state=? WHERE reservation_id=?',
                   ('UNKNOWN', 'reservation-a'))
        before = db.total_changes
        result = history(binding, context)
        assert result['status'] == 'OBSERVED'
        assert result['targets'][0]['state'] == 'UNKNOWN'
        assert result['targets'][0]['cancel'] is None
        assert result['counts']['NOT_SENT'] == 0 and result['counts']['UNKNOWN'] == 1
        assert result['read_only'] is True and result['retry_permitted'] is False
        assert db.total_changes == before


@pytest.mark.parametrize('fields', [
    {'history_version': None}, {'history_version': True}, {'history_version': 2},
    {'original_generation': ''}, {'original_generation': None},
])
def test_invalid_history_contract_rejected_before_read(monkeypatch, fields):
    with historical_control(monkeypatch) as (binding, control, context, _, _, _):
        monkeypatch.setattr(control, 'cancel_scope_status', lambda *a, **k: pytest.fail('unexpected read'))
        assert history(binding, context, **fields)['code'] == 'INVALID_REQUEST'


@pytest.mark.parametrize('fields', [
    {'original_generation': 'wrong'}, {'operation_id': 'not-present'},
])
def test_missing_or_wrong_original_identity_is_unknown(monkeypatch, fields):
    with historical_control(monkeypatch) as (binding, _, context, db, _, _):
        before = db.total_changes
        assert history(binding, context, **fields) == {'status': 'UNKNOWN', 'cause_id': ''}
        assert db.total_changes == before


def test_absent_original_generation_and_unbound_persisted_generation_fail_closed(monkeypatch):
    with historical_control(monkeypatch, stored_generation=None) as (binding, _, context, _, _, _):
        args = {'operation_id': 'operation', 'strategy': 'strategy-a', 'after': '', 'limit': 1,
                'expected_journal_id': 'journal', 'expected_generation': 'generation', 'history_version': 1}
        assert call(binding, context, 'cancel_scope_status', **args)['code'] == 'INVALID_REQUEST'
        assert history(binding, context) == {'status': 'UNKNOWN', 'cause_id': ''}


@pytest.mark.parametrize('fields,code', [
    ({'strategy': 'strategy-b'}, 'SCOPE_DENIED'),
    ({'expected_journal_id': 'wrong'}, 'JOURNAL_ID_MISMATCH'),
    ({'expected_generation': 'old-generation'}, 'JOURNAL_GENERATION_MISMATCH'),
])
def test_current_authorization_and_owner_identity_are_required(monkeypatch, fields, code):
    with historical_control(monkeypatch) as (binding, control, context, _, _, _):
        monkeypatch.setattr(control, 'cancel_scope_status', lambda *a, **k: pytest.fail('unexpected read'))
        assert history(binding, context, **fields)['code'] == code


def test_original_origin_and_actor_mismatch_never_disclose(monkeypatch):
    with historical_control(monkeypatch, origin='risk:some-other-operator') as (binding, _, context, _, _, _):
        assert history(binding, context) == {'status': 'UNKNOWN', 'cause_id': ''}
        wrong_actor = replace(context, identity=WireIdentity(WireRole.APPLICATION_CLIENT, 'risk-principal', 'other'))
        assert history(binding, wrong_actor)['code'] == 'SCOPE_DENIED'


def test_context_must_match_original_registry_admission(monkeypatch):
    with historical_control(monkeypatch) as (binding, control, context, _, _, _):
        monkeypatch.setattr(ServiceDispatchAdmission, 'matches_context', lambda self, ctx: ctx is context)
        monkeypatch.setattr(control, 'cancel_scope_status', lambda *a, **k: pytest.fail('unexpected read'))
        other_registration = replace(context, registration_id='another-registration')
        assert history(binding, other_registration)['code'] == 'ADMISSION_DENIED'


def test_other_scope_and_corrupt_target_beyond_page_do_not_leak_counts(monkeypatch):
    with historical_control(monkeypatch) as (binding, _, context, db, cause, _):
        db.execute('UPDATE direct_control_causes SET scope_value=? WHERE cause_id=?', ('strategy-b', cause))
        assert history(binding, context) == {'status': 'UNKNOWN', 'cause_id': ''}
        db.execute('UPDATE direct_control_causes SET scope_value=? WHERE cause_id=?', ('strategy-a', cause))
        db.execute('UPDATE direct_control_targets SET strategy=? WHERE reservation_id=?',
                   ('strategy-b', 'reservation-z'))
        assert history(binding, context) == {'status': 'UNKNOWN', 'cause_id': ''}


def test_grant_revoked_during_read_and_replaced_owner_fail_closed(monkeypatch):
    with historical_control(monkeypatch) as (binding, control, context, _, _, _):
        original = control.cancel_scope_status

        def revoke(*args, **kwargs):
            value = original(*args, **kwargs)
            binding._control_grants.clear()
            return value

        monkeypatch.setattr(control, 'cancel_scope_status', revoke)
        assert history(binding, context) == {'status': 'UNKNOWN', 'cause_id': ''}
    with historical_control(monkeypatch) as (binding, control, context, _, _, _):
        original = control.cancel_scope_status

        def replace_owner(*args, **kwargs):
            value = original(*args, **kwargs)
            binding._owner = object()
            return value

        monkeypatch.setattr(control, 'cancel_scope_status', replace_owner)
        assert history(binding, context) == {'status': 'UNKNOWN', 'cause_id': ''}


def test_history_not_available_via_strategy_registration_or_cancel_write(monkeypatch):
    with historical_control(monkeypatch) as (binding, _, context, _, _, _):
        context = replace(context, scope=WireScope('domain', account_ref='account', strategy_ref='strategy-a'))
        args = {'operation_id': 'operation', 'after': '', 'limit': 1, 'history_version': 1,
                'original_generation': 'old-generation', 'expected_journal_id': 'journal',
                'expected_generation': 'generation'}
        assert call(binding, context, 'cancel_scope_status', **args)['code'] == 'INVALID_REQUEST'
        assert call(binding, context, 'cancel_scope', operation_id='operation', limit=1,
                    history_version=1, original_generation='old-generation')['code'] == 'INVALID_REQUEST'
