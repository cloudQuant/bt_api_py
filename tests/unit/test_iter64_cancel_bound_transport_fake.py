"""SDK owner to ZMQ local lineage, using no socket, network or owner thread."""

import hashlib
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
import zmq
from bt_api_transport_zmq import service_v2 as service

from bt_api_py import service_runtime as runtime_module
from bt_api_py import service_runtime_thread as thread
from bt_api_py.service_runtime import ServiceClientRuntime
from tests.unit.test_iter64_cancel_ack_binding_pure import (  # noqa: F401
    ACK,
    OPERATIONS,
    PEER,
    READ,
    bound,
    raw,
)

_REAL_RPC = ServiceClientRuntime._rpc


@pytest.fixture
def wired(raw, monkeypatch):  # noqa: F811 -- imported pytest fixture.
    """The real owner/runtime/adapter with a socket and replies replaced in memory."""
    monkeypatch.setattr(ServiceClientRuntime, '_rpc', _REAL_RPC)
    monkeypatch.setattr(runtime_module, 'validate_reply', lambda *args, **kwargs: None)
    raw._cleanup_unknown = None
    # The shared fixture constructs the runtime via __new__; initialize the
    # fields used by the real RPC's live-parent capability invalidation.
    raw._live_parent_capabilities_lock = threading.RLock()
    raw._live_parent_capabilities = {}
    raw._registration_mounts = None
    raw._startup_rollback = False
    raw._degraded, raw._degradation_codes = 0, []
    raw._config = {'communication': {'profiles': {'transport': {'options': {'request_timeout_ms': 1000}}}}}
    client = raw._transports[('service', 'transport')]._transport
    socket = client._socket
    socket.sent = []
    socket.on_poll = lambda event: None
    socket.poll = lambda timeout, event: (socket.on_poll(event), 1)[1]
    socket.send = lambda value, flags: socket.sent.append(value)
    socket.close = lambda linger: setattr(socket, 'closed', True)
    client._receiver.receive = lambda maximum: [service._REPLY_FRAME_PREFIX + b'x' * 48]
    client._last_reply_receipt = client._registration_observation = None
    client._last_uncertain_message_id = None
    client._socket_message_ids = set()
    client._max_message_bytes, client._reply_envelope_max_bytes = 1024, 900
    client._identity = PEER
    client._reply_box = SimpleNamespace(decrypt=lambda value: b'decoded')
    fingerprint = hashlib.sha256(b'server').hexdigest()
    raw._authenticated_registration_peers[('service', 'transport', 'registration', 'service-epoch')] = (
        PEER, fingerprint)
    monkeypatch.setattr(ServiceClientRuntime, 'authenticated_peer_for_binding',
                        lambda self, binding: (PEER, fingerprint))
    monkeypatch.setattr(service, 'validate_request', lambda value: None)
    monkeypatch.setattr(service, 'validate_reply', lambda *args, **kwargs: None)
    request = []
    def encode(value, **kwargs):
        request[:] = [value]
        return b'encoded'
    def decode(value, **kwargs):
        header = request[0].header
        if header.operation == 'service.renew':
            result = {'registration_id': 'registration', 'service_epoch': getattr(raw, '_fake_renew_epoch', 'service-epoch'),
                      'operations': list(OPERATIONS), 'policy_version': 'p', 'requirements_version': 'r',
                      'expires_at': 99, 'lease_remaining_seconds': 100}
        else:
            result = {'status': 'OK'}
        return SimpleNamespace(header=SimpleNamespace(correlation_id=header.message_id,
            message_id='reply', operation=header.operation),
            payload={'ok': True, 'code': 'OK', 'result': result})
    monkeypatch.setattr(service, 'encode_envelope', encode)
    monkeypatch.setattr(service, 'decode_envelope', decode)
    return raw, client, socket


def read_receipt(runtime):
    call = bound(READ)
    return thread._finish_bound_call(runtime, call, thread._execute_bound_call(runtime, call))


def test_read_and_ack_are_sent_using_registered_local_socket(wired):
    runtime, _, socket = wired
    receipt = read_receipt(runtime)
    ack = bound(ACK, receipt.before, receipt._binding_token)
    thread._finish_bound_call(runtime, ack, thread._execute_bound_call(runtime, ack))
    assert socket.sent == [b'encoded', b'encoded']


@pytest.mark.parametrize('change', ['lease', 'proof', 'binding', 'peer', 'server', 'receiver', 'socket'])
def test_mutation_during_write_poll_prevents_read_send(wired, change):
    runtime, client, socket = wired
    def mutate(event):
        if event != zmq.POLLOUT:
            return
        binding = runtime._bindings[0]
        if change == 'lease':
            object.__setattr__(binding, 'lease_deadline', 0.0)
        elif change == 'proof':
            runtime._registration_lineage.pop(id(binding))
        elif change == 'binding':
            runtime._bindings[0] = replace(binding)
        elif change == 'peer':
            runtime._peers['service'] = object()
        elif change == 'server':
            client._server_key = b'other'
        elif change == 'receiver':
            client._receiver = SimpleNamespace(_socket=socket)
        elif change == 'socket':
            client._socket = SimpleNamespace(closed=False)
    socket.on_poll = mutate
    with pytest.raises(thread.ThreadedRuntimeError, match='BOUND_OUTCOME_UNKNOWN'):
        thread._execute_bound_call(runtime, bound(READ))
    assert not socket.sent


@pytest.mark.parametrize('change', ['receiver', 'proof'])
def test_read_receipt_cannot_authorize_ack_after_lineage_changes(wired, change):
    runtime, client, socket = wired
    receipt = read_receipt(runtime)
    if change == 'receiver':
        client._receiver = SimpleNamespace(_socket=socket)
    else:
        key = id(runtime._bindings[0])
        runtime._registration_lineage[key] = tuple(list(runtime._registration_lineage[key]))  # noqa: C414
    with pytest.raises(thread.ThreadedRuntimeError, match='BOUND_ROUTE_UNAVAILABLE'):
        thread._execute_bound_call(runtime, bound(ACK, receipt.before, receipt._binding_token))
    assert socket.sent == [b'encoded']


def test_ack_without_read_receipt_is_rejected_before_send(wired):
    runtime, _, socket = wired
    with pytest.raises(thread.ThreadedRuntimeError, match='BOUND_ROUTE_UNAVAILABLE'):
        thread._execute_bound_call(runtime, bound(ACK))
    assert not socket.sent


def test_mutation_after_read_sent_is_unknown_and_no_token_issued(wired):
    runtime, client, socket = wired
    def mutate(event):
        if event == zmq.POLLIN:
            client._socket_generation += 1
    socket.on_poll = mutate
    with pytest.raises(thread.ThreadedRuntimeError, match='BOUND_OUTCOME_UNKNOWN'):
        thread._execute_bound_call(runtime, bound(READ))
    assert socket.sent == [b'encoded']


def test_real_renewal_rebinds_lineage_and_requires_fresh_read(wired):
    runtime, _, socket = wired
    old = read_receipt(runtime)
    old_binding = runtime._bindings[0]
    renewed = runtime.renew().bindings[0]
    assert renewed is not old_binding
    assert not runtime._has_current_registration_observation(old_binding)
    assert runtime._has_current_registration_observation(renewed)
    with pytest.raises(thread.ThreadedRuntimeError, match='BOUND_ROUTE_UNAVAILABLE'):
        thread._execute_bound_call(runtime, bound(ACK, old.before, old._binding_token))
    fresh = read_receipt(runtime)
    ack = bound(ACK, fresh.before, fresh._binding_token)
    thread._finish_bound_call(runtime, ack, thread._execute_bound_call(runtime, ack))
    assert socket.sent == [b'encoded'] * 4


def test_startup_maintenance_rebinds_earlier_mount_before_start_finishes(wired):
    runtime, _, socket = wired
    original = runtime._bindings[0]
    other = replace(original, mount='later-mount', registration_id='later-registration',
                    renewal_due_at=thread._NATIVE_MONOTONIC() + 50)
    runtime._bindings.append(other)
    runtime._state = 'starting'
    runtime._start_in_progress = True
    runtime._startup_maintaining = False
    summary = runtime._maintain_startup()
    renewed = summary.bindings[0]
    assert renewed is not original and summary.bindings[1] is other
    assert id(original) not in runtime._registration_lineage
    assert id(renewed) in runtime._registration_lineage
    runtime._state = 'registered'
    runtime._start_in_progress = False
    assert runtime._has_current_registration_observation(renewed)
    fresh = read_receipt(runtime)
    ack = bound(ACK, fresh.before, fresh._binding_token)
    thread._finish_bound_call(runtime, ack, thread._execute_bound_call(runtime, ack))
    assert socket.sent == [b'encoded'] * 3


def test_real_start_without_sticky_cleanup_rebinds_startup_renewal(wired, monkeypatch):
    runtime, _, socket = wired
    first = runtime._bindings[0]
    first_proof = runtime._registration_lineage[id(first)]
    later = replace(first, mount='later-mount', registration_id='later-registration',
                    renewal_due_at=thread._NATIVE_MONOTONIC() + 50)
    runtime._bindings = []
    runtime._registration_lineage = {}
    runtime._state = 'new'
    runtime._start_in_progress = runtime._startup_maintaining = False
    runtime._config['clients'] = {'risk': {'settings': {'bindings': [{}, {}]}}}
    registrations = []
    def register(self, _binding, **kwargs):
        registrations.append(self._start_in_progress)
        if len(registrations) == 1:
            self._bindings.append(first)
            self._registration_lineage[id(first)] = first_proof
        else:
            self._bindings.append(later)
    monkeypatch.setattr(ServiceClientRuntime, '_register', register)
    assert not runtime.sticky_cleanup_enabled
    summary = runtime.start()
    assert registrations == [False, False]
    renewed = summary.bindings[0]
    assert renewed is not first and summary.bindings[1] is later
    assert runtime._has_current_registration_observation(renewed)
    fresh = read_receipt(runtime)
    ack = bound(ACK, fresh.before, fresh._binding_token)
    thread._finish_bound_call(runtime, ack, thread._execute_bound_call(runtime, ack))
    assert socket.sent == [b'encoded'] * 3


@pytest.mark.parametrize('change', ['generation', 'old_proof'])
def test_renewal_does_not_rebind_changed_socket_or_lost_registration(wired, change):
    runtime, client, socket = wired
    old = read_receipt(runtime)
    def mutate(event):
        if event != zmq.POLLIN:
            return
        if change == 'generation':
            client._socket_generation += 1
        else:
            runtime._registration_lineage.pop(id(runtime._bindings[0]), None)
    socket.on_poll = mutate
    renewed = runtime.renew().bindings[0]
    assert not runtime._has_current_registration_observation(renewed)
    with pytest.raises(thread.ThreadedRuntimeError, match='BOUND_ROUTE_UNAVAILABLE'):
        thread._execute_bound_call(runtime, bound(ACK, old.before, old._binding_token))
    with pytest.raises(thread.ThreadedRuntimeError, match='BOUND_ROUTE_UNAVAILABLE'):
        thread._execute_bound_call(runtime, bound(READ))
    assert socket.sent == [b'encoded'] * 2


def test_renewal_with_changed_service_epoch_revokes_lineage(wired):
    runtime, _, socket = wired
    old = read_receipt(runtime)
    runtime._fake_renew_epoch = 'different-epoch'
    with pytest.raises(runtime_module.ServiceRuntimeError, match='RENEWAL_BINDING_MISMATCH'):
        runtime.renew()
    assert runtime.state == 'failed' and not runtime._registration_lineage
    with pytest.raises(thread.ThreadedRuntimeError, match='BOUND_ROUTE_UNAVAILABLE'):
        thread._execute_bound_call(runtime, bound(ACK, old.before, old._binding_token))
    assert socket.sent == [b'encoded'] * 2


def test_exhausted_read_does_not_rotate_or_claim_receipt(wired):
    runtime, client, socket = wired
    client._socket_message_ids = {f'prior-{i}' for i in range(4095)}
    with pytest.raises(thread.ThreadedRuntimeError, match='BOUND_OUTCOME_UNKNOWN'):
        thread._execute_bound_call(runtime, bound(READ))
    assert not socket.sent and not socket.closed and client._socket is socket
