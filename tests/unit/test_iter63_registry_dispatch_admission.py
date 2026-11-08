"""Dispatch permits recheck Registry authorization at delayed journal commit."""

from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceDispatchAdmission,
    ServiceDispatchAdmissionError,
    ServiceRegistry,
)
from tests.unit.test_service_registry import (
    _business,
    _identity,
    _make_registry,
    _next_request,
    _register,
    _strategy_scope,
)


def _fixture(path=":memory:"):
    clock = [100.0]
    identity = _identity(principal_ref="principal-a", actor_ref="strategy-a")
    scope = _strategy_scope(strategy="strategy-a")
    capability = ServiceCapability(
        "gateway-a",
        "service.direct_write",
        frozenset({"direct.dispatch"}),
        frozenset({"direct.dispatch"}),
    )
    grant = ResourceGrant(
        "gateway-a",
        "service.direct_write",
        "scope-a",
        _strategy_scope(strategy="strategy-a", run_id=None),
        frozenset({"direct.dispatch"}),
    )
    registry = ServiceRegistry(
        path,
        service_id="gateway-service",
        capabilities=(capability,),
        grants={"principal-a": (PrincipalGrant(identity, (grant,)),)},
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        lease_seconds=10,
        clock=lambda: clock[0],
    )
    captured = []
    registry.register_handler(
        "gateway-a",
        "direct.dispatch",
        lambda context, args: captured.append(context) or {"status": "accepted"},
    )
    response = _register(
        registry,
        identity=identity,
        scope=scope,
        principal="principal-a",
        instance_id="gateway-a",
        port="service.direct_write",
        scope_ref="scope-a",
        operations=("direct.dispatch",),
    )
    assert response["ok"], response
    registration = response["result"]
    args = {
        "request_key": "key-a",
        "reservation_id": "r-a",
        "digest": "d-a",
        "expected_version": 1,
        "metadata": {"parts": [1, 2]},
    }
    response = registry.handle(
        "principal-a",
        _next_request(
            "direct.dispatch",
            {
                "registration_id": registration["registration_id"],
                "service_epoch": registration["service_epoch"],
                "instance_id": "gateway-a",
                "arguments": args,
            },
            identity=identity,
            scope=scope,
        ),
    )
    assert response["ok"], response
    return registry, clock, identity, scope, registration, args, captured[0]


def test_handoff_match_and_close():
    registry, _, _, _, _, args, context = _fixture()
    admission = context.dispatch_admission
    try:
        assert type(admission) is ServiceDispatchAdmission
        assert admission.matches_context(context)
        assert not admission.matches_context(replace(context, principal_ref="another"))
        assert admission.matches(args)
        assert not admission.matches(dict(args, request_key="different"))
        outcomes = []

        def owner():
            with admission.scope():
                outcomes.append("allowed")
            with admission.scope():
                outcomes.append("again")

        thread = threading.Thread(target=owner)
        thread.start()
        thread.join(2)
        assert outcomes == ["allowed", "again"]
        admission.close()
        assert not admission.matches(args)
        with pytest.raises(ServiceDispatchAdmissionError), admission.scope():
            pass
    finally:
        registry.close()


def test_non_direct_dispatch_receives_no_admission(tmp_path):
    registry = _make_registry(tmp_path / "legacy.sqlite")
    captured = []
    registry.register_handler(
        "account-instance",
        "orders.submit",
        lambda context, arguments: captured.append(context) or {"ok": True},
    )
    try:
        registration = _register(registry)["result"]
        assert _business(registry, registration)["ok"]
        assert captured[0].dispatch_admission is None
    finally:
        registry.close()


def _unregister(registry, identity, scope, registration):
    return registry.handle(
        "principal-a",
        _next_request(
            "service.unregister",
            {
                "registration_id": registration["registration_id"],
                "service_epoch": registration["service_epoch"],
            },
            identity=identity,
            scope=scope,
        ),
    )


@pytest.mark.parametrize(
    "condition",
    [
        "expired",
        "unregistered",
        "revoked",
        "run_changed",
        "stopped",
        "not_ready",
        "policy",
        "stale_epoch",
        "closed",
        "migration_disabled",
    ],
)
def test_delayed_scope_revalidates_current_authority(condition):
    registry, clock, identity, scope, registration, _, context = _fixture()
    admission = context.dispatch_admission
    try:
        with admission.scope() as recheck:
            recheck()
        if condition == "expired":
            clock[0] += 10
        elif condition == "unregistered":
            assert _unregister(registry, identity, scope, registration)["ok"]
        elif condition == "revoked":
            with registry._lock:
                registry._grants = {}
        elif condition == "run_changed":
            with registry._transaction():
                registry._db.execute("UPDATE strategy_write_claims SET run_id='different'")
        elif condition == "stopped":
            assert registry.stop_strategy_new(scope, "stop-1")["status"] == "applied"
        elif condition == "not_ready":
            registry.set_ready(False)
        elif condition == "policy":
            with registry._lock:
                registry.policy_version = "policy-v2"
        elif condition == "stale_epoch":
            with registry._lock:
                registry._db.execute(
                    "UPDATE registry_meta SET value='other' WHERE key='service_epoch'"
                )
        elif condition == "closed":
            registry.close()
        elif condition == "migration_disabled":
            with registry._lock:
                registry._migration_writes_enabled = False
        with pytest.raises(ServiceDispatchAdmissionError), admission.scope():
            pytest.fail("stale authority entered local commit")
    finally:
        registry.close()


def test_recheck_tracks_clock_inside_scope_and_expires_after_exit():
    registry, clock, _, _, _, _, context = _fixture()
    admission = context.dispatch_admission
    try:
        with admission.scope() as recheck:
            recheck()
            clock[0] += 10
            with pytest.raises(ServiceDispatchAdmissionError, match="NOT_REGISTERED"):
                recheck()
        with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"):
            recheck()
    finally:
        admission.close()
        registry.close()


def test_recheck_is_bound_to_one_scope_and_one_thread():
    registry, _, _, _, _, _, context = _fixture()
    admission = context.dispatch_admission
    errors = []
    try:
        with admission.scope() as first:
            with (
                pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_BUSY"),
                admission.scope(),
            ):
                pass

            def foreign():
                try:
                    first()
                except ServiceDispatchAdmissionError as error:
                    errors.append(error.code)

            thread = threading.Thread(target=foreign)
            thread.start()
            thread.join(2)
            assert errors == ["ADMISSION_CLOSED"]
        with admission.scope() as second:
            with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"):
                first()
            second()
    finally:
        admission.close()
        registry.close()


def test_forged_context_and_unissued_copy_are_rejected():
    import copy

    registry, _, _, _, _, args, context = _fixture()
    admission = context.dispatch_admission
    try:
        assert not admission.matches_context(replace(context, registration_id="other"))
        assert not admission.matches_context(
            replace(context, scope=_strategy_scope(account="other"))
        )
        assert not admission.matches_context(
            replace(context, identity=_identity(actor_ref="other"))
        )
        with pytest.raises(ServiceDispatchAdmissionError, match="NOT_ISSUED"):
            ServiceDispatchAdmission(
                object(),
                registry,
                registration_id="x",
                service_epoch="x",
                instance_id="x",
                principal="x",
                identity=context.identity,
                scope=context.scope,
                arguments=args,
            )
        copied = copy.copy(admission)
        copied_context = replace(context, dispatch_admission=copied)
        assert not copied.matches_context(copied_context)
        assert not copied.matches(args)
        with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"), copied.scope():
            pass
    finally:
        admission.close()
        registry.close()


def test_revocation_waits_for_local_commit_scope():
    registry, _, _, _, _, _, context = _fixture()
    admission = context.dispatch_admission
    entered, release, revoked = threading.Event(), threading.Event(), threading.Event()
    errors = []
    later = []

    def owner():
        try:
            with admission.scope() as recheck:
                entered.set()
                assert release.wait(2)
                recheck()
            assert revoked.wait(2)
            try:
                with admission.scope():
                    pytest.fail("revoked dispatch entered")
            except ServiceDispatchAdmissionError as error:
                later.append(error.code)
        except Exception as error:
            errors.append(error)

    def revoke():
        with registry._lock:
            registry._grants = {}
        revoked.set()

    owner_thread = threading.Thread(target=owner)
    revoke_thread = threading.Thread(target=revoke)
    try:
        owner_thread.start()
        assert entered.wait(2)
        revoke_thread.start()
        assert not revoked.wait(0.05)
        release.set()
        owner_thread.join(2)
        revoke_thread.join(2)
        assert not errors and revoked.is_set() and later == ["DENIED"]
        # Single-owner restriction itself fences reuse from this test thread.
        with (
            pytest.raises(ServiceDispatchAdmissionError, match="WRONG_OWNER_THREAD"),
            admission.scope(),
        ):
            pass
    finally:
        release.set()
        owner_thread.join(2)
        if revoke_thread.ident is not None:
            revoke_thread.join(2)
        admission.close()
        registry.close()


def test_issued_binding_cannot_be_replaced_and_arguments_are_detached():
    registry, _, _, _, _, args, context = _fixture()
    admission = context.dispatch_admission
    try:
        original = admission._binding
        args["metadata"]["parts"].append(3)
        assert not admission.matches(args)
        admission._binding = replace(original, arguments=b"forged")
        assert not admission.matches_context(context)
        with (
            pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"),
            admission.scope(),
        ):
            pass
        admission._binding = original
    finally:
        admission.close()
        registry.close()


def test_recheck_after_waiting_on_journal_lock_rejects_expired_lease(tmp_path):
    import sqlite3

    registry, clock, _, _, _, _, context = _fixture()
    admission = context.dispatch_admission
    path = tmp_path / "journal.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE commits (value TEXT)")
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    waiting, finished = threading.Event(), threading.Event()
    errors = []

    def owner():
        conn = sqlite3.connect(path, timeout=3)
        try:
            with admission.scope() as recheck:
                waiting.set()
                conn.execute("BEGIN IMMEDIATE")
                recheck()
                conn.execute("INSERT INTO commits VALUES ('sent')")
                conn.commit()
        except ServiceDispatchAdmissionError as error:
            errors.append(error.code)
            conn.rollback()
        finally:
            conn.close()
            finished.set()

    thread = threading.Thread(target=owner)
    try:
        thread.start()
        assert waiting.wait(2)
        clock[0] += 10
        db.commit()
        assert finished.wait(3)
        thread.join(2)
        assert errors == ["NOT_REGISTERED"]
        assert db.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 0
    finally:
        db.rollback()
        db.close()
        thread.join(2)
        admission.close()
        registry.close()


def test_forked_or_pid_changed_token_fails_without_touching_locks(monkeypatch):
    import os

    registry, _, _, _, _, args, context = _fixture()
    admission = context.dispatch_admission
    pid = os.getpid()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "getpid", lambda: pid + 1)
            assert not admission.matches(args)
            assert not admission.matches_context(context)
            with (
                pytest.raises(ServiceDispatchAdmissionError, match="WRONG_PROCESS"),
                admission.scope(),
            ):
                pass
            with pytest.raises(ServiceDispatchAdmissionError, match="WRONG_PROCESS"):
                admission.close()
    finally:
        admission.close()
        registry.close()


def test_internal_revalidation_failure_has_finite_error(monkeypatch):
    registry, _, _, _, _, _, context = _fixture()
    admission = context.dispatch_admission

    def fail(*_):
        raise RuntimeError("private internal details")

    monkeypatch.setattr(registry, "_direct_dispatch_admission_error", fail)
    try:
        with pytest.raises(ServiceDispatchAdmissionError, match="^UNAVAILABLE$"), admission.scope():
            pass
    finally:
        admission.close()
        registry.close()


def test_nonblocking_close_before_recheck_rejects():
    registry, _, _, _, _, _, context = _fixture()
    admission = context.dispatch_admission
    try:
        with admission.scope() as recheck:
            admission.close()
            with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"):
                recheck()
        with (
            pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"),
            admission.scope(),
        ):
            pass
    finally:
        registry.close()


def test_close_after_last_recheck_does_not_revoke_inflight_local_commit():
    registry, _, _, _, _, _, context = _fixture()
    admission = context.dispatch_admission
    committed = []
    ready, proceed = threading.Event(), threading.Event()
    callbacks = []

    def owner():
        with admission.scope() as recheck:
            recheck()
            callbacks.append(recheck)
            ready.set()
            assert proceed.wait(2)
            committed.append("local-commit")

    thread = threading.Thread(target=owner)
    close_finished = threading.Event()

    def closer():
        admission.close()
        close_finished.set()

    close_thread = threading.Thread(target=closer)
    try:
        thread.start()
        assert ready.wait(2)
        close_thread.start()
        assert close_finished.wait(1)  # Must not wait on the owner's Registry lock.
        proceed.set()
        thread.join(2)
        close_thread.join(2)
        assert committed == ["local-commit"]
        with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"):
            callbacks[0]()
        with (
            pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"),
            admission.scope(),
        ):
            pass
    finally:
        proceed.set()
        thread.join(2)
        if close_thread.ident is not None:
            close_thread.join(2)
        registry.close()


def test_second_sqlite_connection_is_outside_registry_lock_boundary(tmp_path):
    """A WAL snapshot cannot observe out-of-band epoch writes mid-scope."""
    import sqlite3

    path = tmp_path / "registry.sqlite"
    registry, _, _, _, _, _, context = _fixture(path)
    admission = context.dispatch_admission
    external = sqlite3.connect(path, timeout=2)
    try:
        assert external.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        with admission.scope() as recheck:
            external.execute(
                "UPDATE registry_meta SET value='external-epoch' WHERE key='service_epoch'"
            )
            external.commit()
            # Existing WAL read snapshots stay fixed even though an
            # out-of-band writer changed the disk; this is not a hard fence.
            recheck()
        with pytest.raises(ServiceDispatchAdmissionError, match="STALE_EPOCH"), admission.scope():
            pass
    finally:
        external.close()
        admission.close()
        registry.close()


@pytest.mark.parametrize("event", ["expired", "closed"])
def test_marker_wait_does_not_allow_expired_or_closed_admission(monkeypatch, event):
    registry, clock, _, _, _, _, context = _fixture()
    admission = context.dispatch_admission
    marker_waiting, continue_marker = threading.Event(), threading.Event()
    calls = [0]
    errors, commits = [], []

    def marker(*, fact_kind):
        assert fact_kind == "dispatch"
        calls[0] += 1
        if calls[0] == 2:
            marker_waiting.set()
            assert continue_marker.wait(2)
        return False

    monkeypatch.setattr(registry, "_record_migration_write_attempt", marker)

    def owner():
        try:
            with admission.scope() as recheck:
                recheck()
                commits.append("committed")
        except ServiceDispatchAdmissionError as error:
            errors.append(error.code)

    thread = threading.Thread(target=owner)
    try:
        thread.start()
        assert marker_waiting.wait(2)
        if event == "expired":
            clock[0] += 10
        else:
            admission.close()
        continue_marker.set()
        thread.join(2)
        assert not thread.is_alive()
        assert errors == (["NOT_REGISTERED"] if event == "expired" else ["ADMISSION_CLOSED"])
        assert commits == []
    finally:
        continue_marker.set()
        thread.join(2)
        admission.close()
        registry.close()


def test_close_during_final_authority_check_is_not_lost(monkeypatch):
    registry, _, _, _, _, _, context = _fixture()
    admission = context.dispatch_admission
    original = registry._direct_dispatch_admission_error
    checking, continue_check = threading.Event(), threading.Event()
    count = [0]
    errors, commits = [], []

    def delayed(*args):
        count[0] += 1
        result = original(*args)
        if count[0] == 4:  # final authority check after callback's marker
            checking.set()
            assert continue_check.wait(2)
        return result

    monkeypatch.setattr(registry, "_direct_dispatch_admission_error", delayed)

    def owner():
        try:
            with admission.scope() as recheck:
                recheck()
                commits.append("committed")
        except ServiceDispatchAdmissionError as error:
            errors.append(error.code)

    thread = threading.Thread(target=owner)
    try:
        thread.start()
        assert checking.wait(2)
        admission.close()
        continue_check.set()
        thread.join(2)
        assert errors == ["ADMISSION_CLOSED"] and commits == []
    finally:
        continue_check.set()
        thread.join(2)
        admission.close()
        registry.close()
