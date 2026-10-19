"""No-dispatch handling when an owner quota wait outlives a Risk permit."""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest
from bt_api_base.service_protocol import WireScope
from bt_api_execution import (
    CancelIntent,
    ExecutionScope,
    ExecutionState,
    ManagedCancellationFacade,
    ManagedExecutionFacade,
    OrderIntent,
    ProviderObservation,
    SqliteExecutionStore,
    Side,
)
from bt_api_risk import (
    AccountScope,
    DurableRiskGate,
    PermitInvalidError,
    RiskPolicy,
)

from bt_api_py.service_execution_risk_cancel import RiskExecutionCancelAdmissionGate
from bt_api_py.service_risk_order_journal import RiskOrderJournal


def _intent(scope: ExecutionScope, cancel_id: str) -> CancelIntent:
    return CancelIntent(
        cancel_id=cancel_id,
        scope=scope,
        target_intent_id="target-order",
        provider_order_id="provider-target-order",
        metadata_version="synthetic-v1",
        tags={
            "owner_cancel_binding_version": "1",
            "owner_cancel_batch_key": "batch-" + cancel_id,
            "owner_cancel_command_id": "command-" + cancel_id,
            "owner_cancel_principal_ref": "operator-principal",
            "owner_cancel_actor_ref": "operator-actor",
            "owner_cancel_strategy_ref": scope.strategy_id,
            "owner_cancel_run_id": "run-expiry",
        },
    )


def _risk_rows(path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA query_only = ON")
        reservations = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT status FROM risk_reservations ORDER BY issued_at, permit_id"
            )
        )
        claims = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT permit_id FROM risk_dispatch_claims ORDER BY permit_id"
            )
        )
        return reservations, claims
    finally:
        connection.close()


@pytest.mark.unit
def test_quota_wait_expiring_real_risk_permit_records_no_dispatch_and_releases(tmp_path) -> None:
    execution_scope = ExecutionScope(
        "PAPER", "simulation", "account-cancel-expiry", "strategy-cancel-expiry", "20261017"
    )
    risk_scope = AccountScope("PAPER", execution_scope.account_ref, "simulation")
    wire_scope = WireScope(
        "paper:cancel-expiry",
        account_ref=execution_scope.account_ref,
        strategy_ref=execution_scope.strategy_id,
    )
    execution_store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    target_owner = ManagedExecutionFacade(
        execution_store,
        execution_scope,
        writer_id="cancel-expiry-target-owner",
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
        lambda intent: ProviderObservation.accepted(intent.intent_id, "provider-target-order"),
    )
    journal = RiskOrderJournal(tmp_path / "journal.sqlite3", writer_id="expiry-journal")
    risk_clock = [100.0]
    risk_path = tmp_path / "risk.sqlite3"
    risk_gate = DurableRiskGate(
        risk_path,
        RiskPolicy(
            "cancel-expiry-v1",
            Decimal("0"),
            0,
            permit_ttl_seconds=1.0,
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
    dispatched: list[str] = []

    def dispatch(intent: CancelIntent):
        dispatched.append(intent.cancel_id)
        raise AssertionError("expired permit must not reach the provider")

    def quota_wait(intent: CancelIntent) -> None:
        assert intent.cancel_id == "cancel-expired"
        # Simulate a quota owner returning after the exact permit TTL. The
        # Risk claim transaction must perform its own post-wait validation.
        risk_clock[0] = 102.0

    try:
        first = facade.cancel(
            _intent(execution_scope, "cancel-expired"),
            dispatch,
            before_dispatch=quota_wait,
        )

        assert first.state is ExecutionState.BLOCKED
        assert first.dispatch_attempts == 1
        assert first.review_required is True
        assert dispatched == []
        assert risk_gate.snapshot(risk_scope)["active_freeze_reasons"] == []
        reservations, claims = _risk_rows(risk_path)
        assert reservations == ("released",)
        assert claims == ()
        with pytest.raises(PermitInvalidError) as missing_claim:
            risk_gate.dispatch_claim_binding(first.permit_reference)
        assert missing_claim.value.code == "PERMIT_NOT_DISPATCH_CLAIMED"

        # A separate command can still reserve and then receive a fresh local
        # quota denial. It must also finish without provider dispatch or a
        # stale cancellation latch from the expired command.
        second = facade.cancel(
            _intent(execution_scope, "cancel-next"),
            dispatch,
            before_dispatch=lambda _intent: (_ for _ in ()).throw(TimeoutError()),
        )
        assert second.state is ExecutionState.BLOCKED
        assert second.cancel_id != first.cancel_id
        assert dispatched == []
        assert risk_gate.snapshot(risk_scope)["active_freeze_reasons"] == []
        reservations, claims = _risk_rows(risk_path)
        assert reservations == ("released", "released")
        assert claims == ()
    finally:
        risk_gate.close()
        execution_store.close()
