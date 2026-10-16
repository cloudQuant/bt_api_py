"""Synchronous application control path for the optional Monitor service.

This module is loaded only when the trusted service host selects the Monitor
``control`` role. The public adapter exposes ``monitor.control`` only. Its
owner claim/result calls stay in process, and Risk is imported lazily when a
real Risk owner is constructed.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_monitor.control import (
    ClaimedControlCommand,
    ControlAction,
    ControlOwnerOutcome,
    ControlStatus,
    monitor_freeze_cause_id,
)
from bt_api_monitor.service_control import (
    MONITOR_CONTROL_PUBLIC_PORT,
    MONITOR_CONTROL_PUBLIC_OPERATIONS,
    MONITOR_CONTROL_SUBMIT_OPERATIONS,
    MONITOR_CONTROL_STATUS_OPERATIONS,
    MonitorControlService,
    MonitorControlServiceError,
    MonitorControlTarget,
)

if TYPE_CHECKING:
    from bt_api_risk import AccountScope, DurableRiskGate


_MONITOR_FREEZE_CAUSE_RE = re.compile(r"^monitor-freeze-v1:[0-9a-f]{64}$")
_MONITOR_CONTROL_OPERATION = "monitor.control"
_MONITOR_CONTROL_STATUS_OPERATION = "monitor.control_status"
_OWNER_CLAIM_OPERATION = "monitor.claim"
_OWNER_ACK_OPERATION = "monitor.acknowledge"
_MAX_BACKLOG_DRAIN = 256
MONITOR_CONTROL_OPERATIONS = MONITOR_CONTROL_SUBMIT_OPERATIONS


class MonitorControlRuntimeError(RuntimeError):
    """Finite local owner/composition errors without command payload data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class MonitorRiskControlOwner:
    """Trusted in-process adapter from one Monitor route to one Risk gate.

    The route is fixed at construction. ``run_command`` claims only the named
    durable command for this exact account/mode/scope and executor, applies the
    idempotent Risk operation, then persists a typed owner result. ``run_next``
    is an internal backlog/recovery helper and is never a public RPC.
    """

    def __init__(
        self,
        service: MonitorControlService,
        gate: DurableRiskGate,
        risk_scope: AccountScope,
        *,
        wire_scope: WireScope,
        owner_identity: WireIdentity,
        executor_id: str,
        account_id: str,
        mode: str,
        command_scope: str,
    ) -> None:
        if type(service) is not MonitorControlService:
            raise MonitorControlRuntimeError("MONITOR_CONTROL_SERVICE_REQUIRED")
        try:
            from bt_api_risk import AccountScope, DurableRiskGate
        except ImportError:
            raise MonitorControlRuntimeError("RISK_OWNER_UNAVAILABLE") from None
        if type(gate) is not DurableRiskGate or type(risk_scope) is not AccountScope:
            raise MonitorControlRuntimeError("DURABLE_RISK_GATE_REQUIRED")
        if type(wire_scope) is not WireScope or type(owner_identity) is not WireIdentity:
            raise MonitorControlRuntimeError("INVALID_OWNER_ROUTE")
        if owner_identity.role is not WireRole.SERVICE:
            raise MonitorControlRuntimeError("OWNER_SERVICE_IDENTITY_REQUIRED")
        for value in (executor_id, account_id, mode, command_scope):
            if type(value) is not str or not value or value != value.strip():
                raise MonitorControlRuntimeError("INVALID_OWNER_ROUTE")
        target = service.route_targets.get(wire_scope)
        if type(target) is not MonitorControlTarget:
            raise MonitorControlRuntimeError("OWNER_ROUTE_NOT_CONFIGURED")
        if (
            target.owner_identity != owner_identity
            or target.executor_id != executor_id
            or target.account_id != account_id
            or target.mode != mode
            or target.command_scope != command_scope
            or risk_scope.account_id != account_id
            or risk_scope.environment != mode
        ):
            raise MonitorControlRuntimeError("OWNER_ROUTE_BINDING_MISMATCH")

        self._service = service
        self._target = target
        self._gate = gate
        self._risk_scope = risk_scope
        self._wire_scope = wire_scope
        self._owner_identity = owner_identity
        self._executor_id = executor_id
        self._account_id = account_id
        self._mode = mode
        self._command_scope = command_scope
        self._lock = threading.RLock()

    @property
    def wire_scope(self) -> WireScope:
        """Return the configured in-process route template."""

        return self._wire_scope

    @property
    def service(self) -> MonitorControlService:
        """Return the exact control service this owner is bound to."""

        return self._service

    def run_command(self, command_id: str) -> Mapping[str, object]:
        """Apply or recover one exact command and return its persisted state."""

        if type(command_id) is not str or not command_id or command_id != command_id.strip():
            raise MonitorControlRuntimeError("INVALID_COMMAND_ID")
        with self._lock:
            current = self._read(command_id)
            if current["status"] not in (ControlStatus.PENDING.value, ControlStatus.UNKNOWN.value):
                return current

            claim = self._owner_call(
                _OWNER_CLAIM_OPERATION,
                {"schema_version": 2, "command_id": command_id},
            )
            if claim.get("claimed") is not True:
                return self._read(command_id)
            row = self._target.ingress.ledger.get(command_id)
            if type(row) is not ClaimedControlCommand or row.status is not ControlStatus.CLAIMED:
                raise MonitorControlRuntimeError("CLAIMED_COMMAND_UNAVAILABLE")
            if row.executor_id != self._executor_id or not self._command_matches_route(row):
                raise MonitorControlRuntimeError("CLAIMED_COMMAND_ROUTE_MISMATCH")
            return self._execute_claimed(row)

    def run_next(self) -> Mapping[str, object] | None:
        """Claim and process the oldest eligible control on this fixed route."""

        with self._lock:
            claim = self._owner_call(
                _OWNER_CLAIM_OPERATION,
                {"schema_version": 1},
            )
            if claim.get("claimed") is not True:
                return None
            command = claim.get("command")
            if not isinstance(command, Mapping) or type(command.get("command_id")) is not str:
                raise MonitorControlRuntimeError("MALFORMED_CLAIM_RESULT")
            command_id = str(command["command_id"])
            row = self._target.ingress.ledger.get(command_id)
            if type(row) is not ClaimedControlCommand or row.status is not ControlStatus.CLAIMED:
                raise MonitorControlRuntimeError("CLAIMED_COMMAND_UNAVAILABLE")
            if row.executor_id != self._executor_id or not self._command_matches_route(row):
                raise MonitorControlRuntimeError("CLAIMED_COMMAND_ROUTE_MISMATCH")
            return self._execute_claimed(row)

    def _execute_claimed(self, row: ClaimedControlCommand) -> Mapping[str, object]:
        command_id = row.command.command_id
        outcome, effect_ref, effect_result = self._apply(row)
        try:
            return self._acknowledge(command_id, outcome, effect_ref, effect_result)
        except Exception:
            # The Risk transaction may have committed even if the final
            # Monitor acknowledgement did not. Record uncertainty; a later
            # same-executor v2 retry re-reads/replays the Risk operation.
            try:
                return self._acknowledge(command_id, ControlOwnerOutcome.UNKNOWN, None, None)
            except Exception:
                persisted = self._read(command_id)
                if persisted["status"] in (
                    ControlStatus.ACKNOWLEDGED.value,
                    ControlStatus.REJECTED.value,
                    ControlStatus.UNKNOWN.value,
                ):
                    return persisted
                raise MonitorControlRuntimeError("OWNER_OUTCOME_NOT_DURABLE") from None

    def _apply(
        self, row: ClaimedControlCommand
    ) -> tuple[ControlOwnerOutcome, str | None, Mapping[str, object] | None]:
        command = row.command
        try:
            if command.action is ControlAction.FREEZE:
                cause_id = monitor_freeze_cause_id(command)
                if _MONITOR_FREEZE_CAUSE_RE.fullmatch(cause_id) is None:
                    raise ValueError("Monitor freeze cause id is invalid")
                from bt_api_risk import FreezeActivationResult, FreezeActivationStatus

                activation = self._gate.activate_freeze_once(
                    self._risk_scope, cause_id, command.reason
                )
                if type(activation) is not FreezeActivationResult:
                    raise ValueError("Risk activator returned an invalid result")
                if type(activation.has_other_active_freezes) is not bool:
                    raise ValueError("Risk activator returned an invalid freeze summary")
                if activation.status in (
                    FreezeActivationStatus.CREATED,
                    FreezeActivationStatus.ALREADY_ACTIVE,
                ):
                    cause_active = True
                    state = "active"
                elif activation.status is FreezeActivationStatus.ALREADY_RESOLVED:
                    # A delayed duplicate must not resurrect a cause that a
                    # later exact RESUME already resolved. The command did
                    # apply historically, so retain an APPLIED owner outcome
                    # while reporting the current cause state explicitly.
                    cause_active = False
                    state = "resolved"
                else:
                    raise ValueError("Risk activator returned an unsupported status")
                return (
                    ControlOwnerOutcome.APPLIED,
                    cause_id,
                    MappingProxyType(
                        {
                            "action": "freeze",
                            "cause_id": cause_id,
                            "activation_status": activation.status.value,
                            "cause_active": cause_active,
                            "has_other_active_freezes": activation.has_other_active_freezes,
                            "risk_state": state,
                        }
                    ),
                )
            if command.action is ControlAction.RESUME:
                cause_id = command.target_freeze_cause_id
                if (
                    command.schema_version < 2
                    or type(cause_id) is not str
                    or _MONITOR_FREEZE_CAUSE_RE.fullmatch(cause_id) is None
                ):
                    return (
                        ControlOwnerOutcome.UNKNOWN,
                        None,
                        None,
                    )
                from bt_api_risk import FreezeResolution, FreezeResolutionStatus

                resolution = self._gate.resolve_freeze_exact(self._risk_scope, cause_id)
                if type(resolution) is not FreezeResolution:
                    raise ValueError("Risk resolver returned an invalid result")
                if type(resolution.has_other_active_freezes) is not bool:
                    raise ValueError("Risk resolver returned an invalid freeze summary")
                if resolution.status is FreezeResolutionStatus.NOT_FOUND:
                    outcome = ControlOwnerOutcome.REJECTED
                    effect_ref = None
                elif resolution.status in (
                    FreezeResolutionStatus.CLEARED,
                    FreezeResolutionStatus.ALREADY_RESOLVED,
                ):
                    outcome = ControlOwnerOutcome.APPLIED
                    effect_ref = cause_id
                else:
                    raise ValueError("Risk resolver returned an unsupported status")
                return (
                    outcome,
                    effect_ref,
                    MappingProxyType(
                        {
                            "action": "resume",
                            "cause_id": cause_id,
                            "resolution_status": resolution.status.value,
                            "has_other_active_freezes": resolution.has_other_active_freezes,
                            "account_still_frozen": resolution.has_other_active_freezes,
                        }
                    ),
                )
            if command.action is ControlAction.DRAIN:
                return (
                    ControlOwnerOutcome.REJECTED,
                    None,
                    MappingProxyType({"action": "drain", "result": "unsupported"}),
                )
        except Exception:
            return (ControlOwnerOutcome.UNKNOWN, None, None)
        return (ControlOwnerOutcome.UNKNOWN, None, None)

    def _acknowledge(
        self,
        command_id: str,
        outcome: ControlOwnerOutcome,
        effect_ref: str | None,
        effect_result: Mapping[str, object] | None,
    ) -> Mapping[str, object]:
        result = self._owner_call(
            _OWNER_ACK_OPERATION,
            {
                "schema_version": 2,
                "command_id": command_id,
                "outcome": outcome.value,
                "effect_ref": effect_ref,
                "effect_result": effect_result,
            },
        )
        if result.get("command_id") != command_id:
            raise MonitorControlRuntimeError("OWNER_OUTCOME_COMMAND_MISMATCH")
        expected_status = {
            ControlOwnerOutcome.APPLIED: ControlStatus.ACKNOWLEDGED.value,
            ControlOwnerOutcome.REJECTED: ControlStatus.REJECTED.value,
            ControlOwnerOutcome.UNKNOWN: ControlStatus.UNKNOWN.value,
        }[outcome]
        if result.get("status") != expected_status or result.get("owner_final") is not (
            outcome is not ControlOwnerOutcome.UNKNOWN
        ):
            raise MonitorControlRuntimeError("OWNER_OUTCOME_NOT_PERSISTED")
        return result

    def _owner_call(self, operation: str, arguments: Mapping[str, object]) -> Mapping[str, object]:
        return self._service.handle(
            operation,
            principal_ref=self._owner_identity.principal_ref,
            identity=self._owner_identity,
            scope=self._wire_scope,
            arguments=arguments,
        )

    def _read(self, command_id: str) -> Mapping[str, object]:
        return self._service.get_command_status(command_id, scope=self._wire_scope)

    def _command_matches_route(self, row: ClaimedControlCommand) -> bool:
        return (
            row.command.account_id == self._account_id
            and row.command.mode == self._mode
            and row.command.scope == self._command_scope
        )


class MonitorControlRuntime:
    """Submit public controls and maintain their fixed owners on the host thread."""

    OPERATIONS = MONITOR_CONTROL_OPERATIONS
    PUBLIC_OPERATIONS = MONITOR_CONTROL_PUBLIC_OPERATIONS
    PUBLIC_PORT = MONITOR_CONTROL_PUBLIC_PORT

    def __init__(
        self,
        service: MonitorControlService,
        owners: Mapping[WireScope, MonitorRiskControlOwner],
    ) -> None:
        if type(service) is not MonitorControlService:
            raise MonitorControlRuntimeError("MONITOR_CONTROL_SERVICE_REQUIRED")
        if not isinstance(owners, Mapping) or not owners:
            raise MonitorControlRuntimeError("CONTROL_OWNERS_REQUIRED")
        configured = service.route_targets
        if set(owners) != set(configured):
            raise MonitorControlRuntimeError("CONTROL_OWNER_ROUTES_MISMATCH")
        copied: dict[WireScope, MonitorRiskControlOwner] = {}
        for wire_scope, owner in owners.items():
            if type(wire_scope) is not WireScope or type(owner) is not MonitorRiskControlOwner:
                raise MonitorControlRuntimeError("INVALID_CONTROL_OWNER")
            target = configured[wire_scope]
            if owner.service is not service or owner._target is not target:
                raise MonitorControlRuntimeError("CONTROL_OWNER_TARGET_MISMATCH")
            copied[wire_scope] = owner
        self._service = service
        self._owners = MappingProxyType(copied)
        self._owner_by_target = {id(configured[route]): owner for route, owner in copied.items()}
        self._route_locks = {id(owner): threading.RLock() for owner in copied.values()}

    def handle(
        self,
        operation: str,
        *,
        principal_ref: str,
        identity: WireIdentity,
        scope: WireScope,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, object]:
        """Submit one authorized application request or read its exact status."""

        if type(operation) is not str or operation not in self.PUBLIC_OPERATIONS:
            raise MonitorControlRuntimeError("UNSUPPORTED_OPERATION")
        if operation in MONITOR_CONTROL_STATUS_OPERATIONS:
            return self._service.handle(
                operation,
                principal_ref=principal_ref,
                identity=identity,
                scope=scope,
                arguments=arguments,
            )
        target = self._service.target_for_scope(scope)
        owner = self._owner_by_target.get(id(target))
        if owner is None:
            raise MonitorControlRuntimeError("CONTROL_OWNER_ROUTE_UNAVAILABLE")
        with self._route_locks[id(owner)]:
            # Admission is synchronous; the Risk effect is deliberately left
            # for maintain(), which ServiceHost invokes on its owner thread.
            return self._service.handle(
                _MONITOR_CONTROL_OPERATION,
                principal_ref=principal_ref,
                identity=identity,
                scope=scope,
                arguments=arguments,
            )

    def maintain(self, _active_registration_ids: frozenset[str]) -> None:
        """Run durable owner actions only through the ServiceHost maintenance hook."""

        if type(_active_registration_ids) is not frozenset or any(
            type(value) is not str for value in _active_registration_ids
        ):
            raise MonitorControlRuntimeError("INVALID_ACTIVE_REGISTRATIONS")
        for owner in self._owners.values():
            with self._route_locks[id(owner)]:
                for _ in range(_MAX_BACKLOG_DRAIN):
                    result = owner.run_next()
                    if result is None:
                        break
                    if result.get("status") in (
                        ControlStatus.CLAIMED.value,
                        ControlStatus.UNKNOWN.value,
                    ):
                        # The earlier sequence is unresolved. Do not overtake it.
                        break

    def handle_context(self, context: object, arguments: Mapping[str, Any]) -> Mapping[str, object]:
        """Convenience bridge from ServiceCallContext for in-process tests."""

        from bt_api_py.service_registry import ServiceCallContext

        if type(context) is not ServiceCallContext:
            raise MonitorControlRuntimeError("SERVICE_CALL_CONTEXT_REQUIRED")
        return self.handle(
            _MONITOR_CONTROL_OPERATION,
            principal_ref=context.principal_ref,
            identity=context.identity,
            scope=context.scope,
            arguments=arguments,
        )


__all__ = [
    "MONITOR_CONTROL_OPERATIONS",
    "MonitorControlRuntime",
    "MonitorControlRuntimeError",
    "MonitorControlServiceError",
    "MonitorRiskControlOwner",
]
