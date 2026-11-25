"""Owner-bound Gateway control-call fixture for Iteration 64 unit tests.

This fixture never starts a Gateway.  It gives legacy reply-shape tests the
same identity/continuity envelope required by the production adapter while
leaving their synthetic operation replies unchanged.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.service_runtime_thread import (
    BoundCallObservation,
    BoundCallResult,
    BoundCallRoute,
)

CONTROL_FINGERPRINT = "f" * 64
CONTROL_OPERATIONS = frozenset(
    {
        "direct.control.identity",
        "direct.control.apply",
        "direct.control.status",
        "direct.control.list",
        "direct.control.cancel_scope",
        "direct.control.cancel_scope_status",
    }
)


def control_route(
    account_ref: str,
    *,
    scope_ref: str,
    service_id: str,
    instance_id: str,
) -> BoundCallRoute:
    return BoundCallRoute(
        WireIdentity(WireRole.APPLICATION_CLIENT, "risk-control", "risk-control-actor"),
        WireScope("test-domain", account_ref=account_ref),
        scope_ref,
        service_id,
        instance_id,
        WireIdentity(WireRole.SERVICE, "gateway-principal", "gateway"),
    )


class BoundControlRuntimeMixin:
    """Adapt a synthetic ``call`` runtime to the owner-bound call contract."""

    _control_binding_token: object | None = None

    def _control_identity_reply(self, route: BoundCallRoute) -> dict[str, Any]:
        return {
            "status": "OBSERVED",
            "schema_version": 1,
            "account_scope": route.scope.account_ref,
            "journal_id": "journal-a",
            "journal_generation": "generation-a",
        }

    def _bound_control_call(
        self,
        mount: str,
        operation: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        legacy_arguments = dict(arguments)
        assert legacy_arguments.pop("expected_journal_id") == "journal-a"
        assert legacy_arguments.pop("expected_generation") == "generation-a"
        return self.call(mount, operation, legacy_arguments, **kwargs)

    def call_bound(
        self,
        mount: str,
        operation: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ) -> BoundCallResult:
        route = kwargs["route"]
        token = self._control_binding_token
        if token is None:
            token = object()
            self._control_binding_token = token
        observation = BoundCallObservation(
            route.identity,
            "run-a",
            route.scope,
            "registration-a",
            "service-epoch-a",
            mount,
            route.service_id,
            route.instance_id,
            route.scope_ref,
            CONTROL_OPERATIONS,
            route.peer,
            CONTROL_FINGERPRINT,
            100.0,
            True,
            True,
            operation,
        )
        assert kwargs["expected_server_key_fingerprint"] == CONTROL_FINGERPRINT
        if operation == "direct.control.identity":
            reply = self._control_identity_reply(route)
        else:
            assert kwargs["expected_binding"] == replace(
                observation, operation="direct.control.identity"
            )
            assert kwargs["_expected_token"] is token
            reply = self._bound_control_call(
                mount,
                operation,
                arguments,
                **kwargs,
            )
        return BoundCallResult(reply, observation, observation, token)


def seed_cause_binding(adapter: Any, cause_id: str) -> None:
    proof, error = adapter._capture_binding()
    assert error is None and proof is not None
    adapter._cause_bindings[cause_id] = proof


def seed_operation_binding(adapter: Any, operation_id: str) -> None:
    proof, error = adapter._capture_binding()
    assert error is None and proof is not None
    adapter._operation_bindings[operation_id] = proof
