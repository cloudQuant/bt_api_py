"""Registry-local Risk v2 admission; it does not enable a Risk handler."""

from __future__ import annotations

import copy
import sqlite3
import threading
from dataclasses import replace
from decimal import Decimal

import pytest
from bt_api_base.service_protocol import WireRole

from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceDispatchAdmission,
    ServiceDispatchAdmissionError,
    ServiceRegistry,
)
from tests.unit.test_service_registry import _identity, _next_request, _register, _strategy_scope

OP = "risk.order.dispatch.v2"
TOKEN = "9f" * 32  # Synthetic bearer; never write the raw value to Registry storage.


def make_registry(
    path=":memory:",
    *,
    enabled=True,
    role=WireRole.STRATEGY_CLIENT,
    write=True,
    port="service.risk_order",
):
    clock = [100.0]
    identity = _identity(role=role, principal_ref="principal-a", actor_ref="strategy-a")
    scope = _strategy_scope(strategy="strategy-a")
    capability = ServiceCapability(
        "risk-a", port, frozenset({OP}), frozenset({OP}) if write else frozenset()
    )
    grant = ResourceGrant(
        "risk-a",
        port,
        "scope-a",
        _strategy_scope(strategy="strategy-a", run_id=None),
        frozenset({OP}),
    )
    registry = ServiceRegistry(
        path,
        service_id="risk-service",
        capabilities=(capability,),
        grants={"curve-a": (PrincipalGrant(identity, (grant,)),)},
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        clock=lambda: clock[0],
        lease_seconds=10,
        enable_risk_v2_dispatch_admission=enabled,
    )
    return registry, clock, identity, scope


def register_and_capture(registry, identity, scope):
    captured = []
    registry.register_handler(
        "risk-a", OP, lambda context, args: captured.append((context, args)) or {"status": "ok"}
    )
    result = _register(
        registry,
        principal="curve-a",
        identity=identity,
        scope=scope,
        instance_id="risk-a",
        port="service.risk_order",
        scope_ref="scope-a",
        operations=(OP,),
    )
    assert result["ok"], result
    registration = result["result"]
    args = {
        "request_key": "key-a",
        "permit_id": "permit-a",
        "reservation_id": "reservation-a",
        "view_digest": "ab" * 32,
        "dispatch_capability": TOKEN,
        "metadata": {"nested": [1, 2]},
    }
    reply = registry.handle(
        "curve-a",
        _next_request(
            OP,
            {
                "registration_id": registration["registration_id"],
                "service_epoch": registration["service_epoch"],
                "instance_id": "risk-a",
                "arguments": args,
            },
            identity=identity,
            scope=scope,
        ),
    )
    assert reply["ok"], reply
    return registration, args, captured[0][0]


def unregister(registry, identity, scope, registration):
    return registry.handle(
        "curve-a",
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


def test_disabled_by_default_and_requires_explicit_write_capability():
    registry, _, _, _ = make_registry(enabled=False)
    try:
        with pytest.raises(ValueError, match="not explicitly enabled"):
            registry.register_handler("risk-a", OP, lambda *_: {})
    finally:
        registry.close()
    for option in ({"write": False}, {"port": "service.direct_write"}):
        registry, _, _, _ = make_registry(**option)
        try:
            with pytest.raises(ValueError, match="not explicitly enabled"):
                registry.register_handler("risk-a", OP, lambda *_: {})
        finally:
            registry.close()


def test_exact_context_and_complete_argument_binding_without_token_persistence(tmp_path):
    path = tmp_path / "registry.sqlite"
    registry, _, identity, scope = make_registry(path)
    try:
        _, args, context = register_and_capture(registry, identity, scope)
        admission = context.dispatch_admission
        assert type(admission) is ServiceDispatchAdmission
        assert admission.matches_context(context)
        assert admission.matches_operation(OP)
        assert admission.matches(args)
        assert not admission.matches(dict(args, dispatch_capability="ab" * 32))
        assert not admission.matches(dict(args, metadata={"nested": [1, 3]}))
        assert not admission.matches(dict(args, extra=True))
        assert not admission.matches_context(replace(context, principal_ref="foreign"))
        assert not admission.matches_context(replace(context, scope=replace(scope, run_id="other")))
        forged = copy.copy(admission)
        assert not forged.matches(args)
        assert not forged.matches_context(replace(context, dispatch_admission=forged))
        assert TOKEN not in repr(admission._binding)
        with admission.scope() as recheck:
            recheck()
        admission.close()
        assert not admission.matches(args)
        assert TOKEN.encode() not in path.read_bytes()
    finally:
        registry.close()


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
        "opt_in_revoked",
        "capability_removed",
        "write_grant_removed",
    ],
)
def test_rechecks_live_authority_after_admission(condition):
    registry, clock, identity, scope = make_registry()
    registration, _, context = register_and_capture(registry, identity, scope)
    admission = context.dispatch_admission
    try:
        with admission.scope() as recheck:
            recheck()
        if condition == "expired":
            clock[0] += 10
        elif condition == "unregistered":
            assert unregister(registry, identity, scope, registration)["ok"]
        elif condition == "revoked":
            with registry._lock:
                registry._grants = {}
        elif condition == "run_changed":
            with registry._transaction():
                registry._db.execute("UPDATE strategy_write_claims SET run_id='other'")
        elif condition == "stopped":
            assert registry.freeze_strategy(scope, "stop-1")["status"] == "applied"
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
        elif condition == "opt_in_revoked":
            with registry._lock:
                registry._risk_v2_dispatch_admission_enabled = False
        elif condition == "capability_removed":
            with registry._lock:
                registry._capabilities = {}
        elif condition == "write_grant_removed":
            with registry._transaction():
                registry._db.execute("UPDATE sessions SET write_operations_json='[]'")
        else:
            registry.close()
        with pytest.raises(ServiceDispatchAdmissionError), admission.scope():
            pytest.fail("stale authority entered local commit")
    finally:
        registry.close()


def test_recheck_fails_after_expiry_within_local_transaction(tmp_path):
    registry, clock, identity, scope = make_registry()
    _, _, context = register_and_capture(registry, identity, scope)
    admission = context.dispatch_admission
    path = tmp_path / "risk.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE risk_claims (request_key TEXT)")
    conn.commit()
    try:
        with pytest.raises(ServiceDispatchAdmissionError), admission.scope() as recheck:
            conn.execute("BEGIN IMMEDIATE")
            try:
                recheck()
                conn.execute("INSERT INTO risk_claims VALUES ('key-a')")
                clock[0] += 10
                recheck()
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        assert conn.execute("SELECT count(*) FROM risk_claims").fetchone()[0] == 0
    finally:
        conn.close()
        registry.close()


def test_risk_journal_rolls_back_when_admission_expires_before_commit(tmp_path):
    AccountScope = pytest.importorskip("bt_api_risk.core.admission").AccountScope
    PretradeLedger = pytest.importorskip("bt_api_risk.pretrade.ledger").PretradeLedger
    RiskOrderJournal = pytest.importorskip("bt_api_risk.risk_order_journal").RiskOrderJournal
    registry, clock, identity, scope = make_registry()
    _, _, context = register_and_capture(registry, identity, scope)
    journal = RiskOrderJournal(tmp_path / "risk.sqlite", writer_id="test-only")
    risk_scope = AccountScope(provider="test", account_id="synthetic", environment="test")
    try:
        with journal.writer_fence(risk_scope) as fence:
            ledger = PretradeLedger(journal, fence)
            with (
                pytest.raises(ServiceDispatchAdmissionError),
                context.dispatch_admission.scope() as recheck,
                ledger.transaction() as transaction,
            ):
                recheck()
                transaction.add_counter("TEST", "20261113", "order", Decimal(1))
                clock[0] += 10
                recheck()
            with ledger.transaction() as transaction:
                assert transaction.counter("TEST", "20261113", "order") == 0
    finally:
        registry.close()


def test_revocation_waits_for_local_transaction_scope(tmp_path):
    registry, _, identity, scope = make_registry()
    registration, _, context = register_and_capture(registry, identity, scope)
    admission = context.dispatch_admission
    local = sqlite3.connect(tmp_path / "risk.sqlite", check_same_thread=False)
    local.execute("CREATE TABLE risk_claims (request_key TEXT)")
    local.commit()
    entered = threading.Event()
    allow_commit = threading.Event()
    revoke_started = threading.Event()
    revoked = threading.Event()
    order = []
    errors = []

    def commit():
        try:
            with admission.scope() as recheck:
                local.execute("BEGIN IMMEDIATE")
                recheck()
                local.execute("INSERT INTO risk_claims VALUES ('key-a')")
                entered.set()
                assert allow_commit.wait(2)
                recheck()
                local.commit()
                order.append("committed")
        except BaseException as error:
            errors.append(error)
            local.rollback()

    def revoke():
        revoke_started.set()
        if unregister(registry, identity, scope, registration)["ok"]:
            order.append("revoked")
        revoked.set()

    owner = threading.Thread(target=commit)
    revoker = threading.Thread(target=revoke)
    try:
        owner.start()
        assert entered.wait(2)
        revoker.start()
        assert revoke_started.wait(2)
        assert not revoked.wait(0.1)
        allow_commit.set()
        owner.join(2)
        revoker.join(2)
        assert not owner.is_alive() and not revoker.is_alive()
        assert not errors and order == ["committed", "revoked"]
        assert local.execute("SELECT count(*) FROM risk_claims").fetchone()[0] == 1
        with pytest.raises(ServiceDispatchAdmissionError), admission.scope():
            pass
    finally:
        allow_commit.set()
        owner.join(2)
        if revoker.ident is not None:
            revoker.join(2)
        local.close()
        registry.close()


def test_scope_rejects_concurrent_reentry_and_foreign_thread():
    registry, _, identity, scope = make_registry()
    _, _, context = register_and_capture(registry, identity, scope)
    admission = context.dispatch_admission
    errors = []
    try:
        with admission.scope() as recheck:
            with (
                pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_BUSY"),
                admission.scope(),
            ):
                pass

            def use_foreign():
                try:
                    recheck()
                except ServiceDispatchAdmissionError as error:
                    errors.append(error.code)

            worker = threading.Thread(target=use_foreign)
            worker.start()
            worker.join(2)
            assert errors == ["ADMISSION_CLOSED"]
            recheck()
        admission.close()
        with pytest.raises(ServiceDispatchAdmissionError), admission.scope():
            pass
    finally:
        registry.close()


def test_strategy_actor_must_match_the_single_account_and_run():
    registry, _, original, scope = make_registry()
    identity = replace(original, actor_ref="other-actor")
    resource = ResourceGrant(
        "risk-a",
        "service.risk_order",
        "scope-a",
        _strategy_scope(strategy="strategy-a", run_id=None),
        frozenset({OP}),
    )
    registry._grants = {"curve-a": (PrincipalGrant(identity, (resource,)),)}
    calls = []
    try:
        registry.register_handler("risk-a", OP, lambda context, args: calls.append(context) or {})
        result = _register(
            registry,
            principal="curve-a",
            identity=identity,
            scope=scope,
            instance_id="risk-a",
            port="service.risk_order",
            scope_ref="scope-a",
            operations=(OP,),
        )
        if result["ok"]:
            reg = result["result"]
            result = registry.handle(
                "curve-a",
                _next_request(
                    OP,
                    {
                        "registration_id": reg["registration_id"],
                        "service_epoch": reg["service_epoch"],
                        "instance_id": "risk-a",
                        "arguments": {"request_key": "key-a"},
                    },
                    identity=identity,
                    scope=scope,
                ),
            )
        assert not result["ok"]
        assert not calls
    finally:
        registry.close()


def test_service_role_never_receives_strategy_only_admission():
    registry, _, identity, scope = make_registry(role=WireRole.SERVICE)
    calls = []
    try:
        registry.register_handler("risk-a", OP, lambda context, args: calls.append(context) or {})
        result = _register(
            registry,
            principal="curve-a",
            identity=identity,
            scope=scope,
            instance_id="risk-a",
            port="service.risk_order",
            scope_ref="scope-a",
            operations=(OP,),
        )
        if result["ok"]:
            reg = result["result"]
            result = registry.handle(
                "curve-a",
                _next_request(
                    OP,
                    {
                        "registration_id": reg["registration_id"],
                        "service_epoch": reg["service_epoch"],
                        "instance_id": "risk-a",
                        "arguments": {"request_key": "key-a"},
                    },
                    identity=identity,
                    scope=scope,
                ),
            )
        assert not result["ok"]
        assert not calls
    finally:
        registry.close()


def test_risk_v1_handler_unchanged_and_gets_no_v2_admission():
    identity = _identity(principal_ref="principal-a", actor_ref="strategy-a")
    scope = _strategy_scope(strategy="strategy-a")
    operation = "risk.order.dispatch"
    capability = ServiceCapability(
        "risk-a", "service.risk_order", frozenset({operation}), frozenset({operation})
    )
    grant = ResourceGrant(
        "risk-a",
        "service.risk_order",
        "scope-a",
        _strategy_scope(strategy="strategy-a", run_id=None),
        frozenset({operation}),
    )
    registry = ServiceRegistry(
        ":memory:",
        service_id="risk-service",
        capabilities=(capability,),
        grants={"curve-a": (PrincipalGrant(identity, (grant,)),)},
        policy_version="v1",
        requirements_version="v1",
    )
    contexts = []
    try:
        registry.register_handler(
            "risk-a", operation, lambda context, args: contexts.append(context) or {}
        )
        result = _register(
            registry,
            principal="curve-a",
            identity=identity,
            scope=scope,
            instance_id="risk-a",
            port="service.risk_order",
            scope_ref="scope-a",
            operations=(operation,),
        )
        assert result["ok"]
        reg = result["result"]
        result = registry.handle(
            "curve-a",
            _next_request(
                operation,
                {
                    "registration_id": reg["registration_id"],
                    "service_epoch": reg["service_epoch"],
                    "instance_id": "risk-a",
                    "arguments": {"request_key": "key-a"},
                },
                identity=identity,
                scope=scope,
            ),
        )
        assert result["ok"]
        assert len(contexts) == 1 and contexts[0].dispatch_admission is None
    finally:
        registry.close()
