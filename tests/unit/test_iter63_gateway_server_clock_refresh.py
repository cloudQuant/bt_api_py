"""Synthetic Gateway owner/background clock handoff without network I/O."""

import threading
import time
from dataclasses import replace

import pytest

from bt_api_py import clock_verification
from bt_api_py.clock_verification import ClockVerifier
from bt_api_py.gateway_server_clock_refresh import GatewayServerClockRefresher, GatewayServerClockRefreshSettings
from bt_api_py.gateway_dispatch_claim import DispatchClaimError, GatewayDispatchClaimCoordinator


def make_refresher(monkeypatch, query, **overrides):
    monkeypatch.setattr(clock_verification, '_sntp_query', query)
    verifier = ClockVerifier(servers=('trusted',), validity_seconds=5)
    options = dict(allowed_servers=frozenset({'trusted'}), max_error_bound_ns=5_000_000,
                   max_sample_validity_seconds=1, refresh_interval_seconds=0.02)
    options.update(overrides)
    return GatewayServerClockRefresher(verifier, **options), verifier


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.002)
    raise AssertionError('timed out waiting for clock state')


def test_owner_receives_sealed_fresh_sample_and_stop_revokes(monkeypatch):
    source, _ = make_refresher(monkeypatch, lambda *_: (20, 1000))
    source.start()
    sample = wait_for(source.current)
    assert source.is_current(sample)
    assert sample.evidence.server == 'trusted'
    assert sample.evidence.valid_until_monotonic_ns - sample.evidence.measured_monotonic_ns <= 1_000_000_000
    assert not source.is_current(replace(sample))
    failures = []
    def wrong_owner():
        try:
            source.current()
        except RuntimeError:
            failures.append(True)
    thread = threading.Thread(target=wrong_owner)
    thread.start()
    thread.join()
    assert failures == [True]
    assert source.stop(timeout_seconds=2)
    assert source.current() is None and not source.is_current(sample)


def test_untrusted_server_and_query_rejected_before_network(monkeypatch):
    monkeypatch.setattr(clock_verification, '_sntp_query', lambda *_: (0, 0))
    verifier = ClockVerifier(servers=('untrusted',))
    with pytest.raises(ValueError, match='INVALID_GATEWAY_SERVER_CLOCK'):
        GatewayServerClockRefresher(verifier, allowed_servers=frozenset({'trusted'}),
                                    max_error_bound_ns=1, max_sample_validity_seconds=1,
                                    refresh_interval_seconds=1)
    verifier = ClockVerifier(servers=('trusted',), query=lambda *_: (0, 0))
    with pytest.raises(ValueError, match='INVALID_GATEWAY_SERVER_CLOCK'):
        GatewayServerClockRefresher(verifier, allowed_servers=frozenset({'trusted'}),
                                    max_error_bound_ns=1, max_sample_validity_seconds=1,
                                    refresh_interval_seconds=1)


@pytest.mark.parametrize('settings', [
    (True, 1, 1), (-1, 1, 1), (1, float('nan'), 1),
    (1, 3601, 1), (1, 1, 0), (1, 1, float('inf')),
])
def test_untrusted_policy_values_are_rejected(settings):
    with pytest.raises(ValueError, match='INVALID_GATEWAY_SERVER_CLOCK'):
        GatewayServerClockRefreshSettings(*settings)


def test_new_successful_sample_revokes_previous_identity(monkeypatch):
    source, _ = make_refresher(monkeypatch, lambda *_: (0, 100), refresh_interval_seconds=0.05)
    source.start()
    first = wait_for(source.current)
    next_sample = wait_for(lambda: (item if (item := source.current()) is not first else None))
    assert source.is_current(next_sample) and not source.is_current(first)
    assert source.stop(timeout_seconds=2)


def test_failed_refresh_invalidates_prior_sample(monkeypatch):
    calls = []
    def query(*_):
        calls.append(1)
        if len(calls) > 1:
            raise OSError('unavailable')
        return 0, 100
    source, _ = make_refresher(monkeypatch, query, refresh_interval_seconds=0.1)
    source.start()
    sample = wait_for(source.current)
    wait_for(lambda: len(calls) >= 2 and source.current() is None)
    assert not source.is_current(sample)
    assert source.stop(timeout_seconds=2)


def test_sample_expires_and_source_mutation_fails_closed(monkeypatch):
    source, verifier = make_refresher(monkeypatch, lambda *_: (0, 100),
                                      max_sample_validity_seconds=0.03,
                                      refresh_interval_seconds=1)
    source.start()
    sample = wait_for(source.current)
    wait_for(lambda: source.current() is None)
    assert not source.is_current(sample)
    assert source.stop(timeout_seconds=2)
    source.start()
    sample = wait_for(source.current)
    verifier._servers = ('untrusted',)
    assert source.current() is None and not source.is_current(sample)
    assert source.stop(timeout_seconds=2)


def test_stop_during_inflight_query_revokes_and_can_be_retried(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def query(*_):
        calls.append(1)
        if len(calls) > 1:
            entered.set()
            release.wait(2)
        return 0, 100
    source, _ = make_refresher(monkeypatch, query)
    source.start()
    sample = wait_for(source.current)
    assert entered.wait(2)
    assert source.stop(timeout_seconds=0) is False
    assert source.current() is None and not source.is_current(sample)
    release.set()
    assert source.stop(timeout_seconds=2)
    assert source.current() is None


def test_excessive_error_bound_never_publishes(monkeypatch):
    queried = threading.Event()
    def query(*_):
        queried.set()
        return 0, 10_000_000
    source, _ = make_refresher(monkeypatch, query)
    source.start()
    assert queried.wait(2)
    assert source.stop(timeout_seconds=2)
    assert source.current() is None


@pytest.mark.parametrize('jump_ns', [-11_000_000, -2_000_000, 2_000_000, 11_000_000])
def test_sample_wall_jump_is_rejected_or_counted(monkeypatch, jump_ns):
    source, _ = make_refresher(monkeypatch, lambda *_: (0, 100), max_error_bound_ns=4_000_000)
    mono = iter((1_000_000_000, 1_001_000_000, 1_002_000_000))
    wall = iter((1_800_000_000_000_000_000, 1_800_000_000_002_000_000 + jump_ns))
    monkeypatch.setattr(clock_verification.time, 'monotonic_ns', lambda: next(mono))
    monkeypatch.setattr(clock_verification.time, 'time_ns', lambda: next(wall))
    sample = source._capture()
    if abs(jump_ns) > 10_000_000:
        assert sample is None
    else:
        assert sample.evidence.error_bound_ns >= abs(jump_ns)


def test_sample_replacement_and_stop_invalidate_shared_session_domain(monkeypatch):
    coordinator = GatewayDispatchClaimCoordinator._for_test('account', 'generation')
    for domain in ('registry', 'market', 'parent_facts', 'control', 'session_source', 'risk'):
        token = coordinator.begin_change(domain)
        coordinator.finish_change(token, 'NOT_SELECTED' if domain == 'risk' else 'READY')
    before = coordinator.capture()
    source, _ = make_refresher(monkeypatch, lambda *_: (0, 100),
                               claim_coordinator=coordinator, refresh_interval_seconds=1)
    source.start()
    wait_for(source.current)
    with pytest.raises(DispatchClaimError, match='DOMAIN_UNAVAILABLE'):
        coordinator.capture()
    assert coordinator._domains['session_source'][0] > dict((d, v) for d, v, _ in before.versions)['session_source']
    version = coordinator._domains['session_source'][0]
    assert source.stop(timeout_seconds=2)
    assert coordinator._domains['session_source'][0] > version


def test_network_wait_never_holds_claim_lock(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def query(*_):
        entered.set()
        release.wait(2)
        return 0, 100
    coordinator = GatewayDispatchClaimCoordinator._for_test('account', 'generation')
    source, _ = make_refresher(monkeypatch, query, claim_coordinator=coordinator)
    source.start()
    assert entered.wait(2)
    token = coordinator.begin_change('market')
    coordinator.finish_change(token, 'UNAVAILABLE')
    release.set()
    wait_for(source.current)
    assert source.stop(timeout_seconds=2)


def test_sample_change_failure_poison_is_permanent(monkeypatch):
    # Name kept for history: a failed sample publication now ends UNAVAILABLE
    # (epoch moved, claims refused) instead of a permanent poison.
    coordinator = GatewayDispatchClaimCoordinator._for_test('account', 'generation')
    source, _ = make_refresher(monkeypatch, lambda *_: (0, 100), claim_coordinator=coordinator)
    epoch = coordinator._domains['session_source'][0]
    with pytest.raises(RuntimeError, match='synthetic mutation failure'):
        with source._sample_change():
            raise RuntimeError('synthetic mutation failure')
    state = coordinator._domains['session_source']
    assert state[4] is False and state[1] == 'UNAVAILABLE' and state[0] > epoch
    with pytest.raises(DispatchClaimError, match='DOMAIN_UNAVAILABLE'):
        coordinator.capture()


def test_invalid_coordinator_cannot_be_supplied(monkeypatch):
    with pytest.raises(ValueError, match='INVALID_GATEWAY_CLOCK_COORDINATOR'):
        make_refresher(monkeypatch, lambda *_: (0, 100), claim_coordinator=object())


@pytest.mark.parametrize('failure', ['poisoned', 'closed'])
def test_stop_revokes_and_joins_even_if_coordinator_unavailable(monkeypatch, failure):
    coordinator = GatewayDispatchClaimCoordinator._for_test('account', 'generation')
    entered, release = threading.Event(), threading.Event()
    calls = []

    def query(*_):
        calls.append(1)
        if len(calls) > 1:
            entered.set()
            release.wait(2)
        return 0, 100

    source, _ = make_refresher(monkeypatch, query, claim_coordinator=coordinator)
    source.start()
    sample = wait_for(source.current)
    assert entered.wait(2)
    worker = source._worker
    if failure == 'poisoned':
        coordinator.poison_domain('session_source')
    else:
        coordinator.close()
    try:
        assert source.stop(timeout_seconds=0) is False
        assert source.current() is None and not source.is_current(sample)
        with pytest.raises(DispatchClaimError, match='COORDINATOR_CLOSED'):
            coordinator.begin_change('market')
    finally:
        release.set()
    assert source.stop(timeout_seconds=2)
    assert not worker.is_alive()


def test_stop_does_not_hide_join_failure(monkeypatch):
    source, _ = make_refresher(monkeypatch, lambda *_: (0, 100))
    source.start()
    wait_for(source.current)
    with monkeypatch.context() as context:
        context.setattr(source._worker, 'join', lambda *_: (_ for _ in ()).throw(RuntimeError('join failed')))
        with pytest.raises(RuntimeError, match='join failed'):
            source.stop(timeout_seconds=2)
    assert source.current() is None
    assert source.stop(timeout_seconds=2)
