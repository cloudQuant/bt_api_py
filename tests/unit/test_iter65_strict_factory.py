"""Strict factory wiring with synthetic credentials and sockets; no network I/O."""

from __future__ import annotations

import threading

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_transport_zmq import service_v2 as transport

from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ServiceRuntimeStartupCleanupError,
    ZmqTransportFactory,
)


PEER = WireIdentity(WireRole.SERVICE, 'synthetic-peer', 'synthetic-service')
PROFILE = {
    'backend': 'zmq', 'security_profile_ref': 'test-key',
    'endpoints': {'rpc_connect': 'tcp://127.0.0.1:1'},
    'options': {'high_water_mark': 1000, 'max_message_bytes': 65536},
}


class Socket:
    def __init__(self):
        self.options = {}
        self.events = []
        self.closed = False
        self.close_failures = 0
        self.close_threads = []

    def setsockopt(self, option, value):
        self.options[option] = value
        self.events.append(('option', option, value))

    def getsockopt(self, option):
        return self.options[option]

    def connect(self, endpoint):
        self.events.append(('connect', endpoint))

    def close(self, *, linger):
        self.close_threads.append(threading.get_ident())
        if self.close_failures:
            self.close_failures -= 1
            raise RuntimeError('synthetic secret must not escape')
        self.closed = True


class Context:
    def __init__(self, **_kwargs):
        self.sockets = []
        self.terminated = False

    def socket(self, kind):
        assert kind == zmq.DEALER
        sock = Socket()
        self.sockets.append(sock)
        return sock

    def term(self):
        self.terminated = True


@pytest.fixture
def setup(monkeypatch):
    contexts = []

    def context(**kwargs):
        result = Context(**kwargs)
        contexts.append(result)
        return result

    monkeypatch.setattr(transport.zmq, 'Context', context)
    server, _ = zmq.curve_keypair()
    public, secret = zmq.curve_keypair()
    keys = ClientCurveKeys(server, public, secret)
    return keys, contexts


def factory(keys, **kwargs):
    return ZmqTransportFactory({'test-key': keys}, **kwargs)


def test_default_uses_legacy_constructor_signature_and_needs_no_new_capability(setup, monkeypatch):
    keys, _ = setup
    calls = []

    class LegacyClient:
        def __init__(self, endpoint, peer, *, credentials, high_water_mark, max_message_bytes, context_pool):
            calls.append((endpoint, peer))

    monkeypatch.setattr(transport, 'ZmqServiceClient', LegacyClient)
    monkeypatch.delattr(transport, '_strict_client_binding')
    monkeypatch.delattr(transport, '_ServiceSocketBinding')
    assert isinstance(factory(keys)(PROFILE, PEER)._transport, LegacyClient)
    assert isinstance(factory(keys, strict_single_connection=False)(PROFILE, PEER)._transport, LegacyClient)
    assert len(calls) == 2


@pytest.mark.parametrize('value', [None, 0, 1, 'true', [], object()])
def test_option_requires_exact_bool_before_socket_allocation(setup, value):
    keys, contexts = setup
    with pytest.raises(ValueError, match='strict_single_connection'):
        factory(keys, strict_single_connection=value)
    assert contexts == []


def test_strict_real_transport_sets_options_before_connect_and_pins_first_binding(setup):
    keys, contexts = setup
    client = factory(keys, strict_single_connection=True)(PROFILE, PEER)._transport
    sock = contexts[0].sockets[0]
    try:
        binding = transport._strict_client_binding(client)
        assert binding is client._socket_binding and binding.strict_single_connection is True
        assert client._socket_binding_current(binding)
        connect = sock.events.index(('connect', PROFILE['endpoints']['rpc_connect']))
        assert ('option', zmq.IMMEDIATE, 1) in sock.events[:connect]
        assert ('option', zmq.RECONNECT_IVL, -1) in sock.events[:connect]
        assert sum(event[0] == 'connect' for event in sock.events) == 1
    finally:
        client.close()
    assert sock.closed and contexts[0].terminated


def test_missing_capability_fails_closed_without_constructing(setup, monkeypatch):
    keys, contexts = setup
    monkeypatch.delattr(transport, '_strict_client_binding')
    with pytest.raises(ServiceRuntimeError, match='STRICT_TRANSPORT_UNAVAILABLE'):
        factory(keys, strict_single_connection=True)(PROFILE, PEER)
    assert not contexts


def test_old_constructor_rejects_strict_without_fallback_or_secret_in_error(setup, monkeypatch):
    keys, contexts = setup
    calls = []

    class LegacyClient:
        def __new__(cls, *args, **kwargs):
            calls.append(kwargs)
            raise TypeError('synthetic secret must not escape')

    monkeypatch.setattr(transport, 'ZmqServiceClient', LegacyClient)
    with pytest.raises(ServiceRuntimeError, match='STRICT_TRANSPORT_UNAVAILABLE') as caught:
        factory(keys, strict_single_connection=True)(PROFILE, PEER)
    assert caught.value.__suppress_context__ and 'secret' not in str(caught.value)
    assert len(calls) == 1 and calls[0]['strict_single_connection'] is True
    assert not contexts


def test_postcheck_failure_closes_on_owner_thread(setup, monkeypatch):
    keys, contexts = setup
    monkeypatch.setattr(transport, '_strict_client_binding', lambda _client: None)
    with pytest.raises(ServiceRuntimeError, match='STRICT_TRANSPORT_UNAVAILABLE'):
        factory(keys, strict_single_connection=True)(PROFILE, PEER)
    sock = contexts[0].sockets[0]
    assert sock.closed and contexts[0].terminated
    assert sock.close_threads == [threading.get_ident()]


def test_postcheck_cleanup_failure_preserves_handle_for_same_thread_retry(setup, monkeypatch):
    keys, contexts = setup
    original = transport._strict_client_binding

    def reject_after_construction(client):
        binding = original(client)
        if binding is not None:
            binding.socket.close_failures = 1
            return None
        return binding

    monkeypatch.setattr(transport, '_strict_client_binding', reject_after_construction)
    with pytest.raises(ServiceRuntimeStartupCleanupError) as caught:
        factory(keys, strict_single_connection=True)(PROFILE, PEER)
    client = caught.value.transport
    sock = contexts[0].sockets[0]
    assert not sock.closed and 'secret' not in str(caught.value)
    client.close()
    assert sock.closed and contexts[0].terminated
    assert sock.close_threads == [threading.get_ident()] * 2


def runtime_for(factory_):
    config = parse_client_config({
        'client_config_schema_version': 1, 'process_role': 'strategy_client',
        'registration': {'strategy_ref': 'synthetic-strategy'},
        'clients': {'gateway': {'config_schema_version': 1, 'settings': {'bindings': [{
            'mount': 'store.account', 'service_id': 'synthetic-service',
            'instance_id': 'synthetic-instance', 'scope_ref': 'synthetic-account',
            'account_ref': 'synthetic-account', 'transport_ref': 'transport',
        }]}}},
        'communication': {'config_schema_version': 1, 'profiles': {'transport': {
            'backend': 'zmq', 'config_schema_version': 1,
            'endpoints': PROFILE['endpoints'], 'security_profile_ref': 'test-key',
        }}},
    })
    return ServiceClientRuntime(
        config, identity=WireIdentity(WireRole.STRATEGY_CLIENT, 'synthetic-client', 'synthetic-actor'),
        scope_resolver=lambda _: WireScope('synthetic-domain', account_ref='synthetic-account'),
        peer_resolver=lambda _: PEER, transport_factory=factory_,
    )


def test_runtime_retains_strict_postcheck_cleanup_failure_for_retry(setup, monkeypatch):
    keys, contexts = setup
    original = transport._strict_client_binding

    def reject_after_construction(client):
        binding = original(client)
        if binding is not None:
            binding.socket.close_failures = 2
            return None
        return binding

    monkeypatch.setattr(transport, '_strict_client_binding', reject_after_construction)
    runtime = runtime_for(factory(keys, strict_single_connection=True))
    with pytest.raises(ServiceRuntimeError, match='CLOSE_FAILED'):
        runtime.start()
    sock = contexts[0].sockets[0]
    assert runtime.state == 'close_failed' and not sock.closed
    assert len(runtime._transports) == 1
    runtime.close()
    assert runtime.state == 'closed' and sock.closed and contexts[0].terminated
    assert sock.close_threads == [threading.get_ident()] * 3


def test_old_constructor_signature_has_no_strict_fallback(setup, monkeypatch):
    keys, contexts = setup
    calls = []

    class LegacyClient:
        def __init__(self, endpoint, peer, *, credentials, high_water_mark, max_message_bytes, context_pool):
            calls.append('constructed')

    monkeypatch.setattr(transport, 'ZmqServiceClient', LegacyClient)
    with pytest.raises(ServiceRuntimeError, match='STRICT_TRANSPORT_UNAVAILABLE'):
        factory(keys, strict_single_connection=True)(PROFILE, PEER)
    assert not calls and not contexts


def test_changed_socket_option_fails_postcheck_and_closes(setup, monkeypatch):
    keys, contexts = setup
    original = transport._strict_client_binding

    def change_after_construction(client):
        binding = original(client)
        if binding is not None:
            binding.socket.options[zmq.RECONNECT_IVL] = 50
        return binding

    monkeypatch.setattr(transport, '_strict_client_binding', change_after_construction)
    with pytest.raises(ServiceRuntimeError, match='STRICT_TRANSPORT_UNAVAILABLE'):
        factory(keys, strict_single_connection=True)(PROFILE, PEER)
    assert contexts[0].sockets[0].closed and contexts[0].terminated


def test_transport_constructor_cleanup_error_retains_handle(setup, monkeypatch):
    keys, contexts = setup
    original = Socket.setsockopt

    def fail_option(sock, option, value):
        if option == zmq.RECONNECT_IVL:
            sock.close_failures = 1
            raise RuntimeError('synthetic secret must not escape')
        original(sock, option, value)

    monkeypatch.setattr(Socket, 'setsockopt', fail_option)
    with pytest.raises(ServiceRuntimeStartupCleanupError) as caught:
        factory(keys, strict_single_connection=True)(PROFILE, PEER)
    sock = contexts[0].sockets[0]
    assert not sock.closed and 'secret' not in str(caught.value)
    caught.value.transport.close()
    assert sock.closed and contexts[0].terminated
    assert sock.close_threads == [threading.get_ident()] * 2


def test_runtime_start_uses_strict_client_and_rolls_back_on_discovery_failure(setup, monkeypatch):
    keys, contexts = setup
    runtime = runtime_for(factory(keys, strict_single_connection=True))
    seen = []

    def fail_discovery(_service_id, _transport_ref, _scope, operation, _arguments, *, timeout):
        assert operation == 'service.discover'
        adapter = next(iter(runtime._transports.values()))
        client = adapter._transport
        binding = transport._strict_client_binding(client)
        assert binding is client._socket_binding and client._socket_binding_current(binding)
        seen.append(binding)
        raise ServiceRuntimeError('SYNTHETIC_DISCOVERY_FAILURE')

    monkeypatch.setattr(runtime, '_rpc', fail_discovery)
    with pytest.raises(ServiceRuntimeError, match='SYNTHETIC_DISCOVERY_FAILURE'):
        runtime.start()
    assert len(seen) == 1 and not runtime._transports
    assert contexts[0].sockets[0].closed and contexts[0].terminated


@pytest.mark.parametrize(('initial', 'changed'), [(True, False), (False, True), (True, 1), (False, None)])
def test_factory_mode_mutation_before_call_fails_before_allocating(setup, initial, changed):
    keys, contexts = setup
    instance = factory(keys, strict_single_connection=initial)
    instance._strict_single_connection = changed
    with pytest.raises(ServiceRuntimeError, match='STRICT_TRANSPORT_UNAVAILABLE'):
        instance(PROFILE, PEER)
    assert not contexts


def test_factory_missing_mode_fails_closed(setup):
    keys, contexts = setup
    instance = factory(keys, strict_single_connection=True)
    del instance._strict_single_connection
    with pytest.raises(ServiceRuntimeError, match='STRICT_TRANSPORT_UNAVAILABLE'):
        instance(PROFILE, PEER)
    assert not contexts


@pytest.mark.parametrize('initial', [False, True])
def test_concurrent_factory_mode_change_closes_constructed_client(setup, monkeypatch, initial):
    keys, contexts = setup
    instance = factory(keys, strict_single_connection=initial)
    entered = threading.Event()
    resume = threading.Event()
    original = Socket.connect
    result = []

    def connect(sock, endpoint):
        original(sock, endpoint)
        entered.set()
        assert resume.wait(5)

    def construct():
        try:
            instance(PROFILE, PEER)
        except BaseException as error:
            result.append((error, threading.get_ident()))

    monkeypatch.setattr(Socket, 'connect', connect)
    worker = threading.Thread(target=construct)
    worker.start()
    try:
        assert entered.wait(5)
        instance._strict_single_connection = not initial
    finally:
        resume.set()
        worker.join(5)
    assert not worker.is_alive() and len(result) == 1
    assert isinstance(result[0][0], ServiceRuntimeError)
    assert result[0][0].code == 'STRICT_TRANSPORT_UNAVAILABLE'
    sock = contexts[0].sockets[0]
    assert sock.closed and contexts[0].terminated
    assert sock.close_threads == [result[0][1]]
    if initial:
        assert sock.options[zmq.RECONNECT_IVL] == -1


def test_factory_mode_change_cleanup_failure_keeps_handle(setup, monkeypatch):
    keys, contexts = setup
    instance = factory(keys, strict_single_connection=True)
    original = Socket.connect

    def change(sock, endpoint):
        original(sock, endpoint)
        sock.close_failures = 1
        instance._strict_single_connection = False

    monkeypatch.setattr(Socket, 'connect', change)
    with pytest.raises(ServiceRuntimeStartupCleanupError) as caught:
        instance(PROFILE, PEER)
    sock = contexts[0].sockets[0]
    assert not sock.closed
    caught.value.transport.close()
    assert sock.closed and contexts[0].terminated
    assert sock.close_threads == [threading.get_ident()] * 2


def test_factory_mode_registry_weak_lifetime_and_fork_reset(setup, monkeypatch):
    import gc
    import weakref
    import bt_api_py.service_runtime as runtime_module

    keys, contexts = setup
    instance = factory(keys, strict_single_connection=True)
    factory_id = id(instance)
    reference = weakref.ref(instance)
    assert runtime_module._FACTORY_MODES[factory_id][0]() is instance
    del instance
    gc.collect()
    assert reference() is None and factory_id not in runtime_module._FACTORY_MODES

    # Simulate the child-side hook without disturbing other tests' registry.
    monkeypatch.setattr(runtime_module, '_FACTORY_MODES', {})
    monkeypatch.setattr(runtime_module, '_FACTORY_MODES_LOCK', threading.Lock())
    inherited = factory(keys, strict_single_connection=True)
    runtime_module._reset_factory_modes_after_fork()
    with pytest.raises(ServiceRuntimeError, match='STRICT_TRANSPORT_UNAVAILABLE'):
        inherited(PROFILE, PEER)
    assert not contexts
    fresh = factory(keys, strict_single_connection=False)
    client = fresh(PROFILE, PEER)._transport
    client.close()
    assert contexts[0].terminated


@pytest.mark.parametrize('interrupt', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('phase', ['factory_check', 'strict_postcheck', 'adapter'])
def test_interrupt_after_construction_cleans_and_propagates(setup, monkeypatch, interrupt, phase):
    import bt_api_py.service_runtime as runtime_module

    keys, contexts = setup
    instance = factory(keys, strict_single_connection=True)
    original_check = instance._require_factory_mode
    calls = []

    def check():
        calls.append(None)
        if phase == 'factory_check' and len(calls) == 3:
            raise interrupt('synthetic interrupt')
        return original_check()

    monkeypatch.setattr(instance, '_require_factory_mode', check)
    if phase == 'strict_postcheck':
        original_binding = transport._strict_client_binding

        def fail_after_construction(client):
            binding = original_binding(client)
            if binding is not None:
                raise interrupt('synthetic interrupt')
            return binding

        monkeypatch.setattr(transport, '_strict_client_binding', fail_after_construction)
    if phase == 'adapter':
        def fail_adapter(*_args):
            raise interrupt('synthetic interrupt')

        monkeypatch.setattr(runtime_module, '_RequestNotSentAdapter', fail_adapter)
    with pytest.raises(interrupt, match='synthetic interrupt'):
        instance(PROFILE, PEER)
    sock = contexts[0].sockets[0]
    assert sock.closed and contexts[0].terminated
    assert sock.close_threads == [threading.get_ident()]


def test_interrupt_cleanup_failure_retains_handle(setup, monkeypatch):
    keys, contexts = setup
    instance = factory(keys, strict_single_connection=True)
    original = transport._strict_client_binding

    def interrupt(client):
        binding = original(client)
        if binding is not None:
            binding.socket.close_failures = 1
            raise KeyboardInterrupt('synthetic interrupt')
        return binding

    monkeypatch.setattr(transport, '_strict_client_binding', interrupt)
    with pytest.raises(ServiceRuntimeStartupCleanupError) as caught:
        instance(PROFILE, PEER)
    sock = contexts[0].sockets[0]
    assert not sock.closed
    caught.value.transport.close()
    assert sock.closed and contexts[0].terminated


def test_factory_checks_mode_after_adapter_creation(setup, monkeypatch):
    import bt_api_py.service_runtime as runtime_module

    keys, contexts = setup
    instance = factory(keys, strict_single_connection=True)
    original = runtime_module._RequestNotSentAdapter

    def change_mode(*args):
        adapter = original(*args)
        instance._strict_single_connection = False
        return adapter

    monkeypatch.setattr(runtime_module, '_RequestNotSentAdapter', change_mode)
    with pytest.raises(ServiceRuntimeError, match='STRICT_TRANSPORT_UNAVAILABLE'):
        instance(PROFILE, PEER)
    assert contexts[0].sockets[0].closed and contexts[0].terminated


def test_reinitialization_cannot_downgrade_or_change_profile(setup):
    keys, contexts = setup
    instance = factory(keys, strict_single_connection=True)
    old_profiles = instance._profiles
    with pytest.raises(ServiceRuntimeError, match='FACTORY_ALREADY_INITIALIZED'):
        instance.__init__({}, strict_single_connection=False)
    assert instance._profiles is old_profiles and instance._strict_single_connection is True
    client = instance(PROFILE, PEER)._transport
    try:
        assert transport._strict_client_binding(client) is client._socket_binding
    finally:
        client.close()
    assert contexts[0].terminated


def test_concurrent_reinitialization_rejected_before_field_changes(setup):
    from collections.abc import Mapping

    keys, contexts = setup
    instance = ZmqTransportFactory.__new__(ZmqTransportFactory)
    entered = threading.Event()
    resume = threading.Event()
    errors = []

    class BlockingProfiles(Mapping):
        def __iter__(self):
            entered.set()
            assert resume.wait(5)
            return iter(('test-key',))

        def __len__(self):
            return 1

        def __getitem__(self, name):
            assert name == 'test-key'
            return keys

    def initialize():
        try:
            instance.__init__(BlockingProfiles(), strict_single_connection=True)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=initialize)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(ServiceRuntimeError, match='FACTORY_ALREADY_INITIALIZED'):
            instance.__init__({}, strict_single_connection=False)
        assert not hasattr(instance, '_strict_single_connection')
    finally:
        resume.set()
        thread.join(5)
    assert not thread.is_alive() and not errors
    assert instance._strict_single_connection is True
    client = instance(PROFILE, PEER)._transport
    client.close()
    assert contexts[0].terminated


def test_failed_factory_initialization_releases_reservation(setup):
    from collections.abc import Mapping

    keys, contexts = setup

    class BadProfiles(Mapping):
        def __iter__(self):
            raise RuntimeError('synthetic initialization failure')

        def __len__(self):
            return 1

        def __getitem__(self, _name):
            raise AssertionError('not reached')

    instance = ZmqTransportFactory.__new__(ZmqTransportFactory)
    with pytest.raises(RuntimeError, match='synthetic initialization failure'):
        instance.__init__(BadProfiles(), strict_single_connection=True)
    instance.__init__({'test-key': keys}, strict_single_connection=True)
    client = instance(PROFILE, PEER)._transport
    client.close()
    assert contexts[0].terminated


def test_return_line_interrupt_cleans_created_client(setup):
    import inspect
    import sys

    keys, contexts = setup
    instance = factory(keys, strict_single_connection=True)
    lines, start = inspect.getsourcelines(ZmqTransportFactory.__call__)
    return_line = next(start + index for index, line in enumerate(lines) if line.strip() == 'return adapter')
    previous = sys.gettrace()
    injected = []

    def trace(frame, event, _arg):
        if frame.f_code is ZmqTransportFactory.__call__.__code__ and event == 'line' and frame.f_lineno == return_line:
            injected.append(True)
            raise KeyboardInterrupt('synthetic return interrupt')
        return trace

    try:
        sys.settrace(trace)
        with pytest.raises(KeyboardInterrupt, match='synthetic return interrupt'):
            instance(PROFILE, PEER)
    finally:
        sys.settrace(previous)
    sock = contexts[0].sockets[0]
    assert injected == [True] and sock.closed and contexts[0].terminated
    assert sock.close_threads == [threading.get_ident()]


@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('fail_cleanup', [False, True])
def test_runtime_handoff_interrupted_after_factory_call(setup, strict, fail_cleanup):
    import dis
    import inspect
    import sys
    import bt_api_py.service_runtime as runtime_module

    keys, contexts = setup
    runtime = runtime_for(factory(keys, strict_single_connection=strict))
    lines, start = inspect.getsourcelines(ServiceClientRuntime._register)
    factory_line = next(start + index for index, line in enumerate(lines)
                        if 'self._factory(profiles[transport_ref], peer, _pending=pending)' in line)
    instructions = list(dis.get_instructions(ServiceClientRuntime._register))
    call_index = next(index for index, inst in enumerate(instructions)
                      if inst.opname == 'CALL' and inst.positions.lineno == factory_line)
    after_call = instructions[call_index + 1].offset
    assert any(inst.opname == 'STORE_SUBSCR' for inst in instructions[call_index + 1:call_index + 6])
    old_trace = sys.gettrace()
    injected = []

    def trace(frame, event, _arg):
        if frame.f_code is ServiceClientRuntime._register.__code__:
            frame.f_trace_opcodes = True
            if event == 'opcode' and frame.f_lasti == after_call:
                pending = next(iter(runtime._transports.values()))
                assert type(pending) is runtime_module._PendingZmqTransport
                adapter = pending._transport
                assert type(adapter) is runtime_module._RequestNotSentAdapter
                client = adapter._transport
                if strict:
                    assert transport._strict_client_binding(client) is client._socket_binding
                sock = contexts[0].sockets[0]
                sock.close_failures = 2 if fail_cleanup else 0
                injected.append(True)
                raise KeyboardInterrupt('synthetic post-call interrupt')
        return trace

    try:
        sys.settrace(trace)
        if fail_cleanup:
            with pytest.raises(ServiceRuntimeError, match='CLOSE_FAILED'):
                runtime.start()
        else:
            with pytest.raises(KeyboardInterrupt, match='synthetic post-call interrupt'):
                runtime.start()
    finally:
        sys.settrace(old_trace)
    assert injected == [True]
    sock = contexts[0].sockets[0]
    if fail_cleanup:
        assert runtime.state == 'close_failed' and not sock.closed
        assert len(runtime._transports) == 1
        runtime.close()
        assert runtime.state == 'closed'
        assert sock.close_threads == [threading.get_ident()] * 3
    else:
        assert runtime.state == 'failed' and not runtime._transports
        assert sock.close_threads == [threading.get_ident()]
    assert sock.closed and contexts[0].terminated


def test_pending_handoff_rejects_cross_thread_close(setup):
    import bt_api_py.service_runtime as runtime_module

    keys, contexts = setup
    pending = runtime_module._PendingZmqTransport()
    adapter = factory(keys, strict_single_connection=True)(PROFILE, PEER, _pending=pending)
    errors = []

    def close_elsewhere():
        try:
            pending.close()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=close_elsewhere)
    thread.start()
    thread.join(5)
    assert not thread.is_alive() and len(errors) == 1
    assert isinstance(errors[0], ServiceRuntimeError) and errors[0].code == 'TRANSPORT_OWNER_MISMATCH'
    assert pending._transport is adapter and not contexts[0].sockets[0].closed
    pending.close()
    pending.close()
    assert contexts[0].sockets[0].close_threads == [threading.get_ident()]
    assert contexts[0].terminated


def test_third_party_factory_keeps_old_interface_and_cleanup():
    calls = []

    class LocalTransport:
        def request(self, *_args, **_kwargs):
            raise RuntimeError('synthetic discovery failure')

        def close(self):
            calls.append('closed')

    def third_party(profile, peer):
        calls.append((profile['backend'], peer))
        return LocalTransport()

    runtime = runtime_for(third_party)
    with pytest.raises(ServiceRuntimeError, match='REQUIRED_REGISTRATION_FAILED'):
        runtime.start()
    assert calls == [('zmq', PEER), 'closed'] and not runtime._transports


def test_pending_handoff_rejects_foreign_process_identity(setup):
    import bt_api_py.service_runtime as runtime_module

    keys, contexts = setup
    pending = runtime_module._PendingZmqTransport()
    factory(keys)(PROFILE, PEER, _pending=pending)
    owner_pid = pending._owner_pid
    pending._owner_pid = -1  # Simulate the PID fence after a fork.
    with pytest.raises(ServiceRuntimeError, match='TRANSPORT_OWNER_MISMATCH'):
        pending.close()
    assert not contexts[0].sockets[0].closed
    pending._owner_pid = owner_pid
    pending.close()
    assert contexts[0].sockets[0].closed and contexts[0].terminated


@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('fail_cleanup', [False, True])
def test_runtime_constructor_return_store_interrupt_keeps_owner(setup, strict, fail_cleanup):
    import dis
    import inspect
    import sys
    import bt_api_py.service_runtime as runtime_module

    keys, contexts = setup
    runtime = runtime_for(factory(keys, strict_single_connection=strict))
    lines, first_line = inspect.getsourcelines(ZmqTransportFactory.__call__)
    constructor_line = next(first_line + index for index, line in enumerate(lines)
                            if 'client = ZmqServiceClient(endpoint, expected_peer, **options)' in line)
    instructions = list(dis.get_instructions(ZmqTransportFactory.__call__))
    call_index = next(index for index, inst in enumerate(instructions)
                      if inst.opname in ('CALL', 'CALL_FUNCTION_EX') and inst.positions.lineno == constructor_line)
    target = instructions[call_index + 1]
    assert target.opname == 'STORE_FAST' and target.argval == 'client'
    previous = sys.gettrace()
    injected = []

    def trace(frame, event, _arg):
        if frame.f_code is ZmqTransportFactory.__call__.__code__:
            frame.f_trace_opcodes = True
            if event == 'opcode' and frame.f_lasti == target.offset:
                pending = next(iter(runtime._transports.values()))
                assert type(pending) is runtime_module._PendingZmqTransport
                owner = pending._transport
                assert type(owner) is transport._ServiceClientStartupOwner
                assert type(owner._client) is transport.ZmqServiceClient
                if strict:
                    assert transport._strict_client_binding(owner._client) is owner._client._socket_binding
                contexts[0].sockets[0].close_failures = 2 if fail_cleanup else 0
                injected.append(True)
                raise KeyboardInterrupt('synthetic constructor return interrupt')
        return trace

    try:
        sys.settrace(trace)
        if fail_cleanup:
            with pytest.raises(ServiceRuntimeError, match='CLOSE_FAILED'):
                runtime.start()
        else:
            with pytest.raises(KeyboardInterrupt, match='synthetic constructor return interrupt'):
                runtime.start()
    finally:
        sys.settrace(previous)
    assert injected == [True]
    sock = contexts[0].sockets[0]
    if fail_cleanup:
        assert runtime.state == 'close_failed' and not sock.closed
        assert len(runtime._transports) == 1
        runtime.close()
        assert sock.close_threads == [threading.get_ident()] * 3
    else:
        assert runtime.state == 'failed' and not runtime._transports
        assert sock.close_threads == [threading.get_ident()]
    assert sock.closed and contexts[0].terminated


@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('fail_cleanup', [False, True])
def test_runtime_constructor_exception_owner_cleanup(setup, monkeypatch, strict, fail_cleanup):
    keys, contexts = setup
    runtime = runtime_for(factory(keys, strict_single_connection=strict))
    original = Socket.connect

    def fail(sock, endpoint):
        original(sock, endpoint)
        sock.close_failures = 2 if fail_cleanup else 0
        raise RuntimeError('synthetic constructor failure with secret')

    monkeypatch.setattr(Socket, 'connect', fail)
    expected = 'CLOSE_FAILED' if fail_cleanup else (
        'STRICT_TRANSPORT_UNAVAILABLE' if strict else 'REQUIRED_REGISTRATION_FAILED'
    )
    with pytest.raises(ServiceRuntimeError, match=expected) as caught:
        runtime.start()
    assert 'secret' not in str(caught.value)
    sock = contexts[0].sockets[0]
    if fail_cleanup:
        assert runtime.state == 'close_failed' and not sock.closed
        runtime.close()
        assert sock.close_threads == [threading.get_ident()] * 3
    else:
        assert not runtime._transports
        assert sock.close_threads == [threading.get_ident()]
    assert sock.closed and contexts[0].terminated


def test_old_transport_without_startup_owner_keeps_default_runtime_path(setup, monkeypatch):
    keys, contexts = setup
    monkeypatch.delattr(transport, '_ServiceClientStartupOwner')
    runtime = runtime_for(factory(keys))

    def stop(*_args, **_kwargs):
        raise ServiceRuntimeError('SYNTHETIC_DISCOVERY_FAILURE')

    monkeypatch.setattr(runtime, '_rpc', stop)
    with pytest.raises(ServiceRuntimeError, match='SYNTHETIC_DISCOVERY_FAILURE'):
        runtime.start()
    assert contexts[0].sockets[0].closed and contexts[0].terminated


def test_strict_requires_transport_startup_owner_capability(setup, monkeypatch):
    keys, contexts = setup
    monkeypatch.delattr(transport, '_ServiceClientStartupOwner')
    with pytest.raises(ServiceRuntimeError, match='STRICT_TRANSPORT_UNAVAILABLE'):
        runtime_for(factory(keys, strict_single_connection=True)).start()
    assert not contexts


@pytest.mark.parametrize('interrupt', [KeyboardInterrupt, SystemExit])
def test_runtime_constructor_interrupt_cleans_owner_and_propagates(setup, monkeypatch, interrupt):
    keys, contexts = setup
    runtime = runtime_for(factory(keys, strict_single_connection=True))
    original = Socket.connect

    def interrupted(sock, endpoint):
        original(sock, endpoint)
        raise interrupt('synthetic constructor interrupt')

    monkeypatch.setattr(Socket, 'connect', interrupted)
    with pytest.raises(interrupt, match='synthetic constructor interrupt'):
        runtime.start()
    sock = contexts[0].sockets[0]
    assert sock.closed and contexts[0].terminated and not runtime._transports
    assert sock.close_threads == [threading.get_ident()]
