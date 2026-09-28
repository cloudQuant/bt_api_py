"""Acceptance tests for reviewed reconciliation and per-intent freeze release."""

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
    CapabilityCatalog,
    CapabilityPin,
    ControlCommandStatus,
    ManagedReconciliationControlPort,
    ReconciliationEvidence,
    ReleaseIntentFreezeCommand,
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
        required_capabilities=(
            CAPABILITY_EXECUTION,
            CAPABILITY_RISK,
            CAPABILITY_MONITOR,
        ),
        effective_digest="a" * 64,
    )


def _catalog(monkeypatch: pytest.MonkeyPatch) -> CapabilityCatalog:
    for source in _CAPABILITY_SOURCES:
        monkeypatch.syspath_prepend(str(source))
    return CapabilityCatalog(
        (
            CapabilityPin(
                CAPABILITY_EXECUTION, "bt_api_execution", "bt_api_execution", "0.1.0"
            ),
            CapabilityPin(CAPABILITY_RISK, "bt_api_risk", "bt_api_risk", "0.1.0"),
            CapabilityPin(
                CAPABILITY_MONITOR, "bt_api_monitor", "bt_api_monitor", "0.1.0"
            ),
        ),
        importer=importlib.import_module,
        version_getter=lambda distribution: "0.1.0",
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


def _unknown_intent(runtime, execution):
    snapshot = runtime.instrument_metadata_snapshot
    assert snapshot is not None
    return execution.OrderIntent.limit(
        intent_id="intent.unknown",
        scope=runtime.scope,
        signal_id="signal.unknown",
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


def _make_unknown(runtime, execution):
    intent = _unknown_intent(runtime, execution)

    def timeout_provider(_intent: object) -> object:
        raise TimeoutError("fixture timeout")

    record = runtime.submit(intent, timeout_provider)
    assert record.state is execution.ExecutionState.UNKNOWN
    cause_id = "dispatch-inflight:" + intent.intent_id
    assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
    return intent, cause_id


def _evidence(intent, execution) -> ReconciliationEvidence:
    return ReconciliationEvidence(
        evidence_id="evidence.unknown.1",
        intent_id=intent.intent_id,
        observation=execution.ProviderObservation.accepted(
            intent.intent_id, "provider.order.unknown"
        ),
        source_receipt_digest="b" * 64,
        observed_at=_NOW,
    )


def _command(runtime, evidence: ReconciliationEvidence) -> ReleaseIntentFreezeCommand:
    return ReleaseIntentFreezeCommand(
        command_id="release.unknown.1",
        scope=runtime.scope.key,
        intent_id=evidence.intent_id,
        evidence_id=evidence.evidence_id,
        evidence_fingerprint=evidence.fingerprint,
        issuer_id="operator.alice",
        reason_code="dual_review_complete",
        issued_at=_NOW + 1.0,
        expires_at=_NOW + 60.0,
    )


def _approve(request) -> AuthorizationDecision:
    assert request.command.issuer_id == "operator.alice"
    assert request.evidence.intent_id == request.command.intent_id
    return AuthorizationDecision(
        approved=True,
        subject_id="operator.alice",
        receipt_digest="c" * 64,
        reason_code="dual_review_complete",
    )


def _control(
    runtime, state_directory: Path, authorize=_approve
) -> ManagedReconciliationControlPort:
    assert runtime.state_directory == state_directory.resolve(strict=False)
    return runtime.create_reconciliation_control(
        authorize=authorize,
        clock=lambda: _NOW + 2.0,
    )


def _event_types(runtime) -> list[str]:
    return [
        event.event.event_type
        for event in runtime.outbox.read_pending(
            "reconcile-test", runtime.scope.key, limit=100
        )
    ]


def test_unknown_reconcile_then_authorized_release_requires_audited_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded, runtime = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    control = _control(runtime, tmp_path)
    try:
        intent, cause_id = _make_unknown(runtime, execution)
        evidence = _evidence(intent, execution)

        # The facade can record evidence, but it remains ledger-only and leaves
        # the runtime freeze in place until the control port has an auditable
        # reconciliation and an explicitly authorized release command.
        direct = runtime.facade.reconcile(evidence.observation)
        assert direct.state is execution.ExecutionState.ACKED
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        assert "execution_reconciled" not in _event_types(runtime)

        reconciled = control.reconcile(evidence)
        assert reconciled.record.state is execution.ExecutionState.ACKED
        assert reconciled.audit.monitor_published is True
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)

        command = _command(runtime, evidence)
        released = control.release_intent_freeze(command)

        assert released.released is True
        assert released.idempotent is False
        assert cause_id not in runtime.risk_gate.active_freeze_reasons(
            runtime.risk_scope
        )
        audit = control.audit.get_command(command.command_id)
        assert audit is not None
        assert audit.status is ControlCommandStatus.RELEASED
        assert audit.authorization_subject_id == command.issuer_id
        assert audit.authorization_receipt_digest == "c" * 64
        assert _event_types(runtime).count("execution_reconciled") == 1
        assert _event_types(runtime).count("execution_freeze_release_authorized") == 1
        assert _event_types(runtime).count("execution_freeze_released") == 1

        # The generic restart recovery pass may publish its own deterministic
        # execution fact, but it must not reassert a latch after this separate
        # audited control port has released it.
        runtime.recover()
        assert cause_id not in runtime.risk_gate.active_freeze_reasons(
            runtime.risk_scope
        )

        replay = control.release_intent_freeze(command)
        assert replay.idempotent is True
        assert _event_types(runtime).count("execution_freeze_released") == 1
    finally:
        control.close()
        runtime.close()


def test_unauthorized_release_is_durably_refused_and_keeps_freeze(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded, runtime = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    authorizer_calls: list[str] = []

    def deny(request) -> AuthorizationDecision:
        authorizer_calls.append(request.command.command_id)
        return AuthorizationDecision(
            approved=False,
            subject_id=request.command.issuer_id,
            receipt_digest="d" * 64,
            reason_code="operator_not_authorized",
        )

    control = _control(runtime, tmp_path, deny)
    try:
        intent, cause_id = _make_unknown(runtime, execution)
        evidence = _evidence(intent, execution)
        control.reconcile(evidence)
        command = _command(runtime, evidence)

        with pytest.raises(RuntimePluginError) as caught:
            control.release_intent_freeze(command)

        assert caught.value.code == "CONTROL_AUTHORIZATION_DENIED"
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        audit = control.audit.get_command(command.command_id)
        assert audit is not None
        assert audit.status is ControlCommandStatus.DENIED
        assert audit.authorization_subject_id == command.issuer_id
        assert authorizer_calls == [command.command_id]

        with pytest.raises(RuntimePluginError) as repeated:
            control.release_intent_freeze(command)
        assert repeated.value.code == "CONTROL_AUTHORIZATION_DENIED"
        assert authorizer_calls == [command.command_id]
    finally:
        control.close()
        runtime.close()


def test_reconciliation_monitor_outbox_failure_keeps_unknown_freeze(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded, runtime = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    control = _control(runtime, tmp_path)
    original_append = runtime.outbox.append

    def fail_reconciliation(event: object) -> object:
        if event.event_type == "execution_reconciled":
            raise OSError("monitor unavailable")
        return original_append(event)

    try:
        intent, cause_id = _make_unknown(runtime, execution)
        evidence = _evidence(intent, execution)
        monkeypatch.setattr(runtime.outbox, "append", fail_reconciliation)

        with pytest.raises(RuntimePluginError) as caught:
            control.reconcile(evidence)

        assert caught.value.code == "RECONCILIATION_MONITOR_OUTBOX_UNCONFIRMED"
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        audit = control.audit.get_reconciliation(evidence.evidence_id)
        assert audit is not None
        assert audit.monitor_published is False

        monkeypatch.setattr(runtime.outbox, "append", original_append)
        recovered = control.reconcile(evidence)
        assert recovered.audit.monitor_published is True
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
    finally:
        control.close()
        runtime.close()


def test_release_monitor_outbox_failure_reasserts_freeze_before_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded, runtime = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    control = _control(runtime, tmp_path)
    original_append = runtime.outbox.append

    def fail_authorization_event(event: object) -> object:
        if event.event_type == "execution_freeze_release_authorized":
            raise OSError("monitor unavailable")
        return original_append(event)

    try:
        intent, cause_id = _make_unknown(runtime, execution)
        evidence = _evidence(intent, execution)
        control.reconcile(evidence)
        command = _command(runtime, evidence)
        monkeypatch.setattr(runtime.outbox, "append", fail_authorization_event)

        with pytest.raises(RuntimePluginError) as caught:
            control.release_intent_freeze(command)

        assert caught.value.code == "CONTROL_MONITOR_OUTBOX_UNCONFIRMED"
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        audit = control.audit.get_command(command.command_id)
        assert audit is not None
        assert audit.status is ControlCommandStatus.AUTHORIZED

        monkeypatch.setattr(runtime.outbox, "append", original_append)
        retried = control.release_intent_freeze(command)
        assert retried.released is True
        assert cause_id not in runtime.risk_gate.active_freeze_reasons(
            runtime.risk_scope
        )
    finally:
        control.close()
        runtime.close()


def test_final_release_monitor_outbox_failure_reasserts_freeze(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded, runtime = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    control = _control(runtime, tmp_path)
    original_append = runtime.outbox.append

    def fail_release_event(event: object) -> object:
        if event.event_type == "execution_freeze_released":
            raise OSError("monitor unavailable")
        return original_append(event)

    try:
        intent, cause_id = _make_unknown(runtime, execution)
        evidence = _evidence(intent, execution)
        control.reconcile(evidence)
        command = _command(runtime, evidence)
        monkeypatch.setattr(runtime.outbox, "append", fail_release_event)

        with pytest.raises(RuntimePluginError) as caught:
            control.release_intent_freeze(command)

        assert caught.value.code == "CONTROL_MONITOR_OUTBOX_UNCONFIRMED"
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        audit = control.audit.get_command(command.command_id)
        assert audit is not None
        assert audit.status is ControlCommandStatus.PENDING

        monkeypatch.setattr(runtime.outbox, "append", original_append)
        retried = control.release_intent_freeze(command)
        assert retried.released is True
        assert cause_id not in runtime.risk_gate.active_freeze_reasons(
            runtime.risk_scope
        )
    finally:
        control.close()
        runtime.close()


def test_reconcile_and_release_are_idempotent_across_runtime_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded, first = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    first_control = _control(first, tmp_path)
    intent, cause_id = _make_unknown(first, execution)
    evidence = _evidence(intent, execution)
    command = _command(first, evidence)
    try:
        first_control.reconcile(evidence)
        first_control.reconcile(evidence)
        assert _event_types(first).count("execution_reconciled") == 1
        assert cause_id in first.risk_gate.active_freeze_reasons(first.risk_scope)
    finally:
        first_control.close()
        first.close()

    _, second = _runtime(monkeypatch, tmp_path)
    second_control = _control(second, tmp_path)
    try:
        replayed = second_control.reconcile(evidence)
        assert replayed.audit.monitor_published is True
        assert _event_types(second).count("execution_reconciled") == 1
        assert cause_id in second.risk_gate.active_freeze_reasons(second.risk_scope)

        released = second_control.release_intent_freeze(command)
        assert released.released is True
        assert _event_types(second).count("execution_freeze_released") == 1
    finally:
        second_control.close()
        second.close()

    _, third = _runtime(monkeypatch, tmp_path)
    third_control = _control(third, tmp_path)
    try:
        replay = third_control.release_intent_freeze(command)
        assert replay.released is True
        assert replay.idempotent is True
        assert cause_id not in third.risk_gate.active_freeze_reasons(third.risk_scope)
        assert _event_types(third).count("execution_freeze_released") == 1
    finally:
        third_control.close()
        third.close()


@pytest.mark.parametrize("release_was_called", [False, True])
def test_restart_reasserts_prepared_release_before_or_after_risk_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, release_was_called: bool
) -> None:
    """A prepared audit row is never treated as successful release evidence.

    This simulates a process death on either side of the separate risk SQLite
    mutation.  The next control-port construction must restore the intent latch
    before another reviewed retry can be considered.
    """

    loaded, first = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    first_control = _control(first, tmp_path)
    intent, cause_id = _make_unknown(first, execution)
    evidence = _evidence(intent, execution)
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
            assert cause_id not in first.risk_gate.active_freeze_reasons(
                first.risk_scope
            )
    finally:
        first_control.close()
        first.close()

    _, restarted = _runtime(monkeypatch, tmp_path)
    restarted_control = _control(restarted, tmp_path)
    try:
        assert cause_id in restarted.risk_gate.active_freeze_reasons(
            restarted.risk_scope
        )
        audit = restarted_control.audit.get_command(command.command_id)
        assert audit is not None
        assert audit.status is ControlCommandStatus.PENDING
        # The prepare timestamp survives reassertion so an eventual retry has a
        # stable monitor event identity instead of conflicting with an already
        # persisted delivery fact.
        assert audit.release_applied_at == prepared.release_applied_at
        released = restarted_control.release_intent_freeze(command)
        assert released.released is True
        assert cause_id not in restarted.risk_gate.active_freeze_reasons(
            restarted.risk_scope
        )
    finally:
        restarted_control.close()
        restarted.close()


@pytest.mark.parametrize("failure_stage", ["outbox", "final_audit"])
def test_release_failure_then_restart_keeps_freeze_and_reuses_stable_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_stage: str
) -> None:
    """Outbox/audit failures after risk mutation remain fail-closed across restart."""

    loaded, first = _runtime(monkeypatch, tmp_path)
    execution = loaded.require(CAPABILITY_EXECUTION)
    first_control = _control(first, tmp_path)
    intent, cause_id = _make_unknown(first, execution)
    evidence = _evidence(intent, execution)
    command = _command(first, evidence)
    try:
        first_control.reconcile(evidence)
        if failure_stage == "outbox":
            original_append = first.outbox.append

            def fail_release_event(event):
                if event.event_type == "execution_freeze_released":
                    raise OSError("monitor unavailable")
                return original_append(event)

            monkeypatch.setattr(first.outbox, "append", fail_release_event)
            expected_code = "CONTROL_MONITOR_OUTBOX_UNCONFIRMED"
        else:
            monkeypatch.setattr(
                first_control.audit,
                "mark_command_released",
                lambda _command_id: (_ for _ in ()).throw(OSError("audit unavailable")),
            )
            expected_code = "CONTROL_AUDIT_UNCONFIRMED"

        with pytest.raises(RuntimePluginError) as caught:
            first_control.release_intent_freeze(command)
        assert caught.value.code == expected_code
        assert cause_id in first.risk_gate.active_freeze_reasons(first.risk_scope)
        audit = first_control.audit.get_command(command.command_id)
        assert audit is not None
        assert audit.status is ControlCommandStatus.PENDING
        assert audit.release_applied_at is not None
    finally:
        first_control.close()
        first.close()

    _, restarted = _runtime(monkeypatch, tmp_path)
    restarted_control = _control(restarted, tmp_path)
    try:
        assert cause_id in restarted.risk_gate.active_freeze_reasons(
            restarted.risk_scope
        )
        assert restarted_control.release_intent_freeze(command).released is True
        assert cause_id not in restarted.risk_gate.active_freeze_reasons(
            restarted.risk_scope
        )
        # A final-audit failure may already have made the stable release event
        # durable.  A retry must use that same id/payload rather than conflict.
        assert _event_types(restarted).count("execution_freeze_released") == 1
    finally:
        restarted_control.close()
        restarted.close()
