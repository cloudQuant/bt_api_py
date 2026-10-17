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


def test_stop_new_fixed_postimage_scope_persistence_and_migration_priority(tmp_path: Path) -> None:
    database = tmp_path / "stop-new-fixed-postimage.sqlite"
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
        command_id = "afi-monitor-stop-new-v1-" + "f" * 32
        first = dict(registry.stop_strategy_new(scope, command_id))
        assert first["status"] == "applied"
        assert first["effect_result"] == {"action": "stop_new", "result": "fenced"}
        assert dict(registry.stop_strategy_new(scope, command_id)) == first

        other_run = WireScope(
            scope.execution_domain_ref,
            account_ref=scope.account_ref,
            strategy_ref=scope.strategy_ref,
            run_id="run-2026-10-17-b",
        )
        with pytest.raises(ServiceRegistryStopNewError) as conflict:
            registry.stop_strategy_new(other_run, command_id)
        assert conflict.value.code == "COMMAND_SCOPE_CONFLICT"
        escaped = _call(registry, identity, other_run, registration, "orders.submit")
        assert escaped["ok"] is False
        assert escaped["code"] == "SCOPE_DENIED"
        assert handled == []

        arm_migration_cutover(
            database,
            source_epoch="source-epoch",
            new_epoch="destination-epoch",
            migration_id="stop-new-fixed-postimage-boundary",
        )
        after_cutover = _call(registry, identity, scope, registration, "orders.submit")
        assert after_cutover["code"] == "UNAVAILABLE"
        assert handled == []
        marker = read_migration_marker(database)
        assert marker is not None and marker.status == "COMMITTED"
    finally:
        registry.close()

    reopened, reopened_identity, reopened_scope = _make_registry(
        database, migration_deployment_epoch="destination-epoch"
    )
    try:
        assert dict(reopened.stop_strategy_new(reopened_scope, command_id)) == first
        assert _register(
            reopened,
            reopened_identity,
            reopened_scope,
            key="fixed-postimage-reopen-submit",
        )["code"] == "STRATEGY_STOPPED"
        with reopened._transaction():
            row = reopened._db.execute(
                "SELECT stop_new_command_id FROM strategy_write_claims WHERE "
                "execution_domain_ref=? AND strategy_ref=? AND account_ref=?",
                (scope.execution_domain_ref, scope.strategy_ref, scope.account_ref),
            ).fetchone()
            receipt_count = reopened._db.execute(
                "SELECT COUNT(*) FROM strategy_stop_new_receipts WHERE command_id=?",
                (command_id,),
            ).fetchone()[0]
        assert row is not None and row["stop_new_command_id"] == command_id
        assert receipt_count == 1
    finally:
        reopened.close()


def test_fixed_postimage_host_maintenance_passes_same_registry_to_monitor_control(
    tmp_path: Path,
) -> None:
    import threading
    from types import SimpleNamespace

    from bt_api_py.service_host import ServiceHost

    instance_id = "monitor-control"
    registry = ServiceRegistry(
        tmp_path / "host-maintain.sqlite",
        service_id="venue-a-service",
        capabilities=(
            ServiceCapability(
                instance_id,
                "service.monitor_control",
                frozenset({"monitor.control", "monitor.control_status"}),
            ),
        ),
        grants={},
        policy_version="policy-v1",
        requirements_version="requirements-v1",
    )
    calls: list[tuple[frozenset[str], object]] = []

    def maintain(active_ids: frozenset[str], *, owner_registry: object) -> None:
        calls.append((active_ids, owner_registry))

    component_spec = SimpleNamespace(
        instance_id=instance_id,
        component="monitor",
        options={"role": "control"},
    )
    component = SimpleNamespace(
        instance_id=instance_id,
        capabilities=(),
        maintain=maintain,
        maintain_registrations=None,
    )
    host = ServiceHost.__new__(ServiceHost)
    host._registry = registry
    host._prepared = (SimpleNamespace(spec=component_spec),)
    host._components = [component]
    host._component_maintenance_faults = set()
    host._market_active_registration_count = 0
    host._owner_thread = threading.get_ident()
    host._paper_monitor_relays = []
    host._paper_monitor_faults = {}
    try:
        host._maintain_components()
        assert len(calls) == 1
        assert calls[0][0] == frozenset()
        assert calls[0][1] is registry
        assert instance_id not in host._component_maintenance_faults
        assert host._paper_monitor_faults == {}
    finally:
        registry.close()
