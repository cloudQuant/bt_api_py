"""CTP lease recovery fails closed with fake sources and temporary journals."""
from contextlib import contextmanager

from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
from bt_api_base.direct_write_protocol import NativeOrderIdentity, PreDispatchRecord, WriteOperation
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_control import GatewayDirectControl
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome, _identity

from bt_api_py.ctp_gateway_lease_recovery import CtpGatewayLeaseRecoverySource
from bt_api_py.gateway_lease_recovery import LeaseRecoveryEvidence
from tests.unit.test_iter63_ctp_gateway_strategy_ledger import D1, ROUTE, order
from tests.unit.test_iter63_ctp_gateway_strategy_ledger import env as legacy_env
from tests.unit.test_iter63_gateway_lease_recovery_proof import scenario as legacy_scenario
from tests.unit.test_iter63_lease_expiry_duration_and_ctp_recovery import BINDING, _Parts
from tests.unit.test_service_registry import _next_request, _register

env = legacy_env
scenario = legacy_scenario


def _owner(owner, callback):
    return owner.submit_service_call(callback).result(timeout_seconds=2)


def _proof(source, binding=BINDING):
    with source.hold_verified(binding) as evidence:
        return evidence


def test_empty_buffer_and_local_sequence_are_not_snapshot_proof(scenario):
    harness, owner = scenario[:2]
    parts = _Parts(harness.journal)
    _owner(owner, parts.bind)
    assert parts.returns.ledger_continuous() is True
    assert parts.buffer.status().record_count == 0
    assert parts.buffer.status().last_sequence == 7
    assert _owner(owner, lambda: _proof(parts.source)) is None
    parts.buffer.last_sequence = 500
    assert _owner(owner, lambda: _proof(parts.source)) is None


def test_old_proof_recheck_rejects_and_rolls_back_drain_clear(scenario):
    harness, owner, _, control, _, recovery, event, _ = scenario
    parts = _Parts(harness.journal)
    _owner(owner, parts.bind)
    old = LeaseRecoveryEvidence(BINDING, 'historical', 'ctp-return-buffer:3:7:20300101', 10**12)
    assert _owner(owner, lambda: parts.source.recheck(old)) is False

    # Emulate an in-flight old caller still yielding legacy evidence. The
    # commit's source recheck must roll back even when the local binding fits.
    class OldInFlight(CtpGatewayLeaseRecoverySource):
        checks = 0

        @contextmanager
        def hold_verified(self, binding):
            yield LeaseRecoveryEvidence(binding, 'historical', 'ctp-return-buffer:3:7:20300101', 10**12)

        def recheck(self, evidence):
            self.checks += 1
            return super().recheck(evidence)

    source = OldInFlight(account_scope='account', strategy_symbols={'strategy': frozenset({'X'})})
    recovery.source = source
    assert recovery.recover_auto(event['event_id']) == 'NOT_COMPARABLE'
    assert source.checks == 1
    cause = _owner(owner, lambda: control.get(event['event_id']))
    assert (cause['active'], cause['version']) == (1, 1)


def test_auto_and_manual_recovery_keep_the_same_drain(scenario):
    harness, owner, registry, control, _, recovery, event, _ = scenario
    parts = _Parts(harness.journal)
    _owner(owner, parts.bind)
    recovery.source = parts.source
    assert recovery.recover_auto(event['event_id']) == 'NOT_COMPARABLE'
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'operator-actor')
    scope = WireScope('domain', account_ref='account', strategy_ref='strategy')

    def handler(ctx, args):
        try:
            result = _owner(owner, lambda: recovery.recover_manual_owned(
                args['cause_id'], args['expected_version'], args['operation_id'],
                admission=ctx.dispatch_admission, context=ctx, arguments=args))
            return {'status': 'OBSERVED' if result else 'RECOVERY_REQUIRED'}
        finally:
            ctx.dispatch_admission.close()

    registry.register_handler('gateway', 'direct.control.resume', handler)
    registration = _register(registry, principal='operator', identity=identity, scope=scope, key='operator',
        instance_id='gateway', port='service.direct_write', scope_ref='scope',
        operations=('direct.control.resume',))['result']
    response = registry.handle('operator', _next_request('direct.control.resume', {
        'registration_id': registration['registration_id'], 'service_epoch': registration['service_epoch'],
        'instance_id': 'gateway', 'arguments': {'cause_id': event['event_id'], 'expected_version': 1,
                                                'operation_id': 'manual'}}, identity=identity, scope=scope))
    assert response['result']['status'] == 'RECOVERY_REQUIRED'
    cause = _owner(owner, lambda: control.get(event['event_id']))
    assert (cause['active'], cause['version']) == (1, 1)


def test_reopened_journal_and_late_callback_do_not_change_drain(scenario):
    harness, owner, _, control, _, recovery, event, _ = scenario
    before = _owner(owner, lambda: control.get(event['event_id']))
    journal = GatewayDirectCommandJournal(harness.root / 'journal', 'account',
        generation=harness.journal.generation, expected_journal_id=harness.journal.journal_id,
        minimum_revision=0, controls_enabled=True)
    parts = _Parts(journal)
    _owner(owner, parts.bind)  # new source instance after a simulated restart
    recovery.source = parts.source
    assert recovery.recover_auto(event['event_id']) == 'NOT_COMPARABLE'
    parts.buffer.record_count = 1  # a callback that was not yet in the local buffer
    parts.buffer.last_sequence += 1
    assert recovery.recover_auto(event['event_id']) == 'NOT_COMPARABLE'
    parts.buffer.record_count = 0  # processed, but still no common venue cut
    assert recovery.recover_auto(event['event_id']) == 'NOT_COMPARABLE'
    assert _owner(owner, lambda: control.get(event['event_id'])) == before
    with journal._transaction() as db:
        row = db.execute('SELECT active,version FROM direct_control_causes WHERE account=? AND cause_id=?',
                         ('account', event['event_id'])).fetchone()
    assert tuple(row) == (1, 1)


def test_existing_order_query_and_cancel_remain_available_with_drain(env):
    journal = env.journal
    control = GatewayDirectControl(journal, env.guard)
    command = journal.reserve('s', 'old', 'p', order())
    native = NativeOrderIdentity(ROUTE, 'native-ref', {'trading_day': D1}, 1)
    identity = _identity(native, ROUTE)
    args = ('s', 'old', command.reservation_id, command.digest, 'p')
    started = journal.begin_dispatch(*args, command.version, identity, ('native-key',))
    journal.finish(*args, started.record.version, identity, ('native-key',), 'SENT')
    control.apply('lease-cause', 'DRAIN', scope_kind='strategy', scope_value='s', origin='system:lease:test')
    source = CtpGatewayLeaseRecoverySource(account_scope='acct', strategy_symbols={'s': frozenset({'rb2701'})})
    source.bind(session=env.session, provider=env.provider, returns=env.returns, journal=journal)
    assert _proof(source) is None
    assert journal.observe('s', 'old', 'p').record.status == 'SENT'
    cancels = GatewayDirectCancelJournal(journal)
    row = cancels.request('s', 'cancel-1', 'p', 'old', command.reservation_id,
                          command.digest, target_identity=native, symbol='rb2701')
    calls = []

    def cancel(target, symbol, port, scope):
        prepared = PreparedCancelRequest('provider-reservation', target, symbol, D1, 3,
                                         {'request_id': 12}, ('cancel-key',))
        marker = port.commit(PreDispatchRecord(WriteOperation.CANCEL, scope, ROUTE, symbol, None,
                                               target, {}, D1, 3, 'synthetic', prepared))
        calls.append(symbol)
        return NativeDispatchOutcome(True, target, marker)

    direct = GatewayDirectDispatcher(journal, ROUTE, env.guard, lambda *_: None, lambda _: (), lambda _: True)
    dispatcher = GatewayDirectCancelDispatcher(cancels, direct, env.guard, ROUTE, cancel, lambda _: True)
    assert dispatcher.dispatch('s', 'cancel-1', 'p', expected_version=row.version).status == 'SENT'
    assert calls == ['rb2701']
    assert control.get('lease-cause')['active'] == 1
