"""Durable owner-hold behavior for a mismatched Risk terminal proof receipt."""

from __future__ import annotations

import hashlib
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity
from bt_api_execution import (
    ExecutionState,
    GroupChildOrderStatus,
    GroupDispatchResolutionReceipt,
    GroupLegClaimReceipt,
    GroupLegOrderReceipt,
    GroupLegPermitReference,
    risk_account_scope_fingerprint,
)
from bt_api_risk import (
    AccountScope,
    DispatchEvidenceClass,
    DispatchTerminalProof,
    DispatchTerminalResolutionReceipt,
    DispatchTerminalResolutionReceiptStatus,
    DispatchTerminalState,
    DurableRiskGate,
    IntentAction,
    RiskIntent,
    RiskGroupLegIntent,
    RiskPolicy,
    VerifiedDispatchResolution,
)

from bt_api_py.service_execution_group_authority import (
    ExecutionGroupAuthority,
    ExecutionGroupAuthorityError,
    GroupAuthorityReconciliationRejected,
)
from bt_api_py.service_execution_group import (
    GROUP_ORDER_ENTRY_PORT,
    GROUP_ORDER_QUERY_PORT,
    ExecutionGroupOrderEntryService,
    ServiceExecutionGroupError,
)
from bt_api_py.service_registry import ActiveRegistrationContext
from tests.unit.test_service_execution_group_authority import (
    _authorization,
    _capture_registry_contexts,
    _execution_scope,
    _intent_and_binding,
    _register_and_dispatch,
    _writer_lease,
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


class _ExactSyntheticJournalAuthority:
    """Permit one exact source proof through Risk's normal verified API."""

    def __init__(self, proof: DispatchTerminalProof | None) -> None:
        self._proof = proof

    def register(self, proof: DispatchTerminalProof) -> None:
        if self._proof is not None:
            raise AssertionError("one exact terminal source proof is allowed")
        self._proof = proof

    @contextmanager
    def dispatch_resolution_guard(self, proof, *, claim):
        if (
            type(proof) is not DispatchTerminalProof
            or proof != self._proof
            or proof.scope != claim.scope
            or proof.permit_id != claim.permit_id
            or proof.intent_id != claim.intent_id
            or proof.intent_hash != claim.intent_hash
            or proof.claim_digest != claim.claim_digest
        ):
            yield None
            return
        yield VerifiedDispatchResolution(
            scope=claim.scope,
            permit_id=claim.permit_id,
            intent_id=claim.intent_id,
            intent_hash=claim.intent_hash,
            claim_digest=claim.claim_digest,
            proof_sha256=proof.fingerprint,
            journal_revision=proof.journal_revision,
            journal_record_sha256=proof.journal_record_sha256,
            writer_fence_sha256=proof.writer_fence_sha256,
        )


def test_mismatched_existing_risk_receipt_keeps_sqlite_authority_hold(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """An authentic Risk receipt cannot release a hold for another source hash."""

    registry, _contexts = _capture_registry_contexts(tmp_path / "registry.sqlite3")
    request.addfinalizer(registry.close)
    execution_scope = _execution_scope()
    intent, original_binding = _intent_and_binding()
    writer_lease = _writer_lease()
    risk_scope = AccountScope("fixture", "provider-account-a", "simulation")
    policy = RiskPolicy(
        "owner-recovery-receipt-policy",
        Decimal("1000"),
        max_increase_count=4,
        require_strategy_allocation=True,
        notional_unit="USD",
    )
    profile = _sha("owner-recovery-exposure-profile")
    # Group admission requires trusted instrument facts with an interval that
    # covers the Risk reservation deadline.  This test exercises receipt-hash
    # mismatch handling, so supply a bounded synthetic facts window rather
    # than relying on Risk's deliberately fail-closed missing-facts default.
    facts_observed_at_ns = intent.deadline_ns - 86_400_000_000_000
    facts_valid_until_ns = intent.deadline_ns + 1_000_000_000
    reserved = RiskIntent(
        intent_id=intent.intent_id,
        scope=risk_scope,
        action=IntentAction.INCREASE,
        notional=Decimal("120"),
        payload_fingerprint=_sha("reserved-intent"),
        strategy_id=execution_scope.strategy_id,
        allocation_version="owner-recovery-v1",
        instrument=intent.instrument,
        quantity=Decimal("2"),
        quantity_unit="base",
        notional_unit="USD",
        exposure_profile_fingerprint=profile,
        instrument_facts_observed_at_ns=facts_observed_at_ns,
        instrument_facts_valid_until_ns=facts_valid_until_ns,
    )
    child = replace(
        reserved,
        notional=Decimal("60"),
        payload_fingerprint=_sha("actual-child-intent"),
        quantity=Decimal("1"),
    )
    risk_journal_authority = _ExactSyntheticJournalAuthority(None)
    risk_gate = DurableRiskGate(
        tmp_path / "risk.sqlite3",
        policy,
        execution_journal_authority=risk_journal_authority,
    )
    request.addfinalizer(risk_gate.close)
    risk_gate.set_strategy_allocation(
        risk_scope,
        execution_scope.strategy_id,
        "owner-recovery-v1",
        max_notional=Decimal("500"),
        notional_unit="USD",
    )
    group_vector = risk_gate.reserve_group_vector(
        risk_scope,
        original_binding.group_id,
        (RiskGroupLegIntent(original_binding.leg_id, reserved),),
        deadline_ns=intent.deadline_ns,
    )
    risk_leg = group_vector.legs[0]
    risk_claim = risk_gate.claim_group_leg(
        risk_scope,
        group_vector.group_id,
        risk_leg.leg_id,
        risk_leg.permit_id,
        child,
        expected_group_fingerprint=group_vector.group_fingerprint,
        expected_deadline_ns=group_vector.deadline_ns,
    )
    risk_gate.settle(risk_leg.permit_id)
    committed_risk_proof = DispatchTerminalProof(
        scope=risk_scope,
        permit_id=risk_leg.permit_id,
        intent_id=intent.intent_id,
        intent_hash=risk_leg.reserved_intent_hash,
        cause_id=risk_claim.claim.cause_id,
        claim_digest=risk_claim.claim.claim_digest,
        evidence_class=DispatchEvidenceClass.SIMULATION_JOURNAL,
        terminal_state=DispatchTerminalState.CANCELED_NO_FILL,
        dispatch_attempt_count=1,
        journal_revision=7,
        journal_record_sha256=_sha("risk-terminal-journal-row"),
        reconciliation_evidence_sha256=_sha("risk-terminal-reconciliation"),
        writer_fence_sha256=_sha("risk-terminal-writer-fence"),
        fee_record_count=0,
        fee_history_complete=True,
    )
    risk_journal_authority.register(committed_risk_proof)
    risk_receipt = risk_gate.resolve_dispatch_freeze(committed_risk_proof)
    assert type(risk_receipt) is DispatchTerminalResolutionReceipt
    assert (
        risk_receipt.status
        is DispatchTerminalResolutionReceiptStatus.NO_FILL_TERMINAL_RESOLVED
    )
    assert risk_receipt.proof == committed_risk_proof
    assert risk_receipt.proof_sha256 == committed_risk_proof.fingerprint
    assert risk_receipt.group_claim is not None and risk_receipt.group_claim.resolved
    persisted_risk_receipt = risk_gate.dispatch_terminal_resolution_receipt(risk_leg.permit_id)
    assert persisted_risk_receipt == risk_receipt

    # This represents a different immutable source-journal row. It is not
    # submitted to Risk and does not alter the authentic receipt above.
    source_proof = replace(
        committed_risk_proof,
        journal_revision=8,
        journal_record_sha256=_sha("different-source-journal-row"),
        reconciliation_evidence_sha256=_sha("different-source-reconciliation"),
        writer_fence_sha256=_sha("different-source-writer-fence"),
    )
    assert source_proof.fingerprint != persisted_risk_receipt.proof_sha256

    binding = replace(
        original_binding,
        risk_scope_fingerprint=risk_account_scope_fingerprint(risk_scope.key),
        permit_id=risk_leg.permit_id,
        group_fingerprint=group_vector.group_fingerprint,
        policy_fingerprint=policy.fingerprint,
        reserved_intent_hash=risk_leg.reserved_intent_hash,
        exposure_profile_fingerprint=risk_leg.exposure_profile_fingerprint,
        actual_child_intent_hash=risk_claim.actual_intent_hash,
        deadline_ns=group_vector.deadline_ns,
    )
    permit = GroupLegPermitReference(
        group_id=binding.group_id,
        leg_id=binding.leg_id,
        portfolio_ref=binding.portfolio_ref,
        strategy_id=binding.strategy_id,
        account_ref=binding.account_ref,
        coordinator_principal_ref=binding.coordinator_principal_ref,
        risk_scope_fingerprint=binding.risk_scope_fingerprint,
        permit_id=binding.permit_id,
        group_fingerprint=binding.group_fingerprint,
        policy_fingerprint=binding.policy_fingerprint,
        reserved_intent_hash=binding.reserved_intent_hash,
        exposure_profile_fingerprint=binding.exposure_profile_fingerprint,
        order_intent_hash=binding.order_intent_hash,
        deadline_ns=binding.deadline_ns,
    )
    claim = GroupLegClaimReceipt(
        permit=permit,
        actual_child_intent_hash=risk_claim.actual_intent_hash,
        claim_digest=risk_claim.claim.claim_digest,
        actual_quantity=risk_claim.actual_quantity,
        actual_notional=risk_claim.actual_notional,
    )
    status = GroupChildOrderStatus(
        intent_id=intent.intent_id,
        state=ExecutionState.CANCELLED,
        filled_quantity=Decimal("0"),
        provider_order_id="provider-cancelled-order",
        order_revision=7,
        trade_count=0,
    )
    resolution = GroupDispatchResolutionReceipt(
        claim=claim,
        resolution_kind="TERMINAL_NO_FILL_RESOLVED",
        proof_sha256=persisted_risk_receipt.proof_sha256,
        final_state=ExecutionState.CANCELLED,
        requested_quantity=child.quantity,
        filled_quantity=Decimal("0"),
        trade_count=0,
        order_revision=status.order_revision,
        journal_revision=persisted_risk_receipt.proof.journal_revision,
        exposure_proof_sha256=None,
        exposure_reservation_id=None,
        exposure_reservation_sha256=None,
        dispatch_latch_active=False,
        has_other_active_freezes=persisted_risk_receipt.has_other_active_freezes,
    )
    assert resolution.proof_sha256 == persisted_risk_receipt.proof_sha256
    assert resolution.proof_sha256 != source_proof.fingerprint
    assert persisted_risk_receipt.proof is not None
    presented_receipt = GroupLegOrderReceipt(status=status, claim=claim, resolution=resolution)

    def verify_same_source_hash(_hold, candidate: object) -> bool:
        current_risk_receipt = risk_gate.dispatch_terminal_resolution_receipt(
            risk_leg.permit_id
        )
        return (
            current_risk_receipt == persisted_risk_receipt
            and type(candidate) is GroupLegOrderReceipt
            and candidate.resolution is not None
            and candidate.resolution.proof_sha256 == current_risk_receipt.proof_sha256
            and candidate.resolution.proof_sha256 == source_proof.fingerprint
        )

    authority = ExecutionGroupAuthority(
        tmp_path / "group-authority.sqlite3",
        completion_verifier=lambda _hold: False,
        reconciliation_verifier=verify_same_source_hash,
        monotonic_clock_ns=lambda: 1,
    )
    request.addfinalizer(authority.close)
    context = _register_and_dispatch(registry, session="risk-proof-hash-mismatch")
    authority.control.activate(
        context,
        execution_scope=execution_scope,
        risk_account_scope_key=risk_scope.key,
    )
    with authority.guard.bind_service_call(context, execution_scope=execution_scope):
        with pytest.raises(RuntimeError, match="provider outcome remains unknown"):
            with _authorization(
                authority,
                context,
                intent=intent,
                binding=binding,
                lease=writer_lease,
            ):
                raise RuntimeError("provider outcome remains unknown")

        holds = authority.pending_holds()
        assert len(holds) == 1
        hold = holds[0]
        assert authority.guard.reconciliation_hold_state(hold.hold_id) == "pending"
        with pytest.raises(GroupAuthorityReconciliationRejected):
            authority.control.reconcile_hold(hold.hold_id, presented_receipt)
        assert authority.pending_holds() == holds
        assert authority.guard.reconciliation_hold_state(hold.hold_id) == "pending"
        assert risk_gate.dispatch_terminal_resolution_receipt(risk_leg.permit_id) == (
            persisted_risk_receipt
        )


def test_reconciliation_reader_pair_rejects_other_principal_scope_group_and_epoch(
    tmp_path: Path,
) -> None:
    """A read route can name an owner only within its stable Registry epoch."""

    registry, _contexts = _capture_registry_contexts(tmp_path / "registry.sqlite3")
    try:
        entry_context = _register_and_dispatch(registry, session="owner-recovery-pair")
        query_context = replace(entry_context, registration_id="query-registration-pair")
        execution_scope = _execution_scope()
        expires_at = time.time() + 60
        entry_registration = ActiveRegistrationContext(
            context=entry_context,
            instance_id="execution-owner",
            port="service.order_entry",
            scope_ref="portfolio-grant-a",
            operations=frozenset({"orders.submit_group_leg", "orders.resolve_group_leg"}),
            write_operations=frozenset(
                {"orders.submit_group_leg", "orders.resolve_group_leg"}
            ),
            service_epoch="owner-service-epoch-pair",
            policy_version="policy-v1",
            requirements_version="requirements-v1",
            expires_at=expires_at,
            strategy_write_run_id="owner-run-pair",
        )
        query_registration = ActiveRegistrationContext(
            context=query_context,
            instance_id="execution-owner",
            port="service.order_query",
            scope_ref="portfolio-query-grant-a",
            operations=frozenset({"orders.query_group_leg"}),
            write_operations=frozenset(),
            service_epoch=entry_registration.service_epoch,
            policy_version=entry_registration.policy_version,
            requirements_version=entry_registration.requirements_version,
            expires_at=expires_at,
            strategy_write_run_id=entry_registration.strategy_write_run_id,
        )
        authority = ExecutionGroupAuthority(tmp_path / "authority.sqlite3")
        try:
            with authority.guard.bind_service_call(
                query_context,
                execution_scope=execution_scope,
                reconciliation_registrations=(query_registration, entry_registration),
            ):
                pass

            cancel_entry_operations = entry_registration.operations | {
                "orders.cancel_group_leg"
            }
            cancel_query_operations = query_registration.operations | {
                "orders.query_group_cancel"
            }
            cancel_entry_registration = replace(
                entry_registration,
                operations=cancel_entry_operations,
                write_operations=cancel_entry_operations,
            )
            cancel_query_registration = replace(
                query_registration,
                operations=cancel_query_operations,
            )
            with authority.guard.bind_service_call(
                query_context,
                execution_scope=execution_scope,
                reconciliation_registrations=(
                    cancel_query_registration,
                    cancel_entry_registration,
                ),
            ):
                pass
            with pytest.raises(GroupAuthorityReconciliationRejected):
                with authority.guard.bind_service_call(
                    query_context,
                    execution_scope=execution_scope,
                    reconciliation_registrations=(
                        cancel_query_registration,
                        entry_registration,
                    ),
                ):
                    pass

            foreign_identity = replace(
                query_context.identity,
                principal_ref="different-owner-principal",
            )
            foreign_principal_context = replace(
                query_context,
                identity=foreign_identity,
                principal_ref=foreign_identity.principal_ref,
            )
            foreign_principal_query = replace(
                query_registration,
                context=foreign_principal_context,
            )
            with pytest.raises(GroupAuthorityReconciliationRejected):
                with authority.guard.bind_service_call(
                    foreign_principal_context,
                    execution_scope=execution_scope,
                    reconciliation_registrations=(
                        foreign_principal_query,
                        entry_registration,
                    ),
                ):
                    pass

            foreign_scope = replace(query_context.scope, account_ref="different-owner-account")
            foreign_scope_context = replace(query_context, scope=foreign_scope)
            foreign_scope_query = replace(query_registration, context=foreign_scope_context)
            foreign_execution_scope = replace(
                execution_scope,
                account_ref="different-owner-account",
            )
            with pytest.raises(GroupAuthorityReconciliationRejected):
                with authority.guard.bind_service_call(
                    foreign_scope_context,
                    execution_scope=foreign_execution_scope,
                    reconciliation_registrations=(
                        foreign_scope_query,
                        entry_registration,
                    ),
                ):
                    pass

            foreign_group_scope = replace(
                query_context.scope,
                group_id="different-owner-group",
                leg_id="different-owner-leg",
            )
            foreign_group_context = replace(query_context, scope=foreign_group_scope)
            foreign_group_query = replace(query_registration, context=foreign_group_context)
            with pytest.raises(ExecutionGroupAuthorityError):
                with authority.guard.bind_service_call(
                    foreign_group_context,
                    execution_scope=execution_scope,
                    reconciliation_registrations=(
                        foreign_group_query,
                        entry_registration,
                    ),
                ):
                    pass

            restarted_entry = replace(
                entry_registration,
                service_epoch="different-owner-service-epoch",
            )
            with pytest.raises(GroupAuthorityReconciliationRejected):
                with authority.guard.bind_service_call(
                    query_context,
                    execution_scope=execution_scope,
                    reconciliation_registrations=(query_registration, restarted_entry),
                ):
                    pass

            expired_query = replace(
                query_registration,
                expires_at=time.time() - 1,
            )
            with pytest.raises(GroupAuthorityReconciliationRejected):
                with authority.guard.bind_service_call(
                    query_context,
                    execution_scope=execution_scope,
                    reconciliation_registrations=(expired_query, entry_registration),
                ):
                    pass
        finally:
            authority.close()
    finally:
        registry.close()


@pytest.mark.parametrize(
    "changed_field",
    ("identity", "scope", "registration_id", "principal_ref"),
)
def test_query_pair_matches_result_callback_but_rejects_auth_context_changes(
    tmp_path: Path, changed_field: str
) -> None:
    """The read-size callback is per-call metadata; the four auth fields stay exact."""

    registry, _contexts = _capture_registry_contexts(
        tmp_path / f"registry-{changed_field}.sqlite3"
    )
    authority = None
    try:
        live_entry_context = _register_and_dispatch(
            registry, session=f"owner-recovery-{changed_field}"
        )
        entry_context = replace(live_entry_context, result_fits=None)
        query_context = replace(
            entry_context, registration_id=f"query-registration-{changed_field}"
        )
        execution_scope = _execution_scope()
        expires_at = time.time() + 60
        service_epoch = f"owner-service-epoch-{changed_field}"
        policy_version = "policy-v1"
        requirements_version = "requirements-v1"
        entry_operations = frozenset(
            {"orders.submit_group_leg", "orders.resolve_group_leg"}
        )
        entry_registration = ActiveRegistrationContext(
            context=entry_context,
            instance_id="execution-owner",
            port=GROUP_ORDER_ENTRY_PORT,
            scope_ref="portfolio-entry-grant",
            operations=entry_operations,
            write_operations=entry_operations,
            service_epoch=service_epoch,
            policy_version=policy_version,
            requirements_version=requirements_version,
            expires_at=expires_at,
            strategy_write_run_id="owner-run",
        )
        query_registration = ActiveRegistrationContext(
            context=query_context,
            instance_id="execution-owner",
            port=GROUP_ORDER_QUERY_PORT,
            scope_ref="portfolio-query-grant",
            operations=frozenset({"orders.query_group_leg"}),
            write_operations=frozenset(),
            service_epoch=service_epoch,
            policy_version=policy_version,
            requirements_version=requirements_version,
            expires_at=expires_at,
            strategy_write_run_id="owner-run",
        )
        expected_operations = {
            entry_context.registration_id: entry_operations,
            query_context.registration_id: frozenset({"orders.query_group_leg"}),
        }

        def policy_matches(registration, operation: str) -> bool:
            return (
                registration.instance_id == "execution-owner"
                and registration.context.identity == entry_context.identity
                and registration.context.principal_ref == entry_context.principal_ref
                and registration.context.scope == entry_context.scope
                and operation in expected_operations.get(
                    registration.context.registration_id, frozenset()
                )
            )

        # Isolate the exact coordinator pairing predicate. The selected spawned
        # integration separately exercises real Registry policies and ZMQ.
        coordinator = SimpleNamespace(
            _clock_ns=time.time_ns,
            _lock=threading.RLock(),
            _registrations={
                (entry_context.registration_id, GROUP_ORDER_ENTRY_PORT): entry_registration,
                (query_context.registration_id, GROUP_ORDER_QUERY_PORT): query_registration,
            },
            _policy_matches=policy_matches,
            _targets={query_context.scope: execution_scope},
        )
        live_query_context = replace(
            query_context, result_fits=lambda _payload: True
        )
        pair = ExecutionGroupOrderEntryService._reconciliation_registration_pair(
            coordinator,
            query_registration,
            live_query_context,
            execution_scope,
            origin_run_id="owner-run",
        )
        assert pair == (query_registration, entry_registration)

        authority = ExecutionGroupAuthority(
            tmp_path / f"authority-{changed_field}.sqlite3"
        )
        with authority.guard.bind_service_call(
            live_query_context,
            execution_scope=execution_scope,
            reconciliation_registrations=(query_registration, entry_registration),
        ):
            pass

        if changed_field == "identity":
            changed_context = replace(
                live_query_context,
                identity=replace(
                    live_query_context.identity,
                    actor_ref=f"{live_query_context.identity.actor_ref}-other",
                ),
            )
        elif changed_field == "scope":
            changed_context = replace(
                live_query_context,
                scope=replace(
                    live_query_context.scope,
                    portfolio_ref=(
                        f"{live_query_context.scope.portfolio_ref}-other"
                    ),
                ),
            )
        elif changed_field == "registration_id":
            changed_context = replace(
                live_query_context, registration_id="different-query-registration"
            )
        else:
            changed_principal = (
                f"{live_query_context.principal_ref}-other"
            )
            changed_context = replace(
                live_query_context,
                identity=replace(
                    live_query_context.identity,
                    principal_ref=changed_principal,
                ),
                principal_ref=changed_principal,
            )

        with pytest.raises(ServiceExecutionGroupError) as coordinator_error:
            ExecutionGroupOrderEntryService._reconciliation_registration_pair(
                coordinator,
                query_registration,
                changed_context,
                execution_scope,
                origin_run_id="owner-run",
            )
        assert coordinator_error.value.code == "GROUP_QUERY_REGISTRATION_INVALID"

        with pytest.raises(GroupAuthorityReconciliationRejected):
            with authority.guard.bind_service_call(
                changed_context,
                execution_scope=execution_scope,
                reconciliation_registrations=(query_registration, entry_registration),
            ):
                pass
    finally:
        if authority is not None:
            authority.close()
        registry.close()
