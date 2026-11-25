"""Strategy control identity and per-call journal fencing without I/O."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from types import MethodType, SimpleNamespace

import pytest

from tests.unit.test_iter64_strategy_cancel_gateway_fake import OwnedGuard, call, setup


def strategy_binding(monkeypatch, *, account=False):
    binding, control, context = setup(monkeypatch, account=account)
    grant = binding._control_grants['risk-principal']
    binding._control_grants['risk-principal'] = replace(
        grant, operations=grant.operations | {'direct.control.identity'}
    )

    @contextmanager
    def transaction():
        yield object()

    binding._journal._transaction = transaction
    binding._journal._assert_anchor = lambda _: None
    return binding, control, context


def test_only_granted_strategy_can_read_identity_or_cancel(monkeypatch):
    binding, _, context = strategy_binding(monkeypatch)
    reply = call(binding, context, 'identity', strategy='strategy-a')
    assert reply == {'status': 'OBSERVED', 'schema_version': 1, 'account_scope': 'account',
                     'journal_id': 'journal', 'journal_generation': 'generation'}
    assert call(binding, context, 'identity', strategy='strategy-b')['code'] == 'SCOPE_DENIED'
    assert call(binding, context, 'identity')['code'] == 'SCOPE_DENIED'
    assert call(binding, context, 'cancel_scope', operation_id='op', limit=1)['code'] == 'SCOPE_DENIED'
    assert call(binding, context, 'cancel_scope', operation_id='op', limit=1,
                strategy='strategy-b')['code'] == 'SCOPE_DENIED'


def test_identity_requires_identity_operation_grant(monkeypatch):
    binding, _, context = setup(monkeypatch, account=False)
    assert call(binding, context, 'identity', strategy='strategy-a')['code'] == 'SCOPE_DENIED'


def test_identity_checks_real_journal_anchor_in_memory(monkeypatch):
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

    binding, _, context = strategy_binding(monkeypatch)
    db = sqlite3.connect(':memory:')
    try:
        db.execute('PRAGMA temp_store=MEMORY')
        assert db.execute('PRAGMA temp_store').fetchone()[0] == 2
        assert db.execute('PRAGMA journal_mode=MEMORY').fetchone()[0] == 'memory'
        assert db.execute('PRAGMA database_list').fetchone()[2] == ''
        db.execute('CREATE TABLE direct_journal_meta '
                   '(account TEXT, schema_version INTEGER, max_records INTEGER, journal_id TEXT, revision INTEGER)')
        db.execute('INSERT INTO direct_journal_meta VALUES (?, ?, ?, ?, ?)', ('account', 3, 10, 'journal', 1))
        journal = binding._journal
        journal._account, journal._schema_version, journal._max_records = 'account', 3, 10
        journal._journal_id, journal._minimum_revision = 'journal', 0
        journal._assert_anchor = MethodType(GatewayDirectCommandJournal._assert_anchor, journal)

        @contextmanager
        def transaction():
            yield db

        journal._transaction = transaction
        assert call(binding, context, 'identity', strategy='strategy-a')['journal_id'] == 'journal'
        db.execute("UPDATE direct_journal_meta SET journal_id='different'")
        assert call(binding, context, 'identity', strategy='strategy-a') == {'status': 'UNKNOWN', 'cause_id': ''}
    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize('generation', [None, '', 1])
def test_identity_refuses_missing_or_invalid_generation(monkeypatch, generation):
    binding, _, context = strategy_binding(monkeypatch)
    binding._journal.generation = generation
    assert call(binding, context, 'identity', strategy='strategy-a')['code'] == 'CONTROL_UNAVAILABLE'


def test_identity_refuses_journal_generation_not_owned_by_control(monkeypatch):
    binding, _, context = strategy_binding(monkeypatch)
    binding._journal.generation = 'new-generation'
    assert call(binding, context, 'identity', strategy='strategy-a') == {'status': 'UNKNOWN', 'cause_id': ''}


def test_strategy_status_requires_expected_journal(monkeypatch):
    binding, control, context = strategy_binding(monkeypatch)
    call(binding, context, 'cancel_scope', operation_id='op', limit=1, strategy='strategy-a',
         expected_journal_id='journal', expected_generation='generation')
    original = control.cancel_scope_status
    reads = []

    def observed(*args, **kwargs):
        reads.append(args)
        return original(*args, **kwargs)

    control.cancel_scope_status = observed
    args = {'operation_id': 'op', 'strategy': 'strategy-a', 'after': '', 'limit': 1}
    assert call(binding, context, 'cancel_scope_status', **args)['code'] == 'INVALID_REQUEST'
    assert call(binding, context, 'cancel_scope_status', **args, expected_journal_id='', expected_generation='generation')['code'] == 'INVALID_REQUEST'
    assert call(binding, context, 'cancel_scope_status', **args, expected_journal_id='wrong', expected_generation='generation')['code'] == 'JOURNAL_ID_MISMATCH'
    assert reads == []
    assert call(binding, context, 'cancel_scope_status', **args,
                expected_journal_id='journal', expected_generation='generation')['status'] == 'OBSERVED'
    assert len(reads) == 1


@pytest.mark.parametrize('operation', ['cancel_scope', 'cancel_scope_status'])
@pytest.mark.parametrize('identity_fields', [
    {}, {'expected_journal_id': 'journal'}, {'expected_generation': 'generation'},
    {'expected_journal_id': '', 'expected_generation': 'generation'},
    {'expected_journal_id': 'journal', 'expected_generation': ''},
    {'expected_journal_id': 'journal', 'expected_generation': None},
])
def test_strategy_create_and_status_require_exact_identity_fields(monkeypatch, operation, identity_fields):
    binding, control, context = strategy_binding(monkeypatch)
    args = {'operation_id': 'op', 'strategy': 'strategy-a', 'limit': 1, **identity_fields}
    if operation == 'cancel_scope_status':
        args['after'] = ''
    assert call(binding, context, operation, **args)['code'] == 'INVALID_REQUEST'
    assert not control.causes and not control.dispatched


@pytest.mark.parametrize('operation', ['cancel_scope', 'cancel_scope_status'])
def test_previous_owner_generation_is_not_accepted_after_restart_with_same_journal(monkeypatch, operation):
    binding, control, context = strategy_binding(monkeypatch)
    old = call(binding, context, 'identity', strategy='strategy-a')
    assert old['journal_id'] == 'journal' and old['journal_generation'] == 'generation'
    binding._journal.generation = 'new-generation'
    control._generation = 'new-generation'
    args = {'operation_id': 'op', 'strategy': 'strategy-a', 'limit': 1,
            'expected_journal_id': old['journal_id'], 'expected_generation': old['journal_generation']}
    if operation == 'cancel_scope_status':
        args['after'] = ''
    assert call(binding, context, operation, **args)['code'] == 'JOURNAL_GENERATION_MISMATCH'
    assert not control.causes and not control.dispatched


def test_identity_values_bind_strategy_create_and_status(monkeypatch):
    binding, control, context = strategy_binding(monkeypatch)
    identity = call(binding, context, 'identity', strategy='strategy-a')
    values = {'expected_journal_id': identity['journal_id'],
              'expected_generation': identity['journal_generation']}
    created = call(binding, context, 'cancel_scope', operation_id='op', limit=1,
                   strategy='strategy-a', **values)
    assert created['status'] == 'OBSERVED' and control.dispatched == [('strategy-a', 'r-a')]
    status = call(binding, context, 'cancel_scope_status', operation_id='op', limit=1, after='',
                  strategy='strategy-a', **values)
    assert status['status'] == 'OBSERVED' and status['scope_value'] == 'strategy-a'


@pytest.mark.parametrize('stored_generation', ['old-generation', None, 'generation'])
def test_real_control_rejects_old_cause_before_reconcile_or_claim(monkeypatch, stored_generation):
    from bt_api_gateway import direct_control as direct_control_module
    from bt_api_gateway.direct_control import GatewayDirectControl, cancel_scope_cause_id

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
                   'request_key TEXT, reservation_id TEXT, operation_id TEXT, state TEXT, '
                   'first_extra TEXT, second_extra TEXT)')
        db.execute('CREATE TABLE direct_control_blocked (account TEXT, cause_id TEXT, reservation_id TEXT)')
        db.execute('CREATE TABLE direct_journal_commands (account TEXT, strategy TEXT, request_key TEXT, '
                   'reservation_id TEXT, native_identity_json TEXT, payload_json TEXT, status TEXT)')
        db.execute('INSERT INTO direct_journal_commands VALUES (?, ?, ?, ?, ?, ?, ?)',
                   ('account', 'strategy-a', 'request-a', 'reservation-a', '{}', '{}', 'SENT'))
        cause = cancel_scope_cause_id('operation')
        origin = 'risk:' + hashlib.sha256(
            json.dumps(['risk-principal', 'actor'], separators=(',', ':')).encode()
        ).hexdigest()
        db.execute('INSERT INTO direct_control_causes VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, NULL)',
                   ('account', cause, 'strategy', 'strategy-a', 'CANCEL_ONLY', origin, stored_generation))
        db.execute('INSERT INTO direct_control_targets VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)',
                   ('account', cause, 'strategy-a', 'request-a', 'reservation-a', 'cancel-a', 'PENDING'))

        @contextmanager
        def transaction():
            yield db

        journal = SimpleNamespace(account_scope='account', _schema_version=17, journal_id='journal',
                                  generation='generation', _max_records=100, _transaction=transaction,
                                  _bump=lambda _: None)
        control = object.__new__(GatewayDirectControl)
        control._journal, control._generation, control._claims = journal, 'generation', {}
        control._guard = OwnedGuard()
        binding._journal, binding._control = journal, control
        monkeypatch.setattr(GatewayDirectControl, '_owned', lambda _: None)
        monkeypatch.setattr(direct_control_module, '_terminal_observation', lambda *_: None)
        reconciled, claimed, dispatched = [], [], []

        def reconcile(self, cause_id, *, before_commit):
            reconciled.append(cause_id)
            before_commit()

        real_claim = GatewayDirectControl.claim_target

        def claim(self, *args, **kwargs):
            claimed.append(args)
            return real_claim(self, *args, **kwargs)

        def dispatch(self, target, dispatcher, token, *, request_fence, requester, authority_source):
            with request_fence(target) as check:
                check()
                dispatched.append(target['reservation_id'])

        monkeypatch.setattr(GatewayDirectControl, 'reconcile_targets', reconcile)
        monkeypatch.setattr(GatewayDirectControl, 'claim_target', claim)
        monkeypatch.setattr(GatewayDirectControl, 'dispatch_claimed_cancel', dispatch)
        identity = {'strategy': 'strategy-a', 'expected_journal_id': 'journal',
                    'expected_generation': 'generation'}
        status = call(binding, context, 'cancel_scope_status', operation_id='operation',
                      after='', limit=1, **identity)
        result = call(binding, context, 'cancel_scope', operation_id='operation', limit=1, **identity)
        state = db.execute('SELECT state FROM direct_control_targets WHERE cause_id=?', (cause,)).fetchone()[0]
        if stored_generation == 'generation':
            assert status['status'] == 'OBSERVED' and result['status'] == 'OBSERVED'
            assert reconciled == [cause] and len(claimed) == 1
            assert dispatched == ['reservation-a'] and state == 'UNKNOWN'
        else:
            assert status == {'status': 'UNKNOWN', 'cause_id': ''}
            assert result == {'status': 'UNKNOWN', 'cause_id': ''}
            assert reconciled == [] and claimed == [] and dispatched == [] and state == 'PENDING'
            assert db.execute('SELECT generation FROM direct_control_causes WHERE cause_id=?', (cause,)).fetchone()[0] == stored_generation
    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize('change', ['journal', 'generation', 'control_generation', 'control', 'owner', 'guard', 'grant'])
@pytest.mark.parametrize('phase', ['before_commit', 'after_commit'])
def test_create_stops_without_dispatch_when_binding_changes(monkeypatch, change, phase):
    binding, control, context = strategy_binding(monkeypatch)
    original = control.cancel_scope

    def mutate():
        if change == 'journal':
            binding._journal = SimpleNamespace(account_scope='account', journal_id='journal', generation='generation')
        elif change == 'generation':
            binding._journal.generation = 'new-generation'
        elif change == 'control_generation':
            control._generation = 'new-generation'
        elif change == 'control':
            binding._control = object()
        elif change == 'owner':
            binding._owner = object()
        elif change == 'guard':
            control._guard = object()
        else:
            binding._control_grants.clear()

    def changed(*args, **kwargs):
        if phase == 'before_commit':
            mutate()
        result = original(*args, **kwargs)
        if phase == 'after_commit':
            mutate()
        return result

    control.cancel_scope = changed
    reply = call(binding, context, 'cancel_scope', operation_id='op', limit=1, strategy='strategy-a',
                 expected_journal_id='journal', expected_generation='generation')
    assert reply == {'status': 'UNKNOWN', 'cause_id': ''}
    assert control.dispatched == []
    assert bool(control.causes) is (phase == 'after_commit')


@pytest.mark.parametrize('change', [
    'generation', 'control_generation', 'owner', 'guard', 'live_guard', 'grant'
])
def test_create_rechecks_binding_before_target_dispatch(monkeypatch, change):
    binding, control, context = strategy_binding(monkeypatch)

    def mutate():
        if change == 'generation':
            binding._journal.generation = 'new-generation'
        elif change == 'control_generation':
            control._generation = 'new-generation'
        elif change == 'owner':
            binding._owner = object()
        elif change == 'guard':
            control._guard = object()
        elif change == 'live_guard':
            control._guard.owned = False
        else:
            binding._control_grants.clear()

    control.revoke_on_dispatch = mutate
    reply = call(binding, context, 'cancel_scope', operation_id='op', limit=1, strategy='strategy-a',
                 expected_journal_id='journal', expected_generation='generation')
    assert reply == {'status': 'UNKNOWN', 'cause_id': ''}
    assert control.dispatched == []
    assert control.frozen['op'][0]['state'] == 'UNKNOWN'


@pytest.mark.parametrize('change', [
    'generation', 'control_generation', 'journal', 'control', 'grant', 'ownership', 'live_guard'
])
def test_strategy_status_never_returns_data_after_binding_changes(monkeypatch, change):
    binding, control, context = strategy_binding(monkeypatch)
    call(binding, context, 'cancel_scope', operation_id='op', limit=1, strategy='strategy-a',
         expected_journal_id='journal', expected_generation='generation')
    original = control.cancel_scope_status

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == 'generation':
            binding._journal.generation = 'new-generation'
        elif change == 'control_generation':
            control._generation = 'new-generation'
        elif change == 'journal':
            binding._journal = SimpleNamespace(account_scope='account', journal_id='journal', generation='generation')
        elif change == 'control':
            binding._control = object()
        elif change == 'grant':
            binding._control_grants.clear()
        elif change == 'ownership':
            control._owned = lambda: (_ for _ in ()).throw(RuntimeError('synthetic secret'))
        else:
            control._guard.owned = False
        return result

    control.cancel_scope_status = changed
    result = call(binding, context, 'cancel_scope_status', operation_id='op', strategy='strategy-a',
                  after='', limit=1, expected_journal_id='journal', expected_generation='generation')
    assert result == {'status': 'UNKNOWN', 'cause_id': ''}
    assert 'synthetic secret' not in repr(result)


@pytest.mark.parametrize('change', ['generation', 'grant', 'journal'])
def test_identity_rechecks_after_anchor_and_never_leaks_changed_result(monkeypatch, change):
    binding, _, context = strategy_binding(monkeypatch)

    def changed(_):
        if change == 'generation':
            binding._journal.generation = 'new-generation'
        elif change == 'grant':
            binding._control_grants.clear()
        else:
            binding._journal = SimpleNamespace(account_scope='account', journal_id='journal', generation='generation')

    binding._journal._assert_anchor = changed
    assert call(binding, context, 'identity', strategy='strategy-a') == {'status': 'UNKNOWN', 'cause_id': ''}


def test_identity_rechecks_after_transaction_exit(monkeypatch):
    binding, _, context = strategy_binding(monkeypatch)

    @contextmanager
    def transaction():
        yield object()
        binding._journal.generation = 'new-generation'

    binding._journal._transaction = transaction
    assert call(binding, context, 'identity', strategy='strategy-a') == {'status': 'UNKNOWN', 'cause_id': ''}


def test_identity_rechecks_grant_after_owner_check(monkeypatch):
    binding, control, context = strategy_binding(monkeypatch)
    control._owned = lambda: binding._control_grants.clear()
    assert call(binding, context, 'identity', strategy='strategy-a') == {'status': 'UNKNOWN', 'cause_id': ''}


def test_risk_account_status_requires_and_accepts_current_identity(monkeypatch):
    binding, _, context = strategy_binding(monkeypatch, account=True)
    identity = call(binding, context, 'identity')
    assert call(binding, context, 'cancel_scope', operation_id='op', limit=1)['code'] == 'INVALID_REQUEST'
    expected = {
        'expected_journal_id': identity['journal_id'],
        'expected_generation': identity['journal_generation'],
    }
    assert call(binding, context, 'cancel_scope', operation_id='op', limit=1,
                **expected)['status'] == 'OBSERVED'
    assert call(binding, context, 'cancel_scope_status', operation_id='op', after='',
                limit=1)['code'] == 'INVALID_REQUEST'
    result = call(binding, context, 'cancel_scope_status', operation_id='op', after='',
                  limit=1, **expected)
    assert result['status'] == 'OBSERVED' and result['scope_kind'] == 'account'


def test_existing_strategy_scoped_registration_keeps_its_prior_contract(monkeypatch):
    binding, control, context = setup(monkeypatch, account=False, strategy_scope='strategy-a')
    result = call(binding, context, 'cancel_scope', operation_id='legacy', limit=1)
    assert result['status'] == 'OBSERVED' and result['scope_value'] == 'strategy-a'
    assert control.dispatched == [('strategy-a', 'r-a')]
    status = call(binding, context, 'cancel_scope_status', operation_id='legacy', after='', limit=1)
    assert status['status'] == 'OBSERVED' and status['scope_value'] == 'strategy-a'
