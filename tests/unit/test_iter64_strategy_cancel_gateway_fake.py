"""Exercise the Gateway control handler without a host, account, or I/O."""
import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_direct_owner import GatewayOwnerServiceCallError
from bt_api_py.gateway_direct_service import GatewayControlGrant, GatewayDirectServiceBinding
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission


class ImmediateResult:
    def __init__(self, function):
        self.function = function

    def result(self, timeout_seconds):
        try:
            return self.function()
        except Exception as error:
            raise GatewayOwnerServiceCallError('SYNTHETIC_FAILURE') from error


class OwnedGuard:
    def __init__(self, generation='generation'):
        self.account_key_digest = hashlib.sha256(b'account').hexdigest()
        self.generation = generation
        self.owned = True

    def assert_owned(self):
        if not self.owned:
            raise RuntimeError('synthetic ownership lost')

    def acquire(self):
        self.assert_owned()
        return self.generation


class Control:
    def __init__(self):
        self.order_strategies = {'r-a': 'strategy-a', 'r-b': 'strategy-b'}
        self.causes = {}
        self.frozen = {}
        self.dispatched = []
        self.corrupt_page = False
        self.foreign_origin = False
        self.revoke_on_dispatch = None

    def _owned(self):
        pass

    def cancel_scope(self, operation_id, *, scope_kind, scope_value, origin, before_commit):
        row = {'cause_id': 'cause-' + operation_id, 'scope_kind': scope_kind, 'scope_value': scope_value,
               'origin': origin, 'mode': 'CANCEL_ONLY', 'version': 1, 'generation': self._generation}
        before_commit()
        old = self.causes.get(operation_id)
        if old is not None and old != row:
            raise ValueError('conflict')
        self.causes[operation_id] = row
        self.frozen.setdefault(operation_id, [
            {'cause_id': row['cause_id'], 'strategy': strategy, 'reservation_id': reservation,
             'request_key': 'request-' + reservation, 'state': 'PENDING'}
            for reservation, strategy in self.order_strategies.items()
            if scope_kind != 'strategy' or strategy == scope_value])
        return row

    def cancel_scope_status(self, operation_id, *, after, limit):
        row = self.causes.get(operation_id)
        if row is None:
            return None
        rows = self.frozen[operation_id]
        if row['scope_kind'] == 'strategy' and any(t['strategy'] != row['scope_value'] for t in rows):
            raise ValueError('target scope conflict')
        rows_after = [target for target in rows if target['reservation_id'] > after]
        counts = dict.fromkeys(('QUEUED', 'SENT', 'NOT_SENT', 'UNKNOWN', 'TERMINAL'), 0)
        for target in rows:
            counts['QUEUED' if target['state'] == 'PENDING' else 'UNKNOWN'] += 1
        page = [dict(target, state='QUEUED' if target['state'] == 'PENDING' else 'UNKNOWN', cancel=None)
                for target in rows_after[:limit]]
        returned_row = dict(row, origin='risk:other') if self.foreign_origin else dict(row)
        return returned_row, page, counts, len(rows_after)

    def reconcile_targets(self, cause_id, *, before_commit):
        before_commit()

    def targets(self, cause_id, *, after, limit):
        operation_id = cause_id.removeprefix('cause-')
        rows = [dict(row) for row in self.frozen[operation_id] if row['reservation_id'] > after]
        if self.corrupt_page and rows:
            rows[0]['strategy'] = 'strategy-b'
        return rows[:limit]

    def claim_target(self, cause_id, reservation_id, *, before_commit):
        operation_id = cause_id.removeprefix('cause-')
        row = next(item for item in self.frozen[operation_id] if item['reservation_id'] == reservation_id)
        before_commit()
        row['state'] = 'UNKNOWN'
        return dict(row), object()

    def dispatch_claimed_cancel(self, row, dispatcher, token, *, request_fence, requester, authority_source):
        if self.revoke_on_dispatch is not None:
            self.revoke_on_dispatch()
        with request_fence(row) as check:
            check()
            self.dispatched.append((row['strategy'], row['reservation_id']))


def setup(monkeypatch, *, strategies=frozenset({'strategy-a'}), account=True, strategy_scope=None):
    operation_names = frozenset({'direct.control.cancel_scope', 'direct.control.cancel_scope_status'})
    grant = GatewayControlGrant('actor', account, strategies, operation_names, kind='risk')
    binding = object.__new__(GatewayDirectServiceBinding)
    control = Control()
    binding._control = control
    binding._control_grants = {'risk-principal': grant}
    binding._journal = SimpleNamespace(account_scope='account', _schema_version=17, journal_id='journal',
                                       generation='generation')
    control._journal = binding._journal
    control._generation = 'generation'
    control._guard = OwnedGuard()
    binding._cancel_journal = object()
    binding._owner = SimpleNamespace(supports_cancel=True, _cancel_dispatcher=object(),
                                     submit_service_call=lambda fn: ImmediateResult(fn))
    admission = object.__new__(ServiceDispatchAdmission)
    admission._binding = SimpleNamespace(service_epoch='epoch')
    admission.closed = False
    monkeypatch.setattr(ServiceDispatchAdmission, 'matches_operation', lambda self, _: True)
    monkeypatch.setattr(ServiceDispatchAdmission, 'matches_context', lambda self, _: True)
    monkeypatch.setattr(ServiceDispatchAdmission, 'matches', lambda self, _: True)
    monkeypatch.setattr(ServiceDispatchAdmission, 'close', lambda self: setattr(self, 'closed', True))
    @contextmanager
    def scope(self):
        yield lambda: None
    monkeypatch.setattr(ServiceDispatchAdmission, 'scope', scope)
    context = ServiceCallContext(
        WireIdentity(WireRole.APPLICATION_CLIENT, 'risk-principal', 'actor'),
        WireScope('domain', account_ref='account', strategy_ref=strategy_scope),
        'registration', 'risk-principal', dispatch_admission=admission)
    return binding, control, context


def call(binding, context, operation, **arguments):
    return binding.handle('direct.control.' + operation, context=context, arguments=arguments)


def test_account_registration_cancels_only_granted_strategy_and_is_idempotent(monkeypatch):
    binding, control, context = setup(monkeypatch)
    request = {'operation_id': 'op-a', 'limit': 2, 'strategy': 'strategy-a',
               'expected_journal_id': 'journal', 'expected_generation': 'generation'}
    first = call(binding, context, 'cancel_scope', **request)
    assert first['status'] == 'OBSERVED'
    assert (first['scope_kind'], first['scope_value'], first['issued']) == ('strategy', 'strategy-a', 1)
    assert control.dispatched == [('strategy-a', 'r-a')]
    second = call(binding, context, 'cancel_scope', **request)
    assert second['issued'] == 0 and control.dispatched == [('strategy-a', 'r-a')]
    page = call(binding, context, 'cancel_scope_status', operation_id='op-a', strategy='strategy-a', after='', limit=1, expected_journal_id='journal', expected_generation='generation')
    assert page['status'] == 'OBSERVED' and [row['strategy'] for row in page['targets']] == ['strategy-a']
    assert page['targets'][0]['state'] == 'UNKNOWN'
    expected = 'risk:' + hashlib.sha256(json.dumps(['risk-principal', 'actor'], separators=(',', ':')).encode()).hexdigest()
    assert page['origin'] == expected
    assert context.dispatch_admission.closed


def test_account_registration_with_strategy_only_grant_never_grants_account_cancel(monkeypatch):
    binding, control, context = setup(monkeypatch, account=False, strategies=frozenset({'strategy-a'}))
    request = {'operation_id': 'op-a', 'limit': 2, 'strategy': 'strategy-a',
               'expected_journal_id': 'journal', 'expected_generation': 'generation'}
    result = call(binding, context, 'cancel_scope', **request)
    assert (result['status'], result['scope_kind'], result['scope_value']) == ('OBSERVED', 'strategy', 'strategy-a')
    assert control.dispatched == [('strategy-a', 'r-a')]
    status = call(binding, context, 'cancel_scope_status', operation_id='op-a', strategy='strategy-a', after='', limit=1, expected_journal_id='journal', expected_generation='generation')
    assert status['status'] == 'OBSERVED' and status['scope_value'] == 'strategy-a'
    assert call(binding, context, 'cancel_scope', operation_id='op-b', limit=1, strategy='strategy-b')['code'] == 'SCOPE_DENIED'
    assert call(binding, context, 'cancel_scope', operation_id='op-account', limit=1)['code'] == 'SCOPE_DENIED'
    assert call(binding, context, 'cancel_scope_status', operation_id='op-a', strategy='strategy-b', after='', limit=1, expected_journal_id='journal', expected_generation='generation')['code'] == 'SCOPE_DENIED'
    assert call(binding, context, 'cancel_scope_status', operation_id='op-a', after='', limit=1)['code'] == 'SCOPE_DENIED'
    assert list(control.causes) == ['op-a'] and control.dispatched == [('strategy-a', 'r-a')]


@pytest.mark.parametrize('changes', [
    {'strategies': frozenset()},
    {'strategies': frozenset({'strategy-b'})},
])
def test_account_scope_alone_does_not_authorize_strategy(monkeypatch, changes):
    binding, control, context = setup(monkeypatch, **changes)
    result = call(binding, context, 'cancel_scope', operation_id='op', limit=1, strategy='strategy-a')
    assert result == {'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}
    assert control.causes == {} and control.dispatched == []


def test_invalid_account_actor_or_duplicate_scope_is_rejected(monkeypatch):
    binding, control, context = setup(monkeypatch)
    other = replace(context, scope=WireScope('domain', account_ref='other'))
    assert call(binding, other, 'cancel_scope', operation_id='op', limit=1, strategy='strategy-a')['code'] == 'SCOPE_DENIED'
    other = replace(context, identity=WireIdentity(WireRole.APPLICATION_CLIENT, 'risk-principal', 'other'))
    assert call(binding, other, 'cancel_scope', operation_id='op', limit=1, strategy='strategy-a')['code'] == 'SCOPE_DENIED'
    other = replace(context, scope=WireScope('domain', account_ref='account', strategy_ref='strategy-a'))
    assert call(binding, other, 'cancel_scope', operation_id='op', limit=1, strategy='strategy-a')['code'] == 'INVALID_REQUEST'
    assert control.causes == {} and control.dispatched == []


def test_mismatched_status_scope_and_origin_do_not_leak(monkeypatch):
    binding, control, context = setup(monkeypatch, strategies=frozenset({'strategy-a', 'strategy-b'}))
    call(binding, context, 'cancel_scope', operation_id='op', limit=1, strategy='strategy-a', expected_journal_id='journal', expected_generation='generation')
    assert call(binding, context, 'cancel_scope_status', operation_id='op', strategy='strategy-b', after='', limit=1, expected_journal_id='journal', expected_generation='generation')['status'] == 'UNKNOWN'
    assert call(binding, context, 'cancel_scope', operation_id='op', limit=1, strategy='strategy-b', expected_journal_id='journal', expected_generation='generation')['status'] == 'UNKNOWN'
    assert control.dispatched == [('strategy-a', 'r-a')]
    control.foreign_origin = True
    assert call(binding, context, 'cancel_scope_status', operation_id='op', strategy='strategy-a', after='', limit=1, expected_journal_id='journal', expected_generation='generation')['code'] == 'SCOPE_DENIED'


def test_mixed_strategy_page_and_revoked_grant_cannot_dispatch(monkeypatch):
    binding, control, context = setup(monkeypatch)
    control.corrupt_page = True
    assert call(binding, context, 'cancel_scope', operation_id='op', limit=1, strategy='strategy-a', expected_journal_id='journal', expected_generation='generation')['status'] == 'UNKNOWN'
    assert control.dispatched == []
    binding, control, context = setup(monkeypatch)
    control.revoke_on_dispatch = lambda: binding._control_grants.clear()
    assert call(binding, context, 'cancel_scope', operation_id='op', limit=1, strategy='strategy-a', expected_journal_id='journal', expected_generation='generation')['status'] == 'UNKNOWN'
    assert control.dispatched == []
    assert control.frozen['op'][0]['state'] == 'UNKNOWN'


def test_handler_and_real_control_freeze_only_authorized_strategy_in_memory(monkeypatch):
    import sqlite3

    from bt_api_gateway import direct_control as direct_control_module
    from bt_api_gateway.direct_control import GatewayDirectControl

    binding, _, context = setup(monkeypatch, account=False)
    db = sqlite3.connect(':memory:')
    try:
        assert db.execute('PRAGMA journal_mode=MEMORY').fetchone()[0] == 'memory'
        db.execute('PRAGMA temp_store=MEMORY')
        assert db.execute('PRAGMA temp_store').fetchone()[0] == 2
        assert db.execute('PRAGMA database_list').fetchone()[2] == ''
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE direct_control_causes (account TEXT, cause_id TEXT, scope_kind TEXT, '
                   'scope_value TEXT, mode TEXT, origin TEXT, generation TEXT, active INTEGER, '
                   'version INTEGER, resume_id TEXT)')
        db.execute('CREATE TABLE direct_control_targets (account TEXT, cause_id TEXT, strategy TEXT, '
                   'request_key TEXT, reservation_id TEXT, operation_id TEXT, state TEXT, first_extra TEXT, '
                   'second_extra TEXT)')
        db.execute('CREATE TABLE direct_control_blocked (account TEXT, cause_id TEXT, reservation_id TEXT)')
        db.execute('CREATE TABLE direct_journal_commands (account TEXT, strategy TEXT, request_key TEXT, '
                   'reservation_id TEXT, native_identity_json TEXT, payload_json TEXT, status TEXT)')
        db.executemany('INSERT INTO direct_journal_commands VALUES (?, ?, ?, ?, ?, ?, ?)', [
            ('account', 'strategy-a', 'request-a', 'reservation-a', '{}', '{}', 'SENT'),
            ('account', 'strategy-b', 'request-b', 'reservation-b', '{}', '{}', 'SENT'),
        ])
        @contextmanager
        def transaction():
            yield db
        journal = SimpleNamespace(account_scope='account', _schema_version=17, journal_id='journal', generation='generation',
                                  _max_records=100, _transaction=transaction, _bump=lambda _: None)
        control = object.__new__(GatewayDirectControl)
        control._journal, control._generation, control._claims = journal, 'generation', {}
        control._guard = OwnedGuard()
        binding._journal, binding._control = journal, control
        monkeypatch.setattr(GatewayDirectControl, '_owned', lambda _: None)
        monkeypatch.setattr(direct_control_module, '_terminal_observation', lambda *_: None)
        monkeypatch.setattr(GatewayDirectControl, 'reconcile_targets',
                            lambda self, cause, before_commit: before_commit())
        dispatched = []
        def fake_dispatch(self, target, dispatcher, token, *, request_fence, requester, authority_source):
            with request_fence(target) as check:
                check()
                dispatched.append((target['strategy'], target['reservation_id']))
        monkeypatch.setattr(GatewayDirectControl, 'dispatch_claimed_cancel', fake_dispatch)
        result = call(binding, context, 'cancel_scope', operation_id='operation', limit=5, strategy='strategy-a', expected_journal_id='journal', expected_generation='generation')
        assert (result['status'], result['scope_kind'], result['scope_value'], result['issued']) == (
            'OBSERVED', 'strategy', 'strategy-a', 1)
        assert dispatched == [('strategy-a', 'reservation-a')]
        rows = db.execute('SELECT strategy, reservation_id, state FROM direct_control_targets').fetchall()
        assert [tuple(row) for row in rows] == [('strategy-a', 'reservation-a', 'UNKNOWN')]
        status = call(binding, context, 'cancel_scope_status', operation_id='operation',
                      strategy='strategy-a', after='', limit=1, expected_journal_id='journal', expected_generation='generation')
        assert status['status'] == 'OBSERVED' and status['targets'][0]['strategy'] == 'strategy-a'
        again = call(binding, context, 'cancel_scope', operation_id='operation', limit=5, strategy='strategy-a', expected_journal_id='journal', expected_generation='generation')
        assert again['issued'] == 0 and len(dispatched) == 1
        cause = db.execute('SELECT cause_id FROM direct_control_causes').fetchone()[0]
        db.execute('INSERT INTO direct_control_targets VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)',
                   ('account', cause, 'strategy-b', 'request-b', 'reservation-b', 'cancel-b', 'PENDING'))
        # The foreign target is beyond the first page but still invalidates
        # both the aggregate counts and any subsequent dispatch attempt.
        status = call(binding, context, 'cancel_scope_status', operation_id='operation',
                      strategy='strategy-a', after='', limit=1, expected_journal_id='journal', expected_generation='generation')
        assert status == {'status': 'UNKNOWN', 'cause_id': ''}
        retry = call(binding, context, 'cancel_scope', operation_id='operation', limit=5, strategy='strategy-a', expected_journal_id='journal', expected_generation='generation')
        assert retry == {'status': 'UNKNOWN', 'cause_id': ''}
        assert dispatched == [('strategy-a', 'reservation-a')]
    finally:
        db.rollback()
        db.close()
