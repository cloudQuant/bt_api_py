"""Local CURVE Gateway facts to fenced Risk journal; synthetic inputs only."""
import hashlib
import queue
import threading
import time
import traceback
from dataclasses import replace

import pytest
import zmq
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.gateway_account_comparison import GatewayAccountComparison
from bt_api_py.gateway_account_facts import ACCOUNT_FACTS_OPERATIONS, GatewayAccountFactsObserver
from bt_api_py.gateway_account_observer import GatewayAccountObserver
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import (
    GatewayControlGrant,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.risk_service_host import OBSERVER_OPERATIONS
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ServiceClientRuntime
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.integration.test_iter63_account_facts_curve import command, fact
from tests.integration.test_iter63_delegate_risk_execution_loopback import (
    ACCOUNT_ONLY,
    RISK,
    _read_service,
)
from tests.integration.test_iter63_gateway_direct_service import ACCOUNT, CLIENT_A, DOMAIN, SERVICE
from tests.integration.test_iter63_gateway_dual_ports import _dual_config
from tests.integration.test_service_account_instrument_metadata import _free_endpoint


@pytest.mark.parametrize('timeout_case', [False, True])
def test_host_start_and_maintenance_commit_curve_facts_before_ack(tmp_path, monkeypatch, timeout_case):
    endpoint = _free_endpoint()
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    grants = PrincipalGrant(RISK, (ResourceGrant('gateway-a', 'service.account_read', 'scope-a',
                                                 ACCOUNT_ONLY, ACCOUNT_FACTS_OPERATIONS | OBSERVER_OPERATIONS),))
    authority = TrustedServiceAuthority(identity=SERVICE, scope=WireScope(DOMAIN), scope_ref='scope-a',
        scope_version='scope-v1', security_ref='security-a', security_version='security-v1',
        registration_policy_ref='direct-policy-v1', registration_policy_version='direct-policy-v1',
        service_requirements_ref='direct-requirements-v1', service_requirements_version='direct-requirements-v1',
        curve_public_key=server_public, curve_secret_key=server_secret,
        principals={client_public: RISK.principal_ref}, grants={RISK.principal_ref: (grants,)})
    ready, stop = queue.Queue(), threading.Event()
    (tmp_path / 'guard').mkdir()

    def serve():
        owner = host = None
        try:
            state = {}
            def factory():
                guard = LocalAccountSessionGuard(tmp_path / 'guard', ACCOUNT, 'owner')
                journal = GatewayDirectCommandJournal.create(tmp_path / 'gateway.sqlite', ACCOUNT,
                                                              generation=guard.acquire())
                ledger = GatewayDirectExecutionLedger(journal, guard)
                command(journal, strategy=CLIENT_A.actor_ref, key='old', anchor='old-anchor')
                event = replace(fact(keys=('old-anchor',), binding=('old-anchor',), venue='old-anchor'),
                                account_scope=ACCOUNT)
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
            read_service = _read_service(tmp_path, {RISK.principal_ref, CLIENT_A.principal_ref})
            binding = GatewayDirectServiceBinding(journal,
                {CLIENT_A.principal_ref: GatewayDirectGrant(CLIENT_A.actor_ref, frozenset({'rb'}))}, owner=owner,
                strategy_financial_enabled=True, cancel_enabled=True, control=state['control'],
                control_grants={CLIENT_A.principal_ref: GatewayControlGrant(
                    CLIENT_A.actor_ref, True, frozenset(), frozenset({'direct.control.status'}))})
            facts = GatewayAccountFactsObserver(owner, journal, ledger, account_scope=ACCOUNT,
                execution_domain_ref=DOMAIN, principals=frozenset({RISK.principal_ref}))
            observer = GatewayAccountObserver(owner, journal, ledger, account_scope=ACCOUNT,
                execution_domain_ref=DOMAIN, principals=frozenset({RISK.principal_ref}))
            comparison = GatewayAccountComparison(owner, journal, account_scope=ACCOUNT,
                principals=frozenset({CLIENT_A.principal_ref}), instruments=frozenset({
                    MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb2601', 'rb2601')}))
            config = _dual_config(endpoint).to_public_dict()
            config['components']['gateway']['settings']['options'].update(dispatch_enabled=True, control_enabled=True,
                strategy_ledger_enabled=True, account_extensions_enabled=True, account_facts_financial_enabled=True,
                cancel_enabled=True, strategy_origin_enabled=True, control_cancel_enabled=True,
                account_fact_recovery_enabled=True, account_event_recovery_enabled=True)
            host = ServiceHost(parse_service_config(config), authority, tmp_path / 'registry', lambda *_:
                ComponentResources.borrowed({'binding': binding, 'read_service': read_service,
                    'account_facts': facts, 'account_observer': observer, 'account_comparison': comparison},
                    ready=lambda: True))
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

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    from types import SimpleNamespace

    import bt_api_py.risk_service_gateway as gateway_module
    import bt_api_py.risk_service_host as host_module
    from bt_api_py.risk_service_host import RiskServiceHost
    from tests.unit.test_risk_account_facts_host import seed_v4, selected_config

    risk_root = tmp_path / 'risk-config'
    risk_root.mkdir()
    risk_config = selected_config(risk_root)
    delegate = replace(risk_config.gateway.delegate, principal_ref=RISK.principal_ref, actor_ref=RISK.actor_ref,
                       public_key=client_public, secret_key=client_secret)
    gateway = replace(risk_config.gateway, service_id=SERVICE.actor_ref, principal_ref=SERVICE.principal_ref,
                      instance_id='gateway-a', scope_ref='scope-a', rpc_endpoint=endpoint,
                      server_public_key=server_public, delegate=delegate)
    risk_config = replace(risk_config, gateway=gateway, account_scope=ACCOUNT, execution_domain_ref=DOMAIN)
    seed_v4(risk_config, extension=2)
    host = RiskServiceHost(risk_config)
    acked = []
    served = []
    reads = []
    maintenance = []
    renewals = []

    class FakeRuntime:
        def __init__(self, mount, operations):
            self.mount, self.operations = mount, operations
        def start(self, **_kwargs):
            binding = SimpleNamespace(active=True, mount=self.mount, operations=self.operations)
            return SimpleNamespace(state='running', healthy=True, bindings=(binding,))
        def status(self):
            return SimpleNamespace(state='running')
        def close(self, **_kwargs):
            return SimpleNamespace(state='closed', thread_alive=False, error_code=None,
                unregister_uncertainties=0, owner_cleanup_uncertainties=0, unknown_registration_count=0)

    class FakeEngine:
        def recover(self):
            pass
        def maintain(self):
            pass

    class FakePump:
        def __init__(self, *_args, **_kwargs):
            pass
        def poll(self):
            pass

    class FakeService:
        state = 'started'
        def serve_once(self, _timeout):
            served.append(True)
        def close(self):
            self.state = 'closed'

    try:
        started = ready.get(timeout=20)
        assert started[0] == 'ready', started
        def build_engine():
            host.engine = FakeEngine()
            host.facts = object()
            host.delegate_storage = object()
            host.control_runtime = FakeRuntime('application.direct_write', gateway_module.CONTROL_OPERATIONS)
        host.build_engine = build_engine
        host.start_service_host = lambda: setattr(host, 'service_host', FakeService())
        monkeypatch.setattr(host_module, 'bootstrap_policy', lambda *_: None)
        monkeypatch.setattr(host_module, 'fault_point_from_environment', lambda *_: None)
        monkeypatch.setattr(gateway_module, 'ObservationPump', FakePump)
        monkeypatch.setattr(gateway_module, 'payload_reader', lambda *_: lambda *_: None)
        original_config = host_module._gateway_client_config
        def fast_heartbeat(config, mount, **kwargs):
            result = original_config(config, mount, **kwargs).to_public_dict()
            result['clients']['gateway']['settings']['options'] = {'heartbeat_interval_seconds': 1}
            return parse_service_config(result)
        monkeypatch.setattr(host_module, '_gateway_client_config', fast_heartbeat)
        original_maintain = ServiceClientRuntime.maintain
        def observed_maintain(runtime):
            if runtime._requested_operations == {'upstream.account': host_module.ACCOUNT_FACTS_OBSERVATION_OPERATIONS}:
                maintenance.append(True)
            return original_maintain(runtime)
        monkeypatch.setattr(ServiceClientRuntime, 'maintain', observed_maintain)
        original_rpc = ServiceClientRuntime._rpc
        def observed_rpc(runtime, *args, **kwargs):
            result = original_rpc(runtime, *args, **kwargs)
            if (len(args) >= 4 and args[3] == 'service.renew'
                    and runtime._requested_operations == {'upstream.account': host_module.ACCOUNT_FACTS_OBSERVATION_OPERATIONS}):
                renewals.append(True)
            return result
        monkeypatch.setattr(ServiceClientRuntime, '_rpc', observed_rpc)
        if timeout_case:
            original_handle = GatewayAccountFactsObserver.handle
            def delayed(observer, operation, context, arguments):
                if operation == 'account.facts.read_after.v2':
                    reads.append(True)
                    time.sleep(0.20)
                return original_handle(observer, operation, context, arguments)
            monkeypatch.setattr(GatewayAccountFactsObserver, 'handle', delayed)
        original_call = ThreadedServiceClientRuntime.call_bound
        def checked_ack(runtime, mount, operation, arguments, **kwargs):
            if runtime is host._account_facts_runtime and operation == 'account.facts.ack':
                with host.ledger._journal._transaction() as db:
                    cursor, pending = db.execute('SELECT cursor,pending FROM risk_r05_state').fetchone()
                    assert (cursor, pending) == (arguments['sequence'], arguments['sequence'])
                    assert db.execute('SELECT count(*) FROM risk_r05_source_binding').fetchone()[0] == 1
                acked.append(arguments['sequence'])
            return original_call(runtime, mount, operation, arguments, **kwargs)
        monkeypatch.setattr(ThreadedServiceClientRuntime, 'call_bound', checked_ack)
        host.start()
        assert host.account_facts_observation_status() == 'READY'
        observer_snapshot = host._observer_runtime.status()
        facts_snapshot = host._account_facts_runtime.status()
        assert observer_snapshot.healthy and facts_snapshot.healthy
        assert observer_snapshot.bindings[0].registration_id != facts_snapshot.bindings[0].registration_id
        assert observer_snapshot.bindings[0].operations == OBSERVER_OPERATIONS
        assert facts_snapshot.bindings[0].operations == host_module.ACCOUNT_FACTS_OBSERVATION_OPERATIONS
        before_maintenance = len(maintenance)
        host.run_once()
        if timeout_case:
            assert host.account_facts_observation_status() == 'UNKNOWN' and acked == []
            host.run_once()
            assert len(reads) == 1 and len(served) == 2
            assert host._account_facts_runtime.status().thread_alive is True
            deadline = time.monotonic() + 2
            while len(maintenance) <= before_maintenance and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(maintenance) > before_maintenance
            deadline = time.monotonic() + 3
            while not renewals and time.monotonic() < deadline:
                time.sleep(0.01)
            assert renewals
        else:
            assert host.account_facts_observation_status() == 'APPLIED' and acked == [1]
            with host.ledger.transaction() as txn:
                progress = txn.r05_progress()
                assert progress['cursor'] == 1 and progress['pending'] is None and progress['ack_observed'] == 1
                assert txn.r05_source_binding()['fingerprint'] == hashlib.sha256(server_public).hexdigest()
        assert host.close() is True
    finally:
        if host._cleanup_state == 'ready':
            host.close()
        stop.set()
        server.join(15)
        assert not server.is_alive()
