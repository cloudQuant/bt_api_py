"""Risk control principal, contract argument and cancel-only operation on the control port (G64-02..04).

Synthetic Registry, owner, journal and provider callbacks; no CTP, account or network.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
from bt_api_base.direct_write_protocol import NativeOrderIdentity, PreDispatchRecord, WriteOperation
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import DirectCommandJournalError
from bt_api_gateway.direct_control import check_controls
from bt_api_gateway.direct_dispatch import NativeDispatchOutcome, _identity

from bt_api_py.gateway_direct_service import (
    CONTROL_MODES,
    GatewayControlGrant,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from tests.test_iter63_gateway_direct_owner import Harness
from tests.unit.test_iter63_gateway_cancel_owner import owner_for
from tests.unit.test_service_registry import _next_request, _register

RB = {'symbol': 'rb2701', 'exchange_id': 'SHFE', 'side': 'buy', 'offset': 'open', 'quantity': '1'}
HC = dict(RB, symbol='hc2701')
SCOPE = 'SHFE:rb2701'
OPERATIONS = frozenset({'direct.control.apply', 'direct.control.status', 'direct.control.list',
                        'direct.control.cancel_scope', 'direct.control.cancel_scope_status'})
OPERATOR_OPERATIONS = OPERATIONS | {'direct.control.resume'}
WRITES = frozenset({'direct.control.apply', 'direct.control.cancel_scope', 'direct.control.resume'})
ACCOUNT = WireScope('test-domain', account_ref='account')
STRATEGY = WireScope('test-domain', account_ref='account', strategy_ref='strategy-a')
CALLERS = {
    # The Risk service's control principal: account-wide, STOP_NEW and FREEZE, may name a contract.
    'risk-acct': (WireIdentity(WireRole.APPLICATION_CLIENT, 'risk-acct', 'risk-a'), ACCOUNT,
                  GatewayControlGrant('risk-a', True, frozenset({'strategy-a'}), OPERATIONS, kind='risk',
                                      modes=frozenset({'STOP_NEW', 'FREEZE'}), instrument_scope=True)),
    'risk-strategy': (WireIdentity(WireRole.APPLICATION_CLIENT, 'risk-strategy', 'risk-s'), STRATEGY,
                      GatewayControlGrant('risk-s', True, frozenset({'strategy-a'}), OPERATIONS, kind='risk',
                                          modes=frozenset({'STOP_NEW'}), instrument_scope=True)),
    # A person, as before: every mode, no contract argument.
    'ops-acct': (WireIdentity(WireRole.APPLICATION_CLIENT, 'ops-acct', 'ops-a'), ACCOUNT,
                 GatewayControlGrant('ops-a', True, frozenset(), OPERATOR_OPERATIONS)),
    'ops-strategy': (WireIdentity(WireRole.APPLICATION_CLIENT, 'ops-strategy', 'ops-s'), STRATEGY,
                     GatewayControlGrant('ops-s', False, frozenset({'strategy-a'}), OPERATIONS)),
}


def _origin(kind, principal, actor):
    return kind + ':' + hashlib.sha256(json.dumps([principal, actor], separators=(',', ':')).encode()).hexdigest()


@pytest.fixture
def port(tmp_path):
    h = Harness(tmp_path)
    calls = []

    def factory():
        bundle = h.factory()

        def native(target, symbol, commit_port, scope):
            prep = PreparedCancelRequest('provider-reservation', target, symbol, '20300102', 2, {'request_id': 12},
                                         (f'cancel-key-{len(calls)}',))
            marker = commit_port.commit(PreDispatchRecord(WriteOperation.CANCEL, scope, 'SYN', symbol, None, target,
                                                          {}, prep.trading_day, prep.session_generation,
                                                          'synthetic', prep))
            calls.append(symbol)
            return NativeDispatchOutcome(True, target, marker)
        cancel = GatewayDirectCancelDispatcher(GatewayDirectCancelJournal(bundle.journal), bundle.dispatcher,
                                               h.guard, 'SYN', native, lambda _: True)
        return replace(bundle, cancel_dispatcher=cancel)
    owner = owner_for(factory)
    assert owner.start(2)
    registry = None
    try:
        control = owner.submit_service_call(lambda: h.journal.enable_controls(h.guard)).result(timeout_seconds=2)
        binding = GatewayDirectServiceBinding(
            h.journal, {'strategy-p': GatewayDirectGrant('strategy-a', frozenset({'rb2701', 'hc2701'}))},
            owner=owner, cancel_enabled=True, control=control,
            control_grants={name: grant for name, (_, _, grant) in CALLERS.items()})
        capability = ServiceCapability('gateway-a', 'service.direct_write', OPERATOR_OPERATIONS, WRITES)
        registry = ServiceRegistry(
            ':memory:', service_id='gateway-service', capabilities=(capability,),
            grants={name: (PrincipalGrant(identity, (ResourceGrant(
                'gateway-a', 'service.direct_write', 'scope-a', scope, grant.operations),)),)
                for name, (identity, scope, grant) in CALLERS.items()},
            policy_version='policy', requirements_version='requirements', lease_seconds=10, clock=lambda: 100.0)
        for operation in OPERATOR_OPERATIONS:
            registry.register_handler('gateway-a', operation,
                                      lambda ctx, args, op=operation: binding.handle(op, context=ctx, arguments=args))
        records = {}
        for name, (identity, scope, grant) in CALLERS.items():
            registered = _register(registry, principal=name, identity=identity, scope=scope, instance_id='gateway-a',
                                   port='service.direct_write', scope_ref='scope-a',
                                   operations=tuple(grant.operations))
            assert registered['ok'], registered
            records[name] = registered['result']

        def call(name, operation, arguments):
            identity, scope, _ = CALLERS[name]
            return registry.handle(name, _next_request(operation, {
                'registration_id': records[name]['registration_id'],
                'service_epoch': records[name]['service_epoch'], 'instance_id': 'gateway-a',
                'arguments': arguments}, identity=identity, scope=scope))

        def on_owner(callback):
            return owner.submit_service_call(callback).result(timeout_seconds=2)

        def sent(key, payload, strategy='strategy-a'):
            row = h.journal.reserve(strategy, key, 'strategy-p', payload)
            native = _identity(NativeOrderIdentity('SYN', 'ref-' + key, {'trading_day': '20300101', 'front_id': 1},
                                                   1), 'SYN')
            began = h.journal.begin_dispatch(strategy, key, row.reservation_id, row.digest, 'strategy-p', 1, native,
                                             ('key-' + key,)).record
            h.journal.finish(strategy, key, row.reservation_id, row.digest, 'strategy-p', began.version, native,
                             ('key-' + key,), 'SENT')
            return row

        def upgrade():
            for step in ('enable_strategy_ledger', 'enable_account_extensions', 'enable_parent_authority',
                         'enable_account_facts_financial', 'enable_actor_stream', 'enable_strategy_origin',
                         'enable_control_cancel'):
                getattr(h.journal, step)(h.guard)
        yield SimpleNamespace(call=call, on_owner=on_owner, h=h, control=control, sent=sent,
                              upgrade=lambda: on_owner(upgrade), calls=calls)
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)


def _cause(port, cause_id):
    return port.on_owner(lambda: port.control.get(cause_id))


# -- G64-02/G64-03 ------------------------------------------------------------------------------

def test_risk_principal_applies_a_contract_stop_new_named_as_risk(port, caplog):
    caplog.set_level(logging.WARNING, logger='bt_api_py.gateway_direct_service')
    reply = port.call('risk-acct', 'direct.control.apply', {'cause_id': 'r04-rb', 'mode': 'STOP_NEW',
                                                            'instrument': SCOPE})
    assert reply['result'] == {'status': 'OBSERVED', 'cause_id': 'r04-rb', 'version': 1, 'active': True}, reply
    cause = _cause(port, 'r04-rb')
    assert (cause['scope_kind'], cause['scope_value'], cause['mode']) == ('instrument', SCOPE, 'STOP_NEW')
    assert cause['origin'] == _origin('risk', 'risk-acct', 'risk-a')
    line, = [record.getMessage() for record in caplog.records if 'control apply' in record.getMessage()]
    assert line.startswith('Risk control apply: cause r04-rb (STOP_NEW, scope instrument SHFE:rb2701) version 1 '
                           'by principal risk-acct actor risk-a registration ')
    with pytest.raises(DirectCommandJournalError, match='^CONTROL_STOP_NEW$'):
        port.h.journal.reserve('strategy-a', 'rb-open', 'strategy-p', RB)
    assert port.h.journal.reserve('strategy-a', 'hc-open', 'strategy-p', HC).status == 'RESERVED'
    # The same request is idempotent; the account-wide operator sees the contract cause.
    again = port.call('risk-acct', 'direct.control.apply', {'cause_id': 'r04-rb', 'mode': 'STOP_NEW',
                                                            'instrument': SCOPE})
    assert again['result'] == reply['result']
    listed = port.call('ops-acct', 'direct.control.list', {'after': '', 'limit': 10})['result']['causes']
    assert [(row['cause_id'], row['origin']) for row in listed] == [('r04-rb', cause['origin'])]


@pytest.mark.parametrize('name,arguments', [
    ('risk-acct', {'cause_id': 'r04-rb', 'mode': 'STOP_NEW', 'instrument': 'SHFE:hc2701'}),   # other contract
    ('risk-acct', {'cause_id': 'r04-rb', 'mode': 'FREEZE', 'instrument': SCOPE}),            # other mode
    ('risk-acct', {'cause_id': 'r04-rb', 'mode': 'STOP_NEW'}),                               # other scope
    ('ops-acct', {'cause_id': 'r04-rb', 'mode': 'STOP_NEW'}),                                # other origin
])
def test_the_same_cause_id_with_another_scope_mode_or_origin_is_never_a_second_cause(port, name, arguments):
    first = port.call('risk-acct', 'direct.control.apply', {'cause_id': 'r04-rb', 'mode': 'STOP_NEW',
                                                            'instrument': SCOPE})
    assert first['result']['status'] == 'OBSERVED'
    refused = port.call(name, 'direct.control.apply', arguments)
    assert refused['ok'] is False or refused['result'].get('status') != 'OBSERVED', refused
    cause = _cause(port, 'r04-rb')
    assert (cause['scope_kind'], cause['scope_value'], cause['mode'], cause['version']) == (
        'instrument', SCOPE, 'STOP_NEW', 1)

    def attempt():
        try:
            port.control.apply('r04-rb', arguments['mode'],
                               scope_kind='instrument' if 'instrument' in arguments else 'account',
                               scope_value=arguments.get('instrument', ''),
                               origin=_origin('risk' if name.startswith('risk') else 'operator', name,
                                              CALLERS[name][2].actor_ref))
        except DirectCommandJournalError as error:
            return error.code
    assert port.on_owner(attempt) == 'CONTROL_CAUSE_CONFLICT'


@pytest.mark.parametrize('name,arguments,code', [
    ('risk-acct', {'cause_id': 'c', 'mode': 'DRAIN', 'instrument': SCOPE}, 'SCOPE_DENIED'),   # mode not granted
    ('risk-acct', {'cause_id': 'c', 'mode': 'DRAIN'}, 'SCOPE_DENIED'),
    ('ops-acct', {'cause_id': 'c', 'mode': 'STOP_NEW', 'instrument': SCOPE}, 'SCOPE_DENIED'),  # no contract grant
    ('risk-strategy', {'cause_id': 'c', 'mode': 'STOP_NEW', 'instrument': SCOPE}, 'SCOPE_DENIED'),  # not account
    ('ops-strategy', {'cause_id': 'c', 'mode': 'STOP_NEW', 'instrument': SCOPE}, 'SCOPE_DENIED'),
    ('risk-acct', {'cause_id': 'c', 'mode': 'STOP_NEW', 'instrument': 'rb2701'}, 'INVALID_REQUEST'),
    ('risk-acct', {'cause_id': 'c', 'mode': 'STOP_NEW', 'instrument': 'SHFE:rb:2701'}, 'INVALID_REQUEST'),
    ('risk-acct', {'cause_id': 'c', 'mode': 'STOP_NEW', 'instrument': 7}, 'INVALID_REQUEST'),
    ('risk-acct', {'cause_id': 'c', 'mode': 'STOP_NEW', 'instrument': ''}, 'INVALID_REQUEST'),
    ('risk-acct', {'cause_id': 'c', 'mode': 'STOP_NEW', 'instrument': SCOPE, 'extra': 1}, 'INVALID_REQUEST'),
])
def test_contract_argument_and_modes_are_held_to_the_grant(port, name, arguments, code):
    assert port.call(name, 'direct.control.apply', arguments)['result'] == {
        'status': 'REQUEST_REJECTED', 'code': code}
    assert port.call('ops-acct', 'direct.control.list', {'after': '', 'limit': 10})['result']['causes'] == []


def test_human_operator_behaviour_is_unchanged(port, caplog):
    caplog.set_level(logging.WARNING, logger='bt_api_py.gateway_direct_service')
    for name, mode in (('ops-acct', 'DRAIN'), ('ops-strategy', 'FREEZE')):
        reply = port.call(name, 'direct.control.apply', {'cause_id': name, 'mode': mode})
        assert reply['result']['status'] == 'OBSERVED', reply
        cause = _cause(port, name)
        assert cause['origin'] == _origin('operator', name, CALLERS[name][2].actor_ref)
        assert cause['scope_kind'] == ('account' if name == 'ops-acct' else 'strategy')
    assert all(record.getMessage().startswith('Operator control apply') for record in caplog.records)
    # A strategy-scoped Risk principal applies within its strategy, as an operator does.
    reply = port.call('risk-strategy', 'direct.control.apply', {'cause_id': 'rs', 'mode': 'STOP_NEW'})
    assert reply['result']['status'] == 'OBSERVED' and _cause(port, 'rs')['scope_value'] == 'strategy-a'


@pytest.mark.parametrize('changes', [
    {'kind': 'risk', 'operations': frozenset({'direct.control.apply', 'direct.control.resume'})},   # D64-21
    {'kind': 'auditor'},
    {'modes': frozenset()},
    {'modes': frozenset({'STOP_NEW', 'RESUME'})},
    {'modes': ('STOP_NEW',)},
    {'instrument_scope': 'yes'},
    {'account_scope_allowed': False, 'strategies': frozenset({'s'}), 'instrument_scope': True},
])
def test_control_grants_that_overreach_are_refused(changes):
    values = {'actor_ref': 'a', 'account_scope_allowed': True, 'strategies': frozenset(),
              'operations': frozenset({'direct.control.apply'})}
    values.update(changes)
    with pytest.raises(ValueError, match='^INVALID_CONTROL_GRANT$'):
        GatewayControlGrant(**values)


def test_an_operator_grant_keeps_every_mode_by_default():
    grant = GatewayControlGrant('a', True, frozenset(), frozenset({'direct.control.apply'}))
    assert (grant.kind, grant.modes, grant.instrument_scope) == ('operator', CONTROL_MODES, False)


# -- G64-04 -------------------------------------------------------------------------------------

def test_cancel_scope_cancels_each_working_order_once_and_blocks_nothing(port, caplog):
    caplog.set_level(logging.WARNING, logger='bt_api_py.gateway_direct_service')
    rb1, rb2, hc = port.sent('rb-1', RB), port.sent('rb-2', RB), port.sent('hc-1', HC)
    port.upgrade()
    request = {'operation_id': 'kill-rb', 'limit': 1, 'instrument': SCOPE}
    first = port.call('risk-acct', 'direct.control.cancel_scope', request)
    assert first['ok'], first
    assert first['result'] == {'status': 'OBSERVED', 'operation_id': 'kill-rb', 'scope_kind': 'instrument',
                               'scope_value': SCOPE, 'issued': 1,
                               'counts': {'QUEUED': 1, 'SENT': 1, 'NOT_SENT': 0, 'UNKNOWN': 0, 'TERMINAL': 0}}
    assert port.calls == ['rb2701']
    # Nothing is blocked: the contract's new orders pass the control check, no block was stored.
    def admitted():
        with port.h.journal._transaction() as db:
            check_controls(db, 'account', 'strategy-a', RB)
            return db.execute('SELECT count(*) FROM direct_control_blocked').fetchone()[0]
    assert port.on_owner(admitted) == 0
    assert port.call('ops-acct', 'direct.control.list', {'after': '', 'limit': 10})['result']['causes'] == []
    # The same operation again continues with what is left; each order is cancelled once.
    second = port.call('risk-acct', 'direct.control.cancel_scope', dict(request, limit=5))
    assert second['result']['issued'] == 1 and second['result']['counts']['SENT'] == 2
    third = port.call('risk-acct', 'direct.control.cancel_scope', dict(request, limit=5))
    assert third['result']['issued'] == 0 and sorted(port.calls) == ['rb2701', 'rb2701']
    status = port.call('risk-acct', 'direct.control.cancel_scope_status',
                       {'operation_id': 'kill-rb', 'after': '', 'limit': 10})['result']
    assert (status['status'], status['scope_kind'], status['scope_value'], status['origin']) == (
        'OBSERVED', 'instrument', SCOPE, _origin('risk', 'risk-acct', 'risk-a'))
    assert sorted((item['reservation_id'], item['strategy'], item['state'], item['cancel'])
                  for item in status['targets']) == sorted(
        (row.reservation_id, 'strategy-a', 'SENT', 'SENT') for row in (rb1, rb2))
    assert hc.reservation_id not in {item['reservation_id'] for item in status['targets']}
    assert [line for line in (r.getMessage() for r in caplog.records) if 'cancel scope' in line][0].startswith(
        'Risk control cancel scope (1 cancel(s) issued): cause cancel-scope:kill-rb (CANCEL_ONLY, scope '
        'instrument SHFE:rb2701)')
    # The same operation id with another scope is never a second operation.
    conflict = port.call('risk-acct', 'direct.control.cancel_scope', {'operation_id': 'kill-rb', 'limit': 5})
    assert conflict['ok'] is False or conflict['result'].get('status') != 'OBSERVED'
    assert sorted(port.calls) == ['rb2701', 'rb2701']


def test_cancel_scope_status_of_an_unknown_operation_and_of_another_scope(port):
    port.sent('rb-1', RB)
    port.upgrade()
    assert port.call('risk-acct', 'direct.control.cancel_scope_status',
                     {'operation_id': 'absent', 'after': '', 'limit': 10})['result'] == {
        'status': 'NOT_FOUND', 'operation_id': 'absent'}
    reply = port.call('ops-strategy', 'direct.control.cancel_scope', {'operation_id': 'mine', 'limit': 1})
    assert reply['result']['scope_kind'] == 'strategy' and reply['result']['issued'] == 1
    # An account-wide caller does not read a strategy's operation as its own, nor the reverse.
    for name, operation in (('risk-acct', 'mine'),):
        refused = port.call(name, 'direct.control.cancel_scope_status',
                            {'operation_id': operation, 'after': '', 'limit': 10})
        assert refused['ok'] is False or refused['result'].get('status') != 'OBSERVED'


@pytest.mark.parametrize('arguments,code', [
    ({'operation_id': 'k', 'limit': 0}, 'INVALID_REQUEST'),
    ({'operation_id': 'k', 'limit': 257}, 'INVALID_REQUEST'),
    ({'operation_id': 'k'}, 'INVALID_REQUEST'),
    ({'operation_id': '', 'limit': 1}, 'INVALID_REQUEST'),
    ({'operation_id': 'k', 'limit': 1, 'instrument': 'nope'}, 'INVALID_REQUEST'),
])
def test_cancel_scope_requests_are_strict(port, arguments, code):
    port.upgrade()
    assert port.call('risk-acct', 'direct.control.cancel_scope', arguments)['result'] == {
        'status': 'REQUEST_REJECTED', 'code': code}


def test_cancel_scope_needs_the_cancel_proof_journal(port):
    assert port.call('risk-acct', 'direct.control.cancel_scope', {'operation_id': 'k', 'limit': 1})['result'] == {
        'status': 'REQUEST_REJECTED', 'code': 'CONTROL_CANCEL_PROOF_REQUIRED'}
    assert port.call('ops-acct', 'direct.control.cancel_scope', {'operation_id': 'k', 'limit': 1,
                                                                 'instrument': SCOPE})['result'] == {
        'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}


def test_cancel_scope_is_granted_by_its_own_operation_names(tmp_path):
    grant = GatewayControlGrant('a', True, frozenset(), frozenset({'direct.control.apply'}))
    assert 'direct.control.cancel_scope' not in grant.operations
    from bt_api_py.gateway_direct_service import CONTROL_OPERATIONS, CONTROL_WRITE_OPERATIONS
    from bt_api_py.service_registry import (
        _GATEWAY_CONTROL_READ_OPERATIONS,
        _GATEWAY_CONTROL_WRITE_OPERATIONS,
    )

    assert {'direct.control.cancel_scope', 'direct.control.cancel_scope_status'} <= CONTROL_OPERATIONS
    assert CONTROL_WRITE_OPERATIONS == _GATEWAY_CONTROL_WRITE_OPERATIONS
    assert CONTROL_OPERATIONS - CONTROL_WRITE_OPERATIONS == _GATEWAY_CONTROL_READ_OPERATIONS


def test_an_operator_releases_a_risk_contract_cause_while_orders_on_it_are_undecided(port):
    """Only the unresolved-return watcher's contract cause waits for undecided orders (D63-59)."""
    row = port.h.journal.reserve('strategy-a', 'going-out', 'strategy-p', RB)
    native = _identity(NativeOrderIdentity('SYN', 'ref-x', {'trading_day': '20300101', 'front_id': 1}, 1), 'SYN')
    port.h.journal.begin_dispatch('strategy-a', 'going-out', row.reservation_id, row.digest, 'strategy-p', 1,
                                  native, ('key-x',))
    assert port.call('risk-acct', 'direct.control.apply', {'cause_id': 'r04', 'mode': 'STOP_NEW',
                                                           'instrument': SCOPE})['result']['active'] is True
    released = port.call('ops-acct', 'direct.control.resume', {'cause_id': 'r04', 'expected_version': 1,
                                                               'operation_id': 'release-r04'})
    assert released['result'] == {'status': 'OBSERVED', 'cause_id': 'r04', 'version': 2, 'active': False}
    # A Risk principal holds no resume (D64-21), so it cannot release it itself.
    refused = port.call('risk-acct', 'direct.control.resume', {'cause_id': 'r04', 'expected_version': 2,
                                                               'operation_id': 'again'})
    assert refused['ok'] is False and refused['code'] == 'SCOPE_DENIED'
