"""Exercise Risk's terminal CLOSE receipt and fee producer on SQLite.

Only the BtApi feed is synthetic. The order-entry path, SQLite command/event
journal, DurableRiskGate close receipt, and roll-exit projection are real. This
is local producer evidence, not a ServiceHost or provider acceptance test.
"""

from __future__ import annotations

import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from bt_api_base.account_protocol import AccountResourceKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import ExecutionState
from bt_api_risk import (
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
)
from bt_api_py.bt_api import BtApi
from bt_api_py.service_registry import TrustedGroupWritePolicy
from bt_api_py.service_risk_group_roll_exit import RiskGroupRollExitOwner, _order_arguments
from bt_api_py.service_risk_order_entry import (
    RiskBtApiProviderContract,
    RiskOrderEntryService,
    RiskOrderTarget,
    _derive_group_compensation_risk_intent_id,
)
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from tests.integration import test_dim04_strategy_account_isolation as dim04
from tests.unit import test_service_group_roll_exit as roll_exit_contract
from tests.unit import test_service_risk_order_exposure_integration as exposure
from tests.unit.test_dim04_strategy_net_close_late_fill import _synthetic_provider_catalog
from bt_api_risk import AccountScope


def test_real_risk_sqlite_close_producer_projects_receipt_and_fee_evidence(tmp_path: Path) -> None:
    source, request = roll_exit_contract._source_and_request()
    account_scope = AccountScope("fixture", "provider-account-a", "simulation")
    scope = WireScope(
        "paper:dim05-roll-exit-producer",
        account_ref=source.account_ref,
        strategy_ref=source.strategy_id,
        portfolio_ref=source.portfolio_ref,
    )
    principal_ref = "dim05-roll-exit-risk-owner-principal"
    instance_id = "dim05-roll-exit-risk-owner"
    identity = WireIdentity(WireRole.SERVICE, principal_ref, instance_id)
    journal = RiskOrderJournal(
        tmp_path / "risk-order-journal.sqlite3",
        writer_id="dim05-roll-exit-producer-writer",
    )
    gate = DurableRiskGate(
        tmp_path / "risk.sqlite3",
        dim04._risk_policy(journal),
        execution_journal_authority=journal,
        gross_exposure_authority=journal,
    )
    bt_api = None
    try:
        metadata = InstrumentRiskMetadata(
            instrument=source.instrument,
            metadata_version=source.close_metadata_version,
            as_of_ns=time.time_ns() - 1_000,
            expires_at_ns=source.close_metadata_valid_until_ns,
            tick_size=source.close_tick_size,
            quantity_step=source.close_quantity_step,
            contract_multiplier=Decimal("1"),
            max_gross_notional=Decimal("100000"),
            quantity_unit="BTC",
            valuation_unit="USD",
            quote_currency="USD",
        )
        registry = InstrumentRiskRegistry((metadata,))
        resource = AccountResourceKey(
            "spot", f"{account_scope.account_id}-{account_scope.environment}"
        )
        resource_by_instrument = {source.instrument: resource}
        target = RiskOrderTarget(
            account_scope=account_scope,
            strategy_id=source.strategy_id,
            exchange_name=exposure._EXCHANGE,
            provider_account_id=account_scope.account_id,
            registry=registry,
            resource_by_instrument=resource_by_instrument,
        )
        allocation_version = "dim05-roll-exit-allocation-v1"
        gate.set_strategy_allocation(
            account_scope,
            source.strategy_id,
            allocation_version,
            max_notional=Decimal("100000"),
            notional_unit="USD",
            max_gross_occupancy_notional=Decimal("100000"),
            max_gross_occupancy_intent_count=100,
        )
        gate.set_closable_position(
            account_scope,
            source.instrument,
            source.strategy_id,
            allocation_version,
            closable_quantity=source.remaining_quantity,
            quantity_unit="BTC",
        )
        mapper = InstrumentRiskAdmissionMapper(
            account_scope,
            registry,
            clock_ns=time.time_ns,
            allocation_reader=lambda execution_scope: gate.get_strategy_allocation(
                account_scope, execution_scope.strategy_id
            ),
            resource_by_instrument=resource_by_instrument,
            wire_account_ref=source.account_ref,
        )
        admission = DurableRiskAdmissionAdapter(gate, mapper)
        contract = replace(
            exposure._contract(("GTC",)),
            supported_position_effects=("OPEN", "CLOSE"),
            quote_currency_by_instrument=((source.instrument, "USD"),),
        )
        provider = exposure._V2SyntheticBtApiFeed(
            str(tmp_path / "synthetic-provider.sqlite3"), ("filled",)
        )
        with _synthetic_provider_catalog() as discovered_groups:
            bt_api = BtApi(debug=False)
        assert discovered_groups == ["bt_api.plugins"]
        bt_api.exchange_feeds[target.exchange_name] = provider
        risk_owner = RiskOrderEntryService(
            bt_api,
            {scope: target},
            {scope: admission},
            journal,
            gate,
            {scope: contract},
        )

        base_authorization = roll_exit_contract._authorization(source, request)
        risk_intent_id = _derive_group_compensation_risk_intent_id(
            intent_id=base_authorization.action_id,
            principal_ref=principal_ref,
            scope=scope,
            target=target,
        )
        authorization = type(base_authorization).build(
            request=base_authorization.request,
            source=base_authorization.source,
            intent=base_authorization.intent,
            deadline_ns=base_authorization.deadline_ns,
            risk_intent_id=risk_intent_id,
        )
        arguments = {
            **_order_arguments(authorization),
            "tags": MappingProxyType({}),
        }
        reply = risk_owner._submit(
            principal_ref=principal_ref,
            identity=identity,
            scope=scope,
            target=target,
            admission=admission,
            contract=contract,
            arguments=MappingProxyType(arguments),
            trusted_client_order_id=authorization.client_order_id,
            reject_duplicate=True,
            internal_group_roll_exit=True,
        )

        assert reply["ok"] is True, reply
        assert reply["result"]["record"]["state"] == ExecutionState.FILLED.value
        assert reply["result"]["record"]["dispatch_attempts"] == 1
        writes, _provider_counters = provider.snapshot()
        assert writes == (authorization.client_order_id,)

        command = journal.get_command(
            account_scope, principal_ref, authorization.intent.intent_id
        )
        assert command is not None and command.permit_id is not None
        actual_receipt = gate.dispatch_close_resolution_receipt(command.permit_id)
        assert actual_receipt.claim.intent_id == risk_intent_id
        assert not hasattr(actual_receipt, "journal_record_sha256")
        with journal.writer_fence(account_scope) as fence:
            actual_proof = journal.load_terminal_close_resolution_proof(
                fence,
                principal_ref=principal_ref,
                intent_id=authorization.intent.intent_id,
            )
        assert actual_proof is not None

        # Instantiate only the projection object: its producer call below reads
        # the actual command, fenced journal proof, and DurableRiskGate receipt.
        projector = object.__new__(RiskGroupRollExitOwner)
        projector._owner = risk_owner
        projector._policy = TrustedGroupWritePolicy(
            instance_id=instance_id,
            principal_ref=principal_ref,
            identity=identity,
            scope_ref="dim05-roll-exit-scope-v1",
            scope=scope,
            run_id=source.origin_run_id,
            operation="orders.submit_group_roll_exit",
        )
        observation = projector._observation_from_reply(
            authorization, target, reply
        )

        assert observation.phase.value == "RESOLVED"
        assert observation.close_state is ExecutionState.FILLED
        assert observation.risk_intent_id == risk_intent_id
        assert observation.close_receipt is not None
        assert observation.close_receipt["claim"]["intent_id"] == risk_intent_id
        assert observation.close_receipt["proof_sha256"] == actual_receipt.proof_sha256
        assert (
            observation.close_receipt["exposure_proof_sha256"]
            == actual_receipt.exposure_proof_sha256
        )
        evidence = observation.close_fee_evidence
        assert evidence is not None
        assert evidence.intent_id == authorization.action_id
        assert evidence.journal_revision == observation.close_receipt["journal_revision"]
        assert evidence.journal_revision == actual_proof.observation.journal_revision
        assert evidence.fee_currency == "USD"
        assert evidence.fee_amount == Decimal("-0.05")
        assert len(evidence.fills) == 1
        actual_fill = actual_proof.observation.fills[0]
        assert evidence.fill_observation_sha256 == actual_proof.observation.journal_record_sha256
        assert evidence.fills[0].fill_id == actual_fill.trade_id
        assert evidence.fills[0].fill_fingerprint == actual_fill.fingerprint
        assert evidence.fills[0].source_row_sha256 == actual_fill.source_record_sha256
        assert evidence.fills[0].amount == actual_fill.fee_amount == Decimal("-0.05")
        assert evidence.fills[0].currency == actual_fill.fee_currency == "USD"
    finally:
        if bt_api is not None:
            bt_api.close()
        gate.close()
