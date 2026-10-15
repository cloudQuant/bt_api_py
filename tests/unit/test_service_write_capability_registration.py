"""Trusted write-operation metadata fences competing strategy sessions."""

from __future__ import annotations

from pathlib import Path

from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_execution import (
    CancelObservation,
    ExecutionScope,
    OrderIntent,
    ProviderObservation,
    SqliteExecutionStore,
)

from bt_api_py.service_components import (
    ComponentResources,
    ComponentSpec,
    build_component,
    validate_components,
)
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)

_DOMAIN = "paper:registration-test"
_ACCOUNT = "account-a"
_STRATEGY = "strategy-a"
_PRINCIPAL = "curve-strategy-principal"
_INSTANCE = "execution-owner"
_ENTRY_PORT = "service.order_entry"
_QUERY_PORT = "service.order_query"
_REGISTRATION_TIME = "2026-10-15T10:00:00Z"
_RISK_INSTANCE = "risk-owner"


class _Provider:
    def submit(self, intent: OrderIntent) -> ProviderObservation:
        return ProviderObservation.accepted(intent.intent_id, "provider-order-1")

    def cancel(self, intent) -> CancelObservation:
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


def _registration_request(
    *, identity: WireIdentity, scope: WireScope, key: str, client_session_id: str
) -> WireEnvelope:
    return WireEnvelope(
        header=WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=f"register-{key}",
            correlation_id=None,
            identity=identity,
            scope=scope,
            operation="service.register",
            schema_id="sdk.service.control",
            schema_version=1,
            sent_at_utc=_REGISTRATION_TIME,
        ),
        payload={
            "registration_key": key,
            "client_session_id": client_session_id,
            "instance_id": _INSTANCE,
            "port": _ENTRY_PORT,
            "scope_ref": "strategy-account-grant",
            "operations": ["orders.submit", "orders.cancel"],
        },
    )


def test_built_account_owner_write_metadata_blocks_a_second_strategy_session(
    tmp_path: Path,
) -> None:
    scope_template = WireScope(
        _DOMAIN,
        account_ref=_ACCOUNT,
        strategy_ref=_STRATEGY,
    )
    scope = WireScope(
        _DOMAIN,
        account_ref=_ACCOUNT,
        strategy_ref=_STRATEGY,
        run_id="run-a",
    )
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY)

    prepared = validate_components(
        (
            ComponentSpec(
                "execution",
                _INSTANCE,
                (_ENTRY_PORT, _QUERY_PORT),
                {"role": "account_owner"},
            ),
        )
    )[0]
    store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    component = build_component(
        prepared,
        scope=scope_template,
        resource_provider=lambda *_: ComponentResources.borrowed(
            {
                "store": store,
                "targets": {
                    scope_template: ExecutionScope(
                        "PAPER", "simulation", _ACCOUNT, _STRATEGY, "20261015"
                    )
                },
                "dispatch": _Provider(),
                "admission": {"allow_unprotected": True},
            },
            ready=lambda: True,
        ),
    )
    registry: ServiceRegistry | None = None
    try:
        capabilities = component.capabilities
        entry = next(cap for cap in capabilities if cap.port == _ENTRY_PORT)
        query = next(cap for cap in capabilities if cap.port == _QUERY_PORT)
        assert type(entry) is ServiceCapability
        assert entry.operations == frozenset({"orders.submit", "orders.cancel"})
        assert entry.write_operations == frozenset({"orders.submit", "orders.cancel"})
        assert query.operations == frozenset({"orders.query"})
        assert query.write_operations == frozenset()

        grants = {
            _PRINCIPAL: (
                PrincipalGrant(
                    identity,
                    (
                        ResourceGrant(
                            _INSTANCE,
                            _ENTRY_PORT,
                            "strategy-account-grant",
                            scope_template,
                            entry.operations,
                        ),
                    ),
                ),
            )
        }
        registry = ServiceRegistry(
            tmp_path / "service-registry.sqlite3",
            service_id="registration-test-service",
            capabilities=capabilities,
            grants=grants,
            policy_version="policy-v1",
            requirements_version="requirements-v1",
        )
        for operation, handler in component.handlers.items():
            registry.register_handler(_INSTANCE, operation, handler)

        first = registry.handle(
            _PRINCIPAL,
            _registration_request(
                identity=identity,
                scope=scope,
                key="registration-a",
                client_session_id="session-a",
            ),
        )
        second = registry.handle(
            _PRINCIPAL,
            _registration_request(
                identity=identity,
                scope=scope,
                key="registration-b",
                client_session_id="session-b",
            ),
        )

        assert first["ok"] is True
        assert second == {"ok": False, "code": "DUPLICATE_ACTIVE_RUN", "result": {}}
    finally:
        if registry is not None:
            registry.close()
        component.close()
        store.close()


def test_risk_account_owner_declares_only_submit_as_write() -> None:
    risk_prepared = validate_components(
        (
            ComponentSpec(
                "risk",
                _RISK_INSTANCE,
                (_ENTRY_PORT, _QUERY_PORT),
                {"role": "account_owner", "account_policy_ref": "risk-policy-v1"},
            ),
        )
    )[0]
    assert risk_prepared.write_operations_by_port == {
        _ENTRY_PORT: frozenset({"orders.submit"}),
        _QUERY_PORT: frozenset(),
    }
