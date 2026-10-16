from decimal import Decimal
from fractions import Fraction

import pytest

from bt_api_execution import (
    ExecutionScope,
    ExecutionState,
    GroupAccountReservationSnapshot,
    GroupChildOrderStatus,
    GroupLegPhase,
    GroupLegProgress,
    GroupOrderIntent,
    GroupOrderLegPlan,
    GroupOrderSnapshot,
    GroupOrderState,
    GroupReservationPhase,
    OrderIntent,
    OrderType,
    PositionEffect,
    Side,
    TimeInForce,
    group_leg_intent_id,
)
from bt_api_py.service_execution_group import _owner_signed_filled_exposure


def _snapshot(first_state: ExecutionState, first_filled: str) -> GroupOrderSnapshot:
    scope = ExecutionScope(
        provider="synthetic",
        environment="test",
        account_ref="account-a",
        strategy_id="strategy-a",
    )
    deadline_ns = 100
    first = OrderIntent(
        intent_id=group_leg_intent_id("group-exposure", "leg-buy"),
        scope=scope,
        signal_id="signal-leg-buy",
        instrument="BTC-USD",
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        price=Decimal("10"),
        time_in_force=TimeInForce.IOC,
        metadata_version="metadata-v1",
        deadline_ns=deadline_ns,
    )
    second = OrderIntent(
        intent_id=group_leg_intent_id("group-exposure", "leg-sell"),
        scope=scope,
        signal_id="signal-leg-sell",
        instrument="BTC-USD",
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.5"),
        price=Decimal("10"),
        time_in_force=TimeInForce.IOC,
        metadata_version="metadata-v1",
        deadline_ns=deadline_ns,
    )
    plans = (
        GroupOrderLegPlan(
            leg_id="leg-buy",
            account_ref="account-a",
            maximum_intent=first,
            base_units_per_order_unit=Decimal("2"),
            quantity_step=Decimal("0.1"),
            metadata_valid_until_ns=deadline_ns,
        ),
        GroupOrderLegPlan(
            leg_id="leg-sell",
            account_ref="account-a",
            maximum_intent=second,
            base_units_per_order_unit=Decimal("4"),
            quantity_step=Decimal("0.1"),
            metadata_valid_until_ns=deadline_ns,
        ),
    )
    request = GroupOrderIntent(
        group_id="group-exposure",
        portfolio_ref="portfolio-a",
        strategy_id="strategy-a",
        coordinator_principal_ref="coordinator-a",
        received_at_ns=1,
        deadline_ns=deadline_ns,
        first_leg_id="leg-buy",
        legs=plans,
        version=1,
    )
    progresses = (
        GroupLegProgress(
            leg_id="leg-buy",
            phase=GroupLegPhase.OBSERVED,
            submitted_intent=first,
            child_status=GroupChildOrderStatus(
                intent_id=first.intent_id,
                state=first_state,
                filled_quantity=Decimal(first_filled),
            ),
        ),
        GroupLegProgress(
            leg_id="leg-sell",
            phase=GroupLegPhase.OBSERVED,
            submitted_intent=second,
            child_status=GroupChildOrderStatus(
                intent_id=second.intent_id,
                state=ExecutionState.FILLED,
                filled_quantity=Decimal("0.25"),
            ),
        ),
    )
    reservation = GroupAccountReservationSnapshot(
        group_id=request.group_id,
        account_ref="account-a",
        scope_fingerprint="0" * 64,
        phase=GroupReservationPhase.ACTIVE,
        deadline_ns=deadline_ns,
    )
    return GroupOrderSnapshot(
        request=request,
        state=GroupOrderState.RECOVERY_REQUIRED,
        revision=0,
        reservations=(reservation,),
        legs=progresses,
        residual_base_units=None,
        error_code=None,
        created_at_ns=1,
        updated_at_ns=1,
    )


@pytest.mark.parametrize(
    ("state", "filled", "expected_buy"),
    [
        (ExecutionState.FILLED, "1", Fraction(2)),
        (ExecutionState.CANCELLED, "0.5", Fraction(1)),
        (ExecutionState.REJECTED, "0", Fraction(0)),
    ],
)
def test_owner_signed_filled_exposure_accepts_group_source_terminal_states(
    state: ExecutionState,
    filled: str,
    expected_buy: Fraction,
) -> None:
    exposure = _owner_signed_filled_exposure(_snapshot(state, filled), ())

    assert exposure == {"leg-buy": expected_buy, "leg-sell": Fraction(-1)}


@pytest.mark.parametrize(
    "state",
    [
        ExecutionState.UNKNOWN,
        ExecutionState.ACKED,
        ExecutionState.PARTIALLY_FILLED,
        ExecutionState.BLOCKED,
    ],
)
def test_owner_signed_filled_exposure_rejects_non_group_terminal_states(
    state: ExecutionState,
) -> None:
    assert _owner_signed_filled_exposure(_snapshot(state, "0"), ()) is None
