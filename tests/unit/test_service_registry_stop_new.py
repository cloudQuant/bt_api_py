from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_py.service_migration_gate import arm_migration_cutover, read_migration_marker

from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
    ServiceRegistryStopNewError,
    validate_capabilities_and_grants,
)


def _request(
    operation: str,
    payload: dict[str, Any],
    *,
    identity: WireIdentity,
    scope: WireScope,
) -> WireEnvelope:
    return WireEnvelope(
        header=WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=uuid4().hex,
            correlation_id=None,
            identity=identity,
            scope=scope,
            operation=operation,
            schema_id="sdk.service.control" if operation.startswith("service.") else "sdk.service.call",
            schema_version=1,
            sent_at_utc="2026-10-17T00:00:00Z",
        ),
        payload=payload,
    )


def _make_registry(
    path: Path, *, migration_deployment_epoch: str | None = None
) -> tuple[ServiceRegistry, WireIdentity, WireScope]:
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-key", "strategy:alpha")
    scope_template = WireScope(
        "live:venue-a", account_ref="account-a", strategy_ref="strategy-alpha"
    )
    scope = WireScope(
        "live:venue-a",
        account_ref="account-a",
        strategy_ref="strategy-alpha",
        run_id="run-2026-10-17-a",
    )
    capability = ServiceCapability(
        "execution-alpha",
        "service.order_entry",
        frozenset({"orders.submit", "orders.cancel"}),
        frozenset({"orders.submit", "orders.cancel"}),
    )
    grant = ResourceGrant(
        "execution-alpha",
        "service.order_entry",
        "strategy-alpha-orders",
        scope_template,
        frozenset({"orders.submit", "orders.cancel"}),
    )
    registry = ServiceRegistry(
        path,
        service_id="venue-a-service",
        capabilities=(capability,),
        grants={"strategy-key": (PrincipalGrant(identity, (grant,)),)},
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        migration_deployment_epoch=migration_deployment_epoch,
    )
    return registry, identity, scope


def _register(
    registry: ServiceRegistry,
    identity: WireIdentity,
    scope: WireScope,
    *,
    operations: tuple[str, ...] = ("orders.submit", "orders.cancel"),
    key: str = "strategy-registration-1",
) -> dict[str, Any]:
    return dict(
        registry.handle(
            "strategy-key",
            _request(
                "service.register",
                {
                    "registration_key": key,
                    "client_session_id": "strategy-session-1",
                    "instance_id": "execution-alpha",
                    "port": "service.order_entry",
                    "scope_ref": "strategy-alpha-orders",
                    "operations": list(operations),
                },
                identity=identity,
                scope=scope,
            ),
        )
    )


def _call(
    registry: ServiceRegistry,
    identity: WireIdentity,
    scope: WireScope,
    registration: dict[str, Any],
    operation: str,
) -> dict[str, Any]:
    receipt = registration["result"]
    return dict(
        registry.handle(
            "strategy-key",
            _request(
                operation,
                {
                    "registration_id": receipt["registration_id"],
                    "service_epoch": receipt["service_epoch"],
                    "instance_id": "execution-alpha",
                    "arguments": {"symbol": "TEST"},
                },
                identity=identity,
                scope=scope,
            ),
        )
    )


def test_stop_new_persists_exact_run_fence_and_leaves_cancel_available(tmp_path: Path) -> None:
    database = tmp_path / "stop-new.sqlite"
    registry, identity, scope = _make_registry(database)
    handled: list[str] = []
    registry.register_handler(
        "execution-alpha",
        "orders.submit",
        lambda _context, _arguments: handled.append("submit") or {"accepted": True},
    )
    registry.register_handler(
        "execution-alpha",
        "orders.cancel",
        lambda _context, _arguments: handled.append("cancel") or {"cancelled": True},
    )
    try:
        registration = _register(registry, identity, scope)
        assert registration["ok"] is True

        receipt = dict(registry.stop_strategy_new(scope, "afi-monitor-stop-new-v1-" + "a" * 32))
        assert receipt["status"] == "applied"
        assert receipt["effect_ref"].startswith("strategy-stop-new-v1:")
        assert receipt["effect_result"] == {"action": "stop_new", "result": "fenced"}

        assert _call(registry, identity, scope, registration, "orders.submit")["code"] == (
            "STRATEGY_STOPPED"
        )
        assert handled == []
        assert _call(registry, identity, scope, registration, "orders.cancel")["ok"] is True
        assert handled == ["cancel"]

        unregister = dict(
            registry.handle(
                "strategy-key",
                _request(
                    "service.unregister",
                    {
                        "registration_id": registration["result"]["registration_id"],
                        "service_epoch": registration["result"]["service_epoch"],
                    },
                    identity=identity,
                    scope=scope,
                ),
            )
        )
        assert unregister["ok"] is True
        assert _register(registry, identity, scope, key="new-submit-registration")["code"] == (
            "STRATEGY_STOPPED"
        )
        assert _register(
            registry,
            identity,
            scope,
            operations=("orders.cancel",),
            key="cancel-only-registration",
        )["ok"] is True

        replay = dict(registry.stop_strategy_new(scope, "afi-monitor-stop-new-v1-" + "a" * 32))
        assert replay == receipt
        already_stopped = dict(
            registry.stop_strategy_new(scope, "afi-monitor-stop-new-v1-" + "b" * 32)
        )
        assert already_stopped["status"] == "applied"
        assert already_stopped["effect_ref"] == receipt["effect_ref"]
        assert already_stopped["effect_result"] == {
            "action": "stop_new",
            "result": "already_fenced",
        }
    finally:
        registry.close()

    restarted, restarted_identity, restarted_scope = _make_registry(database)
    try:
        assert dict(
            restarted.stop_strategy_new(
                restarted_scope, "afi-monitor-stop-new-v1-" + "a" * 32
            )
        ) == receipt
        assert _register(
            restarted,
            restarted_identity,
            restarted_scope,
            key="after-restart-submit-registration",
        )["code"] == "STRATEGY_STOPPED"
    finally:
        restarted.close()


def test_exact_run_resource_grants_are_operator_bound_monitor_only() -> None:
    identity = WireIdentity(
        WireRole.APPLICATION_CLIENT,
        "application:control",
        "operator:alice",
        "delegate:owner-control",
    )
    exact_scope = WireScope(
        "live:venue-a",
        account_ref="account-a",
        strategy_ref="strategy-alpha",
        run_id="run-2026-10-17-a",
    )
    capability = ServiceCapability(
        "monitor-control",
        "service.monitor_control",
        frozenset({"monitor.control", "monitor.control_status"}),
    )
    grant = ResourceGrant(
        "monitor-control",
        "service.monitor_control",
        "exact-strategy-run",
        exact_scope,
        frozenset({"monitor.control", "monitor.control_status"}),
    )
    validate_capabilities_and_grants(
        (capability,), {identity.principal_ref: (PrincipalGrant(identity, (grant,)),)}
    )

    service_identity = WireIdentity(WireRole.SERVICE, "service:control", "control")
    with pytest.raises(ValueError, match="exact run grants"):
        validate_capabilities_and_grants(
            (capability,),
            {
                service_identity.principal_ref: (
                    PrincipalGrant(service_identity, (grant,)),
                )
            },
        )


def test_stopped_submit_does_not_bypass_migration_read_only_gate(
    tmp_path: Path,
) -> None:
    database = tmp_path / "stop-new-migration-dispatch.sqlite"
    registry, identity, scope = _make_registry(
        database, migration_deployment_epoch="destination-epoch"
    )
    handled: list[str] = []
    registry.register_handler(
        "execution-alpha",
        "orders.submit",
        lambda _context, _arguments: handled.append("submit") or {"accepted": True},
    )
    try:
        registration = _register(registry, identity, scope)
        assert registration["ok"] is True
        receipt = registry.stop_strategy_new(
            scope, "afi-monitor-stop-new-v1-" + "c" * 32
        )
        assert receipt["status"] == "applied"

        arm_migration_cutover(
            database,
            source_epoch="source-epoch",
            new_epoch="destination-epoch",
            migration_id="stop-new-dispatch-boundary",
        )
        response = _call(registry, identity, scope, registration, "orders.submit")
        # The process started before the committed marker existed, so the
        # marker check fails closed while the destination is still read-only.
        # A STOP_NEW denial must not skip that boundary and disguise the call
        # as a normal post-fence rejection.
        assert response["code"] == "UNAVAILABLE"
        assert handled == []
        marker = read_migration_marker(database)
        assert marker is not None
        assert marker.status == "COMMITTED"
    finally:
        registry.close()


def test_inactive_stop_rejection_does_not_seal_or_write_owner_receipt(
    tmp_path: Path,
) -> None:
    database = tmp_path / "stop-new-migration-inactive.sqlite"
    arm_migration_cutover(
        database,
        source_epoch="source-epoch",
        new_epoch="destination-epoch",
        migration_id="stop-new-inactive-boundary",
    )
    registry, _identity, scope = _make_registry(
        database, migration_deployment_epoch="destination-epoch"
    )
    try:
        result = registry.stop_strategy_new(
            scope, "afi-monitor-stop-new-v1-" + "d" * 32
        )
        assert result["status"] == "rejected"
        assert result["effect_result"] == {
            "action": "stop_new",
            "result": "run_not_active",
        }
        marker = read_migration_marker(database)
        assert marker is not None and marker.status == "COMMITTED"
        with registry._transaction():
            receipts = registry._db.execute(
                "SELECT COUNT(*) FROM strategy_stop_new_receipts"
            ).fetchone()[0]
        assert receipts == 0
    finally:
        registry.close()


def test_active_stop_is_refused_on_read_only_migration_destination(
    tmp_path: Path,
) -> None:
    database = tmp_path / "stop-new-migration-read-only.sqlite"
    arm_migration_cutover(
        database,
        source_epoch="source-epoch",
        new_epoch="destination-epoch",
        migration_id="stop-new-readonly-boundary",
    )
    registry, _identity, scope = _make_registry(
        database, migration_deployment_epoch="destination-epoch"
    )
    try:
        with registry._transaction():
            registry._db.execute(
                "INSERT INTO strategy_write_claims "
                "(execution_domain_ref, strategy_ref, account_ref, run_id, created_at) "
                "VALUES(?,?,?,?,?)",
                (
                    scope.execution_domain_ref,
                    scope.strategy_ref,
                    scope.account_ref,
                    scope.run_id,
                    1.0,
                ),
            )

        command_id = "afi-monitor-stop-new-v1-" + "e" * 32
        with pytest.raises(ServiceRegistryStopNewError) as blocked:
            registry.stop_strategy_new(scope, command_id)
        assert blocked.value.code == "MIGRATION_WRITE_DISABLED"
        with registry._transaction():
            claim = registry._db.execute(
                "SELECT stop_new_at, stop_new_command_id FROM strategy_write_claims "
                "WHERE execution_domain_ref=? AND strategy_ref=? AND account_ref=?",
                (scope.execution_domain_ref, scope.strategy_ref, scope.account_ref),
            ).fetchone()
            receipt_count = registry._db.execute(
                "SELECT COUNT(*) FROM strategy_stop_new_receipts"
            ).fetchone()[0]
        assert claim is not None
        assert claim["stop_new_at"] is None
        assert claim["stop_new_command_id"] is None
        assert receipt_count == 0
        marker = read_migration_marker(database)
        assert marker is not None and marker.status == "COMMITTED"
    finally:
        registry.close()
