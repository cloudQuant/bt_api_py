"""Narrow composition guards for the Execution group proof source."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pytest
from bt_api_base.service_protocol import WireScope
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
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskAdmissionPolicyBinding,
    RiskPolicy,
)

from bt_api_py.bt_api import BtApi
from bt_api_py.service_execution_group_proofs import (
    GroupProofJournal,
    GroupProofRuntimeBundle,
    GroupProofSource,
    GroupProofSourceError,
    GroupProofTarget,
)
from bt_api_py.service_group_reservations import GroupReservationTarget
from bt_api_py.service_risk_order_entry import RiskBtApiProviderContract, RiskOrderTarget
from bt_api_py.service_risk_order_journal import RiskOrderJournal


def test_group_runtime_bundle_rejects_live_gross_without_shared_cursor(
    tmp_path: Path,
) -> None:
    policy = RiskPolicy(
        "group-live-gross-test",
        Decimal("1000"),
        max_increase_count=10,
        require_strategy_allocation=True,
        notional_unit="USD",
        exposure_accounting_mode=ExposureAccountingMode.LIVE_GROSS,
        max_gross_occupancy_notional=Decimal("2000"),
        max_gross_occupancy_intent_count=10,
        max_exposure_facts_age_seconds=3600,
    )
    gate = DurableRiskGate(tmp_path / "risk.sqlite3", policy)
    admission = DurableRiskAdmissionAdapter(gate, lambda _intent: None)
    try:
        binding = admission.policy_binding
        assert type(binding) is RiskAdmissionPolicyBinding
        assert binding.exposure_accounting_mode is ExposureAccountingMode.LIVE_GROSS
        with pytest.raises(GroupProofSourceError) as error:
            # The mode check intentionally precedes the unrelated resource
            # shape checks, so an incompatible real gate cannot be hidden by
            # an otherwise incomplete candidate bundle.
            GroupProofRuntimeBundle(admission, None, {}, {}, None)
        assert error.value.code == "GROUP_LIVE_GROSS_CURSOR_UNAVAILABLE"
    finally:
        gate.close()


def test_group_proof_target_accepts_ioc_with_combined_tif_profile_and_binds_strategy(
    tmp_path: Path,
) -> None:
    strategy_id = "strategy-a"
    execution_scope = ExecutionScope("fixture", "simulation", "account-a", strategy_id, "20261015")
    risk_scope = AccountScope("fixture", "provider-account", "simulation")
    metadata = InstrumentRiskMetadata(
        instrument="BTC-USD",
        metadata_version="fixture-metadata-v1",
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
        account_scope=risk_scope,
        strategy_id=strategy_id,
        exchange_name="FIXTURE___SPOT",
        provider_account_id="provider-account",
        registry=InstrumentRiskRegistry((metadata,)),
    )
    contract = RiskBtApiProviderContract(
        exchange_name="FIXTURE___SPOT",
        profile_id="fixture-profile-v2",
        order_schema="fixture-order-v2",
        fill_schema="fixture-fill-v2",
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
    bt_api = BtApi(debug=False)
    execution_store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    risk_order_journal = RiskOrderJournal(
        tmp_path / "risk-order.sqlite3", writer_id="group-proof-test"
    )
    try:
        target = GroupProofTarget(
            execution_scope=execution_scope,
            risk_target=risk_target,
            provider_contract=contract,
            bt_api=bt_api,
            execution_store=execution_store,
            risk_order_journal=risk_order_journal,
            evidence_class=DispatchEvidenceClass.SIMULATION_JOURNAL,
        )
        assert target.provider_contract.supported_time_in_force == ("GTC", "IOC")

        with pytest.raises(GroupProofSourceError) as error:
            GroupProofTarget(
                execution_scope=execution_scope,
                risk_target=replace(risk_target, strategy_id="strategy-b"),
                provider_contract=contract,
                bt_api=bt_api,
                execution_store=execution_store,
                risk_order_journal=risk_order_journal,
                evidence_class=DispatchEvidenceClass.SIMULATION_JOURNAL,
            )
        assert error.value.code == "GROUP_PROOF_TARGET_SCOPE_INVALID"
    finally:
        bt_api.close()
        execution_store.close()


def test_group_runtime_bundle_rejects_same_scope_different_risk_gate(
    tmp_path: Path,
) -> None:
    strategy_id = "strategy-a"
    wire_scope = WireScope("fixture", account_ref="account-a", strategy_ref=strategy_id)
    execution_scope = ExecutionScope("fixture", "simulation", "account-a", strategy_id, "20261015")
    risk_scope = AccountScope("fixture", "provider-account", "simulation")
    metadata = InstrumentRiskMetadata(
        instrument="BTC-USD",
        metadata_version="fixture-metadata-v1",
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
        account_scope=risk_scope,
        strategy_id=strategy_id,
        exchange_name="FIXTURE___SPOT",
        provider_account_id="provider-account",
        registry=InstrumentRiskRegistry((metadata,)),
    )
    contract = RiskBtApiProviderContract(
        exchange_name="FIXTURE___SPOT",
        profile_id="fixture-profile-v2",
        order_schema="fixture-order-v2",
        fill_schema="fixture-fill-v2",
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
    policy = RiskPolicy(
        "group-gate-identity-test",
        Decimal("1000"),
        max_increase_count=10,
        require_strategy_allocation=True,
        notional_unit="USD",
    )
    reservation_gate = DurableRiskGate(tmp_path / "risk-reservation.sqlite3", policy)
    other_gate = DurableRiskGate(tmp_path / "risk-other.sqlite3", policy)
    reservation_admission = DurableRiskAdmissionAdapter(reservation_gate, lambda _intent: None)
    other_admission = DurableRiskAdmissionAdapter(other_gate, lambda _intent: None)
    account_admission = AccountScopedGroupAdmissionAdapter(
        other_admission, account_scope=risk_scope
    )
    bt_api = BtApi(debug=False)
    execution_store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    risk_order_journal = RiskOrderJournal(
        tmp_path / "risk-order.sqlite3", writer_id="group-gate-identity-test"
    )
    proof_journal = GroupProofJournal(
        tmp_path / "group-proof.sqlite3", writer_id="group-gate-identity-test"
    )
    try:
        target = GroupProofTarget(
            execution_scope=execution_scope,
            risk_target=risk_target,
            provider_contract=contract,
            bt_api=bt_api,
            execution_store=execution_store,
            risk_order_journal=risk_order_journal,
            evidence_class=DispatchEvidenceClass.SIMULATION_JOURNAL,
        )
        proof_targets = MappingProxyType({wire_scope: target})
        source = GroupProofSource(proof_journal, proof_targets)
        reservation_targets = MappingProxyType(
            {wire_scope: GroupReservationTarget(risk_scope, reservation_admission)}
        )

        # Same policy and Risk scope produce equal fingerprints, but they do
        # not prove that the account adapter uses the reservation gate.
        assert reservation_admission.policy_binding == other_admission.policy_binding
        with pytest.raises(GroupProofSourceError) as error:
            GroupProofRuntimeBundle(
                reservation_admission,
                account_admission,
                reservation_targets,
                proof_targets,
                source,
            )
        assert error.value.code == "GROUP_RISK_ADMISSION_GATE_IDENTITY_MISMATCH"
    finally:
        proof_journal.close()
        bt_api.close()
        execution_store.close()
        reservation_gate.close()
        other_gate.close()


def test_group_proof_projects_real_normalizer_float_records_and_rejects_nonfinite(
    tmp_path: Path,
) -> None:
    """BtApi's finite float order/fill projections stay hash-stable in the proof DTO."""

    import hashlib
    import json
    import time
    from types import SimpleNamespace

    from bt_api_execution import (
        OrderIntent as ExecutionOrderIntent,
    )
    from bt_api_execution import (
        OrderType as ExecutionOrderType,
    )
    from bt_api_execution import (
        PositionEffect,
        TimeInForce,
    )
    from bt_api_execution import (
        Side as ExecutionSide,
    )

    from bt_api_py._contracts.models import (
        OrderRequest,
        OrderType,
        QueryOrderRequest,
        Side,
    )
    from bt_api_py._normalization import NormalizedRecord, NormalizedRecords, normalize_result
    from bt_api_py.service_execution_group_proofs import (
        INSTRUMENT_METADATA_DIGEST_TAG,
        GroupProofDispatchSession,
        _decimal,
        _json_scalar,
        _normalized_fill,
        _normalized_float_decimal,
        _normalized_float_matches_source,
        _normalized_order,
        _persisted_normalized_matches_source,
        _safe_ack,
    )
    from bt_api_py.service_risk_order_journal import canonical_json

    exchange_name = "FIXTURE___SPOT"
    symbol = "BTC-USD"
    account_id = "provider-account"
    client_order_id = "group-float-projection"
    order_row = {
        "account_id": account_id,
        "avg_price": "100.25",
        "client_order_id": client_order_id,
        "filled": "0.3",
        "order_id": "provider-order-float-projection",
        "order_revision": 2,
        "price": "100.25",
        "quantity": "0.3",
        "side": "buy",
        "status": "filled",
        "symbol": symbol,
    }
    order_envelope = {
        "account_id": account_id,
        "client_order_id": client_order_id,
        "complete": True,
        "fill_watermark": 2,
        "schema_version": "fixture-order-v2",
        "watermark": "order-watermark-2",
        "data": [order_row],
    }
    request = OrderRequest(
        symbol=symbol,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.3"),
        account_id=account_id,
        client_order_id=client_order_id,
        price=Decimal("100.25"),
        time_in_force="IOC",
        idempotency_key=client_order_id,
        quantity_unit="base",
        offset="open",
    )

    acknowledgement = normalize_result("make_order", order_envelope, exchange_name, symbol, request)
    order = normalize_result(
        "query_order",
        order_envelope,
        exchange_name,
        symbol,
        QueryOrderRequest(
            symbol=symbol,
            account_id=account_id,
            client_order_id=client_order_id,
        ),
    )
    fill_envelope = {
        "account_id": account_id,
        "client_order_id": client_order_id,
        "complete": True,
        "fill_watermark": 2,
        "schema_version": "fixture-fill-v2",
        "watermark": "fill-watermark-2",
        "data": [
            {
                "account_id": account_id,
                "client_order_id": client_order_id,
                "fee_amount": "-0.05",
                "fee_currency": "USD",
                "fill_revision": 2,
                "order_id": order_row["order_id"],
                "quantity": "0.3",
                "side": "buy",
                "symbol": symbol,
                "trade_id": "trade-float-projection",
                "trade_price": "100.25",
            }
        ],
    }
    fills = normalize_result("get_deals", fill_envelope, exchange_name, symbol)

    assert type(acknowledgement) is NormalizedRecord
    assert type(order) is NormalizedRecord
    assert type(fills) is NormalizedRecords
    assert len(fills) == 1
    assert type(acknowledgement["filled"]) is float
    assert type(order["size"]) is float and type(order["filled"]) is float
    assert all(type(fills[0][key]) is float for key in ("size", "price", "fee"))
    assert acknowledgement.source_alignment_valid is True
    assert order.source_alignment_valid is True and order.source_row_count == 1
    assert fills.source_alignment_valid is True and len(fills.row_provenance) == 1

    ack_projection = _safe_ack(acknowledgement)
    assert ack_projection is not None
    assert ack_projection["record"]["filled"] == "0.3"
    order_projection = _normalized_order(order)
    fill_projection = _normalized_fill(fills[0])
    assert order_projection["size"] == "0.3"
    assert order_projection["filled"] == "0.3"
    assert fill_projection["size"] == "0.3"
    assert fill_projection["price"] == "100.25"
    assert fill_projection["fee"] == "-0.05"
    assert _normalized_float_matches_source(order["size"], Decimal("0.3"), "quantity")
    assert _normalized_float_matches_source(fills[0]["fee"], Decimal("-0.05"), "fee")
    assert _normalized_float_decimal(0.30000000000000004, "quantity") == Decimal(
        "0.30000000000000004"
    )
    with pytest.raises(GroupProofSourceError) as raw_float_error:
        _decimal(0.3, "raw_quantity")
    assert raw_float_error.value.code == "raw_quantity"

    projected = {
        "acknowledgement": ack_projection,
        "order": order_projection,
        "fill": fill_projection,
    }
    encoded = canonical_json(projected)
    assert canonical_json(json.loads(encoded)) == encoded
    assert _json_scalar(True) is True

    # The normalizer's finite binary64 result is the documented projection of
    # an exact provider decimal. Ingest and persisted readback must apply the
    # same projection while all source arithmetic keeps the original Decimal.
    high_precision_row = {
        **order_row,
        "filled": "0.100000000000000001",
        "quantity": "0.100000000000000001",
    }
    high_precision_envelope = {**order_envelope, "data": [high_precision_row]}
    exact_high_precision_quantity = Decimal("0.100000000000000001")
    high_precision_request = replace(request, quantity=exact_high_precision_quantity)
    high_precision_ack = normalize_result(
        "make_order",
        high_precision_envelope,
        exchange_name,
        symbol,
        high_precision_request,
    )
    high_precision_order = normalize_result(
        "query_order",
        high_precision_envelope,
        exchange_name,
        symbol,
        QueryOrderRequest(
            symbol=symbol,
            account_id=account_id,
            client_order_id=client_order_id,
        ),
    )
    assert type(high_precision_order["size"]) is float
    assert high_precision_order["size"] == 0.1
    assert _normalized_float_matches_source(
        high_precision_order["size"], exact_high_precision_quantity, "quantity", positive=True
    )
    persisted_high_precision_order = _normalized_order(high_precision_order)
    assert persisted_high_precision_order["size"] == "0.1"
    assert _persisted_normalized_matches_source(
        persisted_high_precision_order["size"],
        exact_high_precision_quantity,
        "quantity",
        positive=True,
    )
    assert not _persisted_normalized_matches_source(
        "0.1", Decimal("0.10000000000000005"), "quantity", positive=True
    )
    high_precision_fill_envelope = {
        **fill_envelope,
        "data": [
            {
                **fill_envelope["data"][0],
                "quantity": "0.100000000000000001",
            }
        ],
    }
    high_precision_fills = normalize_result(
        "get_deals", high_precision_fill_envelope, exchange_name, symbol
    )
    assert type(high_precision_fills[0]["size"]) is float
    assert high_precision_fills[0]["size"] == 0.1
    with pytest.raises(GroupProofSourceError) as bool_ack_error:
        _safe_ack({**high_precision_ack, "filled": True})
    assert bool_ack_error.value.code == "GROUP_SOURCE_FACT_INVALID"
    # Boolean status flags are legitimate fields; only acknowledgement numeric
    # projections are required to be exact finite floats.
    assert _safe_ack({**high_precision_ack, "terminal_confirmed": True}) is not None

    # Exercise the same source-event projection, append API, hash-chain
    # verification, SQLite JSON roundtrip, and source validator used by the
    # group dispatch path. This fixture asserts source-journal semantics only;
    # it does not call a provider or authorize an Execution/Risk claim. The
    # opaque claim digest below is only an event-shape fixture value and never
    # reaches a Risk resolution or proof API.
    now_ns = time.time_ns()
    execution_scope = ExecutionScope("fixture", "simulation", "account-a", "strategy-a", "20261016")
    account_scope = AccountScope("FIXTURE", account_id, "simulation")
    metadata = InstrumentRiskMetadata(
        instrument=symbol,
        metadata_version="fixture-metadata-float-v1",
        as_of_ns=now_ns - 1_000_000_000,
        expires_at_ns=now_ns + 60_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.000000000000000001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("100000"),
        quantity_unit="base",
        valuation_unit="USD",
    )
    risk_target = RiskOrderTarget(
        account_scope=account_scope,
        strategy_id=execution_scope.strategy_id,
        exchange_name=exchange_name,
        provider_account_id=account_id,
        registry=InstrumentRiskRegistry((metadata,)),
    )
    contract = RiskBtApiProviderContract(
        exchange_name=exchange_name,
        profile_id="fixture-float-journal-v2",
        order_schema="fixture-order-v2",
        fill_schema="fixture-fill-v2",
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
        quote_currency_by_instrument=((symbol, "USD"),),
    )
    trusted_intent = ExecutionOrderIntent(
        intent_id="group-float-journal-intent",
        scope=execution_scope,
        signal_id="group-float-journal-signal",
        instrument=symbol,
        side=ExecutionSide.BUY,
        position_effect=PositionEffect.OPEN,
        order_type=ExecutionOrderType.LIMIT,
        quantity=exact_high_precision_quantity,
        price=Decimal("100.25"),
        time_in_force=TimeInForce.IOC,
        metadata_version=metadata.metadata_version,
        tags={INSTRUMENT_METADATA_DIGEST_TAG: metadata.digest},
    )
    bt_api = BtApi(debug=False)
    execution_store = SqliteExecutionStore(tmp_path / "float-source-execution.sqlite3")
    risk_order_journal = RiskOrderJournal(
        tmp_path / "float-source-risk-order.sqlite3", writer_id="group-float-source-test"
    )
    proof_journal_path = tmp_path / "float-source-proof.sqlite3"
    proof_journal = GroupProofJournal(proof_journal_path, writer_id="group-float-source-test")
    reopened_journal = None
    try:
        target = GroupProofTarget(
            execution_scope=execution_scope,
            risk_target=risk_target,
            provider_contract=contract,
            bt_api=bt_api,
            execution_store=execution_store,
            risk_order_journal=risk_order_journal,
            evidence_class=DispatchEvidenceClass.SIMULATION_JOURNAL,
        )
        stable_scope = WireScope(
            "fixture",
            account_ref="account-a",
            strategy_ref="strategy-a",
            portfolio_ref="portfolio-a",
        )
        wire_scope = replace(stable_scope, group_id="float-group", leg_id="float-leg")
        source = GroupProofSource(
            proof_journal, MappingProxyType({stable_scope: target}), clock_ns=lambda: now_ns
        )
        normalized_facts_session = object.__new__(GroupProofDispatchSession)
        normalized_facts_session.source = source
        normalized_facts_session.target = target
        normalized_facts_session.intent = trusted_intent
        normalized_facts_session._request = high_precision_request
        normalized_facts_session._accepted_request_sha256 = hashlib.sha256(
            canonical_json(
                {
                    "exchange_name": exchange_name,
                    "operation": "BtApi.make_order(normalized=True)",
                    "request": high_precision_request.to_dict(),
                }
            ).encode("utf-8")
        ).hexdigest()
        normalized_facts_session._acknowledgement = _safe_ack(high_precision_ack)
        facts = normalized_facts_session._validate_provider_source(
            high_precision_order, high_precision_fills
        )
        assert facts["quantity"] == exact_high_precision_quantity
        assert facts["normalized_order"]["size"] == "0.1"
        assert facts["fill_records"][0]["record"]["size"] == "0.1"
        status = normalized_facts_session._status_from_facts(facts, facts["state"])
        assert status.average_price == Decimal("100.25")
        normalized_facts_session._risk_fence = None
        normalized_facts_session._claim_projection = SimpleNamespace(claim_digest="a" * 64)
        normalized_facts_session._source_event = None
        normalized_facts_session.permit = SimpleNamespace(
            group_id="float-group", leg_id="float-leg"
        )
        normalized_facts_session.wire_scope = wire_scope

        with risk_order_journal.writer_fence(account_scope) as fence:
            normalized_facts_session._risk_fence = fence
            source_payload = normalized_facts_session._source_event_payload(
                facts, state=facts["state"], status=status
            )
            event = source._append_event(
                target=target,
                fence=fence,
                intent=trusted_intent,
                permit=normalized_facts_session.permit,
                event_type="SOURCE_OBSERVED",
                payload=source_payload,
            )
            obs_material = {
                "accepted_request_sha256": facts["accepted_request_sha256"],
                "contract_sha256": facts["contract_sha256"],
                "order_collection_provenance": facts["order_collection_provenance"],
                "order_provenance": facts["order_provenance"],
                "normalized_order": facts["normalized_order"],
            }
            fill_material = {
                "fill_collection_provenance": facts["fill_collection_provenance"],
                "fill_rows": facts["fill_records"],
            }
            observation_digest = hashlib.sha256(
                canonical_json(obs_material).encode("utf-8")
            ).hexdigest()
            fill_digest = hashlib.sha256(canonical_json(fill_material).encode("utf-8")).hexdigest()
            assert facts["observation_sha256"] == observation_digest
            assert facts["fill_history_sha256"] == fill_digest
            assert event.payload["observation_sha256"] == observation_digest
            assert event.payload["fill_history_sha256"] == fill_digest

            proof_journal.close()
            reopened_journal = GroupProofJournal(
                proof_journal_path, writer_id="group-float-source-test"
            )
            recovered = reopened_journal.events(account_scope.key, trusted_intent.intent_id)
            assert len(recovered) == 1 and recovered[0].event_type == "SOURCE_OBSERVED"
            persisted = recovered[0]
            persisted_payload = dict(persisted.payload)
            persisted_obs_material = {
                "accepted_request_sha256": persisted_payload["accepted_request_sha256"],
                "contract_sha256": persisted_payload["contract_sha256"],
                "order_collection_provenance": persisted_payload["order_collection_provenance"],
                "order_provenance": persisted_payload["order_provenance"],
                "normalized_order": persisted_payload["normalized_order"],
            }
            persisted_fill_material = {
                "fill_collection_provenance": persisted_payload["fill_collection_provenance"],
                "fill_rows": persisted_payload["fill_records"],
            }
            assert (
                hashlib.sha256(canonical_json(persisted_obs_material).encode("utf-8")).hexdigest()
                == persisted_payload["observation_sha256"]
                == observation_digest
            )
            assert (
                hashlib.sha256(canonical_json(persisted_fill_material).encode("utf-8")).hexdigest()
                == persisted_payload["fill_history_sha256"]
                == fill_digest
            )
            assert persisted_payload["acknowledgement"]["record"]["filled"] == "0.1"
            assert persisted_payload["normalized_order"]["size"] == "0.1"
            assert persisted_payload["fill_records"][0]["record"]["size"] == "0.1"
            normalized_facts_session._validate_source_event(persisted)
    finally:
        if reopened_journal is not None:
            reopened_journal.close()
        else:
            proof_journal.close()
        execution_store.close()
        bt_api.close()

    for invalid in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(GroupProofSourceError) as scalar_error:
            _json_scalar(invalid)
        assert scalar_error.value.code == "GROUP_SOURCE_FACT_INVALID"
        with pytest.raises(GroupProofSourceError):
            _normalized_order({**order, "filled": invalid})
        with pytest.raises(GroupProofSourceError):
            _normalized_fill({**fills[0], "fee": invalid})
        with pytest.raises(GroupProofSourceError):
            _normalized_float_decimal(invalid, "normalized_float")
    with pytest.raises(GroupProofSourceError) as bool_numeric_error:
        _normalized_float_decimal(True, "normalized_float")
    assert bool_numeric_error.value.code == "normalized_float"
    with pytest.raises(GroupProofSourceError) as bool_persisted_error:
        _persisted_normalized_matches_source(True, Decimal("1"), "normalized_float")
    assert bool_persisted_error.value.code == "normalized_float"


def test_group_finish_requires_receipt_cached_under_the_live_authority_hold() -> None:
    """Post-package finish accepts only the exact result validated in-callback.

    The normal two-leg ServiceHost loopback exercises the real
    ExecutionOrderEntryService hold/release path. This focused regression pins
    the narrow finish contract: it cannot re-enter the already-released hold,
    and a missing callback cache or changed record remains fail-closed.
    """

    from contextlib import contextmanager
    from decimal import Decimal
    from types import SimpleNamespace

    from bt_api_execution import (
        ExecutionRecord,
        ExecutionState,
        GroupChildOrderStatus,
        GroupDispatchResolutionReceipt,
        GroupLegClaimReceipt,
        GroupLegOrderReceipt,
        GroupLegPermitReference,
        OrderIntent,
        Side,
        TimeInForce,
        group_leg_intent_id,
    )
    from bt_api_risk import (
        AccountScope,
        DispatchClaimBinding,
        IntentAction,
        RiskGroupLegClaimReceipt,
        RiskPermit,
    )

    from bt_api_py.service_execution_group_proofs import GroupProofDispatchSession

    execution_scope = ExecutionScope("fixture", "simulation", "account-a", "strategy-a", "20261016")
    now_ns = 1_900_000_000_000_000_000
    group_id = "finish-group"
    leg_id = "leg-a"
    intent = OrderIntent.limit(
        intent_id=group_leg_intent_id(group_id, leg_id),
        scope=execution_scope,
        signal_id="finish-signal",
        instrument="BTC-USD",
        side=Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        time_in_force=TimeInForce.IOC,
        metadata_version="finish-metadata-v1",
        deadline_ns=now_ns + 60_000_000_000,
    )
    permit = GroupLegPermitReference(
        group_id=group_id,
        leg_id=leg_id,
        portfolio_ref="portfolio-a",
        strategy_id="strategy-a",
        account_ref="account-a",
        coordinator_principal_ref="group-coordinator-principal",
        risk_scope_fingerprint="a" * 64,
        permit_id="finish-permit-a",
        group_fingerprint="b" * 64,
        policy_fingerprint="c" * 64,
        reserved_intent_hash="d" * 64,
        exposure_profile_fingerprint="e" * 64,
        order_intent_hash=intent.fingerprint,
        deadline_ns=intent.deadline_ns,
    )
    binding = permit.bind_actual_child("f" * 64)
    claim = GroupLegClaimReceipt(
        permit=permit,
        actual_child_intent_hash="f" * 64,
        claim_digest="1" * 64,
        actual_quantity=Decimal("1"),
        actual_notional=Decimal("100"),
    )
    risk_scope = AccountScope("fixture", "account-a", "simulation")
    risk_permit = RiskPermit(
        permit_id=permit.permit_id,
        intent_id=intent.intent_id,
        scope=risk_scope,
        action=IntentAction.INCREASE,
        notional=Decimal("100"),
        policy_fingerprint=permit.policy_fingerprint,
        generation=1,
        issued_at=1_900_000_000.0,
        expires_at=1_900_000_060.0,
        strategy_id="strategy-a",
        instrument="BTC-USD",
        quantity=Decimal("1"),
        quantity_unit="base",
        notional_unit="USD",
    )
    risk_claim = RiskGroupLegClaimReceipt(
        scope=risk_scope,
        group_id=group_id,
        group_fingerprint=permit.group_fingerprint,
        policy_fingerprint=permit.policy_fingerprint,
        deadline_ns=permit.deadline_ns,
        leg_id=leg_id,
        permit=risk_permit,
        reserved_intent_hash=permit.reserved_intent_hash,
        actual_intent_hash=claim.actual_child_intent_hash,
        exposure_profile_fingerprint=permit.exposure_profile_fingerprint,
        actual_quantity=claim.actual_quantity,
        actual_notional=claim.actual_notional,
        claim=DispatchClaimBinding(
            scope=risk_scope,
            permit_id=permit.permit_id,
            intent_id=intent.intent_id,
            intent_hash=permit.reserved_intent_hash,
            cause_id=f"dispatch-inflight:{intent.intent_id}",
            claimed_at=1_900_000_000.0,
            claim_digest=claim.claim_digest,
        ),
    )
    status = GroupChildOrderStatus(
        intent_id=intent.intent_id,
        state=ExecutionState.FILLED,
        filled_quantity=Decimal("1"),
        provider_order_id="provider-finish-order",
        average_price=Decimal("100"),
        cumulative_commission=Decimal("-0.05"),
        cumulative_commission_currency="USD",
        order_revision=1,
        trade_count=1,
    )
    resolution = GroupDispatchResolutionReceipt(
        claim=claim,
        resolution_kind="TERMINAL_EXPOSURE_RESOLVED",
        proof_sha256="2" * 64,
        final_state=ExecutionState.FILLED,
        requested_quantity=Decimal("1"),
        filled_quantity=Decimal("1"),
        trade_count=1,
        order_revision=1,
        journal_revision=2,
        exposure_proof_sha256="3" * 64,
        exposure_reservation_id="finish-exposure-a",
        exposure_reservation_sha256="4" * 64,
        dispatch_latch_active=False,
        has_other_active_freezes=False,
    )
    receipt = GroupLegOrderReceipt(status=status, claim=claim, resolution=resolution)
    record = ExecutionRecord(
        intent_id=intent.intent_id,
        scope_key=execution_scope.key,
        payload_sha256=intent.fingerprint,
        state=ExecutionState.FILLED,
        provider_order_id="provider-finish-order",
        filled_quantity=Decimal("1"),
        average_price=Decimal("100"),
        permit_reference=permit.permit_id,
        dispatch_attempts=1,
        unknown_reason=None,
        review_required=False,
        created_at_ns=now_ns,
        updated_at_ns=now_ns + 1,
        cumulative_commission=Decimal("-0.05"),
        group_binding=binding,
    )
    lease = object()
    authorization = object()
    fence = object()

    @contextmanager
    def risk_writer_fence(_scope):
        # The exact Risk fence remains owned by the session until finish.
        yield fence

    target = SimpleNamespace(
        execution_scope=execution_scope,
        account_scope=object(),
        execution_store=SimpleNamespace(
            get=lambda intent_id, *, scope: (
                record if intent_id == intent.intent_id and scope == execution_scope else None
            )
        ),
        risk_order_journal=SimpleNamespace(writer_fence=risk_writer_fence),
    )
    session = object.__new__(GroupProofDispatchSession)
    session._finished = False
    session._aborted = False
    session._terminal_receipt = receipt
    session._terminal_record = record
    session._terminal_binding = binding
    session._terminal_authorization = authorization
    session._terminal_writer_lease = lease
    session._terminal_claim_receipt = risk_claim
    session._status = status
    session._claim_receipt = risk_claim
    session._claim_projection = claim
    session._authorization = authorization
    session._writer_lease = lease
    session._risk_fence = fence
    session._risk_fence_context = None
    session._context_token = None
    session._attempt_started = True
    session._attempt_unknown = False
    session.target = target
    session.binding = binding
    session.intent = intent
    session.source = None
    session.permit = permit

    # The package's normal return has already released the coordinator hold.
    # finish must rely on the exact receipt cached by the still-held result
    # callback, rather than asking the active-hold authority to reauthorize.
    session.active_authority = SimpleNamespace(
        assert_current_group_leg_authorization=lambda **_kwargs: pytest.fail(
            "finish must not re-enter the released group-authority hold"
        )
    )
    assert session.finish(record=record) is receipt

    changed_session = object.__new__(GroupProofDispatchSession)
    changed_session.__dict__.update(session.__dict__)
    changed_session._finished = False
    changed_session._risk_fence = fence
    with pytest.raises(GroupProofSourceError) as changed_error:
        changed_session.finish(record=replace(record, filled_quantity=Decimal("0.5")))
    assert changed_error.value.code == "GROUP_TERMINAL_RECEIPT_UNPROVEN"

    incomplete_session = object.__new__(GroupProofDispatchSession)
    incomplete_session.__dict__.update(session.__dict__)
    incomplete_session._finished = False
    incomplete_session._risk_fence = fence
    incomplete_session._terminal_receipt = None
    with pytest.raises(GroupProofSourceError) as incomplete_error:
        incomplete_session.finish(record=record)
    assert incomplete_error.value.code == "GROUP_SESSION_FINISH_INVALID"
