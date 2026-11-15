"""Opt-in localhost CURVE with temporary journals and synthetic principals.

SOURCE v1 is only a local observation. This test does not establish v2
readiness, original strategy-principal provenance, or any old-writer fence.
"""
from dataclasses import replace
import hashlib
import json
import os
import queue
import sqlite3
import threading
import time

import pytest
import zmq

from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_execution.local_gateway_parent_preparation import LocalGatewayParentPreparation
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_parent_authority import GatewayParentAuthority
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.execution_parent_grant import QUERY, SUBMIT, PORT
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources, GatewayOwnerStartupCleanupError
from bt_api_py.gateway_parent_service import GatewayParentGrant, GatewayParentService, GatewayParentSettings, REGISTER_V2, SOURCE
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from bt_api_py.service_runtime import (
    BoundServiceTarget, ClientCurveKeys, ServiceCallOutcomeUnknownError, ServiceClientRuntime,
    ServiceRuntimeError, ServiceRuntimeRequestNotSentError, ZmqTransportFactory,
)
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime
from bt_api_transport_zmq.server import CurveClientCredentials, CurveServerCredentials
from bt_api_transport_zmq.service_v2 import ZmqServiceClient, ZmqServiceServer
from test_iter65_atomic_submission_intake import atomic  # noqa: F401
from test_iter65_fresh_submission_intake import KEY
from tests.unit.test_iter65_bound_runtime_curve import _config
from tests.unit.test_service_registry import _next_request, _register


pytestmark = pytest.mark.skipif(
    os.environ.get('BT_API_SYNTHETIC_CURVE_LOOPBACK') != '1' or not zmq.has('curve'),
    reason='requires explicit synthetic localhost CURVE sandbox opt-in',
)


@pytest.fixture
def local_curve(tmp_path, atomic, request):
    rig, store, lease = atomic
    mismatch = getattr(request, 'param', None) == 'mismatched-subject'
    if not mismatch:
        # Retire the fixture's previous identity before configuring a single
        # aligned strategy subject for the local Atomic intake.
        old_identity = rig.identity
        old_registration = rig.registration
        unregistered = rig.registry.handle(old_identity.principal_ref, _next_request('service.unregister', {
            'registration_id': old_registration['registration_id'],
            'service_epoch': old_registration['service_epoch'],
        }, identity=old_identity, scope=rig.scope))
        assert unregistered['ok']
        assert rig.registry.revoke_execution_parent(rig.grant)
        rig.identity = replace(old_identity, actor_ref=rig.scope.strategy_ref)
        rig.grant = replace(rig.grant, identity=rig.identity)
        with rig.registry._lock:
            grants = dict(rig.registry._grants)
            grants[rig.identity.principal_ref] = tuple(
                replace(grant, identity=rig.identity) for grant in grants[rig.identity.principal_ref])
            rig.registry._grants = grants
        rig.registry.configure_execution_parents(grants=(rig.grant,))
        local_registration = _register(rig.registry, identity=rig.identity, scope=rig.scope,
            principal=rig.identity.principal_ref, instance_id='exec-a', port=PORT, scope_ref='scope-a',
            operations=(SUBMIT, QUERY), key='aligned-strategy-subject')
        assert local_registration['ok'], local_registration
        rig.registration = local_registration['result']
    request = rig.parent()
    assert rig.call(SUBMIT, request)['ok']
    preparation = LocalGatewayParentPreparation(host=rig.host, scope=rig.target, lease=lease,
                                                 wire_scope=rig.scope, identity=rig.identity)
    preparation.prepare(request['spec']['parent_ref'])
    scope_a = rig.scope
    scope_b = replace(scope_a, strategy_ref='strategy-b', run_id='run-b')
    service_identity = WireIdentity(WireRole.SERVICE, 'curve-execution', 'execution')
    strategy_a = WireIdentity(WireRole.STRATEGY_CLIENT,
        'curve-strategy-a' if mismatch else rig.identity.principal_ref, scope_a.strategy_ref)
    strategy_b = WireIdentity(WireRole.STRATEGY_CLIENT, 'curve-strategy-b', scope_b.strategy_ref)
    peer = WireIdentity(WireRole.SERVICE, 'curve-gateway', 'gateway')
    server_public, server_secret = zmq.curve_keypair()
    service_public, service_secret = zmq.curve_keypair()
    strategy_public_a, strategy_secret_a = zmq.curve_keypair()
    strategy_public_b, strategy_secret_b = zmq.curve_keypair()
    guards = tmp_path / 'gateway-guards'
    guards.mkdir(mode=0o700)
    parts, startup_errors = {}, []

    def factory():
        guard = None
        try:
            guard = LocalAccountSessionGuard(guards, scope_a.account_ref, 'synthetic-owner')
            generation = guard.acquire()
            journal = GatewayDirectCommandJournal.create(tmp_path / 'gateway.sqlite', scope_a.account_ref,
                                                         generation=generation)
            journal.enable_controls(guard)
            journal.enable_strategy_ledger(guard)
            journal.enable_account_extensions(guard)
            journal.enable_parent_authority(guard)
            journal.enable_parent_original_runs(guard)
            authority = GatewayParentAuthority(journal, guard, now_ns=lambda: 100, trading_day=lambda: '20261109')
            dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, lambda *_: None,
                                                 lambda _: ('key',), lambda _: True)
            parts.update(journal=journal, authority=authority)
            return GatewayDirectOwnerResources(dispatcher, journal, guard.release)
        except BaseException as error:
            startup_errors.append(error)
            raise GatewayOwnerStartupCleanupError(guard.release if guard is not None else lambda: None) from error

    owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=2, busy_timeout_seconds=2)
    registry = None
    clients, runtimes = [], []
    ready, failures, stop, seen, results, hook = queue.Queue(), queue.Queue(), threading.Event(), [], [], [None]
    server_thread = None
    try:
        assert owner.start(3), startup_errors
        operations = frozenset({SOURCE, REGISTER_V2})
        resource_a = ResourceGrant('synthetic-instance', 'service.direct_write', 'synthetic-scope',
                                   replace(scope_a, run_id=None), operations)
        resource_b = replace(resource_a, scope=replace(scope_b, run_id=None))
        strategy_resource_a = replace(resource_a, operations=frozenset({'direct.reserve'}))
        strategy_resource_b = replace(resource_b, operations=frozenset({'direct.reserve'}))
        cap = ServiceCapability('synthetic-instance', 'service.direct_write',
            operations | {'direct.reserve'}, frozenset({REGISTER_V2, 'direct.reserve'}))
        registry = ServiceRegistry(':memory:', service_id='synthetic-gateway', capabilities=(cap,),
            grants={service_identity.principal_ref: (PrincipalGrant(service_identity, (resource_a, resource_b)),),
                    strategy_a.principal_ref: (PrincipalGrant(strategy_a, (strategy_resource_a,)),),
                    strategy_b.principal_ref: (PrincipalGrant(strategy_b, (strategy_resource_b,)),)},
            policy_version='policy', requirements_version='requirements')
        grants = tuple(GatewayParentGrant(service_identity, scope.account_ref, scope.strategy_ref,
            scope.execution_domain_ref, 'execution', 'policy-v1', frozenset({KEY}), operations)
            for scope in (scope_a, scope_b))
        parent = GatewayParentService(GatewayParentSettings(grants), owner=owner, journal=parts['journal'],
            authority=parts['authority'], instance_id='synthetic-instance', scope_ref='synthetic-scope')
        parent.configure(registry)
        registry.register_handler('synthetic-instance', 'direct.reserve', lambda *_: {})
        for operation in operations:
            registry.register_handler('synthetic-instance', operation,
                lambda context, arguments, operation=operation: parent.handle(operation, context, arguments))

        def handler(principal, envelope):
            seen.append((principal, envelope.header.operation, envelope.header.scope, envelope.header.identity))
            result = registry.handle(principal, envelope)
            if envelope.header.operation == SOURCE:
                results.append(result)
            if envelope.header.operation == SOURCE and hook[0] is not None:
                hook[0](result)
            return result

        def serve():
            server = None
            try:
                server = ZmqServiceServer('tcp://127.0.0.1:*', peer,
                    {service_public: service_identity.principal_ref, strategy_public_a: strategy_a.principal_ref,
                     strategy_public_b: strategy_b.principal_ref}, handler,
                    credentials=CurveServerCredentials(server_public, server_secret))
                ready.put(server.endpoint)
                while not stop.is_set():
                    server.serve_once(timeout_ms=20)
            except BaseException as error:
                failures.put(error)
                ready.put(None)
            finally:
                if server is not None:
                    server.close()
        server_thread = threading.Thread(target=serve, daemon=True)
        server_thread.start()
        endpoint = ready.get(timeout=5)
        assert endpoint and endpoint.startswith('tcp://127.0.0.1:')

        def register_strategy(identity, scope, public, secret):
            client = ZmqServiceClient(endpoint, peer,
                credentials=CurveClientCredentials(server_public, public, secret))
            clients.append(client)
            response = client.request(_next_request('service.register', {
                'registration_key': 'key-' + identity.principal_ref,
                'client_session_id': 'session-' + identity.principal_ref,
                'instance_id': 'synthetic-instance', 'port': 'service.direct_write',
                'scope_ref': 'synthetic-scope', 'operations': ['direct.reserve'],
            }, identity=identity, scope=scope), timeout=3).payload
            assert response['ok'], response
            return response['result']
        registration_a = register_strategy(strategy_a, scope_a, strategy_public_a, strategy_secret_a)
        registration_b = register_strategy(strategy_b, scope_b, strategy_public_b, strategy_secret_b)

        def start_runtime(scope):
            raw = _config(endpoint).to_public_dict()
            raw['clients']['gateway']['settings']['bindings'][0]['account_ref'] = scope.account_ref
            keys = ClientCurveKeys(server_public, service_public, service_secret)
            owners = []
            def build():
                runtime = ServiceClientRuntime(parse_service_config(raw), identity=service_identity,
                    scope_resolver=lambda _: scope, peer_resolver=lambda _: peer,
                    transport_factory=ZmqTransportFactory({'synthetic-key': keys}, strict_single_connection=True),
                    run_id=scope.run_id, requested_operations={'upstream.direct_write': operations})
                owners.append(runtime)
                return runtime
            threaded = ThreadedServiceClientRuntime(build)
            runtimes.append(threaded)
            binding, = threaded.start(timeout_seconds=5).bindings
            target = BoundServiceTarget('upstream.direct_write', 'service.direct_write', 'synthetic-gateway',
                'synthetic-instance', 'synthetic-scope', scope, service_identity, scope.run_id, 'gateway-transport',
                'synthetic-key', endpoint, peer, hashlib.sha256(server_public).hexdigest(),
                binding.registration_id, binding.service_epoch)
            return threaded, target, owners[0]

        threaded, target, runtime = start_runtime(scope_a)
        yield {'rig': rig, 'store': store, 'lease': lease, 'request': request, 'preparation': preparation,
               'threaded': threaded, 'target': target, 'runtime': runtime, 'registry': registry,
               'registration_a': registration_a, 'registration_b': registration_b, 'service_identity': service_identity,
               'grant_a': grants[0], 'strategy_a': strategy_a, 'journal': parts['journal'],
               'scope_b': scope_b, 'start_runtime': start_runtime, 'seen': seen,
               'source_results': results, 'hook': hook,
               'db': tmp_path / 'gateway.sqlite'}
    finally:
        for runtime in reversed(runtimes):
            runtime.close(timeout_seconds=5)
        for client in clients:
            client.close()
        stop.set()
        if server_thread is not None:
            server_thread.join(timeout=5)
            assert not server_thread.is_alive()
        if registry is not None:
            registry.close()
        assert owner.close(5)
        assert failures.empty()


def parent_counts(env):
    with sqlite3.connect(env['db']) as db:
        return tuple(db.execute('SELECT (SELECT count(*) FROM direct_execution_parents), '
                                '(SELECT count(*) FROM direct_execution_parent_operations)').fetchone())


def source_calls(env):
    return [item for item in env['seen'] if item[1] == SOURCE]


def test_real_curve_prepared_source_is_read_only_and_register_remains_closed(local_curve):
    env = local_curve
    cap = env['threaded'].capture_prepared_parent_source(
        env['target'], env['preparation'], env['request']['spec']['parent_ref'])
    assert not cap._revoked.is_set() and cap in env['runtime']._live_parent_capabilities
    entry = env['runtime']._live_parent_capabilities[cap][1]
    assert entry[8].operation == SOURCE
    local_identity = env['preparation'].read_prepared(env['request']['spec']['parent_ref']).identity
    assert local_identity['principal_ref'] == env['strategy_a'].principal_ref
    assert local_identity['actor_ref'] == env['strategy_a'].actor_ref == env['rig'].scope.strategy_ref
    local_record = json.loads(env['preparation'].read_prepared(env['request']['spec']['parent_ref']).canonical_record)
    assert env['source_results'][0]['result']['strategy_subject'] == {
        'identity': local_record['intake']['identity'], 'scope': local_record['intake']['wire_scope']}
    source = env['source_results'][0]['result']['result']
    with sqlite3.connect(env['db']) as db:
        revision = db.execute('SELECT revision FROM direct_journal_meta').fetchone()[0]
    assert entry[7] == (source['account_scope'], source['strategy_scope'], source['execution_domain_ref'],
                        source['journal_id'], source['gateway_generation'], source['journal_revision'])
    assert (source['journal_id'], source['gateway_generation'], source['journal_revision']) == (
        env['journal'].journal_id, env['journal'].generation, revision)
    assert env['source_results'][0]['ok'] and env['source_results'][0]['result']['status'] == 'OBSERVED'
    assert len(source_calls(env)) == 1 and parent_counts(env) == (0, 0)
    spec = json.loads(env['preparation'].read_prepared(env['request']['spec']['parent_ref']).gateway_spec_json)
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        env['threaded'].register_live_parent(cap, env['target'], spec)
    assert cap._revoked.is_set() and parent_counts(env) == (0, 0)
    assert not [item for item in env['seen'] if item[1] == REGISTER_V2]


@pytest.mark.parametrize('local_curve', ['mismatched-subject'], indirect=True)
def test_source_observation_rejects_different_original_strategy_subject(local_curve):
    env = local_curve
    original = env['preparation'].read_prepared(env['request']['spec']['parent_ref']).identity
    assert (original['principal_ref'], original['actor_ref']) != (
        env['strategy_a'].principal_ref, env['strategy_a'].actor_ref)
    # Gateway reports its current authenticated subject; the Execution caller
    # must reject it when it differs from the original local intake.
    with pytest.raises(ServiceCallOutcomeUnknownError):
        env['threaded'].capture_prepared_parent_source(
            env['target'], env['preparation'], env['request']['spec']['parent_ref'])
    assert not env['runtime']._live_parent_capabilities
    assert env['source_results'][0]['ok'] and env['source_results'][0]['result']['status'] == 'OBSERVED'
    observed = env['source_results'][0]['result']['strategy_subject']['identity']
    assert (observed['principal_ref'], observed['actor_ref']) == (
        env['strategy_a'].principal_ref, env['strategy_a'].actor_ref)
    assert parent_counts(env) == (0, 0)


def test_real_curve_valid_strategy_b_cannot_use_local_strategy_a_preparation(local_curve):
    env = local_curve
    registrations = [item for item in env['seen'] if item[1] == 'service.register'
                     and item[0] == 'curve-strategy-b']
    assert len(registrations) == 1 and registrations[0][3].actor_ref == env['scope_b'].strategy_ref
    assert registrations[0][2] == env['scope_b']
    with env['registry']._lock:
        row = env['registry']._db.execute('SELECT expires_at FROM sessions WHERE registration_id=?',
            (env['registration_b']['registration_id'],)).fetchone()
        assert row is not None and row['expires_at'] > time.time()
    threaded, target, _ = env['start_runtime'](env['scope_b'])
    # Independently prove that B's own live Gateway route can observe SOURCE.
    assert threaded.call_bound(target, SOURCE, {'account_scope': env['scope_b'].account_ref,
        'strategy_scope': env['scope_b'].strategy_ref,
        'execution_domain_ref': env['scope_b'].execution_domain_ref})['status'] == 'OBSERVED'
    before = len(source_calls(env))
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        threaded.capture_prepared_parent_source(target, env['preparation'], env['request']['spec']['parent_ref'])
    assert len(source_calls(env)) == before and parent_counts(env) == (0, 0)


@pytest.mark.parametrize('failure', ['strategy-expired', 'grant-revoked'])
def test_real_curve_lost_strategy_or_service_grant_never_issues_capability(local_curve, failure):
    env = local_curve
    registry = env['registry']
    with registry._lock:
        if failure == 'strategy-expired':
            registry._db.execute('UPDATE sessions SET expires_at=0 WHERE registration_id=?',
                                 (env['registration_a']['registration_id'],))
        else:
            key = ('synthetic-instance', 'synthetic-scope', env['service_identity'].principal_ref,
                   env['rig'].scope.account_ref, env['rig'].scope.strategy_ref)
            registry._gateway_parent_grants[key] = replace(env['grant_a'], operations=frozenset({REGISTER_V2}))
    with pytest.raises((ServiceRuntimeError, ThreadedRuntimeError)):
        env['threaded'].capture_prepared_parent_source(
            env['target'], env['preparation'], env['request']['spec']['parent_ref'])
    assert len(source_calls(env)) == 1 and len(env['source_results']) == 1
    response = env['source_results'][0]
    assert not response['ok'] or response['result'].get('status') != 'OBSERVED'
    assert not env['runtime']._live_parent_capabilities and parent_counts(env) == (0, 0)


@pytest.mark.parametrize('change', ['lease', 'record'])
def test_real_curve_local_change_during_source_discards_capability(local_curve, change):
    env = local_curve
    def mutate(result):
        assert result['ok'] and result['result']['status'] == 'OBSERVED'
        with env['store']._transaction() as cursor:
            if change == 'lease':
                cursor.execute('UPDATE execution_writer_leases SET expires_at_ns=1 WHERE owner_id=?',
                               (env['lease'].owner_id,))
            else:
                cursor.execute('UPDATE execution_parent_records SET request_sha256=? WHERE parent_id=?',
                               ('f' * 64, env['request']['spec']['parent_ref']))
    env['hook'][0] = mutate
    with pytest.raises(ServiceCallOutcomeUnknownError):
        env['threaded'].capture_prepared_parent_source(
            env['target'], env['preparation'], env['request']['spec']['parent_ref'])
    assert len(source_calls(env)) == 1 and not env['runtime']._live_parent_capabilities
    assert parent_counts(env) == (0, 0)
