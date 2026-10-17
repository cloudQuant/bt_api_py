"""Local composition checks for the shared account/group execution owner."""

from __future__ import annotations

import threading
import time
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    AccountScopedGroupAdmissionAdapter,
    CancelObservation,
    ExecutionAdmissionValidatorRegistry,
    ExecutionBasicAdmissionGate,
    ExecutionProviderValidatorV1,
    ExecutionScope,
    ExecutionState,
    OrderType,
    PositionEffect,
    ProviderObservation,
    Side,
    SqliteExecutionStore,
    TimeInForce,
)
from bt_api_risk import (
    AccountScope,
    DispatchEvidenceClass,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    ExposureAccountingMode,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskPolicy,
)

from bt_api_py.bt_api import BtApi
from bt_api_py.service_components import (
    ComponentResources,
    ServiceComponentError,
    build_component,
    component_specs,
    validate_components,
)
from bt_api_py.service_execution_account_books import ExecutionAccountOwnerService
from bt_api_py.service_execution_account_group_owner import (
    ExecutionAccountGroupOwnerService,
)
from bt_api_py.service_execution_group import ExecutionGroupOrderEntryService
from bt_api_py.service_execution_group_authority import ExecutionGroupAuthority
from bt_api_py.service_execution_group_client import AcceptedGroupBindingStore
from bt_api_py.service_execution_group_proofs import (
    GroupProofJournal,
    GroupProofRuntimeBundle,
    GroupProofSource,
    GroupProofTarget,
    GroupReservationTarget,
)
from bt_api_py.service_registry import (
    ActiveRegistrationContext,
    ServiceCallContext,
    TrustedGroupResolutionPolicy,
    TrustedGroupWritePolicy,
)
from bt_api_py.service_risk_order_entry import RiskBtApiProviderContract, RiskOrderTarget
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from tests.unit.test_service_host import _service_config


_DOMAIN = "paper:account-group-owner"
_ACCOUNT = "account-a"
_PORTFOLIO = "portfolio-a"
_STRATEGY = "strategy-a"
_INSTANCE = "execution-account-group-owner"
_COORDINATOR = "group-coordinator-principal"


class _SyntheticDispatch:
    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.cancelled: list[str] = []
        self.entered: threading.Event | None = None
        self.release: threading.Event | None = None

    def submit(self, intent):
        if self.entered is not None:
            self.entered.set()
            assert self.release is not None and self.release.wait(3)
        self.submitted.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, "synthetic-order-1")

    def submit_authorized(self, intent, _authorization):
        if self.entered is not None:
            self.entered.set()
            assert self.release is not None and self.release.wait(3)
        self.submitted.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, "synthetic-group-order")

    def cancel(self, intent):
        self.cancelled.append(intent.cancel_id)
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


def _provider_contract() -> RiskBtApiProviderContract:
    return RiskBtApiProviderContract(
        exchange_name="FIXTURE___SPOT",
        profile_id="account-group-owner-fixture-v1",
        order_schema="account-group-owner-order-v1",
        fill_schema="account-group-owner-fill-v1",
        client_order_id_query=True,
        client_order_id_query_unique=True,
        complete_order_snapshot=True,
        complete_fill_history=True,
        provider_accepts_client_order_id=True,
        provider_status_mapping=(
            ("cancelled", "canceled"),
            ("expired", "expired"),
            ("filled", "completed"),
            ("open", "accepted"),
            ("partial", "partial"),
            ("rejected", "rejected"),
        ),
        supported_order_types=("LIMIT",),
        supported_time_in_force=("GTC", "IOC"),
        supported_position_effects=("OPEN",),
        normalizer_schema="bt-api-order-source-provenance-v2",
        order_revision_field="order_revision",
        fill_revision_field="fill_revision",
        fill_watermark_field="fill_watermark",
        fee_amount_field="fee_amount",
        fee_currency_field="fee_currency",
        quote_currency_by_instrument=(("BTC-USD", "USD"),),
    )


class _Runtime:
    def __init__(self, tmp_path: Path, *, close_resource=None) -> None:
        self.tmp_path = tmp_path
        self.account_scope = AccountScope("fixture", "provider-account-a", "simulation")
        self.entry_scope = WireScope(
            _DOMAIN,
            account_ref=_ACCOUNT,
            strategy_ref=_STRATEGY,
            portfolio_ref=_PORTFOLIO,
        )
        self.extra_scope = WireScope(
            _DOMAIN,
            account_ref=_ACCOUNT,
            strategy_ref="strategy-b",
            portfolio_ref=_PORTFOLIO,
        )
        self.execution_scope = ExecutionScope(
            "fixture", "simulation", _ACCOUNT, _STRATEGY, "20261018"
        )
        self.extra_execution_scope = ExecutionScope(
            "fixture", "simulation", _ACCOUNT, "strategy-b", "20261018"
        )
        self.targets = {
            self.entry_scope: self.execution_scope,
            self.extra_scope: self.extra_execution_scope,
        }
        metadata = InstrumentRiskMetadata(
            instrument="BTC-USD",
            metadata_version="account-group-owner-metadata-v1",
            as_of_ns=1,
            expires_at_ns=10,
            tick_size=Decimal("0.01"),
            quantity_step=Decimal("0.001"),
            contract_multiplier=Decimal("1"),
            max_gross_notional=Decimal("100000"),
            quantity_unit="base",
            valuation_unit="USD",
        )
        self.risk_target = RiskOrderTarget(
            account_scope=self.account_scope,
            strategy_id=_STRATEGY,
            exchange_name="FIXTURE___SPOT",
            provider_account_id="provider-account-a",
            registry=InstrumentRiskRegistry((metadata,)),
        )
        self.bt_api = BtApi(debug=False)
        self.store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
        self.dispatch = _SyntheticDispatch()
        self.risk_journal = RiskOrderJournal(
            tmp_path / "risk-order.sqlite3", writer_id=_INSTANCE
        )
        self.proof_journal = GroupProofJournal(
            tmp_path / "group-proof.sqlite3", writer_id=_INSTANCE
        )
        self.proof_target = GroupProofTarget(
            execution_scope=self.execution_scope,
            risk_target=self.risk_target,
            provider_contract=_provider_contract(),
            bt_api=self.bt_api,
            execution_store=self.store,
            risk_order_journal=self.risk_journal,
            evidence_class=DispatchEvidenceClass.SIMULATION_JOURNAL,
        )
        self.proof_targets = MappingProxyType({self.entry_scope: self.proof_target})
        self.proof_source = GroupProofSource(self.proof_journal, self.proof_targets)
        self.risk_gate = DurableRiskGate(
            tmp_path / "risk-gate.sqlite3",
            RiskPolicy(
                "account-group-owner-policy-v1",
                Decimal("100000"),
                max_increase_count=10,
                require_strategy_allocation=False,
                notional_unit="USD",
                exposure_accounting_mode=ExposureAccountingMode.RESERVATIONS_ONLY,
            ),
            execution_journal_authority=self.proof_source,
        )
        self.risk_admission = DurableRiskAdmissionAdapter(
            self.risk_gate,
            InstrumentRiskAdmissionMapper(
                self.account_scope,
                self.risk_target.registry,
                clock_ns=time.time_ns,
                wire_account_ref=_ACCOUNT,
            ),
        )
        self.group_admission = AccountScopedGroupAdmissionAdapter(
            self.risk_admission, account_scope=self.account_scope
        )
        self.reservation_target = GroupReservationTarget(
            self.account_scope, self.risk_admission
        )
        self.bundle = GroupProofRuntimeBundle(
            self.risk_admission,
            self.group_admission,
            MappingProxyType({self.entry_scope: self.reservation_target}),
            self.proof_targets,
            self.proof_source,
        )
        self.accepted_store = AcceptedGroupBindingStore(
            tmp_path / "accepted-groups.sqlite3"
        )
        self.authority = ExecutionGroupAuthority(
            tmp_path / "group-authority.sqlite3",
            completion_verifier=lambda _hold: False,
        )
        self.coordinator_identity = WireIdentity(
            WireRole.SERVICE, _COORDINATOR, "group-coordinator-service"
        )
        self.write_policy = TrustedGroupWritePolicy(
            _INSTANCE,
            _COORDINATOR,
            self.coordinator_identity,
            "group-entry-scope-v1",
            self.entry_scope,
            "run-a",
        )
        self.resolution_policy = TrustedGroupResolutionPolicy(
            _INSTANCE,
            _COORDINATOR,
            self.coordinator_identity,
            "group-entry-scope-v1",
            self.entry_scope,
            "run-a",
            _ACCOUNT,
        )
        self.basic_gate = ExecutionBasicAdmissionGate(
            self.store,
            ExecutionAdmissionValidatorRegistry(
                (
                    ExecutionProviderValidatorV1(
                        provider_id="fixture",
                        validator_id="account-group-owner-basic-v1",
                        version="1",
                        dispatch_port=self.dispatch,
                        instrument_map={"BTC-USD": "BTC-USD"},
                        quantity_steps={"BTC-USD": Decimal("0.001")},
                        max_quantities={"BTC-USD": Decimal("10")},
                        price_ticks={"BTC-USD": Decimal("0.01")},
                        supported_order_types=(OrderType.LIMIT,),
                        supported_time_in_force=(TimeInForce.GTC,),
                        supported_actions=((Side.BUY, PositionEffect.OPEN),),
                        supports_reduce_only=False,
                    ),
                )
            ),
        )
        self.close_resource = close_resource

    def component_config(self):
        config = _service_config(
            {
                "execution": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": _INSTANCE,
                        "bind_ports": ["service.order_entry", "service.order_query"],
                        "options": {"role": "account_group_owner"},
                    },
                }
            },
            service_id="account-group-owner-test",
        )
        return validate_components(component_specs(config.components))[0]

    def resources(self, *, owned: bool = False) -> ComponentResources:
        values = {
            "store": self.store,
            "targets": self.targets,
            "dispatch": self.dispatch,
            "admission": {"basic_admission_gate": self.basic_gate},
            "accepted_group_store": self.accepted_store,
            "group_authority": self.authority.guard,
            "group_runtime_bundle": self.bundle,
        }
        if owned:
            assert self.close_resource is not None
            return ComponentResources.owned(
                values, ready=lambda: True, close=self.close_resource
            )
        return ComponentResources.borrowed(values, ready=lambda: True)

    def build(self, *, owned: bool = False):
        return build_component(
            self.component_config(),
            scope=WireScope(_DOMAIN),
            resource_provider=lambda *_: self.resources(owned=owned),
            trusted_group_write_policies=(self.write_policy,),
            trusted_group_resolution_policies=(self.resolution_policy,),
        )

    def registrations(self):
        entry_context = ServiceCallContext(
            self.coordinator_identity,
            self.entry_scope,
            "coordinator-entry-registration",
            _COORDINATOR,
        )
        query_context = ServiceCallContext(
            self.coordinator_identity,
            self.entry_scope,
            "coordinator-query-registration",
            _COORDINATOR,
        )
        entry = ActiveRegistrationContext(
            entry_context,
            _INSTANCE,
            "service.order_entry",
            "group-entry-scope-v1",
            frozenset({"orders.submit_group_leg", "orders.resolve_group_leg"}),
            frozenset({"orders.submit_group_leg", "orders.resolve_group_leg"}),
            "epoch-v1",
            "policy-v1",
            "requirements-v1",
            time.time() + 60,
            "run-a",
        )
        query = ActiveRegistrationContext(
            query_context,
            _INSTANCE,
            "service.order_query",
            "group-query-scope-v1",
            frozenset({"orders.query_group_leg"}),
            frozenset(),
            "epoch-v1",
            "policy-v1",
            "requirements-v1",
            time.time() + 60,
            "run-a",
        )
        return entry, query, entry_context, query_context

    def close(self) -> None:
        self.risk_gate.close()
        self.authority.close()
        self.accepted_store.close()
        self.proof_journal.close()
        self.store.close()
        self.bt_api.close()


def _ordinary_arguments(intent_id: str) -> dict[str, object]:
    return {
        "intent_id": intent_id,
        "signal_id": f"signal-{intent_id}",
        "instrument": "BTC-USD",
        "side": "BUY",
        "position_effect": "OPEN",
        "order_type": "LIMIT",
        "quantity": "0.2",
        "price": "10",
        "time_in_force": "GTC",
        "reduce_only": False,
        "metadata_version": "account-group-owner-metadata-v1",
        "deadline_ns": None,
        "tags": {},
    }


def test_account_group_owner_builds_real_adapters_and_serializes_both_routes(
    tmp_path: Path,
) -> None:
    runtime = _Runtime(tmp_path)
    component = None
    try:
        prepared = runtime.component_config()
        assert prepared.spec.options == {"role": "account_group_owner"}
        assert prepared.operations_by_port == {
            "service.order_entry": frozenset(
                {
                    "orders.submit",
                    "orders.cancel",
                    "orders.submit_group_leg",
                    "orders.resolve_group_leg",
                    "orders.cancel_group_leg",
                }
            ),
            "service.order_query": frozenset(
                {"orders.query", "orders.query_group_leg", "orders.query_group_cancel"}
            ),
        }
        component = runtime.build()
        assert component.readiness() is True
        capabilities = {item.port: item for item in component.capabilities}
        assert capabilities["service.order_entry"].operations == prepared.operations_by_port[
            "service.order_entry"
        ]
        assert capabilities["service.order_query"].write_operations == frozenset()
        assert component.maintain_registrations is not None
        entry, query, entry_context, query_context = runtime.registrations()
        component.maintain_registrations((entry, query))

        ordinary_context = ServiceCallContext(
            WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-principal-a", _STRATEGY),
            runtime.entry_scope,
            "strategy-registration",
            "strategy-principal-a",
        )
        intent_id = "account-group-owner-ordinary-1"
        ordinary_result: dict[str, object] = {}
        group_result: dict[str, object] = {}
        group_started = threading.Event()
        runtime.dispatch.entered = threading.Event()
        runtime.dispatch.release = threading.Event()

        ordinary_thread = threading.Thread(
            target=lambda: ordinary_result.update(
                component.handlers["orders.submit"](
                    ordinary_context, _ordinary_arguments(intent_id)
                )
            )
        )
        group_thread = threading.Thread(
            target=lambda: (
                group_started.set(),
                group_result.update(
                    component.handlers["orders.query_group_leg"](
                        query_context,
                        {
                            "origin_run_id": "run-a",
                            "intent_id": "not-yet-accepted",
                        },
                    )
                )
            ),
        )
        ordinary_thread.start()
        assert runtime.dispatch.entered.wait(2)
        group_thread.start()
        assert group_started.wait(2)
        assert not group_result
        runtime.dispatch.release.set()
        ordinary_thread.join(3)
        group_thread.join(3)
        assert not ordinary_thread.is_alive() and not group_thread.is_alive()
        assert ordinary_result["ok"] is True
        assert ordinary_result["result"]["record"]["state"] == ExecutionState.ACKED.value
        assert group_result["ok"] is True
        assert runtime.dispatch.submitted == [intent_id]
        assert runtime.store.get_intent(intent_id, scope=runtime.execution_scope) is not None

        # The same owner uses the ordinary directory's second target, while
        # group registration remains limited to the proof-backed first target.
        extra_context = ServiceCallContext(
            WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-principal-b", "strategy-b"),
            runtime.extra_scope,
            "strategy-b-registration",
            "strategy-principal-b",
        )
        runtime.dispatch.release.set()
        extra = component.handlers["orders.submit"](
            extra_context, _ordinary_arguments("account-group-owner-ordinary-2")
        )
        assert extra["ok"] is True
        assert runtime.dispatch.submitted == [intent_id, "account-group-owner-ordinary-2"]

        # A second builder over this same SQLite owner is rejected; closing the
        # first component releases the one shared owner lock.
        with pytest.raises(ServiceComponentError, match="ORDER_OWNER_ALREADY_ACTIVE"):
            runtime.build()
        component.close()
        component = None
        replacement = runtime.build()
        assert replacement.readiness() is True
        replacement.close()
    finally:
        if component is not None:
            component.close()
        runtime.close()


def test_account_group_owner_keeps_owner_lock_until_shared_resource_close_succeeds(
    tmp_path: Path,
) -> None:
    attempts = 0

    def close_resource() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("synthetic close failure")

    runtime = _Runtime(tmp_path, close_resource=close_resource)
    component = None
    try:
        component = runtime.build(owned=True)
        with pytest.raises(RuntimeError, match="synthetic close failure"):
            component.close()
        # The shared resource close did not finish, so a competing component
        # cannot take the SQLite owner lock.
        with pytest.raises(ServiceComponentError, match="ORDER_OWNER_ALREADY_ACTIVE"):
            runtime.build()
        component.close()
        component = None
        assert attempts == 2
        replacement = runtime.build()
        replacement.close()
    finally:
        if component is not None:
            try:
                component.close()
            except Exception:
                pass
        runtime.close()


def test_account_group_owner_constructor_checks_shared_owner_and_subset_targets(
    tmp_path: Path,
) -> None:
    runtime = _Runtime(tmp_path)
    ordinary = ExecutionAccountOwnerService(
        runtime.store,
        runtime.targets,
        runtime.dispatch,
        basic_admission_gate=runtime.basic_gate,
        writer_id=_INSTANCE,
    )
    grouped = ExecutionGroupOrderEntryService(
        runtime.store,
        {runtime.entry_scope: runtime.execution_scope},
        runtime.dispatch,
        runtime.group_admission,
        runtime.accepted_store,
        runtime.authority.guard,
        runtime.proof_source,
        (runtime.write_policy,),
        (runtime.resolution_policy,),
        writer_id=_INSTANCE,
    )
    try:
        owner = ExecutionAccountGroupOwnerService(
            ordinary,
            grouped,
            store=runtime.store,
            targets=runtime.targets,
            dispatch=runtime.dispatch,
            writer_id=_INSTANCE,
        )
        owner.close()
        assert owner.ready is False

        grouped._instance_id = "different-group-writer"
        with pytest.raises(ValueError, match="share one execution owner"):
            ExecutionAccountGroupOwnerService(
                ordinary,
                grouped,
                store=runtime.store,
                targets=runtime.targets,
                dispatch=runtime.dispatch,
                writer_id=_INSTANCE,
            )
        grouped._instance_id = _INSTANCE

        other_store = SqliteExecutionStore(tmp_path / "other.sqlite3")
        try:
            with pytest.raises(ValueError, match="share one execution owner"):
                ExecutionAccountGroupOwnerService(
                    ordinary,
                    grouped,
                    store=other_store,
                    targets=runtime.targets,
                    dispatch=runtime.dispatch,
                    writer_id=_INSTANCE,
                )
        finally:
            other_store.close()
        with pytest.raises(ValueError, match="share one execution owner"):
            ExecutionAccountGroupOwnerService(
                ordinary,
                grouped,
                store=runtime.store,
                targets=runtime.targets,
                dispatch=_SyntheticDispatch(),
                writer_id=_INSTANCE,
            )
        with pytest.raises(ValueError, match="share one execution owner"):
            ExecutionAccountGroupOwnerService(
                ordinary,
                grouped,
                store=runtime.store,
                targets=runtime.targets,
                dispatch=runtime.dispatch,
                writer_id="other-writer",
            )
        mismatched = dict(runtime.targets)
        mismatched[runtime.entry_scope] = ExecutionScope(
            "fixture", "simulation", _ACCOUNT, _STRATEGY, "different-session"
        )
        with pytest.raises(ValueError, match="share one execution owner"):
            ExecutionAccountGroupOwnerService(
                ordinary,
                grouped,
                store=runtime.store,
                targets=mismatched,
                dispatch=runtime.dispatch,
                writer_id=_INSTANCE,
            )
    finally:
        grouped.close()
        runtime.close()
