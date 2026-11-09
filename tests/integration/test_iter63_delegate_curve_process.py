"""Synthetic Risk and strategy clients over real CURVE to a spawned Gateway."""
from __future__ import annotations

import multiprocessing
import os
import queue
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import zmq
from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
from bt_api_base.direct_write_protocol import NativeOrderIdentity, PreDispatchRecord, WriteOperation
from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding, gateway_payload_digest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_control import GatewayDirectControl
from bt_api_gateway.direct_delegation import GatewayDelegateGrant
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_delegate_service_client import GatewayDelegateRoute, GatewayDelegateServiceClient
from bt_api_py.gateway_delegate_risk_host import GatewayRiskDelegateHost
from bt_api_py.gateway_delegate_actor_service import GatewayDelegateActorService
from bt_api_py.gateway_delegation_service import GatewayDelegationService, GatewayDelegationSettings
from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayControlGrant, GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.integration.test_iter63_gateway_direct_service import _client_config, _service_config, SERVICE
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_strategy_client import Host as StrategyHost
from tests.unit.test_iter63_gateway_strategy_delegated import Host as CancelHost

ACCOUNT, DOMAIN, RUN = 'account-a', 'paper:demo', 'strategy-run'
STRATEGY = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal-a', 'strategy-a')
RISK = WireIdentity(WireRole.SERVICE, 'risk-principal', 'risk-service')
SCOPE = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=STRATEGY.actor_ref, run_id=RUN)
INSTRUMENT = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
STRATEGY_OPS = frozenset({'direct.reserve', 'direct.command_status', 'direct.delegated_command_status',
                          'direct.delegated_cancel', 'direct.delegated_cancel_status'})
RISK_OPS = frozenset({'direct.delegated_reserve', 'direct.delegated_dispatch', 'direct.delegate_actor_status',
                      'direct.delegate_actor_cancel', 'direct.delegate_actor_cancel_status',
                      'direct.delegate_actor_release', 'direct.delegate_actor_release_status'})


def _server(root, endpoint, server_keys, strategy_public, risk_public, messages, controls):
    owner = host = None
    try:
        root = Path(root)
        directory = root / 'gateway-guard'
        directory.mkdir()
        holder = {}
        def factory():
            guard = LocalAccountSessionGuard(directory, ACCOUNT, 'owner')
            journal = GatewayDirectCommandJournal.create(root / 'journal.sqlite', ACCOUNT, generation=guard.acquire())
            journal.enable_controls(guard)
            journal.enable_strategy_ledger(guard)
            journal.enable_account_extensions(guard)
            holder['control'] = GatewayDirectControl(journal, guard)
            def place(payload, port, account):
                identity = NativeOrderIdentity('CTP', payload['client_order_id'], {'trading_day': '20300101'}, 1)
                marker = port.commit(PreDispatchRecord(WriteOperation.PLACE, account, 'CTP', 'rb', identity,
                    None, payload, '20300101', 1, 'synthetic'))
                return NativeDispatchOutcome(True, identity, marker)
            from iter63_claim_support import SyntheticClaimPort, claiming

            # Decision B: no dispatch without a claim port.
            dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, claiming(place),
                                                 lambda identity: ('native-' + identity.client_order_id,),
                                                 lambda _: True, claim_port=SyntheticClaimPort())
            def cancel(target, symbol, port, account):
                prepared = PreparedCancelRequest('cancel-reservation', target, symbol, '20300102', 2,
                                                 {'request_id': 12}, ('cancel-' + target.client_order_id,))
                marker = port.commit(PreDispatchRecord(WriteOperation.CANCEL, account, 'CTP', symbol, None, target,
                    {}, prepared.trading_day, prepared.session_generation, 'synthetic', prepared))
                return NativeDispatchOutcome(True, target, marker)
            cancel_dispatcher = GatewayDirectCancelDispatcher(GatewayDirectCancelJournal(journal), dispatcher,
                                                                guard, 'CTP', cancel, lambda _: True)
            holder['journal'] = journal
            return GatewayDirectOwnerResources(dispatcher, journal, guard.release, cancel_dispatcher=cancel_dispatcher)
        owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=5, busy_timeout_seconds=5)
        assert owner.start(5)
        journal = holder['journal']
        binding = GatewayDirectServiceBinding(journal, {STRATEGY.principal_ref:
            GatewayDirectGrant(STRATEGY.actor_ref, frozenset({'rb'}))}, owner=owner, cancel_enabled=True,
            control=holder['control'], control_grants={'operator': GatewayControlGrant('operator', True, frozenset(),
                frozenset({'direct.control.status'}))})
        grant = GatewayDelegateGrant(RISK, ACCOUNT, STRATEGY.actor_ref, DOMAIN, 'risk', 'grant-v1',
            frozenset({INSTRUMENT}), frozenset({'direct.reserve', 'direct.dispatch', 'direct.command_status',
                                                'direct.cancel', 'direct.cancel_status', 'direct.release'}))
        delegation = GatewayDelegationService(GatewayDelegationSettings((grant,), lambda value: value == INSTRUMENT),
            owner=owner, journal=journal, instance_id='gateway-a', scope_ref='scope-a')
        actor_service = GatewayDelegateActorService(delegation)
        template = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=STRATEGY.actor_ref)
        def resource(identity, operations):
            return (PrincipalGrant(identity, (ResourceGrant('gateway-a', 'service.direct_write', 'scope-a',
                                                              template, operations),)),)
        authority = TrustedServiceAuthority(identity=SERVICE, scope=WireScope(DOMAIN), scope_ref='scope-a',
            scope_version='scope-v1', security_ref='security-a', security_version='security-v1',
            registration_policy_ref='direct-policy-v1', registration_policy_version='direct-policy-v1',
            service_requirements_ref='direct-requirements-v1', service_requirements_version='direct-requirements-v1',
            curve_public_key=server_keys[0], curve_secret_key=server_keys[1],
            principals={strategy_public: STRATEGY.principal_ref, risk_public: RISK.principal_ref},
            grants={STRATEGY.principal_ref: resource(STRATEGY, STRATEGY_OPS), RISK.principal_ref: resource(RISK, RISK_OPS)})
        raw = _service_config(endpoint).to_public_dict()
        raw['components']['gateway']['settings']['options'].update(dispatch_enabled=True, cancel_enabled=True,
            control_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True, delegation_enabled=True)
        host = ServiceHost(parse_service_config(raw), authority, root / 'registry.sqlite',
            lambda *_: ComponentResources.borrowed({'binding': binding, 'delegation_service': delegation,
                                                     'delegate_actor_service': actor_service}, ready=lambda: True))
        host.start()
        messages.put(('ready', os.getpid(), journal.journal_id))
        while True:
            try:
                if controls.get_nowait() == 'stop':
                    break
            except queue.Empty:
                pass
            host.serve_once(10)
    except BaseException as error:
        import traceback
        messages.put(('error', type(error).__name__, str(error), traceback.format_exc()))
    finally:
        if host is not None and host.state == 'started':
            host.close()
        if owner is not None:
            owner.close(5)
        messages.put(('closed', os.getpid()))


def _risk_config(endpoint):
    raw = _service_config(_free_endpoint()).to_public_dict()
    raw['service_id'] = RISK.actor_ref
    raw['clients'] = {'gateway': {'config_schema_version': 1, 'settings': {'bindings': [{
        'mount': 'upstream.direct_write', 'service_id': SERVICE.actor_ref, 'instance_id': 'gateway-a',
        'scope_ref': 'scope-a', 'account_ref': ACCOUNT, 'transport_ref': 'client-transport'}]}}}
    raw['communication']['profiles']['client-transport'] = {'backend': 'zmq', 'config_schema_version': 1,
        'endpoints': {'rpc_connect': endpoint}, 'security_profile_ref': 'security-a'}
    return parse_service_config(raw)


def test_risk_and_strategy_delegate_over_curve_process(tmp_path):
    context = multiprocessing.get_context('spawn')
    messages, controls = context.Queue(), context.Queue()
    server, strategy_keys, risk_keys = zmq.curve_keypair(), zmq.curve_keypair(), zmq.curve_keypair()
    endpoint = _free_endpoint()
    process = context.Process(target=_server, args=(str(tmp_path), endpoint, server,
        strategy_keys[0], risk_keys[0], messages, controls))
    strategy = risk_runtime = fence_manager = None
    process.start()
    try:
        ready = messages.get(timeout=20)
        assert ready[0] == 'ready', ready
        assert ready[1] == process.pid != os.getpid()
        binding = GatewayCommandBinding(ready[2], ACCOUNT, STRATEGY.actor_ref, STRATEGY.principal_ref)
        strategy = GatewayStrategyClientSession(_client_config(endpoint, STRATEGY), binding=binding,
            route=GatewayClientRoute('broker.direct_write', SERVICE.actor_ref, 'gateway-a', 'scope-a', SCOPE, STRATEGY),
            peer=SERVICE, security_profiles={'security-a': ClientCurveKeys(server[0], *strategy_keys)},
            host=StrategyHost(), delegated_cancel_host=CancelHost(), requested_operations=STRATEGY_OPS)
        assert strategy.start(timeout_seconds=10).healthy
        config = _risk_config(endpoint)
        keys = {'security-a': ClientCurveKeys(server[0], *risk_keys)}
        dropped = []
        transport_factory = ZmqTransportFactory(keys)
        def transport(*args):
            wrapped = transport_factory(*args)
            class DropOneCancelReply:
                def request(self, request, *, timeout):
                    response = wrapped.request(request, timeout=timeout)
                    if request.header.operation == 'direct.delegate_actor_cancel' and not dropped:
                        dropped.append(True)
                        raise TimeoutError('synthetic lost actor cancel reply')
                    return response
                def close(self):
                    wrapped.close()
            return DropOneCancelReply()
        def runtime_factory():
            return ServiceClientRuntime(config, identity=RISK, scope_resolver=lambda _: SCOPE,
                peer_resolver=lambda _: SERVICE, transport_factory=transport, run_id=RUN,
                requested_operations={'upstream.direct_write': RISK_OPS})
        risk_runtime = ThreadedServiceClientRuntime(runtime_factory)
        assert risk_runtime.start(timeout_seconds=10).healthy
        from bt_api_risk.core.admission import AccountScope
        from bt_api_risk.gateway_delegate_journal import RiskGatewayDelegateJournal
        from bt_api_risk.risk_order_journal import RiskOrderJournal
        risk_journal = RiskOrderJournal(tmp_path / 'risk.sqlite', writer_id='risk')
        fence_manager = risk_journal.writer_fence(AccountScope('ctp', ACCOUNT, 'simnow'))
        risk_fence = fence_manager.__enter__()
        route = GatewayDelegateRoute('upstream.direct_write', SERVICE.actor_ref, 'gateway-a', 'scope-a', SCOPE, RISK)
        risk_host = GatewayRiskDelegateHost(RiskGatewayDelegateJournal(risk_journal, risk_fence), route=route)
        client = GatewayDelegateServiceClient(risk_runtime, route=route, host=risk_host)
        request = OrderRequest(symbol='rb', side=Side.BUY, order_type=OrderType.LIMIT, quantity=Decimal('1'),
            price=Decimal('10'), account_id=ACCOUNT, client_order_id='client', idempotency_key='order-1',
            offset='open', quantity_unit='lots', exchange_id='SHFE')
        command = GatewayDelegatedCommand(ACCOUNT, STRATEGY.actor_ref, DOMAIN, RISK.principal_ref, RISK.actor_ref,
            'risk', RUN, 'order-1', gateway_payload_digest(request.to_dict()), INSTRUMENT, 'grant-v1')
        assert client.reserve(command, request).outcome == 'OBSERVED'
        assert client.dispatch('order-1').outcome == 'QUEUED'
        assert risk_host.load_delegate_dispatch_marker('order-1') is not None
        facade = GatewayBtApi(strategy, exchange_name='CTP')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            observed = facade.delegated_command_status('CTP', 'order-1')
            if observed.observation and observed.observation.state == 'SENT':
                break
            time.sleep(0.01)
        assert observed.observation.state == 'SENT' and observed.observation.actor_principal == RISK.principal_ref
        assert client.reserve(command, request).observation.state == 'SENT'
        assert facade.cancel_delegated_order('CTP', observed.observation, operation_id='cancel-1').outcome in {'QUEUED', 'OBSERVED'}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            cancel = facade.delegated_cancel_status('CTP', 'cancel-1')
            if cancel.observation and cancel.observation.state == 'SENT':
                break
            time.sleep(0.01)
        assert cancel.observation.state == 'SENT' and cancel.observation.target_actor_principal == RISK.principal_ref
        request2 = replace(request, client_order_id='client-2', idempotency_key='order-2')
        command2 = replace(command, request_key='order-2', payload_digest=gateway_payload_digest(request2.to_dict()))
        assert client.reserve(command2, request2).outcome == 'OBSERVED'
        assert client.dispatch('order-2').outcome == 'QUEUED'
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            own = client.actor_status('order-2')
            if own.observation and own.observation.command.state == 'SENT':
                break
            time.sleep(0.01)
        assert own.observation.command.state == 'SENT' and own.observation.native_identity_digest
        own_cancel = client.cancel_actor(own.observation, operation_id='risk-cancel-2')
        assert own_cancel.outcome == 'UNKNOWN' and dropped == [True], own_cancel
        assert client.cancel_actor(own.observation, operation_id='risk-cancel-2').outcome in {'OBSERVED', 'UNKNOWN'}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            own_cancel = client.actor_cancel_status('risk-cancel-2')
            if own_cancel.cancel and own_cancel.cancel.state == 'SENT':
                break
            time.sleep(0.01)
        assert own_cancel.cancel.state == 'SENT' and own_cancel.cancel.target_actor_principal == RISK.principal_ref
        request3 = replace(request, client_order_id='client-3', idempotency_key='order-3')
        command3 = replace(command, request_key='order-3', payload_digest=gateway_payload_digest(request3.to_dict()))
        assert client.reserve(command3, request3).outcome == 'OBSERVED'
        pending = client.actor_status('order-3')
        assert pending.observation.command.state == 'RESERVED'
        released = client.release_actor(pending.observation, operation_id='risk-release-3')
        assert released.outcome == 'OBSERVED' and released.state == 'RELEASED', released
        assert client.actor_release_status('risk-release-3').state == 'RELEASED'
        fence_manager.__exit__(None, None, None)
        fence_manager = None
        restarted = RiskOrderJournal(tmp_path / 'risk.sqlite', writer_id='risk-restarted')
        with restarted.writer_fence(AccountScope('ctp', ACCOUNT, 'simnow')) as resumed_fence:
            resumed = GatewayRiskDelegateHost(RiskGatewayDelegateJournal(restarted, resumed_fence), route=route)
            assert resumed.load_delegate_intent('order-1').command == command
            assert resumed.load_delegate_observation('order-1').state == 'SENT'
            assert resumed.load_delegate_dispatch_marker('order-1')[1] == command.payload_digest
            assert resumed.load_delegate_cancel_intent('risk-cancel-2').request_key == 'order-2'
            assert resumed.load_delegate_cancel_observation('risk-cancel-2').state == 'SENT'
            assert resumed.load_delegate_cancel_marker('risk-cancel-2')[0] == 'order-2'
            assert resumed.load_delegate_release_intent('risk-release-3').request_key == 'order-3'
            assert resumed.load_delegate_release_marker('risk-release-3')[0] == 'order-3'
    finally:
        if risk_runtime is not None:
            risk_runtime.close(timeout_seconds=5)
        if strategy is not None:
            strategy.close(timeout_seconds=5)
        if fence_manager is not None:
            fence_manager.__exit__(None, None, None)
        controls.put('stop')
        process.join(10)
        assert not process.is_alive() and process.exitcode == 0
