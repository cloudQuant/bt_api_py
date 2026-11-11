"""A delegated reserve is admitted or refused exactly like the represented strategy's own (G64-07, G64-09).

Synthetic Registry, owner and journal with a strategy ledger; no provider, account or network.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.direct_write_protocol import DirectExecutionEventKind, NativeOrderIdentity
from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    GatewayDirectCommandJournal,
)
from bt_api_gateway.direct_control import GatewayDirectControl
from bt_api_gateway.direct_delegation import GatewayDelegateGrant
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_gateway.strategy_position_ledger import (
    StrategyBaselineEvidence,
    StrategyInstrumentBinding,
)

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.gateway_delegation_service import GatewayDelegationService, GatewayDelegationSettings
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayReservationAdmissionDenied
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from tests.unit.test_service_registry import _next_request, _register

RB = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb2601', 'rb2601')
BINDING = StrategyInstrumentBinding(RB, 'CTP', 'lots', True)
DAY = '20300101'
STRATEGY = 'strategy'
OWN = 'strategy-principal'
OWN_IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, OWN, STRATEGY)
RISK =WireIdentity(WireRole.SERVICE, 'risk-principal', 'risk-actor')
SCOPE = WireScope('domain', account_ref='acct', strategy_ref=STRATEGY, run_id='run')
TEMPLATE = WireScope('domain', account_ref='acct', strategy_ref=STRATEGY)
DELEGATE_OPS = frozenset({'direct.delegated_reserve', 'direct.delegated_dispatch'})


def _order(key, *, side=Side.SELL, offset='close_today', quantity='1'):
    return OrderRequest(symbol='rb2601', side=side, order_type=OrderType.LIMIT, quantity=Decimal(quantity),
        price=Decimal('1'), account_id='acct', client_order_id='client-' + key, idempotency_key=key,
        offset=offset, quantity_unit='lots', exchange_id='SHFE')


def _command(key, payload):
    return GatewayDelegatedCommand('acct', STRATEGY, 'domain', RISK.principal_ref, RISK.actor_ref, 'risk', 'run',
                                   key, gateway_payload_digest(payload), RB, 'rev1')


def _build(tmp_path, *, fence=None):
    root = tmp_path / 'guards'
    root.mkdir()
    holder = {'continuous': True}

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
        holder.update(journal=journal, guard=guard)
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release, cancel_dispatcher=cancel)

    owner = GatewayDirectOwner(factory, queue_size=8, max_queue_wait_seconds=2, busy_timeout_seconds=2)
    assert owner.start(2)
    try:
        return _compose(owner, holder, fence)
    except BaseException:
        owner.close(2)
        raise


def _compose(owner, holder, fence):
    journal, guard = holder['journal'], holder['guard']

    def prepare():
        # Empty baseline, ledger ports, then the strategy's own 1-lot long fill on rb2601.
        evidence = StrategyBaselineEvidence('acct', journal.journal_id, DAY, 'CTP', journal.generation, 1,
            'source', 'position-snapshot', 'orders-snapshot', 'cut', ('resource',), 100, 200,
            True, True, True, True)

        @contextmanager
        def baseline_fence():
            yield
        journal.initialize_empty_strategy_ledger(evidence, guard=guard, baseline_fence=baseline_fence,
                                                 verify_current_empty=lambda actual: actual == evidence)
        journal.install_strategy_ledger_ports(
            guard=guard, resolve_binding=lambda _: BINDING, current_trading_day=lambda: DAY,
            verify_day_transition=lambda *_: False,
            verify_current_continuity=lambda *_: holder['continuous'])
        payload = {'symbol': 'rb2601', 'side': 'buy', 'offset': 'open', 'quantity': '1',
                   'quantity_unit': 'lots', 'exchange_id': 'SHFE'}
        record = journal.reserve(STRATEGY, 'open', OWN, payload)
        native = {'exchange_name': 'CTP', 'client_order_id': 'open', 'session_generation': 1,
                  'fields_json': '{}'}
        journal.begin_dispatch(STRATEGY, 'open', record.reservation_id, record.digest, OWN, 1, native,
                               ('anchor',))
        fact = GatewayReturnFact('acct', 'CTP', RB, DAY, DirectExecutionEventKind.TRADE, 'source',
            identity=NativeOrderIdentity('CTP', 'ref', {'trading_day': DAY}, 1), identity_keys=('anchor',),
            binding_keys=('anchor',), venue_key='anchor', trade_key=('SHFE', 'trade'), side='buy',
            offset='open', trade_quantity='1', trade_price='10')
        assert GatewayDirectExecutionLedger(journal, guard).ingest(fact).applied
        return GatewayDirectControl(journal, guard)

    control = owner.submit_service_call(prepare).result(timeout_seconds=2)
    grant = GatewayDelegateGrant(RISK, 'acct', STRATEGY, 'domain', 'risk', 'rev1', frozenset({RB}),
                                 frozenset({'direct.reserve', 'direct.dispatch'}))
    settings = GatewayDelegationSettings((grant,), lambda instrument: instrument == RB)
    extra = {} if fence is None else {'reservation_fence': fence}
    delegation = GatewayDelegationService(settings, owner=owner, journal=journal, instance_id='gateway',
                                          scope_ref='scope', **extra)
    own_ops = frozenset({'direct.reserve'})
    capability = ServiceCapability('gateway', 'service.direct_write', DELEGATE_OPS | own_ops,
                                   DELEGATE_OPS | own_ops)
    registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(capability,),
        grants={RISK.principal_ref: (PrincipalGrant(RISK, (ResourceGrant(
                    'gateway', 'service.direct_write', 'scope', TEMPLATE, DELEGATE_OPS),)),),
                OWN: (PrincipalGrant(OWN_IDENTITY, (ResourceGrant(
                    'gateway', 'service.direct_write', 'scope', TEMPLATE, own_ops),)),)},
        policy_version='p', requirements_version='r')
    delegation.configure(registry)
    registry.register_handler('gateway', 'direct.reserve', lambda *_: {'unused': True})
    for op in DELEGATE_OPS:
        registry.register_handler('gateway', op, lambda ctx, args, op=op: delegation.handle(op, ctx, args))
    # The represented strategy's run is registered first: a delegate acts for a current run.
    for principal, identity, ops in ((OWN, OWN_IDENTITY, own_ops), (RISK.principal_ref, RISK, DELEGATE_OPS)):
        registered = _register(registry, principal=principal, identity=identity, scope=SCOPE, key=principal,
                               instance_id='gateway', port='service.direct_write', scope_ref='scope',
                               operations=tuple(ops))
        assert registered['ok'], registered
    session = registered['result']

    def delegated_reserve(key, **order):
        payload = _order(key, **order).to_dict()
        response = registry.handle(RISK.principal_ref, _next_request('direct.delegated_reserve', {
            'registration_id': session['registration_id'], 'service_epoch': session['service_epoch'],
            'instance_id': 'gateway', 'arguments': {'command': _command(key, payload).to_mapping(),
                                                    'payload': payload, 'ttl_seconds': 10}},
            identity=RISK, scope=SCOPE))
        assert response['ok'], response
        return response['result']

    def own_reserve(key, **order):
        payload = _order(key, **order).to_dict()

        def reserve():
            try:
                return journal.reserve(STRATEGY, key, OWN, payload).status
            except DirectCommandJournalError as error:
                return error.code
        return owner.submit_service_call(reserve).result(timeout_seconds=2)

    def stored(key, principal=RISK.principal_ref):
        def read():
            with journal._transaction() as db:
                delegated = db.execute('SELECT count(*) FROM direct_delegated_commands WHERE request_key=?',
                                       (key,)).fetchone()[0]
            try:
                return journal.get(STRATEGY, key, principal).status, delegated
            except DirectCommandJournalError as error:
                return error.code, delegated
        return owner.submit_service_call(read).result(timeout_seconds=2)

    def on_owner(callback):
        return owner.submit_service_call(callback).result(timeout_seconds=2)

    return SimpleNamespace(owner=owner, registry=registry, journal=journal, control=control, holder=holder,
                           delegated_reserve=delegated_reserve, own_reserve=own_reserve, stored=stored,
                           on_owner=on_owner)


@pytest.fixture
def gateway(tmp_path):
    site = _build(tmp_path)
    try:
        yield site
    finally:
        site.registry.close()
        assert site.owner.close(2)


@pytest.mark.parametrize('mode', ['STOP_NEW', 'FREEZE', 'DRAIN'])
@pytest.mark.parametrize('scope_kind', ['account', 'strategy'])
def test_control_refusal_of_a_delegated_reserve_is_definite_and_writes_nothing(gateway, mode, scope_kind):
    gateway.on_owner(lambda: gateway.control.apply('cause', mode, scope_kind=scope_kind,
                                                   scope_value='' if scope_kind == 'account' else STRATEGY,
                                                   origin='operator:test'))
    result = gateway.delegated_reserve('blocked-open', side=Side.BUY, offset='open')
    assert result == {'status': 'REQUEST_REJECTED', 'code': 'CONTROL_' + mode}, result
    # The owner transaction rolled back: neither the command nor its delegation envelope exists.
    assert gateway.stored('blocked-open') == ('NOT_FOUND', 0)
    # The represented strategy's own open is refused with the same code.
    assert gateway.own_reserve('own-open', side=Side.BUY, offset='open') == 'CONTROL_' + mode


def test_ledger_refusal_of_a_delegated_close_is_definite_and_writes_nothing(gateway):
    gateway.holder['continuous'] = False
    result = gateway.delegated_reserve('close-unverified')
    assert result == {'status': 'REQUEST_REJECTED', 'code': 'STRATEGY_LEDGER_UNAVAILABLE'}, result
    assert gateway.stored('close-unverified') == ('NOT_FOUND', 0)
    assert gateway.own_reserve('own-close-unverified') == 'STRATEGY_LEDGER_UNAVAILABLE'


def test_delegated_close_beyond_the_strategys_position_is_refused_like_its_own(gateway):
    # The strategy holds one lot long; a Risk delegate asks to close two on its behalf (INV63-06).
    result = gateway.delegated_reserve('close-two', quantity='2')
    assert result == {'status': 'REQUEST_REJECTED', 'code': 'STRATEGY_CLOSE_EXCEEDS'}, result
    assert gateway.stored('close-two') == ('NOT_FOUND', 0)
    assert gateway.own_reserve('own-close-two', quantity='2') == 'STRATEGY_CLOSE_EXCEEDS'
    # One lot is within the attributed position.
    admitted = gateway.delegated_reserve('close-one')
    assert admitted['status'] == 'OBSERVED' and admitted['state'] == 'RESERVED', admitted


@pytest.mark.parametrize('first', ['delegated', 'own'])
def test_delegated_and_own_closes_together_never_exceed_the_position(gateway, first):
    if first == 'delegated':
        assert gateway.delegated_reserve('close-delegated')['state'] == 'RESERVED'
        assert gateway.own_reserve('close-own') == 'STRATEGY_CLOSE_EXCEEDS'
    else:
        assert gateway.own_reserve('close-own') == 'RESERVED'
        result = gateway.delegated_reserve('close-delegated')
        assert result == {'status': 'REQUEST_REJECTED', 'code': 'STRATEGY_CLOSE_EXCEEDS'}, result
        assert gateway.stored('close-delegated') == ('NOT_FOUND', 0)


def test_concurrent_delegated_and_own_closes_admit_exactly_the_position(gateway):
    barrier = threading.Barrier(2)
    results = {}

    def delegated():
        barrier.wait()
        results['delegated'] = gateway.delegated_reserve('close-delegated')

    def own():
        barrier.wait()
        results['own'] = gateway.own_reserve('close-own')

    threads = [threading.Thread(target=delegated), threading.Thread(target=own)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    admitted = [results['own'] == 'RESERVED', results['delegated'].get('state') == 'RESERVED']
    assert admitted.count(True) == 1, results
    refused = (results['own'] if not admitted[0]
               else results['delegated'] if results['delegated'].get('status') != 'REQUEST_REJECTED'
               else results['delegated']['code'])
    assert refused == 'STRATEGY_CLOSE_EXCEEDS', results


def _fenced_gateway(tmp_path, behaviour):
    calls = []

    @contextmanager
    def fence(context, order):
        calls.append((context.identity, order.idempotency_key, order.offset))
        if behaviour == 'deny':
            raise GatewayReservationAdmissionDenied()
        if behaviour == 'ledger':
            raise DirectCommandJournalError('STRATEGY_LEDGER_UNAVAILABLE')
        yield
    return _build(tmp_path, fence=fence), calls


@pytest.mark.parametrize('behaviour,code', [('deny', 'ADMISSION_DENIED'), ('ledger', 'STRATEGY_LEDGER_UNAVAILABLE')])
def test_delegated_reserve_goes_through_the_strategys_trading_fence(tmp_path, behaviour, code):
    site, calls = _fenced_gateway(tmp_path, behaviour)
    try:
        result = site.delegated_reserve('fenced-open', side=Side.BUY, offset='open')
        assert result == {'status': 'REQUEST_REJECTED', 'code': code}, result
        assert site.stored('fenced-open') == ('NOT_FOUND', 0)
        assert calls == [(RISK, 'fenced-open', 'open')]
    finally:
        site.registry.close()
        assert site.owner.close(2)


def test_admitting_fence_is_held_for_a_new_reservation_only(tmp_path):
    site, calls = _fenced_gateway(tmp_path, 'admit')
    try:
        first = site.delegated_reserve('fenced-close')
        assert first['state'] == 'RESERVED', first
        assert calls == [(RISK, 'fenced-close', 'close_today')]
        # The same request again re-observes the stored reservation without the trading fence.
        assert site.delegated_reserve('fenced-close') == first
        assert len(calls) == 1
    finally:
        site.registry.close()
        assert site.owner.close(2)
