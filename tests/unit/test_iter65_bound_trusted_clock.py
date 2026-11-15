"""Cross-layer trusted monotonic checks with synthetic, non-network transport."""
import time

import pytest
from bt_api_transport_zmq import service_v2 as transport
from bt_api_transport_zmq.service_v2 import ZmqServiceClient
from bt_api_py import service_runtime, service_runtime_thread
from bt_api_py.service_runtime import ServiceCallOutcomeUnknownError, ServiceRuntimeRequestNotSentError
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.unit.test_iter65_account_bound_runtime import OP, account_runtime, threaded_factory
from tests.unit.test_iter65_bound_runtime import fake_reply


def test_all_three_modules_capture_actual_default_clock():
    assert service_runtime._BOUND_MONOTONIC is time.monotonic
    assert service_runtime_thread._BOUND_MONOTONIC is time.monotonic
    assert transport._BOUND_MONOTONIC is time.monotonic


def test_replaced_global_clock_before_construction_is_rejected(monkeypatch):
    real = time.monotonic
    fake = lambda: real() + 1000
    calls = []
    with monkeypatch.context() as patch:
        patch.setattr(time, 'monotonic', fake)
        runtime, target, _ = account_runtime()
        patch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            runtime.call_bound(target, OP, {}, deadline_monotonic=fake() + 5)
        wrapper = ThreadedServiceClientRuntime(threaded_factory, monotonic_clock=fake)
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            wrapper.call_bound(target, OP, {}, deadline_monotonic=fake() + 5)
    assert not calls


@pytest.mark.parametrize('module', [service_runtime, service_runtime_thread, transport])
def test_single_layer_clock_mismatch_prevents_enqueue(monkeypatch, module):
    _, target, _ = account_runtime()
    wrapper = ThreadedServiceClientRuntime(threaded_factory)
    monkeypatch.setattr(module, '_BOUND_MONOTONIC', lambda: 1.0)
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        wrapper.call_bound(target, OP, {}, deadline_monotonic=time.monotonic() + 5)
    assert not wrapper._queue


@pytest.mark.parametrize('missing', ['_BOUND_MONOTONIC', '_BOUND_MONOTONIC_NS'])
def test_older_transport_without_trusted_clock_fails_closed(monkeypatch, missing):
    runtime, target, _ = account_runtime()
    wrapper = ThreadedServiceClientRuntime(threaded_factory)
    monkeypatch.delattr(transport, missing)
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.call_bound(target, OP, {}, deadline_monotonic=time.monotonic() + 5)
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        wrapper.call_bound(target, OP, {}, deadline_monotonic=time.monotonic() + 5)


@pytest.mark.parametrize('change', ['process_clock', 'transport_clock'])
def test_clock_changes_after_fake_transport_dispatch_is_unknown(monkeypatch, change):
    runtime, target, _ = account_runtime()
    real = time.monotonic
    calls = []
    def change_clock():
        if change == 'process_clock':
            monkeypatch.setattr(time, 'monotonic', lambda: real() + 1000)
        else:
            monkeypatch.setattr(transport, '_BOUND_MONOTONIC', lambda: real() + 1000)
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls, on_call=change_clock))
    try:
        with pytest.raises(ServiceCallOutcomeUnknownError):
            runtime.call_bound(target, OP, {}, deadline_monotonic=real() + 5)
        assert len(calls) == 1
    finally:
        monkeypatch.setattr(time, 'monotonic', real)


def test_global_clock_change_while_queued_prevents_second_send(monkeypatch):
    import queue
    import threading

    _, target, _ = account_runtime()
    real = time.monotonic
    started, release = threading.Event(), threading.Event()
    calls, outcomes = [], queue.Queue()
    def hold():
        started.set()
        assert release.wait(3)
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls, on_call=hold))
    wrapper = ThreadedServiceClientRuntime(threaded_factory)
    def invoke():
        try: outcomes.put(wrapper.call_bound(target, OP, {}, timeout_seconds=3))
        except Exception as error: outcomes.put(error)
    first, second = threading.Thread(target=invoke), threading.Thread(target=invoke)
    try:
        assert wrapper.start().healthy
        first.start()
        assert started.wait(2)
        second.start()
        with wrapper._condition:
            limit = real() + 2
            while not wrapper._queue:
                left = limit - real()
                assert left > 0
                wrapper._condition.wait(left)
            monkeypatch.setattr(time, 'monotonic', lambda: real() + 1000)
        release.set()
        first.join(3); second.join(3)
        assert not first.is_alive() and not second.is_alive()
        results = [outcomes.get_nowait(), outcomes.get_nowait()]
        assert any(isinstance(value, ServiceCallOutcomeUnknownError) for value in results)
        assert any(isinstance(value, ServiceRuntimeRequestNotSentError) for value in results)
        assert len(calls) == 1
    finally:
        monkeypatch.setattr(time, 'monotonic', real)
        release.set()
        first.join(3); second.join(3)
        assert wrapper.close().state == 'closed'


@pytest.mark.parametrize('module', [service_runtime, service_runtime_thread, transport])
def test_single_layer_nanosecond_clock_mismatch_prevents_enqueue(monkeypatch, module):
    _, target, _ = account_runtime()
    wrapper = ThreadedServiceClientRuntime(threaded_factory)
    monkeypatch.setattr(module, '_BOUND_MONOTONIC_NS', lambda: 1)
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        wrapper.call_bound(target, OP, {}, deadline_monotonic=time.monotonic() + 5)
    assert not wrapper._queue


def test_replaced_nanosecond_clock_is_rejected_without_send(monkeypatch):
    runtime, target, _ = account_runtime()
    real_ns = time.monotonic_ns
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    try:
        monkeypatch.setattr(time, 'monotonic_ns', lambda: 1)
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            runtime.call_bound(target, OP, {}, deadline_monotonic=time.monotonic() + 5)
        wrapper = ThreadedServiceClientRuntime(threaded_factory)
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            wrapper.call_bound(target, OP, {}, deadline_monotonic=time.monotonic() + 5)
        assert not calls
    finally:
        monkeypatch.setattr(time, 'monotonic_ns', real_ns)


def test_sdk_receipt_time_is_bounded_by_request_and_deadline():
    before_ns = time.monotonic_ns()
    receipt_ns = time.monotonic_ns()
    deadline = time.monotonic() + 2
    assert service_runtime._bound_receipt_time_valid(receipt_ns, before_ns, deadline)
    assert not service_runtime._bound_receipt_time_valid(before_ns - 1, before_ns, deadline)
    assert not service_runtime._bound_receipt_time_valid(receipt_ns, before_ns, time.monotonic() - 2)


@pytest.mark.parametrize('deadline', [0.0, 0.1, 100.000000001, 2**53 * 1.0, 1e300, -0.1])
def test_deadline_conversion_floors_exact_binary_float(deadline):
    numerator, denominator = deadline.as_integer_ratio()
    expected = numerator * 1_000_000_000 // denominator
    assert service_runtime._deadline_ns_floor(deadline) == expected
    assert transport._deadline_ns_floor(deadline) == expected
    assert expected * denominator <= numerator * 1_000_000_000 < (expected + 1) * denominator


@pytest.mark.parametrize('age,expected_receipt', [(0, True), (2_000_000_000, False)])
def test_sdk_only_publishes_bound_receipt_from_current_request(monkeypatch, age, expected_receipt):
    import os
    import threading
    from bt_api_transport_zmq.service_v2 import ServiceReplyReceipt

    runtime, target, adapter = account_runtime()
    calls = []
    reply = fake_reply(target.expected_peer, calls)
    def request(client, envelope, **kwargs):
        response = reply(client, envelope, **kwargs)
        client._last_reply_receipt = ServiceReplyReceipt(
            envelope.header.message_id, response.header.message_id, envelope.header.message_id,
            OP, time.time_ns(), time.monotonic_ns() - age, os.getpid(), threading.get_ident())
        object.__setattr__(client._last_reply_receipt, '_socket_binding', kwargs['socket_binding'])
        return response
    monkeypatch.setattr(ZmqServiceClient, 'request', request)
    payload = dict(registration_id=target.registration_id, service_epoch=target.service_epoch,
                   instance_id=target.instance_id, arguments={})
    result, receipt = runtime._rpc(target.service_id, target.transport_ref, target.scope, OP, payload,
        control=False, timeout=1, include_receive_receipt=True, bound_transport=adapter,
        bound_client=adapter._transport, bound_socket_binding=adapter._transport._socket_binding,
        bound_peer=target.expected_peer, bound_identity=target.identity, deadline_monotonic=time.monotonic() + 2)
    assert result == {'value': 'synthetic'} and (receipt is not None) is expected_receipt


def test_sdk_clock_change_during_reply_validation_marks_unknown_and_retires(monkeypatch):
    runtime, target, adapter = account_runtime()
    client = adapter._transport
    socket = client._socket
    socket.close = lambda *, linger: setattr(socket, 'closed', True)
    client._owned_sockets = [socket]
    client._pending_retirements = []
    client._socket_message_ids = set()
    real_ns = time.monotonic_ns
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    original_validate = service_runtime.validate_reply
    def validate_then_replace(*args, **kwargs):
        original_validate(*args, **kwargs)
        monkeypatch.setattr(time, 'monotonic_ns', lambda: 1)
    monkeypatch.setattr(service_runtime, 'validate_reply', validate_then_replace)
    try:
        with pytest.raises(ServiceCallOutcomeUnknownError):
            runtime.call_bound(target, OP, {}, deadline_monotonic=time.monotonic() + 5)
        assert len(calls) == 1 and socket.closed
        assert client._last_uncertain_message_id == calls[0][0].header.message_id
        assert not client._pending_retirements
    finally:
        monkeypatch.setattr(time, 'monotonic_ns', real_ns)


@pytest.mark.parametrize('change', [
    'valid', 'missing_socket', 'wrong_socket', 'replace_adapter', 'replace_client',
    'replace_socket', 'remove_proof', 'change_epoch', 'inactive', 'expired',
])
def test_bound_receipt_requires_exact_current_registered_transport(monkeypatch, change):
    import os
    import threading
    from dataclasses import replace
    from types import SimpleNamespace
    from bt_api_transport_zmq.service_v2 import RequestNotSentError, ServiceReplyReceipt
    from bt_api_py.service_runtime import _RequestNotSentAdapter

    runtime, target, adapter = account_runtime()
    original_client = adapter._transport
    original_binding = original_client._socket_binding
    calls = []
    reply = fake_reply(target.expected_peer, calls)
    proof_key = (target.service_id, target.transport_ref, target.registration_id, target.service_epoch)
    def request(client, envelope, **kwargs):
        response = reply(client, envelope, **kwargs)
        candidate = ServiceReplyReceipt(
            envelope.header.message_id, response.header.message_id, envelope.header.message_id,
            OP, time.time_ns(), time.monotonic_ns(), os.getpid(), threading.get_ident())
        if change != 'missing_socket':
            object.__setattr__(candidate, '_socket_binding', object() if change == 'wrong_socket' else original_binding)
        client._last_reply_receipt = candidate
        if change in ('replace_adapter', 'replace_client'):
            replacement = object.__new__(ZmqServiceClient)
            replacement.__dict__.update(client.__dict__)
            if change == 'replace_adapter':
                runtime._transports[(target.service_id, target.transport_ref)] = _RequestNotSentAdapter(
                    replacement, RequestNotSentError)
            else:
                adapter._transport = replacement
        elif change == 'replace_socket':
            client._socket = SimpleNamespace(closed=False)
        elif change == 'remove_proof':
            runtime._authenticated_registration_transports.pop(proof_key)
        elif change == 'change_epoch':
            runtime._bindings = [replace(runtime._bindings[0], service_epoch='changed')]
        elif change == 'inactive':
            runtime._bindings = [replace(runtime._bindings[0], active=False)]
        elif change == 'expired':
            runtime._bindings = [replace(runtime._bindings[0], lease_deadline=time.monotonic() - 1)]
        return response
    monkeypatch.setattr(ZmqServiceClient, 'request', request)
    payload = dict(registration_id=target.registration_id, service_epoch=target.service_epoch,
                   instance_id=target.instance_id, arguments={})
    result, receipt = runtime._rpc(target.service_id, target.transport_ref, target.scope, OP, payload,
        control=False, timeout=1, include_receive_receipt=True, bound_transport=adapter,
        bound_client=original_client, bound_socket_binding=original_binding,
        bound_peer=target.expected_peer, bound_identity=target.identity, deadline_monotonic=time.monotonic() + 2)
    assert result == {'value': 'synthetic'}
    assert (receipt is not None) is (change == 'valid')
    if receipt is not None:
        assert receipt is original_client._last_reply_receipt
        assert receipt._socket_binding is original_binding


def test_bound_receipt_request_and_deadline_integer_edges(monkeypatch):
    # Synthetic clock pair for exact nanosecond edges, without changing process time.
    now_ns = 102_000_000_000
    monkeypatch.setattr(service_runtime, '_bound_clock_current', lambda: True)
    monkeypatch.setattr(service_runtime, '_BOUND_MONOTONIC', lambda: 102.0)
    monkeypatch.setattr(service_runtime, '_BOUND_MONOTONIC_NS', lambda: now_ns)
    started_ns = 100_000_000_500
    deadline = 101.0
    end_ns = 101_000_000_000
    assert not service_runtime._bound_receipt_time_valid(started_ns - 1, started_ns, deadline)
    assert service_runtime._bound_receipt_time_valid(started_ns, started_ns, deadline)
    assert service_runtime._bound_receipt_time_valid(end_ns, started_ns, deadline)
    assert not service_runtime._bound_receipt_time_valid(end_ns + 1, started_ns, deadline)


@pytest.mark.parametrize('seconds', [float(2**34), 1e300])
def test_ulp_aware_domain_check_does_not_relax_integer_bounds(monkeypatch, seconds):
    import math

    center_ns = service_runtime._deadline_ns_floor(seconds)
    margin_ns = service_runtime._clock_domain_margin_ns(seconds, seconds)
    assert margin_ns > 1000
    assert margin_ns == transport._clock_domain_margin_ns(seconds, seconds)
    now_ns = [center_ns + margin_ns // 2]
    monkeypatch.setattr(service_runtime, '_bound_clock_current', lambda: True)
    monkeypatch.setattr(service_runtime, '_BOUND_MONOTONIC', lambda: seconds)
    monkeypatch.setattr(service_runtime, '_BOUND_MONOTONIC_NS', lambda: now_ns[0])
    deadline = math.nextafter(seconds, math.inf)
    assert service_runtime._bound_receipt_time_valid(now_ns[0], now_ns[0], deadline)
    assert not service_runtime._bound_receipt_time_valid(now_ns[0], now_ns[0] + 1, deadline)
    assert not service_runtime._bound_receipt_time_valid(now_ns[0], now_ns[0], seconds)
    now_ns[0] = center_ns + margin_ns + 1
    assert not service_runtime._bound_receipt_time_valid(now_ns[0], now_ns[0], deadline)


def test_ulp_domain_margin_handles_max_finite_without_float_multiplication_overflow():
    import math
    import sys

    seconds = sys.float_info.max
    numerator, denominator = math.ulp(seconds).as_integer_ratio()
    expected = max(1000, (numerator * 1_000_000_000 + denominator - 1) // denominator)
    assert service_runtime._clock_domain_margin_ns(seconds, seconds) == expected
    assert transport._clock_domain_margin_ns(seconds, seconds) == expected
