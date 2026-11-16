"""Synthetic runtime + temporary SQLite worker tests; no live Monitor or Gateway."""

import hashlib
import json
import os
import threading
import time
from dataclasses import replace

import pytest
from bt_api_gateway.recovery_observation_clock import RecoveryObservationQualification

from bt_api_py.gateway_recovery_monitor_worker import (
    GatewayRecoveryMonitorSettings,
    GatewayRecoveryMonitorWorker,
)
from bt_api_py.gateway_recovery_observation import GatewayRecoveryObservationRecorder
from bt_api_py.gateway_recovery_qualified_capture import GatewayQualifiedRecoveryPage
from bt_api_py.service_runtime import ServiceClientRuntime
from tests.unit.test_gateway_recovery_monitor_relay import Remote
from tests.unit.test_gateway_recovery_observation import (
    owner_for,
    page,
)
from tests.unit.test_gateway_recovery_observation import (
    settings as recorder_settings,
)
from tests.unit.test_service_monitor_outbox import (
    _OUTBOX_SCOPE,
    _PEER,
    _PRODUCER,
    _SCOPE,
    _SCOPE_REF,
    _config,
    _RegistrationIds,
    _RuntimeRpcLeaf,
)


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for synthetic worker")


def qualified_page(after, _limit):
    value = page(after)
    return GatewayQualifiedRecoveryPage(
        value,
        tuple(
            RecoveryObservationQualification(item, "UNKNOWN", "SOURCE_UNKNOWN")
            for item in value.observations
        ),
    )


def factory_for(remote, *, decorate=None):
    def expected_event_id(source_event_id):
        scope = dict(vars(_SCOPE))
        scope["account_refs"] = list(scope["account_refs"])
        identity = {
            "identity": {
                "role": _PRODUCER.role.value,
                "principal_ref": _PRODUCER.principal_ref,
                "actor_ref": _PRODUCER.actor_ref,
                "delegate_ref": _PRODUCER.delegate_ref,
            },
            "scope": scope,
            "outbox_scope": _OUTBOX_SCOPE,
            "binding_ref": "binding",
            "source_ref": "source",
            "source_event_id": source_event_id,
        }
        encoded = json.dumps(
            identity, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        return "service-event-v2:" + hashlib.sha256(encoded).hexdigest()

    remote.expected_event_id = expected_event_id
    created, transports, thread_ids = [], [], []
    registrations = _RegistrationIds()

    def factory(*, require_unregister_ack):
        thread_ids.append(threading.get_ident())

        def transport_factory(*_args):
            transport = _RuntimeRpcLeaf(remote, registrations)
            transports.append(transport)
            if decorate is not None:
                decorate("transport", transport)
            return transport

        runtime = ServiceClientRuntime(
            _config(),
            identity=_PRODUCER,
            scope_resolver=lambda _: _SCOPE,
            peer_resolver=lambda _: _PEER,
            transport_factory=transport_factory,
            run_id=_SCOPE.run_id,
            requested_operations={"upstream.telemetry": frozenset({"events.append"})},
            require_unregister_ack=require_unregister_ack,
        )
        runtime.authenticated_peer_for_binding = lambda binding: (_PEER, "f" * 64)
        # Synthetic transport cannot provide ZMQ receipt lineage. These two
        # proof hooks are explicit local test substitutes, not authentication.
        original_start = runtime.start
        runtime._has_current_registration_observation = lambda b: (
            id(b) in runtime._registration_lineage and runtime._registration_lineage[id(b)][0] is b
        )

        def start_with_fake_lineage():
            result = original_start()
            for item in runtime.bindings:
                runtime._registration_lineage[id(item)] = (
                    item,
                    tuple(vars(item).items()),
                    *tuple(object() for _ in range(7)),
                )
            return result

        runtime.start = start_with_fake_lineage
        if decorate is not None:
            decorate("runtime", runtime)
        created.append(runtime)
        return runtime

    return factory, created, transports, thread_ids


def worker_settings(tmp_path, factory, **overrides):
    values = {
        "runtime_factory": factory,
        "expected_peer": _PEER,
        "scope_ref": _SCOPE_REF,
        "outbox_scope": _OUTBOX_SCOPE,
        "binding_ref": "binding",
        "source_ref": "source",
        "journal_path": tmp_path / "recovery.sqlite",
        "journal_scope_ref": "account",
        "checkpoint_path": tmp_path / "relay.sqlite",
        "poll_interval_seconds": 0.02,
        "start_timeout_seconds": 2,
        "close_timeout_seconds": 0.3,
        "max_records": 8,
        "max_bytes": 65536,
    }
    values.update(overrides)
    return GatewayRecoveryMonitorSettings(**values)


@pytest.fixture
def recorder(tmp_path):
    settings = recorder_settings(
        tmp_path,
        qualification_mode="local_clock_v1",
        clock_max_interval_ns=1000000000,
        close_timeout_seconds=0.5,
    )
    owner, harness = owner_for(tmp_path)
    recorder = GatewayRecoveryObservationRecorder(owner, qualified_page, settings)
    try:
        recorder.start()
    except BaseException:
        owner.close(2)
        raise
    wait_for(lambda: recorder.status()["state"] == "ready")
    yield recorder, owner, harness
    assert recorder.close()
    assert owner.close(2)


def test_borrow_original_after_close_and_concurrent_drain_stop_marker(tmp_path, recorder):
    source, owner, _ = recorder
    journal = source.borrow_journal()
    assert source.borrow_journal() is journal and journal.schema_version == 2
    remote = Remote()
    factory, runtimes, transports, thread_ids = factory_for(remote)
    worker = GatewayRecoveryMonitorWorker(source, worker_settings(tmp_path, factory))
    try:
        worker.start()
        wait_for(lambda: worker.status()["cursor"] is not None and worker.status()["cursor"] >= 2)
        assert source.close()
        assert source.borrow_journal() is journal
        wait_for(
            lambda: any(call["data"]["event"]["kind"] == "capture_stopped" for call in remote.calls)
        )
        status = worker.status()
        assert status["resource_state"] == "ACTIVE" and status["pending_count"] == 0
        assert len(runtimes) == 1 and thread_ids[0] != threading.get_ident()
        assert (
            owner.submit_service_call(lambda: "owner-active").result(timeout_seconds=1)
            == "owner-active"
        )
    finally:
        assert worker.close()
    assert worker.status()["resource_state"] == "COMPLETE"
    assert transports[0].closed and runtimes[0].state == "closed"


def test_slow_monitor_does_not_block_recorder_or_owner_and_close_retains(tmp_path, recorder):
    source, owner, _ = recorder
    entered, release = threading.Event(), threading.Event()

    class SlowRemote(Remote):
        def append(self, arguments):
            entered.set()
            assert release.wait(3)
            return super().append(arguments)

    factory, runtimes, _, _ = factory_for(SlowRemote())
    worker = GatewayRecoveryMonitorWorker(source, worker_settings(tmp_path, factory))
    try:
        worker.start()
        assert entered.wait(2)
        assert owner.submit_service_call(lambda: 42).result(timeout_seconds=1) == 42
        assert source.close()
        assert source.borrow_journal().read_after(0, limit=10).records[-1].kind == "capture_stopped"
        assert worker.close(timeout_seconds=0.02) is False
        assert worker.status()["resource_state"] == "UNKNOWN"
        assert len(runtimes) == 1 and worker._runtime is runtimes[0] and worker._thread.is_alive()
    finally:
        release.set()
        assert worker.close(timeout_seconds=2)


def test_ack_loss_pending_survives_close_and_replays_same_envelope(tmp_path, recorder):
    source, _, _ = recorder
    remote = Remote()

    def decorate(kind, obj):
        if kind == "transport":
            obj.fail_after_append = True

    factory, _, _, _ = factory_for(remote, decorate=decorate)
    worker = GatewayRecoveryMonitorWorker(
        source, worker_settings(tmp_path, factory, poll_interval_seconds=1)
    )
    try:
        worker.start()
        wait_for(lambda: worker.status()["last_relay_state"] == "SINK_UNAVAILABLE")
        assert worker.status()["pending_count"] == 1 and worker.status()["cursor"] == 0
        original = dict(remote.calls[0])
    finally:
        assert worker.close()
    assert worker.status()["pending_count"] == 1 and worker.status()["cursor"] == 0
    factory2, _, _, _ = factory_for(remote)
    again = GatewayRecoveryMonitorWorker(source, worker_settings(tmp_path, factory2))
    try:
        again.start()
        wait_for(lambda: len(remote.calls) >= 2)
        assert remote.calls[1] == original
        wait_for(lambda: again.status()["cursor"] >= 2)
    finally:
        assert again.close()


def test_close_failure_waits_for_explicit_retry_on_same_owner(tmp_path, recorder):
    source, _, _ = recorder
    close_gate = threading.Event()
    close_threads = []

    def decorate(kind, runtime):
        if kind != "runtime":
            return
        original = runtime.close

        def close():
            close_threads.append(threading.get_ident())
            if not close_gate.is_set():
                raise RuntimeError("private close failure")
            return original()

        runtime.close = close

    factory, runtimes, _, threads = factory_for(Remote(), decorate=decorate)
    worker = GatewayRecoveryMonitorWorker(source, worker_settings(tmp_path, factory))
    try:
        worker.start()
        assert not worker.close(timeout_seconds=0.05)
        wait_for(lambda: worker.status()["state"] == "retained")
        original = worker._runtime
        assert original is runtimes[0] and len(close_threads) == 1
        time.sleep(0.08)
        assert len(close_threads) == 1 and worker._thread.is_alive()
        close_gate.set()
        assert worker.close(timeout_seconds=2)
        assert worker._runtime is original and len(runtimes) == 1
        assert close_threads == [threads[0], threads[0]]
    finally:
        close_gate.set()
        assert worker.close(timeout_seconds=2)


def test_factory_error_and_unstarted_recorder_never_create_runtime(tmp_path, recorder):
    source, owner, _ = recorder
    calls = []

    def failure(*, require_unregister_ack):
        calls.append(require_unregister_ack)
        raise RuntimeError("private factory details")

    worker = GatewayRecoveryMonitorWorker(source, worker_settings(tmp_path, failure))
    with pytest.raises(RuntimeError, match="FACTORY_FAILED"):
        worker.start()
    assert calls == [True] and worker.status()["resource_state"] == "NOT_STARTED"
    wait_for(lambda: not worker._thread.is_alive())
    assert worker.close()
    other = GatewayRecoveryObservationRecorder(
        owner,
        qualified_page,
        recorder_settings(
            tmp_path / "missing",
            qualification_mode="local_clock_v1",
            clock_max_interval_ns=1000000000,
        ),
    )
    with pytest.raises(RuntimeError, match="RECORDER_JOURNAL_UNAVAILABLE"):
        other.borrow_journal()
    no_source = GatewayRecoveryMonitorWorker(other, worker_settings(tmp_path, failure))
    with pytest.raises(RuntimeError, match="SOURCE_JOURNAL_UNAVAILABLE"):
        no_source.start()
    wait_for(lambda: not no_source._thread.is_alive())
    assert no_source.close() and calls == [True]


def test_v1_source_is_rejected_before_factory(tmp_path):
    owner, _ = owner_for(tmp_path)
    source = GatewayRecoveryObservationRecorder(
        owner, lambda after, limit: page(after), recorder_settings(tmp_path)
    )
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)

    worker = GatewayRecoveryMonitorWorker(source, worker_settings(tmp_path, factory))
    try:
        source.start()
        with pytest.raises(RuntimeError, match="SOURCE_JOURNAL_UNAVAILABLE"):
            worker.start()
        wait_for(lambda: not worker._thread.is_alive())
        assert worker.close() and calls == []
    finally:
        assert source.close()
        assert owner.close(2)


def test_start_failure_keeps_exact_runtime_until_strict_cleanup(tmp_path, recorder):
    source, _, _ = recorder

    def decorate(kind, runtime):
        if kind == "runtime":
            runtime.start = lambda: (_ for _ in ()).throw(RuntimeError("private startup"))

    factory, runtimes, _, _ = factory_for(Remote(), decorate=decorate)
    worker = GatewayRecoveryMonitorWorker(source, worker_settings(tmp_path, factory))
    with pytest.raises(RuntimeError, match="WORKER_SETUP_FAILED"):
        worker.start()
    assert worker._runtime is runtimes[0] and worker._thread.is_alive()
    assert worker.status()["resource_state"] == "UNKNOWN"
    assert worker.close(timeout_seconds=2) and runtimes[0].state == "closed"


def test_invalid_settings_and_pid_are_finite(tmp_path, recorder):
    source, _, _ = recorder
    factory, _, _, _ = factory_for(Remote())
    with pytest.raises(ValueError, match="INVALID_RECOVERY_MONITOR_SETTINGS"):
        worker_settings(tmp_path, factory, poll_interval_seconds=0)
    worker = GatewayRecoveryMonitorWorker(source, worker_settings(tmp_path, factory))
    assert "factory" not in repr(worker._settings).lower()
    worker._pid = -1
    with pytest.raises(RuntimeError, match="WORKER_WRONG_PROCESS"):
        worker.status()
    worker._pid = os.getpid()
    assert worker.close()


def test_binding_proof_loss_stops_automatic_sends_without_rebuilding(tmp_path, recorder):
    source, _, _ = recorder
    lost = threading.Event()
    remote = Remote()

    def decorate(kind, runtime):
        if kind != "runtime":
            return
        runtime.authenticated_peer_for_binding = lambda _: (
            replace(_PEER, actor_ref="other") if lost.is_set() else _PEER,
            "f" * 64,
        )

    factory, runtimes, _, _ = factory_for(remote, decorate=decorate)
    worker = GatewayRecoveryMonitorWorker(source, worker_settings(tmp_path, factory))
    try:
        worker.start()
        wait_for(lambda: worker.status()["cursor"] is not None and worker.status()["cursor"] >= 2)
        lost.set()
        wait_for(lambda: worker.status()["state"] == "degraded")
        count = len(remote.calls)
        time.sleep(0.1)
        assert len(remote.calls) == count and worker._runtime is runtimes[0] and len(runtimes) == 1
        assert worker.status()["last_failure_code"] == "BINDING_CHANGED"
    finally:
        lost.clear()
        assert worker.close(timeout_seconds=2)


def test_nonclosed_runtime_never_claims_cleanup_and_retries_on_owner(tmp_path, recorder):
    source, _, _ = recorder
    permit = threading.Event()
    close_calls = []

    def decorate(kind, runtime):
        if kind != "runtime":
            return
        original = runtime.close

        def close():
            close_calls.append(threading.get_ident())
            if permit.is_set():
                original()

        runtime.close = close

    factory, runtimes, _, threads = factory_for(Remote(), decorate=decorate)
    worker = GatewayRecoveryMonitorWorker(source, worker_settings(tmp_path, factory))
    try:
        worker.start()
        assert worker.close(timeout_seconds=0.05) is False
        wait_for(lambda: worker.status()["state"] == "retained")
        assert runtimes[0].state == "registered" and worker.status()["resource_state"] == "UNKNOWN"
        permit.set()
        assert worker.close(timeout_seconds=2)
        assert close_calls == [threads[0], threads[0]] and runtimes[0].state == "closed"
    finally:
        permit.set()
        assert worker.close(timeout_seconds=2)


def test_start_timeout_retains_original_until_owner_thread_finishes_cleanup(tmp_path, recorder):
    source, _, _ = recorder
    entered, release = threading.Event(), threading.Event()

    def decorate(kind, runtime):
        if kind != "runtime":
            return
        original = runtime.start

        def start():
            entered.set()
            assert release.wait(3)
            return original()

        runtime.start = start

    factory, runtimes, _, _ = factory_for(Remote(), decorate=decorate)
    worker = GatewayRecoveryMonitorWorker(
        source, worker_settings(tmp_path, factory, start_timeout_seconds=0.03)
    )
    try:
        with pytest.raises(RuntimeError, match="WORKER_START_UNCONFIRMED"):
            worker.start()
        assert entered.is_set() and worker._runtime is runtimes[0]
        assert worker.close(timeout_seconds=0.02) is False
        assert worker.status()["resource_state"] == "UNKNOWN"
    finally:
        release.set()
        assert worker.close(timeout_seconds=2)
    assert len(runtimes) == 1 and runtimes[0].state == "closed"


def test_sink_start_failure_keeps_runtime_and_no_recreate(tmp_path, recorder):
    from bt_api_base.service_protocol import WireIdentity, WireRole

    source, _, _ = recorder
    factory, runtimes, _, _ = factory_for(Remote())
    other = WireIdentity(WireRole.SERVICE, "wrong-principal", "other-peer")
    worker = GatewayRecoveryMonitorWorker(
        source, worker_settings(tmp_path, factory, expected_peer=other)
    )
    with pytest.raises(RuntimeError, match="WORKER_SETUP_FAILED"):
        worker.start()
    assert len(runtimes) == 1 and worker._runtime is runtimes[0] and worker._thread.is_alive()
    assert worker.status()["resource_state"] == "UNKNOWN"
    assert worker.close(timeout_seconds=2) and runtimes[0].state == "closed"


def test_relay_start_failure_retains_partial_relay_until_owner_cleanup(tmp_path, recorder):
    source, _, _ = recorder
    (tmp_path / "relay.sqlite").write_bytes(b"not-a-checkpoint")
    factory, runtimes, _, _ = factory_for(Remote())
    worker = GatewayRecoveryMonitorWorker(source, worker_settings(tmp_path, factory))
    with pytest.raises(RuntimeError, match="WORKER_SETUP_FAILED"):
        worker.start()
    assert (
        worker._relay is not None and worker._runtime is runtimes[0] and worker._thread.is_alive()
    )
    assert worker.status()["resource_state"] == "UNKNOWN"
    assert worker.close(timeout_seconds=2) and runtimes[0].state == "closed"


def test_nonzero_cleanup_counter_cannot_report_complete(tmp_path, recorder):
    source, _, _ = recorder

    def decorate(kind, runtime):
        if kind == "runtime":
            original = runtime.close

            def close():
                original()
                runtime._unknown_registration_count = 1

            runtime.close = close

    factory, runtimes, _, _ = factory_for(Remote(), decorate=decorate)
    worker = GatewayRecoveryMonitorWorker(source, worker_settings(tmp_path, factory))
    try:
        worker.start()
        assert worker.close(timeout_seconds=0.05) is False
        wait_for(lambda: worker.status()["state"] == "retained")
        assert worker.status()["resource_state"] == "UNKNOWN" and runtimes[0].state == "closed"
    finally:
        # Only the synthetic test fixture may clear its injected counter.
        runtimes[0]._unknown_registration_count = 0
        runtimes[0].close = lambda: None
        assert worker.close(timeout_seconds=2)


def test_recorder_failed_start_never_publishes_journal(tmp_path, monkeypatch):
    import bt_api_py.gateway_recovery_observation as observation

    owner, _ = owner_for(tmp_path)
    source = GatewayRecoveryObservationRecorder(
        owner, lambda after, limit: page(after), recorder_settings(tmp_path)
    )

    def fail(*args, **kwargs):
        raise RuntimeError("private journal error")

    monkeypatch.setattr(observation, "RecoveryObservationJournal", fail)
    try:
        with pytest.raises(RuntimeError, match="RECORDER_START_FAILED"):
            source.start()
        with pytest.raises(RuntimeError, match="RECORDER_JOURNAL_UNAVAILABLE"):
            source.borrow_journal()
        assert source.close()
    finally:
        assert owner.close(2)


@pytest.mark.parametrize("value", [-1, True, float("inf"), 10**1000, "1"])
def test_close_timeout_invalid_is_value_error(tmp_path, recorder, value):
    source, _, _ = recorder
    factory, _, _, _ = factory_for(Remote())
    worker = GatewayRecoveryMonitorWorker(source, worker_settings(tmp_path, factory))
    with pytest.raises(ValueError, match="INVALID_WORKER_CLOSE_TIMEOUT"):
        worker.close(timeout_seconds=value)
    assert worker.close()


@pytest.mark.parametrize(
    "kind,reason,expected",
    [
        ("relay", "EVENT_TOO_LARGE", "EVENT_TOO_LARGE"),
        ("relay", "CHECKPOINT_UNCERTAIN", "CHECKPOINT_UNCERTAIN"),
        ("relay", "CHECKPOINT_INVALID", "CHECKPOINT_INVALID"),
        ("relay", "SOURCE_HISTORY_CHANGED", "SOURCE_HISTORY_CHANGED"),
        ("relay", "BINDING_CHANGED", "BINDING_CHANGED"),
        ("relay", "private reason", "RELAY_OR_BINDING_UNAVAILABLE"),
        ("sink", "INGRESS_BINDING_CHANGED", "BINDING_CHANGED"),
        ("sink", "private reason", "RELAY_OR_BINDING_UNAVAILABLE"),
        ("other", "private reason", "RELAY_OR_BINDING_UNAVAILABLE"),
    ],
)
def test_worker_reports_only_allowlisted_failure_codes(
    tmp_path, recorder, monkeypatch, kind, reason, expected
):
    from bt_api_py.gateway_recovery_monitor_relay import (
        GatewayRecoveryMonitorRelay,
        GatewayRecoveryRelayError,
    )
    from bt_api_py.service_monitor_authenticated import AuthenticatedMonitorSinkError

    source, _, _ = recorder
    trigger = threading.Event()
    original = GatewayRecoveryMonitorRelay.pump

    def pump(relay, **kwargs):
        if trigger.is_set():
            if kind == "relay":
                raise GatewayRecoveryRelayError(reason)
            if kind == "sink":
                raise AuthenticatedMonitorSinkError(reason)
            raise RuntimeError(reason)
        return original(relay, **kwargs)

    monkeypatch.setattr(GatewayRecoveryMonitorRelay, "pump", pump)
    factory, _, _, _ = factory_for(Remote())
    worker = GatewayRecoveryMonitorWorker(source, worker_settings(tmp_path, factory))
    try:
        worker.start()
        trigger.set()
        wait_for(lambda: worker.status()["state"] == "degraded")
        assert worker.status()["last_failure_code"] == expected
        assert "private" not in str(worker.status())
    finally:
        assert worker.close(timeout_seconds=2)
