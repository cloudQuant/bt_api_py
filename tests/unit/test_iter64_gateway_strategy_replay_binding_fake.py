"""Synchronous fake coverage of the SDK's bound strategy reserve path."""
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
import sqlite3

import pytest
from bt_api_base.service_protocol import WireRole
from bt_api_gateway.direct_command_journal import DirectCommandJournalError, payload_digest
from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py import gateway_direct_service as service
from bt_api_py import gateway_strategy_origin_source as source_module
from bt_api_gateway import direct_strategy_origin as origin_module


class FakeContext:
    def __init__(self):
        self.principal_ref = 'p'
        self.identity = SimpleNamespace(role=WireRole.STRATEGY_CLIENT, principal_ref='p')
        self.scope = SimpleNamespace(account_ref='acct', strategy_ref='s', run_id='run', account_refs=(),
                                     portfolio_ref=None, manual_book_ref=None, group_id=None, leg_id=None)
        self.dispatch_admission = None


class FakeAdmission:
    def __init__(self):
        self.active = True
        self.checks = 0
        self._binding = SimpleNamespace(service_epoch='epoch')

    def matches_context(self, _): return self.active
    def matches_operation(self, _): return self.active
    def matches(self, _): return self.active
    def close(self): self.active = False

    @contextmanager
    def scope(self):
        def check():
            self.checks += 1
            if not self.active:
                raise RuntimeError('revoked')
        check()
        yield check


class FakeSource:
    def __init__(self):
        self.instrument = ('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
        self.current = True

    def resolve(self, *_): return self.instrument
    def recheck(self, instrument): return self.current and instrument == self.instrument


class FakeOrigin:
    def __init__(self, context, instrument, registry_recheck, instrument_recheck):
        self.context, self.instrument = context, instrument
        self.registry_recheck, self.instrument_recheck = registry_recheck, instrument_recheck

    def recheck(self, *_):
        if self.registry_recheck(self.context) is not True or self.instrument_recheck(self.instrument) is not True:
            raise DirectCommandJournalError('STRATEGY_ORIGIN_UNAVAILABLE')


class FakeJournal:
    account_scope = 'acct'
    journal_id = 'journal'
    generation = 'gen'
    _schema_version = 16

    def __init__(self, bound=True):
        self._instrument_binding_enabled = bound
        self.db = sqlite3.connect(':memory:')
        assert self.db.execute('PRAGMA journal_mode=MEMORY').fetchone()[0] == 'memory'
        self.db.execute('PRAGMA temp_store=MEMORY')
        assert self.db.execute('PRAGMA temp_store').fetchone()[0] == 2
        assert self.db.execute('PRAGMA database_list').fetchone()[2] == ''
        self.db.execute('CREATE TABLE commands (digest TEXT, instrument TEXT, state TEXT, ttl INTEGER)')
        self.reserve_calls = 0
        self.on_not_found = None
        self.on_observe = None

    def insert(self, payload, instrument, state='SENT'):
        self.db.execute('INSERT INTO commands VALUES (?, ?, ?, ?)',
                        (payload_digest(payload), repr(instrument) if instrument else None, state, 7))
        self.db.commit()

    def observe(self, *_):
        row = self.db.execute('SELECT digest, instrument, state, ttl FROM commands').fetchone()
        if row is None:
            if self.on_not_found:
                callback, self.on_not_found = self.on_not_found, None
                callback()
            raise DirectCommandJournalError('NOT_FOUND')
        if self.on_observe:
            self.on_observe()
        return SimpleNamespace(record=SimpleNamespace(digest=row[0], state=row[2], ttl=row[3]))

    def reserve(self, _strategy, _key, _principal, payload, *, strategy_origin_permit, **_kwargs):
        self.reserve_calls += 1
        strategy_origin_permit.recheck()
        row = self.db.execute('SELECT digest, instrument FROM commands').fetchone()
        if row is None:
            self.insert(payload, strategy_origin_permit.instrument, 'RESERVED')
        elif row != (payload_digest(payload), repr(strategy_origin_permit.instrument)):
            raise DirectCommandJournalError('INSTRUMENT_BINDING_UNAVAILABLE')
        strategy_origin_permit.recheck()


class FakeOwner:
    def submit_service_call(self, callback):
        class Result:
            def result(self, timeout_seconds):
                assert timeout_seconds == 1.0
                return callback()
        return Result()


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(service, 'ServiceCallContext', FakeContext)
    monkeypatch.setattr(service, 'ServiceDispatchAdmission', FakeAdmission)
    monkeypatch.setattr(source_module, 'GatewayStrategyOriginSource', FakeSource)
    monkeypatch.setattr(origin_module, 'authorize_strategy_origin',
        lambda context, instrument, payload, **kwargs: FakeOrigin(context, instrument,
            kwargs['registry_recheck'], kwargs['instrument_recheck']))
    monkeypatch.setattr(service, '_public', lambda observation, journal: {
        'status': 'OK', 'state': observation.record.state, 'ttl': observation.record.ttl})
    journal, source = FakeJournal(), FakeSource()
    binding = object.__new__(service.GatewayDirectServiceBinding)
    binding._journal, binding._owner = journal, FakeOwner()
    binding._strategy_origin_source = source
    binding._reservation_fence = lambda *_: None
    binding._grants = {'p': service.GatewayDirectGrant('s', frozenset({'rb'}))}
    binding._risk_only_submit = frozenset()
    payload = OrderRequest('rb', Side.BUY, OrderType.LIMIT, Decimal('1'), 'acct', 'client',
        price=Decimal('10'), idempotency_key='key', exchange_id='SHFE', offset='open', quantity_unit='lots').to_dict()

    def call():
        context, admission = FakeContext(), FakeAdmission()
        context.dispatch_admission = admission
        response = binding.handle('direct.reserve', context=context,
            arguments={'request_key': 'key', 'payload': payload, 'ttl_seconds': 10})
        assert not admission.active
        return response, admission

    yield SimpleNamespace(journal=journal, source=source, binding=binding, payload=payload, call=call)
    journal.db.close()


def test_bound_replay_rechecks_and_preserves_sent_row(setup):
    setup.journal.insert(setup.payload, setup.source.instrument)
    before = setup.journal.db.execute('SELECT * FROM commands').fetchall()
    result, admission = setup.call()
    assert result == {'status': 'OK', 'state': 'SENT', 'ttl': 7}
    assert admission.checks >= 4 and setup.journal.reserve_calls == 1
    assert setup.journal.db.execute('SELECT * FROM commands').fetchall() == before


@pytest.mark.parametrize('failure', ['changed_instrument', 'missing_binding', 'revoked_source', 'missing_source'])
def test_bound_replay_without_complete_current_proof_is_unknown(setup, failure):
    setup.journal.insert(setup.payload, None if failure == 'missing_binding' else setup.source.instrument)
    before = setup.journal.db.execute('SELECT * FROM commands').fetchall()
    if failure == 'changed_instrument':
        setup.source.instrument = ('ctp', 'SHFE', 'simnow', 'option', 'rb', 'rb')
    if failure == 'revoked_source':
        setup.source.current = False
    if failure == 'missing_source':
        setup.binding._strategy_origin_source = None
    assert setup.call()[0] == {'status': 'UNKNOWN'}
    assert setup.journal.db.execute('SELECT * FROM commands').fetchall() == before


def test_concurrent_insert_after_preflight_still_checks_full_key(setup):
    other = ('ctp', 'SHFE', 'simnow', 'option', 'rb', 'rb')
    setup.journal.on_not_found = lambda: setup.journal.insert(setup.payload, other)
    assert setup.call()[0] == {'status': 'UNKNOWN'}
    assert setup.journal.reserve_calls == 1
    assert setup.journal.db.execute('SELECT instrument, state, ttl FROM commands').fetchone() == (repr(other), 'SENT', 7)


def test_revocation_after_journal_check_is_unknown(setup):
    setup.journal.insert(setup.payload, setup.source.instrument)
    observations = []
    def revoke_after_second_observation():
        observations.append(None)
        if len(observations) == 2:
            setup.source.current = False
    setup.journal.on_observe = revoke_after_second_observation
    assert setup.call()[0] == {'status': 'UNKNOWN'}
    assert setup.journal.db.execute('SELECT state, ttl FROM commands').fetchone() == ('SENT', 7)


def test_missing_source_after_not_found_is_unknown(setup):
    setup.binding._strategy_origin_source = None
    assert setup.call()[0] == {'status': 'UNKNOWN'}
    assert setup.journal.reserve_calls == 0


def test_different_payload_remains_a_conflict(setup):
    setup.journal.insert(setup.payload, setup.source.instrument)
    setup.payload['price'] = '11'
    assert setup.call()[0] == {'status': 'REQUEST_REJECTED', 'code': 'REQUEST_CONFLICT'}
    assert setup.journal.reserve_calls == 0


def test_unselected_binding_keeps_old_observation_path(setup):
    setup.journal._instrument_binding_enabled = False
    setup.journal.insert(setup.payload, None)
    setup.source.current = False
    assert setup.call()[0] == {'status': 'OK', 'state': 'SENT', 'ttl': 7}
    assert setup.journal.reserve_calls == 0
