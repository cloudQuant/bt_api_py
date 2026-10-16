"""Owner-thread coordinator for durable, ordered two-leg order groups.

The coordinator uses only the existing account-reservations and orders ports.
It owns sequencing and a local SQLite journal; it does not import a Risk
implementation, call a provider, or make progress from ``query``.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Protocol

from .contracts import TERMINAL_STATES, ExecutionState, OrderIntent
from .errors import ContractValidationError, IntentConflictError
from .group_order_contracts import (
    GroupAccountReservationSnapshot,
    GroupBudgetQuote,
    GroupChildOrderStatus,
    GroupDispatchResolutionReceipt,
    GroupLegClaimReceipt,
    GroupLegOrderReceipt,
    GroupLegPermitReference,
    GroupLegPhase,
    GroupLegProgress,
    GroupOrderIntent,
    GroupOrderSnapshot,
    GroupOrderState,
    GroupReservationPhase,
    PortfolioGroupBudgetPolicy,
)
from .group_store import SqliteOrderGroupStore

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DENIAL_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,95}$")


class AccountReservationsPort(Protocol):
    """Trusted account-bound facade over existing reservations operations."""

    @property
    def risk_scope_fingerprint(self) -> str: ...

    def reserve_group_vector(
        self,
        group_id: str,
        members: Sequence[tuple[str, OrderIntent]],
        *,
        deadline_ns: int,
    ) -> GroupAccountReservationSnapshot: ...

    def query_group_vector(self, group_id: str) -> GroupAccountReservationSnapshot: ...

    def release_group_unclaimed(
        self, group_id: str, reason: str
    ) -> GroupAccountReservationSnapshot: ...


class GroupOrderEntryPort(Protocol):
    """Trusted account-bound facade over existing group submit and order query."""

    def submit_group_leg(
        self, intent: OrderIntent, permit: GroupLegPermitReference
    ) -> GroupLegOrderReceipt: ...

    def query(self, intent_id: str) -> GroupLegOrderReceipt | None: ...

    def resolve_group_leg(
        self,
        intent: OrderIntent,
        permit: GroupLegPermitReference,
        claim: GroupLegClaimReceipt,
        status: GroupChildOrderStatus,
    ) -> GroupDispatchResolutionReceipt: ...


class GroupBudgetQuoteBuilder(Protocol):
    """Trusted local assessor; it must use current server-owned instrument facts."""

    def __call__(
        self,
        request: GroupOrderIntent,
        policy: PortfolioGroupBudgetPolicy,
        *,
        now_ns: int,
    ) -> GroupBudgetQuote: ...


class GroupReservationDeniedError(PermissionError):
    """A coded, known denial from the account reservation owner."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not _DENIAL_CODE.fullmatch(code):
            raise ContractValidationError("invalid reservation denial code")
        self.code = code
        super().__init__(code)


class GroupOrderCoordinator:
    """Advance or recover two-leg groups from the configured service thread.

    Account-bound adapters are trusted composition inputs. Risk mapping and
    construction of the final ``GroupPermitBinding`` stay inside each account
    owner; the coordinator persists and forwards only a permit reference.
    """

    def __init__(
        self,
        store: SqliteOrderGroupStore,
        *,
        reservations: Mapping[str, AccountReservationsPort],
        orders: Mapping[str, GroupOrderEntryPort],
        portfolio_budget_policies: Mapping[str, PortfolioGroupBudgetPolicy] | None = None,
        budget_quote_builder: GroupBudgetQuoteBuilder | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if type(store) is not SqliteOrderGroupStore or not callable(clock_ns):
            raise ContractValidationError("group coordinator requires its durable store and clock")
        keys = set(reservations)
        if not keys or set(orders) != keys:
            raise ContractValidationError("group account-owner maps must have the same targets")
        fingerprints: dict[str, str] = {}
        for account_ref, reservation_port in reservations.items():
            fingerprint = getattr(reservation_port, "risk_scope_fingerprint", None)
            if type(fingerprint) is not str or not _SHA256.fullmatch(fingerprint):
                raise ContractValidationError("account reservation owner must bind its Risk scope")
            fingerprints[account_ref] = fingerprint
            if (
                not callable(getattr(reservations[account_ref], "query_group_vector", None))
                or not callable(getattr(reservations[account_ref], "reserve_group_vector", None))
                or not callable(getattr(reservations[account_ref], "release_group_unclaimed", None))
            ):
                raise ContractValidationError("account reservations port is incomplete")
            if any(
                not callable(getattr(orders[account_ref], name, None))
                for name in ("submit_group_leg", "query", "resolve_group_leg")
            ):
                raise ContractValidationError("account group order port is incomplete")
        policies = {} if portfolio_budget_policies is None else dict(portfolio_budget_policies)
        if any(
            type(policy) is not PortfolioGroupBudgetPolicy or portfolio_ref != policy.portfolio_ref
            for portfolio_ref, policy in policies.items()
        ):
            raise ContractValidationError("portfolio budget policies must use exact portfolio keys")
        if policies and not callable(budget_quote_builder):
            raise ContractValidationError("trusted group budget quote builder is required")
        self._store = store
        self._scope_fingerprints = fingerprints
        self._reservations = dict(reservations)
        self._orders = dict(orders)
        self._budget_policies = policies
        self._budget_quote_builder = budget_quote_builder
        self._clock_ns = clock_ns
        self._lock = threading.RLock()

    def accept(self, request: GroupOrderIntent) -> GroupOrderSnapshot:
        """Durably accept an immutable request without remote calls or progress."""

        if type(request) is not GroupOrderIntent:
            raise ContractValidationError("normalized group order intent is required")
        if any(account not in self._reservations for account in request.account_refs):
            raise ContractValidationError("group references an unconfigured account target")
        existing = self._store.get(request.portfolio_ref, request.group_id)
        if existing is not None:
            if existing.request.unquoted_fingerprint != request.unquoted_fingerprint:
                raise IntentConflictError()
            return existing
        if request.version != 1 or request.budget_quote is not None:
            raise ContractValidationError("new group budget quotes are built by the trusted owner")
        policy = self._budget_policies.get(request.portfolio_ref)
        if policy is None or self._budget_quote_builder is None:
            raise ContractValidationError("trusted portfolio group budget is not configured")
        now = self._now()
        if request.deadline_ns <= now:
            raise ContractValidationError("group deadline has elapsed")
        quote = self._budget_quote_builder(request, policy, now_ns=now)
        if type(quote) is not GroupBudgetQuote or quote.policy_fingerprint != policy.fingerprint:
            raise ContractValidationError("trusted quote builder returned an unbound group quote")
        accepted_request = replace(request, version=2, budget_quote=quote)
        snapshot = self._store.create_or_get(
            accepted_request,
            budget_policy=policy,
            now_ns=now,
            clock_ns=self._clock_ns,
        )
        return self._store.bind_account_scopes(snapshot, self._scope_fingerprints, now_ns=now)

    def query(self, portfolio_ref: str, group_id: str) -> GroupOrderSnapshot | None:
        """Return the local durable view only; this method never advances work."""

        return self._store.get(portfolio_ref, group_id)

    def advance_group(self, portfolio_ref: str, group_id: str) -> GroupOrderSnapshot:
        """Advance one group from the service owner thread, or fail closed."""

        with self._lock:
            snapshot = self._required(portfolio_ref, group_id)
            if snapshot.state in {
                GroupOrderState.PAIRED,
                GroupOrderState.CLOSED,
                GroupOrderState.REJECTED_BEFORE_DISPATCH,
            }:
                return snapshot
            if snapshot.request.version == 1:
                # Migrated legacy groups remain queryable, but there is no
                # trusted budget estimate to authorize a new provider write.
                return self._mark_recovery(snapshot, "LEGACY_GROUP_BUDGET_UNAVAILABLE")
            snapshot = self._store.bind_account_scopes(
                snapshot, self._scope_fingerprints, now_ns=self._now()
            )
            snapshot = self._advance_reservations(snapshot)
            if snapshot.state is GroupOrderState.REJECTED_BEFORE_DISPATCH:
                return snapshot
            if (
                snapshot.state is GroupOrderState.RECOVERY_REQUIRED
                and self._first_leg(snapshot).submitted_intent is None
                and not all(
                    self._reservation_dispatchable(snapshot, account_ref)
                    for account_ref in snapshot.request.account_refs
                )
            ):
                # A recovered reservation query can reauthorize progression
                # only when every exact account receipt is currently
                # dispatchable. A claim, expired member, or uncertain vector
                # without a child intent remains recovery-only.
                return snapshot
            if not self._all_reservations_present(snapshot):
                return snapshot
            if snapshot.state is GroupOrderState.PREPARING:
                snapshot = self._store.update(
                    snapshot,
                    event_type="RESERVATION_CONFIRMED",
                    event_payload={"account_refs": list(snapshot.request.account_refs)},
                    state=GroupOrderState.FIRST_PENDING,
                    now_ns=self._now(),
                )
            elif (
                snapshot.state is GroupOrderState.RECOVERY_REQUIRED
                and self._first_leg(snapshot).submitted_intent is None
            ):
                snapshot = self._store.update(
                    snapshot,
                    event_type="RESERVATION_CONFIRMED",
                    event_payload={"account_refs": list(snapshot.request.account_refs)},
                    state=GroupOrderState.FIRST_PENDING,
                    error_code=None,
                    now_ns=self._now(),
                )

            # A single owner-thread tick may complete both legs when the
            # account order service returns terminal evidence synchronously.
            for _ in range(4):
                snapshot = self._required(portfolio_ref, group_id)
                if snapshot.state is GroupOrderState.FIRST_PENDING or (
                    snapshot.state is GroupOrderState.RECOVERY_REQUIRED
                    and self._first_leg(snapshot).submitted_intent is not None
                    and self._second_leg(snapshot).submitted_intent is None
                ):
                    snapshot, ready = self._advance_first_leg(snapshot)
                    if not ready:
                        return snapshot
                    snapshot = self._required(portfolio_ref, group_id)
                    if snapshot.state in {
                        GroupOrderState.CLOSED,
                        GroupOrderState.RECOVERY_REQUIRED,
                    }:
                        return snapshot
                    continue
                if snapshot.state is GroupOrderState.HEDGE_PENDING or (
                    snapshot.state is GroupOrderState.RECOVERY_REQUIRED
                    and self._second_leg(snapshot).submitted_intent is not None
                ):
                    snapshot, done = self._advance_hedge_leg(snapshot)
                    if done or snapshot.state is GroupOrderState.RECOVERY_REQUIRED:
                        return snapshot
                    continue
                return snapshot
            return self._mark_recovery(self._required(portfolio_ref, group_id), "ADVANCE_LIMIT")

    def recover_interrupted_groups(self) -> tuple[GroupOrderSnapshot, ...]:
        """Owner-thread startup recovery by original group, permit and order IDs."""

        return tuple(
            self.advance_group(item.request.portfolio_ref, item.request.group_id)
            for item in self._store.list_recoverable()
        )

    def _advance_reservations(self, snapshot: GroupOrderSnapshot) -> GroupOrderSnapshot:
        request = snapshot.request
        if all(
            self._reservation_dispatchable(snapshot, account_ref)
            for account_ref in request.account_refs
        ):
            return snapshot
        for account_ref in request.account_refs:
            snapshot = self._required(request.portfolio_ref, request.group_id)
            current = self._reservation(snapshot, account_ref)
            try:
                self._record_reservation_event(
                    snapshot,
                    account_ref,
                    "RESERVATION_QUERY_STARTED",
                    phase=current.phase,
                )
                receipt = self._reservations[account_ref].query_group_vector(request.group_id)
            except Exception as error:
                if _error_code(error) == "GROUP_NOT_FOUND":
                    receipt = None
                else:
                    snapshot = self._required(request.portfolio_ref, request.group_id)
                    snapshot = self._set_reservation_phase(
                        snapshot,
                        account_ref,
                        GroupReservationPhase.UNKNOWN,
                        event_type="RESERVATION_UNKNOWN",
                        error_code="RESERVATION_QUERY_UNKNOWN",
                    )
                    return self._mark_recovery(snapshot, "RESERVATION_QUERY_UNKNOWN")
            snapshot = self._required(request.portfolio_ref, request.group_id)
            if receipt is not None:
                try:
                    reservation = self._reservation_from_receipt(snapshot, account_ref, receipt)
                except (AttributeError, ContractValidationError, TypeError, ValueError):
                    snapshot = self._set_reservation_phase(
                        snapshot,
                        account_ref,
                        GroupReservationPhase.UNKNOWN,
                        event_type="RESERVATION_UNKNOWN",
                        error_code="RESERVATION_RECEIPT_MISMATCH",
                    )
                    return self._mark_recovery(snapshot, "RESERVATION_RECEIPT_MISMATCH")
                if current.phase is GroupReservationPhase.RELEASE_REQUESTED and all(
                    (leg.claimed and leg.resolved)
                    or (not leg.claimed and leg.state in {"RELEASED", "EXPIRED"})
                    for leg in reservation.legs
                ):
                    # The Risk owner may have committed an authorized release
                    # before its response was lost. Promote only from one
                    # complete durable receipt proving every claimed leg is
                    # resolved and every unclaimed leg is terminal.
                    reservation = replace(reservation, phase=GroupReservationPhase.RELEASED)
                    snapshot = self._replace_reservation(
                        snapshot,
                        reservation,
                        event_type="RESERVATION_RELEASED",
                        payload={
                            "account_ref": account_ref,
                            "group_fingerprint": reservation.group_fingerprint,
                            "recovered_after_lost_reply": True,
                        },
                    )
                    if not any(leg.submitted_intent is not None for leg in snapshot.legs):
                        return self._reject_before_dispatch(snapshot, "RESERVATION_NOT_ACTIVE")
                    continue
                snapshot = self._replace_reservation(
                    snapshot,
                    reservation,
                    event_type="RESERVATION_CONFIRMED",
                    payload={
                        "account_ref": account_ref,
                        "group_fingerprint": reservation.group_fingerprint,
                    },
                )
                if not self._reservation_dispatchable(
                    snapshot, account_ref
                ) and self._has_claim_or_expired(reservation):
                    if not any(leg.submitted_intent is not None for leg in snapshot.legs):
                        # A complete account snapshot that is already expired
                        # before any child intent was persisted is a terminal
                        # pre-dispatch condition. Reconcile every account and
                        # release only after the exact unclaimed vector is
                        # proven below; never strand portfolio capacity merely
                        # because the Risk read API intentionally does not
                        # perform TTL cleanup.
                        return self._reject_before_dispatch(snapshot, "RESERVATION_NOT_ACTIVE")
                    return self._mark_recovery(snapshot, "RESERVATION_NOT_ACTIVE")
                continue

            if current.phase in {
                GroupReservationPhase.ACTIVE,
                GroupReservationPhase.RELEASED,
                GroupReservationPhase.DENIED,
            }:
                return self._mark_recovery(snapshot, "RESERVATION_DISAPPEARED")
            if self._now() >= request.deadline_ns:
                return self._reject_before_dispatch(snapshot, "GROUP_DEADLINE_ELAPSED")
            snapshot = self._set_reservation_phase(
                snapshot,
                account_ref,
                GroupReservationPhase.REQUESTING,
                event_type="RESERVATION_QUERY_STARTED",
                error_code=None,
            )
            members = tuple(
                (leg.leg_id, leg.maximum_intent)
                for leg in sorted(
                    request.legs_for_account(account_ref), key=lambda item: item.leg_id
                )
            )
            try:
                receipt = self._reservations[account_ref].reserve_group_vector(
                    request.group_id,
                    members,
                    deadline_ns=request.deadline_ns,
                )
            except Exception as error:
                snapshot = self._required(request.portfolio_ref, request.group_id)
                # A reservation call can commit remotely before its reply is
                # lost. Only the neutral, typed denial contract proves that
                # the operation had no effect; an arbitrary ``.code`` field
                # is diagnostic data, not evidence of a deterministic reject.
                if isinstance(error, GroupReservationDeniedError):
                    code = error.code
                    snapshot = self._set_reservation_phase(
                        snapshot,
                        account_ref,
                        GroupReservationPhase.DENIED,
                        event_type="RESERVATION_DENIED",
                        error_code=code.lower(),
                    )
                    return self._reject_before_dispatch(snapshot, code)
                snapshot = self._set_reservation_phase(
                    snapshot,
                    account_ref,
                    GroupReservationPhase.UNKNOWN,
                    event_type="RESERVATION_UNKNOWN",
                    error_code="RESERVATION_OUTCOME_UNKNOWN",
                )
                return self._mark_recovery(snapshot, "RESERVATION_OUTCOME_UNKNOWN")
            snapshot = self._required(request.portfolio_ref, request.group_id)
            try:
                reservation = self._reservation_from_receipt(snapshot, account_ref, receipt)
            except (AttributeError, ContractValidationError, TypeError, ValueError):
                snapshot = self._set_reservation_phase(
                    snapshot,
                    account_ref,
                    GroupReservationPhase.UNKNOWN,
                    event_type="RESERVATION_UNKNOWN",
                    error_code="RESERVATION_RECEIPT_MISMATCH",
                )
                return self._mark_recovery(snapshot, "RESERVATION_RECEIPT_MISMATCH")
            snapshot = self._replace_reservation(
                snapshot,
                reservation,
                event_type="RESERVATION_CONFIRMED",
                payload={
                    "account_ref": account_ref,
                    "group_fingerprint": reservation.group_fingerprint,
                },
            )
            if not self._reservation_dispatchable(snapshot, account_ref):
                return self._reject_before_dispatch(snapshot, "RESERVATION_NOT_ACTIVE")
        return self._required(request.portfolio_ref, request.group_id)

    def _advance_first_leg(self, snapshot: GroupOrderSnapshot) -> tuple[GroupOrderSnapshot, bool]:
        first_plan, _ = snapshot.request.ordered_legs
        progress = self._leg(snapshot, first_plan.leg_id)
        if progress.submitted_intent is None:
            intent = first_plan.maximum_intent
            if self._now() >= snapshot.request.deadline_ns:
                return self._mark_recovery(snapshot, "GROUP_DEADLINE_ELAPSED"), False
            progress = replace(progress, phase=GroupLegPhase.REQUESTING, submitted_intent=intent)
            snapshot = self._replace_leg(
                snapshot,
                progress,
                event_type="FIRST_LEG_QUERY_STARTED",
                payload={"leg_id": first_plan.leg_id, "intent_id": intent.intent_id},
            )
            try:
                order_receipt = self._orders[first_plan.account_ref].query(intent.intent_id)
            except Exception:
                return self._uncertain_leg(snapshot, first_plan.leg_id, "FIRST_LEG_QUERY_UNKNOWN")
            if order_receipt is None:
                return self._submit_leg(snapshot, first_plan, intent, first=True)
            return self._record_child_status(snapshot, first_plan, order_receipt, first=True)

        if progress.phase is GroupLegPhase.UNKNOWN:
            try:
                order_receipt = self._orders[first_plan.account_ref].query(
                    progress.submitted_intent.intent_id
                )
            except Exception:
                return self._mark_recovery(snapshot, "FIRST_LEG_QUERY_UNKNOWN"), False
            if order_receipt is None:
                return self._mark_recovery(snapshot, "FIRST_LEG_STILL_UNKNOWN"), False
            return self._record_child_status(snapshot, first_plan, order_receipt, first=True)
        try:
            order_receipt = self._orders[first_plan.account_ref].query(
                progress.submitted_intent.intent_id
            )
        except Exception:
            return self._mark_recovery(snapshot, "FIRST_LEG_QUERY_UNKNOWN"), False
        if order_receipt is None:
            return self._mark_recovery(snapshot, "FIRST_LEG_RECORD_MISSING"), False
        return self._record_child_status(snapshot, first_plan, order_receipt, first=True)

    def _submit_leg(
        self,
        snapshot: GroupOrderSnapshot,
        plan: Any,
        intent: OrderIntent,
        *,
        first: bool,
    ) -> tuple[GroupOrderSnapshot, bool]:
        if self._now() >= snapshot.request.deadline_ns:
            return self._mark_recovery(snapshot, "GROUP_DEADLINE_ELAPSED"), False
        try:
            permit = self._permit_reference(snapshot, plan, intent)
        except (AttributeError, ContractValidationError, TypeError, ValueError):
            return self._mark_recovery(snapshot, "GROUP_PERMIT_REFERENCE_UNAVAILABLE"), False
        event = "FIRST_LEG_SUBMIT_STARTED" if first else "HEDGE_LEG_SUBMIT_STARTED"
        snapshot = self._record_leg_phase(
            snapshot,
            plan.leg_id,
            GroupLegPhase.REQUESTING,
            event_type=event,
            payload={"leg_id": plan.leg_id, "intent_id": intent.intent_id},
        )
        try:
            order_receipt = self._orders[plan.account_ref].submit_group_leg(intent, permit)
        except Exception:
            return self._uncertain_leg(snapshot, plan.leg_id, "ORDER_SUBMIT_OUTCOME_UNKNOWN")
        return self._record_child_status(snapshot, plan, order_receipt, first=first)

    def _record_child_status(
        self,
        snapshot: GroupOrderSnapshot,
        plan: Any,
        order_receipt: object,
        *,
        first: bool,
    ) -> tuple[GroupOrderSnapshot, bool]:
        if type(order_receipt) is not GroupLegOrderReceipt:
            return self._uncertain_leg(snapshot, plan.leg_id, "ORDER_RECEIPT_INVALID")
        status = order_receipt.status
        progress = self._leg(snapshot, plan.leg_id)
        intent = progress.submitted_intent
        if (
            intent is None
            or status.intent_id != intent.intent_id
            or status.filled_quantity > intent.quantity
        ):
            return self._uncertain_leg(snapshot, plan.leg_id, "ORDER_STATUS_MISMATCH")
        try:
            permit = (
                order_receipt.claim.permit
                if order_receipt.claim is not None
                else self._permit_reference(snapshot, plan, intent, require_claimable=False)
            )
            expected_permit = self._permit_reference(
                snapshot, plan, intent, require_claimable=False
            )
        except (AttributeError, ContractValidationError, TypeError, ValueError):
            return self._uncertain_leg(snapshot, plan.leg_id, "GROUP_PERMIT_REFERENCE_MISMATCH")
        if permit != expected_permit:
            return self._uncertain_leg(snapshot, plan.leg_id, "GROUP_PERMIT_REFERENCE_MISMATCH")
        claim = order_receipt.claim
        if claim is not None and (
            claim.permit != permit or claim.actual_quantity != intent.quantity
        ):
            return self._uncertain_leg(snapshot, plan.leg_id, "GROUP_CLAIM_RECEIPT_MISMATCH")
        if claim is None and (
            status.filled_quantity > 0
            or status.provider_order_id is not None
            or status.state
            in {
                ExecutionState.DISPATCHING,
                ExecutionState.ACKED,
                ExecutionState.PARTIALLY_FILLED,
                ExecutionState.FILLED,
            }
        ):
            return self._uncertain_leg(snapshot, plan.leg_id, "GROUP_CLAIM_RECEIPT_MISSING")
        if progress.claim_receipt is not None and claim != progress.claim_receipt:
            return self._uncertain_leg(snapshot, plan.leg_id, "GROUP_CLAIM_RECEIPT_CHANGED")
        if order_receipt.resolution is not None and (
            claim is None
            or status.state not in TERMINAL_STATES
            or order_receipt.resolution.claim != claim
        ):
            return self._uncertain_leg(snapshot, plan.leg_id, "GROUP_RESOLUTION_RECEIPT_MISMATCH")
        if status.state in TERMINAL_STATES and (
            status.trade_count is None
            or (status.filled_quantity > 0 and status.order_revision is None)
            or (status.filled_quantity > 0 and status.trade_count <= 0)
            or (status.filled_quantity == 0 and status.trade_count != 0)
        ):
            return self._uncertain_leg(snapshot, plan.leg_id, "TERMINAL_FILL_FACTS_INCOMPLETE")
        prior = progress.child_status
        if prior is not None and (
            status.filled_quantity < prior.filled_quantity
            or not _state_transition_allowed(prior.state, status.state)
        ):
            return self._uncertain_leg(snapshot, plan.leg_id, "ORDER_STATUS_REGRESSION")
        phase = (
            GroupLegPhase.UNKNOWN
            if status.state is ExecutionState.UNKNOWN
            else GroupLegPhase.OBSERVED
        )
        event = "FIRST_LEG_STATUS_OBSERVED" if first else "HEDGE_LEG_STATUS_OBSERVED"
        state = snapshot.state
        if status.state is ExecutionState.UNKNOWN:
            state = GroupOrderState.RECOVERY_REQUIRED
        elif snapshot.state is GroupOrderState.RECOVERY_REQUIRED:
            state = GroupOrderState.FIRST_PENDING if first else GroupOrderState.HEDGE_PENDING
        snapshot = self._replace_leg(
            snapshot,
            replace(
                progress,
                phase=phase,
                child_status=status,
                claim_receipt=claim or progress.claim_receipt,
                resolution_receipt=order_receipt.resolution or progress.resolution_receipt,
            ),
            event_type=event,
            payload={"leg_id": plan.leg_id, "state": status.state.value},
            state=state,
        )
        if status.state is ExecutionState.UNKNOWN:
            return self._mark_recovery(snapshot, "ORDER_OUTCOME_UNKNOWN"), False
        if status.state not in TERMINAL_STATES:
            return snapshot, False
        if claim is not None:
            resolution = order_receipt.resolution
            if resolution is None:
                try:
                    resolution = self._orders[plan.account_ref].resolve_group_leg(
                        intent, permit, claim, status
                    )
                except Exception:
                    return self._mark_recovery(snapshot, "GROUP_RISK_RESOLUTION_UNKNOWN"), False
            if type(resolution) is not GroupDispatchResolutionReceipt or resolution.claim != claim:
                return self._mark_recovery(
                    snapshot, "GROUP_RISK_RESOLUTION_RECEIPT_MISMATCH"
                ), False
            expected_resolution = (
                "TERMINAL_EXPOSURE_RESOLVED"
                if status.filled_quantity > 0
                else "TERMINAL_NO_FILL_RESOLVED"
            )
            if (
                resolution.resolution_kind != expected_resolution
                or resolution.final_state is not status.state
                or resolution.requested_quantity != claim.actual_quantity
                or resolution.filled_quantity != status.filled_quantity
                or resolution.trade_count != status.trade_count
                or (
                    status.order_revision is not None
                    and resolution.order_revision != status.order_revision
                )
                or resolution.dispatch_latch_active
            ):
                return self._mark_recovery(snapshot, "GROUP_RISK_RESOLUTION_FACTS_MISMATCH"), False
            progress = self._leg(snapshot, plan.leg_id)
            if (
                progress.resolution_receipt is not None
                and progress.resolution_receipt != resolution
            ):
                return self._mark_recovery(snapshot, "GROUP_RISK_RESOLUTION_CHANGED"), False
            snapshot = self._replace_leg(
                snapshot,
                replace(progress, resolution_receipt=resolution),
                event_type="GROUP_DISPATCH_RESOLUTION_RECORDED",
                payload={
                    "leg_id": plan.leg_id,
                    "claim_digest": claim.claim_digest,
                    "proof_sha256": resolution.proof_sha256,
                    "resolution_kind": resolution.resolution_kind,
                },
            )
            snapshot, resolved = self._refresh_risk_for_leg(snapshot, plan, claim)
            if not resolved:
                return self._mark_recovery(snapshot, "GROUP_RISK_RESOLUTION_UNCONFIRMED"), False
            if resolution.has_other_active_freezes and status.filled_quantity > 0:
                return self._mark_recovery(snapshot, "OTHER_RISK_FREEZE_AFTER_FIRST_LEG"), False
        if first:
            if status.filled_quantity == 0:
                return self._close_no_fill(snapshot, plan.leg_id), False
            if claim is None:
                return self._mark_recovery(snapshot, "FIRST_LEG_CLAIM_MISSING"), False
            return self._size_hedge(snapshot, plan, status.filled_quantity), True
        if status.filled_quantity > 0 and claim is None:
            return self._mark_recovery(snapshot, "HEDGE_LEG_CLAIM_MISSING"), False
        return self._finish_hedge(snapshot)

    def _size_hedge(
        self, snapshot: GroupOrderSnapshot, first_plan: Any, first_filled: Decimal
    ) -> GroupOrderSnapshot:
        _, second_plan = snapshot.request.ordered_legs
        base_filled = Fraction(first_filled) * Fraction(first_plan.base_units_per_order_unit)
        step = Fraction(second_plan.quantity_step)
        raw_hedge = base_filled / Fraction(second_plan.base_units_per_order_unit)
        steps = raw_hedge // step
        quantity = _fraction_to_decimal(steps * step)
        quantity = min(quantity, second_plan.maximum_intent.quantity)
        quantity = quantity - (quantity % second_plan.quantity_step)
        if quantity <= 0:
            return self._mark_recovery(snapshot, "HEDGE_QUANTITY_ROUNDS_TO_ZERO")
        intent = replace(second_plan.maximum_intent, quantity=quantity)
        progress = replace(
            self._leg(snapshot, second_plan.leg_id),
            phase=GroupLegPhase.READY,
            submitted_intent=intent,
            child_status=None,
        )
        residual = base_filled - Fraction(quantity) * Fraction(
            second_plan.base_units_per_order_unit
        )
        snapshot = self._replace_leg(
            snapshot,
            progress,
            event_type="HEDGE_LEG_SIZED",
            payload={
                "leg_id": second_plan.leg_id,
                "intent_id": intent.intent_id,
                "quantity": format(quantity, "f"),
                "first_filled_quantity": format(first_filled, "f"),
            },
            state=GroupOrderState.HEDGE_PENDING,
            residual_base_units=_fraction_to_decimal(residual),
        )
        return snapshot

    def _advance_hedge_leg(self, snapshot: GroupOrderSnapshot) -> tuple[GroupOrderSnapshot, bool]:
        first_plan, second_plan = snapshot.request.ordered_legs
        first_status = self._leg(snapshot, first_plan.leg_id).child_status
        progress = self._leg(snapshot, second_plan.leg_id)
        intent = progress.submitted_intent
        if (
            first_status is None
            or first_status.state not in TERMINAL_STATES
            or first_status.filled_quantity <= 0
        ):
            return self._mark_recovery(snapshot, "FIRST_LEG_NOT_TERMINAL_FILLED"), True
        if intent is None:
            return self._size_hedge(snapshot, first_plan, first_status.filled_quantity), False
        # The absolute deadline fences only a new submit. A previously
        # submitted leg must remain queryable after the deadline so an
        # UNKNOWN/response-loss outcome can be reconciled by its original ID.
        if progress.phase is GroupLegPhase.UNKNOWN:
            try:
                order_receipt = self._orders[second_plan.account_ref].query(intent.intent_id)
            except Exception:
                return self._mark_recovery(snapshot, "HEDGE_LEG_QUERY_UNKNOWN"), True
            if order_receipt is None:
                return self._mark_recovery(snapshot, "HEDGE_LEG_STILL_UNKNOWN"), True
            updated, done = self._record_child_status(
                snapshot, second_plan, order_receipt, first=False
            )
            return updated, done
        try:
            order_receipt = self._orders[second_plan.account_ref].query(intent.intent_id)
        except Exception:
            return self._mark_recovery(snapshot, "HEDGE_LEG_QUERY_UNKNOWN"), True
        if order_receipt is None:
            if progress.phase is not GroupLegPhase.READY:
                return self._mark_recovery(snapshot, "HEDGE_LEG_RECORD_MISSING"), True
            return self._submit_leg(snapshot, second_plan, intent, first=False)
        updated, done = self._record_child_status(snapshot, second_plan, order_receipt, first=False)
        return updated, done

    def _finish_hedge(self, snapshot: GroupOrderSnapshot) -> tuple[GroupOrderSnapshot, bool]:
        first_plan, second_plan = snapshot.request.ordered_legs
        first_status = self._leg(snapshot, first_plan.leg_id).child_status
        second_status = self._leg(snapshot, second_plan.leg_id).child_status
        if first_status is None or second_status is None:
            return self._mark_recovery(snapshot, "GROUP_CHILD_STATUS_MISSING"), True
        if first_status.state not in TERMINAL_STATES or second_status.state not in TERMINAL_STATES:
            return snapshot, False
        first_base = Fraction(first_status.filled_quantity) * Fraction(
            first_plan.base_units_per_order_unit
        )
        second_base = Fraction(second_status.filled_quantity) * Fraction(
            second_plan.base_units_per_order_unit
        )
        residual = first_base - second_base
        snapshot = self._store.update(
            snapshot,
            event_type="GROUP_PAIRED" if residual == 0 else "GROUP_RECOVERY_REQUIRED",
            event_payload={
                "first_leg_id": first_plan.leg_id,
                "second_leg_id": second_plan.leg_id,
                "first_filled_quantity": format(first_status.filled_quantity, "f"),
                "second_filled_quantity": format(second_status.filled_quantity, "f"),
                "residual_base_units": format(_fraction_to_decimal(residual), "f"),
            },
            state=GroupOrderState.PAIRED if residual == 0 else GroupOrderState.RECOVERY_REQUIRED,
            residual_base_units=_fraction_to_decimal(residual),
            error_code=None if residual == 0 else "hedge_residual_unresolved",
            now_ns=self._now(),
        )
        return snapshot, True

    def _close_no_fill(self, snapshot: GroupOrderSnapshot, first_leg_id: str) -> GroupOrderSnapshot:
        first_plan = snapshot.request.leg(first_leg_id)
        if not self._release_after_no_fill(snapshot, first_plan):
            snapshot = self._required(snapshot.request.portfolio_ref, snapshot.request.group_id)
            return self._mark_recovery(snapshot, "NO_FILL_RISK_RELEASE_UNRESOLVED")
        snapshot = self._required(snapshot.request.portfolio_ref, snapshot.request.group_id)
        return self._store.update(
            snapshot,
            event_type="GROUP_CLOSED_NO_EXPOSURE",
            event_payload={"first_leg_id": first_leg_id, "reason": "terminal_no_fill"},
            state=GroupOrderState.CLOSED,
            residual_base_units=Decimal("0"),
            error_code=None,
            now_ns=self._now(),
        )

    def _release_after_no_fill(self, snapshot: GroupOrderSnapshot, first_plan: Any) -> bool:
        for account_ref in snapshot.request.account_refs:
            snapshot = self._required(snapshot.request.portfolio_ref, snapshot.request.group_id)
            try:
                receipt = self._query_reservation(snapshot, account_ref)
                snapshot = self._required(snapshot.request.portfolio_ref, snapshot.request.group_id)
                reservation = self._reservation_from_receipt(snapshot, account_ref, receipt)
            except Exception:
                return False
            expected_claimed = (
                {first_plan.leg_id} if account_ref == first_plan.account_ref else set()
            )
            for leg in reservation.legs:
                if leg.claimed and (leg.leg_id not in expected_claimed or not leg.resolved):
                    return False
                if not leg.claimed and leg.state not in {"ACTIVE", "RELEASED", "EXPIRED"}:
                    return False
            if all(
                leg.state in {"RELEASED", "EXPIRED"} or (leg.claimed and leg.resolved)
                for leg in reservation.legs
            ):
                snapshot = self._replace_reservation(
                    snapshot,
                    reservation,
                    event_type="RESERVATION_RELEASED",
                    payload={"account_ref": account_ref},
                    phase=GroupReservationPhase.RELEASED,
                )
                continue
            self._record_reservation_event(
                snapshot,
                account_ref,
                "RESERVATION_RELEASE_STARTED",
                phase=GroupReservationPhase.RELEASE_REQUESTED,
            )
            snapshot = self._required(snapshot.request.portfolio_ref, snapshot.request.group_id)
            try:
                released = self._reservations[account_ref].release_group_unclaimed(
                    snapshot.request.group_id, "group_first_leg_no_fill"
                )
                snapshot = self._required(snapshot.request.portfolio_ref, snapshot.request.group_id)
                reservation = self._reservation_from_receipt(snapshot, account_ref, released)
            except Exception:
                return False
            if any(not leg.claimed and leg.state == "ACTIVE" for leg in reservation.legs):
                return False
            snapshot = self._replace_reservation(
                self._required(snapshot.request.portfolio_ref, snapshot.request.group_id),
                reservation,
                event_type="RESERVATION_RELEASED",
                payload={"account_ref": account_ref},
                phase=GroupReservationPhase.RELEASED,
            )
        return True

    def _reject_before_dispatch(
        self, snapshot: GroupOrderSnapshot, code: str
    ) -> GroupOrderSnapshot:
        if any(progress.submitted_intent is not None for progress in snapshot.legs):
            return self._mark_recovery(snapshot, "PRE_DISPATCH_REJECTION_AFTER_ORDER_INTENT")

        # Query all accounts before releasing any vector. A partial preflight
        # must never release account A and only then discover that account B
        # has a claim or an uncertain reservation outcome.
        preflight: list[tuple[str, GroupAccountReservationSnapshot | None]] = []
        for account_ref in snapshot.request.account_refs:
            reservation = self._reservation(snapshot, account_ref)
            try:
                receipt = self._query_reservation(snapshot, account_ref)
                snapshot = self._required(snapshot.request.portfolio_ref, snapshot.request.group_id)
                risk = self._reservation_from_receipt(snapshot, account_ref, receipt)
            except Exception as error:
                if _error_code(error) == "GROUP_NOT_FOUND" and (
                    reservation.phase
                    in {
                        GroupReservationPhase.NOT_STARTED,
                        GroupReservationPhase.REQUESTING,
                        GroupReservationPhase.UNKNOWN,
                        GroupReservationPhase.DENIED,
                    }
                    and reservation.group_fingerprint is None
                ):
                    # The account owner proves the original group ID has no
                    # reservation. This is safe only for a local phase that
                    # never recorded a durable Risk vector.
                    preflight.append((account_ref, None))
                    continue
                return self._mark_recovery(snapshot, "RESERVATION_RELEASE_QUERY_UNKNOWN")
            if any(leg.claimed or leg.resolved for leg in risk.legs):
                return self._mark_recovery(snapshot, "RESERVATION_CLAIM_BEFORE_DISPATCH")
            if any(leg.state not in {"ACTIVE", "RELEASED", "EXPIRED"} for leg in risk.legs):
                return self._mark_recovery(snapshot, "RESERVATION_RELEASE_UNSAFE")
            preflight.append((account_ref, risk))

        # No account effect occurs until every existing vector has been
        # checked for exact identity, membership, and absence of claims.
        for account_ref, risk in preflight:
            snapshot = self._required(snapshot.request.portfolio_ref, snapshot.request.group_id)
            local = self._reservation(snapshot, account_ref)
            if risk is None:
                if local.phase not in {
                    GroupReservationPhase.NOT_STARTED,
                    GroupReservationPhase.DENIED,
                }:
                    snapshot = self._set_reservation_phase(
                        snapshot,
                        account_ref,
                        GroupReservationPhase.DENIED,
                        event_type="RESERVATION_DENIED",
                        error_code="group_not_found",
                    )
                continue
            if all(leg.state in {"RELEASED", "EXPIRED"} for leg in risk.legs):
                snapshot = self._replace_reservation(
                    snapshot,
                    risk,
                    event_type="RESERVATION_RELEASED",
                    payload={"account_ref": account_ref, "already_terminal": True},
                    phase=GroupReservationPhase.RELEASED,
                )
                continue
            if any(leg.state != "ACTIVE" for leg in risk.legs):
                return self._mark_recovery(snapshot, "RESERVATION_RELEASE_UNSAFE")
            if any(leg.state == "ACTIVE" for leg in risk.legs):
                snapshot = self._record_reservation_event(
                    snapshot,
                    account_ref,
                    "RESERVATION_RELEASE_STARTED",
                    phase=GroupReservationPhase.RELEASE_REQUESTED,
                )
                snapshot = self._required(snapshot.request.portfolio_ref, snapshot.request.group_id)
                try:
                    released = self._reservations[account_ref].release_group_unclaimed(
                        snapshot.request.group_id, "group_rejected_before_dispatch"
                    )
                    snapshot = self._required(
                        snapshot.request.portfolio_ref, snapshot.request.group_id
                    )
                    risk = self._reservation_from_receipt(snapshot, account_ref, released)
                except Exception:
                    return self._mark_recovery(snapshot, "RESERVATION_RELEASE_UNKNOWN")
                if any(
                    leg.claimed or leg.resolved or leg.state not in {"RELEASED", "EXPIRED"}
                    for leg in risk.legs
                ):
                    return self._mark_recovery(snapshot, "RESERVATION_RELEASE_INCOMPLETE")
            snapshot = self._replace_reservation(
                self._required(snapshot.request.portfolio_ref, snapshot.request.group_id),
                risk,
                event_type="RESERVATION_RELEASED",
                payload={"account_ref": account_ref},
                phase=GroupReservationPhase.RELEASED,
            )
        snapshot = self._required(snapshot.request.portfolio_ref, snapshot.request.group_id)
        return self._store.update(
            snapshot,
            event_type="GROUP_REJECTED_BEFORE_DISPATCH",
            event_payload={"code": code},
            state=GroupOrderState.REJECTED_BEFORE_DISPATCH,
            error_code=code.lower(),
            now_ns=self._now(),
        )

    def _reservation_from_receipt(
        self, snapshot: GroupOrderSnapshot, account_ref: str, receipt: object
    ) -> GroupAccountReservationSnapshot:
        request = snapshot.request
        if type(receipt) is not GroupAccountReservationSnapshot:
            raise ContractValidationError("account owner must return a typed group reservation")
        if (
            receipt.account_ref != account_ref
            or receipt.group_id != request.group_id
            or receipt.scope_fingerprint != self._scope_fingerprints[account_ref]
            or receipt.deadline_ns != request.deadline_ns
            or receipt.group_fingerprint is None
            or receipt.policy_fingerprint is None
        ):
            raise ContractValidationError("Risk group receipt scope or identity mismatch")
        expected = {leg.leg_id: leg for leg in request.legs_for_account(account_ref)}
        if {item.leg_id for item in receipt.legs} != set(expected):
            raise ContractValidationError("Risk group receipt membership mismatch")
        for leg in receipt.legs:
            plan = expected[leg.leg_id]
            if leg.intent_id != plan.maximum_intent.intent_id:
                raise ContractValidationError("Risk group reservation intent identity mismatch")
            if leg.expires_at_ns > request.deadline_ns:
                raise ContractValidationError(
                    "Risk group reservation exceeds its absolute deadline"
                )
        current = self._reservation(snapshot, account_ref)
        if current.group_fingerprint is not None and (
            current.group_fingerprint != receipt.group_fingerprint
            or current.policy_fingerprint != receipt.policy_fingerprint
        ):
            raise ContractValidationError("Risk group fingerprint changed during recovery")
        terminal = {"RELEASED", "EXPIRED", "NO_FILL_TERMINAL"}
        if all(leg.state in terminal for leg in receipt.legs):
            phase = (
                GroupReservationPhase.RELEASED
                if any(leg.state == "RELEASED" for leg in receipt.legs)
                else GroupReservationPhase.DENIED
            )
        else:
            phase = GroupReservationPhase.ACTIVE
        return replace(
            receipt,
            phase=current.phase
            if current.phase is GroupReservationPhase.RELEASE_REQUESTED
            else phase,
        )

    def _permit_reference(
        self,
        snapshot: GroupOrderSnapshot,
        plan: Any,
        intent: OrderIntent,
        *,
        require_claimable: bool = True,
    ) -> GroupLegPermitReference:
        reservation = self._reservation(snapshot, plan.account_ref)
        risk_leg = next((item for item in reservation.legs if item.leg_id == plan.leg_id), None)
        if risk_leg is None or (
            require_claimable
            and (
                risk_leg.state != "ACTIVE"
                or risk_leg.claimed
                or risk_leg.resolved
                or risk_leg.expires_at_ns <= self._now()
            )
        ):
            raise ContractValidationError("group reservation is not claimable")
        if intent.quantity > plan.maximum_intent.quantity or intent.intent_id != risk_leg.intent_id:
            raise ContractValidationError("actual child exceeds its reserved profile")
        permit = GroupLegPermitReference(
            group_id=snapshot.request.group_id,
            leg_id=plan.leg_id,
            portfolio_ref=snapshot.request.portfolio_ref,
            strategy_id=snapshot.request.strategy_id,
            account_ref=plan.account_ref,
            coordinator_principal_ref=snapshot.request.coordinator_principal_ref,
            risk_scope_fingerprint=reservation.scope_fingerprint,
            permit_id=risk_leg.permit_id,
            group_fingerprint=_required_text(reservation.group_fingerprint),
            policy_fingerprint=_required_text(reservation.policy_fingerprint),
            reserved_intent_hash=risk_leg.reserved_intent_hash,
            exposure_profile_fingerprint=risk_leg.exposure_profile_fingerprint,
            order_intent_hash=intent.fingerprint,
            deadline_ns=snapshot.request.deadline_ns,
        )
        permit.validate_order(intent)
        return permit

    def _refresh_risk_for_leg(
        self, snapshot: GroupOrderSnapshot, plan: Any, claim: GroupLegClaimReceipt
    ) -> tuple[GroupOrderSnapshot, bool]:
        try:
            receipt = self._query_reservation(snapshot, plan.account_ref)
            snapshot = self._required(snapshot.request.portfolio_ref, snapshot.request.group_id)
            reservation = self._reservation_from_receipt(snapshot, plan.account_ref, receipt)
        except Exception:
            return snapshot, False
        risk_leg = next((item for item in reservation.legs if item.leg_id == plan.leg_id), None)
        if (
            risk_leg is None
            or not risk_leg.claimed
            or not risk_leg.resolved
            or risk_leg.actual_intent_hash != claim.actual_child_intent_hash
            or risk_leg.permit_id != claim.permit.permit_id
        ):
            snapshot = self._replace_reservation(
                self._required(snapshot.request.portfolio_ref, snapshot.request.group_id),
                reservation,
                event_type="RESERVATION_CONFIRMED",
                payload={
                    "account_ref": plan.account_ref,
                    "group_fingerprint": reservation.group_fingerprint,
                },
            )
            return snapshot, False
        snapshot = self._replace_reservation(
            self._required(snapshot.request.portfolio_ref, snapshot.request.group_id),
            reservation,
            event_type="RESERVATION_CONFIRMED",
            payload={
                "account_ref": plan.account_ref,
                "group_fingerprint": reservation.group_fingerprint,
            },
        )
        return snapshot, True

    def _query_reservation(self, snapshot: GroupOrderSnapshot, account_ref: str) -> object:
        self._record_reservation_event(
            snapshot,
            account_ref,
            "RESERVATION_QUERY_STARTED",
            phase=self._reservation(snapshot, account_ref).phase,
        )
        return self._reservations[account_ref].query_group_vector(snapshot.request.group_id)

    def _reservation_dispatchable(self, snapshot: GroupOrderSnapshot, account_ref: str) -> bool:
        reservation = self._reservation(snapshot, account_ref)
        plans = {plan.leg_id: plan for plan in snapshot.request.legs_for_account(account_ref)}
        if (
            reservation.phase is not GroupReservationPhase.ACTIVE
            or reservation.deadline_ns != snapshot.request.deadline_ns
            or {leg.leg_id for leg in reservation.legs} != set(plans)
        ):
            return False
        has_claim = any(leg.claimed for leg in reservation.legs)
        for risk_leg in reservation.legs:
            progress = self._leg(snapshot, risk_leg.leg_id)
            if not risk_leg.claimed:
                if (
                    risk_leg.state != "ACTIVE"
                    or risk_leg.resolved
                    or (not has_claim and risk_leg.expires_at_ns <= self._now())
                ):
                    return False
                continue
            if (
                risk_leg.state != "SETTLED"
                or risk_leg.actual_intent_hash is None
                or risk_leg.intent_id != plans[risk_leg.leg_id].maximum_intent.intent_id
                or progress.submitted_intent is None
            ):
                return False
            if progress.claim_receipt is not None and (
                progress.claim_receipt.permit.permit_id != risk_leg.permit_id
                or progress.claim_receipt.actual_child_intent_hash != risk_leg.actual_intent_hash
            ):
                return False
            if progress.resolution_receipt is not None and not risk_leg.resolved:
                return False
        return True

    def _has_claim_or_expired(self, reservation: GroupAccountReservationSnapshot) -> bool:
        return any(
            leg.claimed or leg.resolved or leg.state != "ACTIVE" or leg.expires_at_ns <= self._now()
            for leg in reservation.legs
        )

    def _all_reservations_present(self, snapshot: GroupOrderSnapshot) -> bool:
        first_submitted = self._first_leg(snapshot).submitted_intent is not None
        for item in snapshot.reservations:
            if (
                item.group_fingerprint is None
                or item.policy_fingerprint is None
                or item.deadline_ns != snapshot.request.deadline_ns
                or {leg.leg_id for leg in item.legs}
                != {leg.leg_id for leg in snapshot.request.legs_for_account(item.account_ref)}
            ):
                return False
            if item.phase is GroupReservationPhase.ACTIVE:
                continue
            if (
                item.phase is GroupReservationPhase.RELEASED
                and first_submitted
                and all(
                    leg.claimed
                    and leg.resolved
                    or not leg.claimed
                    and leg.state in {"RELEASED", "EXPIRED"}
                    for leg in item.legs
                )
            ):
                # Recovery after a terminal no-fill may observe the durable
                # group release before the coordinator records CLOSED. Query
                # the original first-leg ID and finish that same transition.
                continue
            return False
        return True

    def _replace_reservation(
        self,
        snapshot: GroupOrderSnapshot,
        reservation: GroupAccountReservationSnapshot,
        *,
        event_type: str,
        payload: Mapping[str, Any],
        phase: GroupReservationPhase | None = None,
    ) -> GroupOrderSnapshot:
        if phase is not None:
            reservation = replace(reservation, phase=phase)
        entries = tuple(
            reservation if item.account_ref == reservation.account_ref else item
            for item in snapshot.reservations
        )
        return self._store.update(
            snapshot,
            event_type=event_type,
            event_payload=payload,
            reservations=entries,
            now_ns=self._now(),
        )

    def _record_reservation_event(
        self,
        snapshot: GroupOrderSnapshot,
        account_ref: str,
        event_type: str,
        *,
        phase: GroupReservationPhase,
    ) -> GroupOrderSnapshot:
        current = self._reservation(snapshot, account_ref)
        return self._set_reservation_phase(
            snapshot,
            account_ref,
            phase,
            event_type=event_type,
            error_code=current.phase.value.lower() if current.phase is not phase else None,
        )

    def _set_reservation_phase(
        self,
        snapshot: GroupOrderSnapshot,
        account_ref: str,
        phase: GroupReservationPhase,
        *,
        event_type: str,
        error_code: str | None,
    ) -> GroupOrderSnapshot:
        old = self._reservation(snapshot, account_ref)
        new = replace(old, phase=phase)
        return self._replace_reservation(
            snapshot,
            new,
            event_type=event_type,
            payload={
                "account_ref": account_ref,
                "phase": phase.value,
                "prior_phase": old.phase.value,
                "code": error_code,
            },
        )

    def _replace_leg(
        self,
        snapshot: GroupOrderSnapshot,
        leg: GroupLegProgress,
        *,
        event_type: str,
        payload: Mapping[str, Any],
        state: GroupOrderState | None = None,
        residual_base_units: Decimal | None | object = ...,
    ) -> GroupOrderSnapshot:
        entries = tuple(leg if item.leg_id == leg.leg_id else item for item in snapshot.legs)
        return self._store.update(
            snapshot,
            event_type=event_type,
            event_payload=payload,
            state=state,
            legs=entries,
            residual_base_units=residual_base_units,
            error_code=None
            if state in {GroupOrderState.FIRST_PENDING, GroupOrderState.HEDGE_PENDING}
            else ...,
            now_ns=self._now(),
        )

    def _record_leg_phase(
        self,
        snapshot: GroupOrderSnapshot,
        leg_id: str,
        phase: GroupLegPhase,
        *,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> GroupOrderSnapshot:
        progress = replace(self._leg(snapshot, leg_id), phase=phase)
        return self._replace_leg(snapshot, progress, event_type=event_type, payload=payload)

    def _uncertain_leg(
        self, snapshot: GroupOrderSnapshot, leg_id: str, code: str
    ) -> tuple[GroupOrderSnapshot, bool]:
        progress = self._leg(snapshot, leg_id)
        snapshot = self._replace_leg(
            snapshot,
            replace(progress, phase=GroupLegPhase.UNKNOWN),
            event_type=(
                "FIRST_LEG_STATUS_OBSERVED"
                if leg_id == snapshot.request.first_leg_id
                else "HEDGE_LEG_STATUS_OBSERVED"
            ),
            payload={"leg_id": leg_id, "state": "UNKNOWN", "code": code},
            state=GroupOrderState.RECOVERY_REQUIRED,
        )
        return snapshot, False

    def _mark_recovery(self, snapshot: GroupOrderSnapshot, code: str) -> GroupOrderSnapshot:
        if snapshot.state is GroupOrderState.RECOVERY_REQUIRED:
            return (
                snapshot
                if snapshot.error_code == code.lower()
                else self._store.update(
                    snapshot,
                    event_type="GROUP_RECOVERY_REQUIRED",
                    event_payload={"code": code},
                    error_code=code.lower(),
                    now_ns=self._now(),
                )
            )
        return self._store.update(
            snapshot,
            event_type="GROUP_RECOVERY_REQUIRED",
            event_payload={"code": code},
            state=GroupOrderState.RECOVERY_REQUIRED,
            error_code=code.lower(),
            now_ns=self._now(),
        )

    def _required(self, portfolio_ref: str, group_id: str) -> GroupOrderSnapshot:
        snapshot = self._store.get(portfolio_ref, group_id)
        if snapshot is None:
            raise ContractValidationError("unknown group order")
        return snapshot

    def _reservation(
        self, snapshot: GroupOrderSnapshot, account_ref: str
    ) -> GroupAccountReservationSnapshot:
        return next(item for item in snapshot.reservations if item.account_ref == account_ref)

    def _leg(self, snapshot: GroupOrderSnapshot, leg_id: str) -> GroupLegProgress:
        return next(item for item in snapshot.legs if item.leg_id == leg_id)

    def _first_leg(self, snapshot: GroupOrderSnapshot) -> GroupLegProgress:
        return self._leg(snapshot, snapshot.request.first_leg_id)

    def _second_leg(self, snapshot: GroupOrderSnapshot) -> GroupLegProgress:
        leg_id = next(
            item.leg_id
            for item in snapshot.request.legs
            if item.leg_id != snapshot.request.first_leg_id
        )
        return self._leg(snapshot, leg_id)

    def _now(self) -> int:
        value = self._clock_ns()
        if type(value) is not int or value <= 0:
            raise ContractValidationError("invalid coordinator clock")
        return value


def _state_transition_allowed(source: ExecutionState, target: ExecutionState) -> bool:
    if source is target:
        return True
    if source is ExecutionState.UNKNOWN:
        return target in {
            ExecutionState.ACKED,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELLED,
            ExecutionState.REJECTED,
        }
    allowed = {
        ExecutionState.PENDING_ADMISSION: {
            ExecutionState.PENDING_DISPATCH,
            ExecutionState.BLOCKED,
            ExecutionState.REJECTED,
        },
        ExecutionState.PENDING_DISPATCH: {
            ExecutionState.DISPATCHING,
            ExecutionState.BLOCKED,
            ExecutionState.CANCELLED,
        },
        ExecutionState.DISPATCHING: {
            ExecutionState.UNKNOWN,
            ExecutionState.ACKED,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELLED,
            ExecutionState.REJECTED,
            ExecutionState.BLOCKED,
        },
        ExecutionState.ACKED: {
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELLED,
            ExecutionState.UNKNOWN,
        },
        ExecutionState.PARTIALLY_FILLED: {
            ExecutionState.FILLED,
            ExecutionState.CANCELLED,
            ExecutionState.UNKNOWN,
        },
    }
    return target in allowed.get(source, set())


def _error_code(error: BaseException) -> str | None:
    code = getattr(error, "code", None)
    return code if type(code) is str and _DENIAL_CODE.fullmatch(code) else None


def _digest(value: object, name: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise ContractValidationError(f"invalid {name}")
    return value


def _reference(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > 256:
        raise ContractValidationError(f"invalid {name}")
    return value


def _required_text(value: str | None) -> str:
    if type(value) is not str:
        raise ContractValidationError("required group reservation fingerprint is missing")
    return value


def _fraction_to_decimal(value: Fraction) -> Decimal:
    """Convert a finite base-10 rational exactly, without context rounding."""

    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ContractValidationError("group sizing produced a non-decimal fraction")
    scale = max(twos, fives)
    coefficient = value.numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    sign = 1 if coefficient < 0 else 0
    digits = tuple(int(char) for char in str(abs(coefficient)))
    return Decimal((sign, digits, -scale))


__all__ = [
    "AccountReservationsPort",
    "GroupBudgetQuoteBuilder",
    "GroupOrderCoordinator",
    "GroupOrderEntryPort",
    "GroupReservationDeniedError",
]
