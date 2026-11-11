"""Account-wide reads of every strategy's funds and book for an account operator (G64-06 b).

One real SQLite owner and Registry with synthetic financial evidence, ledger and account cut;
no CTP, account or network.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from bt_api_base.direct_write_protocol import DirectExecutionEventKind, NativeOrderIdentity
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_gateway.strategy_position_ledger import (
    ExternalBaselinePosition,
    StrategyBaselineEvidence,
    StrategyInstrumentBinding,
)

from bt_api_py.gateway_account_strategy_reads import OPERATIONS, GatewayAccountStrategyReads
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import ACCOUNT_STRATEGY_READ_OPERATIONS
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from tests.unit.test_service_registry import _next_request, _register

RB = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb2601', 'rb2601')
HC = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'hc2601', 'hc2601')
BINDING = StrategyInstrumentBinding(RB, 'CTP', 'contracts', True)
DAY = '20300101'
ACCOUNT = WireScope('domain', account_ref='acct')
CALLERS = {
    'risk-acct': (WireIdentity(WireRole.APPLICATION_CLIENT, 'risk-acct', 'risk-a'), ACCOUNT),
    'other-app': (WireIdentity(WireRole.APPLICATION_CLIENT, 'other-app', 'other-a'), ACCOUNT),
    'ops-strategy': (WireIdentity(WireRole.APPLICATION_CLIENT, 'ops-strategy', 'ops-s'),
                     WireScope('domain', account_ref='acct', strategy_ref='s1')),
    'strategy-p': (WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-p', 's1'),
                   WireScope('domain', account_ref='acct', strategy_ref='s1', run_id='run')),
}


def _resolve(payload):
    if payload.get('symbol') != 'rb2601':
        raise ValueError('INSTRUMENT_UNRESOLVED')    # hc2601 is declared but has no close rule here
    return BINDING


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'bt_api/bt_api_gateway/tests'))
    from test_iter63_strategy_financial_ledger import sample

    (tmp_path / 'guards').mkdir()
    holder = {'financial_current': True, 'cut_current': True, 'cut_moves': False, 'cuts': 0}

    def read_account_cut(_db, _account, _instrument):
        # The kept account cut; while the account moves, every look at it names a new one.
        if holder['cut_moves']:
            holder['cuts'] += 1
        return BINDING, DAY, 'cut-7' if not holder['cut_moves'] else f"cut-{8 + holder['cuts']}", '6'

    @contextmanager
    def financial_fence(account, strategy, currency):
        yield lambda _db, snapshot: holder['financial_current'] and snapshot.strategy == strategy

    @contextmanager
    def comparison_fence():
        yield

    def factory():
        guard = LocalAccountSessionGuard(tmp_path / 'guards', 'acct', 'owner')
        journal = GatewayDirectCommandJournal.create(tmp_path / 'db', 'acct', generation=guard.acquire())
        for step in ('enable_controls', 'enable_strategy_ledger', 'enable_account_extensions',
                     'enable_parent_authority', 'enable_account_facts_financial'):
            getattr(journal, step)(guard)
        # Declared external holdings: 2 lots today and 3 yesterday long on rb2601.
        evidence = StrategyBaselineEvidence('acct', journal.journal_id, DAY, 'CTP', journal.generation, 1,
            'source', 'position-snapshot', 'orders-snapshot', 'cut', ('resource',), 100, 200, True, True, False, True,
            external_positions=(ExternalBaselinePosition(RB, 'long', '2', '3'),), operator_ref='operator',
            operation_id='operation')
        journal.initialize_external_strategy_ledger(evidence, guard=guard, baseline_fence=comparison_fence,
                                                    verify_authorized_snapshot=lambda actual: actual == evidence)
        journal.install_strategy_ledger_ports(guard=guard, resolve_binding=_resolve, current_trading_day=lambda: DAY,
            verify_day_transition=lambda *_: False, verify_current_continuity=lambda *_: True,
            verify_external_current=lambda *_: True)
        journal.install_account_comparison_ports(guard=guard, source_fence=comparison_fence,
            read_account_cut=read_account_cut,
            verify_account_cut=lambda *_: holder['cut_current'], verify_external_current=lambda *_: True,
            verify_external_cut_current=lambda *_: holder['cut_current'])
        journal.install_strategy_financial_port(guard=guard, source_fence=financial_fence)
        assert journal.append_strategy_financial(sample(account='acct', strategy='s1', journal_id=journal.journal_id,
                                                        owner_generation=journal.generation), guard=guard)
        # Strategy s1 opens one lot on rb2601.
        payload = {'symbol': 'rb2601', 'side': 'buy', 'offset': 'open', 'quantity': '1',
                   'quantity_unit': 'contracts', 'exchange_id': 'SHFE'}
        record = journal.reserve('s1', 'open', 'p-s1', payload)
        native = {'exchange_name': 'CTP', 'client_order_id': 'open', 'session_generation': 1, 'fields_json': '{}'}
        journal.begin_dispatch('s1', 'open', record.reservation_id, record.digest, 'p-s1', 1, native, ('anchor',))
        assert GatewayDirectExecutionLedger(journal, guard).ingest(GatewayReturnFact(
            'acct', 'CTP', RB, DAY, DirectExecutionEventKind.TRADE, 'source',
            identity=NativeOrderIdentity('CTP', 'ref', {'trading_day': DAY}, 1), identity_keys=('anchor',),
            binding_keys=('anchor',), venue_key='anchor', trade_key=('SHFE', 'trade'), side='buy', offset='open',
            trade_quantity='1', trade_price='10')).applied
        holder['journal'] = journal
        dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, lambda *_: None, lambda _: ('k',), lambda _: True)
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release)

    owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=2, busy_timeout_seconds=2)
    assert owner.start(5)
    registry = None
    try:
        reads = GatewayAccountStrategyReads(owner, holder['journal'], account_scope='acct',
            principals=frozenset({'risk-acct', 'ops-strategy'}), strategies=frozenset({'s1', 's2'}),
            instruments=frozenset({RB, HC}), resolve_binding=_resolve)
        capability = ServiceCapability('gateway', 'service.account_read', OPERATIONS, frozenset())
        registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(capability,),
            grants={name: (PrincipalGrant(identity, (ResourceGrant(
                'gateway', 'service.account_read', 'scope', replace(scope, run_id=None), OPERATIONS),)),)
                for name, (identity, scope) in CALLERS.items()},
            policy_version='p', requirements_version='r')
        for operation in OPERATIONS:
            registry.register_handler('gateway', operation,
                                      lambda ctx, args, op=operation: reads.handle(op, ctx, args))
        records = {}
        for name, (identity, scope) in CALLERS.items():
            registered = _register(registry, principal=name, identity=identity, scope=scope, key=name,
                                   instance_id='gateway', port='service.account_read', scope_ref='scope',
                                   operations=tuple(OPERATIONS))
            assert registered['ok'], registered
            records[name] = registered['result']

        def call(name, operation, arguments):
            identity, scope = CALLERS[name]
            return registry.handle(name, _next_request(operation, {
                'registration_id': records[name]['registration_id'],
                'service_epoch': records[name]['service_epoch'], 'instance_id': 'gateway',
                'arguments': arguments}, identity=identity, scope=scope))
        yield SimpleNamespace(call=call, holder=holder, reads=reads, journal=holder['journal'])
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)


FINANCIAL_ITEM = {'strategy', 'currency', 'status', 'model', 'revision', 'cut_ref', 'owner_generation',
                  'snapshot_digest', 'value', 'cash'}


def test_operations_are_the_published_ones():
    assert OPERATIONS == ACCOUNT_STRATEGY_READ_OPERATIONS == {'account.strategy_financial', 'account.strategy_book'}


def test_strategy_financial_of_every_strategy_with_the_strategy_reads_fields(site):
    reply = site.call('risk-acct', 'account.strategy_financial', {})
    assert reply['ok'], reply
    result = reply['result']
    assert (result['status'], result['schema_version'], result['account_scope'], result['complete']) == (
        'OBSERVED', 1, 'acct', False)
    assert result['owner_generation'] == site.journal.generation
    s1, s2 = result['strategies']
    assert set(s1) == FINANCIAL_ITEM and (s1['strategy'], s1['currency'], s1['status']) == ('s1', 'CNY', 'OBSERVED')
    assert (Decimal(s1['value']), Decimal(s1['cash'])) == (Decimal('104900'), Decimal('74900'))
    assert (s1['model'], s1['revision'], s1['owner_generation']) == (
        'futures_linear_v1', 1, site.journal.generation)
    # s2 has no projection in any currency: said so, never a zero.
    assert s2 == {'strategy': 's2', 'currency': None, 'status': 'UNAVAILABLE'}
    one = site.call('risk-acct', 'account.strategy_financial', {'strategy': 's1', 'currency': 'CNY'})['result']
    assert one['complete'] is True and one['strategies'] == [s1]
    # A projection whose source is not current is not served.
    site.holder['financial_current'] = False
    stale = site.call('risk-acct', 'account.strategy_financial', {'strategy': 's1', 'currency': 'CNY'})['result']
    assert stale['complete'] is False and stale['strategies'] == [
        {'strategy': 's1', 'currency': 'CNY', 'status': 'UNAVAILABLE'}]


def test_strategy_book_pages_every_strategy_and_the_external_book(site):
    first = site.call('risk-acct', 'account.strategy_book', {'limit': 3})['result']
    assert (first['status'], first['schema_version'], first['has_more'], first['complete']) == (
        'OBSERVED', 1, True, False)
    rest = site.call('risk-acct', 'account.strategy_book', {'limit': 10, 'after': first['next_after']})['result']
    assert rest['has_more'] is False and rest['next_after'] == rest['rows'][-1]['cursor']
    rows = first['rows'] + rest['rows']
    assert [row['cursor'] for row in rows] == sorted(row['cursor'] for row in rows) and len(rows) == 6
    by_key = {(row['book'], row.get('strategy'), row['instrument']['native_symbol']): row for row in rows}
    external = by_key[('external', None, 'rb2601')]
    assert external == {'cursor': json.dumps(['external', '', 'SHFE', 'rb2601'], separators=(',', ':')),
                        'book': 'external', 'instrument': RB.to_dict(), 'status': 'OBSERVED', 'net': '5',
                        'trading_day': DAY, 'cut': 'cut-7'}
    s1 = by_key[('strategy', 's1', 'rb2601')]
    assert (s1['status'], s1['long'], s1['short'], s1['net'], s1['long_today'], s1['trading_day']) == (
        'OBSERVED', '1', '0', '1', '1', DAY)
    assert s1['projected_buckets'] is False
    assert by_key[('strategy', 's2', 'rb2601')]['net'] == '0'
    # hc2601 is declared but the installed resolver does not bind it: nothing is known of it.
    for key in (('external', None, 'hc2601'), ('strategy', 's1', 'hc2601'), ('strategy', 's2', 'hc2601')):
        assert by_key[key]['status'] == 'UNAVAILABLE' and 'net' not in by_key[key]
    # An account cut that is not current leaves the external book unknown, the strategies' books read.
    site.holder['cut_current'] = False
    again = site.call('risk-acct', 'account.strategy_book', {'limit': 10})['result']
    assert [row['status'] for row in again['rows'] if row['book'] == 'external'] == ['UNAVAILABLE'] * 2
    assert {row['status'] for row in again['rows']
            if row['book'] == 'strategy' and row['instrument']['native_symbol'] == 'rb2601'} == {'OBSERVED'}


@pytest.mark.parametrize('name', ['other-app', 'ops-strategy'])
def test_only_an_account_wide_operator_named_for_the_reads_is_served(site, name):
    for operation, arguments in (('account.strategy_financial', {}), ('account.strategy_book', {'limit': 1})):
        assert site.call(name, operation, arguments)['result'] == {'status': 'REQUEST_REJECTED',
                                                                   'code': 'SCOPE_DENIED'}


def test_a_strategy_client_is_refused_by_the_registry_and_by_the_handler(site):
    # The Registry admits these reads for application clients only.
    for operation, arguments in (('account.strategy_financial', {}), ('account.strategy_book', {'limit': 1})):
        refused = site.call('strategy-p', operation, arguments)
        assert (refused['ok'], refused['code']) == (False, 'DENIED'), refused
    identity, scope = CALLERS['strategy-p']
    context = SimpleNamespace(identity=identity, scope=scope, principal_ref='strategy-p')
    assert site.reads.handle('account.strategy_book', context, {'limit': 1}) == {
        'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}


@pytest.mark.parametrize('operation,arguments,code', [
    ('account.strategy_financial', {'strategy': 's9'}, 'SCOPE_DENIED'),
    ('account.strategy_financial', {'strategy': 's1', 'cut': 'mine'}, 'INVALID_REQUEST'),
    ('account.strategy_financial', {'currency': ''}, 'INVALID_REQUEST'),
    ('account.strategy_book', {}, 'INVALID_REQUEST'),
    ('account.strategy_book', {'limit': 0}, 'INVALID_REQUEST'),
    ('account.strategy_book', {'limit': 257}, 'INVALID_REQUEST'),
    ('account.strategy_book', {'limit': 1, 'after': 7}, 'INVALID_REQUEST'),
    ('account.strategy_book', {'limit': 1, 'account_net': '0'}, 'INVALID_REQUEST'),
])
def test_requests_are_strict_and_never_carry_facts(site, operation, arguments, code):
    assert site.call('risk-acct', operation, arguments)['result'] == {'status': 'REQUEST_REJECTED', 'code': code}


def test_reads_without_the_needed_journal_features_are_unavailable(site, monkeypatch):
    monkeypatch.setattr(site.journal, '_strategy_financial_port', None)
    assert site.call('risk-acct', 'account.strategy_financial', {})['result'] == {
        'status': 'REQUEST_REJECTED', 'code': 'STRATEGY_FINANCIAL_UNAVAILABLE'}
    monkeypatch.setattr(site.reads, '_resolve_binding', None)
    assert site.call('risk-acct', 'account.strategy_book', {'limit': 1})['result'] == {
        'status': 'REQUEST_REJECTED', 'code': 'STRATEGY_LEDGER_UNAVAILABLE'}


def test_reads_bind_to_one_owner_and_declared_names():
    with pytest.raises(ValueError, match='^INVALID_ACCOUNT_STRATEGY_READS$'):
        GatewayAccountStrategyReads(object(), object(), account_scope='acct', principals=frozenset(),
                                    strategies=frozenset({'s1'}), instruments=frozenset({RB}))


def _ctp_profile(tmp_path, monkeypatch, *, reader_grant, read_ops, extra=None, name='reads'):
    """A CTP trading profile with an operator ('operator') and optionally one more account reader."""
    from dataclasses import replace as _replace

    import zmq

    from bt_api_py.ctp_gateway_trading import build_ctp_gateway_trading_profile
    from bt_api_py.service_config import parse_service_config
    from tests.test_iter63_ctp_gateway_trading import CLIENT_A, _read_profile

    _, settings, config, authority, grants, read = _read_profile(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    options = raw['components']['gateway']['settings']['options']
    options.update(control_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True)
    config = parse_service_config(raw)
    domain, account = authority.scope.execution_domain_ref, settings.account_scope
    operator = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'actor')
    direct_scope = WireScope(domain, account_ref=account,
                             strategy_ref=None if reader_grant.account_scope_allowed else CLIENT_A.actor_ref)
    resources = (ResourceGrant('gateway-a', 'service.direct_write', config.scope_ref, direct_scope,
                               frozenset({'direct.control.status'})),
                 ResourceGrant('gateway-a', 'service.account_read', config.scope_ref,
                               WireScope(domain, account_ref=account), read_ops))
    public, _ = zmq.curve_keypair()
    principals = {**authority.principals, public: 'operator'}
    granted = {**authority.grants, 'operator': (PrincipalGrant(operator, resources),)}
    readers = {CLIENT_A.principal_ref, 'operator'}
    if extra == 'reader_without_control_grant':
        reader = WireIdentity(WireRole.APPLICATION_CLIENT, 'reader', 'reader-actor')
        public, _ = zmq.curve_keypair()
        principals[public] = 'reader'
        granted['reader'] = (PrincipalGrant(reader, (ResourceGrant(
            'gateway-a', 'service.account_read', config.scope_ref, WireScope(domain, account_ref=account),
            frozenset({'account.strategy_book'})),)),)
        readers.add('reader')
    elif extra == 'strategy_client':
        entry, = granted[CLIENT_A.principal_ref]
        own = ResourceGrant('gateway-a', 'service.account_read', config.scope_ref,
                            WireScope(domain, account_ref=account, strategy_ref=CLIENT_A.actor_ref),
                            frozenset({'account.strategy_book'}))
        granted[CLIENT_A.principal_ref] = (PrincipalGrant(entry.identity, tuple(
            item for item in entry.resources if item.port != 'service.account_read') + (own,)),)
    authority = _replace(authority, principals=principals, grants=granted)
    profile = build_ctp_gateway_trading_profile(config, authority, tmp_path / (name + '.sqlite'),
        profile_ref='account-' + name, settings=settings, grants=grants, request_feed_factory=lambda: None,
        market_stream_factory=lambda *_: None, assert_admitted=lambda _: True, account_read_settings=read,
        account_read_principals=frozenset(readers), control_grants={'operator': reader_grant},
        upgrade_controls_from_v9=True, upgrade_strategy_ledger_from_v10=True,
        upgrade_account_extensions_from_v11=True)
    return profile, options, authority, read, CLIENT_A


def test_ctp_profile_installs_the_reads_for_an_account_wide_control_principal(tmp_path, monkeypatch):
    from bt_api_py.gateway_direct_service import GatewayControlGrant
    from bt_api_py.service_registry import ServiceCallContext

    grant = GatewayControlGrant('actor', True, frozenset(), frozenset({'direct.control.status'}), kind='risk',
                                modes=frozenset({'STOP_NEW'}))
    profile, options, authority, read, client = _ctp_profile(tmp_path, monkeypatch, reader_grant=grant,
                                                             read_ops=ACCOUNT_STRATEGY_READ_OPERATIONS)
    resources = profile.deployment_factory()._provide_resources('gateway', 'gateway-a', options, authority.scope)
    try:
        reads = resources.values['account_strategy_reads']
        assert type(reads) is GatewayAccountStrategyReads
        assert reads.principals == frozenset({'operator'})
        assert reads.strategies == frozenset({client.actor_ref})
        assert reads.instruments == frozenset(read.observation_instruments)
        operator = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'actor')
        scope = WireScope(authority.scope.execution_domain_ref, account_ref=reads.account_scope)
        # Without the Registry's admission nothing is read.
        assert reads.handle('account.strategy_book', ServiceCallContext(operator, scope, 'registration', 'operator'),
                            {'limit': 1}) == {'status': 'REQUEST_REJECTED', 'code': 'ADMISSION_DENIED'}
    finally:
        resources.close()


@pytest.mark.parametrize('case', ['strategy_scoped_operator', 'reader_without_control_grant', 'strategy_client'])
def test_ctp_profile_refuses_the_reads_for_anyone_but_an_account_wide_control_principal(tmp_path, monkeypatch,
                                                                                        case):
    from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError
    from bt_api_py.gateway_direct_service import GatewayControlGrant
    from tests.test_iter63_ctp_gateway_trading import CLIENT_A

    account_wide = case != 'strategy_scoped_operator'
    grant = GatewayControlGrant('actor', account_wide, frozenset() if account_wide else frozenset({CLIENT_A.actor_ref}),
                                frozenset({'direct.control.status'}))
    read_ops = frozenset({'account.strategy_book'}) if case == 'strategy_scoped_operator' else frozenset(
        {'account.strategy_net_comparison'})
    # The same profile composes with the same principals when nobody but the operator holds the reads.
    if case != 'strategy_scoped_operator':
        (tmp_path / 'ok').mkdir()
        _ctp_profile(tmp_path / 'ok', monkeypatch, reader_grant=grant, read_ops=read_ops)
    with pytest.raises(CtpGatewayTradingError, match='^INVALID_TRADING_CONFIGURATION$'):
        _ctp_profile(tmp_path, monkeypatch, reader_grant=grant, read_ops=read_ops,
                     extra=None if case == 'strategy_scoped_operator' else case)


def test_a_named_principal_in_another_role_or_scope_is_refused_by_the_handler(site):
    from bt_api_py.service_registry import ServiceCallContext

    cases = (
        (WireIdentity(WireRole.STRATEGY_CLIENT, 'risk-acct', 'risk-a'), ACCOUNT),
        (WireIdentity(WireRole.SERVICE, 'risk-acct', 'risk-a'), ACCOUNT),
        (WireIdentity(WireRole.APPLICATION_CLIENT, 'risk-acct', 'risk-a', 'delegate'), ACCOUNT),
        (WireIdentity(WireRole.APPLICATION_CLIENT, 'risk-acct', 'risk-a'), WireScope('domain', account_ref='other')),
    )
    for identity, scope in cases:
        context = ServiceCallContext(identity, scope, 'registration', 'risk-acct')
        assert site.reads.handle('account.strategy_financial', context, {}) == {
            'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}, (identity, scope)


def test_the_external_book_is_unknown_while_the_account_cut_moves(site):
    site.holder['cut_moves'] = True
    rows = site.call('risk-acct', 'account.strategy_book', {'limit': 10})['result']['rows']
    assert [row['status'] for row in rows if row['book'] == 'external'] == ['UNAVAILABLE'] * 2
