"""Integration tests for the optional instrument-aware managed admission helper."""

from __future__ import annotations

import importlib
from decimal import Decimal
from pathlib import Path

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
    compose_instrument_risk_admission,
    compose_managed_execution,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CAPABILITY_SOURCES = (
    _REPO_ROOT / "bt_api" / "bt_api_execution" / "src",
    _REPO_ROOT / "bt_api" / "bt_api_risk" / "src",
    _REPO_ROOT / "bt_api" / "bt_api_monitor" / "src",
)


def _contract(*, managed: bool = True) -> RuntimeCapabilityContract:
    return RuntimeCapabilityContract(
        strategy_id="example.014_1.ctp_options_lowfreq",
        mode="live" if managed else "simulation",
        preset="managed_live_direct" if managed else "replay",
        environment="production" if managed else "offline",
        order_route="managed_execution" if managed else None,
        required_capabilities=(
            CAPABILITY_EXECUTION,
            CAPABILITY_RISK,
            CAPABILITY_MONITOR,
        )
        if managed
        else (),
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


def _metadata(risk: object) -> object:
    return risk.InstrumentRiskMetadata(
        instrument="fixture/contract",
        metadata_version="instrument-v1",
        as_of_ns=1_000,
        expires_at_ns=2_000,
        tick_size=Decimal("0.1"),
        quantity_step=Decimal("0.25"),
        contract_multiplier=Decimal("3"),
        max_gross_notional=Decimal("1_000"),
        taker_fee_bps=Decimal("10"),
        fixed_fee=Decimal("0.2"),
        max_slippage_bps=Decimal("50"),
    )


def _normalized_snapshot_payload(
    *,
    expires_at_ns: int = 2_000,
    trading_day: str = "20260922",
    instrument_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    instrument: dict[str, object] = {
        "instrument": "fixture/contract",
        "tick_size": "0.1",
        "lot_size": "1",
        "contract_multiplier": "3",
        "max_gross_notional_account": "1000",
        "quote_currency": "USDT",
        "fee_currency": "USDT",
        "quote_to_account_fx": "1.25",
        "fee_to_account_fx": "1.5",
        "taker_fee_bps": "10",
        "fixed_fee": "0.2",
        "max_slippage_bps": "50",
        "quantity_unit": "contracts",
        "min_quantity": "1",
        "max_quantity": "10",
    }
    if instrument_overrides:
        instrument.update(instrument_overrides)
    return {
        "provider": "fixture_provider",
        "environment": "production",
        "account_ref": "fixture_account",
        "trading_day": trading_day,
        "metadata_version": "normalized-v1",
        "as_of_ns": 1_000,
        "expires_at_ns": expires_at_ns,
        "account_currency": "USD",
        "instruments": [instrument],
    }


def _sealed_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    snapshot: SealedNormalizedInstrumentMetadataSnapshot,
    clock_ns: int = 1_500,
):
    loaded = _catalog(monkeypatch).load(_contract())
    return loaded, compose_managed_execution(
        loaded,
        state_directory=tmp_path / "managed-state",
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="fixture.writer",
        policy_id="normalized-instrument-policy",
        max_increase_notional=Decimal("2_000"),
        max_increase_count=2,
        trading_day=snapshot.trading_day,
        instrument_metadata_snapshot=snapshot,
        instrument_clock_ns=lambda: clock_ns,
    )


def _sealed_intent(
    execution: object, runtime: object, snapshot: object, **overrides: object
):
    values: dict[str, object] = {
        "intent_id": "intent.normalized",
        "scope": runtime.scope,
        "signal_id": "signal.normalized",
        "instrument": "fixture/contract",
        "side": execution.Side.BUY,
        "quantity": Decimal("2"),
        "price": Decimal("100"),
        "metadata_version": snapshot.metadata_version,
        "tags": {
            "instrument_metadata_digest": snapshot.instrument_digest("fixture/contract"),
            "quantity_unit": snapshot.instrument_metadata("fixture/contract").quantity_unit,
        },
    }
    values.update(overrides)
    return execution.OrderIntent.limit(**values)


def test_helper_installs_exact_metadata_bound_admission_before_provider_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded = _catalog(monkeypatch).load(_contract())
    execution = loaded.require(CAPABILITY_EXECUTION)
    risk = loaded.require(CAPABILITY_RISK)
    execution_scope = execution.ExecutionScope(
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
    )
    risk_scope = risk.AccountScope("fixture_provider", "fixture_account", "production")
    risk_gate = risk.DurableRiskGate(
        tmp_path / "risk.sqlite3",
        risk.RiskPolicy("instrument-policy", Decimal("2_000"), 2),
    )
    metadata = _metadata(risk)
    admission = compose_instrument_risk_admission(
        loaded,
        risk_gate=risk_gate,
        risk_scope=risk_scope,
        metadata=(metadata,),
        clock_ns=lambda: 1_500,
    )
    facade = execution.ManagedExecutionFacade(
        execution.SqliteExecutionStore(tmp_path / "execution.sqlite3"),
        execution_scope,
        writer_id="fixture_writer",
        admission_gate=admission.admission_gate,
    )
    intent = execution.OrderIntent.limit(
        intent_id="intent.instrument",
        scope=execution_scope,
        signal_id="signal.instrument",
        instrument=metadata.instrument,
        side=execution.Side.BUY,
        quantity=Decimal("2"),
        price=Decimal("100"),
        metadata_version=metadata.metadata_version,
        tags={risk.INSTRUMENT_METADATA_DIGEST_TAG: metadata.digest},
    )
    provider_calls: list[str] = []
    try:
        record = facade.submit(
            intent,
            lambda order: (
                provider_calls.append(order.intent_id)
                or execution.ProviderObservation.accepted(
                    order.intent_id, "provider.order.1"
                )
            ),
        )

        assert record.state is execution.ExecutionState.ACKED
        assert provider_calls == [intent.intent_id]
        assert admission.map_execution_intent(intent).notional == Decimal("603.8030")
    finally:
        facade.close()
        risk_gate.close()


def test_missing_metadata_digest_blocks_facade_before_provider_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded = _catalog(monkeypatch).load(_contract())
    execution = loaded.require(CAPABILITY_EXECUTION)
    risk = loaded.require(CAPABILITY_RISK)
    execution_scope = execution.ExecutionScope(
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
    )
    risk_scope = risk.AccountScope("fixture_provider", "fixture_account", "production")
    risk_gate = risk.DurableRiskGate(
        tmp_path / "risk.sqlite3",
        risk.RiskPolicy("instrument-policy", Decimal("2_000"), 2),
    )
    metadata = _metadata(risk)
    admission = compose_instrument_risk_admission(
        loaded,
        risk_gate=risk_gate,
        risk_scope=risk_scope,
        metadata=(metadata,),
        clock_ns=lambda: 1_500,
    )
    facade = execution.ManagedExecutionFacade(
        execution.SqliteExecutionStore(tmp_path / "execution.sqlite3"),
        execution_scope,
        writer_id="fixture_writer",
        admission_gate=admission.admission_gate,
    )
    intent = execution.OrderIntent.limit(
        intent_id="intent.no-digest",
        scope=execution_scope,
        signal_id="signal.no-digest",
        instrument=metadata.instrument,
        side=execution.Side.BUY,
        quantity=Decimal("2"),
        price=Decimal("100"),
        metadata_version=metadata.metadata_version,
    )
    provider_calls: list[str] = []
    try:
        record = facade.submit(
            intent,
            lambda order: (
                provider_calls.append(order.intent_id)
                or execution.ProviderObservation.accepted(
                    order.intent_id, "provider.order.1"
                )
            ),
        )

        assert record.state is execution.ExecutionState.BLOCKED
        assert provider_calls == []
    finally:
        facade.close()
        risk_gate.close()


def test_helper_rejects_non_managed_contract_without_loading_optional_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = _catalog(monkeypatch).load(_contract(managed=False))

    with pytest.raises(RuntimePluginError) as caught:
        compose_instrument_risk_admission(
            loaded,
            risk_gate=object(),
            risk_scope=object(),
            metadata=(),
        )

    assert caught.value.code == "MANAGED_CONTRACT_REQUIRED"


def test_helper_is_a_declared_runtime_plugin_public_export() -> None:
    import bt_api_py.runtime_plugins as runtime_plugins

    assert "InstrumentRiskAdmission" in runtime_plugins.__all__
    assert "NormalizedInstrumentMetadata" in runtime_plugins.__all__
    assert "SealedNormalizedInstrumentMetadataSnapshot" in runtime_plugins.__all__
    assert "compose_instrument_risk_admission" in runtime_plugins.__all__
    assert (
        runtime_plugins.compose_instrument_risk_admission
        is compose_instrument_risk_admission
    )


def test_sealed_normalized_snapshot_binds_lot_fee_fx_and_trading_day_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
        _normalized_snapshot_payload()
    )
    loaded, runtime = _sealed_runtime(monkeypatch, tmp_path, snapshot=snapshot)
    execution = loaded.require(CAPABILITY_EXECUTION)
    provider_calls: list[str] = []
    try:
        assert runtime.instrument_metadata_snapshot is snapshot
        assert runtime.instrument_admission.snapshot is snapshot
        intent = _sealed_intent(execution, runtime, snapshot)
        mapped = runtime.instrument_admission.map_execution_intent(intent)

        # 2 * ceil(100 * 1.005, 0.1) * 3 * 1.25 + 10 bps fee
        # plus 0.2 USDT * 1.5 USD/USDT fixed fee.
        assert mapped.notional == Decimal("754.80375")
        record = runtime.facade.submit(
            intent,
            lambda order: (
                provider_calls.append(order.intent_id)
                or execution.ProviderObservation.accepted(
                    order.intent_id, "provider.order.1"
                )
            ),
        )

        assert record.state is execution.ExecutionState.ACKED
        assert provider_calls == [intent.intent_id]
        assert runtime.instrument_admission.metadata_digest_for(
            intent.instrument
        ) == snapshot.instrument_digest(intent.instrument)
    finally:
        runtime.close()


def test_missing_normalized_metadata_digest_blocks_before_fake_provider_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
        _normalized_snapshot_payload()
    )
    loaded, runtime = _sealed_runtime(monkeypatch, tmp_path, snapshot=snapshot)
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = _sealed_intent(execution, runtime, snapshot, tags={})
    provider_calls: list[str] = []
    try:
        with pytest.raises(RuntimePluginError) as caught:
            runtime.instrument_admission.map_execution_intent(intent)
        assert caught.value.code == "INSTRUMENT_SNAPSHOT_METADATA_DIGEST_REQUIRED"

        record = runtime.facade.submit(
            intent,
            lambda order: (
                provider_calls.append(order.intent_id)
                or execution.ProviderObservation.accepted(
                    order.intent_id, "provider.order.1"
                )
            ),
        )

        assert record.state is execution.ExecutionState.BLOCKED
        assert record.dispatch_attempts == 0
        assert provider_calls == []
    finally:
        runtime.close()


def test_live_snapshot_requires_quantity_unit_and_rejects_mutated_intent_unit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Quantity semantics are sealed facts, never mutable bridge metadata."""

    incomplete = _normalized_snapshot_payload()
    incomplete["instruments"][0].pop("quantity_unit")
    snapshot_without_unit = SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
        incomplete
    )
    with pytest.raises(RuntimePluginError) as missing_unit:
        _sealed_runtime(monkeypatch, tmp_path / "missing-unit", snapshot=snapshot_without_unit)
    assert missing_unit.value.code == "INSTRUMENT_SNAPSHOT_QUANTITY_UNIT_REQUIRED"

    snapshot = SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
        _normalized_snapshot_payload()
    )
    loaded, runtime = _sealed_runtime(monkeypatch, tmp_path / "mismatch", snapshot=snapshot)
    execution = loaded.require(CAPABILITY_EXECUTION)
    provider_calls: list[str] = []
    intent = _sealed_intent(
        execution,
        runtime,
        snapshot,
        tags={
            "instrument_metadata_digest": snapshot.instrument_digest("fixture/contract"),
            "quantity_unit": "base",
        },
    )
    try:
        with pytest.raises(RuntimePluginError) as caught:
            runtime.instrument_admission.map_execution_intent(intent)
        assert caught.value.code == "INSTRUMENT_SNAPSHOT_QUANTITY_UNIT_MISMATCH"

        record = runtime.facade.submit(
            intent,
            lambda order: (
                provider_calls.append(order.intent_id)
                or execution.ProviderObservation.accepted(
                    order.intent_id, "provider.order.quantity-unit"
                )
            ),
        )
        assert record.state is execution.ExecutionState.BLOCKED
        assert record.dispatch_attempts == 0
        assert provider_calls == []
    finally:
        runtime.close()


def test_stale_normalized_metadata_blocks_before_fake_provider_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
        _normalized_snapshot_payload(expires_at_ns=1_500)
    )
    loaded, runtime = _sealed_runtime(
        monkeypatch, tmp_path, snapshot=snapshot, clock_ns=1_500
    )
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = _sealed_intent(execution, runtime, snapshot)
    provider_calls: list[str] = []
    try:
        with pytest.raises(RuntimePluginError) as caught:
            runtime.instrument_admission.map_execution_intent(intent)
        assert caught.value.code == "INSTRUMENT_SNAPSHOT_METADATA_STALE"

        record = runtime.facade.submit(
            intent,
            lambda order: (
                provider_calls.append(order.intent_id)
                or execution.ProviderObservation.accepted(
                    order.intent_id, "provider.order.1"
                )
            ),
        )

        assert record.state is execution.ExecutionState.BLOCKED
        assert record.dispatch_attempts == 0
        assert provider_calls == []
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "mutator",
    (
        lambda payload: payload.pop("trading_day"),
        lambda payload: payload["instruments"][0].pop("quote_to_account_fx"),
        lambda payload: payload["instruments"][0].pop("fee_to_account_fx"),
    ),
)
def test_incomplete_normalized_provider_payload_never_creates_a_dispatchable_snapshot(
    mutator: object,
) -> None:
    payload = _normalized_snapshot_payload()
    mutator(payload)

    with pytest.raises(ValueError):
        SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(payload)


def test_normalized_provider_snapshot_rejects_noncanonical_trading_day() -> None:
    with pytest.raises(ValueError):
        SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
            _normalized_snapshot_payload(trading_day="untrusted-day")
        )


def test_serialized_normalized_snapshot_round_trips_with_schema_and_account_currency() -> None:
    snapshot = SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
        _normalized_snapshot_payload()
    )

    reloaded = SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
        snapshot.to_payload()
    )

    assert reloaded == snapshot
    assert reloaded.digest == snapshot.digest


def test_serialized_normalized_snapshot_rejects_unknown_schema_or_record_account_currency() -> None:
    snapshot = SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
        _normalized_snapshot_payload()
    )
    wrong_schema = snapshot.to_payload()
    wrong_schema["schema"] = "untrusted.snapshot.v9"

    with pytest.raises(ValueError, match="schema"):
        SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(wrong_schema)

    wrong_currency = snapshot.to_payload()
    wrong_currency["instruments"][0]["account_currency"] = "EUR"
    with pytest.raises(ValueError, match="account currency"):
        SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(wrong_currency)


@pytest.mark.parametrize(
    ("trading_day", "instrument_overrides"),
    (
        ("20260923", {}),
        ("20260922", {"lot_size": "2"}),
        ("20260922", {"contract_multiplier": "4"}),
        ("20260922", {"taker_fee_bps": "11"}),
        ("20260922", {"quote_to_account_fx": "1.30"}),
        ("20260922", {"fee_to_account_fx": "1.60"}),
        ("20260922", {"quantity_unit": "base"}),
    ),
)
def test_snapshot_digest_changes_when_bound_provider_facts_change(
    trading_day: str, instrument_overrides: dict[str, object]
) -> None:
    baseline = SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
        _normalized_snapshot_payload()
    )
    changed = SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
        _normalized_snapshot_payload(
            trading_day=trading_day,
            instrument_overrides=instrument_overrides,
        )
    )

    assert changed.digest != baseline.digest
    assert changed.instrument_digest("fixture/contract") != baseline.instrument_digest(
        "fixture/contract"
    )


def test_old_snapshot_digest_blocks_after_fx_or_trading_day_change_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prior = SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
        _normalized_snapshot_payload()
    )
    current = SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
        _normalized_snapshot_payload(
            trading_day="20260923", instrument_overrides={"quote_to_account_fx": "1.30"}
        )
    )
    loaded, runtime = _sealed_runtime(monkeypatch, tmp_path, snapshot=current)
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = _sealed_intent(
        execution,
        runtime,
        current,
        tags={
            "instrument_metadata_digest": prior.instrument_digest("fixture/contract"),
            "quantity_unit": prior.instrument_metadata("fixture/contract").quantity_unit,
        },
    )
    provider_calls: list[str] = []
    try:
        with pytest.raises(RuntimePluginError) as caught:
            runtime.instrument_admission.map_execution_intent(intent)
        assert caught.value.code == "INSTRUMENT_SNAPSHOT_METADATA_DIGEST_MISMATCH"

        record = runtime.facade.submit(
            intent,
            lambda order: (
                provider_calls.append(order.intent_id)
                or execution.ProviderObservation.accepted(
                    order.intent_id, "provider.order.1"
                )
            ),
        )

        assert record.state is execution.ExecutionState.BLOCKED
        assert record.dispatch_attempts == 0
        assert provider_calls == []
    finally:
        runtime.close()
