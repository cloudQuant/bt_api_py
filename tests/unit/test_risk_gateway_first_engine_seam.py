"""Synthetic v2 engine/bridge seam; never live source or write authorization."""
import json
import sqlite3
import zmq
from dataclasses import replace

import pytest

from bt_api_base.risk_service_protocol import RiskOrderView
from bt_api_py.risk_gateway_first_reserve_client import RiskGatewayFirstReserveError
from bt_api_risk.pretrade.engine import _v2_first_native_clock as _real_native_clock
from tests.unit.test_risk_gateway_first_reserve_client import bridge_harness


@pytest.fixture
def engine_case(bridge_harness, monkeypatch):
    from bt_api_risk.pretrade import engine as engine_module
    from bt_api_risk.pretrade import CallerIdentity, PolicyStore, PretradeEngine, RuleRegistry
    from test_pretrade_support import FakeFacts, FakeGateway, FakeControl, OPERATOR, base_policy
    h, bridge, values, request, peer, runtime, target, registry, context, arguments, binding = bridge_harness
    # Test-only sources; both production guards remain unchanged.
    monkeypatch.setattr(engine_module, '_v1_source_quarantined', lambda: False)
    monkeypatch.setattr(engine_module, '_v2_first_native_clock', lambda _ledger, _clock: True)
    rules = RuleRegistry()
    store = PolicyStore(h.ledger)
    engine = PretradeEngine(h.ledger, store, rules, FakeFacts(h.clock), FakeGateway(), FakeControl(),
        account_scope=binding.account_scope, route=binding.route, now_ns=h.clock,
        reservation_ttl_seconds=10, actor_principal=request.command.actor_principal,
        dispatch_recheck_seconds=5, dispatch_recheck_limit=8)
    engine.publish_policy(OPERATOR, base_policy(self_trade={'enabled': False}), 0, 'synthetic')
    caller = CallerIdentity(context.principal_ref, context.identity.actor_ref, 'strategy',
        frozenset({binding.strategy_ref}), binding.run_id, frozenset({'risk.order.reserve.v2'}))
    view = RiskOrderView.from_mapping(json.loads(values['view_json']))
    return h, bridge, engine, caller, view, values, peer, registry, context


def test_engine_bridge_atomic_first_attempt(engine_case):
    h, bridge, engine, caller, view, values, peer, registry, _ = engine_case
    token = engine._v2_begin_first(caller, view, values['payload_json'], caller.run_id,
                                   bridge._binding, bridge._request_snapshot)
    with bridge._admission.scope() as check:
        created, snapshot = engine._v2_prepare_first_atomic(token, gateway_service_id=bridge._target_snapshot.service_id,
            gateway_instance_id=bridge._target_snapshot.instance_id, recheck=check)
    assert created is True and type(snapshot) is tuple
    assert engine._v2_post_first(token) is True
    with h.ledger.transaction() as txn:
        assert txn.permit(bridge._binding.permit_id).state == 'RESERVING'
        assert txn._v2_record(bridge._binding)['status'] == 'PENDING'
        assert txn._v4_snapshot(bridge._binding.permit_id) == snapshot
    assert peer.calls == []


def test_explicit_bridge_runs_engine_then_one_strict_call(engine_case):
    h, bridge, engine, caller, view, values, peer, _, _ = engine_case
    attempt = bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    ticket = bridge.send_once(attempt, timeout_seconds=2)
    result = bridge.take_verified(bridge.consume_once(ticket))
    assert result.matches_request(bridge._request_snapshot)
    assert len(peer.calls) == 1
    with h.ledger.transaction() as txn:
        assert txn.permit(bridge._binding.permit_id).state == 'RESERVING'


def test_production_guard_refuses_before_facts_gather(engine_case, monkeypatch):
    from bt_api_risk.pretrade import engine as module
    _, bridge, engine, caller, view, values, peer, _, _ = engine_case
    monkeypatch.setattr(module, '_v1_source_quarantined', lambda: True)
    monkeypatch.setattr(engine._facts, 'trading_day', lambda: pytest.fail('facts gathered'))
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    assert peer.calls == []


def test_ledger_source_guard_independently_blocks_before_gather(engine_case, monkeypatch):
    from bt_api_risk.pretrade import ledger as module
    _, bridge, engine, caller, view, values, peer, _, _ = engine_case
    monkeypatch.setattr(module, '_source_unverified', lambda _connection: True)
    monkeypatch.setattr(engine._facts, 'trading_day', lambda: pytest.fail('facts gathered'))
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    assert peer.calls == []


def test_replaced_engine_method_rejected_before_gather(engine_case, monkeypatch):
    _, bridge, engine, caller, view, values, peer, _, _ = engine_case
    monkeypatch.setattr(engine, '_evaluate', lambda *a, **k: pytest.fail('untrusted callback'))
    monkeypatch.setattr(engine._facts, 'trading_day', lambda: pytest.fail('facts gathered'))
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    assert peer.calls == []


def test_native_clock_and_original_engine_methods_required(engine_case, monkeypatch):
    from bt_api_risk.pretrade import engine as module
    _, bridge, engine, caller, view, values, peer, _, _ = engine_case
    monkeypatch.setattr(module, '_v2_first_native_clock', _real_native_clock)
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    assert peer.calls == []


def test_custom_rules_refused_before_rule_execution(engine_case):
    _, bridge, engine, caller, view, values, peer, _, _ = engine_case
    class Evil:
        def evaluate(self, _ctx):
            pytest.fail('custom rule executed')
    engine._active = replace(engine._active, custom=((object(), Evil(), 1),))
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    assert peer.calls == []


@pytest.mark.parametrize('item', ['caller', 'view', 'binding', 'request', 'facts', 'policy', 'order'])
def test_candidate_snapshots_refuse_low_level_mutation(engine_case, item):
    _, bridge, engine, caller, view, values, peer, _, _ = engine_case
    token = engine._v2_begin_first(caller, view, values['payload_json'], caller.run_id,
                                   bridge._binding, bridge._request_snapshot)
    record = engine._v2_first_candidates[token]
    target = {'caller': caller, 'view': view, 'binding': bridge._binding,
              'request': bridge._request_snapshot, 'facts': record['facts'],
              'policy': record['active'].policy, 'order': record['order']}[item]
    field, changed = {'caller': ('principal_ref', 'other'), 'view': ('request_key', 'other'),
        'binding': ('run_id', 'other'), 'request': ('risk_permit_id', 'other'),
        'facts': ('trading_day', '20990101'), 'policy': ('policy_id', 'other'),
        'order': ('strategy_ref', 'other')}[item]
    original = getattr(target, field)
    object.__setattr__(target, field, changed)
    with bridge._admission.scope() as check:
        with pytest.raises(Exception):
            engine._v2_prepare_first_atomic(token, gateway_service_id=bridge._target_snapshot.service_id,
                gateway_instance_id=bridge._target_snapshot.instance_id, recheck=check)
        object.__setattr__(target, field, original)
        with pytest.raises(Exception):
            engine._v2_prepare_first_atomic(token, gateway_service_id=bridge._target_snapshot.service_id,
                gateway_instance_id=bridge._target_snapshot.instance_id, recheck=check)
    assert peer.calls == []


def test_failed_attempt_insert_rolls_back_permit_and_v2(engine_case):
    h, bridge, engine, caller, view, values, peer, _, _ = engine_case
    with sqlite3.connect(h.path) as db:
        db.execute("CREATE TRIGGER synthetic_fail_v4 BEFORE INSERT ON risk_pretrade_first_attempt_v4 "
                   "BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    with sqlite3.connect(h.path) as db:
        assert db.execute('SELECT count(*) FROM risk_pretrade_permits').fetchone() == (0,)
        assert db.execute('SELECT count(*) FROM risk_pretrade_dispatch_v2').fetchone() == (0,)
        assert db.execute('SELECT count(*) FROM risk_pretrade_first_attempt_v4').fetchone() == (0,)
    assert peer.calls == []


def test_prepare_uses_one_transaction_for_all_three_rows(engine_case, monkeypatch):
    from bt_api_risk.pretrade import engine as module
    from bt_api_risk.risk_order_journal_storage import RiskOrderJournalStorage
    h, bridge, engine, caller, view, values, peer, _, _ = engine_case
    original = RiskOrderJournalStorage._connection
    statements = []
    def traced_connection(self, **kwargs):
        connection = original(self, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection
    # Synthetic instrumentation only; both original native identity pins are
    # replaced so the trace can observe SQL issued by the existing code.
    monkeypatch.setattr(RiskOrderJournalStorage, '_connection', traced_connection)
    monkeypatch.setattr(module, '_V2_FIRST_STORAGE_CONNECTION', traced_connection)
    bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    normalized = [statement.upper().strip() for statement in statements]
    begins = [i for i, statement in enumerate(normalized) if statement == 'BEGIN IMMEDIATE']
    commits = [i for i, statement in enumerate(normalized) if statement == 'COMMIT']
    assert len(begins) == len(commits) == 1
    for table in ('RISK_PRETRADE_PERMITS', 'RISK_PRETRADE_DISPATCH_V2', 'RISK_PRETRADE_FIRST_ATTEMPT_V4'):
        inserts = [i for i, statement in enumerate(normalized) if statement.startswith('INSERT INTO ' + table + ' ')]
        # SQLite traces a statement again when its trigger executes.
        assert inserts and all(begins[0] < index < commits[0] for index in inserts)
    with sqlite3.connect(h.path) as connection:
        for table in ('risk_pretrade_permits', 'risk_pretrade_dispatch_v2', 'risk_pretrade_first_attempt_v4'):
            assert connection.execute('SELECT count(*) FROM ' + table).fetchone() == (1,)
    assert peer.calls == []


def test_denied_policy_never_creates_first_attempt_or_sends(engine_case):
    from test_pretrade_support import OPERATOR, base_policy
    h, bridge, engine, caller, view, values, peer, _, _ = engine_case
    document = base_policy(self_trade={'enabled': False})
    document['per_order']['max_quantity'][view.strategy_ref][view.instrument.instrument_id] = '0.5'
    engine.publish_policy(OPERATOR, document, 1, 'tighten')
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    with sqlite3.connect(h.path) as db:
        assert db.execute('SELECT state FROM risk_pretrade_permits').fetchall() == [('DENIED',)]
        assert db.execute('SELECT count(*) FROM risk_pretrade_dispatch_v2').fetchone() == (0,)
        assert db.execute('SELECT count(*) FROM risk_pretrade_first_attempt_v4').fetchone() == (0,)
    assert peer.calls == []


def test_existing_first_attempt_cannot_be_recreated_or_sent(engine_case):
    from bt_api_py.risk_gateway_first_reserve_client import RiskGatewayFirstReserveBridge
    h, bridge, engine, caller, view, values, peer, _, context = engine_case
    bridge.prepare(values)
    other = RiskGatewayFirstReserveBridge(ledger=h.ledger, runtime=bridge._runtime,
        context=context, arguments=bridge._arguments, binding=bridge._binding,
        target=bridge._target, request=bridge._request, payload=bridge._payload)
    with pytest.raises(RiskGatewayFirstReserveError):
        other.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    with sqlite3.connect(h.path) as db:
        assert db.execute('SELECT count(*) FROM risk_pretrade_first_attempt_v4').fetchone() == (1,)
    assert peer.calls == []


def test_uncertain_after_commit_never_sends_or_retries(engine_case):
    h, bridge, engine, caller, view, values, peer, _, _ = engine_case
    state = h.ledger._v2_clock_ns.__self__
    def clock_fails_after_commit():
        try:
            with sqlite3.connect(h.path, timeout=0) as db:
                db.execute('BEGIN IMMEDIATE')
                db.rollback()
        except sqlite3.OperationalError:
            return h.clock()
        raise RuntimeError('synthetic postcommit clock uncertainty')
    state.source = clock_fails_after_commit
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    with sqlite3.connect(h.path) as db:
        assert db.execute('SELECT count(*) FROM risk_pretrade_permits').fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM risk_pretrade_first_attempt_v4').fetchone() == (1,)
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    assert peer.calls == []


def test_facts_and_gateway_run_outside_registry_and_sql_locks(engine_case, monkeypatch):
    h, bridge, engine, caller, view, values, peer, registry, _ = engine_case
    facts = engine._facts
    original = facts.trading_day
    def check_locks():
        assert not registry._lock._is_owned()
        assert not registry._db.in_transaction
        with sqlite3.connect(h.path, timeout=0.1) as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.rollback()
    def trading_day():
        check_locks()
        return original()
    facts.trading_day = trading_day
    peer.before_reply = check_locks
    target = bridge._target_snapshot
    socket = bridge._runtime._transports[(target.service_id, target.transport_ref)]._transport._socket
    original_poll = socket.poll
    def poll(timeout, event):
        check_locks()
        return original_poll(timeout, event)
    monkeypatch.setattr(socket, 'poll', poll)
    attempt = bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    bridge.send_once(attempt, timeout_seconds=2)
    assert len(peer.calls) == 1


def test_admission_expired_during_facts_gather_cannot_write_or_send(engine_case):
    h, bridge, engine, caller, view, values, peer, registry, context = engine_case
    original = engine._facts.trading_day
    def expire():
        registry._db.execute('UPDATE sessions SET expires_at=? WHERE registration_id=?',
                             (0, context.registration_id))
        return original()
    engine._facts.trading_day = expire
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    with sqlite3.connect(h.path) as db:
        assert db.execute('SELECT count(*) FROM risk_pretrade_permits').fetchone() == (0,)
    assert peer.calls == []


def test_caller_must_match_original_registry_context(engine_case):
    h, bridge, engine, caller, view, values, peer, _, _ = engine_case
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.prepare_from_engine(engine, replace(caller, actor_ref='other'), view,
                                   values['payload_json'], caller.run_id)
    with sqlite3.connect(h.path) as db:
        assert db.execute('SELECT count(*) FROM risk_pretrade_permits').fetchone() == (0,)
    assert peer.calls == []


def test_policy_change_after_preparation_blocks_send(engine_case):
    _, bridge, engine, caller, view, values, peer, _, _ = engine_case
    attempt = bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    object.__setattr__(engine._active.policy, 'policy_id', 'changed')
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.send_once(attempt, timeout_seconds=2)
    assert peer.calls == []


def test_permit_expiry_equal_to_now_blocks_send(engine_case):
    h, bridge, engine, caller, view, values, peer, _, _ = engine_case
    attempt = bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    with h.ledger.transaction() as txn:
        expiry = txn.permit(bridge._binding.permit_id).expires_ns
    h.clock.value = expiry
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.send_once(attempt, timeout_seconds=2)
    assert peer.calls == []


@pytest.mark.parametrize('change', ['permit_expiry', 'policy'])
def test_change_at_call_bound_entry_is_refused_before_transport(engine_case, monkeypatch, change):
    from test_pretrade_support import OPERATOR, base_policy
    h, bridge, engine, caller, view, values, peer, _, _ = engine_case
    attempt = bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    original = bridge._runtime._call_bound
    def changed(*args, **kwargs):
        if change == 'permit_expiry':
            h.clock.value = engine._v2_first_candidates[bridge._engine_first[1]]['expires']
        else:
            policy = base_policy(self_trade={'enabled': False})
            policy['per_order']['max_quantity'][view.strategy_ref][view.instrument.instrument_id] = '0.5'
            engine.publish_policy(OPERATOR, policy, 1, 'synthetic tightening')
        return original(*args, **kwargs)
    monkeypatch.setattr(bridge._runtime, '_call_bound', changed)
    with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_ATTEMPT_UNKNOWN'):
        bridge.send_once(attempt, timeout_seconds=2)
    with sqlite3.connect(h.path) as db:
        assert db.execute('SELECT state FROM risk_pretrade_permits').fetchall() == [('RESERVING',)]
        assert db.execute('SELECT count(*) FROM risk_pretrade_first_attempt_v4').fetchone() == (1,)
    assert peer.calls == []


@pytest.mark.parametrize('expiry', ['permit', 'reference'])
def test_wall_clock_jump_during_poll_prevents_socket_send(engine_case, monkeypatch, expiry):
    from bt_api_py import service_runtime as runtime_module
    from bt_api_transport_zmq import service_v2 as transport
    from test_pretrade_support import OPERATOR, base_policy
    h, bridge, engine, caller, view, values, peer, _, _ = engine_case
    if expiry == 'reference':
        policy = base_policy(self_trade={'enabled': False}, price_band={
            'enabled': True, 'max_deviation_ticks': {view.instrument.instrument_id: '5'},
            'max_reference_age_ms': 20})
        engine.publish_policy(OPERATOR, policy, 1, 'synthetic tightening')
    attempt = bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    wall = [h.clock.value]
    def synthetic_wall():
        return wall[0]
    monkeypatch.setattr(runtime_module.time, 'time_ns', synthetic_wall)
    monkeypatch.setattr(runtime_module, '_BOUND_RISK_WALL_NS', synthetic_wall)
    monkeypatch.setattr(transport, '_BOUND_WALL_NS', synthetic_wall)
    monkeypatch.setattr(transport, '_bound_wall_clock_current', lambda: True)
    monkeypatch.setattr(transport, '_TRUSTED_BOUND_WALL_CLOCK_CHECK', transport._bound_wall_clock_current)
    target = bridge._target_snapshot
    socket = bridge._runtime._transports[(target.service_id, target.transport_ref)]._transport._socket
    original_poll = socket.poll
    def jump_during_poll(timeout, event):
        if event == zmq.POLLOUT:
            record = engine._v2_first_candidates[bridge._engine_first[1]]
            wall[0] = engine._v2_first_wall_deadline(record)
        return original_poll(timeout, event)
    monkeypatch.setattr(socket, 'poll', jump_during_poll)
    with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_ATTEMPT_UNKNOWN'):
        bridge.send_once(attempt, timeout_seconds=2)
    assert peer.calls == []


def test_wall_expiry_after_send_remains_unknown_without_retry(engine_case, monkeypatch):
    from bt_api_py import service_runtime as runtime_module
    from bt_api_transport_zmq import service_v2 as transport
    h, bridge, engine, caller, view, values, peer, _, _ = engine_case
    attempt = bridge.prepare_from_engine(engine, caller, view, values['payload_json'], caller.run_id)
    wall = [h.clock.value]
    def synthetic_wall():
        return wall[0]
    monkeypatch.setattr(runtime_module.time, 'time_ns', synthetic_wall)
    monkeypatch.setattr(runtime_module, '_BOUND_RISK_WALL_NS', synthetic_wall)
    monkeypatch.setattr(transport, '_BOUND_WALL_NS', synthetic_wall)
    monkeypatch.setattr(transport, '_bound_wall_clock_current', lambda: True)
    monkeypatch.setattr(transport, '_TRUSTED_BOUND_WALL_CLOCK_CHECK', transport._bound_wall_clock_current)
    target = bridge._target_snapshot
    socket = bridge._runtime._transports[(target.service_id, target.transport_ref)]._transport._socket
    original_send = socket.send
    def send_then_expire(*args, **kwargs):
        original_send(*args, **kwargs)
        record = engine._v2_first_candidates[bridge._engine_first[1]]
        wall[0] = engine._v2_first_wall_deadline(record)
    monkeypatch.setattr(socket, 'send', send_then_expire)
    with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_ATTEMPT_UNKNOWN'):
        bridge.send_once(attempt, timeout_seconds=2)
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.send_once(attempt, timeout_seconds=2)
    with sqlite3.connect(h.path) as db:
        assert db.execute('SELECT state FROM risk_pretrade_permits').fetchall() == [('RESERVING',)]
        assert db.execute('SELECT count(*) FROM risk_pretrade_first_attempt_v4').fetchone() == (1,)
    assert len(peer.calls) == 1
