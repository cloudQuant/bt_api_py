"""Synthetic account-bound calls; no network or account authority is exercised."""
from dataclasses import replace
import queue
import threading
import time

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_transport_zmq.service_v2 import ZmqServiceClient

from bt_api_py.service_runtime import (
    BoundAccountServiceTarget, PORT_OPERATIONS, ServiceClientRuntime,
    ServiceRuntimeError, ServiceRuntimeRequestNotSentError,
)
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime
from tests.unit.test_iter65_bound_runtime import fake_reply, make_runtime

OP = 'direct.parent.acceptance_order'


def account_runtime():
    runtime, old, adapter, binding = make_runtime()
    scope = WireScope('synthetic-domain', account_ref='synthetic-account')
    runtime._bindings = [replace(binding, scope=scope, operations=frozenset({OP}))]
    target = BoundAccountServiceTarget(
        old.mount, old.port, old.service_id, old.instance_id, old.scope_ref, scope,
        old.identity, old.run_id, old.transport_ref, old.security_profile_ref,
        old.endpoint, old.expected_peer, old.server_key_fingerprint,
        old.registration_id, old.service_epoch,
    )
    return runtime, target, adapter


def test_account_call_sends_only_account_scope(monkeypatch):
    runtime, target, _ = account_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    assert runtime.call_bound(target, OP, {'sample': 1}, deadline_monotonic=time.monotonic() + 5) == {'value': 'synthetic'}
    request = calls[0][0]
    assert request.header.scope == target.scope
    assert request.header.scope.run_id is request.header.scope.strategy_ref is None
    assert request.payload['arguments'] == {'sample': 1}
    assert 'runtime_run_id' not in request.payload
    assert target.runtime_run_id not in repr(request)


@pytest.mark.parametrize('field,wrong', [
    ('service_id', 'other'), ('instance_id', 'other'), ('scope_ref', 'other'),
    ('transport_ref', 'other'), ('security_profile_ref', 'other'),
    ('endpoint', 'tcp://127.0.0.1:2'), ('registration_id', 'other'),
    ('service_epoch', 'other'), ('server_key_fingerprint', '0' * 64),
    ('runtime_run_id', 'other'),
])
def test_retarget_rejected_without_sending(monkeypatch, field, wrong):
    runtime, target, _ = account_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.call_bound(replace(target, **{field: wrong}), OP, {}, deadline_monotonic=time.monotonic() + 5)
    assert calls == []


@pytest.mark.parametrize('changed', ['scope', 'identity', 'peer', 'socket', 'proof', 'duplicate', 'operation', 'runtime_run', 'pid', 'thread', 'deadline'])
def test_authentication_changes_fail_before_send(monkeypatch, changed):
    runtime, target, adapter = account_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    deadline = time.monotonic() + 5
    if changed == 'scope': target = replace(target, scope=replace(target.scope, account_ref='other'))
    elif changed == 'identity': target = replace(target, identity=replace(target.identity, actor_ref='other'))
    elif changed == 'peer': target = replace(target, expected_peer=replace(target.expected_peer, actor_ref='other'))
    elif changed == 'socket': adapter._transport._socket.closed = True
    elif changed == 'proof': runtime._authenticated_registration_transports.clear()
    elif changed == 'duplicate': runtime._bindings.append(runtime._bindings[0])
    elif changed == 'operation': runtime._bindings = [replace(runtime._bindings[0], operations=frozenset())]
    elif changed == 'runtime_run': runtime.run_id = 'other'
    elif changed == 'pid': runtime._factory._owner_pid = -1
    elif changed == 'thread': runtime._owner_thread_id = -1
    else: deadline = time.monotonic() - 1
    with pytest.raises(ServiceRuntimeError):
        runtime.call_bound(target, OP, {}, deadline_monotonic=deadline)
    assert calls == []


@pytest.mark.parametrize('scope', [
    WireScope('domain'), WireScope('domain', account_refs=('a',)),
    WireScope('domain', account_refs=('a', 'b')),
    WireScope('domain', account_ref='a', strategy_ref='strategy', run_id='run'),
    WireScope('domain', account_ref='a', manual_book_ref='manual'),
    WireScope('domain', account_ref='a', portfolio_ref='portfolio'),
    WireScope('domain', account_refs=('a', 'b'), portfolio_ref='p', group_id='g'),
    WireScope('domain', account_ref='a', portfolio_ref='p', group_id='g', leg_id='l'),
])
def test_account_target_rejects_non_account_scope(scope):
    _, target, _ = account_runtime()
    with pytest.raises(ValueError): replace(target, scope=scope)


@pytest.mark.parametrize('field,value', [
    ('identity', WireIdentity(WireRole.SERVICE, 'p', 'a', 'delegate')),
    ('identity', WireIdentity(WireRole.STRATEGY_CLIENT, 'p', 'a')),
    ('expected_peer', WireIdentity(WireRole.SERVICE, 'p', 'a', 'delegate')),
    ('expected_peer', WireIdentity(WireRole.STRATEGY_CLIENT, 'p', 'a')),
    ('mount', 'other'), ('port', 'service.account_read'),
])
def test_account_target_rejects_identity_or_port(field, value):
    _, target, _ = account_runtime()
    with pytest.raises(ValueError): replace(target, **{field: value})


def test_account_target_cannot_invoke_any_existing_operation(monkeypatch):
    runtime, target, _ = account_runtime()
    runtime._bindings = [replace(runtime._bindings[0], operations=PORT_OPERATIONS[target.port])]
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    all_operations = frozenset().union(*PORT_OPERATIONS.values())
    for operation in all_operations - {OP}:
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            runtime.call_bound(target, operation, {}, deadline_monotonic=time.monotonic() + 5)
    assert not calls


def test_old_bound_target_does_not_gain_account_operation(monkeypatch):
    runtime, target, _, binding = make_runtime()
    runtime._bindings = [replace(binding, operations=frozenset({OP}))]
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.call_bound(target, OP, {}, deadline_monotonic=time.monotonic() + 5)
    assert not calls


@pytest.mark.parametrize('requested,remote,expected', [
    (None, {'direct.reserve', OP}, ['direct.reserve']),
    (None, {'direct.reserve'}, ['direct.reserve']),
    (frozenset({OP}), {'direct.reserve', OP}, [OP]),
])
def test_discovery_requires_explicit_opt_in(requested, remote, expected):
    runtime, target, _ = account_runtime()
    runtime._requested_operations = {} if requested is None else {target.mount: requested}
    runtime.client_session_id = 'session'
    captured = []
    def rpc(*args, **kwargs):
        captured.append(args[4]['operations'])
        raise RuntimeError('stop before registration')
    runtime._rpc = rpc
    binding = dict(mount=target.mount, service_id=target.service_id, instance_id=target.instance_id,
                   scope_ref=target.scope_ref, transport_ref=target.transport_ref)
    caps = [dict(instance_id=target.instance_id, port=target.port, operations=list(remote))]
    with pytest.raises(RuntimeError, match='stop before registration'):
        runtime._register_port(binding, target.scope, target.port, caps, timeout=1, required=True, heartbeat_interval_seconds=1)
    assert captured == [expected]


def test_missing_explicit_capability_does_not_register():
    runtime, target, _ = account_runtime()
    runtime._requested_operations = {target.mount: frozenset({OP})}
    binding = dict(mount=target.mount, service_id=target.service_id, instance_id=target.instance_id,
                   scope_ref=target.scope_ref, transport_ref=target.transport_ref)
    caps = [dict(instance_id=target.instance_id, port=target.port, operations=['direct.reserve'])]
    with pytest.raises(ServiceRuntimeError, match='CAPABILITY_UNAVAILABLE'):
        runtime._register_port(binding, target.scope, target.port, caps, timeout=1, required=True, heartbeat_interval_seconds=1)


def threaded_factory():
    runtime, _, _ = account_runtime()
    runtime.start = lambda: None
    runtime.maintain = lambda: None
    runtime.next_maintenance_delay = lambda: 1.0
    runtime.close = lambda: None
    return runtime


def test_threaded_account_call_and_exact_type(monkeypatch):
    _, target, _ = account_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    wrapper = ThreadedServiceClientRuntime(threaded_factory)
    class Derived(BoundAccountServiceTarget): pass
    with pytest.raises(ThreadedRuntimeError):
        wrapper.call_bound(Derived(**target.__dict__), OP, {})
    try:
        assert wrapper.start().healthy
        assert wrapper.call_bound(target, OP, {}) == {'value': 'synthetic'}
        assert calls[0][0].header.scope.run_id is None
        with pytest.raises(ThreadedRuntimeError): wrapper.call_bound(target, 'direct.parent.query', {})
        assert len(calls) == 1
    finally:
        assert wrapper.close().state == 'closed'


def test_queued_target_is_revalidated_on_owner(monkeypatch):
    _, target, _ = account_runtime()
    started, release = threading.Event(), threading.Event()
    calls, results = [], queue.Queue()
    def hold():
        started.set()
        assert release.wait(3)
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls, on_call=hold))
    wrapper = ThreadedServiceClientRuntime(threaded_factory)
    def invoke():
        try: results.put(wrapper.call_bound(target, OP, {}, timeout_seconds=3))
        except Exception as error: results.put(error)
    first, second = threading.Thread(target=invoke), threading.Thread(target=invoke)
    try:
        assert wrapper.start().healthy
        first.start()
        assert started.wait(2)
        second.start()
        with wrapper._condition:
            limit = time.monotonic() + 2
            while not wrapper._queue:
                left = limit - time.monotonic()
                assert left > 0
                wrapper._condition.wait(left)
            wrapper._queue[0].operation = 'direct.parent.query'
        release.set()
        first.join(3); second.join(3)
        assert not first.is_alive() and not second.is_alive()
        outcomes = [results.get_nowait(), results.get_nowait()]
        assert sum(isinstance(result, ServiceRuntimeRequestNotSentError) for result in outcomes) == 1
        assert len(calls) == 1
    finally:
        release.set()
        first.join(3); second.join(3)
        assert wrapper.close().state == 'closed'


def two_account_runtime():
    runtime, first, adapter = account_runtime()
    second_scope = replace(first.scope, account_ref='synthetic-account-b')
    second = replace(first, scope=second_scope, scope_ref='scope-b', registration_id='registration-b')
    original = runtime._bindings[0]
    runtime._bindings.append(replace(original, scope=second_scope, scope_ref=second.scope_ref,
                                     registration_id=second.registration_id))
    old_key = (first.service_id, first.transport_ref, first.registration_id, first.service_epoch)
    new_key = (second.service_id, second.transport_ref, second.registration_id, second.service_epoch)
    runtime._authenticated_registration_peers[new_key] = runtime._authenticated_registration_peers[old_key]
    runtime._authenticated_registration_transports[new_key] = runtime._authenticated_registration_transports[old_key]
    return runtime, first, second, adapter


def test_ordinary_calls_refuse_account_operation_before_send(monkeypatch):
    runtime, target, _ = account_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.call(target.mount, OP, {}, account_ref=target.scope.account_ref)
    wrapper = ThreadedServiceClientRuntime(threaded_factory)
    try:
        assert wrapper.start().healthy
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            wrapper.call(target.mount, OP, {}, account_ref=target.scope.account_ref)
        assert calls == []
        assert not wrapper._queue
    finally:
        assert wrapper.close().state == 'closed'


@pytest.mark.parametrize('mutation', ['target', 'arguments', 'deadline'])
def test_queued_call_rejects_replacement_or_mutation_even_with_two_valid_accounts(monkeypatch, mutation):
    runtime, first_target, second_target, _ = two_account_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(first_target.expected_peer, calls))
    # Both targets have distinct, valid authenticated registrations.
    for target in (first_target, second_target):
        assert runtime.call_bound(target, OP, {}, deadline_monotonic=time.monotonic() + 5) == {'value': 'synthetic'}
    assert [call[0].header.scope.account_ref for call in calls] == ['synthetic-account', 'synthetic-account-b']
    calls.clear()
    started, release = threading.Event(), threading.Event()
    def hold():
        started.set()
        assert release.wait(3)
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(first_target.expected_peer, calls, on_call=hold))
    def factory():
        owner, _, _, _ = two_account_runtime()
        owner.start = lambda: None
        owner.maintain = lambda: None
        owner.next_maintenance_delay = lambda: 1.0
        owner.close = lambda: None
        return owner
    wrapper = ThreadedServiceClientRuntime(factory)
    results = queue.Queue()
    def invoke():
        try: results.put(wrapper.call_bound(first_target, OP, {'nested': [1]}, timeout_seconds=3))
        except Exception as error: results.put(error)
    first, second = threading.Thread(target=invoke), threading.Thread(target=invoke)
    try:
        assert wrapper.start().healthy
        first.start()
        assert started.wait(2)
        second.start()
        with wrapper._condition:
            limit = time.monotonic() + 2
            while not wrapper._queue:
                left = limit - time.monotonic()
                assert left > 0
                wrapper._condition.wait(left)
            queued = wrapper._queue[0]
            if mutation == 'target': queued.target = second_target
            elif mutation == 'arguments': queued.arguments['nested'][0] = True
            else: queued.deadline += 5
        release.set()
        first.join(3); second.join(3)
        assert not first.is_alive() and not second.is_alive()
        outcomes = [results.get_nowait(), results.get_nowait()]
        assert sum(isinstance(result, ServiceRuntimeRequestNotSentError) for result in outcomes) == 1
        assert len(calls) == 1 and calls[0][0].header.scope.account_ref == 'synthetic-account'
    finally:
        release.set()
        first.join(3); second.join(3)
        assert wrapper.close().state == 'closed'


def test_original_arguments_are_copied_before_enqueue(monkeypatch):
    _, target, _ = account_runtime()
    started, release = threading.Event(), threading.Event()
    calls, results = [], queue.Queue()
    def hold():
        if not started.is_set():
            started.set()
            assert release.wait(3)
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls, on_call=hold))
    wrapper = ThreadedServiceClientRuntime(threaded_factory)
    original = {'nested': [1]}
    def invoke(arguments):
        try: results.put(wrapper.call_bound(target, OP, arguments, timeout_seconds=3))
        except Exception as error: results.put(error)
    first = threading.Thread(target=invoke, args=({},))
    second = threading.Thread(target=invoke, args=(original,))
    try:
        assert wrapper.start().healthy
        first.start()
        assert started.wait(2)
        second.start()
        with wrapper._condition:
            limit = time.monotonic() + 2
            while not wrapper._queue:
                left = limit - time.monotonic()
                assert left > 0
                wrapper._condition.wait(left)
            original['nested'][0] = 2
        release.set()
        first.join(3); second.join(3)
        assert not first.is_alive() and not second.is_alive()
        assert results.get_nowait() == results.get_nowait() == {'value': 'synthetic'}
        assert calls[1][0].payload['arguments']['nested'] == (1,)
    finally:
        release.set()
        first.join(3); second.join(3)
        assert wrapper.close().state == 'closed'
