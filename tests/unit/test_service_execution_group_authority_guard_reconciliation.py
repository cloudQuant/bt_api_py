"""Focused coverage for the real Guard-to-Control hold reconciliation path."""

from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from bt_api_execution import (
    ExecutionState,
    GroupChildOrderStatus,
    GroupDispatchResolutionReceipt,
    GroupLegClaimReceipt,
    GroupLegOrderReceipt,
    GroupLegPermitReference,
)

from bt_api_py.service_execution_group import (
    GROUP_ORDER_ENTRY_PORT,
    GROUP_ORDER_QUERY_PORT,
)
from bt_api_py.service_execution_group_authority import (
    ExecutionGroupAuthority,
    GroupAuthorityReconciliationRejected,
)
from bt_api_py.service_registry import ActiveRegistrationContext
from tests.unit.test_service_execution_group_authority import (
    _RISK_SCOPE_KEY,
    _authorization,
    _capture_registry_contexts,
    _execution_scope,
    _intent_and_binding,
    _register_and_dispatch,
    _writer_lease,
)


class _ExactReceiptSource:
    """Narrow source-verifier leaf; the authority and its SQLite store stay real."""

    def __init__(self, execution_scope, entry_context, expected_guard) -> None:
        self.execution_scope = execution_scope
        self.entry_context = entry_context
        self.expected_guard = expected_guard
        self.hold = None
        self.receipt = None
        self.calls: list[tuple[object, object]] = []

    def verify_authority_reconciliation(
        self,
        hold,
        proof,
        *,
        active_authority,
        context,
        execution_scope,
    ) -> bool:
        self.calls.append((hold, proof))
        return (
            hold == self.hold
            and proof == self.receipt
            and active_authority is self.expected_guard
            and context == self.entry_context
            and execution_scope == self.execution_scope
        )


def _terminal_receipt(intent, binding) -> GroupLegOrderReceipt:
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
        actual_child_intent_hash=binding.actual_child_intent_hash,
        claim_digest=hashlib.sha256(b"exact-guard-reconciliation-claim").hexdigest(),
        actual_quantity=intent.quantity,
        actual_notional=intent.quantity * intent.price,
    )
    proof_sha256 = hashlib.sha256(b"exact-guard-reconciliation-proof").hexdigest()
    resolution = GroupDispatchResolutionReceipt(
        claim=claim,
        resolution_kind="TERMINAL_NO_FILL_RESOLVED",
        proof_sha256=proof_sha256,
        final_state=ExecutionState.REJECTED,
        requested_quantity=intent.quantity,
        filled_quantity=Decimal("0"),
        trade_count=0,
        order_revision=None,
        journal_revision=1,
        exposure_proof_sha256=None,
        exposure_reservation_id=None,
        exposure_reservation_sha256=None,
        dispatch_latch_active=False,
        has_other_active_freezes=False,
    )
    return GroupLegOrderReceipt(
        status=GroupChildOrderStatus(
            intent_id=intent.intent_id,
            state=ExecutionState.REJECTED,
            filled_quantity=Decimal("0"),
            reason_code="synthetic_reject",
            trade_count=0,
        ),
        claim=claim,
        resolution=resolution,
    )


def test_guard_reconciles_exact_orphan_hold_through_real_control_api(
    tmp_path: Path,
) -> None:
    registry, _contexts = _capture_registry_contexts(tmp_path / "registry.sqlite3")
    authority = None
    try:
        execution_scope = _execution_scope()
        entry_context = replace(
            _register_and_dispatch(registry, session="guard-control-reconciliation"),
            result_fits=None,
        )
        query_context = replace(
            entry_context, registration_id="guard-control-query-registration"
        )
        intent, binding = _intent_and_binding()
        writer_lease = _writer_lease()

        authority = ExecutionGroupAuthority(
            tmp_path / "authority.sqlite3",
            monotonic_clock_ns=lambda: 1,
        )
        authority.control.activate(
            entry_context,
            execution_scope=execution_scope,
            risk_account_scope_key=_RISK_SCOPE_KEY,
        )
        with authority.guard.bind_service_call(
            entry_context, execution_scope=execution_scope
        ):
            with pytest.raises(RuntimeError, match="synthetic owner interruption"):
                with _authorization(
                    authority,
                    entry_context,
                    intent=intent,
                    binding=binding,
                    lease=writer_lease,
                ):
                    raise RuntimeError("synthetic owner interruption")

        holds = authority.pending_holds()
        assert len(holds) == 1
        hold = holds[0]
        assert authority.guard.reconciliation_hold_state(hold.hold_id) == "pending"
        assert authority.ready is False

        entry_operations = frozenset(
            {"orders.submit_group_leg", "orders.resolve_group_leg"}
        )
        service_epoch = "guard-control-service-epoch-v1"
        policy_version = "guard-control-policy-v1"
        requirements_version = "guard-control-requirements-v1"
        expires_at = time.time() + 60
        entry_registration = ActiveRegistrationContext(
            context=entry_context,
            instance_id="execution-owner",
            port=GROUP_ORDER_ENTRY_PORT,
            scope_ref="portfolio-grant-a",
            operations=entry_operations,
            write_operations=entry_operations,
            service_epoch=service_epoch,
            policy_version=policy_version,
            requirements_version=requirements_version,
            expires_at=expires_at,
            strategy_write_run_id="guard-control-run-v1",
        )
        query_registration = ActiveRegistrationContext(
            context=query_context,
            instance_id="execution-owner",
            port=GROUP_ORDER_QUERY_PORT,
            scope_ref="portfolio-query-grant-a",
            operations=frozenset({"orders.query_group_leg"}),
            write_operations=frozenset(),
            service_epoch=service_epoch,
            policy_version=policy_version,
            requirements_version=requirements_version,
            expires_at=expires_at,
            strategy_write_run_id="guard-control-run-v1",
        )
        pair = (query_registration, entry_registration)
        receipt = _terminal_receipt(intent, binding)
        source = _ExactReceiptSource(execution_scope, entry_context, authority.guard)
        source.hold = hold
        source.receipt = receipt
        authority.guard.bind_reconciliation_source(source)

        wrong_receipt = replace(
            receipt,
            status=replace(receipt.status, reason_code="different_terminal_fact"),
        )
        with authority.guard.bind_service_call(
            query_context,
            execution_scope=execution_scope,
            reconciliation_registrations=pair,
        ):
            with pytest.raises(GroupAuthorityReconciliationRejected):
                authority.guard.reconcile_hold(
                    hold.hold_id,
                    wrong_receipt,
                    context=query_context,
                    execution_scope=execution_scope,
                )
            assert authority.pending_holds() == holds
            assert authority.guard.reconciliation_hold_state(hold.hold_id) == "pending"
            assert authority.ready is False

            authority.guard.reconcile_hold(
                hold.hold_id,
                receipt,
                context=query_context,
                execution_scope=execution_scope,
            )
            assert authority.pending_holds() == ()
            assert authority.guard.reconciliation_hold_state(hold.hold_id) == "reconciled"
            assert authority.ready is True

            # A retry after the durable tombstone is idempotent and still
            # crosses the same bound source verifier.
            authority.guard.reconcile_hold(
                hold.hold_id,
                receipt,
                context=query_context,
                execution_scope=execution_scope,
            )

        assert authority.pending_holds() == ()
        assert authority.guard.reconciliation_hold_state(hold.hold_id) == "reconciled"
        assert len(source.calls) == 3
        assert all(call_hold == hold for call_hold, _proof in source.calls)
    finally:
        if authority is not None:
            authority.close()
        registry.close()
