"""Lease-expiry DRAIN: mandatory expiry duration (D63-28) and the CTP recovery proof (D63-33, I63-E128)."""
import json
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.ctp_gateway_lease_recovery import CtpGatewayLeaseRecoverySource
from bt_api_py.gateway_lease_drain import GatewayLeaseDrain, GatewayLeaseDrainSettings
from bt_api_py.gateway_lease_recovery import LeaseRecoveryBinding, LeaseRecoveryEvidence
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from tests.unit.test_iter63_gateway_lease_recovery_proof import scenario  # noqa: F401  (fixture)
from tests.unit.test_service_registry import _next_request, _register

DAY = '20300101'
IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
SCOPE = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
OPS = frozenset({'direct.reserve'})


def _registry(clock, path=':memory:'):
    capability = ServiceCapability('gateway', 'service.direct_write', OPS, OPS)
    resource = ResourceGrant('gateway', 'service.direct_write', 'scope', replace(SCOPE, run_id=None), OPS)
    registry = ServiceRegistry(path, service_id='gateway-service', capabilities=(capability,),
        grants={'principal': (PrincipalGrant(IDENTITY, (resource,)),)}, policy_version='p',
        requirements_version='r', lease_seconds=10, clock=lambda: clock[0])
    registry.register_handler('gateway', 'direct.reserve', lambda *_: {'handled': True})
    return registry


def _register_strategy(registry, key):
    result = _register(registry, principal='principal', identity=IDENTITY, scope=SCOPE, key=key,
        instance_id='gateway', port='service.direct_write', scope_ref='scope', operations=tuple(OPS))
    assert result['ok'], result
    return result['result']


def _reserve(registry, registration):
    return registry.handle('principal', _next_request('direct.reserve', {
        'registration_id': registration['registration_id'], 'service_epoch': registration['service_epoch'],
        'instance_id': 'gateway', 'arguments': {}}, identity=IDENTITY, scope=SCOPE))


def _pending(registry):
    return registry.pending_gateway_lease_expirations(instance_id='gateway', account_ref='account')


def _states(registry):
    return [row[0] for row in registry._db.execute(
        'SELECT state FROM gateway_lease_expirations ORDER BY expires_at')]


def test_lease_must_stay_expired_for_the_configured_duration_before_the_event_is_pending():
    clock = [100.0]
    registry = _registry(clock)
    try:
        registry.configure_gateway_lease_drain(instance_id='gateway', account_ref='account',
                                               strategies=frozenset({'strategy'}), expiry_seconds=30)
        first = _register_strategy(registry, 'first')        # lease until 110, due at 140
        for moment in (111.0, 139.9):
            clock[0] = moment
            assert _pending(registry) == () and _states(registry) == ['WAITING']
        clock[0] = 140.0
        event, = _pending(registry)
        assert (event['registration_id'], event['state'], event['expires_at'], event['apply_at']) == (
            first['registration_id'], 'PENDING', 110.0, 140.0)
        # Too late: a registration after the duration is fenced until the cause is stored.
        second = _register_strategy(registry, 'second')
        assert _states(registry) == ['PENDING']
        fenced = _reserve(registry, second)
        assert fenced['ok'] is False and fenced['code'] == 'STRATEGY_STOPPED'
    finally:
        registry.close()


def test_a_strategy_that_registers_again_before_the_duration_withdraws_the_event():
    clock = [100.0]
    registry = _registry(clock)
    try:
        registry.configure_gateway_lease_drain(instance_id='gateway', account_ref='account',
                                               strategies=frozenset({'strategy'}), expiry_seconds=30)
        _register_strategy(registry, 'first')                 # lease until 110, due at 140
        clock[0] = 125.0
        second = _register_strategy(registry, 'second')       # back in time; lease until 135
        assert _states(registry) == ['RETURNED'] and _pending(registry) == ()
        accepted = _reserve(registry, second)
        assert accepted['ok'] is True and accepted['result'] == {'handled': True}
        # The first lease's due time passes: nothing is applied for it. The second
        # lease then expires on its own and waits for its own duration (due 165).
        clock[0] = 141.0
        assert _pending(registry) == () and _states(registry) == ['RETURNED', 'WAITING']
        clock[0] = 165.0
        event, = _pending(registry)
        assert event['registration_id'] == second['registration_id']
        assert _states(registry) == ['RETURNED', 'PENDING']
    finally:
        registry.close()


def test_another_strategy_registering_does_not_withdraw_the_waiting_event():
    clock = [100.0]
    other = WireIdentity(WireRole.STRATEGY_CLIENT, 'other', 'other-strategy')
    other_scope = WireScope('domain', account_ref='account', strategy_ref='other-strategy', run_id='run')
    capability = ServiceCapability('gateway', 'service.direct_write', OPS, OPS)
    registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(capability,),
        grants={'principal': (PrincipalGrant(IDENTITY, (ResourceGrant(
                    'gateway', 'service.direct_write', 'scope', replace(SCOPE, run_id=None), OPS),)),),
                'other': (PrincipalGrant(other, (ResourceGrant(
                    'gateway', 'service.direct_write', 'scope', replace(other_scope, run_id=None), OPS),)),)},
        policy_version='p', requirements_version='r', lease_seconds=10, clock=lambda: clock[0])
    try:
        registry.configure_gateway_lease_drain(instance_id='gateway', account_ref='account',
                                               strategies=frozenset({'strategy'}), expiry_seconds=30)
        _register_strategy(registry, 'first')
        clock[0] = 120.0
        result = _register(registry, principal='other', identity=other, scope=other_scope, key='other',
            instance_id='gateway', port='service.direct_write', scope_ref='scope', operations=tuple(OPS))
        assert result['ok'], result
        assert _states(registry) == ['WAITING']
        clock[0] = 140.0
        assert len(_pending(registry)) == 1
    finally:
        registry.close()


@pytest.mark.parametrize('bad', [None, True, -1, float('inf'), float('nan'), 86401, '5'])
def test_the_expiry_duration_is_mandatory_and_bounded(bad):
    with pytest.raises(TypeError):
        GatewayLeaseDrainSettings(frozenset({'strategy'}))    # no default (D63-28)
    with pytest.raises(ValueError, match='INVALID_GATEWAY_LEASE_DRAIN'):
        GatewayLeaseDrainSettings(frozenset({'strategy'}), bad)
    assert GatewayLeaseDrainSettings(frozenset({'strategy'}), 45.5).expiry_seconds == 45.5
    registry = _registry([100.0])
    try:
        with pytest.raises(TypeError):
            registry.configure_gateway_lease_drain(instance_id='gateway', account_ref='account',
                                                   strategies=frozenset({'strategy'}))
        with pytest.raises(ValueError, match='invalid gateway lease policy'):
            registry.configure_gateway_lease_drain(instance_id='gateway', account_ref='account',
                                                   strategies=frozenset({'strategy'}), expiry_seconds=bad)
    finally:
        registry.close()


def test_the_duration_cannot_change_while_the_registry_runs_and_an_older_table_gains_the_column(tmp_path):
    clock = [100.0]
    registry = _registry(clock, tmp_path / 'registry')
    try:
        # A table written before the duration existed (events applied at expiry).
        registry._db.execute('CREATE TABLE gateway_lease_expirations ('
            'event_id TEXT PRIMARY KEY,instance_id TEXT NOT NULL,account_ref TEXT NOT NULL,'
            'strategy_ref TEXT NOT NULL,run_id TEXT NOT NULL,principal TEXT NOT NULL,'
            'registration_id TEXT NOT NULL,service_epoch TEXT NOT NULL,expires_at REAL NOT NULL,'
            'observed_at REAL NOT NULL,state TEXT NOT NULL)')
        registry._db.execute("INSERT INTO gateway_lease_expirations VALUES "
                             "('old','gateway','account','strategy','run','principal','r','e',1.0,2.0,'APPLIED')")
        configure = lambda seconds: registry.configure_gateway_lease_drain(  # noqa: E731
            instance_id='gateway', account_ref='account', strategies=frozenset({'strategy'}),
            expiry_seconds=seconds)
        configure(20)
        configure(20.0)
        with pytest.raises(ValueError, match='gateway lease policy changed'):
            configure(21)
        _register_strategy(registry, 'first')                 # lease until 110, due at 130
        clock[0] = 129.0
        assert _pending(registry) == ()
        clock[0] = 130.0
        event, = _pending(registry)
        assert event['apply_at'] == 130.0
        assert registry._db.execute("SELECT state,apply_at FROM gateway_lease_expirations "
                                    "WHERE event_id='old'").fetchone()[:] == ('APPLIED', None)
    finally:
        registry.close()


# ---------------------------------------------------------------- CTP proof source


class _Buffer:
    def __init__(self):
        self.gap, self.record_count, self.last_sequence = False, 0, 7

    def status(self):
        return SimpleNamespace(gap=self.gap, record_count=self.record_count, last_sequence=self.last_sequence)


class _Provider:
    generation, day = 3, DAY

    def session_generation(self):
        return self.generation

    def trading_day(self):
        return self.day


class _Returns:
    continuous = True

    def ledger_continuous(self):
        return self.continuous


class _Parts:
    def __init__(self, journal):
        self.buffer, self.provider, self.returns, self.journal = _Buffer(), _Provider(), _Returns(), journal
        self.source = CtpGatewayLeaseRecoverySource(
            account_scope='account', strategy_symbols={'strategy': frozenset({'X', 'Y'})})

    def bind(self):
        self.source.bind(session=SimpleNamespace(return_buffer=self.buffer), provider=self.provider,
                         returns=self.returns, journal=self.journal)

    def fact(self, digest, symbol, *, day=DAY, disposition='UNATTRIBUTED', account='account'):
        with self.journal._transaction() as db:
            db.execute("INSERT INTO direct_execution_facts VALUES (?, ?, ?, ?, 'UNMATCHED', NULL, NULL, 0)",
                       (account, digest, json.dumps({'instrument': {'native_symbol': symbol}, 'trading_day': day}),
                        disposition))


BINDING = LeaseRecoveryBinding('gateway', 'account', 'strategy', 'run', 'principal', 'old-registration',
                               'new-registration', 'epoch', 'journal', 'generation', 'cause', 1, 5, 'consumer', 0)


def _prove(parts, binding=BINDING):
    with parts.source.hold_verified(binding) as evidence:
        return evidence


@pytest.fixture
def parts(scenario):  # noqa: F811
    h, owner = scenario[0], scenario[1]
    value = _Parts(h.journal)
    value.on_owner = lambda callback: owner.submit_service_call(callback).result(timeout_seconds=2)
    value.on_owner(value.bind)
    return value


def test_ctp_source_proves_a_quiet_complete_cut_and_binds_it_to_the_request(parts):
    evidence = parts.on_owner(lambda: _prove(parts))
    assert type(evidence) is LeaseRecoveryEvidence and evidence.binding is BINDING
    assert evidence.provider_cut == 'ctp-return-buffer:3:7:' + DAY
    assert len(evidence.evidence_id) == 64
    assert parts.on_owner(lambda: parts.source.recheck(evidence)) is True
    other = parts.on_owner(lambda: _prove(parts, replace(BINDING, cause_version=2)))
    assert other.evidence_id != evidence.evidence_id
    # Returns elsewhere do not concern this strategy: another instrument, an
    # earlier trading day, another account, or a return that is settled.
    parts.on_owner(lambda: parts.fact('d1', 'Z'))
    parts.on_owner(lambda: parts.fact('d2', 'X', day='20291231'))
    parts.on_owner(lambda: parts.fact('d3', 'X', disposition='APPLIED'))
    assert parts.on_owner(lambda: _prove(parts)) is not None


@pytest.mark.parametrize('obstacle', [
    'returns_not_continuous', 'callback_waiting', 'gap', 'no_trading_day', 'no_generation',
    'unattributed_return_on_own_instrument', 'conflict_on_own_instrument', 'unreadable_return',
    'too_many_open_returns', 'strategy_without_instruments', 'other_account', 'not_a_binding', 'unbound'])
def test_ctp_source_gives_no_evidence_when_the_cut_cannot_be_shown(parts, obstacle, monkeypatch):
    binding = BINDING
    if obstacle == 'returns_not_continuous':
        parts.returns.continuous = None
    elif obstacle == 'callback_waiting':
        parts.buffer.record_count = 1
    elif obstacle == 'gap':
        parts.buffer.gap = True
    elif obstacle == 'no_trading_day':
        parts.provider.day = None
    elif obstacle == 'no_generation':
        parts.provider.generation = None
    elif obstacle == 'unattributed_return_on_own_instrument':
        parts.on_owner(lambda: parts.fact('d1', 'Y'))
    elif obstacle == 'conflict_on_own_instrument':
        parts.on_owner(lambda: parts.fact('d1', 'X', disposition='CONFLICT'))
    elif obstacle == 'unreadable_return':
        def unreadable():
            with parts.journal._transaction() as db:
                db.execute("INSERT INTO direct_execution_facts VALUES ('account','d1','{}','UNATTRIBUTED',"
                           "'UNMATCHED',NULL,NULL,0)")
        parts.on_owner(unreadable)
    elif obstacle == 'too_many_open_returns':
        monkeypatch.setattr('bt_api_py.ctp_gateway_lease_recovery._MAX_OPEN_FACTS', 2)
        for index in range(3):
            parts.on_owner(lambda index=index: parts.fact(f'd{index}', 'Z'))
    elif obstacle == 'strategy_without_instruments':
        binding = replace(BINDING, strategy='unlisted')
    elif obstacle == 'other_account':
        binding = replace(BINDING, account='other')
    elif obstacle == 'not_a_binding':
        binding = {'strategy': 'strategy'}
    elif obstacle == 'unbound':
        parts.source.unbind()
    assert parts.on_owner(lambda: _prove(parts, binding)) is None


def test_ctp_source_proves_only_on_the_thread_that_bound_it(parts):
    assert threading.current_thread() is not parts.source._bound[4]
    assert _prove(parts) is None
    evidence = parts.on_owner(lambda: _prove(parts))
    assert parts.source.recheck(evidence) is False


@pytest.mark.parametrize('change', ['callback', 'sequence', 'generation', 'day', 'continuity', 'expired', 'forged'])
def test_ctp_recheck_fails_once_the_cut_has_moved(parts, change, monkeypatch):
    evidence = parts.on_owner(lambda: _prove(parts))
    assert parts.on_owner(lambda: parts.source.recheck(evidence)) is True
    if change == 'callback':
        parts.buffer.record_count = 1
    elif change == 'sequence':
        parts.buffer.last_sequence = 8
    elif change == 'generation':
        parts.provider.generation = 4
    elif change == 'day':
        parts.provider.day = '20300102'
    elif change == 'continuity':
        parts.returns.continuous = False
    elif change == 'expired':
        monkeypatch.setattr('bt_api_py.ctp_gateway_lease_recovery.time',
                            SimpleNamespace(monotonic=lambda: evidence.valid_until_monotonic))
    elif change == 'forged':
        evidence = {'provider_cut': evidence.provider_cut}
    assert parts.on_owner(lambda: parts.source.recheck(evidence)) is False


@pytest.mark.parametrize('bad', [
    {'account_scope': ''}, {'strategy_symbols': {}}, {'strategy_symbols': {'s': frozenset()}},
    {'strategy_symbols': {'s': {'X'}}}, {'validity_seconds': 0}, {'validity_seconds': 5.1},
    {'validity_seconds': True}])
def test_ctp_source_rejects_invalid_settings(bad):
    arguments = dict({'account_scope': 'account', 'strategy_symbols': {'s': frozenset({'X'})}}, **bad)
    with pytest.raises(ValueError, match='INVALID_CTP_LEASE_RECOVERY_SOURCE'):
        CtpGatewayLeaseRecoverySource(**arguments)


# ------------------------------------------- bridge: automatic and manual release


class _World:
    """A real owner with a cancel path, a Registry and the lease bridge with the CTP source."""

    def __init__(self, tmp_path, *, automatic=True, expiry_seconds=0):
        from bt_api_base.gateway_execution_stream_protocol import gateway_execution_consumer_ref
        from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
        from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal

        from tests.test_iter63_gateway_direct_owner import Harness
        from tests.unit.test_iter63_gateway_cancel_owner import owner_for

        harness = Harness(tmp_path)

        def factory():
            bundle = harness.factory()
            cancel = GatewayDirectCancelDispatcher(GatewayDirectCancelJournal(bundle.journal), bundle.dispatcher,
                                                    harness.guard, 'SYN', lambda *_: None, lambda _: True)
            return replace(bundle, cancel_dispatcher=cancel)

        self.owner = owner_for(factory)
        assert self.owner.start(2)
        self.journal = harness.journal
        self.control = self.on_owner(lambda: harness.journal.enable_controls(harness.guard))
        self.parts = _Parts(harness.journal)
        self.on_owner(self.parts.bind)
        ops = frozenset({'direct.reserve', 'direct.dispatch'})
        capability = ServiceCapability('gateway', 'service.direct_write', ops, ops)
        resource = ResourceGrant('gateway', 'service.direct_write', 'scope', replace(SCOPE, run_id=None), ops)
        self.clock = [100.0]
        self.registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(capability,),
            grants={'principal': (PrincipalGrant(IDENTITY, (resource,)),)}, policy_version='p',
            requirements_version='r', lease_seconds=10, clock=lambda: self.clock[0])
        for operation in ops:
            self.registry.register_handler('gateway', operation, lambda *_: {'handled': True})
        self.manager = GatewayLeaseDrain(
            GatewayLeaseDrainSettings(frozenset({'strategy'}), expiry_seconds, automatic_resume=automatic,
                                      recovery_source=self.parts.source),
            instance_id='gateway', account_ref='account', owner=self.owner, control=self.control,
            service_identity=WireIdentity(WireRole.SERVICE, 'gateway-service', 'gateway-actor'))
        self.manager.configure(self.registry)
        self._ops = tuple(ops)
        self._consumer = gateway_execution_consumer_ref(
            principal_ref='principal', actor_ref='strategy', execution_domain_ref='domain',
            account_scope='account', strategy_scope='strategy')

    def on_owner(self, callback):
        return self.owner.submit_service_call(callback).result(timeout_seconds=2)

    def register(self, key):
        result = _register(self.registry, principal='principal', identity=IDENTITY, scope=SCOPE, key=key,
            instance_id='gateway', port='service.direct_write', scope_ref='scope', operations=self._ops)
        assert result['ok'], result
        return result['result']

    def reserve(self, registration):
        return _reserve(self.registry, registration)

    def strategy_gets_an_event(self):
        def insert():
            with self.journal._transaction() as db:
                db.execute('INSERT INTO direct_execution_events VALUES (1,?,?,?,?)',
                           ('account', 'strategy', 'digest-1', '{}'))
        self.on_owner(insert)

    def strategy_has_read_its_stream(self):
        def insert():
            with self.journal._transaction() as db:
                latest = db.execute('SELECT coalesce(max(sequence),0) FROM direct_execution_events '
                                    'WHERE account=? AND strategy=?', ('account', 'strategy')).fetchone()[0]
                db.execute('INSERT INTO direct_execution_consumers VALUES (?,?,?,?,?,?)',
                           ('account', 'principal', self._consumer, 'strategy', latest, latest))
        self.on_owner(insert)

    def causes(self):
        return list(self.on_owner(lambda: self.control.active_system_causes(self.manager.origin)))

    def close(self):
        self.registry.close()
        assert self.owner.close(2)


@pytest.fixture
def world(tmp_path, request, monkeypatch):
    # Only the bridge and the source read this clock; the owner keeps real time.
    monotonic = [1000.0]
    stub = SimpleNamespace(monotonic=lambda: monotonic[0])
    monkeypatch.setattr('bt_api_py.gateway_lease_drain.time', stub)
    monkeypatch.setattr('bt_api_py.ctp_gateway_lease_recovery.time', stub)
    value = _World(tmp_path, **getattr(request, 'param', {}))
    value.monotonic = monotonic
    try:
        yield value
    finally:
        value.close()


def _expire_and_return(world):
    """The strategy's lease expired, it registered again and has one event it has not read."""
    world.register('first')
    world.clock[0] = 111.0
    current = world.register('second')
    world.strategy_gets_an_event()
    world.manager.maintain(world.registry)                    # the cause is stored
    cause, = world.causes()
    assert cause['mode'] == 'DRAIN' and cause['scope_value'] == 'strategy'
    return current, cause


def test_bridge_releases_the_lease_cause_once_the_ctp_source_proves_recovery(world):
    current, cause = _expire_and_return(world)
    # Registered again, but the strategy has not read the event recorded for it.
    world.monotonic[0] += 2
    world.manager.maintain(world.registry)
    assert [item['cause_id'] for item in world.causes()] == [cause['cause_id']]
    world.strategy_has_read_its_stream()
    # A callback is still waiting in the return buffer: not a complete cut.
    world.parts.buffer.record_count = 1
    world.monotonic[0] += 2
    world.manager.maintain(world.registry)
    assert len(world.causes()) == 1
    # Provable now, but the same cause is offered again only after the retry pause.
    world.parts.buffer.record_count = 0
    world.monotonic[0] += 0.5
    world.manager.maintain(world.registry)
    assert len(world.causes()) == 1
    world.monotonic[0] += 0.6
    world.manager.maintain(world.registry)
    assert world.causes() == []
    released = world.on_owner(lambda: world.control.get(cause['cause_id']))
    assert (released['active'], released['version']) == (0, cause['version'] + 1)
    accepted = world.reserve(current)
    assert accepted['ok'] is True and accepted['result'] == {'handled': True}
    world.monotonic[0] += 2
    world.manager.maintain(world.registry)                    # nothing left to release
    assert world.on_owner(lambda: world.control.get(cause['cause_id']))['version'] == released['version']


def test_bridge_does_not_release_another_origin_or_an_unreturned_strategy(world):
    world.register('first')
    world.clock[0] = 111.0
    world.manager.maintain(world.registry)                    # expired, nobody came back
    cause, = world.causes()
    world.on_owner(lambda: world.control.apply('operator-cause', 'FREEZE', scope_kind='strategy',
                                               scope_value='strategy', origin='operator'))
    world.strategy_has_read_its_stream()
    world.monotonic[0] += 2
    world.manager.maintain(world.registry)
    assert [item['cause_id'] for item in world.causes()] == [cause['cause_id']]   # no new registration
    world.register('second')
    world.monotonic[0] += 2
    world.manager.maintain(world.registry)
    assert world.causes() == []
    assert world.on_owner(lambda: world.control.get('operator-cause'))['active'] == 1


@pytest.mark.parametrize('world', [{'automatic': False}], indirect=True)
def test_bridge_leaves_the_cause_to_the_operator_when_automatic_release_is_off(world):
    _, cause = _expire_and_return(world)
    world.strategy_has_read_its_stream()
    for _ in range(3):
        world.monotonic[0] += 2
        world.manager.maintain(world.registry)
    assert [item['cause_id'] for item in world.causes()] == [cause['cause_id']]
    # An operator's resume goes through the same recovery object and source.
    assert world.manager.recovery.source is world.parts.source


@pytest.mark.parametrize('world', [{'expiry_seconds': 30}], indirect=True)
def test_no_cause_is_applied_for_a_strategy_that_returns_within_the_duration(world):
    world.register('first')                                   # lease until 110, due at 140
    world.clock[0] = 125.0
    current = world.register('second')
    world.manager.maintain(world.registry)
    event_id, = [row[0] for row in world.registry._db.execute('SELECT event_id FROM gateway_lease_expirations')]
    assert _states(world.registry) == ['RETURNED']            # withdrawn, never applied
    def stored_causes():
        with world.journal._transaction() as db:
            return db.execute('SELECT count(*) FROM direct_control_causes WHERE cause_id=?', (event_id,)).fetchone()[0]
    assert world.causes() == [] and world.on_owner(stored_causes) == 0
    world.clock[0] = 134.0                                    # second lease still valid (until 135)
    world.manager.maintain(world.registry)
    assert _states(world.registry) == ['RETURNED']
    assert world.causes() == [] and world.reserve(current)['ok'] is True


def test_a_busy_owner_defers_the_release_attempt_without_a_fault(world):
    import threading as _threading

    _, cause = _expire_and_return(world)
    world.strategy_has_read_its_stream()
    gate, running = _threading.Event(), _threading.Event()

    def hold():
        running.set()
        gate.wait(20)

    handle = world.owner.submit_service_call(hold)
    assert running.wait(2)
    try:
        world.monotonic[0] += 2
        world.manager.maintain(world.registry)                # returns although the owner is busy
    finally:
        gate.set()
        handle.result(timeout_seconds=2)
    assert len(world.causes()) == 1
    # The deferred attempt is repeated after the owner retry pause, not after
    # the longer pause that follows an attempt that actually ran.
    world.monotonic[0] += 0.3
    world.manager.maintain(world.registry)
    assert world.causes() == []


def test_an_owner_that_turns_busy_at_the_release_step_costs_only_the_owner_retry_pause(world):
    _expire_and_return(world)
    world.strategy_has_read_its_stream()
    real, deferred = world.manager._on_owner, []

    def busy_once(callback):
        if getattr(callback, '__name__', '') == 'attempt' and not deferred:
            deferred.append(callback)
            return False, None
        return real(callback)

    world.manager._on_owner = busy_once
    world.monotonic[0] += 2
    world.manager.maintain(world.registry)
    assert len(deferred) == 1 and len(world.causes()) == 1
    world.monotonic[0] += 0.3                                 # less than the pause after a real attempt
    world.manager.maintain(world.registry)
    assert world.causes() == []



def test_bridge_releases_a_returned_strategy_that_never_had_an_event(world):
    """Found on SimNow 7x24: such a strategy has no consumer row and nothing to restore."""
    world.register('first')
    world.clock[0] = 111.0
    world.register('second')
    world.manager.maintain(world.registry)                    # stored, and released in the same pass
    event_id, state = world.registry._db.execute(
        'SELECT event_id,state FROM gateway_lease_expirations').fetchone()
    cause = world.on_owner(lambda: world.control.get(event_id))
    assert state == 'APPLIED' and (cause['mode'], cause['active'], cause['version']) == ('DRAIN', 0, 2)
    assert world.causes() == []
