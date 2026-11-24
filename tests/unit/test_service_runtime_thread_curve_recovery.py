"""Opted-in CURVE rollover for borrowed Execution and Monitor reads."""

from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py import service_runtime as service_runtime_module
from bt_api_py import service_runtime_thread as runtime_thread_module
from bt_api_py.execution_management_read_grant import PARENT_DISCOVER_RECENT_V1, PARENT_READ_V1
from bt_api_py.execution_management_read_client import ExecutionManagementReadClient
from bt_api_py.service_runtime import (
    BoundExecutionManagementReadTarget,
    BoundMonitorQueryTarget,
    ServiceRuntimeRequestNotSentError,
)
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime


_APP = WireIdentity(WireRole.APPLICATION_CLIENT, "curve-app", "curve-actor")
_PEER = WireIdentity(WireRole.SERVICE, "curve-service-principal", "curve-service")
_SCOPE = WireScope("paper:curve-recovery", account_ref="synthetic-account",
                   strategy_ref="synthetic-strategy")
_FINGERPRINT = hashlib.sha256(b"synthetic-server-key").hexdigest()
_OPERATION = "observation.query"


class _SyntheticServiceClientRuntime:
    """Owner-thread fake with explicit unregister and socket-generation facts."""

    def __init__(self, index: int, *, remaining: int, mount: str):
        self.index = index
        self._state = "new"
        self._require_unregister_ack = True
        self._monotonic_clock = runtime_thread_module._BOUND_MONOTONIC
        self.identity = _APP
        self.run_id = f"runtime-{index}"
        self.peer = _PEER
        self.remaining = remaining
        self.retire_after_call = False
        self.close_uncertain = False
        self.close_calls = 0
        self.calls = []
        self._binding = SimpleNamespace(
            registration_id=f"registration-{index}",
            service_epoch=f"epoch-{index}",
            active=True,
            required=True,
            mount=mount,
            service_id="curve-service",
            instance_id="curve-instance",
            scope_ref="curve-scope",
            scope=_SCOPE,
            operations=frozenset({_OPERATION, PARENT_READ_V1}),
            lease_deadline=runtime_thread_module._BOUND_MONOTONIC() + 3600,
        )
        self._cleanup_failures = 0
        self._unregister_uncertainties = 0
        self._unknown_registration_count = 0

    @property
    def state(self):
        return self._state

    @property
    def bindings(self):
        return (self._binding,) if self._state == "registered" else ()

    @property
    def healthy(self):
        return self._state == "registered"

    @property
    def cleanup_failures(self):
        return self._cleanup_failures

    @property
    def unregister_uncertainties(self):
        return self._unregister_uncertainties

    @property
    def unknown_registration_count(self):
        return self._unknown_registration_count

    @property
    def open_transport_count(self):
        return 1 if self._state == "registered" else 0

    @property
    def min_socket_message_ids_remaining(self):
        return self.remaining if self._state == "registered" else None

    def authenticated_peer_for_binding(self, _binding):
        if self.peer is None:
            return None, None
        return self.peer, _FINGERPRINT

    def start(self):
        assert self._state == "new"
        self._state = "registered"
        return self.bindings

    def maintain(self):
        assert self._state == "registered"

    def next_maintenance_delay(self):
        return 1.0

    def call_bound(self, target, operation, arguments, *, deadline_monotonic):
        assert self._state == "registered"
        assert target.runtime_run_id == self.run_id
        assert target.registration_id == self._binding.registration_id
        assert target.service_epoch == self._binding.service_epoch
        assert operation in self._binding.operations
        assert isinstance(arguments, dict)
        assert deadline_monotonic > runtime_thread_module._BOUND_MONOTONIC()
        self.calls.append((target, operation, arguments))
        if self.retire_after_call:
            self.peer = None
            self.retire_after_call = False
        if (type(target) is BoundExecutionManagementReadTarget
                and type(arguments) is dict and "query_id" in arguments):
            from bt_api_py.execution_management_read_client import _FIELDS
            snapshot = {field: None for field in _FIELDS}
            snapshot.update(status="UNKNOWN", source="LOCAL_JOURNAL")
            return {"schema_version": 1, "snapshot": snapshot}
        return {"runtime_index": self.index}

    def close(self):
        self.close_calls += 1
        if self.close_uncertain:
            self._unregister_uncertainties = 1
            self._state = "close_failed"
            raise RuntimeError("synthetic unregister uncertainty")
        self._state = "closed"
        self.remaining = None
        return None


def _install_synthetic_factory(monkeypatch, *, first_remaining=4096, mount="application.monitor"):
    created = []

    def factory():
        index = len(created)
        runtime = _SyntheticServiceClientRuntime(
            index, remaining=first_remaining if index == 0 else 4096, mount=mount
        )
        created.append(runtime)
        return runtime

    monkeypatch.setattr(service_runtime_module, "ServiceClientRuntime", _SyntheticServiceClientRuntime)
    monkeypatch.setattr(runtime_thread_module, "_bound_clock_current", lambda: True)
    return factory, created


def _target(kind, runtime):
    common = (
        "curve-service", "curve-instance", "curve-scope", _SCOPE, _APP,
        runtime.run_id, "curve-transport", "curve-security",
        "tcp://127.0.0.1:59991", _PEER, _FINGERPRINT,
        runtime._binding.registration_id, runtime._binding.service_epoch,
    )
    if kind is BoundMonitorQueryTarget:
        return kind("application.monitor", "service.monitor_query", *common)
    return kind("application.execution_management", "service.execution_management_read", *common)


def _operation(kind):
    return _OPERATION if kind is BoundMonitorQueryTarget else PARENT_READ_V1


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


@pytest.mark.parametrize("target_type", [BoundExecutionManagementReadTarget, BoundMonitorQueryTarget])
def test_retired_socket_call_is_not_replayed_and_next_call_uses_successor(
    monkeypatch, target_type
):
    factory, created = _install_synthetic_factory(
        monkeypatch,
        mount=("application.monitor" if target_type is BoundMonitorQueryTarget
               else "application.execution_management"),
    )
    wrapper = ThreadedServiceClientRuntime(
        factory,
        require_unregister_ack=True,
        recover_on_socket_retirement=True,
    )
    wrapper.start(timeout_seconds=2)
    original_target = _target(target_type, created[0])
    created[0].retire_after_call = True

    first = wrapper.call_bound(
        original_target, _operation(target_type), {"request_id": "first"}, timeout_seconds=2
    )
    assert first == {"runtime_index": 0}
    _wait_until(lambda: len(created) == 2 and wrapper.status().run_id == "runtime-1")

    # The same trusted target can be reused. The wrapper refreshes only the
    # runtime-owned run id/registration/epoch fields for the successor.
    second = wrapper.call_bound(
        original_target, _operation(target_type), {"request_id": "second"}, timeout_seconds=2
    )
    assert second == {"runtime_index": 1}
    assert len(created[0].calls) == 1
    assert len(created[1].calls) == 1
    assert created[0].close_calls == 1
    assert created[1].calls[0][0].runtime_run_id == "runtime-1"
    assert created[1].calls[0][0].registration_id == "registration-1"
    assert created[1].calls[0][0].service_epoch == "epoch-1"
    wrapper.close(timeout_seconds=2)
    assert created[1].close_calls == 1


def test_low_socket_budget_rotates_before_dispatch(monkeypatch):
    factory, created = _install_synthetic_factory(monkeypatch, first_remaining=32)
    wrapper = ThreadedServiceClientRuntime(
        factory,
        require_unregister_ack=True,
        recover_on_socket_retirement=True,
    )
    snapshot = wrapper.start(timeout_seconds=2)
    assert len(created) == 2
    assert created[0].close_calls == 1
    assert created[0].calls == []
    assert snapshot.run_id == "runtime-1"
    assert snapshot.min_socket_message_ids_remaining == 4096
    wrapper.close(timeout_seconds=2)


@pytest.mark.parametrize("originally_granted", [True, False])
def test_first_read_after_rotation_requires_its_original_operation_grant(monkeypatch, originally_granted):
    factory, created = _install_synthetic_factory(monkeypatch, mount="application.execution_management")

    def with_discovery_grant():
        runtime = factory()
        if originally_granted or runtime.index == 1:
            runtime._binding.operations |= {PARENT_DISCOVER_RECENT_V1}
        return runtime

    wrapper = ThreadedServiceClientRuntime(
        with_discovery_grant,
        require_unregister_ack=True,
        recover_on_socket_retirement=True,
    )
    wrapper.start(timeout_seconds=2)
    try:
        target = _target(BoundExecutionManagementReadTarget, created[0])
        created[0].retire_after_call = True
        assert wrapper.call_bound(target, PARENT_READ_V1, {}, timeout_seconds=2) == {"runtime_index": 0}
        _wait_until(lambda: wrapper.status().run_id == "runtime-1")

        if originally_granted:
            assert wrapper.call_bound(target, PARENT_DISCOVER_RECENT_V1, {}, timeout_seconds=2) == {
                "runtime_index": 1
            }
            assert [call[1] for call in created[1].calls] == [PARENT_DISCOVER_RECENT_V1]
        else:
            with pytest.raises(ServiceRuntimeRequestNotSentError):
                wrapper.call_bound(target, PARENT_DISCOVER_RECENT_V1, {}, timeout_seconds=2)
            assert created[1].calls == []
        assert [call[1] for call in created[0].calls] == [PARENT_READ_V1]
    finally:
        wrapper.close(timeout_seconds=2)


def test_execution_read_adapter_rebinds_successor_without_replaying_query(monkeypatch):
    factory, created = _install_synthetic_factory(monkeypatch, mount="application.execution_management")
    wrapper = ThreadedServiceClientRuntime(
        factory,
        require_unregister_ack=True,
        recover_on_socket_retirement=True,
    )
    wrapper.start(timeout_seconds=2)
    target = _target(BoundExecutionManagementReadTarget, created[0])
    forged_client = ExecutionManagementReadClient(
        wrapper,
        target=replace(
            target,
            runtime_run_id="forged-runtime",
            registration_id="forged-registration",
            service_epoch="forged-epoch",
        ),
        grant_revision="synthetic-grant-revision",
    )
    forged = forged_client.query("synthetic-parent", query_id="forged-query")
    assert forged.status == "UNKNOWN"
    assert created[0].calls == []

    client = ExecutionManagementReadClient(
        wrapper, target=target, grant_revision="synthetic-grant-revision"
    )
    created[0].retire_after_call = True

    first = client.query("synthetic-parent", query_id="query-before-rotation")
    assert first.status == "UNKNOWN"
    _wait_until(lambda: len(created) == 2 and wrapper.status().run_id == "runtime-1")
    second = client.query("synthetic-parent", query_id="query-after-rotation")

    assert second.status == "UNKNOWN"
    assert [call[2]["query_id"] for call in created[0].calls] == ["query-before-rotation"]
    assert [call[2]["query_id"] for call in created[1].calls] == ["query-after-rotation"]
    assert created[0].close_calls == 1
    assert created[0].unregister_uncertainties == 0
    wrapper.close(timeout_seconds=2)


def test_unregister_uncertainty_retains_old_runtime_without_successor(monkeypatch):
    factory, created = _install_synthetic_factory(monkeypatch)
    wrapper = ThreadedServiceClientRuntime(
        factory,
        require_unregister_ack=True,
        recover_on_socket_retirement=True,
    )
    sticky_observations = []

    class _StickyObserved(BaseException):
        pass

    def intercept_sticky(runtime, extension, error):
        with wrapper._condition:
            wrapper._cleanup_sticky = True
            wrapper._sticky_resources = (runtime, extension)
            wrapper._sticky_errors.append(error)
            wrapper._stop = True
            wrapper._state = "close_failed"
            wrapper._healthy = False
            wrapper._condition.notify_all()
        sticky_observations.append(runtime)
        raise _StickyObserved()

    wrapper._enter_sticky = intercept_sticky
    wrapper.start(timeout_seconds=2)
    target = _target(BoundMonitorQueryTarget, created[0])
    created[0].retire_after_call = True
    created[0].close_uncertain = True

    assert wrapper.call_bound(target, _OPERATION, {"request_id": "once"}, timeout_seconds=2) == {
        "runtime_index": 0
    }
    _wait_until(lambda: bool(sticky_observations))
    assert sticky_observations == [created[0]]
    assert created[0].close_calls == 1
    assert len(created) == 1
    assert wrapper._sticky_resources[0] is created[0]


def test_socket_recovery_requires_strict_unregister_and_bound_targets(monkeypatch):
    factory, _created = _install_synthetic_factory(monkeypatch)
    with pytest.raises(ValueError, match="invalid socket recovery policy"):
        ThreadedServiceClientRuntime(factory, recover_on_socket_retirement=True)

    wrapper = ThreadedServiceClientRuntime(
        factory,
        require_unregister_ack=True,
        recover_on_socket_retirement=True,
    )
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        wrapper.call("application.monitor", _OPERATION, {})
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        wrapper.call_bound("application.monitor", _OPERATION, {})
