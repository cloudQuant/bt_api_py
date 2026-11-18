"""Bounded close contract when a close cannot be confirmed.

A Required Monitor that stays unreachable must not make repeated SIGTERM/
SIGINT stops hang the operator application forever: after a finite number
of unconfirmed stop commands the owner thread ends with an explicit
uncertain close verdict, and the Required Monitor worker itself exits
within a bounded shutdown budget while retaining its cleanup uncertainty.
No real account, venue or server is involved.
"""
from __future__ import annotations

from contextlib import suppress
from types import SimpleNamespace

import pytest

import bt_api_py.gateway_required_monitor as monitor_module
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.gateway_operator import GatewayOperatorDeployment
from bt_api_py.gateway_operator_application import (
    GatewayOperatorApplication,
    GatewayOperatorApplicationError,
    GatewayOperatorProfile,
)
from bt_api_py.gateway_required_monitor import RequiredMonitor, RequiredMonitorSettings
from bt_api_py.service_host import ServiceHostError


class _CloseUncertainDeployment(GatewayOperatorDeployment):
    """A deployment whose close stays unconfirmed, like a Gateway whose
    Required Monitor cleanup cannot be confirmed because the Monitor is
    unreachable."""

    def __init__(self, *, error: BaseException | None = None, fail_close_calls: int | None = None) -> None:
        self._state = "new"
        self._error = error if error is not None else RuntimeError("synthetic close unconfirmed")
        self.close_calls = 0
        # ``None`` means every close stays unconfirmed; an integer means only
        # that many initial closes fail before the close confirms.
        self._fail_close_calls = fail_close_calls

    @property
    def state(self) -> str:
        return self._state

    @property
    def cleanup_status(self):
        return {"unclaimed_provider_count": 0, "last_cleanup_code": None}

    def start(self):
        self._state = "started"
        return self

    def serve_once(self, timeout_ms: int = 0) -> bool:
        return False

    def close(self) -> None:
        self.close_calls += 1
        self._state = "close_failed"
        if self._fail_close_calls is None or self.close_calls <= self._fail_close_calls:
            raise self._error
        self._state = "closed"


def _app_for(deployment: GatewayOperatorDeployment) -> GatewayOperatorApplication:
    profile = GatewayOperatorProfile("close-budget", "market_hub", lambda: deployment)
    return GatewayOperatorApplication(profile, poll_interval_ms=5)


def test_repeated_unconfirmed_stops_end_with_bounded_uncertain_verdict() -> None:
    deployment = _CloseUncertainDeployment(error=ServiceHostError("CLOSE_FAILED"))
    app = _app_for(deployment)
    assert app.start(timeout_seconds=2)["state"] == "running"
    try:
        for _ in range(2):
            with pytest.raises(GatewayOperatorApplicationError) as stop_error:
                app.stop(timeout_seconds=2)
            assert stop_error.value.code == "CLOSE_FAILED"
            assert app.status["state"] == "uncertain"
            assert app._thread is not None and app._thread.is_alive()

        # Third unconfirmed stop: bounded final verdict with the explicit
        # error code; the owner thread ends instead of retrying forever.
        status = app.stop(timeout_seconds=2)
        assert status["state"] == "failed"
        assert status["error_code"] == "CLOSE_FAILED"
        assert dict(status["operation"]) == {
            "name": "stop", "state": "outcome_uncertain", "error_code": "CLOSE_FAILED",
        }
        assert app._thread is not None and not app._thread.is_alive()
        assert deployment.close_calls == 3

        # A later stop reports the same terminal verdict without new work.
        assert app.stop(timeout_seconds=2)["state"] == "failed"
        assert deployment.close_calls == 3
    finally:
        if app._thread is not None and app._thread.is_alive():
            with suppress(GatewayOperatorApplicationError):
                app.stop(timeout_seconds=2)
            app._thread.join(1)
            assert not app._thread.is_alive()


def test_transient_close_failure_does_not_consume_the_retry_budget() -> None:
    deployment = _CloseUncertainDeployment(fail_close_calls=1)
    app = _app_for(deployment)
    assert app.start(timeout_seconds=2)["state"] == "running"
    try:
        with pytest.raises(GatewayOperatorApplicationError) as stop_error:
            app.stop(timeout_seconds=2)
        assert stop_error.value.code == "APPLICATION_CLOSE_UNCERTAIN"
        assert app.status["state"] == "uncertain"
        assert app._thread is not None and app._thread.is_alive()

        # The retry confirms the close; the budget is reset, so a future
        # transient failure still gets the full bounded retry allowance.
        assert app.stop(timeout_seconds=2)["state"] == "closed"
        assert app._stop_close_failures == 0
        assert app._thread is not None and not app._thread.is_alive()
    finally:
        if app._thread is not None and app._thread.is_alive():
            with suppress(GatewayOperatorApplicationError):
                app.stop(timeout_seconds=2)
            app._thread.join(1)
            assert not app._thread.is_alive()


class _PendingCleanupDeployment(_CloseUncertainDeployment):
    """A deployment with retained provider cleanup that is still retryable."""

    def __init__(self) -> None:
        super().__init__()
        self.pending_unclaimed = 1

    @property
    def cleanup_status(self):
        return {
            "unclaimed_provider_count": self.pending_unclaimed,
            "last_cleanup_code": (
                "UNCLAIMED_PROVIDER_CLOSE_UNCONFIRMED" if self.pending_unclaimed else None
            ),
        }

    def retry_unclaimed_provider_cleanup(self):
        self.pending_unclaimed = 0
        return {"closed": 1, "pending": 0}


def test_pending_provider_cleanup_keeps_the_owner_for_an_explicit_cleanup_retry() -> None:
    deployment = _PendingCleanupDeployment()
    app = _app_for(deployment)
    assert app.start(timeout_seconds=2)["state"] == "running"
    try:
        for _ in range(3):
            with pytest.raises(GatewayOperatorApplicationError):
                app.stop(timeout_seconds=2)
            assert app.status["state"] == "uncertain"

        # Retained provider cleanup is still retryable on the same owner
        # thread; the bounded verdict must not drop it silently.
        assert app._thread is not None and app._thread.is_alive()
        status = app.retry_unclaimed_provider_cleanup(timeout_seconds=2)
        assert status["cleanup"]["unclaimed_provider_count"] == 0
    finally:
        if app._thread is not None and app._thread.is_alive():
            with suppress(GatewayOperatorApplicationError):
                app.stop(timeout_seconds=2)
            app._thread.join(1)
            assert not app._thread.is_alive()


class _OwnerStub:
    def __init__(self, generation: str) -> None:
        self._binding = SimpleNamespace(generation=generation)

    def binding(self):
        return self._binding

    def status(self):
        return SimpleNamespace(thread_alive=True)

    def submit_service_call(self, callback):
        class _Result:
            def result(self, **_):
                return callback()

        return _Result()


class _UnreachableMonitorRuntime:
    """A registered runtime whose heartbeat and retire stay unconfirmed."""

    state = "registered"
    unregister_uncertainties = 0
    unknown_registration_count = 0
    open_transport_count = 1

    def start(self) -> None:
        pass

    def maintain(self) -> None:
        pass

    def call(self, *_args, **_kwargs):
        raise RuntimeError("synthetic unreachable monitor")

    def retire_required_monitor_session(self) -> bool:
        return False


def test_monitor_worker_exits_within_bounded_shutdown_budget(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(monitor_module, "_SHUTDOWN_CLEANUP_BUDGET_SECONDS", 0.3)
    monkeypatch.setattr(
        monitor_module.RequiredMonitor, "_precheck_runtime", lambda self, runtime: None
    )
    monkeypatch.setattr(
        monitor_module.RequiredMonitor,
        "_binding",
        lambda self, runtime: SimpleNamespace(
            registration_id="reg", service_epoch="epoch", scope_ref="scope"
        ),
    )
    root = tmp_path / "guards"
    root.mkdir()
    guard = LocalAccountSessionGuard(root, "account", "owner")
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal.create(tmp_path / "db", "account", generation=generation)
    control = journal.enable_controls(guard)
    settings = RequiredMonitorSettings(
        lambda: _UnreachableMonitorRuntime(),
        WireIdentity(WireRole.SERVICE, "gateway", "gateway"),
        WireIdentity(WireRole.SERVICE, "monitor", "monitor"),
        WireScope("domain", account_ref="account"),
        interval_seconds=0.01,
        timeout_seconds=0.05,
    )
    monitor = RequiredMonitor(settings, generation)
    monitor.initialize_on_owner(journal, control, guard)
    owner = _OwnerStub(generation)
    try:
        monitor.start(owner, control)
        assert monitor._thread is not None and monitor._thread.is_alive()

        # The close stays unconfirmed while the Monitor is unreachable ...
        assert not monitor.close(0.05)
        # ... but the worker exits on its own within the bounded shutdown
        # budget instead of waiting forever for the next explicit close.
        monitor._thread.join(2)
        assert not monitor._thread.is_alive()
        assert monitor._cleanup_uncertain is True
        assert monitor.diagnostic["phase"] == "BLOCKED"

        # The retained registration keeps the verdict explicitly uncertain;
        # later closes never report the cleanup as confirmed.
        assert not monitor.close(0.1)
    finally:
        with suppress(Exception):
            monitor.close(0.1)
        guard.release()


def test_monitor_worker_shutdown_wait_is_bounded_not_indefinite() -> None:
    """The shutdown-phase retry wait must use a bounded timeout.

    Static check: the shutdown branch uses the cumulative budget and never
    falls back to an unbounded ``wait()`` that would hang the close.
    """

    import inspect

    body = inspect.getsource(monitor_module)
    assert "_SHUTDOWN_CLEANUP_BUDGET_SECONDS" in body
    assert "self._cleanup_retry.wait()" not in body
