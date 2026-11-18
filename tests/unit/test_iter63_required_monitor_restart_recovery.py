"""Iteration 63/68: Required Monitor restart recovery and owner-detach budget.

These tests drive the real ``RequiredMonitor._run`` loop (real gate, real
journal/control, real frozen heartbeat wire) against synthetic runtimes, so no
network or account is involved:

1. A Monitor restart (new registration epoch, ``STALE_EPOCH`` on the old
   session) must converge back to ``MONITORING/NONE`` through retire + a new
   runtime instead of staying in ``RECONCILING/STARTING``.
2. ``MONITOR_OWNER_CHANGED`` is permanent for the single-use worker: it must
   retire its Monitor session once and stop instead of retrying every interval
   (the per-second failure storm seen in gateway-r55/r25).
3. While the Monitor is unreachable, the already-persisted outage cause must
   not be re-submitted through the owner queue on every failed heartbeat.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.gateway_required_monitor import RequiredMonitor, RequiredMonitorSettings
from bt_api_py.service_runtime import ServiceRuntimeError


class CountingOwner:
    """The trusted native owner fixture: one fixed executor thread, counted calls.

    Journal/control operations are pinned to the guard's acquiring thread, so
    like the real ``GatewayDirectOwner`` the queued callbacks must not run on
    the heartbeat worker thread.
    """

    def __init__(self, generation):
        self.value = SimpleNamespace(generation=generation)
        self.calls = 0
        self._queue = queue.Queue()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            callback, box = self._queue.get()
            if callback is None:
                return
            try:
                box.append(callback())
            except BaseException as error:  # noqa: BLE001 - forwarded to caller
                box.append(error)
            finally:
                self._queue.task_done()

    def binding(self):
        return self.value

    def status(self):
        return SimpleNamespace(thread_alive=self._thread.is_alive())

    def submit_service_call(self, callback):
        self.calls += 1
        box = []
        self._queue.put((callback, box))

        class Result:
            def result(self, **_):
                while not box:
                    time.sleep(0.001)
                outcome = box.pop()
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome

        return Result()


class FakeRuntime:
    """A heartbeat-only runtime double owned by the worker thread."""

    state = "registered"
    unregister_uncertainties = 0
    unknown_registration_count = 0
    open_transport_count = 1

    def __init__(self, registration_id, service_epoch):
        self.current_binding = SimpleNamespace(
            registration_id=registration_id,
            service_epoch=service_epoch,
            scope_ref="monitor-scope",
        )
        self._call = None
        self.started = False
        self.retired = False

    def start(self):
        self.started = True

    def maintain(self):
        return None

    def call(self, mount, operation, request, **_):
        assert mount == "upstream.telemetry" and operation == "monitor.heartbeat"
        return self._call(request)

    def retire_required_monitor_session(self):
        self.retired = True
        self.state = "closed"
        self.open_transport_count = 0
        return True


def _setup(tmp_path, runtimes, *, interval=0.02, timeout=0.3):
    # The session guard pins its acquiring thread; in production the journal,
    # controls and the monitor gate are all created on the native owner
    # thread, so this fixture runs the whole setup there too.
    owner = CountingOwner(generation=None)
    root = tmp_path / "guards"
    root.mkdir()
    guard = None

    def create_session():
        nonlocal guard
        guard = LocalAccountSessionGuard(root, "account", "owner")
        generation = guard.acquire()
        journal = GatewayDirectCommandJournal.create(
            tmp_path / "db", "account", generation=generation
        )
        control = journal.enable_controls(guard)
        return generation, journal, control

    generation, journal, control = owner.submit_service_call(create_session).result(
        timeout_seconds=5
    )
    owner.value = SimpleNamespace(generation=generation)
    pending = list(runtimes)
    lock = threading.Lock()
    created = []

    def runtime_factory():
        with lock:
            runtime = pending.pop(0)
            created.append(runtime)
            return runtime

    settings = RequiredMonitorSettings(
        runtime_factory,
        WireIdentity(WireRole.SERVICE, "gateway", "gateway"),
        WireIdentity(WireRole.SERVICE, "monitor", "monitor"),
        WireScope("domain", account_ref="account"),
        interval_seconds=interval,
        timeout_seconds=timeout,
    )
    monitor = RequiredMonitor(settings, generation)
    owner.submit_service_call(
        lambda: monitor.initialize_on_owner(journal, control, guard)
    ).result(timeout_seconds=5)
    monitor._owner, monitor._control = owner, control
    # The gate owner is bound by monitor.start(owner, control).
    # The strict runtime/binding identity checks are integration concerns;
    # these tests exercise the worker's recovery loop against doubles.
    monitor._precheck_runtime = lambda runtime: None
    monitor._binding = lambda runtime: runtime.current_binding
    monitor._confirmed_incompatible = lambda runtime, error: False
    return monitor, control, owner, created, guard


def _wait(predicate, seconds, message):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail(f"timeout waiting for {message}")


def _owner_call(owner, callback):
    return owner.submit_service_call(callback).result(timeout_seconds=5)


def _bind_ack(monitor, *runtimes):
    principal = monitor.settings.monitor_identity.principal_ref

    def ack(request):
        return {**request, "monitor_principal": principal}

    for runtime in runtimes:
        runtime._call = ack
    return ack


def test_monitor_restart_with_new_epoch_converges_back_to_monitoring(tmp_path):
    stale_calls = {"count": 0}

    def stale_call(_request):
        stale_calls["count"] += 1
        raise ServiceRuntimeError("STALE_EPOCH")

    old_runtime = FakeRuntime("registration-old", "epoch-old")
    new_runtime = FakeRuntime("registration-new", "epoch-new")
    old_runtime._call = stale_call
    monitor, control, owner, created, guard = _setup(
        tmp_path, [old_runtime, new_runtime]
    )
    try:
        _bind_ack(monitor, new_runtime)
        monitor.start(monitor._owner, control)
        _wait(
            lambda: monitor.diagnostic["phase"] == "MONITORING"
            and monitor.diagnostic["failure_category"] == "NONE",
            5,
            "MONITORING/NONE after Monitor restart",
        )
        _wait(lambda: monitor.gate(), 5, "live gate after Monitor restart")
        assert monitor.diagnostic == {
            "phase": "MONITORING", "failure_category": "NONE", "cleanup_kind": "NONE"
        }
        assert stale_calls["count"] >= 1
        assert len(created) == 2
        assert created[0].retired and created[0].state == "closed"
        assert created[1].started and not created[1].retired
        assert not _owner_call(owner, lambda: control.active_system_causes(monitor.origin))
        assert monitor.startup_probe(0) == "COMPATIBLE"
    finally:
        monitor.close(2)
        _owner_call(owner, guard.release)


def test_owner_change_stops_the_worker_instead_of_a_failure_storm(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    runtime = FakeRuntime("registration-1", "epoch-1")
    monitor, _control, owner, created, guard = _setup(tmp_path, [runtime])
    try:
        _bind_ack(monitor, runtime)
        monitor.start(owner, monitor._control)
        _wait(
            lambda: monitor.diagnostic["phase"] == "MONITORING"
            and monitor.diagnostic["failure_category"] == "NONE",
            5,
            "initial MONITORING",
        )
        # The native owner rebinds to another generation (or starts its final
        # cleanup): the binding this worker was created for is gone for good.
        caplog.clear()
        owner.value = None
        monitor._thread.join(5)
        assert not monitor._thread.is_alive()
        storm = [
            record
            for record in caplog.records
            if "MONITOR_OWNER_CHANGED" in record.getMessage()
        ]
        assert 1 <= len(storm) <= 2
        # Several intervals pass with no further heartbeat attempts or logs.
        time.sleep(0.3)
        assert not monitor._thread.is_alive()
        assert len([
            record
            for record in caplog.records
            if "MONITOR_OWNER_CHANGED" in record.getMessage()
        ]) == len(storm)
        assert monitor.diagnostic["phase"] == "STOPPED"
        assert monitor.diagnostic["cleanup_kind"] == "COMPLETE"
        assert created[0].retired and created[0].open_transport_count == 0
    finally:
        monitor.close(2)
        _owner_call(owner, guard.release)


def test_persisted_outage_is_not_resubmitted_on_every_failed_heartbeat(tmp_path):
    def unreachable(_request):
        raise ServiceRuntimeError("REQUEST_TIMEOUT")

    runtime = FakeRuntime("registration-1", "epoch-1")
    runtime._call = unreachable
    monitor, control, owner, _created, guard = _setup(tmp_path, [runtime])
    try:
        monitor.start(owner, control)
        _wait(
            lambda: monitor.diagnostic["failure_category"] == "UNREACHABLE",
            5,
            "first unreachable observation",
        )
        assert _owner_call(owner, lambda: control.active_system_causes(monitor.origin))
        before = owner.calls
        # ~25 heartbeat intervals with the Monitor unreachable: the outage
        # cause is already on the journal, so no per-second owner-queued
        # persist call may run (each one blocks the worker for the timeout).
        time.sleep(0.5)
        assert owner.calls - before <= 2
        assert monitor.diagnostic["phase"] == "MONITORING"
        assert monitor.diagnostic["failure_category"] == "UNREACHABLE"
    finally:
        monitor.close(2)
        _owner_call(owner, guard.release)
