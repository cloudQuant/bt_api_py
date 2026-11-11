"""A delegate acting beyond its grant is refused at the RPC boundary, not reported as UNKNOWN.

Synthetic Registry, owner and journal only; the dispatcher never reaches a provider.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.direct_write_protocol import NativeOrderIdentity
from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    GatewayDirectCommandJournal,
)
from bt_api_gateway.direct_delegation import GatewayDelegateGrant, authorize_delegated_command
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, _identity
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.gateway_delegate_actor_service import OPERATIONS as ACTOR_OPERATIONS
from bt_api_py.gateway_delegate_actor_service import GatewayDelegateActorService
from bt_api_py.gateway_delegation_service import GatewayDelegationService, GatewayDelegationSettings
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from tests.unit.test_service_registry import _next_request, _register

RB = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb2601', 'rb2601')
HC = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'hc2601', 'hc2601')
STRATEGY = WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-principal', 'strategy')
RISK = WireIdentity(WireRole.SERVICE, 'risk-principal', 'risk-actor')
OTHER = WireIdentity(WireRole.SERVICE, 'other-risk-principal', 'other-risk-actor')
SCOPE = WireScope('domain', account_ref='acct', strategy_ref='strategy', run_id='run')
TEMPLATE = WireScope('domain', account_ref='acct', strategy_ref='strategy')
DELEGATE_OPS = frozenset({'direct.delegated_reserve', 'direct.delegated_dispatch'}) | ACTOR_OPERATIONS
LOGICAL = frozenset({'direct.reserve', 'direct.dispatch', 'direct.command_status', 'direct.cancel',
                     'direct.cancel_status', 'direct.release'})


def _payload(key, symbol='rb2601', exchange='SHFE'):
    return OrderRequest(symbol=symbol, side=Side.BUY, order_type=OrderType.LIMIT, quantity=Decimal('1'),
        price=Decimal('1'), account_id='acct', client_order_id='client-' + key, idempotency_key=key,
        offset='open', quantity_unit='lots', exchange_id=exchange).to_dict()


def _command(identity, key, payload, instrument=RB, **changes):
    value = GatewayDelegatedCommand('acct', 'strategy', 'domain', identity.principal_ref, identity.actor_ref,
        'risk', 'run', key, gateway_payload_digest(payload), instrument, 'rev1')
    return replace(value, **changes)


@contextmanager
def _fence():
    yield


@pytest.fixture
def gateway(tmp_path):
    root = tmp_path / 'guards'
    root.mkdir()
    holder = {}

    def factory():
        guard = LocalAccountSessionGuard(root, 'acct', 'owner')
        journal = GatewayDirectCommandJournal.create(tmp_path / 'db', 'acct', generation=guard.acquire())
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        journal.enable_account_extensions(guard)
        dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, lambda *_: None, lambda _: ('key',),
                                             lambda _: True)
        cancel = GatewayDirectCancelDispatcher(GatewayDirectCancelJournal(journal), dispatcher, guard, 'CTP',
                                               lambda *_: None, lambda _: True)
        holder['journal'] = journal
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release, cancel_dispatcher=cancel)

    owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=1, busy_timeout_seconds=1)
    assert owner.start(2)
    journal = holder['journal']
    grants = tuple(GatewayDelegateGrant(identity, 'acct', 'strategy', 'domain', 'risk', 'rev1',
                                        frozenset({RB}), LOGICAL) for identity in (RISK, OTHER))
    settings = GatewayDelegationSettings(grants, lambda instrument: instrument == RB)
    delegation = GatewayDelegationService(settings, owner=owner, journal=journal,
                                          instance_id='gateway', scope_ref='scope')
    actor = GatewayDelegateActorService(delegation)
    strategy_ops = frozenset({'direct.reserve'})
    capability = ServiceCapability('gateway', 'service.direct_write', strategy_ops | DELEGATE_OPS,
        frozenset({'direct.reserve', 'direct.delegated_reserve', 'direct.delegated_dispatch',
                   'direct.delegate_actor_cancel', 'direct.delegate_actor_release'}))
    principals = {STRATEGY.principal_ref: (PrincipalGrant(STRATEGY, (ResourceGrant(
        'gateway', 'service.direct_write', 'scope', TEMPLATE, strategy_ops),)),)}
    for identity in (RISK, OTHER):
        principals[identity.principal_ref] = (PrincipalGrant(identity, (ResourceGrant(
            'gateway', 'service.direct_write', 'scope', TEMPLATE, DELEGATE_OPS),)),)
    registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(capability,),
        grants=principals, policy_version='p', requirements_version='r')
    delegation.configure(registry)
    registry.register_handler('gateway', 'direct.reserve', lambda *_: {'unused': True})
    for op in ('direct.delegated_reserve', 'direct.delegated_dispatch'):
        registry.register_handler('gateway', op, lambda ctx, args, op=op: delegation.handle(op, ctx, args))
    for op in ACTOR_OPERATIONS:
        registry.register_handler('gateway', op, lambda ctx, args, op=op: actor.handle(op, ctx, args))

    def register(identity, ops, scope=SCOPE, key=None):
        return _register(registry, principal=identity.principal_ref, identity=identity, scope=scope,
            key=key or identity.principal_ref, instance_id='gateway', port='service.direct_write',
            scope_ref='scope', operations=tuple(ops))

    assert register(STRATEGY, strategy_ops)['ok']
    sessions = {}
    for identity in (RISK, OTHER):
        result = register(identity, DELEGATE_OPS)
        assert result['ok'], result
        sessions[identity.principal_ref] = result['result']

    def call(identity, operation, arguments):
        rec = sessions[identity.principal_ref]
        response = registry.handle(identity.principal_ref, _next_request(operation, {
            'registration_id': rec['registration_id'], 'service_epoch': rec['service_epoch'],
            'instance_id': 'gateway', 'arguments': arguments}, identity=identity, scope=SCOPE))
        assert response['ok'], response
        return response['result']

    def sent(principal, key, *, delegated):
        """Seed one SENT order in the real journal; no native call is made."""
        payload = _payload(key)
        native = _identity(NativeOrderIdentity('CTP', 'ref-' + key, {'trading_day': '20300101'}, 1), 'CTP')
        if delegated:
            identity = {RISK.principal_ref: RISK, OTHER.principal_ref: OTHER}[principal]
            command = _command(identity, key, payload)
            grant = next(g for g in grants if g.identity == identity)
            context = SimpleNamespace(identity=identity, scope=SCOPE, principal_ref=principal,
                                      registration_id=sessions[principal]['registration_id'])

            def permit(operation):
                return authorize_delegated_command(context, grant, command, operation, payload,
                    registry_recheck=lambda _: True, current_grant=lambda: grant,
                    instrument_recheck=lambda instrument: instrument == RB)
            reserve = {'reservation_fence': _fence, 'delegation_permit': permit('direct.reserve')}
            begin = {'delegation_permit': permit('direct.dispatch'), 'delegation_fence': _fence}
        else:
            command, reserve, begin = None, {}, {}

        def seed():
            row = journal.reserve('strategy', key, principal, payload, **reserve)
            started = journal.begin_dispatch('strategy', key, row.reservation_id, row.digest, principal,
                row.version, native, ('anchor-' + key,), **begin).record
            journal.finish('strategy', key, row.reservation_id, row.digest, principal, started.version,
                           native, ('anchor-' + key,), 'SENT')
            return journal.get('strategy', key, principal)
        return owner.submit_service_call(seed).result(timeout_seconds=2), payload, command

    try:
        yield SimpleNamespace(owner=owner, journal=journal, call=call, register=register, sent=sent,
                              grant=grants[0])
    finally:
        registry.close()
        assert owner.close(2)


def _rows(gateway, strategy, key, principal):
    def read():
        try:
            return gateway.journal.get(strategy, key, principal)
        except DirectCommandJournalError as error:
            return error.code
    return gateway.owner.submit_service_call(read).result(timeout_seconds=2)


def test_delegate_for_an_undeclared_strategy_cannot_register(gateway):
    other = WireScope('domain', account_ref='acct', strategy_ref='strategy-b', run_id='run')
    refused = gateway.register(RISK, DELEGATE_OPS, scope=other, key='risk-other-strategy')
    assert not refused['ok'] and refused['code'] == 'SCOPE_DENIED', refused


@pytest.mark.parametrize('case', ['instrument_outside_grant', 'payload_names_other_instrument',
                                  'other_strategy', 'other_run', 'other_actor_kind', 'other_revision'])
def test_reserve_and_dispatch_beyond_the_grant_are_refused_and_nothing_is_written(gateway, case):
    payload = _payload('k-' + case)
    command = _command(RISK, 'k-' + case, payload)
    if case == 'instrument_outside_grant':
        payload = _payload('k-' + case, symbol='hc2601')
        command = _command(RISK, 'k-' + case, payload, HC)
    elif case == 'payload_names_other_instrument':
        payload = _payload('k-' + case, symbol='hc2601')
        command = _command(RISK, 'k-' + case, payload)
    elif case == 'other_strategy':
        command = replace(command, strategy_scope='strategy-b')
    elif case == 'other_run':
        command = replace(command, run_id='another-run')
    elif case == 'other_actor_kind':
        command = replace(command, actor_kind='execution', parent_ref='parent', child_id='child')
    else:
        command = replace(command, grant_revision='rev2')
    reserve = gateway.call(RISK, 'direct.delegated_reserve',
                           {'command': command.to_mapping(), 'payload': payload, 'ttl_seconds': 10})
    assert reserve == {'status': 'REQUEST_REJECTED', 'code': 'DELEGATION_DENIED'}, reserve
    dispatch = gateway.call(RISK, 'direct.delegated_dispatch', {'command': command.to_mapping(),
        'payload': payload, 'reservation_id': 'reservation', 'expected_version': 1})
    assert dispatch == {'status': 'REQUEST_REJECTED', 'code': 'DELEGATION_DENIED'}, dispatch
    for strategy in ('strategy', 'strategy-b'):
        assert _rows(gateway, strategy, 'k-' + case, RISK.principal_ref) == 'NOT_FOUND'


def test_whitelisted_reserve_is_still_admitted(gateway):
    payload = _payload('allowed')
    command = _command(RISK, 'allowed', payload)
    observed = gateway.call(RISK, 'direct.delegated_reserve',
                            {'command': command.to_mapping(), 'payload': payload, 'ttl_seconds': 10})
    assert observed['status'] == 'OBSERVED' and observed['strategy_scope'] == 'strategy', observed
    assert observed['actor_principal'] == RISK.principal_ref
    assert _rows(gateway, 'strategy', 'allowed', RISK.principal_ref).status == 'RESERVED'


@pytest.mark.parametrize('owner_of_target', ['strategy', 'other_delegate'])
def test_delegate_cannot_read_or_cancel_an_order_of_another_party(gateway, owner_of_target):
    principal = STRATEGY.principal_ref if owner_of_target == 'strategy' else OTHER.principal_ref
    row, payload, _ = gateway.sent(principal, 'target', delegated=owner_of_target != 'strategy')
    # The Risk delegate presents a well-formed envelope of its own for that key.
    claimed = _command(RISK, 'target', payload)
    for operation, extra in (('direct.delegate_actor_status', {}),
                             ('direct.delegate_actor_cancel', {'operation_id': 'cancel-1',
                                                               'reservation_id': row.reservation_id})):
        result = gateway.call(RISK, operation, {'command': claimed.to_mapping(), 'payload': payload, **extra})
        assert result == {'status': 'REQUEST_REJECTED', 'code': 'DELEGATION_DENIED'}, (operation, result)
    cancels = GatewayDirectCancelJournal(gateway.journal)

    def attempt():
        try:
            return cancels.get('strategy', 'cancel-1', RISK.principal_ref)
        except DirectCommandJournalError as error:
            return error.code
    assert gateway.owner.submit_service_call(attempt).result(timeout_seconds=2) == 'NOT_FOUND'
    assert _rows(gateway, 'strategy', 'target', principal).status == 'SENT'


def test_delegate_reads_its_own_order_and_a_missing_key_stays_unresolved(gateway):
    row, payload, command = gateway.sent(RISK.principal_ref, 'own', delegated=True)
    own = gateway.call(RISK, 'direct.delegate_actor_status', {'command': command.to_mapping(), 'payload': payload})
    assert own['status'] == 'OBSERVED' and own['state'] == 'SENT' and own['strategy_scope'] == 'strategy'
    # A key that does not exist proves nothing about an earlier lost reply.
    payload = _payload('absent')
    missing = gateway.call(RISK, 'direct.delegate_actor_status',
                           {'command': _command(RISK, 'absent', payload).to_mapping(), 'payload': payload})
    assert missing == {'status': 'UNKNOWN'}


@pytest.mark.parametrize('case', ['other_strategy', 'instrument_outside_grant', 'changed_payload'])
def test_actor_reads_and_cancels_only_its_exact_envelope(gateway, case):
    row, payload, command = gateway.sent(RISK.principal_ref, 'own', delegated=True)
    if case == 'other_strategy':
        command = replace(command, strategy_scope='strategy-b')
    elif case == 'instrument_outside_grant':
        payload = _payload('own', symbol='hc2601')
        command = _command(RISK, 'own', payload, HC)
    else:
        # Same key and actor, another order: the stored envelope does not match it.
        payload = dict(payload, quantity='2')
        command = _command(RISK, 'own', payload)
    for operation, extra in (('direct.delegate_actor_status', {}),
                             ('direct.delegate_actor_cancel', {'operation_id': 'cancel-own',
                                                               'reservation_id': row.reservation_id})):
        result = gateway.call(RISK, operation, {'command': command.to_mapping(), 'payload': payload, **extra})
        assert result == {'status': 'REQUEST_REJECTED', 'code': 'DELEGATION_DENIED'}, (operation, result)
    cancels = GatewayDirectCancelJournal(gateway.journal)

    def attempt():
        try:
            return cancels.get('strategy', 'cancel-own', RISK.principal_ref)
        except DirectCommandJournalError as error:
            return error.code
    assert gateway.owner.submit_service_call(attempt).result(timeout_seconds=2) == 'NOT_FOUND'


def test_static_check_names_each_refused_claim():
    from bt_api_gateway.direct_delegation import delegated_request_matches

    payload = _payload('k')
    grant = GatewayDelegateGrant(RISK, 'acct', 'strategy', 'domain', 'risk', 'rev1', frozenset({RB}),
                                 frozenset({'direct.reserve'}))
    context = SimpleNamespace(identity=RISK, scope=SCOPE, principal_ref=RISK.principal_ref,
                              registration_id='registration')
    command = _command(RISK, 'k', payload)
    assert delegated_request_matches(context, grant, command, 'direct.reserve', payload)
    assert not delegated_request_matches(context, grant, command, 'direct.dispatch', payload)
    assert not delegated_request_matches(context, grant, command, 'direct.unknown', payload)
    assert not delegated_request_matches(context, grant, command, 'direct.reserve', dict(payload, symbol='hc2601'))
    assert not delegated_request_matches(context, grant, command, 'direct.reserve',
                                         dict(payload, exchange_id='DCE'))
    assert not delegated_request_matches(context, grant, command, 'direct.reserve',
                                         dict(payload, idempotency_key='other'))
    assert not delegated_request_matches(context, grant, command, 'direct.reserve', list(payload))
    assert not delegated_request_matches(replace_context(context, principal_ref='other'), grant, command,
                                         'direct.reserve', payload)
    assert not delegated_request_matches(replace_context(context, registration_id=''), grant, command,
                                         'direct.reserve', payload)
    assert not delegated_request_matches(replace_context(context, scope=replace(SCOPE, account_ref='acct-b')),
                                         grant, command, 'direct.reserve', payload)
    assert not delegated_request_matches(context, replace(grant, instruments=frozenset({HC})), command,
                                         'direct.reserve', payload)
    assert not delegated_request_matches(context, grant, replace(command, actor_ref='other'), 'direct.reserve',
                                         payload)
    assert not delegated_request_matches(context, object(), command, 'direct.reserve', payload)


def replace_context(context, **changes):
    values = dict(vars(context))
    values.update(changes)
    return SimpleNamespace(**values)
