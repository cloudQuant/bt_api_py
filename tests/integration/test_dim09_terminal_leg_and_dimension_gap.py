"""DIM09 focused coverage for terminal third-leg and sizing gaps.

These tests reuse the real coordinator, account-owner, Risk admission, trusted
metadata registry, provider-proof journal, and SQLite-backed fixture graph.
Only the provider leaf remains synthetic.  They intentionally leave unresolved
third-leg exposure in recovery; they do not claim that these cases are closed.
"""

from __future__ import annotations

import time
from decimal import Decimal

from bt_api_execution import (
    ExecutionState,
    GroupBudgetReservationState,
    GroupOrderSnapshot,
    GroupOrderState,
)
from bt_api_execution.group_order_contracts import GroupReservationPhase
from bt_api_risk import InstrumentRiskMetadata, RiskGroupReservationState

from tests.integration import _dim09_three_leg_fixtures as fixtures


def _source_event(cluster, intent_id: str):
    events = cluster.proof_journal.events(cluster.risk_scope.key, intent_id)
    observed = [event for event in events if event.event_type == "SOURCE_OBSERVED"]
    assert len(observed) == 1
    return observed[0]


def _assert_provider_write_bindings(cluster, group_id: str, snapshot, provider_writes) -> None:
    intent_ids = [leg.submitted_intent.intent_id for leg in snapshot.legs]
    assert intent_ids == [
        cluster.bound_intent_id(group_id, leg.leg_id) for leg in snapshot.legs
    ]

    loopback_intent_ids = cluster.loopback.submitted_intent_ids()
    assert loopback_intent_ids == intent_ids
    assert len(loopback_intent_ids) == len(set(loopback_intent_ids)) == len(snapshot.legs) == 3

    bound_client_order_ids = []
    for leg in snapshot.legs:
        events = cluster.proof_journal.events(
            cluster.risk_scope.key, leg.submitted_intent.intent_id
        )
        attempts = [event for event in events if event.event_type == "ATTEMPT_STARTED"]
        observed = [event for event in events if event.event_type == "SOURCE_OBSERVED"]
        assert len(attempts) == 1
        assert len(observed) == 1
        for event in (*attempts, *observed):
            assert event.account_scope_key == cluster.risk_scope.key
            assert event.group_id == group_id
            assert event.leg_id == leg.leg_id
            assert event.intent_id == leg.submitted_intent.intent_id

        attempt_client_order_id = attempts[0].payload.get("client_order_id")
        observed_client_order_id = observed[0].payload.get("client_order_id")
        assert type(attempt_client_order_id) is str and attempt_client_order_id
        assert observed_client_order_id == attempt_client_order_id
        bound_client_order_ids.append(attempt_client_order_id)

    assert len(bound_client_order_ids) == len(set(bound_client_order_ids)) == len(snapshot.legs)
    assert len(provider_writes) == len(set(provider_writes)) == len(snapshot.legs)
    assert tuple(bound_client_order_ids) == tuple(provider_writes)


def _assert_source_fee(cluster, leg, *, fee: Decimal, row_count: int) -> None:
    assert leg.submitted_intent is not None
    assert leg.child_status is not None
    event = _source_event(cluster, leg.submitted_intent.intent_id)
    payload = event.payload
    records = payload["fill_records"]
    assert len(records) == row_count
    assert Decimal(str(payload["fee_total"])) == fee
    assert payload["fee_currency"] == ("USD" if row_count else None)
    if row_count:
        record = records[0]
        normalized = record["record"]
        provenance = record["normalization_provenance"]
        assert Decimal(str(normalized["fee"])) == fee
        assert Decimal(str(provenance["source_values"]["fee_amount"])) == fee
        assert provenance["source_values"]["fee_currency"] == "USD"
        assert leg.child_status.cumulative_commission == fee
        assert leg.child_status.cumulative_commission_currency == "USD"
    else:
        assert leg.child_status.cumulative_commission is None
        assert leg.child_status.cumulative_commission_currency is None


def _assert_unclosed_recovery_holds(cluster, snapshot, expected_risk_states) -> None:
    assert snapshot.state is GroupOrderState.RECOVERY_REQUIRED
    assert snapshot.residual_vector is not None
    assert sum((abs(value) for value in snapshot.residual_vector), Decimal("0")) == (
        snapshot.residual_base_units
    )
    assert snapshot.residual_base_units > 0
    assert snapshot.budget_reservation is not None
    assert snapshot.budget_reservation.state is GroupBudgetReservationState.HELD
    quote = snapshot.request.budget_quote
    assert quote is not None
    assert snapshot.budget_reservation.gross_notional == quote.gross_notional
    assert len(snapshot.reservations) == 1
    assert snapshot.reservations[0].phase is GroupReservationPhase.ACTIVE

    vector = cluster.risk_admission.read_group_vector_snapshot(
        cluster.risk_scope, snapshot.request.group_id
    )
    risk_by_leg = {item.leg_id: item for item in vector.legs}
    assert set(risk_by_leg) == set(fixtures.LEG_IDS)
    assert all(item.claimed and item.resolved for item in vector.legs)
    assert {leg_id: item.state for leg_id, item in risk_by_leg.items()} == expected_risk_states


def test_dim09_third_leg_terminal_zero_fill_preserves_residual_and_holds(
    tmp_path, monkeypatch
) -> None:
    cluster = fixtures.Dim09ThreeLegCluster(
        tmp_path, monkeypatch, initial_states=("filled", "filled", "rejected")
    )
    try:
        group_id = "dim09-third-zero-terminal"
        result = cluster.submit(cluster.v3_body(group_id))
        assert result["ok"] is True, result
        snapshot = GroupOrderSnapshot.from_payload(result["result"]["snapshot"])

        _assert_unclosed_recovery_holds(
            cluster,
            snapshot,
            {
                "leg-put": RiskGroupReservationState.SETTLED,
                "leg-future": RiskGroupReservationState.SETTLED,
                "leg-call": RiskGroupReservationState.NO_FILL_TERMINAL,
            },
        )
        assert snapshot.error_code == "chain_interrupted_zero_fill"
        assert snapshot.residual_vector == (
            Decimal("0"),
            Decimal("0"),
            Decimal("1"),
        )
        assert snapshot.residual_base_units == Decimal("1")

        expected = (
            (ExecutionState.FILLED, Decimal("1"), "TERMINAL_EXPOSURE_RESOLVED", 1),
            (ExecutionState.FILLED, Decimal("1"), "TERMINAL_EXPOSURE_RESOLVED", 1),
            (ExecutionState.REJECTED, Decimal("0"), "TERMINAL_NO_FILL_RESOLVED", 0),
        )
        for leg, (state, filled, resolution, trades) in zip(snapshot.legs, expected, strict=True):
            assert leg.child_status is not None
            assert leg.child_status.state is state
            assert leg.child_status.filled_quantity == filled
            assert leg.resolution_receipt is not None
            assert leg.resolution_receipt.final_state is state
            assert leg.resolution_receipt.resolution_kind == resolution
            assert leg.resolution_receipt.trade_count == trades
        _assert_source_fee(cluster, snapshot.legs[0], fee=Decimal("-0.05"), row_count=1)
        _assert_source_fee(cluster, snapshot.legs[1], fee=Decimal("-0.05"), row_count=1)
        _assert_source_fee(cluster, snapshot.legs[2], fee=Decimal("0"), row_count=0)

        writes, counters = cluster.provider.snapshot()
        _assert_provider_write_bindings(cluster, group_id, snapshot, writes)
        assert counters == {"order_queries": 3, "fill_queries": 3}
        assert cluster.current_snapshot(group_id) == snapshot
    finally:
        cluster.close()


def test_dim09_third_leg_terminal_partial_fill_keeps_actual_residual_and_holds(
    tmp_path, monkeypatch
) -> None:
    cluster = fixtures.Dim09ThreeLegCluster(
        tmp_path,
        monkeypatch,
        initial_states=("filled", "filled", "cancelled:0.5"),
    )
    try:
        group_id = "dim09-third-partial-terminal"
        result = cluster.submit(cluster.v3_body(group_id))
        assert result["ok"] is True, result
        snapshot = GroupOrderSnapshot.from_payload(result["result"]["snapshot"])

        _assert_unclosed_recovery_holds(
            cluster,
            snapshot,
            dict.fromkeys(fixtures.LEG_IDS, RiskGroupReservationState.SETTLED),
        )
        assert snapshot.error_code == "chain_residual_unresolved"
        assert snapshot.residual_vector == (
            Decimal("0"),
            Decimal("0"),
            Decimal("0.5"),
        )
        assert snapshot.residual_base_units == Decimal("0.5")

        third = snapshot.legs[2]
        assert third.submitted_intent is not None
        assert third.child_status is not None
        assert third.child_status.state is ExecutionState.CANCELLED
        assert third.child_status.filled_quantity == Decimal("0.5")
        assert third.child_status.average_price == Decimal("100")
        assert third.child_status.trade_count == 1
        assert third.resolution_receipt is not None
        assert third.resolution_receipt.final_state is ExecutionState.CANCELLED
        assert third.resolution_receipt.resolution_kind == "TERMINAL_EXPOSURE_RESOLVED"
        assert third.resolution_receipt.filled_quantity == Decimal("0.5")
        assert third.resolution_receipt.trade_count == 1

        for leg in snapshot.legs[:2]:
            assert leg.child_status is not None
            assert leg.child_status.filled_quantity == Decimal("1")
            assert leg.resolution_receipt is not None
            assert leg.resolution_receipt.trade_count == 1
        for leg in snapshot.legs:
            _assert_source_fee(cluster, leg, fee=Decimal("-0.05"), row_count=1)

        writes, counters = cluster.provider.snapshot()
        _assert_provider_write_bindings(cluster, group_id, snapshot, writes)
        assert counters == {"order_queries": 3, "fill_queries": 3}
        assert cluster.current_snapshot(group_id) == snapshot
    finally:
        cluster.close()


def test_dim09_v3_sizes_nonunit_ratios_from_heterogeneous_trusted_multipliers(
    tmp_path, monkeypatch
) -> None:
    now_ns = time.time_ns()
    symbols = ("BTC-USD", "ETH-USD", "SOL-USD")
    multipliers = (Decimal("2"), Decimal("3"), Decimal("5"))
    metadata = tuple(
        InstrumentRiskMetadata(
            instrument=symbol,
            metadata_version=f"dim09-dimension-{index}-v1",
            as_of_ns=now_ns - 1_000_000_000,
            expires_at_ns=now_ns + 3_600_000_000_000,
            tick_size=Decimal("0.01"),
            quantity_step=Decimal("0.001"),
            contract_multiplier=multiplier,
            max_gross_notional=Decimal("1000000"),
            taker_fee_bps=Decimal("0"),
            fixed_fee=Decimal("0"),
            max_slippage_bps=Decimal("0"),
            quantity_unit="base",
            valuation_unit="USD",
            quote_currency="USD",
        )
        for index, (symbol, multiplier) in enumerate(zip(symbols, multipliers, strict=True), 1)
    )
    expected_orders = (
        (symbols[0], Decimal("2"), Decimal("100"), "buy-limit"),
        (symbols[1], Decimal("2"), Decimal("100"), "sell-limit"),
        (symbols[2], Decimal("0.48"), Decimal("100"), "buy-limit"),
    )
    cluster = fixtures.Dim09ThreeLegCluster(
        tmp_path,
        monkeypatch,
        initial_states=("filled", "filled", "filled"),
        instrument_metadata=metadata,
        base_units_per_order_unit_by_instrument=dict(zip(symbols, multipliers, strict=True)),
        expected_provider_orders=expected_orders,
    )
    try:
        group_id = "dim09-nonunit-ratios-multipliers"
        body = cluster.v3_body(
            group_id,
            ratios=("1", "1.5", "0.6"),
            leg_instruments=symbols,
            quantities=(Decimal("2"), Decimal("2"), Decimal("0.48")),
            prices=(Decimal("100"), Decimal("100"), Decimal("100")),
        )
        result = cluster.submit(body)
        assert result["ok"] is True, result
        snapshot = GroupOrderSnapshot.from_payload(result["result"]["snapshot"])

        assert snapshot.state is GroupOrderState.PAIRED
        assert snapshot.residual_vector == (Decimal("0"), Decimal("0"), Decimal("0"))
        assert snapshot.residual_base_units == Decimal("0")
        assert [leg.ratio for leg in snapshot.request.ordered_legs] == [
            Decimal("1"),
            Decimal("1.5"),
            Decimal("0.6"),
        ]
        assert [leg.base_units_per_order_unit for leg in snapshot.request.ordered_legs] == [
            Decimal("2"),
            Decimal("3"),
            Decimal("5"),
        ]
        actual = [leg.submitted_intent for leg in snapshot.legs]
        assert [intent.instrument for intent in actual] == list(symbols)
        assert [intent.quantity for intent in actual] == [
            Decimal("2"),
            Decimal("2"),
            Decimal("0.48"),
        ]
        base_exposures = [
            intent.quantity * plan.base_units_per_order_unit
            for intent, plan in zip(actual, snapshot.request.ordered_legs, strict=True)
        ]
        assert base_exposures == [Decimal("4"), Decimal("6"), Decimal("2.40")]

        quote = snapshot.request.budget_quote
        assert quote is not None
        assert quote.valuation_unit == "USD"
        assert [leg.gross_notional for leg in quote.legs] == [
            Decimal("400"),
            Decimal("600"),
            Decimal("240.00"),
        ]
        assert quote.gross_notional == Decimal("1240.00")
        assert snapshot.budget_reservation is not None
        assert snapshot.budget_reservation.state is GroupBudgetReservationState.HELD
        assert snapshot.budget_reservation.gross_notional == quote.gross_notional
        assert snapshot.budget_reservation.quote_fingerprint == quote.fingerprint
        assert len(snapshot.reservations) == 1
        assert snapshot.reservations[0].phase is GroupReservationPhase.ACTIVE

        vector = cluster.risk_admission.read_group_vector_snapshot(cluster.risk_scope, group_id)
        assert len(vector.legs) == 3
        assert all(
            item.claimed and item.resolved and item.state is RiskGroupReservationState.SETTLED
            for item in vector.legs
        )
        for leg, expected_metadata in zip(snapshot.legs, metadata, strict=True):
            assert leg.child_status is not None
            assert leg.child_status.state is ExecutionState.FILLED
            assert leg.child_status.filled_quantity == leg.submitted_intent.quantity
            assert leg.child_status.cumulative_commission == Decimal("-0.05")
            event = _source_event(cluster, leg.submitted_intent.intent_id)
            assert event.payload["metadata_digest"] == expected_metadata.digest
            assert event.payload["metadata_version"] == expected_metadata.metadata_version
            assert Decimal(str(event.payload["metadata_multiplier"])) == (
                expected_metadata.contract_multiplier
            )
            _assert_source_fee(cluster, leg, fee=Decimal("-0.05"), row_count=1)

        writes, counters = cluster.provider.snapshot()
        _assert_provider_write_bindings(cluster, group_id, snapshot, writes)
        assert counters == {"order_queries": 3, "fill_queries": 3}
    finally:
        cluster.close()
