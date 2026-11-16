"""Synthetic Gateway owner startup: bounded binding and retained cleanup."""
import threading
import time

import pytest
from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

import tests.test_iter63_ctp_gateway_trading as fixtures
from bt_api_py._gateway_owner_guard_binding import _GatewayOwnerGuardBinding
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayOwnerServiceCallError, GatewayOwnerServiceCallHandle
from bt_api_py.service_host import ServiceHost, ServiceHostError
from tests.unit.test_iter64_gateway_host_guard_binding import FakeListener, profile


def test_host_waits_for_owner_maintenance_within_configured_queue_budget(tmp_path, monkeypatch):
    entered, release, armed = threading.Event(), threading.Event(), threading.Event()
    old_refresh = fixtures.FakeSession.refresh_session
    old_bind = ServiceHost._bind_gateway_owner_guard
    old_result = GatewayOwnerServiceCallHandle.result
    seen = []

    def refresh(self):
        if armed.is_set() and not entered.is_set():
            entered.set()
            assert release.wait(2)
        return old_refresh(self)

    def bind(self, *args):
        armed.set()
        assert entered.wait(2)
        timer = threading.Timer(0.15, release.set)
        timer.start()
        try:
            return old_bind(self, *args)
        finally:
            timer.join()

    def scaled(self, *, timeout_seconds=5.0):
        if getattr(self._task.callback, '__name__', '') == 'bind_on_owner':
            seen.append(timeout_seconds)
            # Scale the old hard-coded five-second budget to 50 ms; the real
            # owner queue budget in this fixture remains 30 seconds.
            timeout_seconds = 0.05 if timeout_seconds == 5.0 else timeout_seconds
        return old_result(self, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(fixtures.FakeSession, 'refresh_session', refresh)
    monkeypatch.setattr(ServiceHost, '_bind_gateway_owner_guard', bind)
    monkeypatch.setattr(GatewayOwnerServiceCallHandle, 'result', scaled)
    built, _, _, _, _, _ = profile(tmp_path, monkeypatch)
    deployment = built.deployment_factory()
    try:
        deployment.start()
        host = deployment.service_host
        owner = host._gateway_owner_guard_bindings['gateway-a'][2]
        assert seen == [owner.service_call_queue_timeout_seconds] == [30.0]
        with pytest.raises(AttributeError):
            owner.service_call_queue_timeout_seconds = 1.0
        assert len(FakeListener.instances) == 1
        assert host._registry.ready is True
    finally:
        release.set()
        deployment.close()


@pytest.mark.parametrize('kind,cancel_succeeds', [('queued', True), ('queued', False), ('running', False)])
def test_timeout_classification_and_no_late_publication(tmp_path, monkeypatch, kind, cancel_succeeds):
    entered, release, armed = threading.Event(), threading.Event(), threading.Event()
    old_refresh = fixtures.FakeSession.refresh_session
    old_bind = ServiceHost._bind_gateway_owner_guard
    old_check = _GatewayOwnerGuardBinding.assert_bound
    old_result = GatewayOwnerServiceCallHandle.result
    cancellations = []
    old_cancel = GatewayOwnerServiceCallHandle.cancel

    def refresh(self):
        if kind == 'queued' and armed.is_set() and not entered.is_set():
            entered.set()
            assert release.wait(3)
        return old_refresh(self)

    def bind(self, *args):
        if kind == 'queued':
            armed.set()
            assert entered.wait(2)
        return old_bind(self, *args)

    def check(self, owner, journal):
        if kind == 'running':
            entered.set()
            assert release.wait(3)
        return old_check(self, owner, journal)

    def short(self, *, timeout_seconds=5.0):
        if getattr(self._task.callback, '__name__', '') == 'bind_on_owner':
            return old_result(self, timeout_seconds=0.05)
        return old_result(self, timeout_seconds=timeout_seconds)

    def cancel(self):
        result = old_cancel(self) if cancel_succeeds else False
        cancellations.append(result)
        return result

    monkeypatch.setattr(fixtures.FakeSession, 'refresh_session', refresh)
    monkeypatch.setattr(ServiceHost, '_bind_gateway_owner_guard', bind)
    monkeypatch.setattr(_GatewayOwnerGuardBinding, 'assert_bound', check)
    monkeypatch.setattr(GatewayOwnerServiceCallHandle, 'result', short)
    monkeypatch.setattr(GatewayOwnerServiceCallHandle, 'cancel', cancel)
    built, settings, _, _, _, _ = profile(tmp_path, monkeypatch)
    deployment = built.deployment_factory()
    host = deployment.service_host
    contender = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, 'other')
    try:
        with pytest.raises(ServiceHostError, match='START_CLEANUP_FAILED'):
            deployment.start()
        assert entered.is_set() and not host._gateway_owner_guard_active
        assert host._start_failure == 'GATEWAY_OWNER_GUARD_TIMEOUT_' + kind.upper()
        assert cancellations == ([cancel_succeeds] if kind == 'queued' else [])
        assert not host._gateway_owner_guard_bindings and not FakeListener.instances
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
    finally:
        release.set()
        deployment.close()
    assert not host._gateway_owner_guard_bindings
    contender.acquire()
    contender.release()


@pytest.mark.parametrize('error,expected', [
    ('SERVICE_CALL_EXPIRED', 'GATEWAY_OWNER_GUARD_EXPIRED'),
    ('SERVICE_CALL_NOT_READY', 'GATEWAY_OWNER_GUARD_NOT_READY'),
    ('SERVICE_CALL_QUEUE_FULL', 'GATEWAY_OWNER_GUARD_QUEUE_FULL'),
    ('SERVICE_CALL_FAILED', 'GATEWAY_OWNER_GUARD_BINDING_INVALID'),
])
def test_finite_service_failures_and_unknown_remain_distinct(tmp_path, monkeypatch, error, expected):
    built, _, _, _, _, _ = profile(tmp_path, monkeypatch)
    deployment = built.deployment_factory()
    original = GatewayDirectOwner.submit_service_call

    def submit(owner, callback):
        if getattr(callback, '__name__', '') == 'bind_on_owner':
            raise GatewayOwnerServiceCallError(error)
        return original(owner, callback)

    monkeypatch.setattr(GatewayDirectOwner, 'submit_service_call', submit)
    try:
        with pytest.raises(ServiceHostError, match='^' + expected + '$'):
            deployment.start()
        assert not deployment.service_host._gateway_owner_guard_bindings
        assert not FakeListener.instances
    finally:
        deployment.close()
