"""Fixed-source DIM05 dated-contract coverage on the normal group Host path.

This node reuses the accepted spawned account/coordinator fixture but submits
two distinct dated contracts of the same underlying. It proves that the
current opaque-instrument route preserves each contract identity while the
same-account Risk reservation covers the complete two-leg vector once.
Old-contract close/roll recovery is intentionally not asserted here; that
requires a separate typed group-exit contract.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from bt_api_execution import GroupOrderState, SqliteOrderGroupStore

from tests.integration import test_arb03_dim05_group_partial_fill_service as group_fixture


_OLD_CONTRACT = "BTC-USD-202612-FUT"
_NEXT_CONTRACT = "BTC-USD-202703-FUT"
_GROUP_ID = "group-dim05-cross-period-01"


def test_dim05_single_account_cross_period_group_reserves_once_and_pairs(
    tmp_path: Path,
) -> None:
    """A partial old-contract fill is hedged on the next contract once."""

    with group_fixture._GroupCluster(
        tmp_path,
        accounts=group_fixture._DIM05_ACCOUNTS,
        instruments={"account-a": (_OLD_CONTRACT, _NEXT_CONTRACT)},
        initial_states={"account-a": ("cancelled:1.5", "filled")},
        expected_orders={
            "account-a": (
                (_OLD_CONTRACT, "2", "100", "buy-limit"),
                (_NEXT_CONTRACT, "1.5", "100", "sell-limit"),
            )
        },
        rule_specs={"account-a": {_OLD_CONTRACT: "1", _NEXT_CONTRACT: "1"}},
    ) as cluster:
        snapshot = cluster.submit_group(
            group_fixture._group_request(
                _GROUP_ID,
                cluster.metadata_reference_ns,
                (
                    ("leg-a", "account-a", group_fixture.Side.BUY, Decimal("2"), _OLD_CONTRACT),
                    ("leg-b", "account-a", group_fixture.Side.SELL, Decimal("2"), _NEXT_CONTRACT),
                ),
            )
        )

        assert snapshot.state is GroupOrderState.PAIRED
        assert snapshot.error_code is None
        assert snapshot.residual_base_units == 0
        assert len(snapshot.legs) == 2
        assert tuple(leg.submitted_intent.instrument for leg in snapshot.legs) == (
            _OLD_CONTRACT,
            _NEXT_CONTRACT,
        )
        assert snapshot.legs[0].child_status is not None
        assert snapshot.legs[0].child_status.state.value == "CANCELLED"
        assert snapshot.legs[0].child_status.filled_quantity == Decimal("1.5")
        assert snapshot.legs[1].child_status is not None
        assert snapshot.legs[1].child_status.state.value == "FILLED"
        assert snapshot.legs[1].submitted_intent is not None
        assert snapshot.legs[1].submitted_intent.quantity == Decimal("1.5")

        assert len(snapshot.reservations) == 1
        reservation = snapshot.reservations[0]
        assert reservation.account_ref == "account-a"
        assert len(reservation.legs) == 2
        assert {leg.leg_id for leg in reservation.legs} == {"leg-a", "leg-b"}

    groups = SqliteOrderGroupStore(cluster.coordinator_paths["groups"])
    try:
        persisted = groups.get(group_fixture._PORTFOLIO, _GROUP_ID)
        assert persisted == snapshot
        events = groups.events(group_fixture._PORTFOLIO, _GROUP_ID)
    finally:
        groups.close()
    assert sum(event.event_type == "GROUP_PAIRED" for event in events) == 1

    risk_legs = group_fixture._risk_legs(
        cluster.account_paths, "account-a", _GROUP_ID
    )
    assert risk_legs == (("leg-a", True, True), ("leg-b", True, True))
    group_fixture._assert_holds_resolved(cluster.account_paths, "account-a", _GROUP_ID)
    group_fixture._assert_provider_writes(
        Path(cluster.account_paths["account-a"]["provider"]), 2
    )
