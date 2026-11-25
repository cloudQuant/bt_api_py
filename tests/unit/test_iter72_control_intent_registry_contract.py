"""Explicit Registry vocabulary for the Iteration-72 control-intent entry.

UNIT/FAKE only: an in-memory Registry is used; no Gateway owner, provider,
account, Risk host, network transport, or automatic retry is started.
"""

from __future__ import annotations

from types import SimpleNamespace

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.control_intent_wire import (
    CONTROL_INTENT_APPLY_OPERATION,
    CONTROL_INTENT_WRITE_OPERATIONS,
)

from bt_api_py import service_registry as registry_module
from bt_api_py import service_runtime as runtime_module
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceDispatchAdmission,
    ServiceRegistry,
)
from tests.unit.test_service_registry import _next_request, _register

_OLD_BINDING_OPERATIONS = frozenset(
    {
        "direct.control.identity",
        "direct.control.apply",
        "direct.control.status",
        "direct.control.cancel_scope",
        "direct.control.cancel_scope_status",
    }
)
_OPERATIONS = _OLD_BINDING_OPERATIONS | CONTROL_INTENT_WRITE_OPERATIONS
_IDENTITY = WireIdentity(WireRole.APPLICATION_CLIENT, "risk-control", "risk-actor")
_SCOPE = WireScope("test-domain", account_ref="account-a")


def _registry(tmp_path, *, identity: WireIdentity = _IDENTITY) -> ServiceRegistry:
    capability = ServiceCapability(
        "gateway-a",
        "service.direct_write",
        _OPERATIONS,
        CONTROL_INTENT_WRITE_OPERATIONS,
    )
    return ServiceRegistry(
        tmp_path / ("registry-" + identity.role.value + ".sqlite"),
        service_id="gateway-service",
        capabilities=(capability,),
        grants={
            identity.principal_ref: (
                PrincipalGrant(
                    identity,
                    (
                        ResourceGrant(
                            "gateway-a",
                            "service.direct_write",
                            "scope-a",
                            _SCOPE,
                            _OPERATIONS,
                        ),
                    ),
                ),
            )
        },
        policy_version="policy",
        requirements_version="requirements",
        lease_seconds=10,
        clock=lambda: 100.0,
    )


def _arguments() -> dict[str, object]:
    return {
        "schema_version": 1,
        "cause_id": "risk-cause-a",
        "mode": "STOP_NEW",
        "scope_kind": "account",
        "scope_value": "",
        "latch_id": None,
        "request_digest": "a" * 64,
        "binding_digest": "b" * 64,
        "expected_journal_id": "journal-a",
        "expected_generation": "generation-a",
    }


def test_intent_operation_is_separate_write_vocabulary() -> None:
    assert registry_module._GATEWAY_CONTROL_INTENT_WRITE_OPERATIONS == (
        CONTROL_INTENT_WRITE_OPERATIONS
    )
    assert CONTROL_INTENT_WRITE_OPERATIONS.isdisjoint(registry_module._GATEWAY_CONTROL_OPERATIONS)
    assert registry_module._GATEWAY_AUTHENTICATED_CONTROL_WRITE_OPERATIONS == (
        registry_module._GATEWAY_CONTROL_WRITE_OPERATIONS | CONTROL_INTENT_WRITE_OPERATIONS
    )
    assert runtime_module._CONTROL_INTENT_OPERATIONS == CONTROL_INTENT_WRITE_OPERATIONS
    assert CONTROL_INTENT_APPLY_OPERATION in runtime_module.PORT_OPERATIONS["service.direct_write"]


def test_intent_operation_requires_explicit_registration(tmp_path) -> None:
    registry = _registry(tmp_path)
    try:
        registered = _register(
            registry,
            principal=_IDENTITY.principal_ref,
            identity=_IDENTITY,
            scope=_SCOPE,
            instance_id="gateway-a",
            port="service.direct_write",
            scope_ref="scope-a",
            operations=tuple(sorted(_OLD_BINDING_OPERATIONS)),
        )
        assert registered["ok"] is True
        active = registry.active_registration_contexts(
            instance_id="gateway-a", port="service.direct_write"
        )
        assert len(active) == 1
        assert active[0].operations == _OLD_BINDING_OPERATIONS
        assert CONTROL_INTENT_APPLY_OPERATION not in active[0].operations
        assert active[0].write_operations == frozenset()
    finally:
        registry.close()


def test_explicit_intent_registration_receives_argument_bound_admission(tmp_path) -> None:
    registry = _registry(tmp_path)
    seen: list[SimpleNamespace] = []

    def handler(context, arguments):
        admission = context.dispatch_admission
        assert type(admission) is ServiceDispatchAdmission
        assert admission.matches_context(context)
        assert admission.matches_operation(CONTROL_INTENT_APPLY_OPERATION)
        assert admission.matches(arguments)
        seen.append(SimpleNamespace(context=context, arguments=dict(arguments)))
        admission.close()
        return {"status": "OBSERVED", "cause_id": arguments["cause_id"]}

    registry.register_handler("gateway-a", CONTROL_INTENT_APPLY_OPERATION, handler)
    try:
        registered = _register(
            registry,
            principal=_IDENTITY.principal_ref,
            identity=_IDENTITY,
            scope=_SCOPE,
            instance_id="gateway-a",
            port="service.direct_write",
            scope_ref="scope-a",
            operations=tuple(sorted(_OPERATIONS)),
        )
        assert registered["ok"] is True
        response = registry.handle(
            _IDENTITY.principal_ref,
            _next_request(
                CONTROL_INTENT_APPLY_OPERATION,
                {
                    "registration_id": registered["result"]["registration_id"],
                    "service_epoch": registered["result"]["service_epoch"],
                    "instance_id": "gateway-a",
                    "arguments": _arguments(),
                },
                identity=_IDENTITY,
                scope=_SCOPE,
            ),
        )
        assert response == {
            "ok": True,
            "code": "OK",
            "result": {"status": "OBSERVED", "cause_id": "risk-cause-a"},
        }
        assert len(seen) == 1
        assert seen[0].context.identity == _IDENTITY
        assert seen[0].context.scope == _SCOPE
        assert seen[0].arguments == _arguments()
    finally:
        registry.close()


def test_old_control_write_is_not_reclassified_by_intent_vocabulary(tmp_path) -> None:
    registry = _registry(tmp_path)
    try:
        registered = _register(
            registry,
            principal=_IDENTITY.principal_ref,
            identity=_IDENTITY,
            scope=_SCOPE,
            instance_id="gateway-a",
            port="service.direct_write",
            scope_ref="scope-a",
            operations=tuple(sorted(_OPERATIONS)),
        )
        assert registered["ok"] is True
        response = registry.handle(
            _IDENTITY.principal_ref,
            _next_request(
                "direct.control.apply",
                {
                    "registration_id": registered["result"]["registration_id"],
                    "service_epoch": registered["result"]["service_epoch"],
                    "instance_id": "gateway-a",
                    "arguments": {"cause_id": "legacy", "mode": "STOP_NEW"},
                },
                identity=_IDENTITY,
                scope=_SCOPE,
            ),
        )
        assert response == {
            "ok": False,
            "code": "CAPABILITY_UNAVAILABLE",
            "result": {},
        }
    finally:
        registry.close()


def test_service_role_cannot_register_control_intent_write(tmp_path) -> None:
    service_identity = WireIdentity(WireRole.SERVICE, "risk-service", "risk-service")
    registry = _registry(tmp_path, identity=service_identity)
    try:
        registered = _register(
            registry,
            principal=service_identity.principal_ref,
            identity=service_identity,
            scope=_SCOPE,
            instance_id="gateway-a",
            port="service.direct_write",
            scope_ref="scope-a",
            operations=tuple(sorted(_OPERATIONS)),
        )
        assert registered == {"ok": False, "code": "SCOPE_DENIED", "result": {}}
    finally:
        registry.close()
