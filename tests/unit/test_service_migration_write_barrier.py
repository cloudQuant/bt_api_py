"""Focused production-boundary tests for the MIG03 write-attempt barrier.

The marker is sealed immediately before entering an authorized mutating
handler.  This deliberately records an attempt, not proof that the handler's
downstream storage committed; the full state journal and migration drill are
separate evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import bt_api_py.service_migration_gate as migration_gate
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_py.service_migration_gate import (
    CODE_MARKER_DURABILITY_UNCERTAIN,
    MigrationGateError,
    arm_migration_cutover,
    marker_path,
    read_migration_marker,
    record_migration_write_attempt,
)
from bt_api_py.service_host import ServiceHost, ServiceHostError
from bt_api_py.service_registry import (
    ActiveRegistrationContext,
    PrincipalGrant,
    ResourceGrant,
    ServiceCallContext,
    ServiceCapability,
    ServiceRegistry,
)

_SOURCE_EPOCH = "deploy-previous"
_DESTINATION_EPOCH = "deploy-current"
_PRINCIPAL = "curve-a"
_IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-a", "actor-a")
_SCOPE = WireScope(
    execution_domain_ref="live:venue-a",
    account_ref="account-a",
    strategy_ref="strategy-a",
    run_id="run-a",
)
_SENT_AT = "2026-10-16T00:00:00Z"


def _request(operation: str, payload: dict[str, Any], *, control: bool = False) -> WireEnvelope:
    _request.counter += 1
    return WireEnvelope(
        header=WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=f"write-barrier-{_request.counter}",
            correlation_id=None,
            identity=_IDENTITY,
            scope=_SCOPE,
            operation=operation,
            schema_id="sdk.service.control" if control else "sdk.service.call",
            schema_version=1,
            sent_at_utc=_SENT_AT,
        ),
        payload=payload,
    )


_request.counter = 0  # type: ignore[attr-defined]


def _registry(path: Path, *, deployment_epoch: str | None) -> ServiceRegistry:
    operations = frozenset({"account.read", "account.ack", "orders.submit"})
    capability = ServiceCapability(
        "account-instance",
        "service.account_read",
        operations,
        frozenset({"orders.submit"}),
    )
    grant = ResourceGrant(
        "account-instance",
        "service.account_read",
        "account-grant-a",
        WireScope("live:venue-a", account_ref="account-a", strategy_ref="strategy-a"),
        operations,
    )
    return ServiceRegistry(
        path,
        service_id="account-service-a",
        capabilities=(capability,),
        grants={_PRINCIPAL: (PrincipalGrant(_IDENTITY, (grant,)),)},
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        migration_deployment_epoch=deployment_epoch,
    )


def _register(registry: ServiceRegistry) -> dict[str, Any]:
    reply = registry.handle(
        _PRINCIPAL,
        _request(
            "service.register",
            {
                "registration_key": f"registration-{_request.counter}",
                "client_session_id": f"client-{_request.counter}",
                "instance_id": "account-instance",
                "port": "service.account_read",
                "scope_ref": "account-grant-a",
                "operations": ["account.read", "account.ack", "orders.submit"],
            },
            control=True,
        ),
    )
    assert reply["ok"] is True
    return dict(reply["result"])


def _dispatch(
    registry: ServiceRegistry, registration: dict[str, Any], operation: str
) -> dict[str, Any]:
    reply = registry.handle(
        _PRINCIPAL,
        _request(
            operation,
            {
                "registration_id": registration["registration_id"],
                "service_epoch": registration["service_epoch"],
                "instance_id": "account-instance",
                "arguments": {"cursor": 7},
            },
        ),
    )
    return dict(reply)


def _deny_marker_stat(monkeypatch: Any, state: Path) -> None:
    marker = marker_path(state)
    original_stat = Path.stat

    def denied_stat(path: Path, *args: Any, **kwargs: Any):
        if path == marker:
            raise PermissionError("simulated marker access denial")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied_stat)


def test_registry_seals_write_attempt_before_mutating_handler(tmp_path: Path) -> None:
    state = tmp_path / "registry.sqlite3"
    arm_migration_cutover(
        state, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH
    )
    observed: list[str | None] = []
    registry = _registry(state, deployment_epoch=_DESTINATION_EPOCH)
    try:
        registration = _register(registry)
        assert registration["service_epoch"] != _DESTINATION_EPOCH

        def handler(_context: object, _arguments: object) -> dict[str, bool]:
            marker = read_migration_marker(state)
            observed.append(None if marker is None else marker.status)
            return {"accepted": True}

        registry.register_handler("account-instance", "orders.submit", handler)
        response = _dispatch(registry, registration, "orders.submit")

        assert response["ok"] is True
        assert observed == ["SEALED"]
        marker = read_migration_marker(state)
        assert marker is not None and marker.sealed
        assert marker.first_write_fact_kind == "dispatch"
    finally:
        registry.close()


def test_account_ack_is_cursor_write_attempt_even_without_write_capability(
    tmp_path: Path,
) -> None:
    state = tmp_path / "account-cursor.sqlite3"
    arm_migration_cutover(
        state, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH
    )
    observed: list[str | None] = []
    registry = _registry(state, deployment_epoch=_DESTINATION_EPOCH)
    try:
        registration = _register(registry)

        def handler(_context: object, _arguments: object) -> dict[str, bool]:
            marker = read_migration_marker(state)
            observed.append(None if marker is None else marker.status)
            return {"accepted": True}

        registry.register_handler("account-instance", "account.ack", handler)
        response = _dispatch(registry, registration, "account.ack")

        assert response["ok"] is True
        assert observed == ["SEALED"]
        marker = read_migration_marker(state)
        assert marker is not None and marker.first_write_fact_kind == "cursor"
    finally:
        registry.close()


def test_registry_never_substitutes_process_epoch_for_deployment_epoch(
    tmp_path: Path,
) -> None:
    state = tmp_path / "missing-stable-epoch.sqlite3"
    armed = arm_migration_cutover(
        state, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH
    )
    called = False
    registry = _registry(state, deployment_epoch=None)
    try:
        registration = _register(registry)

        def handler(_context: object, _arguments: object) -> dict[str, bool]:
            nonlocal called
            called = True
            return {"accepted": True}

        registry.register_handler("account-instance", "orders.submit", handler)
        response = _dispatch(registry, registration, "orders.submit")

        assert response == {"ok": False, "code": "UNAVAILABLE", "result": {}}
        assert called is False
        assert read_migration_marker(state) == armed
        assert marker_path(state).exists()
    finally:
        registry.close()


def test_unreadable_marker_stat_denies_write_before_handler(
    tmp_path: Path, monkeypatch: Any
) -> None:
    state = tmp_path / "unreadable-marker.sqlite3"
    armed = arm_migration_cutover(
        state, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH
    )
    called = False
    registry = _registry(state, deployment_epoch=_DESTINATION_EPOCH)
    try:
        registration = _register(registry)

        def handler(_context: object, _arguments: object) -> dict[str, bool]:
            nonlocal called
            called = True
            return {"accepted": True}

        registry.register_handler("account-instance", "orders.submit", handler)
        with monkeypatch.context() as patch:
            _deny_marker_stat(patch, state)

            try:
                read_migration_marker(state)
            except MigrationGateError as exc:
                assert exc.code == "CUTOVER_MARKER_UNREADABLE"
            else:
                raise AssertionError("an unreadable marker must not look unarmed")

            response = _dispatch(registry, registration, "orders.submit")

            assert response == {"ok": False, "code": "UNAVAILABLE", "result": {}}
            assert called is False
        assert read_migration_marker(state) == armed
    finally:
        registry.close()


def test_unreadable_marker_stat_blocks_host_start_gate(
    tmp_path: Path, monkeypatch: Any
) -> None:
    state = tmp_path / "host-unreadable-marker.sqlite3"
    arm_migration_cutover(
        state, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH
    )
    host = object.__new__(ServiceHost)
    host._registry_path = state
    host._migration_deployment_epoch = _SOURCE_EPOCH
    _deny_marker_stat(monkeypatch, state)

    try:
        host._enforce_migration_gate()
    except ServiceHostError as exc:
        assert exc.code == "CUTOVER_MARKER_UNREADABLE"
    else:
        raise AssertionError("host start gate must reject an unreadable marker")


def test_sealed_marker_reconfirms_durability_before_later_write_attempt(
    tmp_path: Path, monkeypatch: Any
) -> None:
    state = tmp_path / "durability-uncertain.sqlite3"
    arm_migration_cutover(
        state, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH
    )
    marker = marker_path(state)
    registry = _registry(state, deployment_epoch=_DESTINATION_EPOCH)
    called = False
    failed_sync_statuses: list[str] = []
    try:
        registration = _register(registry)

        def handler(_context: object, _arguments: object) -> dict[str, bool]:
            nonlocal called
            called = True
            return {"accepted": True}

        registry.register_handler("account-instance", "orders.submit", handler)

        def fail_directory_sync(path: Path) -> None:
            assert path == marker
            # This hook runs after os.replace; observing SEALED proves the
            # first transition is visible despite its failed durability step.
            document = json.loads(path.read_text(encoding="utf-8"))
            failed_sync_statuses.append(document["status"])
            raise MigrationGateError(CODE_MARKER_DURABILITY_UNCERTAIN)

        with monkeypatch.context() as patch:
            patch.setattr(
                migration_gate, "_fsync_parent_directory", fail_directory_sync
            )

            first_response = _dispatch(registry, registration, "orders.submit")
            assert first_response == {
                "ok": False,
                "code": "UNAVAILABLE",
                "result": {},
            }
            assert called is False
            sealed_bytes = marker.read_bytes()
            assert json.loads(sealed_bytes)["status"] == "SEALED"

            retry_response = _dispatch(registry, registration, "orders.submit")
            assert retry_response == {
                "ok": False,
                "code": "UNAVAILABLE",
                "result": {},
            }
            assert called is False
            assert marker.read_bytes() == sealed_bytes

        confirmed_response = _dispatch(registry, registration, "orders.submit")

        assert confirmed_response["ok"] is True
        assert called is True
        assert failed_sync_statuses == ["SEALED", "SEALED"]
        assert marker.read_bytes() == sealed_bytes
    finally:
        registry.close()


def test_unarmed_registry_keeps_ordinary_write_behavior(tmp_path: Path) -> None:
    state = tmp_path / "never-armed.sqlite3"
    called: list[bool] = []
    registry = _registry(state, deployment_epoch=None)
    try:
        registration = _register(registry)
        registry.register_handler(
            "account-instance",
            "orders.submit",
            lambda _context, _arguments: (called.append(True) or {"accepted": True}),
        )

        response = _dispatch(registry, registration, "orders.submit")

        assert response["ok"] is True
        assert called == [True]
        assert read_migration_marker(state) is None
        assert not marker_path(state).exists()
    finally:
        registry.close()


def test_write_attempt_rejects_non_destination_deployment_epoch(tmp_path: Path) -> None:
    state = tmp_path / "epoch-bound.sqlite3"
    armed = arm_migration_cutover(
        state, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH
    )

    try:
        record_migration_write_attempt(
            state,
            deployment_epoch="another-deployment",
            fact_kind="dispatch",
        )
    except MigrationGateError as exc:
        assert exc.code == "MIGRATION_GATE_EPOCH_MISMATCH"
    else:
        raise AssertionError("non-destination deployment must be refused")

    assert read_migration_marker(state) == armed


def test_write_attempt_keeps_never_armed_fast_noop(tmp_path: Path) -> None:
    state = tmp_path / "unarmed-attempt.sqlite3"

    assert (
        record_migration_write_attempt(
            state,
            deployment_epoch=None,
            fact_kind="dispatch",
        )
        is False
    )
    assert list(tmp_path.iterdir()) == []


def _maintenance_host(state: Path, *, deployment_epoch: str | None, component: object):
    """Build only the owner-thread maintenance projection used by the host."""

    host = object.__new__(ServiceHost)
    host._registry_path = state
    host._migration_deployment_epoch = deployment_epoch
    host._registry = SimpleNamespace(
        active_registration_ids=lambda *, instance_id: frozenset(),
    )
    host._components = [component]
    host._prepared = ()
    host._component_maintenance_faults = set()
    host._market_active_registration_count = 0
    return host


def test_host_gates_internal_analytics_task_maintenance_before_callback(
    tmp_path: Path,
) -> None:
    state = tmp_path / "analytics-task.sqlite3"
    arm_migration_cutover(
        state, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH
    )
    observed: list[str | None] = []

    def maintain(_active_ids: frozenset[str]) -> None:
        marker = read_migration_marker(state)
        observed.append(None if marker is None else marker.status)

    component = SimpleNamespace(
        instance_id="risk-analytics",
        capabilities=(),
        maintain=maintain,
        maintain_registrations=None,
    )
    host = _maintenance_host(
        state,
        deployment_epoch=_DESTINATION_EPOCH,
        component=component,
    )
    host._prepared = (
        SimpleNamespace(
            spec=SimpleNamespace(
                component="risk",
                instance_id="risk-analytics",
                options={"role": "portfolio_analytics"},
            )
        ),
    )

    host._maintain_components()

    assert observed == ["SEALED"]
    assert host._component_maintenance_faults == set()
    marker = read_migration_marker(state)
    assert marker is not None and marker.first_write_fact_kind == "dispatch"


def test_host_fails_closed_before_internal_maintenance_without_stable_epoch(
    tmp_path: Path,
) -> None:
    state = tmp_path / "analytics-missing-epoch.sqlite3"
    armed = arm_migration_cutover(
        state, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH
    )
    called = False

    def maintain(_active_ids: frozenset[str]) -> None:
        nonlocal called
        called = True

    component = SimpleNamespace(
        instance_id="risk-analytics",
        capabilities=(),
        maintain=maintain,
        maintain_registrations=None,
    )
    host = _maintenance_host(state, deployment_epoch=None, component=component)
    host._prepared = (
        SimpleNamespace(
            spec=SimpleNamespace(
                component="risk",
                instance_id="risk-analytics",
                options={"role": "portfolio_analytics"},
            )
        ),
    )

    host._maintain_components()

    assert called is False
    assert host._component_maintenance_faults == {"risk-analytics"}
    assert read_migration_marker(state) == armed


def test_host_seals_group_ownership_before_registry_claim_and_adapter_callback(
    tmp_path: Path,
) -> None:
    state = tmp_path / "group-ownership.sqlite3"
    arm_migration_cutover(
        state, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH
    )
    service_identity = WireIdentity(WireRole.SERVICE, "service-principal", "service-a")
    group_scope = WireScope(
        execution_domain_ref="live:venue-a",
        account_ref="account-a",
        strategy_ref="strategy-a",
        portfolio_ref="portfolio-a",
    )
    call_context = ServiceCallContext(
        service_identity,
        group_scope,
        "registration-group-a",
        "service-principal",
    )
    active = ActiveRegistrationContext(
        context=call_context,
        instance_id="execution-groups",
        port="service.order_entry",
        scope_ref="group-scope-a",
        operations=frozenset({"orders.submit_group_leg"}),
        write_operations=frozenset({"orders.submit_group_leg"}),
        service_epoch="registry-process-epoch",
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        expires_at=100.0,
        strategy_write_run_id="run-a",
    )
    observations: list[tuple[str, str | None]] = []

    def observe(phase: str) -> None:
        marker = read_migration_marker(state)
        observations.append((phase, None if marker is None else marker.status))

    registry = SimpleNamespace(
        active_registration_contexts=lambda *, instance_id, port: (active,),
        bind_trusted_group_run=lambda *args, **kwargs: observe("registry_claim"),
    )
    component = SimpleNamespace(
        instance_id="execution-groups",
        capabilities=(SimpleNamespace(port="service.order_entry"),),
        maintain=None,
        maintain_registrations=lambda _contexts: observe("adapter_maintenance"),
    )
    host = _maintenance_host(
        state,
        deployment_epoch=_DESTINATION_EPOCH,
        component=component,
    )
    host._registry = registry
    host._prepared = (
        SimpleNamespace(
            spec=SimpleNamespace(
                component="execution",
                instance_id="execution-groups",
                options={"role": "group_coordinator"},
            )
        ),
    )

    host._maintain_components()

    assert observations == [
        ("registry_claim", "SEALED"),
        ("adapter_maintenance", "SEALED"),
    ]
    assert host._component_maintenance_faults == set()
    marker = read_migration_marker(state)
    assert marker is not None and marker.first_write_fact_kind == "ownership"
