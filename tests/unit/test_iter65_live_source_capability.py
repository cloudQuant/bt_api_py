"""Offline strict receipt/source behavior with a synthetic sealed-frame peer."""
from dataclasses import replace
import copy
import gc
import hashlib
import pickle
import threading
import time
import weakref

import pytest
import zmq
from nacl.public import PublicKey
from zmq.utils import z85
from bt_api_base.service_protocol import (
    WIRE_PROTOCOL_VERSION, WireEnvelope, WireIdentity, WireRole, WireScope, decode_envelope, encode_envelope,
    wire_contract_digest,
)
from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
from bt_api_transport_zmq import service_v2 as transport
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_runtime import (
    BoundServiceTarget, ClientCurveKeys, ServiceCallOutcomeUnknownError, ServiceClientRuntime,
    ServiceRuntimeRequestNotSentError, ZmqTransportFactory,
)

IDENTITY = WireIdentity(WireRole.SERVICE, 'synthetic-execution', 'synthetic-actor')
PEER = WireIdentity(WireRole.SERVICE, 'synthetic-gateway', 'synthetic-peer')
SCOPE = WireScope('synthetic-domain', account_ref='synthetic-account', strategy_ref='synthetic-strategy', run_id='synthetic-run')
OPS = frozenset({'direct.parent.source', 'direct.parent.register_v2'})


def config():
    return parse_service_config({
        'service_config_schema_version': 1, 'process_role': 'service', 'service_id': 'synthetic-execution',
        'scope_ref': 'execution-root', 'transport_ref': 'unused', 'registration_policy_ref': 'policy',
        'service_requirements_ref': 'requirements',
        'components': {'monitor': {'config_schema_version': 2, 'settings': {
            'instance_id': 'monitor', 'bind_ports': ['service.telemetry_ingress'],
            'options': {'role': 'gateway_heartbeat'}}}},
        'clients': {'gateway': {'config_schema_version': 1, 'settings': {'bindings': [{
            'mount': 'upstream.direct_write', 'service_id': 'synthetic-service',
            'instance_id': 'synthetic-instance', 'scope_ref': 'synthetic-account',
            'account_ref': 'synthetic-account', 'transport_ref': 'gateway'}]}}},
        'communication': {'config_schema_version': 1, 'profiles': {
            'unused': {'backend': 'zmq', 'config_schema_version': 1,
                       'endpoints': {'rpc_bind': 'tcp://127.0.0.1:1'}, 'security_profile_ref': 'unused'},
            'gateway': {'backend': 'zmq', 'config_schema_version': 1,
                        'endpoints': {'rpc_connect': 'tcp://127.0.0.1:1'}, 'security_profile_ref': 'synthetic-key'},
        }},
    })


class Socket:
    def __init__(self, peer):
        self.peer = peer
        self.options = {}
        self.frame = None
        self.closed = False
        self.fail_closes = 0

    def setsockopt(self, option, value):
        self.options[option] = value

    def getsockopt(self, option):
        return 0 if option == zmq.RCVMORE else self.options[option]

    def connect(self, _endpoint):
        pass

    def poll(self, _timeout, _event):
        return 1

    def send(self, data, *, flags):
        assert flags == zmq.NOBLOCK
        request = decode_envelope(data)
        result = self.peer.handle(request)
        reply = transport._make_reply(PEER, request, {'ok': True, 'code': 'OK', 'result': result})
        self.frame = transport._seal_reply(encode_envelope(reply), PublicKey(z85.decode(self.curve_publickey)))

    def recv(self, *, flags, copy):
        value, self.frame = self.frame, None
        assert value is not None
        return value

    def close(self, *, linger):
        if self.fail_closes:
            self.fail_closes -= 1
            raise RuntimeError('synthetic close failure')
        self.closed = True


class Context:
    def __init__(self, peer):
        self.peer = peer

    def socket(self, _kind):
        return Socket(self.peer)

    def term(self):
        pass


class Peer:
    def __init__(self):
        self.revision = 5
        self.journal_id = 'synthetic-journal'
        self.generation = 'generation'
        self.registers = []
        self.registration_attempts = 0
        self.before_register = None
        self.before_source = None

    def handle(self, request):
        op = request.header.operation
        if op == 'service.discover':
            return {'protocol_version': WIRE_PROTOCOL_VERSION, 'wire_contract': wire_contract_digest(),
                    'service_id': 'synthetic-service', 'ready': True, 'capabilities': [{
                        'instance_id': 'synthetic-instance', 'port': 'service.direct_write', 'operations': list(OPS)}]}
        if op == 'service.register':
            self.registration_attempts += 1
            return {'registration_id': 'registration', 'service_epoch': 'epoch', 'operations': sorted(OPS),
                    'expires_at': time.time() + 120, 'lease_remaining_seconds': 120,
                    'policy_version': 'policy', 'requirements_version': 'requirements'}
        if op == 'service.unregister':
            return {'unregistered': True, 'registration_id': 'registration'}
        if op == 'direct.parent.source':
            if self.before_source is not None:
                self.before_source()
            return {'status': 'OBSERVED', 'schema_version': 1, 'operation': op, 'result': {
                'schema_version': 1, 'account_scope': 'synthetic-account', 'strategy_scope': 'synthetic-strategy',
                'execution_domain_ref': 'synthetic-domain', 'journal_id': self.journal_id,
                'gateway_generation': self.generation, 'journal_revision': self.revision}}
        if op == 'direct.parent.register_v2':
            arguments = request.payload['arguments']
            self.registers.append(arguments)
            if self.before_register is not None:
                self.before_register()
            spec = dict(arguments['spec'])
            return {'status': 'OBSERVED', 'schema_version': 2, 'operation': op,
                    'result': {'spec': spec, 'spec_digest': hashlib.sha256(
                        canonical_gateway_payload(spec).encode('ascii')).hexdigest()}, 'source': {
                        'account_scope': 'synthetic-account', 'journal_id': 'synthetic-journal',
                        'gateway_generation': 'generation', 'journal_revision': self.revision}}
        raise AssertionError(op)


@pytest.fixture
def setup(monkeypatch):
    peer = Peer()
    monkeypatch.setattr(transport.zmq, 'Context', lambda **_kwargs: Context(peer))
    server, _ = zmq.curve_keypair()
    public, secret = zmq.curve_keypair()
    keys = ClientCurveKeys(server, public, secret)
    runtime = ServiceClientRuntime(config(), identity=IDENTITY, scope_resolver=lambda _: SCOPE,
        peer_resolver=lambda _: PEER, transport_factory=ZmqTransportFactory({'synthetic-key': keys},
            strict_single_connection=True), run_id='synthetic-run', requested_operations={'upstream.direct_write': OPS})
    runtime.start()
    binding, = runtime.bindings
    target = BoundServiceTarget('upstream.direct_write', 'service.direct_write', 'synthetic-service',
        'synthetic-instance', 'synthetic-account', SCOPE, IDENTITY, 'synthetic-run', 'gateway',
        'synthetic-key', 'tcp://127.0.0.1:1', PEER, hashlib.sha256(server).hexdigest(),
        binding.registration_id, binding.service_epoch)
    spec = {'account_scope': 'synthetic-account', 'strategy_scope': 'synthetic-strategy',
            'execution_domain_ref': 'synthetic-domain', 'parent_ref': 'parent', 'registration_operation_id': 'op'}
    try:
        yield runtime, target, spec, peer
    finally:
        runtime.close()


def deadline():
    return time.monotonic() + 5


def test_strict_live_source_revision_highwater_and_write_disabled(setup):
    runtime, target, spec, peer = setup
    first = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    peer.revision = 10
    second = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    assert list(runtime._live_parent_revisions.values()) == [10]
    peer.revision = 7
    with pytest.raises(ServiceCallOutcomeUnknownError):
        runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.register_live_parent(second, target, spec, deadline_monotonic=deadline())
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.register_live_parent(first, target, spec, deadline_monotonic=deadline())
    assert not peer.registers


def test_ordinary_source_revokes_prior_cap_before_highwater_can_be_bypassed(setup):
    runtime, target, spec, peer = setup
    cap = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    peer.revision = 10
    args = {key: spec[key] for key in ('account_scope', 'strategy_scope', 'execution_domain_ref')}
    result = runtime.call_bound(target, 'direct.parent.source', args, deadline_monotonic=deadline())
    assert result['result']['journal_revision'] == 10 and cap._revoked.is_set()
    peer.revision = 7
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.register_live_parent(cap, target, spec, deadline_monotonic=deadline())
    assert not peer.registers


def test_capture_cannot_authorize_write_without_real_host_dispatch_claim(setup):
    runtime, target, spec, peer = setup
    cap = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.register_live_parent(cap, target, spec, deadline_monotonic=deadline())
    assert cap._revoked.is_set() and not peer.registers


def test_capability_cannot_copy_retarget_or_reuse(setup):
    runtime, target, spec, peer = setup
    cap = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    with pytest.raises(TypeError):
        copy.copy(cap)
    with pytest.raises(TypeError):
        copy.deepcopy(cap)
    with pytest.raises(TypeError):
        pickle.dumps(cap)
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.register_live_parent(cap, target, dict(spec, parent_ref='other'), deadline_monotonic=deadline())
    assert not peer.registers
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.register_live_parent(cap, target, spec, deadline_monotonic=deadline())


def test_capability_rejects_unproven_registration_and_changed_socket(setup):
    runtime, target, spec, peer = setup
    proofs = runtime._strict_registration_transports
    runtime._strict_registration_transports = {}
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    runtime._strict_registration_transports = proofs
    cap = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    adapter = runtime._transports[(target.service_id, target.transport_ref)]
    adapter._transport._socket.options[zmq.RECONNECT_IVL] = 1
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.register_live_parent(cap, target, spec, deadline_monotonic=deadline())
    assert not peer.registers


@pytest.mark.parametrize('change', ['parent', 'operation_id', 'target', 'lease', 'peer', 'keys', 'socket'])
def test_changed_binding_is_definitely_not_sent(setup, change):
    runtime, target, spec, peer = setup
    cap = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    if change == 'parent':
        spec = dict(spec, parent_ref='other')
    elif change == 'operation_id':
        spec = dict(spec, registration_operation_id='other')
    elif change == 'target':
        target = replace(target, registration_id='other')
    elif change == 'lease':
        runtime._bindings[0] = replace(runtime._bindings[0], lease_deadline=time.monotonic() - 1)
    elif change == 'peer':
        runtime._peers[target.service_id] = WireIdentity(WireRole.SERVICE, 'other', 'other')
    elif change == 'keys':
        runtime._factory._profiles[target.security_profile_ref] = ClientCurveKeys(b'z' * 40, b'x' * 40, b'c' * 40)
    else:
        runtime._transports[(target.service_id, target.transport_ref)]._transport._socket.options[zmq.IMMEDIATE] = 0
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.register_live_parent(cap, target, spec, deadline_monotonic=deadline())
    assert not peer.registers


def test_replaced_instance_request_cannot_enter_source_evidence_path(setup, monkeypatch):
    import os
    import threading
    runtime, target, spec, _ = setup
    client = runtime._transports[(target.service_id, target.transport_ref)]._transport
    invoked = []

    def forged(request, *, timeout, deadline_monotonic, socket_binding):
        invoked.append(True)
        result = {'status': 'OBSERVED', 'schema_version': 1, 'operation': request.header.operation,
                  'result': {'schema_version': 1, 'account_scope': 'synthetic-account',
                    'strategy_scope': 'synthetic-strategy', 'execution_domain_ref': 'synthetic-domain',
                    'journal_id': 'synthetic-journal', 'gateway_generation': 'generation', 'journal_revision': 5}}
        reply = transport._make_reply(PEER, request, {'ok': True, 'code': 'OK', 'result': result})
        receipt = transport.ServiceReplyReceipt(request.header.message_id, reply.header.message_id,
            request.header.message_id, request.header.operation, time.time_ns(), time.monotonic_ns(),
            os.getpid(), threading.get_ident())
        object.__setattr__(receipt, '_socket_binding', socket_binding)
        client._last_reply_receipt = receipt
        transport._issue_strict_receipt(client, receipt, request, reply, socket_binding,
            deadline_monotonic, deadline_monotonic, encode_envelope(request), encode_envelope(reply))
        return reply

    monkeypatch.setattr(client, 'request', forged)
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    assert not invoked and not runtime._live_parent_capabilities


def test_same_capability_cannot_cross_runtime(setup):
    runtime, target, spec, peer = setup
    cap = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    other = ServiceClientRuntime(config(), identity=IDENTITY, scope_resolver=lambda _: SCOPE,
        peer_resolver=lambda _: PEER, transport_factory=runtime._factory,
        run_id='synthetic-run', requested_operations={'upstream.direct_write': OPS})
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        other.register_live_parent(cap, target, spec, deadline_monotonic=deadline())
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.register_live_parent(cap, target, spec, deadline_monotonic=deadline())
    assert not peer.registers


def test_close_revokes_source_capability(setup):
    runtime, target, spec, peer = setup
    cap = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    runtime.close()
    assert cap._revoked.is_set()
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.register_live_parent(cap, target, spec, deadline_monotonic=deadline())
    assert not peer.registers


def test_threaded_runtime_carries_capability_to_owner(monkeypatch):
    from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
    peer = Peer()
    monkeypatch.setattr(transport.zmq, 'Context', lambda **_kwargs: Context(peer))
    server, _ = zmq.curve_keypair()
    public, secret = zmq.curve_keypair()
    keys = ClientCurveKeys(server, public, secret)

    def build():
        return ServiceClientRuntime(config(), identity=IDENTITY, scope_resolver=lambda _: SCOPE,
            peer_resolver=lambda _: PEER, transport_factory=ZmqTransportFactory({'synthetic-key': keys},
                strict_single_connection=True), run_id='synthetic-run', requested_operations={'upstream.direct_write': OPS})

    threaded = ThreadedServiceClientRuntime(build)
    try:
        snapshot = threaded.start()
        binding, = snapshot.bindings
        target = BoundServiceTarget('upstream.direct_write', 'service.direct_write', 'synthetic-service',
            'synthetic-instance', 'synthetic-account', SCOPE, IDENTITY, 'synthetic-run', 'gateway',
            'synthetic-key', 'tcp://127.0.0.1:1', PEER, hashlib.sha256(server).hexdigest(),
            binding.registration_id, binding.service_epoch)
        spec = {'account_scope': 'synthetic-account', 'strategy_scope': 'synthetic-strategy',
                'execution_domain_ref': 'synthetic-domain', 'parent_ref': 'parent', 'registration_operation_id': 'op'}
        cap = threaded.capture_live_parent_source(target, spec)
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            threaded.register_live_parent(cap, target, spec)
        assert cap._revoked.is_set() and not peer.registers
    finally:
        threaded.close()


def test_fake_registration_receipt_cannot_create_strict_registration_proof(monkeypatch):
    import os
    peer = Peer()
    monkeypatch.setattr(transport.zmq, 'Context', lambda **_kwargs: Context(peer))
    server, _ = zmq.curve_keypair()
    public, secret = zmq.curve_keypair()
    keys = ClientCurveKeys(server, public, secret)
    original = transport.ZmqServiceClient.request

    def forged(self, req, *, timeout, deadline_monotonic=None, socket_binding=None):
        if req.header.operation != 'service.register':
            return original(self, req, timeout=timeout, deadline_monotonic=deadline_monotonic,
                            socket_binding=socket_binding)
        reply = transport._make_reply(PEER, req, {'ok': True, 'code': 'OK', 'result': peer.handle(req)})
        receipt = transport.ServiceReplyReceipt(req.header.message_id, reply.header.message_id,
            req.header.message_id, req.header.operation, time.time_ns(), time.monotonic_ns(),
            os.getpid(), threading.get_ident())
        object.__setattr__(receipt, '_socket_binding', self._socket_binding)
        self._last_reply_receipt = receipt
        end = time.monotonic() + timeout
        transport._issue_strict_receipt(self, receipt, req, reply, self._socket_binding,
            end, deadline_monotonic, encode_envelope(req), encode_envelope(reply))
        return reply

    monkeypatch.setattr(transport.ZmqServiceClient, 'request', forged)
    runtime = ServiceClientRuntime(config(), identity=IDENTITY, scope_resolver=lambda _: SCOPE,
        peer_resolver=lambda _: PEER, transport_factory=ZmqTransportFactory({'synthetic-key': keys},
            strict_single_connection=True), run_id='synthetic-run', requested_operations={'upstream.direct_write': OPS})
    try:
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            runtime.start()
        assert not runtime._strict_registration_transports
        assert peer.registration_attempts == 0
    finally:
        runtime.close()


def test_threaded_source_timeout_after_dispatch_is_unknown_and_never_retries(monkeypatch):
    from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime
    peer = Peer()
    entered = threading.Event()
    release = threading.Event()

    def wait_for_release():
        entered.set()
        assert release.wait(5)

    peer.before_source = wait_for_release
    monkeypatch.setattr(transport.zmq, 'Context', lambda **_kwargs: Context(peer))
    server, _ = zmq.curve_keypair()
    public, secret = zmq.curve_keypair()
    keys = ClientCurveKeys(server, public, secret)

    def build():
        return ServiceClientRuntime(config(), identity=IDENTITY, scope_resolver=lambda _: SCOPE,
            peer_resolver=lambda _: PEER, transport_factory=ZmqTransportFactory({'synthetic-key': keys},
                strict_single_connection=True), run_id='synthetic-run', requested_operations={'upstream.direct_write': OPS})

    threaded = ThreadedServiceClientRuntime(build)
    try:
        snapshot = threaded.start()
        binding, = snapshot.bindings
        target = BoundServiceTarget('upstream.direct_write', 'service.direct_write', 'synthetic-service',
            'synthetic-instance', 'synthetic-account', SCOPE, IDENTITY, 'synthetic-run', 'gateway',
            'synthetic-key', 'tcp://127.0.0.1:1', PEER, hashlib.sha256(server).hexdigest(),
            binding.registration_id, binding.service_epoch)
        spec = {'account_scope': 'synthetic-account', 'strategy_scope': 'synthetic-strategy',
                'execution_domain_ref': 'synthetic-domain', 'parent_ref': 'parent', 'registration_operation_id': 'op'}
        failures = []

        def invoke():
            try:
                threaded.capture_live_parent_source(target, spec, timeout_seconds=0.1)
            except BaseException as error:
                failures.append(error)

        caller = threading.Thread(target=invoke)
        caller.start()
        assert entered.wait(2)
        caller.join(2)
        assert not caller.is_alive() and len(failures) == 1
        assert isinstance(failures[0], ThreadedRuntimeError) and failures[0].code == 'CALL_OUTCOME_UNKNOWN'
        assert not peer.registers
        release.set()
    finally:
        release.set()
        threaded.close()


def test_capability_rejects_changed_process_identity(setup):
    runtime, target, spec, peer = setup
    cap = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    old_pid = runtime._factory._owner_pid
    runtime._factory._owner_pid = -1
    try:
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            runtime.register_live_parent(cap, target, spec, deadline_monotonic=deadline())
        assert not peer.registers
    finally:
        runtime._factory._owner_pid = old_pid


def test_non_strict_registration_cannot_issue_source_capability(monkeypatch):
    peer = Peer()
    monkeypatch.setattr(transport.zmq, 'Context', lambda **_kwargs: Context(peer))
    server, _ = zmq.curve_keypair()
    public, secret = zmq.curve_keypair()
    runtime = ServiceClientRuntime(config(), identity=IDENTITY, scope_resolver=lambda _: SCOPE,
        peer_resolver=lambda _: PEER, transport_factory=ZmqTransportFactory({
            'synthetic-key': ClientCurveKeys(server, public, secret)}), run_id='synthetic-run',
        requested_operations={'upstream.direct_write': OPS})
    try:
        runtime.start()
        binding, = runtime.bindings
        target = BoundServiceTarget('upstream.direct_write', 'service.direct_write', 'synthetic-service',
            'synthetic-instance', 'synthetic-account', SCOPE, IDENTITY, 'synthetic-run', 'gateway',
            'synthetic-key', 'tcp://127.0.0.1:1', PEER, hashlib.sha256(server).hexdigest(),
            binding.registration_id, binding.service_epoch)
        spec = {'account_scope': 'synthetic-account', 'strategy_scope': 'synthetic-strategy',
                'execution_domain_ref': 'synthetic-domain', 'parent_ref': 'parent', 'registration_operation_id': 'op'}
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
        assert not peer.registers
    finally:
        runtime.close()


def test_owner_interrupted_during_source_send_is_unknown_and_cap_revoked(setup):
    runtime, target, spec, peer = setup
    cap = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())

    def interrupt():
        raise KeyboardInterrupt('synthetic interrupted send')

    peer.before_source = interrupt
    with pytest.raises(ServiceCallOutcomeUnknownError) as caught:
        runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    assert caught.value.operation == 'direct.parent.source'
    assert cap._revoked.is_set() and not peer.registers
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.register_live_parent(cap, target, spec, deadline_monotonic=deadline())
    assert not peer.registers


def test_direct_issuer_source_outside_tracked_capture_revokes_cap(setup):
    runtime, target, spec, peer = setup
    cap = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    peer.revision = 10
    args = {key: spec[key] for key in ('account_scope', 'strategy_scope', 'execution_domain_ref')}
    result, _receipt = runtime._call_bound(target, 'direct.parent.source', args,
        deadline_monotonic=deadline(), _require_issued_receipt=True)
    assert result['result']['journal_revision'] == 10 and cap._revoked.is_set()
    peer.revision = 7
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.register_live_parent(cap, target, spec, deadline_monotonic=deadline())
    assert not peer.registers


@pytest.mark.parametrize('field', ['journal_id', 'generation'])
def test_source_identity_change_on_same_socket_revokes_and_remains_unavailable(setup, field):
    runtime, target, spec, peer = setup
    cap = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    setattr(peer, field, 'other-source')
    with pytest.raises(ServiceCallOutcomeUnknownError):
        runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    assert cap._revoked.is_set()
    with pytest.raises(ServiceCallOutcomeUnknownError):
        runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.register_live_parent(cap, target, spec, deadline_monotonic=deadline())
    assert not peer.registers


def test_register_target_mutation_by_another_thread_never_sends(setup, monkeypatch):
    runtime, target, spec, peer = setup
    cap = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    entered, mutated = threading.Event(), threading.Event()
    original = runtime._invalidate_live_parent_capabilities

    def invalidate():
        entered.set()
        assert mutated.wait(2)
        original()

    def mutate():
        assert entered.wait(2)
        object.__setattr__(target, 'transport_ref', 'other-socket')
        mutated.set()

    monkeypatch.setattr(runtime, '_invalidate_live_parent_capabilities', invalidate)
    thread = threading.Thread(target=mutate)
    thread.start()
    try:
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            runtime.register_live_parent(cap, target, spec, deadline_monotonic=deadline())
    finally:
        thread.join(2)
        monkeypatch.setattr(runtime, '_invalidate_live_parent_capabilities', original)
    assert not thread.is_alive() and not peer.registers


def test_replaced_decoder_cannot_issue_source_receipt(setup, monkeypatch):
    runtime, target, spec, _ = setup
    original = transport.decode_envelope

    def changed(data, **kwargs):
        reply = original(data, **kwargs)
        if reply.header.operation == 'direct.parent.source':
            result = dict(reply.payload['result'])
            source = dict(result['result'])
            source['journal_revision'] = 99
            result['result'] = source
            return WireEnvelope(reply.header, {'ok': True, 'code': 'OK', 'result': result})
        return reply

    monkeypatch.setattr(transport, 'decode_envelope', changed)
    with pytest.raises(ServiceCallOutcomeUnknownError):
        runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    assert not runtime._live_parent_capabilities


def test_abandoned_source_capability_is_removed_by_owner(monkeypatch):
    from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime
    peer = Peer()
    monkeypatch.setattr(transport.zmq, 'Context', lambda **_kwargs: Context(peer))
    server, _ = zmq.curve_keypair()
    public, secret = zmq.curve_keypair()
    keys = ClientCurveKeys(server, public, secret)
    ready, release = threading.Event(), threading.Event()
    runtimes, issued = [], []
    original = ServiceClientRuntime.capture_live_parent_source

    def delayed(self, target, spec, *, deadline_monotonic):
        cap = original(self, target, spec, deadline_monotonic=deadline_monotonic)
        issued.append(cap)
        if len(issued) == 1:
            ready.set()
            assert release.wait(5)
        return cap

    monkeypatch.setattr(ServiceClientRuntime, 'capture_live_parent_source', delayed)

    def build():
        runtime = ServiceClientRuntime(config(), identity=IDENTITY, scope_resolver=lambda _: SCOPE,
            peer_resolver=lambda _: PEER, transport_factory=ZmqTransportFactory({'synthetic-key': keys},
                strict_single_connection=True), run_id='synthetic-run', requested_operations={'upstream.direct_write': OPS})
        runtimes.append(runtime)
        return runtime

    threaded = ThreadedServiceClientRuntime(build)
    try:
        binding, = threaded.start().bindings
        target = BoundServiceTarget('upstream.direct_write', 'service.direct_write', 'synthetic-service',
            'synthetic-instance', 'synthetic-account', SCOPE, IDENTITY, 'synthetic-run', 'gateway',
            'synthetic-key', 'tcp://127.0.0.1:1', PEER, hashlib.sha256(server).hexdigest(),
            binding.registration_id, binding.service_epoch)
        spec = {'account_scope': 'synthetic-account', 'strategy_scope': 'synthetic-strategy',
                'execution_domain_ref': 'synthetic-domain', 'parent_ref': 'parent', 'registration_operation_id': 'op'}
        failures = []

        def invoke():
            try:
                threaded.capture_live_parent_source(target, spec, timeout_seconds=0.1)
            except BaseException as error:
                failures.append(error)

        caller = threading.Thread(target=invoke)
        caller.start()
        assert ready.wait(2)
        caller.join(2)
        assert not caller.is_alive() and len(failures) == 1
        assert isinstance(failures[0], ThreadedRuntimeError) and failures[0].code == 'CALL_OUTCOME_UNKNOWN'
        release.set()
        next_cap = threaded.capture_live_parent_source(target, spec)
        assert issued[0]._revoked.is_set() and issued[0] not in runtimes[0]._live_parent_capabilities
        assert next_cap in runtimes[0]._live_parent_capabilities
    finally:
        release.set()
        threaded.close()


def test_revoked_entries_are_pruned_before_capacity_check(setup):
    from bt_api_py.service_runtime import _LiveParentSourceCapability
    runtime, target, spec, _ = setup
    old = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    old._revoked.set()
    for _ in range(1023):
        revoked = _LiveParentSourceCapability()
        revoked._revoked.set()
        runtime._live_parent_capabilities[revoked] = ()
    assert len(runtime._live_parent_capabilities) == 1024
    fresh = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    assert len(runtime._live_parent_capabilities) == 1
    assert fresh in runtime._live_parent_capabilities


def _observe_close_refs(runtime, target, spec):
    capability = runtime.capture_live_parent_source(target, spec, deadline_monotonic=deadline())
    adapter = runtime._transports[(target.service_id, target.transport_ref)]
    client = adapter._transport
    return capability, weakref.ref(client), weakref.ref(client._socket)


def test_confirmed_close_clears_proofs_and_allows_socket_collection(setup):
    runtime, target, spec, _ = setup
    cap, client_ref, socket_ref = _observe_close_refs(runtime, target, spec)
    assert runtime._strict_registration_transports and runtime._authenticated_registration_transports
    assert runtime._live_parent_revisions and runtime._live_parent_source_identities
    runtime.close()
    assert cap._revoked.is_set()
    assert not runtime._strict_registration_transports and not runtime._authenticated_registration_transports
    assert not runtime._authenticated_registration_peers
    assert not runtime._live_parent_revisions and not runtime._live_parent_source_identities
    gc.collect()
    assert client_ref() is None and socket_ref() is None


def test_failed_close_keeps_proofs_and_handle_until_successful_retry(setup):
    from bt_api_py.service_runtime import ServiceRuntimeError
    runtime, target, spec, _ = setup
    cap, client_ref, socket_ref = _observe_close_refs(runtime, target, spec)
    client_ref()._socket.fail_closes = 1
    with pytest.raises(ServiceRuntimeError, match='CLOSE_FAILED'):
        runtime.close()
    assert cap._revoked.is_set() and runtime.state == 'close_failed'
    assert runtime._transports and runtime._strict_registration_transports
    assert runtime._authenticated_registration_transports and runtime._live_parent_revisions
    assert runtime._live_parent_source_identities and client_ref() is not None and socket_ref() is not None
    runtime.close()
    assert not runtime._transports and not runtime._strict_registration_transports
    assert not runtime._authenticated_registration_transports and not runtime._authenticated_registration_peers
    assert not runtime._live_parent_revisions and not runtime._live_parent_source_identities
    gc.collect()
    assert client_ref() is None and socket_ref() is None


def test_recover_releases_prior_generations_and_bounds_caches(setup):
    runtime, target, spec, _ = setup
    old_clients, old_sockets = [], []
    for _ in range(4):
        cap, client_ref, socket_ref = _observe_close_refs(runtime, target, spec)
        old_clients.append(client_ref)
        old_sockets.append(socket_ref)
        runtime.recover()
        assert cap._revoked.is_set()
        gc.collect()
        assert all(reference() is None for reference in old_clients + old_sockets)
        assert len(runtime._strict_registration_transports) == 1
        assert len(runtime._authenticated_registration_transports) == 1
        assert not runtime._live_parent_revisions and not runtime._live_parent_source_identities


def test_shared_client_proofs_retained_until_all_handles_close(setup):
    from bt_api_py.service_runtime import ServiceRuntimeError, _RequestNotSentAdapter
    runtime, target, spec, _ = setup
    cap, client_ref, socket_ref = _observe_close_refs(runtime, target, spec)
    original = runtime._transports[(target.service_id, target.transport_ref)]
    alternate = _RequestNotSentAdapter(original._transport, original._error_type)
    runtime._transports[('synthetic-alias', 'synthetic-alias')] = alternate
    attempts = []

    def fail_once():
        attempts.append(True)
        raise RuntimeError('synthetic alternate close failure')

    alternate.close = fail_once
    with pytest.raises(ServiceRuntimeError, match='CLOSE_FAILED'):
        runtime.close()
    assert cap._revoked.is_set() and len(runtime._transports) == 1
    assert runtime._strict_registration_transports and runtime._authenticated_registration_transports
    assert runtime._live_parent_revisions and runtime._live_parent_source_identities
    del alternate.close
    runtime.close()
    assert not runtime._strict_registration_transports and not runtime._authenticated_registration_transports
    assert not runtime._live_parent_revisions and not runtime._live_parent_source_identities
    del original, alternate
    gc.collect()
    assert client_ref() is None and socket_ref() is None


def test_poisoned_registration_marker_is_preserved_after_close(setup):
    runtime, target, spec, _ = setup
    _observe_close_refs(runtime, target, spec)
    poisoned = ('another-service', 'another-transport', 'registration', 'epoch')
    runtime._authenticated_registration_transports[poisoned] = None
    runtime._authenticated_registration_peers[poisoned] = (PEER, target.server_key_fingerprint)
    runtime.close()
    assert runtime._authenticated_registration_transports == {poisoned: None}
    assert poisoned in runtime._authenticated_registration_peers
    assert not runtime._strict_registration_transports
    assert not runtime._live_parent_revisions and not runtime._live_parent_source_identities


def test_other_connection_proofs_survive_when_its_close_fails(setup):
    from bt_api_py.service_runtime import ServiceRuntimeError
    runtime, target, spec, _ = setup
    _observe_close_refs(runtime, target, spec)
    main_key = (target.service_id, target.transport_ref, target.registration_id, target.service_epoch)
    profile = dict(runtime._config['communication']['profiles'][target.transport_ref])
    runtime._config['communication']['profiles']['gateway-other'] = profile
    runtime._peers['other-service'] = PEER
    other = runtime._factory(profile, PEER)
    runtime._transports[('other-service', 'gateway-other')] = other
    runtime._rpc('other-service', 'gateway-other', SCOPE, 'service.register', {}, timeout=2)
    other_key = ('other-service', 'gateway-other', 'registration', 'epoch')
    other_binding = runtime._strict_registration_transports[other_key][2]
    runtime._live_parent_revisions[(other_binding, 'other-account', 'journal', 'generation')] = 7
    runtime._live_parent_source_identities[(other_binding, 'other-account')] = ('journal', 'generation')

    def fail():
        raise RuntimeError('synthetic other close failure')

    other.close = fail
    with pytest.raises(ServiceRuntimeError, match='CLOSE_FAILED'):
        runtime.close()
    assert main_key not in runtime._strict_registration_transports
    assert main_key not in runtime._authenticated_registration_transports
    assert other_key in runtime._strict_registration_transports
    assert other_key in runtime._authenticated_registration_transports
    assert (other_binding, 'other-account', 'journal', 'generation') in runtime._live_parent_revisions
    assert (other_binding, 'other-account') in runtime._live_parent_source_identities
    del other.close
    runtime.close()
    assert not runtime._strict_registration_transports and not runtime._authenticated_registration_transports
    assert not runtime._live_parent_revisions and not runtime._live_parent_source_identities
