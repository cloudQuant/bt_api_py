"""Absolute deadline tests on synthetic authenticated bindings, without network."""
import queue
import threading
import time

import pytest
from bt_api_transport_zmq.service_v2 import ZmqServiceClient

from bt_api_py.service_runtime import ServiceRuntimeRequestNotSentError
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime
from tests.unit.test_iter65_account_bound_runtime import OP, account_runtime, threaded_factory
from tests.unit.test_iter65_bound_runtime import fake_reply


def test_preflight_time_counts_and_exact_absolute_deadline_reaches_transport(monkeypatch):
    _, target, _ = account_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    wrapper = ThreadedServiceClientRuntime(threaded_factory)
    try:
        assert wrapper.start().healthy
        deadline = time.monotonic() + 2
        threading.Event().wait(0.03)  # represents work already done by the caller
        assert wrapper.call_bound(target, OP, {}, timeout_seconds=5, deadline_monotonic=deadline) == {'value': 'synthetic'}
        assert calls[0][2] == deadline
    finally:
        assert wrapper.close().state == 'closed'


def test_preflight_expired_deadline_cannot_send(monkeypatch):
    _, target, _ = account_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    wrapper = ThreadedServiceClientRuntime(threaded_factory)
    try:
        assert wrapper.start().healthy
        deadline = time.monotonic() + 0.01
        threading.Event().wait(0.03)
        with pytest.raises(ThreadedRuntimeError) as error:
            wrapper.call_bound(target, OP, {}, timeout_seconds=5, deadline_monotonic=deadline)
        assert error.value.code == 'TIMEOUT_BEFORE_START'
        assert not calls
    finally:
        assert wrapper.close().state == 'closed'


def test_timeout_and_absolute_deadline_always_choose_earlier(monkeypatch):
    _, target, _ = account_runtime()
    calls = []
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls))
    wrapper = ThreadedServiceClientRuntime(threaded_factory)
    try:
        assert wrapper.start().healthy
        now = time.monotonic()
        early_absolute = now + 1
        assert wrapper.call_bound(target, OP, {}, timeout_seconds=5, deadline_monotonic=early_absolute)
        assert calls[-1][2] == early_absolute
        before = time.monotonic()
        assert wrapper.call_bound(target, OP, {}, timeout_seconds=1, deadline_monotonic=before + 5)
        after = time.monotonic()
        assert before + 1 <= calls[-1][2] <= after + 1
        # Omitting the new argument retains the prior relative-timeout behavior.
        before = time.monotonic()
        assert wrapper.call_bound(target, OP, {}, timeout_seconds=1)
        after = time.monotonic()
        assert before + 1 <= calls[-1][2] <= after + 1
    finally:
        assert wrapper.close().state == 'closed'


@pytest.mark.parametrize('invalid', [True, False, '123', float('nan'), float('inf'), -float('inf'), 10 ** 400])
def test_invalid_absolute_deadline_is_value_error(invalid):
    _, target, _ = account_runtime()
    wrapper = ThreadedServiceClientRuntime(threaded_factory)
    with pytest.raises(ValueError, match='deadline_monotonic'):
        wrapper.call_bound(target, OP, {}, deadline_monotonic=invalid)


def test_clock_domain_must_remain_real_monotonic():
    _, target, _ = account_runtime()
    wrapper = ThreadedServiceClientRuntime(threaded_factory, monotonic_clock=lambda: time.monotonic())
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        wrapper.call_bound(target, OP, {}, deadline_monotonic=time.monotonic() + 5)


@pytest.mark.parametrize('invalid', [True, 0, -1, float('inf'), float('nan'), 10 ** 400])
def test_existing_timeout_validation_is_preserved(invalid):
    _, target, _ = account_runtime()
    wrapper = ThreadedServiceClientRuntime(threaded_factory)
    with pytest.raises(ValueError, match='timeout_seconds'):
        wrapper.call_bound(target, OP, {}, timeout_seconds=invalid, deadline_monotonic=time.monotonic() + 5)


@pytest.mark.parametrize('mutation', ['none', 'deadline', 'clock'])
def test_queued_deadline_expiry_or_tampering_never_sends(monkeypatch, mutation):
    _, target, _ = account_runtime()
    started, release = threading.Event(), threading.Event()
    calls, results = [], queue.Queue()
    def hold():
        started.set()
        assert release.wait(3)
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls, on_call=hold))
    wrapper = ThreadedServiceClientRuntime(threaded_factory)
    def invoke(deadline):
        try:
            results.put(wrapper.call_bound(target, OP, {}, timeout_seconds=3, deadline_monotonic=deadline))
        except Exception as error:
            results.put(error)
    first = threading.Thread(target=invoke, args=(time.monotonic() + 3,))
    second = threading.Thread(target=invoke, args=(time.monotonic() + (0.1 if mutation == 'none' else 2),))
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
            if mutation == 'deadline':
                wrapper._queue[0].deadline += 30
            elif mutation == 'clock':
                wrapper._monotonic_clock = lambda: time.monotonic()
        if mutation == 'none':
            second.join(2)
            assert not second.is_alive()
        release.set()
        first.join(3); second.join(3)
        outcomes = [results.get_nowait(), results.get_nowait()]
        if mutation == 'none':
            assert any(isinstance(v, ThreadedRuntimeError) and v.code == 'TIMEOUT_BEFORE_START' for v in outcomes)
        else:
            assert any(isinstance(v, ServiceRuntimeRequestNotSentError) for v in outcomes)
        assert len(calls) == 1
    finally:
        release.set()
        first.join(3); second.join(3)
        assert wrapper.close().state == 'closed'


def test_absolute_timeout_after_owner_start_is_unknown(monkeypatch):
    _, target, _ = account_runtime()
    started, release = threading.Event(), threading.Event()
    calls = []
    def hold():
        started.set()
        assert release.wait(3)
    monkeypatch.setattr(ZmqServiceClient, 'request', fake_reply(target.expected_peer, calls, on_call=hold))
    wrapper = ThreadedServiceClientRuntime(threaded_factory)
    try:
        assert wrapper.start().healthy
        with pytest.raises(ThreadedRuntimeError) as error:
            wrapper.call_bound(target, OP, {}, timeout_seconds=2, deadline_monotonic=time.monotonic() + 0.08)
        assert error.value.code == 'TIMEOUT_AFTER_START' and started.is_set() and len(calls) == 1
    finally:
        release.set()
        assert wrapper.close().state == 'closed'
