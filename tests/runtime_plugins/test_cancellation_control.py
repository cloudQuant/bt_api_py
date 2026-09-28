"""Local acceptance for audited release of an unknown cancellation freeze.

These fixtures use only SQLite journals and fake callbacks.  They intentionally
prove no provider dispatch and no automatic release of an ambiguous
cancellation outcome.
"""

from __future__ import annotations

import importlib
from decimal import Decimal
from pathlib import Path

import pytest

from bt_api_py.runtime_plugins import (
    CAPABILITY_EXECUTION,
    CAPABILITY_MONITOR,
    CAPABILITY_RISK,
    AuthorizationDecision,
    CancellationReconciliationEvidence,
    CapabilityCatalog,
    CapabilityPin,
    ControlCommandStatus,
    ManagedCancellationReconciliationControlPort,
    ReleaseCancellationFreezeCommand,
    RuntimeCapabilityContract,
    RuntimePluginError,
    SealedNormalizedInstrumentMetadataSnapshot,
    compose_managed_execution,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CAPABILITY_SOURCES = (
    _REPO_ROOT / "bt_api" / "bt_api_base" / "src",
    _REPO_ROOT / "bt_api" / "bt_api_execution" / "src",
    _REPO_ROOT / "bt_api" / "bt_api_risk" / "src",
    _REPO_ROOT / "bt_api" / "bt_api_monitor" / "src",
)
_NOW = 1_700_000_000.0


def _contract() -> RuntimeCapabilityContract:
    return RuntimeCapabilityContract(
        strategy_id="example.014_1.ctp_options_lowfreq",
        mode="live",
        preset="managed_live_direct",
        environment="production",
        order_route="managed_execution",
        required_capabilities=(CAPABILITY_EXECUTION, CAPABILITY_RISK, CAPABILITY_MONITOR),
        effective_digest="a" * 64,
    )


def _catalog(monkeypatch: pytest.MonkeyPatch) -> CapabilityCatalog:
    for source in _CAPABILITY_SOURCES:
        monkeypatch.syspath_prepend(str(source))
    return CapabilityCatalog(
        (
            CapabilityPin(CAPABILITY_EXECUTION, "bt_api_execution", "bt_api_execution", "0.1.0"),
            CapabilityPin(CAPABILITY_RISK, "bt_api_risk", "bt_api_risk", "0.1.0"),
            CapabilityPin(CAPABILITY_MONITOR, "bt_api_monitor", "bt_api_monitor", "0.1.0"),
        ),
        importer=importlib.import_module,
        version_getter=lambda _distribution: "0.1.0",
    )


def _snapshot() -> SealedNormalizedInstrumentMetadataSnapshot:
    return SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
        {
            "provider": "fixture_provider",
            "environment": "production",
            "account_ref": "fixture_account",
            "trading_day": "20260922",
            "metadata_version": "fixture-normalized-v1",
            "as_of_ns": 1_000,
            "expires_at_ns": 2_000,
            "account_currency": "USD",
            "instruments": [
                {
                    "instrument": "fixture/contract",
                    "tick_size": "0.1",
                    "lot_size": "1",
                    "contract_multiplier": "1",
                    "max_gross_notional_account": "1000",
                    "quote_currency": "USD",
                    "fee_currency": "USD",
                    "quote_to_account_fx": "1",
                    "fee_to_account_fx": "1",
                    "taker_fee_bps": "0",
                    "fixed_fee": "0",
                    "max_slippage_bps": "0",
                    "quantity_unit": "contracts",
                }
            ],
        }
    )


def _runtime(monkeypatch: pytest.MonkeyPatch, state_directory: Path):
    loaded = _catalog(monkeypatch).load(_contract())
    snapshot = _snapshot()
    return loaded, compose_managed_execution(
        loaded,
        state_directory=state_directory,
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="fixture_writer",
        policy_id="fixture_policy",
        max_increase_notional=Decimal("100"),
        max_increase_count=3,
        trading_day=snapshot.trading_day,
        instrument_metadata_snapshot=snapshot,
        instrument_clock_ns=lambda: 1_500,
    )


class _CancelGate:
    def reserve(self, intent):
        return {"permit_id": "cancel-permit-" + intent.cancel_id}

    def validate(self, permit_reference, intent):
        assert permit_reference == "cancel-permit-" + intent.cancel_id

    def settle(self, permit_reference):
        assert permit_reference.startswith("cancel-permit-")

    def release(self, permit_reference, reason):
        assert permit_reference.startswith("cancel-permit-")
        assert reason


def _order_intent(runtime, execution):
    snapshot = runtime.instrument_metadata_snapshot
    assert snapshot is not None
    return execution.OrderIntent.limit(
        intent_id="intent.cancel.unknown",
        scope=runtime.scope,
        signal_id="signal.cancel.unknown",
        instrument="fixture/contract",
        side=execution.Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("10"),
        metadata_version=snapshot.metadata_version,
        tags={
            "instrument_metadata_digest": snapshot.instrument_digest("fixture/contract"),
            "quantity_unit": snapshot.instrument_metadata("fixture/contract").quantity_unit,
        },
    )


def _unknown_cancel(runtime, execution):
    order_intent = _order_intent(runtime, execution)
    runtime.submit(
        order_intent,
        lambda intent: execution.ProviderObservation.accepted(intent.intent_id, "provider.order.1"),
    )
    facade = execution.ManagedCancellationFacade(
        runtime.execution_store,
        runtime.scope,
        acquire_writer_lease=runtime.facade.acquire_writer_lease,
        admission_gate=_CancelGate(),
    )
    cancel_intent = execution.CancelIntent(
        cancel_id="cancel.unknown.1",
        scope=runtime.scope,
        target_intent_id=order_intent.intent_id,
        provider_order_id="provider.order.1",
        metadata_version="metadata.1",
    )

    def timeout_provider(_intent):
        raise TimeoutError("fixture cancellation timeout")

    record = facade.cancel(cancel_intent, timeout_provider)
    assert record.state is execution.ExecutionState.UNKNOWN
    cause_id = "cancel-outcome-unknown:" + runtime.scope.key + ":" + cancel_intent.cancel_id
    runtime.risk_gate.freeze(runtime.risk_scope, cause_id, cause_id)
    assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
    return facade, cancel_intent, cause_id


def _evidence(cancel_intent, execution) -> CancellationReconciliationEvidence:
    return CancellationReconciliationEvidence(
        evidence_id="cancel-evidence.1",
        cancel_id=cancel_intent.cancel_id,
        target_intent_id=cancel_intent.target_intent_id,
        provider_order_id=cancel_intent.provider_order_id,
        observation=execution.CancelObservation.cancelled(
            cancel_intent.cancel_id,
            cancel_intent.target_intent_id,
            cancel_intent.provider_order_id,
        ),
        source_receipt_digest="b" * 64,
        observed_at=_NOW,
    )


def _command(
    runtime, evidence: CancellationReconciliationEvidence
) -> ReleaseCancellationFreezeCommand:
    return ReleaseCancellationFreezeCommand(
        command_id="release-cancel.1",
        scope=runtime.scope.key,
        cancel_id=evidence.cancel_id,
        evidence_id=evidence.evidence_id,
        evidence_fingerprint=evidence.fingerprint,
        issuer_id="operator.alice",
        reason_code="dual_review_complete",
        issued_at=_NOW + 1.0,
        expires_at=_NOW + 60.0,
    )


def _approve(request) -> AuthorizationDecision:
    assert request.command.issuer_id == "operator.alice"
    assert request.evidence.cancel_id == request.command.cancel_id
    return AuthorizationDecision(
        approved=True,
        subject_id="operator.alice",
        receipt_digest="c" * 64,
        reason_code="dual_review_complete",
    )


def _control(runtime, facade, state_directory: Path, authorize=_approve):
    return ManagedCancellationReconciliationControlPort(
        runtime,
        facade,
        state_directory=state_directory,
        authorize=authorize,
        clock=lambda: _NOW + 2.0,
    )


def _event_types(runtime) -> list[str]:
    return [
        item.event.event_type
        for item in runtime.outbox.read_pending("cancel-control-test", runtime.scope.key, limit=100)
    ]


def test_terminal_cancel_reconcile_then_authorized_release_is_audited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded, runtime = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    facade, cancel_intent, cause_id = _unknown_cancel(runtime, execution)
    control = _control(runtime, facade, tmp_path)
    try:
        evidence = _evidence(cancel_intent, execution)

        # A direct journal reconciliation makes the cancellation known but is
        # never itself an authorization to lift the account freeze.
        direct = facade.reconcile(evidence.observation)
        assert direct.state is execution.ExecutionState.CANCELLED
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)

        reconciled = control.reconcile(evidence)
        assert reconciled.record.state is execution.ExecutionState.CANCELLED
        assert reconciled.audit.monitor_published is True
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)

        command = _command(runtime, evidence)
        released = control.release_cancel_freeze(command)
        assert released.released is True
        assert released.idempotent is False
        assert cause_id not in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        audit = control.audit.get_command(command.command_id)
        assert audit is not None
        assert audit.status is ControlCommandStatus.RELEASED
        assert audit.authorization_subject_id == command.issuer_id
        assert _event_types(runtime).count("cancellation_reconciled") == 1
        assert _event_types(runtime).count("cancellation_freeze_release_authorized") == 1
        assert _event_types(runtime).count("cancellation_freeze_released") == 1

        replay = control.release_cancel_freeze(command)
        assert replay.idempotent is True
        assert _event_types(runtime).count("cancellation_freeze_released") == 1
    finally:
        control.close()
        runtime.close()


def test_acknowledged_cancel_evidence_cannot_release_unknown_freeze(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded, runtime = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    facade, cancel_intent, cause_id = _unknown_cancel(runtime, execution)
    control = _control(runtime, facade, tmp_path)
    try:
        evidence = CancellationReconciliationEvidence(
            evidence_id="cancel-evidence.pending",
            cancel_id=cancel_intent.cancel_id,
            target_intent_id=cancel_intent.target_intent_id,
            provider_order_id=cancel_intent.provider_order_id,
            observation=execution.CancelObservation.accepted(
                cancel_intent.cancel_id,
                cancel_intent.target_intent_id,
                cancel_intent.provider_order_id,
            ),
            source_receipt_digest="b" * 64,
            observed_at=_NOW,
        )
        with pytest.raises(RuntimePluginError) as caught:
            control.reconcile(evidence)
        assert caught.value.code == "CANCELLATION_RECONCILIATION_INCOMPLETE"
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        assert facade.get(cancel_intent.cancel_id).state is execution.ExecutionState.UNKNOWN
    finally:
        control.close()
        runtime.close()


def test_denied_or_identity_mismatched_authorization_keeps_cancel_freeze(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded, runtime = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    facade, cancel_intent, cause_id = _unknown_cancel(runtime, execution)

    def forged_identity(_request) -> AuthorizationDecision:
        return AuthorizationDecision(
            approved=True,
            subject_id="operator.mallory",
            receipt_digest="d" * 64,
            reason_code="dual_review_complete",
        )

    control = _control(runtime, facade, tmp_path, forged_identity)
    try:
        evidence = _evidence(cancel_intent, execution)
        control.reconcile(evidence)
        with pytest.raises(RuntimePluginError) as caught:
            control.release_cancel_freeze(_command(runtime, evidence))
        assert caught.value.code == "CANCELLATION_CONTROL_AUTHORIZATION_IDENTITY_MISMATCH"
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        audit = control.audit.get_command("release-cancel.1")
        assert audit is not None
        assert audit.status is ControlCommandStatus.PENDING
    finally:
        control.close()
        runtime.close()


def test_release_outbox_failure_reasserts_cancel_freeze_before_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded, runtime = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    facade, cancel_intent, cause_id = _unknown_cancel(runtime, execution)
    control = _control(runtime, facade, tmp_path)
    original_append = runtime.outbox.append

    def fail_release_event(event):
        if event.event_type == "cancellation_freeze_released":
            raise OSError("monitor unavailable")
        return original_append(event)

    try:
        evidence = _evidence(cancel_intent, execution)
        control.reconcile(evidence)
        command = _command(runtime, evidence)
        monkeypatch.setattr(runtime.outbox, "append", fail_release_event)
        with pytest.raises(RuntimePluginError) as caught:
            control.release_cancel_freeze(command)
        assert caught.value.code == "CANCELLATION_CONTROL_MONITOR_OUTBOX_UNCONFIRMED"
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        audit = control.audit.get_command(command.command_id)
        assert audit is not None
        assert audit.status is ControlCommandStatus.PENDING

        monkeypatch.setattr(runtime.outbox, "append", original_append)
        retry = control.release_cancel_freeze(command)
        assert retry.released is True
        assert cause_id not in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
    finally:
        control.close()
        runtime.close()


def test_cancel_control_survives_restart_without_duplicate_monitor_facts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded, first = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    facade, cancel_intent, cause_id = _unknown_cancel(first, execution)
    first_control = _control(first, facade, tmp_path)
    evidence = _evidence(cancel_intent, execution)
    command = _command(first, evidence)
    try:
        first_control.reconcile(evidence)
        assert cause_id in first.risk_gate.active_freeze_reasons(first.risk_scope)
    finally:
        first_control.close()
        first.close()

    loaded, second = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    second_facade = execution.ManagedCancellationFacade(
        second.execution_store,
        second.scope,
        acquire_writer_lease=second.facade.acquire_writer_lease,
        admission_gate=_CancelGate(),
    )
    second_control = _control(second, second_facade, tmp_path)
    try:
        replayed = second_control.reconcile(evidence)
        assert replayed.audit.monitor_published is True
        assert _event_types(second).count("cancellation_reconciled") == 1
        released = second_control.release_cancel_freeze(command)
        assert released.released is True
        assert cause_id not in second.risk_gate.active_freeze_reasons(second.risk_scope)
    finally:
        second_control.close()
        second.close()


@pytest.mark.parametrize("release_was_called", [False, True])
def test_cancel_restart_reasserts_prepared_release_before_or_after_risk_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, release_was_called: bool
) -> None:
    """Prepared cancel-release audit state never implies a successful release."""

    loaded, first = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    facade, cancel_intent, cause_id = _unknown_cancel(first, execution)
    first_control = _control(first, facade, tmp_path)
    evidence = _evidence(cancel_intent, execution)
    command = _command(first, evidence)
    try:
        first_control.reconcile(evidence)
        first_control.audit.record_command(command)
        first_control.audit.record_authorization(
            command.command_id,
            AuthorizationDecision(
                approved=True,
                subject_id=command.issuer_id,
                receipt_digest="c" * 64,
                reason_code="dual_review_complete",
            ),
        )
        prepared = first_control.audit.record_release_applied(command.command_id)
        assert prepared.release_applied_at is not None
        if release_was_called:
            first.risk_gate.resolve_freeze(first.risk_scope, cause_id)
            assert cause_id not in first.risk_gate.active_freeze_reasons(first.risk_scope)
    finally:
        first_control.close()
        first.close()

    loaded, restarted = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    restarted_facade = execution.ManagedCancellationFacade(
        restarted.execution_store,
        restarted.scope,
        acquire_writer_lease=restarted.facade.acquire_writer_lease,
        admission_gate=_CancelGate(),
    )
    restarted_control = _control(restarted, restarted_facade, tmp_path)
    try:
        assert cause_id in restarted.risk_gate.active_freeze_reasons(restarted.risk_scope)
        audit = restarted_control.audit.get_command(command.command_id)
        assert audit is not None
        assert audit.status is ControlCommandStatus.PENDING
        assert audit.release_applied_at == prepared.release_applied_at
        assert restarted_control.release_cancel_freeze(command).released is True
        assert cause_id not in restarted.risk_gate.active_freeze_reasons(restarted.risk_scope)
    finally:
        restarted_control.close()
        restarted.close()


@pytest.mark.parametrize("failure_stage", ["outbox", "final_audit"])
def test_cancel_release_failure_then_restart_reasserts_and_recovers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_stage: str
) -> None:
    """Cancellation release remains frozen through outbox/audit recovery gaps."""

    loaded, first = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    facade, cancel_intent, cause_id = _unknown_cancel(first, execution)
    first_control = _control(first, facade, tmp_path)
    evidence = _evidence(cancel_intent, execution)
    command = _command(first, evidence)
    try:
        first_control.reconcile(evidence)
        if failure_stage == "outbox":
            original_append = first.outbox.append

            def fail_release_event(event):
                if event.event_type == "cancellation_freeze_released":
                    raise OSError("monitor unavailable")
                return original_append(event)

            monkeypatch.setattr(first.outbox, "append", fail_release_event)
            expected_code = "CANCELLATION_CONTROL_MONITOR_OUTBOX_UNCONFIRMED"
        else:
            monkeypatch.setattr(
                first_control.audit,
                "mark_command_released",
                lambda _command_id: (_ for _ in ()).throw(OSError("audit unavailable")),
            )
            expected_code = "CANCELLATION_CONTROL_AUDIT_UNCONFIRMED"

        with pytest.raises(RuntimePluginError) as caught:
            first_control.release_cancel_freeze(command)
        assert caught.value.code == expected_code
        assert cause_id in first.risk_gate.active_freeze_reasons(first.risk_scope)
        audit = first_control.audit.get_command(command.command_id)
        assert audit is not None
        assert audit.status is ControlCommandStatus.PENDING
        assert audit.release_applied_at is not None
    finally:
        first_control.close()
        first.close()

    loaded, restarted = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    restarted_facade = execution.ManagedCancellationFacade(
        restarted.execution_store,
        restarted.scope,
        acquire_writer_lease=restarted.facade.acquire_writer_lease,
        admission_gate=_CancelGate(),
    )
    restarted_control = _control(restarted, restarted_facade, tmp_path)
    try:
        assert cause_id in restarted.risk_gate.active_freeze_reasons(restarted.risk_scope)
        assert restarted_control.release_cancel_freeze(command).released is True
        assert cause_id not in restarted.risk_gate.active_freeze_reasons(restarted.risk_scope)
        assert _event_types(restarted).count("cancellation_freeze_released") == 1
    finally:
        restarted_control.close()
        restarted.close()
