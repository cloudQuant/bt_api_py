"""Registry-issued cancel admission with local-only creation/owner scopes."""
from __future__ import annotations

import os
import threading
from dataclasses import FrozenInstanceError

import pytest

from bt_api_py.service_registry import (
    PrincipalGrant, ResourceGrant, ServiceCapability, ServiceDispatchAdmission,
    ServiceDispatchAdmissionError, ServiceRegistry,
)
from tests.unit.test_service_registry import _identity, _next_request, _register, _strategy_scope


def _fixture():
    clock = [100.0]
    identity = _identity(principal_ref="principal-a", actor_ref="strategy-a")
    scope = _strategy_scope(strategy="strategy-a")
    operations = frozenset({"direct.dispatch", "direct.cancel"})
    capability = ServiceCapability("gateway-a", "service.direct_write", operations, operations)
    grant = ResourceGrant("gateway-a", "service.direct_write", "scope-a",
                          _strategy_scope(strategy="strategy-a", run_id=None), operations)
    registry = ServiceRegistry(
        ":memory:", service_id="gateway-service", capabilities=(capability,),
        grants={"principal-a": (PrincipalGrant(identity, (grant,)),)},
        policy_version="policy-v1", requirements_version="requirements-v1",
        lease_seconds=10, clock=lambda: clock[0],
    )
    captured = []
    def handler(context, args):
        captured.append((context, dict(args)))
        return {"accepted": True}
    for operation in operations:
        registry.register_handler("gateway-a", operation, handler)
    response = _register(registry, identity=identity, scope=scope, principal="principal-a",
        instance_id="gateway-a", port="service.direct_write", scope_ref="scope-a",
        operations=("direct.dispatch", "direct.cancel"))
    assert response["ok"]
    registration = response["result"]
    def invoke(operation):
        args = {"request_key": "key", "reservation_id": "reservation", "digest": "digest"}
        reply = registry.handle("principal-a", _next_request(operation, {
            "registration_id": registration["registration_id"],
            "service_epoch": registration["service_epoch"], "instance_id": "gateway-a",
            "arguments": args,
        }, identity=identity, scope=scope))
        return reply, args
    return registry, clock, scope, invoke, captured


def test_issuance_binds_exact_operation_and_creation_to_owner_handoff():
    registry, _, _, invoke, captured = _fixture()
    try:
        dispatch_result, args = invoke("direct.dispatch")
        cancel_result, _ = invoke("direct.cancel")
        assert dispatch_result["ok"] and cancel_result["ok"]
        dispatch, cancel = [context.dispatch_admission for context, _ in captured]
        assert type(dispatch) is type(cancel) is ServiceDispatchAdmission
        assert dispatch.matches(args) and cancel.matches(args)
        assert dispatch.matches_operation("direct.dispatch") and not dispatch.matches_operation("direct.cancel")
        assert cancel.matches_operation("direct.cancel") and not cancel.matches_operation("direct.dispatch")
        with pytest.raises(FrozenInstanceError):
            cancel._binding.operation = "direct.dispatch"
        with pytest.raises(ServiceDispatchAdmissionError, match="OPERATION_MISMATCH"), dispatch.creation_scope():
            pass
        with cancel.creation_scope() as recheck:
            recheck()
            with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_BUSY"), cancel.scope():
                pass
        with pytest.raises(ServiceDispatchAdmissionError, match="CREATION_SCOPE_CONSUMED"), cancel.creation_scope():
            pass
        outcomes = []
        def owner():
            with cancel.scope() as recheck:
                recheck()
                outcomes.append("owner")
        worker = threading.Thread(target=owner)
        worker.start(); worker.join(2)
        assert not worker.is_alive() and outcomes == ["owner"]
        with pytest.raises(ServiceDispatchAdmissionError, match="WRONG_OWNER_THREAD"), cancel.scope():
            pass
        cancel.close()
        assert not cancel.matches_operation("direct.cancel")
    finally:
        registry.close()


def test_creation_only_issuer_and_no_creation_after_owner():
    registry, _, _, invoke, captured = _fixture()
    try:
        assert invoke("direct.cancel")[0]["ok"]
        admission = captured[-1][0].dispatch_admission
        errors = []
        def outsider():
            try:
                with admission.creation_scope():
                    pass
            except ServiceDispatchAdmissionError as exc:
                errors.append(exc.code)
        t = threading.Thread(target=outsider); t.start(); t.join(2)
        assert not t.is_alive() and errors == ["WRONG_ISSUER_THREAD"]
        # Failed wrong-thread entry did not claim the creation scope.
        with admission.creation_scope():
            pass
        assert invoke("direct.cancel")[0]["ok"]
        second = captured[-1][0].dispatch_admission
        with second.scope():
            pass
        with pytest.raises(ServiceDispatchAdmissionError, match="CREATION_SCOPE_CONSUMED"), second.creation_scope():
            pass
    finally:
        registry.close()


def test_stop_new_permits_cancel_but_not_dispatch():
    registry, _, scope, invoke, captured = _fixture()
    try:
        assert invoke("direct.dispatch")[0]["ok"]
        old_dispatch = captured[-1][0].dispatch_admission
        assert registry.freeze_strategy(scope, "stop-test")["status"] == "applied"
        with pytest.raises(ServiceDispatchAdmissionError, match="STRATEGY_STOPPED"), old_dispatch.scope():
            pass
        assert invoke("direct.dispatch")[0]["code"] == "STRATEGY_STOPPED"
        assert invoke("direct.cancel")[0]["ok"]
        cancel = captured[-1][0].dispatch_admission
        with cancel.creation_scope() as recheck:
            recheck()
        with cancel.scope() as recheck:
            recheck()
    finally:
        registry.close()


@pytest.mark.parametrize("condition", ["expired", "revoked", "claim", "policy", "migration", "capability"])
def test_creation_rechecks_authority_and_mig03(condition):
    registry, clock, _, invoke, captured = _fixture()
    try:
        assert invoke("direct.cancel")[0]["ok"]
        admission = captured[-1][0].dispatch_admission
        if condition == "expired":
            clock[0] += 10
        elif condition == "revoked":
            with registry._lock:
                registry._grants = {}
        elif condition == "claim":
            with registry._transaction():
                registry._db.execute("UPDATE strategy_write_claims SET run_id='other'")
        elif condition == "policy":
            with registry._lock:
                registry.policy_version = "policy-v2"
        elif condition == "migration":
            with registry._lock:
                registry._migration_writes_enabled = False
        else:
            with registry._lock:
                registry._capabilities = {}
        called = []
        with pytest.raises(ServiceDispatchAdmissionError), admission.creation_scope():
            called.append(True)
        assert called == []
    finally:
        registry.close()


def test_cancel_recheck_expires_while_in_creation_scope():
    registry, clock, _, invoke, captured = _fixture()
    try:
        assert invoke("direct.cancel")[0]["ok"]
        admission = captured[-1][0].dispatch_admission
        with admission.creation_scope() as recheck:
            clock[0] += 10
            with pytest.raises(ServiceDispatchAdmissionError, match="NOT_REGISTERED"):
                recheck()
        with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"):
            recheck()
    finally:
        registry.close()


def test_fork_and_nonowner_recheck_rejected_before_lock(monkeypatch):
    registry, _, _, invoke, captured = _fixture()
    try:
        assert invoke("direct.cancel")[0]["ok"]
        admission = captured[-1][0].dispatch_admission
        with admission.creation_scope() as recheck:
            errors = []
            def outsider():
                try:
                    recheck()
                except ServiceDispatchAdmissionError as exc:
                    errors.append(exc.code)
            t = threading.Thread(target=outsider); t.start(); t.join(2)
            assert not t.is_alive() and errors == ["ADMISSION_CLOSED"]
        pid = os.getpid()
        with monkeypatch.context() as patch:
            patch.setattr(os, "getpid", lambda: pid + 1)
            assert not admission.matches_operation("direct.cancel")
            with pytest.raises(ServiceDispatchAdmissionError, match="WRONG_PROCESS"), admission.creation_scope():
                pass
            with pytest.raises(ServiceDispatchAdmissionError, match="WRONG_PROCESS"), admission.scope():
                pass
    finally:
        registry.close()


@pytest.mark.parametrize("condition", ["expired", "revoked", "claim", "migration"])
def test_owner_handoff_rechecks_authority_after_creation(condition):
    registry, clock, _, invoke, captured = _fixture()
    try:
        assert invoke("direct.cancel")[0]["ok"]
        admission = captured[-1][0].dispatch_admission
        with admission.creation_scope() as recheck:
            recheck()
        if condition == "expired":
            clock[0] += 10
        elif condition == "revoked":
            with registry._lock:
                registry._grants = {}
        elif condition == "claim":
            with registry._transaction():
                registry._db.execute("UPDATE strategy_write_claims SET run_id='other'")
        else:
            with registry._lock:
                registry._migration_writes_enabled = False
        calls = []
        errors = []
        def owner():
            try:
                with admission.scope():
                    calls.append(True)
            except ServiceDispatchAdmissionError as exc:
                errors.append(exc.code)
        worker = threading.Thread(target=owner)
        worker.start(); worker.join(2)
        assert not worker.is_alive() and errors and not calls
    finally:
        registry.close()


def test_cancel_request_mig03_disabled_does_not_enter_handler():
    registry, _, _, invoke, captured = _fixture()
    try:
        with registry._lock:
            registry._migration_writes_enabled = False
        assert invoke("direct.cancel")[0]["code"] == "MIGRATION_WRITE_DISABLED"
        assert captured == []
    finally:
        registry.close()


def test_cancel_issued_before_stop_new_remains_authorized():
    registry, _, scope, invoke, captured = _fixture()
    try:
        assert invoke("direct.cancel")[0]["ok"]
        cancel = captured[-1][0].dispatch_admission
        assert registry.freeze_strategy(scope, "stop-after-cancel-issued")["status"] == "applied"
        with cancel.creation_scope() as recheck:
            recheck()
        with cancel.scope() as recheck:
            recheck()
    finally:
        registry.close()


def test_creation_scope_concurrent_close_prevents_local_commit(tmp_path):
    import sqlite3

    registry, _, _, invoke, captured = _fixture()
    try:
        assert invoke("direct.cancel")[0]["ok"]
        admission = captured[-1][0].dispatch_admission
        database = tmp_path / "creation-transaction.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE attempts (id TEXT NOT NULL)")
            closed = threading.Event()

            def close_from_other_thread():
                admission.close()
                closed.set()

            with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"):
                with admission.creation_scope() as recheck:
                    with connection:
                        connection.execute("INSERT INTO attempts VALUES (?)", ("attempt",))
                        worker = threading.Thread(target=close_from_other_thread)
                        worker.start()
                        worker.join(timeout=2)
                        assert not worker.is_alive() and closed.is_set()
                        recheck()
            assert connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
        with sqlite3.connect(database) as check:
            assert check.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    finally:
        registry.close()


def _real_cancel_journal(tmp_path):
    from bt_api_base.direct_write_protocol import NativeOrderIdentity
    from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    from bt_api_gateway.direct_dispatch import _identity as encode_identity

    path = tmp_path / "actual-command-journal.sqlite3"
    journal = GatewayDirectCommandJournal.create(path, "account-a", generation="owner-g1")
    order = journal.reserve("strategy-a", "order-key", "principal-a", {
        "symbol": "IF2612", "side": "buy", "quantity": "1",
    })
    identity = NativeOrderIdentity(
        "CTP", "order-ref", {"trading_day": "20261108", "front_id": 1}, 1
    )
    started = journal.begin_dispatch(
        "strategy-a", "order-key", order.reservation_id, order.digest, "principal-a", 1,
        encode_identity(identity, "CTP"), ("original-order-key",),
    )
    assert started.newly_started
    return path, journal, GatewayDirectCancelJournal(journal), started.record, identity


def _request_real_cancel(cancel, order, identity, before_commit):
    return cancel.request(
        "strategy-a", "cancel-operation", "principal-a", "order-key",
        order.reservation_id, order.digest, target_identity=identity, symbol="IF2612",
        before_commit=before_commit,
    )


def test_real_cancel_journal_lock_wait_rechecks_expired_lease(tmp_path, monkeypatch):
    import sqlite3
    from contextlib import contextmanager

    registry, clock, _, invoke, captured = _fixture()
    path, journal, cancel, order, identity = _real_cancel_journal(tmp_path)
    lock_held, begin_attempted, release_lock, request_finished = (threading.Event() for _ in range(4))
    errors, callbacks, native_calls = [], [], []
    original = journal._transaction_on

    @contextmanager
    def observe_begin(db):
        begin_attempted.set()
        with original(db) as connection:
            yield connection

    monkeypatch.setattr(journal, "_transaction_on", observe_begin)

    def blocker():
        try:
            with sqlite3.connect(path, timeout=3) as connection:
                connection.execute("BEGIN IMMEDIATE")
                lock_held.set()
                if not begin_attempted.wait(2):
                    raise AssertionError("request did not attempt journal transaction")
                if request_finished.wait(0.1):
                    raise AssertionError("request did not wait on held journal write lock")
                clock[0] += 10
                release_lock.set()
                connection.rollback()
        except BaseException as exc:
            errors.append(exc)
            release_lock.set()

    worker = threading.Thread(target=blocker)
    try:
        assert invoke("direct.cancel")[0]["ok"]
        admission = captured[-1][0].dispatch_admission
        worker.start()
        assert lock_held.wait(2)
        with pytest.raises(ServiceDispatchAdmissionError, match="NOT_REGISTERED"):
            with admission.creation_scope() as recheck:
                def before_commit():
                    callbacks.append("called")
                    recheck()
                try:
                    _request_real_cancel(cancel, order, identity, before_commit)
                finally:
                    request_finished.set()
        worker.join(2)
        assert not worker.is_alive() and release_lock.is_set() and errors == []
        assert callbacks == ["called"] and native_calls == []
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM direct_cancel_attempts").fetchone()[0] == 0
    finally:
        request_finished.set()
        worker.join(3)
        registry.close()


def test_close_after_final_recheck_allows_commit_but_blocks_owner(tmp_path):
    import sqlite3

    registry, _, _, invoke, captured = _fixture()
    path, _, cancel, order, identity = _real_cancel_journal(tmp_path)
    native_calls, closed = [], threading.Event()
    try:
        assert invoke("direct.cancel")[0]["ok"]
        admission = captured[-1][0].dispatch_admission
        with admission.creation_scope() as recheck:
            def before_commit():
                recheck()
                worker = threading.Thread(target=lambda: (admission.close(), closed.set()))
                worker.start(); worker.join(2)
                assert not worker.is_alive() and closed.is_set()
            record = _request_real_cancel(cancel, order, identity, before_commit)
        assert record.status == "REQUESTED"
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM direct_cancel_attempts").fetchone()[0] == 1
        with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"), admission.scope():
            native_calls.append(True)
        assert native_calls == []
    finally:
        registry.close()
