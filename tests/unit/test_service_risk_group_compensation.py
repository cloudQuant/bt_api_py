"""Focused Risk checks for source-derived group CLOSE authorization."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType

from bt_api_execution import ExecutionState, GroupChildOrderStatus, Side
from bt_api_py.service_group_compensation_contracts import (
    LIMIT_CLOSE_NOTIONAL_V1,
    GroupCompensationActionPhase,
    GroupCompensationSourceFacts,
    derive_group_compensation_action_id,
    group_compensation_budget_policy_fingerprint,
)
from bt_api_py.service_risk_order_entry import _assert_group_compensation_source_exposure


def test_first_leg_filled_exposure_can_be_compensated_with_zero_chain_deficit() -> None:
    """An orphan fill is bounded by actual exposure even when the chain deficit is zero."""

    group_id = "group-first-leg-orphan"
    group_fingerprint = "a" * 64
    source_leg_id = "leg-1"
    source_event_digest = "b" * 64
    action_id = derive_group_compensation_action_id(
        group_id=group_id,
        group_fingerprint=group_fingerprint,
        source_leg_id=source_leg_id,
        source_event_digest=source_event_digest,
        ordinal=1,
    )
    cap = Decimal("100")
    policy_id = "compensation-cap-v1"
    policy_version = 1
    budget_fingerprint = group_compensation_budget_policy_fingerprint(
        policy_id=policy_id,
        policy_version=policy_version,
        cap_amount=cap,
        currency="USD",
        metric=LIMIT_CLOSE_NOTIONAL_V1,
    )
    facts = GroupCompensationSourceFacts(
        phase=GroupCompensationActionPhase.UNSTARTED,
        action_id=action_id,
        ordinal=1,
        group_id=group_id,
        origin_run_id="run-orphan-fill",
        group_fingerprint=group_fingerprint,
        execution_scope_key="paper:account-a:strategy-a:2026-10-16",
        account_ref="account-a",
        strategy_id="strategy-a",
        source_leg_id=source_leg_id,
        source_intent_id="intent-first-leg",
        source_request_fingerprint="c" * 64,
        source_terminal_status=GroupChildOrderStatus(
            intent_id="intent-first-leg",
            state=ExecutionState.FILLED,
            filled_quantity=Decimal("0.75"),
            provider_order_id="provider-order-first-leg",
        ),
        source_requested_quantity=Decimal("0.75"),
        source_claim_digest="d" * 64,
        source_risk_resolution_digest="e" * 64,
        source_event_digest=source_event_digest,
        source_side=Side.BUY,
        instrument="BTC-USD",
        metadata_version="metadata-v1",
        source_price=Decimal("20"),
        source_base_units_per_order_unit=Decimal("1"),
        source_quantity_unit="BTC",
        source_quote_currency="USD",
        source_uncompensated_position_base_units=Decimal("0.75"),
        source_chain_deficit_vector_base_units=(
            ("leg-1", Decimal("0")),
            ("leg-2", Decimal("0")),
            ("leg-3", Decimal("0")),
        ),
        source_filled_position_exposure_vector_base_units=(
            ("leg-1", Decimal("0.75")),
            ("leg-2", Decimal("0")),
            ("leg-3", Decimal("0")),
        ),
        prior_resolved_actions=(),
        compensation_budget_cap_amount=cap,
        action_remaining_budget_amount=cap,
        action_remaining_budget_currency="USD",
        action_remaining_budget_metric=LIMIT_CLOSE_NOTIONAL_V1,
        action_remaining_budget_policy_id=policy_id,
        action_remaining_budget_policy_version=policy_version,
        action_remaining_budget_policy_fingerprint=budget_fingerprint,
    )

    _assert_group_compensation_source_exposure(facts, MappingProxyType({}))

    assert facts.source_chain_deficit_vector_base_units == (
        ("leg-1", Decimal("0")),
        ("leg-2", Decimal("0")),
        ("leg-3", Decimal("0")),
    )
    assert facts.source_uncompensated_position_base_units == Decimal("0.75")
    assert Fraction(facts.source_terminal_status.filled_quantity) == Fraction("0.75")
