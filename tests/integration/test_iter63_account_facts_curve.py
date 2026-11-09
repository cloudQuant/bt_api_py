"""Account facts through an actual ServiceHost, CURVE and account owner.

Only synthetic facts and temporary SQLite files are used; no provider session.
"""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import traceback
import queue
import threading

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.direct_parent_authority import GatewayParentAuthority
from bt_api_gateway.direct_delegation import GatewayDelegateGrant, authorize_delegated_command
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_gateway.direct_strategy_origin import authorize_strategy_origin
from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.gateway_account_facts import ACCOUNT_FACTS_OPERATIONS, GatewayAccountFactsObserver
from bt_api_py.gateway_account_comparison import GatewayAccountComparison
from bt_api_py.gateway_account_facts_client import GatewayAccountFactsClient
from bt_api_py.gateway_account_observer_client import GatewayAccountObserverRoute
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import (GatewayControlGrant, GatewayDirectGrant, GatewayDirectServiceBinding, STORAGE_OPERATIONS)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ServiceRuntimeError, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.integration.test_iter63_gateway_direct_service import ACCOUNT, CLIENT_A, CLIENT_B, DOMAIN, SERVICE
from tests.integration.test_iter63_gateway_dual_ports import _dual_client, _dual_config, _resources
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from bt_api_base.direct_write_protocol import DirectExecutionEventKind, NativeOrderIdentity
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.market_protocol import MarketInstrumentKey


def command(journal, *, strategy, key, anchor):
    row = journal.reserve(strategy, key, 'principal', {'symbol': 'rb2601', 'side': 'buy',
        'offset': 'open', 'quantity': '2'})
    native = {'exchange_name': 'CTP', 'client_order_id': key, 'session_generation': 1, 'fields_json': '{}'}
    journal.begin_dispatch(strategy, key, row.reservation_id, row.digest, 'principal', 1, native, (anchor,))


def fact(*, keys, binding, venue, trade_key=('SHFE', 'trade-1'), ref='source'):
    instrument = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb2601', 'rb2601')
    identity = NativeOrderIdentity('CTP', 'ref-a', {'trading_day': '20300101'}, 1)
    return GatewayReturnFact('acct', 'CTP', instrument, '20300101', DirectExecutionEventKind.TRADE,
        ref, identity=identity, identity_keys=keys, binding_keys=binding, venue_key=venue,
        trade_key=trade_key, side='buy', offset='open', trade_quantity='1', trade_price='10')


def test_fact_stream_actual_curve_scope_bootstrap_live_and_ack(tmp_path):
    endpoint = _free_endpoint()
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    other_public, other_secret = zmq.curve_keypair()
    scope_grant = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref)
    grants = PrincipalGrant(CLIENT_A, (
        ResourceGrant('gateway-a', 'service.direct_write', 'scope-a', scope_grant, STORAGE_OPERATIONS),
        ResourceGrant('gateway-a', 'service.account_read', 'scope-a', scope_grant, ACCOUNT_FACTS_OPERATIONS),
    ))
    other_scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_B.actor_ref)
    other_grants = PrincipalGrant(CLIENT_B, (
        ResourceGrant('gateway-a', 'service.direct_write', 'scope-a', other_scope, STORAGE_OPERATIONS),
        ResourceGrant('gateway-a', 'service.account_read', 'scope-a', other_scope, frozenset({'account.facts.status'})),
    ))
    authority = TrustedServiceAuthority(identity=SERVICE, scope=WireScope(DOMAIN), scope_ref='scope-a',
        scope_version='scope-v1', security_ref='security-a', security_version='security-v1',
        registration_policy_ref='direct-policy-v1', registration_policy_version='direct-policy-v1',
        service_requirements_ref='direct-requirements-v1', service_requirements_version='direct-requirements-v1',
        curve_public_key=server_public, curve_secret_key=server_secret,
        principals={client_public: CLIENT_A.principal_ref, other_public: CLIENT_B.principal_ref},
        grants={CLIENT_A.principal_ref: (grants,), CLIENT_B.principal_ref: (other_grants,)})
    ready, stop = queue.Queue(), threading.Event()
    (tmp_path / 'guard').mkdir()

    def serve():
        owner = host = None
        try:
            state = {}
            def factory():
                guard = LocalAccountSessionGuard(tmp_path/'guard', ACCOUNT, 'owner')
                journal = GatewayDirectCommandJournal.create(tmp_path/'facts.sqlite', ACCOUNT, generation=guard.acquire())
                ledger = GatewayDirectExecutionLedger(journal, guard)
                control = journal.enable_controls(guard)
                journal.enable_strategy_ledger(guard)
                journal.enable_account_extensions(guard)
                journal.enable_parent_authority(guard)
                command(journal, strategy=CLIENT_A.actor_ref, key='old', anchor='old-anchor')
                old = replace(fact(keys=('old-anchor',), binding=('old-anchor',), venue='old-anchor'), account_scope=ACCOUNT)
                assert ledger.ingest(old).disposition == 'ATTRIBUTED'
                journal.enable_account_facts_financial(guard)
                incoming = replace(fact(keys=('unknown',), binding=('unknown',), venue='unknown',
                                        trade_key=('SHFE', 'new-trade'), ref='PRIVATE-SOURCE'), account_scope=ACCOUNT)
                assert ledger.ingest(incoming).disposition == 'UNATTRIBUTED'
                instrument = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb2601', 'rb2601')
                payload = {'symbol': 'rb2601', 'exchange_id': 'SHFE', 'idempotency_key': 'delegated',
                           'offset': 'open', 'side': 'buy', 'quantity': '1', 'quantity_unit': 'lots'}
                actor = WireIdentity(WireRole.SERVICE, 'risk-principal', 'risk-actor')
                delegate_scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref, run_id='run')
                envelope = GatewayDelegatedCommand(ACCOUNT, CLIENT_A.actor_ref, DOMAIN, actor.principal_ref,
                    actor.actor_ref, 'risk', 'run', 'delegated', gateway_payload_digest(payload), instrument, 'grant-v1')
                grant = GatewayDelegateGrant(actor, ACCOUNT, CLIENT_A.actor_ref, DOMAIN, 'risk', 'grant-v1',
                    frozenset({instrument}), frozenset({'direct.reserve', 'direct.dispatch'}))
                context = SimpleNamespace(identity=actor, scope=delegate_scope, principal_ref=actor.principal_ref,
                                          registration_id='synthetic-registration')
                def permit(operation):
                    return authorize_delegated_command(context, grant, envelope, operation, payload,
                        registry_recheck=lambda _: True, current_grant=lambda: grant,
                        instrument_recheck=lambda item: item == instrument)
                @contextmanager
                def fence():
                    yield
                reserved = journal.reserve(CLIENT_A.actor_ref, 'delegated', actor.principal_ref, payload,
                    reservation_fence=fence, delegation_permit=permit('direct.reserve'))
                native = {'exchange_name': 'CTP', 'client_order_id': 'delegated', 'session_generation': 1,
                          'fields_json': '{}'}
                journal.begin_dispatch(CLIENT_A.actor_ref, 'delegated', reserved.reservation_id, reserved.digest,
                    actor.principal_ref, reserved.version, native, ('risk-anchor',),
                    delegation_permit=permit('direct.dispatch'), delegation_fence=fence)
                risk_fact = replace(fact(keys=('risk-anchor',), binding=('risk-anchor',), venue='risk-anchor',
                    trade_key=('SHFE', 'risk-trade')), account_scope=ACCOUNT)
                assert ledger.ingest(risk_fact).disposition == 'ATTRIBUTED'
                state['risk_reservation'] = reserved.reservation_id
                # Parent and child are registered under the same owner journal;
                # all external checks below are synthetic, no provider is contacted.
                parent = GatewayParentAuthority(journal, guard, now_ns=lambda: 100,
                    trading_day=lambda: '20300101', transfer_completed=lambda db, spec: '0')
                parent.register_parent(dict(account_scope=ACCOUNT, strategy_scope=CLIENT_A.actor_ref,
                    parent_ref='parent-1', registration_operation_id='register-parent', execution_domain_ref=DOMAIN,
                    instrument=instrument.to_dict(), quantity='10', quantity_unit='lots', side='buy',
                    offset='open', limit_price='10', creation_trading_day='20300101',
                    allowed_trading_days=['20300101'], expires_at_ns=1000, grant_revision='exec-v1',
                    target_position_ref=None, plan_digest='a'*64, plan_version='v1', time_in_force='GTD'),
                    principal='execution-principal', actor_ref='execution-actor', fence=fence, recheck=lambda: True)
                child = 'child-1'
                child_payload = OrderRequest(symbol='rb2601', side=Side.BUY, order_type=OrderType.LIMIT,
                    quantity=Decimal('1'), account_id=ACCOUNT, client_order_id=child, price=Decimal('10'),
                    idempotency_key=child, quantity_unit='lots', offset='open', exchange_id='SHFE').to_dict()
                exec_actor = WireIdentity(WireRole.SERVICE, 'execution-principal', 'execution-actor')
                exec_envelope = GatewayDelegatedCommand(ACCOUNT, CLIENT_A.actor_ref, DOMAIN, exec_actor.principal_ref,
                    exec_actor.actor_ref, 'execution', 'run', child, gateway_payload_digest(child_payload),
                    instrument, 'exec-v1', 'parent-1', child)
                exec_grant = GatewayDelegateGrant(exec_actor, ACCOUNT, CLIENT_A.actor_ref, DOMAIN, 'execution',
                    'exec-v1', frozenset({instrument}), frozenset({'direct.reserve', 'direct.dispatch'}))
                exec_context = SimpleNamespace(identity=exec_actor, scope=delegate_scope,
                    principal_ref=exec_actor.principal_ref, registration_id='execution-synthetic-registration')
                def exec_permit(operation):
                    return authorize_delegated_command(exec_context, exec_grant, exec_envelope, operation,
                        child_payload, registry_recheck=lambda _: True, current_grant=lambda: exec_grant,
                        instrument_recheck=lambda item: item == instrument, parent_recheck=parent.recheck_child)
                child_row = journal.reserve(CLIENT_A.actor_ref, child, exec_actor.principal_ref, child_payload,
                    reservation_fence=fence, delegation_permit=exec_permit('direct.reserve'))
                child_native = {'exchange_name': 'CTP', 'client_order_id': child, 'session_generation': 1,
                                'fields_json': '{}'}
                journal.begin_dispatch(CLIENT_A.actor_ref, child, child_row.reservation_id, child_row.digest,
                    exec_actor.principal_ref, child_row.version, child_native, ('exec-anchor',),
                    delegation_permit=exec_permit('direct.dispatch'), delegation_fence=fence)
                child_fact = replace(fact(keys=('exec-anchor',), binding=('exec-anchor',), venue='exec-anchor',
                    trade_key=('SHFE', 'exec-trade')), account_scope=ACCOUNT)
                assert ledger.ingest(child_fact).disposition == 'ATTRIBUTED'
                state['exec_reservation'] = child_row.reservation_id
                journal.enable_actor_stream(guard)
                journal.enable_strategy_origin(guard)
                self_payload = {'symbol': 'rb2601', 'exchange_id': 'SHFE', 'idempotency_key': 'self-order',
                    'offset': 'open', 'side': 'buy', 'quantity': '1', 'quantity_unit': 'lots'}
                self_scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref,
                                       run_id='run-strategy-a')
                self_context = SimpleNamespace(identity=CLIENT_A, scope=self_scope,
                    principal_ref=CLIENT_A.principal_ref, registration_id='strategy-registration')
                self_permit = authorize_strategy_origin(self_context, instrument, self_payload,
                    account=ACCOUNT, journal_id=journal.journal_id, generation=journal.generation,
                    service_epoch='strategy-epoch', registry_recheck=lambda _: True,
                    instrument_recheck=lambda value: value == instrument)
                self_row = journal.reserve(CLIENT_A.actor_ref, 'self-order', CLIENT_A.principal_ref, self_payload,
                    reservation_fence=fence, strategy_origin_permit=self_permit)
                self_native = {'exchange_name': 'CTP', 'client_order_id': 'self-order', 'session_generation': 1,
                               'fields_json': '{}'}
                journal.begin_dispatch(CLIENT_A.actor_ref, 'self-order', self_row.reservation_id, self_row.digest,
                    CLIENT_A.principal_ref, self_row.version, self_native, ('self-anchor',))
                own_fact = replace(fact(keys=('self-anchor',), binding=('self-anchor',), venue='self-anchor',
                    trade_key=('SHFE', 'self-trade')), account_scope=ACCOUNT)
                assert ledger.ingest(own_fact).disposition == 'ATTRIBUTED'
                state['self_reservation'] = self_row.reservation_id
                journal.enable_control_cancel(guard)
                journal.enable_account_fact_recovery(guard)
                dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, lambda *_: None, lambda _: (), lambda _: True)
                state.update(journal=journal, ledger=ledger, control=control)
                cancel = GatewayDirectCancelDispatcher(GatewayDirectCancelJournal(journal), dispatcher, guard,
                    'CTP', lambda *_: None, lambda _: True)
                return GatewayDirectOwnerResources(dispatcher, journal, guard.release, cancel_dispatcher=cancel)
            owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=5, busy_timeout_seconds=5)
            assert owner.start(10)
            journal, ledger = state['journal'], state['ledger']
            _old_binding, read_service, model = _resources(tmp_path)
            binding = GatewayDirectServiceBinding(journal,
                {CLIENT_A.principal_ref: GatewayDirectGrant(CLIENT_A.actor_ref, frozenset({'rb'})),
                 CLIENT_B.principal_ref: GatewayDirectGrant(CLIENT_B.actor_ref, frozenset({'rb'}))}, owner=owner,
                strategy_financial_enabled=True, cancel_enabled=True, control=state['control'],
                control_grants={CLIENT_A.principal_ref: GatewayControlGrant(
                    CLIENT_A.actor_ref, True, frozenset(), frozenset({'direct.control.status'}))})
            facts = GatewayAccountFactsObserver(owner, journal, ledger, account_scope=ACCOUNT,
                execution_domain_ref=DOMAIN, principals=frozenset({CLIENT_A.principal_ref}))
            comparison = GatewayAccountComparison(owner, journal, account_scope=ACCOUNT,
                principals=frozenset({CLIENT_A.principal_ref}), instruments=frozenset({
                    MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb2601', 'rb2601')}))
            config = _dual_config(endpoint).to_public_dict()
            config['components']['gateway']['settings']['options'].update(dispatch_enabled=True, control_enabled=True,
                strategy_ledger_enabled=True, account_extensions_enabled=True, account_facts_financial_enabled=True,
                cancel_enabled=True, strategy_origin_enabled=True, control_cancel_enabled=True,
                account_fact_recovery_enabled=True)
            host = ServiceHost(parse_service_config(config), authority, tmp_path/'registry', lambda *_:
                ComponentResources.borrowed({'binding': binding, 'read_service': read_service, 'account_facts': facts,
                                             'account_comparison': comparison},
                                            ready=lambda: True))
            host.start()
            ready.put(('ready', state['risk_reservation'], state['exec_reservation'], state['self_reservation'], owner, ledger))
            while not stop.is_set():
                host.serve_once(10)
        except BaseException as error:
            ready.put(('error', type(error).__name__, str(error), traceback.format_exc()))
        finally:
            if host is not None and host.state == 'started':
                host.close()
            if owner is not None:
                owner.close(10)
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    runtime = other_runtime = successor = replacement = None
    try:
        started = ready.get(timeout=20)
        assert started[0] == 'ready', started
        runtime = ThreadedServiceClientRuntime(lambda: ServiceClientRuntime(_dual_client(endpoint, CLIENT_A),
            identity=CLIENT_A, scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
            peer_resolver=lambda _: SERVICE,
            transport_factory=ZmqTransportFactory({'security-a': ClientCurveKeys(server_public, client_public, client_secret)}),
            run_id='run-strategy-a', requested_operations={
                'broker.direct_write': STORAGE_OPERATIONS, 'store.account': ACCOUNT_FACTS_OPERATIONS}))
        assert runtime.start(timeout_seconds=10).healthy
        scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref, run_id='run-strategy-a')
        route = GatewayAccountObserverRoute('store.account', SERVICE.actor_ref, 'gateway-a', 'scope-a', scope, CLIENT_A)
        client = GatewayAccountFactsClient(runtime, route=route)
        page = client.read_after(limit=4, timeout_seconds=5)
        assert page.outcome == 'OBSERVED', page
        assert page.page.progress.bootstrap_count == 1 and page.page.has_more
        assert [(e.phase, e.disposition, e.strategy_scope) for e in page.page.events] == [
            ('BOOTSTRAP', 'ATTRIBUTED', CLIENT_A.actor_ref), ('LIVE', 'UNATTRIBUTED', None),
            ('LIVE', 'ATTRIBUTED', CLIENT_A.actor_ref), ('LIVE', 'ATTRIBUTED', CLIENT_A.actor_ref)]
        direct, unknown, risk, execution = page.page.events
        assert (direct.reservation_id, direct.origin, direct.parent_ref, direct.child_id) == (None, None, None, None)
        assert (unknown.reservation_id, unknown.origin, unknown.parent_ref, unknown.child_id) == (None, None, None, None)
        assert risk.reservation_id == started[1] and risk.origin == 'risk:risk-principal'
        assert risk.parent_ref is None and risk.child_id is None
        assert (execution.reservation_id, execution.origin, execution.parent_ref, execution.child_id) == (
            started[2], 'execution:execution-principal', 'parent-1', 'child-1')
        assert 'PRIVATE-SOURCE' not in repr(page)
        assert client.status().fact_status.status == 'UNAVAILABLE'
        page2 = client.read_after(after_sequence=4, expected_epoch=page.page.progress.epoch, limit=4)
        assert page2.outcome == 'OBSERVED' and not page2.page.has_more
        own, = page2.page.events
        assert (own.reservation_id, own.origin, own.parent_ref, own.child_id) == (started[3], 'self', None, None)
        replay = client.read_after(after_sequence=4, expected_epoch=page.page.progress.epoch, limit=4)
        assert replay.outcome == 'OBSERVED' and replay.page.events == page2.page.events
        # A second live registration of the same logical consumer must not
        # acquire A's offered cursor, including when its run ID differs.
        duplicate_config = _dual_client(endpoint, CLIENT_A).to_public_dict()
        duplicate_config['clients']['gateway']['settings']['bindings'] = [
            b for b in duplicate_config['clients']['gateway']['settings']['bindings'] if b['mount'] == 'store.account']
        successor = ThreadedServiceClientRuntime(lambda: ServiceClientRuntime(parse_client_config(duplicate_config),
            identity=CLIENT_A, scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
            peer_resolver=lambda _: SERVICE, transport_factory=ZmqTransportFactory(
                {'security-a': ClientCurveKeys(server_public, client_public, client_secret)}),
            run_id='new-run', requested_operations={'store.account': ACCOUNT_FACTS_OPERATIONS}))
        assert successor.start(timeout_seconds=10).healthy
        successor_scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref, run_id='new-run')
        successor_client = GatewayAccountFactsClient(successor, route=GatewayAccountObserverRoute(
            'store.account', SERVICE.actor_ref, 'gateway-a', 'scope-a', successor_scope, CLIENT_A))
        assert client.progress().outcome != 'OBSERVED'
        assert successor_client.acknowledge(5, expected_epoch=page.page.progress.epoch).outcome != 'OBSERVED'
        runtime.close(timeout_seconds=10)
        runtime = None
        progress = successor_client.progress()
        assert progress.outcome == 'OBSERVED' and progress.progress.acknowledged_sequence == 0
        assert progress.progress.offered_sequence == 0
        assert successor_client.acknowledge(5, expected_epoch=page.page.progress.epoch).code == 'CONSUMER_REPLAY_REQUIRED'
        assert successor_client.read_after(after_sequence=4, expected_epoch=page.page.progress.epoch).code == 'CONSUMER_REPLAY_REQUIRED'
        redelivered = successor_client.read_after(limit=10, expected_epoch=page.page.progress.epoch)
        assert redelivered.outcome == 'OBSERVED' and len(redelivered.page.events) == 5
        assert redelivered.page.events[-1] == own
        # Compact while B has not ACKed. Its cursor must explicitly recover a
        # consistent local fact snapshot, not treat the retained gap as empty.
        metadata = started[4].submit_service_call(lambda: started[5].compact_account_facts()).result(timeout_seconds=5)
        assert metadata.retained_floor == 5
        gap = successor_client.read_after(after_sequence=0, expected_epoch=page.page.progress.epoch)
        assert gap.outcome == 'REQUEST_DENIED' and gap.code == 'RECOVERY_REQUIRED', gap
        info = successor_client.snapshot_info()
        assert info.outcome == 'OBSERVED' and info.snapshot_info.retained_floor == 5
        sid, digest = info.snapshot_info.snapshot_id, info.snapshot_info.snapshot_digest
        first_snapshot = successor_client.read_snapshot(sid, limit=2)
        assert first_snapshot.outcome == 'OBSERVED' and first_snapshot.snapshot.has_more
        assert successor_client.confirm_snapshot(sid, digest).code == 'SNAPSHOT_NOT_OFFERED'
        # A new registration cannot confirm B's partly/fully offered snapshot;
        # it must receive the entire snapshot from ordinal zero under its own lease.
        replacement = ThreadedServiceClientRuntime(lambda: ServiceClientRuntime(parse_client_config(duplicate_config),
            identity=CLIENT_A, scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
            peer_resolver=lambda _: SERVICE, transport_factory=ZmqTransportFactory(
                {'security-a': ClientCurveKeys(server_public, client_public, client_secret)}),
            run_id='third-run', requested_operations={'store.account': ACCOUNT_FACTS_OPERATIONS}))
        assert replacement.start(timeout_seconds=10).healthy
        replacement_scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref, run_id='third-run')
        replacement_client = GatewayAccountFactsClient(replacement, route=GatewayAccountObserverRoute(
            'store.account', SERVICE.actor_ref, 'gateway-a', 'scope-a', replacement_scope, CLIENT_A))
        assert replacement_client.confirm_snapshot(sid, digest).outcome != 'OBSERVED'
        successor.close(timeout_seconds=10)
        successor = None
        assert replacement_client.confirm_snapshot(sid, digest).code == 'CONSUMER_REPLAY_REQUIRED'
        assert replacement_client.read_snapshot(sid, after_ordinal=2).code == 'INVALID_SEQUENCE'
        ordinal, snapshot_facts = 0, []
        while True:
            item = replacement_client.read_snapshot(sid, after_ordinal=ordinal, limit=2)
            assert item.outcome == 'OBSERVED', item
            assert item.snapshot.snapshot_id == sid and item.snapshot.snapshot_digest == digest
            snapshot_facts.extend(item.snapshot.events)
            ordinal += len(item.snapshot.events)
            if not item.snapshot.has_more:
                break
        assert ordinal == info.snapshot_info.snapshot_count == 5
        assert len({fact.fact_digest for fact in snapshot_facts}) == 5
        confirmed = replacement_client.confirm_snapshot(sid, digest)
        assert confirmed.outcome == 'OBSERVED' and confirmed.snapshot_info.retained_floor == 5
        progress = replacement_client.progress()
        assert progress.outcome == 'OBSERVED' and progress.progress.acknowledged_sequence == 5
        tail = replacement_client.read_after(after_sequence=5, expected_epoch=page.page.progress.epoch)
        assert tail.outcome == 'OBSERVED' and tail.page.events == ()
        assert replacement_client.read_after(after_sequence=5, expected_epoch='0'*64).code == 'EPOCH_MISMATCH'
        other_runtime = ThreadedServiceClientRuntime(lambda: ServiceClientRuntime(_dual_client(endpoint, CLIENT_B),
            identity=CLIENT_B, scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
            peer_resolver=lambda _: SERVICE, transport_factory=ZmqTransportFactory(
                {'security-a': ClientCurveKeys(server_public, other_public, other_secret)}),
            run_id='run-strategy-b', requested_operations={'broker.direct_write': STORAGE_OPERATIONS,
                'store.account': frozenset({'account.facts.status'})}))
        assert other_runtime.start(timeout_seconds=10).healthy
        other_route = GatewayAccountObserverRoute('store.account', SERVICE.actor_ref, 'gateway-a', 'scope-a',
            WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_B.actor_ref, run_id='run-strategy-b'), CLIENT_B)
        other_client = GatewayAccountFactsClient(other_runtime, route=other_route)
        assert other_client.status().code == 'SCOPE_DENIED'
        assert other_client.read_after().outcome == 'TRANSPORT_NOT_STARTED'
        with pytest.raises(ServiceRuntimeError, match='AMBIGUOUS_OR_MISSING_ROUTE'):
            other_runtime.call('store.account', 'account.facts.read_after',
                {'after_sequence': 0, 'expected_epoch': None, 'limit': 1}, timeout_seconds=2)
    finally:
        if replacement is not None:
            replacement.close(timeout_seconds=10)
        if successor is not None:
            successor.close(timeout_seconds=10)
        if other_runtime is not None:
            other_runtime.close(timeout_seconds=10)
        if runtime is not None:
            runtime.close(timeout_seconds=10)
        stop.set()
        thread.join(15)
        assert not thread.is_alive()
