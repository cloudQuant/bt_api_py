"""Adversarial checks for the offline-only managed replay dispatch authority."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import bt_api_execution as execution
import bt_api_monitor as monitor
import bt_api_risk as risk
import pytest

from bt_api_py.runtime_plugins.catalog import LoadedCapabilities
from bt_api_py.runtime_plugins.contracts import (
    CAPABILITY_EXECUTION,
    CAPABILITY_MONITOR,
    CAPABILITY_RISK,
    RuntimeCapabilityContract,
)
from bt_api_py.runtime_plugins.fake_dispatch_authority import (
    FakeDispatchJournalError,
    ManagedFakeProviderJournalAuthority,
)
from bt_api_py.runtime_plugins.managed import (
    ManagedExecutionRuntime,
    RuntimePluginError,
    compose_managed_execution,
)

_STRATEGY_ID = "example.013_3.sa_midfreq_simnow"
_PROVIDER = "iteration41_managed_replay_fake_provider"
_ACCOUNT = "iteration41_managed_replay_fake_account"
_POLICY = "iteration41.managed_replay.l2"
_METADATA_DIGEST = "a" * 64


def _contract(strategy_id: str = _STRATEGY_ID) -> RuntimeCapabilityContract:
    return RuntimeCapabilityContract(
        strategy_id=strategy_id,
        mode="simulation",
        preset="replay",
        environment="offline",
        order_route="managed_execution",
        required_capabilities=(CAPABILITY_EXECUTION, CAPABILITY_RISK, CAPABILITY_MONITOR),
        effective_digest="b" * 64,
    )


def _compose(tmp_path: Path, *, strategy_id: str = _STRATEGY_ID) -> ManagedExecutionRuntime:
    contract = _contract(strategy_id)
    capabilities = LoadedCapabilities(
        contract=contract,
        modules={
            CAPABILITY_EXECUTION: execution,
            CAPABILITY_RISK: risk,
            CAPABILITY_MONITOR: monitor,
        },
    )
    return compose_managed_execution(
        capabilities,
        state_directory=tmp_path / "state",
        provider=_PROVIDER,
        environment="offline",
        account_ref=_ACCOUNT,
        strategy_id=strategy_id,
        writer_id="fake-fixture-test-writer",
        policy_id=_POLICY,
        max_increase_notional=Decimal("10000"),
        max_increase_count=20,
    )


def _intent(runtime: ManagedExecutionRuntime, intent_id: str = "fake-order-1") -> Any:
    return runtime.execution.OrderIntent.limit(
        intent_id=intent_id,
        scope=runtime.scope,
        signal_id="fixture-signal-1",
        instrument="IF2609",
        side=runtime.execution.Side.BUY,
        quantity=Decimal("2"),
        price=Decimal("6"),
        metadata_version="fixture-metadata-v1",
        tags={"instrument_metadata_digest": _METADATA_DIGEST},
    )


def _ack(intent: Any, provider_order_id: str = "fake-order-stable-17") -> Any:
    return execution.ProviderObservation.accepted(intent.intent_id, provider_order_id)


def test_acked_fake_order_transfers_to_durable_exposure_and_keeps_risk_counted(
    tmp_path: Path,
) -> None:
    runtime = _compose(tmp_path)
    intent = _intent(runtime)
    captured: list[Any] = []
    create_proof = runtime.fake_dispatch_authority.create_resolution_proof

    def capture_proof(intent_id: str, risk_gate: Any) -> Any:
        proof = create_proof(intent_id, risk_gate)
        captured.append(proof)
        return proof

    runtime.fake_dispatch_authority.create_resolution_proof = capture_proof
    try:
        record = runtime.submit(intent, lambda current: _ack(current))

        assert record.state is execution.ExecutionState.ACKED
        assert len(captured) == 1
        assert captured[0].evidence_class is risk.DispatchEvidenceClass.SIMULATION_JOURNAL
        assert captured[0].provider_order_id == "fake-order-stable-17"
        assert captured[0].filled_quantity == captured[0].trade_count == 0
        snapshot = runtime.risk_gate.snapshot(runtime.risk_scope)
        assert snapshot["increase_count"] == 1
        assert snapshot["increase_notional"] == Decimal("12")
        assert snapshot["active_freeze_reasons"] == []

        # Reopening the risk owner preserves the one-time resolution digest.
        restarted_gate = risk.DurableRiskGate(
            runtime.state_directory / "risk.sqlite3",
            risk.RiskPolicy(_POLICY, Decimal("10000"), 20),
            execution_journal_authority=runtime.fake_dispatch_authority,
        )
        try:
            with pytest.raises(risk.PermitInvalidError):
                restarted_gate.resolve_dispatch_freeze(captured[0])
            assert restarted_gate.snapshot(runtime.risk_scope)["increase_notional"] == Decimal("12")
        finally:
            restarted_gate.close()
    finally:
        runtime.close()


def test_resolved_fake_dispatch_attestation_is_immutable_after_later_cancel(
    tmp_path: Path,
) -> None:
    runtime = _compose(tmp_path)
    intent = _intent(runtime, "fake-cancel-after-dispatch-proof")
    try:
        record = runtime.submit(intent, lambda current: _ack(current))
        assert record.state is execution.ExecutionState.ACKED
        assert runtime.recovery_coordinator.freeze_status_for(
            runtime.scope.key, intent.intent_id
        ) == "RESOLVED"

        cancelled = runtime.facade.reconcile(
            execution.ProviderObservation(
                intent_id=intent.intent_id,
                state=execution.ExecutionState.CANCELLED,
                provider_order_id=record.provider_order_id,
                filled_quantity=Decimal("0"),
            )
        )
        assert cancelled.state is execution.ExecutionState.CANCELLED
        report = runtime.recover()
        assert intent.intent_id not in report.reconciliation_required_intent_ids

        with sqlite3.connect(runtime.state_directory / "fake_dispatch.sqlite3") as connection:
            row = connection.execute(
                "SELECT provider_state, record_state, record_filled_quantity "
                "FROM fake_dispatch_journal WHERE intent_id = ?",
                (intent.intent_id,),
            ).fetchone()
        assert row == ("ACKED", "ACKED", "0")
        assert runtime.risk_gate.snapshot(runtime.risk_scope)["increase_count"] == 1
        assert runtime.risk_gate.active_freeze_reasons(runtime.risk_scope) == []
    finally:
        runtime.close()


def test_unresolved_fake_dispatch_rejects_later_sdk_state_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_finish = ManagedExecutionRuntime._finish_coordinated_freeze_if_ready
    monkeypatch.setattr(
        ManagedExecutionRuntime, "_finish_coordinated_freeze_if_ready", lambda *_: None
    )
    runtime = _compose(tmp_path)
    intent = _intent(runtime, "fake-unresolved-later-cancel")
    cause_id = "dispatch-inflight:" + intent.intent_id
    try:
        record = runtime.submit(intent, lambda current: _ack(current))
        assert record.state is execution.ExecutionState.ACKED
        assert runtime.recovery_coordinator.freeze_status_for(
            runtime.scope.key, intent.intent_id
        ) == "PENDING"
        # Leave the original dispatch unresolved, but exercise the production
        # release-attempt path during the recovery pass below.
        monkeypatch.setattr(
            ManagedExecutionRuntime,
            "_finish_coordinated_freeze_if_ready",
            original_finish,
        )
        runtime.facade.reconcile(
            execution.ProviderObservation(
                intent_id=intent.intent_id,
                state=execution.ExecutionState.CANCELLED,
                provider_order_id=record.provider_order_id,
                filled_quantity=Decimal("0"),
            )
        )

        with pytest.raises(RuntimePluginError) as error:
            runtime.recover()
        assert error.value.code == "DISPATCH_FREEZE_RESOLUTION_FAILED"
        assert runtime.risk_gate.active_freeze_reasons(runtime.risk_scope) == [cause_id]
        assert runtime.recovery_coordinator.freeze_status_for(
            runtime.scope.key, intent.intent_id
        ) == "RELEASE_ATTEMPTED"
    finally:
        runtime.close()


def test_bad_writer_fence_proof_keeps_existing_dispatch_latch(tmp_path: Path) -> None:
    runtime = _compose(tmp_path)
    intent = _intent(runtime)
    create_proof = runtime.fake_dispatch_authority.create_resolution_proof

    def wrong_fence(intent_id: str, risk_gate: Any) -> Any:
        proof = create_proof(intent_id, risk_gate)
        return replace(proof, writer_fence_sha256="f" * 64)

    runtime.fake_dispatch_authority.create_resolution_proof = wrong_fence
    cause_id = "dispatch-inflight:" + intent.intent_id
    try:
        with pytest.raises(RuntimePluginError) as error:
            runtime.submit(intent, lambda current: _ack(current))
        assert error.value.code == "DISPATCH_FREEZE_RESOLUTION_FAILED"
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
    finally:
        runtime.close()


def test_mismatched_typed_receipt_keeps_latch_while_authority_guard_is_held(
    tmp_path: Path,
) -> None:
    runtime = _compose(tmp_path)
    intent = _intent(runtime, "fake-bad-receipt-1")
    authority = runtime.fake_dispatch_authority
    original_guard = authority.dispatch_resolution_guard

    @contextmanager
    def wrong_receipt(proof: Any, *, claim: Any) -> Any:
        with original_guard(proof, claim=claim) as receipt:
            yield replace(receipt, proof_sha256="c" * 64)

    authority.dispatch_resolution_guard = wrong_receipt
    cause_id = "dispatch-inflight:" + intent.intent_id
    try:
        with pytest.raises(RuntimePluginError) as error:
            runtime.submit(intent, lambda current: _ack(current))
        assert error.value.code == "DISPATCH_FREEZE_RESOLUTION_FAILED"
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
    finally:
        runtime.close()


def test_stale_fake_journal_revision_is_rejected_and_latch_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ManagedExecutionRuntime, "_finish_coordinated_freeze_if_ready", lambda *_: None
    )
    runtime = _compose(tmp_path)
    intent = _intent(runtime)
    cause_id = "dispatch-inflight:" + intent.intent_id
    try:
        record = runtime.submit(intent, lambda current: _ack(current))
        assert record.state is execution.ExecutionState.ACKED
        proof = runtime.fake_dispatch_authority.create_resolution_proof(
            intent.intent_id, runtime.risk_gate
        )
        with sqlite3.connect(runtime.state_directory / "fake_dispatch.sqlite3") as connection:
            connection.execute(
                "UPDATE fake_dispatch_journal SET revision = revision + 1 WHERE intent_id = ?",
                (intent.intent_id,),
            )

        with pytest.raises(risk.PermitInvalidError):
            runtime.risk_gate.resolve_dispatch_freeze(proof)
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
    finally:
        runtime.close()


def test_coordinated_request_hash_edit_to_journal_and_exposure_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ManagedExecutionRuntime,
        "_finish_coordinated_freeze_if_ready",
        lambda *_: None,
    )
    runtime = _compose(tmp_path)
    intent = _intent(runtime, "fake-coordinated-edit-1")
    cause_id = "dispatch-inflight:" + intent.intent_id
    try:
        record = runtime.submit(intent, lambda current: _ack(current))
        assert record.state is execution.ExecutionState.ACKED
        proof = runtime.fake_dispatch_authority.create_resolution_proof(
            intent.intent_id, runtime.risk_gate
        )
        forged_request_sha256 = "e" * 64
        with sqlite3.connect(runtime.state_directory / "fake_dispatch.sqlite3") as connection:
            connection.execute(
                "UPDATE fake_dispatch_journal SET request_sha256 = ? WHERE intent_id = ?",
                (forged_request_sha256, intent.intent_id),
            )
            connection.execute(
                "UPDATE fake_order_exposures SET request_sha256 = ? WHERE intent_id = ?",
                (forged_request_sha256, intent.intent_id),
            )

        with pytest.raises(risk.PermitInvalidError):
            runtime.risk_gate.resolve_dispatch_freeze(proof)
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
    finally:
        runtime.close()


def test_unknown_dispatch_stays_frozen_after_restart_without_redispatch(tmp_path: Path) -> None:
    runtime = _compose(tmp_path)
    intent = _intent(runtime, "fake-unknown-1")
    calls = 0

    def uncertain_dispatch(_current: Any) -> Any:
        nonlocal calls
        calls += 1
        raise TimeoutError("fixture dispatch outcome unavailable")

    try:
        record = runtime.submit(intent, uncertain_dispatch)
        assert record.state is execution.ExecutionState.UNKNOWN
        cause_id = "dispatch-inflight:" + intent.intent_id
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        with pytest.raises(FakeDispatchJournalError):
            runtime.fake_dispatch_authority.create_resolution_proof(
                intent.intent_id, runtime.risk_gate
            )
    finally:
        runtime.close()

    restarted = _compose(tmp_path)
    try:
        restarted.recover()
        assert calls == 1
        assert "dispatch-inflight:" + intent.intent_id in restarted.risk_gate.active_freeze_reasons(
            restarted.risk_scope
        )
        assert restarted.execution_store.get(intent.intent_id, scope=restarted.scope).state is (
            execution.ExecutionState.UNKNOWN
        )
    finally:
        restarted.close()


def test_fill_observation_cannot_be_used_as_acked_tracked_transfer(tmp_path: Path) -> None:
    runtime = _compose(tmp_path)
    intent = _intent(runtime, "fake-fill-cannot-transfer")
    cause_id = "dispatch-inflight:" + intent.intent_id
    fill = execution.ProviderObservation(
        intent_id=intent.intent_id,
        state=execution.ExecutionState.FILLED,
        provider_order_id="fake-fill-id-1",
        filled_quantity=Decimal("2"),
        average_price=Decimal("6"),
    )
    try:
        with pytest.raises(RuntimePluginError) as error:
            runtime.submit(intent, lambda _current: fill)
        assert error.value.code == "DISPATCH_FREEZE_RESOLUTION_FAILED"
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        assert runtime.risk_gate.snapshot(runtime.risk_scope)["increase_count"] == 1
    finally:
        runtime.close()


def test_rejected_zero_fill_with_average_price_is_not_a_terminal_proof(
    tmp_path: Path,
) -> None:
    runtime = _compose(tmp_path)
    intent = _intent(runtime, "fake-rejection-with-price")
    rejected_with_price = execution.ProviderObservation(
        intent_id=intent.intent_id,
        state=execution.ExecutionState.REJECTED,
        average_price=Decimal("6"),
        reason_code="fixture_rejected",
    )
    cause_id = "dispatch-inflight:" + intent.intent_id
    try:
        with pytest.raises(RuntimePluginError) as error:
            runtime.submit(intent, lambda _current: rejected_with_price)
        assert error.value.code == "DISPATCH_FREEZE_RESOLUTION_FAILED"
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
    finally:
        runtime.close()


def test_stable_provider_id_must_match_sdk_execution_projection(tmp_path: Path) -> None:
    runtime = _compose(tmp_path)
    intent = _intent(runtime, "fake-mismatched-provider-id")
    observed_id = _ack(intent, "fake-order-observed-A")
    projected_id = _ack(intent, "fake-order-projected-B")
    cause_id = "dispatch-inflight:" + intent.intent_id
    try:

        def dispatch(current: Any) -> Any:
            runtime.fake_dispatch_authority.record_provider_observation(current, observed_id)
            return projected_id

        record = runtime.facade.submit(intent, dispatch)
        assert record.state is execution.ExecutionState.ACKED
        runtime.fake_dispatch_authority.record_execution_result(intent, record)
        with pytest.raises(FakeDispatchJournalError, match="projection differs"):
            runtime.fake_dispatch_authority.create_resolution_proof(
                intent.intent_id, runtime.risk_gate
            )
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
    finally:
        runtime.close()


def test_missing_dispatch_latch_is_an_explicit_blocker_not_generic_recreated(
    tmp_path: Path,
) -> None:
    runtime = _compose(tmp_path)
    try:
        with pytest.raises(RuntimePluginError) as error:
            runtime._ensure_dispatch_freeze("latch-was-lost")
        assert error.value.code == "MANAGED_RECOVERY_DISPATCH_FREEZE_MISSING"
        assert "dispatch-inflight:latch-was-lost" not in runtime.risk_gate.active_freeze_reasons(
            runtime.risk_scope
        )
    finally:
        runtime.close()


def test_fake_journal_authority_rejects_nonoffline_risk_scope(tmp_path: Path) -> None:
    execution_scope = execution.ExecutionScope(
        provider=_PROVIDER,
        environment="offline",
        account_ref=_ACCOUNT,
        strategy_id=_STRATEGY_ID,
    )
    risk_scope = risk.AccountScope(provider="fake", account_id=_ACCOUNT, environment="sandbox")
    with pytest.raises(FakeDispatchJournalError, match="offline risk environment"):
        ManagedFakeProviderJournalAuthority(
            database_path=tmp_path / "not-created.sqlite3",
            execution_scope=execution_scope,
            risk_scope=risk_scope,
            execution_store=None,
            facade=None,
            risk_types=risk,
        )


def test_unlisted_managed_replay_shape_gets_no_fake_authority(tmp_path: Path) -> None:
    runtime = _compose(tmp_path, strategy_id="example.unlisted.managed_replay")
    try:
        assert runtime.fake_dispatch_authority is None
        assert runtime.risk_scope.provider == _PROVIDER
    finally:
        runtime.close()


def test_fixture_labels_have_separate_local_account_fence_keys() -> None:
    first = execution.ExecutionScope(
        provider="iteration41_managed_replay_fake_provider",
        environment="offline",
        account_ref="iteration41_managed_replay_fake_account",
        strategy_id="example.013_3.sa_midfreq_simnow",
    )
    second = execution.ExecutionScope(
        provider="iteration41_ctp_mechanical_fake_provider",
        environment="offline",
        account_ref="iteration41_ctp_mechanical_fake_account",
        strategy_id="example.ctp_options_simnow.mechanical_managed_replay_l2",
    )
    assert first.account_key != second.account_key
