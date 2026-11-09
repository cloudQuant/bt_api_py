"""Two real CURVE registrations cannot share an account.events offer.

Only synthetic facts and temporary journal files are used.
"""
from dataclasses import replace
import queue
import threading
import traceback

import zmq
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.gateway_account_observer import GatewayAccountObserver, ACCOUNT_OBSERVER_OPERATIONS
from bt_api_py.gateway_account_observer_client import GatewayAccountObserverClient, GatewayAccountObserverRoute
from bt_api_py.gateway_account_comparison import GatewayAccountComparison
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayControlGrant, GatewayDirectGrant, GatewayDirectServiceBinding, STORAGE_OPERATIONS
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.integration.test_iter63_account_facts_curve import command, fact
from tests.integration.test_iter63_gateway_direct_service import ACCOUNT, CLIENT_A, DOMAIN, SERVICE
from tests.integration.test_iter63_gateway_dual_ports import _dual_client, _dual_config, _resources
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from bt_api_base.market_protocol import MarketInstrumentKey


def test_events_cursor_owner_handoff_with_real_registry_and_curve(tmp_path):
    endpoint = _free_endpoint()
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    scope_grant = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref)
    grants = PrincipalGrant(CLIENT_A, (
        ResourceGrant('gateway-a', 'service.direct_write', 'scope-a', scope_grant, STORAGE_OPERATIONS),
        ResourceGrant('gateway-a', 'service.account_read', 'scope-a', scope_grant, ACCOUNT_OBSERVER_OPERATIONS),
    ))
    authority = TrustedServiceAuthority(identity=SERVICE, scope=WireScope(DOMAIN), scope_ref='scope-a',
        scope_version='scope-v1', security_ref='security-a', security_version='security-v1',
        registration_policy_ref='direct-policy-v1', registration_policy_version='direct-policy-v1',
        service_requirements_ref='direct-requirements-v1', service_requirements_version='direct-requirements-v1',
        curve_public_key=server_public, curve_secret_key=server_secret,
        principals={client_public: CLIENT_A.principal_ref}, grants={CLIENT_A.principal_ref: (grants,)})
    ready, stop = queue.Queue(), threading.Event()
    (tmp_path / 'guard').mkdir()

    def serve():
        owner = host = None
        try:
            state = {}
            def factory():
                guard = LocalAccountSessionGuard(tmp_path/'guard', ACCOUNT, 'owner')
                journal = GatewayDirectCommandJournal.create(tmp_path/'journal.sqlite', ACCOUNT, generation=guard.acquire())
                ledger = GatewayDirectExecutionLedger(journal, guard)
                command(journal, strategy=CLIENT_A.actor_ref, key='old', anchor='old-anchor')
                event = replace(fact(keys=('old-anchor',), binding=('old-anchor',), venue='old-anchor'), account_scope=ACCOUNT)
                assert ledger.ingest(event).disposition == 'ATTRIBUTED'
                control = journal.enable_controls(guard)
                journal.enable_strategy_ledger(guard)
                journal.enable_account_extensions(guard)
                journal.enable_parent_authority(guard)
                journal.enable_account_facts_financial(guard)
                journal.enable_actor_stream(guard)
                journal.enable_strategy_origin(guard)
                journal.enable_control_cancel(guard)
                journal.enable_account_fact_recovery(guard)
                journal.enable_account_event_recovery(guard)
                dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, lambda *_: None, lambda _: (), lambda _: True)
                cancel = GatewayDirectCancelDispatcher(GatewayDirectCancelJournal(journal), dispatcher, guard,
                                                       'CTP', lambda *_: None, lambda _: True)
                state.update(journal=journal, ledger=ledger, control=control)
                return GatewayDirectOwnerResources(dispatcher, journal, guard.release, cancel_dispatcher=cancel)
            owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=5, busy_timeout_seconds=5)
            assert owner.start(10)
            journal, ledger = state['journal'], state['ledger']
            _old_binding, read_service, _model = _resources(tmp_path)
            binding = GatewayDirectServiceBinding(journal,
                {CLIENT_A.principal_ref: GatewayDirectGrant(CLIENT_A.actor_ref, frozenset({'rb'}))}, owner=owner,
                strategy_financial_enabled=True, cancel_enabled=True, control=state['control'],
                control_grants={CLIENT_A.principal_ref: GatewayControlGrant(
                    CLIENT_A.actor_ref, True, frozenset(), frozenset({'direct.control.status'}))})
            observer = GatewayAccountObserver(owner, journal, ledger, account_scope=ACCOUNT,
                execution_domain_ref=DOMAIN, principals=frozenset({CLIENT_A.principal_ref}))
            comparison = GatewayAccountComparison(owner, journal, account_scope=ACCOUNT,
                principals=frozenset({CLIENT_A.principal_ref}), instruments=frozenset({
                    MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb2601', 'rb2601')}))
            config = _dual_config(endpoint).to_public_dict()
            config['components']['gateway']['settings']['options'].update(dispatch_enabled=True, control_enabled=True,
                strategy_ledger_enabled=True, account_extensions_enabled=True, account_facts_financial_enabled=True,
                cancel_enabled=True, strategy_origin_enabled=True, control_cancel_enabled=True,
                account_fact_recovery_enabled=True, account_event_recovery_enabled=True)
            host = ServiceHost(parse_service_config(config), authority, tmp_path/'registry', lambda *_:
                ComponentResources.borrowed({'binding': binding, 'read_service': read_service,
                    'account_observer': observer, 'account_comparison': comparison}, ready=lambda: True))
            host.start()
            ready.put(('ready',))
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
    runtimes = []
    try:
        started = ready.get(timeout=20)
        assert started[0] == 'ready', started
        raw = _dual_client(endpoint, CLIENT_A).to_public_dict()
        raw['clients']['gateway']['settings']['bindings'] = [
            b for b in raw['clients']['gateway']['settings']['bindings'] if b['mount'] == 'store.account']
        config = parse_client_config(raw)
        def connect(run):
            runtime = ThreadedServiceClientRuntime(lambda: ServiceClientRuntime(config,
                identity=CLIENT_A, scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
                peer_resolver=lambda _: SERVICE, transport_factory=ZmqTransportFactory(
                    {'security-a': ClientCurveKeys(server_public, client_public, client_secret)}),
                run_id=run, requested_operations={'store.account': ACCOUNT_OBSERVER_OPERATIONS}))
            assert runtime.start(timeout_seconds=10).healthy
            runtimes.append(runtime)
            scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref, run_id=run)
            route = GatewayAccountObserverRoute('store.account', SERVICE.actor_ref, 'gateway-a', 'scope-a', scope, CLIENT_A)
            return runtime, GatewayAccountObserverClient(runtime, route=route)
        a, client_a = connect('run-a')
        first = client_a.read_after()
        assert first.outcome == 'OBSERVED' and len(first.page.events) == 1
        epoch = first.page.progress.epoch
        b, client_b = connect('run-b')
        assert client_a.progress().outcome != 'OBSERVED'
        assert client_b.acknowledge(1, expected_epoch=epoch).outcome != 'OBSERVED'
        a.close(timeout_seconds=10)
        runtimes.remove(a)
        progress = client_b.progress()
        assert progress.outcome == 'OBSERVED' and progress.progress.offered_sequence == 0
        assert progress.progress.acknowledged_sequence == 0
        assert client_b.acknowledge(1, expected_epoch=epoch).code == 'CONSUMER_REPLAY_REQUIRED'
        assert client_b.read_after(after_sequence=1, expected_epoch=epoch).code == 'CONSUMER_REPLAY_REQUIRED'
        redelivered = client_b.read_after(expected_epoch=epoch)
        assert redelivered.outcome == 'OBSERVED' and redelivered.page.events == first.page.events
        assert client_b.acknowledge(1, expected_epoch=epoch).progress.acknowledged_sequence == 1
        b.close(timeout_seconds=10)
        runtimes.remove(b)
        _c, client_c = connect('run-c')
        progress = client_c.progress()
        assert progress.outcome == 'OBSERVED' and progress.progress.acknowledged_sequence == 1
        assert client_c.acknowledge(1, expected_epoch=epoch).code == 'CONSUMER_REPLAY_REQUIRED'
        assert client_c.read_after(after_sequence=1, expected_epoch=epoch).outcome == 'OBSERVED'
        assert client_c.acknowledge(1, expected_epoch=epoch).outcome == 'OBSERVED'
    finally:
        for runtime in runtimes:
            runtime.close(timeout_seconds=10)
        stop.set()
        thread.join(15)
        assert not thread.is_alive()
