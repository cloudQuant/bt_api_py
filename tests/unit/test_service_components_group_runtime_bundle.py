"""Real local composition coverage for the group runtime bundle adapter."""

from __future__ import annotations

import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    AccountScopedGroupAdmissionAdapter,
    ExecutionScope,
    SqliteExecutionStore,
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
    _group_runtime_bundle,
    build_component,
    component_specs,
    validate_components,
)
from bt_api_py.service_execution_group_authority import ExecutionGroupAuthority
from bt_api_py.service_execution_group_client import AcceptedGroupBindingStore
from bt_api_py.service_execution_group_proofs import (
    GroupProofJournal,
    GroupProofRuntimeBundle,
    GroupProofSource,
    GroupProofSourceError,
    GroupProofTarget,
    GroupReservationTarget,
)
from bt_api_py.service_registry import TrustedGroupResolutionPolicy, TrustedGroupWritePolicy
from bt_api_py.service_risk_order_entry import RiskBtApiProviderContract, RiskOrderTarget
from bt_api_py.service_risk_order_journal import RiskOrderJournal


class _UnusedDispatch:
    def submit(self, _intent: object) -> None:
        raise AssertionError("the component-construction test must not dispatch")

    def cancel(self, _intent: object) -> None:
        raise AssertionError("the component-construction test must not dispatch")


def _provider_contract() -> RiskBtApiProviderContract:
    return RiskBtApiProviderContract(
        exchange_name="FIXTURE___SPOT",
        profile_id="group-components-fixture-v1",
        order_schema="group-components-order-v1",
        fill_schema="group-components-fill-v1",
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


def test_group_bundle_uses_stable_template_binding_and_child_scope_resolver(
    tmp_path: Path,
) -> None:
    account_ref = "account-a"
    strategy_ref = "strategy-a"
    instance_id = "group-account-owner-a"
    principal_ref = "group-coordinator-principal"
    coordinator_identity = WireIdentity(
        WireRole.SERVICE, principal_ref, "group-coordinator-service"
    )
    stable_scope = WireScope(
        "paper:group-components",
        account_ref=account_ref,
        strategy_ref=strategy_ref,
        portfolio_ref="portfolio-a",
    )
    execution_scope = ExecutionScope(
        "fixture", "simulation", account_ref, strategy_ref, "20261016"
    )
    account_scope = AccountScope("fixture", "provider-account-a", "simulation")
    metadata = InstrumentRiskMetadata(
        instrument="BTC-USD",
        metadata_version="group-components-metadata-v1",
        as_of_ns=1,
        expires_at_ns=10,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("100000"),
        quantity_unit="base",
        valuation_unit="USD",
    )
    risk_target = RiskOrderTarget(
        account_scope=account_scope,
        strategy_id=strategy_ref,
        exchange_name="FIXTURE___SPOT",
        provider_account_id="provider-account-a",
        registry=InstrumentRiskRegistry((metadata,)),
    )

    bt_api = BtApi(debug=False)
    execution_store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    risk_order_journal = RiskOrderJournal(
        tmp_path / "risk-order.sqlite3", writer_id="group-components-test"
    )
    proof_journal = GroupProofJournal(
        tmp_path / "group-proof.sqlite3", writer_id="group-components-test"
    )
    risk_gate: DurableRiskGate | None = None
    authority: ExecutionGroupAuthority | None = None
    accepted_store: AcceptedGroupBindingStore | None = None
    component = None
    try:
        proof_target = GroupProofTarget(
            execution_scope=execution_scope,
            risk_target=risk_target,
            provider_contract=_provider_contract(),
            bt_api=bt_api,
            execution_store=execution_store,
            risk_order_journal=risk_order_journal,
            evidence_class=DispatchEvidenceClass.SIMULATION_JOURNAL,
        )
        mutable_targets = {stable_scope: proof_target}
        proof_targets = MappingProxyType(mutable_targets)
        proof_source = GroupProofSource(proof_journal, proof_targets)
        risk_gate = DurableRiskGate(
            tmp_path / "risk-gate.sqlite3",
            RiskPolicy(
                "group-components-risk-policy-v1",
                Decimal("100000"),
                max_increase_count=10,
                require_strategy_allocation=False,
                notional_unit="USD",
                exposure_accounting_mode=ExposureAccountingMode.RESERVATIONS_ONLY,
            ),
            execution_journal_authority=proof_source,
        )
        risk_admission = DurableRiskAdmissionAdapter(
            risk_gate,
            InstrumentRiskAdmissionMapper(
                account_scope,
                risk_target.registry,
                clock_ns=time.time_ns,
                wire_account_ref=account_ref,
            ),
        )
        account_admission = AccountScopedGroupAdmissionAdapter(
            risk_admission, account_scope=account_scope
        )
        reservation_target = GroupReservationTarget(account_scope, risk_admission)
        mutable_reservations = {stable_scope: reservation_target}
        bundle = GroupProofRuntimeBundle(
            risk_admission,
            account_admission,
            MappingProxyType(mutable_reservations),
            proof_targets,
            proof_source,
        )

        # The exact template lookup validates the bundle's stable map. The
        # child lookup remains a distinct API and requires both child IDs.
        assert proof_source.target_for_template(stable_scope) is proof_target
        assert _group_runtime_bundle(bundle) is bundle
        child_scope = replace(
            stable_scope,
            group_id="group-a",
            leg_id="leg-a",
        )
        assert bundle.proof_target_for(child_scope) is proof_target
        assert bundle.execution_scope_for(child_scope) is execution_scope
        with pytest.raises(GroupProofSourceError) as error:
            bundle.proof_target_for(stable_scope)
        assert error.value.code == "GROUP_WIRE_SCOPE_INVALID"

        # A bundle object whose mutable backing map no longer agrees with the
        # source's exact target object must fail closed.
        substituted_target = replace(
            proof_target,
            max_observation_age_ns=proof_target.max_observation_age_ns + 1,
        )
        mutable_targets[stable_scope] = substituted_target
        with pytest.raises(ServiceComponentError) as error:
            _group_runtime_bundle(bundle)
        assert error.value.code == "GROUP_RUNTIME_BUNDLE_INVALID"
        mutable_targets[stable_scope] = proof_target

        mutable_reservations[stable_scope] = GroupReservationTarget(
            replace(account_scope, account_id="provider-account-other"),
            risk_admission,
        )
        with pytest.raises(ServiceComponentError) as error:
            _group_runtime_bundle(bundle)
        assert error.value.code == "GROUP_RUNTIME_BUNDLE_INVALID"
        mutable_reservations[stable_scope] = reservation_target
        assert _group_runtime_bundle(bundle) is bundle

        raw_components = {
            "execution": {
                "settings": {
                    "instance_id": instance_id,
                    "bind_ports": ["service.order_entry", "service.order_query"],
                    "options": {"role": "group_account_owner"},
                }
            }
        }
        prepared = validate_components(component_specs(raw_components))[0]
        write_policy = TrustedGroupWritePolicy(
            instance_id,
            principal_ref,
            coordinator_identity,
            "group-order-entry-scope-a",
            stable_scope,
            "run-a",
        )
        resolution_policy = TrustedGroupResolutionPolicy(
            instance_id,
            principal_ref,
            coordinator_identity,
            "group-order-entry-scope-a",
            stable_scope,
            "run-a",
            account_ref,
        )
        accepted_store = AcceptedGroupBindingStore(tmp_path / "accepted-groups.sqlite3")
        authority = ExecutionGroupAuthority(
            tmp_path / "group-authority.sqlite3",
            completion_verifier=lambda _hold: False,
        )

        def resolve_resources(
            _component: str,
            _instance_id: str,
            _options: object,
            _scope: WireScope,
        ) -> ComponentResources:
            return ComponentResources.borrowed(
                {
                    "store": execution_store,
                    "dispatch": _UnusedDispatch(),
                    "accepted_group_store": accepted_store,
                    "group_authority": authority.guard,
                    "group_runtime_bundle": bundle,
                },
                ready=lambda: True,
            )

        component = build_component(
            prepared,
            scope=WireScope("paper:group-components"),
            resource_provider=resolve_resources,
            trusted_group_write_policies=(write_policy,),
            trusted_group_resolution_policies=(resolution_policy,),
        )
        assert {capability.port for capability in component.capabilities} == {
            "service.order_entry",
            "service.order_query",
        }
    finally:
        if component is not None:
            component.close()
        if authority is not None:
            authority.close()
        if accepted_store is not None:
            accepted_store.close()
        if risk_gate is not None:
            risk_gate.close()
        proof_journal.close()
        execution_store.close()
        bt_api.close()
