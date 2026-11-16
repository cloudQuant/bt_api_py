"""Authenticated CURVE account observer to Risk SQLite cursor; synthetic provider facts."""
from dataclasses import replace
from contextlib import ExitStack
import hashlib
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
from bt_api_py.gateway_direct_service import GatewayControlGrant, GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.integration.test_iter63_account_facts_curve import command, fact
from tests.integration.test_iter63_gateway_direct_service import ACCOUNT, CLIENT_A, DOMAIN, SERVICE
from tests.integration.test_iter63_gateway_dual_ports import _dual_config
from tests.integration.test_iter63_delegate_risk_execution_loopback import RISK, ACCOUNT_ONLY, _runtime, _read_service
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from bt_api_base.market_protocol import MarketInstrumentKey


def test_authenticated_observer_applies_risk_cursor_before_ack(tmp_path):
    endpoint = _free_endpoint()
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    grants = PrincipalGrant(RISK, (
        ResourceGrant('gateway-a', 'service.account_read', 'scope-a', ACCOUNT_ONLY, ACCOUNT_OBSERVER_OPERATIONS),
    ))
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
            read_service = _read_service(tmp_path, {RISK.principal_ref, CLIENT_A.principal_ref})
            binding = GatewayDirectServiceBinding(journal,
                {CLIENT_A.principal_ref: GatewayDirectGrant(CLIENT_A.actor_ref, frozenset({'rb'}))}, owner=owner,
                strategy_financial_enabled=True, cancel_enabled=True, control=state['control'],
                control_grants={CLIENT_A.principal_ref: GatewayControlGrant(
                    CLIENT_A.actor_ref, True, frozenset(), frozenset({'direct.control.status'}))})
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
        runtime = _runtime(endpoint, server_public, RISK, (client_public, client_secret),
                           'upstream.account', ACCOUNT_OBSERVER_OPERATIONS, ACCOUNT_ONLY)
        assert runtime.start(timeout_seconds=10).healthy
        runtimes.append(runtime)
        route = GatewayAccountObserverRoute('upstream.account', SERVICE.actor_ref, 'gateway-a',
                                            'scope-a', ACCOUNT_ONLY, RISK)
        client = GatewayAccountObserverClient(runtime, route=route, peer=SERVICE,
            fingerprint=hashlib.sha256(server_public).hexdigest())
        from bt_api_risk.core.admission import AccountScope
        from bt_api_risk.pretrade import PolicyStore, PretradeEngine, PretradeLedger, RuleRegistry
        from bt_api_risk.risk_order_journal import RiskOrderJournal
        from bt_api_py.risk_service_gateway import ObservationPump, RiskFactsCache
        from tests.unit.test_iter64_risk_service_component import Facts, Gateway, Control
        from bt_api_risk.pretrade.engine import _v1_source_quarantined
        with ExitStack() as stack:
            journal = RiskOrderJournal(tmp_path / 'risk.sqlite', writer_id='synthetic')
            fence = stack.enter_context(journal.writer_fence(AccountScope('ctp', ACCOUNT, 'simnow')))
            ledger = PretradeLedger(journal, fence)
            engine = PretradeEngine(ledger, PolicyStore(ledger), RuleRegistry(), Facts(), Gateway(), Control(),
                account_scope=ACCOUNT, route='CTP', now_ns=lambda: 1_900_000_000_000_000_000,
                reservation_ttl_seconds=10.0, actor_principal='risk-principal',
                dispatch_recheck_seconds=5.0, dispatch_recheck_limit=8)
            original_ack = client.acknowledge
            def checked_ack(sequence, **kwargs):
                assert engine.observation_cursor_matches(kwargs['expected_epoch'], sequence - 1,
                                                         pump._cursor_revision) is True
                return original_ack(sequence, **kwargs)
            client.acknowledge = checked_ack
            pump = ObservationPump(engine, client, RiskFactsCache({}), risk_principal='risk-principal',
                                   intent_payload=lambda _: None, timeout_seconds=5)
            assert _v1_source_quarantined() is True
            assert pump.poll() == 1, pump.last_code
            assert pump.acknowledged == 1 and pump.verified_epoch is not None
            assert client.progress().progress.acknowledged_sequence == 1
            assert _v1_source_quarantined() is True
    finally:
        for runtime in runtimes:
            runtime.close(timeout_seconds=10)
        stop.set()
        thread.join(15)
        assert not thread.is_alive()
