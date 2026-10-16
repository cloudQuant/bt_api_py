"""Reconstruct source facts for the internal P5B compensation bridge.

The bridge receives no source authority from its wire payload.  This adapter
rebuilds the accepted group, source child proof, and compensation lifecycle
from the exact stores owned by the existing coordinator/account-owner
composition.  All stores are borrowed and remain owned by their components.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType
from typing import Any

from bt_api_base.service_protocol import WireRole, WireScope
from bt_api_execution import (
    ExecutionRecord,
    ExecutionScope,
    ExecutionState,
    GroupChildOrderStatus,
    GroupDispatchResolutionReceipt,
    GroupLegOrderReceipt,
    GroupLegPermitReference,
    GroupOrderSnapshot,
    GroupOrderState,
    GroupPermitBinding,
    OrderIntent,
    Side,
    SqliteExecutionStore,
    SqliteOrderGroupStore,
    payload_sha256,
)

from .service_execution_group_authority import ExecutionGroupAuthorityGuard
from .service_execution_group_client import (
    AcceptedGroupBinding,
    AcceptedGroupBindingStore,
    GroupOriginRunBinding,
    GroupOriginRunBindingStore,
    verify_accepted_group_projection,
    validate_group_reservation_transition,
)
from .service_execution_group_proofs import (
    GroupProofSource,
    GroupProofSourceError,
    GroupProofTarget,
)
from .service_group_compensation_contracts import (
    GroupCompensationActionPhase,
    GroupCompensationActionRef,
    GroupCompensationCloseAuthorization,
    GroupCompensationClosureFacts,
    GroupCompensationResolvedActionRef,
    GroupCompensationSourceLegFacts,
    GroupCompensationSourceFacts,
    LIMIT_CLOSE_NOTIONAL_V1,
    derive_group_compensation_action_id,
    derive_group_compensation_client_order_id,
    derive_group_compensation_intent_id,
    group_compensation_budget_policy_fingerprint,
)
from .service_registry import ActiveRegistrationContext, ServiceCallContext

_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMPENSATION_OPERATIONS = frozenset(
    {
        "orders.prepare_group_compensation_close",
        "orders.query_group_compensation_close",
        "orders.submit_group_compensation_close",
    }
)
_QUERY_OPERATIONS = frozenset(
    {
        "orders.prepare_group_compensation_close",
        "orders.query_group_compensation_close",
    }
)
_QUERY_PORT = "service.order_query"
_ENTRY_PORT = "service.order_entry"
_TERMINAL = frozenset(
    {ExecutionState.FILLED, ExecutionState.CANCELLED, ExecutionState.REJECTED}
)
_MAX_GROUP_EVENTS = 100_000
_MAX_COMPENSATION_ACTIONS = 9


class GroupCompensationAuthorizationSourceError(RuntimeError):
    """Finite refusal when exact durable compensation provenance is absent."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or _REF.fullmatch(code) is None:
            raise ValueError("invalid compensation source error")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _CompensationEvent:
    authorization: GroupCompensationCloseAuthorization
    plan_index: int
    phase: GroupCompensationActionPhase
    status_payloads: tuple[Mapping[str, Any], ...]
    receipt: Mapping[str, Any] | None


class GroupCompensationAuthorizationSourceV1:
    """Concrete read-only source port required by the Risk compensation bridge.

    Constructor order is intentionally fixed and is part of the account-owner
    component composition contract:

    ``(accepted_group_store, origin_run_binding_store, group_store,
    execution_store, proof_source, authority_guard)``.
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
            raise TypeError("exact durable compensation source resources are required")
        proof_targets = tuple(proof_source.targets.values())
        if (
            len(proof_targets) != 1
            or type(proof_targets[0]) is not GroupProofTarget
            or proof_targets[0].execution_store is not execution_store
            or proof_source.runtime_bundle is None
            or proof_source.runtime_bundle.proof_source is not proof_source
            or proof_source.runtime_bundle.proof_targets
            != proof_source.targets
            or authority_guard._owner._reconciliation_source is not proof_source
        ):
            raise TypeError("compensation source resources do not share one proof composition")
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
        """Exact borrowed-source identity tuple used by component validation."""

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
        """Check live handle and identity composition without closing resources."""

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

    def resolve_action(
        self,
        registration: ActiveRegistrationContext,
        action_ref: GroupCompensationActionRef,
        *,
        operation: str,
    ) -> GroupCompensationSourceFacts:
        """Resolve the next one-shot action from accepted durable group facts."""

        try:
            if type(action_ref) is not GroupCompensationActionRef:
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_ACTION_REF_INVALID")
            accepted, origin, snapshot, history = self._resolve_group(
                registration, action_ref.group_id, operation=operation
            )
            if (
                snapshot.state not in {GroupOrderState.RECOVERY_REQUIRED, GroupOrderState.UNWINDING}
                or snapshot.request.version != 3
                or snapshot.request.compensation_budget_policy is None
                or accepted.reservation is None
            ):
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_GROUP_NOT_RECOVERABLE")
            selected = self._selected_leg(snapshot, action_ref.source_leg_id)
            parsed = self._parse_compensation_events(history)
            pending = [event for event in parsed if event.phase is not GroupCompensationActionPhase.RESOLVED]
            if pending:
                if len(pending) != 1 or pending[0].authorization.source_leg_id != action_ref.source_leg_id:
                    raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_ACTION_PENDING")
                current = pending[0]
                return self._facts_for_action(
                    registration,
                    accepted,
                    origin,
                    snapshot,
                    history[: current.plan_index],
                    current.authorization,
                    phase=current.phase,
                )
            prior_refs = self._resolved_action_refs(parsed)
            selected_ordinal = 1 + sum(
                item.source_leg_id == action_ref.source_leg_id for item in prior_refs
            )
            if selected_ordinal > 1_000_000:
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_ORDINAL_LIMIT")
            source = self._verified_source_set(registration, accepted, snapshot)
            selected_data = source.get(action_ref.source_leg_id)
            if selected_data is None:
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_SOURCE_LEG_MISSING")
            plan, intent, status, claim, resolution, source_event_digest = selected_data
            prior_for_group = prior_refs
            action_id = derive_group_compensation_action_id(
                group_id=snapshot.request.group_id,
                group_fingerprint=snapshot.request.fingerprint,
                source_leg_id=action_ref.source_leg_id,
                source_event_digest=source_event_digest,
                ordinal=selected_ordinal,
            )
            provisional = self._facts_from_verified_source(
                registration=registration,
                accepted=accepted,
                origin=origin,
                snapshot=snapshot,
                source=source,
                selected_leg_id=action_ref.source_leg_id,
                action_id=action_id,
                ordinal=selected_ordinal,
                prior_refs=prior_for_group,
                phase=GroupCompensationActionPhase.UNSTARTED,
            )
            # A pending plan can be present only between the coordinator's
            # durable PLAN_RECORDED event and its submit marker. The parsed
            # branch above returns that exact phase; this is the only path
            # that can mint a fresh authorization source digest.
            return provisional
        except GroupCompensationAuthorizationSourceError:
            raise
        except Exception:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_SOURCE_UNAVAILABLE") from None

    def assert_authorization(
        self,
        registration: ActiveRegistrationContext,
        authorization: GroupCompensationCloseAuthorization,
        *,
        operation: str,
    ) -> GroupCompensationSourceFacts:
        """Rebuild the exact plan and monotonic phase from the event chain."""

        try:
            if (
                type(authorization) is not GroupCompensationCloseAuthorization
                or operation not in {"orders.submit_group_compensation_close", "orders.query_group_compensation_close"}
            ):
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_AUTHORIZATION_INVALID")
            accepted, origin, snapshot, history = self._resolve_group(
                registration, authorization.group_id, operation=operation
            )
            parsed = self._parse_compensation_events(history)
            matches = [item for item in parsed if item.authorization.action_id == authorization.action_id]
            if len(matches) != 1 or matches[0].authorization.to_payload() != authorization.to_payload():
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_PLAN_UNVERIFIED")
            current = matches[0]
            if authorization.origin_run_id != origin.origin_run_id:
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_RUN_BINDING_MISMATCH")
            facts = self._facts_for_action(
                registration,
                accepted,
                origin,
                snapshot,
                history[: current.plan_index],
                authorization,
                phase=current.phase,
            )
            if (
                authorization.source_facts_digest != facts.digest
                or authorization.authorization_digest
                != GroupCompensationCloseAuthorization.from_payload(
                    authorization.to_payload()
                ).digest
            ):
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_SOURCE_DIGEST_MISMATCH")
            return facts
        except GroupCompensationAuthorizationSourceError:
            raise
        except Exception:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_AUTHORIZATION_UNAVAILABLE") from None

    def resolve_close(
        self,
        registration: ActiveRegistrationContext,
        group_id: str,
        *,
        operation: str,
    ) -> GroupCompensationClosureFacts:
        """Rebuild the exact source-side closure projection from durable stores.

        This is a read-only query operation.  Risk independently rereads each
        CLOSE command and terminal receipt before treating any supplied facts
        as evidence for the coordinator's terminal event.
        """

        try:
            if operation != "orders.query_group_compensation_close":
                raise GroupCompensationAuthorizationSourceError(
                    "GROUP_COMPENSATION_CLOSURE_QUERY_REQUIRED"
                )
            accepted, origin, snapshot, history = self._resolve_group(
                registration, group_id, operation=operation
            )
            request = snapshot.request
            policy = request.compensation_budget_policy
            if (
                snapshot.state
                not in {
                    GroupOrderState.UNWINDING,
                    GroupOrderState.RECOVERY_REQUIRED,
                }
                or request.version != 3
                or len(request.legs) != 3
                or policy is None
                or snapshot.residual_vector is None
                or snapshot.budget_reservation is None
                or snapshot.budget_reservation.state.value != "HELD"
                or len(snapshot.reservations) != 1
                or snapshot.reservations[0].phase.value != "RELEASED"
            ):
                raise GroupCompensationAuthorizationSourceError(
                    "GROUP_COMPENSATION_GROUP_NOT_CLOSABLE"
                )
            reservation = snapshot.reservations[0]
            reservation_legs = {item.leg_id: item for item in reservation.legs}
            requested_leg_ids = tuple(plan.leg_id for plan in request.legs)
            if (
                len(reservation_legs) != len(reservation.legs)
                or set(reservation_legs) != set(requested_leg_ids)
            ):
                raise GroupCompensationAuthorizationSourceError(
                    "GROUP_COMPENSATION_RESERVATION_LEG_SET_MISMATCH"
                )
            for leg_id in requested_leg_ids:
                leg = reservation_legs[leg_id]
                if leg.claimed:
                    if not leg.resolved or leg.state not in {"SETTLED", "NO_FILL_TERMINAL"}:
                        raise GroupCompensationAuthorizationSourceError(
                            "GROUP_COMPENSATION_RESERVATION_OBLIGATION_PENDING"
                        )
                elif leg.state not in {"RELEASED", "EXPIRED"}:
                    raise GroupCompensationAuthorizationSourceError(
                        "GROUP_COMPENSATION_RESERVATION_OBLIGATION_PENDING"
                    )

            source = self._verified_source_set(
                registration, accepted, snapshot, include_zero_fill=True
            )
            parsed_actions = self._parse_compensation_events(history)
            if (
                not parsed_actions
                or any(item.phase is not GroupCompensationActionPhase.RESOLVED or item.receipt is None for item in parsed_actions)
                or len(parsed_actions) > _MAX_COMPENSATION_ACTIONS
            ):
                raise GroupCompensationAuthorizationSourceError(
                    "GROUP_COMPENSATION_ACTIONS_UNRESOLVED"
                )
            resolved_actions = self._resolved_action_refs(parsed_actions)
            if len(resolved_actions) != len(parsed_actions):
                raise GroupCompensationAuthorizationSourceError(
                    "GROUP_COMPENSATION_ACTION_RECEIPT_MISSING"
                )

            source_legs: list[GroupCompensationSourceLegFacts] = []
            for plan in request.legs:
                progress = next(item for item in snapshot.legs if item.leg_id == plan.leg_id)
                intent = progress.submitted_intent
                source_intent = plan.maximum_intent if intent is None else intent
                try:
                    metadata = self._proof_target.risk_target.registry.get(source_intent.instrument)
                    quote_currency = self._proof_target.provider_contract.quote_currency(source_intent.instrument)
                    if (
                        metadata is None
                        or metadata.quote_currency is None
                        or metadata.metadata_version != source_intent.metadata_version
                        or metadata.quantity_unit == ""
                        or metadata.contract_multiplier != plan.base_units_per_order_unit
                        or metadata.quote_currency != quote_currency
                    ):
                        raise ValueError
                except Exception:
                    raise GroupCompensationAuthorizationSourceError(
                        "GROUP_COMPENSATION_SOURCE_METADATA_MISMATCH"
                    ) from None

                if intent is None:
                    if (
                        progress.phase.value != "READY"
                        or progress.child_status is not None
                        or progress.claim_receipt is not None
                        or progress.resolution_receipt is not None
                    ):
                        raise GroupCompensationAuthorizationSourceError(
                            "GROUP_COMPENSATION_UNSUBMITTED_LEG_HAS_RESULT"
                        )
                    leg_facts = GroupCompensationSourceLegFacts(
                        leg_id=plan.leg_id,
                        source_request_fingerprint=plan.maximum_intent.fingerprint,
                        source_intent_id=None,
                        source_terminal_status=None,
                        source_claim_digest=None,
                        source_risk_resolution_digest=None,
                        source_event_digest=None,
                        side=source_intent.side,
                        requested_quantity=Decimal(0),
                        filled_quantity=Decimal(0),
                        instrument=source_intent.instrument,
                        metadata_version=source_intent.metadata_version,
                        source_price=None,
                        base_units_per_order_unit=plan.base_units_per_order_unit,
                        quantity_unit=metadata.quantity_unit,
                        quote_currency=quote_currency,
                    )
                else:
                    selected = source.get(plan.leg_id)
                    if selected is None:
                        raise GroupCompensationAuthorizationSourceError(
                            "GROUP_COMPENSATION_TERMINAL_PROOF_MISSING"
                        )
                    actual_plan, verified_intent, status, claim, resolution, source_event_digest = selected
                    if actual_plan != plan or verified_intent != intent:
                        raise GroupCompensationAuthorizationSourceError(
                            "GROUP_COMPENSATION_SOURCE_CHANGED"
                        )
                    if status.filled_quantity > 0 and (
                        source_intent.price is None or quote_currency != policy.currency
                    ):
                        raise GroupCompensationAuthorizationSourceError(
                            "GROUP_COMPENSATION_SOURCE_BUDGET_CURRENCY_MISMATCH"
                        )
                    leg_facts = GroupCompensationSourceLegFacts(
                        leg_id=plan.leg_id,
                        source_request_fingerprint=plan.maximum_intent.fingerprint,
                        source_intent_id=intent.intent_id,
                        source_terminal_status=status,
                        source_claim_digest=payload_sha256(claim.to_payload()),
                        source_risk_resolution_digest=payload_sha256(resolution.to_payload()),
                        source_event_digest=None if source_event_digest == "0" * 64 else source_event_digest,
                        side=intent.side,
                        requested_quantity=intent.quantity,
                        filled_quantity=status.filled_quantity,
                        instrument=intent.instrument,
                        metadata_version=intent.metadata_version,
                        source_price=intent.price,
                        base_units_per_order_unit=plan.base_units_per_order_unit,
                        quantity_unit=metadata.quantity_unit,
                        quote_currency=quote_currency,
                    )
                source_legs.append(leg_facts)

            if any(type(item.sequence) is not int or item.sequence <= 0 for item in history) or any(
                previous.sequence >= current.sequence
                for previous, current in zip(history, history[1:])
            ):
                raise GroupCompensationAuthorizationSourceError(
                    "GROUP_COMPENSATION_EVENT_SEQUENCE_INVALID"
                )
            event_chain_digest = payload_sha256(
                [
                    {
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "created_at_ns": event.created_at_ns,
                        "payload": dict(event.payload),
                    }
                    for event in history
                ]
            )
            chain_vector = tuple(
                (plan.leg_id, value)
                for plan, value in zip(request.legs, snapshot.residual_vector, strict=True)
            )
            exposure: dict[str, Fraction] = {
                leg.leg_id: (
                    (1 if leg.side is Side.BUY else -1)
                    * Fraction(leg.filled_quantity)
                    * Fraction(leg.base_units_per_order_unit)
                )
                for leg in source_legs
            }
            for action in resolved_actions:
                sign = 1 if action.closing_side is Side.BUY else -1
                exposure[action.source_leg_id] += (
                    sign
                    * Fraction(action.close_quantity)
                    * Fraction(action.source_base_units_per_order_unit)
                )
            signed_vector = tuple(
                (plan.leg_id, _fraction_to_decimal(exposure[plan.leg_id]))
                for plan in request.legs
            )
            consumed = sum(
                (Fraction(item.close_reference_notional) for item in resolved_actions),
                Fraction(0),
            )
            remaining = Fraction(policy.cap_amount) - consumed
            if remaining < 0:
                raise GroupCompensationAuthorizationSourceError(
                    "GROUP_COMPENSATION_BUDGET_EXCEEDED"
                )
            reservation_digest = payload_sha256(
                [item.to_payload() for item in snapshot.reservations]
            )
            budget_reservation_digest = payload_sha256(
                snapshot.budget_reservation.to_payload()
            )
            active_latches = sum(
                1
                for item in parsed_actions
                if item.receipt is None or item.receipt.get("dispatch_latch_active") is not False
            )
            return GroupCompensationClosureFacts(
                group_id=request.group_id,
                origin_run_id=origin.origin_run_id,
                group_fingerprint=request.fingerprint,
                execution_scope_key=self._execution_scope.key,
                account_ref=accepted.account_ref,
                strategy_id=request.strategy_id,
                group_state=snapshot.state.value,
                group_revision=snapshot.revision,
                source_legs=tuple(source_legs),
                chain_deficit_vector_base_units=chain_vector,
                signed_uncompensated_filled_exposure_vector_base_units=signed_vector,
                resolved_actions=resolved_actions,
                budget_policy_id=policy.policy_id,
                budget_policy_version=policy.policy_version,
                budget_policy_fingerprint=policy.fingerprint,
                budget_metric=policy.metric,
                budget_currency=policy.currency,
                budget_cap_amount=policy.cap_amount,
                budget_consumed_amount=_fraction_to_decimal(consumed),
                budget_remaining_amount=_fraction_to_decimal(remaining),
                pending_budget_obligation_amount=Decimal(0),
                pending_action_count=sum(
                    item.phase is not GroupCompensationActionPhase.RESOLVED
                    for item in parsed_actions
                ),
                unknown_order_count=sum(
                    leg.source_terminal_status is not None
                    and leg.source_terminal_status.state not in _TERMINAL
                    for leg in source_legs
                ),
                unresolved_group_obligation_count=sum(
                    item.phase.value != "RELEASED" for item in snapshot.reservations
                ),
                active_dispatch_latch_count=active_latches,
                account_reservation_phase=reservation.phase.value,
                account_reservation_sha256=reservation_digest,
                group_budget_reservation_state=snapshot.budget_reservation.state.value,
                group_budget_reservation_sha256=budget_reservation_digest,
                group_event_chain_sha256=event_chain_digest,
            )
        except GroupCompensationAuthorizationSourceError:
            raise
        except Exception:
            raise GroupCompensationAuthorizationSourceError(
                "GROUP_COMPENSATION_CLOSURE_UNAVAILABLE"
            ) from None

    def _validate_registration(
        self, registration: ActiveRegistrationContext, *, operation: str
    ) -> ActiveRegistrationContext:
        if operation not in _COMPENSATION_OPERATIONS or type(registration) is not ActiveRegistrationContext:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_REGISTRATION_INVALID")
        port = _QUERY_PORT if operation in _QUERY_OPERATIONS else _ENTRY_PORT
        operations = _QUERY_OPERATIONS if port == _QUERY_PORT else frozenset({"orders.submit_group_compensation_close"})
        writes = frozenset() if port == _QUERY_PORT else frozenset({"orders.submit_group_compensation_close"})
        context = registration.context
        if (
            registration.port != port
            or registration.operations != operations
            or registration.write_operations != writes
            or operation not in registration.operations
            or (operation in writes) != (operation in registration.write_operations)
            or context.identity.role is not WireRole.SERVICE
            or context.identity.principal_ref != context.principal_ref
            or context.principal_ref != self._coordinator_principal_ref
            or type(context.registration_id) is not str
            or _REF.fullmatch(context.registration_id) is None
            or registration.expires_at <= time.time()
            or registration.strategy_write_run_id is None
            or not any(
                stable_scope == context.scope
                for stable_scope in self._proof_source.targets
            )
            or context.scope.account_ref != self._execution_scope.account_ref
            or context.scope.strategy_ref != self._execution_scope.strategy_id
        ):
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_REGISTRATION_DENIED")
        return registration

    def _resolve_group(
        self,
        registration: ActiveRegistrationContext,
        group_id: str,
        *,
        operation: str,
    ) -> tuple[AcceptedGroupBinding, GroupOriginRunBinding, GroupOrderSnapshot, tuple[Any, ...]]:
        current = self._validate_registration(registration, operation=operation)
        context = current.context
        origin_run_id = current.strategy_write_run_id
        if type(group_id) is not str or _REF.fullmatch(group_id) is None:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_GROUP_ID_INVALID")
        try:
            accepted = self._accepted_group_store.get_binding(
                current,
                origin_run_id=origin_run_id,
                group_id=group_id,
                operation=operation,
            )
            origin = self._origin_run_binding_store.get(context.scope.portfolio_ref, group_id)
        except Exception:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_ACCEPTED_BINDING_UNAVAILABLE") from None
        if type(accepted) is not AcceptedGroupBinding or type(origin) is not GroupOriginRunBinding:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_ACCEPTED_BINDING_MISSING")
        if (
            accepted.group_id != group_id
            or accepted.principal_ref != self._coordinator_principal_ref
            or accepted.origin_run_id != origin_run_id
            or accepted.stable_scope != context.scope
            or origin.request.group_id != group_id
            or origin.origin_run_id != origin_run_id
            or origin.coordinator_principal_ref != self._coordinator_principal_ref
            or origin.request_fingerprint != origin.request.fingerprint
            or origin.request.coordinator_principal_ref != self._coordinator_principal_ref
            or origin.request.portfolio_ref != context.scope.portfolio_ref
            or origin.request.strategy_id != context.scope.strategy_ref
            or origin.request.account_refs != (context.scope.account_ref,)
        ):
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_ORIGIN_BINDING_MISMATCH")
        try:
            snapshot = self._group_store.get(origin.request.portfolio_ref, group_id)
            history = self._group_store.events(origin.request.portfolio_ref, group_id)
        except Exception:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_GROUP_JOURNAL_UNAVAILABLE") from None
        try:
            accepted_fingerprint = verify_accepted_group_projection(origin, snapshot, history)
        except Exception:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_GROUP_JOURNAL_MISMATCH") from None
        if (
            type(snapshot) is not GroupOrderSnapshot
            or snapshot.request.version != 3
            or snapshot.request.compensation_budget_policy is None
            or snapshot.request.unquoted_fingerprint != origin.request.unquoted_fingerprint
            or accepted_fingerprint != snapshot.request.fingerprint
            or len(history) != snapshot.revision + 1
            or len(history) > _MAX_GROUP_EVENTS
        ):
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_GROUP_JOURNAL_MISMATCH")
        self._assert_accepted_projection(accepted, snapshot)
        return accepted, origin, snapshot, history

    def _assert_accepted_projection(self, accepted: AcceptedGroupBinding, snapshot: GroupOrderSnapshot) -> None:
        request = snapshot.request
        requested_leg_ids = tuple(leg.leg_id for leg in request.legs)
        accepted_members = dict(accepted.members)
        if (
            accepted.reservation is None
            or accepted.group_id != request.group_id
            or accepted.account_ref != self._execution_scope.account_ref
            or accepted.portfolio_ref != request.portfolio_ref
            or accepted.strategy_id != request.strategy_id
            or accepted.origin_run_id == ""
            or len(accepted_members) != len(accepted.members)
            or set(accepted_members) != set(requested_leg_ids)
            or any(
                accepted_members[leg.leg_id] != leg.maximum_intent
                for leg in request.legs
            )
        ):
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_ACCEPTED_PROJECTION_MISMATCH")
        if len(snapshot.reservations) != 1:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_RISK_RESERVATION_MISMATCH")
        accepted_reservation = accepted.reservation
        reservation = snapshot.reservations[0]
        accepted_legs = {leg.leg_id: leg for leg in accepted_reservation.legs}
        current_legs = {leg.leg_id: leg for leg in reservation.legs}
        if (
            len(accepted_legs) != len(accepted_reservation.legs)
            or len(current_legs) != len(reservation.legs)
            or set(accepted_legs) != set(requested_leg_ids)
            or set(current_legs) != set(requested_leg_ids)
            or accepted_reservation.group_id != request.group_id
            or accepted_reservation.account_ref != accepted.account_ref
            or accepted_reservation.group_fingerprint is None
            or accepted_reservation.policy_fingerprint is None
            or reservation.group_id != accepted_reservation.group_id
            or reservation.account_ref != accepted_reservation.account_ref
            or reservation.scope_fingerprint != accepted_reservation.scope_fingerprint
            or reservation.group_fingerprint != accepted_reservation.group_fingerprint
            or reservation.policy_fingerprint != accepted_reservation.policy_fingerprint
            or reservation.deadline_ns != accepted_reservation.deadline_ns
        ):
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_RISK_RESERVATION_INVALID")
        # The accepted sidecar anchors immutable permit identity. Its per-leg
        # claim/settlement fields are the reservation-time projection and do
        # not change when a dispatched leg is later claimed or settled. The
        # group journal carries the latest account-owner snapshot. Align by
        # stable leg ID, then apply the same monotonic transition validator
        # used by the accepted-binding store.
        aligned_current = replace(
            reservation,
            legs=tuple(current_legs[leg_id] for leg_id, _intent in accepted.members),
        )
        try:
            validate_group_reservation_transition(accepted_reservation, aligned_current)
        except Exception:
            raise GroupCompensationAuthorizationSourceError(
                "GROUP_COMPENSATION_RISK_RESERVATION_TRANSITION_INVALID"
            ) from None

    @staticmethod
    def _selected_leg(snapshot: GroupOrderSnapshot, leg_id: str):
        matches = [plan for plan in snapshot.request.legs if plan.leg_id == leg_id]
        if len(matches) != 1:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_SOURCE_LEG_INVALID")
        return matches[0]

    def _verified_source_set(
        self,
        registration: ActiveRegistrationContext,
        accepted: AcceptedGroupBinding,
        snapshot: GroupOrderSnapshot,
        *,
        include_zero_fill: bool = False,
    ) -> dict[str, tuple[Any, OrderIntent, GroupChildOrderStatus, Any, GroupDispatchResolutionReceipt, str]]:
        result: dict[str, tuple[Any, OrderIntent, GroupChildOrderStatus, Any, GroupDispatchResolutionReceipt, str]] = {}
        context = registration.context
        progress_by_leg = {item.leg_id: item for item in snapshot.legs}
        for plan in snapshot.request.legs:
            progress = progress_by_leg[plan.leg_id]
            intent = progress.submitted_intent
            if intent is None:
                if progress.child_status is not None or progress.claim_receipt is not None or progress.resolution_receipt is not None:
                    raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_UNSUBMITTED_LEG_HAS_RESULT")
                continue
            status = progress.child_status
            if (
                progress.phase.value != "OBSERVED"
                or status is None
                or status.state not in _TERMINAL
                or status.intent_id != intent.intent_id
            ):
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_CHILD_NOT_TERMINAL")
            if status.filled_quantity > intent.quantity:
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_CHILD_FILL_INVALID")
            if status.filled_quantity == 0 and progress.claim_receipt is None:
                if progress.resolution_receipt is not None:
                    raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_NO_FILL_RESOLUTION_INVALID")
                if include_zero_fill:
                    raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_TERMINAL_PROOF_MISSING")
                continue
            if progress.claim_receipt is None or progress.resolution_receipt is None:
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_TERMINAL_PROOF_MISSING")
            claim = progress.claim_receipt
            resolution = progress.resolution_receipt
            if (
                claim.permit.group_id != snapshot.request.group_id
                or claim.permit.leg_id != plan.leg_id
                or claim.permit.account_ref != plan.account_ref
                or claim.permit.coordinator_principal_ref != self._coordinator_principal_ref
                or claim.permit.deadline_ns != snapshot.request.deadline_ns
                or resolution.claim != claim
            ):
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_TERMINAL_BINDING_MISMATCH")
            current_reservation = snapshot.reservations[0]
            risk_leg = next(
                (row for row in current_reservation.legs if row.leg_id == plan.leg_id),
                None,
            )
            if (
                risk_leg is None
                or risk_leg.permit_id != claim.permit.permit_id
                or risk_leg.intent_id != intent.intent_id
                or risk_leg.reserved_intent_hash != claim.permit.reserved_intent_hash
                or risk_leg.exposure_profile_fingerprint != claim.permit.exposure_profile_fingerprint
                or risk_leg.actual_intent_hash != claim.actual_child_intent_hash
                or not risk_leg.claimed
                or not risk_leg.resolved
            ):
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_RISK_LEG_MISMATCH")
            record = self._execution_store.get(intent.intent_id, scope=self._execution_scope)
            if (
                type(record) is not ExecutionRecord
                or record.dispatch_attempts != 1
                or record.group_binding is None
                or type(record.group_binding) is not GroupPermitBinding
                or record.payload_sha256 != intent.fingerprint
            ):
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_EXECUTION_ROW_MISSING")
            wire_scope = replace(context.scope, group_id=snapshot.request.group_id, leg_id=plan.leg_id)
            try:
                receipt = self._proof_source.read_receipt(
                    binding=record.group_binding,
                    intent=intent,
                    permit=claim.permit,
                    principal_ref=context.principal_ref,
                    identity=context.identity,
                    wire_scope=wire_scope,
                    service_context=context,
                    active_authority=self._authority_guard,
                )
                events = self._proof_source._events(self._proof_target, intent.intent_id)
            except GroupProofSourceError:
                raise
            except Exception:
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_PROOF_READ_UNAVAILABLE") from None
            if (
                type(receipt) is not GroupLegOrderReceipt
                or receipt.status != status
                or receipt.claim != claim
                or receipt.resolution != resolution
                or resolution.dispatch_latch_active
                or resolution.requested_quantity != claim.actual_quantity
                or resolution.filled_quantity != status.filled_quantity
                or resolution.final_state is not status.state
                or resolution.resolution_kind
                != ("TERMINAL_EXPOSURE_RESOLVED" if status.filled_quantity > 0 else "TERMINAL_NO_FILL_RESOLVED")
            ):
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_PROOF_PROJECTION_MISMATCH")
            source_events = [
                event for event in events
                if event.event_type in {"SOURCE_OBSERVED", "SOURCE_RECONCILED"}
            ]
            if len(source_events) not in {0, 1}:
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_SOURCE_EVENT_AMBIGUOUS")
            source_event_digest = (
                "0" * 64 if not source_events else source_events[0].record_sha256
            )
            if status.filled_quantity > 0 and (
                len(source_events) != 1
                or source_event_digest == "0" * 64
                or not _SHA256.fullmatch(source_event_digest)
            ):
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_SOURCE_EVENT_MISSING")
            if status.filled_quantity > 0 or include_zero_fill:
                result[plan.leg_id] = (plan, intent, status, claim, resolution, source_event_digest)
        return result

    def _facts_for_action(
        self,
        registration: ActiveRegistrationContext,
        accepted: AcceptedGroupBinding,
        origin: GroupOriginRunBinding,
        snapshot: GroupOrderSnapshot,
        prefix_history: tuple[Any, ...],
        authorization: GroupCompensationCloseAuthorization,
        *,
        phase: GroupCompensationActionPhase,
    ) -> GroupCompensationSourceFacts:
        source = self._verified_source_set(registration, accepted, snapshot)
        selected = source.get(authorization.source_leg_id)
        if selected is None:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_SOURCE_FILL_MISSING")
        plan, intent, status, claim, resolution, source_event_digest = selected
        refs = self._resolved_action_refs(self._parse_compensation_events(prefix_history))
        return self._facts_from_verified_source(
            registration=registration,
            accepted=accepted,
            origin=origin,
            snapshot=snapshot,
            source=source,
            selected_leg_id=authorization.source_leg_id,
            action_id=authorization.action_id,
            ordinal=authorization.ordinal,
            prior_refs=refs,
            phase=phase,
            expected_source=(intent, status, claim, resolution, source_event_digest),
        )

    def _facts_from_verified_source(
        self,
        *,
        registration: ActiveRegistrationContext,
        accepted: AcceptedGroupBinding,
        origin: GroupOriginRunBinding,
        snapshot: GroupOrderSnapshot,
        source: Mapping[str, tuple[Any, OrderIntent, GroupChildOrderStatus, Any, GroupDispatchResolutionReceipt, str]],
        selected_leg_id: str,
        action_id: str,
        ordinal: int,
        prior_refs: tuple[GroupCompensationResolvedActionRef, ...],
        phase: GroupCompensationActionPhase,
        expected_source: tuple[Any, ...] | None = None,
    ) -> GroupCompensationSourceFacts:
        selected = source.get(selected_leg_id)
        if selected is None:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_SOURCE_FILL_MISSING")
        plan, intent, status, claim, resolution, source_event_digest = selected
        if expected_source is not None and expected_source != (intent, status, claim, resolution, source_event_digest):
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_SOURCE_CHANGED")
        if intent.price is None or intent.quantity <= 0:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_LIMIT_SOURCE_REQUIRED")
        request = snapshot.request
        policy = request.compensation_budget_policy
        if policy is None:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_BUDGET_POLICY_MISSING")
        try:
            metadata = self._proof_target.risk_target.registry.get(intent.instrument)
            contract = self._proof_target.provider_contract
            quote_currency = contract.quote_currency(intent.instrument)
            if (
                metadata is None
                or metadata.quote_currency is None
                or metadata.metadata_version != intent.metadata_version
                or metadata.quantity_unit == ""
                or metadata.contract_multiplier != plan.base_units_per_order_unit
                or metadata.quote_currency != policy.currency
                or metadata.valuation_unit != policy.currency
                or quote_currency != policy.currency
            ):
                raise ValueError
        except Exception:
            raise GroupCompensationAuthorizationSourceError(
                "GROUP_COMPENSATION_SOURCE_METADATA_MISMATCH"
            ) from None
        if snapshot.residual_vector is None or len(snapshot.residual_vector) != 3:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_CHAIN_VECTOR_MISSING")
        expected_legs = tuple(item.leg_id for item in request.legs)
        if any(ref.source_leg_id not in expected_legs for ref in prior_refs):
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_PRIOR_SOURCE_MISMATCH")

        exposures: list[tuple[str, Decimal]] = []
        for group_plan in request.legs:
            progress = next(item for item in snapshot.legs if item.leg_id == group_plan.leg_id)
            filled = Decimal(0)
            if progress.child_status is not None:
                if progress.child_status.state not in _TERMINAL:
                    raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_CHILD_NOT_TERMINAL")
                filled = progress.child_status.filled_quantity
            position = Fraction(filled) * Fraction(group_plan.base_units_per_order_unit)
            if progress.submitted_intent is not None and progress.submitted_intent.side is Side.SELL:
                position = -position
            close_delta = sum(
                (
                    Fraction(ref.close_quantity)
                    * Fraction(ref.source_base_units_per_order_unit)
                    * (1 if ref.closing_side is Side.BUY else -1)
                    for ref in prior_refs
                    if ref.source_leg_id == group_plan.leg_id
                ),
                Fraction(0),
            )
            exposures.append((group_plan.leg_id, _fraction_to_decimal(position + close_delta)))
        selected_position = dict(exposures)[selected_leg_id]
        if selected_position == 0:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_SOURCE_EXPOSURE_ZERO")
        policy_fingerprint = group_compensation_budget_policy_fingerprint(
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            cap_amount=policy.cap_amount,
            currency=policy.currency,
            metric=policy.metric,
        )
        remaining = Fraction(policy.cap_amount) - sum(
            (Fraction(ref.close_reference_notional) for ref in prior_refs),
            Fraction(0),
        )
        return GroupCompensationSourceFacts(
            phase=phase,
            action_id=action_id,
            ordinal=ordinal,
            group_id=request.group_id,
            origin_run_id=origin.origin_run_id,
            group_fingerprint=request.fingerprint,
            execution_scope_key=self._execution_scope.key,
            account_ref=accepted.account_ref,
            strategy_id=request.strategy_id,
            source_leg_id=selected_leg_id,
            source_intent_id=intent.intent_id,
            source_request_fingerprint=request.fingerprint,
            source_terminal_status=status,
            source_requested_quantity=intent.quantity,
            source_claim_digest=payload_sha256(claim.to_payload()),
            source_risk_resolution_digest=payload_sha256(resolution.to_payload()),
            source_event_digest=source_event_digest,
            source_side=intent.side,
            instrument=intent.instrument,
            metadata_version=intent.metadata_version,
            source_price=intent.price,
            source_base_units_per_order_unit=plan.base_units_per_order_unit,
            source_quantity_unit=metadata.quantity_unit,
            source_quote_currency=quote_currency,
            source_uncompensated_position_base_units=selected_position,
            source_chain_deficit_vector_base_units=tuple(
                (leg_id, value) for leg_id, value in zip(expected_legs, snapshot.residual_vector, strict=True)
            ),
            source_filled_position_exposure_vector_base_units=tuple(exposures),
            prior_resolved_actions=prior_refs,
            compensation_budget_cap_amount=policy.cap_amount,
            action_remaining_budget_amount=_fraction_to_decimal(remaining),
            action_remaining_budget_currency=policy.currency,
            action_remaining_budget_metric=policy.metric,
            action_remaining_budget_policy_id=policy.policy_id,
            action_remaining_budget_policy_version=policy.policy_version,
            action_remaining_budget_policy_fingerprint=policy_fingerprint,
        )

    @classmethod
    def _parse_compensation_events(cls, history: tuple[Any, ...]) -> tuple[_CompensationEvent, ...]:
        actions: dict[str, dict[str, Any]] = {}
        statuses: dict[str, list[Mapping[str, Any]]] = {}
        output: list[_CompensationEvent] = []
        for index, event in enumerate(history):
            payload = event.payload
            if event.event_type == "GROUP_COMPENSATION_PLAN_RECORDED":
                if set(payload) != {"action_id", "authorization_digest", "authorization"}:
                    raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_PLAN_EVENT_INVALID")
                authorization = GroupCompensationCloseAuthorization.from_payload(payload["authorization"])
                action_id = authorization.action_id
                if (
                    action_id in actions
                    or payload["action_id"] != action_id
                    or payload["authorization_digest"] != authorization.digest
                    or authorization.authorization_digest != authorization.digest
                ):
                    raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_PLAN_EVENT_MISMATCH")
                actions[action_id] = {"authorization": authorization, "plan_index": index, "started": False, "resolved": None}
            elif event.event_type == "GROUP_COMPENSATION_SUBMIT_STARTED":
                if set(payload) != {"action_id", "authorization_digest", "intent_id", "client_order_id"}:
                    raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_START_EVENT_INVALID")
                action = actions.get(payload["action_id"])
                if (
                    action is None
                    or action["started"]
                    or payload["authorization_digest"] != action["authorization"].digest
                    or payload["intent_id"] != action["authorization"].intent_id
                    or payload["client_order_id"] != action["authorization"].client_order_id
                ):
                    raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_START_EVENT_MISMATCH")
                action["started"] = True
                action["phase"] = GroupCompensationActionPhase.SUBMIT_STARTED
            elif event.event_type == "GROUP_COMPENSATION_STATUS_OBSERVED":
                if set(payload) != {"action_id", "authorization_digest", "status"}:
                    raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_STATUS_EVENT_INVALID")
                action = actions.get(payload["action_id"])
                if (
                    action is None
                    or not action["started"]
                    or action["resolved"] is not None
                    or payload["authorization_digest"] != action["authorization"].digest
                ):
                    raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_STATUS_EVENT_MISMATCH")
                status = GroupChildOrderStatus.from_payload(payload["status"])
                authorization = action["authorization"]
                prior = statuses.setdefault(authorization.action_id, [])
                previous_status = (
                    None
                    if not prior
                    else GroupChildOrderStatus.from_payload(prior[-1])
                )
                if (
                    status.intent_id != authorization.intent_id
                    or status.filled_quantity > authorization.close_quantity
                    or (
                        previous_status is not None
                        and (
                            previous_status.state in _TERMINAL
                            or status.filled_quantity < previous_status.filled_quantity
                        )
                    )
                ):
                    raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_STATUS_REGRESSION")
                prior.append(MappingProxyType(dict(payload["status"])))
                action["phase"] = GroupCompensationActionPhase.STATUS_OBSERVED
            elif event.event_type == "GROUP_COMPENSATION_RESOLVED":
                if set(payload) != {"action_id", "authorization_digest", "authorization", "close_receipt"}:
                    raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_RESOLUTION_EVENT_INVALID")
                action = actions.get(payload["action_id"])
                if (
                    action is None
                    or not action["started"]
                    or action["resolved"] is not None
                    or payload["authorization_digest"] != action["authorization"].digest
                    or payload["authorization"] != action["authorization"].to_payload()
                    or not isinstance(payload["close_receipt"], Mapping)
                ):
                    raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_RESOLUTION_EVENT_MISMATCH")
                authorization = action["authorization"]
                # Resolve the projection for this event's exact action.  Do not
                # rely on the loop's previous ``action_id`` binding: when two
                # compensation actions are recorded, that would validate the
                # new receipt against the preceding action's status history.
                action_statuses = statuses.get(payload["action_id"], ())
                if not action_statuses:
                    raise GroupCompensationAuthorizationSourceError(
                        "GROUP_COMPENSATION_RESOLUTION_STATUS_MISSING"
                    )
                final_status = GroupChildOrderStatus.from_payload(action_statuses[-1])
                if (
                    final_status.state is not ExecutionState.FILLED
                    or final_status.filled_quantity != authorization.close_quantity
                ):
                    raise GroupCompensationAuthorizationSourceError(
                        "GROUP_COMPENSATION_RESOLUTION_STATUS_NOT_FILLED"
                    )
                cls._validate_close_receipt_payload(
                    payload["close_receipt"], authorization
                )
                action["resolved"] = MappingProxyType(dict(payload["close_receipt"]))
                action["phase"] = GroupCompensationActionPhase.RESOLVED
            if event.event_type in {
                "GROUP_COMPENSATION_PLAN_RECORDED",
                "GROUP_COMPENSATION_SUBMIT_STARTED",
                "GROUP_COMPENSATION_STATUS_OBSERVED",
                "GROUP_COMPENSATION_RESOLVED",
            }:
                # One unresolved action at a time prevents two reservations
                # from independently consuming the same durable cap remainder.
                action_id = payload["action_id"]
                action = actions[action_id]
                output.append(
                    _CompensationEvent(
                        authorization=action["authorization"],
                        plan_index=action["plan_index"],
                        phase=action.get("phase", GroupCompensationActionPhase.PLAN_RECORDED),
                        status_payloads=tuple(statuses.get(action_id, ())),
                        receipt=action["resolved"],
                    )
                )
        if len(actions) > _MAX_COMPENSATION_ACTIONS:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_ACTION_LIMIT")
        if sum(action["resolved"] is None for action in actions.values()) > 1:
            raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_MULTIPLE_PENDING_ACTIONS")
        # Collapse duplicate per-event projections to the latest phase while
        # retaining plan order and the exact plan event index.
        latest: dict[str, _CompensationEvent] = {}
        for item in output:
            latest[item.authorization.action_id] = item
        return tuple(sorted(latest.values(), key=lambda item: item.plan_index))

    @staticmethod
    def _validate_close_receipt_payload(
        receipt: Mapping[str, Any],
        authorization: GroupCompensationCloseAuthorization,
    ) -> None:
        expected_keys = {
            "status",
            "claim",
            "proof_sha256",
            "exposure_proof_sha256",
            "final_state",
            "requested_quantity",
            "filled_quantity",
            "trade_count",
            "order_revision",
            "journal_revision",
            "exposure_reservation_id",
            "exposure_reservation_sha256",
            "dispatch_latch_active",
            "has_other_active_freezes",
            "position_effect",
            "receipt_sha256",
        }
        claim = receipt.get("claim")
        body = dict(receipt)
        digest = body.pop("receipt_sha256", None)
        if (
            set(receipt) != expected_keys
            or type(digest) is not str
            or _SHA256.fullmatch(digest) is None
            or payload_sha256(body) != digest
            or receipt.get("status") != "TERMINAL_CLOSE_RESOLVED"
            or receipt.get("final_state") != ExecutionState.FILLED.value
            or receipt.get("position_effect") != "CLOSE"
            or receipt.get("dispatch_latch_active") is not False
            or receipt.get("has_other_active_freezes") is not False
            or receipt.get("requested_quantity") != format(authorization.close_quantity, "f")
            or receipt.get("filled_quantity") != format(authorization.close_quantity, "f")
            or type(receipt.get("trade_count")) is not int
            or receipt["trade_count"] <= 0
            or type(claim) is not dict
            or set(claim)
            != {
                "cause_id",
                "claim_digest",
                "claimed_at",
                "intent_hash",
                "intent_id",
                "permit_id",
                "scope_key",
            }
            or claim.get("intent_id") != authorization.risk_intent_id
            or any(
                type(claim.get(key)) is not str or not claim[key]
                for key in (
                    "cause_id",
                    "claim_digest",
                    "intent_hash",
                    "intent_id",
                    "permit_id",
                    "scope_key",
                )
            )
            or type(claim.get("claimed_at")) not in (int, float)
            or not math.isfinite(claim["claimed_at"])
            or claim["claimed_at"] <= 0
            or type(receipt.get("order_revision")) is not int
            or receipt["order_revision"] < 0
            or type(receipt.get("journal_revision")) is not int
            or receipt["journal_revision"] < 0
            or type(receipt.get("exposure_reservation_id")) is not str
            or not receipt["exposure_reservation_id"]
            or any(
                type(receipt.get(key)) is not str
                or _SHA256.fullmatch(receipt[key]) is None
                for key in (
                    "proof_sha256",
                    "exposure_proof_sha256",
                    "exposure_reservation_sha256",
                )
            )
        ):
            raise GroupCompensationAuthorizationSourceError(
                "GROUP_COMPENSATION_CLOSE_RECEIPT_INVALID"
            )

    @staticmethod
    def _resolved_action_refs(
        events: tuple[_CompensationEvent, ...],
    ) -> tuple[GroupCompensationResolvedActionRef, ...]:
        resolved: list[GroupCompensationResolvedActionRef] = []
        for item in events:
            if item.phase is not GroupCompensationActionPhase.RESOLVED or item.receipt is None:
                continue
            authorization = item.authorization
            receipt = item.receipt
            body = dict(receipt)
            digest = body.pop("receipt_sha256", None)
            if (
                type(digest) is not str
                or _SHA256.fullmatch(digest) is None
                or payload_sha256(body) != digest
                or receipt.get("status") != "TERMINAL_CLOSE_RESOLVED"
                or receipt.get("position_effect") != "CLOSE"
                or receipt.get("final_state") != ExecutionState.FILLED.value
                or receipt.get("dispatch_latch_active") is not False
                or receipt.get("has_other_active_freezes") is not False
                or receipt.get("claim", {}).get("intent_id") != authorization.risk_intent_id
                or receipt.get("requested_quantity") != format(authorization.close_quantity, "f")
                or receipt.get("filled_quantity") != format(authorization.close_quantity, "f")
            ):
                raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_PRIOR_RECEIPT_INVALID")
            resolved.append(
                GroupCompensationResolvedActionRef(
                    action_id=authorization.action_id,
                    source_leg_id=authorization.source_leg_id,
                    intent_id=authorization.intent_id,
                    risk_intent_id=authorization.risk_intent_id,
                    client_order_id=authorization.client_order_id,
                    permit_id=receipt["claim"]["permit_id"],
                    authorization_digest=authorization.digest,
                    receipt_sha256=digest,
                    closing_side=authorization.closing_side,
                    close_quantity=authorization.close_quantity,
                    close_quantity_unit=authorization.close_quantity_unit,
                    source_base_units_per_order_unit=authorization.source_base_units_per_order_unit,
                    instrument=authorization.instrument,
                    metadata_version=authorization.metadata_version,
                    close_limit_price=authorization.source_price,
                    close_reference_notional=authorization.close_reference_notional,
                    quote_currency=authorization.source_quote_currency,
                    budget_policy_fingerprint=authorization.action_remaining_budget_policy_fingerprint,
                )
            )
        return tuple(resolved)


def _fraction_to_decimal(value: Fraction) -> Decimal:
    """Convert a finite base-ten rational without Decimal context rounding."""

    if type(value) is not Fraction:
        raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_DECIMAL_INVALID")
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    scale = max(twos, fives)
    if denominator != 1 or scale > 512 or len(str(abs(value.numerator))) > 1024:
        raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_DECIMAL_NOT_EXACT")
    unscaled = value.numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    sign = "-" if unscaled < 0 else ""
    digits = str(abs(unscaled))
    if scale:
        digits = digits.rjust(scale + 1, "0")
        text = sign + digits[:-scale] + "." + digits[-scale:]
    else:
        text = sign + digits
    result = Decimal(text)
    if Fraction(result) != value:
        raise GroupCompensationAuthorizationSourceError("GROUP_COMPENSATION_DECIMAL_NOT_EXACT")
    return result


__all__ = [
    "GroupCompensationAuthorizationSourceError",
    "GroupCompensationAuthorizationSourceV1",
]
