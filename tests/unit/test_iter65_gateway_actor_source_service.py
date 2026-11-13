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
from bt_api_py.gateway_delegate_actor_service import OPERATIONS, V2_OPERATIONS
ACTOR_OPERATIONS = OPERATIONS | V2_OPERATIONS
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
DELEGATE_OPS = frozenset({'direct.delegated_reserve', 'direct.delegated_dispatch', 'direct.parent.query'}) | ACTOR_OPERATIONS
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
        assert dispatcher.is_bound_to(journal, guard, 'CTP')
        journal.install_actor_source_guard(guard=guard)
        holder['guard'] = guard
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
    actor = GatewayDelegateActorService(delegation, actor_source_guard=holder['guard'], actor_source_route='CTP')
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
    from bt_api_py.gateway_parent_service import GatewayParentGrant
    registry.configure_gateway_parents(instance_id='gateway', scope_ref='scope', grants=tuple(
        GatewayParentGrant(identity, 'acct', 'strategy', 'domain', 'execution', 'rev1', frozenset({RB}),
                           frozenset({'direct.parent.query'})) for identity in (RISK, OTHER)))
    registry.register_handler('gateway', 'direct.parent.query', lambda *_: {'unused': True})
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
        return response['result'] if response['ok'] else {'status': 'REQUEST_REJECTED', 'code': response['code']}

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
                              grant=grants[0], registry=registry, actor=actor, delegation=delegation, guard=holder['guard'])
    finally:
        registry.close()
        assert owner.close(2)



def source(gateway):
    def read():
        with gateway.journal._transaction() as db:
            revision = gateway.journal._assert_anchor(db)
        return {'account_scope': 'acct', 'journal_id': gateway.journal.journal_id,
                'gateway_generation': gateway.journal.generation, 'minimum_revision': revision}
    return gateway.owner.submit_service_call(read).result(timeout_seconds=2)


def v2_args(gateway, row, payload, command):
    return {'command': command.to_mapping(), 'payload': payload, 'expected_source': source(gateway),
            'reservation_id': row.reservation_id, 'minimum_version': row.version}


def test_status_v2_real_registry_and_owner_transaction(gateway, monkeypatch):
    row, payload, command = gateway.sent(RISK.principal_ref, 'v2-own', delegated=True)
    args = v2_args(gateway, row, payload, command)
    calls = []
    submit = gateway.owner.submit_service_call
    def counted(callback, **kwargs):
        calls.append(callback)
        return submit(callback, **kwargs)
    monkeypatch.setattr(gateway.owner, 'submit_service_call', counted)
    result = gateway.call(RISK, 'direct.delegate_actor_status_v2', args)
    assert len(calls) == 1, result
    assert result['status'] == 'OBSERVED' and result['schema_version'] == 2
    assert result['state'] == 'SENT' and result['reservation_id'] == row.reservation_id
    assert result['source'] == {
        'account_scope': 'acct', 'journal_id': gateway.journal.journal_id,
        'gateway_generation': gateway.journal.generation,
        'journal_revision': args['expected_source']['minimum_revision']}
    v1 = gateway.call(RISK, 'direct.delegate_actor_status', {'command': command.to_mapping(), 'payload': payload})
    assert v1['status'] == 'OBSERVED' and v1['schema_version'] == 1 and 'source' not in v1


@pytest.mark.parametrize('field,value', [('account_scope', 'wrong'), ('journal_id', 'wrong'),
    ('gateway_generation', 'wrong'), ('minimum_revision', 10**9)])
def test_rejects_wrong_source(gateway, field, value):
    row, payload, command = gateway.sent(RISK.principal_ref, 'bad-' + field, delegated=True)
    args = v2_args(gateway, row, payload, command)
    args['expected_source'][field] = value
    assert gateway.call(RISK, 'direct.delegate_actor_status_v2', args)['status'] != 'OBSERVED'


@pytest.mark.parametrize('change', ['actor', 'run', 'payload', 'reservation', 'version', 'malformed_source', 'guard_argument'])
def test_rejects_changed_identity_or_request(gateway, change):
    row, payload, command = gateway.sent(RISK.principal_ref, 'id-' + change, delegated=True)
    args = v2_args(gateway, row, payload, command)
    if change == 'actor':
        args['command'] = replace(command, actor_ref='other').to_mapping()
    elif change == 'run':
        args['command'] = replace(command, run_id='other').to_mapping()
    elif change == 'payload':
        args['payload'] = dict(payload, quantity='2')
    elif change == 'reservation':
        args['reservation_id'] = 'other'
    elif change == 'version':
        args['minimum_version'] = row.version + 1
    elif change == 'malformed_source':
        args['expected_source']['minimum_revision'] = True
    else:
        args['guard'] = 'caller-supplied'
    assert gateway.call(RISK, 'direct.delegate_actor_status_v2', args)['status'] != 'OBSERVED'


def test_release_status_v2_requires_matching_operation(gateway):
    payload = _payload('release')
    command = _command(RISK, 'release', payload)
    created = gateway.call(RISK, 'direct.delegated_reserve',
        {'command': command.to_mapping(), 'payload': payload, 'ttl_seconds': 10})
    assert created['status'] == 'OBSERVED'
    release = gateway.call(RISK, 'direct.delegate_actor_release', {'command': command.to_mapping(),
        'payload': payload, 'operation_id': 'release-1', 'reservation_id': created['reservation_id'],
        'expected_version': created['version']})
    assert release['state'] == 'RELEASED'
    args = {'command': command.to_mapping(), 'payload': payload, 'expected_source': source(gateway),
        'reservation_id': created['reservation_id'], 'minimum_version': release['version'],
        'operation_id': 'release-1', 'expected_version': created['version']}
    result = gateway.call(RISK, 'direct.delegate_actor_release_status_v2', args)
    assert result['status'] == 'OBSERVED' and result['release_operation_id'] == 'release-1', result
    assert result['release_expected_version'] == created['version']
    for key, value in [('operation_id', 'other'), ('expected_version', created['version'] + 1)]:
        assert gateway.call(RISK, 'direct.delegate_actor_release_status_v2', dict(args, **{key: value}))['status'] != 'OBSERVED'


def test_revoked_grant_and_transaction_failure_fail_closed(gateway, monkeypatch):
    row, payload, command = gateway.sent(RISK.principal_ref, 'revoked', delegated=True)
    args = v2_args(gateway, row, payload, command)
    key = ('gateway', 'scope', RISK.principal_ref, 'acct', 'strategy')
    grant = gateway.registry._gateway_delegate_grants.pop(key)
    assert gateway.call(RISK, 'direct.delegate_actor_status_v2', args)['status'] != 'OBSERVED'
    gateway.registry._gateway_delegate_grants[key] = grant
    def fail(*_):
        raise RuntimeError('injected transaction failure')
    monkeypatch.setattr(gateway.journal, '_actor_source_on', fail)
    assert gateway.call(RISK, 'direct.delegate_actor_status_v2', args) == {'status': 'UNKNOWN'}


def test_unbound_owner_does_not_enable_v2(gateway):
    actor = GatewayDelegateActorService(gateway.delegation)
    assert not actor.supports_actor_source_v2
    with pytest.raises(Exception):
        GatewayDelegateActorService(gateway.delegation, actor_source_guard=object(), actor_source_route='CTP')
    with pytest.raises(Exception):
        GatewayDelegateActorService(gateway.delegation, actor_source_guard=gateway.guard, actor_source_route='OTHER')


def test_parent_release_status_requires_current_query_grant(gateway):
    from bt_api_py.gateway_parent_service import GatewayParentGrant

    delegate = replace(gateway.grant, actor_kind='execution')
    delegate_key = ('gateway', 'scope', RISK.principal_ref, 'acct', 'strategy')
    gateway.registry._gateway_delegate_grants[delegate_key] = delegate
    gateway.delegation.settings = GatewayDelegationSettings((delegate,), lambda key: key == RB,
                                                              parent_recheck=lambda *_: True)
    parent = GatewayParentGrant(RISK, 'acct', 'strategy', 'domain', 'execution', 'rev1',
                                frozenset({RB}), frozenset({'direct.parent.query'}))
    gateway.registry._gateway_parent_grants[delegate_key] = parent
    def resolve(context, operation):
        return gateway.registry.gateway_parent_grant(instance_id='gateway', scope_ref='scope',
            identity=context.identity, scope=context.scope, operation=operation)
    gateway.actor._parent_grant_resolver = resolve
    payload = _payload('parent')
    command = replace(_command(RISK, 'parent', payload), actor_kind='execution', parent_ref='parent-ref', child_id='child')
    context = SimpleNamespace(identity=RISK, scope=SCOPE, principal_ref=RISK.principal_ref, registration_id='seed')
    def permit(operation):
        return authorize_delegated_command(context, delegate, command, operation, payload,
            registry_recheck=lambda _: True, current_grant=lambda: delegate,
            instrument_recheck=lambda key: key == RB, parent_recheck=lambda *_: True)
    def seed():
        row = gateway.journal.reserve('strategy', 'parent', RISK.principal_ref, payload,
            reservation_fence=_fence, delegation_permit=permit('direct.reserve'))
        return gateway.journal.release('strategy', 'parent', row.reservation_id, row.digest, row.principal,
            'parent-release', row.version, delegation_permit=permit('direct.release'), delegation_fence=_fence,
            release_target_recheck=lambda *_: True), row.version
    row, release_version = gateway.owner.submit_service_call(seed).result(timeout_seconds=2)
    args = v2_args(gateway, row, payload, command)
    args.update(operation_id='parent-release', expected_version=release_version)
    assert gateway.call(RISK, 'direct.delegate_actor_release_status_v2', args)['status'] == 'OBSERVED'
    gateway.registry._gateway_parent_grants.pop(delegate_key)
    assert gateway.call(RISK, 'direct.delegate_actor_release_status_v2', args)['status'] != 'OBSERVED'
    gateway.registry._gateway_parent_grants[delegate_key] = replace(parent, operations=frozenset({'direct.parent.cancel'}))
    assert gateway.call(RISK, 'direct.delegate_actor_release_status_v2', args)['status'] != 'OBSERVED'


def test_grant_revocation_inside_transaction_fails_closed(gateway, monkeypatch):
    row, payload, command = gateway.sent(RISK.principal_ref, 'mid-revoke', delegated=True)
    args = v2_args(gateway, row, payload, command)
    original = gateway.journal._actor_source_on
    key = ('gateway', 'scope', RISK.principal_ref, 'acct', 'strategy')
    def revoke(db, expected):
        result = original(db, expected)
        gateway.registry._gateway_delegate_grants.pop(key, None)
        return result
    monkeypatch.setattr(gateway.journal, '_actor_source_on', revoke)
    assert gateway.call(RISK, 'direct.delegate_actor_status_v2', args)['status'] != 'OBSERVED'


def test_unbound_service_rejects_valid_v2_admission(gateway):
    row, payload, command = gateway.sent(RISK.principal_ref, 'unbound', delegated=True)
    args = v2_args(gateway, row, payload, command)
    gateway.actor._actor_source_guard = None
    assert gateway.call(RISK, 'direct.delegate_actor_status_v2', args) == {
        'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}


def test_guard_lost_during_transaction_fails_closed(gateway, monkeypatch):
    row, payload, command = gateway.sent(RISK.principal_ref, 'lost-guard', delegated=True)
    args = v2_args(gateway, row, payload, command)
    original = gateway.journal._actor_source_on
    def lose_guard(db, expected):
        result = original(db, expected)
        gateway.guard.release()
        return result
    monkeypatch.setattr(gateway.journal, '_actor_source_on', lose_guard)
    assert gateway.call(RISK, 'direct.delegate_actor_status_v2', args) == {'status': 'UNKNOWN'}
