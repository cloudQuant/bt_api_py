"""Cancel-only start (D63-58): account DRAIN before serving, then one cancel per working order."""
import json
import threading
from dataclasses import replace

import pytest
from bt_api_base.direct_write_protocol import (
    DirectExecutionEventKind,
    DirectOrderStatus,
    NativeOrderIdentity,
    PreDispatchRecord,
    WriteOperation,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import DirectCommandJournalError
from bt_api_gateway.direct_dispatch import NativeDispatchOutcome, _identity
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from test_iter63_direct_cancel_journal import prepared
from test_iter63_direct_execution_journal import fact

from bt_api_py.gateway_cancel_only import GatewayCancelOnlyRun
from tests.test_iter63_gateway_direct_owner import Harness
from tests.unit.test_iter63_gateway_cancel_owner import owner_for

SERVICE = WireIdentity(WireRole.SERVICE, 'gateway-service', 'gateway-actor')
X = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'X', 'X')


class World:
    def __init__(self, tmp_path, *, schema17=True):
        self.harness, self.calls, self.ready, self.clock = Harness(tmp_path), [], [False], [1000.0]

        def factory():
            bundle = self.harness.factory()

            def native(target, symbol, port, scope):
                # A distinct provider cancel key and reservation per order.
                number = len(self.calls) + 1
                prep = prepared(target, key=f'cancel-key-{number}', reservation=f'provider-reservation-{number}')
                record = PreDispatchRecord(WriteOperation.CANCEL, scope, 'SYN', symbol, None, target, {},
                                           prep.trading_day, prep.session_generation, 'synthetic', prep)
                marker = port.commit(record)
                self.calls.append(marker)
                return NativeDispatchOutcome(True, target, marker)
            cancel = GatewayDirectCancelDispatcher(GatewayDirectCancelJournal(bundle.journal), bundle.dispatcher,
                                                    self.harness.guard, 'SYN', native, lambda _: True)
            return replace(bundle, cancel_dispatcher=cancel)

        self.owner = owner_for(factory)
        assert self.owner.start(2)
        self.journal = self.harness.journal
        self.control = self.on_owner(lambda: self.journal.enable_controls(self.harness.guard))
        self.schema17 = schema17
        self._upgraded = False

    def on_owner(self, callback):
        return self.owner.submit_service_call(callback).result(timeout_seconds=2)

    def order(self, key, status='SENT', strategy='strategy'):
        """A command of the journal in the given dispatch state (before the schema upgrade)."""
        command = self.journal.reserve(strategy, key, 'principal', {'symbol': 'X', 'side': 'buy', 'quantity': '1'})
        native = NativeOrderIdentity('SYN', 'ref-' + key, {'trading_day': '20300101', 'front_id': 1}, 1)
        started = self.journal.begin_dispatch(strategy, key, command.reservation_id, command.digest, 'principal', 1,
                                              _identity(native, 'SYN'), ('key-' + key,))
        if status != 'DISPATCHING':
            self.journal.finish(strategy, key, command.reservation_id, command.digest, 'principal',
                                started.record.version, _identity(native, 'SYN'), ('key-' + key,), status)
        return command.reservation_id

    def upgrade(self):
        if not self.schema17 or self._upgraded:
            return

        def walk():
            for step in ('enable_strategy_ledger', 'enable_account_extensions', 'enable_parent_authority',
                         'enable_account_facts_financial', 'enable_actor_stream', 'enable_strategy_origin',
                         'enable_control_cancel'):
                getattr(self.journal, step)(self.harness.guard)
        self.on_owner(walk)
        self._upgraded = True

    def run(self, **kwargs):
        self.upgrade()
        value = GatewayCancelOnlyRun(
            instance_id='gateway', account_ref='account', owner=self.owner, control=self.control,
            operator='ops-1', reason='gateway cannot serve', service_identity=SERVICE,
            returns_ready=lambda: self.ready[0], clock=lambda: self.clock[0], **kwargs)
        self.on_owner(value.apply_on_owner)
        return value

    def becomes_terminal(self, key, status=DirectOrderStatus.CANCELLED):
        def ingest():
            terminal = replace(
                fact(kind=DirectExecutionEventKind.ORDER, keys=('key-' + key, 'venue-' + key), binding=('key-' + key,),
                     venue='venue-' + key, route='SYN', instrument=X, status=status, ref='source-' + key),
                account_scope='account', order_quantity='1', filled_quantity='0')
            return GatewayDirectExecutionLedger(self.journal, self.harness.guard).ingest(terminal).disposition
        assert self.on_owner(ingest) == 'ATTRIBUTED'

    def cause(self, run):
        return self.on_owner(lambda: self.control.get(run.cause_id))

    def close(self):
        assert self.owner.close(2)


@pytest.fixture
def world(tmp_path, request):
    value = World(tmp_path, **getattr(request, 'param', {}))
    try:
        yield value
    finally:
        value.close()


def test_cancel_only_blocks_new_orders_then_cancels_each_working_order_once(world):
    world.order('one')
    world.order('two')
    run = world.run()
    cause = world.cause(run)
    assert (cause['mode'], cause['scope_kind'], cause['scope_value'], cause['active']) == ('DRAIN', 'account', '', 1)
    assert cause['origin'].startswith('system:cancel_only:') and run.cause_id.startswith('cancel-only:')
    # Nothing is cancelled before the return stream of the session is complete.
    run.maintain()
    assert run.status()['state'] == 'WAITING_FOR_RETURNS' and world.calls == []
    world.ready[0] = True
    run.maintain()
    assert len(world.calls) == 1 and run.status()['state'] == 'CANCELLING'
    run.maintain()
    assert len(world.calls) == 2
    for _ in range(3):
        run.maintain()                                        # a sent cancel is never sent again
    status = run.status()
    assert len(world.calls) == 2 and not run.finished
    assert (status['targets'], status['pending'], status['unknown'], status['cancel_dispatched']) == (2, 0, 2, 2)

    def proofs():
        with world.journal._transaction() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT proof_json FROM direct_control_cancel_proofs')]
    sources = [proof['source'] for proof in world.on_owner(proofs)]
    assert len(sources) == 2 and all(source == {
        'kind': 'operator', 'actor': 'ops-1', 'registration': 'cancel-only:' + run.run_id,
        'epoch': world.journal.generation, 'event_id': None} for source in sources)
    assert {proof['requester'] for proof in world.on_owner(proofs)} == {'gateway-service'}
    world.becomes_terminal('one')
    run.maintain()
    assert run.status()['observed_terminal'] == 1 and not run.finished
    world.becomes_terminal('two')
    run.maintain()
    status = run.status()
    assert run.finished and (status['state'], status['code'], status['observed_terminal']) == ('COMPLETE', None, 2)
    assert world.cause(run)['active'] == 1                    # stays until an operator resumes it
    run.maintain()
    assert len(world.calls) == 2


def test_an_order_that_already_ended_is_not_cancelled(world):
    world.order('one')
    world.becomes_terminal('one', DirectOrderStatus.REJECTED)
    run = world.run()
    world.ready[0] = True
    run.maintain()
    # It ended before the cause was stored: it is not even a target (I63-E131), and nothing is sent.
    status = run.status()
    assert world.calls == [] and (status['state'], status['targets'], status['observed_terminal']) == ('COMPLETE', 0, 0)


def test_an_order_that_ends_after_the_cause_was_stored_is_not_cancelled_either(world):
    world.order('one')
    run = world.run()                       # the cause is stored while the order may still be working
    world.becomes_terminal('one', DirectOrderStatus.CANCELLED)
    world.ready[0] = True
    run.maintain()
    status = run.status()
    assert world.calls == [] and (status['state'], status['targets'], status['observed_terminal']) == ('COMPLETE', 1, 1)


def test_a_journal_without_working_orders_completes_at_once(world):
    run = world.run()
    world.ready[0] = True
    run.maintain()
    status = run.status()
    assert (status['state'], status['targets'], status['cancel_dispatched']) == ('COMPLETE', 0, 0)


@pytest.mark.parametrize('status', ['UNKNOWN', 'DISPATCHING'])
def test_an_order_with_an_unknown_dispatch_outcome_is_not_cancelled_blindly(world, status):
    world.order('one', status=status)
    run = world.run(deadline_seconds=30)
    world.ready[0] = True
    for _ in range(3):
        run.maintain()
    assert world.calls == [] and not run.finished
    world.clock[0] += 30
    run.maintain()
    status = run.status()
    assert run.finished and status['state'] == 'INCOMPLETE' and status['observed_terminal'] == 0
    assert status['code'] in ('STEP_FAILED', 'DEADLINE_PASSED')


def test_the_run_is_incomplete_when_a_cancelled_order_is_not_seen_terminal_in_time(world):
    world.order('one')
    run = world.run(deadline_seconds=30)
    world.ready[0] = True
    run.maintain()
    assert len(world.calls) == 1
    world.clock[0] += 29.9
    run.maintain()
    assert not run.finished
    world.clock[0] += 0.1
    run.maintain()
    status = run.status()
    assert (status['state'], status['code'], status['unknown']) == ('INCOMPLETE', 'DEADLINE_PASSED', 1)
    run.maintain()
    assert len(world.calls) == 1


def test_returns_that_never_complete_end_the_run_without_any_cancel(world):
    world.order('one')
    run = world.run(deadline_seconds=30)
    world.clock[0] += 30
    run.maintain()
    assert world.calls == [] and run.status()['state'] == 'INCOMPLETE'
    # The run has ended: returns that complete afterwards start nothing.
    world.ready[0] = True
    run.maintain()
    target, = world.on_owner(lambda: world.control.targets(run.cause_id))
    assert world.calls == [] and target['state'] == 'PENDING'


def test_the_cancel_fence_holds_only_while_the_run_is_cancelling(world):
    world.order('one')
    run = world.run()
    with pytest.raises(ValueError, match='CANCEL_ONLY_NOT_RUNNING'), run._fence(None):
        pass
    world.ready[0] = True
    run.maintain()
    assert run.status()['state'] == 'CANCELLING'
    with run._fence(None) as check:
        check()
        run._state = 'INCOMPLETE'
        with pytest.raises(ValueError, match='CANCEL_ONLY_NOT_RUNNING'):
            check()


@pytest.mark.parametrize('world', [{'schema17': False}], indirect=True)
def test_below_schema_17_the_cause_is_stored_and_the_run_reports_unavailable(world):
    world.order('one')
    run = world.run()
    assert run.finished and (run.status()['state'], run.status()['code']) == (
        'UNAVAILABLE', 'CANCEL_ONLY_REQUIRES_JOURNAL_17')
    assert world.cause(run)['active'] == 1                    # still no new order

    def reserve_new():
        try:
            return world.journal.reserve('strategy', 'new', 'principal', {'symbol': 'X'}).status
        except DirectCommandJournalError as error:
            return error.code
    assert world.on_owner(reserve_new) == 'CONTROL_DRAIN'
    world.ready[0] = True
    run.maintain()
    assert world.calls == [] and run.status()['code'] == 'CANCEL_ONLY_REQUIRES_JOURNAL_17'
    target, = world.on_owner(lambda: world.control.targets(run.cause_id))
    assert target['state'] == 'PENDING'                       # never claimed


def test_a_busy_owner_defers_the_step(world):
    world.order('one')
    run = world.run()
    world.ready[0] = True
    gate, running = threading.Event(), threading.Event()

    def hold():
        running.set()
        gate.wait(20)

    handle = world.owner.submit_service_call(hold)
    assert running.wait(2)
    submit, asked = world.owner.submit_service_call, []

    def counting(callback):
        asked.append(callback)
        return submit(callback)

    world.owner.submit_service_call = counting
    try:
        run.maintain()
        assert world.calls == [] and run.status()['code'] is None and len(asked) == 1
        run.maintain()                                        # inside the retry pause: no owner call at all
        assert len(asked) == 1
    finally:
        world.owner.submit_service_call = submit
        gate.set()
        handle.result(timeout_seconds=2)
    world.clock[0] += 0.3
    run.maintain()
    assert len(world.calls) == 1


@pytest.mark.parametrize('bad', [
    {'operator': ''}, {'reason': ' padded '}, {'instance_id': ''}, {'account_ref': 'other'},
    {'service_identity': WireIdentity(WireRole.STRATEGY_CLIENT, 'p', 'a')}, {'returns_ready': None},
    {'deadline_seconds': 0}, {'deadline_seconds': 3601}, {'deadline_seconds': True}])
def test_invalid_settings_are_refused(world, bad):
    arguments = {'instance_id': 'gateway', 'account_ref': 'account', 'owner': world.owner,
                 'control': world.control, 'operator': 'ops-1', 'reason': 'r', 'service_identity': SERVICE,
                 'returns_ready': lambda: True}
    arguments.update(bad)
    with pytest.raises(ValueError, match='INVALID_GATEWAY_CANCEL_ONLY'):
        GatewayCancelOnlyRun(**arguments)
