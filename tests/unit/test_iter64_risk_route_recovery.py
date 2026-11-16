"""Iteration 64: replacing a Risk runtime after a Risk service restart.

UNIT/FAKE only.  The client tests replace runtime status/start/close on a real
``ThreadedServiceClientRuntime`` that is never started; no socket, Risk host,
Gateway or account exists.  The evidence tests at the end drive the real SDK
runtime against an in-process synthetic Registry transport.

The contract under test: a binding refused by the registry on renewal
(``STALE_EPOCH``/``NOT_REGISTERED``) leaves an unacknowledged unregister behind.
A new registration may replace it only when the registry durably retired the old
pair, the old writer and bindings are gone, and the new registration is fresh;
nothing in flight is ever resent.  A timeout, an elapsed lease, or a rejected
unregister alone stays refused.
"""

from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireEnvelope, WireMessageKind

from bt_api_py.risk_service_client import (
    RiskRouteRecovery,
    RiskServiceClient,
    _recovery_observation,
)
from bt_api_py.service_runtime_thread import (
    BindingSnapshot,
    RuntimeSnapshot,
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
)
from tests.unit.test_iter64_risk_service_client import OPS, risk_route

IDENTITY = risk_route().identity
RUN_ID = risk_route().scope.run_id


# --- fake runtimes ---------------------------------------------------------------------


class FakeRuntime:
    """A real wrapper whose reads are replaced; it owns no thread or transport."""

    def __init__(self, monkeypatch, initial: RuntimeSnapshot):
        self.runtime = ThreadedServiceClientRuntime(lambda: None, require_unregister_ack=True)
        self.box = {"value": initial}
        self.calls: list[str] = []
        monkeypatch.setattr(self.runtime, "status", lambda: self.box["value"])
        monkeypatch.setattr(self.runtime, "bounded_status", lambda **_: self.box["value"])
        monkeypatch.setattr(
            self.runtime,
            "cleanup_observation",
            lambda **_: (self.box["value"], False),
        )
        monkeypatch.setattr(self.runtime, "call", self._business_call)
        monkeypatch.setattr(self.runtime, "start", self._start)
        monkeypatch.setattr(self.runtime, "start_bounded_once", self._bounded_start)
        monkeypatch.setattr(self.runtime, "close", self._close)
        monkeypatch.setattr(self.runtime, "close_once_if_state", self._close)
        self.allowed_start = None

    def _business_call(self, *_args, **_kwargs):
        self.calls.append("call")
        raise AssertionError("recovery must never send a business request")

    def _start(self, **_kwargs):
        self.calls.append("start")
        if self.allowed_start is not None:
            self.box["value"] = self.allowed_start
            return self.box["value"]
        raise ThreadedRuntimeError("NOT_NEW")

    def _bounded_start(self, **kwargs):
        return self._start(**kwargs)

    def _close(self, **_kwargs):
        self.calls.append("close")
        self.box["value"] = replace(
            self.box["value"],
            state="closed",
            healthy=False,
            thread_alive=False,
            bindings=(),
            unregister_uncertainties=0,
        )
        return self.box["value"]


def snapshot(
    route,
    *,
    state="running",
    thread_alive=None,
    healthy=None,
    registration="reg-2",
    epoch="epoch-2",
    bindings="default",
    error_code=None,
    cleanup_failures=0,
    unregister_uncertainties=0,
    unresolved=None,
    remote=None,
    maintenance_code=None,
    unknown_registration_count=0,
    owner_cleanup_uncertainties=0,
    strict_unregister_ack=True,
    open_transport_count=0,
    identity=None,
    run_id=None,
) -> RuntimeSnapshot:
    if bindings == "default":
        bindings = (
            (
                BindingSnapshot(
                    registration,
                    epoch,
                    True,
                    route.mount,
                    route.service_id,
                    route.instance_id,
                    route.scope_ref,
                    route.scope,
                    frozenset(OPS),
                    True,
                ),
            )
            if state == "running"
            else ()
        )
    return RuntimeSnapshot(
        state=state,
        thread_alive=state == "running" if thread_alive is None else thread_alive,
        healthy=state == "running" if healthy is None else healthy,
        error_code=error_code,
        bindings=bindings,
        cleanup_failures=cleanup_failures,
        unregister_uncertainties=unregister_uncertainties,
        identity=IDENTITY if identity is None else identity,
        run_id=RUN_ID if run_id is None else run_id,
        owner_cleanup_uncertainties=owner_cleanup_uncertainties,
        unknown_registration_count=unknown_registration_count,
        strict_unregister_ack=strict_unregister_ack,
        maintenance_code=maintenance_code,
        unresolved_registration_ids=unresolved,
        remote_inactive_registrations=remote,
        open_transport_count=open_transport_count,
    )


def stale_failure(route, **overrides):
    """A failed runtime after the registry retired its only binding on renewal."""
    values = dict(
        state="failed",
        thread_alive=False,
        healthy=False,
        bindings=(),
        error_code="MAINTENANCE_FAILED",
        cleanup_failures=1,
        unregister_uncertainties=1,
        unresolved=("reg-1",),
        remote=(("reg-1", "epoch-1"),),
        maintenance_code="STALE_EPOCH",
    )
    values.update(overrides)
    return snapshot(route, **values)


def candidate_snapshot(route, *, registration="reg-2", epoch="epoch-2"):
    return snapshot(route, registration=registration, epoch=epoch)


def compose_client(monkeypatch, route, old_snapshot, *, clock=None, candidate=None):
    old = FakeRuntime(monkeypatch, old_snapshot)
    fresh = FakeRuntime(
        monkeypatch,
        candidate if candidate is not None else candidate_snapshot(route),
    )
    fresh.allowed_start = fresh.box["value"]
    client = RiskServiceClient(old.runtime, route=route, monotonic_clock=clock or (lambda: 100.0))
    client._factory = lambda: fresh.runtime
    return client, old, fresh


# --- allowed recovery ------------------------------------------------------------------


def test_stale_epoch_with_recorded_unregister_uncertainty_allows_replacement(monkeypatch):
    route = risk_route()
    client, old, fresh = compose_client(monkeypatch, route, stale_failure(route))

    assert client.reconnect(timeout_seconds=1) is True
    assert client._runtime is fresh.runtime and client._pending_runtime is None
    assert client.available is True
    # The retired runtime is inspected and never sent a business request.
    assert old.calls == [] and fresh.calls == ["start"]
    assert client._last_recovery == RiskRouteRecovery(
        True,
        "ROUTE_RECOVERY_READY",
        "STALE_EPOCH",
        unresolved_registrations=(("reg-1", "epoch-1"),),
    )


def test_not_registered_renewal_is_the_same_durable_proof(monkeypatch):
    route = risk_route()
    client, _old, fresh = compose_client(
        monkeypatch, route, stale_failure(route, maintenance_code="NOT_REGISTERED")
    )
    assert client.reconnect(timeout_seconds=1) is True
    assert client._runtime is fresh.runtime and client.available


def test_recovery_status_reports_ready_without_changing_the_runtime(monkeypatch):
    route = risk_route()
    client, old, fresh = compose_client(monkeypatch, route, stale_failure(route))
    observation = client.route_recovery_status()
    assert observation.replaceable is True
    assert observation.code == "ROUTE_RECOVERY_READY"
    assert observation.reason == "STALE_EPOCH" and observation.missing == ()
    assert observation.unresolved_registrations == (("reg-1", "epoch-1"),)
    assert client._runtime is old.runtime and fresh.calls == []
    assert client.runtime_status().state == "failed"


def test_status_keeps_route_unavailable_and_attaches_the_recovery_evidence(monkeypatch):
    route = risk_route()
    client, old, fresh = compose_client(monkeypatch, route, stale_failure(route))
    result = client.status("a-key", timeout_seconds=1)
    # The existing public code is unchanged; the detail is additive.
    assert result.outcome == "NOT_SENT" and result.code == "ROUTE_UNAVAILABLE"
    assert result.detail is not None and result.detail.replaceable is True
    assert result.detail.reason == "STALE_EPOCH"
    assert old.calls == [] and fresh.calls == []


def test_status_reports_missing_evidence_for_an_unproven_failure(monkeypatch):
    route = risk_route()
    client, _old, fresh = compose_client(monkeypatch, route, stale_failure(route, remote=()))
    result = client.status("a-key", timeout_seconds=1)
    assert result.outcome == "NOT_SENT" and result.code == "ROUTE_UNAVAILABLE"
    assert result.detail.replaceable is False
    assert "UNRESOLVED_REGISTRATION_UNPROVEN" in result.detail.missing
    assert fresh.calls == []


def test_running_runtime_needs_no_replacement(monkeypatch):
    route = risk_route()
    client, old, fresh = compose_client(
        monkeypatch, route, candidate_snapshot(route, registration="reg-1", epoch="epoch-1")
    )
    observation = client.route_recovery_status()
    assert observation.replaceable is False
    assert observation.code == "ROUTE_RECOVERY_NOT_REQUIRED"
    assert client.reconnect(timeout_seconds=1) is True
    assert client._runtime is old.runtime and fresh.calls == []


# --- refusals: the old registration is not durably retired -----------------------------


@pytest.mark.parametrize(
    ("overrides", "token"),
    [
        (dict(maintenance_code="REQUIRED_LEASE_EXPIRED"), "MAINTENANCE_CAUSE_UNPROVEN"),
        (dict(maintenance_code="SERVICE_UNAVAILABLE"), "MAINTENANCE_CAUSE_UNPROVEN"),
        (dict(remote=()), "UNRESOLVED_REGISTRATION_UNPROVEN"),
        (dict(remote=(("other-reg", "other-epoch"),)), "UNRESOLVED_REGISTRATION_UNPROVEN"),
    ],
    ids=["lease-expiry", "transport-timeout", "no-server-proof", "wrong-registration"],
)
def test_failure_without_a_durable_registry_refusal_stays_refused(monkeypatch, overrides, token):
    route = risk_route()
    client, old, fresh = compose_client(monkeypatch, route, stale_failure(route, **overrides))

    assert client.reconnect(timeout_seconds=1) is False
    assert client._runtime is old.runtime and fresh.calls == []
    assert client._runtime_cleanup_uncertain is True
    observation = client.route_recovery_status()
    assert observation.replaceable is False and observation.code == "ROUTE_RECOVERY_UNPROVEN"
    assert token in observation.missing


def test_unrecorded_unregister_uncertainty_stays_refused(monkeypatch):
    route = risk_route()
    # The counter says one release is uncertain, but no registration id is recorded.
    client, old, fresh = compose_client(
        monkeypatch, route, stale_failure(route, unresolved=None, remote=())
    )
    assert client.reconnect(timeout_seconds=1) is False
    assert client._runtime is old.runtime and fresh.calls == []
    assert "UNREGISTER_UNCERTAINTY_UNRECORDED" in client.route_recovery_status().missing


def test_count_mismatch_stays_refused(monkeypatch):
    route = risk_route()
    client, _old, fresh = compose_client(
        monkeypatch, route, stale_failure(route, unregister_uncertainties=2)
    )
    assert client.reconnect(timeout_seconds=1) is False
    assert fresh.calls == []
    assert "UNREGISTER_UNCERTAINTY_UNRECORDED" in client.route_recovery_status().missing


@pytest.mark.parametrize(
    ("overrides", "token"),
    [
        (dict(unknown_registration_count=1), "UNKNOWN_REGISTRATION"),
        (dict(owner_cleanup_uncertainties=1), "OWNER_CLEANUP_UNKNOWN"),
        (dict(strict_unregister_ack=None), "CLEANUP_POLICY_UNPROVEN"),
        (dict(cleanup_failures=None), "CLEANUP_STATE_UNPROVEN"),
        (dict(bindings=(SimpleNamespace(),)), "OLD_BINDING_RETAINED"),
        (dict(open_transport_count=1), "OLD_TRANSPORT_RETAINED"),
        (dict(open_transport_count=None), "OLD_TRANSPORT_RETAINED"),
    ],
    ids=[
        "unknown-registration",
        "owner-cleanup",
        "policy",
        "cleanup-state",
        "retained-binding",
        "open-transport",
        "transport-evidence",
    ],
)
def test_unproven_cleanup_state_stays_refused(monkeypatch, overrides, token):
    route = risk_route()
    client, old, fresh = compose_client(monkeypatch, route, stale_failure(route, **overrides))
    assert client.reconnect(timeout_seconds=1) is False
    assert client._runtime is old.runtime and fresh.calls == []
    assert token in client.route_recovery_status().missing


def test_route_identity_or_run_mismatch_stays_refused(monkeypatch):
    route = risk_route()
    for overrides in (
        dict(identity=replace(IDENTITY, actor_ref="someone-else")),
        dict(run_id="another-run"),
    ):
        client, old, fresh = compose_client(monkeypatch, route, stale_failure(route, **overrides))
        assert client.reconnect(timeout_seconds=1) is False
        assert client._runtime is old.runtime and fresh.calls == []
        assert "ROUTE_IDENTITY_MISMATCH" in client.route_recovery_status().missing


# --- a fresh registration under the original identity ----------------------------------


def test_new_registration_must_not_reuse_the_retired_pair(monkeypatch):
    route = risk_route()
    client, _old, fresh = compose_client(
        monkeypatch,
        route,
        stale_failure(route),
        candidate=candidate_snapshot(route, registration="reg-1", epoch="epoch-1"),
    )
    assert client.reconnect(timeout_seconds=1) is False
    # The candidate was installed before its registration could be checked, so
    # the client refuses to use it and requires an explicit close.
    assert client._runtime is fresh.runtime
    assert client._close_required is True and client.available is False
    assert client._last_recovery is not None
    assert client._last_recovery.missing == ("FRESH_REGISTRATION_UNPROVEN",)


def test_same_registration_id_with_a_new_epoch_still_overlaps_the_old_writer(monkeypatch):
    route = risk_route()
    client, _old, fresh = compose_client(
        monkeypatch,
        route,
        stale_failure(route),
        candidate=candidate_snapshot(route, registration="reg-1", epoch="epoch-9"),
    )
    assert client.reconnect(timeout_seconds=1) is False
    assert client.available is False and fresh.calls == ["start"]
    assert client._last_recovery.missing == ("FRESH_REGISTRATION_UNPROVEN",)


# --- a worker still cleaning up is not poisoned ----------------------------------------


def test_transient_cleanup_observation_is_retried_not_poisoned(monkeypatch):
    route = risk_route()
    clock = {"now": 100.0}
    in_progress = stale_failure(route, thread_alive=True)
    client, old, fresh = compose_client(monkeypatch, route, in_progress, clock=lambda: clock["now"])

    assert client.reconnect(timeout_seconds=1) is False
    assert client._runtime_cleanup_uncertain is False
    assert client.route_recovery_status().code == "ROUTE_RECOVERY_IN_PROGRESS"
    old.box["value"] = stale_failure(route)
    clock["now"] += 2.0
    assert client.reconnect(timeout_seconds=1) is True
    assert client._runtime is fresh.runtime and client.available


def test_final_unproven_refusal_is_sticky(monkeypatch):
    route = risk_route()
    client, old, fresh = compose_client(monkeypatch, route, stale_failure(route, remote=()))
    assert client.reconnect(timeout_seconds=1) is False
    assert client._runtime_cleanup_uncertain is True
    before = dict(old.box["value"].__dict__)
    assert client.reconnect(timeout_seconds=1) is False
    assert old.box["value"].__dict__ == before and fresh.calls == []
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED"):
        client.close(timeout_seconds=1)


# --- real SDK runtime: the evidence the client reads -----------------------------------


def _synthetic_runtime(tmp_path, *, renew=None, unregister=None):
    """A real ThreadedServiceClientRuntime over an in-process fake transport.

    ``renew``/``unregister`` select ``"reject:CODE"`` or ``"timeout"`` for that
    operation; anything else is served by the synthetic Registry.
    """
    import time

    from tests.unit.test_service_runtime import (
        LocalTransport,
        ManualClock,
        PEER,
        _config,
        _registry,
        _runtime,
    )

    clock = ManualClock(1000)
    operations = []
    actions = {"service.renew": renew, "service.unregister": unregister}

    def handle(operation, request):
        action = actions.get(operation)
        if action == "timeout":
            raise TimeoutError("synthetic transport timeout")
        if isinstance(action, str) and action.startswith("reject:"):
            header = replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="rejected",
                correlation_id=request.header.message_id,
                identity=PEER,
            )
            return WireEnvelope(header, {"ok": False, "code": action.split(":", 1)[1]})
        return None

    def factory():
        registry = _registry(tmp_path / "registry.sqlite", [])

        class Transport(LocalTransport):
            def request(self, request, *, timeout):
                operations.append(request.header.operation)
                synthetic = handle(request.header.operation, request)
                if synthetic is not None:
                    return synthetic
                return super().request(request, timeout=timeout)

            def close(self):
                super().close()
                registry.close()

        return _runtime(
            _config(),
            lambda *_: Transport(registry),
            monotonic_clock=clock,
            require_unregister_ack=True,
        )

    owner = ThreadedServiceClientRuntime(
        factory, monotonic_clock=clock, require_unregister_ack=True
    )
    owner.start(timeout_seconds=2)
    return owner, clock, operations


def _wait_failed(owner, time):
    deadline = time.monotonic() + 3
    while owner.status().state != "failed" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert owner.status().state == "failed"


def test_real_runtime_records_the_durable_refusal_and_unresolved_release(tmp_path):
    import time

    owner, clock, _operations = _synthetic_runtime(
        tmp_path, renew="reject:STALE_EPOCH", unregister="reject:STALE_EPOCH"
    )
    lease = owner.status().bindings[0]
    assert lease.active is True
    # Past the renewal deadline but inside the lease, so the rejection is the
    # registry's STALE_EPOCH rather than a local lease expiry.
    clock.advance(10.1)
    _wait_failed(owner, time)
    snapshot = owner.close(timeout_seconds=2)

    assert snapshot.state == "failed" and snapshot.thread_alive is False
    assert snapshot.error_code == "MAINTENANCE_FAILED"
    assert snapshot.maintenance_code == "STALE_EPOCH"
    assert snapshot.unresolved_registration_ids == (lease.registration_id,)
    assert snapshot.remote_inactive_registrations == ((lease.registration_id, lease.service_epoch),)
    assert snapshot.unregister_uncertainties == 1

    observation, unresolved, remote = _recovery_observation(
        replace(snapshot, identity=IDENTITY, run_id=RUN_ID), risk_route()
    )
    assert observation.replaceable is True, observation
    assert unresolved == frozenset({lease.registration_id})
    assert remote == frozenset({(lease.registration_id, lease.service_epoch)})


def test_real_transport_timeout_never_records_a_durable_refusal(tmp_path):
    import time

    owner, clock, _operations = _synthetic_runtime(tmp_path, renew="timeout", unregister="timeout")
    clock.advance(40.0)
    _wait_failed(owner, time)
    snapshot = owner.close(timeout_seconds=2)

    assert snapshot.state == "failed" and snapshot.thread_alive is False
    assert snapshot.remote_inactive_registrations == ()
    assert snapshot.maintenance_code == "REQUIRED_LEASE_EXPIRED"
    assert snapshot.unresolved_registration_ids
    observation, _, _ = _recovery_observation(
        replace(snapshot, identity=IDENTITY, run_id=RUN_ID), risk_route()
    )
    assert observation.replaceable is False
    assert "MAINTENANCE_CAUSE_UNPROVEN" in observation.missing
