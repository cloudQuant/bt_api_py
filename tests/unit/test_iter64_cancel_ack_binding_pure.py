"""Bound ACK tests; no threads, services, network, locks or databases."""
import os
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_transport_zmq.service_v2 import RequestNotSentError, ZmqServiceClient

from bt_api_py import gateway_account_cancel_attempts_client as client_module
from bt_api_py import service_runtime as runtime_module
from bt_api_py import service_runtime_thread as thread
from bt_api_py.gateway_account_observer_client import GatewayAccountObserverRoute
from bt_api_py.service_runtime import RegisteredBinding, ServiceClientRuntime

READ = 'account.cancel_attempts.read_after'
ACK = 'account.cancel_attempts.ack'
IDENTITY = WireIdentity(WireRole.SERVICE, 'risk', 'risk-actor')
PEER = WireIdentity(WireRole.SERVICE, 'gateway', 'gateway-actor')
SCOPE = WireScope('domain', account_ref='account')
ROUTE = thread.BoundCallRoute(IDENTITY, SCOPE, 'scope', 'service', 'instance', PEER)
OPERATIONS = frozenset({READ, ACK})


@pytest.fixture
def raw(monkeypatch):
    runtime = object.__new__(ServiceClientRuntime)
    runtime._owner_thread_id = threading.get_ident()
    runtime._owner_pid = os.getpid()
    runtime._observation_owner_pid = os.getpid()
    runtime._observation_owner_thread = threading.current_thread()
    runtime._monotonic_clock = thread._NATIVE_MONOTONIC
    runtime._state = 'registered'
    runtime.identity, runtime.run_id = IDENTITY, 'run'
    runtime._bindings = [RegisteredBinding(
        mount='upstream.account', service_id='service', instance_id='instance', scope_ref='scope',
        scope=SCOPE, registration_id='registration', service_epoch='service-epoch',
        operations=OPERATIONS, expires_at=99, policy_version='p', requirements_version='r',
        transport_ref='transport', timeout=1, lease_deadline=thread._NATIVE_MONOTONIC() + 100,
        required=True)]
    # Exact client and registration lineage, but all socket objects are fake.
    socket, context = SimpleNamespace(closed=False), SimpleNamespace(closed=False)
    client = object.__new__(ZmqServiceClient)
    client._owner_pid, client._owner_thread = os.getpid(), threading.get_ident()
    client._observation_owner_thread = threading.current_thread()
    client._closed = client._close_pending = client._inflight = False
    client._socket_retirement_pending = False
    client._socket, client._context, client._socket_generation = socket, context, 1
    client._server_key, client._public_key = b'server', b'client'
    client._fixed_curve_keys = (b'server', b'client')
    client._socket_binding = (socket, context, 1, b'server', b'client')
    client._receiver = SimpleNamespace(_socket=socket)
    adapter = runtime_module._RequestNotSentAdapter(client, RequestNotSentError)
    runtime._transports = {('service', 'transport'): adapter}
    runtime._registration_failure_generation = {}
    response_result = {}
    response = SimpleNamespace(payload={'result': response_result})
    receipt = SimpleNamespace(message='registered')
    transport_proof = (('service', 'transport'), adapter, client, socket, context,
                       1, b'server', b'client', 0)
    binding = runtime._bindings[0]
    runtime._registration_lineage = {id(binding): (
        binding, tuple(vars(binding).items()), transport_proof, response, receipt,
        response_result, b'encoded', tuple(vars(receipt).items()), 'key')}
    runtime._peers = {'service': PEER}
    runtime._authenticated_registration_peers = {
        ('service', 'transport', 'registration', 'service-epoch'): (PEER, 'a' * 64)}
    runtime.sent = []
    monkeypatch.setattr(ServiceClientRuntime, '_strict_business_entry', lambda self: None)
    monkeypatch.setattr(ServiceClientRuntime, '_require_healthy', lambda self: None)
    monkeypatch.setattr(ServiceClientRuntime, 'healthy', property(lambda self: True))
    monkeypatch.setattr(ServiceClientRuntime, '_monotonic_now', lambda self: thread._NATIVE_MONOTONIC())
    monkeypatch.setattr(runtime_module, 'encode_envelope', lambda value: b'encoded')
    monkeypatch.setattr(ServiceClientRuntime, 'authenticated_peer_for_binding',
                        lambda self, binding: (PEER, 'a' * 64))
    monkeypatch.setattr(ServiceClientRuntime, '_rpc',
                        lambda self, *args, **kwargs: self.sent.append(args) or {'status': 'OK'})
    return runtime


def bound(operation, expected=None, token=None):
    return thread._BoundCall('upstream.account', operation, {}, {
        'account_ref': 'account', 'portfolio_ref': None, 'scope_ref': 'scope',
        'max_reply_message_bytes': None}, ROUTE, expected, token)


def read_receipt(raw):
    call = bound(READ)
    return thread._finish_bound_call(raw, call, thread._execute_bound_call(raw, call))


def test_owner_accepts_exact_read_binding(raw):
    receipt = read_receipt(raw)
    raw.sent.clear()
    thread._execute_bound_call(raw, bound(ACK, receipt.before, receipt._binding_token))
    assert len(raw.sent) == 1


def test_owner_refuses_renewed_lease_before_raw_ack(raw):
    receipt = read_receipt(raw)
    observed = receipt.before
    raw.sent.clear()
    raw._bindings[0] = replace(raw._bindings[0], lease_deadline=observed.lease_deadline + 10)
    with pytest.raises(thread.ThreadedRuntimeError, match='BOUND_ROUTE_UNAVAILABLE'):
        thread._execute_bound_call(raw, bound(ACK, observed, receipt._binding_token))
    assert raw.sent == []


def test_owner_refuses_mutated_queued_expectation(raw, monkeypatch):
    receipt = read_receipt(raw)
    observed = receipt.before
    raw.sent.clear()
    call = bound(ACK, observed, receipt._binding_token)

    def mutate(self, binding):
        object.__setattr__(call.expected_binding, 'registration_id', 'changed')
        return PEER, 'a' * 64

    monkeypatch.setattr(ServiceClientRuntime, 'authenticated_peer_for_binding', mutate)
    with pytest.raises(thread.ThreadedRuntimeError, match='BOUND_ROUTE_UNAVAILABLE'):
        thread._execute_bound_call(raw, call)
    assert raw.sent == []


@pytest.mark.parametrize('field,value', [
    ('registration_id', 'other'), ('service_epoch', 'other'), ('run_id', 'other'),
    ('fingerprint', 'b' * 64), ('lease_deadline', 1.0), ('operations', frozenset({ACK})),
    ('peer', WireIdentity(WireRole.SERVICE, 'other', 'gateway-actor')),
    ('scope', WireScope('domain', account_ref='other')),
    ('identity', WireIdentity(WireRole.SERVICE, 'other', 'risk-actor')),
    ('service_id', 'other'), ('instance_id', 'other'), ('scope_ref', 'other'),
    ('mount', 'other'), ('active', False), ('required', False),
])
def test_owner_refuses_changed_binding_before_raw_ack(raw, field, value):
    receipt = read_receipt(raw)
    observed = receipt.before
    raw.sent.clear()
    with pytest.raises(thread.ThreadedRuntimeError, match='BOUND_ROUTE_UNAVAILABLE'):
        thread._execute_bound_call(raw, bound(ACK, replace(observed, **{field: value}), receipt._binding_token))
    assert raw.sent == []


@pytest.mark.parametrize('replacement', ['binding', 'transport', 'runtime'])
def test_owner_refuses_equal_replacement_before_raw_ack(raw, replacement):
    receipt = read_receipt(raw)
    raw.sent.clear()
    if replacement == 'binding':
        raw._bindings[0] = replace(raw._bindings[0])
    elif replacement == 'transport':
        raw._transports[('service', 'transport')] = object()
    else:
        receipt = replace(receipt, _binding_token=replace(receipt._binding_token, runtime=object()))
    with pytest.raises(thread.ThreadedRuntimeError, match='BOUND_ROUTE_UNAVAILABLE'):
        thread._execute_bound_call(raw, bound(ACK, receipt.before, receipt._binding_token))
    assert raw.sent == []


def test_client_requires_its_own_latest_read_binding(monkeypatch):
    route = GatewayAccountObserverRoute('upstream.account', 'service', 'instance', 'scope', SCOPE, IDENTITY)
    observation = thread.BoundCallObservation(IDENTITY, 'run', SCOPE, 'registration', 'service-epoch',
        route.mount, route.service_id, route.instance_id, route.scope_ref, OPERATIONS, PEER, 'a' * 64,
        1000.0, True, True, READ)
    epoch = 'b' * 64
    page = {'schema_version': 1, 'account_scope': 'account', 'consumer_ref': 'consumer', 'epoch': epoch,
        'offered_sequence': 1, 'acknowledged_sequence': 0, 'history_complete': False, 'overflow': False,
        'has_more': False, 'events': [{'sequence': 1, 'schema_version': 1, 'kind': 'SOURCE_GAP',
            'account_scope': 'account', 'journal_id': 'journal', 'attempt_id': 'attempt', 'version': 2,
            'state': 'DISPATCHING', 'reason': 'FACT_DAY_SOURCE_UNAVAILABLE'}]}
    progress = {k: v for k, v in page.items() if k not in ('has_more', 'events')}
    progress['acknowledged_sequence'] = 1
    calls = []

    def call_bound(mount, operation, arguments, **kwargs):
        calls.append((operation, kwargs))
        current = replace(observation, operation=operation)
        payload = {'status': 'OK', 'page': page} if operation == READ else {'status': 'OK', 'progress': progress}
        fake_token = thread._BoundBindingToken(object(), object(), object(), object(), (), (), current)
        return thread.BoundCallResult(payload, current, current, fake_token)

    class Lock:
        held = False
        def acquire(self, blocking=False):
            if self.held:
                return False
            self.held = True
            return True
        def release(self): self.held = False

    c = object.__new__(client_module.GatewayAccountCancelAttemptsClient)
    c._pid, c._thread, c._lock = os.getpid(), threading.current_thread(), Lock()
    c._route, c._bound_route, c._expected_fingerprint = route, ROUTE, 'a' * 64
    c._consumer_ref, c._read_binding = 'consumer', None
    c._registration = lambda operation: ('registration', 'service-epoch')
    c._runtime = SimpleNamespace(call_bound=call_bound)
    page['events'][0]['schema_version'] = True
    assert c.read_after().outcome == 'UNKNOWN'
    page['events'][0]['schema_version'] = 1
    calls.clear()
    result = c.read_after()
    assert result.outcome == 'OBSERVED'
    assert c.acknowledge(1, expected_epoch=epoch, binding=replace(result.binding)).code == 'READ_BINDING_REQUIRED'
    assert c.acknowledge(0, expected_epoch=epoch, binding=result.binding).code == 'READ_BINDING_REQUIRED'
    assert len(calls) == 1
    assert c.acknowledge(1, expected_epoch=epoch, binding=result.binding).outcome == 'OBSERVED'
    assert calls[-1][1]['expected_binding'] is result.binding
    assert c.acknowledge(1, expected_epoch=epoch, binding=result.binding).code == 'READ_BINDING_REQUIRED'
    assert len(calls) == 2

    # A higher server cursor can belong to another client and cannot prove
    # that this reader's local durable state covers that additional range.
    newer = c.read_after()
    progress['acknowledged_sequence'] = 2
    progress['offered_sequence'] = 2
    assert c.acknowledge(1, expected_epoch=epoch, binding=newer.binding).outcome == 'UNKNOWN'
    progress['acknowledged_sequence'] = 1
    progress['offered_sequence'] = 1

    # A new read revokes the previous capability, including if that read fails.
    new = c.read_after()
    assert new.outcome == 'OBSERVED'
    original_runtime = c._runtime
    c._runtime = SimpleNamespace(call_bound=lambda *args, **kwargs: None)
    assert c.acknowledge(1, expected_epoch=epoch, binding=new.binding).code == 'READ_BINDING_REQUIRED'
    assert c.read_after().outcome == 'UNKNOWN'
    assert c.acknowledge(1, expected_epoch=epoch, binding=new.binding).code == 'READ_BINDING_REQUIRED'

    # Once an ACK starts, even an invalid/unknown response consumes the receipt.
    c._runtime = original_runtime
    new = c.read_after()
    original_runtime.call_bound = lambda *args, **kwargs: None
    assert c.acknowledge(1, expected_epoch=epoch, binding=new.binding).outcome == 'UNKNOWN'
    assert c.acknowledge(1, expected_epoch=epoch, binding=new.binding).code == 'READ_BINDING_REQUIRED'


def test_client_mutation_and_reentry_cannot_authorize_ack(monkeypatch):
    route = GatewayAccountObserverRoute('upstream.account', 'service', 'instance', 'scope', SCOPE, IDENTITY)
    observation = thread.BoundCallObservation(IDENTITY, 'run', SCOPE, 'registration', 'service-epoch',
        route.mount, route.service_id, route.instance_id, route.scope_ref, OPERATIONS, PEER, 'a' * 64,
        1000.0, True, True, READ)
    epoch = 'b' * 64
    page = {'schema_version': 1, 'account_scope': 'account', 'consumer_ref': 'consumer', 'epoch': epoch,
        'offered_sequence': 1, 'acknowledged_sequence': 0, 'history_complete': False, 'overflow': False,
        'has_more': False, 'events': [{'sequence': 1, 'schema_version': 1, 'kind': 'SOURCE_GAP',
            'account_scope': 'account', 'journal_id': 'journal', 'attempt_id': 'attempt', 'version': 2,
            'state': 'DISPATCHING', 'reason': 'FACT_DAY_SOURCE_UNAVAILABLE'}]}

    class Lock:
        held = False
        def acquire(self, blocking=False):
            if self.held:
                return False
            self.held = True
            return True
        def release(self): self.held = False

    c = object.__new__(client_module.GatewayAccountCancelAttemptsClient)
    c._pid, c._thread, c._lock = os.getpid(), threading.current_thread(), Lock()
    c._route, c._bound_route, c._expected_fingerprint = route, ROUTE, 'a' * 64
    c._consumer_ref, c._read_binding = 'consumer', None
    c._registration = lambda operation: ('registration', 'service-epoch')

    def call_bound(*args, **kwargs):
        with pytest.raises(RuntimeError, match='CANCEL_STREAM_BUSY'):
            c.read_after()
        fake_token = thread._BoundBindingToken(object(), object(), object(), object(), (), (), observation)
        return thread.BoundCallResult({'status': 'OK', 'page': page}, observation, observation, fake_token)

    c._runtime = SimpleNamespace(call_bound=call_bound)
    read = c.read_after()
    assert read.outcome == 'OBSERVED'
    object.__setattr__(observation, 'registration_id', 'changed')
    assert c.acknowledge(1, expected_epoch=epoch, binding=read.binding).code == 'READ_BINDING_REQUIRED'
