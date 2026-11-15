"""Synchronous service -> dispatcher -> memory journal tests with fake authority and native leaf."""
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from decimal import Decimal
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from bt_api_base.direct_write_protocol import NativeOrderIdentity, PreDispatchRecord, WriteOperation
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import DirectCommandJournalError
from bt_api_gateway.direct_dispatch import DirectDispatchError, GatewayDirectDispatcher, NativeDispatchOutcome
from bt_api_gateway.direct_strategy_origin import authorize_strategy_origin
from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py import gateway_direct_service as service
from tests._synthetic_strategy_origin_source import SyntheticStrategyOriginSource
from bt_api_py.service_registry import ServiceCallContext

_spec = importlib.util.spec_from_file_location('gateway_memory_binding_fixture', Path(__file__).parents[2] /
    'bt_api/bt_api_gateway/tests/test_direct_instrument_binding_memory.py')
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)


class Admission:
    def __init__(self, operation='direct.dispatch'):
        self.current = True
        self.operation = operation
        self.expected_context = None
        self._binding = SimpleNamespace(service_epoch='epoch')
    def matches_context(self, value): return self.current and (self.expected_context is None or value == self.expected_context)
    def matches_operation(self, value): return self.current and value == self.operation
    def matches(self, _): return self.current
    def close(self): self.current = False
    @contextmanager
    def scope(self):
        def check():
            if not self.current: raise RuntimeError('revoked')
        check()
        yield check


class Claim:
    def ready(self): return True
    def claim_native(self, record, _identity, validate):
        validate(0)
        return record.reservation_id
    def native_check(self, _): return lambda: True
    def retire(self, _): pass


class Owner:
    def __init__(self, dispatcher): self.dispatcher, self.error = dispatcher, None
    def dispatch_refusal(self): return None
    def submit_service_call(self, callback):
        class Result:
            def result(self, timeout_seconds): return callback()
        return Result()
    def enqueue(self, command, *, request_fence, finalize, **_):
        try:
            self.dispatcher.dispatch(command.strategy, command.request_key, command.principal,
                command.reservation_id, command.digest, command.expected_version, request_fence=request_fence)
        except Exception as error:
            self.error = error
        finally:
            finalize()
        return 'QUEUED'


@pytest.fixture
def chain(monkeypatch):
    fixture = _fixture.owner.__wrapped__(monkeypatch)
    journal, db = next(fixture)
    journal.recheck_dispatch_controls = lambda *_: None
    payload = OrderRequest('rb2601', Side.BUY, OrderType.LIMIT, Decimal('1'), 'acct', 'client',
        price=Decimal('10'), idempotency_key='req', exchange_id='SHFE', offset='open', quantity_unit='lots').to_dict()
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
    scope = WireScope('domain', account_ref='acct', strategy_ref='strategy', run_id='run')
    context = ServiceCallContext(identity, scope, 'reg', 'principal')
    original = authorize_strategy_origin(context, _fixture.KEY, payload, account='acct', journal_id='jid',
        generation='generation', service_epoch='epoch', registry_recheck=lambda _: True, instrument_recheck=lambda _: True)
    row = journal.reserve('strategy', 'req', 'principal', payload, reservation_fence=nullcontext,
                          strategy_origin_permit=original)
    state = {'key': _fixture.KEY, 'active': True, 'sent': 0, 'raise': False, 'revoke_at_commit': False,
             'revoke_admission_at_commit': False, 'interrupt_at_commit': False}
    def source_check(key):
        if state['raise']: raise RuntimeError('source failure')
        return state['active'] and key == state['key']
    import bt_api_py.gateway_strategy_origin_source as source_module
    monkeypatch.setattr(source_module, "GatewayStrategyOriginSource", SyntheticStrategyOriginSource)
    source = SyntheticStrategyOriginSource(resolve=lambda *_: state['key'], recheck=source_check)
    admission = Admission()
    context = ServiceCallContext(identity, scope, 'reg', 'principal', dispatch_admission=admission)
    monkeypatch.setattr(service, 'ServiceDispatchAdmission', Admission)
    monkeypatch.setattr(service, '_public', lambda observation, journal: {'status': 'OK', 'state': observation.record.status})
    native_identity = NativeOrderIdentity('CTP', 'synthetic', {'trading_day': '20300101', 'front_id': 1}, 1)
    def native(payload, port, account):
        record = PreDispatchRecord(operation=WriteOperation.PLACE, host_scope=account, exchange_name='CTP',
            symbol='rb2601', identity=native_identity, target_identity=None, request_summary=payload,
            trading_day='20300101', session_generation=1, created_at_utc='synthetic')
        if state['revoke_at_commit']: state['active'] = False
        if state['revoke_admission_at_commit']: admission.current = False
        if state['interrupt_at_commit']: raise KeyboardInterrupt()
        marker = port.commit(record)
        if marker is not None:
            with port.native_fence() as check:
                if check(): state['sent'] += 1
        return NativeDispatchOutcome(True, native_identity, marker)
    dispatcher = object.__new__(GatewayDirectDispatcher)
    dispatcher._journal, dispatcher._exchange, dispatcher._native_place = journal, 'CTP', native
    dispatcher._identity_keys, dispatcher._admission = lambda _: ('strong',), lambda _: True
    dispatcher._claim_port, dispatcher._faulted, dispatcher._active = Claim(), False, False
    dispatcher._owned = lambda: None
    # Finish is a fake leaf; the actual dispatch marker is written by the journal.
    journal.finish = lambda *args: journal.get('strategy', 'req', 'principal')
    owner = Owner(dispatcher)
    binding = object.__new__(service.GatewayDirectServiceBinding)
    binding._journal, binding._owner, binding._strategy_origin_source = journal, owner, source
    binding._reservation_fence = lambda *_: nullcontext()
    binding._grants = {'principal': service.GatewayDirectGrant('strategy', frozenset({'rb2601'}))}
    binding._risk_only_submit, binding._market_gate_required = frozenset(), False
    def call():
        return binding.handle('direct.dispatch', context=context, arguments={
            'request_key': 'req', 'reservation_id': row.reservation_id,
            'digest': row.digest, 'expected_version': row.version})
    yield SimpleNamespace(journal=journal, db=db, row=row, state=state, owner=owner, call=call,
                          admission=admission, binding=binding, context=context, payload=payload)
    source.close()
    try: next(fixture)
    except StopIteration: pass


def test_bound_dispatch_refuses_before_enqueue_and_preserves_reservation(chain):
    assert chain.call() == {'status': 'REQUEST_REJECTED', 'code': 'STRATEGY_ORIGIN_CLAIM_UNAVAILABLE'}
    assert chain.owner.error is None and chain.state['sent'] == 0
    assert chain.db.execute('SELECT status FROM direct_journal_commands').fetchone()[0] == 'RESERVED'


def test_dispatcher_itself_refuses_before_expiry_or_claim(chain):
    chain.journal.expire = lambda *_: pytest.fail('must not expire the old reservation')
    dispatcher, row = chain.owner.dispatcher, chain.row
    with pytest.raises(DirectDispatchError, match='STRATEGY_ORIGIN_CLAIM_UNAVAILABLE'):
        dispatcher.dispatch(row.strategy_scope, row.request_key, row.principal, row.reservation_id,
                            row.digest, row.version)
    assert chain.state['sent'] == 0
    assert chain.db.execute('SELECT status FROM direct_journal_commands').fetchone()[0] == 'RESERVED'


@pytest.mark.parametrize('failure', ['wrong_key', 'revoked', 'exception', 'revoke_before_marker', 'registry_revoked'])
def test_bad_source_cannot_write_marker_or_send(chain, failure):
    if failure == 'wrong_key': chain.state['key'] = replace(_fixture.KEY, environment='different')
    if failure == 'revoked': chain.state['active'] = False
    if failure == 'exception': chain.state['raise'] = True
    if failure == 'revoke_before_marker': chain.state['revoke_at_commit'] = True
    if failure == 'registry_revoked': chain.state['revoke_admission_at_commit'] = True
    assert chain.call() == {'status': 'REQUEST_REJECTED', 'code': 'STRATEGY_ORIGIN_CLAIM_UNAVAILABLE'}
    assert chain.owner.error is None
    assert chain.state['sent'] == 0
    assert chain.db.execute('SELECT status FROM direct_journal_commands').fetchone()[0] == 'RESERVED'
    assert chain.db.execute('SELECT count(*) FROM direct_journal_strong_keys').fetchone()[0] == 0


def test_unselected_instrument_binding_dispatch_does_not_require_source(chain):
    chain.journal._instrument_binding_enabled = False
    chain.state['active'] = False
    chain.call()
    assert chain.owner.error is None
    assert chain.state['sent'] == 1


@pytest.mark.parametrize('state', ['DISPATCHING', 'SENT', 'UNKNOWN'])
def test_prior_dispatch_fact_is_observed_without_new_claim(chain, state):
    chain.db.execute('UPDATE direct_journal_commands SET status=?', (state,))
    before = tuple(chain.db.execute('SELECT * FROM direct_journal_commands').fetchone())
    assert chain.call() == {'status': 'OK', 'state': state, 'dispatch_queue_state': 'OBSERVED'}
    assert tuple(chain.db.execute('SELECT * FROM direct_journal_commands').fetchone()) == before
    assert chain.state['sent'] == 0


@pytest.mark.parametrize('change', ['registry_aba', 'owner_shutdown'])
def test_real_claim_race_before_marker_never_sends(chain, change):
    from bt_api_py.gateway_direct_owner import GatewayDirectOwner
    from bt_api_py.gateway_dispatch_claim import GatewayDispatchClaimCoordinator, GatewayNativeDispatchClaimPort

    owner = GatewayDirectOwner(lambda: None, queue_size=1, max_queue_wait_seconds=1, busy_timeout_seconds=1)
    coordinator = GatewayDispatchClaimCoordinator._for_test('acct', 'generation')
    owner._bind_dispatch_lifecycle(coordinator, account_scope='acct', generation='generation')
    for domain in coordinator._domains:
        token = coordinator.begin_change(domain)
        coordinator.finish_change(token, 'READY')
    original = coordinator.try_claim
    def mutate_then_claim(candidate):
        if change == 'registry_aba':
            token = coordinator.begin_change('registry')
            coordinator.finish_change(token, 'READY')
        else:
            owner._fence_dispatch_shutdown()
        return original(candidate)
    coordinator.try_claim = mutate_then_claim
    chain.journal._instrument_binding_enabled = False
    chain.owner.dispatcher._claim_port = GatewayNativeDispatchClaimPort(coordinator, journal_id='jid', max_attempts=1)
    chain.call()
    assert getattr(chain.owner.error, 'code', None) == 'DISPATCH_CLAIM_BUSY'
    assert chain.state['sent'] == 0
    assert chain.db.execute('SELECT status FROM direct_journal_commands').fetchone()[0] == 'RESERVED'
    assert chain.db.execute('SELECT count(*) FROM direct_journal_strong_keys').fetchone()[0] == 0


def reserve_call(chain, *, context=None):
    admission = Admission('direct.reserve')
    context = context or chain.context
    context = replace(context, dispatch_admission=admission)
    admission.expected_context = context
    result = chain.binding.handle('direct.reserve', context=context,
        arguments={'request_key': 'req', 'payload': chain.payload, 'ttl_seconds': 10})
    assert not admission.current
    return result


@pytest.mark.parametrize('failure', ['none', 'wrong_key', 'revoked', 'exception', 'race'])
def test_reserve_service_uses_actual_journal_and_origin_permit(chain, failure):
    before = tuple(chain.db.execute('SELECT * FROM direct_journal_commands').fetchone())
    if failure in {'wrong_key', 'race'}: chain.state['key'] = replace(_fixture.KEY, environment='different')
    if failure == 'revoked': chain.state['active'] = False
    if failure == 'exception': chain.state['raise'] = True
    if failure == 'race':
        observe = chain.journal.observe
        calls = []
        def not_found_once(*args):
            calls.append(None)
            if len(calls) == 1: raise DirectCommandJournalError('NOT_FOUND')
            return observe(*args)
        chain.journal.observe = not_found_once
    result = reserve_call(chain)
    assert result['status'] == ('OK' if failure == 'none' else 'UNKNOWN')
    assert tuple(chain.db.execute('SELECT * FROM direct_journal_commands').fetchone()) == before
    assert chain.state['sent'] == 0


@pytest.mark.parametrize('state', ['SENT', 'UNKNOWN'])
def test_replay_preserves_already_dispatched_facts(chain, state):
    chain.db.execute('UPDATE direct_journal_commands SET status=?', (state,))
    before = tuple(chain.db.execute('SELECT * FROM direct_journal_commands').fetchone())
    result = reserve_call(chain)
    assert result == {'status': 'OK', 'state': state}
    assert tuple(chain.db.execute('SELECT * FROM direct_journal_commands').fetchone()) == before
    assert chain.state['sent'] == 0


@pytest.mark.parametrize('field', ['scope', 'principal', 'run'])
def test_reserve_rejects_context_changed_after_admission(chain, field):
    admission = Admission('direct.reserve')
    original = replace(chain.context, dispatch_admission=admission)
    admission.expected_context = original
    if field == 'scope': altered = replace(original, scope=replace(original.scope, account_ref='other'))
    elif field == 'principal': altered = replace(original, principal_ref='other')
    else: altered = replace(original, scope=replace(original.scope, run_id='other'))
    result = chain.binding.handle('direct.reserve', context=altered,
        arguments={'request_key': 'req', 'payload': chain.payload, 'ttl_seconds': 10})
    assert result == {'status': 'REQUEST_REJECTED', 'code': 'ADMISSION_DENIED'}
    assert chain.db.execute('SELECT status FROM direct_journal_commands').fetchone()[0] == 'RESERVED'
