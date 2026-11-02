"""SDK composition for Risk journal and optional Execution cancellation sources."""
from __future__ import annotations

import threading
from contextlib import closing
from decimal import Decimal
from typing import Any

from bt_api_risk import (
    AccountScope, CancelDispatchResolutionProof, CancelDispatchSource,
    CancelDispatchTerminalState, CancelTargetPostcondition, DispatchClaimBinding,
    DispatchEvidenceClass, DispatchTrackedOrderProof,
)
from bt_api_risk.risk_order_journal import (
    _SHA256_LENGTH, _WIRE_SCOPE_KEYS, _claim_payload, _dispatch_claim_from_payload,
    _tracked_proof_from_events, canonical_json, sha256_text,
)
from bt_api_risk.risk_order_journal_contracts import (
    RiskOrderCommand, RiskOrderJournalError, RiskOrderJournalEvent, RiskOrderWriterFence,
)


class RiskExecutionJournalBridge:
    """Bind exact Execution source objects to a Risk journal instance."""

    def _init_cancel_resolution_sources(self):
        self._cancel_resolution_sources = {}
        self._cancel_resolution_sources_lock = threading.RLock()

    def bind_cancel_resolution_source(
        self,
        scope: AccountScope,
        execution_scope: object,
        wire_scope_template: object,
        store: object,
    ) -> None:
        """Bind one exact Execution scope/store for owner-cancel proof reads.

        This is a trusted composition hook.  The user-facing cancel request
        cannot select the store or the Risk account mapping.
        """

        try:
            from bt_api_execution import ExecutionScope, SqliteExecutionStore
            from bt_api_base.service_protocol import WireScope
        except Exception:
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_UNAVAILABLE") from None
        if (
            type(scope) is not AccountScope
            or type(execution_scope) is not ExecutionScope
            or type(wire_scope_template) is not WireScope
            or type(store) is not SqliteExecutionStore
            or scope.provider != execution_scope.provider
            or scope.environment != execution_scope.environment
            or wire_scope_template.run_id is not None
            or wire_scope_template.strategy_ref != execution_scope.strategy_id
            or wire_scope_template.account_ref != execution_scope.account_ref
            or wire_scope_template.account_refs
            or wire_scope_template.strategy_ref is None
            or wire_scope_template.manual_book_ref is not None
            or wire_scope_template.group_id is not None
            or wire_scope_template.leg_id is not None
        ):
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_BINDING_INVALID")
        key = (scope.key, execution_scope.key)
        with self._cancel_resolution_sources_lock:
            prior = self._cancel_resolution_sources.get(key)
            if prior is not None and (
                prior[0] is not execution_scope
                or prior[1] != wire_scope_template
                or prior[2] is not store
            ):
                raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_REBOUND")
            self._cancel_resolution_sources[key] = (
                execution_scope,
                wire_scope_template,
                store,
            )

    def _read_cancel_resolution_source(
        self,
        scope: AccountScope,
        *,
        execution_scope_key: str,
        cancel_id: str,
    ) -> object:
        try:
            from bt_api_execution import ExecutionScope, SqliteExecutionStore
        except Exception:
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_UNAVAILABLE") from None
        if (
            type(scope) is not AccountScope
            or type(execution_scope_key) is not str
            or not execution_scope_key
            or type(cancel_id) is not str
            or not cancel_id
        ):
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_IDENTITY_INVALID")
        with self._cancel_resolution_sources_lock:
            binding = self._cancel_resolution_sources.get((scope.key, execution_scope_key))
        if (
            binding is None
            or type(binding[0]) is not ExecutionScope
            or type(binding[2]) is not SqliteExecutionStore
        ):
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_NOT_BOUND")
        execution_scope, wire_scope_template, store = binding
        try:
            source = store.read_cancel_resolution_source_by_key_v1(
                cancel_id, scope_key=execution_scope_key
            )
        except Exception:
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_UNAVAILABLE") from None
        if (
            source.scope != execution_scope
            or source.scope.key != execution_scope_key
            or source.scope.provider != scope.provider
            or source.scope.environment != scope.environment
            or source.scope.account_ref != source.owner_batch_body["wire_scope"].get("account_ref")
            or source.scope.strategy_id != source.owner_batch_body["wire_scope"].get("strategy_ref")
        ):
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_SCOPE_MISMATCH")
        tags = source.cancel_intent.tags
        wire_scope = source.owner_batch_body["wire_scope"]
        if (
            type(wire_scope) is not dict
            or set(wire_scope) != _WIRE_SCOPE_KEYS
            or wire_scope.get("account_refs") != []
            or wire_scope.get("manual_book_ref") is not None
            or wire_scope.get("group_id") is not None
            or wire_scope.get("leg_id") is not None
            or wire_scope.get("strategy_ref") != tags.get("owner_cancel_strategy_ref")
            or wire_scope.get("run_id") != tags.get("owner_cancel_run_id")
            or type(wire_scope.get("strategy_ref")) is not str
            or not wire_scope["strategy_ref"]
            or type(wire_scope.get("run_id")) is not str
            or not wire_scope["run_id"]
            or wire_scope.get("execution_domain_ref")
            != wire_scope_template.execution_domain_ref
            or wire_scope.get("account_ref") != wire_scope_template.account_ref
            or wire_scope.get("account_refs") != list(wire_scope_template.account_refs)
            or wire_scope.get("strategy_ref") != wire_scope_template.strategy_ref
            or wire_scope.get("manual_book_ref") != wire_scope_template.manual_book_ref
            or wire_scope.get("portfolio_ref") != wire_scope_template.portfolio_ref
            or wire_scope.get("group_id") != wire_scope_template.group_id
            or wire_scope.get("leg_id") != wire_scope_template.leg_id
            or source.cancel_record.permit_reference is None
            or source.cancel_record.state.value not in {"CANCELLED", "REJECTED"}
            or source.cancel_record.dispatch_attempts != 1
        ):
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_BINDING_MISMATCH")
        return source

    def _read_cancel_target_risk_source(
        self, fence: RiskOrderWriterFence, source: object
    ) -> tuple[RiskOrderCommand, DispatchTrackedOrderProof, DispatchClaimBinding]:
        from bt_api_execution.store import CancelResolutionSourceV1

        if type(source) is not CancelResolutionSourceV1:
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_INVALID")
        wire_scope = source.owner_batch_body["wire_scope"]
        wire_scope_json = canonical_json(dict(wire_scope))
        with closing(self._connection()) as connection:
            self._assert_current_in(connection, fence)
            rows = connection.execute(
                "SELECT * FROM risk_order_commands WHERE scope_key=? AND intent_id=? "
                "AND wire_scope_json=? LIMIT 2",
                (fence.scope.key, source.target_intent.intent_id, wire_scope_json),
            ).fetchall()
            if len(rows) != 1:
                raise RiskOrderJournalError("CANCEL_TARGET_RISK_COMMAND_AMBIGUOUS")
            row = rows[0]
            command = self._command_from_row(connection, row, fence.scope)
            event_rows = connection.execute(
                "SELECT * FROM risk_order_events WHERE scope_key=? AND principal_ref=? "
                "AND intent_id=? ORDER BY revision LIMIT 257",
                (fence.scope.key, command.principal_ref, command.intent_id),
            ).fetchall()
            if not event_rows or len(event_rows) > 256:
                raise RiskOrderJournalError("CANCEL_TARGET_RISK_HISTORY_UNAVAILABLE")
            events = tuple(self._event_from_row(item, fence.scope) for item in event_rows)
            head = connection.execute(
                "SELECT revision FROM risk_order_events WHERE scope_key=? "
                "ORDER BY revision DESC LIMIT 1",
                (fence.scope.key,),
            ).fetchone()
            if (
                head is None
                or command.scope != fence.scope
                or command.intent_id != source.target_intent.intent_id
                or command.wire_scope != dict(wire_scope)
                or not self._verify_event_chains(
                    connection, row, fence.scope, int(head["revision"]),
                    target_revision=events[-1].revision,
                )
            ):
                raise RiskOrderJournalError("CANCEL_TARGET_RISK_CHAIN_INVALID")
        by_type: dict[str, list[RiskOrderJournalEvent]] = {}
        for event in events:
            by_type.setdefault(event.event_type, []).append(event)
        reserved = by_type.get("RESERVED", [])
        claimed = by_type.get("CLAIMED", [])
        dispatching = by_type.get("DISPATCHING", [])
        proof_events = by_type.get("PROOF_READY", [])
        acknowledgements = by_type.get("ACKED_TRACKED", [])
        if (
            len(reserved) != 1
            or len(claimed) != 1
            or len(dispatching) != 1
            or len(proof_events) != 1
            or len(acknowledgements) != 1
        ):
            raise RiskOrderJournalError("CANCEL_TARGET_RISK_PROOF_INCOMPLETE")
        claim_payload = claimed[0].payload
        nested_claim = claim_payload.get("claim")
        target_claim = _dispatch_claim_from_payload(fence.scope, nested_claim)
        proof_event = proof_events[0]
        target_proof = _tracked_proof_from_events(command, target_claim, proof_event)
        ack = acknowledgements[0]
        ack_payload = dict(ack.payload)
        expected_ack_fields = {
            "claim_digest",
            "dispatch_attempt_count",
            "exposure_reference_id",
            "exposure_reference_sha256",
            "filled_quantity",
            "permit_id",
            "proof_ready_record_sha256",
            "proof_ready_revision",
            "proof_sha256",
            "provider_order_id",
            "public_state",
            "recovery_attestation_sha256",
            "reconciliation_evidence_sha256",
            "risk_intent_id",
            "trade_count",
            "updated_at_ns",
        }
        recovery_attestation_sha256 = ack_payload.get("recovery_attestation_sha256")
        target_payload = command.canonical_payload
        if target_payload.get("schema") not in (None, ""):
            profile = self._risk_dispatch_profile(target_claim.scope.provider)
            target_intent_matches = bool(
                profile.target_order_schema == target_payload.get("schema")
                and profile.validate_target_order is not None
                and profile.validate_target_order(
                    target_payload, target_intent=source.target_intent, target_claim=target_claim,
                )
            )
        else:
            target_intent_matches = (
                target_payload.get("instrument") == source.target_intent.instrument
            )
        if (
            set(ack_payload) != expected_ack_fields
            or
            command.permit_id != target_proof.permit_id
            or command.risk_intent_id != target_claim.intent_id
            or target_proof.provider_order_id != source.cancel_intent.provider_order_id
            or target_proof.accepted_request_sha256 != command.order_request_sha256
            or not target_intent_matches
            or reserved[0].revision >= claimed[0].revision
            or claimed[0].revision >= dispatching[0].revision
            or dispatching[0].revision >= proof_event.revision
            or proof_event.revision >= ack.revision
            or claim_payload.get("claim") != _claim_payload(target_claim)
            or claim_payload.get("permit_id") != target_claim.permit_id
            or claim_payload.get("risk_intent_id") != target_claim.intent_id
            or reserved[0].payload.get("permit_id") != target_claim.permit_id
            or reserved[0].payload.get("risk_intent_id") != target_claim.intent_id
            or dispatching[0].payload.get("dispatch_attempt_count") != 1
            or proof_event.payload.get("claim") != _claim_payload(target_claim)
            or proof_event.payload.get("provider_order_id") != source.cancel_intent.provider_order_id
            or ack_payload.get("claim_digest") != target_claim.claim_digest
            or ack_payload.get("dispatch_attempt_count") != 1
            or ack_payload.get("exposure_reference_id") != target_proof.exposure_reservation_id
            or ack_payload.get("exposure_reference_sha256") != target_proof.exposure_reservation_sha256
            or ack_payload.get("filled_quantity") != 0
            or ack_payload.get("permit_id") != target_claim.permit_id
            or ack_payload.get("proof_ready_record_sha256") != proof_event.record_sha256
            or ack_payload.get("proof_ready_revision") != proof_event.revision
            or ack_payload.get("proof_sha256") != target_proof.fingerprint
            or ack_payload.get("provider_order_id") != source.cancel_intent.provider_order_id
            or ack_payload.get("public_state") != "ACKED_TRACKED"
            or (
                recovery_attestation_sha256 is not None
                and (
                    type(recovery_attestation_sha256) is not str
                    or len(recovery_attestation_sha256) != _SHA256_LENGTH
                    or any(char not in "0123456789abcdef" for char in recovery_attestation_sha256)
                )
            )
            or ack_payload.get("reconciliation_evidence_sha256")
            != target_proof.reconciliation_evidence_sha256
            or ack_payload.get("risk_intent_id") != target_claim.intent_id
            or ack_payload.get("trade_count") != 0
            or type(ack_payload.get("updated_at_ns")) is not int
            or ack_payload.get("updated_at_ns") <= 0
            or command.state not in {"ACKED_TRACKED", "PROVIDER_QUERY", "EXPOSURE_APPLIED", "UNKNOWN"}
        ):
            raise RiskOrderJournalError("CANCEL_TARGET_RISK_BINDING_MISMATCH")
        return command, target_proof, target_claim

    def _build_cancel_dispatch_resolution_proof(
        self,
        fence: RiskOrderWriterFence,
        *,
        claim: DispatchClaimBinding,
        source: object,
        target_order_proof: DispatchTrackedOrderProof,
        writer_owner_id: str | None = None,
        writer_fencing_token: int | None = None,
        writer_fence_sha256: str | None = None,
    ) -> CancelDispatchResolutionProof:
        from bt_api_execution import ExecutionState
        from bt_api_execution.store import CancelResolutionSourceV1
        from bt_api_risk import IntentAction, RiskIntent

        if type(source) is not CancelResolutionSourceV1:
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_INVALID")
        cancel_intent = source.cancel_intent
        expected_intent_id = "cancel-admission:" + source.scope.key + ":" + cancel_intent.cancel_id
        risk_intent = RiskIntent(
            intent_id=expected_intent_id,
            scope=fence.scope,
            action=IntentAction.CANCEL,
            notional=Decimal("0"),
            payload_fingerprint=cancel_intent.fingerprint,
        )
        if (
            claim.scope != fence.scope
            or claim.intent_id != expected_intent_id
            or claim.intent_hash != risk_intent.fingerprint
            or claim.cause_id != "dispatch-inflight:" + expected_intent_id
            or source.cancel_record.permit_reference != claim.permit_id
            or source.cancel_record.state not in {ExecutionState.CANCELLED, ExecutionState.REJECTED}
            or source.cancel_record.dispatch_attempts != 1
            or target_order_proof.provider_order_id != cancel_intent.provider_order_id
            or target_order_proof.intent_id == claim.intent_id
        ):
            raise RiskOrderJournalError("CANCEL_DISPATCH_PROOF_IDENTITY_MISMATCH")
        from bt_api_execution.cancel_observation_projection import terminal_cancel_observation

        cancel_event = terminal_cancel_observation(
            source, cancel_intent, error_type=RiskOrderJournalError,
        )
        target_event = source.target_events[-1]
        terminal_state = (
            CancelDispatchTerminalState.CANCELLED
            if source.cancel_record.state is ExecutionState.CANCELLED
            else CancelDispatchTerminalState.REJECTED
        )
        target_postcondition = (
            CancelTargetPostcondition.TARGET_TERMINAL
            if terminal_state is CancelDispatchTerminalState.CANCELLED
            else CancelTargetPostcondition.TARGET_REMAINS_OPEN
        )
        if (
            terminal_state is CancelDispatchTerminalState.CANCELLED
            and source.target_record.state is not ExecutionState.CANCELLED
        ) or (
            terminal_state is CancelDispatchTerminalState.REJECTED
            and source.target_record.state not in {ExecutionState.ACKED, ExecutionState.PARTIALLY_FILLED}
        ):
            raise RiskOrderJournalError("CANCEL_TARGET_POSTCONDITION_INVALID")
        writer_owner_id = fence.owner_id if writer_owner_id is None else writer_owner_id
        writer_fencing_token = (
            fence.fencing_token if writer_fencing_token is None else writer_fencing_token
        )
        writer_fence_sha256 = (
            fence.fence_sha256 if writer_fence_sha256 is None else writer_fence_sha256
        )
        cancel_event_sha256 = sha256_text(canonical_json(_cancel_event_payload(cancel_event)))
        cancel_history_sha256 = sha256_text(
            canonical_json([_cancel_event_payload(event) for event in source.cancel_events])
        )
        target_event_sha256 = sha256_text(canonical_json(_execution_event_payload(target_event)))
        target_record_sha256 = sha256_text(
            canonical_json(_execution_record_payload(source.target_record))
        )
        evidence_class = (
            DispatchEvidenceClass.SIMULATION_JOURNAL
            if fence.scope.provider.lower() in {"fake", "fixture", "paper"}
            else DispatchEvidenceClass.NATIVE_PROVIDER_JOURNAL
        )
        source_digest = sha256_text(
            canonical_json(
                {
                    "cancel_event_sha256": cancel_event_sha256,
                    "cancel_history_sha256": cancel_history_sha256,
                    "cancel_intent": cancel_intent.to_payload(),
                    "cancel_record": _cancel_record_payload(source.cancel_record),
                    "claim": _claim_payload(claim),
                    "execution_scope": source.scope.to_payload(),
                    "owner_batch_body": dict(source.owner_batch_body),
                    "owner_batch_sha256": source.owner_batch_sha256,
                    "target_event_sha256": target_event_sha256,
                    "target_intent": source.target_intent.to_payload(),
                    "target_order_proof_sha256": target_order_proof.fingerprint,
                    "target_record_sha256": target_record_sha256,
                    "schema": "bt-api-risk-cancel-execution-source-v1",
                }
            )
        )
        return CancelDispatchResolutionProof(
            scope=fence.scope,
            permit_id=claim.permit_id,
            intent_id=claim.intent_id,
            intent_hash=claim.intent_hash,
            cause_id=claim.cause_id,
            claim_digest=claim.claim_digest,
            evidence_class=evidence_class,
            dispatch_attempt_count=1,
            cancel_intent_fingerprint=cancel_intent.fingerprint,
            execution_scope_key=source.scope.key,
            cancel_id=cancel_intent.cancel_id,
            target_intent_id=source.target_intent.intent_id,
            provider_order_id=cancel_intent.provider_order_id,
            terminal_state=terminal_state,
            cancel_event_id=cancel_event.event_id,
            cancel_event_sequence=cancel_event.sequence,
            cancel_event_sha256=cancel_event_sha256,
            cancel_source=CancelDispatchSource(str(cancel_event.payload["source"])),
            source_evidence_sha256=source_digest,
            target_postcondition=target_postcondition,
            target_state=source.target_record.state.value,
            target_filled_quantity=_cancel_decimal_text(source.target_record.filled_quantity),
            target_updated_at_ns=source.target_record.updated_at_ns,
            target_record_sha256=target_record_sha256,
            target_event_id=target_event.event_id,
            target_event_sequence=target_event.sequence,
            target_event_type=target_event.event_type,
            target_event_sha256=target_event_sha256,
            writer_owner_id=writer_owner_id,
            writer_fencing_token=writer_fencing_token,
            writer_fence_sha256=writer_fence_sha256,
        )


def _cancel_event_payload(event: object) -> dict[str, Any]:
    from bt_api_execution.source_projection import ExecutionSourceProjectionError, cancel_event_payload

    try:
        return cancel_event_payload(event)
    except ExecutionSourceProjectionError as exc:
        raise RiskOrderJournalError(exc.code) from None

def _execution_event_payload(event: object) -> dict[str, Any]:
    from bt_api_execution.source_projection import ExecutionSourceProjectionError, execution_event_payload

    try:
        return execution_event_payload(event)
    except ExecutionSourceProjectionError as exc:
        raise RiskOrderJournalError(exc.code) from None

def _execution_record_payload(record: object) -> dict[str, Any]:
    from bt_api_execution.source_projection import ExecutionSourceProjectionError, execution_record_payload

    try:
        return execution_record_payload(record)
    except ExecutionSourceProjectionError as exc:
        raise RiskOrderJournalError(exc.code) from None

def _cancel_record_payload(record: object) -> dict[str, Any]:
    from bt_api_execution.source_projection import ExecutionSourceProjectionError, cancel_record_payload

    try:
        return cancel_record_payload(record)
    except ExecutionSourceProjectionError as exc:
        raise RiskOrderJournalError(exc.code) from None

def _cancel_decimal_text(value: object) -> str:
    from bt_api_execution.source_projection import ExecutionSourceProjectionError, cancel_decimal_text

    try:
        return cancel_decimal_text(value)
    except ExecutionSourceProjectionError as exc:
        raise RiskOrderJournalError(exc.code) from None
