"""Local owner lifecycle observations with fake listeners and synthetic resources."""

import threading
from dataclasses import replace

import pytest
from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

import tests.test_iter63_ctp_gateway_trading as fixtures
from bt_api_py.gateway_direct_owner import GatewayDirectOwnerBinding, GatewayDirectOwnerError
from bt_api_py.service_host import ServiceHostError
from bt_api_py.service_registry import ServiceRegistry
from tests.test_iter63_gateway_direct_owner import Harness, wait_for
from tests.unit.test_iter63_gateway_owner_service_calls import owner_for
from tests.unit.test_iter64_gateway_host_guard_binding import FakeListener, profile


@pytest.mark.parametrize("phase", ["before", "during", "after", "ready", "published"])
def test_owner_close_at_listener_boundaries(tmp_path, monkeypatch, phase):
    built, _, _, _, _, _ = profile(tmp_path, monkeypatch)
    deployment = built.deployment_factory()
    host = deployment.service_host
    entered, release = threading.Event(), threading.Event()
    errors = []
    original_check = host._check_gateway_owner_lifecycles
    original_init = FakeListener.__init__
    original_set_ready = ServiceRegistry.set_ready
    ready_states = []
    count = 0

    def barrier():
        entered.set()
        assert release.wait(3)

    def check():
        nonlocal count
        count += 1
        if (phase, count) in (("before", 1), ("after", 2), ("ready", 3), ("published", 4)):
            barrier()
        return original_check()

    def init(listener, *args, **kwargs):
        original_init(listener, *args, **kwargs)
        if phase == "during":
            barrier()

    def set_ready(registry, value):
        result = original_set_ready(registry, value)
        ready_states.append(value)
        return result

    def close_owner():
        try:
            assert entered.wait(3)
            owner = host._gateway_owner_guard_bindings["gateway-a"][2]
            owner.close(0.001)
        except BaseException as exc:
            errors.append(exc)
        finally:
            release.set()

    monkeypatch.setattr(host, "_check_gateway_owner_lifecycles", check)
    monkeypatch.setattr(FakeListener, "__init__", init)
    monkeypatch.setattr(ServiceRegistry, "set_ready", set_ready)
    worker = threading.Thread(target=close_owner)
    worker.start()
    try:
        with pytest.raises(ServiceHostError, match="GATEWAY_OWNER_GUARD_BINDING_INVALID"):
            deployment.start()
        assert not errors
        assert len(FakeListener.instances) == (0 if phase == "before" else 1)
        assert host._server is None
        assert not host._gateway_owner_guard_bindings
        if phase == "published":
            assert ready_states == [True, False]
    finally:
        release.set()
        worker.join(3)
        deployment.close()
    assert not worker.is_alive()


def test_listener_close_failure_retains_component_until_retry(tmp_path, monkeypatch):
    built, settings, _, _, _, _ = profile(tmp_path, monkeypatch)
    deployment = built.deployment_factory()
    host = deployment.service_host
    original_init = FakeListener.__init__
    contender = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, "other")

    def init(listener, *args, **kwargs):
        original_init(listener, *args, **kwargs)
        FakeListener.close_allowed = False
        # An unconfirmed native close retains the guard independently.
        fixtures.FakeSession.instances[-1].close_allowed = False
        host._gateway_owner_guard_bindings["gateway-a"][2].close(0.001)

    monkeypatch.setattr(FakeListener, "__init__", init)
    try:
        with pytest.raises(ServiceHostError, match="START_CLEANUP_FAILED"):
            deployment.start()
        assert host._server is FakeListener.instances[0]
        assert host._components
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
    finally:
        FakeListener.close_allowed = True
        fixtures.FakeSession.instances[-1].close_allowed = True
        deployment.close()
    contender.acquire()
    contender.release()


def test_maintenance_fault_during_listener_construction(tmp_path, monkeypatch):
    built, _, _, _, _, _ = profile(tmp_path, monkeypatch)
    deployment = built.deployment_factory()
    host = deployment.service_host
    fault = threading.Event()
    original_refresh = fixtures.FakeSession.refresh_session
    original_init = FakeListener.__init__

    def refresh(session):
        if fault.is_set():
            raise RuntimeError("synthetic maintenance failure")
        return original_refresh(session)

    def init(listener, *args, **kwargs):
        original_init(listener, *args, **kwargs)
        owner = host._gateway_owner_guard_bindings["gateway-a"][2]
        fault.set()
        wait_for(lambda: owner.status().failure_category == "maintenance_failed")

    monkeypatch.setattr(fixtures.FakeSession, "refresh_session", refresh)
    monkeypatch.setattr(FakeListener, "__init__", init)
    try:
        with pytest.raises(ServiceHostError, match="GATEWAY_OWNER_GUARD_BINDING_INVALID"):
            deployment.start()
        assert len(FakeListener.instances) == 1
        assert host._server is None
    finally:
        deployment.close()


def test_maintenance_failure_invalidates_snapshot(tmp_path):
    harness = Harness(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def maintain():
        entered.set()
        assert release.wait(3)
        raise RuntimeError("synthetic")

    owner = owner_for(lambda: replace(harness.factory(), maintain=maintain,
                                      maintenance_interval_seconds=0.01))
    try:
        assert owner.start(2) and entered.wait(2)
        snapshot = owner._lifecycle_snapshot()
        assert owner._lifecycle_is_current(snapshot)
        release.set()
        wait_for(lambda: owner.status().failure_category == "maintenance_failed")
        assert not owner._lifecycle_is_current(snapshot)
    finally:
        release.set()
        assert owner.close(2)


def test_thread_exit_invalidates_snapshot_even_without_cleanup():
    owner = owner_for(lambda: None)
    published, release = threading.Event(), threading.Event()

    def unexpected_exit():
        with owner._condition:
            owner._binding = GatewayDirectOwnerBinding("account", "journal", "generation")
            owner._startup.set()
        published.set()
        assert release.wait(3)

    owner._run_owned = unexpected_exit
    try:
        assert owner.start(2) and published.wait(2)
        snapshot = owner._lifecycle_snapshot()
        epoch = owner._lifecycle_epoch
        release.set()
        owner._thread.join(2)
        assert not owner._thread.is_alive()
        assert owner._lifecycle_epoch > epoch
        assert not owner._lifecycle_is_current(snapshot)
        assert not owner.closed  # no invented cleanup confirmation
    finally:
        release.set()


def test_running_start_timeout_cannot_publish_lifecycle(tmp_path):
    harness = Harness(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def factory():
        entered.set()
        assert release.wait(3)
        return harness.factory()

    owner = owner_for(factory)
    try:
        assert not owner.start(0.01)
        assert entered.is_set()
        epoch = owner._lifecycle_epoch
        release.set()
        wait_for(lambda: owner.closed)
        assert owner._lifecycle_epoch > epoch
        with pytest.raises(GatewayDirectOwnerError, match="OWNER_LIFECYCLE_INVALID"):
            owner._lifecycle_snapshot()
    finally:
        release.set()
        assert owner.close(2)
