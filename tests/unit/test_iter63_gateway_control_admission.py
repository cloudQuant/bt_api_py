"""Synthetic registry checks for delayed operator control authorization."""
import threading
import pytest

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.unit.test_service_registry import _next_request, _register


def test_control_admission_is_operator_bound_and_expires_on_owner_handoff():
    clock = [100.0]
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator-p', 'operator-a')
    scope = WireScope('test-domain', account_ref='account-a')
    operations = frozenset({'direct.control.apply'})
    capability = ServiceCapability('gateway-a', 'service.direct_write', operations, operations)
    resource = ResourceGrant('gateway-a', 'service.direct_write', 'scope-a', scope, operations)
    registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(capability,),
        grants={'operator-p': (PrincipalGrant(identity, (resource,)),)},
        policy_version='policy', requirements_version='requirements', lease_seconds=10,
        clock=lambda: clock[0])
    seen = []
    registry.register_handler('gateway-a', 'direct.control.apply', lambda ctx, args: seen.append(ctx) or {'ok': True})
    try:
        registered = _register(registry, principal='operator-p', identity=identity, scope=scope,
            instance_id='gateway-a', port='service.direct_write', scope_ref='scope-a',
            operations=('direct.control.apply',))
        assert registered['ok'], registered
        rec = registered['result']
        args = {'cause_id': 'one', 'mode': 'FREEZE'}
        reply = registry.handle('operator-p', _next_request('direct.control.apply',
            {'registration_id': rec['registration_id'], 'service_epoch': rec['service_epoch'],
             'instance_id': 'gateway-a', 'arguments': args}, identity=identity, scope=scope))
        assert reply['ok'], reply
        permit = seen[0].dispatch_admission
        assert permit.matches(args) and permit.matches_operation('direct.control.apply')
        assert not permit.matches({'cause_id': 'two', 'mode': 'FREEZE'})
        clock[0] = 111.0
        errors = []
        def owner():
            try:
                with permit.scope():
                    pass
            except Exception as exc:
                errors.append(getattr(exc, 'code', type(exc).__name__))
        t = threading.Thread(target=owner); t.start(); t.join(2)
        assert not t.is_alive() and errors == ['NOT_REGISTERED']
    finally:
        registry.close()


def test_operator_rpc_controls_real_owner_and_durable_journal(tmp_path):
    from bt_api_gateway.direct_command_journal import DirectCommandJournalError
    from bt_api_py.gateway_direct_service import GatewayControlGrant, GatewayDirectGrant, GatewayDirectServiceBinding
    from tests.test_iter63_gateway_direct_owner import Harness, create
    import pytest

    h = Harness(tmp_path)
    owner = create(h)
    assert owner.start(2)
    registry = None
    try:
        control = owner.submit_service_call(lambda: h.journal.enable_controls(h.guard)).result(timeout_seconds=2)
        operations = frozenset({'direct.control.identity', 'direct.control.apply', 'direct.control.resume', 'direct.control.status', 'direct.control.list', 'direct.control.resume_status'})
        writes = frozenset({'direct.control.apply', 'direct.control.resume'})
        binding = GatewayDirectServiceBinding(h.journal,
            {'strategy-p': GatewayDirectGrant('strategy-a', frozenset({'rb'}))}, owner=owner,
            control=control, control_grants={'operator-p': GatewayControlGrant('operator-a', True, frozenset(), operations)})
        identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator-p', 'operator-a')
        scope = WireScope('test-domain', account_ref='account')
        capability = ServiceCapability('gateway-a', 'service.direct_write', operations, writes)
        resource = ResourceGrant('gateway-a', 'service.direct_write', 'scope-a', scope, operations)
        registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(capability,),
            grants={'operator-p': (PrincipalGrant(identity, (resource,)),)}, policy_version='policy',
            requirements_version='requirements', lease_seconds=10, clock=lambda: 100.0)
        for operation in operations:
            registry.register_handler('gateway-a', operation,
                lambda ctx, args, op=operation: binding.handle(op, context=ctx, arguments=args))
        registered = _register(registry, principal='operator-p', identity=identity, scope=scope,
            instance_id='gateway-a', port='service.direct_write', scope_ref='scope-a', operations=tuple(operations))
        assert registered['ok'], registered
        rec = registered['result']
        def call(op, args):
            return registry.handle('operator-p', _next_request(op, {
                'registration_id': rec['registration_id'], 'service_epoch': rec['service_epoch'],
                'instance_id': 'gateway-a', 'arguments': args}, identity=identity, scope=scope))
        identity_reply = call('direct.control.identity', {})
        assert identity_reply['ok'] and identity_reply['result'] == {
            'status': 'OBSERVED', 'schema_version': 1, 'account_scope': 'account', 'journal_id': h.journal.journal_id}
        denied = call('direct.control.apply', {'cause_id': 'wrong', 'mode': 'FREEZE', 'expected_journal_id': 'different'})
        assert denied['result'] == {'status': 'REQUEST_REJECTED', 'code': 'JOURNAL_ID_MISMATCH'}
        assert call('direct.control.status', {'cause_id': 'wrong'})['result']['status'] == 'NOT_FOUND'
        result = call('direct.control.apply', {'cause_id': 'freeze', 'mode': 'FREEZE', 'expected_journal_id': h.journal.journal_id})
        assert result['ok'] and result['result']['active'] is True, result
        with pytest.raises(DirectCommandJournalError, match='CONTROL_FREEZE'):
            h.journal.reserve('strategy-a', 'new', 'strategy-p', {'symbol': 'rb'})
        assert call('direct.control.status', {'cause_id': 'freeze'})['result']['cause']['version'] == 1
        assert call('direct.control.status', {'cause_id': 'absent'})['result'] == {'status':'NOT_FOUND','cause_id':'absent'}
        assert call('direct.control.resume_status', {'operation_id':'resume-1'})['result']['status'] == 'NOT_FOUND'
        listed = call('direct.control.list', {'after': '', 'limit': 10})
        assert listed['ok'] and [cause['cause_id'] for cause in listed['result']['causes']] == ['freeze']
        denied = call('direct.control.resume', {'cause_id': 'freeze', 'expected_version': 1,
            'operation_id': 'resume-1', 'expected_journal_id': 'different'})
        assert denied['result'] == {'status': 'REQUEST_REJECTED', 'code': 'JOURNAL_ID_MISMATCH'}
        args = {'cause_id': 'freeze', 'expected_version': 1, 'operation_id': 'resume-1',
                'expected_journal_id': h.journal.journal_id}
        assert call('direct.control.resume', args)['result']['active'] is False
        assert call('direct.control.resume', args)['result']['version'] == 2
        assert call('direct.control.resume_status', {'operation_id':'resume-1'})['result'] == {
            'status':'OBSERVED','cause_id':'freeze','operation_id':'resume-1','version':2,'active':False}
        assert h.journal.reserve('strategy-a', 'new', 'strategy-p', {'symbol': 'rb'}).status == 'RESERVED'
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)


@pytest.mark.parametrize('control_proof', [False, True])
def test_operator_rpc_drain_respects_control_proof(tmp_path, control_proof):
    from dataclasses import replace
    from bt_api_base.direct_write_protocol import NativeOrderIdentity, PreDispatchRecord, WriteOperation
    from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
    from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
    from bt_api_gateway.direct_dispatch import NativeDispatchOutcome, _identity
    from bt_api_py.gateway_direct_service import GatewayControlGrant, GatewayDirectGrant, GatewayDirectServiceBinding
    from tests.test_iter63_gateway_direct_owner import Harness
    from tests.unit.test_iter63_gateway_cancel_owner import owner_for
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bt_api/bt_api_gateway/tests'))
    from test_iter63_direct_cancel_journal import prepared
    h = Harness(tmp_path)
    calls = []
    def factory():
        bundle = h.factory()
        def native(target, symbol, port, scope):
            prep = prepared(target)
            record = PreDispatchRecord(WriteOperation.CANCEL, scope, 'SYN', symbol, None, target, {},
                                       prep.trading_day, prep.session_generation, 'synthetic', prep)
            marker = port.commit(record)
            calls.append(marker)
            return NativeDispatchOutcome(True, target, marker)
        cancel = GatewayDirectCancelDispatcher(GatewayDirectCancelJournal(bundle.journal), bundle.dispatcher,
                                                h.guard, 'SYN', native, lambda _: True)
        return replace(bundle, cancel_dispatcher=cancel)
    owner = owner_for(factory)
    assert owner.start(2)
    registry = None
    try:
        control = owner.submit_service_call(lambda: h.journal.enable_controls(h.guard)).result(timeout_seconds=2)
        row = h.journal.reserve('strategy-a', 'order', 'strategy-p', {'symbol': 'X', 'side': 'buy', 'quantity': '1'})
        identity = NativeOrderIdentity('SYN', 'ref', {'trading_day': '20300101', 'front_id': 1}, 1)
        order = h.journal.begin_dispatch('strategy-a', 'order', row.reservation_id, row.digest, 'strategy-p', 1,
                                         _identity(identity, 'SYN'), ('order-key',)).record
        if control_proof:
            h.journal.finish('strategy-a', 'order', row.reservation_id, row.digest, 'strategy-p', order.version,
                             _identity(identity, 'SYN'), ('order-key',), 'SENT')
            def upgrade():
                h.journal.enable_strategy_ledger(h.guard)
                h.journal.enable_account_extensions(h.guard)
                h.journal.enable_parent_authority(h.guard)
                h.journal.enable_account_facts_financial(h.guard)
                h.journal.enable_actor_stream(h.guard)
                h.journal.enable_strategy_origin(h.guard)
                h.journal.enable_control_cancel(h.guard)
            owner.submit_service_call(upgrade).result(timeout_seconds=2)
        operations = frozenset({'direct.control.apply', 'direct.control.drain_target', 'direct.control.targets', 'direct.control.target_status'})
        writes = frozenset({'direct.control.apply', 'direct.control.drain_target'})
        binding = GatewayDirectServiceBinding(h.journal, {'strategy-p': GatewayDirectGrant('strategy-a', frozenset({'X'}))},
            owner=owner, cancel_enabled=True, control=control,
            control_grants={'operator-p': GatewayControlGrant('operator-a', True, frozenset(), operations)})
        client = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator-p', 'operator-a')
        scope = WireScope('test-domain', account_ref='account')
        capability = ServiceCapability('gateway-a', 'service.direct_write', operations, writes)
        resource = ResourceGrant('gateway-a', 'service.direct_write', 'scope-a', scope, operations)
        registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(capability,),
            grants={'operator-p': (PrincipalGrant(client, (resource,)),)}, policy_version='policy',
            requirements_version='requirements', lease_seconds=10, clock=lambda: 100.0)
        for op in operations:
            registry.register_handler('gateway-a', op, lambda ctx, args, current=op: binding.handle(current, context=ctx, arguments=args))
        registered = _register(registry, principal='operator-p', identity=client, scope=scope,
            instance_id='gateway-a', port='service.direct_write', scope_ref='scope-a', operations=tuple(operations))
        assert registered['ok'], registered
        rec = registered['result']
        def call(op, args):
            return registry.handle('operator-p', _next_request(op, {'registration_id': rec['registration_id'],
                'service_epoch': rec['service_epoch'], 'instance_id': 'gateway-a', 'arguments': args}, identity=client, scope=scope))
        result = call('direct.control.apply', {'cause_id': 'drain', 'mode': 'DRAIN'})
        assert result['ok'] and result['result']['active'] is True, result
        result = call('direct.control.targets', {'cause_id': 'drain', 'after': '', 'limit': 10})
        target, = result['result']['targets']
        lookup = call('direct.control.target_status', {'cause_id':'drain','operation_id':target['operation_id']})
        assert lookup['result']['target']['state'] == 'PENDING'
        assert target['reservation_id'] == order.reservation_id
        args = {'cause_id': 'drain', 'reservation_id': order.reservation_id, 'expected_journal_id': 'different'}
        denied = call('direct.control.drain_target', args)
        assert denied['result'] == {'status': 'REQUEST_REJECTED', 'code':
                                    'JOURNAL_ID_MISMATCH' if control_proof else 'CONTROL_CANCEL_PROOF_REQUIRED'}
        assert call('direct.control.target_status', {'cause_id':'drain','operation_id':target['operation_id']})['result']['target']['state'] == 'PENDING'
        assert not calls
        args['expected_journal_id'] = h.journal.journal_id
        if control_proof:
            result = call('direct.control.drain_target', args)
            assert result['ok'], result
            assert len(calls) == 1
            assert call('direct.control.drain_target', args)['result']['status'] != 'OBSERVED'
            assert len(calls) == 1
            return
        assert call('direct.control.drain_target', args)['result']['code'] == 'CONTROL_CANCEL_PROOF_REQUIRED'
        lookup = call('direct.control.target_status', {'cause_id':'drain','operation_id':target['operation_id']})
        assert lookup['result']['target']['state'] == 'PENDING'
        assert call('direct.control.drain_target', args)['result']['code'] == 'CONTROL_CANCEL_PROOF_REQUIRED'
        assert not calls and h.journal.get('strategy-a', 'order', 'strategy-p').status == 'DISPATCHING'
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)
