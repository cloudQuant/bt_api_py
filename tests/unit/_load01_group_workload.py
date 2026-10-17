"""Registered LOAD01 N5 group intent inputs and client call boundary.

The V3 payload follows the DIM09 wire contract with N5's fixed balanced
three-instrument terms. This module deliberately does not build or attach a
budget quote: the production GroupCoordinatorService must do that through its
configured V3 quote builder.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from bt_api_execution import (
    ExecutionScope,
    OrderIntent,
    Side,
    TimeInForce,
    group_leg_intent_id,
)
from bt_api_risk import INSTRUMENT_METADATA_DIGEST_TAG, InstrumentRiskMetadata

from tests.unit._load01_profiles import Load01Profile


N5_LEG_IDS = ("leg-put", "leg-future", "leg-call")
N5_GROUP_QUANTITY_UNIT = "native"


@dataclass(frozen=True, slots=True)
class N5LegSpec:
    leg_id: str
    instrument: str
    side: Side
    quantity: Decimal
    price: Decimal
    ratio: Decimal
    quantity_step: Decimal
    contract_multiplier: Decimal
    taker_fee_bps: Decimal
    fixed_fee: Decimal
    max_slippage_bps: Decimal


N5_LEG_SPECS = (
    N5LegSpec(
        "leg-put", "PUT-USD", Side.BUY, Decimal("2"), Decimal("10"), Decimal("1"),
        Decimal("0.5"), Decimal("2"), Decimal("25"), Decimal("0.4"), Decimal("100"),
    ),
    N5LegSpec(
        "leg-future", "FUT-USD", Side.SELL, Decimal("8"), Decimal("20"), Decimal("2"),
        Decimal("0.25"), Decimal("1"), Decimal("10"), Decimal("0.1"), Decimal("50"),
    ),
    N5LegSpec(
        "leg-call", "CALL-USD", Side.BUY, Decimal("4"), Decimal("5"), Decimal("3"),
        Decimal("0.5"), Decimal("3"), Decimal("25"), Decimal("0.2"), Decimal("100"),
    ),
)
N5_BASE_UNIT_SCALES = tuple(
    (spec.instrument, spec.contract_multiplier) for spec in N5_LEG_SPECS
)


def build_n5_group_request(
    *,
    group_id: str,
    account_ref: str,
    scope: ExecutionScope,
    metadata: tuple[InstrumentRiskMetadata, ...],
    deadline_ns: int,
) -> dict[str, Any]:
    """Build the fixed, balanced N5 V3 put/future/call request for one owner."""
    if (
        type(group_id) is not str
        or not group_id
        or type(account_ref) is not str
        or not account_ref
        or type(scope) is not ExecutionScope
        or scope.account_ref != account_ref
        or type(metadata) is not tuple
        or len(metadata) != len(N5_LEG_SPECS)
        or any(type(item) is not InstrumentRiskMetadata for item in metadata)
        or type(deadline_ns) is not int
        or deadline_ns <= 0
    ):
        raise ValueError("LOAD01 N5 group request source facts are invalid")

    for spec, item in zip(N5_LEG_SPECS, metadata, strict=True):
        if (
            item.instrument != spec.instrument
            or item.quantity_unit != N5_GROUP_QUANTITY_UNIT
            or item.valuation_unit != "USD"
            or item.quantity_step != spec.quantity_step
            or item.contract_multiplier != spec.contract_multiplier
            or item.taker_fee_bps != spec.taker_fee_bps
            or item.fixed_fee != spec.fixed_fee
            or item.max_slippage_bps != spec.max_slippage_bps
            or item.expires_at_ns < deadline_ns
        ):
            raise ValueError("LOAD01 N5 per-leg metadata does not match its fixed template")

    legs = []
    for spec, item in zip(N5_LEG_SPECS, metadata, strict=True):
        intent = OrderIntent.limit(
            intent_id=group_leg_intent_id(group_id, spec.leg_id),
            scope=scope,
            signal_id=f"dim09-signal-{spec.leg_id}",
            instrument=item.instrument,
            side=spec.side,
            quantity=spec.quantity,
            price=spec.price,
            time_in_force=TimeInForce.IOC,
            metadata_version=item.metadata_version,
            deadline_ns=deadline_ns,
            tags={INSTRUMENT_METADATA_DIGEST_TAG: item.digest},
        )
        legs.append(
            {
                "leg_id": spec.leg_id,
                "account_ref": account_ref,
                "ratio": format(spec.ratio, "f"),
                "intent": intent.to_payload(),
            }
        )
    return {
        "version": 3,
        "group_id": group_id,
        "deadline_ns": deadline_ns,
        "first_leg_id": N5_LEG_SPECS[0].leg_id,
        "legs": legs,
    }


def submit_n5_group(
    runtime: Any,
    profile: Load01Profile,
    *,
    strategy_index: int,
    cycle: int,
    request: dict[str, Any],
    account_ref: str,
    portfolio_ref: str,
    scope_ref: str,
) -> Any:
    """Use the normal client selector for one scheduled N5 group cycle."""
    if (
        profile.profile_id != "N5"
        or profile.order_kind_for_cycle(strategy_index, cycle) != "group"
        or type(request) is not dict
        or type(account_ref) is not str
        or not account_ref
        or type(request.get("legs")) is not list
        or not request["legs"]
        or any(
            type(leg) is not dict or leg.get("account_ref") != account_ref
            for leg in request["legs"]
        )
        or type(portfolio_ref) is not str
        or not portfolio_ref
        or type(scope_ref) is not str
        or not scope_ref
    ):
        raise ValueError("LOAD01 N5 group request does not match its registered cycle")
    # A group coordinator binding is portfolio/strategy scoped: its WireScope
    # carries account_refs, so account_ref is validated on every child leg
    # above and must not be used as a singleton Runtime route selector here.
    return runtime.call(
        "broker.order_groups",
        "groups.submit",
        request,
        portfolio_ref=portfolio_ref,
        scope_ref=scope_ref,
    )


def query_n5_group(
    runtime: Any,
    profile: Load01Profile,
    *,
    strategy_index: int,
    cycle: int,
    group_id: str,
    portfolio_ref: str,
    scope_ref: str,
) -> Any:
    """Read the same group through the client runtime's registered query route."""
    if (
        profile.profile_id != "N5"
        or profile.order_kind_for_cycle(strategy_index, cycle) != "group"
        or type(group_id) is not str
        or not group_id
        or type(portfolio_ref) is not str
        or not portfolio_ref
        or type(scope_ref) is not str
        or not scope_ref
    ):
        raise ValueError("LOAD01 N5 group query does not match its registered cycle")
    return runtime.call(
        "broker.order_groups",
        "groups.query",
        {"group_id": group_id},
        portfolio_ref=portfolio_ref,
        scope_ref=scope_ref,
    )
