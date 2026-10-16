"""Exact owner.reduce grants require the currently claimed strategy run."""

from __future__ import annotations

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
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
    validate_capabilities_and_grants,
)

_OWNER_IDENTITY = WireIdentity(
    WireRole.SERVICE,
    "reduction-owner",
    "operator",
    "reduction-delegate",
)
_STRATEGY_IDENTITY = WireIdentity(
    WireRole.STRATEGY_CLIENT,
    "strategy-client",
    "strategy-runner",
    None,
)
_SCOPE = WireScope(
    execution_domain_ref="paper:synthetic",
    account_ref="account-a",
    strategy_ref="strategy-a",
    run_id="run-a",
)
_TEMPLATE = WireScope(
    execution_domain_ref="paper:synthetic",
    account_ref="account-a",
    strategy_ref="strategy-a",
    run_id=None,
)
_CAPABILITIES = (
    ServiceCapability(
        "execution",
        "service.order_entry",
        frozenset({"orders.submit"}),
        frozenset({"orders.submit"}),
    ),
    ServiceCapability(
        "execution",
        "service.owner_control",
        frozenset({"owner.reduce"}),
        frozenset({"owner.reduce"}),
    ),
)


def _request(operation: str, payload: dict[str, Any], *, identity: WireIdentity, scope: WireScope):
    _request.counter += 1
    return WireEnvelope(
        header=WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=f"owner-reduce-{_request.counter}",
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


_request.counter = 0  # type: ignore[attr-defined]


def _grants(*, owner_scope: WireScope = _SCOPE) -> dict[str, tuple[PrincipalGrant, ...]]:
    return {
        "owner-curve": (
            PrincipalGrant(
                _OWNER_IDENTITY,
                (
                    ResourceGrant(
                        "execution",
                        "service.owner_control",
                        "strategy-a-run-a-reduction",
                        owner_scope,
                        frozenset({"owner.reduce"}),
                    ),
                ),
            ),
        ),
        "strategy-curve": (
            PrincipalGrant(
                _STRATEGY_IDENTITY,
                (
                    ResourceGrant(
                        "execution",
                        "service.order_entry",
                        "strategy-a",
                        _TEMPLATE,
                        frozenset({"orders.submit"}),
                    ),
                ),
            ),
        ),
    }


def test_owner_reduce_requires_an_exact_service_strategy_run() -> None:
    with pytest.raises(ValueError, match="owner.reduce requires an exact service strategy run"):
        validate_capabilities_and_grants(
            _CAPABILITIES,
            _grants(owner_scope=_TEMPLATE),
        )


def test_owner_reduce_dispatch_requires_the_live_exact_run_claim(tmp_path: Path) -> None:
    handler_calls: list[tuple[str, str]] = []
    registry = ServiceRegistry(
        tmp_path / "owner-reduce.sqlite",
        service_id="execution-service",
        capabilities=_CAPABILITIES,
        grants=_grants(),
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        lease_seconds=30,
    )

    def handle_reduce(context, arguments):
        handler_calls.append((context.scope.strategy_ref, context.scope.run_id))
        return {"accepted": True}

    registry.register_handler("execution", "owner.reduce", handle_reduce)
    try:
        owner_registration = registry.handle(
            "owner-curve",
            _request(
                "service.register",
                {
                    "registration_key": "owner-reduction-registration",
                    "client_session_id": "owner-reduction-session",
                    "instance_id": "execution",
                    "port": "service.owner_control",
                    "scope_ref": "strategy-a-run-a-reduction",
                    "operations": ("owner.reduce",),
                },
                identity=_OWNER_IDENTITY,
                scope=_SCOPE,
            ),
        )
        assert owner_registration["ok"] is True

        denied_without_run = registry.handle(
            "owner-curve",
            _request(
                "owner.reduce",
                {
                    "registration_id": owner_registration["result"]["registration_id"],
                    "service_epoch": owner_registration["result"]["service_epoch"],
                    "instance_id": "execution",
                    "arguments": {},
                },
                identity=_OWNER_IDENTITY,
                scope=_SCOPE,
            ),
        )
        assert denied_without_run == {
            "ok": False,
            "code": "DUPLICATE_ACTIVE_RUN",
            "result": {},
        }
        assert handler_calls == []

        strategy_registration = registry.handle(
            "strategy-curve",
            _request(
                "service.register",
                {
                    "registration_key": "strategy-run-registration",
                    "client_session_id": "strategy-run-session",
                    "instance_id": "execution",
                    "port": "service.order_entry",
                    "scope_ref": "strategy-a",
                    "operations": ("orders.submit",),
                },
                identity=_STRATEGY_IDENTITY,
                scope=_SCOPE,
            ),
        )
        assert strategy_registration["ok"] is True

        accepted = registry.handle(
            "owner-curve",
            _request(
                "owner.reduce",
                {
                    "registration_id": owner_registration["result"]["registration_id"],
                    "service_epoch": owner_registration["result"]["service_epoch"],
                    "instance_id": "execution",
                    "arguments": {"target_ref": "approved-target-only"},
                },
                identity=_OWNER_IDENTITY,
                scope=_SCOPE,
            ),
        )
        assert accepted == {"ok": True, "code": "OK", "result": {"accepted": True}}
        assert handler_calls == [("strategy-a", "run-a")]

        wrong_run = registry.handle(
            "owner-curve",
            _request(
                "owner.reduce",
                {
                    "registration_id": owner_registration["result"]["registration_id"],
                    "service_epoch": owner_registration["result"]["service_epoch"],
                    "instance_id": "execution",
                    "arguments": {"target_ref": "approved-target-only"},
                },
                identity=_OWNER_IDENTITY,
                scope=WireScope(
                    execution_domain_ref="paper:synthetic",
                    account_ref="account-a",
                    strategy_ref="strategy-a",
                    run_id="run-b",
                ),
            ),
        )
        assert wrong_run == {"ok": False, "code": "SCOPE_DENIED", "result": {}}
        assert handler_calls == [("strategy-a", "run-a")]
    finally:
        registry.close()
