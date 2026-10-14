from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
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

from bt_api_py.service_registry import (
    SERVICE_ERROR_CODES,
    PrincipalGrant,
    ResourceGrant,
    ServiceCallContext,
    ServiceCapability,
    ServiceRegistry,
    ServiceRegistryBusyError,
)

_SENT_AT = "2026-10-13T12:00:00Z"


def _identity(
    role: WireRole = WireRole.STRATEGY_CLIENT,
    *,
    principal_ref: str = "principal-a",
    actor_ref: str = "actor-a",
    delegate_ref: str | None = None,
) -> WireIdentity:
    return WireIdentity(role, principal_ref, actor_ref, delegate_ref)


def _strategy_scope(
    *, account: str = "account-a", run_id: str | None = "run-a", strategy: str = "strategy-a"
) -> WireScope:
    return WireScope(
        execution_domain_ref="live:venue-a",
        account_ref=account,
        strategy_ref=strategy,
        run_id=run_id,
    )


def _request(
    operation: str,
    payload: dict[str, Any],
    *,
    identity: WireIdentity,
    scope: WireScope,
    schema_id: str | None = None,
) -> WireEnvelope:
    return WireEnvelope(
        header=WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=f"message-{_request.next_id}",
            correlation_id=None,
            identity=identity,
            scope=scope,
            operation=operation,
            schema_id=schema_id
            or ("sdk.service.control" if operation.startswith("service.") else "sdk.service.call"),
            schema_version=1,
            sent_at_utc=_SENT_AT,
        ),
        payload=payload,
    )


_request.next_id = 0  # type: ignore[attr-defined]


def _next_request(*args: Any, **kwargs: Any) -> WireEnvelope:
    _request.next_id += 1  # type: ignore[attr-defined]
    return _request(*args, **kwargs)


def _make_registry(
    path: Path,
    *,
    clock=lambda: 100.0,
    identity: WireIdentity | None = None,
    scope_template: WireScope | None = None,
    capability_operations: frozenset[str] = frozenset(
        {"account.read", "orders.submit", "orders.cancel"}
    ),
    grant_operations: frozenset[str] = frozenset({"account.read", "orders.submit"}),
    write_operations: frozenset[str] = frozenset({"orders.submit", "orders.cancel"}),
    principal: str = "curve-a",
    max_sessions: int = 1024,
    max_registration_keys: int = 8192,
) -> ServiceRegistry:
    identity = identity or _identity()
    if scope_template is None:
        scope_template = _strategy_scope(run_id=None)
    capability = ServiceCapability(
        "account-instance",
        "service.account_read",
        capability_operations,
        write_operations & capability_operations,
    )
    resource = ResourceGrant(
        "account-instance",
        "service.account_read",
        "account-grant-a",
        scope_template,
        grant_operations,
    )
    return ServiceRegistry(
        path,
        service_id="account-service-a",
        capabilities=(capability,),
        grants={principal: (PrincipalGrant(identity, (resource,)),)},
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        lease_seconds=10,
        clock=clock,
        max_sessions=max_sessions,
        max_registration_keys=max_registration_keys,
    )


def _register(
    registry: ServiceRegistry,
    *,
    identity: WireIdentity | None = None,
    scope: WireScope | None = None,
    principal: str = "curve-a",
    key: str = "registration-a",
    client_session_id: str = "client-session-a",
    operations: tuple[str, ...] = ("account.read", "orders.submit"),
    instance_id: str = "account-instance",
    port: str = "service.account_read",
    scope_ref: str = "account-grant-a",
) -> dict[str, Any]:
    identity = identity or _identity()
    scope = scope or _strategy_scope()
    response = registry.handle(
        principal,
        _next_request(
            "service.register",
            {
                "registration_key": key,
                "client_session_id": client_session_id,
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


def _business(
    registry: ServiceRegistry,
    registration: dict[str, Any],
    *,
    identity: WireIdentity | None = None,
    scope: WireScope | None = None,
    principal: str = "curve-a",
    operation: str = "orders.submit",
    instance_id: str = "account-instance",
) -> dict[str, Any]:
    identity = identity or _identity()
    scope = scope or _strategy_scope()
    response = registry.handle(
        principal,
        _next_request(
            operation,
            {
                "registration_id": registration["registration_id"],
                "service_epoch": registration["service_epoch"],
                "instance_id": instance_id,
                "arguments": {"symbol": "BTC-USD"},
            },
            identity=identity,
            scope=scope,
        ),
    )
    return dict(response)


def test_discovery_and_dispatch_bind_authenticated_identity_scope_and_operations(
    tmp_path: Path,
) -> None:
    registry = _make_registry(tmp_path / "registry.sqlite")
    calls: list[tuple[ServiceCallContext, dict[str, Any]]] = []
    registry.register_handler(
        "account-instance",
        "orders.submit",
        lambda context, arguments: calls.append((context, dict(arguments))) or {"accepted": True},
    )
    try:
        discovery = registry.handle(
            "curve-a",
            _next_request("service.discover", {}, identity=_identity(), scope=_strategy_scope()),
        )
        assert discovery["ok"] is True
        assert discovery["result"]["protocol_version"] == 2
        assert discovery["result"]["service_epoch"] == registry.service_epoch
        assert len(registry.service_epoch) == 32 and registry.service_epoch[0].isalnum()
        assert discovery["result"]["capabilities"] == [
            {
                "instance_id": "account-instance",
                "port": "service.account_read",
                "operations": ["account.read", "orders.cancel", "orders.submit"],
            }
        ]
        forged = registry.handle(
            "curve-a",
            _next_request(
                "service.discover",
                {},
                identity=replace(_identity(), actor_ref="attacker"),
                scope=_strategy_scope(),
            ),
        )
        assert forged["code"] == "DENIED"

        ungranted = _register(registry, operations=("orders.cancel",))
        assert ungranted["code"] == "SCOPE_DENIED"
        missing_capability = _register(registry, operations=("orders.execute_unlisted",))
        assert missing_capability["code"] == "CAPABILITY_UNAVAILABLE"
        registration = _register(registry)
        assert registration["ok"] is True
        assert len(registration["result"]["registration_id"]) == 32
        assert registration["result"]["registration_id"][0].isalnum()
        assert registration["result"]["operations"] == ["account.read", "orders.submit"]
        assert registration["result"]["policy_version"] == "policy-v1"
        assert registration["result"]["requirements_version"] == "requirements-v1"

        dispatched = _business(registry, registration["result"])
        assert dispatched == {"ok": True, "code": "OK", "result": {"accepted": True}}
        context, arguments = calls[0]
        assert context.identity == _identity()
        assert context.scope == _strategy_scope()
        assert context.registration_id == registration["result"]["registration_id"]
        assert context.principal_ref == "curve-a"
        assert arguments == {"symbol": "BTC-USD"}

        cross_account = _business(
            registry, registration["result"], scope=_strategy_scope(account="account-b")
        )
        assert cross_account["code"] == "SCOPE_DENIED"
        forged_actor = _business(
            registry,
            registration["result"],
            identity=replace(_identity(), actor_ref="attacker"),
        )
        assert forged_actor["code"] == "DENIED"
        ungranted_operation = _business(registry, registration["result"], operation="orders.cancel")
        assert ungranted_operation["code"] == "SCOPE_DENIED"
        assert len(calls) == 1
    finally:
        registry.close()


def test_registration_idempotency_conflict_and_strategy_write_claim_survive_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "durable-registry.sqlite"
    registry = _make_registry(path)
    try:
        first = _register(registry)
        repeated = _register(registry)
        assert repeated["result"]["registration_id"] == first["result"]["registration_id"]
        assert repeated["result"]["expires_at"] == first["result"]["expires_at"]
        competing_same_run = _register(
            registry,
            key="second-key-same-run",
            client_session_id="second-session-same-run",
        )
        assert competing_same_run["code"] == "DUPLICATE_ACTIVE_RUN"
        conflicting_key_use = _register(
            registry, operations=("account.read",), key="registration-a"
        )
        assert conflicting_key_use["code"] == "CONFLICT"

        unregister = registry.handle(
            "curve-a",
            _next_request(
                "service.unregister",
                {
                    "registration_id": first["result"]["registration_id"],
                    "service_epoch": first["result"]["service_epoch"],
                },
                identity=_identity(),
                scope=_strategy_scope(),
            ),
        )
        assert unregister["ok"] is True
        new_run = _register(
            registry,
            key="registration-run-b",
            client_session_id="client-session-b",
            scope=_strategy_scope(run_id="run-b"),
        )
        assert new_run["code"] == "DUPLICATE_ACTIVE_RUN"
    finally:
        old_epoch = registry.service_epoch
        registry.close()

    restarted = _make_registry(path)
    try:
        assert restarted.service_epoch != old_epoch
        old_request = _business(restarted, first["result"])
        assert old_request["code"] == "STALE_EPOCH"
        another_run = _register(
            restarted,
            key="registration-after-restart",
            client_session_id="client-session-after-restart",
            scope=_strategy_scope(run_id="run-c"),
        )
        assert another_run["code"] == "DUPLICATE_ACTIVE_RUN"
        same_run_new_lease = _register(
            restarted,
            key="registration-after-restart-same-run",
            client_session_id="client-session-after-restart-same-run",
        )
        assert same_run_new_lease["ok"] is True
    finally:
        restarted.close()


def test_concurrent_strategy_writer_registrations_are_serialized(tmp_path: Path) -> None:
    registry = _make_registry(tmp_path / "concurrent-writer.sqlite")
    try:

        def register(key: str) -> dict[str, Any]:
            return _register(
                registry,
                key=key,
                client_session_id=f"session-{key}",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(register, ("writer-a", "writer-b")))
        assert sorted(response["code"] for response in responses) == [
            "DUPLICATE_ACTIVE_RUN",
            "OK",
        ]
    finally:
        registry.close()


def test_expiry_renews_by_epoch_and_old_registration_cannot_dispatch(tmp_path: Path) -> None:
    now = [100.0]
    registry = _make_registry(tmp_path / "expiry.sqlite", clock=lambda: now[0])
    try:
        registration = _register(registry, operations=("account.read",))
        original = registration["result"]
        renew = registry.handle(
            "curve-a",
            _next_request(
                "service.renew",
                {
                    "registration_id": original["registration_id"],
                    "service_epoch": original["service_epoch"],
                },
                identity=_identity(),
                scope=_strategy_scope(),
            ),
        )
        assert renew["ok"] is True
        assert renew["result"]["expires_at"] == 110.0

        registry.register_handler(
            "account-instance", "account.read", lambda _ctx, _args: {"value": 1}
        )
        now[0] = 111.0
        assert (
            _business(registry, renew["result"], operation="account.read")["code"]
            == "NOT_REGISTERED"
        )

        replacement = _register(registry, operations=("account.read",))
        assert replacement["ok"] is True
        assert replacement["result"]["registration_id"] != original["registration_id"]
        assert _business(registry, original, operation="account.read")["code"] == "NOT_REGISTERED"
        assert _business(registry, replacement["result"], operation="account.read")["result"] == {
            "value": 1
        }
    finally:
        registry.close()


def test_app_workers_do_not_consume_strategy_writer_claims(tmp_path: Path) -> None:
    path = tmp_path / "workers.sqlite"
    app_identity = _identity(
        WireRole.APPLICATION_CLIENT, principal_ref="app", actor_ref="app-worker"
    )
    app_scope = WireScope(
        execution_domain_ref="live:venue-a",
        account_ref="account-a",
        manual_book_ref="app-book-a",
    )
    app_account_only_scope = WireScope(execution_domain_ref="live:venue-a", account_ref="account-a")
    strategy_identity = _identity()
    strategy_scope = _strategy_scope(run_id=None)
    capability = ServiceCapability(
        "shared-instance",
        "service.order_entry",
        frozenset({"orders.submit"}),
        frozenset({"orders.submit"}),
    )
    grants = {
        "curve-app": (
            PrincipalGrant(
                app_identity,
                (
                    ResourceGrant(
                        "shared-instance",
                        "service.order_entry",
                        "app-account-a",
                        app_scope,
                        frozenset({"orders.submit"}),
                    ),
                    ResourceGrant(
                        "shared-instance",
                        "service.order_entry",
                        "app-unallocated-account-a",
                        app_account_only_scope,
                        frozenset({"orders.submit"}),
                    ),
                    ResourceGrant(
                        "shared-instance",
                        "service.order_entry",
                        "app-strategy-a",
                        _strategy_scope(run_id=None),
                        frozenset({"orders.submit"}),
                    ),
                ),
            ),
        ),
        "curve-strategy": (
            PrincipalGrant(
                strategy_identity,
                (
                    ResourceGrant(
                        "shared-instance",
                        "service.order_entry",
                        "strategy-account-a",
                        strategy_scope,
                        frozenset({"orders.submit"}),
                    ),
                ),
            ),
        ),
    }
    registry = ServiceRegistry(
        path,
        service_id="shared-service",
        capabilities=(capability,),
        grants=grants,
        policy_version="policy-v1",
        requirements_version="requirements-v1",
    )
    try:
        first_worker = _register(
            registry,
            identity=app_identity,
            scope=app_scope,
            principal="curve-app",
            key="worker-a",
            client_session_id="app-session-a",
            operations=("orders.submit",),
            instance_id="shared-instance",
            port="service.order_entry",
            scope_ref="app-account-a",
        )
        second_worker = _register(
            registry,
            identity=app_identity,
            scope=app_scope,
            principal="curve-app",
            key="worker-b",
            client_session_id="app-session-b",
            operations=("orders.submit",),
            instance_id="shared-instance",
            port="service.order_entry",
            scope_ref="app-account-a",
        )
        strategy = _register(
            registry,
            identity=strategy_identity,
            scope=_strategy_scope(),
            principal="curve-strategy",
            key="strategy-run-a",
            client_session_id="strategy-session-a",
            operations=("orders.submit",),
            instance_id="shared-instance",
            port="service.order_entry",
            scope_ref="strategy-account-a",
        )
        strategy_impersonation = _register(
            registry,
            identity=app_identity,
            scope=_strategy_scope(run_id=None),
            principal="curve-app",
            key="app-strategy-write",
            client_session_id="app-strategy-session",
            operations=("orders.submit",),
            instance_id="shared-instance",
            port="service.order_entry",
            scope_ref="app-strategy-a",
        )
        unallocated_account_write = _register(
            registry,
            identity=app_identity,
            scope=app_account_only_scope,
            principal="curve-app",
            key="app-account-only-write",
            client_session_id="app-account-only-session",
            operations=("orders.submit",),
            instance_id="shared-instance",
            port="service.order_entry",
            scope_ref="app-unallocated-account-a",
        )
        assert first_worker["ok"] and second_worker["ok"] and strategy["ok"]
        assert strategy_impersonation["code"] == "SCOPE_DENIED"
        assert unallocated_account_write["code"] == "SCOPE_DENIED"
    finally:
        registry.close()


def test_service_portfolio_writes_wait_for_coordinator_generation_claim(
    tmp_path: Path,
) -> None:
    principal = "curve-group-coordinator"
    identity = _identity(
        WireRole.SERVICE, principal_ref="group-coordinator", actor_ref="group-coordinator"
    )
    portfolio_scope = WireScope(
        execution_domain_ref="live:venue-a",
        account_refs=("account-a", "account-b"),
        portfolio_ref="portfolio-a",
    )
    capability = ServiceCapability(
        "group-instance",
        "service.order_group_entry",
        frozenset({"orders.submit"}),
        frozenset({"orders.submit"}),
    )
    resource = ResourceGrant(
        "group-instance",
        "service.order_group_entry",
        "portfolio-grant-a",
        portfolio_scope,
        frozenset({"orders.submit"}),
    )
    registry = ServiceRegistry(
        tmp_path / "portfolio-coordinator.sqlite",
        service_id="group-service",
        capabilities=(capability,),
        grants={principal: (PrincipalGrant(identity, (resource,)),)},
        policy_version="policy-v1",
        requirements_version="requirements-v1",
    )
    try:
        first_coordinator = _register(
            registry,
            identity=identity,
            scope=portfolio_scope,
            principal=principal,
            key="coordinator-generation-a",
            client_session_id="coordinator-session-a",
            operations=("orders.submit",),
            instance_id="group-instance",
            port="service.order_group_entry",
            scope_ref="portfolio-grant-a",
        )
        competing_coordinator = _register(
            registry,
            identity=identity,
            scope=portfolio_scope,
            principal=principal,
            key="coordinator-generation-b",
            client_session_id="coordinator-session-b",
            operations=("orders.submit",),
            instance_id="group-instance",
            port="service.order_group_entry",
            scope_ref="portfolio-grant-a",
        )
        assert first_coordinator["code"] == "SCOPE_DENIED"
        assert competing_coordinator["code"] == "SCOPE_DENIED"
    finally:
        registry.close()


def test_manual_book_and_paper_scope_are_exact_trusted_resources(tmp_path: Path) -> None:
    identity = _identity(
        WireRole.APPLICATION_CLIENT, principal_ref="paper-app", actor_ref="operator"
    )
    paper_scope = WireScope(
        execution_domain_ref="paper:venue-a",
        account_ref="paper-account-a",
        manual_book_ref="manual-book-a",
    )
    capability = ServiceCapability(
        "paper-instance",
        "service.order_entry",
        frozenset({"orders.submit"}),
        frozenset({"orders.submit"}),
    )
    resource = ResourceGrant(
        "paper-instance",
        "service.order_entry",
        "paper-book-a",
        paper_scope,
        frozenset({"orders.submit"}),
    )
    registry = ServiceRegistry(
        tmp_path / "paper.sqlite",
        service_id="paper-service",
        capabilities=(capability,),
        grants={"curve-paper": (PrincipalGrant(identity, (resource,)),)},
        policy_version="policy-v1",
        requirements_version="requirements-v1",
    )
    try:
        paper = _register(
            registry,
            identity=identity,
            scope=paper_scope,
            principal="curve-paper",
            key="paper-registration",
            client_session_id="paper-session",
            operations=("orders.submit",),
            instance_id="paper-instance",
            port="service.order_entry",
            scope_ref="paper-book-a",
        )
        assert paper["ok"] is True
        live_scope = replace(paper_scope, execution_domain_ref="live:venue-a")
        assert (
            _register(
                registry,
                identity=identity,
                scope=live_scope,
                principal="curve-paper",
                key="live-registration",
                client_session_id="live-session",
                operations=("orders.submit",),
                instance_id="paper-instance",
                port="service.order_entry",
                scope_ref="paper-book-a",
            )["code"]
            == "SCOPE_DENIED"
        )
        other_book = replace(paper_scope, manual_book_ref="manual-book-b")
        assert (
            _register(
                registry,
                identity=identity,
                scope=other_book,
                principal="curve-paper",
                key="other-book-registration",
                client_session_id="other-book-session",
                operations=("orders.submit",),
                instance_id="paper-instance",
                port="service.order_entry",
                scope_ref="paper-book-a",
            )["code"]
            == "SCOPE_DENIED"
        )
    finally:
        registry.close()


def test_not_ready_blocks_registration_renewal_and_dispatch_but_allows_unregister(
    tmp_path: Path,
) -> None:
    registry = _make_registry(tmp_path / "ready.sqlite")
    calls: list[int] = []
    registry.register_handler(
        "account-instance", "orders.submit", lambda _ctx, _args: calls.append(1) or {}
    )
    try:
        registration = _register(registry)
        result = registration["result"]
        registry.set_ready(False)
        assert (
            registry.handle(
                "curve-a",
                _next_request(
                    "service.discover", {}, identity=_identity(), scope=_strategy_scope()
                ),
            )["result"]["ready"]
            is False
        )
        assert _business(registry, result)["code"] == "POLICY_REQUIREMENT_UNMET"
        assert (
            registry.handle(
                "curve-a",
                _next_request(
                    "service.renew",
                    {
                        "registration_id": result["registration_id"],
                        "service_epoch": result["service_epoch"],
                    },
                    identity=_identity(),
                    scope=_strategy_scope(),
                ),
            )["code"]
            == "POLICY_REQUIREMENT_UNMET"
        )
        assert (
            _register(registry, key="while-not-ready", client_session_id="session-not-ready")[
                "code"
            ]
            == "POLICY_REQUIREMENT_UNMET"
        )
        assert calls == []
        assert (
            registry.handle(
                "curve-a",
                _next_request(
                    "service.unregister",
                    {
                        "registration_id": result["registration_id"],
                        "service_epoch": result["service_epoch"],
                    },
                    identity=_identity(),
                    scope=_strategy_scope(),
                ),
            )["ok"]
            is True
        )
    finally:
        registry.close()


def test_handler_exceptions_are_finite_and_registry_bounds_are_enforced(tmp_path: Path) -> None:
    registry = _make_registry(tmp_path / "bounded.sqlite", max_sessions=1)
    try:
        with pytest.raises(ValueError):
            registry.register_handler(
                "account-instance", "orders.execute_unlisted", lambda _ctx, _args: {}
            )
        registry.register_handler(
            "account-instance",
            "orders.submit",
            lambda _ctx, _args: (_ for _ in ()).throw(RuntimeError("private detail")),
        )
        registry.register_handler(
            "account-instance",
            "account.read",
            lambda _ctx, _args: {"data": "x" * (901 * 1024)},
        )
        first = _register(registry)
        assert _business(registry, first["result"])["code"] == "HANDLER_FAILED"
        assert "private detail" not in repr(_business(registry, first["result"]))
        too_large = _business(registry, first["result"], operation="account.read")
        assert too_large["code"] == "HANDLER_FAILED"
        full = _register(
            registry,
            key="second-registration",
            client_session_id="second-session",
            operations=("account.read",),
        )
        assert full["code"] == "CAPACITY"
        assert (
            frozenset(
                {
                    "CAPACITY",
                    "CAPABILITY_UNAVAILABLE",
                    "CLOSED",
                    "CONFLICT",
                    "DENIED",
                    "DUPLICATE_ACTIVE_RUN",
                    "HANDLER_FAILED",
                    "INVALID_REQUEST",
                    "NOT_REGISTERED",
                    "OK",
                    "POLICY_REQUIREMENT_UNMET",
                    "SCOPE_DENIED",
                    "STALE_EPOCH",
                    "UNKNOWN_OPERATION",
                    "UNAVAILABLE",
                }
            )
            == SERVICE_ERROR_CODES
        )
    finally:
        registry.close()


def test_durable_idempotency_key_limit_never_evicts_collision_history(tmp_path: Path) -> None:
    registry = _make_registry(
        tmp_path / "key-limit.sqlite",
        max_sessions=2,
        max_registration_keys=1,
    )
    try:
        first = _register(registry, operations=("account.read",))
        assert first["ok"] is True
        unregistered = registry.handle(
            "curve-a",
            _next_request(
                "service.unregister",
                {
                    "registration_id": first["result"]["registration_id"],
                    "service_epoch": first["result"]["service_epoch"],
                },
                identity=_identity(),
                scope=_strategy_scope(),
            ),
        )
        assert unregistered["ok"] is True

        second_key = _register(
            registry,
            key="registration-b",
            client_session_id="client-session-b",
            operations=("account.read",),
        )
        assert second_key["code"] == "CAPACITY"
        same_key_retry = _register(registry, operations=("account.read",))
        assert same_key_retry["ok"] is True
        assert same_key_retry["result"]["registration_id"] != first["result"]["registration_id"]
    finally:
        registry.close()


def test_database_has_one_live_owner_and_persisted_epoch_fences_old_instance(
    tmp_path: Path,
) -> None:
    path = tmp_path / "single-owner.sqlite"
    registry = _make_registry(path)
    try:
        with pytest.raises(ServiceRegistryBusyError):
            _make_registry(path)

        # Simulate another authoritative process replacing the persisted epoch
        # after this instance's startup. Every request must be fenced before it
        # can renew or invoke an old registration.
        with sqlite3.connect(path) as other:
            other.execute("UPDATE registry_meta SET value=? WHERE key='service_epoch'", ("0" * 32,))
        stale = registry.handle(
            "curve-a",
            _next_request("service.discover", {}, identity=_identity(), scope=_strategy_scope()),
        )
        assert stale["code"] == "STALE_EPOCH"
    finally:
        registry.close()

    reopened = _make_registry(path)
    try:
        assert reopened.service_epoch != "0" * 32
    finally:
        reopened.close()


def test_invalid_schema_and_unknown_control_are_finite(tmp_path: Path) -> None:
    registry = _make_registry(tmp_path / "schema.sqlite")
    try:
        wrong_schema = registry.handle(
            "curve-a",
            _next_request(
                "service.discover",
                {},
                identity=_identity(),
                scope=_strategy_scope(),
                schema_id="sdk.service.call",
            ),
        )
        assert wrong_schema == {"ok": False, "code": "INVALID_REQUEST", "result": {}}
        unknown = registry.handle(
            "curve-a",
            _next_request(
                "service.remote_register_handler",
                {},
                identity=_identity(),
                scope=_strategy_scope(),
            ),
        )
        assert unknown["code"] == "UNKNOWN_OPERATION"
    finally:
        registry.close()


def test_transport_printable_principal_refs_are_not_limited_to_registry_ids(
    tmp_path: Path,
) -> None:
    principal = "afi:desk/+worker"
    registry = _make_registry(tmp_path / "printable-principal.sqlite", principal=principal)
    try:
        result = _register(registry, principal=principal)
        assert result["ok"] is True
    finally:
        registry.close()
