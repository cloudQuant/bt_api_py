"""Iteration 68.05: startup classification and recovery gating of the worker.

The Gateway side of the frozen heartbeat wire is exercised here without a
network: one owner/control/journal fixture, a patched monotonic clock and the
real frozen matcher/builder. A test passes only when a provisional startup
observation cannot mask a later confirmed incompatibility and when only a
current, identity-matched proof can release the Monitor cause.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bt_api_gateway.monitor_heartbeat_wire import (
    MONITOR_HEARTBEAT_OPERATIONS,
    build_monitor_heartbeat_request,
    monitor_heartbeat_ack_matches,
)

from bt_api_py.gateway_required_monitor import (
    MONITOR_STARTUP_AUTH_FAILED,
    MONITOR_STARTUP_COMPATIBLE,
    MONITOR_STARTUP_INCOMPATIBLE,
    MONITOR_STARTUP_TIMEOUT,
    MONITOR_STARTUP_UNKNOWN,
    MONITOR_STARTUP_UNREACHABLE,
    _MonitorWorkerError,
    _startup_category_for,
)
from bt_api_py.service_runtime import ServiceCallOutcomeUnknownError, ServiceRuntimeError
from tests.unit.test_iter63_gateway_required_monitor import setup_monitor


def _started(monitor):
    """Mark the worker as started without running its transport loop."""
    monitor._thread = object()
    return monitor


def test_provisional_observation_never_masks_a_confirmed_incompatible(tmp_path):
    monitor, _journal, _control, guard = setup_monitor(tmp_path)
    try:
        _started(monitor)
        monitor._record_startup(MONITOR_STARTUP_UNREACHABLE)
        assert monitor.startup_probe(0) == MONITOR_STARTUP_UNREACHABLE
        # The authenticated legacy discovery arrives later in the grace.
        monitor._record_startup(MONITOR_STARTUP_INCOMPATIBLE)
        assert monitor.startup_probe(0) == MONITOR_STARTUP_INCOMPATIBLE
    finally:
        monitor.gate.close()
        guard.release()


def test_unknown_timeout_and_unreachable_never_become_compatible(tmp_path):
    monitor, _journal, _control, guard = setup_monitor(tmp_path)
    try:
        _started(monitor)
        monitor._record_startup(MONITOR_STARTUP_UNKNOWN)
        assert monitor.startup_probe(0) == MONITOR_STARTUP_UNKNOWN
        # A later, less cautious provisional observation cannot downgrade it.
        monitor._record_startup(MONITOR_STARTUP_UNREACHABLE)
        assert monitor.startup_probe(0) == MONITOR_STARTUP_UNKNOWN
        monitor._record_startup(MONITOR_STARTUP_COMPATIBLE)
        assert monitor.startup_probe(0) == MONITOR_STARTUP_COMPATIBLE
    finally:
        monitor.gate.close()
        guard.release()


def test_an_expired_probe_reports_timeout_and_never_compatible(tmp_path):
    monitor, _journal, _control, guard = setup_monitor(tmp_path)
    try:
        _started(monitor)
        assert monitor.startup_probe(0) == MONITOR_STARTUP_TIMEOUT
    finally:
        monitor.gate.close()
        guard.release()


@pytest.mark.parametrize(
    "error, category",
    [
        (ServiceRuntimeError("DENIED"), MONITOR_STARTUP_AUTH_FAILED),
        (ServiceRuntimeError("SCOPE_DENIED"), MONITOR_STARTUP_AUTH_FAILED),
        (ServiceRuntimeError("UNAUTHORIZED_RELAY"), MONITOR_STARTUP_AUTH_FAILED),
        (ServiceCallOutcomeUnknownError("message", "monitor.heartbeat"), MONITOR_STARTUP_UNKNOWN),
        (_MonitorWorkerError("MONITOR_ACK_INVALID"), MONITOR_STARTUP_UNKNOWN),
        (_MonitorWorkerError("MONITOR_PROOF_EXPIRED"), MONITOR_STARTUP_UNKNOWN),
        (ServiceRuntimeError("INVALID_HEARTBEAT"), MONITOR_STARTUP_UNKNOWN),
        (ServiceRuntimeError("UNSUPPORTED_OPERATION"), MONITOR_STARTUP_UNKNOWN),
        (ServiceRuntimeError("CAPABILITY_UNAVAILABLE"), MONITOR_STARTUP_UNREACHABLE),
        (ServiceRuntimeError("REQUEST_NOT_SENT"), MONITOR_STARTUP_UNREACHABLE),
        (ServiceRuntimeError("STALE_EPOCH"), MONITOR_STARTUP_UNREACHABLE),
        (RuntimeError("unknown local failure"), MONITOR_STARTUP_UNREACHABLE),
    ],
)
def test_each_failure_is_classified_without_claiming_compatibility(error, category):
    result = _startup_category_for(error)
    assert result == category
    assert result != MONITOR_STARTUP_COMPATIBLE


def test_a_forged_or_replayed_ack_is_not_an_ack():
    request = build_monitor_heartbeat_request(
        relay_epoch="registration-1", heartbeat_id="h" * 32, sent_at_ns=123
    )
    ack = {**request, "monitor_principal": "monitor-p"}
    assert set(MONITOR_HEARTBEAT_OPERATIONS) == {"monitor.heartbeat"}
    assert monitor_heartbeat_ack_matches(request, ack, "monitor-p") is True
    for forged in (
        {**ack, "schema_version": 2},
        {**ack, "relay_epoch": "registration-2"},
        {**ack, "heartbeat_id": "h" * 31},
        {**ack, "sent_at_ns": 124},
        {**ack, "monitor_principal": "other-p"},
        {key: value for key, value in ack.items() if key != "relay_epoch"},
        {**ack, "extra": 1},
        None,
    ):
        assert monitor_heartbeat_ack_matches(request, forged, "monitor-p") is False


def test_old_or_duplicate_proof_cannot_release_the_monitor_cause(tmp_path, monkeypatch):
    import bt_api_py.gateway_required_monitor as module

    monkeypatch.setattr(module.time, "monotonic_ns", lambda: 1_000)
    monitor, _journal, control, guard = setup_monitor(tmp_path)
    try:
        proof_old = (monitor.generation, "registration-1", "epoch-1", "heartbeat-old")
        proof_new = (monitor.generation, "registration-1", "epoch-1", "heartbeat-new")
        assert monitor.gate.acknowledge(proof_new, 2_000) is False
        with pytest.raises(RuntimeError, match="MONITOR_PROOF_EXPIRED"):
            monitor._restore(proof_old)
        assert monitor.gate() is False
        assert control.active_system_causes(monitor.origin)
        monitor._restore(proof_new)
        assert monitor.gate() is True
        assert not control.active_system_causes(monitor.origin)
    finally:
        monitor.gate.close()
        guard.release()


def test_a_monitor_cause_that_is_not_its_own_stop_new_is_preserved(tmp_path, monkeypatch):
    import bt_api_py.gateway_required_monitor as module

    monkeypatch.setattr(module.time, "monotonic_ns", lambda: 1_000)
    monitor, _journal, control, guard = setup_monitor(tmp_path)
    try:
        control.apply("monitor-freeze", "FREEZE", scope_kind="account", origin=monitor.origin)
        proof = (monitor.generation, "registration-1", "epoch-1", "heartbeat-1")
        assert monitor.gate.acknowledge(proof, 2_000) is False
        with pytest.raises(RuntimeError, match="MONITOR_CAUSE_MISMATCH"):
            monitor._restore(proof)
        assert monitor.gate() is False
        assert control.get("monitor-freeze")["active"] == 1
    finally:
        monitor.gate.close()
        guard.release()


def test_a_stale_cause_version_cannot_release_a_newer_cause(tmp_path, monkeypatch):
    from bt_api_gateway.direct_command_journal import DirectCommandJournalError

    import bt_api_py.gateway_required_monitor as module

    monkeypatch.setattr(module.time, "monotonic_ns", lambda: 1_000)
    monitor, _journal, control, guard = setup_monitor(tmp_path)
    try:
        real = control.active_system_causes

        def out_of_order(origin):
            # Simulate a version that was reordered between the read and the
            # release; the optimistic expected_version must refuse it.
            return tuple({**cause, "version": cause["version"] + 1} for cause in real(origin))

        monkeypatch.setattr(control, "active_system_causes", out_of_order)
        proof = (monitor.generation, "registration-1", "epoch-1", "heartbeat-1")
        assert monitor.gate.acknowledge(proof, 2_000) is False
        with pytest.raises(DirectCommandJournalError, match="CONTROL_VERSION_CONFLICT"):
            monitor._restore(proof)
        assert monitor.gate() is False
        monkeypatch.undo()
        assert control.active_system_causes(monitor.origin)
    finally:
        monitor.gate.close()
        guard.release()


def test_the_release_path_has_no_webhook_or_notification_dependency():
    """Webhook success is never evidence for the Gateway's liveness contract."""
    import bt_api_py.gateway_required_monitor as module

    source = Path(module.__file__).read_text(encoding="utf-8").lower()
    assert "webhook" not in source
    assert "notification" not in source
    names = {name.lower() for name in dir(module.RequiredMonitor)}
    assert not any("webhook" in name or "notify" in name for name in names)
