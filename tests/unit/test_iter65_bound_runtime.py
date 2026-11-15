"""Bound SDK calls with an entirely synthetic transport and registration."""

from __future__ import annotations

import hashlib
import os
import queue
import threading
import time
from collections.abc import Mapping
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_transport_zmq.service_v2 import (
    RequestNotSentError,
    RequestOutcomeUnknownError,
    ServiceReplyReceipt,
    ZmqServiceClient,
    _ServiceSocketBinding,
)

from bt_api_py.service_runtime import (
    BoundServiceTarget,
    ClientCurveKeys,
    RegisteredBinding,
    ServiceClientRuntime,
    ServiceRuntimeRequestNotSentError,
    ZmqTransportFactory,
    _RequestNotSentAdapter,
)
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime

_OPERATION = 'direct.delegated_reserve'


def make_runtime():
    identity = WireIdentity(WireRole.SERVICE, 'synthetic-execution', 'synthetic-actor')
    peer = WireIdentity(WireRole.SERVICE, 'synthetic-gateway', 'synthetic-service')
    scope = WireScope('synthetic-domain', account_ref='synthetic-account',
                      strategy_ref='synthetic-strategy', run_id='synthetic-run')
    keys = ClientCurveKeys(b'a' * 40, b'b' * 40, b'c' * 40)
    factory = ZmqTransportFactory({'synthetic-security': keys})
    client = object.__new__(ZmqServiceClient)
    client._server_key, client._public_key, client._secret_key = keys.server_key, keys.public_key, keys.secret_key
    client._endpoint, client._identity = 'tcp://127.0.0.1:1', peer
    client._owner_pid, client._owner_thread = os.getpid(), threading.get_ident()
    client._closed = client._close_pending = client._socket_retirement_pending = False
    client._socket = SimpleNamespace(closed=False)
    client._receiver = SimpleNamespace(_socket=client._socket)
    client._reply_box = object()
    client._socket_binding = _ServiceSocketBinding(client._socket, client._receiver, client._reply_box,
        client._endpoint, peer, keys.server_key, keys.public_key, keys.secret_key)
    adapter = _RequestNotSentAdapter(client, RequestNotSentError)
    binding = RegisteredBinding(
        'upstream.direct_write', 'synthetic-service-id', 'synthetic-instance', 'synthetic-scope', scope,
        'synthetic-registration', 'synthetic-epoch', frozenset({_OPERATION}), time.time() + 100,
        'policy', 'requirements', 'synthetic-transport', 5.0, lease_deadline=time.monotonic() + 100,
    )
    runtime = object.__new__(ServiceClientRuntime)
    runtime._owner_pid = os.getpid()
    runtime._owner_thread_id, runtime._monotonic_clock = threading.get_ident(), time.monotonic
    runtime._live_parent_capabilities_lock = threading.RLock()
    runtime._live_parent_capabilities = {}
    runtime._strict_registration_transports = {}
    runtime._execution_upstream_lease_enabled = False
    runtime._execution_upstream_clock_leases = {}
    runtime.identity, runtime.run_id = identity, 'synthetic-run'
    runtime._state, runtime._bindings, runtime._factory = 'registered', [binding], factory
    runtime._startup_rollback = False
    runtime._cleanup_failures = runtime._unresolved_unregister_count = 0
    runtime._cleanup_unknown = None
    runtime._cleanup_in_progress = False
    runtime._require_unregister_ack = False
    runtime._unknown_registration_count = 0
    runtime._last_unknown_registration_key = None
    runtime._unregister_uncertainties = {}
    runtime._config = {'communication': {'profiles': {'synthetic-transport': {
        'backend': 'zmq', 'security_profile_ref': 'synthetic-security',
        'endpoints': {'rpc_connect': 'tcp://127.0.0.1:1'}, 'options': {'request_timeout_ms': 5000},
    }}}}
    runtime._transports = {('synthetic-service-id', 'synthetic-transport'): adapter}
    runtime._peers = {'synthetic-service-id': peer}
    proof_key = ('synthetic-service-id', 'synthetic-transport', 'synthetic-registration', 'synthetic-epoch')
    fingerprint = hashlib.sha256(keys.server_key).hexdigest()
    runtime._authenticated_registration_peers = {proof_key: (peer, fingerprint)}
    runtime._authenticated_registration_transports = {proof_key: (adapter, client, client._socket_binding)}
    target = BoundServiceTarget(
        'upstream.direct_write', 'service.direct_write', 'synthetic-service-id', 'synthetic-instance',
        'synthetic-scope', replace(scope), replace(identity), 'synthetic-run', 'synthetic-transport',
        'synthetic-security', 'tcp://127.0.0.1:1', replace(peer), fingerprint,
        'synthetic-registration', 'synthetic-epoch',
    )
    return runtime, target, adapter, binding


def fake_reply(peer, calls, *, on_call=None):
    def request(_self, request, *, timeout, deadline_monotonic, socket_binding):
        assert socket_binding is _self._socket_binding
        calls.append((request, timeout, deadline_monotonic))
        if on_call is not None:
            on_call()
        return WireEnvelope(replace(request.header, kind=WireMessageKind.REPLY, identity=peer,
                                    message_id='synthetic-reply', correlation_id=request.header.message_id),
                            {'ok': True, 'code': 'OK', 'result': {'value': 'synthetic'}})
    return request


def test_bound_call_uses_exact_binding_transport_and_absolute_deadline(monkeypatch):
    runtime, target, _adapter, binding = make_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    deadline = time.monotonic() + 10
    assert runtime.call_bound(target, _OPERATION, {'sample': 'value'}, deadline_monotonic=deadline) == {
        'value': 'synthetic'
    }
    assert len(calls) == 1
    request, timeout, actual_deadline = calls[0]
    assert request.header.identity == target.identity and request.header.scope == target.scope
    assert request.payload['instance_id'] == target.instance_id
    assert request.payload['registration_id'] == target.registration_id
    assert request.payload['service_epoch'] == target.service_epoch
    assert actual_deadline == min(deadline, binding.lease_deadline)
    assert timeout <= binding.timeout


def test_bound_target_repr_does_not_expose_key_fingerprint():
    _runtime, target, _adapter, _binding = make_runtime()
    assert target.server_key_fingerprint not in repr(target)


@pytest.mark.parametrize('lost', ['replaced_socket', 'retired_socket', 'replaced_adapter', 'proof_missing'])
def test_authenticated_peer_snapshot_fails_closed_when_socket_proof_is_lost(lost):
    runtime, target, adapter, binding = make_runtime()
    assert runtime.authenticated_peer_for_binding(binding) == (target.expected_peer, target.server_key_fingerprint)
    if lost == 'replaced_socket':
        adapter._transport._socket = SimpleNamespace(closed=False)
    elif lost == 'retired_socket':
        adapter._transport._socket.closed = True
    elif lost == 'replaced_adapter':
        runtime._transports[(target.service_id, target.transport_ref)] = _RequestNotSentAdapter(
            adapter._transport, RequestNotSentError)
    else:
        runtime._authenticated_registration_transports.clear()
    assert runtime.authenticated_peer_for_binding(binding) is None
    wrapper = ThreadedServiceClientRuntime(lambda: runtime)
    wrapper._capture(runtime)
    snapshot = wrapper.status()
    assert snapshot.bindings[0].authenticated_peer is None
    assert snapshot.bindings[0].server_key_fingerprint is None


@pytest.mark.parametrize('field,wrong', [
    ('service_id', 'other-service'), ('instance_id', 'other-instance'), ('scope_ref', 'other-scope'),
    ('transport_ref', 'other-transport'), ('security_profile_ref', 'other-security'),
    ('endpoint', 'tcp://127.0.0.1:2'), ('registration_id', 'other-registration'),
    ('service_epoch', 'other-epoch'), ('server_key_fingerprint', '0' * 64),
])
def test_mismatched_expected_binding_never_enters_transport(monkeypatch, field, wrong):
    runtime, target, _adapter, _binding = make_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.call_bound(replace(target, **{field: wrong}), _OPERATION, {}, deadline_monotonic=time.monotonic() + 5)
    assert calls == []


@pytest.mark.parametrize('changed', ['scope', 'identity', 'peer', 'duplicate', 'inactive', 'not_required',
                                     'operations', 'replaced_transport', 'replaced_client', 'missing_registration_proof',
                                     'profile_key', 'clock', 'expired_lease'])
def test_binding_or_authentication_loss_never_enters_transport(monkeypatch, changed):
    runtime, target, adapter, binding = make_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    if changed == 'scope':
        target = replace(target, scope=replace(target.scope, account_ref='other-account'))
    elif changed == 'identity':
        target = replace(target, identity=replace(target.identity, principal_ref='other-principal'))
    elif changed == 'peer':
        target = replace(target, expected_peer=replace(target.expected_peer, principal_ref='other-peer'))
    elif changed == 'duplicate':
        runtime._bindings.append(binding)
    elif changed == 'inactive':
        runtime._bindings = [replace(binding, active=False)]
    elif changed == 'not_required':
        runtime._bindings = [replace(binding, required=False)]
    elif changed == 'operations':
        runtime._bindings = [replace(binding, operations=frozenset({'direct.delegated_dispatch'}))]
    elif changed == 'replaced_transport':
        runtime._transports[('synthetic-service-id', 'synthetic-transport')] = _RequestNotSentAdapter(
            adapter._transport, RequestNotSentError)
    elif changed == 'replaced_client':
        replacement = object.__new__(ZmqServiceClient)
        replacement.__dict__.update(adapter._transport.__dict__)
        adapter._transport = replacement
    elif changed == 'missing_registration_proof':
        runtime._authenticated_registration_transports.clear()
    elif changed == 'profile_key':
        runtime._factory._profiles['synthetic-security'] = ClientCurveKeys(b'z' * 40, b'b' * 40, b'c' * 40)
    elif changed == 'clock':
        runtime._monotonic_clock = lambda: time.monotonic()
    else:
        runtime._bindings = [replace(binding, lease_deadline=time.monotonic() - 1)]
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.call_bound(target, _OPERATION, {}, deadline_monotonic=time.monotonic() + 5)
    assert calls == []


def test_expired_caller_deadline_and_ungranted_operation_do_not_send(monkeypatch):
    runtime, target, _adapter, _binding = make_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    for operation, deadline in [(_OPERATION, time.monotonic() - 1), ('unknown.operation', time.monotonic() + 5)]:
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            runtime.call_bound(target, operation, {}, deadline_monotonic=deadline)
    assert calls == []


def test_threaded_bound_call_requires_real_clock_and_exact_runtime(monkeypatch):
    wrapper = ThreadedServiceClientRuntime(lambda: None, monotonic_clock=lambda: time.monotonic())
    _runtime, target, _adapter, _binding = make_runtime()
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        wrapper.call_bound(target, _OPERATION, {})


def _threaded_factory():
    runtime, _target, _adapter, _binding = make_runtime()
    runtime.start = lambda: None
    runtime.maintain = lambda: None
    runtime.next_maintenance_delay = lambda: 1.0
    runtime.close = lambda: None
    return runtime


def test_threaded_bound_call_dispatches_on_runtime_owner(monkeypatch):
    _runtime, target, _adapter, _binding = make_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    wrapper = ThreadedServiceClientRuntime(_threaded_factory)
    try:
        assert wrapper.start().healthy
        assert wrapper.call_bound(target, _OPERATION, {'sample': 'value'}) == {'value': 'synthetic'}
        assert len(calls) == 1 and calls[0][0].header.scope == target.scope
    finally:
        assert wrapper.close().state == 'closed'


def test_queued_bound_call_times_out_before_start_without_sending(monkeypatch):
    _runtime, target, _adapter, _binding = make_runtime()
    started, release = threading.Event(), threading.Event()
    calls = []

    def hold_first():
        started.set()
        assert release.wait(3)

    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls, on_call=hold_first))
    wrapper = ThreadedServiceClientRuntime(_threaded_factory)
    results = queue.Queue()
    first = threading.Thread(target=lambda: results.put(wrapper.call_bound(target, _OPERATION, {}, timeout_seconds=3)))
    try:
        assert wrapper.start().healthy
        first.start()
        assert started.wait(2)
        with pytest.raises(ThreadedRuntimeError) as error:
            wrapper.call_bound(target, _OPERATION, {}, timeout_seconds=0.05)
        assert error.value.code == 'TIMEOUT_BEFORE_START' and len(calls) == 1
    finally:
        release.set()
        first.join(3)
        assert not first.is_alive()
        assert wrapper.close().state == 'closed'
    assert results.get_nowait() == {'value': 'synthetic'}


def test_timeout_after_owner_starts_is_unknown_and_does_not_cancel_inflight(monkeypatch):
    _runtime, target, _adapter, _binding = make_runtime()
    started, release = threading.Event(), threading.Event()
    calls = []

    def hold():
        started.set()
        assert release.wait(3)

    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls, on_call=hold))
    wrapper = ThreadedServiceClientRuntime(_threaded_factory)
    try:
        assert wrapper.start().healthy
        with pytest.raises(ThreadedRuntimeError) as error:
            wrapper.call_bound(target, _OPERATION, {}, timeout_seconds=0.05)
        assert error.value.code == 'TIMEOUT_AFTER_START' and started.is_set() and len(calls) == 1
    finally:
        release.set()
        assert wrapper.close().state == 'closed'


def test_queued_call_copies_nested_scope_before_caller_mutation(monkeypatch):
    _runtime, target, _adapter, _binding = make_runtime()
    started, release = threading.Event(), threading.Event()
    calls, results = [], queue.Queue()

    def hold():
        started.set()
        assert release.wait(3)

    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls, on_call=hold))
    wrapper = ThreadedServiceClientRuntime(_threaded_factory)
    first = threading.Thread(target=lambda: results.put(wrapper.call_bound(target, _OPERATION, {}, timeout_seconds=3)))
    second = threading.Thread(target=lambda: results.put(wrapper.call_bound(target, _OPERATION, {}, timeout_seconds=3)))
    try:
        assert wrapper.start().healthy
        first.start()
        assert started.wait(2)
        second.start()
        with wrapper._condition:
            deadline = time.monotonic() + 2
            while not wrapper._queue:
                remaining = deadline - time.monotonic()
                assert remaining > 0
                wrapper._condition.wait(remaining)
        object.__setattr__(target.scope, 'account_ref', 'mutated-after-enqueue')
        release.set()
        first.join(3)
        second.join(3)
        assert not first.is_alive() and not second.is_alive()
        assert [call[0].header.scope.account_ref for call in calls] == ['synthetic-account', 'synthetic-account']
        assert results.get_nowait() == results.get_nowait() == {'value': 'synthetic'}
    finally:
        release.set()
        first.join(3)
        second.join(3)
        assert wrapper.close().state == 'closed'


def test_registration_proof_is_tied_to_exact_transport_and_poisoned_on_replacement(monkeypatch):
    runtime, target, first, _binding = make_runtime()
    proof_key = (target.service_id, target.transport_ref, target.registration_id, target.service_epoch)
    runtime._authenticated_registration_peers.clear()
    runtime._authenticated_registration_transports.clear()

    def register(client, request, *, timeout):
        reply = WireEnvelope(replace(request.header, kind=WireMessageKind.REPLY, identity=target.expected_peer,
                                     message_id='registration-reply', correlation_id=request.header.message_id),
                             {'ok': True, 'code': 'OK', 'result': {
                                 'registration_id': target.registration_id, 'service_epoch': target.service_epoch}})
        client._last_reply_receipt = ServiceReplyReceipt(
            request.header.message_id, reply.header.message_id, request.header.message_id,
            'service.register', time.time_ns(), time.monotonic_ns(), os.getpid(), threading.get_ident())
        object.__setattr__(client._last_reply_receipt, '_socket_binding', client._socket_binding)
        return reply

    monkeypatch.setattr(ZmqServiceClient, 'request', register)
    runtime._rpc(target.service_id, target.transport_ref, target.scope, 'service.register', {}, timeout=1.0)
    assert runtime._authenticated_registration_transports[proof_key] == (
        first, first._transport, first._transport._socket_binding)
    replacement_client = object.__new__(ZmqServiceClient)
    replacement_client.__dict__.update(first._transport.__dict__)
    replacement = _RequestNotSentAdapter(replacement_client, RequestNotSentError)
    runtime._transports[(target.service_id, target.transport_ref)] = replacement
    runtime._rpc(target.service_id, target.transport_ref, target.scope, 'service.register', {}, timeout=1.0)
    assert runtime._authenticated_registration_transports[proof_key] is None
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.call_bound(target, _OPERATION, {}, deadline_monotonic=time.monotonic() + 5)


def test_client_replacement_after_binding_check_is_not_sent(monkeypatch):
    runtime, target, adapter, _binding = make_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    original_rpc = runtime._rpc
    replacement = object.__new__(ZmqServiceClient)
    replacement.__dict__.update(adapter._transport.__dict__)

    def replace_then_rpc(*args, **kwargs):
        adapter._transport = replacement
        return original_rpc(*args, **kwargs)

    runtime._rpc = replace_then_rpc
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.call_bound(target, _OPERATION, {}, deadline_monotonic=time.monotonic() + 5)
    assert calls == []


def test_registration_proof_uses_received_socket_not_current_socket(monkeypatch):
    runtime, target, adapter, _binding = make_runtime()
    proof_key = (target.service_id, target.transport_ref, target.registration_id, target.service_epoch)
    received_socket_binding = adapter._transport._socket_binding
    runtime._authenticated_registration_peers.clear()
    runtime._authenticated_registration_transports.clear()

    def register_then_replace(client, request, *, timeout):
        reply = WireEnvelope(replace(request.header, kind=WireMessageKind.REPLY, identity=target.expected_peer,
                                     message_id='registration-reply', correlation_id=request.header.message_id),
                             {'ok': True, 'code': 'OK', 'result': {
                                 'registration_id': target.registration_id, 'service_epoch': target.service_epoch}})
        client._last_reply_receipt = ServiceReplyReceipt(
            request.header.message_id, reply.header.message_id, request.header.message_id,
            'service.register', time.time_ns(), time.monotonic_ns(), os.getpid(), threading.get_ident())
        object.__setattr__(client._last_reply_receipt, '_socket_binding', received_socket_binding)
        client._socket = SimpleNamespace(closed=False)
        client._receiver = SimpleNamespace(_socket=client._socket)
        client._socket_binding = _ServiceSocketBinding(client._socket, client._receiver, client._reply_box,
            client._endpoint, target.expected_peer, client._server_key, client._public_key, client._secret_key)
        return reply

    monkeypatch.setattr(ZmqServiceClient, 'request', register_then_replace)
    runtime._rpc(target.service_id, target.transport_ref, target.scope, 'service.register', {}, timeout=1.0)
    assert runtime._authenticated_registration_transports[proof_key][2] is received_socket_binding
    assert runtime._authenticated_registration_transports[proof_key][2] is not adapter._transport._socket_binding
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.call_bound(target, _OPERATION, {}, deadline_monotonic=time.monotonic() + 5)


def test_public_binding_and_caller_target_mutation_cannot_retarget_envelope(monkeypatch):
    runtime, target, _adapter, binding = make_runtime()
    calls = []
    peer = replace(target.expected_peer)
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(peer, calls))
    original_rpc = runtime._rpc

    def mutate_after_check(*args, **kwargs):
        object.__setattr__(binding.scope, 'account_ref', 'mutated-binding-account')
        object.__setattr__(binding, 'registration_id', 'mutated-binding-registration')
        object.__setattr__(target.scope, 'account_ref', 'mutated-target-account')
        object.__setattr__(target.identity, 'principal_ref', 'mutated-target-principal')
        object.__setattr__(target.expected_peer, 'principal_ref', 'mutated-target-peer')
        return original_rpc(*args, **kwargs)

    runtime._rpc = mutate_after_check
    assert runtime.call_bound(target, _OPERATION, {}, deadline_monotonic=time.monotonic() + 5) == {
        'value': 'synthetic'
    }
    assert len(calls) == 1
    request = calls[0][0]
    assert request.header.scope.account_ref == 'synthetic-account'
    assert request.header.identity.principal_ref == 'synthetic-execution'
    assert request.payload['registration_id'] == 'synthetic-registration'


def test_untrusted_argument_copy_runs_before_binding_validation(monkeypatch):
    runtime, target, _adapter, binding = make_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))

    class MutatingMapping(Mapping):
        def __len__(self):
            return 1

        def __iter__(self):
            return iter(('sample',))

        def __getitem__(self, _key):
            object.__setattr__(binding, 'registration_id', 'changed-before-validation')
            return 'value'

    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.call_bound(target, _OPERATION, MutatingMapping(), deadline_monotonic=time.monotonic() + 5)
    assert calls == []


def test_only_exact_transport_not_sent_is_mapped_to_not_sent(monkeypatch):
    runtime, target, adapter, _binding = make_runtime()

    def not_sent(_client, request, **_kwargs):
        raise RequestNotSentError(request.header.message_id)

    monkeypatch.setattr(ZmqServiceClient, 'request', not_sent)
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.call_bound(target, _OPERATION, {}, deadline_monotonic=time.monotonic() + 5)

    def unknown(_client, request, **_kwargs):
        raise RequestOutcomeUnknownError(request.header.message_id)

    original_rpc = runtime._rpc

    def replace_error_type_then_rpc(*args, **kwargs):
        adapter._error_type = Exception
        return original_rpc(*args, **kwargs)

    monkeypatch.setattr(ZmqServiceClient, 'request', unknown)
    runtime._rpc = replace_error_type_then_rpc
    with pytest.raises(RequestOutcomeUnknownError):
        runtime.call_bound(target, _OPERATION, {}, deadline_monotonic=time.monotonic() + 5)
