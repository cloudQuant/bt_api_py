"""Required Monitor gate and reason handling; no real account or server."""

from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import DirectCommandJournalError, GatewayDirectCommandJournal
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.gateway_required_monitor import RequiredMonitor, RequiredMonitorSettings


class Owner:
    def __init__(self, generation):
        self.value = SimpleNamespace(generation=generation)

    def binding(self):
        return self.value

    def status(self):
        return SimpleNamespace(thread_alive=True)

    def submit_service_call(self, callback):
        class Result:
            def result(self, **_):
                return callback()
        return Result()


def setup_monitor(tmp_path, *, automatic=True):
    root = tmp_path / "guards"
    root.mkdir()
    guard = LocalAccountSessionGuard(root, "account", "owner")
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal.create(tmp_path / "db", "account", generation=generation)
    control = journal.enable_controls(guard)
    settings = RequiredMonitorSettings(
        lambda: None, WireIdentity(WireRole.SERVICE, "gateway", "gateway"),
        WireIdentity(WireRole.SERVICE, "monitor", "monitor"),
        WireScope("domain", account_ref="account"), automatic_resume=automatic,
    )
    monitor = RequiredMonitor(settings, generation)
    monitor.initialize_on_owner(journal, control, guard)
    owner = Owner(generation)
    monitor._owner, monitor._control = owner, control
    monitor.gate.bind_owner(owner)
    return monitor, journal, control, guard


def test_only_monitor_cause_resumes_and_deadline_gates_even_with_owner_stalled(tmp_path, monkeypatch):
    import bt_api_py.gateway_required_monitor as module

    now = [1_000]
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: now[0])
    monitor, journal, control, guard = setup_monitor(tmp_path)
    try:
        with pytest.raises(DirectCommandJournalError, match="CONTROL_STOP_NEW"):
            journal.reserve("s", "first", "p", {"symbol": "X"})
        control.apply("other", "FREEZE", scope_kind="account", origin="operator")
        proof = (monitor.generation, "reg", "epoch", "nonce")
        assert monitor.gate.acknowledge(proof, 2_000) is False
        monitor._restore(proof)
        assert monitor.gate() is True
        assert control.get("other")["active"] == 1
        with pytest.raises(DirectCommandJournalError, match="CONTROL_FREEZE"):
            journal.reserve("s", "second", "p", {"symbol": "X"})
        control.resume("other", expected_version=1, operation_id="operator-resume")
        assert journal.reserve("s", "third", "p", {"symbol": "X"}).status == "RESERVED"
        now[0] = 2_000
        assert monitor.gate() is False
        with pytest.raises(DirectCommandJournalError, match="CONTROL_STOP_NEW"):
            journal.reserve("s", "fourth", "p", {"symbol": "X"})
        with pytest.raises(RuntimeError, match="MONITOR_PROOF_EXPIRED"):
            monitor._restore(proof)
    finally:
        monitor.gate.close()
        guard.release()


def test_manual_resume_requires_operator_to_clear_monitor_cause(tmp_path, monkeypatch):
    import bt_api_py.gateway_required_monitor as module

    monkeypatch.setattr(module.time, "monotonic_ns", lambda: 1_000)
    monitor, journal, control, guard = setup_monitor(tmp_path, automatic=False)
    try:
        proof = (monitor.generation, "reg", "epoch", "nonce")
        monitor.gate.acknowledge(proof, 2_000)
        monitor._restore(proof)
        assert monitor.gate() is False
        cause, = control.active_system_causes(monitor.origin)
        control.resume(cause["cause_id"], expected_version=cause["version"], operation_id="manual")
        monitor._restore(proof)
        assert monitor.gate() is True
    finally:
        monitor.gate.close()
        guard.release()
