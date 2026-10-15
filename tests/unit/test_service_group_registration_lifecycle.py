from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

from bt_api_py.service_components import (
    BorrowedServiceClient,
    ServiceComponent,
    ServiceComponentError,
)
from bt_api_py.service_host import ServiceHost
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
    ServiceRegistryGroupWritePolicyError,
    ServiceRegistryStrategyRunConflictError,
    TrustedGroupReservationPolicy,
    TrustedGroupResolutionPolicy,
    TrustedGroupWritePolicy,
)

_SENT_AT = "2026-10-15T12:00:00Z"
_GROUP_OPERATION = "orders.submit_group_leg"


def _request(
    operation: str,
    payload: dict[str, Any],
    *,
    identity: WireIdentity,
    scope: WireScope,
) -> WireEnvelope:
    _request.next_id += 1
    return WireEnvelope(
        header=WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=f"message-{_request.next_id}",
            correlation_id=None,
            identity=identity,
            scope=scope,
            operation=operation,
            schema_id=(
                "sdk.service.control" if operation.startswith("service.") else "sdk.service.call"
            ),
            schema_version=1,
            sent_at_utc=_SENT_AT,
        ),
        payload=payload,
    )


_request.next_id = 0  # type: ignore[attr-defined]


def _group_scope() -> WireScope:
    return WireScope(
        execution_domain_ref="live:venue-a",
        account_ref="account-a",
        strategy_ref="strategy-a",
        portfolio_ref="portfolio-a",
    )


def _new_registry(
    path: Path,
    *,
    clock=lambda: 100.0,
    group_run_id: str = "run-a",
    strategy_run_id: str | None = None,
) -> tuple[ServiceRegistry, WireIdentity, WireScope, TrustedGroupWritePolicy]:
    scope = _group_scope()
    identity = WireIdentity(
        WireRole.SERVICE,
        principal_ref="group-coordinator",
        actor_ref="group-coordinator",
    )
    policy = TrustedGroupWritePolicy(
        instance_id="account-owner",
        principal_ref="group-coordinator",
        identity=identity,
        scope_ref="account-scope-a",
        scope=scope,
        run_id=group_run_id,
    )
    operations = frozenset({_GROUP_OPERATION})
    capability = ServiceCapability(
        "account-owner", "service.order_entry", operations, operations
    )
    resource = ResourceGrant(
        "account-owner",
        "service.order_entry",
        "account-scope-a",
        scope,
        operations,
    )
    grants: dict[str, tuple[PrincipalGrant, ...]] = {
        "group-coordinator": (PrincipalGrant(identity, (resource,)),)
    }
    capabilities = [capability]
    if strategy_run_id is not None:
        strategy_identity = WireIdentity(
            WireRole.STRATEGY_CLIENT,
            principal_ref="strategy-client",
            actor_ref="strategy-a",
        )
        strategy_scope = WireScope(
            execution_domain_ref=scope.execution_domain_ref,
            account_ref=scope.account_ref,
            strategy_ref=scope.strategy_ref,
        )
        strategy_capability = ServiceCapability(
            "strategy-owner",
            "service.strategy_orders",
            frozenset({"orders.submit"}),
            frozenset({"orders.submit"}),
        )
        strategy_resource = ResourceGrant(
            "strategy-owner",
            "service.strategy_orders",
            "strategy-scope-a",
            strategy_scope,
            frozenset({"orders.submit"}),
        )
        grants["strategy-curve"] = (PrincipalGrant(strategy_identity, (strategy_resource,)),)
        capabilities.append(strategy_capability)

    registry = ServiceRegistry(
        path,
        service_id="execution-service",
        capabilities=tuple(capabilities),
        grants=grants,
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        lease_seconds=10,
        clock=clock,
        trusted_group_write_policies=(policy,),
    )
    return registry, identity, scope, policy


def _register_group(
    registry: ServiceRegistry, identity: WireIdentity, scope: WireScope
) -> dict[str, Any]:
    response = registry.handle(
        "group-coordinator",
        _request(
            "service.register",
            {
                "registration_key": "group-registration-key",
                "client_session_id": "group-session",
                "instance_id": "account-owner",
                "port": "service.order_entry",
                "scope_ref": "account-scope-a",
                "operations": (_GROUP_OPERATION,),
            },
            identity=identity,
            scope=scope,
        ),
    )
    assert response["ok"] is True
    return dict(response["result"])


def _dispatch_group(
    registry: ServiceRegistry,
    identity: WireIdentity,
    scope: WireScope,
    registration: dict[str, Any],
) -> dict[str, Any]:
    return dict(
        registry.handle(
            "group-coordinator",
            _request(
                _GROUP_OPERATION,
                {
                    "registration_id": registration["registration_id"],
                    "service_epoch": registration["service_epoch"],
                    "instance_id": "account-owner",
                    "arguments": {"accepted_group_ref": "group-a"},
                },
                identity=identity,
                scope=scope,
            ),
        )
    )


def test_group_registration_is_eligible_before_run_binding_then_dispatch_is_fenced(
    tmp_path: Path,
) -> None:
    now = [100.0]
    registry, identity, scope, policy = _new_registry(
        tmp_path / "group-owner.sqlite", clock=lambda: now[0]
    )
    calls: list[tuple[str, str]] = []
    registry.register_handler(
        "account-owner",
        _GROUP_OPERATION,
        lambda context, arguments: calls.append(
            (context.registration_id, str(arguments["accepted_group_ref"]))
        )
        or {"submitted": True},
    )
    try:
        registration = _register_group(registry, identity, scope)
        changes_before_snapshot = registry._db.total_changes
        contexts = registry.active_registration_contexts(
            instance_id="account-owner", port="service.order_entry"
        )
        assert len(contexts) == 1
        assert contexts[0].context.registration_id == registration["registration_id"]
        assert contexts[0].strategy_write_run_id is None
        assert contexts[0].service_epoch == registry.service_epoch
        assert contexts[0].write_operations == frozenset({_GROUP_OPERATION})
        assert registry._db.total_changes == changes_before_snapshot

        # The eligible service registration is not a run authority. A write
        # cannot reach the handler until the trusted accepted-group callback
        # binds the exact operator-configured logical run.
        blocked = _dispatch_group(registry, identity, scope, registration)
        assert blocked["ok"] is False
        assert blocked["code"] == "DUPLICATE_ACTIVE_RUN"
        assert calls == []

        assert registry.bind_trusted_group_run(
            "live:venue-a",
            "strategy-a",
            "account-a",
            registration_id=registration["registration_id"],
        ) == policy.run_id
        assert registry.bind_trusted_group_run(
            "live:venue-a",
            "strategy-a",
            "account-a",
            registration_id=registration["registration_id"],
        ) == policy.run_id
        assert registry.strategy_write_run_id(
            "live:venue-a", "strategy-a", "account-a"
        ) == policy.run_id
        claim = registry._db.execute(
            "SELECT run_id, active_registration_id FROM strategy_write_claims "
            "WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?",
            ("live:venue-a", "strategy-a", "account-a"),
        ).fetchone()
        assert claim["run_id"] == policy.run_id
        assert claim["active_registration_id"] is None

        contexts_after_bind = registry.active_registration_contexts(
            instance_id="account-owner", port="service.order_entry"
        )
        assert contexts_after_bind[0].strategy_write_run_id == policy.run_id
        accepted = _dispatch_group(registry, identity, scope, registration)
        assert accepted["ok"] is True
        assert accepted["result"] == {"submitted": True}
        assert calls == [(registration["registration_id"], "group-a")]

        now[0] = 111.0
        changes_before_expired_snapshot = registry._db.total_changes
        assert registry.active_registration_contexts(
            instance_id="account-owner", port="service.order_entry"
        ) == ()
        assert registry._db.total_changes == changes_before_expired_snapshot
        assert registry._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert registry.strategy_write_run_id(
            "live:venue-a", "strategy-a", "account-a"
        ) == policy.run_id
    finally:
        registry.close()


def test_group_binding_cannot_replace_an_existing_logical_run_or_claim(
    tmp_path: Path,
) -> None:
    registry, identity, scope, policy = _new_registry(
        tmp_path / "group-conflict.sqlite",
        group_run_id="run-new",
        strategy_run_id="run-old",
    )
    calls: list[str] = []
    registry.register_handler(
        "account-owner", _GROUP_OPERATION, lambda context, args: calls.append("called") or {}
    )
    strategy_identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        principal_ref="strategy-client",
        actor_ref="strategy-a",
    )
    strategy_scope = WireScope(
        execution_domain_ref="live:venue-a",
        account_ref="account-a",
        strategy_ref="strategy-a",
        run_id="run-old",
    )
    try:
        strategy_registration = registry.handle(
            "strategy-curve",
            _request(
                "service.register",
                {
                    "registration_key": "strategy-key",
                    "client_session_id": "strategy-session",
                    "instance_id": "strategy-owner",
                    "port": "service.strategy_orders",
                    "scope_ref": "strategy-scope-a",
                    "operations": ("orders.submit",),
                },
                identity=strategy_identity,
                scope=strategy_scope,
            ),
        )
        assert strategy_registration["ok"] is True
        group_registration = _register_group(registry, identity, scope)

        with pytest.raises(ServiceRegistryStrategyRunConflictError):
            registry.bind_trusted_group_run(
                "live:venue-a",
                "strategy-a",
                "account-a",
                registration_id=group_registration["registration_id"],
            )
        with pytest.raises(ServiceRegistryGroupWritePolicyError):
            registry.bind_trusted_group_run(
                "live:venue-a",
                "strategy-a",
                "account-a",
                registration_id="unregistered-group-owner",
            )
        assert registry.strategy_write_run_id(
            "live:venue-a", "strategy-a", "account-a"
        ) == "run-old"
        claim = registry._db.execute(
            "SELECT run_id, active_registration_id FROM strategy_write_claims "
            "WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?",
            ("live:venue-a", "strategy-a", "account-a"),
        ).fetchone()
        assert claim["run_id"] == "run-old"
        assert claim["active_registration_id"] == strategy_registration["result"][
            "registration_id"
        ]

        denied = _dispatch_group(registry, identity, scope, group_registration)
        assert denied["ok"] is False
        assert denied["code"] == "DUPLICATE_ACTIVE_RUN"
        assert calls == []
    finally:
        registry.close()


def test_host_registration_lifecycle_seeds_exact_run_before_adapter_receives_context(
    tmp_path: Path,
) -> None:
    registry, identity, scope, policy = _new_registry(tmp_path / "host-context.sqlite")
    contexts_seen: list[tuple[object, ...]] = []
    component = ServiceComponent(
        component="execution",
        instance_id="account-owner",
        capabilities=(
            ServiceCapability(
                "account-owner",
                "service.order_entry",
                frozenset({_GROUP_OPERATION}),
                frozenset({_GROUP_OPERATION}),
            ),
        ),
        handlers={_GROUP_OPERATION: lambda context, args: {}},
        internal_ports={},
        readiness=lambda: True,
        close=lambda: None,
        maintain_registrations=contexts_seen.append,
    )
    registry.register_handler("account-owner", _GROUP_OPERATION, component.handlers[_GROUP_OPERATION])
    host = object.__new__(ServiceHost)
    host._registry = registry
    host._components = [component]
    host._component_maintenance_faults = set()
    host._market_active_registration_count = 0
    try:
        registration = _register_group(registry, identity, scope)
        changes_before = registry._db.total_changes
        host._maintain_components()
        assert len(contexts_seen) == 1
        assert len(contexts_seen[0]) == 1
        projected = contexts_seen[0][0]
        assert projected.context.registration_id == registration["registration_id"]
        assert projected.strategy_write_run_id == policy.run_id
        assert host._component_maintenance_faults == set()
        assert registry._db.total_changes == changes_before + 1
        claim = registry._db.execute(
            "SELECT run_id, active_registration_id FROM strategy_write_claims "
            "WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?",
            ("live:venue-a", "strategy-a", "account-a"),
        ).fetchone()
        assert claim["run_id"] == policy.run_id
        assert claim["active_registration_id"] is None
        dispatched = _dispatch_group(registry, identity, scope, registration)
        assert dispatched["ok"] is True, dispatched
    finally:
        registry.close()


def test_borrowed_upstream_exposes_only_owner_thread_call() -> None:
    class Runtime:
        state = "registered"
        healthy = True

        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        def call(self, mount, operation, arguments, **selectors):
            self.calls.append((mount, operation, arguments, selectors))
            return {"ok": True}

        def close(self) -> None:  # Not exposed by the borrowed facade.
            raise AssertionError("component closed Host-owned upstream runtime")

    runtime = Runtime()
    borrowed = BorrowedServiceClient(runtime)
    assert borrowed.call("account-owner", "orders.submit_group_leg", {"group": "g"}) == {
        "ok": True
    }
    assert runtime.calls == [
        ("account-owner", "orders.submit_group_leg", {"group": "g"}, {
            "account_ref": None,
            "portfolio_ref": None,
            "scope_ref": None,
            "max_reply_message_bytes": None,
        })
    ]
    assert not hasattr(borrowed, "close")
    errors: list[BaseException] = []

    def call_from_other_thread() -> None:
        try:
            borrowed.call("account-owner", "orders.submit_group_leg", {})
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=call_from_other_thread)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert len(errors) == 1
    assert type(errors[0]) is ServiceComponentError
    assert errors[0].code == "OWNER_THREAD_REQUIRED"


def _register_route(
    registry: ServiceRegistry,
    *,
    identity: WireIdentity,
    scope: WireScope,
    instance_id: str,
    port: str,
    scope_ref: str,
    operations: tuple[str, ...],
    suffix: str,
) -> dict[str, Any]:
    response = registry.handle(
        identity.principal_ref,
        _request(
            "service.register",
            {
                "registration_key": f"registration-key-{suffix}",
                "client_session_id": f"client-session-{suffix}",
                "instance_id": instance_id,
                "port": port,
                "scope_ref": scope_ref,
                "operations": operations,
            },
            identity=identity,
            scope=scope,
        ),
    )
    return dict(response)


def _call_registered(
    registry: ServiceRegistry,
    *,
    identity: WireIdentity,
    scope: WireScope,
    registration: dict[str, Any],
    instance_id: str,
    operation: str,
) -> dict[str, Any]:
    result = registry.handle(
        identity.principal_ref,
        _request(
            operation,
            {
                "registration_id": registration["result"]["registration_id"],
                "service_epoch": registration["result"]["service_epoch"],
                "instance_id": instance_id,
                "arguments": {},
            },
            identity=identity,
            scope=scope,
        ),
    )
    return dict(result)


def test_reservation_registry_policy_is_fixed_to_exact_scalar_account_route(
    tmp_path: Path,
) -> None:
    scope = _group_scope()
    identity = WireIdentity(
        WireRole.SERVICE,
        principal_ref="coordinator",
        actor_ref="coordinator",
    )
    policy = TrustedGroupReservationPolicy(
        instance_id="risk-owner",
        principal_ref="coordinator",
        identity=identity,
        scope_ref="account-scope-a",
        scope=scope,
        run_id="run-a",
        account_ref="account-a",
    )
    operations = policy.operations
    capability = ServiceCapability(
        "risk-owner",
        "service.group_reservations",
        operations,
        policy.write_operations,
    )
    grant = ResourceGrant(
        "risk-owner",
        "service.group_reservations",
        "account-scope-a",
        scope,
        operations,
    )
    registry = ServiceRegistry(
        tmp_path / "reservation-policy.sqlite",
        service_id="risk-service",
        capabilities=(capability,),
        grants={"coordinator": (PrincipalGrant(identity, (grant,)),)},
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        trusted_group_reservation_policies=(policy,),
    )
    dispatched: list[str] = []
    for operation in operations:
        registry.register_handler(
            "risk-owner",
            operation,
            lambda context, arguments, op=operation: dispatched.append(op)
            or {"operation": op},
        )
    try:
        registered = _register_route(
            registry,
            identity=identity,
            scope=scope,
            instance_id="risk-owner",
            port="service.group_reservations",
            scope_ref="account-scope-a",
            operations=tuple(sorted(operations)),
            suffix="reservation",
        )
        assert registered["ok"] is True

        lifecycle_calls: list[tuple[str, ...]] = []

        def observe_reservation_runs(contexts: tuple[object, ...]) -> None:
            lifecycle_calls.append(
                tuple(
                    f"{item.context.registration_id}:{item.strategy_write_run_id}"
                    for item in contexts
                )
            )

        component = ServiceComponent(
            component="risk",
            instance_id="risk-owner",
            capabilities=(capability,),
            handlers={operation: (lambda context, arguments: {}) for operation in operations},
            internal_ports={},
            readiness=lambda: True,
            close=lambda: None,
            maintain_registrations=observe_reservation_runs,
        )
        host = object.__new__(ServiceHost)
        host._registry = registry
        host._components = [component]
        host._component_maintenance_faults = set()
        host._market_active_registration_count = 0
        host._maintain_components()
        assert lifecycle_calls == [
            (f"{registered['result']['registration_id']}:run-a",)
        ]
        assert host._component_maintenance_faults == set()
        assert registry.strategy_write_run_id(
            "live:venue-a", "strategy-a", "account-a"
        ) == "run-a"
        claim = registry._db.execute(
            "SELECT run_id, active_registration_id FROM strategy_write_claims "
            "WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?",
            ("live:venue-a", "strategy-a", "account-a"),
        ).fetchone()
        assert claim["run_id"] == "run-a"
        assert claim["active_registration_id"] is None

        reserve = _call_registered(
            registry,
            identity=identity,
            scope=scope,
            registration=registered,
            instance_id="risk-owner",
            operation="reservations.reserve",
        )
        assert reserve["ok"] is True
        assert reserve["result"] == {"operation": "reservations.reserve"}
        assert dispatched == ["reservations.reserve"]

        # A scalar policy cannot be reused for another account or a multi-
        # account wire scope, even though the same principal is authenticated.
        wrong_account_scope = WireScope(
            execution_domain_ref=scope.execution_domain_ref,
            account_ref="account-b",
            strategy_ref=scope.strategy_ref,
            portfolio_ref=scope.portfolio_ref,
        )
        wrong_account = _register_route(
            registry,
            identity=identity,
            scope=wrong_account_scope,
            instance_id="risk-owner",
            port="service.group_reservations",
            scope_ref="account-scope-a",
            operations=("reservations.reserve",),
            suffix="wrong-account",
        )
        assert wrong_account["ok"] is False
        assert wrong_account["code"] == "SCOPE_DENIED"

        multi_account_scope = WireScope(
            execution_domain_ref=scope.execution_domain_ref,
            account_refs=("account-a", "account-b"),
            strategy_ref=scope.strategy_ref,
            portfolio_ref=scope.portfolio_ref,
        )
        multi_account = _register_route(
            registry,
            identity=identity,
            scope=multi_account_scope,
            instance_id="risk-owner",
            port="service.group_reservations",
            scope_ref="account-scope-a",
            operations=("reservations.reserve",),
            suffix="multi-account",
        )
        assert multi_account["ok"] is False
        assert multi_account["code"] == "SCOPE_DENIED"

        # Query-only registration cannot seed the owner-local logical run.
        query_registry = ServiceRegistry(
            tmp_path / "reservation-query-only.sqlite",
            service_id="risk-service",
            capabilities=(capability,),
            grants={"coordinator": (PrincipalGrant(identity, (grant,)),)},
            policy_version="policy-v1",
            requirements_version="requirements-v1",
            trusted_group_reservation_policies=(policy,),
        )
        try:
            query_only = _register_route(
                query_registry,
                identity=identity,
                scope=scope,
                instance_id="risk-owner",
                port="service.group_reservations",
                scope_ref="account-scope-a",
                operations=("reservations.query",),
                suffix="query-only",
            )
            assert query_only["ok"] is True
            with pytest.raises(ServiceRegistryGroupWritePolicyError):
                query_registry.bind_trusted_group_run(
                    "live:venue-a",
                    "strategy-a",
                    "account-a",
                    registration_id=query_only["result"]["registration_id"],
                )
            assert query_registry.strategy_write_run_id(
                "live:venue-a", "strategy-a", "account-a"
            ) is None
        finally:
            query_registry.close()

        with pytest.raises(ValueError, match="group reservation policy scope is invalid"):
            TrustedGroupReservationPolicy(
                instance_id="risk-owner",
                principal_ref="coordinator",
                identity=identity,
                scope_ref="account-scope-a",
                scope=multi_account_scope,
                run_id="run-a",
                account_ref="account-a",
            )
    finally:
        registry.close()


def test_group_resolution_requires_separate_exact_operator_policy_and_run(
    tmp_path: Path,
) -> None:
    scope = _group_scope()
    identity = WireIdentity(
        WireRole.SERVICE,
        principal_ref="group-coordinator",
        actor_ref="group-coordinator",
    )
    submit_policy = TrustedGroupWritePolicy(
        instance_id="account-owner",
        principal_ref="group-coordinator",
        identity=identity,
        scope_ref="account-scope-a",
        scope=scope,
        run_id="run-a",
    )
    resolution_policy = TrustedGroupResolutionPolicy(
        instance_id="account-owner",
        principal_ref="group-coordinator",
        identity=identity,
        scope_ref="account-scope-a",
        scope=scope,
        run_id="run-a",
        account_ref="account-a",
    )
    operations = frozenset(
        {"orders.submit_group_leg", "orders.resolve_group_leg"}
    )
    capability = ServiceCapability(
        "account-owner", "service.order_entry", operations, operations
    )
    grant = ResourceGrant(
        "account-owner", "service.order_entry", "account-scope-a", scope, operations
    )
    registry = ServiceRegistry(
        tmp_path / "resolution-policy.sqlite",
        service_id="execution-service",
        capabilities=(capability,),
        grants={"group-coordinator": (PrincipalGrant(identity, (grant,)),)},
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        trusted_group_write_policies=(submit_policy,),
        trusted_group_resolution_policies=(resolution_policy,),
    )
    calls: list[str] = []
    registry.register_handler(
        "account-owner",
        "orders.resolve_group_leg",
        lambda context, arguments: calls.append("orders.resolve_group_leg")
        or {"resolved": True},
    )
    try:
        registered = _register_route(
            registry,
            identity=identity,
            scope=scope,
            instance_id="account-owner",
            port="service.order_entry",
            scope_ref="account-scope-a",
            operations=("orders.submit_group_leg", "orders.resolve_group_leg"),
            suffix="resolution",
        )
        assert registered["ok"] is True
        registry.bind_trusted_group_run(
            "live:venue-a",
            "strategy-a",
            "account-a",
            registration_id=registered["result"]["registration_id"],
        )
        resolved = _call_registered(
            registry,
            identity=identity,
            scope=scope,
            registration=registered,
            instance_id="account-owner",
            operation="orders.resolve_group_leg",
        )
        assert resolved["ok"] is True
        assert resolved["result"] == {"resolved": True}
        assert calls == ["orders.resolve_group_leg"]
        # Same authenticated ACL and capability do not create the separate
        # resolution permission when the local trusted policy is absent.
        denied_registry = ServiceRegistry(
            tmp_path / "resolution-policy-absent.sqlite",
            service_id="execution-service",
            capabilities=(capability,),
            grants={"group-coordinator": (PrincipalGrant(identity, (grant,)),)},
            policy_version="policy-v1",
            requirements_version="requirements-v1",
            trusted_group_write_policies=(submit_policy,),
        )
        try:
            denied = _register_route(
                denied_registry,
                identity=identity,
                scope=scope,
                instance_id="account-owner",
                port="service.order_entry",
                scope_ref="account-scope-a",
                operations=("orders.submit_group_leg", "orders.resolve_group_leg"),
                suffix="resolution-without-policy",
            )
            assert denied["ok"] is False
            assert denied["code"] == "SCOPE_DENIED"
        finally:
            denied_registry.close()
    finally:
        registry.close()


def test_registry_rejects_duplicate_and_conflicting_trusted_group_routes(
    tmp_path: Path,
) -> None:
    scope = _group_scope()
    first_identity = WireIdentity(
        WireRole.SERVICE,
        principal_ref="reservation-owner-a",
        actor_ref="reservation-owner-a",
    )
    first = TrustedGroupReservationPolicy(
        instance_id="risk-owner-a",
        principal_ref=first_identity.principal_ref,
        identity=first_identity,
        scope_ref="account-scope-a",
        scope=scope,
        run_id="run-a",
        account_ref="account-a",
    )
    operations = first.operations

    def make_registry(path: Path, policies: tuple[TrustedGroupReservationPolicy, ...]):
        capability_map: dict[tuple[str, str], ServiceCapability] = {}
        resources_by_principal: dict[str, tuple[WireIdentity, list[ResourceGrant]]] = {}
        for policy in policies:
            capability_map.setdefault(
                (policy.instance_id, policy.port),
                ServiceCapability(
                    policy.instance_id,
                    policy.port,
                    operations,
                    policy.write_operations,
                ),
            )
            grant = ResourceGrant(
                policy.instance_id,
                policy.port,
                policy.scope_ref,
                policy.scope,
                operations,
            )
            identity, principal_resources = resources_by_principal.setdefault(
                policy.principal_ref, (policy.identity, [])
            )
            assert identity == policy.identity
            if grant not in principal_resources:
                principal_resources.append(grant)
        grants = {
            principal: (PrincipalGrant(identity, tuple(resources)),)
            for principal, (identity, resources) in resources_by_principal.items()
        }
        return ServiceRegistry(
            path,
            service_id="risk-service",
            capabilities=tuple(capability_map.values()),
            grants=grants,
            policy_version="policy-v1",
            requirements_version="requirements-v1",
            trusted_group_reservation_policies=policies,
        )

    with pytest.raises(ValueError, match="duplicate trusted group route policy"):
        make_registry(tmp_path / "duplicate-policy.sqlite", (first, first))

    second_identity = WireIdentity(
        WireRole.SERVICE,
        principal_ref="reservation-owner-b",
        actor_ref="reservation-owner-b",
    )
    second = TrustedGroupReservationPolicy(
        instance_id="risk-owner-b",
        principal_ref=second_identity.principal_ref,
        identity=second_identity,
        scope_ref="account-scope-b",
        scope=scope,
        run_id="run-b",
        account_ref="account-a",
    )
    with pytest.raises(ValueError, match="conflicting trusted group logical runs"):
        make_registry(tmp_path / "conflicting-policy.sqlite", (first, second))


def test_multi_account_registration_snapshot_requires_one_durable_run_for_every_account(
    tmp_path: Path,
) -> None:
    path = tmp_path / "group-coordinator-vector.sqlite"
    identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        principal_ref="strategy-client",
        actor_ref="strategy-a",
    )
    template = WireScope(
        execution_domain_ref="live:venue-a",
        account_refs=("account-a", "account-b"),
        strategy_ref="strategy-a",
        portfolio_ref="portfolio-a",
    )
    run_scope = WireScope(
        execution_domain_ref=template.execution_domain_ref,
        account_refs=template.account_refs,
        strategy_ref=template.strategy_ref,
        run_id="run-vector-a",
        portfolio_ref=template.portfolio_ref,
    )
    operations = frozenset({"groups.submit", "groups.query"})
    capability = ServiceCapability(
        "group-coordinator",
        "service.order_group_entry",
        operations,
        frozenset({"groups.submit"}),
    )
    grant = ResourceGrant(
        "group-coordinator",
        "service.order_group_entry",
        "strategy-scope-vector-a",
        template,
        operations,
    )
    grants = {"strategy-client": (PrincipalGrant(identity, (grant,)),)}

    def make_registry() -> ServiceRegistry:
        return ServiceRegistry(
            path,
            service_id="group-coordinator-service",
            capabilities=(capability,),
            grants=grants,
            policy_version="policy-v1",
            requirements_version="requirements-v1",
            lease_seconds=30,
            clock=lambda: 100.0,
        )

    registry = make_registry()
    registration = registry.handle(
        "strategy-client",
        _request(
            "service.register",
            {
                "registration_key": "vector-registration-key",
                "client_session_id": "vector-session",
                "instance_id": "group-coordinator",
                "port": "service.order_group_entry",
                "scope_ref": "strategy-scope-vector-a",
                "operations": tuple(sorted(operations)),
            },
            identity=identity,
            scope=run_scope,
        ),
    )
    assert registration["ok"] is True, registration
    registration_id = registration["result"]["registration_id"]
    try:
        before = registry._db.total_changes
        contexts = registry.active_registration_contexts(
            instance_id="group-coordinator", port="service.order_group_entry"
        )
        assert len(contexts) == 1
        assert contexts[0].context.registration_id == registration_id
        assert contexts[0].strategy_write_run_id == "run-vector-a"
        assert registry._db.total_changes == before

        with registry._transaction():
            registry._db.execute(
                "DELETE FROM strategy_write_claims WHERE execution_domain_ref=? "
                "AND strategy_ref=? AND account_ref=?",
                ("live:venue-a", "strategy-a", "account-b"),
            )
        before = registry._db.total_changes
        missing = registry.active_registration_contexts(
            instance_id="group-coordinator", port="service.order_group_entry"
        )
        assert len(missing) == 1
        assert missing[0].strategy_write_run_id is None
        assert registry._db.total_changes == before

        with registry._transaction():
            registry._db.execute(
                "INSERT INTO strategy_write_claims "
                "(execution_domain_ref, strategy_ref, account_ref, run_id, created_at, "
                "active_registration_id) VALUES(?,?,?,?,?,?)",
                (
                    "live:venue-a",
                    "strategy-a",
                    "account-b",
                    "run-vector-other",
                    100.0,
                    registration_id,
                ),
            )
        before = registry._db.total_changes
        conflicting = registry.active_registration_contexts(
            instance_id="group-coordinator", port="service.order_group_entry"
        )
        assert len(conflicting) == 1
        assert conflicting[0].strategy_write_run_id is None
        assert registry._db.total_changes == before

        with registry._transaction():
            registry._db.execute(
                "UPDATE strategy_write_claims SET run_id=? WHERE execution_domain_ref=? "
                "AND strategy_ref=? AND account_ref=?",
                ("run-vector-a", "live:venue-a", "strategy-a", "account-b"),
            )
    finally:
        registry.close()

    restarted = make_registry()
    try:
        assert restarted.service_epoch != registration["result"]["service_epoch"]
        assert restarted.active_registration_contexts(
            instance_id="group-coordinator", port="service.order_group_entry"
        ) == ()
        for account_ref in ("account-a", "account-b"):
            assert restarted.strategy_write_run_id(
                "live:venue-a", "strategy-a", account_ref
            ) == "run-vector-a"
        claims = restarted._db.execute(
            "SELECT run_id, active_registration_id FROM strategy_write_claims "
            "WHERE execution_domain_ref=? AND strategy_ref=? ORDER BY account_ref",
            ("live:venue-a", "strategy-a"),
        ).fetchall()
        assert [(row["run_id"], row["active_registration_id"]) for row in claims] == [
            ("run-vector-a", None),
            ("run-vector-a", None),
        ]
    finally:
        restarted.close()

