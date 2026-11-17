"""Synthetic Risk host account-facts configuration and lifecycle."""
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_risk.core.admission import AccountScope
from bt_api_risk.pretrade.ledger import PretradeLedger
from bt_api_risk.risk_order_journal import RiskOrderJournal
from bt_api_risk.risk_order_journal_contracts import RiskOrderJournalError

from bt_api_py.gateway_account_observer_client import GatewayAccountObserverRoute
from bt_api_py.risk_service_config import (
    RiskAccountFactsObservation,
    RiskServiceConfigError,
    ensure_private_directory,
    load_risk_service_config,
    parse_risk_service_config,
)
from bt_api_py.risk_service_host import (
    ACCOUNT_FACTS_OBSERVATION_OPERATIONS,
    OBSERVER_OPERATIONS,
    RiskServiceHost,
    RiskServiceStartError,
)
from tests.unit.test_iter64_risk_service_config import document
from tests.unit.test_iter64_risk_service_host import setup

pytest_plugins = ('tests.unit.test_iter64_risk_service_host', 'tests.unit.test_gateway_account_facts_v2')


def selected_config(tmp_path):
    config = load_risk_service_config(setup(tmp_path))
    return replace(config, pretrade_schema_version=4,
                   account_facts_observation=RiskAccountFactsObservation(1.0, 0.1, 8))


def seed_v4(config, *, extension):
    ensure_private_directory(config.storage_directory)
    journal = RiskOrderJournal(config.risk_database, writer_id='synthetic-seed')
    scope = AccountScope(config.provider, config.account_scope, config.environment)
    with journal.writer_fence(scope) as fence:
        ledger = PretradeLedger(journal, fence, schema_version=4)
        if extension == 1:
            ledger.enable_r05_observations()
        elif extension == 2:
            ledger.enable_r05_observations_v2()


def account_route(config):
    gateway = config.gateway
    return GatewayAccountObserverRoute('upstream.account', gateway.service_id, gateway.instance_id,
        gateway.scope_ref, WireScope(config.execution_domain_ref, account_ref=config.account_scope),
        WireIdentity(WireRole.SERVICE, gateway.delegate.principal_ref, gateway.delegate.actor_ref))


def test_config_default_and_explicit_bounds(tmp_path):
    setup(tmp_path)
    raw = document()
    assert parse_risk_service_config(raw, path=tmp_path / 'risk.yaml').account_facts_observation is None
    raw['pretrade'] = {'schema_version': 4}
    raw['account_facts_observation'] = {'poll_interval_seconds': 1, 'timeout_seconds': '0.250', 'page_size': 256}
    config = parse_risk_service_config(raw, path=tmp_path / 'risk.yaml')
    assert config.account_facts_observation == RiskAccountFactsObservation(1.0, 0.25, 256)
    for key, value in [('poll_interval_seconds', '0.999'), ('timeout_seconds', '0.251'),
                       ('timeout_seconds', '0.0009'), ('page_size', 0), ('page_size', 257)]:
        bad = document()
        bad['pretrade'] = {'schema_version': 4}
        bad['account_facts_observation'] = {'poll_interval_seconds': 1, 'timeout_seconds': '0.1', 'page_size': 8}
        bad['account_facts_observation'][key] = value
        with pytest.raises(RiskServiceConfigError):
            parse_risk_service_config(bad, path=tmp_path / 'risk.yaml')
    raw['pretrade'] = {'schema_version': 3}
    with pytest.raises(RiskServiceConfigError):
        parse_risk_service_config(raw, path=tmp_path / 'risk.yaml')
    assert OBSERVER_OPERATIONS.isdisjoint(ACCOUNT_FACTS_OBSERVATION_OPERATIONS)


def test_consumer_identity_projection_keeps_existing_wire_bytes():
    from bt_api_py.gateway_account_facts_identity import account_facts_consumer_ref

    identity = WireIdentity(WireRole.SERVICE, '测试', 'actor')
    scope = WireScope('domain', account_ref='account')
    assert account_facts_consumer_ref(principal_ref=identity.principal_ref, identity=identity, scope=scope) == (
        'account-facts:354bc448464ea8e1bf44bd6317f7afe8987bf0b41987e219e078bdbff4c2dc3c')


def test_default_host_does_not_construct_facts_runtime(tmp_path, monkeypatch):
    host = RiskServiceHost(load_risk_service_config(setup(tmp_path)))
    monkeypatch.setattr(host, '_service_runtime', lambda *_a, **_k: pytest.fail('unexpected runtime'))
    host._start_account_facts(object())
    assert host._runtimes == [] and host.account_facts_observation_status() == 'DISABLED'
    assert host.close() is True


@pytest.mark.parametrize('extension', [None, 1])
def test_selected_host_refuses_missing_or_v1_extension_without_upgrading(tmp_path, extension):
    config = selected_config(tmp_path)
    seed_v4(config, extension=extension)
    host = RiskServiceHost(config)
    with pytest.raises(RiskServiceStartError, match='ACCOUNT_FACTS_V2_REQUIRED'):
        host.open_storage()
    assert host.close() is True
    import sqlite3
    with sqlite3.connect(config.risk_database) as db:
        meta = db.execute("SELECT count(*) FROM sqlite_master WHERE name='risk_r05_meta'").fetchone()[0]
        assert meta == (0 if extension is None else 1)
        if extension == 1:
            assert db.execute('SELECT version FROM risk_r05_meta').fetchone()[0] == 1


def test_selected_host_attaches_existing_v2_without_enabling(tmp_path):
    config = selected_config(tmp_path)
    seed_v4(config, extension=2)
    host = RiskServiceHost(config)
    host.open_storage()
    try:
        assert host.ledger._r05_enabled and host.ledger._r05_version == 2
        with host.ledger.transaction() as txn:
            assert txn.r05_source_binding() is None and txn.r05_progress() is None
    finally:
        assert host.close() is True


def test_uncertain_v2_attach_retains_original_fence(tmp_path, monkeypatch):
    config = selected_config(tmp_path)
    seed_v4(config, extension=2)
    host = RiskServiceHost(config)
    def uncertain(_ledger):
        raise RiskOrderJournalError('R05_OBSERVATION_COMMIT_UNCERTAIN')
    monkeypatch.setattr(PretradeLedger, 'attach_existing_r05_observations_v2', uncertain)
    with pytest.raises(RiskServiceStartError, match='STORAGE_INIT_COMMIT_UNCERTAIN'):
        host.open_storage()
    assert host._storage_init_uncertain and host._uncertain_storage is not None
    assert host.close() is False


def test_facts_unknown_latches_but_service_loop_continues(tmp_path):
    config = selected_config(tmp_path)
    clock = [10.0]
    host = RiskServiceHost(config, monotonic=lambda: clock[0])
    calls = []
    class Service:
        def serve_once(self, _timeout):
            calls.append('serve')
    class Facts:
        def poll_once(self):
            calls.append('facts')
            return SimpleNamespace(status='UNKNOWN')
    host.service_host = Service()
    host.ledger = SimpleNamespace(fence=object(), _r05_uncertain=False)
    host.pump = SimpleNamespace(poll=lambda: calls.append('events'))
    host.engine = SimpleNamespace(maintain=lambda: calls.append('maintain'))
    host._reconnect_observer = lambda: None
    host._account_facts_coordinator = Facts()
    host.account_facts_status = 'READY'
    host._next = {'observation': 0.0, 'maintain': 0.0, 'market': 0.0, 'account': 0.0,
                  'financial': 0.0, 'account_facts': 0.0}
    host.run_once()
    assert host.account_facts_observation_status() == 'UNKNOWN'
    clock[0] += 2
    host.run_once()
    assert calls.count('serve') == 2 and calls.count('facts') == 1
    assert calls.count('events') == 2 and calls.count('maintain') == 2


@pytest.mark.parametrize('failure', ['grant', 'missing_ack', 'missing_read', 'start'])
def test_partial_facts_start_missing_grant_keeps_original_runtime_for_cleanup(tmp_path, monkeypatch, failure):
    config = selected_config(tmp_path)
    seed_v4(config, extension=2)
    host = RiskServiceHost(config)
    host.open_storage()
    class Runtime:
        def start(self, **_kwargs):
            if failure == 'start':
                raise RuntimeError('synthetic startup failure')
            operations = ({'account.facts.read_after.v2'} if failure == 'missing_ack' else
                          {'account.facts.ack'} if failure == 'missing_read' else set())
            binding = SimpleNamespace(active=True, mount='upstream.account', operations=frozenset(operations))
            return SimpleNamespace(state='running', healthy=True, bindings=(binding,))
        def close(self, **_kwargs):
            return SimpleNamespace(state='failed', thread_alive=True, error_code='MAINTENANCE_FAILED',
                unregister_uncertainties=0, owner_cleanup_uncertainties=0, unknown_registration_count=0)
    runtime = Runtime()
    options = []
    def make(*_args, **kwargs):
        options.append(kwargs)
        return runtime
    monkeypatch.setattr(host, '_service_runtime', make)
    host._resource_owner = threading.current_thread()
    route = account_route(host.config)
    with pytest.raises(RiskServiceStartError, match='FACTS_UNAVAILABLE' if failure == 'start' else 'GRANT_REQUIRED'):
        host._start_account_facts(route)
    assert options == [{'strict_connection': True}]
    assert host._runtimes == [runtime] and host._account_facts_runtime is runtime
    assert host.close() is False and host._cleanup_uncertain
    assert runtime in host._cleanup_uncertain_resources


def seed_pending(host, route):
    import hashlib

    from test_account_order_observations import page

    from bt_api_py.gateway_account_facts import account_facts_consumer_ref

    peer = host._peer()
    binding = {'service_id': route.service_id, 'instance_id': route.instance_id,
        'peer_role': peer.role.value, 'peer_principal': peer.principal_ref, 'peer_actor': peer.actor_ref,
        'peer_delegate': peer.delegate_ref,
        'fingerprint': hashlib.sha256(host.config.gateway.server_public_key).hexdigest()}
    consumer = account_facts_consumer_ref(principal_ref=route.identity.principal_ref,
                                          identity=route.identity, scope=route.scope)
    with host.ledger.transaction() as txn:
        txn.apply_r05_bound_fact_page(source_binding=binding, **page(consumer_ref=consumer))


def test_pending_reopen_starts_paused_without_read_or_ack(tmp_path, monkeypatch):
    from tests.unit.test_iter63_account_facts_client import _client

    config = replace(selected_config(tmp_path), account_scope='synthetic-account')
    seed_v4(config, extension=2)
    first = RiskServiceHost(config)
    first.open_storage()
    seed_pending(first, account_route(config))
    assert first.close() is True
    host = RiskServiceHost(config, monotonic=lambda: 10.0)
    host.open_storage()
    route = account_route(config)
    _, runtime, snap, binding, _, _ = _client(monkeypatch)
    binding = replace(binding, mount='upstream.account', service_id=route.service_id,
        instance_id=route.instance_id, scope_ref=route.scope_ref, scope=route.scope,
        operations=ACCOUNT_FACTS_OBSERVATION_OPERATIONS, required=True)
    snap = replace(snap, identity=route.identity, bindings=(binding,))
    monkeypatch.setattr(runtime, 'start', lambda **_: snap)
    monkeypatch.setattr(runtime, 'status', lambda: snap)
    monkeypatch.setattr(runtime, 'call_bound', lambda *_a, **_k: pytest.fail('unexpected facts RPC'))
    monkeypatch.setattr(runtime, 'close', lambda **_: SimpleNamespace(state='closed', thread_alive=False,
        error_code=None, unregister_uncertainties=0, owner_cleanup_uncertainties=0, unknown_registration_count=0))
    monkeypatch.setattr(host, '_service_runtime', lambda *_a, **_k: runtime)
    try:
        host._start_account_facts(route)
        assert host.account_facts_observation_status() == 'RECOVERY_REQUIRED'
        host.service_host = SimpleNamespace(serve_once=lambda _: None, state='closed')
        host.pump = SimpleNamespace(poll=lambda: None)
        host.engine = SimpleNamespace(maintain=lambda: None)
        host._reconnect_observer = lambda: None
        host._next = {'observation': 0.0, 'maintain': 0.0, 'market': 0.0, 'account': 0.0,
                      'financial': 0.0, 'account_facts': 0.0}
        host.run_once()
        assert host.account_facts_observation_status() == 'RECOVERY_REQUIRED'
    finally:
        assert host.close() is True


@pytest.mark.parametrize('change', ['key', 'peer', 'instance'])
def test_changed_peer_or_key_fails_before_runtime_start(tmp_path, monkeypatch, change):
    import zmq

    config = replace(selected_config(tmp_path), account_scope='synthetic-account')
    seed_v4(config, extension=2)
    first = RiskServiceHost(config)
    first.open_storage()
    seed_pending(first, account_route(config))
    assert first.close() is True
    if change == 'key':
        new_key, _ = zmq.curve_keypair()
        gateway = replace(config.gateway, server_public_key=new_key)
    elif change == 'peer':
        gateway = replace(config.gateway, principal_ref='other-gateway-principal')
    else:
        gateway = replace(config.gateway, instance_id='other-gateway-instance')
    changed = replace(config, gateway=gateway)
    host = RiskServiceHost(changed)
    host.open_storage()
    monkeypatch.setattr(host, '_service_runtime', lambda *_a, **_k: pytest.fail('unexpected runtime or RPC'))
    try:
        with pytest.raises(RiskServiceStartError, match='SOURCE_MISMATCH'):
            host._start_account_facts(account_route(changed))
        assert host._runtimes == []
    finally:
        assert host.close() is True


def test_coordinator_changed_consumer_refuses_before_rpc(v2, tmp_path):
    from bt_api_py.gateway_account_facts_client import GatewayAccountFactsClient
    from bt_api_py.risk_account_facts_coordinator import RiskAccountFactsCoordinator
    from tests.unit.test_risk_account_facts_coordinator import risk_ledger

    client, runtime, calls, _, _ = v2
    with risk_ledger(tmp_path / 'risk.sqlite') as (ledger, journal):
        assert RiskAccountFactsCoordinator(client, ledger).poll_once().status == 'APPLIED'
        reopened = PretradeLedger(journal, ledger._fence, schema_version=4)
        reopened.attach_existing_r05_observations_v2()
        identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'other-principal', client._route.identity.actor_ref)
        route = replace(client._route, identity=identity)
        changed = GatewayAccountFactsClient(runtime, route=route, peer=client._bound_route.peer,
                                            fingerprint=client._expected_fingerprint)
        coordinator = RiskAccountFactsCoordinator(changed, reopened)
        count = len(calls)
        assert coordinator.poll_once().status == 'UNKNOWN'
        assert len(calls) == count
