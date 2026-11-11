"""Stand-in Risk and Execution services act for one strategy through a spawned loopback Gateway.

63.13 base (D63-56): delegated ordering with ``on_behalf_of`` attribution, cancels, the
account observation stream and over-authority refusals, over real CURVE to a separate
process. INTEGRATION_SIM evidence only: synthetic dispatcher and return facts, temporary
SQLite files; no provider session and no account.
"""
from __future__ import annotations

import multiprocessing
import os
import queue
import time
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import zmq
from bt_api_base.account_protocol import (
    AccountBalance,
    AccountKey,
    AccountObservation,
    AccountResourceKey,
)
from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
from bt_api_base.direct_write_protocol import (
    DirectExecutionEventKind,
    DirectOrderStatus,
    NativeOrderIdentity,
    PreDispatchRecord,
    WriteOperation,
)
from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding, gateway_payload_digest
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.gateway_account_observer import ACCOUNT_OBSERVER_OPERATIONS
from bt_api_py.gateway_account_observer_client import (
    GatewayAccountObserverClient,
    GatewayAccountObserverRoute,
)
from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_delegate_actor_service import OPERATIONS as ACTOR_OPERATIONS
from bt_api_py.gateway_delegate_service_client import (
    GatewayDelegateRoute,
    GatewayDelegateServiceClient,
)
from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_direct_service import EVENT_OPERATIONS
from bt_api_py.gateway_parent_client import GatewayParentServiceClient
from bt_api_py.gateway_parent_service import OPERATIONS as PARENT_OPERATIONS
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime
from tests.integration.test_iter63_gateway_direct_service import (
    SERVICE,
    _client_config,
    _service_config,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_strategy_client import Host as StrategyHost
from tests.unit.test_iter63_gateway_strategy_delegated import Host as CancelHost

ACCOUNT, DOMAIN, RUN = 'account-a', 'paper:demo', 'strategy-run'
STRATEGY = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal-a', 'strategy-a')
RISK = WireIdentity(WireRole.SERVICE, 'risk-principal', 'risk-service')
EXECUTION = WireIdentity(WireRole.SERVICE, 'execution-principal', 'execution-service')
OBSERVER = WireIdentity(WireRole.SERVICE, 'observer-principal', 'observer-service')
SCOPE = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=STRATEGY.actor_ref, run_id=RUN)
ACCOUNT_ONLY = WireScope(DOMAIN, account_ref=ACCOUNT)
INSTRUMENT = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
OUTSIDE = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'hc', 'hc')
LOGICAL = frozenset({'direct.reserve', 'direct.dispatch', 'direct.command_status', 'direct.cancel',
                     'direct.cancel_status', 'direct.release'})
DELEGATE_OPS = frozenset({'direct.delegated_reserve', 'direct.delegated_dispatch'}) | ACTOR_OPERATIONS
STRATEGY_OPS = frozenset({'direct.reserve', 'direct.command_status', 'direct.delegated_command_status',
                          'direct.delegated_cancel', 'direct.delegated_cancel_status'}) | EVENT_OPERATIONS
TRADING_DAY = '20300101'


def _read_service(root, principals):
    """Account read port resources the Host requires; the observer itself never uses them."""
    from bt_api_gateway.account_read_model import GatewayAccountReadModel
    from bt_api_gateway.event_journal import GatewayEventJournal
    from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
    from bt_api_gateway.service_component import GatewayReadService, GatewayServiceAccountScope

    events = GatewayEventJournal(root / 'account.sqlite')
    model = GatewayAccountReadModel(events,
        account_key=AccountKey('synthetic', 'venue', 'test', ACCOUNT), journal_account_scope=ACCOUNT,
        state_scope='state-a', required_coverage=(AccountResourceKey('future', 'main'),),
        assert_current_generation=lambda _: None, max_staleness_seconds=30)
    now = datetime.now(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')
    resource = AccountResourceKey('future', 'main')
    model.start_generation('gen-1')
    model.finish_refresh(model.begin_refresh(), AccountObservation(
        account_key=model.account_key, observation_id='offline-observation', coverage=(resource,),
        balances=(AccountBalance(resource, 'CNY', '100', '90', '10', now),), positions=(), valuation=None,
        started_at_utc=now, completed_at_utc=now, consistency='multi_query', balances_complete=True,
        positions_complete=True, open_orders=None, open_orders_complete=False))
    account = GatewayServiceAccountScope(account_ref=ACCOUNT, execution_domain_ref=DOMAIN,
        journal_account_scope=ACCOUNT, account_read_strategy_scope='state-a')
    return GatewayReadService(events, accounts={ACCOUNT: account},
        principals={item: GatewayPrincipal(item, frozenset({ACCOUNT}), frozenset({'state-a'}),
                                           frozenset({GatewayCommandKind.READ})) for item in principals},
        state_worker=model)


def _server(root, endpoint, server_keys, publics, messages, controls):
    owner = host = None
    try:
        from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
        from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
        from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
        from bt_api_gateway.direct_control import GatewayDirectControl
        from bt_api_gateway.direct_delegation import GatewayDelegateGrant
        from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome
        from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
        from bt_api_gateway.direct_parent_authority import GatewayParentAuthority
        from bt_api_gateway.session_guard import LocalAccountSessionGuard

        from bt_api_py.gateway_account_comparison import GatewayAccountComparison
        from bt_api_py.gateway_account_observer import GatewayAccountObserver
        from bt_api_py.gateway_delegate_actor_service import GatewayDelegateActorService
        from bt_api_py.gateway_delegation_service import (
            GatewayDelegationService,
            GatewayDelegationSettings,
        )
        from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
        from bt_api_py.gateway_direct_service import (
            GatewayControlGrant,
            GatewayDirectGrant,
            GatewayDirectServiceBinding,
        )
        from bt_api_py.gateway_execution_stream_service import GatewayExecutionStreamService
        from bt_api_py.gateway_parent_service import (
            GatewayParentGrant,
            GatewayParentService,
            GatewayParentSettings,
        )
        from bt_api_py.service_components import ComponentResources
        from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
        from bt_api_py.service_registry import (
            PrincipalGrant,
            ResourceGrant,
            ServiceDispatchAdmission,
        )

        root = Path(root)
        (root / 'guard').mkdir()
        holder = {}

        def factory():
            guard = LocalAccountSessionGuard(root / 'guard', ACCOUNT, 'owner')
            journal = GatewayDirectCommandJournal.create(root / 'journal.sqlite', ACCOUNT, generation=guard.acquire())
            journal.enable_controls(guard)
            journal.enable_strategy_ledger(guard)
            journal.enable_account_extensions(guard)
            journal.enable_parent_authority(guard)
            holder['control'] = GatewayDirectControl(journal, guard)
            holder['ledger'] = GatewayDirectExecutionLedger(journal, guard)
            holder['parent'] = GatewayParentAuthority(journal, guard, now_ns=time.time_ns,
                trading_day=lambda: TRADING_DAY, transfer_completed=lambda _db, _spec: '0')

            def place(payload, port, account):
                identity = NativeOrderIdentity('CTP', payload['client_order_id'], {'trading_day': TRADING_DAY}, 1)
                marker = port.commit(PreDispatchRecord(WriteOperation.PLACE, account, 'CTP', payload['symbol'],
                    identity, None, payload, TRADING_DAY, 1, 'synthetic'))
                return NativeDispatchOutcome(True, identity, marker)
            from iter63_claim_support import SyntheticClaimPort, claiming

            dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, claiming(place),
                lambda identity: ('native-' + identity.client_order_id,), lambda _: True,
                claim_port=SyntheticClaimPort())

            def cancel(target, symbol, port, account):
                prepared = PreparedCancelRequest('cancel-' + target.client_order_id, target, symbol, TRADING_DAY, 1,
                                                 {'request_id': 12}, ('cancel-' + target.client_order_id,))
                marker = port.commit(PreDispatchRecord(WriteOperation.CANCEL, account, 'CTP', symbol, None, target,
                    {}, prepared.trading_day, prepared.session_generation, 'synthetic', prepared))
                return NativeDispatchOutcome(True, target, marker)
            cancel_dispatcher = GatewayDirectCancelDispatcher(GatewayDirectCancelJournal(journal), dispatcher,
                                                              guard, 'CTP', cancel, lambda _: True)
            holder['journal'] = journal
            return GatewayDirectOwnerResources(dispatcher, journal, guard.release, cancel_dispatcher=cancel_dispatcher)

        owner = GatewayDirectOwner(factory, queue_size=8, max_queue_wait_seconds=5, busy_timeout_seconds=5)
        assert owner.start(10)
        journal, ledger, parent = holder['journal'], holder['ledger'], holder['parent']
        stream = GatewayExecutionStreamService(ledger, journal, {STRATEGY.principal_ref: STRATEGY.actor_ref},
                                               execution_domain_ref=DOMAIN)
        binding = GatewayDirectServiceBinding(journal, {STRATEGY.principal_ref:
            GatewayDirectGrant(STRATEGY.actor_ref, frozenset({'rb'}))}, owner=owner, cancel_enabled=True,
            execution_stream=stream, execution_domain_ref=DOMAIN, control=holder['control'],
            control_grants={'operator': GatewayControlGrant('operator', True, frozenset(),
                                                           frozenset({'direct.control.status'}))})
        delegates = (
            GatewayDelegateGrant(RISK, ACCOUNT, STRATEGY.actor_ref, DOMAIN, 'risk', 'risk-v1',
                                 frozenset({INSTRUMENT}), LOGICAL),
            GatewayDelegateGrant(EXECUTION, ACCOUNT, STRATEGY.actor_ref, DOMAIN, 'execution', 'exec-v1',
                                 frozenset({INSTRUMENT}), LOGICAL))
        delegation = GatewayDelegationService(GatewayDelegationSettings(delegates, lambda value: value == INSTRUMENT,
            parent_recheck=parent.recheck_child), owner=owner, journal=journal,
            instance_id='gateway-a', scope_ref='scope-a')
        parent_service = GatewayParentService(GatewayParentSettings((GatewayParentGrant(EXECUTION, ACCOUNT,
            STRATEGY.actor_ref, DOMAIN, 'execution', 'exec-v1', frozenset({INSTRUMENT}), PARENT_OPERATIONS),)),
            owner=owner, journal=journal, authority=parent, instance_id='gateway-a', scope_ref='scope-a')

        def current_parent_grant(context, parent_operation):
            # Same resolution as the CTP profile composition (ctp_gateway_trading).
            admission = context.dispatch_admission
            if type(admission) is not ServiceDispatchAdmission or not admission.matches_context(context):
                return None
            registry = admission._registry
            with registry._lock:
                row = registry._db.execute('SELECT scope_ref FROM sessions WHERE registration_id=?',
                                           (context.registration_id,)).fetchone()
                if (row is None or parent_operation not in {'direct.parent.cancel', 'direct.parent.query'}
                        or parent_operation != admission._binding.parent_operation):
                    return None
                return registry.gateway_parent_grant(instance_id='gateway-a', scope_ref=row['scope_ref'],
                    identity=context.identity, scope=context.scope, operation=parent_operation)
        actor = GatewayDelegateActorService(delegation, release_target_recheck=parent.check_release_target,
            parent_cancel_recheck=parent.check_cancel_target, parent_grant_resolver=current_parent_grant)
        observers = frozenset({RISK.principal_ref, EXECUTION.principal_ref, OBSERVER.principal_ref})
        observer = GatewayAccountObserver(owner, journal, ledger, account_scope=ACCOUNT, execution_domain_ref=DOMAIN,
                                          principals=observers)
        comparison = GatewayAccountComparison(owner, journal, account_scope=ACCOUNT, principals=frozenset(),
                                              instruments=frozenset({INSTRUMENT}))
        read_service = _read_service(root, observers)
        template = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=STRATEGY.actor_ref)

        def direct(operations):
            return ResourceGrant('gateway-a', 'service.direct_write', 'scope-a', template, operations)
        observe = ResourceGrant('gateway-a', 'service.account_read', 'scope-a', ACCOUNT_ONLY,
                                ACCOUNT_OBSERVER_OPERATIONS)
        grants = {STRATEGY.principal_ref: (PrincipalGrant(STRATEGY, (direct(STRATEGY_OPS),)),),
                  RISK.principal_ref: (PrincipalGrant(RISK, (direct(DELEGATE_OPS), observe)),),
                  EXECUTION.principal_ref: (PrincipalGrant(EXECUTION, (direct(DELEGATE_OPS | PARENT_OPERATIONS),
                                                                       observe)),),
                  OBSERVER.principal_ref: (PrincipalGrant(OBSERVER, (observe,)),)}
        authority = TrustedServiceAuthority(identity=SERVICE, scope=WireScope(DOMAIN), scope_ref='scope-a',
            scope_version='scope-v1', security_ref='security-a', security_version='security-v1',
            registration_policy_ref='direct-policy-v1', registration_policy_version='direct-policy-v1',
            service_requirements_ref='direct-requirements-v1', service_requirements_version='direct-requirements-v1',
            curve_public_key=server_keys[0], curve_secret_key=server_keys[1], principals=publics, grants=grants)
        raw = _service_config(endpoint).to_public_dict()
        raw['components']['gateway']['settings']['bind_ports'].append('service.account_read')
        raw['components']['gateway']['settings']['options'].update(dispatch_enabled=True, cancel_enabled=True,
            control_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True,
            delegation_enabled=True, parent_authority_enabled=True)
        host = ServiceHost(parse_service_config(raw), authority, root / 'registry.sqlite',
            lambda *_: ComponentResources.borrowed({'binding': binding, 'delegation_service': delegation,
                'delegate_actor_service': actor, 'parent_service': parent_service, 'read_service': read_service,
                'account_observer': observer, 'account_comparison': comparison}, ready=lambda: True))
        host.start()
        messages.put(('ready', os.getpid(), journal.journal_id))
        while True:
            try:
                item = controls.get_nowait()
            except queue.Empty:
                item = None
            if item == 'stop':
                break
            if item is not None:
                # A synthetic venue return for one dispatched order (never a provider call).
                client_order_id, status = item
                fact = GatewayReturnFact(ACCOUNT, 'CTP', INSTRUMENT, TRADING_DAY, DirectExecutionEventKind.ORDER,
                    'synthetic-' + status + '-' + client_order_id,
                    identity=NativeOrderIdentity('CTP', client_order_id, {'trading_day': TRADING_DAY}, 1),
                    identity_keys=('native-' + client_order_id,), binding_keys=('native-' + client_order_id,),
                    status=DirectOrderStatus(status))
                result = owner.submit_service_call(lambda fact=fact: ledger.ingest(fact)).result(timeout_seconds=5)
                messages.put(('ingested', client_order_id, result.disposition))
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


class MemoryDelegateHost:
    """Stand-in service storage; a real Risk or Execution service owns a durable store."""

    def __init__(self):
        self.values = {}

    def _save(self, kind, key, value):
        self.values[(kind, key)] = value
        return value

    def _load(self, kind, key):
        return self.values.get((kind, key))

    def save_delegate_intent(self, intent):
        return self._save('intent', intent.command.request_key, intent)

    def load_delegate_intent(self, key):
        return self._load('intent', key)

    def save_delegate_observation(self, key, value):
        return self._save('observation', key, value)

    def load_delegate_observation(self, key):
        return self._load('observation', key)

    def save_delegate_dispatch_marker(self, key, value):
        return self._save('dispatch', key, value)

    def load_delegate_dispatch_marker(self, key):
        return self._load('dispatch', key)

    def save_delegate_cancel_intent(self, plan):
        return self._save('cancel_intent', plan.operation_id, plan)

    def load_delegate_cancel_intent(self, key):
        return self._load('cancel_intent', key)

    def save_delegate_cancel_observation(self, key, value):
        return self._save('cancel_observation', key, value)

    def load_delegate_cancel_observation(self, key):
        return self._load('cancel_observation', key)

    def save_delegate_cancel_marker(self, key, value):
        return self._save('cancel_marker', key, value)

    def load_delegate_cancel_marker(self, key):
        return self._load('cancel_marker', key)

    def save_delegate_release_intent(self, plan):
        return self._save('release_intent', plan.operation_id, plan)

    def load_delegate_release_intent(self, key):
        return self._load('release_intent', key)

    def save_delegate_release_marker(self, key, value):
        return self._save('release_marker', key, value)

    def load_delegate_release_marker(self, key):
        return self._load('release_marker', key)

    def prepare_parent_dispatch(self, spec, operation_id, kind, expected_version):
        key = (spec['parent_ref'], operation_id)
        if ('parent', key) in self.values:
            return False
        self.values[('parent', key)] = (kind, expected_version)
        return True


def _service_client(endpoint, identity, mount):
    raw = _service_config(_free_endpoint()).to_public_dict()
    raw['service_id'] = identity.actor_ref
    raw['clients'] = {'gateway': {'config_schema_version': 1, 'settings': {'bindings': [{
        'mount': mount, 'service_id': SERVICE.actor_ref, 'instance_id': 'gateway-a',
        'scope_ref': 'scope-a', 'account_ref': ACCOUNT, 'transport_ref': 'client-transport'}]}}}
    raw['communication']['profiles']['client-transport'] = {'backend': 'zmq', 'config_schema_version': 1,
        'endpoints': {'rpc_connect': endpoint}, 'security_profile_ref': 'security-a'}
    return parse_service_config(raw)


def _runtime(endpoint, server_public, identity, keys, mount, operations, scope):
    config = _service_client(endpoint, identity, mount)
    curve = {'security-a': ClientCurveKeys(server_public, *keys)}
    return ThreadedServiceClientRuntime(lambda: ServiceClientRuntime(config, identity=identity,
        scope_resolver=lambda _: scope, peer_resolver=lambda _: SERVICE,
        transport_factory=ZmqTransportFactory(curve), run_id=RUN, requested_operations={mount: operations}))


def _request(key, client_order_id, symbol='rb'):
    return OrderRequest(symbol=symbol, side=Side.BUY, order_type=OrderType.LIMIT, quantity=Decimal('1'),
        price=Decimal('10'), account_id=ACCOUNT, client_order_id=client_order_id, idempotency_key=key,
        offset='open', quantity_unit='lots', exchange_id='SHFE')


def _until(read, done, seconds=5.0):
    deadline = time.monotonic() + seconds
    while True:
        value = read()
        if done(value) or time.monotonic() >= deadline:
            return value
        time.sleep(0.01)


def _spawn(tmp_path):
    context = multiprocessing.get_context('spawn')
    messages, controls = context.Queue(), context.Queue()
    keys = {name: zmq.curve_keypair() for name in ('server', 'strategy', 'risk', 'execution', 'observer')}
    publics = {keys['strategy'][0]: STRATEGY.principal_ref, keys['risk'][0]: RISK.principal_ref,
               keys['execution'][0]: EXECUTION.principal_ref, keys['observer'][0]: OBSERVER.principal_ref}
    endpoint = _free_endpoint()
    process = context.Process(target=_server, args=(str(tmp_path), endpoint, keys['server'], publics,
                                                    messages, controls))
    process.start()
    return process, messages, controls, keys, endpoint


def _stop(process, controls, runtimes):
    for runtime in runtimes:
        if runtime is not None:
            runtime.close(timeout_seconds=5)
    controls.put('stop')
    process.join(15)
    assert not process.is_alive() and process.exitcode == 0


def _ingest(messages, controls, client_order_id, status):
    controls.put((client_order_id, status))
    reply = messages.get(timeout=10)
    assert reply == ('ingested', client_order_id, 'ATTRIBUTED'), reply


def _strategy(endpoint, server_public, keys, journal_id):
    session = GatewayStrategyClientSession(_client_config(endpoint, STRATEGY),
        binding=GatewayCommandBinding(journal_id, ACCOUNT, STRATEGY.actor_ref, STRATEGY.principal_ref),
        route=GatewayClientRoute('broker.direct_write', SERVICE.actor_ref, 'gateway-a', 'scope-a', SCOPE, STRATEGY),
        peer=SERVICE, security_profiles={'security-a': ClientCurveKeys(server_public, *keys)},
        host=StrategyHost(), delegated_cancel_host=CancelHost(), requested_operations=STRATEGY_OPS)
    assert session.start(timeout_seconds=10).healthy
    return session


def test_risk_and_execution_stand_ins_order_cancel_and_read_the_observation_stream(tmp_path):
    process, messages, controls, keys, endpoint = _spawn(tmp_path)
    server_public = keys['server'][0]
    strategy = risk = execution = risk_watch = execution_watch = watcher = None
    try:
        ready = messages.get(timeout=30)
        assert ready[0] == 'ready', '\n'.join(map(str, ready))
        assert ready[1] == process.pid != os.getpid()
        # The represented strategy's run must be live before a delegate can register.
        strategy = _strategy(endpoint, server_public, keys['strategy'], ready[2])
        risk = _runtime(endpoint, server_public, RISK, keys['risk'], 'upstream.direct_write', DELEGATE_OPS, SCOPE)
        execution = _runtime(endpoint, server_public, EXECUTION, keys['execution'], 'upstream.direct_write',
                             DELEGATE_OPS | PARENT_OPERATIONS, SCOPE)
        assert risk.start(timeout_seconds=10).healthy and execution.start(timeout_seconds=10).healthy
        risk_route = GatewayDelegateRoute('upstream.direct_write', SERVICE.actor_ref, 'gateway-a', 'scope-a', SCOPE, RISK)
        exec_route = replace(risk_route, identity=EXECUTION)
        risk_host, exec_host = MemoryDelegateHost(), MemoryDelegateHost()
        risk_client = GatewayDelegateServiceClient(risk, route=risk_route, host=risk_host)
        exec_client = GatewayDelegateServiceClient(execution, route=exec_route, host=exec_host)
        parent_client = GatewayParentServiceClient(execution, route=exec_route, host=exec_host)

        # Risk stand-in places one order on behalf of the strategy.
        risk_request = _request('risk-order-1', 'risk-client-1')
        risk_command = GatewayDelegatedCommand(ACCOUNT, STRATEGY.actor_ref, DOMAIN, RISK.principal_ref,
            RISK.actor_ref, 'risk', RUN, 'risk-order-1', gateway_payload_digest(risk_request.to_dict()),
            INSTRUMENT, 'risk-v1')
        reserved = risk_client.reserve(risk_command, risk_request)
        assert reserved.outcome == 'OBSERVED' and reserved.observation.strategy_scope == STRATEGY.actor_ref
        assert risk_client.dispatch('risk-order-1').outcome == 'QUEUED'
        risk_sent = _until(lambda: risk_client.actor_status('risk-order-1'),
                           lambda r: r.observation is not None and r.observation.command.state == 'SENT')
        assert risk_sent.observation.command.state == 'SENT', risk_sent
        _ingest(messages, controls, 'risk-client-1', 'accepted')

        # Execution stand-in registers a parent, then places a child order of it.
        spec = {'account_scope': ACCOUNT, 'strategy_scope': STRATEGY.actor_ref, 'parent_ref': 'parent-1',
                'registration_operation_id': 'register-parent-1', 'execution_domain_ref': DOMAIN,
                'instrument': INSTRUMENT.to_dict(), 'quantity': '2', 'quantity_unit': 'lots', 'side': 'buy',
                'offset': 'open', 'limit_price': '10', 'creation_trading_day': TRADING_DAY,
                'allowed_trading_days': [TRADING_DAY], 'expires_at_ns': 2 ** 62, 'grant_revision': 'exec-v1',
                'target_position_ref': None, 'plan_digest': 'a' * 64, 'plan_version': 'v1', 'time_in_force': 'DAY'}
        registered = parent_client.register_parent(spec, 'register-parent-1')
        assert parent_client.verify(registered, spec, 'register-parent-1') == 'ACCEPTED', registered
        child_request = _request('exec-child-1', 'exec-client-1')
        child_command = GatewayDelegatedCommand(ACCOUNT, STRATEGY.actor_ref, DOMAIN, EXECUTION.principal_ref,
            EXECUTION.actor_ref, 'execution', RUN, 'exec-child-1', gateway_payload_digest(child_request.to_dict()),
            INSTRUMENT, 'exec-v1', 'parent-1', 'exec-child-1')
        child = exec_client.reserve(child_command, child_request, ttl_seconds=30)
        assert child.outcome == 'OBSERVED' and child.observation.strategy_scope == STRATEGY.actor_ref, child
        assert exec_client.dispatch('exec-child-1').outcome == 'QUEUED'
        # The child of a live, authorised parent is dispatched through the claim like any order.
        exec_sent = _until(lambda: exec_client.actor_status('exec-child-1'),
                           lambda r: r.observation is not None and r.observation.command.state == 'SENT')
        assert exec_sent.observation.command.state == 'SENT', exec_sent
        assert exec_sent.observation.native_identity_digest is not None
        _ingest(messages, controls, 'exec-client-1', 'accepted')

        # The represented strategy reads both orders under its own name, with the actor kept.
        facade = GatewayBtApi(strategy, exchange_name='CTP')
        for key, actor, state in (('risk-order-1', RISK.principal_ref, 'SENT'),
                                  ('exec-child-1', EXECUTION.principal_ref, 'SENT')):
            seen = facade.delegated_command_status('CTP', key).observation
            assert (seen.strategy_scope, seen.actor_principal, seen.state) == (STRATEGY.actor_ref, actor, state)

        # Over-authority at the process boundary: Risk acting beyond its grant is refused.
        outside_request = _request('risk-order-hc', 'risk-client-hc', symbol='hc')
        outside = replace(risk_command, request_key='risk-order-hc', instrument=OUTSIDE,
                          payload_digest=gateway_payload_digest(outside_request.to_dict()))
        refused = risk_client.reserve(outside, outside_request)
        assert (refused.outcome, refused.code) == ('REQUEST_DENIED', 'DELEGATION_DENIED'), refused
        other_strategy = risk.call('upstream.direct_write', 'direct.delegated_reserve', {
            'command': {**risk_command.to_mapping(), 'strategy_scope': 'strategy-b'},
            'payload': risk_request.to_dict(), 'ttl_seconds': 10},
            account_ref=ACCOUNT, scope_ref='scope-a', timeout_seconds=5)
        assert dict(other_strategy) == {'status': 'REQUEST_REJECTED', 'code': 'DELEGATION_DENIED'}
        # Risk tries to cancel the Execution child: it is another party's order.
        claimed = replace(risk_command, request_key='exec-child-1',
                          payload_digest=gateway_payload_digest(child_request.to_dict()))
        for operation, extra in (('direct.delegate_actor_status', {}),
                                 ('direct.delegate_actor_cancel', {'operation_id': 'steal-1',
                                  'reservation_id': exec_sent.observation.command.reservation_id})):
            foreign = risk.call('upstream.direct_write', operation, {
                'command': claimed.to_mapping(), 'payload': child_request.to_dict(), **extra},
                account_ref=ACCOUNT, scope_ref='scope-a', timeout_seconds=5)
            assert dict(foreign) == {'status': 'REQUEST_REJECTED', 'code': 'DELEGATION_DENIED'}, operation
        assert exec_client.actor_status('exec-child-1').observation.command.state == 'SENT'

        # Each stand-in cancels its own order; Execution freezes its parent first.
        risk_cancel = risk_client.cancel_actor(risk_sent.observation, operation_id='risk-cancel-1')
        assert risk_cancel.outcome in {'QUEUED', 'OBSERVED'}, risk_cancel
        risk_cancel = _until(lambda: risk_client.actor_cancel_status('risk-cancel-1'),
                             lambda r: r.cancel is not None and r.cancel.state == 'SENT')
        assert risk_cancel.cancel.state == 'SENT'
        assert risk_cancel.cancel.target_actor_principal == RISK.principal_ref
        assert risk_cancel.cancel.strategy_scope == STRATEGY.actor_ref
        frozen = parent_client.cancel_parent(spec, 'cancel-parent-1', 1)
        assert parent_client.verify(frozen, spec, 'cancel-parent-1') == 'ACCEPTED', frozen
        target, = frozen['result']['targets']
        assert (target['child_id'], target['request_key'], target['phase']) == (
            'exec-child-1', 'exec-child-1', 'CANCEL_PENDING')
        # The sent child is cancelled under the frozen parent with the frozen target's operation id.
        exec_sent = exec_client.actor_status('exec-child-1')
        exec_cancel = exec_client.cancel_actor(exec_sent.observation, operation_id=target['cancel_operation_id'])
        assert exec_cancel.outcome in {'QUEUED', 'OBSERVED'}, exec_cancel
        exec_cancel = _until(lambda: exec_client.actor_cancel_status(target['cancel_operation_id']),
                             lambda r: r.cancel is not None and r.cancel.state == 'SENT')
        assert exec_cancel.cancel.state == 'SENT', exec_cancel
        assert exec_cancel.cancel.target_actor_principal == EXECUTION.principal_ref
        assert exec_cancel.cancel.strategy_scope == STRATEGY.actor_ref
        _ingest(messages, controls, 'risk-client-1', 'cancelled')
        _ingest(messages, controls, 'exec-client-1', 'cancelled')

        # Both stand-ins and a pure observer read the same account observation stream.
        risk_watch = _runtime(endpoint, server_public, RISK, keys['risk'], 'upstream.account',
                              ACCOUNT_OBSERVER_OPERATIONS, ACCOUNT_ONLY)
        execution_watch = _runtime(endpoint, server_public, EXECUTION, keys['execution'], 'upstream.account',
                                   ACCOUNT_OBSERVER_OPERATIONS, ACCOUNT_ONLY)
        watcher = _runtime(endpoint, server_public, OBSERVER, keys['observer'], 'upstream.account',
                           ACCOUNT_OBSERVER_OPERATIONS, ACCOUNT_ONLY)
        # Both orders reached the (synthetic) venue; their returns keep the acting delegate.
        expected = [('risk-order-1', RISK.principal_ref, DirectOrderStatus.ACCEPTED),
                    ('exec-child-1', EXECUTION.principal_ref, DirectOrderStatus.ACCEPTED),
                    ('risk-order-1', RISK.principal_ref, DirectOrderStatus.CANCELLED),
                    ('exec-child-1', EXECUTION.principal_ref, DirectOrderStatus.CANCELLED)]
        consumers = set()
        for identity, runtime in ((RISK, risk_watch), (EXECUTION, execution_watch), (OBSERVER, watcher)):
            assert runtime.start(timeout_seconds=10).healthy
            client = GatewayAccountObserverClient(runtime, route=GatewayAccountObserverRoute('upstream.account',
                SERVICE.actor_ref, 'gateway-a', 'scope-a', ACCOUNT_ONLY, identity))
            page = client.read_after()
            assert page.outcome == 'OBSERVED', page
            assert [(e.event.request_key, e.event.command_principal, e.event.observation.status)
                    for e in page.page.events] == expected
            assert {e.strategy_scope for e in page.page.events} == {STRATEGY.actor_ref}
            consumers.add(page.page.progress.consumer_ref)
        assert len(consumers) == 3  # one independent cursor per observer

        # The orders were written into the represented strategy's own stream.
        events = strategy.execution_stream_client.read_after()
        assert events.outcome == 'OBSERVED', events
        assert [(e.request_key, e.command_principal, e.observation.status) for e in events.page.events] == expected
        assert events.page.acknowledged_sequence == 0  # observers never acknowledge for the strategy
    finally:
        _stop(process, controls, (risk_watch, execution_watch, watcher, risk, execution))
        if strategy is not None:
            strategy.close(timeout_seconds=5)


def _refused(runtime):
    try:
        snapshot = runtime.start(timeout_seconds=5)
    except ThreadedRuntimeError as error:
        return str(error)
    raise AssertionError(('registration was admitted', snapshot))


def test_delegates_and_observers_cannot_register_beyond_their_grants(tmp_path):
    process, messages, controls, keys, endpoint = _spawn(tmp_path)
    server_public = keys['server'][0]
    runtimes = []
    strategy = None
    try:
        ready = messages.get(timeout=30)
        assert ready[0] == 'ready', '\n'.join(map(str, ready))
        # No delegate can register before the represented strategy's run is live.
        early = _runtime(endpoint, server_public, RISK, keys['risk'], 'upstream.direct_write', DELEGATE_OPS, SCOPE)
        runtimes.append(early)
        _refused(early)
        strategy = _strategy(endpoint, server_public, keys['strategy'], ready[2])
        # Acting for a strategy the Gateway has not registered for this delegate.
        other = replace(SCOPE, strategy_ref='strategy-b')
        undeclared = _runtime(endpoint, server_public, RISK, keys['risk'], 'upstream.direct_write', DELEGATE_OPS, other)
        runtimes.append(undeclared)
        _refused(undeclared)
        # The pure observer holds a read-only grant: no write registration of any kind.
        writer = _runtime(endpoint, server_public, OBSERVER, keys['observer'], 'upstream.direct_write',
                          DELEGATE_OPS, SCOPE)
        runtimes.append(writer)
        _refused(writer)
        # Execution may not register parent operations under Risk's identity, nor Risk under its own.
        risk_parent = _runtime(endpoint, server_public, RISK, keys['risk'], 'upstream.direct_write',
                               DELEGATE_OPS | PARENT_OPERATIONS, SCOPE)
        runtimes.append(risk_parent)
        _refused(risk_parent)
        # The legitimate registration still works after those refusals.
        risk = _runtime(endpoint, server_public, RISK, keys['risk'], 'upstream.direct_write', DELEGATE_OPS, SCOPE)
        runtimes.append(risk)
        assert risk.start(timeout_seconds=10).healthy
    finally:
        _stop(process, controls, runtimes)
        if strategy is not None:
            strategy.close(timeout_seconds=5)
