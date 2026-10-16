"""ARB04 group-cancel timeout recovery over the real local ServiceHost chain.

The fixed synthetic provider persists a cancel side effect and then loses its
response. The coordinator's upstream CURVE transport also drops the first
account-owner reply. Both uncertainties must remain UNKNOWN and the original
cancel business id may only be queried on replay.
"""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from bt_api_execution import (
    CancelRecord,
    ExecutionState,
    GroupBudgetReservationState,
    GroupOrderState,
    Side,
    SqliteExecutionStore,
    group_leg_intent_id,
)

from bt_api_py.service_execution_group_cancel import (
    GroupCancelPlanStore,
    group_cancel_leg_id,
)
from bt_api_py.service_execution_group_client import GroupOriginRunBindingStore

from tests.integration._arb04_group_reply_loss_support import Arb04GroupCluster
from tests.integration.test_arb03_dim05_group_partial_fill_service import (
    _PORTFOLIO,
    _RUN,
    _execution_scope,
    _group_request,
    _assert_holds_resolved,
    _risk_legs,
)
from tests.unit._service_group_provider_fixtures import _V2_INSTRUMENT

_ARB04_CANCEL_ACCOUNTS = ("account-a", "account-b")
_GROUP_CANCEL_UNKNOWN = "group-arb04-cancel-unknown-01"
_GROUP_CANCEL_REQUEST = "cancel-arb04-unknown-01"
_GROUP_CANCEL_REQUEST_REPLAY = "cancel-arb04-unknown-02"


def test_arb04_group_cancel_unknown_replays_only_original_cancel_query(
    tmp_path: Path,
) -> None:
    """A timeout after provider side effect cannot duplicate group cancellation."""

    with Arb04GroupCluster(
        tmp_path,
        accounts=_ARB04_CANCEL_ACCOUNTS,
        instruments={account: (_V2_INSTRUMENT,) for account in _ARB04_CANCEL_ACCOUNTS},
        initial_states={"account-a": ("open",), "account-b": ("open",)},
        expected_orders={
            "account-a": ((_V2_INSTRUMENT, "2", "100", "buy-limit"),),
            "account-b": ((_V2_INSTRUMENT, "4", "100", "sell-limit"),),
        },
        rule_specs={
            "account-a": {_V2_INSTRUMENT: "2"},
            "account-b": {_V2_INSTRUMENT: "1"},
        },
        fault_operation="orders.cancel_group_leg",
        fault_drops=0,
        enable_group_cancel=True,
    ) as cluster:
        original_request = _group_request(
            _GROUP_CANCEL_UNKNOWN,
            cluster.metadata_reference_ns,
            (
                ("leg-a", "account-a", Side.BUY, Decimal("2"), _V2_INSTRUMENT),
                ("leg-b", "account-b", Side.SELL, Decimal("4"), _V2_INSTRUMENT),
            ),
        )
        accepted = cluster.submit_group(original_request)
        assert accepted.state is GroupOrderState.FIRST_PENDING
        assert accepted.budget_reservation is not None
        assert accepted.budget_reservation.state is GroupBudgetReservationState.HELD
        assert accepted.legs[0].submitted_intent is not None
        assert accepted.legs[0].child_status is not None
        assert accepted.legs[0].child_status.state is ExecutionState.ACKED
        assert accepted.legs[1].submitted_intent is None

        # Keep setup requests clean; arm the single reply loss only after the
        # target order has an observed provider receipt.
        setup_counts, setup_dropped, setup_remaining = cluster.fault_stats()
        assert setup_counts[("account-a", "orders.submit_group_leg")] == 1
        assert setup_dropped == 0
        assert setup_remaining == 0
        cluster.arm_fault("orders.cancel_group_leg", 1)

        # The synthetic provider changes its durable order state, then times
        # out. The account service persists UNKNOWN; the coordinator never
        # receives the first account-owner reply either.
        first = cluster.cancel_group(_GROUP_CANCEL_UNKNOWN, _GROUP_CANCEL_REQUEST)
        assert first["found"] is True
        assert first["group_id"] == _GROUP_CANCEL_UNKNOWN
        assert first["cancel_request_id"] == _GROUP_CANCEL_REQUEST
        assert len(first["legs"]) == 1
        first_leg = first["legs"][0]
        assert first_leg["leg_id"] == "leg-a"
        assert first_leg["account_ref"] == "account-a"
        assert first_leg["cancel_id"] == group_cancel_leg_id(
            _PORTFOLIO,
            _GROUP_CANCEL_UNKNOWN,
            _RUN,
            "leg-a",
            "account-a",
            _execution_scope("account-a").key,
            first_leg["target_intent_id"],
            first_leg["provider_order_id"],
        )
        assert first_leg["target_intent_id"] == group_leg_intent_id(
            _GROUP_CANCEL_UNKNOWN, "leg-a"
        )
        assert first_leg["provider_order_id"] == accepted.legs[0].child_status.provider_order_id
        assert first_leg["cancel_id"] != group_cancel_leg_id(
            _PORTFOLIO,
            _GROUP_CANCEL_UNKNOWN,
            f"{_RUN}-next",
            "leg-a",
            "account-a",
            _execution_scope("account-a").key,
            first_leg["target_intent_id"],
            first_leg["provider_order_id"],
        )
        assert first_leg["phase"] == "UNKNOWN"
        # The coordinator has no account result in hand when the reply is
        # dropped; it records the leg as UNKNOWN without fabricating one.
        assert first_leg["result"] is None
        counts, dropped, remaining = cluster.fault_stats()
        assert counts[("account-a", "orders.cancel_group_leg")] == 1
        assert counts[("account-a", "orders.submit_group_leg")] == 1
        assert counts.get(("account-b", "orders.cancel_group_leg")) is None
        assert counts.get(("account-a", "reservations.release")) is None
        assert counts.get(("account-b", "reservations.release")) is None
        assert dropped == 1
        assert remaining == 0

        # Replaying the same request reads the same durable child cancel id.
        # It does not call the provider cancellation route a second time.
        replay = cluster.cancel_group(_GROUP_CANCEL_UNKNOWN, _GROUP_CANCEL_REQUEST)
        assert replay["found"] is True
        assert replay["cancel_request_id"] == _GROUP_CANCEL_REQUEST
        assert len(replay["legs"]) == 1
        assert replay["legs"][0]["cancel_id"] == first["legs"][0]["cancel_id"]
        assert replay["legs"][0]["result"]["record"]["state"] == "UNKNOWN", (
            replay["legs"][0]["result"]["record"]["state"]
        )
        assert replay["legs"][0]["phase"] == "UNKNOWN", replay["legs"][0]
        assert replay["legs"][0]["result"]["found"] is True
        first_record = replay["legs"][0]["result"]["record"]
        assert set(first_record) == {
            "cancel_id",
            "scope_key",
            "target_intent_id",
            "provider_order_id",
            "payload_sha256",
            "state",
            "dispatch_attempts",
            "review_required",
            "created_at_ns",
            "updated_at_ns",
            "is_terminal",
        }
        assert first_record["cancel_id"] == first["legs"][0]["cancel_id"]
        assert first_record["target_intent_id"] == first["legs"][0]["target_intent_id"]
        assert first_record["provider_order_id"] == first["legs"][0]["provider_order_id"]
        assert first_record["scope_key"] == _execution_scope("account-a").key
        assert len(first_record["payload_sha256"]) == 64
        assert first_record["state"] == "UNKNOWN"
        assert first_record["dispatch_attempts"] == 1
        assert first_record["review_required"] is True
        assert first_record["is_terminal"] is False
        counts, dropped, remaining = cluster.fault_stats()
        assert counts[("account-a", "orders.cancel_group_leg")] == 1
        assert counts[("account-a", "orders.query_group_cancel")] == 1
        assert counts[("account-a", "orders.submit_group_leg")] == 1
        assert counts.get(("account-b", "orders.cancel_group_leg")) is None
        assert counts.get(("account-a", "reservations.release")) is None
        assert counts.get(("account-b", "reservations.release")) is None
        assert dropped == 1
        assert remaining == 0

        # A new request id has its own coordinator idempotency record, while
        # the shared child-cancel claim forces query-only handling of the same
        # original order cancellation. It cannot issue another cancel RPC.
        second_request = cluster.cancel_group(
            _GROUP_CANCEL_UNKNOWN, _GROUP_CANCEL_REQUEST_REPLAY
        )
        assert second_request["found"] is True
        assert second_request["cancel_request_id"] == _GROUP_CANCEL_REQUEST_REPLAY
        assert len(second_request["legs"]) == 1
        assert second_request["legs"][0]["cancel_id"] == first["legs"][0]["cancel_id"]
        assert second_request["legs"][0]["phase"] == "UNKNOWN"
        assert second_request["legs"][0]["result"]["record"]["state"] == "UNKNOWN"
        counts, dropped, remaining = cluster.fault_stats()
        assert counts[("account-a", "orders.cancel_group_leg")] == 1
        assert counts[("account-a", "orders.query_group_cancel")] == 2
        assert counts[("account-a", "orders.submit_group_leg")] == 1
        assert counts.get(("account-a", "reservations.release")) is None
        assert counts.get(("account-b", "reservations.release")) is None
        assert dropped == 1
        assert remaining == 0

        # The external provider side effect is not itself enough to settle the
        # original submit claim. The cancellation receipt remains UNKNOWN and
        # is only read above; an exact replay of the accepted group request is
        # the independent original-order reconciliation path. It may settle
        # the original hold only from the provider's complete order/fill proof.
        provider_path = cluster.account_paths["account-a"]["provider"]
        with sqlite3.connect(provider_path) as connection:
            order_json, fills_json = connection.execute(
                "SELECT order_json, fills_json FROM provider_orders"
            ).fetchone()
            provider_order = json.loads(order_json)
            cancel_rows = connection.execute(
                "SELECT cancel_id, provider_order_id, target_intent_id "
                "FROM provider_cancel_dispatches"
            ).fetchall()
            cancel_attempts = connection.execute(
                "SELECT cancel_id, provider_order_id, target_intent_id "
                "FROM provider_cancel_attempts ORDER BY sequence"
            ).fetchall()
        assert provider_order["status"] == "cancelled"
        assert json.loads(fills_json) == []
        assert len(cancel_rows) == 1
        assert cancel_rows[0] == (
            replay["legs"][0]["cancel_id"],
            replay["legs"][0]["provider_order_id"],
            replay["legs"][0]["target_intent_id"],
        )
        assert cancel_attempts == cancel_rows

        completed = cluster.submit_group(original_request)
        assert completed.state is GroupOrderState.CLOSED
        assert completed.budget_reservation is not None
        assert completed.budget_reservation.state is GroupBudgetReservationState.RELEASED
        assert completed.legs[0].child_status is not None
        assert completed.legs[0].child_status.state is ExecutionState.CANCELLED
        assert completed.legs[0].child_status.filled_quantity == Decimal("0")
        assert completed.legs[0].claim_receipt is not None
        assert completed.legs[0].resolution_receipt is not None
        assert completed.legs[0].resolution_receipt.claim == completed.legs[0].claim_receipt
        assert completed.legs[0].resolution_receipt.final_state is ExecutionState.CANCELLED
        assert completed.legs[0].resolution_receipt.resolution_kind == "TERMINAL_NO_FILL_RESOLVED"
        assert completed.legs[0].resolution_receipt.filled_quantity == Decimal("0")
        assert completed.legs[0].resolution_receipt.dispatch_latch_active is False
        assert _risk_legs(cluster.account_paths, "account-a", _GROUP_CANCEL_UNKNOWN) == (
            ("leg-a", True, True),
        )
        _assert_holds_resolved(
            cluster.account_paths, "account-a", _GROUP_CANCEL_UNKNOWN
        )

        execution_store = SqliteExecutionStore(
            cluster.account_paths["account-a"]["execution"]
        )
        try:
            intent = execution_store.get_cancel_intent(
                first["legs"][0]["cancel_id"], scope=_execution_scope("account-a")
            )
            record = execution_store.get_cancel(
                first["legs"][0]["cancel_id"], scope=_execution_scope("account-a")
            )
            target = execution_store.get(
                first["legs"][0]["target_intent_id"], scope=_execution_scope("account-a")
            )
            assert intent is not None
            assert "cancel_request_id" not in intent.tags
            assert type(record) is CancelRecord
            assert record.state is ExecutionState.UNKNOWN
            assert record.dispatch_attempts == 1
            assert record.review_required is True
            assert target is not None and target.state is ExecutionState.CANCELLED
            assert target.filled_quantity == Decimal("0")
        finally:
            execution_store.close()

        counts, dropped, remaining = cluster.fault_stats()
        assert counts[("account-a", "orders.cancel_group_leg")] == 1
        assert counts[("account-a", "orders.query_group_cancel")] == 2
        assert counts[("account-a", "orders.submit_group_leg")] == 1
        assert counts[("account-a", "orders.query_group_leg")] >= 1
        assert dropped == 1
        assert remaining == 0

    # Coordinator plan durability survives Host shutdown and is read-only here.
    run_bindings = GroupOriginRunBindingStore(cluster.coordinator_paths["run_bindings"])
    try:
        plan = GroupCancelPlanStore(run_bindings).get(
            _PORTFOLIO, _GROUP_CANCEL_UNKNOWN, _GROUP_CANCEL_REQUEST
        )
        replay_plan = GroupCancelPlanStore(run_bindings).get(
            _PORTFOLIO, _GROUP_CANCEL_UNKNOWN, _GROUP_CANCEL_REQUEST_REPLAY
        )
        assert plan is not None and replay_plan is not None
        assert plan.cancel_request_id == _GROUP_CANCEL_REQUEST
        assert replay_plan.cancel_request_id == _GROUP_CANCEL_REQUEST_REPLAY
        assert len(plan.legs) == len(replay_plan.legs) == 1
        assert plan.legs[0].cancel_id == first["legs"][0]["cancel_id"]
        assert replay_plan.legs[0].cancel_id == first["legs"][0]["cancel_id"]
        assert plan.legs[0].phase == replay_plan.legs[0].phase == "UNKNOWN"
        assert plan.legs[0].result is not None and replay_plan.legs[0].result is not None
        assert plan.legs[0].result["record"]["dispatch_attempts"] == 1
        assert replay_plan.legs[0].result["record"]["dispatch_attempts"] == 1
        child_claim = run_bindings._connection.execute(
            "SELECT portfolio_ref, group_id, origin_run_id, leg_id, account_ref, "
            "target_intent_id, provider_order_id, owner_cancel_request_id, phase "
            "FROM coordinator_group_cancel_child_claims WHERE cancel_id=?",
            (first["legs"][0]["cancel_id"],),
        ).fetchone()
        assert child_claim is not None
        assert tuple(child_claim) == (
            _PORTFOLIO,
            _GROUP_CANCEL_UNKNOWN,
            _RUN,
            "leg-a",
            "account-a",
            first["legs"][0]["target_intent_id"],
            first["legs"][0]["provider_order_id"],
            _GROUP_CANCEL_REQUEST,
            "UNKNOWN",
        )
    finally:
        run_bindings.close()
