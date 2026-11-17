"""Offline Risk-first Gateway wiring; the production journal gate remains closed."""

import threading
from dataclasses import replace

import pytest
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal, DirectCommandJournalError
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py._gateway_owner_guard_binding import _GatewayOwnerGuardBinding
from bt_api_py.gateway_delegation_service import GatewayDelegationService, GatewayDelegationSettings
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from tests.unit.test_iter64_registry_gateway_risk_first_v2 import KEY, OP, register_actor, request as wire_request, setup_registry
from tests.unit.test_iter64_risk_first_payload_consistency import _payload, _request


@pytest.fixture
def wiring(tmp_path, monkeypatch):
    state = {}
    closing = threading.Event()
    guards = tmp_path / 'guard'
    guards.mkdir()

    def factory():
        guard = LocalAccountSessionGuard(guards, 'acct', 'owner')
        journal = GatewayDirectCommandJournal.create(tmp_path / 'journal.sqlite', 'acct', generation=guard.acquire())
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        journal.enable_account_extensions(guard)
        journal.enable_risk_v2_binding_read_only(guard)
        dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, lambda *_: pytest.fail('dispatch'),
                                             lambda _: ('anchor',), lambda _: True)
        holder = _GatewayOwnerGuardBinding(owner, journal, guard, dispatcher, account='acct',
            journal_id=journal.journal_id, generation=journal.generation, exchange='CTP',
            closing=closing, instance_id='gateway')
        state.update(journal=journal, holder=holder)
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release)

    owner = GatewayDirectOwner(factory, queue_size=2, max_queue_wait_seconds=2, busy_timeout_seconds=2)
    assert owner.start(3)
    registry, clock, actor, scope, grant = setup_registry(path=tmp_path / 'registry.sqlite')
    # setup_registry pre-installs the exact synthetic grant; configure is the
    # service's real installation point in this isolated registry.
    registry._gateway_delegate_grants.clear()
    settings = GatewayDelegationSettings((grant,), lambda instrument: instrument == KEY, risk_first_enabled=True)
    service = GatewayDelegationService(settings, owner=owner, journal=state['journal'], instance_id='gateway',
                                      scope_ref='scope', risk_first_owner_binding=state['holder'])
    service.configure(registry, risk_first_host_check=lambda: True)
    registry.register_handler('gateway', OP, lambda context, args: service.handle(OP, context, args))
    registration = register_actor(registry, actor, scope)['result']
    payload = _payload()
    payload['symbol'] = KEY.native_symbol
    request = _request(payload)
    command = replace(request.command, strategy_scope='strategy-a', actor_principal='risk-p',
                      actor_ref='risk-actor', instrument=KEY, grant_revision=grant.revision)
    request = replace(request, command=command)
    state.update(owner=owner, registry=registry, clock=clock, actor=actor, scope=scope, grant=grant,
                 registration=registration, payload=payload, request=request, service=service)
    try:
        yield state
    finally:
        registry.close()
        assert owner.close(3)


def invoke(state, payload=None):
    args = {'request': state['request'].to_mapping(), 'payload': state['payload'] if payload is None else payload}
    reply = state['registry'].handle('risk-p', wire_request(state['actor'], state['scope'], state['registration'], args))
    assert reply['ok'], reply
    return reply['result']


def test_real_registry_scope_owner_journal_hits_unchanged_write_gate(wiring, monkeypatch):
    state = wiring
    journal = state['journal']
    seen = []
    original = journal.reserve_first_delegated

    def observed(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except DirectCommandJournalError as error:
            seen.append(error.code)
            raise

    monkeypatch.setattr(journal, 'reserve_first_delegated', observed)
    assert invoke(state) == {'status': 'UNKNOWN'}
    assert seen == ['RISK_V2_BINDING_UNAVAILABLE']
    assert journal._risk_v2_writes_authorized() is False
    def rows():
        with journal._transaction() as db:
            return db.execute('SELECT COUNT(*) FROM direct_journal_commands').fetchone()[0]
    assert state['owner'].submit_service_call(rows).result(timeout_seconds=2) == 0


def test_invalid_payload_or_host_drift_never_reaches_journal(wiring, monkeypatch):
    state = wiring
    journal = state['journal']
    monkeypatch.setattr(journal, 'reserve_first_delegated', lambda *a, **k: pytest.fail('journal reached'))
    wrong = dict(state['payload'], quantity='2')
    assert invoke(state, wrong) == {'status': 'UNKNOWN'}
    state['service']._risk_first_host_check = lambda: False
    assert invoke(state) == {'status': 'UNKNOWN'}


def test_unconfigured_or_untrusted_risk_first_does_not_run(wiring):
    state = wiring
    settings = GatewayDelegationSettings((state['grant'],), lambda _: True)
    service = GatewayDelegationService(settings, owner=state['owner'], journal=state['journal'],
                                      instance_id='gateway', scope_ref='scope')
    assert service.handle(OP, object(), {}) == {'status': 'UNKNOWN'}
    with pytest.raises(ValueError, match='INVALID_GATEWAY_DELEGATION'):
        GatewayDelegationService(replace(settings, risk_first_enabled=True), owner=state['owner'],
            journal=state['journal'], instance_id='gateway', scope_ref='scope')


def _synthetic_first(state):
    from bt_api_gateway.direct_command_journal import FirstDelegatedReservation
    request, journal = state['request'], state['journal']
    command = request.command
    return FirstDelegatedReservation(
        account_scope=command.account_scope, strategy_scope=command.strategy_scope,
        request_key=command.request_key, reservation_id='reservation',
        actor_principal=command.actor_principal, run_id=command.run_id,
        payload_digest=command.payload_digest, delegation_digest=command.digest,
        journal_id=journal.journal_id, owner_generation=journal.generation,
        version=1, expires_at=1000.0, remaining_ttl_seconds=0.9,
    )


def test_synthetic_first_result_wire_projection_matches_request(wiring, monkeypatch):
    """Projection isolation only; this synthetic result proves no journal write."""
    from bt_api_base.gateway_risk_first_reserve_v2 import GatewayRiskFirstReserveResultV2
    state = wiring
    first = _synthetic_first(state)
    seen = []

    def synthetic_first(owner, journal, request, payload, **kwargs):
        seen.append((threading.current_thread() is owner._thread, request, payload, kwargs))
        return first

    monkeypatch.setattr(state['holder'], 'reserve_first_risk', synthetic_first)
    monkeypatch.setattr(state['holder'], 'first_risk_remaining', lambda *args: 0.8)
    reply = invoke(state)
    assert set(reply) == {'status', 'result'} and reply['status'] == 'RESERVED'
    result = GatewayRiskFirstReserveResultV2.from_mapping_for_request(reply['result'], state['request'])
    assert result.remaining_ttl_ms == 800 and result.reservation_id == first.reservation_id
    assert len(seen) == 1 and seen[0][0] is True and seen[0][1] == state['request']
    assert state['journal']._risk_v2_writes_authorized() is False


@pytest.mark.parametrize('bad_field', ['account_scope', 'actor_principal', 'payload_digest',
                                       'delegation_digest', 'journal_id', 'owner_generation', 'version'])
def test_synthetic_mismatched_first_result_is_unknown(wiring, monkeypatch, bad_field):
    state = wiring
    first = _synthetic_first(state)
    bad_value = 2 if bad_field == 'version' else 'different'
    monkeypatch.setattr(state['holder'], 'reserve_first_risk', lambda *a, **kw: replace(first, **{bad_field: bad_value}))
    monkeypatch.setattr(state['holder'], 'first_risk_remaining', lambda *a: 0.8)
    assert invoke(state) == {'status': 'UNKNOWN'}


def test_synthetic_expired_first_result_is_unknown(wiring, monkeypatch):
    state = wiring
    monkeypatch.setattr(state['holder'], 'reserve_first_risk', lambda *a, **kw: _synthetic_first(state))
    monkeypatch.setattr(state['holder'], 'first_risk_remaining', lambda *a: 0.0005)
    assert invoke(state) == {'status': 'UNKNOWN'}


def test_synthetic_boolean_version_is_not_an_integer_version(wiring, monkeypatch):
    state = wiring
    first = replace(_synthetic_first(state), version=True)
    monkeypatch.setattr(state['holder'], 'reserve_first_risk', lambda *a, **kw: first)
    assert invoke(state) == {'status': 'UNKNOWN'}


@pytest.mark.parametrize('code', ['FIRST_RESERVATION_ALREADY_EXISTS', 'FIRST_RESERVATION_UNCERTAIN'])
def test_first_refusal_or_uncertain_commit_never_reads_existing_row(wiring, monkeypatch, code):
    state = wiring
    def fail(*args, **kwargs):
        raise DirectCommandJournalError(code)
    monkeypatch.setattr(state['holder'], 'reserve_first_risk', fail)
    monkeypatch.setattr(state['journal'], 'get', lambda *a, **kw: pytest.fail('read old reservation'))
    assert invoke(state) == {'status': 'UNKNOWN'}


def test_synthetic_transaction_registry_expiry_rolls_back(wiring, monkeypatch):
    """Exercise the permit under a controlled SQLite transaction, not the v2 gate."""
    import sqlite3
    state = wiring
    observations = []

    def transaction(owner, journal, request, payload, *, permit, trading_fence):
        assert threading.current_thread() is owner._thread
        db = sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE marker (value INTEGER)')
            db.execute('BEGIN')
            try:
                permit.recheck(db)
                db.execute('INSERT INTO marker VALUES (1)')
                state['clock'][0] += 11.0  # Registry lease expires before commit.
                permit.recheck(db)
                db.commit()
            except Exception:
                db.rollback()
                observations.append(db.execute('SELECT COUNT(*) FROM marker').fetchone()[0])
                raise
        finally:
            db.close()
        pytest.fail('expired admission committed')

    monkeypatch.setattr(state['holder'], 'reserve_first_risk', transaction)
    assert invoke(state) == {'status': 'UNKNOWN'}
    assert observations == [0]
    assert state['journal']._risk_v2_writes_authorized() is False


@pytest.mark.parametrize('risk_first_enabled', [False, True])
def test_real_synthetic_host_explicit_install_advertises_but_gate_refuses(tmp_path, monkeypatch,
                                                                        risk_first_enabled):
    """Real local Host/Registry/owner assembly; no venue session or write enabled."""
    from dataclasses import replace
    from decimal import Decimal
    import zmq
    from bt_api_base.gateway_direct_protocol import gateway_payload_digest
    from bt_api_base.market_protocol import MarketInstrumentKey
    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
    from bt_api_gateway.direct_delegation import GatewayDelegateGrant
    from bt_api_py._contracts.models import OrderRequest, OrderType, Side
    from bt_api_py.ctp_gateway_trading import build_ctp_gateway_trading_profile
    from bt_api_py.gateway_direct_service import GatewayControlGrant
    from bt_api_py.service_config import parse_service_config
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
    from tests.test_iter63_ctp_gateway_trading import CLIENT_A, make
    from tests.unit.test_service_registry import _next_request, _register

    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    raw['components']['gateway']['settings']['options'].update(
        control_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True,
        delegation_enabled=True)
    config = parse_service_config(raw)
    account = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
    template = replace(account, strategy_ref=CLIENT_A.actor_ref)
    operator = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'operator-actor')
    actor = WireIdentity(WireRole.SERVICE, 'risk-principal', 'risk-actor')
    control_ops = frozenset({'direct.control.status'})
    operator_grant = ResourceGrant('gateway-a', 'service.direct_write', config.scope_ref, account, control_ops)
    risk_ops = frozenset({OP if risk_first_enabled else 'direct.delegated_reserve'})
    risk_grant = ResourceGrant('gateway-a', 'service.direct_write', config.scope_ref, template, risk_ops)
    operator_key, _ = zmq.curve_keypair()
    actor_key, _ = zmq.curve_keypair()
    authority = replace(authority, principals={**authority.principals, operator_key: 'operator', actor_key: 'risk-principal'},
        grants={**authority.grants, 'operator': (PrincipalGrant(operator, (operator_grant,)),),
                'risk-principal': (PrincipalGrant(actor, (risk_grant,)),)})
    key = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
    grant = GatewayDelegateGrant(actor, settings.account_scope, CLIENT_A.actor_ref, authority.scope.execution_domain_ref,
                                 'risk', 'rev1', frozenset({key}), frozenset({'direct.reserve', 'direct.dispatch'}))
    profile = build_ctp_gateway_trading_profile(config, authority, tmp_path / 'registry.sqlite',
        profile_ref='risk-first', settings=settings, grants=grants, request_feed_factory=lambda: None,
        market_stream_factory=lambda *_: None, assert_admitted=lambda _: True,
        control_grants={'operator': GatewayControlGrant('operator-actor', True, frozenset(), control_ops)},
        upgrade_controls_from_v9=True, upgrade_strategy_ledger_from_v10=True, upgrade_account_extensions_from_v11=True,
        delegation_settings=GatewayDelegationSettings((grant,), lambda instrument: instrument == key,
                                                      risk_first_enabled=risk_first_enabled))
    deployment = profile.deployment_factory()
    deployment.start()
    try:
        host, scope = deployment._host, replace(template, run_id='run')
        registry = host._registry
        capability = registry._capabilities[('gateway-a', 'service.direct_write')]
        assert (OP in capability.operations) is risk_first_enabled
        assert (OP in capability.write_operations) is risk_first_enabled
        assert (('gateway-a', OP) in registry._handlers) is risk_first_enabled
        strategy = _register(registry, principal=CLIENT_A.principal_ref, identity=CLIENT_A, scope=scope, key='strategy',
            instance_id='gateway-a', port='service.direct_write', scope_ref=config.scope_ref, operations=('direct.reserve',))
        assert strategy['ok'], strategy
        registered = _register(registry, principal=actor.principal_ref, identity=actor, scope=scope, key='risk',
            instance_id='gateway-a', port='service.direct_write', scope_ref=config.scope_ref, operations=(OP,))
        if not risk_first_enabled:
            assert not registered['ok']
            return
        assert registered['ok'], registered
        payload = OrderRequest(symbol='rb', side=Side.BUY, order_type=OrderType.LIMIT,
            quantity=Decimal('1'), price=Decimal('1'), account_id=settings.account_scope,
            client_order_id='client', idempotency_key='key', offset='open', quantity_unit='lots', exchange_id='SHFE').to_dict()
        request = _request(payload)
        command = replace(request.command, account_scope=settings.account_scope, strategy_scope=CLIENT_A.actor_ref,
            execution_domain_ref=authority.scope.execution_domain_ref, actor_principal=actor.principal_ref,
            actor_ref=actor.actor_ref, instrument=key, grant_revision=grant.revision,
            payload_digest=gateway_payload_digest(payload))
        request = replace(request, command=command)
        journal = host._gateway_delegations[0].journal
        original = journal.reserve_first_delegated
        observed = []
        def capture(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            except DirectCommandJournalError as error:
                observed.append(error.code)
                raise
        monkeypatch.setattr(journal, 'reserve_first_delegated', capture)
        rec = registered['result']
        reply = registry.handle(actor.principal_ref, _next_request(OP, {'registration_id': rec['registration_id'],
            'service_epoch': rec['service_epoch'], 'instance_id': 'gateway-a',
            'arguments': {'request': request.to_mapping(), 'payload': payload}}, identity=actor, scope=scope))
        assert reply['ok'] and reply['result'] == {'status': 'UNKNOWN'}, reply
        assert observed == ['RISK_V2_BINDING_UNAVAILABLE']
        assert journal._risk_v2_writes_authorized() is False
    finally:
        deployment.close()


def test_registry_lock_is_not_held_while_waiting_for_owner(wiring, monkeypatch):
    state = wiring
    owner, registry = state['owner'], state['registry']
    entered, release, queued = threading.Event(), threading.Event(), threading.Event()
    original = owner.submit_service_call

    def blocker():
        entered.set()
        assert release.wait(2)

    active = original(blocker)
    assert entered.wait(2)

    def capture(callback):
        handle = original(callback)
        queued.set()
        return handle

    monkeypatch.setattr(owner, 'submit_service_call', capture)
    outcomes, errors = [], []
    def call():
        try:
            outcomes.append(invoke(state))
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=call)
    worker.start()
    try:
        assert queued.wait(2)
        obtained = registry._lock.acquire(timeout=0.2)
        assert obtained, 'Registry lock was held while waiting for owner'
        registry._lock.release()
    finally:
        release.set()
        worker.join(3)
    active.result(timeout_seconds=2)
    assert not worker.is_alive() and errors == [] and outcomes == [{'status': 'UNKNOWN'}]


def test_expiry_during_final_ttl_sampling_returns_unknown(wiring, monkeypatch):
    state = wiring
    monkeypatch.setattr(state['holder'], 'reserve_first_risk', lambda *a, **kw: _synthetic_first(state))
    def remaining(*args):
        state['clock'][0] += 11.0
        return 0.8
    monkeypatch.setattr(state['holder'], 'first_risk_remaining', remaining)
    assert invoke(state) == {'status': 'UNKNOWN'}


def test_host_drift_during_final_ttl_sampling_returns_unknown(wiring, monkeypatch):
    state = wiring
    monkeypatch.setattr(state['holder'], 'reserve_first_risk', lambda *a, **kw: _synthetic_first(state))
    def remaining(*args):
        state['service']._risk_first_host_check = lambda: False
        return 0.8
    monkeypatch.setattr(state['holder'], 'first_risk_remaining', remaining)
    assert invoke(state) == {'status': 'UNKNOWN'}


def test_owner_condition_race_after_scope_probe_does_not_block(wiring, monkeypatch):
    state = wiring
    owner, holder = state['owner'], state['holder']
    proceed, locked, release, checked = (threading.Event() for _ in range(4))
    original = holder._assert_bound_nonblocking

    def contend():
        assert proceed.wait(3)
        with owner._condition:
            locked.set()
            assert release.wait(3)

    thread = threading.Thread(target=contend)
    thread.start()

    def checked_binding(owner_arg, journal_arg):
        proceed.set()
        assert locked.wait(2)
        try:
            return original(owner_arg, journal_arg)
        finally:
            checked.set()

    monkeypatch.setattr(holder, '_assert_bound_nonblocking', checked_binding)
    outcomes, errors = [], []
    def call():
        try:
            outcomes.append(invoke(state))
        except BaseException as error:
            errors.append(error)
    request_thread = threading.Thread(target=call)
    request_thread.start()
    try:
        assert locked.wait(2)
        assert checked.wait(0.3), 'owner waited on condition after Registry admission'
    finally:
        release.set()
        thread.join(3)
        request_thread.join(3)
    assert not thread.is_alive() and not request_thread.is_alive()
    assert errors == [] and outcomes == [{'status': 'UNKNOWN'}]
