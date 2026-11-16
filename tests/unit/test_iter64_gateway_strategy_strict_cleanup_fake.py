"""Strict Gateway session cleanup using synthetic snapshots, without an owner."""

from dataclasses import replace

import pytest

import bt_api_py.gateway_strategy_client as gateway
from bt_api_py.service_runtime import ServiceClientRuntime
from bt_api_py.service_runtime_thread import (
    BindingSnapshot,
    RuntimeSnapshot,
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
)
from tests.unit.test_iter63_gateway_strategy_client import parts


@pytest.fixture
def harness(monkeypatch):
    roots = {}
    monkeypatch.setattr(gateway, "_RETAINED_STRATEGY_SESSIONS", roots)
    session = gateway.GatewayStrategyClientSession(**parts(), strict_cleanup=True)
    runtime = session.runtime
    running = RuntimeSnapshot(
        "running", True, True, None, (BindingSnapshot(
            "registration", "epoch", True, session.route.mount, session.route.service_id,
            session.route.instance_id, session.route.scope_ref, session.route.scope,
            frozenset({"direct.events.progress"}), True),), 0, 0, session.route.identity,
        session.route.scope.run_id, 0, 0, True,
    )
    closed = replace(running, state="closed", thread_alive=False, healthy=False, bindings=())
    observations = {"snapshot": running, "thread": True}
    calls = []

    def start(**_):
        calls.append("start")
        return running

    def observe(**_):
        calls.append("observe")
        return observations["snapshot"], observations["thread"]

    def close(**_):
        calls.append("close")
        observations["snapshot"] = closed
        return closed

    monkeypatch.setattr(runtime, "start", start)
    monkeypatch.setattr(runtime, "cleanup_observation", observe)
    monkeypatch.setattr(runtime, "close_once_if_state", close)
    return session, runtime, running, closed, observations, calls, roots


def test_strict_configuration_propagates_to_both_runtime_layers(monkeypatch):
    calls = []

    def fake_raw(*args, **kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(gateway, "ServiceClientRuntime", fake_raw)
    monkeypatch.setattr(gateway, "ZmqTransportFactory", lambda _: object())
    session = gateway.GatewayStrategyClientSession(**parts(), strict_cleanup=True)
    assert session.runtime.strict_cleanup_required is True
    session.runtime._factory()
    assert calls[0]["require_unregister_ack"] is True
    assert calls[0]["cleanup_unknown"]() is False
    assert callable(calls[0]["on_cleanup_sticky"])
    assert session.runtime._cleanup_unknown() is False
    assert callable(session.runtime._on_cleanup_sticky)


def test_normal_close_requires_consistent_strict_snapshot_and_is_not_repeated(harness):
    session, _, _, closed, _, calls, roots = harness
    session.start()
    assert session.close() == closed
    assert session.close() == closed
    assert calls.count("close") == 1
    assert not roots


@pytest.mark.parametrize("change", [
    {"strict_unregister_ack": False},
    {"strict_unregister_ack": None},
    {"unregister_uncertainties": 1},
    {"cleanup_failures": 1},
    {"owner_cleanup_uncertainties": 1},
    {"unknown_registration_count": 1},
    {"thread_alive": True},
    {"identity": None},
    {"run_id": "another-run"},
])
def test_unconfirmed_close_retains_original_without_retry(harness, monkeypatch, change):
    session, runtime, _, closed, observations, calls, roots = harness
    session.start()

    def close(**_):
        calls.append("close")
        observations["snapshot"] = replace(closed, **change)
        return observations["snapshot"]

    monkeypatch.setattr(runtime, "close_once_if_state", close)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        session.close()
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        session.close()
    assert calls.count("close") == 1
    assert roots[id(session)] is session and session.runtime is runtime


def test_unstarted_session_closes_without_claiming_an_ack(monkeypatch):
    roots = {}
    monkeypatch.setattr(gateway, "_RETAINED_STRATEGY_SESSIONS", roots)
    session = gateway.GatewayStrategyClientSession(**parts(), strict_cleanup=True)
    snapshot = session.close()
    assert snapshot.state == "closed" and snapshot.strict_unregister_ack is None
    assert not roots
    assert session.close() == snapshot


def test_interrupted_start_does_not_reclose_a_running_owner(harness, monkeypatch):
    session, runtime, _, _, _, calls, roots = harness

    def interrupted(**_):
        calls.append("start")
        raise KeyboardInterrupt

    monkeypatch.setattr(runtime, "start", interrupted)
    with pytest.raises(KeyboardInterrupt):
        session.start()
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        session.close()
    assert "close" not in calls and roots[id(session)] is session


def test_interrupted_close_is_not_repeated(harness, monkeypatch):
    session, runtime, _, _, _, calls, roots = harness
    session.start()

    def interrupted(**_):
        calls.append("close")
        raise KeyboardInterrupt

    monkeypatch.setattr(runtime, "close_once_if_state", interrupted)
    with pytest.raises(KeyboardInterrupt):
        session.close()
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        session.close()
    assert calls.count("close") == 1 and roots[id(session)] is session


def test_closed_session_cannot_restart(harness):
    session, _, _, _, _, calls, _ = harness
    session.start()
    session.close()
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_POLICY_UNPROVEN"):
        session.start()
    assert calls.count("start") == 1


def test_changed_runtime_is_not_cleaned_or_substituted(harness):
    session, runtime, _, _, _, calls, roots = harness
    session.start()
    session._runtime = object()
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        session.close()
    assert roots[id(session)] is session and session._original_runtime is runtime
    assert calls.count("close") == 0


def test_unknown_session_does_not_fence_a_separate_same_identity_session(harness, monkeypatch):
    session, runtime, running, _, _, calls, roots = harness
    session.start()

    def interrupted(**_):
        calls.append("close")
        raise KeyboardInterrupt

    monkeypatch.setattr(runtime, "close_once_if_state", interrupted)
    with pytest.raises(KeyboardInterrupt):
        session.close()

    other = gateway.GatewayStrategyClientSession(**parts(), strict_cleanup=True)
    assert other.route.identity == session.route.identity
    assert other.route.scope == session.route.scope
    # These synthetic starts use no owner, process, network or real account.
    monkeypatch.setattr(other.runtime, "start", lambda **_: running)
    assert other.start() == running
    assert roots[id(session)] is session and roots[id(other)] is other


def test_invalid_timeout_does_not_consume_a_cleanup_attempt(harness):
    session, _, _, closed, _, calls, _ = harness
    session.start()
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_NOT_STARTED"):
        session.close(timeout_seconds=0)
    assert calls.count("close") == 0
    assert session.close(timeout_seconds=1) == closed
    assert calls.count("close") == 1


def test_first_observation_timeout_does_not_start_cleanup_and_is_retryable(harness, monkeypatch):
    session, runtime, running, closed, observations, calls, roots = harness
    session.start()
    first = True

    def observe(**_):
        nonlocal first
        calls.append("observe")
        if first:
            first = False
            raise ThreadedRuntimeError("CLEANUP_SNAPSHOT_TIMEOUT")
        return observations["snapshot"], True

    monkeypatch.setattr(runtime, "cleanup_observation", observe)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_NOT_STARTED"):
        session.close()
    assert observations["snapshot"] == running
    assert calls.count("close") == 0 and roots[id(session)] is session
    assert session.close() == closed and calls.count("close") == 1


def test_close_before_start_timeout_is_retryable_once(harness, monkeypatch):
    session, runtime, _, closed, observations, calls, roots = harness
    session.start()
    first = True

    def close(**_):
        nonlocal first
        calls.append("close_call")
        if first:
            first = False
            raise ThreadedRuntimeError("CLOSE_TIMEOUT_BEFORE_START")
        calls.append("transition")
        observations["snapshot"] = closed
        return closed

    monkeypatch.setattr(runtime, "close_once_if_state", close)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_NOT_STARTED"):
        session.close()
    assert calls.count("transition") == 0 and roots[id(session)] is session
    assert session.close() == closed
    assert calls.count("close_call") == 2 and calls.count("transition") == 1


def test_actual_close_once_lock_timeout_has_no_transition_and_is_retryable(monkeypatch):
    roots = {}
    monkeypatch.setattr(gateway, "_RETAINED_STRATEGY_SESSIONS", roots)
    session = gateway.GatewayStrategyClientSession(**parts(), strict_cleanup=True)
    runtime = session.runtime
    new = RuntimeSnapshot("new", False, False, None, (), 0, 0, None, None, 0, 0, None)

    class UnavailableCondition:
        def acquire(self, **_):
            return False

    original_condition = runtime._condition
    runtime._condition = UnavailableCondition()
    monkeypatch.setattr(runtime, "cleanup_observation", lambda **_: (
        replace(new, state=runtime._state), False))
    assert type(runtime).close_once_if_state is ThreadedServiceClientRuntime.close_once_if_state
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_NOT_STARTED"):
        session.close()
    assert runtime._state == "new" and runtime._cleanup_retry == 0
    assert roots[id(session)] is session
    runtime._condition = original_condition
    assert session.close().state == "closed"
    assert runtime._cleanup_retry == 0 and not roots


@pytest.mark.parametrize("code", ["CLOSE_TIMEOUT_WORKER_ALIVE", "CLEANUP_STATE_UNCERTAIN"])
def test_close_after_possible_transition_is_not_retried(harness, monkeypatch, code):
    session, runtime, _, _, _, calls, roots = harness
    session.start()

    def close(**_):
        calls.append("close")
        raise ThreadedRuntimeError(code)

    monkeypatch.setattr(runtime, "close_once_if_state", close)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        session.close()
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        session.close()
    assert calls.count("close") == 1 and roots[id(session)] is session


def test_observation_timeout_after_close_was_issued_stays_unknown(harness, monkeypatch):
    session, runtime, _, closed, observations, calls, roots = harness
    session.start()
    observed = 0

    def observe(**_):
        nonlocal observed
        observed += 1
        if observed == 2:
            raise ThreadedRuntimeError("CLEANUP_SNAPSHOT_TIMEOUT")
        return observations["snapshot"], True

    monkeypatch.setattr(runtime, "cleanup_observation", observe)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        session.close()
    assert observations["snapshot"] == closed
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        session.close()
    assert calls.count("close") == 1 and roots[id(session)] is session


def test_preclose_interrupt_remains_unknown(harness, monkeypatch):
    session, runtime, _, _, _, calls, roots = harness
    session.start()
    monkeypatch.setattr(runtime, "cleanup_observation", lambda **_: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        session.close()
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        session.close()
    assert calls.count("close") == 0 and roots[id(session)] is session


def test_reentrant_lifecycle_cannot_start_during_retryable_observation(harness, monkeypatch):
    session, runtime, _, closed, observations, calls, _ = harness
    session.start()
    first = True

    def observe(**_):
        nonlocal first
        if first:
            first = False
            with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
                session.start()
            with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
                session.close()
            raise ThreadedRuntimeError("CLEANUP_SNAPSHOT_TIMEOUT")
        return observations["snapshot"], True

    monkeypatch.setattr(runtime, "cleanup_observation", observe)
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_NOT_STARTED"):
        session.close()
    assert session.close() == closed
    assert calls.count("start") == 1 and calls.count("close") == 1


def test_interrupted_start_allows_only_observation_of_an_already_stopped_owner(harness, monkeypatch):
    session, runtime, _, closed, observations, calls, roots = harness

    def interrupted(**_):
        calls.append("start")
        raise KeyboardInterrupt

    monkeypatch.setattr(runtime, "start", interrupted)
    with pytest.raises(KeyboardInterrupt):
        session.start()
    observations["snapshot"] = closed
    assert session.close() == closed
    assert "close" not in calls and not roots


def test_interrupted_start_can_close_proven_never_started_runtime(harness, monkeypatch):
    session, runtime, _, _, observations, calls, roots = harness
    new = RuntimeSnapshot("new", False, False, None, (), 0, 0, None, None, 0, 0, None)
    closed = replace(new, state="closed")
    observations.update(snapshot=new, thread=False)

    def interrupted(**_):
        raise KeyboardInterrupt

    def close(*, expected_state, **_):
        assert expected_state == "new"
        calls.append("close")
        observations["snapshot"] = closed
        return closed

    monkeypatch.setattr(runtime, "start", interrupted)
    monkeypatch.setattr(runtime, "close_once_if_state", close)
    with pytest.raises(KeyboardInterrupt):
        session.start()
    assert session.close() == closed
    assert calls.count("close") == 1 and not roots


@pytest.mark.parametrize("reply", [
    {"unregistered": False, "registration_id": "original"},
    {"unregistered": True, "registration_id": "different"},
    {},
])
def test_actual_raw_unregister_rejects_missing_or_wrong_ack(monkeypatch, reply):
    raw = object.__new__(ServiceClientRuntime)
    raw._cleanup_unknown = None
    raw._require_unregister_ack = True
    raw._unregister_uncertainties = {}
    raw._cleanup_failures = 0
    monkeypatch.setattr(raw, "_rpc", lambda *_, **__: reply)
    assert raw._unregister("gateway", "transport", object(), "original", "epoch", 1) is False
    assert raw._unregister_uncertainties == {"original": None}
    assert raw._cleanup_failures == 1


def test_actual_raw_unregister_accepts_matching_ack(monkeypatch):
    raw = object.__new__(ServiceClientRuntime)
    raw._cleanup_unknown = None
    raw._require_unregister_ack = True
    raw._unregister_uncertainties = {"original": None}
    raw._cleanup_failures = 0
    monkeypatch.setattr(raw, "_rpc", lambda *_, **__: {
        "unregistered": True, "registration_id": "original"})
    assert raw._unregister("gateway", "transport", object(), "original", "epoch", 1) is True
    assert raw._unregister_uncertainties == {}


@pytest.mark.parametrize("name", ["account_settings", "market_settings", "instrument_settings"])
def test_strict_rejects_uncomposed_optional_owner(name):
    with pytest.raises(ValueError, match="STRICT_OWNER_CLEANUP_UNAVAILABLE"):
        gateway.GatewayStrategyClientSession(**parts(), strict_cleanup=True, **{name: object()})
