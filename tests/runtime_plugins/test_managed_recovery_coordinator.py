"""Fault/restart coverage for the Iteration 41 local recovery authority.

These tests use only the provider-neutral fake dispatch port.  They prove local
SQLite recovery semantics; they are not evidence of provider-side atomicity or
remote reconciliation.
"""

from __future__ import annotations

import importlib
import sqlite3
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bt_api_py.runtime_plugins import (
    CAPABILITY_EXECUTION,
    CAPABILITY_MONITOR,
    CAPABILITY_RISK,
    CapabilityCatalog,
    CapabilityPin,
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
        version_getter=lambda _: "0.1.0",
    )


def _snapshot() -> SealedNormalizedInstrumentMetadataSnapshot:
    """Return a code-owned fake-provider snapshot for managed-live fault tests."""

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


def _runtime(
    monkeypatch: pytest.MonkeyPatch,
    state_directory: Path,
    *,
    writer_id: str,
) -> tuple[Any, Any]:
    loaded = _catalog(monkeypatch).load(_contract())
    snapshot = _snapshot()
    runtime = compose_managed_execution(
        loaded,
        state_directory=state_directory,
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id=_contract().strategy_id,
        writer_id=writer_id,
        policy_id="recovery-policy",
        max_increase_notional=Decimal("100"),
        max_increase_count=3,
        trading_day=snapshot.trading_day,
        instrument_metadata_snapshot=snapshot,
        instrument_clock_ns=lambda: 1_500,
    )
    return loaded, runtime


def _intent(execution: Any, runtime: Any, intent_id: str) -> Any:
    snapshot = runtime.instrument_metadata_snapshot
    assert snapshot is not None
    return execution.OrderIntent.limit(
        intent_id=intent_id,
        scope=runtime.scope,
        signal_id="signal." + intent_id,
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


def test_crash_after_durable_prepare_marks_unknown_on_reopen_and_never_redispatches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fatal interruption after provider entry is recovery-only, never a retry."""

    loaded, runtime = _runtime(monkeypatch, tmp_path, writer_id="writer.initial")
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = _intent(execution, runtime, "intent.crash-before-result")
    dispatched: list[str] = []

    def fatal_provider(order: Any) -> Any:
        dispatched.append(order.intent_id)
        raise SystemExit("simulated process death after provider entry")

    try:
        with pytest.raises(SystemExit, match="simulated process death"):
            runtime.submit(intent, fatal_provider)
        work = runtime.recovery_coordinator.work_for(
            runtime.scope.key, intent.intent_id
        )
        assert work is not None
        assert work.phase == "DISPATCH_PREPARED"
        assert work.scope_key == runtime.scope.key
        assert work.payload_sha256 == intent.fingerprint
        assert (
            runtime.facade.get(intent.intent_id).state
            is execution.ExecutionState.DISPATCHING
        )
    finally:
        runtime.close()

    _, reopened = _runtime(monkeypatch, tmp_path, writer_id="writer.reopened")
    redispatches: list[str] = []
    try:
        report = reopened.recover()
        record = reopened.facade.get(intent.intent_id)
        cause_id = "dispatch-inflight:" + intent.intent_id

        assert report.recovered_unknown_intent_ids == (intent.intent_id,)
        assert report.reconciliation_required_intent_ids == (intent.intent_id,)
        assert record.state is execution.ExecutionState.UNKNOWN
        assert (
            reopened.recovery_coordinator.work_for(
                reopened.scope.key, intent.intent_id
            ).recovery_reason
            == "coordinator_recovery_unknown"
        )
        assert cause_id in reopened.risk_gate.active_freeze_reasons(reopened.risk_scope)
        blocked_open = reopened.submit(
            _intent(execution, reopened, "intent.blocked-by-recovery"),
            lambda order: redispatches.append(order.intent_id),
        )
        assert blocked_open.state is execution.ExecutionState.BLOCKED
        repeated = reopened.submit(
            intent, lambda order: redispatches.append(order.intent_id)
        )
        assert repeated.state is execution.ExecutionState.UNKNOWN
        assert dispatched == [intent.intent_id]
        assert redispatches == []
    finally:
        reopened.close()


def test_restart_rebuilds_monitor_fact_after_provider_result_before_monitor_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A known result survives the gap before the coordinator records its outbox fact."""

    loaded, runtime = _runtime(monkeypatch, tmp_path, writer_id="writer.initial")
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = _intent(execution, runtime, "intent.crash-after-result")
    dispatched: list[str] = []

    def fatal_after_result(*_: Any, **__: Any) -> Any:
        raise SystemExit("simulated process death before monitor journal")

    monkeypatch.setattr(
        runtime.recovery_coordinator, "record_result", fatal_after_result
    )
    try:
        with pytest.raises(SystemExit, match="simulated process death"):
            runtime.submit(
                intent,
                lambda order: (
                    dispatched.append(order.intent_id)
                    or execution.ProviderObservation.accepted(
                        order.intent_id, "provider.order.1"
                    )
                ),
            )
        assert (
            runtime.facade.get(intent.intent_id).state is execution.ExecutionState.ACKED
        )
    finally:
        runtime.close()

    _, reopened = _runtime(monkeypatch, tmp_path, writer_id="writer.reopened")
    redispatches: list[str] = []
    try:
        first = reopened.recover()
        pending = reopened.outbox.read_pending("recovery-monitor", reopened.scope.key)
        event_id = reopened.recovery_coordinator.event_id(
            reopened.scope.key, intent.intent_id, "ACKED"
        )
        cause_id = "dispatch-inflight:" + intent.intent_id

        assert first.emitted_event_ids == (event_id,)
        assert first.resolved_freeze_intent_ids == (intent.intent_id,)
        assert [item.event.event_id for item in pending] == [event_id]
        assert cause_id not in reopened.risk_gate.active_freeze_reasons(
            reopened.risk_scope
        )
        assert reopened.recover().emitted_event_ids == ()
        assert reopened.submit(
            intent, lambda order: redispatches.append(order.intent_id)
        ).state is (execution.ExecutionState.ACKED)
        assert dispatched == [intent.intent_id]
        assert redispatches == []
        assert (
            len(reopened.outbox.read_pending("another-monitor", reopened.scope.key))
            == 1
        )
    finally:
        reopened.close()


def test_monitor_append_failure_is_replayed_idempotently_after_reopen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The local event journal retains a confirmed result when monitor SQLite is unavailable."""

    loaded, runtime = _runtime(monkeypatch, tmp_path, writer_id="writer.initial")
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = _intent(execution, runtime, "intent.monitor-replay")
    cause_id = "dispatch-inflight:" + intent.intent_id

    def reject_append(_: Any) -> Any:
        raise OSError("simulated monitor journal outage")

    monkeypatch.setattr(runtime.outbox, "append", reject_append)
    try:
        with pytest.raises(RuntimePluginError) as caught:
            runtime.submit(
                intent,
                lambda order: execution.ProviderObservation.accepted(
                    order.intent_id, "provider.order.monitor"
                ),
            )
        assert caught.value.code == "MONITOR_OUTBOX_UNCONFIRMED"
        assert (
            runtime.facade.get(intent.intent_id).state is execution.ExecutionState.ACKED
        )
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        assert runtime.recovery_coordinator.pending_events(runtime.scope.key)
    finally:
        runtime.close()

    _, reopened = _runtime(monkeypatch, tmp_path, writer_id="writer.reopened")
    try:
        report = reopened.recover()
        assert len(report.emitted_event_ids) == 1
        assert report.resolved_freeze_intent_ids == (intent.intent_id,)
        assert cause_id not in reopened.risk_gate.active_freeze_reasons(
            reopened.risk_scope
        )
        assert len(reopened.outbox.read_pending("monitor", reopened.scope.key)) == 1
        assert reopened.recovery_coordinator.pending_events(reopened.scope.key) == ()
    finally:
        reopened.close()


def test_independent_runtime_writer_is_fenced_while_provider_dispatch_is_inflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The facade writer lease fences two local runtimes sharing one coordinator journal."""

    loaded, first = _runtime(monkeypatch, tmp_path, writer_id="writer.one")
    _, contender = _runtime(monkeypatch, tmp_path, writer_id="writer.two")
    execution = loaded.require(CAPABILITY_EXECUTION)
    first_intent = _intent(execution, first, "intent.writer-one")
    contender_intent = _intent(execution, contender, "intent.writer-two")
    entered = threading.Event()
    release = threading.Event()
    first_result: list[Any] = []
    first_errors: list[BaseException] = []
    contender_dispatches: list[str] = []

    def blocking_provider(order: Any) -> Any:
        entered.set()
        assert release.wait(timeout=5)
        return execution.ProviderObservation.accepted(
            order.intent_id, "provider.order.one"
        )

    def submit_first() -> None:
        try:
            first_result.append(first.submit(first_intent, blocking_provider))
        except BaseException as error:  # pragma: no cover - asserted below
            first_errors.append(error)

    thread = threading.Thread(target=submit_first)
    thread.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(execution.WriterLeaseUnavailable):
            contender.submit(
                contender_intent,
                lambda order: contender_dispatches.append(order.intent_id),
            )
    finally:
        release.set()
        thread.join(timeout=5)
        first.close()
        contender.close()

    assert not thread.is_alive()
    assert first_errors == []
    assert first_result[0].state is execution.ExecutionState.ACKED
    assert contender_dispatches == []


def test_crash_after_monitor_append_replays_same_event_id_without_duplicate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An append/checkpoint crash is at-least-once with one immutable event identity."""

    loaded, runtime = _runtime(monkeypatch, tmp_path, writer_id="writer.initial")
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = _intent(execution, runtime, "intent.crash-after-monitor-append")
    dispatched: list[str] = []

    def crash_before_coordinator_confirmation(_: str) -> None:
        raise SystemExit("simulated death after monitor outbox append")

    monkeypatch.setattr(
        runtime.recovery_coordinator,
        "_mark_event_emitted",
        crash_before_coordinator_confirmation,
    )
    try:
        with pytest.raises(SystemExit, match="simulated death after monitor"):
            runtime.submit(
                intent,
                lambda order: (
                    dispatched.append(order.intent_id)
                    or execution.ProviderObservation.accepted(
                        order.intent_id, "provider.order.append"
                    )
                ),
            )
        assert (
            len(runtime.outbox.read_pending("before-restart", runtime.scope.key)) == 1
        )
    finally:
        runtime.close()

    _, reopened = _runtime(monkeypatch, tmp_path, writer_id="writer.reopened")
    redispatches: list[str] = []
    try:
        report = reopened.recover()
        event_id = reopened.recovery_coordinator.event_id(
            reopened.scope.key, intent.intent_id, "ACKED"
        )

        assert report.emitted_event_ids == (event_id,)
        assert (
            len(reopened.outbox.read_pending("after-restart", reopened.scope.key)) == 1
        )
        assert reopened.submit(
            intent, lambda order: redispatches.append(order.intent_id)
        ).state is (execution.ExecutionState.ACKED)
        assert dispatched == [intent.intent_id]
        assert redispatches == []
    finally:
        reopened.close()


def test_known_result_crash_before_risk_settlement_retains_exposure_until_recovery_proves_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A claimed permit cannot expire out of risk accounting after a known result."""

    loaded, runtime = _runtime(monkeypatch, tmp_path, writer_id="writer.initial")
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = _intent(execution, runtime, "intent.known-before-risk-settlement")
    cause_id = "dispatch-inflight:" + intent.intent_id

    # Simulate death after the execution record and coordinator fact commit,
    # but before the facade's normal risk settlement / runtime finish path.
    monkeypatch.setattr(
        runtime.facade,
        "_settle_after_evidenced_outcome",
        lambda record, **_kwargs: record,
    )
    def crash_after_result(*_args: Any, **_kwargs: Any) -> tuple[str, ...]:
        if runtime.recovery_coordinator.pending_events(runtime.scope.key):
            raise SystemExit("simulated death before risk settlement")
        return ()

    monkeypatch.setattr(
        runtime.recovery_coordinator, "append_pending_monitor_events", crash_after_result
    )
    try:
        with pytest.raises(SystemExit, match="before risk settlement"):
            runtime.submit(
                intent,
                lambda order: execution.ProviderObservation.accepted(
                    order.intent_id, "provider.order.known"
                ),
            )
        record = runtime.facade.get(intent.intent_id)
        work = runtime.recovery_coordinator.work_for(runtime.scope.key, intent.intent_id)
        assert record.state is execution.ExecutionState.ACKED
        assert work.risk_settlement_status == "PENDING"
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)

        # Force the permit's deadline into the past.  Calling snapshot performs
        # the TTL sweep, which must retain a dispatch-claimed reservation.
        with sqlite3.connect(tmp_path / "risk.sqlite3") as connection:
            connection.execute("UPDATE risk_reservations SET expires_at = 0")
        assert runtime.risk_gate.snapshot(runtime.risk_scope)["increase_count"] == 1
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
    finally:
        runtime.close()

    _, reopened = _runtime(monkeypatch, tmp_path, writer_id="writer.reopened")
    try:
        report = reopened.recover()
        work = reopened.recovery_coordinator.work_for(reopened.scope.key, intent.intent_id)

        assert report.resolved_freeze_intent_ids == (intent.intent_id,)
        assert work.risk_settlement_status == "SETTLED"
        assert reopened.risk_gate.snapshot(reopened.risk_scope)["increase_count"] == 1
        assert cause_id not in reopened.risk_gate.active_freeze_reasons(reopened.risk_scope)
    finally:
        reopened.close()


def test_recovery_discovers_dispatching_record_left_before_prepare_hook_without_provider_io(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pre-hook crash becomes UNKNOWN/reconcile instead of stranded DISPATCHING."""

    loaded, runtime = _runtime(monkeypatch, tmp_path, writer_id="writer.initial")
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = _intent(execution, runtime, "intent.prepare-hook-gap")
    provider_calls: list[str] = []

    monkeypatch.setattr(
        runtime.recovery_coordinator,
        "prepare_dispatch",
        lambda _intent: (_ for _ in ()).throw(SystemExit("simulated pre-hook crash")),
    )
    try:
        with pytest.raises(SystemExit, match="pre-hook crash"):
            runtime.submit(
                intent,
                lambda order: provider_calls.append(order.intent_id),
            )
        assert runtime.facade.get(intent.intent_id).state is execution.ExecutionState.DISPATCHING
        assert runtime.recovery_coordinator.work_for(runtime.scope.key, intent.intent_id) is None
        assert provider_calls == []
    finally:
        runtime.close()

    _, reopened = _runtime(monkeypatch, tmp_path, writer_id="writer.reopened")
    retry_calls: list[str] = []
    try:
        report = reopened.recover()
        cause_id = "dispatch-inflight:" + intent.intent_id

        assert report.recovered_unknown_intent_ids == (intent.intent_id,)
        assert report.reconciliation_required_intent_ids == (intent.intent_id,)
        assert reopened.facade.get(intent.intent_id).state is execution.ExecutionState.UNKNOWN
        assert cause_id in reopened.risk_gate.active_freeze_reasons(reopened.risk_scope)
        assert reopened.submit(intent, lambda order: retry_calls.append(order.intent_id)).state is (
            execution.ExecutionState.UNKNOWN
        )
        assert retry_calls == []
    finally:
        reopened.close()


def test_expired_writer_generation_cannot_project_provider_result_after_new_owner_fences_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stale provider thread cannot append ACKED after its lease is replaced."""

    loaded, runtime = _runtime(monkeypatch, tmp_path, writer_id="writer.initial")
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = _intent(execution, runtime, "intent.writer-fenced-after-provider")
    runtime.facade._lease_ttl_ns = 100_000_000  # 100ms; expiry occurs inside provider I/O
    entered = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []
    provider_calls: list[str] = []

    def blocking_provider(order: Any) -> Any:
        provider_calls.append(order.intent_id)
        entered.set()
        assert release.wait(timeout=5)
        return execution.ProviderObservation.accepted(order.intent_id, "provider.order.fenced")

    def submit() -> None:
        try:
            runtime.submit(intent, blocking_provider)
        except BaseException as error:  # asserted after the provider is released
            errors.append(error)

    thread = threading.Thread(target=submit)
    thread.start()
    assert entered.wait(timeout=5)
    try:
        time.sleep(0.15)
        contender = runtime.execution_store.acquire_or_renew_lease(
            runtime.scope,
            "writer.contender",
            ttl_ns=1_000_000_000,
        )
        assert contender.fencing_token > runtime.facade._last_writer_lease.fencing_token
        release.set()
        thread.join(timeout=5)

        assert not thread.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], execution.WriterLeaseUnavailable)
        assert runtime.facade.get(intent.intent_id).state is execution.ExecutionState.DISPATCHING
        # The stale facade close must not delete the contender's generation.
        assert runtime.facade.close() is False
        assert provider_calls == [intent.intent_id]
        assert runtime.execution_store.release_lease(
            runtime.scope,
            "writer.contender",
            fencing_token=contender.fencing_token,
        )
    finally:
        release.set()
        thread.join(timeout=5)
        runtime.close()

    _, reopened = _runtime(monkeypatch, tmp_path, writer_id="writer.reopened")
    try:
        report = reopened.recover()
        assert report.recovered_unknown_intent_ids == (intent.intent_id,)
        assert reopened.facade.get(intent.intent_id).state is execution.ExecutionState.UNKNOWN
    finally:
        reopened.close()
