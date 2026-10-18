"""Bind Execution cancellation dispatch to the durable Risk claim receipt."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireScope
from bt_api_execution import (
    CancelDispatchClaimReceiptV1,
    CancelIntent,
    CancelObservation,
    ContractValidationError,
    ExecutionScope,
    ExecutionState,
    ManagedCancellationFacade,
    ManagedExecutionFacade,
    OrderIntent,
    ProviderObservation,
    Side,
    SqliteExecutionStore,
)
from bt_api_py.service_execution_risk_cancel import (
    RiskExecutionCancelAdmissionError,
    RiskExecutionCancelAdmissionGate,
)
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from bt_api_risk import AccountScope, DurableRiskGate, RiskPolicy


def _cancel_intent(
    scope: ExecutionScope,
    cancel_id: str,
    *,
    provider_order_id: str = "provider-target-order",
) -> CancelIntent:
    return CancelIntent(
        cancel_id=cancel_id,
        scope=scope,
        target_intent_id="target-order",
        provider_order_id=provider_order_id,
        metadata_version="synthetic-v1",
        tags={
            "owner_cancel_binding_version": "1",
            "owner_cancel_batch_key": "batch-" + cancel_id,
            "owner_cancel_command_id": "command-" + cancel_id,
            "owner_cancel_principal_ref": "operator-principal",
            "owner_cancel_actor_ref": "operator-actor",
            "owner_cancel_strategy_ref": scope.strategy_id,
            "owner_cancel_run_id": "run-cancel-claim",
        },
    )


@contextmanager
def _risk_case(tmp_path):
    execution_scope = ExecutionScope(
        "PAPER",
        "simulation",
        "account-cancel-claim",
        "strategy-cancel-claim",
        "20261018",
    )
    risk_scope = AccountScope(
        "PAPER", execution_scope.account_ref, "simulation"
    )
    wire_scope = WireScope(
        "paper:cancel-claim",
        account_ref=execution_scope.account_ref,
        strategy_ref=execution_scope.strategy_id,
    )
    execution_store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    risk_gate = None
    try:
        target_owner = ManagedExecutionFacade(
            execution_store,
            execution_scope,
            writer_id="cancel-claim-target-owner",
            allow_unprotected=True,
        )
        target_intent = OrderIntent.limit(
            intent_id="target-order",
            scope=execution_scope,
            signal_id="signal-target-order",
            instrument="BTC-USDT",
            side=Side.BUY,
            quantity=Decimal("1"),
            price=Decimal("50000"),
            metadata_version="synthetic-v1",
        )
        target_owner.submit(
            target_intent,
            lambda intent: ProviderObservation.accepted(
                intent.intent_id, "provider-target-order"
            ),
        )
        journal = RiskOrderJournal(
            tmp_path / "journal.sqlite3", writer_id="cancel-claim-journal"
        )
        risk_clock = [100.0]
        risk_path = tmp_path / "risk.sqlite3"
        risk_gate = DurableRiskGate(
            risk_path,
            RiskPolicy(
                "cancel-claim-v1",
                Decimal("0"),
                0,
                permit_ttl_seconds=60.0,
                notional_unit="USD",
            ),
            clock=lambda: risk_clock[0],
            execution_journal_authority=journal,
        )
        admission = RiskExecutionCancelAdmissionGate(
            risk_gate,
            journal,
            execution_store,
            risk_scope=risk_scope,
            execution_scope=execution_scope,
            wire_scope_template=wire_scope,
        )
        facade = ManagedCancellationFacade(
            execution_store,
            execution_scope,
            acquire_writer_lease=target_owner.acquire_writer_lease,
            admission_gate=admission,
        )
        yield SimpleNamespace(
            execution_scope=execution_scope,
            execution_store=execution_store,
            target_owner=target_owner,
            risk_scope=risk_scope,
            risk_path=risk_path,
            risk_clock=risk_clock,
            risk_gate=risk_gate,
            journal=journal,
            admission=admission,
            facade=facade,
        )
    finally:
        if risk_gate is not None:
            risk_gate.close()
        execution_store.close()


def _risk_state(case):
    connection = sqlite3.connect(case.risk_path)
    try:
        connection.execute("PRAGMA query_only = ON")
        reservations = tuple(
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT permit_id, status FROM risk_reservations "
                "ORDER BY permit_id"
            )
        )
        claims = tuple(
            (str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]))
            for row in connection.execute(
                "SELECT permit_id, intent_id, intent_hash, scope_key, cause_id "
                "FROM risk_dispatch_claims ORDER BY permit_id"
            )
        )
        freezes = tuple(
            (str(row[0]), str(row[1]), int(row[2]))
            for row in connection.execute(
                "SELECT scope_key, cause_id, active FROM risk_freezes "
                "ORDER BY scope_key, cause_id"
            )
        )
        return reservations, claims, freezes
    finally:
        connection.close()


def _receipt(intent: CancelIntent, permit_reference: str) -> CancelDispatchClaimReceiptV1:
    return CancelDispatchClaimReceiptV1(
        permit_reference=permit_reference,
        scope_key=intent.scope.key,
        cancel_id=intent.cancel_id,
        intent_fingerprint=intent.fingerprint,
    )


class _ClaimAwareSyntheticDispatcher:
    """Synthetic provider edge; Risk verification remains production code."""

    def __init__(self, admission: RiskExecutionCancelAdmissionGate | None = None):
        self.admission = admission
        self.claim_calls: list[tuple[CancelIntent, object]] = []
        self.bindings = []
        self.legacy_calls: list[CancelIntent] = []

    def cancel_with_claim(
        self, intent: CancelIntent, claim_receipt: CancelDispatchClaimReceiptV1
    ) -> CancelObservation:
        self.claim_calls.append((intent, claim_receipt))
        if self.admission is None:
            raise AssertionError("test dispatcher must not be called without a Risk gate")
        self.bindings.append(
            self.admission.verify_dispatch_claim_receipt(intent, claim_receipt)
        )
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )

    def cancel(self, intent: CancelIntent) -> CancelObservation:
        self.legacy_calls.append(intent)
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


def _dispatch_ack(case, intent: CancelIntent, *, monkeypatch=None):
    dispatcher = _ClaimAwareSyntheticDispatcher(case.admission)
    issued_receipts = []
    if monkeypatch is not None:
        real_claim = case.admission.claim_before_dispatch

        def observe_real_claim(permit_reference, claimed_intent):
            receipt = real_claim(permit_reference, claimed_intent)
            issued_receipts.append(receipt)
            return receipt

        monkeypatch.setattr(case.admission, "claim_before_dispatch", observe_real_claim)
    record = case.facade.cancel(intent, dispatcher)
    return record, dispatcher, issued_receipts


@pytest.mark.unit
def test_facade_hands_exact_risk_claim_to_dispatcher_and_verification_is_read_only(
    tmp_path, monkeypatch
):
    with _risk_case(tmp_path) as case:
        intent = _cancel_intent(case.execution_scope, "cancel-claim-positive")
        record, dispatcher, issued_receipts = _dispatch_ack(
            case, intent, monkeypatch=monkeypatch
        )

        assert record.state is ExecutionState.ACKED
        assert record.dispatch_attempts == 1
        assert record.permit_reference is not None
        assert len(issued_receipts) == 1
        assert len(dispatcher.claim_calls) == 1
        passed_intent, passed_receipt = dispatcher.claim_calls[0]
        assert passed_intent == intent
        assert passed_receipt is issued_receipts[0]
        assert type(passed_receipt) is CancelDispatchClaimReceiptV1
        assert passed_receipt.permit_reference == record.permit_reference
        assert passed_receipt.scope_key == intent.scope.key
        assert passed_receipt.cancel_id == intent.cancel_id
        assert passed_receipt.intent_fingerprint == intent.fingerprint
        assert dispatcher.legacy_calls == []

        binding = dispatcher.bindings[0]
        risk_intent_id = (
            "cancel-admission:" + intent.scope.key + ":" + intent.cancel_id
        )
        assert binding.permit_id == record.permit_reference
        assert binding.scope == case.risk_scope
        assert binding.intent_id == risk_intent_id
        assert binding.intent_hash
        assert binding.cause_id == "dispatch-inflight:" + risk_intent_id
        first_state = _risk_state(case)
        assert len(first_state[0]) == 1
        assert first_state[0][0] == (record.permit_reference, "settled")
        assert len(first_state[1]) == 1
        assert first_state[1][0][0] == record.permit_reference
        assert (case.risk_scope.key, binding.cause_id, 1) in first_state[2]

        repeated_binding = case.admission.verify_dispatch_claim_receipt(
            intent, passed_receipt
        )
        assert repeated_binding == binding
        assert _risk_state(case) == first_state
        assert case.risk_gate.dispatch_claim_binding(record.permit_reference) == binding

        replay = case.facade.cancel(intent, dispatcher)
        assert replay == record
        assert len(dispatcher.claim_calls) == 1
        assert dispatcher.legacy_calls == []
        assert _risk_state(case) == first_state


@pytest.mark.unit
@pytest.mark.parametrize(
    ("variant", "expected_code"),
    (
        ("forged-receipt", "cancel_risk_receipt_invalid"),
        ("other-scope", "cancel_owner_scope_mismatch"),
        ("other-fingerprint", "cancel_risk_receipt_scope_mismatch"),
        ("swapped-active-permit", "cancel_risk_claim_unknown"),
    ),
    ids=("forged-receipt-type", "other-scope", "other-intent-fingerprint", "swapped-permit"),
)
def test_risk_claim_verifier_rejects_receipt_and_intent_substitution(
    tmp_path, variant, expected_code
):
    case_dir = tmp_path / variant
    case_dir.mkdir()
    with _risk_case(case_dir) as case:
        intent = _cancel_intent(case.execution_scope, "cancel-claim-source")
        record, _dispatcher, _issued = _dispatch_ack(case, intent)
        receipt = _receipt(intent, record.permit_reference)

        candidate_intent = intent
        candidate_receipt = receipt
        if variant == "forged-receipt":
            candidate_receipt = SimpleNamespace(
                permit_reference=receipt.permit_reference,
                scope_key=receipt.scope_key,
                cancel_id=receipt.cancel_id,
                intent_fingerprint=receipt.intent_fingerprint,
            )
        elif variant == "other-scope":
            other_scope = ExecutionScope(
                case.execution_scope.provider,
                case.execution_scope.environment,
                "other-account",
                case.execution_scope.strategy_id,
                case.execution_scope.trading_day,
            )
            candidate_intent = _cancel_intent(
                other_scope, intent.cancel_id
            )
        elif variant == "other-fingerprint":
            candidate_intent = replace(
                intent, provider_order_id="provider-substituted-order"
            )
        elif variant == "swapped-active-permit":
            other_intent = _cancel_intent(
                case.execution_scope, "cancel-claim-other-permit"
            )
            other_permit = case.admission.reserve(other_intent)
            other_receipt = case.admission.claim_before_dispatch(
                other_permit.permit_id, other_intent
            )
            candidate_receipt = replace(
                receipt, permit_reference=other_receipt.permit_reference
            )

        before = _risk_state(case)
        with pytest.raises(RiskExecutionCancelAdmissionError) as rejected:
            case.admission.verify_dispatch_claim_receipt(
                candidate_intent, candidate_receipt
            )
        assert rejected.value.code == expected_code
        assert _risk_state(case) == before


@pytest.mark.unit
@pytest.mark.parametrize("permit_state", ("missing", "unclaimed", "released"))
def test_risk_claim_verifier_requires_a_real_unresolved_dispatch_claim(
    tmp_path, permit_state
):
    case_dir = tmp_path / permit_state
    case_dir.mkdir()
    with _risk_case(case_dir) as case:
        intent = _cancel_intent(case.execution_scope, "cancel-claim-unclaimed")
        permit_reference = "missing-cancel-permit"
        if permit_state != "missing":
            permit = case.admission.reserve(intent)
            permit_reference = permit.permit_id
            if permit_state == "released":
                case.admission.release(permit_reference, "test_release_unclaimed")
        receipt = _receipt(intent, permit_reference)
        before = _risk_state(case)

        with pytest.raises(RiskExecutionCancelAdmissionError) as rejected:
            case.admission.verify_dispatch_claim_receipt(intent, receipt)

        assert rejected.value.code == "cancel_risk_claim_unknown"
        assert _risk_state(case) == before
        assert before[1] == ()
        if permit_state == "released":
            assert before[0] == ((permit_reference, "released"),)
        assert case.risk_gate.active_freeze_reasons(case.risk_scope) == []


@pytest.mark.unit
def test_claim_aware_dispatcher_cannot_fall_back_without_a_risk_claim(tmp_path):
    with _risk_case(tmp_path) as case:
        intent = _cancel_intent(case.execution_scope, "cancel-claim-missing")
        unprotected = ManagedCancellationFacade(
            case.execution_store,
            case.execution_scope,
            acquire_writer_lease=case.target_owner.acquire_writer_lease,
            allow_unprotected=True,
        )
        dispatcher = _ClaimAwareSyntheticDispatcher()

        record = unprotected.cancel(intent, dispatcher)

        assert record.state is ExecutionState.UNKNOWN
        assert record.dispatch_attempts == 1
        assert record.permit_reference is None
        assert dispatcher.claim_calls == []
        assert dispatcher.legacy_calls == []

