"""Independent delegated actor cursor over a spawned synthetic CURVE Gateway."""
from __future__ import annotations

import multiprocessing
import os
import queue
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import zmq
from bt_api_base.direct_write_protocol import DirectExecutionEventKind, DirectOrderStatus, NativeOrderIdentity
from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding, gateway_payload_digest
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_control import GatewayDirectControl
from bt_api_gateway.direct_delegation import GatewayDelegateGrant, authorize_delegated_command
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.gateway_delegate_actor_stream_client import GatewayDelegateActorStreamClient
from bt_api_py.gateway_delegate_actor_stream_service import ACTOR_STREAM_OPERATIONS, GatewayDelegateActorStreamService
from bt_api_py.gateway_delegate_service_client import GatewayDelegateRoute
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
from tests.integration.test_iter63_delegate_curve_process import ACCOUNT, DOMAIN, INSTRUMENT, RISK, RUN, SCOPE, STRATEGY, _risk_config
from tests.integration.test_iter63_gateway_direct_service import SERVICE, _client_config, _service_config
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_strategy_client import Host as StrategyHost
from tests.unit.test_iter63_gateway_strategy_delegated import Host as CancelHost

STRATEGY_OPS = frozenset({'direct.reserve', 'direct.command_status'})


@contextmanager
def _fence():
    yield


def _server(root, endpoint, server_keys, client_public, strategy_public, ready, control):
    owner = host = None
    try:
        root = Path(root)
        (root / 'guard').mkdir()
        grant = GatewayDelegateGrant(RISK, ACCOUNT, STRATEGY.actor_ref, DOMAIN, 'risk', 'grant-v1',
            frozenset({INSTRUMENT}), frozenset({'direct.reserve', 'direct.dispatch', 'direct.command_status'}))
        settings = GatewayDelegationSettings((grant,), lambda item: item == INSTRUMENT)
        state = {}
        def factory():
            guard = LocalAccountSessionGuard(root / 'guard', ACCOUNT, 'owner')
            journal = GatewayDirectCommandJournal.create(root / 'journal', ACCOUNT, generation=guard.acquire())
            journal.enable_controls(guard)
            journal.enable_strategy_ledger(guard)
            journal.enable_account_extensions(guard)
            journal.enable_parent_authority(guard)
            journal.enable_account_facts_financial(guard)
            journal.enable_actor_stream(guard)
            ledger = GatewayDirectExecutionLedger(journal, guard)
            payload = {'symbol': 'rb', 'exchange_id': 'SHFE', 'idempotency_key': 'key',
                       'offset': 'open', 'side': 'buy', 'quantity': '1', 'quantity_unit': 'lots'}
            command = GatewayDelegatedCommand(ACCOUNT, STRATEGY.actor_ref, DOMAIN, RISK.principal_ref, RISK.actor_ref,
                'risk', RUN, 'key', gateway_payload_digest(payload), INSTRUMENT, 'grant-v1')
            context = SimpleNamespace(identity=RISK, scope=SCOPE, principal_ref=RISK.principal_ref,
                                      registration_id='synthetic-internal')
            def permit(operation):
                return authorize_delegated_command(context, grant, command, operation, payload,
                    registry_recheck=lambda _: True, current_grant=lambda: grant,
                    instrument_recheck=lambda item: item == INSTRUMENT)
            record = journal.reserve(STRATEGY.actor_ref, 'key', RISK.principal_ref, payload,
                reservation_fence=_fence, delegation_permit=permit('direct.reserve'))
            native = {'exchange_name': 'CTP', 'client_order_id': 'key', 'session_generation': 1, 'fields_json': '{}'}
            journal.begin_dispatch(STRATEGY.actor_ref, 'key', record.reservation_id, record.digest,
                RISK.principal_ref, 1, native, ('anchor',), delegation_permit=permit('direct.dispatch'),
                delegation_fence=_fence)
            fact = GatewayReturnFact(ACCOUNT, 'CTP', INSTRUMENT, '20300101', DirectExecutionEventKind.ORDER,
                'synthetic', identity=NativeOrderIdentity('CTP', 'key', {'trading_day': '20300101'}, 1),
                identity_keys=('anchor',), binding_keys=('anchor',), status=DirectOrderStatus.ACCEPTED)
            assert ledger.ingest(fact).disposition == 'ATTRIBUTED'
            state.update(journal=journal, ledger=ledger, control=GatewayDirectControl(journal, guard))
            dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, lambda *_: None, lambda _: (), lambda _: True)
            return GatewayDirectOwnerResources(dispatcher, journal, guard.release)
        owner = GatewayDirectOwner(factory, queue_size=8, max_queue_wait_seconds=5, busy_timeout_seconds=5)
        assert owner.start(10)
        journal = state['journal']
        binding = GatewayDirectServiceBinding(journal, {STRATEGY.principal_ref:
            GatewayDirectGrant(STRATEGY.actor_ref, frozenset({'rb'}))}, owner=owner, control=state['control'],
            control_grants={'operator': GatewayControlGrant('operator', True, frozenset(),
                                                           frozenset({'direct.control.status'}))})
        delegation = GatewayDelegationService(settings, owner=owner, journal=journal,
                                              instance_id='gateway-a', scope_ref='scope-a')
        stream = GatewayDelegateActorStreamService(owner, journal, state['ledger'], settings)
        template = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=STRATEGY.actor_ref)
        principal = PrincipalGrant(RISK, (ResourceGrant('gateway-a', 'service.direct_write', 'scope-a',
                                                       template, ACTOR_STREAM_OPERATIONS),))
        strategy_grant = PrincipalGrant(STRATEGY, (ResourceGrant('gateway-a', 'service.direct_write', 'scope-a',
                                                                template, STRATEGY_OPS),))
        authority = TrustedServiceAuthority(identity=SERVICE, scope=WireScope(DOMAIN), scope_ref='scope-a',
            scope_version='scope-v1', security_ref='security-a', security_version='security-v1',
            registration_policy_ref='direct-policy-v1', registration_policy_version='direct-policy-v1',
            service_requirements_ref='direct-requirements-v1', service_requirements_version='direct-requirements-v1',
            curve_public_key=server_keys[0], curve_secret_key=server_keys[1],
            principals={client_public: RISK.principal_ref, strategy_public: STRATEGY.principal_ref},
            grants={RISK.principal_ref: (principal,), STRATEGY.principal_ref: (strategy_grant,)})
        raw = _service_config(endpoint).to_public_dict()
        raw['components']['gateway']['settings']['options'].update(dispatch_enabled=True, control_enabled=True,
            strategy_ledger_enabled=True, account_extensions_enabled=True, delegation_enabled=True,
            actor_stream_enabled=True)
        host = ServiceHost(parse_service_config(raw), authority, root / 'registry', lambda *_:
            ComponentResources.borrowed({'binding': binding, 'delegation_service': delegation,
                'delegate_actor_stream': stream}, ready=lambda: True))
        host.start()
        ready.put(('ready', os.getpid(), journal.journal_id))
        while True:
            try:
                if control.get_nowait() == 'stop':
                    break
            except queue.Empty:
                pass
            host.serve_once(10)
    except BaseException as exc:
        import traceback
        ready.put(('error', type(exc).__name__, str(exc), traceback.format_exc()))
    finally:
        if host is not None and host.state == 'started':
            host.close()
        if owner is not None:
            owner.close(10)


def test_actor_stream_spawned_gateway_curve_and_independent_ack(tmp_path):
    ctx = multiprocessing.get_context('spawn')
    ready, control = ctx.Queue(), ctx.Queue()
    server_keys, client_keys, strategy_keys = zmq.curve_keypair(), zmq.curve_keypair(), zmq.curve_keypair()
    endpoint = _free_endpoint()
    process = ctx.Process(target=_server, args=(str(tmp_path), endpoint, server_keys, client_keys[0], strategy_keys[0], ready, control))
    runtime = strategy = None
    process.start()
    try:
        started = ready.get(timeout=30)
        assert started[0] == 'ready', '\n'.join(map(str, started))
        assert started[1] == process.pid != os.getpid()
        strategy = GatewayStrategyClientSession(_client_config(endpoint, STRATEGY),
            binding=GatewayCommandBinding(started[2], ACCOUNT, STRATEGY.actor_ref, STRATEGY.principal_ref),
            route=GatewayClientRoute('broker.direct_write', SERVICE.actor_ref, 'gateway-a', 'scope-a', SCOPE, STRATEGY),
            peer=SERVICE, security_profiles={'security-a': ClientCurveKeys(server_keys[0], *strategy_keys)},
            host=StrategyHost(), delegated_cancel_host=CancelHost(), requested_operations=STRATEGY_OPS)
        assert strategy.start(timeout_seconds=10).healthy
        config = _risk_config(endpoint)
        runtime = ThreadedServiceClientRuntime(lambda: ServiceClientRuntime(config, identity=RISK,
            scope_resolver=lambda _: SCOPE, peer_resolver=lambda _: SERVICE,
            transport_factory=ZmqTransportFactory({'security-a': ClientCurveKeys(server_keys[0], *client_keys)}),
            run_id=RUN, requested_operations={'upstream.direct_write': ACTOR_STREAM_OPERATIONS}))
        assert runtime.start(timeout_seconds=10).healthy
        route = GatewayDelegateRoute('upstream.direct_write', SERVICE.actor_ref, 'gateway-a', 'scope-a', SCOPE, RISK)
        client = GatewayDelegateActorStreamClient(runtime, route=route, grant_revision='grant-v1')
        first = client.read_after()
        assert first.outcome == 'OBSERVED', first
        assert len(first.events) == 1 and first.events[0].origin == 'risk:' + RISK.principal_ref
        assert first.events[0].on_behalf_of == STRATEGY.actor_ref
        assert first.progress.acknowledged_sequence == 0
        assert client.acknowledge(first.events[0].event.sequence, expected_epoch=first.progress.epoch).outcome == 'OBSERVED'
        assert client.progress().progress.acknowledged_sequence == first.events[0].event.sequence
        assert client.read_after(after_sequence=first.events[0].event.sequence, expected_epoch='0'*64).code == 'EPOCH_MISMATCH'
        # A previously authenticated service must stop observing after the
        # original strategy run's registration is gone.
        assert strategy.close(timeout_seconds=10).state == 'closed'
        strategy = None
        assert client.progress().outcome != 'OBSERVED'
    finally:
        if runtime is not None:
            runtime.close(timeout_seconds=10)
        if strategy is not None:
            strategy.close(timeout_seconds=10)
        control.put('stop')
        process.join(20)
        assert not process.is_alive() and process.exitcode == 0
