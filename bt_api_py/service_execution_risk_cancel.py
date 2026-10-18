"""Risk owner adapter for exact-run durable Execution cancellation.

This adapter reserves and claims a zero-notional ``CANCEL`` intent in the
existing Risk owner, then resolves only that cancellation-action latch from
the exact persisted Execution cancellation and target-order source.  It never
clears the original order exposure or an unrelated account freeze.
"""

from __future__ import annotations

import re
from decimal import Decimal
from bt_api_base.service_protocol import WireScope
from bt_api_execution import (
    AdmissionClaimNotCreated,
    CancelDispatchClaimReceiptV1,
    CancelIntent,
    ExecutionScope,
    ExecutionState,
    SqliteExecutionStore,
)
from bt_api_execution.store import CancelRecord
from bt_api_risk import (
    AccountScope,
    CancelDispatchResolutionReceipt,
    CancelDispatchResolutionReceiptStatus,
    DispatchClaimBinding,
    DispatchResolutionReceipt,
    DispatchResolutionReceiptStatus,
    DispatchTrackedOrderProof,
    DurableRiskGate,
    IntentAction,
    PermitInvalidError,
    RiskIntent,
    RiskPermit,
)

from .service_risk_order_journal import RiskOrderJournal, RiskOrderJournalError

_OWNER_CANCEL_TAGS = frozenset(
    {
        "owner_cancel_binding_version",
        "owner_cancel_batch_key",
        "owner_cancel_command_id",
        "owner_cancel_principal_ref",
        "owner_cancel_actor_ref",
        "owner_cancel_strategy_ref",
        "owner_cancel_run_id",
    }
)
_FINITE_REASON = re.compile(r"^[a-z][a-z0-9_]{0,95}$")


class RiskExecutionCancelAdmissionError(PermissionError):
    """Finite fail-closed projection for a cancellation Risk owner failure."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or _FINITE_REASON.fullmatch(code) is None:
            raise ValueError("invalid cancellation admission error")
        self.code = code
        super().__init__(code)


class RiskExecutionCancelAdmissionGate:
    """Use the configured Risk owner for exact strategy/run cancellation."""

    def __init__(
        self,
        gate: DurableRiskGate,
        journal: RiskOrderJournal,
        store: SqliteExecutionStore,
        *,
        risk_scope: AccountScope,
        execution_scope: ExecutionScope,
        wire_scope_template: WireScope,
    ) -> None:
        if (
            type(gate) is not DurableRiskGate
            or type(journal) is not RiskOrderJournal
            or type(store) is not SqliteExecutionStore
            or type(risk_scope) is not AccountScope
            or type(execution_scope) is not ExecutionScope
            or type(wire_scope_template) is not WireScope
            or getattr(gate, "_execution_journal_authority", None) is not journal
            or risk_scope.provider != execution_scope.provider
            or risk_scope.environment != execution_scope.environment
            or wire_scope_template.run_id is not None
            or wire_scope_template.account_ref != execution_scope.account_ref
            or wire_scope_template.strategy_ref != execution_scope.strategy_id
            or wire_scope_template.strategy_ref is None
            or wire_scope_template.account_refs
            or wire_scope_template.manual_book_ref is not None
            or wire_scope_template.group_id is not None
            or wire_scope_template.leg_id is not None
        ):
            raise RiskExecutionCancelAdmissionError("cancel_owner_binding_invalid")
        self._gate = gate
        self._journal = journal
        self._store = store
        self._risk_scope = risk_scope
        self._execution_scope = execution_scope
        self._wire_scope_template = wire_scope_template
        try:
            journal.bind_cancel_resolution_source(
                risk_scope, execution_scope, wire_scope_template, store
            )
        except Exception:
            raise RiskExecutionCancelAdmissionError("cancel_owner_source_bind_failed") from None

    def reserve(self, intent: CancelIntent) -> RiskPermit:
        risk_intent = self._risk_intent(intent)
        try:
            permit = self._gate.reserve(risk_intent)
        except Exception:
            raise RiskExecutionCancelAdmissionError("cancel_risk_reservation_denied") from None
        if (
            type(permit) is not RiskPermit
            or permit.scope != self._risk_scope
            or permit.action is not IntentAction.CANCEL
            or permit.notional != Decimal("0")
            or permit.intent_id != risk_intent.intent_id
        ):
            raise RiskExecutionCancelAdmissionError("cancel_risk_permit_invalid")
        return permit

    def validate(self, permit_reference: str, intent: CancelIntent) -> RiskPermit:
        risk_intent = self._risk_intent(intent)
        self._require_permit_reference(permit_reference)
        try:
            permit = self._gate.validate_permit(permit_reference, risk_intent)
        except Exception:
            raise RiskExecutionCancelAdmissionError("cancel_risk_permit_invalid") from None
        if (
            type(permit) is not RiskPermit
            or permit.permit_id != permit_reference
            or permit.intent_id != risk_intent.intent_id
            or permit.scope != self._risk_scope
            or permit.action is not IntentAction.CANCEL
            or permit.notional != Decimal("0")
        ):
            raise RiskExecutionCancelAdmissionError("cancel_risk_permit_invalid")
        return permit

    def claim_before_dispatch(
        self, permit_reference: str, intent: CancelIntent
    ) -> CancelDispatchClaimReceiptV1:
        risk_intent = self._risk_intent(intent)
        self._require_permit_reference(permit_reference)
        try:
            permit = self._gate.claim_for_dispatch(permit_reference, risk_intent)
        except PermitInvalidError as exc:
            if getattr(exc, "code", None) != "PERMIT_NOT_ACTIVE":
                raise RiskExecutionCancelAdmissionError(
                    "cancel_risk_claim_unknown"
                ) from None
            # A TTL expiry is a releasable no-claim outcome only when the
            # durable owner query independently proves that no claim exists.
            # If post-commit validation failed, or the readback is uncertain,
            # preserve UNKNOWN and the Risk latch instead.
            try:
                self._gate.dispatch_claim_binding(permit_reference)
            except PermitInvalidError as readback_error:
                if getattr(readback_error, "code", None) == "PERMIT_NOT_DISPATCH_CLAIMED":
                    raise AdmissionClaimNotCreated(
                        "cancel_risk_permit_expired_before_claim"
                    ) from None
            except Exception:
                pass
            raise RiskExecutionCancelAdmissionError(
                "cancel_risk_claim_unknown"
            ) from None
        except Exception:
            raise RiskExecutionCancelAdmissionError("cancel_risk_claim_unknown") from None
        try:
            claim = self._gate.dispatch_claim_binding(permit_reference)
        except Exception:
            raise RiskExecutionCancelAdmissionError("cancel_risk_claim_unknown") from None
        if (
            type(permit) is not RiskPermit
            or type(claim) is not DispatchClaimBinding
            or permit.permit_id != permit_reference
            or claim.permit_id != permit_reference
            or claim.scope != self._risk_scope
            or claim.intent_id != risk_intent.intent_id
            or claim.intent_hash != risk_intent.fingerprint
            or claim.cause_id != "dispatch-inflight:" + risk_intent.intent_id
        ):
            raise RiskExecutionCancelAdmissionError("cancel_risk_claim_unknown")
        return CancelDispatchClaimReceiptV1(
            permit_reference=permit_reference,
            scope_key=intent.scope.key,
            cancel_id=intent.cancel_id,
            intent_fingerprint=intent.fingerprint,
        )

    def verify_dispatch_claim_receipt(
        self,
        intent: CancelIntent,
        claim_receipt: CancelDispatchClaimReceiptV1,
    ) -> DispatchClaimBinding:
        """Read back one active Risk claim for the exact cancellation receipt.

        The returned binding is read-only evidence that Risk has an active
        dispatch latch. It does not authorize a native call by itself; callers
        must still satisfy the downstream CTP TTL, exact-session, and final
        Store fence immediately before dispatch.
        """

        risk_intent = self._risk_intent(intent)
        if type(claim_receipt) is not CancelDispatchClaimReceiptV1:
            raise RiskExecutionCancelAdmissionError("cancel_risk_receipt_invalid")
        permit_reference = claim_receipt.permit_reference
        self._require_permit_reference(permit_reference)
        if (
            claim_receipt.scope_key != intent.scope.key
            or claim_receipt.cancel_id != intent.cancel_id
            or claim_receipt.intent_fingerprint != intent.fingerprint
        ):
            raise RiskExecutionCancelAdmissionError(
                "cancel_risk_receipt_scope_mismatch"
            )
        try:
            binding = self._gate.dispatch_claim_binding(permit_reference)
        except Exception:
            raise RiskExecutionCancelAdmissionError("cancel_risk_claim_unknown") from None
        if (
            type(binding) is not DispatchClaimBinding
            or binding.permit_id != permit_reference
            or binding.scope != self._risk_scope
            or binding.intent_id != risk_intent.intent_id
            or binding.intent_hash != risk_intent.fingerprint
            or binding.cause_id != "dispatch-inflight:" + risk_intent.intent_id
        ):
            raise RiskExecutionCancelAdmissionError("cancel_risk_claim_unknown")
        return binding

    def settle(self, permit_reference: str) -> RiskPermit:
        self._require_permit_reference(permit_reference)
        try:
            permit = self._gate.settle(permit_reference)
        except Exception:
            raise RiskExecutionCancelAdmissionError("cancel_risk_settlement_unknown") from None
        if (
            type(permit) is not RiskPermit
            or permit.permit_id != permit_reference
            or permit.scope != self._risk_scope
            or permit.action is not IntentAction.CANCEL
            or permit.notional != Decimal("0")
        ):
            raise RiskExecutionCancelAdmissionError("cancel_risk_settlement_unknown")
        return permit

    def release(self, permit_reference: str, reason: str) -> None:
        self._require_permit_reference(permit_reference)
        if type(reason) is not str or _FINITE_REASON.fullmatch(reason) is None:
            raise RiskExecutionCancelAdmissionError("cancel_risk_release_reason_invalid")
        try:
            self._gate.release(permit_reference, reason)
        except Exception:
            raise RiskExecutionCancelAdmissionError("cancel_risk_release_failed") from None

    def resolve_terminal(
        self,
        permit_reference: str,
        intent: CancelIntent,
        record: CancelRecord,
        _writer_lease: object,
    ) -> CancelDispatchResolutionReceipt:
        """Resolve or verify one persisted terminal cancel; never dispatches."""

        self._require_permit_reference(permit_reference)
        self._risk_intent(intent)
        if (
            type(record) is not CancelRecord
            or record.permit_reference != permit_reference
            or record.cancel_id != intent.cancel_id
            or record.scope_key != self._execution_scope.key
            or record.state not in {ExecutionState.CANCELLED, ExecutionState.REJECTED}
            or record.dispatch_attempts != 1
        ):
            raise RiskExecutionCancelAdmissionError("cancel_terminal_source_invalid")
        try:
            persisted = self._store.read_cancel_resolution_source_by_key_v1(
                intent.cancel_id, scope_key=self._execution_scope.key
            )
            if persisted.cancel_intent != intent or persisted.cancel_record != record:
                raise RiskOrderJournalError("CANCEL_TERMINAL_SOURCE_CHANGED")
            receipt = self._gate.cancel_dispatch_resolution_receipt(permit_reference)
            if type(receipt) is not CancelDispatchResolutionReceipt:
                raise RiskOrderJournalError("CANCEL_RISK_RECEIPT_INVALID")
            self._validate_cancel_receipt(receipt, permit_reference, intent)
            if receipt.status is CancelDispatchResolutionReceiptStatus.TERMINAL_RESOLVED:
                proof = receipt.proof
                if proof is None:
                    raise RiskOrderJournalError("CANCEL_RISK_RECEIPT_INVALID")
                with self._journal.writer_fence(self._risk_scope):
                    if not self._journal.verify_cancel_dispatch_resolution_source(
                        proof, claim=receipt.claim
                    ):
                        raise RiskOrderJournalError("CANCEL_RESOLVED_SOURCE_MISMATCH")
                return receipt
            if receipt.status is not CancelDispatchResolutionReceiptStatus.UNRESOLVED_ACTIVE:
                raise RiskOrderJournalError("CANCEL_RISK_RECEIPT_INVALID")
            claim = receipt.claim
            with self._journal.writer_fence(self._risk_scope) as fence:
                target = self._journal.read_cancel_resolution_target_binding(
                    fence,
                    execution_scope_key=self._execution_scope.key,
                    cancel_id=intent.cancel_id,
                )
                target_receipt = self._gate.dispatch_resolution_receipt(target.permit_id)
                if (
                    type(target_receipt) is not DispatchResolutionReceipt
                    or target_receipt.status is not DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
                    or type(target_receipt.proof) is not DispatchTrackedOrderProof
                    or target_receipt.proof_sha256 != target_receipt.proof.fingerprint
                    or target_receipt.dispatch_latch_active
                    or target_receipt.claim.permit_id != target.permit_id
                    or target_receipt.claim.intent_id != target.risk_intent_id
                    or target_receipt.proof.provider_order_id != intent.provider_order_id
                    or not self._journal.has_resolved_ack_history(
                        fence,
                        principal_ref=target.principal_ref,
                        intent_id=target.target_intent_id,
                        receipt=target_receipt,
                    )
                ):
                    raise RiskOrderJournalError("CANCEL_TARGET_RISK_RECEIPT_UNRESOLVED")
                proof = self._journal.create_cancel_dispatch_resolution_proof(
                    fence,
                    claim=claim,
                    target_claim=target_receipt.claim,
                    target_order_proof=target_receipt.proof,
                    execution_scope_key=self._execution_scope.key,
                    cancel_id=intent.cancel_id,
                )
                self._gate.resolve_cancel_dispatch_freeze(proof)
                resolved = self._gate.cancel_dispatch_resolution_receipt(permit_reference)
                if (
                    type(resolved) is not CancelDispatchResolutionReceipt
                    or resolved.status is not CancelDispatchResolutionReceiptStatus.TERMINAL_RESOLVED
                    or resolved.claim != claim
                    or resolved.proof != proof
                    or resolved.proof_sha256 != proof.fingerprint
                    or resolved.dispatch_latch_active
                    or not self._journal.verify_cancel_dispatch_resolution_source(
                        proof, claim=claim
                    )
                ):
                    raise RiskOrderJournalError("CANCEL_RESOLUTION_READBACK_FAILED")
                return resolved
        except RiskExecutionCancelAdmissionError:
            raise
        except Exception:
            raise RiskExecutionCancelAdmissionError("cancel_terminal_resolution_unknown") from None

    def _risk_intent(self, intent: CancelIntent) -> RiskIntent:
        if type(intent) is not CancelIntent or intent.scope != self._execution_scope:
            raise RiskExecutionCancelAdmissionError("cancel_owner_scope_mismatch")
        tags = intent.tags
        if (
            set(tags) != _OWNER_CANCEL_TAGS
            or tags.get("owner_cancel_binding_version") != "1"
            or tags.get("owner_cancel_strategy_ref") != self._wire_scope_template.strategy_ref
            or type(tags.get("owner_cancel_run_id")) is not str
            or not tags["owner_cancel_run_id"]
            or any(
                type(tags.get(key)) is not str or not tags[key]
                for key in (
                    "owner_cancel_batch_key",
                    "owner_cancel_command_id",
                    "owner_cancel_principal_ref",
                    "owner_cancel_actor_ref",
                )
            )
        ):
            raise RiskExecutionCancelAdmissionError("cancel_owner_scope_mismatch")
        try:
            return RiskIntent(
                intent_id="cancel-admission:" + intent.scope.key + ":" + intent.cancel_id,
                scope=self._risk_scope,
                action=IntentAction.CANCEL,
                notional=Decimal("0"),
                payload_fingerprint=intent.fingerprint,
            )
        except Exception:
            raise RiskExecutionCancelAdmissionError("cancel_risk_intent_invalid") from None

    @staticmethod
    def _require_permit_reference(permit_reference: str) -> None:
        if (
            type(permit_reference) is not str
            or not permit_reference
            or permit_reference != permit_reference.strip()
            or len(permit_reference) > 128
        ):
            raise RiskExecutionCancelAdmissionError("cancel_risk_permit_invalid")

    def _validate_cancel_receipt(
        self,
        receipt: CancelDispatchResolutionReceipt,
        permit_reference: str,
        intent: CancelIntent,
    ) -> None:
        risk_intent = self._risk_intent(intent)
        if (
            type(receipt) is not CancelDispatchResolutionReceipt
            or type(receipt.claim) is not DispatchClaimBinding
            or receipt.claim.permit_id != permit_reference
            or receipt.claim.scope != self._risk_scope
            or receipt.claim.intent_id != risk_intent.intent_id
            or receipt.claim.intent_hash != risk_intent.fingerprint
            or receipt.claim.cause_id != "dispatch-inflight:" + risk_intent.intent_id
        ):
            raise RiskExecutionCancelAdmissionError("cancel_risk_receipt_scope_mismatch")
        if receipt.status is CancelDispatchResolutionReceiptStatus.UNRESOLVED_ACTIVE:
            if not receipt.dispatch_latch_active or receipt.proof is not None:
                raise RiskExecutionCancelAdmissionError("cancel_risk_receipt_invalid")
            return
        proof = receipt.proof
        if (
            receipt.status is not CancelDispatchResolutionReceiptStatus.TERMINAL_RESOLVED
            or proof is None
            or receipt.dispatch_latch_active
            or proof.cancel_intent_fingerprint != intent.fingerprint
            or proof.execution_scope_key != intent.scope.key
            or proof.cancel_id != intent.cancel_id
            or proof.target_intent_id != intent.target_intent_id
            or proof.provider_order_id != intent.provider_order_id
        ):
            raise RiskExecutionCancelAdmissionError("cancel_risk_receipt_invalid")
__all__ = ["RiskExecutionCancelAdmissionError", "RiskExecutionCancelAdmissionGate"]
