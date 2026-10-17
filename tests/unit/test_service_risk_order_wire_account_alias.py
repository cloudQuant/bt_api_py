"""The trusted Risk account ID may differ from the routed wire account alias."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import time

import pytest
from bt_api_base.service_protocol import WireScope
from bt_api_risk import (
    AccountScope,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskDeniedError,
    RiskPolicy,
)

from bt_api_py.bt_api import BtApi
from bt_api_py.service_risk_order_entry import (
    RiskBtApiProviderContract,
    RiskOrderEntryService,
    RiskOrderTarget,
    _parse_order,
)
from bt_api_py.service_risk_order_journal import RiskOrderJournal


def test_risk_order_maps_trusted_wire_alias_separately_from_provider_account(tmp_path) -> None:
    now_ns = time.time_ns()
    metadata = InstrumentRiskMetadata(
        instrument="BTC-USD",
        metadata_version="wire-alias-metadata-v1",
        as_of_ns=now_ns - 1_000_000,
        expires_at_ns=now_ns + 60_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("100000"),
        quantity_unit="BTC",
        valuation_unit="USD",
    )
    registry = InstrumentRiskRegistry((metadata,))
    account_scope = AccountScope("fixture", "provider-account-a", "simulation")
    wire_scope = WireScope(
        "wire-alias-domain-v1",
        account_ref="account-a",
        strategy_ref="strategy-a",
    )
    target = RiskOrderTarget(
        account_scope=account_scope,
        strategy_id="strategy-a",
        exchange_name="FIXTURE___SPOT",
        provider_account_id="provider-account-a",
        registry=registry,
    )
    mapper = InstrumentRiskAdmissionMapper(
        account_scope,
        registry,
        clock_ns=time.time_ns,
        wire_account_ref="account-a",
    )
    journal = RiskOrderJournal(tmp_path / "risk-orders.sqlite3", writer_id="wire-alias-test")
    gate = DurableRiskGate(
        tmp_path / "risk-gate.sqlite3",
        RiskPolicy("wire-alias-policy-v1", Decimal("100000"), 10),
        execution_journal_authority=journal,
    )
    admission = DurableRiskAdmissionAdapter(gate, mapper)
    contract = RiskBtApiProviderContract(
        exchange_name="FIXTURE___SPOT",
        profile_id="wire-alias-provider-v1",
        order_schema="wire-alias-order-v1",
        fill_schema="wire-alias-fill-v1",
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
            ("rejected", "rejected"),
        ),
        supported_order_types=("LIMIT",),
        supported_time_in_force=("GTC",),
        supported_position_effects=("OPEN",),
    )
    bt_api = BtApi()
    try:
        service = RiskOrderEntryService(
            bt_api,
            {wire_scope: target},
            {wire_scope: admission},
            journal,
            gate,
            {wire_scope: contract},
        )
        arguments = {
            "intent_id": "wire-alias-intent-v1",
            "signal_id": "wire-alias-signal-v1",
            "instrument": "BTC-USD",
            "side": "BUY",
            "position_effect": "OPEN",
            "order_type": "LIMIT",
            "quantity": "0.1",
            "price": "100",
            "time_in_force": "GTC",
            "reduce_only": False,
            "metadata_version": metadata.metadata_version,
            "deadline_ns": None,
            "tags": {},
        }
        parsed = _parse_order(
            arguments,
            target,
            contract=contract,
            scope=wire_scope,
            principal_ref="wire-alias-principal-v1",
        )
        with journal.writer_fence(account_scope) as fence:
            command, created = service._prepare_command(
                fence,
                principal_ref="wire-alias-principal-v1",
                scope=wire_scope,
                target=target,
                parsed=parsed,
            )
        assert created is True
        assert command.scope.account_id == "provider-account-a"
        assert command.wire_scope["account_ref"] == "account-a"

        order = service._admission_order(command, target, parsed)
        assert order.scope.account_ref == "account-a"
        assert order.scope.account_ref != account_scope.account_id
        assert mapper(order).scope == account_scope

        wrong_alias = replace(
            order,
            scope=replace(order.scope, account_ref="foreign-account-alias"),
        )
        with pytest.raises(RiskDeniedError) as denied:
            mapper(wrong_alias)
        assert denied.value.code == "ACCOUNT_SCOPE_MISMATCH"
    finally:
        bt_api.close()
