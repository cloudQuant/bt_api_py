"""Bind authenticated Execution admission and zero-fill CTP evidence to Risk.

The bridge records one Risk reservation/claim before native dispatch. After
the Execution facade settles an exact ACK observation, it may resolve only a
reader-issued, read-back-verified zero-fill OPEN order into tracked exposure.
Positive fills and terminal outcomes remain outside this bridge slice.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from threading import current_thread
from time import time_ns
from typing import Any

from bt_api_execution import (
    ContractValidationError,
    ExecutionEvent,
    ExecutionRecord,
    ExecutionScope,
    ExecutionState,
    InvalidStateTransition,
    OrderIntent,
    PositionEffect,
    WriterLease,
    payload_sha256,
)
from bt_api_execution.basic_admission import ExecutionAdmissionContextV1
from bt_api_execution.basic_admission import RECOVERY_CLOSE_OPERATION
from bt_api_execution.ctp_single_worker_candidate import CtpManagedDispatchBinding
from bt_api_risk import (
    AccountScope,
    DispatchClaimBinding,
    DispatchEvidenceClass,
    DispatchExposureResolutionReceipt,
    DispatchExposureResolutionReceiptStatus,
    DispatchResolutionReceipt,
    DispatchResolutionReceiptStatus,
    DispatchTrackedOrderProof,
    IntentAction,
    RecoveryCloseSourceProof,
    RecoveryReductionAllocation,
    RiskIntent,
    RiskPermit,
)
from bt_api_risk.order_admission import (
    DurableRiskAdmissionAdapter,
    RiskAdmissionDeniedError,
)

from bt_api_py._contracts.models import OrderRequest
from bt_api_py._contracts.models import OrderType as BtOrderType
from bt_api_py._contracts.models import Side as BtSide
from bt_api_py.service_risk_order_journal import (
    RiskOrderJournal,
    RiskOrderJournalEvent,
    RiskOrderWriterFence,
    sha256_text,
)
from bt_api_py.service_risk_order_journal import (
    canonical_json as risk_canonical_json,
)


class CtpRiskDispatchError(InvalidStateTransition):
    """Finite fail-closed error from the CTP/Risk command handoff."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not code or not code.replace("_", "").isalnum():
            code = "CTP_RISK_DISPATCH_FAILED"
        self.code = code
        super().__init__(code)


@dataclass(slots=True)
class _PermitState:
    intent: OrderIntent
    risk_intent: RiskIntent
    permit: RiskPermit
    principal_ref: str
    risk_intent_id: str
    state: str
    claim: DispatchClaimBinding | None = None
    claim_event: RiskOrderJournalEvent | None = None
    claim_event_updated_at_ns: int | None = None
    dispatch_event: RiskOrderJournalEvent | None = None
    zero_fill_reader: Any | None = None
    zero_fill_evidence: Any | None = None
    tracked_proof: DispatchTrackedOrderProof | None = None
    tracked_receipt: DispatchResolutionReceipt | None = None
    tracked_event: RiskOrderJournalEvent | None = None
    tracked_source_event: ExecutionEvent | None = None
    tracked_update_review_source_event: ExecutionEvent | None = None
    recovery_allocation: RecoveryReductionAllocation | None = None
    recovery_source_proof: RecoveryCloseSourceProof | None = None
    recovery_target_intent_id: str | None = None
    recovery_review_source_event: ExecutionEvent | None = None


class CtpRiskDispatchBridge:
    """Join trusted Execution admission, one Risk claim, and one CTP command.

    ``adapter`` and ``journal`` are supplied by trusted service composition.
    The bridge is bound to the exact live CTP owner after its submit port has
    bound BasicAdmission. All methods that touch the journal or owner are
    confined to that same thread. It never mints a Risk scope, maps a caller
    claim, or treats a native queue result as provider evidence. Its only Risk
    resolution is a callback/query verified zero-fill OPEN order.
    """

    def __init__(
        self,
        adapter: DurableRiskAdmissionAdapter,
        journal: RiskOrderJournal,
        *,
        account_scope: AccountScope,
        exchange_name: str = "SHFE",
    ) -> None:
        if type(adapter) is not DurableRiskAdmissionAdapter:
            raise ContractValidationError("exact durable Risk admission adapter is required")
        if type(journal) is not RiskOrderJournal or type(account_scope) is not AccountScope:
            raise ContractValidationError("exact Risk journal and account scope are required")
        if (
            type(exchange_name) is not str
            or not exchange_name
            or exchange_name != exchange_name.strip()
            or not exchange_name.isascii()
            or len(exchange_name) > 24
        ):
            raise ContractValidationError("invalid configured CTP exchange name")

        self._adapter = adapter
        self._gate = adapter._gate
        self._journal = journal
        self._account_scope = account_scope
        self._exchange_name = exchange_name
        self._thread = current_thread()
        self._owner: Any | None = None
        self._closed = False
        self._close_uncertain = False
        self._permits: dict[str, _PermitState] = {}
        self._by_command: dict[str, _PermitState] = {}
        self._recovery_reservation_attempted_for: set[str] = set()
        if getattr(self._gate, "_execution_journal_authority", None) is not journal:
            raise ContractValidationError(
                "Risk gate and CTP bridge must share the exact execution journal authority"
            )
        self._fence_cm = journal.writer_fence(account_scope)
        try:
            self._fence = self._fence_cm.__enter__()
        except Exception:
            self._fence_cm = None
            raise
        if type(self._fence) is not RiskOrderWriterFence or self._fence.scope != account_scope:
            try:
                self._fence_cm.__exit__(None, None, None)
            finally:
                self._fence_cm = None
            raise ContractValidationError("Risk writer fence does not match configured account")

    @property
    def account_scope(self) -> AccountScope:
        return self._account_scope

    @property
    def _journal_fence(self) -> RiskOrderWriterFence:
        return self._fence

    def is_bound_to(self, gate: object) -> bool:
        """Return true only for this adapter's exact durable Risk gate."""

        return self._adapter.is_bound_to(gate)

    def bind_execution_owner(self, owner: object) -> None:
        """Bind the exact CTP execution owner once, on its confined thread."""

        self._assert_open_thread()
        from bt_api_py.ctp_execution_owner import CtpExecutionOwner

        if type(owner) is not CtpExecutionOwner:
            raise ContractValidationError("exact CTP execution owner is required")
        if self._owner is not None:
            raise InvalidStateTransition("CTP Risk bridge owner binding is one-shot")
        if (
            owner._store is None
            or type(owner._scope) is not ExecutionScope
            or owner._scope.provider != self._account_scope.provider
            or owner._scope.environment != self._account_scope.environment
            or owner._scope.account_ref != self._adapter._expected_wire_account_ref(self._account_scope)
            or type(owner._writer_lease) is not WriterLease
            or owner._store.assert_writer_lease(owner._scope, owner._writer_lease) is not None
        ):
            raise ContractValidationError("CTP owner does not match the exact Risk account")
        self._owner = owner

    def read_recovery_parent(
        self,
        *,
        target_intent_id: str,
        principal_ref: str,
        wire_scope: object,
        writer_lease: WriterLease,
    ) -> tuple[OrderIntent, ExecutionRecord, RiskPermit, RiskIntent]:
        """Read one positive-fill, unknown-fee OPEN still held by Risk."""

        self._assert_open_thread()
        owner = self._require_owner()
        from bt_api_base.service_protocol import WireScope

        if (
            type(target_intent_id) is not str
            or not target_intent_id
            or target_intent_id != target_intent_id.strip()
            or type(principal_ref) is not str
            or not principal_ref
            or (
                type(wire_scope) is not WireScope
                and not _valid_wire_scope_payload(wire_scope)
            )
            or type(writer_lease) is not WriterLease
            or writer_lease is not owner._writer_lease
        ):
            raise CtpRiskDispatchError("RECOVERY_PARENT_BINDING_INVALID")
        owner._assert_worker_thread()
        owner._assert_active_owner()
        owner._store.assert_writer_lease(owner._scope, writer_lease)
        candidates = tuple(
            item
            for item in self._permits.values()
            if item.intent.intent_id == target_intent_id
            and item.principal_ref == principal_ref
        )
        if len(candidates) != 1:
            raise CtpRiskDispatchError("RECOVERY_PARENT_NOT_TRACKED")
        state = candidates[0]
        record = owner._store.get(target_intent_id, scope=owner._scope)
        intent = owner._store.get_intent(target_intent_id, scope=owner._scope)
        if (
            state.state != "ACKED_TRACKED"
            or type(state.permit) is not RiskPermit
            or type(state.risk_intent) is not RiskIntent
            or type(record) is not ExecutionRecord
            or type(intent) is not OrderIntent
            or not _same_order_intent(intent, state.intent)
            or record.intent_id != target_intent_id
            or record.scope_key != owner._scope.key
            or record.payload_sha256 != intent.fingerprint
            or record.permit_reference != state.permit.permit_id
            or record.review_required is not True
            or record.filled_quantity <= 0
            or record.cumulative_commission is not None
            or record.average_price is None
            or not record.provider_order_id
            or intent.position_effect is not PositionEffect.OPEN
            or intent.scope.trading_day != owner._scope.trading_day
        ):
            raise CtpRiskDispatchError("RECOVERY_PARENT_NOT_TRACKED")
        expected_wire_scope = _wire_scope_payload_from_value(wire_scope)
        command = self._journal.get_command(self._account_scope, principal_ref, target_intent_id)
        if (
            command is None
            or command.state != "ACKED_TRACKED"
            or command.permit_id != state.permit.permit_id
            or command.risk_intent_id != state.risk_intent_id
            or dict(command.wire_scope) != expected_wire_scope
            or command.canonical_payload.get("execution_intent_sha256") != intent.fingerprint
            or command.canonical_payload.get("risk_intent_sha256") != state.risk_intent.fingerprint
        ):
            raise CtpRiskDispatchError("RECOVERY_PARENT_JOURNAL_MISMATCH")
        try:
            review_event = self.read_tracked_update_review(
                record, writer_lease=writer_lease
            )
            # ACKED_TRACKED is written only after this bridge has settled the
            # reservation and recorded Risk's resolved receipt. That
            # resolution clears the dispatch freeze, so calling
            # ensure_settled() here would demand a freeze that must stay
            # cleared. The fresh resolution receipt below is the read-only
            # Risk proof that the original reservation remains settled; keep
            # returning the exact permit already bound to this tracked state.
            receipt = self._gate.dispatch_resolution_receipt(state.permit.permit_id)
        except Exception:
            raise CtpRiskDispatchError("RECOVERY_PARENT_READBACK_FAILED") from None
        if (
            type(review_event) is not ExecutionEvent
            or type(state.permit) is not RiskPermit
            or type(receipt) is not DispatchResolutionReceipt
            or not _same_dispatch_resolution_receipt(
                receipt, state.tracked_receipt
            )
            or receipt.status is not DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
            or receipt.dispatch_latch_active is not False
            or receipt.claim.permit_id != state.permit.permit_id
            or receipt.proof != state.tracked_proof
            or state.tracked_proof is None
            or receipt.proof.permit_id != state.permit.permit_id
            or state.tracked_proof.provider_order_id != record.provider_order_id
        ):
            raise CtpRiskDispatchError("RECOVERY_PARENT_READBACK_MISMATCH")
        return intent, record, state.permit, state.risk_intent

    def recovery_ready(
        self,
        *,
        target_intent_id: str,
        review_event: ExecutionEvent,
        record: ExecutionRecord,
        writer_lease: WriterLease,
    ) -> bool:
        """Gate a fresh recovery window on one current positive-fill review."""

        return self._recovery_route_ready(
            target_intent_id=target_intent_id,
            review_event=review_event,
            record=record,
            writer_lease=writer_lease,
            allow_prior_attempt=False,
        )

    def recovery_route_available(
        self,
        *,
        target_intent_id: str,
        review_event: ExecutionEvent,
        record: ExecutionRecord,
        writer_lease: WriterLease,
    ) -> bool:
        """Allow route entry for exact durable replay under a current review.

        This is only a Registry entry-readiness projection. It does not grant
        a new reservation or dispatch; ``prepare_recovery_close`` returns an
        exact existing result first and rejects every new child after an
        earlier reservation attempt.
        """

        return self._recovery_route_ready(
            target_intent_id=target_intent_id,
            review_event=review_event,
            record=record,
            writer_lease=writer_lease,
            allow_prior_attempt=True,
        )

    def _recovery_route_ready(
        self,
        *,
        target_intent_id: str,
        review_event: ExecutionEvent,
        record: ExecutionRecord,
        writer_lease: WriterLease,
        allow_prior_attempt: bool,
    ) -> bool:
        """Check the review latch, optionally allowing handler-only replay."""

        self._assert_open_thread()
        owner = self._require_owner()
        if (
            type(target_intent_id) is not str
            or type(review_event) is not ExecutionEvent
            or type(record) is not ExecutionRecord
            or type(writer_lease) is not WriterLease
            or writer_lease is not owner._writer_lease
            or record.intent_id != target_intent_id
            or record.scope_key != owner._scope.key
        ):
            return False
        if not allow_prior_attempt and self.recovery_reservation_attempted(
            target_intent_id
        ):
            return False
        try:
            parents = tuple(
                state
                for state in self._permits.values()
                if state.intent.intent_id == target_intent_id
            )
            if len(parents) != 1:
                return False
            parent_state = parents[0]
            command = self._journal.get_command(
                self._account_scope, parent_state.principal_ref, target_intent_id
            )
            if command is None or not _valid_wire_scope_payload(command.wire_scope):
                return False
            parent = self.read_recovery_parent(
                target_intent_id=target_intent_id,
                principal_ref=parent_state.principal_ref,
                wire_scope=command.wire_scope,
                writer_lease=writer_lease,
            )
            parent_record = parent[1]
            current_review = self.read_tracked_update_review(
                parent_record, writer_lease=writer_lease
            )
            return (
                parent_record == record
                and current_review == review_event
                and record.cumulative_commission is None
                and record.filled_quantity > 0
            )
        except Exception:
            return False

    def recovery_reservation_attempted(self, target_intent_id: str) -> bool:
        """Return whether a target has crossed its one-use Risk boundary."""

        self._assert_open_thread()
        if type(target_intent_id) is not str or not target_intent_id:
            return True
        return target_intent_id in self._recovery_reservation_attempted_for or any(
            state.recovery_target_intent_id == target_intent_id
            and state.recovery_allocation is not None
            for state in self._permits.values()
        )

    def read_recovery_allocation(
        self,
        *,
        target_intent_id: str,
        child_intent: OrderIntent,
        writer_lease: WriterLease,
    ) -> tuple[RecoveryReductionAllocation, RiskIntent]:
        """Read back one live Risk allocation for its prepared close child."""

        self._assert_open_thread()
        owner = self._require_owner()
        if (
            type(child_intent) is not OrderIntent
            or child_intent.scope != owner._scope
            or type(writer_lease) is not WriterLease
            or writer_lease is not owner._writer_lease
        ):
            raise CtpRiskDispatchError("RECOVERY_CHILD_BINDING_INVALID")
        matches = tuple(
            state
            for state in self._permits.values()
            if state.intent is child_intent
            and state.recovery_target_intent_id == target_intent_id
        )
        if len(matches) != 1:
            raise CtpRiskDispatchError("RECOVERY_ALLOCATION_UNAVAILABLE")
        state = matches[0]
        if (
            type(state.recovery_allocation) is not RecoveryReductionAllocation
            or type(state.recovery_source_proof) is not RecoveryCloseSourceProof
            or state.risk_intent.action is not IntentAction.REDUCE
            or state.risk_intent.notional != Decimal("0")
            or state.recovery_allocation.recovery_intent_hash != state.risk_intent.fingerprint
            or state.recovery_allocation.parent_intent_id != target_intent_id
            or state.recovery_allocation.source_proof_sha256
            != state.recovery_source_proof.fingerprint
        ):
            raise CtpRiskDispatchError("RECOVERY_ALLOCATION_UNAVAILABLE")
        record = owner._store.get(child_intent.intent_id, scope=owner._scope)
        exact_reserved_pending = (
            type(record) is ExecutionRecord
            and state.state == "RESERVED"
            and record.state is ExecutionState.PENDING_DISPATCH
            and record.dispatch_attempts == 0
            and record.review_required is False
            and record.provider_order_id is None
            and record.filled_quantity == Decimal("0")
            and record.average_price is None
            and record.cumulative_commission is None
            and record.unknown_reason is None
        )
        exact_claiming_dispatch = (
            type(record) is ExecutionRecord
            and state.state == "CLAIMING"
            and record.state is ExecutionState.DISPATCHING
            and record.dispatch_attempts == 1
            and record.review_required is False
            and record.provider_order_id is None
            and record.filled_quantity == Decimal("0")
            and record.average_price is None
            and record.cumulative_commission is None
            and record.unknown_reason is None
        )
        if (
            type(record) is not ExecutionRecord
            or record.payload_sha256 != child_intent.fingerprint
            or record.permit_reference != state.permit.permit_id
            or not (exact_reserved_pending or exact_claiming_dispatch)
        ):
            raise CtpRiskDispatchError("RECOVERY_CHILD_NOT_FIRST_DISPATCH")
        try:
            canonical = self._gate.verify_recovery_reduction_allocation(
                state.recovery_allocation, intent=state.risk_intent
            )
        except Exception:
            raise CtpRiskDispatchError("RECOVERY_ALLOCATION_READBACK_FAILED") from None
        if type(canonical) is not RecoveryReductionAllocation or canonical != state.recovery_allocation:
            raise CtpRiskDispatchError("RECOVERY_ALLOCATION_READBACK_MISMATCH")
        return canonical, state.risk_intent

    def reserve(self, intent: Any) -> RiskPermit:
        """Reject untrusted or unauthenticated reservation entry points."""

        raise CtpRiskDispatchError("AUTHENTICATED_CONTEXT_REQUIRED")

    def reserve_for_authenticated_execution(
        self,
        intent: OrderIntent,
        *,
        admission_context: ExecutionAdmissionContextV1,
        writer_lease: WriterLease,
        dispatch_port: object,
    ) -> RiskPermit:
        """Persist PREPARED, reserve Risk, then persist the exact reservation."""

        self._assert_open_thread()
        owner = self._require_owner()
        is_recovery_close = (
            type(admission_context) is ExecutionAdmissionContextV1
            and admission_context.action == RECOVERY_CLOSE_OPERATION
        )
        if (
            type(intent) is not OrderIntent
            or type(admission_context) is not ExecutionAdmissionContextV1
            or type(writer_lease) is not WriterLease
            or writer_lease is not owner._writer_lease
            or intent.scope != owner._scope
            or admission_context.execution_scope != intent.scope
            or admission_context.principal_ref != admission_context.identity.principal_ref
            or admission_context.action
            not in {"orders.submit", RECOVERY_CLOSE_OPERATION}
            or (
                is_recovery_close
                and (
                    not admission_context.recovery_target_intent_id
                    or intent.position_effect is not PositionEffect.CLOSE_TODAY
                    or intent.reduce_only is not True
                    or intent.position_bucket != "TODAY"
                    or intent.quantity != 1
                )
            )
        ):
            raise CtpRiskDispatchError("AUTHENTICATED_CONTEXT_MISMATCH")
        from bt_api_py.ctp_managed_submit_port import CtpManagedSubmitPort

        if (
            type(dispatch_port) is not CtpManagedSubmitPort
            or dispatch_port.execution_owner is not owner
        ):
            raise CtpRiskDispatchError("AUTHENTICATED_CONTEXT_MISMATCH")
        owner._store.assert_writer_lease(owner._scope, writer_lease)

        try:
            mapped = self._adapter._map_order(intent)
        except Exception:
            raise CtpRiskDispatchError("RISK_INTENT_MAPPING_FAILED") from None
        if (
            type(mapped) is not RiskIntent
            or mapped.scope != self._account_scope
            or mapped.intent_id != intent.intent_id
            or mapped.scope.provider != intent.scope.provider
            or mapped.scope.environment != intent.scope.environment
            or intent.scope.account_ref
            != self._adapter._expected_wire_account_ref(mapped.scope)
            or (mapped.strategy_id is not None and mapped.strategy_id != intent.scope.strategy_id)
        ):
            raise CtpRiskDispatchError("RISK_INTENT_SCOPE_MISMATCH")
        expected_action = {
            PositionEffect.OPEN: IntentAction.INCREASE,
            PositionEffect.CLOSE: IntentAction.REDUCE,
            PositionEffect.CLOSE_TODAY: IntentAction.REDUCE,
            PositionEffect.CLOSE_YESTERDAY: IntentAction.REDUCE,
        }.get(intent.position_effect)
        # Execution's quantity is the exact CTP Volume count. The CTP native
        # request has no product-size conversion, so the Risk mapping must use
        # the same canonical contract unit and economic dimensions.
        if (
            expected_action is None
            or type(mapped.action) is not IntentAction
            or mapped.action is not expected_action
            or mapped.instrument != intent.instrument
            or mapped.quantity != intent.quantity
            or mapped.quantity_unit not in {"contracts", "lots"}
            or mapped.position_side != intent.position_side
            or mapped.position_bucket != intent.position_bucket
        ):
            raise CtpRiskDispatchError("RISK_INTENT_ECONOMIC_MISMATCH")

        principal_ref = admission_context.principal_ref
        wire_scope = self._wire_scope_payload(admission_context)
        recovery_proof: RecoveryCloseSourceProof | None = None
        recovery_parent_permit: RiskPermit | None = None
        if is_recovery_close:
            target_intent_id = admission_context.recovery_target_intent_id
            assert target_intent_id is not None
            parent = self.read_recovery_parent(
                target_intent_id=target_intent_id,
                principal_ref=principal_ref,
                wire_scope=admission_context.wire_scope,
                writer_lease=writer_lease,
            )
            parent_intent, parent_record, recovery_parent_permit, parent_risk_intent = parent
            window_for = getattr(dispatch_port, "recovery_position_window_for", None)
            issuer = getattr(owner._store, "issue_ctp_recovery_close_source_proof", None)
            if not callable(window_for) or not callable(issuer):
                raise CtpRiskDispatchError("RECOVERY_SOURCE_AUTHORITY_UNAVAILABLE")
            try:
                position_window = window_for(intent, target_intent_id)
                # Recovery reductions reserve source-proved position quantity;
                # their Risk notional is deliberately zero and never comes
                # from the limit price or an estimated contract multiplier.
                mapped = replace(mapped, notional=Decimal("0"))
                recovery_proof = issuer(
                    owner._scope,
                    target_intent_id,
                    mapped,
                    position_window,
                    owner_handle=owner._owner_handle,
                    writer_lease=writer_lease,
                    parent_permit=recovery_parent_permit,
                    parent_risk_intent=parent_risk_intent,
                )
            except Exception:
                raise CtpRiskDispatchError("RECOVERY_SOURCE_PROOF_REJECTED") from None
            if (
                type(recovery_proof) is not RecoveryCloseSourceProof
                or type(recovery_parent_permit) is not RiskPermit
                or type(parent_risk_intent) is not RiskIntent
                or recovery_proof.scope != self._account_scope
                or recovery_proof.book_id != owner._scope.key
                or recovery_proof.parent_permit_id != recovery_parent_permit.permit_id
                or recovery_proof.parent_intent_id != target_intent_id
                or recovery_proof.parent_intent_hash != parent_risk_intent.fingerprint
                or recovery_proof.recovery_intent_id != intent.intent_id
                or recovery_proof.recovery_intent_hash != mapped.fingerprint
                or recovery_proof.instrument != intent.instrument
                or recovery_proof.strategy_id != intent.scope.strategy_id
                or recovery_proof.allocation_version != mapped.allocation_version
                or recovery_proof.quantity_unit != mapped.quantity_unit
                or recovery_proof.parent_order_side != parent_intent.side.value
                or recovery_proof.recovery_order_side != intent.side.value
                or recovery_proof.parent_position_side != recovery_parent_permit.position_side
                or recovery_proof.parent_position_bucket != recovery_parent_permit.position_bucket
                or recovery_proof.position_side != intent.position_side
                or recovery_proof.position_bucket != intent.position_bucket
                or recovery_proof.parent_filled_quantity != parent_record.filled_quantity
                or recovery_proof.attributable_position_quantity < intent.quantity
                or recovery_proof.valid_until_ns <= time_ns()
            ):
                raise CtpRiskDispatchError("RECOVERY_SOURCE_PROOF_MISMATCH")
        # The adapter's mapped RiskIntent is the authority for this identity.
        # Keep it distinct by domain from Execution's intent only where the
        # mapper actually does so; never mint a parallel synthetic Risk ID.
        risk_intent_id = mapped.intent_id
        canonical_payload = {
            "schema": "ctp-managed-risk-order-v1",
            "execution_intent": intent.to_payload(),
            "execution_intent_sha256": intent.fingerprint,
            "risk_intent_sha256": mapped.fingerprint,
        }
        if is_recovery_close:
            assert recovery_proof is not None
            canonical_payload["recovery_close"] = {
                "parent_intent_id": recovery_proof.parent_intent_id,
                "parent_permit_id": recovery_proof.parent_permit_id,
                "source_evidence_sha256": recovery_proof.source_evidence_sha256,
                "source_proof_sha256": recovery_proof.fingerprint,
                "source_revision": recovery_proof.source_revision,
            }
        request = self._order_request(intent, mapped)
        request_digest = sha256_text(
            risk_canonical_json(
                {
                    "exchange_name": self._exchange_name,
                    "operation": "BtApi.make_order(normalized=True)",
                    "request": request,
                }
            )
        )
        canonical_payload_digest = sha256_text(risk_canonical_json(canonical_payload))
        try:
            command, created = self._journal.prepare_or_get(
                self._fence,
                principal_ref=principal_ref,
                intent_id=intent.intent_id,
                risk_intent_id=risk_intent_id,
                payload_sha256=canonical_payload_digest,
                canonical_payload=canonical_payload,
                wire_scope=wire_scope,
                exchange_name=self._exchange_name,
                provider_account_id=self._account_scope.account_id,
                client_order_id=intent.intent_id,
                order_request=request,
                order_request_sha256=request_digest,
            )
        except Exception:
            raise CtpRiskDispatchError("RISK_PREPARE_FAILED") from None
        if not created or command.state != "PREPARED":
            raise CtpRiskDispatchError("RISK_COMMAND_REPLAY_UNSUPPORTED")

        recovery_allocation: RecoveryReductionAllocation | None = None
        try:
            if is_recovery_close:
                assert recovery_proof is not None
                assert recovery_parent_permit is not None
                self._recovery_reservation_attempted_for.add(target_intent_id)
                allocation = self._gate.reserve_recovery_reduction(
                    mapped,
                    parent_permit_id=recovery_parent_permit.permit_id,
                    source_proof=recovery_proof,
                )
                if type(allocation) is RecoveryReductionAllocation:
                    recovery_allocation = allocation
                    if type(allocation.permit) is RiskPermit:
                        permit = allocation.permit
                        state = _PermitState(
                            intent=intent,
                            risk_intent=mapped,
                            permit=permit,
                            principal_ref=principal_ref,
                            risk_intent_id=risk_intent_id,
                            state="RECOVERY_RESERVED_UNJOURNALED",
                            recovery_allocation=allocation,
                            recovery_source_proof=recovery_proof,
                            recovery_target_intent_id=target_intent_id,
                        )
                        self._permits[permit.permit_id] = state
                if (
                    type(allocation) is not RecoveryReductionAllocation
                    or type(allocation.permit) is not RiskPermit
                    or allocation.permit.intent_id != mapped.intent_id
                    or allocation.recovery_intent_hash != mapped.fingerprint
                    or allocation.parent_permit_id != recovery_parent_permit.permit_id
                    or allocation.parent_intent_id != target_intent_id
                    or allocation.source_proof_sha256 != recovery_proof.fingerprint
                    or allocation.quantity != intent.quantity
                    or allocation.position_side != intent.position_side
                    or allocation.position_bucket != intent.position_bucket
                ):
                    raise CtpRiskDispatchError("RECOVERY_RISK_ALLOCATION_INVALID")
                verified = self._gate.verify_recovery_reduction_allocation(
                    allocation, intent=mapped
                )
                if type(verified) is not RecoveryReductionAllocation or verified != allocation:
                    raise CtpRiskDispatchError("RECOVERY_RISK_ALLOCATION_READBACK_MISMATCH")
                permit = allocation.permit
            else:
                permit = self._adapter.reserve(intent)
            if type(permit) is not RiskPermit or permit.intent_id != mapped.intent_id:
                raise CtpRiskDispatchError("RISK_PERMIT_INVALID")
            state = self._permits.get(permit.permit_id)
            if state is None:
                state = _PermitState(
                    intent=intent,
                    risk_intent=mapped,
                    permit=permit,
                    principal_ref=principal_ref,
                    risk_intent_id=risk_intent_id,
                    state="RESERVED",
                    recovery_allocation=recovery_allocation,
                    recovery_source_proof=recovery_proof,
                    recovery_target_intent_id=(
                        admission_context.recovery_target_intent_id if is_recovery_close else None
                    ),
                )
                self._permits[permit.permit_id] = state
            else:
                state.state = "RESERVED"
            self._journal.append_event(
                self._fence,
                principal_ref=principal_ref,
                intent_id=intent.intent_id,
                event_type="RESERVED",
                payload={
                    "permit_id": permit.permit_id,
                    "risk_intent_id": risk_intent_id,
                    **(
                        {}
                        if recovery_allocation is None
                        else {
                            "recovery_allocation_sha256": recovery_allocation.fingerprint,
                            "recovery_parent_permit_id": recovery_allocation.parent_permit_id,
                            "recovery_source_proof_sha256": recovery_allocation.source_proof_sha256,
                        }
                    ),
                },
            )
        except Exception as error:
            # Risk reservation is still unclaimed here. Attempt only the
            # adapter's normal unclaimed release; never hide either failure.
            if recovery_allocation is not None:
                state = self._permits.get(recovery_allocation.permit_id)
                if state is not None:
                    state.state = "RECOVERY_RESERVED_UNJOURNALED"
                raise CtpRiskDispatchError("RECOVERY_RESERVATION_JOURNAL_FAILED") from None
            if is_recovery_close and target_intent_id in self._recovery_reservation_attempted_for:
                raise CtpRiskDispatchError("RECOVERY_RESERVATION_OUTCOME_UNCERTAIN") from None
            if "permit" in locals() and type(permit) is RiskPermit:
                try:
                    self._adapter.release(permit.permit_id, "ctp_journal_reservation_failed")
                except Exception:
                    raise CtpRiskDispatchError("RISK_RESERVATION_JOURNAL_FAILED") from None
            if isinstance(error, RiskAdmissionDeniedError):
                try:
                    self._journal.append_event(
                        self._fence,
                        principal_ref=principal_ref,
                        intent_id=intent.intent_id,
                        event_type="ADMISSION_DENIED",
                        payload={"code": error.code, "risk_intent_id": risk_intent_id},
                    )
                except Exception:
                    raise CtpRiskDispatchError("RISK_DENIAL_JOURNAL_FAILED") from None
                raise
            if isinstance(error, CtpRiskDispatchError):
                raise
            raise CtpRiskDispatchError("RISK_RESERVATION_FAILED") from None

        state = self._permits.get(permit.permit_id)
        if state is None:
            state = _PermitState(
                intent=intent,
                risk_intent=mapped,
                permit=permit,
                principal_ref=principal_ref,
                risk_intent_id=risk_intent_id,
                state="RESERVED",
            )
            self._permits[permit.permit_id] = state
        return permit

    def validate(self, permit_reference: str, intent: OrderIntent) -> RiskPermit:
        self._assert_open_thread()
        state = self._require_permit(permit_reference, intent)
        if state.state != "RESERVED":
            raise CtpRiskDispatchError("RISK_PERMIT_STATE_CONFLICT")
        try:
            if state.recovery_allocation is not None:
                self.read_recovery_allocation(
                    target_intent_id=state.recovery_target_intent_id or "",
                    child_intent=intent,
                    writer_lease=self._require_owner()._writer_lease,
                )
                permit = self._gate.validate_permit(permit_reference, state.risk_intent)
            else:
                permit = self._adapter.validate(permit_reference, intent)
        except Exception:
            raise CtpRiskDispatchError("RISK_PERMIT_REVALIDATION_FAILED") from None
        if type(permit) is not RiskPermit or permit.permit_id != permit_reference:
            raise CtpRiskDispatchError("RISK_PERMIT_INVALID")
        return permit

    def claim_for_dispatch(self, permit_reference: str, intent: OrderIntent) -> RiskPermit:
        """Use the existing Risk claim exactly once and journal its readback."""

        self._assert_open_thread()
        state = self._require_permit(permit_reference, intent)
        if state.state != "RESERVED":
            raise CtpRiskDispatchError("RISK_CLAIM_REPLAY_REJECTED")
        state.state = "CLAIMING"
        try:
            if state.recovery_allocation is not None:
                self.read_recovery_allocation(
                    target_intent_id=state.recovery_target_intent_id or "",
                    child_intent=intent,
                    writer_lease=self._require_owner()._writer_lease,
                )
                permit = self._gate.claim_for_dispatch(permit_reference, state.risk_intent)
            else:
                permit = self._adapter.claim_for_dispatch(permit_reference, intent)
            claim = self._gate.dispatch_claim_binding(permit_reference)
            if (
                type(permit) is not RiskPermit
                or permit.permit_id != permit_reference
                or type(claim) is not DispatchClaimBinding
                or claim.scope != self._account_scope
                or claim.permit_id != permit_reference
                or claim.intent_id != state.risk_intent.intent_id
                or claim.intent_hash != state.risk_intent.fingerprint
                or not _sha256(claim.claim_digest)
                or type(claim.claimed_at) not in (int, float)
                or type(claim.claimed_at) is bool
                or not math.isfinite(float(claim.claimed_at))
            ):
                raise CtpRiskDispatchError("RISK_CLAIM_BINDING_INVALID")
            claim_payload = {
                "claim": self._claim_payload(claim),
                "permit_id": permit_reference,
                "risk_intent_id": state.risk_intent_id,
            }
            claim_event = self._journal.append_event(
                self._fence,
                principal_ref=state.principal_ref,
                intent_id=intent.intent_id,
                event_type="CLAIMED",
                payload=claim_payload,
            )
            if (
                type(claim_event) is not RiskOrderJournalEvent
                or type(claim_event.payload) is not dict
                or set(claim_event.payload) != set(claim_payload) | {"updated_at_ns"}
                or type(claim_event.payload.get("updated_at_ns")) is not int
                or claim_event.payload["updated_at_ns"] <= 0
                or {
                    key: value
                    for key, value in claim_event.payload.items()
                    if key != "updated_at_ns"
                }
                != claim_payload
            ):
                raise CtpRiskDispatchError("RISK_CLAIM_EVENT_INVALID")
            claim_event_updated_at_ns = claim_event.payload["updated_at_ns"]
        except Exception:
            # Claim status may already be durable. Keep the local latch closed;
            # a retry can never issue another Risk claim.
            state.state = "CLAIMED_UNJOURNALED"
            raise
        state.permit = permit
        state.claim = claim
        state.claim_event = claim_event
        state.claim_event_updated_at_ns = claim_event_updated_at_ns
        state.state = "CLAIMED"
        return permit

    def settle(self, permit_reference: str) -> RiskPermit:
        self._assert_open_thread()
        state = self._permits.get(permit_reference)
        if state is not None and state.state in {"SETTLED", "ACKED_TRACKED"}:
            return self.ensure_settled(permit_reference)
        if state is None or state.state not in {"CLAIMED", "DISPATCHING"}:
            raise CtpRiskDispatchError("RISK_PERMIT_NOT_CLAIMED")
        try:
            permit = self._adapter.settle(permit_reference)
        except Exception:
            raise CtpRiskDispatchError("RISK_PERMIT_SETTLEMENT_FAILED") from None
        if type(permit) is not RiskPermit or permit.permit_id != permit_reference:
            raise CtpRiskDispatchError("RISK_PERMIT_INVALID")
        state.state = "SETTLED"
        return permit

    def ensure_settled(self, permit_reference: str) -> RiskPermit:
        """Delegate the exact permit's existing Risk recovery-settlement read."""

        self._assert_open_thread()
        state = self._permits.get(permit_reference)
        if state is None or state.state not in {
            "CLAIMED",
            "DISPATCHING",
            "SETTLED",
            "ACKED_TRACKED",
            "RISK_RESOLUTION_UNCERTAIN",
        }:
            raise CtpRiskDispatchError("RISK_PERMIT_NOT_CLAIMED")
        try:
            permit = self._adapter.ensure_settled(permit_reference)
        except Exception:
            raise CtpRiskDispatchError("RISK_PERMIT_RECOVERY_SETTLEMENT_FAILED") from None
        if type(permit) is not RiskPermit or permit.permit_id != permit_reference:
            raise CtpRiskDispatchError("RISK_PERMIT_INVALID")
        if state.state not in {"ACKED_TRACKED", "RISK_RESOLUTION_UNCERTAIN"}:
            state.state = "SETTLED"
        return permit

    def release(self, permit_reference: str, reason: str) -> None:
        self._assert_open_thread()
        state = self._permits.get(permit_reference)
        if state is not None and state.state in {
            "CLAIMING",
            "CLAIMED",
            "CLAIMED_UNJOURNALED",
            "DISPATCHING",
            "DISPATCHING_UNCERTAIN",
            "SETTLED",
            "ACKED_TRACKED",
            "RISK_RESOLUTION_UNCERTAIN",
        }:
            # Delegate so DurableRiskGate enforces its claimed-permit guard.
            self._adapter.release(permit_reference, reason)
            return
        self._adapter.release(permit_reference, reason)
        if state is not None:
            state.state = "RELEASED"

    def before_native_dispatch(
        self,
        *,
        owner: object,
        prepared: Any,
        command: Any,
        reservation: Any,
        dispatch_binding: CtpManagedDispatchBinding,
    ) -> None:
        """Persist the exact queued CTP command binding before Store claim."""

        self._assert_open_thread()
        binding = dispatch_binding
        if owner is not self._require_owner() or type(binding) is not CtpManagedDispatchBinding:
            raise CtpRiskDispatchError("CTP_OWNER_BINDING_MISMATCH")
        correlation = getattr(command, "correlation_key", None)
        native_request_id = getattr(correlation, "native_request_id", None)
        if (
            getattr(prepared, "operation", None) != "submit"
            or getattr(command, "operation", None) != "SUBMIT"
            or getattr(command, "status", None) != "READY"
            or getattr(command, "local_queue_receipt_queued", None) is not True
            or getattr(command, "command_id", None) != binding.command_id
            or getattr(command, "command_id", None) in self._by_command
            or binding.managed_intent_id != prepared.managed_intent_id
            or binding.account_key != owner._scope.account_key
            or binding.scope_key != owner._scope.key
            or binding.trading_day != owner._scope.trading_day
            or reservation.managed_intent_id != binding.managed_intent_id
            or reservation.account_key != binding.account_key
            or reservation.scope_key != binding.scope_key
            or reservation.runtime_order_id != binding.runtime_order_id
            or reservation.order_ref != binding.order_ref
            or owner._session_binding.session_generation_id != binding.session_generation_id
            or owner._session_binding.session_binding_sha256 != binding.session_binding_sha256
            or binding.local_queue_receipt_id != command.local_queue_receipt_id
            or type(native_request_id) is not int
            or binding.native_request_id != native_request_id
            or binding.request_payload_sha256 != command.request_payload_sha256
            or binding.native_request_payload_sha256 != command.native_request_payload_sha256
        ):
            raise CtpRiskDispatchError("CTP_QUEUED_BINDING_MISMATCH")
        state = next(
            (
                item
                for item in self._permits.values()
                if item.intent.intent_id == binding.managed_intent_id
                and item.state == "CLAIMED"
                and item.claim is not None
            ),
            None,
        )
        if state is None:
            raise CtpRiskDispatchError("RISK_CLAIM_UNAVAILABLE")
        self._readback_claim(state)
        if binding.request_payload != dict(command.request_payload):
            raise CtpRiskDispatchError("CTP_REQUEST_PAYLOAD_MISMATCH")
        native_payload = dict(binding.native_request_payload)
        if (
            payload_sha256(native_payload) != binding.native_request_payload_sha256
            or native_payload.get("OrderRef") != binding.order_ref
            or native_payload.get("InstrumentID") != state.intent.instrument
            or native_payload.get("ExchangeID") != self._exchange_name
        ):
            raise CtpRiskDispatchError("CTP_NATIVE_PAYLOAD_MISMATCH")
        try:
            session_payload = owner._store._ctp_callback_session_binding_payload(
                owner._session_binding
            )
            reservation_payload = {
                "account_key": reservation.account_key,
                "created_at_ns": reservation.created_at_ns,
                "managed_intent_id": reservation.managed_intent_id,
                "order_ref": reservation.order_ref,
                "runtime_order_id": reservation.runtime_order_id,
                "scope_key": reservation.scope_key,
                "trading_day": reservation.trading_day,
            }
            reservation_digest = payload_sha256(
                {
                    "registration_type": "ctp_order_identity_registration.v1",
                    "reservation": reservation_payload,
                    "session_binding": session_payload,
                }
            )
            dispatch_payload = {
                "schema": "ctp-risk-ctp-submit-binding-v1",
                "risk_intent_id": state.risk_intent_id,
                "permit_id": state.permit.permit_id,
                "risk_claim_digest": state.claim.claim_digest,
                "risk_intent_hash": state.claim.intent_hash,
                "submit_command_id": binding.command_id,
                "managed_intent_id": binding.managed_intent_id,
                "runtime_order_id": binding.runtime_order_id,
                "order_ref": binding.order_ref,
                "provider_order_id": None,
                "account_key": binding.account_key,
                "scope_key": binding.scope_key,
                "risk_scope_key": self._account_scope.key,
                "trading_day": binding.trading_day,
                "reservation_digest_sha256": reservation_digest,
                "callback_owner_intent_id": owner._owner_handle.owner_intent_id,
                "session_generation_id": binding.session_generation_id,
                "session_binding_sha256": binding.session_binding_sha256,
                "request_payload_sha256": binding.request_payload_sha256,
                "native_request_payload_sha256": binding.native_request_payload_sha256,
                "native_request_id": native_request_id,
                "local_queue_receipt_id": binding.local_queue_receipt_id,
                "instrument_id": native_payload.get("InstrumentID"),
                "exchange_id": native_payload.get("ExchangeID"),
            }
            if (
                not _sha256(reservation_digest)
                or type(dispatch_payload["instrument_id"]) is not str
                or type(dispatch_payload["exchange_id"]) is not str
            ):
                raise CtpRiskDispatchError("CTP_DISPATCH_BINDING_INVALID")
            event = self._journal.append_event(
                self._fence,
                principal_ref=state.principal_ref,
                intent_id=state.intent.intent_id,
                event_type="DISPATCHING",
                payload={
                    "dispatch_attempt_count": 1,
                    "permit_id": state.permit.permit_id,
                    "risk_intent_id": state.risk_intent_id,
                    "ctp_dispatch_binding": dispatch_payload,
                },
            )
            state.dispatch_event = event
            dispatch_readback = self.read_dispatch_binding(
                state.principal_ref, state.intent.intent_id
            )
            if dispatch_readback != dispatch_payload:
                raise CtpRiskDispatchError("DISPATCH_BINDING_READBACK_MISMATCH")
        except Exception:
            state.state = "DISPATCHING_UNCERTAIN"
            raise
        state.state = "DISPATCHING"
        self._by_command[binding.command_id] = state

    def read_dispatch_binding(
        self, principal_ref: str, intent_id: str
    ) -> Mapping[str, Any] | None:
        """Read back the exact immutable DISPATCHING event written by this bridge."""

        self._assert_open_thread()
        if type(principal_ref) is not str or type(intent_id) is not str:
            raise ContractValidationError("Risk journal identity is required")
        selected = next(
            (
                item
                for item in self._permits.values()
                if item.principal_ref == principal_ref and item.intent.intent_id == intent_id
            ),
            None,
        )
        if selected is None or selected.dispatch_event is None:
            return None
        event = self._journal.get_event(
            self._account_scope,
            principal_ref,
            intent_id,
            selected.dispatch_event.revision,
        )
        if (
            type(event) is not RiskOrderJournalEvent
            or event.event_type != "DISPATCHING"
            or event.record_sha256 != selected.dispatch_event.record_sha256
            or event.payload.get("ctp_dispatch_binding")
            != selected.dispatch_event.payload.get("ctp_dispatch_binding")
        ):
            raise CtpRiskDispatchError("DISPATCH_BINDING_READBACK_MISMATCH")
        value = event.payload.get("ctp_dispatch_binding")
        return None if not isinstance(value, Mapping) else dict(value)

    def after_evidenced_observation(
        self,
        record: ExecutionRecord,
        event: ExecutionEvent,
        *,
        writer_lease: WriterLease,
    ) -> None:
        """Resolve only an exact zero-fill ACK into durable tracked exposure.

        The Execution facade calls this after its normal permit settlement.
        CTP callback/query evidence is issued and immediately read back here;
        neither the Execution ACK nor a native queue receipt is enough to
        clear the Risk dispatch latch.
        """

        self._assert_open_thread()
        owner = self._require_owner()
        if (
            type(record) is not ExecutionRecord
            or type(event) is not ExecutionEvent
            or type(writer_lease) is not WriterLease
            or writer_lease is not owner._writer_lease
        ):
            raise CtpRiskDispatchError("EXECUTION_OBSERVATION_BINDING_INVALID")
        owner._assert_worker_thread()
        owner._assert_active_owner()
        owner._store.assert_writer_lease(owner._scope, writer_lease)
        intent = self._read_exact_execution_event(owner, record, event)
        state = self._permits.get(record.permit_reference or "")
        if (
            state is None
            or not _same_order_intent(state.intent, intent)
            or state.permit.permit_id != record.permit_reference
            or state.claim is None
            or state.dispatch_event is None
        ):
            raise CtpRiskDispatchError("RISK_PERMIT_IDENTITY_MISMATCH")

        if state.state == "ACKED_TRACKED":
            if state.tracked_source_event == event:
                return
            # Later positive fills need a distinct fee-complete Risk exposure
            # proof. The zero-fill reader cannot authorize that transition.
            if (
                event.event_type in {"provider_observation", "reconciled_observation"}
                and event.intent_id == intent.intent_id
                and event.scope_key == intent.scope.key
                and event.state in {
                    ExecutionState.PARTIALLY_FILLED,
                    ExecutionState.FILLED,
                    ExecutionState.CANCELLED,
                }
                and record.state is event.state
                and record.filled_quantity > 0
                and type(record.provider_order_id) is str
                and state.tracked_proof is not None
                and record.provider_order_id == state.tracked_proof.provider_order_id
            ):
                # Bind the review latch to the latest exact observation. A
                # later partial fill changes the cumulative quantity, and a
                # terminal cancel changes the current state; either needs a
                # fresh adjacent source/review pair rather than stale evidence.
                state.tracked_update_review_source_event = event
            raise CtpRiskDispatchError("CTP_TRACKED_ORDER_UPDATE_REQUIRES_REVIEW")
        if state.state == "RISK_RESOLUTION_UNCERTAIN":
            raise CtpRiskDispatchError("RISK_RESOLUTION_REQUIRES_REVIEW")
        if state.state != "SETTLED":
            raise CtpRiskDispatchError("RISK_PERMIT_NOT_SETTLED")
        if (
            event.event_type not in {"provider_observation", "reconciled_observation"}
            or event.intent_id != intent.intent_id
            or event.scope_key != intent.scope.key
            or event.state is not ExecutionState.ACKED
            or record.state is not ExecutionState.ACKED
            or record.filled_quantity != 0
            or record.average_price is not None
            or record.cumulative_commission is not None
            or record.review_required
            or type(record.provider_order_id) is not str
            or not record.provider_order_id
        ):
            raise CtpRiskDispatchError("CTP_ZERO_FILL_ACK_REQUIRED")

        claim = state.claim
        if type(claim) is not DispatchClaimBinding:
            raise CtpRiskDispatchError("RISK_CLAIM_UNAVAILABLE")
        self._readback_claim(state, expected_state="SETTLED")
        dispatch = self.read_dispatch_binding(state.principal_ref, state.intent.intent_id)
        if (
            type(dispatch) is not dict
            or dispatch.get("permit_id") != state.permit.permit_id
            or dispatch.get("risk_intent_id") != state.risk_intent_id
            or dispatch.get("risk_claim_digest") != claim.claim_digest
        ):
            raise CtpRiskDispatchError("DISPATCH_BINDING_READBACK_MISMATCH")
        command = self._journal.get_command(
            self._account_scope, state.principal_ref, state.intent.intent_id
        )
        if (
            command is None
            or command.state != "DISPATCHING"
            or command.permit_id != state.permit.permit_id
            or command.risk_intent_id != state.risk_intent_id
            or not _sha256(command.order_request_sha256)
        ):
            raise CtpRiskDispatchError("RISK_DISPATCH_NOT_READY")

        recovery_markers = (
            state.recovery_target_intent_id is not None,
            state.recovery_allocation is not None,
            state.recovery_source_proof is not None,
        )
        if any(recovery_markers):
            try:
                self._verify_settled_recovery_zero_fill_ack(
                    owner=owner,
                    state=state,
                    intent=intent,
                    record=record,
                    claim=claim,
                    dispatch=dispatch,
                    command=command,
                )
            except Exception:
                # A partially-bound recovery result remains quarantined. An
                # exact ACK takes the return above without consuming the OPEN
                # zero-fill tracked-order proof or clearing the close latch.
                state.state = "RISK_RESOLUTION_UNCERTAIN"
                raise
            return

        from bt_api_py.ctp_zero_fill_tracked_order import (
            CtpZeroFillTrackedOrderEvidenceV1,
            CtpZeroFillTrackedOrderReader,
        )
        from bt_api_execution import CtpOwnerCallbackObservationNotReadyV1

        reader = CtpZeroFillTrackedOrderReader(owner)
        state.zero_fill_reader = reader
        # From here onward, any failed journal/gate step is one-shot uncertain.
        # Do not issue a second source query or replay a possibly committed
        # Risk resolution automatically.
        state.state = "RISK_RESOLUTION_UNCERTAIN"
        try:
            evidence = reader.issue(intent.intent_id)
            if type(evidence) is CtpOwnerCallbackObservationNotReadyV1:
                raise CtpRiskDispatchError("CTP_ZERO_FILL_EVIDENCE_UNAVAILABLE")
            if type(evidence) is not CtpZeroFillTrackedOrderEvidenceV1:
                raise CtpRiskDispatchError("CTP_ZERO_FILL_EVIDENCE_INVALID")
            state.zero_fill_evidence = evidence
            if reader.readback(evidence) is not evidence:
                raise CtpRiskDispatchError("CTP_ZERO_FILL_READBACK_MISMATCH")
            owner._assert_active_owner()
            owner._store.assert_writer_lease(owner._scope, writer_lease)
            if not _same_order_intent(
                self._read_exact_execution_event(owner, record, event), intent
            ):
                raise CtpRiskDispatchError("EXECUTION_OBSERVATION_BINDING_INVALID")
            if (
                evidence.scope != intent.scope
                or evidence.managed_intent_id != intent.intent_id
                or evidence.intent_fingerprint != intent.fingerprint
                or evidence.provider_order_id != record.provider_order_id
                or evidence.submit_command_id != dispatch.get("submit_command_id")
                or evidence.managed_intent_id != dispatch.get("managed_intent_id")
                or evidence.local_queue_receipt_id != dispatch.get("local_queue_receipt_id")
                or evidence.native_request_id != dispatch.get("native_request_id")
                or evidence.session_binding.session_generation_id
                != dispatch.get("session_generation_id")
                or evidence.session_binding.session_binding_sha256
                != dispatch.get("session_binding_sha256")
                or evidence.projection.account_key != dispatch.get("account_key")
                or evidence.projection.scope_key != dispatch.get("scope_key")
                or evidence.projection.trading_day != dispatch.get("trading_day")
                or evidence.projection.runtime_order_id != dispatch.get("runtime_order_id")
                or evidence.projection.order_ref != dispatch.get("order_ref")
                or evidence.projection.registration_digest
                != dispatch.get("reservation_digest_sha256")
                or evidence.projection.instrument_id != dispatch.get("instrument_id")
                or evidence.projection.exchange_id != dispatch.get("exchange_id")
                or evidence.quantity != intent.quantity
                or evidence.projection.quantity != evidence.quantity
                or evidence.callback_snapshot.owner_intent_id
                != dispatch.get("callback_owner_intent_id")
                or evidence.callback_snapshot.session_generation_id
                != dispatch.get("session_generation_id")
                or evidence.provider_observation.state is not ExecutionState.ACKED
                or evidence.provider_observation.filled_quantity != 0
                or evidence.projection.provider_state != "OPEN"
                or evidence.projection.traded_quantity != 0
                or evidence.projection.remaining_quantity != evidence.quantity
                or evidence.callback_snapshot.trade_notional != 0
                or evidence.callback_snapshot.trade_fact_count != 0
                or evidence.callback_snapshot.provider_state != "ACKNOWLEDGED"
                or evidence.callback_snapshot.filled_quantity != 0
            ):
                raise CtpRiskDispatchError("CTP_ZERO_FILL_EVIDENCE_MISMATCH")

            evidence_payload = evidence.to_payload()
            evidence_sha256 = evidence.evidence_sha256
            # RiskOrderJournal's public observation envelope has a closed
            # top-level allowlist. Keep the CTP-specific current-projection
            # evidence in its bounded `record` member rather than inventing
            # new envelope keys or implying a complete GetDeals history.
            ctp_observation_record = {
                "schema": "ctp-risk-current-order-evidence-v2",
                "evidence_kind": "CTP_CALLBACK_QUERY_ORDER_AND_QUERY_TRADE_TARGET_ORDER_WINDOW",
                "evidence_sha256": evidence_sha256,
                "evidence": evidence_payload,
                "execution_intent_id": intent.intent_id,
                "execution_intent_sha256": intent.fingerprint,
                "risk_intent_id": state.risk_intent_id,
                "observation_scope": "CURRENT_ORDER_AND_TRADE_QUERY_WINDOW",
                "fill_history_completeness": "COMPLETE_FOR_TARGET_ORDER",
                "trade_history_observed": True,
                "trade_fact_count": evidence.callback_snapshot.trade_fact_count,
                "trade_notional": format(evidence.callback_snapshot.trade_notional, "f"),
                "cumulative_commission": None,
                "cumulative_commission_currency": None,
                "commission_quality": "INCOMPLETE",
            }
            tracked_order = {
                "account_id": self._account_scope.account_id,
                "claim_digest": claim.claim_digest,
                "instrument": intent.instrument,
                "position_bucket": intent.position_bucket,
                "position_effect": intent.position_effect.value,
                "position_side": intent.position_side,
                "provider_order_id": evidence.provider_order_id,
                "quantity": format(intent.quantity, "f"),
                "reduce_only": intent.reduce_only,
                "record": {
                    **ctp_observation_record,
                    "provider_state": "OPEN",
                    "provider_status": "ACKED",
                },
                "schema": "ctp-risk-current-order-observation-v1",
                "side": intent.side.value,
                "source_alignment_valid": True,
                "status": "OPEN",
                "filled_quantity": "0",
                "trade_count": 0,
            }
            tracked_fills = {
                "account_id": self._account_scope.account_id,
                "claim_digest": claim.claim_digest,
                "filled_quantity": "0",
                "instrument": intent.instrument,
                "provider_order_id": evidence.provider_order_id,
                "record": {
                    **ctp_observation_record,
                    "requested_quantity": format(intent.quantity, "f"),
                },
                "risk_intent_id": state.risk_intent_id,
                "schema": "ctp-risk-current-order-zero-fill-observation-v1",
                "source_alignment_valid": True,
                "trade_count": 0,
            }
            order_event, fill_event, reconciliation_sha = (
                self._journal.append_resolution_observations(
                    self._fence,
                    principal_ref=state.principal_ref,
                    intent_id=intent.intent_id,
                    order_observation=tracked_order,
                    fill_observation=tracked_fills,
                )
            )
            expected_order_event_payload = {
                **tracked_order,
                "updated_at_ns": order_event.payload.get("updated_at_ns")
                if type(order_event) is RiskOrderJournalEvent
                and type(order_event.payload) is dict
                else None,
            }
            expected_fill_event_payload = {
                **tracked_fills,
                "updated_at_ns": fill_event.payload.get("updated_at_ns")
                if type(fill_event) is RiskOrderJournalEvent
                and type(fill_event.payload) is dict
                else None,
            }
            if (
                type(order_event) is not RiskOrderJournalEvent
                or type(fill_event) is not RiskOrderJournalEvent
                or order_event.event_type != "ORDER_OBSERVATION"
                or fill_event.event_type != "FILL_OBSERVATION"
                or order_event.revision >= fill_event.revision
                or type(order_event.payload) is not dict
                or set(order_event.payload) != set(expected_order_event_payload)
                or risk_canonical_json(order_event.payload)
                != risk_canonical_json(expected_order_event_payload)
                or type(order_event.payload.get("updated_at_ns")) is not int
                or order_event.payload["updated_at_ns"] <= 0
                or type(fill_event.payload) is not dict
                or set(fill_event.payload) != set(expected_fill_event_payload)
                or risk_canonical_json(fill_event.payload)
                != risk_canonical_json(expected_fill_event_payload)
                or type(fill_event.payload.get("updated_at_ns")) is not int
                or fill_event.payload["updated_at_ns"] <= 0
                or self._journal.get_event(
                    self._account_scope,
                    state.principal_ref,
                    intent.intent_id,
                    order_event.revision,
                )
                != order_event
                or self._journal.get_event(
                    self._account_scope,
                    state.principal_ref,
                    intent.intent_id,
                    fill_event.revision,
                )
                != fill_event
            ):
                raise CtpRiskDispatchError("RISK_OBSERVATION_READBACK_MISMATCH")
            settled_permit = self._adapter.ensure_settled(state.permit.permit_id)
            if (
                type(settled_permit) is not RiskPermit
                or settled_permit.permit_id != state.permit.permit_id
            ):
                raise CtpRiskDispatchError("RISK_PERMIT_SETTLEMENT_READBACK_INVALID")
            reservation_id, reservation_sha, _reference = self._journal.store_exposure_reference(
                self._fence,
                principal_ref=state.principal_ref,
                intent_id=intent.intent_id,
                claim=claim,
                evidence_class=_evidence_class(self._account_scope),
                permit_id=state.permit.permit_id,
                provider_order_id=evidence.provider_order_id,
                accepted_request_sha256=command.order_request_sha256,
                reconciliation_evidence_sha256=reconciliation_sha,
                observation_revision=fill_event.revision,
                observation_record_sha256=fill_event.record_sha256,
            )
            proof_command = self._journal.get_command(
                self._account_scope, state.principal_ref, intent.intent_id
            )
            if proof_command is None or proof_command.state != "PROOF_READY":
                raise CtpRiskDispatchError("RISK_PROOF_READY_MISSING")
            proof = DispatchTrackedOrderProof(
                scope=claim.scope,
                permit_id=claim.permit_id,
                intent_id=claim.intent_id,
                intent_hash=claim.intent_hash,
                cause_id=claim.cause_id,
                claim_digest=claim.claim_digest,
                evidence_class=_evidence_class(self._account_scope),
                dispatch_attempt_count=1,
                journal_revision=proof_command.latest_revision,
                journal_record_sha256=proof_command.latest_record_sha256,
                reconciliation_evidence_sha256=reconciliation_sha,
                writer_fence_sha256=self._fence.fence_sha256,
                provider_order_id=evidence.provider_order_id,
                accepted_request_sha256=command.order_request_sha256,
                exposure_reservation_id=reservation_id,
                exposure_reservation_sha256=reservation_sha,
                filled_quantity=0,
                trade_count=0,
            )
            if reader.readback(evidence) is not evidence:
                raise CtpRiskDispatchError("CTP_ZERO_FILL_READBACK_MISMATCH")
            if (
                not _same_order_intent(
                    self._read_exact_execution_event(owner, record, event), intent
                )
                or self._journal.get_command(
                    self._account_scope, state.principal_ref, intent.intent_id
                ).state
                != "PROOF_READY"
            ):
                raise CtpRiskDispatchError("RISK_PROOF_SOURCE_CHANGED")
            gate_receipt = self._gate.resolve_dispatch_freeze(proof)
            if type(gate_receipt) is not DispatchResolutionReceipt:
                gate_receipt = self._gate.dispatch_resolution_receipt(state.permit.permit_id)
            if (
                type(gate_receipt) is not DispatchResolutionReceipt
                or gate_receipt.status is not DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
                or gate_receipt.claim != claim
                or gate_receipt.proof != proof
                or gate_receipt.proof_sha256 != proof.fingerprint
                or gate_receipt.dispatch_latch_active is not False
            ):
                raise CtpRiskDispatchError("RISK_TRACKED_RECEIPT_INVALID")
            tracked_event = self._journal.append_event(
                self._fence,
                principal_ref=state.principal_ref,
                intent_id=intent.intent_id,
                event_type="ACKED_TRACKED",
                payload={
                    "claim_digest": claim.claim_digest,
                    "dispatch_attempt_count": 1,
                    "exposure_reference_id": reservation_id,
                    "exposure_reference_sha256": reservation_sha,
                    "filled_quantity": 0,
                    "permit_id": state.permit.permit_id,
                    "proof_ready_record_sha256": proof.journal_record_sha256,
                    "proof_ready_revision": proof.journal_revision,
                    "proof_sha256": proof.fingerprint,
                    "provider_order_id": evidence.provider_order_id,
                    "public_state": "ACKED_TRACKED",
                    "recovery_attestation_sha256": None,
                    "reconciliation_evidence_sha256": reconciliation_sha,
                    "risk_intent_id": claim.intent_id,
                    "trade_count": 0,
                },
            )
            final_command = self._journal.get_command(
                self._account_scope, state.principal_ref, intent.intent_id
            )
            final_receipt = self._gate.dispatch_resolution_receipt(state.permit.permit_id)
            if (
                final_command is None
                or final_command.state != "ACKED_TRACKED"
                or type(final_receipt) is not DispatchResolutionReceipt
                or not _same_dispatch_resolution_receipt(
                    final_receipt, gate_receipt
                )
                or not self._journal.has_resolved_ack_history(
                    self._fence,
                    principal_ref=state.principal_ref,
                    intent_id=intent.intent_id,
                    receipt=final_receipt,
                )
                or self._journal.get_event(
                    self._account_scope,
                    state.principal_ref,
                    intent.intent_id,
                    tracked_event.revision,
                )
                != tracked_event
            ):
                raise CtpRiskDispatchError("RISK_TRACKED_READBACK_MISMATCH")
            state.tracked_proof = proof
            state.tracked_receipt = final_receipt
            state.tracked_event = tracked_event
            state.tracked_source_event = event
            state.state = "ACKED_TRACKED"
        except Exception:
            state.state = "RISK_RESOLUTION_UNCERTAIN"
            raise

    def _verify_settled_recovery_zero_fill_ack(
        self,
        *,
        owner: Any,
        state: _PermitState,
        intent: OrderIntent,
        record: ExecutionRecord,
        claim: DispatchClaimBinding,
        dispatch: Mapping[str, Any],
        command: RiskOrderJournalEvent,
    ) -> None:
        """Keep an exact zero-fill recovery ACK settled and close-latched."""

        allocation = state.recovery_allocation
        proof = state.recovery_source_proof
        target_intent_id = state.recovery_target_intent_id
        risk_intent = state.risk_intent
        permit = state.permit
        if (
            state.state != "SETTLED"
            or type(allocation) is not RecoveryReductionAllocation
            or type(proof) is not RecoveryCloseSourceProof
            or type(target_intent_id) is not str
            or not target_intent_id
            or type(risk_intent) is not RiskIntent
            or type(permit) is not RiskPermit
            or not _same_order_intent(state.intent, intent)
            or intent.position_effect is not PositionEffect.CLOSE_TODAY
            or intent.reduce_only is not True
            or intent.position_bucket != "TODAY"
            or risk_intent.action is not IntentAction.REDUCE
            or risk_intent.notional != Decimal("0")
            or risk_intent.intent_id != state.risk_intent_id
            or risk_intent.scope != self._account_scope
            or risk_intent.instrument != intent.instrument
            or risk_intent.strategy_id != intent.scope.strategy_id
            or risk_intent.quantity != intent.quantity
            or risk_intent.position_side != intent.position_side
            or risk_intent.position_bucket != intent.position_bucket
            or permit.permit_id != claim.permit_id
            or permit.intent_id != risk_intent.intent_id
            or permit.scope != self._account_scope
            or permit.action is not IntentAction.REDUCE
            or permit.notional != Decimal("0")
            or permit.position_side != intent.position_side
            or permit.position_bucket != intent.position_bucket
            or allocation.permit != permit
            or allocation.parent_intent_id != target_intent_id
            or allocation.parent_permit_id != proof.parent_permit_id
            or allocation.parent_intent_hash != proof.parent_intent_hash
            or allocation.recovery_intent_id != intent.intent_id
            or allocation.recovery_intent_hash != risk_intent.fingerprint
            or allocation.book_id != owner._scope.key
            or allocation.instrument != intent.instrument
            or allocation.strategy_id != intent.scope.strategy_id
            or allocation.position_side != intent.position_side
            or allocation.position_bucket != intent.position_bucket
            or allocation.order_side != intent.side.value
            or allocation.quantity != intent.quantity
            or allocation.quantity_unit != risk_intent.quantity_unit
            or allocation.source_proof_sha256 != proof.fingerprint
            or allocation.source_evidence_sha256 != proof.source_evidence_sha256
            or allocation.source_revision != proof.source_revision
            or proof.scope != self._account_scope
            or proof.book_id != owner._scope.key
            or proof.parent_intent_id != target_intent_id
            or proof.recovery_intent_id != intent.intent_id
            or proof.recovery_intent_hash != risk_intent.fingerprint
            or proof.instrument != intent.instrument
            or proof.strategy_id != intent.scope.strategy_id
            or proof.recovery_order_side != intent.side.value
            or proof.position_side != intent.position_side
            or proof.position_bucket != intent.position_bucket
            or proof.attributable_position_quantity < intent.quantity
            or record.intent_id != intent.intent_id
            or record.scope_key != intent.scope.key
            or record.payload_sha256 != intent.fingerprint
            or record.permit_reference != permit.permit_id
            or record.state is not ExecutionState.ACKED
            or record.dispatch_attempts != 1
            or record.filled_quantity != 0
            or record.average_price is not None
            or record.cumulative_commission is not None
            or record.review_required is not False
            or record.unknown_reason is not None
            or not record.provider_order_id
            or dispatch.get("permit_id") != permit.permit_id
            or dispatch.get("risk_intent_id") != risk_intent.intent_id
            or dispatch.get("risk_claim_digest") != claim.claim_digest
            or dispatch.get("managed_intent_id") != intent.intent_id
            or command.state != "DISPATCHING"
            or command.permit_id != permit.permit_id
            or command.risk_intent_id != risk_intent.intent_id
            or command.canonical_payload.get("execution_intent_sha256")
            != intent.fingerprint
            or command.canonical_payload.get("risk_intent_sha256")
            != risk_intent.fingerprint
        ):
            raise CtpRiskDispatchError("RECOVERY_ZERO_FILL_ACK_BINDING_INVALID")

        expected_recovery_payload = {
            "parent_intent_id": proof.parent_intent_id,
            "parent_permit_id": proof.parent_permit_id,
            "source_evidence_sha256": proof.source_evidence_sha256,
            "source_proof_sha256": proof.fingerprint,
            "source_revision": proof.source_revision,
        }
        if command.canonical_payload.get("recovery_close") != expected_recovery_payload:
            raise CtpRiskDispatchError("RECOVERY_ZERO_FILL_ACK_BINDING_INVALID")

        try:
            receipt = self._gate.dispatch_close_resolution_receipt(permit.permit_id)
        except Exception:
            raise CtpRiskDispatchError("RECOVERY_CLOSE_RECEIPT_READBACK_FAILED") from None
        if (
            type(receipt) is not DispatchExposureResolutionReceipt
            or receipt.status
            is not DispatchExposureResolutionReceiptStatus.UNRESOLVED_ACTIVE
            or receipt.claim != claim
            or receipt.dispatch_latch_active is not True
            or receipt.proof_sha256 is not None
            or receipt.exposure_proof_sha256 is not None
            or receipt.final_state is not None
            or receipt.requested_quantity is not None
            or receipt.filled_quantity is not None
            or receipt.trade_count is not None
            or receipt.order_revision is not None
            or receipt.journal_revision is not None
            or receipt.exposure_reservation_id is not None
            or receipt.exposure_reservation_sha256 is not None
            or receipt.position_effect != "CLOSE"
            or receipt.position_side != intent.position_side
            or receipt.position_bucket != intent.position_bucket
        ):
            raise CtpRiskDispatchError("RECOVERY_CLOSE_RECEIPT_READBACK_MISMATCH")

    def read_tracked_update_review(
        self,
        record: ExecutionRecord,
        *,
        writer_lease: WriterLease,
    ) -> ExecutionEvent:
        """Read back this bridge's exact positive-fill review latch.

        This narrow capability does not resolve or release Risk exposure. It
        permits the runtime to keep ingesting callback-backed facts for an
        ACKED_TRACKED order while its full reservation remains counted.
        """

        self._assert_open_thread()
        owner = self._require_owner()
        if (
            type(record) is not ExecutionRecord
            or type(writer_lease) is not WriterLease
            or writer_lease is not owner._writer_lease
        ):
            raise CtpRiskDispatchError("TRACKED_UPDATE_REVIEW_BINDING_INVALID")
        owner._assert_worker_thread()
        owner._assert_active_owner()
        owner._store.assert_writer_lease(owner._scope, writer_lease)
        state = self._permits.get(record.permit_reference or "")
        source_event = (
            None if state is None else state.tracked_update_review_source_event
        )
        if (
            state is None
            or state.state != "ACKED_TRACKED"
            or type(source_event) is not ExecutionEvent
            or type(state.tracked_proof) is not DispatchTrackedOrderProof
            or type(state.tracked_receipt) is not DispatchResolutionReceipt
            or type(state.tracked_event) is not RiskOrderJournalEvent
            or record.scope_key != owner._scope.key
            or record.intent_id != state.intent.intent_id
            or record.payload_sha256 != state.intent.fingerprint
            or record.permit_reference != state.permit.permit_id
            or record.review_required is not True
            or record.cumulative_commission is not None
            or record.state not in {
                ExecutionState.PARTIALLY_FILLED,
                ExecutionState.FILLED,
                ExecutionState.CANCELLED,
            }
            or record.filled_quantity <= 0
            or record.provider_order_id != state.tracked_proof.provider_order_id
            or source_event.event_type
            not in {"provider_observation", "reconciled_observation"}
            or source_event.intent_id != record.intent_id
            or source_event.scope_key != record.scope_key
            or source_event.state
            not in {
                ExecutionState.PARTIALLY_FILLED,
                ExecutionState.FILLED,
                ExecutionState.CANCELLED,
            }
            or (
                source_event.state is ExecutionState.CANCELLED
                and record.state is not ExecutionState.CANCELLED
            )
            or record.state is not source_event.state
        ):
            raise CtpRiskDispatchError("TRACKED_UPDATE_REVIEW_BINDING_INVALID")
        source_fill_text = source_event.payload.get("filled_quantity")
        if type(source_fill_text) is not str:
            raise CtpRiskDispatchError("TRACKED_UPDATE_REVIEW_BINDING_INVALID")
        try:
            source_fill_quantity = Decimal(source_fill_text)
        except Exception:
            raise CtpRiskDispatchError("TRACKED_UPDATE_REVIEW_BINDING_INVALID") from None
        if (
            not source_fill_quantity.is_finite()
            or source_fill_quantity <= 0
            or source_fill_quantity != record.filled_quantity
            or source_event.payload.get("provider_order_id")
            != record.provider_order_id
            or source_event.payload.get("average_price")
            != (
                None
                if record.average_price is None
                else format(record.average_price, "f")
            )
            or source_event.payload.get("cumulative_commission") is not None
        ):
            raise CtpRiskDispatchError("TRACKED_UPDATE_REVIEW_BINDING_INVALID")
        try:
            intent = self._read_exact_execution_event(owner, record, source_event)
            events = owner._store.read_outbox(
                after_sequence=source_event.sequence - 1,
                limit=2,
                scope=owner._scope,
            )
        except Exception:
            raise CtpRiskDispatchError("TRACKED_UPDATE_REVIEW_READBACK_FAILED") from None
        if (
            not _same_order_intent(state.intent, intent)
            or len(events) != 2
            or events[0] != source_event
            or source_event.payload.get("provider_order_id") != record.provider_order_id
            or source_event.payload.get("source") not in {"provider", "reconcile"}
        ):
            raise CtpRiskDispatchError("TRACKED_UPDATE_REVIEW_READBACK_MISMATCH")
        review_event = events[1]
        if (
            type(review_event) is not ExecutionEvent
            or review_event.sequence != source_event.sequence + 1
            or review_event.intent_id != record.intent_id
            or review_event.scope_key != record.scope_key
            or review_event.event_type != "manual_review_required"
            # The source and review rows must be the adjacent pair written for
            # this exact latest observation. This keeps quantity and terminal
            # state aligned with the current positive-fill, unknown-fee row.
            or review_event.state is not source_event.state
            or dict(review_event.payload)
            != {"reason_code": "evidenced_observation_reconciliation_failed"}
            or owner._store.get(record.intent_id, scope=owner._scope) != record
        ):
            raise CtpRiskDispatchError("TRACKED_UPDATE_REVIEW_READBACK_MISMATCH")

        proof = state.tracked_proof
        receipt = self._gate.dispatch_resolution_receipt(state.permit.permit_id)
        command = self._journal.get_command(
            self._account_scope, state.principal_ref, state.intent.intent_id
        )
        tracked_event = self._journal.get_event(
            self._account_scope,
            state.principal_ref,
            state.intent.intent_id,
            state.tracked_event.revision,
        )
        if (
            type(receipt) is not DispatchResolutionReceipt
            or not _same_dispatch_resolution_receipt(
                receipt, state.tracked_receipt
            )
            or receipt.status
            is not DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
            or receipt.dispatch_latch_active is not False
            or receipt.proof != proof
            or receipt.proof_sha256 != proof.fingerprint
            or command is None
            or command.state != "ACKED_TRACKED"
            or command.permit_id != state.permit.permit_id
            or command.risk_intent_id != state.risk_intent_id
            or tracked_event != state.tracked_event
            or tracked_event.event_type != "ACKED_TRACKED"
            or not self._journal.has_resolved_ack_history(
                self._fence,
                principal_ref=state.principal_ref,
                intent_id=state.intent.intent_id,
                receipt=receipt,
            )
        ):
            raise CtpRiskDispatchError("TRACKED_RISK_RECEIPT_READBACK_MISMATCH")
        return review_event

    def _read_exact_execution_event(
        self, owner: Any, record: ExecutionRecord, event: ExecutionEvent
    ) -> OrderIntent:
        """Bind callback arguments to the current single-event outbox read."""

        page = owner._store.read_execution_outbox_page(
            (owner._scope,), after_sequence=event.sequence - 1, limit=1
        )
        if page.events != (event,):
            raise CtpRiskDispatchError("EXECUTION_EVENT_READBACK_MISMATCH")
        matching = tuple(
            item
            for item in page.intents
            if item.record.intent_id == record.intent_id
        )
        if (
            len(matching) != 1
            or matching[0].record != record
            or matching[0].intent.scope != owner._scope
            or matching[0].intent.intent_id != record.intent_id
            or matching[0].intent.fingerprint != record.payload_sha256
        ):
            raise CtpRiskDispatchError("EXECUTION_RECORD_READBACK_MISMATCH")
        return matching[0].intent

    def close(self) -> None:
        """Release only this bridge's account journal fence on its owner thread."""

        self._assert_thread()
        if self._closed:
            return
        if self._close_uncertain:
            raise CtpRiskDispatchError("RISK_WRITER_FENCE_CLOSE_UNCERTAIN")
        if self._fence_cm is None:
            raise CtpRiskDispatchError("RISK_WRITER_FENCE_UNAVAILABLE")
        try:
            self._fence_cm.__exit__(None, None, None)
        except Exception:
            # A generator context may already be exhausted after a failing
            # exit. A second empty exit is not evidence that its lock released.
            self._close_uncertain = True
            raise CtpRiskDispatchError("RISK_WRITER_FENCE_CLOSE_UNCERTAIN") from None
        self._closed = True
        self._fence_cm = None

    def _wire_scope_payload(self, context: ExecutionAdmissionContextV1) -> dict[str, Any]:
        scope = context.to_payload().get("wire_scope")
        if type(scope) is not dict:
            raise CtpRiskDispatchError("WIRE_SCOPE_INVALID")
        return scope

    def _order_request(self, intent: OrderIntent, mapped: RiskIntent) -> dict[str, Any]:
        side = BtSide(intent.side.value.lower())
        order_type = BtOrderType(intent.order_type.value.lower())
        offset = {
            ("OPEN", "ALL"): "open",
            ("CLOSE", "ALL"): "close",
            ("CLOSE_TODAY", "TODAY"): "close_today",
            ("CLOSE_YESTERDAY", "YESTERDAY"): "close_yesterday",
        }.get((intent.position_effect.value, intent.position_bucket))
        if offset is None:
            raise CtpRiskDispatchError("CTP_POSITION_EFFECT_INVALID")
        request = OrderRequest(
            symbol=intent.instrument,
            side=side,
            order_type=order_type,
            quantity=intent.quantity,
            account_id=self._account_scope.account_id,
            client_order_id=intent.intent_id,
            price=intent.price,
            time_in_force=intent.time_in_force.value,
            reduce_only=intent.reduce_only,
            idempotency_key=intent.intent_id,
            quantity_unit=mapped.quantity_unit,
            position_side=intent.position_side,
            offset=offset,
        )
        return request.to_dict()

    def _claim_payload(self, claim: DispatchClaimBinding) -> dict[str, Any]:
        return {
            "cause_id": claim.cause_id,
            "claim_digest": claim.claim_digest,
            "claimed_at": claim.claimed_at,
            "intent_hash": claim.intent_hash,
            "intent_id": claim.intent_id,
            "permit_id": claim.permit_id,
            "scope_key": claim.scope.key,
        }

    def _readback_claim(
        self, state: _PermitState, *, expected_state: str = "CLAIMED"
    ) -> None:
        """Recheck the current Risk latch and its exact durable CLAIMED event."""

        claim = state.claim
        event_written = state.claim_event
        if (
            state.state != expected_state
            or claim is None
            or type(event_written) is not RiskOrderJournalEvent
            or not _sha256(event_written.record_sha256)
        ):
            raise CtpRiskDispatchError("RISK_CLAIM_UNAVAILABLE")
        try:
            current_claim = self._gate.dispatch_claim_binding(state.permit.permit_id)
            event = self._journal.get_event(
                self._account_scope,
                state.principal_ref,
                state.intent.intent_id,
                event_written.revision,
            )
        except Exception:
            raise CtpRiskDispatchError("RISK_CLAIM_READBACK_FAILED") from None
        expected_payload = {
            "claim": self._claim_payload(claim),
            "permit_id": state.permit.permit_id,
            "risk_intent_id": state.risk_intent_id,
        }
        updated_at_ns = state.claim_event_updated_at_ns
        if type(updated_at_ns) is not int or updated_at_ns <= 0:
            raise CtpRiskDispatchError("RISK_CLAIM_READBACK_MISMATCH")
        expected_event_payload = {
            **expected_payload,
            "updated_at_ns": updated_at_ns,
        }
        if (
            type(current_claim) is not DispatchClaimBinding
            or current_claim != claim
            or type(event) is not RiskOrderJournalEvent
            or event.scope != self._account_scope
            or event.principal_ref != state.principal_ref
            or event.intent_id != state.intent.intent_id
            or event.revision != event_written.revision
            or event.event_type != "CLAIMED"
            or type(event_written.payload) is not dict
            or set(event_written.payload) != set(expected_event_payload)
            or event_written.payload != expected_event_payload
            or type(event.payload) is not dict
            or set(event.payload) != set(expected_event_payload)
            or event.payload != event_written.payload
            or event.payload != expected_event_payload
            or event.record_sha256 != event_written.record_sha256
            or event.writer_owner_id != self._fence.owner_id
            or event.writer_fencing_token != self._fence.fencing_token
            or event.writer_fence_sha256 != self._fence.fence_sha256
        ):
            raise CtpRiskDispatchError("RISK_CLAIM_READBACK_MISMATCH")

    def _require_permit(self, permit_reference: str, intent: OrderIntent) -> _PermitState:
        if type(permit_reference) is not str or type(intent) is not OrderIntent:
            raise CtpRiskDispatchError("RISK_PERMIT_IDENTITY_INVALID")
        state = self._permits.get(permit_reference)
        if (
            state is None
            or state.intent is not intent
            or state.intent.fingerprint != intent.fingerprint
            or state.risk_intent.intent_id != intent.intent_id
        ):
            raise CtpRiskDispatchError("RISK_PERMIT_IDENTITY_MISMATCH")
        return state

    def _require_owner(self) -> Any:
        if self._owner is None:
            raise CtpRiskDispatchError("CTP_OWNER_UNBOUND")
        return self._owner

    def _assert_thread(self) -> None:
        if current_thread() is not self._thread:
            raise ContractValidationError("CTP Risk bridge is confined to its creating thread")

    def _assert_open_thread(self) -> None:
        self._assert_thread()
        if self._closed:
            raise CtpRiskDispatchError("CTP_RISK_BRIDGE_CLOSED")


def _sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _same_dispatch_resolution_receipt(
    current: object, issued: object
) -> bool:
    """Compare immutable ACK proof fields, excluding the live freeze summary.

    ``has_other_active_freezes`` is recalculated from the account's other
    active freezes on every Risk read. It is a current account projection,
    not part of the immutable order claim/proof receipt. Require a real bool
    on both snapshots while binding every other receipt field exactly.
    """

    return (
        type(current) is DispatchResolutionReceipt
        and type(issued) is DispatchResolutionReceipt
        and current.status is issued.status
        and current.claim == issued.claim
        and current.proof == issued.proof
        and current.proof_sha256 == issued.proof_sha256
        and current.dispatch_latch_active is issued.dispatch_latch_active
        and type(current.has_other_active_freezes) is bool
        and type(issued.has_other_active_freezes) is bool
        and current.recovery_attestation == issued.recovery_attestation
    )


def _same_order_intent(left: object, right: object) -> bool:
    """Compare Store-reconstructed intents by exact typed durable value."""

    return (
        type(left) is OrderIntent
        and type(right) is OrderIntent
        and left.fingerprint == right.fingerprint
        and left.to_payload() == right.to_payload()
    )


_WIRE_SCOPE_FIELDS = frozenset(
    {
        "execution_domain_ref",
        "account_ref",
        "account_refs",
        "strategy_ref",
        "run_id",
        "manual_book_ref",
        "portfolio_ref",
        "group_id",
        "leg_id",
    }
)


def _valid_wire_scope_payload(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != _WIRE_SCOPE_FIELDS:
        return False
    if type(value.get("execution_domain_ref")) is not str or not value["execution_domain_ref"]:
        return False
    account_refs = value.get("account_refs")
    if type(account_refs) not in (list, tuple) or any(
        type(item) is not str or not item for item in account_refs
    ):
        return False
    return all(
        value.get(field_name) is None
        or (type(value.get(field_name)) is str and bool(value.get(field_name)))
        for field_name in _WIRE_SCOPE_FIELDS - {"execution_domain_ref", "account_refs"}
    )


def _wire_scope_payload_from_value(value: object) -> dict[str, Any]:
    from bt_api_base.service_protocol import WireScope

    if type(value) is WireScope:
        return {
            "execution_domain_ref": value.execution_domain_ref,
            "account_ref": value.account_ref,
            "account_refs": list(value.account_refs),
            "strategy_ref": value.strategy_ref,
            "run_id": value.run_id,
            "manual_book_ref": value.manual_book_ref,
            "portfolio_ref": value.portfolio_ref,
            "group_id": value.group_id,
            "leg_id": value.leg_id,
        }
    if not _valid_wire_scope_payload(value):
        raise CtpRiskDispatchError("WIRE_SCOPE_INVALID")
    return {
        field_name: (
            list(value[field_name])
            if field_name == "account_refs"
            else value[field_name]
        )
        for field_name in _WIRE_SCOPE_FIELDS
    }


def _evidence_class(scope: AccountScope) -> DispatchEvidenceClass:
    return (
        DispatchEvidenceClass.SIMULATION_JOURNAL
        if scope.provider.casefold() in {"fake", "fixture"}
        else DispatchEvidenceClass.NATIVE_PROVIDER_JOURNAL
    )


__all__ = ["CtpRiskDispatchBridge", "CtpRiskDispatchError"]
