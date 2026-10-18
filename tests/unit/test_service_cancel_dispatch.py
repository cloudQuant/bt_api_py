from __future__ import annotations

from decimal import Decimal

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    PAPER_ENVIRONMENT,
    PAPER_MODEL_REF,
    PAPER_PROVIDER,
    CancelDispatchClaimReceiptV1,
    CancelObservation,
    ContractValidationError,
    ExecutionAdmissionValidatorRegistry,
    ExecutionBasicAdmissionGate,
    ExecutionProviderValidatorV1,
    ExecutionScope,
    ExecutionState,
    OrderIntent,
    OrderType,
    PaperInstrumentV1,
    PaperMatchingPort,
    PaperOwnerDomainV1,
    PaperQuoteV1,
    PositionEffect,
    ProviderObservation,
    Side,
    SqliteExecutionStore,
    TimeInForce,
)
from bt_api_execution.service_order_entry import (
    OWNER_CANCEL_OPERATION,
    SUBMIT_OPERATION,
    ExecutionOrderEntryService,
)
from bt_api_risk import AccountScope, DurableRiskGate, RiskPolicy

from bt_api_py.service_execution_risk_cancel import RiskExecutionCancelAdmissionGate
from bt_api_py.service_risk_order_journal import RiskOrderJournal

_DOMAIN = "paper:service-cancel-dispatch-test"
_ACCOUNT = "account-cancel-dispatch"
_STRATEGY = "strategy-cancel-dispatch"
_STRATEGY_PRINCIPAL = "strategy-principal-cancel-dispatch"
_OWNER_PRINCIPAL = "owner-principal-cancel-dispatch"
_OWNER_ACTOR = "owner-actor-cancel-dispatch"
_EXECUTION_SCOPE = ExecutionScope(
    "PAPER", "simulation", _ACCOUNT, _STRATEGY, "20261018"
)


def _wire_scope(*, run_id: str | None) -> WireScope:
    return WireScope(
        _DOMAIN,
        account_ref=_ACCOUNT,
        strategy_ref=_STRATEGY,
        run_id=run_id,
    )


def _order_arguments(intent_id: str) -> dict[str, object]:
    return {
        "intent_id": intent_id,
        "signal_id": f"signal-{intent_id}",
        "instrument": "BTC-USDT",
        "side": "BUY",
        "position_effect": "OPEN",
        "order_type": "LIMIT",
        "quantity": "1",
        "price": "50000",
        "time_in_force": "GTC",
        "reduce_only": False,
        "metadata_version": "service-cancel-dispatch-v1",
        "deadline_ns": None,
        "tags": {},
    }


def _identity(role: WireRole, principal_ref: str, actor_ref: str) -> WireIdentity:
    return WireIdentity(role, principal_ref, actor_ref)


class _BaseDispatch:
    """Legacy composite port; submissions stay here when cancel is split out."""

    def __init__(self, events: list[tuple[str, str]]) -> None:
        self.events = events
        self.submitted: list[str] = []
        self.cancelled: list[str] = []

    def submit(self, intent: OrderIntent) -> ProviderObservation:
        self.events.append(("submit", intent.intent_id))
        self.submitted.append(intent.intent_id)
        return ProviderObservation.accepted(
            intent.intent_id, f"provider-{intent.intent_id}"
        )

    def cancel(self, intent) -> CancelObservation:
        self.events.append(("legacy-cancel", intent.cancel_id))
        self.cancelled.append(intent.cancel_id)
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


class _SelectedCancelDispatch:
    def __init__(
        self,
        events: list[tuple[str, str]],
        *,
        fail_first: bool = False,
        replay_observation: CancelObservation | None = None,
    ) -> None:
        self.events = events
        self.fail_first = fail_first
        self.replay_observation = replay_observation
        self.cancelled: list[str] = []
        self.reconciled: list[str] = []

    def cancel(self, intent) -> CancelObservation:
        self.events.append(("selected-cancel", intent.cancel_id))
        self.cancelled.append(intent.cancel_id)
        if self.fail_first:
            raise TimeoutError("synthetic cancel dispatch outcome is unknown")
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )

    def reconcile_cancel(self, intent) -> CancelObservation | None:
        self.events.append(("reconcile-cancel", intent.cancel_id))
        self.reconciled.append(intent.cancel_id)
        return self.replay_observation


class _ClaimAwareCancelDispatch(_SelectedCancelDispatch):
    def __init__(self, events: list[tuple[str, str]]) -> None:
        super().__init__(events)
        self.claim_receipts: list[CancelDispatchClaimReceiptV1] = []

    def cancel(self, _intent) -> CancelObservation:
        raise AssertionError("Risk-protected cancellation must receive its exact claim receipt")

    def cancel_with_claim(
        self, intent, claim_receipt: CancelDispatchClaimReceiptV1
    ) -> CancelObservation:
        assert type(claim_receipt) is CancelDispatchClaimReceiptV1
        self.claim_receipts.append(claim_receipt)
        self.events.append(("selected-cancel", intent.cancel_id))
        self.cancelled.append(intent.cancel_id)
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )

def _open_real_risk_case(
    tmp_path,
    *,
    dispatch: _BaseDispatch,
    selected_cancel: _SelectedCancelDispatch,
    before_cancel,
    quota_admission,
) -> tuple[SqliteExecutionStore, ExecutionOrderEntryService, DurableRiskGate, RiskExecutionCancelAdmissionGate]:
    store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    journal = RiskOrderJournal(
        tmp_path / "risk-journal.sqlite3", writer_id="cancel-dispatch-journal"
    )
    risk_gate = DurableRiskGate(
        tmp_path / "risk.sqlite3",
        RiskPolicy(
            "service-cancel-dispatch-v1",
            Decimal("0"),
            0,
            permit_ttl_seconds=60.0,
            notional_unit="USD",
        ),
        clock=lambda: 100.0,
        execution_journal_authority=journal,
    )
    risk_scope = AccountScope("PAPER", _ACCOUNT, "simulation")
    admission = RiskExecutionCancelAdmissionGate(
        risk_gate,
        journal,
        store,
        risk_scope=risk_scope,
        execution_scope=_EXECUTION_SCOPE,
        wire_scope_template=_wire_scope(run_id=None),
    )
    service = ExecutionOrderEntryService(
        store,
        {_wire_scope(run_id=None): _EXECUTION_SCOPE},
        dispatch,
        cancel_dispatch=selected_cancel,
        cancel_before_dispatch=before_cancel,
        cancel_admission_gate=admission,
        cancel_quota_admission=quota_admission,
        allow_unprotected=True,
        writer_id="service-cancel-dispatch-owner",
    )
    return store, service, risk_gate, admission


def _submit_run_order(
    service: ExecutionOrderEntryService, intent_id: str, *, run_id: str
):
    scope = _wire_scope(run_id=run_id)
    identity = _identity(
        WireRole.STRATEGY_CLIENT, _STRATEGY_PRINCIPAL, _STRATEGY
    )
    return service.handle(
        SUBMIT_OPERATION,
        principal_ref=identity.principal_ref,
        identity=identity,
        scope=scope,
        arguments=_order_arguments(intent_id),
    )


def _owner_cancel(service: ExecutionOrderEntryService, command_id: str, *, run_id: str):
    scope = _wire_scope(run_id=run_id)
    identity = _identity(WireRole.SERVICE, _OWNER_PRINCIPAL, _OWNER_ACTOR)
    return service.handle(
        OWNER_CANCEL_OPERATION,
        principal_ref=identity.principal_ref,
        identity=identity,
        scope=scope,
        arguments={"command_id": command_id},
    )


@pytest.mark.unit
def test_owner_batch_routes_cancel_and_orders_quota_prepare_real_risk_claim_and_dispatch(
    tmp_path, monkeypatch
) -> None:
    events: list[tuple[str, str]] = []
    base = _BaseDispatch(events)
    selected = _ClaimAwareCancelDispatch(events)

    def quota(*args):
        events.append(("quota", args[4]))
        return None

    def prepare(intent):
        events.append(("prepare", intent.cancel_id))

    store, service, risk_gate, admission = _open_real_risk_case(
        tmp_path,
        dispatch=base,
        selected_cancel=selected,
        before_cancel=prepare,
        quota_admission=quota,
    )
    claims: list[str] = []
    claim_receipts: list[CancelDispatchClaimReceiptV1] = []
    original_claim = admission.claim_before_dispatch

    def observed_claim(permit_reference, intent):
        events.append(("risk-claim", intent.cancel_id))
        claims.append(intent.cancel_id)
        receipt = original_claim(permit_reference, intent)
        claim_receipts.append(receipt)
        return receipt

    monkeypatch.setattr(admission, "claim_before_dispatch", observed_claim)
    try:
        submitted = _submit_run_order(service, "batch-order", run_id="run-a")
        assert submitted["ok"] is True
        assert base.submitted == ["batch-order"]
        events.clear()

        first = _owner_cancel(service, "batch-stop", run_id="run-a")
        assert first["ok"] is True
        item = first["result"]["items"][0]
        assert item["cancel_state"] == ExecutionState.ACKED.value
        assert item["dispatch_attempts"] == 1
        assert events == [
            ("quota", "CANCEL"),
            ("prepare", item["cancel_id"]),
            ("risk-claim", item["cancel_id"]),
            ("selected-cancel", item["cancel_id"]),
        ]
        assert claims == [item["cancel_id"]]
        assert selected.cancelled == [item["cancel_id"]]
        assert len(selected.claim_receipts) == 1
        receipt = selected.claim_receipts[0]
        assert receipt is claim_receipts[0]
        cancel_record = store.get_cancel(item["cancel_id"], scope=_EXECUTION_SCOPE)
        assert receipt.cancel_id == cancel_record.cancel_id == item["cancel_id"]
        assert receipt.scope_key == cancel_record.scope_key == _EXECUTION_SCOPE.key
        assert receipt.permit_reference == cancel_record.permit_reference
        assert receipt.intent_fingerprint == cancel_record.payload_sha256
        assert base.cancelled == []

        replay = _owner_cancel(service, "batch-stop", run_id="run-a")
        assert replay["result"]["items"][0]["cancel_id"] == item["cancel_id"]
        assert replay["result"]["items"][0]["cancel_state"] == ExecutionState.ACKED.value
        assert events == [
            ("quota", "CANCEL"),
            ("prepare", item["cancel_id"]),
            ("risk-claim", item["cancel_id"]),
            ("selected-cancel", item["cancel_id"]),
        ]
        assert claims == [item["cancel_id"]]
        assert selected.cancelled == [item["cancel_id"]]
    finally:
        risk_gate.close()
        store.close()


@pytest.mark.unit
def test_owner_batch_prepare_failure_blocks_without_risk_claim_or_cancel_dispatch(
    tmp_path, monkeypatch
) -> None:
    events: list[tuple[str, str]] = []
    base = _BaseDispatch(events)
    selected = _ClaimAwareCancelDispatch(events)

    def quota(*args):
        events.append(("quota", args[4]))
        return None

    def fail_prepare(intent):
        events.append(("prepare", intent.cancel_id))
        raise RuntimeError("synthetic local preparation failed")

    store, service, risk_gate, admission = _open_real_risk_case(
        tmp_path,
        dispatch=base,
        selected_cancel=selected,
        before_cancel=fail_prepare,
        quota_admission=quota,
    )
    claims: list[str] = []
    original_claim = admission.claim_before_dispatch

    def observed_claim(permit_reference, intent):
        claims.append(intent.cancel_id)
        return original_claim(permit_reference, intent)

    monkeypatch.setattr(admission, "claim_before_dispatch", observed_claim)
    try:
        assert _submit_run_order(service, "blocked-order", run_id="run-b")["ok"] is True
        result = _owner_cancel(service, "blocked-stop", run_id="run-b")
        item = result["result"]["items"][0]

        assert item["cancel_state"] == ExecutionState.BLOCKED.value
        assert item["dispatch_attempts"] == 1
        assert item["unknown_reason"] == "cancel_pre_dispatch_guard_failed"
        assert events == [("submit", "blocked-order"), ("quota", "CANCEL"), ("prepare", item["cancel_id"])]
        assert claims == []
        assert selected.cancelled == []
        assert selected.claim_receipts == []
        assert base.cancelled == []
    finally:
        risk_gate.close()
        store.close()


@pytest.mark.unit
def test_unknown_cancel_replay_reads_selected_port_without_repeating_prepare_or_dispatch(
    tmp_path,
) -> None:
    events: list[tuple[str, str]] = []
    base = _BaseDispatch(events)
    # The original dispatcher remains the submission port and retains a
    # cancel method for legacy assembly compatibility; the selected cancel
    # port is the only one that should receive cancellations.
    selected = _SelectedCancelDispatch(
        events,
        fail_first=True,
        replay_observation=CancelObservation.cancelled(
            "cancel-late", "late-order", "provider-late-order"
        ),
    )
    prepared: list[str] = []

    def prepare(intent):
        prepared.append(intent.cancel_id)
        events.append(("prepare", intent.cancel_id))

    store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOrderEntryService(
        store,
        {_wire_scope(run_id=None): _EXECUTION_SCOPE},
        base,
        cancel_dispatch=selected,
        cancel_before_dispatch=prepare,
        allow_unprotected=True,
        writer_id="service-cancel-replay-owner",
    )
    scope = _wire_scope(run_id=None)
    identity = _identity(WireRole.STRATEGY_CLIENT, _STRATEGY_PRINCIPAL, _STRATEGY)
    cancel_arguments = {
        "cancel_id": "cancel-late",
        "target_intent_id": "late-order",
        "provider_order_id": "provider-late-order",
        "metadata_version": "service-cancel-dispatch-v1",
        "tags": {},
    }
    try:
        submitted = service.handle(
            SUBMIT_OPERATION,
            principal_ref=identity.principal_ref,
            identity=identity,
            scope=scope,
            arguments=_order_arguments("late-order"),
        )
        assert submitted["ok"] is True

        first = service.handle(
            "orders.cancel",
            principal_ref=identity.principal_ref,
            identity=identity,
            scope=scope,
            arguments=cancel_arguments,
        )
        assert first["result"]["record"]["state"] == ExecutionState.UNKNOWN.value
        assert first["result"]["record"]["dispatch_attempts"] == 1

        replay = service.handle(
            "orders.cancel",
            principal_ref=identity.principal_ref,
            identity=identity,
            scope=scope,
            arguments=cancel_arguments,
        )
        assert replay["result"]["record"]["state"] == ExecutionState.CANCELLED.value
        assert replay["result"]["record"]["dispatch_attempts"] == 1
        assert prepared == ["cancel-late"]
        assert selected.cancelled == ["cancel-late"]
        assert selected.reconciled == ["cancel-late"]
        assert base.cancelled == []
    finally:
        store.close()


class _PaperQuotes:
    def read_quote(self, instrument: str) -> PaperQuoteV1 | None:
        return None


def _paper_composition(path):
    account = "paper-cancel-account"
    strategy = "paper-cancel-strategy"
    domain_ref = "paper:cancel-dispatch"
    wire_scope = WireScope(domain_ref, account_ref=account, strategy_ref=strategy)
    execution_scope = ExecutionScope(
        PAPER_PROVIDER, PAPER_ENVIRONMENT, account, strategy, "20261018"
    )
    domain = PaperOwnerDomainV1(
        execution_domain_ref=domain_ref,
        simulation_account_ref=account,
        experiment_ref="paper-cancel-experiment",
        strategy_ref=strategy,
        model_ref=PAPER_MODEL_REF,
        model_version="1",
        quote_source_ref="paper-cancel-quotes",
        cash_currency="USDT",
        initial_cash=Decimal("100"),
        initial_fees=Decimal("0"),
        fee_rate=Decimal("0.001"),
        fee_quantum=Decimal("0.0001"),
        max_quote_age_ns=30_000_000_000,
        instruments=(
            PaperInstrumentV1(
                "BTC-USDT", "instrument-v1", "BTC", "USDT", Decimal("0.25"), Decimal("0.01")
            ),
        ),
        opening_positions=(),
    )
    store = SqliteExecutionStore(path)
    paper_port = PaperMatchingPort(domain, _PaperQuotes())
    validator = ExecutionProviderValidatorV1(
        provider_id=PAPER_PROVIDER,
        validator_id="paper-cancel-port-binding",
        version="1",
        dispatch_port=paper_port,
        instrument_map={"BTC-USDT": "BTC-USDT"},
        quantity_steps={"BTC-USDT": Decimal("0.25")},
        max_quantities={"BTC-USDT": Decimal("10")},
        price_ticks={"BTC-USDT": Decimal("0.01")},
        supported_order_types=(OrderType.LIMIT,),
        supported_time_in_force=(TimeInForce.GTC,),
        supported_actions=((Side.BUY, PositionEffect.OPEN),),
        supports_reduce_only=False,
    )
    basic_gate = ExecutionBasicAdmissionGate(
        store, ExecutionAdmissionValidatorRegistry((validator,))
    )
    return store, domain, paper_port, basic_gate, wire_scope, execution_scope


class _SeparateCancelAdapter:
    def cancel(self, _intent):
        raise AssertionError("constructor-only regression must not dispatch")


@pytest.mark.parametrize("explicit_same_port", [False, True])
def test_paper_owner_accepts_omitted_or_exact_same_cancel_port(
    tmp_path, explicit_same_port
) -> None:
    store, domain, paper_port, basic_gate, wire_scope, execution_scope = (
        _paper_composition(tmp_path / f"paper-same-cancel-{explicit_same_port}.sqlite3")
    )
    try:
        service = ExecutionOrderEntryService(
            store,
            {wire_scope: execution_scope},
            paper_port,
            **({"cancel_dispatch": paper_port} if explicit_same_port else {}),
            basic_admission_gate=basic_gate,
            paper_owner_domain=domain,
            writer_id=f"paper-cancel-owner-{explicit_same_port}",
        )
        assert service._cancel_dispatch is paper_port
    finally:
        store.close()


@pytest.mark.unit
def test_paper_owner_rejects_a_distinct_cancel_dispatcher(tmp_path) -> None:
    store, domain, paper_port, basic_gate, wire_scope, execution_scope = (
        _paper_composition(tmp_path / "paper-distinct-cancel.sqlite3")
    )
    try:
        with pytest.raises(ContractValidationError):
            ExecutionOrderEntryService(
                store,
                {wire_scope: execution_scope},
                paper_port,
                cancel_dispatch=_SeparateCancelAdapter(),
                basic_admission_gate=basic_gate,
                paper_owner_domain=domain,
                writer_id="paper-cancel-owner-distinct",
            )
    finally:
        store.close()


@pytest.mark.unit
def test_selected_paper_cancel_port_requires_its_owner_domain(tmp_path) -> None:
    store, domain, paper_port, _basic_gate, _wire_scope, _execution_scope = (
        _paper_composition(tmp_path / "paper-cancel-domain-required.sqlite3")
    )
    del domain
    generic_scope = WireScope("venue:generic", account_ref="generic", strategy_ref="generic")
    generic_execution_scope = ExecutionScope(
        "GENERIC", "simulation", "generic", "generic", "20261018"
    )
    try:
        with pytest.raises(ContractValidationError):
            ExecutionOrderEntryService(
                store,
                {generic_scope: generic_execution_scope},
                _BaseDispatch([]),
                cancel_dispatch=paper_port,
                allow_unprotected=True,
                writer_id="generic-owner-paper-cancel-rejected",
            )
    finally:
        store.close()
