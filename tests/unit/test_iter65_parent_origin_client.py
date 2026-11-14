"""Synthetic client-local source receipts and v2 requests; no network or account."""

import copy
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_delegate_service_client import GatewayDelegateRoute
from bt_api_py.gateway_parent_client import (
    CANCEL_V2,
    OPERATION_V2,
    REGISTER_V2,
    SOURCE,
    GatewayParentServiceClient,
    _trusted_parent_query_target,
)
from bt_api_py.service_runtime import ClientCurveKeys
from bt_api_py.service_runtime_thread import (
    BindingSnapshot,
    RuntimeSnapshot,
    ThreadedServiceClientRuntime,
)
from tests.unit.test_iter63_gateway_parent_client import _spec
from tests.unit.test_iter65_gateway_parent_bound_v2 import service  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def _enable_original_runs_for_real_gateway(request):
    if 'service' in request.fixturenames:
        env = request.getfixturevalue('service')
        call = env['owner'].submit_service_call(lambda: env['journal'].enable_parent_original_runs(
            env['parent_service'].authority._guard))
        call.result(timeout_seconds=2)
from tests.unit.test_iter65_parent_bound_query import config


class _Host:
    def __init__(self):
        self.calls = []
        self.claims = set()
        self.allowed = True

    def prepare_parent_dispatch(self, spec, operation_id, kind, expected_version, anchor):
        self.calls.append((copy.deepcopy(spec), operation_id, kind, expected_version, copy.deepcopy(anchor)))
        key = (spec['parent_ref'], operation_id)
        if not self.allowed or key in self.claims:
            return False
        self.claims.add(key)
        return True


@pytest.fixture
def bound_host(monkeypatch, request):
    """Real Authority identity; claim outcome is an explicitly synthetic seam."""
    from bt_api_execution.contracts import ExecutionScope
    from bt_api_execution.parent_authority import ParentAuthority
    from bt_api_execution.store import SqliteExecutionStore

    def install(client):
        store = SqliteExecutionStore(':memory:')
        request.addfinalizer(store.close)
        scope = ExecutionScope('FAKE', 'synthetic', 'account', 'strategy', '20300101')
        lease = store.acquire_or_renew_lease(scope, 'writer', ttl_ns=300_000_000_000)
        authority = ParentAuthority(store, scope, lease, admission=lambda *_: True,
                                    _session_parent_route=('account', 'strategy', 'domain'))
        authority.bind_gateway(client)
        client._host = authority
        state = _Host()
        monkeypatch.setattr(ParentAuthority, 'prepare_parent_dispatch',
                            lambda _host, *args: state.prepare_parent_dispatch(*args))
        return state

    return install


def _client(monkeypatch, *, run='run', epoch='epoch'):
    identity = WireIdentity(WireRole.SERVICE, 'execution-principal', 'execution')
    route = GatewayDelegateRoute('upstream.direct_write', 'gateway', 'instance', 'scope',
        WireScope('domain', account_ref='account', strategy_ref='strategy', run_id=run), identity)
    peer = WireIdentity(WireRole.SERVICE, 'gateway-principal', 'gateway-actor')
    profiles = {'security': ClientCurveKeys(b'synthetic-server-key', b'synthetic-client', b'synthetic-secret')}
    target = _trusted_parent_query_target(config(), route, peer, profiles)
    runtime = ThreadedServiceClientRuntime(lambda: None)
    host = _Host()
    client = GatewayParentServiceClient(runtime, route=route, host=host, _query_target=target)
    operations = frozenset({SOURCE, REGISTER_V2, OPERATION_V2, CANCEL_V2})
    binding = BindingSnapshot('registration', epoch, True, route.mount, route.service_id,
                              route.instance_id, route.scope_ref, route.scope, operations, True)
    snapshot = RuntimeSnapshot('running', True, True, None, (binding,), 0, 0, identity, run)
    monkeypatch.setattr(runtime, 'status', lambda: snapshot)
    monkeypatch.setattr(runtime, 'call', lambda *_a, **_k: pytest.fail('ordinary call used'))
    return client, runtime, host, route, snapshot


def _source_response(*, journal='journal-1', revision=7, generation='gateway-gen-1'):
    return {'status': 'OBSERVED', 'schema_version': 1, 'operation': SOURCE, 'result': {
        'schema_version': 1, 'account_scope': 'account', 'strategy_scope': 'strategy',
        'execution_domain_ref': 'domain', 'journal_id': journal, 'gateway_generation': generation,
        'journal_revision': revision}}


def _readiness_response():
    return {'status': 'OBSERVED', 'schema_version': 2, 'operation': SOURCE,
            'result': _source_response()['result'],
            'checked_operations': [SOURCE, REGISTER_V2, OPERATION_V2, CANCEL_V2]}


def _operation_response(client, operation, *, journal='journal-1', revision=8, generation='gateway-gen-1'):
    spec = client._spec(_spec())
    state = 'CANCEL_REQUESTED' if operation == CANCEL_V2 else 'ACTIVE'
    result = {'spec': spec, 'spec_digest': gateway_payload_digest(spec),
              'execution_principal': 'execution-principal', 'execution_actor_ref': 'execution',
              'state': state, 'version': 2 if state == 'CANCEL_REQUESTED' else 1}
    if operation == CANCEL_V2:
        result['targets'] = []
    if operation == OPERATION_V2:
        result = {'operation_id': 'register-1', 'kind': 'REGISTER', 'result': result}
    return {'status': 'OBSERVED', 'schema_version': 2, 'operation': operation, 'result': result,
            'source': {'account_scope': 'account', 'journal_id': journal,
                       'gateway_generation': generation, 'journal_revision': revision}}


def _observed_anchor(monkeypatch, client, runtime):
    monkeypatch.setattr(runtime, 'call_bound', lambda *_a, **_k: _source_response())
    receipt = client.capture_parent_source(_spec())
    anchor = client.verify_parent_source(receipt, _spec())
    assert anchor is not None
    return receipt, anchor


def test_source_is_bound_and_returns_detached_restart_stable_anchor(monkeypatch):
    client, runtime, _, route, _ = _client(monkeypatch)
    calls = []
    def bound(target, operation, arguments, *, timeout_seconds):
        calls.append((target, operation, arguments))
        return _source_response()
    monkeypatch.setattr(runtime, 'call_bound', bound)
    receipt = client.capture_parent_source(_spec())
    anchor = client.verify_parent_source(receipt, _spec())
    assert set(anchor) == {'account_scope', 'journal_id', 'minimum_revision', 'gateway_generation', 'trusted_target'}
    assert anchor['journal_id'] == 'journal-1' and anchor['minimum_revision'] == 7
    assert anchor['gateway_generation'] == 'gateway-gen-1'
    assert len(anchor['trusted_target']) == 18
    assert not {'run_id', 'registration_id', 'service_epoch'} & set(anchor['trusted_target'])
    assert calls[0][1:] == (SOURCE, {'account_scope': 'account', 'strategy_scope': 'strategy',
                                   'execution_domain_ref': 'domain'})
    assert calls[0][0].scope == route.scope and calls[0][0].registration_id == 'registration'
    anchor['journal_id'] = 'mutated-copy'
    assert client.verify_parent_source(receipt, _spec())['journal_id'] == 'journal-1'
    assert client.verify_parent_source(dict(anchor), _spec()) is None


def test_scope_probe_is_cross_thread_read_only_and_not_a_source_receipt(monkeypatch):
    client, runtime, host, _, _ = _client(monkeypatch)
    calls, results = [], []
    def bound(target, operation, arguments, *, timeout_seconds):
        calls.append((target, operation, arguments, timeout_seconds))
        return _readiness_response()
    monkeypatch.setattr(runtime, 'call_bound', bound)
    thread = threading.Thread(target=lambda: results.append(client.probe_scope_source(timeout_seconds=2.0)), daemon=True)
    thread.start()
    thread.join(3)
    assert not thread.is_alive() and results == [True]
    assert calls[0][1] == SOURCE and 0 < calls[0][3] <= 2.0
    assert calls[0][2]['readiness_version'] == 2
    assert host.calls == [] and client.verify_parent_source(results[0], _spec()) is None


def test_source_and_probe_do_not_accept_mutated_outgoing_scope(monkeypatch):
    client, runtime, _, _, _ = _client(monkeypatch)
    def mutate(_target, _operation, arguments, *, timeout_seconds):
        arguments['strategy_scope'] = 'different-strategy'
        response = _readiness_response() if 'readiness_version' in arguments else _source_response()
        response['result']['strategy_scope'] = 'different-strategy'
        return response
    monkeypatch.setattr(runtime, 'call_bound', mutate)
    assert client.capture_parent_source(_spec()) is None
    assert client.probe_scope_source(timeout_seconds=2.0) is False


@pytest.mark.parametrize('failure', ['fork', 'closed', 'expired', 'invalid_timeout', 'bad_source', 'route_changed'])
def test_scope_probe_fails_closed_without_a_reusable_receipt(monkeypatch, failure):
    from bt_api_py import gateway_parent_client

    client, runtime, host, route, snapshot = _client(monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(gateway_parent_client, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    calls = []
    def bound(_target, operation, _arguments, *, timeout_seconds):
        calls.append(operation)
        if failure == 'closed':
            monkeypatch.setattr(runtime, 'status', lambda: replace(snapshot, state='closed', healthy=False))
        elif failure == 'expired':
            clock[0] = 102.0
        elif failure == 'route_changed':
            object.__setattr__(route, 'instance_id', 'other')
        raw = _readiness_response()
        if failure == 'bad_source':
            raw['result']['account_scope'] = 'other'
        return raw
    monkeypatch.setattr(runtime, 'call_bound', bound)
    if failure == 'fork':
        monkeypatch.setattr(gateway_parent_client, 'os', SimpleNamespace(getpid=lambda: client._pid + 1))
    timeout = float('nan') if failure == 'invalid_timeout' else 1.0
    assert client.probe_scope_source(timeout_seconds=timeout) is False
    assert host.calls == []
    assert calls == ([] if failure in {'fork', 'invalid_timeout'} else [SOURCE])


@pytest.mark.parametrize('failure', [
    'v1_response', 'missing_operation', 'wrong_order', 'duplicate', 'extra_operation',
    'wrong_operation', 'boolean_version', 'missing_checked', 'extra_field', 'unknown',
])
def test_scope_probe_requires_exact_readiness_v2_response(monkeypatch, failure):
    client, runtime, _, _, _ = _client(monkeypatch)
    response = _readiness_response()
    if failure == 'v1_response':
        response = _source_response()
    elif failure == 'missing_operation':
        response['checked_operations'].pop()
    elif failure == 'wrong_order':
        response['checked_operations'][1:3] = reversed(response['checked_operations'][1:3])
    elif failure == 'duplicate':
        response['checked_operations'][3] = REGISTER_V2
    elif failure == 'extra_operation':
        response['checked_operations'].append('direct.parent.query')
    elif failure == 'wrong_operation':
        response['operation'] = OPERATION_V2
    elif failure == 'boolean_version':
        response['schema_version'] = True
    elif failure == 'missing_checked':
        response.pop('checked_operations')
    elif failure == 'extra_field':
        response['unexpected'] = 'value'
    else:
        response = {'status': 'UNKNOWN'}
    monkeypatch.setattr(runtime, 'call_bound', lambda *_a, **_k: response)
    assert client.probe_scope_source(timeout_seconds=2.0) is False


@pytest.mark.parametrize('operation', [SOURCE, REGISTER_V2, OPERATION_V2, CANCEL_V2])
@pytest.mark.parametrize('phase', ['before', 'after'])
def test_scope_probe_fails_if_any_v2_operation_disappears(monkeypatch, operation, phase):
    client, runtime, _, _, snapshot = _client(monkeypatch)
    binding = snapshot.bindings[0]
    narrowed = replace(snapshot, bindings=(replace(binding, operations=binding.operations - {operation}),))
    if phase == 'before':
        monkeypatch.setattr(runtime, 'status', lambda: narrowed)
    def bound(_target, _operation, _arguments, *, timeout_seconds):
        if phase == 'after':
            monkeypatch.setattr(runtime, 'status', lambda: narrowed)
        return _readiness_response()
    monkeypatch.setattr(runtime, 'call_bound', bound)
    assert client.probe_scope_source(timeout_seconds=2.0) is False


def test_scope_probe_fails_if_current_registration_changes_during_call(monkeypatch):
    client, runtime, _, _, snapshot = _client(monkeypatch)
    def bound(_target, _operation, _arguments, *, timeout_seconds):
        binding = replace(snapshot.bindings[0], service_epoch='new-epoch')
        monkeypatch.setattr(runtime, 'status', lambda: replace(snapshot, bindings=(binding,)))
        return _readiness_response()
    monkeypatch.setattr(runtime, 'call_bound', bound)
    assert client.probe_scope_source(timeout_seconds=2.0) is False


@pytest.mark.parametrize('change', ['source', 'spec', 'registration', 'target', 'foreign'])
def test_source_receipt_rejects_tampering_cross_client_and_binding_change(monkeypatch, change):
    client, runtime, _, route, snapshot = _client(monkeypatch)
    receipt, _ = _observed_anchor(monkeypatch, client, runtime)
    spec = _spec()
    if change == 'source':
        receipt._value['journal_id'] = 'other'
    elif change == 'spec':
        spec['quantity'] = '2'
    elif change == 'registration':
        altered = replace(snapshot.bindings[0], service_epoch='new-epoch')
        monkeypatch.setattr(runtime, 'status', lambda: replace(snapshot, bindings=(altered,)))
    elif change == 'target':
        object.__setattr__(route, 'instance_id', 'other-instance')
    else:
        client, _, _, _, _ = _client(monkeypatch)
    assert client.verify_parent_source(receipt, spec) is None


def test_source_receipt_cannot_be_rebound_to_changed_registration(monkeypatch):
    client, runtime, _, _, snapshot = _client(monkeypatch)
    receipt, _ = _observed_anchor(monkeypatch, client, runtime)
    altered = replace(snapshot.bindings[0], registration_id='new-registration')
    monkeypatch.setattr(runtime, 'status', lambda: replace(snapshot, bindings=(altered,)))
    receipt._registration = ('new-registration', 'epoch')
    assert client.verify_parent_source(receipt, _spec()) is None


@pytest.mark.parametrize('source_change', [
    {'account_scope': 'other'}, {'strategy_scope': 'other'}, {'execution_domain_ref': 'other'},
    {'journal_id': ''}, {'gateway_generation': ''}, {'journal_revision': -1},
    {'journal_revision': True}, {'schema_version': True}, {'unexpected': 'value'},
])
def test_source_rejects_untrusted_or_malformed_response(monkeypatch, source_change):
    client, runtime, _, _, _ = _client(monkeypatch)
    response = _source_response()
    response['result'].update(source_change)
    monkeypatch.setattr(runtime, 'call_bound', lambda *_a, **_k: response)
    assert client.capture_parent_source(_spec()) is None


def test_register_requires_host_claim_and_never_resends(monkeypatch, bound_host):
    client, runtime, _, _, _ = _client(monkeypatch)
    host = bound_host(client)
    _, anchor = _observed_anchor(monkeypatch, client, runtime)
    calls = []
    def bound(target, operation, arguments, *, timeout_seconds):
        assert host.calls and host.calls[-1][1:4] == ('register-1', 'REGISTER', None)
        calls.append((target, operation, copy.deepcopy(arguments)))
        return _operation_response(client, operation)
    monkeypatch.setattr(runtime, 'call_bound', bound)
    receipt = client.register_parent_bound(_spec(), 'register-1', anchor)
    assert client.verify_parent_bound(receipt, _spec(), 'register-1', anchor) == 'ACCEPTED'
    assert client.verify(receipt, _spec(), 'register-1') is None
    assert calls[0][1] == REGISTER_V2
    assert calls[0][2]['expected_source'] == {
        'account_scope': 'account', 'journal_id': 'journal-1',
        'gateway_generation': 'gateway-gen-1', 'minimum_revision': 7}
    assert client.register_parent_bound(_spec(), 'register-1', anchor) is None and len(calls) == 1


def test_mutated_outgoing_register_spec_does_not_authorize_a_receipt(monkeypatch, bound_host):
    client, runtime, _, _, _ = _client(monkeypatch)
    host = bound_host(client)
    _, anchor = _observed_anchor(monkeypatch, client, runtime)
    def mutate(_target, operation, arguments, *, timeout_seconds):
        arguments['spec']['quantity'] = '2'
        response = _operation_response(client, operation)
        response['result']['spec'] = arguments['spec']
        response['result']['spec_digest'] = gateway_payload_digest(arguments['spec'])
        return response
    monkeypatch.setattr(runtime, 'call_bound', mutate)
    assert client.register_parent_bound(_spec(), 'register-1', anchor) is None
    assert host.calls[0][0]['quantity'] == '1'


@pytest.mark.parametrize('failure', ['host_refused', 'host_mutates_anchor', 'host_mutates_spec',
                                     'missing_target', 'bad_anchor', 'gateway_rebound'])
def test_register_fails_closed_before_rpc(monkeypatch, bound_host, failure):
    client, runtime, _, route, _ = _client(monkeypatch)
    host = bound_host(client)
    _, anchor = _observed_anchor(monkeypatch, client, runtime)
    monkeypatch.setattr(runtime, 'call_bound', lambda *_a, **_k: pytest.fail('v2 request sent'))
    if failure == 'host_refused':
        host.allowed = False
    elif failure == 'host_mutates_anchor':
        def corrupt(_spec, _op, _kind, _version, value):
            value['journal_id'] = 'other'
            return True
        monkeypatch.setattr(host, 'prepare_parent_dispatch', corrupt)
    elif failure == 'host_mutates_spec':
        def corrupt(spec, _op, _kind, _version, _anchor):
            spec['quantity'] = '2'
            return True
        monkeypatch.setattr(host, 'prepare_parent_dispatch', corrupt)
    elif failure == 'missing_target':
        object.__setattr__(route, 'service_id', 'other')
    elif failure == 'bad_anchor':
        anchor['trusted_target']['service_id'] = 'other'
    else:
        client._host.gateway = object()
    assert client.register_parent_bound(_spec(), 'register-1', anchor) is None


def test_duck_typed_host_cannot_authorize_bound_write(monkeypatch):
    client, runtime, host, _, _ = _client(monkeypatch)
    _, anchor = _observed_anchor(monkeypatch, client, runtime)
    monkeypatch.setattr(runtime, 'call_bound', lambda *_a, **_k: pytest.fail('fake host sent a request'))
    assert client.register_parent_bound(_spec(), 'register-1', anchor) is None
    assert host.calls == []
    assert client.cancel_parent_bound(_spec(), 'cancel-1', 1, anchor) is None
    assert host.calls == []


@pytest.mark.parametrize('change', [
    {'journal': 'other'}, {'revision': 6}, {'revision': True}, {'generation': ''},
    {'generation': 'new-gateway-generation'},
])
def test_gateway_different_journal_low_revision_or_invalid_source_never_resolves(monkeypatch, change):
    client, runtime, _, _, _ = _client(monkeypatch)
    _, anchor = _observed_anchor(monkeypatch, client, runtime)
    monkeypatch.setattr(runtime, 'call_bound', lambda _target, operation, *_a, **_k:
                        _operation_response(client, operation, **change))
    assert client.query_operation_bound(_spec(), 'register-1', anchor) is None


def test_sdk_query_same_generation_after_registration_change_is_not_continuity_proof(monkeypatch):
    client, runtime, _, _, _ = _client(monkeypatch)
    _, anchor = _observed_anchor(monkeypatch, client, runtime)
    restarted, restarted_runtime, _, _, _ = _client(monkeypatch, run='new-run', epoch='new-epoch')
    monkeypatch.setattr(restarted_runtime, 'call_bound', lambda _target, operation, *_a, **_k:
                        _operation_response(restarted, operation, generation='gateway-gen-1'))
    receipt = restarted.query_operation_bound(_spec(), 'register-1', anchor)
    assert restarted.verify_parent_bound(receipt, _spec(), 'register-1', anchor) == 'ACCEPTED'
    assert client.verify_parent_bound(receipt, _spec(), 'register-1', anchor) is None


@pytest.mark.parametrize('change', ['receipt', 'source', 'anchor', 'spec', 'registration', 'operation', 'plain'])
def test_bound_receipt_rejects_mutation_and_registration_change(monkeypatch, change):
    client, runtime, _, _, snapshot = _client(monkeypatch)
    _, anchor = _observed_anchor(monkeypatch, client, runtime)
    monkeypatch.setattr(runtime, 'call_bound', lambda _target, operation, *_a, **_k:
                        _operation_response(client, operation))
    receipt = client.query_operation_bound(_spec(), 'register-1', anchor)
    spec = _spec()
    if change == 'receipt':
        receipt['result']['version'] = 2
    elif change == 'source':
        receipt._source['journal_revision'] = 100
    elif change == 'anchor':
        anchor['minimum_revision'] = 8
    elif change == 'spec':
        spec['quantity'] = '2'
    elif change == 'registration':
        altered = replace(snapshot.bindings[0], registration_id='new-registration')
        monkeypatch.setattr(runtime, 'status', lambda: replace(snapshot, bindings=(altered,)))
    elif change == 'operation':
        receipt._registration = (REGISTER_V2, receipt._registration[1])
    else:
        receipt = dict(receipt)
    assert client.verify_parent_bound(receipt, spec, 'register-1', anchor) is None


def test_cancel_is_bound_to_exact_operation_and_host_claim(monkeypatch, bound_host):
    client, runtime, _, _, _ = _client(monkeypatch)
    host = bound_host(client)
    _, anchor = _observed_anchor(monkeypatch, client, runtime)
    calls = []
    def bound(_target, operation, arguments, *, timeout_seconds):
        calls.append((operation, copy.deepcopy(arguments)))
        return _operation_response(client, operation)
    monkeypatch.setattr(runtime, 'call_bound', bound)
    receipt = client.cancel_parent_bound(_spec(), 'cancel-1', 1, anchor)
    assert host.calls[-1][1:4] == ('cancel-1', 'CANCEL', 1)
    assert calls[0][1]['cancel_operation_id'] == 'cancel-1'
    assert client.verify_parent_bound(receipt, _spec(), 'cancel-1', anchor) == 'ACCEPTED'
    assert client.verify_parent_bound(receipt, _spec(), 'another-cancel', anchor) is None
    assert client.cancel_parent_bound(_spec(), 'cancel-1', 1, anchor) is None


@pytest.mark.parametrize('failure', ['not_started', 'after_start', 'denied', 'unknown', 'registration_changed'])
def test_v2_transport_errors_or_registration_change_do_not_fallback(monkeypatch, failure):
    client, runtime, _, _, snapshot = _client(monkeypatch)
    _, anchor = _observed_anchor(monkeypatch, client, runtime)
    calls = []
    def bound(_target, operation, _arguments, *, timeout_seconds):
        calls.append(operation)
        if failure == 'registration_changed':
            altered = replace(snapshot.bindings[0], service_epoch='next-epoch')
            monkeypatch.setattr(runtime, 'status', lambda: replace(snapshot, bindings=(altered,)))
            return _operation_response(client, operation)
        if failure == 'unknown':
            return {'status': 'UNKNOWN'}
        raise RuntimeError(failure)
    monkeypatch.setattr(runtime, 'call_bound', bound)
    assert client.query_operation_bound(_spec(), 'register-1', anchor) is None
    assert calls == [OPERATION_V2]


def test_synthetic_client_reaches_real_registry_and_gateway_v2(monkeypatch, bound_host, service):  # noqa: F811
    """Bound transport is synthetic; Registry and Gateway journal are real local components."""
    client, runtime, _, _, _ = _client(monkeypatch)
    bound_host(client)
    operations = []
    def bound(_target, operation, arguments, *, timeout_seconds):
        operations.append(operation)
        reply = service['request'](operation, arguments)
        assert reply['ok']
        return reply['result']
    monkeypatch.setattr(runtime, 'call_bound', bound)
    receipt = client.capture_parent_source(_spec())
    anchor = client.verify_parent_source(receipt, _spec())
    assert anchor is not None
    registered = client.register_parent_bound(_spec(), 'register-1', anchor)
    assert client.verify_parent_bound(registered, _spec(), 'register-1', anchor) == 'ACCEPTED'
    queried = client.query_operation_bound(_spec(), 'register-1', anchor)
    assert client.verify_parent_bound(queried, _spec(), 'register-1', anchor) == 'ACCEPTED'
    cancelled = client.cancel_parent_bound(_spec(), 'cancel-1', 1, anchor)
    assert client.verify_parent_bound(cancelled, _spec(), 'cancel-1', anchor) == 'ACCEPTED'
    assert operations == [SOURCE, REGISTER_V2, OPERATION_V2, CANCEL_V2]


@pytest.mark.parametrize('change', ['journal_id', 'gateway_generation'])
def test_synthetic_real_gateway_refuses_wrong_source_before_writing(monkeypatch, bound_host, service, change):  # noqa: F811
    client, runtime, _, _, _ = _client(monkeypatch)
    bound_host(client)
    def bound(_target, operation, arguments, *, timeout_seconds):
        reply = service['request'](operation, arguments)
        assert reply['ok']
        return reply['result']
    monkeypatch.setattr(runtime, 'call_bound', bound)
    anchor = client.verify_parent_source(client.capture_parent_source(_spec()), _spec())
    anchor[change] = 'not-the-gateway-source'
    assert client.register_parent_bound(_spec(), 'register-1', anchor) is None
    import sqlite3
    with sqlite3.connect(service['db']) as db:
        assert db.execute('SELECT COUNT(*) FROM direct_execution_parents').fetchone()[0] == 0


@pytest.mark.parametrize('revoked', [REGISTER_V2, OPERATION_V2, CANCEL_V2])
def test_scope_probe_requires_live_gateway_grant_for_every_v2_operation(monkeypatch, service, revoked):  # noqa: F811
    client, runtime, _, _, _ = _client(monkeypatch)
    def bound(_target, operation, arguments, *, timeout_seconds):
        reply = service['request'](operation, arguments)
        return reply['result'] if reply['ok'] else {'status': 'UNKNOWN'}
    monkeypatch.setattr(runtime, 'call_bound', bound)
    assert client.probe_scope_source(timeout_seconds=2.0) is True
    key = ('instance', 'scope', service['identity'].principal_ref, 'account', 'strategy')
    with service['registry']._lock:
        service['registry']._gateway_parent_grants[key] = replace(
            service['grant'], operations=service['grant'].operations - {revoked})
    assert client.probe_scope_source(timeout_seconds=2.0) is False
