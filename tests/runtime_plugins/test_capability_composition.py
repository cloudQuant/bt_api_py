"""Tests for the Iteration 41 sealed managed-runtime composition root."""

from __future__ import annotations

import importlib
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from bt_api_py.runtime_plugins import (
    CAPABILITY_EXECUTION,
    CAPABILITY_GATEWAY,
    CAPABILITY_MONITOR,
    CAPABILITY_RISK,
    CAPABILITY_TRANSPORT_ZMQ,
    CapabilityCatalog,
    CapabilityPin,
    ManagedExecutionRuntime,
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


def _digest() -> str:
    return "a" * 64


def _contract(*, preset: str = "managed_live_direct") -> RuntimeCapabilityContract:
    shapes = {
        "local_backtest": ("backtest", "local", None, ()),
        "replay": ("simulation", "offline", None, ()),
        "shadow": ("simulation", "public_read", "read_only", ()),
        "paper": ("simulation", "public_read", "local_simulation", ()),
        "sandbox": ("simulation", "sandbox", None, ()),
        "managed_live_direct": (
            "live",
            "production",
            "managed_execution",
            (CAPABILITY_EXECUTION, CAPABILITY_RISK, CAPABILITY_MONITOR),
        ),
        "managed_live_gateway": (
            "live",
            "production",
            "managed_execution",
            (
                CAPABILITY_EXECUTION,
                CAPABILITY_RISK,
                CAPABILITY_MONITOR,
                CAPABILITY_GATEWAY,
                CAPABILITY_TRANSPORT_ZMQ,
            ),
        ),
    }
    mode, environment, route, capabilities = shapes[preset]
    return RuntimeCapabilityContract(
        strategy_id="example.014_1.ctp_options_lowfreq",
        mode=mode,
        preset=preset,
        environment=environment,
        order_route=route,
        required_capabilities=capabilities,
        effective_digest=_digest(),
    )


@pytest.mark.parametrize(
    "preset", ("local_backtest", "replay", "shadow", "paper", "sandbox", "managed_live_direct")
)
def test_contract_accepts_each_sealed_runtime_shape(preset: str) -> None:
    contract = _contract(preset=preset)

    assert contract.is_managed_execution is (preset == "managed_live_direct")
    assert len(contract.fingerprint()) == 64


def test_contract_allows_only_registered_sandbox_managed_shape() -> None:
    contract = RuntimeCapabilityContract(
        strategy_id="example.014_1.ctp_options_lowfreq",
        mode="simulation",
        preset="sandbox",
        environment="sandbox",
        order_route="managed_execution",
        required_capabilities=(CAPABILITY_EXECUTION, CAPABILITY_RISK, CAPABILITY_MONITOR),
        effective_digest=_digest(),
    )

    assert contract.is_managed_execution is True

    with pytest.raises(ValueError, match="sandbox"):
        RuntimeCapabilityContract(
            strategy_id="example.014_1.ctp_options_lowfreq",
            mode="simulation",
            preset="sandbox",
            environment="sandbox",
            order_route="read_only",
            required_capabilities=(),
            effective_digest=_digest(),
        )


def test_contract_allows_only_sealed_offline_managed_replay_shape() -> None:
    contract = RuntimeCapabilityContract(
        strategy_id="example.013_3.sa_midfreq_simnow",
        mode="simulation",
        preset="replay",
        environment="offline",
        order_route="managed_execution",
        required_capabilities=(CAPABILITY_EXECUTION, CAPABILITY_RISK, CAPABILITY_MONITOR),
        effective_digest=_digest(),
    )

    assert contract.is_managed_execution is True
    assert contract.is_managed_live is False

    with pytest.raises(ValueError, match="replay"):
        RuntimeCapabilityContract(
            strategy_id="example.013_3.sa_midfreq_simnow",
            mode="live",
            preset="replay",
            environment="production",
            order_route="managed_execution",
            required_capabilities=(CAPABILITY_EXECUTION, CAPABILITY_RISK, CAPABILITY_MONITOR),
            effective_digest=_digest(),
        )

    with pytest.raises(ValueError, match="replay"):
        RuntimeCapabilityContract(
            strategy_id="example.013_3.sa_midfreq_simnow",
            mode="simulation",
            preset="replay",
            environment="offline",
            order_route="managed_execution",
            required_capabilities=(CAPABILITY_EXECUTION, CAPABILITY_RISK),
            effective_digest=_digest(),
        )


def test_effective_public_projection_keeps_safe_read_only_routes() -> None:
    contract = RuntimeCapabilityContract.from_effective_public_dict(
        {
            "strategy_id": "example.014_1.ctp_options_lowfreq",
            "mode": "simulation",
            "preset": "paper",
            "environment": "public_read",
            "order_route": "local_simulation",
            "required_capabilities": (),
            "effective_digest": _digest(),
            "display_only": "permitted",
        }
    )

    assert contract.order_route == "local_simulation"
    assert contract.is_managed_execution is False


def test_catalog_fails_before_import_when_an_exact_pin_does_not_match() -> None:
    imported: list[str] = []
    catalog = CapabilityCatalog(
        (CapabilityPin(CAPABILITY_EXECUTION, "bt_api_execution", "sealed.execution", "0.1.0"),),
        importer=lambda name: imported.append(name),
        version_getter=lambda distribution: "0.1.1",
    )

    with pytest.raises(RuntimePluginError, match="version") as caught:
        catalog.load(_contract())

    assert caught.value.code == "CAPABILITY_VERSION_MISMATCH"
    assert imported == []


def test_non_managed_contract_imports_no_optional_capability() -> None:
    imported: list[str] = []
    checked: list[str] = []
    catalog = CapabilityCatalog(
        (CapabilityPin(CAPABILITY_EXECUTION, "bt_api_execution", "sealed.execution", "0.1.0"),),
        importer=lambda name: imported.append(name),
        version_getter=lambda distribution: checked.append(distribution) or "0.1.0",
    )

    loaded = catalog.load(_contract(preset="paper"))

    assert dict(loaded.modules) == {}
    assert imported == []
    assert checked == []


def test_managed_runtime_close_releases_each_closable_component() -> None:
    closed: list[str] = []

    class Closeable:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            closed.append(self.name)

    runtime = ManagedExecutionRuntime(
        contract=object(),
        facade=Closeable("facade"),
        execution_store=Closeable("execution_store"),
        outbox=Closeable("outbox"),
        risk_gate=Closeable("risk_gate"),
        risk_scope=object(),
        scope=object(),
        execution=object(),
        outbox_event_type=object(),
    )

    runtime.close()

    assert closed == ["facade", "outbox", "execution_store", "risk_gate"]


def test_managed_runtime_closes_registered_framework_receipt_before_execution_store() -> None:
    closed: list[str] = []

    class Closeable:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            closed.append(self.name)

    receipt = Closeable("framework_receipt")
    runtime = ManagedExecutionRuntime(
        contract=object(),
        facade=Closeable("facade"),
        execution_store=Closeable("execution_store"),
        outbox=Closeable("outbox"),
        risk_gate=Closeable("risk_gate"),
        risk_scope=object(),
        scope=object(),
        execution=object(),
        outbox_event_type=object(),
    )

    runtime.register_framework_projection_closeable(receipt)
    runtime.register_framework_projection_closeable(receipt)
    runtime.close()

    assert closed == ["framework_receipt", "facade", "outbox", "execution_store", "risk_gate"]


def _source_catalog(monkeypatch: pytest.MonkeyPatch) -> CapabilityCatalog:
    for source in _CAPABILITY_SOURCES:
        monkeypatch.syspath_prepend(str(source))
    return CapabilityCatalog(
        (
            CapabilityPin(CAPABILITY_EXECUTION, "bt_api_execution", "bt_api_execution", "0.1.0"),
            CapabilityPin(CAPABILITY_RISK, "bt_api_risk", "bt_api_risk", "0.1.0"),
            CapabilityPin(CAPABILITY_MONITOR, "bt_api_monitor", "bt_api_monitor", "0.1.0"),
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


def _managed_runtime(
    loaded: object,
    state_directory: Path,
    *,
    max_increase_notional: Decimal = Decimal("100"),
    max_increase_count: int = 3,
    permit_ttl_seconds: float = 30.0,
):
    snapshot = _snapshot()
    return compose_managed_execution(
        loaded,
        state_directory=state_directory,
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="fixture_writer",
        policy_id="fixture_policy",
        max_increase_notional=max_increase_notional,
        max_increase_count=max_increase_count,
        permit_ttl_seconds=permit_ttl_seconds,
        trading_day=snapshot.trading_day,
        instrument_metadata_snapshot=snapshot,
        instrument_clock_ns=lambda: 1_500,
    )


def _bound_metadata(runtime: object) -> dict[str, object]:
    snapshot = runtime.instrument_metadata_snapshot
    assert snapshot is not None
    return {
        "metadata_version": snapshot.metadata_version,
        "tags": {
            "instrument_metadata_digest": snapshot.instrument_digest("fixture/contract"),
            "quantity_unit": snapshot.instrument_metadata("fixture/contract").quantity_unit,
        },
    }


def test_real_capability_stack_persists_admission_before_one_fake_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded = _source_catalog(monkeypatch).load(_contract())
    runtime = _managed_runtime(
        loaded,
        tmp_path,
        max_increase_notional=Decimal("100"),
        max_increase_count=1,
    )
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = execution.OrderIntent.limit(
        intent_id="intent.one",
        scope=runtime.scope,
        signal_id="signal.one",
        instrument="fixture/contract",
        side=execution.Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("10"),
        **_bound_metadata(runtime),
    )
    dispatched: list[str] = []

    def fake_provider(order: object) -> object:
        dispatched.append(order.intent_id)
        return execution.ProviderObservation.accepted(order.intent_id, "provider.order.one")

    record = runtime.submit(intent, fake_provider)
    repeated = runtime.submit(intent, fake_provider)

    assert record.state is execution.ExecutionState.ACKED
    assert repeated.state is execution.ExecutionState.ACKED
    assert dispatched == [intent.intent_id]
    pending = runtime.outbox.read_pending("fixture_monitor", runtime.scope.key)
    assert len(pending) == 1
    assert pending[0].event.data == {
        "intent_id": intent.intent_id,
        "scope_digest": (
            runtime.scope.key[6:]
            if runtime.scope.key.startswith("scope:")
            else runtime.scope.key
        ),
        "state": "ACKED",
    }
    runtime.close()


def test_direct_confirmation_resolves_freeze_after_monitor_fact_and_allows_next_opening(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded = _source_catalog(monkeypatch).load(_contract())
    runtime = _managed_runtime(
        loaded,
        tmp_path,
        max_increase_notional=Decimal("100"),
        max_increase_count=3,
    )
    execution = loaded.require(CAPABILITY_EXECUTION)
    first_intent = execution.OrderIntent.limit(
        intent_id="intent.confirmed",
        scope=runtime.scope,
        signal_id="signal.confirmed",
        instrument="fixture/contract",
        side=execution.Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("10"),
        **_bound_metadata(runtime),
    )
    first_cause_id = "dispatch-inflight:" + first_intent.intent_id
    dispatched: list[str] = []

    def confirmed_provider(order: object) -> object:
        assert "dispatch-inflight:" + order.intent_id in runtime.risk_gate.active_freeze_reasons(
            runtime.risk_scope
        )
        dispatched.append(order.intent_id)
        return execution.ProviderObservation.accepted(order.intent_id, "provider.order.1")

    try:
        first = runtime.submit(first_intent, confirmed_provider)

        second_intent = execution.OrderIntent.limit(
            intent_id="intent.confirmed.next",
            scope=runtime.scope,
            signal_id="signal.confirmed.next",
            instrument="fixture/contract",
            side=execution.Side.BUY,
            quantity=Decimal("1"),
            price=Decimal("10"),
            **_bound_metadata(runtime),
        )
        second = runtime.submit(second_intent, confirmed_provider)

        assert runtime.contract is loaded.contract
        assert runtime.risk_scope.account_id == "fixture_account"
        assert first.state is execution.ExecutionState.ACKED
        assert second.state is execution.ExecutionState.ACKED
        assert dispatched == [first_intent.intent_id, second_intent.intent_id]
        assert first_cause_id not in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        assert (
            "dispatch-inflight:" + second_intent.intent_id
            not in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        )
    finally:
        runtime.close()


def test_unknown_dispatch_freeze_survives_permit_expiry_and_reopen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    permit_ttl_seconds = 0.1
    loaded = _source_catalog(monkeypatch).load(_contract())
    runtime = _managed_runtime(
        loaded,
        tmp_path,
        max_increase_notional=Decimal("100"),
        max_increase_count=3,
        permit_ttl_seconds=permit_ttl_seconds,
    )
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = execution.OrderIntent.limit(
        intent_id="intent.unknown",
        scope=runtime.scope,
        signal_id="signal.unknown",
        instrument="fixture/contract",
        side=execution.Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("10"),
        **_bound_metadata(runtime),
    )
    scope = runtime.risk_scope
    cause_id = "dispatch-inflight:" + intent.intent_id

    def failing_provider(order: object) -> object:
        raise TimeoutError(order.intent_id)

    try:
        record = runtime.facade.submit(intent, failing_provider)

        assert record.state is execution.ExecutionState.UNKNOWN
        assert cause_id in runtime.risk_gate.active_freeze_reasons(scope)
        with pytest.raises(RuntimePluginError) as caught:
            runtime.resolve_confirmed_dispatch_freeze(intent.intent_id)
        assert caught.value.code == "CONTROLLED_FREEZE_RELEASE_REQUIRED"
        assert cause_id in runtime.risk_gate.active_freeze_reasons(scope)
    finally:
        runtime.close()

    time.sleep(0.2)
    risk = loaded.require(CAPABILITY_RISK)
    reopened = risk.DurableRiskGate(
        tmp_path / "risk.sqlite3",
        risk.RiskPolicy(
            policy_id="fixture_policy",
            max_increase_notional=Decimal("100"),
            max_increase_count=3,
            permit_ttl_seconds=permit_ttl_seconds,
        ),
    )
    try:
        assert cause_id in reopened.active_freeze_reasons(scope)
        # A dispatch claim proves possible market exposure.  Permit TTL must
        # not erase it from risk accounting while reconciliation is pending.
        assert reopened.snapshot(scope)["increase_count"] == 1
        with pytest.raises(risk.RiskDeniedError) as caught:
            reopened.reserve(
                risk.RiskIntent(
                    intent_id="after-reopen-increase",
                    scope=scope,
                    action=risk.IntentAction.INCREASE,
                    notional=Decimal("1"),
                    payload_fingerprint=_digest(),
                )
            )
        assert caught.value.code == "FROZEN"
        for action in (risk.IntentAction.REDUCE, risk.IntentAction.CANCEL):
            permit = reopened.reserve(
                risk.RiskIntent(
                    intent_id="after-reopen-" + action.value,
                    scope=scope,
                    action=action,
                    payload_fingerprint=_digest(),
                )
            )
            assert permit.action is action
    finally:
        reopened.close()


def test_reconciled_evidence_never_automatically_releases_dispatch_freeze(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded = _source_catalog(monkeypatch).load(_contract())
    runtime = _managed_runtime(
        loaded,
        tmp_path,
        max_increase_notional=Decimal("100"),
        max_increase_count=3,
    )
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = execution.OrderIntent.limit(
        intent_id="intent.reconciled",
        scope=runtime.scope,
        signal_id="signal.reconciled",
        instrument="fixture/contract",
        side=execution.Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("10"),
        **_bound_metadata(runtime),
    )
    cause_id = "dispatch-inflight:" + intent.intent_id

    def failing_provider(order: object) -> object:
        raise TimeoutError(order.intent_id)

    try:
        assert (
            runtime.facade.submit(intent, failing_provider).state
            is execution.ExecutionState.UNKNOWN
        )
        reconciled = runtime.facade.reconcile(
            execution.ProviderObservation.accepted(intent.intent_id, "provider.order.reconciled")
        )

        assert reconciled.state is execution.ExecutionState.ACKED
        assert reconciled.review_required is True
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        with pytest.raises(RuntimePluginError) as caught:
            runtime.resolve_confirmed_dispatch_freeze(intent.intent_id)
        assert caught.value.code == "CONTROLLED_FREEZE_RELEASE_REQUIRED"
    finally:
        runtime.close()


def test_monitor_outbox_failure_keeps_known_dispatch_freeze(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded = _source_catalog(monkeypatch).load(_contract())
    runtime = _managed_runtime(
        loaded,
        tmp_path,
        max_increase_notional=Decimal("100"),
        max_increase_count=3,
    )
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = execution.OrderIntent.limit(
        intent_id="intent.monitor-failure",
        scope=runtime.scope,
        signal_id="signal.monitor-failure",
        instrument="fixture/contract",
        side=execution.Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("10"),
        **_bound_metadata(runtime),
    )
    cause_id = "dispatch-inflight:" + intent.intent_id

    def failing_append(event: object) -> object:
        raise OSError(event.event_id)

    monkeypatch.setattr(runtime.outbox, "append", failing_append)
    try:
        with pytest.raises(RuntimePluginError) as caught:
            runtime.submit(
                intent,
                lambda order: execution.ProviderObservation.accepted(
                    order.intent_id, "provider.order.1"
                ),
            )

        assert caught.value.code == "MONITOR_OUTBOX_UNCONFIRMED"
        assert runtime.facade.get(intent.intent_id).state is execution.ExecutionState.ACKED
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
    finally:
        runtime.close()


def test_auto_freeze_resolution_failure_is_explicit_and_keeps_freeze(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded = _source_catalog(monkeypatch).load(_contract())
    runtime = _managed_runtime(
        loaded,
        tmp_path,
        max_increase_notional=Decimal("100"),
        max_increase_count=3,
    )
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = execution.OrderIntent.limit(
        intent_id="intent.resolve-failure",
        scope=runtime.scope,
        signal_id="signal.resolve-failure",
        instrument="fixture/contract",
        side=execution.Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("10"),
        **_bound_metadata(runtime),
    )
    cause_id = "dispatch-inflight:" + intent.intent_id

    def failing_resolve(scope: object, cause: str) -> None:
        raise OSError(cause)

    monkeypatch.setattr(runtime.risk_gate, "resolve_freeze", failing_resolve)
    try:
        with pytest.raises(RuntimePluginError) as caught:
            runtime.submit(
                intent,
                lambda order: execution.ProviderObservation.accepted(
                    order.intent_id, "provider.order.1"
                ),
            )

        assert caught.value.code == "DISPATCH_FREEZE_RESOLUTION_FAILED"
        assert runtime.facade.get(intent.intent_id).state is execution.ExecutionState.ACKED
        assert cause_id in runtime.risk_gate.active_freeze_reasons(runtime.risk_scope)
        assert len(runtime.outbox.read_pending("fixture_monitor", runtime.scope.key)) == 1
    finally:
        runtime.close()


def test_unproven_market_opening_blocks_before_fake_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded = _source_catalog(monkeypatch).load(_contract())
    runtime = _managed_runtime(
        loaded,
        tmp_path,
        max_increase_notional=Decimal("100"),
        max_increase_count=1,
    )
    execution = loaded.require(CAPABILITY_EXECUTION)
    intent = execution.OrderIntent(
        intent_id="intent.market",
        scope=runtime.scope,
        signal_id="signal.market",
        instrument="fixture/contract",
        side=execution.Side.BUY,
        position_effect=execution.PositionEffect.OPEN,
        order_type=execution.OrderType.MARKET,
        quantity=Decimal("1"),
        **_bound_metadata(runtime),
    )
    dispatched: list[object] = []

    record = runtime.submit(intent, lambda order: dispatched.append(order))

    assert record.state is execution.ExecutionState.BLOCKED
    assert dispatched == []


def test_gateway_managed_contract_fails_before_capability_loading(tmp_path: Path) -> None:
    required: list[str] = []
    contract = _contract(preset="managed_live_gateway")
    loaded = SimpleNamespace(
        contract=contract,
        require=lambda capability: required.append(capability) or object(),
    )

    with pytest.raises(RuntimePluginError) as caught:
        compose_managed_execution(
            loaded,
            state_directory=tmp_path,
            provider="fixture_provider",
            environment="production",
            account_ref="fixture_account",
            strategy_id=contract.strategy_id,
            writer_id="fixture_writer",
            policy_id="fixture_policy",
            max_increase_notional=Decimal("1"),
            max_increase_count=1,
        )

    assert caught.value.code == "GATEWAY_DISPATCH_UNSUPPORTED"
    assert required == []


def test_managed_live_requires_a_sealed_metadata_snapshot_before_capability_construction(
    tmp_path: Path,
) -> None:
    contract = _contract()
    required: list[str] = []
    loaded = SimpleNamespace(
        contract=contract,
        require=lambda capability: required.append(capability) or object(),
    )

    with pytest.raises(RuntimePluginError) as caught:
        compose_managed_execution(
            loaded,
            state_directory=tmp_path,
            provider="fixture_provider",
            environment="production",
            account_ref="fixture_account",
            strategy_id=contract.strategy_id,
            writer_id="fixture_writer",
            policy_id="fixture_policy",
            max_increase_notional=Decimal("1"),
            max_increase_count=1,
        )

    assert caught.value.code == "INSTRUMENT_SNAPSHOT_REQUIRED"
    assert required == []


def test_non_offline_managed_sandbox_requires_sealed_metadata_before_capability_construction(
    tmp_path: Path,
) -> None:
    """A future sandbox provider route cannot fall back to quantity * price."""

    contract = RuntimeCapabilityContract(
        strategy_id="example.014_1.ctp_options_lowfreq",
        mode="simulation",
        preset="sandbox",
        environment="sandbox",
        order_route="managed_execution",
        required_capabilities=(CAPABILITY_EXECUTION, CAPABILITY_RISK, CAPABILITY_MONITOR),
        effective_digest=_digest(),
    )
    required: list[str] = []
    loaded = SimpleNamespace(
        contract=contract,
        require=lambda capability: required.append(capability) or object(),
    )

    with pytest.raises(RuntimePluginError) as caught:
        compose_managed_execution(
            loaded,
            state_directory=tmp_path,
            provider="fixture_provider",
            environment="sandbox",
            account_ref="fixture_account",
            strategy_id=contract.strategy_id,
            writer_id="fixture_writer",
            policy_id="fixture_policy",
            max_increase_notional=Decimal("1"),
            max_increase_count=1,
        )

    assert caught.value.code == "INSTRUMENT_SNAPSHOT_REQUIRED"
    assert required == []


def test_offline_managed_replay_is_not_promoted_to_live_snapshot_requirements(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    contract = RuntimeCapabilityContract(
        strategy_id="example.013_3.sa_midfreq_simnow",
        mode="simulation",
        preset="replay",
        environment="offline",
        order_route="managed_execution",
        required_capabilities=(CAPABILITY_EXECUTION, CAPABILITY_RISK, CAPABILITY_MONITOR),
        effective_digest=_digest(),
    )
    loaded = _source_catalog(monkeypatch).load(contract)

    runtime = compose_managed_execution(
        loaded,
        state_directory=tmp_path,
        provider="fixture_provider",
        environment="offline",
        account_ref="fixture_account",
        strategy_id=contract.strategy_id,
        writer_id="fixture_writer",
        policy_id="fixture-policy",
        max_increase_notional=Decimal("10"),
        max_increase_count=1,
    )
    try:
        assert runtime.instrument_metadata_snapshot is None
        assert runtime.instrument_admission is None
    finally:
        runtime.close()


def test_composition_rejects_scope_downgrade_before_any_provider_is_available(
    tmp_path: Path,
) -> None:
    contract = _contract()
    loaded = SimpleNamespace(contract=contract, require=lambda capability: object())

    with pytest.raises(RuntimePluginError, match="environment") as caught:
        compose_managed_execution(
            loaded,
            state_directory=tmp_path,
            provider="fixture_provider",
            environment="sandbox",
            account_ref="fixture_account",
            strategy_id=contract.strategy_id,
            writer_id="fixture_writer",
            policy_id="fixture_policy",
            max_increase_notional=Decimal("1"),
            max_increase_count=1,
        )

    assert caught.value.code == "ENVIRONMENT_SCOPE_MISMATCH"
