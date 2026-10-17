"""Rebuild accepted two-leg v1/v2 OPEN-derived source facts for DIM05 roll/exit.

This adapter is a read-only capability for the Risk group-roll-exit bridge.
It accepts current registration snapshots supplied by the Risk Registry owner,
then independently reconstructs the selected source leg from the coordinator's
accepted run binding, the account's accepted reservation, Execution's group
journal and the existing Risk-backed terminal proof.  Only source children
derived from an accepted OPEN leg are supported; close-origin legs need a
separate source-owned residual-allocation proof.  No request field can
introduce source identity, quantity, side, fill, or proof data.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from bt_api_base.service_protocol import WireRole, WireScope
from bt_api_execution import (
    ExecutionRecord,
    ExecutionScope,
    ExecutionState,
    GroupChildOrderStatus,
    GroupDispatchResolutionReceipt,
    GroupLegClaimReceipt,
    GroupLegOrderReceipt,
    GroupLegPhase,
    GroupLegProgress,
    GroupOrderEvent,
    GroupOrderIntent,
    GroupOrderLegPlan,
    GroupOrderSnapshot,
    GroupPermitBinding,
    OrderIntent,
    PositionEffect,
    SqliteExecutionStore,
    SqliteOrderGroupStore,
    payload_sha256,
)

from .service_execution_group_authority import ExecutionGroupAuthorityGuard
from .service_execution_group_client import (
    AcceptedGroupBinding,
    AcceptedGroupBindingError,
    AcceptedGroupBindingStore,
    GroupOriginRunBinding,
    GroupOriginRunBindingStore,
    validate_group_reservation_transition,
    verify_accepted_group_projection,
)
from .service_execution_group_proofs import (
    GroupProofJournalEvent,
    GroupProofSource,
    GroupProofSourceError,
    GroupProofTarget,
)
from .service_group_roll_exit import (
    GroupRollExitAuthorizationV1,
    GroupRollExitRequestV1,
    GroupRollExitSourceFactsV1,
)
from .service_registry import ActiveRegistrationContext

_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_QUERY_PORT = "service.order_query"
_ENTRY_PORT = "service.order_entry"
_QUERY_OPERATIONS = frozenset(
    {"orders.prepare_group_roll_exit", "orders.query_group_roll_exit"}
)
_ENTRY_OPERATIONS = frozenset({"orders.submit_group_roll_exit"})
_ROLL_EXIT_OPERATIONS = _QUERY_OPERATIONS | _ENTRY_OPERATIONS
_TERMINAL_POSITIVE = frozenset({ExecutionState.FILLED, ExecutionState.CANCELLED})
_MAX_GROUP_EVENTS = 100_000


class GroupRollExitAuthorizationSourceError(RuntimeError):
    """Finite refusal when accepted two-leg source provenance is unavailable."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or _REF.fullmatch(code) is None:
            raise ValueError("invalid group roll-exit source error")
        self.code = code
        super().__init__(code)


def _identity_payload(identity: object) -> dict[str, str | None]:
    if getattr(identity, "role", None) is None:
        raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_REGISTRATION_INVALID")
    return {
        "role": identity.role.value,
        "principal_ref": identity.principal_ref,
        "actor_ref": identity.actor_ref,
        "delegate_ref": identity.delegate_ref,
    }


def _scope_payload(scope: WireScope) -> dict[str, Any]:
    if type(scope) is not WireScope:
        raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_REGISTRATION_INVALID")
    return {
        "account_ref": scope.account_ref,
        "account_refs": list(scope.account_refs),
        "execution_domain_ref": scope.execution_domain_ref,
        "group_id": scope.group_id,
        "leg_id": scope.leg_id,
        "manual_book_ref": scope.manual_book_ref,
        "portfolio_ref": scope.portfolio_ref,
        "run_id": scope.run_id,
        "strategy_ref": scope.strategy_ref,
    }


def group_roll_exit_registration_digest(
    registration: ActiveRegistrationContext,
) -> str:
    """Canonical identity of every authorization-bearing registration field."""

    if type(registration) is not ActiveRegistrationContext:
        raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_REGISTRATION_INVALID")
    context = registration.context
    return payload_sha256(
        {
            "schema": "bt-api-dim05-roll-exit-registration-v1",
            "context": {
                "identity": _identity_payload(context.identity),
                "principal_ref": context.principal_ref,
                "registration_id": context.registration_id,
                "scope": _scope_payload(context.scope),
            },
            "expires_at": format(registration.expires_at, ".17g"),
            "instance_id": registration.instance_id,
            "operations": sorted(registration.operations),
            "policy_version": registration.policy_version,
            "port": registration.port,
            "requirements_version": registration.requirements_version,
            "scope_ref": registration.scope_ref,
            "service_epoch": registration.service_epoch,
            "strategy_write_run_id": registration.strategy_write_run_id,
            "write_operations": sorted(registration.write_operations),
        }
    )


@dataclass(frozen=True, slots=True)
class GroupRollExitAcceptedSourceV1:
    """Typed accepted-source projection, not itself Risk authorization."""

    operation: str
    request: GroupRollExitRequestV1
    query_registration_digest: str
    entry_registration_digest: str
    origin_binding: GroupOriginRunBinding
    accepted_binding: AcceptedGroupBinding
    group_snapshot: GroupOrderSnapshot
    source_plan: GroupOrderLegPlan
    source_progress: GroupLegProgress
    execution_record: ExecutionRecord
    source_receipt: GroupLegOrderReceipt
    source_event_digest: str
    group_event_chain_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.operation) is not str
            or self.operation not in _ROLL_EXIT_OPERATIONS
            or type(self.request) is not GroupRollExitRequestV1
            or type(self.origin_binding) is not GroupOriginRunBinding
            or type(self.accepted_binding) is not AcceptedGroupBinding
            or type(self.group_snapshot) is not GroupOrderSnapshot
            or type(self.source_plan) is not GroupOrderLegPlan
            or type(self.source_progress) is not GroupLegProgress
            or type(self.execution_record) is not ExecutionRecord
            or type(self.source_receipt) is not GroupLegOrderReceipt
            or type(self.source_event_digest) is not str
            or _SHA256.fullmatch(self.source_event_digest) is None
            or type(self.group_event_chain_digest) is not str
            or _SHA256.fullmatch(self.group_event_chain_digest) is None
        ):
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_SOURCE_PROJECTION_INVALID")

    @property
    def source_plan_payload(self) -> dict[str, Any]:
        plan = self.source_plan
        return plan.to_payload()

    @property
    def digest(self) -> str:
        return payload_sha256(
            {
                "schema": "bt-api-dim05-accepted-source-v1",
                "operation": self.operation,
                "request": self.request.to_payload(),
                "query_registration_digest": self.query_registration_digest,
                "entry_registration_digest": self.entry_registration_digest,
                "origin_request_fingerprint": self.origin_binding.request_fingerprint,
                "accepted_request_fingerprint": self.accepted_binding.request_fingerprint,
                "group_request_fingerprint": self.group_snapshot.request.fingerprint,
                "group_revision": self.group_snapshot.revision,
                "source_plan": self.source_plan.to_payload(),
                "source_progress": self.source_progress.to_payload(),
                "execution_payload_sha256": self.execution_record.payload_sha256,
                "source_receipt": self.source_receipt.to_payload(),
                "source_event_digest": self.source_event_digest,
                "group_event_chain_digest": self.group_event_chain_digest,
            }
        )


class GroupRollExitAuthorizationSourceV1:
    """Concrete account-owner source port for the exact DIM05 two-leg path.

    The six borrowed resources are shared with the already accepted group
    coordinator composition and remain owned by their original components.
    Risk supplies both live Registry snapshots on every call: the read-only
    query registration and the exact write registration that will be bound
    into the resulting Risk authorization.
    """

    def __init__(
        self,
        accepted_group_store: AcceptedGroupBindingStore,
        origin_run_binding_store: GroupOriginRunBindingStore,
        group_store: SqliteOrderGroupStore,
        execution_store: SqliteExecutionStore,
        proof_source: GroupProofSource,
        authority_guard: ExecutionGroupAuthorityGuard,
        *,
        coordinator_principal_ref: str,
    ) -> None:
        if (
            type(accepted_group_store) is not AcceptedGroupBindingStore
            or type(origin_run_binding_store) is not GroupOriginRunBindingStore
            or type(group_store) is not SqliteOrderGroupStore
            or type(execution_store) is not SqliteExecutionStore
            or type(proof_source) is not GroupProofSource
            or type(authority_guard) is not ExecutionGroupAuthorityGuard
            or type(coordinator_principal_ref) is not str
            or _REF.fullmatch(coordinator_principal_ref) is None
        ):
            raise TypeError("exact durable DIM05 source resources are required")
        proof_targets = tuple(proof_source.targets.values())
        if (
            len(proof_targets) != 1
            or type(proof_targets[0]) is not GroupProofTarget
            or proof_targets[0].execution_store is not execution_store
            or proof_source.runtime_bundle is None
            or proof_source.runtime_bundle.proof_source is not proof_source
            or proof_source.runtime_bundle.proof_targets != proof_source.targets
            or authority_guard._owner._reconciliation_source is not proof_source
        ):
            raise TypeError("DIM05 source resources do not share one proof composition")
        self._accepted_group_store = accepted_group_store
        self._origin_run_binding_store = origin_run_binding_store
        self._group_store = group_store
        self._execution_store = execution_store
        self._proof_source = proof_source
        self._authority_guard = authority_guard
        self._coordinator_principal_ref = coordinator_principal_ref
        self._proof_target = proof_targets[0]
        self._account_scope = self._proof_target.account_scope
        self._execution_scope = self._proof_target.execution_scope

    @property
    def composition(self) -> tuple[object, ...]:
        return (
            self._accepted_group_store,
            self._origin_run_binding_store,
            self._group_store,
            self._execution_store,
            self._proof_source,
            self._authority_guard,
        )

    @property
    def account_scope(self):
        return self._account_scope

    @property
    def execution_scope(self) -> ExecutionScope:
        return self._execution_scope

    @property
    def coordinator_principal_ref(self) -> str:
        return self._coordinator_principal_ref

    @property
    def ready(self) -> bool:
        try:
            if (
                self._accepted_group_store._closed
                or self._origin_run_binding_store._closed
                or self._proof_source._journal._closed
                or self._proof_source.runtime_bundle is None
                or self._proof_source.runtime_bundle.proof_source is not self._proof_source
                or self._proof_target.execution_store is not self._execution_store
                or self._proof_target.execution_scope != self._execution_scope
                or self._proof_target.account_scope is not self._account_scope
                or self._authority_guard._owner._reconciliation_source is not self._proof_source
            ):
                return False
            for lock, connection in (
                (self._group_store._lock, self._group_store._connection),
                (self._execution_store._lock, self._execution_store._connection),
                (self._proof_source._journal._lock, self._proof_source._journal._connection),
            ):
                with lock:
                    connection.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    def resolve_group_roll_exit(
        self,
        query_registration: ActiveRegistrationContext,
        request: GroupRollExitRequestV1,
        *,
        operation: str,
        entry_registration: ActiveRegistrationContext,
    ) -> GroupRollExitAcceptedSourceV1:
        """Resolve trusted source rows for prepare/query/submit without writes."""

        try:
            self._validate_registration_pair(
                query_registration,
                entry_registration,
                operation=operation,
            )
            if type(request) is not GroupRollExitRequestV1:
                raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_REQUEST_INVALID")
            return self._rebuild_source(
                query_registration,
                entry_registration,
                request,
                operation=operation,
            )
        except GroupRollExitAuthorizationSourceError:
            raise
        except Exception:
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_SOURCE_UNAVAILABLE") from None

    def assert_authorization(
        self,
        query_registration: ActiveRegistrationContext,
        authorization: GroupRollExitAuthorizationV1,
        *,
        operation: str,
        entry_registration: ActiveRegistrationContext,
    ) -> GroupRollExitAcceptedSourceV1:
        """Rebuild accepted rows and verify auth's source/write-registration binding."""

        try:
            self._validate_registration_pair(
                query_registration,
                entry_registration,
                operation=operation,
            )
            if type(authorization) is not GroupRollExitAuthorizationV1:
                raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_AUTHORIZATION_INVALID")
            request = authorization.request
            source = self._rebuild_source(
                query_registration,
                entry_registration,
                authorization.request,
                operation=operation,
            )
            facts = authorization.source
            authorization_revision = self._authorization_plan_revision(
                source, authorization, operation=operation
            )
            origin = source.origin_binding
            snapshot = source.group_snapshot
            progress = source.source_progress
            plan = source.source_plan
            intent = progress.submitted_intent
            status = source.source_receipt.status
            claim = source.source_receipt.claim
            resolution = source.source_receipt.resolution
            expected_service = {
                "service_principal_ref": entry_registration.context.principal_ref,
                "service_instance_id": entry_registration.instance_id,
                "service_scope_ref": entry_registration.scope_ref,
                "service_registration_id": entry_registration.context.registration_id,
                "service_operation": "orders.submit_group_roll_exit",
                "service_port": _ENTRY_PORT,
                "service_registration_digest": source.entry_registration_digest,
                "service_epoch": entry_registration.service_epoch,
                "service_policy_version": entry_registration.policy_version,
                "service_requirements_version": entry_registration.requirements_version,
                "service_write_run_id": entry_registration.strategy_write_run_id,
            }
            risk_reservations = tuple(
                item for item in snapshot.reservations
                if item.account_ref == entry_registration.context.scope.account_ref
            )
            if len(risk_reservations) != 1:
                raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_RESERVATION_MISMATCH")
            risk_legs = tuple(
                item for item in risk_reservations[0].legs
                if item.leg_id == request.source_leg_id
            )
            if len(risk_legs) != 1 or type(risk_legs[0].actual_intent_hash) is not str:
                raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_RISK_LEG_MISMATCH")
            expected_source = {
                "portfolio_ref": snapshot.request.portfolio_ref,
                "origin_run_id": origin.origin_run_id,
                "group_id": snapshot.request.group_id,
                "group_version": snapshot.request.version,
                "group_revision": authorization_revision,
                "group_fingerprint": snapshot.request.fingerprint,
                "source_risk_reservation_fingerprint": risk_reservations[0].group_fingerprint,
                "source_risk_intent_fingerprint": risk_legs[0].actual_intent_hash,
                "group_request": snapshot.request,
                "account_ref": entry_registration.context.scope.account_ref,
                "strategy_id": snapshot.request.strategy_id,
                "source_leg_id": request.source_leg_id,
                "source_intent_id": plan.maximum_intent.intent_id,
                "source_request_fingerprint": intent.fingerprint,
                "source_child_intent": intent,
                "source_execution_scope": self._execution_scope,
                "source_terminal_status": status,
                "source_requested_quantity": plan.maximum_intent.quantity,
                "source_claim": claim,
                "source_risk_resolution": resolution,
                "source_event_digest": source.source_event_digest,
                "source_side": intent.side,
                "instrument": intent.instrument,
                "source_metadata_version": intent.metadata_version,
                **expected_service,
            }
            if any(getattr(facts, key) != value for key, value in expected_source.items()):
                raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_SOURCE_BINDING_MISMATCH")
            if (
                facts.group_fingerprint != snapshot.request.fingerprint
                or facts.group_revision != authorization_revision
                or facts.source_execution_scope != self._execution_scope
                or facts.source_position_side != intent.position_side
                or facts.source_position_bucket != intent.position_bucket
                or authorization.request != request
                or facts.service_write_run_id != origin.origin_run_id
            ):
                raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_SOURCE_BINDING_MISMATCH")
            return source
        except GroupRollExitAuthorizationSourceError:
            raise
        except Exception:
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_AUTHORIZATION_UNAVAILABLE") from None

    def _authorization_plan_revision(
        self,
        source: GroupRollExitAcceptedSourceV1,
        authorization: GroupRollExitAuthorizationV1,
        *,
        operation: str,
    ) -> int:
        """Bind an advanced group to this authorization's durable plan prefix."""

        snapshot = source.group_snapshot
        revision = authorization.source.group_revision
        if revision == snapshot.revision:
            # Preparation and read-only query may precede a durable plan.
            # Submission requires the plan/start evidence checked below.
            if operation == "orders.submit_group_roll_exit":
                raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_SOURCE_BINDING_MISMATCH")
            return revision
        from .service_execution_group import _owner_roll_exit_actions

        history = self._group_store.events(
            snapshot.request.portfolio_ref, snapshot.request.group_id
        )
        if _group_event_chain_digest(history, snapshot) != source.group_event_chain_digest:
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_SOURCE_BINDING_MISMATCH")
        actions = _owner_roll_exit_actions(snapshot, history)
        matches = tuple(item for item in actions if item["action_id"] == authorization.action_id)
        allowed_phases = {"PLANNED", "PREPARED", "STARTED", "OBSERVED", "RESOLVED"}
        if operation == "orders.submit_group_roll_exit":
            allowed_phases = {"STARTED", "OBSERVED", "RESOLVED"}
        if (
            len(matches) != 1
            or matches[0]["plan_index"] - 1 != revision
            or matches[0]["authorization_sha256"] != authorization.authorization_sha256
            or matches[0]["authorization"].to_payload() != authorization.to_payload()
            or matches[0]["phase"] not in allowed_phases
            or any(
                event.event_type not in {
                    "GROUP_ROLL_EXIT_PLAN_RECORDED",
                    "GROUP_ROLL_EXIT_SUBMIT_STARTED",
                    "GROUP_ROLL_EXIT_STATUS_OBSERVED",
                    "GROUP_ROLL_EXIT_RESOLVED",
                    "GROUP_ROLL_EXIT_CLOSED",
                }
                for event in history[revision + 1:]
            )
        ):
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_SOURCE_BINDING_MISMATCH")
        return revision

    def _validate_registration_pair(
        self,
        query_registration: ActiveRegistrationContext,
        entry_registration: ActiveRegistrationContext,
        *,
        operation: str,
    ) -> None:
        if (
            type(query_registration) is not ActiveRegistrationContext
            or type(entry_registration) is not ActiveRegistrationContext
            or type(operation) is not str
            or operation not in _ROLL_EXIT_OPERATIONS
        ):
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_REGISTRATION_INVALID")
        now = time.time()
        query_context = query_registration.context
        entry_context = entry_registration.context
        query_scope = query_context.scope
        entry_scope = entry_context.scope
        if (
            query_registration.port != _QUERY_PORT
            or query_registration.operations != _QUERY_OPERATIONS
            or query_registration.write_operations
            or operation in _QUERY_OPERATIONS and operation not in query_registration.operations
            or entry_registration.port != _ENTRY_PORT
            or entry_registration.operations != _ENTRY_OPERATIONS
            or entry_registration.write_operations != _ENTRY_OPERATIONS
            or "orders.submit_group_roll_exit" not in entry_registration.write_operations
            or query_context.identity.role is not WireRole.SERVICE
            or entry_context.identity.role is not WireRole.SERVICE
            or query_context.identity != entry_context.identity
            or query_context.principal_ref != self._coordinator_principal_ref
            or entry_context.principal_ref != self._coordinator_principal_ref
            or query_context.identity.principal_ref != query_context.principal_ref
            or entry_context.identity.principal_ref != entry_context.principal_ref
            or query_scope != entry_scope
            or query_registration.strategy_write_run_id != entry_registration.strategy_write_run_id
            or query_registration.expires_at <= now
            or entry_registration.expires_at <= now
            or type(query_context.registration_id) is not str
            or _REF.fullmatch(query_context.registration_id) is None
            or type(entry_context.registration_id) is not str
            or _REF.fullmatch(entry_context.registration_id) is None
            or type(entry_registration.strategy_write_run_id) is not str
            or _REF.fullmatch(entry_registration.strategy_write_run_id) is None
            or type(query_scope) is not WireScope
            or query_scope.execution_domain_ref is None
            or query_scope.account_ref != self._execution_scope.account_ref
            or query_scope.account_refs
            or query_scope.strategy_ref != self._execution_scope.strategy_id
            or query_scope.run_id is not None
            or query_scope.manual_book_ref is not None
            or query_scope.portfolio_ref is None
            or query_scope.group_id is not None
            or query_scope.leg_id is not None
            or self._proof_source.target_for_template(query_scope) is not self._proof_target
        ):
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_REGISTRATION_DENIED")

    def _rebuild_source(
        self,
        query_registration: ActiveRegistrationContext,
        entry_registration: ActiveRegistrationContext,
        request: GroupRollExitRequestV1,
        *,
        operation: str,
    ) -> GroupRollExitAcceptedSourceV1:
        query_scope = query_registration.context.scope
        entry_scope = entry_registration.context.scope
        if request.portfolio_ref != entry_scope.portfolio_ref:
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_PORTFOLIO_MISMATCH")
        origin = self._origin_run_binding_store.get(request.portfolio_ref, request.group_id)
        if type(origin) is not GroupOriginRunBinding:
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_ORIGIN_BINDING_MISSING")
        origin_request = origin.request
        if (
            origin.origin_run_id != entry_registration.strategy_write_run_id
            or origin.coordinator_principal_ref != self._coordinator_principal_ref
            or origin_request.group_id != request.group_id
            or origin_request.portfolio_ref != request.portfolio_ref
            or origin_request.coordinator_principal_ref != self._coordinator_principal_ref
            or origin_request_fingerprint(origin) != origin_request.fingerprint
            or origin_request.version not in {1, 2}
            or len(origin_request.legs) != 2
            or origin_request.compensation_budget_policy is not None
        ):
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_ORIGIN_BINDING_MISMATCH")
        try:
            accepted = self._accepted_group_store.get_binding(
                entry_registration,
                origin_run_id=origin.origin_run_id,
                group_id=request.group_id,
                operation="orders.submit_group_roll_exit",
            )
            snapshot = self._group_store.get(request.portfolio_ref, request.group_id)
            history = self._group_store.events(request.portfolio_ref, request.group_id)
        except AcceptedGroupBindingError:
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_ACCEPTED_BINDING_UNAVAILABLE") from None
        except Exception:
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_GROUP_JOURNAL_UNAVAILABLE") from None
        if type(accepted) is not AcceptedGroupBinding or type(snapshot) is not GroupOrderSnapshot:
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_ACCEPTED_BINDING_MISSING")
        try:
            accepted_fingerprint = verify_accepted_group_projection(origin, snapshot, history)
        except AcceptedGroupBindingError:
            raise GroupRollExitAuthorizationSourceError(
                "GROUP_ROLL_EXIT_GROUP_BINDING_MISMATCH"
            ) from None
        if (
            accepted.principal_ref != self._coordinator_principal_ref
            or accepted.group_id != request.group_id
            or accepted.origin_run_id != origin.origin_run_id
            or accepted.stable_scope != entry_scope
            or accepted.account_ref != entry_scope.account_ref
            or accepted.portfolio_ref != request.portfolio_ref
            or accepted.strategy_id != origin_request.strategy_id
            or accepted.reservation is None
            or accepted_fingerprint != snapshot.request.fingerprint
            or snapshot.request.version not in {1, 2}
            or len(snapshot.request.legs) != 2
            or snapshot.request.compensation_budget_policy is not None
        ):
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_GROUP_BINDING_MISMATCH")
        account_legs = tuple(
            (leg.leg_id, leg.maximum_intent)
            for leg in snapshot.request.legs
            if leg.account_ref == entry_scope.account_ref
        )
        if not account_legs or accepted.members != account_legs:
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_ACCOUNT_LEGS_MISMATCH")
        reservations = tuple(
            item for item in snapshot.reservations if item.account_ref == entry_scope.account_ref
        )
        if len(reservations) != 1:
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_RESERVATION_MISMATCH")
        try:
            # The accepted binding holds the durable reservation baseline,
            # while the group journal may contain a later authenticated claim
            # or terminal-resolution projection. Require the established
            # monotonic reservation transition instead of equality so source
            # proof can use the current Risk leg state without discarding the
            # original reservation identity.
            validate_group_reservation_transition(accepted.reservation, reservations[0])
        except Exception:
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_RESERVATION_MISMATCH") from None
        reservation_leg_ids = tuple(item.leg_id for item in reservations[0].legs)
        if reservation_leg_ids != tuple(leg_id for leg_id, _ in account_legs):
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_RESERVATION_MISMATCH")
        plans = tuple(leg for leg in snapshot.request.legs if leg.leg_id == request.source_leg_id)
        progresses = tuple(item for item in snapshot.legs if item.leg_id == request.source_leg_id)
        if len(plans) != 1 or len(progresses) != 1 or plans[0].account_ref != entry_scope.account_ref:
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_SOURCE_LEG_MISSING")
        plan = plans[0]
        progress = progresses[0]
        child_intent = progress.submitted_intent
        status = progress.child_status
        claim = progress.claim_receipt
        resolution = progress.resolution_receipt
        if (
            progress.phase is not GroupLegPhase.OBSERVED
            or type(child_intent) is not OrderIntent
            or type(status) is not GroupChildOrderStatus
            or type(claim) is not GroupLegClaimReceipt
            or type(resolution) is not GroupDispatchResolutionReceipt
            or plan.maximum_intent.position_effect is not PositionEffect.OPEN
            or child_intent.position_effect is not PositionEffect.OPEN
            or status.state not in _TERMINAL_POSITIVE
            or status.intent_id != child_intent.intent_id
            or status.filled_quantity <= 0
            or status.filled_quantity > child_intent.quantity
            or status.trade_count is None
            or status.trade_count <= 0
            or status.order_revision is None
            or status.state is ExecutionState.FILLED and status.filled_quantity != child_intent.quantity
            or child_intent.intent_id != plan.maximum_intent.intent_id
            or replace(plan.maximum_intent, quantity=child_intent.quantity) != child_intent
            or claim is None
            or claim.permit.group_id != snapshot.request.group_id
            or claim.permit.leg_id != request.source_leg_id
            or claim.permit.account_ref != entry_scope.account_ref
            or claim.permit.portfolio_ref != request.portfolio_ref
            or claim.permit.strategy_id != snapshot.request.strategy_id
            or claim.permit.coordinator_principal_ref != self._coordinator_principal_ref
            or claim.permit.group_fingerprint != reservations[0].group_fingerprint
            or claim.permit.order_intent_hash != child_intent.fingerprint
            # The claim's actual_child_intent_hash is the mapped RiskIntent
            # fingerprint. The execution OrderIntent fingerprint is separately
            # bound by GroupPermitBinding.order_intent_hash below.
            or claim.actual_quantity != child_intent.quantity
            or resolution.claim != claim
            or resolution.resolution_kind != "TERMINAL_EXPOSURE_RESOLVED"
            or resolution.final_state is not status.state
            or resolution.requested_quantity != child_intent.quantity
            or resolution.filled_quantity != status.filled_quantity
            or resolution.trade_count != status.trade_count
            or resolution.order_revision != status.order_revision
            or resolution.dispatch_latch_active
        ):
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_SOURCE_NOT_TERMINAL")
        risk_leg_matches = tuple(
            row for row in reservations[0].legs if row.leg_id == request.source_leg_id
        )
        if (
            len(risk_leg_matches) != 1
            or risk_leg_matches[0].permit_id != claim.permit.permit_id
            or risk_leg_matches[0].intent_id != child_intent.intent_id
            or risk_leg_matches[0].reserved_intent_hash != claim.permit.reserved_intent_hash
            or risk_leg_matches[0].exposure_profile_fingerprint != claim.permit.exposure_profile_fingerprint
            or risk_leg_matches[0].actual_intent_hash != claim.actual_child_intent_hash
            or not risk_leg_matches[0].claimed
            or not risk_leg_matches[0].resolved
        ):
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_RISK_LEG_MISMATCH")
        record = self._execution_store.get(child_intent.intent_id, scope=self._execution_scope)
        if (
            type(record) is not ExecutionRecord
            or record.dispatch_attempts != 1
            or record.group_binding is None
            or type(record.group_binding) is not GroupPermitBinding
            or record.payload_sha256 != child_intent.fingerprint
        ):
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_EXECUTION_RECORD_MISSING")
        try:
            expected_group_binding = claim.permit.bind_actual_child(
                claim.actual_child_intent_hash
            )
            expected_group_binding.validate_order(child_intent)
        except Exception:
            raise GroupRollExitAuthorizationSourceError(
                "GROUP_ROLL_EXIT_EXECUTION_RECORD_MISSING"
            ) from None
        if record.group_binding != expected_group_binding:
            raise GroupRollExitAuthorizationSourceError(
                "GROUP_ROLL_EXIT_EXECUTION_RECORD_MISSING"
            )
        wire_scope = _group_wire_scope(entry_scope, snapshot.request.group_id, request.source_leg_id)
        try:
            receipt = self._proof_source.read_receipt(
                binding=record.group_binding,
                intent=child_intent,
                permit=claim.permit,
                principal_ref=entry_registration.context.principal_ref,
                identity=entry_registration.context.identity,
                wire_scope=wire_scope,
                service_context=entry_registration.context,
                active_authority=self._authority_guard,
            )
            proof_events = self._proof_source._events(self._proof_target, child_intent.intent_id)
        except GroupProofSourceError:
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_RISK_PROOF_UNAVAILABLE") from None
        except Exception:
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_RISK_PROOF_UNAVAILABLE") from None
        if (
            type(receipt) is not GroupLegOrderReceipt
            or receipt.status != status
            or receipt.claim != claim
            or receipt.resolution != resolution
            or receipt.resolution is None
        ):
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_RISK_PROOF_MISMATCH")
        observed = tuple(event for event in proof_events if event.event_type == "SOURCE_OBSERVED")
        reconciled = tuple(event for event in proof_events if event.event_type == "SOURCE_RECONCILED")
        if len(observed) > 1 or len(reconciled) > 1 or not (observed or reconciled):
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_SOURCE_EVENT_INVALID")
        source_event = reconciled[-1] if reconciled else observed[-1]
        if (
            source_event.group_id != snapshot.request.group_id
            or source_event.leg_id != request.source_leg_id
            or source_event.intent_id != child_intent.intent_id
            or source_event.payload.get("state") != status.state.value
            or source_event.payload.get("status") != status.to_payload()
            or source_event.payload.get("intent_fingerprint") != child_intent.fingerprint
            or source_event.payload.get("claim_digest") != claim.claim_digest
        ):
            raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_SOURCE_EVENT_MISMATCH")
        group_digest = _group_event_chain_digest(history, snapshot)
        return GroupRollExitAcceptedSourceV1(
            operation=operation,
            request=request,
            query_registration_digest=group_roll_exit_registration_digest(query_registration),
            entry_registration_digest=group_roll_exit_registration_digest(entry_registration),
            origin_binding=origin,
            accepted_binding=accepted,
            group_snapshot=snapshot,
            source_plan=plan,
            source_progress=progress,
            execution_record=record,
            source_receipt=receipt,
            source_event_digest=source_event.record_sha256,
            group_event_chain_digest=group_digest,
        )


def origin_request_fingerprint(origin: GroupOriginRunBinding) -> str:
    """Read-through guard that the coordinator row retained its request hash."""

    if type(origin) is not GroupOriginRunBinding:
        raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_ORIGIN_BINDING_INVALID")
    return origin.request_fingerprint


def _group_wire_scope(scope: WireScope, group_id: str, leg_id: str) -> WireScope:
    return replace(scope, group_id=group_id, leg_id=leg_id)


def _group_event_chain_digest(
    events: tuple[GroupOrderEvent, ...], snapshot: GroupOrderSnapshot
) -> str:
    if (
        type(events) is not tuple
        or len(events) != snapshot.revision + 1
        or len(events) > _MAX_GROUP_EVENTS
        or any(type(event) is not GroupOrderEvent for event in events)
        or any(type(event.sequence) is not int or event.sequence <= 0 for event in events)
        or any(
            events[index - 1].sequence >= events[index].sequence
            for index in range(1, len(events))
        )
        or any(event.group_id != snapshot.request.group_id for event in events)
    ):
        raise GroupRollExitAuthorizationSourceError("GROUP_ROLL_EXIT_GROUP_EVENT_CHAIN_INVALID")
    rows = [
        {
            "created_at_ns": event.created_at_ns,
            "event_type": event.event_type,
            "group_id": event.group_id,
            "payload": dict(event.payload),
            "sequence": event.sequence,
        }
        for event in events
    ]
    return payload_sha256(
        {
            "schema": "bt-api-dim05-group-event-chain-v1",
            "group_id": snapshot.request.group_id,
            "group_revision": snapshot.revision,
            "events": rows,
        }
    )


__all__ = [
    "GroupRollExitAcceptedSourceV1",
    "GroupRollExitAuthorizationSourceError",
    "GroupRollExitAuthorizationSourceV1",
    "group_roll_exit_registration_digest",
]
