"""Local CURVE Gateway facts to fenced Risk journal; synthetic inputs only."""
import hashlib
import json
import queue
import threading
import traceback
from contextlib import ExitStack
from dataclasses import replace

import zmq
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_risk.core.admission import AccountScope
from bt_api_risk.pretrade.ledger import PretradeLedger
from bt_api_risk.risk_order_journal import RiskOrderJournal

from bt_api_py.gateway_account_comparison import GatewayAccountComparison
from bt_api_py.gateway_account_facts import ACCOUNT_FACTS_OPERATIONS, GatewayAccountFactsObserver
from bt_api_py.gateway_account_facts_client import GatewayAccountFactsClient
from bt_api_py.gateway_account_observer_client import GatewayAccountObserverRoute
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import (
    GatewayControlGrant,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.risk_account_facts_coordinator import RiskAccountFactsCoordinator
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.integration.test_iter63_account_facts_curve import command, fact
from tests.integration.test_iter63_delegate_risk_execution_loopback import (
    ACCOUNT_ONLY,
    RISK,
    _read_service,
    _service_client,
)
from tests.integration.test_iter63_gateway_direct_service import ACCOUNT, CLIENT_A, DOMAIN, SERVICE
from tests.integration.test_iter63_gateway_dual_ports import _dual_config
from tests.integration.test_service_account_instrument_metadata import _free_endpoint


def test_curve_read_commits_risk_page_before_ack(tmp_path):
    endpoint = _free_endpoint()
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    grants = PrincipalGrant(RISK, (ResourceGrant('gateway-a', 'service.account_read', 'scope-a',
                                                 ACCOUNT_ONLY, ACCOUNT_FACTS_OPERATIONS),))
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
            comparison = GatewayAccountComparison(owner, journal, account_scope=ACCOUNT,
                principals=frozenset({CLIENT_A.principal_ref}), instruments=frozenset({
                    MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb2601', 'rb2601')}))
            config = _dual_config(endpoint).to_public_dict()
            config['components']['gateway']['settings']['options'].update(dispatch_enabled=True, control_enabled=True,
                strategy_ledger_enabled=True, account_extensions_enabled=True, account_facts_financial_enabled=True,
                cancel_enabled=True, strategy_origin_enabled=True, control_cancel_enabled=True,
                account_fact_recovery_enabled=True)
            host = ServiceHost(parse_service_config(config), authority, tmp_path / 'registry', lambda *_:
                ComponentResources.borrowed({'binding': binding, 'read_service': read_service,
                    'account_facts': facts, 'account_comparison': comparison}, ready=lambda: True))
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
    runtime = None
    try:
        started = ready.get(timeout=20)
        assert started[0] == 'ready', started
        client_config = _service_client(endpoint, RISK, 'upstream.account')
        curve = {'security-a': ClientCurveKeys(server_public, client_public, client_secret)}
        runtime = ThreadedServiceClientRuntime(lambda: ServiceClientRuntime(client_config, identity=RISK,
            scope_resolver=lambda _: ACCOUNT_ONLY, peer_resolver=lambda _: SERVICE,
            transport_factory=ZmqTransportFactory(curve, strict_single_connection=True),
            run_id='run', requested_operations={'upstream.account': ACCOUNT_FACTS_OPERATIONS}))
        assert runtime.start(timeout_seconds=10).healthy
        route = GatewayAccountObserverRoute('upstream.account', SERVICE.actor_ref, 'gateway-a',
                                            'scope-a', ACCOUNT_ONLY, RISK)
        client = GatewayAccountFactsClient(runtime, route=route, peer=SERVICE,
                                           fingerprint=hashlib.sha256(server_public).hexdigest())
        with ExitStack() as stack:
            journal = RiskOrderJournal(tmp_path / 'risk.sqlite', writer_id='synthetic')
            fence = stack.enter_context(journal.writer_fence(AccountScope('ctp', ACCOUNT, 'simnow')))
            ledger = PretradeLedger(journal, fence, schema_version=4)
            ledger.enable_r05_observations_v2()
            coordinator = RiskAccountFactsCoordinator(client, ledger)
            call_bound = runtime.call_bound
            seen = []
            def checked_ack(mount, operation, arguments, **kwargs):
                if operation == 'account.facts.ack':
                    sequence = arguments['sequence']
                    with journal._transaction() as db:
                        cursor, pending = db.execute('SELECT cursor,pending FROM risk_r05_state').fetchone()
                        assert (cursor, pending) == (sequence, sequence)
                        assert db.execute('SELECT count(*) FROM risk_r05_facts').fetchone()[0] == sequence
                        source = json.loads(db.execute('SELECT binding_json FROM risk_r05_source_binding').fetchone()[0])
                        assert source['peer_principal'] == SERVICE.principal_ref
                        assert source['fingerprint'] == hashlib.sha256(server_public).hexdigest()
                    seen.append(sequence)
                return call_bound(mount, operation, arguments, **kwargs)
            runtime.call_bound = checked_ack
            result = coordinator.poll_once()
            assert (result.status, result.cursor) == ('APPLIED', 1)
            assert seen == [1]
            with ledger.transaction() as txn:
                progress = txn.r05_progress()
                assert progress['cursor'] == 1 and progress['ack_observed'] == 1 and progress['pending'] is None
            assert client.progress().progress.acknowledged_sequence == 1
    finally:
        if runtime is not None:
            runtime.close(timeout_seconds=10)
        stop.set()
        server.join(15)
        assert not server.is_alive()
