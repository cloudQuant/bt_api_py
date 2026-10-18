"""Journal-contract checks for a CTP terminal no-fill Risk resolution.

The CTP evidence below is an explicitly synthetic DTO fixture.  It exercises
the journal's typed source-chain and Gate-receipt checks; it does not claim to
verify a live TraderClient or native callback source.  Production composition
must retain and re-read the exact CTP reader-issued evidence before resolving
Risk, then re-read the Gate receipt before appending ACKED_TERMINAL.
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

import pytest

from bt_api_risk import (
    AccountScope,
    DispatchEvidenceClass,
    DispatchTerminalProof,
    DispatchTerminalResolutionReceiptStatus,
    DispatchTerminalState,
    DurableRiskGate,
    IntentAction,
    PermitInvalidError,
    RiskIntent,
    RiskPolicy,
)
from bt_api_py.service_risk_order_journal import (
    RiskOrderJournal,
    RiskOrderJournalError,
    canonical_json,
    sha256_text,
)


_RISK_SCOPE = AccountScope("ctp", "terminal-fixture-account", "simnow")
_PRINCIPAL = "terminal-fixture-principal"
_MANAGED_INTENT_ID = "managed-ctp-intent-1"
_RISK_INTENT_ID = "risk-mapped-intent-1"
_EXECUTION_SCOPE_KEY = "execution-scope:ctp:terminal-fixture-account:strategy-1"
_ACCOUNT_KEY = "ctp-account-key-1"
_TRADING_DAY = "20261018"
_INSTRUMENT = "rb2710"
_EXCHANGE = "SHFE"


def _claim_payload(claim) -> dict[str, object]:
    return {
        "cause_id": claim.cause_id,
        "claim_digest": claim.claim_digest,
        "claimed_at": claim.claimed_at,
        "intent_hash": claim.intent_hash,
        "intent_id": claim.intent_id,
        "permit_id": claim.permit_id,
        "scope_key": claim.scope.key,
    }


@contextmanager
def _prepared_dispatch(tmp_path):
    journal = RiskOrderJournal(
        str(tmp_path / "ctp-terminal-journal.sqlite3"),
        writer_id="ctp-terminal-journal-writer",
    )
    gate = DurableRiskGate(
        str(tmp_path / "ctp-terminal-risk.sqlite3"),
        RiskPolicy(
            "ctp-terminal-policy",
            Decimal("1000"),
            max_increase_count=1,
            notional_unit="USD",
        ),
        execution_journal_authority=journal,
    )
    risk_intent = RiskIntent(
        intent_id=_RISK_INTENT_ID,
        scope=_RISK_SCOPE,
        action=IntentAction.INCREASE,
        notional=Decimal("100"),
        payload_fingerprint="a" * 64,
        notional_unit="USD",
    )
    request = {
        "client_order_id": "ctp-terminal-client-order-1",
        "price": "100",
        "quantity": "1",
        "side": "BUY",
        "symbol": _INSTRUMENT,
    }
    canonical_payload = {
        "instrument_id": _INSTRUMENT,
        "position_effect": "OPEN",
        "quantity": "1",
        "side": "BUY",
    }
    request_digest = sha256_text(
        canonical_json(
            {
                "exchange_name": "CTP",
                "operation": "BtApi.make_order(normalized=True)",
                "request": request,
            }
        )
    )
    wire_scope = {
        "account_ref": "terminal-fixture-account",
        "account_refs": [],
        "execution_domain_ref": "ctp-execution-domain",
        "group_id": None,
        "leg_id": None,
        "manual_book_ref": None,
        "portfolio_ref": None,
        "run_id": "ctp-terminal-test-run",
        "strategy_ref": "strategy-1",
    }
    try:
        with journal.writer_fence(_RISK_SCOPE) as fence:
            command, created = journal.prepare_or_get(
                fence,
                principal_ref=_PRINCIPAL,
                intent_id=_MANAGED_INTENT_ID,
                risk_intent_id=_RISK_INTENT_ID,
                payload_sha256=sha256_text(canonical_json(canonical_payload)),
                canonical_payload=canonical_payload,
                wire_scope=wire_scope,
                exchange_name="CTP",
                provider_account_id="terminal-fixture-account",
                client_order_id=request["client_order_id"],
                order_request=request,
                order_request_sha256=request_digest,
            )
            assert created is True
            assert command.state == "PREPARED"

            permit = gate.reserve(risk_intent)
            gate.claim_for_dispatch(permit.permit_id, risk_intent)
            claim = gate.dispatch_claim_binding(permit.permit_id)
            gate.settle(permit.permit_id)
            assert claim.intent_id == _RISK_INTENT_ID

            journal.append_event(
                fence,
                principal_ref=_PRINCIPAL,
                intent_id=_MANAGED_INTENT_ID,
                event_type="RESERVED",
                payload={
                    "permit_id": permit.permit_id,
                    "risk_intent_id": _RISK_INTENT_ID,
                },
            )
            journal.append_event(
                fence,
                principal_ref=_PRINCIPAL,
                intent_id=_MANAGED_INTENT_ID,
                event_type="CLAIMED",
                payload={
                    "claim": _claim_payload(claim),
                    "permit_id": permit.permit_id,
                    # The mapped Risk intent id is the exact claim intent id.
                    "risk_intent_id": claim.intent_id,
                },
            )
            binding = {
                "schema": "ctp-risk-ctp-submit-binding-v1",
                "risk_intent_id": claim.intent_id,
                "permit_id": claim.permit_id,
                "risk_claim_digest": claim.claim_digest,
                "risk_intent_hash": claim.intent_hash,
                "submit_command_id": "ctp-command-1",
                "managed_intent_id": _MANAGED_INTENT_ID,
                "runtime_order_id": "runtime-order-1",
                "order_ref": "20261018-000001",
                "provider_order_id": None,
                "account_key": _ACCOUNT_KEY,
                "scope_key": _EXECUTION_SCOPE_KEY,
                "risk_scope_key": _RISK_SCOPE.key,
                "trading_day": _TRADING_DAY,
                "reservation_digest_sha256": "b" * 64,
                "callback_owner_intent_id": "ctp-owner-1",
                "session_generation_id": "session-generation-1",
                "session_binding_sha256": "c" * 64,
                "request_payload_sha256": "d" * 64,
                "native_request_payload_sha256": "e" * 64,
                "native_request_id": 17,
                "local_queue_receipt_id": "queue-receipt-1",
                "instrument_id": _INSTRUMENT,
                "exchange_id": _EXCHANGE,
            }
            journal.append_event(
                fence,
                principal_ref=_PRINCIPAL,
                intent_id=_MANAGED_INTENT_ID,
                event_type="DISPATCHING",
                payload={
                    "dispatch_attempt_count": 1,
                    "permit_id": permit.permit_id,
                    "risk_intent_id": claim.intent_id,
                    "ctp_dispatch_binding": binding,
                },
            )
            yield {
                "binding": binding,
                "claim": claim,
                "command": command,
                "fence": fence,
                "gate": gate,
                "journal": journal,
                "permit": permit,
            }
    finally:
        gate.close()


def _terminal_evidence(binding: dict[str, object], terminal_state: str) -> dict[str, object]:
    cancel_command_id = "ctp-cancel-command-1" if terminal_state == "CANCELLED" else None
    cancel_action_state = "ACKNOWLEDGED" if terminal_state == "CANCELLED" else None
    evidence: dict[str, object] = {
        "schema": "ctp_terminal_order_query_evidence.v1",
        "terminal_state": terminal_state,
        "account_key": binding["account_key"],
        "scope_key": binding["scope_key"],
        "trading_day": binding["trading_day"],
        "managed_intent_id": binding["managed_intent_id"],
        "runtime_order_id": binding["runtime_order_id"],
        "order_ref": binding["order_ref"],
        "submit_command_id": binding["submit_command_id"],
        "provider_order_id": "ctp-provider-order-1",
        "instrument_id": binding["instrument_id"],
        "exchange_id": binding["exchange_id"],
        "order_sys_id": "sys-order-1",
        "callback_owner_intent_id": binding["callback_owner_intent_id"],
        "session_generation_id": binding["session_generation_id"],
        "callback_stream_id": "callback-stream-1",
        "session_binding_sha256": binding["session_binding_sha256"],
        "order_source_sequence": 9,
        "terminal_ingress_record_digest_sha256": "f" * 64,
        "terminal_source_digest_sha256": "1" * 64,
        "request_id": 17,
        "front_id": 1,
        "session_id": 2,
        "quantity": "1",
        "volume_traded": "0",
        "filled_quantity": "0",
        "trade_fact_count": 0,
        "trade_notional": "0",
        "commission_quality": "INCOMPLETE",
        "cancel_command_id": cancel_command_id,
        "cancel_action_state": cancel_action_state,
        "reservation_digest_sha256": binding["reservation_digest_sha256"],
        "query_fence_id": "query-fence-1",
        "query_start_source_sequence": 10,
        "query_end_source_sequence": 12,
        "query_applied_source_sequence": 12,
        "query_request_id": 17,
        "query_filter_sha256": "2" * 64,
        "query_records_sha256": "3" * 64,
        "query_source_sha256": "4" * 64,
        "query_window_sha256": "5" * 64,
        "query_callback_ingress_digests": ["6" * 64, "7" * 64],
        "query_account_fingerprint_sha256": "8" * 64,
        "query_connection_generation": 3,
        "query_started_at_utc": "2026-10-18T00:00:00+00:00",
        "query_completed_at_utc": "2026-10-18T00:00:01+00:00",
        "query_started_monotonic": 100.0,
        "query_completed_monotonic": 101.0,
        "verified_at_ns": 1_000,
        "expires_at_ns": 2_000,
    }
    evidence["evidence_sha256"] = sha256_text(canonical_json(evidence))
    return evidence


def _append_proof_ready(ctx, evidence: dict[str, object]) -> tuple[DispatchTerminalProof, str]:
    journal = ctx["journal"]
    fence = ctx["fence"]
    claim = ctx["claim"]
    permit = ctx["permit"]
    binding = ctx["binding"]
    evidence_sha256 = str(evidence["evidence_sha256"])
    order_event = journal.append_event(
        fence,
        principal_ref=_PRINCIPAL,
        intent_id=_MANAGED_INTENT_ID,
        event_type="ORDER_OBSERVATION",
        payload={
            "schema": "ctp-risk-terminal-dispatch-order-observation-v1",
            "claim": _claim_payload(claim),
            "permit_id": permit.permit_id,
            "risk_intent_id": claim.intent_id,
            "dispatch_attempt_count": 1,
            "evidence_sha256": evidence_sha256,
            "ctp_terminal_evidence": evidence,
        },
    )
    fill_event = journal.append_event(
        fence,
        principal_ref=_PRINCIPAL,
        intent_id=_MANAGED_INTENT_ID,
        event_type="FILL_OBSERVATION",
        payload={
            "schema": "ctp-risk-terminal-dispatch-fill-observation-v1",
            "claim": _claim_payload(claim),
            "dispatch_attempt_count": 1,
            "permit_id": permit.permit_id,
            "risk_intent_id": claim.intent_id,
            "evidence_sha256": evidence_sha256,
            "commission_quality": "INCOMPLETE",
            "fee_history_complete": True,
            "fee_record_count": 0,
            "filled_quantity": 0,
            "records": [],
            "trade_count": 0,
        },
    )
    terminal_state = (
        DispatchTerminalState.CANCELED_NO_FILL
        if evidence["terminal_state"] == "CANCELLED"
        else DispatchTerminalState.REJECTED_NO_FILL
    )
    proof_event = journal.append_event(
        fence,
        principal_ref=_PRINCIPAL,
        intent_id=_MANAGED_INTENT_ID,
        event_type="PROOF_READY",
        payload={
            "schema": "ctp-risk-terminal-dispatch-proof-ready-v1",
            "cause_id": claim.cause_id,
            "claim": _claim_payload(claim),
            "claim_digest": claim.claim_digest,
            "dispatch_attempt_count": 1,
            "evidence_class": DispatchEvidenceClass.NATIVE_PROVIDER_JOURNAL.value,
            "fee_history_complete": True,
            "fee_record_count": 0,
            "filled_quantity": 0,
            "intent_hash": claim.intent_hash,
            "intent_id": claim.intent_id,
            "permit_id": permit.permit_id,
            "risk_intent_id": claim.intent_id,
            "terminal_state": terminal_state.value,
            "trade_count": 0,
            "reconciliation_evidence_sha256": evidence_sha256,
            "writer_fence_sha256": fence.fence_sha256,
            "order_observation_revision": order_event.revision,
            "order_observation_record_sha256": order_event.record_sha256,
            "fill_observation_revision": fill_event.revision,
            "fill_observation_record_sha256": fill_event.record_sha256,
        },
    )
    proof = DispatchTerminalProof(
        scope=_RISK_SCOPE,
        permit_id=permit.permit_id,
        intent_id=claim.intent_id,
        intent_hash=claim.intent_hash,
        cause_id=claim.cause_id,
        claim_digest=claim.claim_digest,
        evidence_class=DispatchEvidenceClass.NATIVE_PROVIDER_JOURNAL,
        terminal_state=terminal_state,
        dispatch_attempt_count=1,
        journal_revision=proof_event.revision,
        journal_record_sha256=proof_event.record_sha256,
        reconciliation_evidence_sha256=evidence_sha256,
        writer_fence_sha256=fence.fence_sha256,
        fee_record_count=0,
        fee_history_complete=True,
        filled_quantity=0,
        trade_count=0,
    )
    assert binding["risk_intent_id"] == proof.intent_id
    return proof, evidence_sha256


@pytest.mark.parametrize("terminal_state", ("CANCELLED", "REJECTED"))
def test_ctp_zero_fill_terminal_requires_typed_risk_receipt_before_ack(
    tmp_path,
    terminal_state: str,
) -> None:
    """Only exact source-chain, proof, and current Risk receipt reach ACKED_TERMINAL."""

    with _prepared_dispatch(tmp_path) as ctx:
        evidence = _terminal_evidence(ctx["binding"], terminal_state)
        proof, _evidence_sha256 = _append_proof_ready(ctx, evidence)
        journal = ctx["journal"]
        gate = ctx["gate"]
        fence = ctx["fence"]
        permit_id = ctx["permit"].permit_id

        before_ack = journal.get_command(_RISK_SCOPE, _PRINCIPAL, _MANAGED_INTENT_ID)
        assert before_ack is not None and before_ack.state == "PROOF_READY"
        with pytest.raises(RiskOrderJournalError) as generic_append:
            journal.append_event(
                fence,
                principal_ref=_PRINCIPAL,
                intent_id=_MANAGED_INTENT_ID,
                event_type="ACKED_TERMINAL",
                payload={"fixture": "must not bypass typed receipt"},
            )
        assert generic_append.value.code == "INVALID_EVENT_TYPE"
        assert journal.get_command(_RISK_SCOPE, _PRINCIPAL, _MANAGED_INTENT_ID) == before_ack

        receipt = gate.resolve_dispatch_freeze(proof)
        assert receipt.status is DispatchTerminalResolutionReceiptStatus.NO_FILL_TERMINAL_RESOLVED
        assert receipt.proof == proof
        assert receipt.dispatch_latch_active is False
        assert receipt.fee_record_count == 0
        assert receipt.fee_history_complete is True
        assert gate.dispatch_terminal_resolution_receipt(permit_id) == receipt

        acknowledged = journal.append_terminal_dispatch_ack(
            fence,
            principal_ref=_PRINCIPAL,
            intent_id=_MANAGED_INTENT_ID,
            receipt=receipt,
            proof=proof,
        )
        assert acknowledged.state == "ACKED_TERMINAL"
        revision = acknowledged.latest_revision
        replayed = journal.append_terminal_dispatch_ack(
            fence,
            principal_ref=_PRINCIPAL,
            intent_id=_MANAGED_INTENT_ID,
            receipt=receipt,
            proof=proof,
        )
        assert replayed == acknowledged
        assert replayed.latest_revision == revision
        assert journal.has_resolved_terminal_dispatch_history(
            fence,
            principal_ref=_PRINCIPAL,
            intent_id=_MANAGED_INTENT_ID,
            receipt=receipt,
        ) is True


def test_ctp_terminal_with_any_reported_fill_keeps_risk_dispatch_frozen(tmp_path) -> None:
    """An incomplete-fee CTP source cannot clear the latch after positive fills."""

    with _prepared_dispatch(tmp_path) as ctx:
        evidence = _terminal_evidence(ctx["binding"], "CANCELLED")
        evidence["volume_traded"] = "1"
        evidence["filled_quantity"] = "1"
        evidence["trade_fact_count"] = 1
        evidence["trade_notional"] = "100"
        evidence["evidence_sha256"] = sha256_text(
            canonical_json({key: value for key, value in evidence.items() if key != "evidence_sha256"})
        )
        proof, _evidence_sha256 = _append_proof_ready(ctx, evidence)
        gate = ctx["gate"]
        journal = ctx["journal"]
        fence = ctx["fence"]
        permit_id = ctx["permit"].permit_id

        with pytest.raises(PermitInvalidError):
            gate.resolve_dispatch_freeze(proof)

        receipt = gate.dispatch_terminal_resolution_receipt(permit_id)
        assert receipt.status is DispatchTerminalResolutionReceiptStatus.UNRESOLVED_ACTIVE
        assert receipt.dispatch_latch_active is True
        command = journal.get_command(_RISK_SCOPE, _PRINCIPAL, _MANAGED_INTENT_ID)
        assert command is not None and command.state == "PROOF_READY"
        assert journal.has_resolved_terminal_dispatch_history(
            fence,
            principal_ref=_PRINCIPAL,
            intent_id=_MANAGED_INTENT_ID,
            receipt=receipt,
        ) is False
