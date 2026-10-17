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
from types import SimpleNamespace

from bt_api_execution import (
    CancelRecord,
    ExecutionState,
    GroupBudgetReservationState,
    GroupLegPhase,
    GroupOrderState,
    Side,
    SqliteExecutionStore,
    SqliteOrderGroupStore,
    group_leg_intent_id,
)

from bt_api_py.service_execution_group_cancel import (
    GroupCancelPlanStore,
    group_cancel_leg_id,
)
from bt_api_py.service_execution_group_authority import (
    ExecutionGroupAuthority,
    ExecutionStoreGroupCompletionVerifier,
)
from bt_api_py.service_execution_group_client import GroupOriginRunBindingStore

from tests.integration._arb04_group_reply_loss_support import (
    Arb04GroupCluster,
    _ReplyLossTransport,
    _WireFaultState,
)
from tests.integration.test_arb03_dim05_group_partial_fill_service import (
    _PORTFOLIO,
    _RUN,
    _execution_scope,
    _group_request,
    _assert_holds_resolved,
    _provider_snapshot,
    _risk_legs,
)
from tests.unit._service_group_provider_fixtures import (
    _V2_INSTRUMENT,
    _V2SyntheticBtApiFeed,
)

_ARB04_CANCEL_ACCOUNTS = ("account-a", "account-b")
_GROUP_CANCEL_UNKNOWN = "group-arb04-cancel-unknown-01"
_GROUP_CANCEL_REQUEST = "cancel-arb04-unknown-01"
_GROUP_CANCEL_REQUEST_REPLAY = "cancel-arb04-unknown-02"
_GROUP_CANCEL_SOCKET_DISCONNECT = "group-arb04-cancel-socket-disconnect-01"
_GROUP_CANCEL_SOCKET_REQUEST = "cancel-arb04-socket-disconnect-01"
_GROUP_DUPLICATE_FILL = "group-arb04-duplicate-fill-01"
_GROUP_LATE_FILL_QUERY = "group-arb04-late-fill-explicit-query-01"


def test_arb04_fault_counter_tracks_wrapped_owner_rpc_only() -> None:
    """The fault counter measures transport requests, not local proof work."""

    class _InnerTransport:
        _transport = None

        def request(self, request: object, *, timeout: float) -> object:
            assert timeout == 5.0
            return request

    state = _WireFaultState("orders.cancel_group_leg", 0)
    transport = _ReplyLossTransport(_InnerTransport(), state, "account-a")
    request = SimpleNamespace(
        header=SimpleNamespace(
            operation="orders.query_group_cancel",
            message_id="arb04-counter-scope-regression",
        )
    )

    assert transport.request(request, timeout=5.0) is request
    assert state.statistics() == (
        (("account-a", "orders.query_group_cancel", 1),),
        0,
        0,
    )


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


def test_arb04_group_cancel_real_socket_close_reconnects_for_original_resolution(
    tmp_path: Path,
) -> None:
    """A closed DEALER after provider dispatch stays UNKNOWN, then reconnects."""

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
        fault_mode="socket_disconnect_after_provider_dispatch",
        enable_group_cancel=True,
    ) as cluster:
        original_request = _group_request(
            _GROUP_CANCEL_SOCKET_DISCONNECT,
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
        assert accepted.legs[0].child_status is not None
        assert accepted.legs[0].child_status.state is ExecutionState.ACKED

        cluster.arm_fault("orders.cancel_group_leg", 1)
        first = cluster.cancel_group(
            _GROUP_CANCEL_SOCKET_DISCONNECT, _GROUP_CANCEL_SOCKET_REQUEST
        )
        assert first["found"] is True
        assert first["cancel_request_id"] == _GROUP_CANCEL_SOCKET_REQUEST
        assert first["legs"][0]["phase"] == "UNKNOWN"
        cancel_id = first["legs"][0]["cancel_id"]
        assert first["legs"][0]["result"] is None

        fault_snapshot = cluster.fault_details()
        assert fault_snapshot["fault_mode"] == "socket_disconnect_after_provider_dispatch"
        assert fault_snapshot["operation"] == "orders.cancel_group_leg"
        fault_counts = {
            (account_ref, operation): count
            for account_ref, operation, count in fault_snapshot["counts"]
        }
        boundary_events = fault_snapshot["boundary_events"]
        assert [event["phase"] for event in boundary_events] == [
            "plan_claim_dispatch",
            "upstream_port_enter",
            "upstream_port_error",
        ], boundary_events
        assert boundary_events[0]["claimed"] is True, boundary_events
        assert boundary_events[2]["error_type"] == "RequestOutcomeUnknownError", (
            boundary_events
        )
        assert fault_counts.get(("account-a", "orders.cancel_group_leg")) == 1
        assert fault_snapshot["dropped"] == 1
        assert fault_snapshot["remaining"] == 0
        fault_events = fault_snapshot["events"]
        assert [event["phase"] for event in fault_events] == [
            "socket_closed_before_reply_receive",
            "native_poll_error_after_close",
            "client_uncertain_result",
        ], fault_snapshot
        close_event, native_error, unknown_event = fault_events
        assert close_event["account_ref"] == "account-a"
        assert close_event["operation"] == "orders.cancel_group_leg"
        assert close_event["message_id"] == native_error["message_id"]
        assert native_error["message_id"] == unknown_event["message_id"]
        assert type(close_event["provider_dispatch_count"]) is int
        assert close_event["provider_dispatch_count"] == 1
        assert close_event["socket_identity"]
        assert unknown_event["cause_code"] == "receive_transport_error"
        assert unknown_event["socket_retired"] is True

        counts, dropped, remaining = cluster.fault_stats()
        assert counts[("account-a", "orders.cancel_group_leg")] == 1
        assert counts[("account-a", "orders.submit_group_leg")] == 1
        assert counts.get(("account-b", "orders.cancel_group_leg")) is None
        assert counts.get(("account-a", "reservations.release")) is None
        assert counts.get(("account-b", "reservations.release")) is None
        assert dropped == 1
        assert remaining == 0

        # The coordinator reads the same durable cancel identity. This recovery
        # uses the read-only query operation and cannot repeat the provider write.
        replay = cluster.cancel_group(
            _GROUP_CANCEL_SOCKET_DISCONNECT, _GROUP_CANCEL_SOCKET_REQUEST
        )
        assert replay["found"] is True
        assert replay["legs"][0]["cancel_id"] == cancel_id
        assert replay["legs"][0]["phase"] == "UNKNOWN"
        assert replay["legs"][0]["result"]["record"]["state"] == "UNKNOWN"
        counts, dropped, remaining = cluster.fault_stats()
        assert counts[("account-a", "orders.cancel_group_leg")] == 1
        assert counts[("account-a", "orders.query_group_cancel")] == 1
        assert counts[("account-a", "orders.submit_group_leg")] == 1
        assert counts.get(("account-a", "reservations.release")) is None
        assert dropped == 1
        assert remaining == 0

        # Independent original-order reconciliation proves the terminal cancel
        # from full order/fill facts. The retired upstream DEALER must be
        # replaced before the source proof can complete.
        completed = cluster.submit_group(original_request)
        assert completed.state is GroupOrderState.CLOSED
        assert completed.budget_reservation is not None
        assert completed.budget_reservation.state is GroupBudgetReservationState.RELEASED
        terminal = completed.legs[0]
        assert terminal.child_status is not None
        assert terminal.submitted_intent is not None
        assert terminal.child_status.state is ExecutionState.CANCELLED
        assert terminal.child_status.filled_quantity == Decimal("0")
        assert terminal.resolution_receipt is not None
        assert terminal.resolution_receipt.final_state is ExecutionState.CANCELLED
        assert terminal.resolution_receipt.dispatch_latch_active is False
        _assert_holds_resolved(
            cluster.account_paths, "account-a", _GROUP_CANCEL_SOCKET_DISCONNECT
        )

        fault_snapshot = cluster.fault_details()
        fault_events = fault_snapshot["events"]
        reconnect_events = [
            event
            for event in fault_events
            if event["phase"] == "same_transport_reconnected"
        ]
        assert len(reconnect_events) == 1, fault_snapshot
        reconnect = reconnect_events[0]
        assert reconnect["account_ref"] == "account-a"
        # The read-only query of the same cancel identity is the first request
        # after the socket fault, so it is where the same client transport
        # recreates its DEALER. Original-order resolution then reuses it.
        assert reconnect["operation"] == "orders.query_group_cancel"
        assert reconnect["old_socket_identity"] == close_event["socket_identity"]
        assert reconnect["new_socket_identity"]
        assert reconnect["new_socket_identity"] != reconnect["old_socket_identity"]

        counts, dropped, remaining = cluster.fault_stats()
        assert counts[("account-a", "orders.cancel_group_leg")] == 1
        assert counts[("account-a", "orders.query_group_cancel")] == 1
        assert counts[("account-a", "orders.submit_group_leg")] == 1
        # Original-order recovery calls GroupProofSource directly in-process;
        # the wrapped coordinator→owner transport counter must not invent a
        # wire `orders.resolve_group_leg` request for that proof work.
        assert counts.get(("account-a", "orders.resolve_group_leg")) is None
        assert counts.get(("account-a", "reservations.release")) == 1
        assert counts.get(("account-b", "reservations.release")) is None
        assert dropped == 1
        assert remaining == 0

        with sqlite3.connect(cluster.account_paths["account-a"]["provider"]) as connection:
            provider_order_json, fills_json = connection.execute(
                "SELECT order_json, fills_json FROM provider_orders"
            ).fetchone()
            provider_cancel_rows = connection.execute(
                "SELECT cancel_id, provider_order_id, target_intent_id "
                "FROM provider_cancel_dispatches"
            ).fetchall()
            provider_attempt_rows = connection.execute(
                "SELECT cancel_id, provider_order_id, target_intent_id "
                "FROM provider_cancel_attempts ORDER BY sequence"
            ).fetchall()
        provider_order = json.loads(provider_order_json)
        assert provider_order["status"] == "cancelled"
        assert json.loads(fills_json) == []
        assert provider_cancel_rows == provider_attempt_rows
        assert provider_cancel_rows == [
            (
                cancel_id,
                terminal.child_status.provider_order_id,
                terminal.submitted_intent.intent_id,
            )
        ]


def test_arb04_duplicate_provider_fill_replay_is_durable_idempotent(
    tmp_path: Path,
) -> None:
    """Repeated real-source reads of one open fill never double-count it."""

    with Arb04GroupCluster(
        tmp_path,
        accounts=_ARB04_CANCEL_ACCOUNTS,
        instruments={account: (_V2_INSTRUMENT,) for account in _ARB04_CANCEL_ACCOUNTS},
        initial_states={"account-a": ("partial:1",), "account-b": ("open",)},
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
    ) as cluster:
        request = _group_request(
            _GROUP_DUPLICATE_FILL,
            cluster.metadata_reference_ns,
            (
                ("leg-a", "account-a", Side.BUY, Decimal("2"), _V2_INSTRUMENT),
                ("leg-b", "account-b", Side.SELL, Decimal("4"), _V2_INSTRUMENT),
            ),
        )
        initial = cluster.submit_group(request)
        assert initial.state is GroupOrderState.FIRST_PENDING
        assert initial.budget_reservation is not None
        assert initial.budget_reservation.state is GroupBudgetReservationState.HELD
        first_leg = initial.legs[0]
        assert first_leg.submitted_intent is not None
        assert first_leg.child_status is not None
        assert first_leg.child_status.state is ExecutionState.PARTIALLY_FILLED
        assert first_leg.child_status.filled_quantity == Decimal("1")
        assert first_leg.child_status.average_price == Decimal("100")
        assert first_leg.child_status.cumulative_commission == Decimal("-0.05")
        assert first_leg.child_status.trade_count == 1
        assert first_leg.resolution_receipt is None

        intent_id = group_leg_intent_id(_GROUP_DUPLICATE_FILL, "leg-a")
        provider_path = Path(cluster.account_paths["account-a"]["provider"])
        writes, initial_counters = _provider_snapshot(provider_path)
        assert len(writes) == 1
        client_order_id = writes[0]
        assert client_order_id
        assert initial_counters["order_queries"] == initial_counters["fill_queries"]

        previous = initial
        previous_counters = initial_counters
        previous_wire_counts, dropped, remaining = cluster.fault_stats()
        assert dropped == 0 and remaining == 0
        for _ in range(2):
            # Re-submitting the exact accepted group request advances the
            # same FIRST_PENDING group by querying its original child intent;
            # it cannot create a new provider order or change its business ID.
            replay = cluster.submit_group(request)
            assert replay.state is GroupOrderState.FIRST_PENDING
            assert replay.legs[0].submitted_intent is not None
            assert replay.legs[0].submitted_intent.intent_id == intent_id
            assert replay.legs[0].child_status == first_leg.child_status
            assert replay.legs[0].resolution_receipt is None
            assert replay.residual_base_units == initial.residual_base_units
            assert replay.budget_reservation is not None
            assert replay.budget_reservation.state is GroupBudgetReservationState.HELD

            current_writes, current_counters = _provider_snapshot(provider_path)
            assert current_writes == writes
            # The provider query is made through the real account Host and
            # DirectBackend/BtApi normalizer for the same sole client order.
            assert current_counters["order_queries"] > previous_counters["order_queries"]
            assert current_counters["fill_queries"] > previous_counters["fill_queries"]
            assert current_counters["order_queries"] == current_counters["fill_queries"]

            current_wire_counts, dropped, remaining = cluster.fault_stats()
            assert current_wire_counts.get(("account-a", "orders.submit_group_leg")) == 1
            assert current_wire_counts.get(("account-a", "orders.query_group_leg"), 0) > (
                previous_wire_counts.get(("account-a", "orders.query_group_leg"), 0)
            )
            assert current_wire_counts.get(("account-a", "orders.resolve_group_leg")) is None
            assert current_wire_counts.get(("account-a", "orders.cancel_group_leg")) is None
            assert current_wire_counts.get(("account-a", "reservations.release")) is None
            assert current_wire_counts.get(("account-b", "orders.submit_group_leg")) is None
            assert dropped == 0 and remaining == 0
            previous = replay
            previous_counters = current_counters
            previous_wire_counts = current_wire_counts

        assert previous.legs[0].child_status == first_leg.child_status
        assert cluster.status("account-a")[4] == ()
        assert cluster.status("account-b")[4] == (
            (_V2_INSTRUMENT, "4", "100", "sell-limit"),
        )

    intent_id = group_leg_intent_id(_GROUP_DUPLICATE_FILL, "leg-a")
    execution_scope = _execution_scope("account-a")
    store = SqliteExecutionStore(cluster.account_paths["account-a"]["execution"])
    try:
        record = store.get(intent_id, scope=execution_scope)
        assert record is not None
        assert record.state is ExecutionState.PARTIALLY_FILLED
        assert record.filled_quantity == Decimal("1")
        assert record.average_price == Decimal("100")
        assert record.cumulative_commission == Decimal("-0.05")
        assert record.dispatch_attempts == 1
        assert record.review_required is False
        execution_events = tuple(
            event
            for event in store.read_outbox(after_sequence=0, limit=1000, scope=execution_scope)
            if event.intent_id == intent_id
        )
        assert tuple(event.event_type for event in execution_events) == (
            "intent_recorded",
            "intent_admitted",
            "dispatch_claimed",
            "provider_observation",
        )
        assert execution_events[-1].state is ExecutionState.PARTIALLY_FILLED
        assert execution_events[-1].payload["filled_quantity"] == "1"
        assert execution_events[-1].payload["cumulative_commission"] == "-0.05"
    finally:
        store.close()

    proof_uri = Path(cluster.account_paths["account-a"]["proof"]).resolve().as_uri()
    with sqlite3.connect(f"{proof_uri}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        proof_rows = connection.execute(
            "SELECT event_type, payload_json FROM group_proof_events "
            "WHERE intent_id=? ORDER BY revision",
            (intent_id,),
        ).fetchall()

    provider_uri = provider_path.resolve().as_uri()
    with sqlite3.connect(f"{provider_uri}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        provider_order_json, fills_json = connection.execute(
            "SELECT order_json, fills_json FROM provider_orders WHERE client_order_id=?",
            (client_order_id,),
        ).fetchone()
    source_events = [
        json.loads(payload_json)
        for event_type, payload_json in proof_rows
        if event_type == "SOURCE_OBSERVED"
    ]
    assert len(source_events) == 1
    source = source_events[0]
    assert Decimal(str(source["filled_quantity"])) == Decimal("1")
    assert Decimal(str(source["fee_total"])) == Decimal("-0.05")
    assert source["trade_count"] == 1
    source_fills = source["fill_records"]
    assert len(source_fills) == 1
    assert source_fills[0]["record"]["trade_id"] == f"fill-{client_order_id}-1"
    assert Decimal(str(source_fills[0]["record"]["size"])) == Decimal("1")
    assert Decimal(str(source_fills[0]["record"]["fee"])) == Decimal("-0.05")
    assert not any(
        event_type in {"SOURCE_PROGRESS", "SOURCE_RECONCILED", "PROOF_READY", "RISK_RESOLVED"}
        for event_type, _payload_json in proof_rows
    )

    provider_order = json.loads(provider_order_json)
    provider_fills = json.loads(fills_json)
    assert provider_order["client_order_id"] == client_order_id
    assert len(provider_fills) == 1
    assert provider_fills[0]["trade_id"] == f"fill-{client_order_id}-1"
    assert provider_fills[0]["quantity"] == "1"
    assert provider_fills[0]["fee_amount"] == "-0.05"
    final_writes, final_counters = _provider_snapshot(provider_path)
    assert final_writes == (client_order_id,)
    assert final_counters["fill_queries"] >= initial_counters["fill_queries"] + 2
    assert final_counters["order_queries"] == final_counters["fill_queries"]

    execution_store = SqliteExecutionStore(cluster.account_paths["account-a"]["execution"])
    authority = ExecutionGroupAuthority(
        cluster.account_paths["account-a"]["group_authority"],
        completion_verifier=ExecutionStoreGroupCompletionVerifier(
            execution_store,
            execution_scopes=(execution_scope,),
        ),
    )
    try:
        pending_holds = authority.pending_holds()
        assert len(pending_holds) == 1
        assert pending_holds[0].intent_id == intent_id
    finally:
        authority.close()
        execution_store.close()

    assert _risk_legs(cluster.account_paths, "account-a", _GROUP_DUPLICATE_FILL) == (
        ("leg-a", True, False),
    )
    _assert_holds_resolved(cluster.account_paths, "account-b", _GROUP_DUPLICATE_FILL)


def test_arb04_unknown_late_fill_explicit_query_resolves_original_hold_once(
    tmp_path: Path,
) -> None:
    """An original-ID query adopts later fills only with a complete terminal proof."""

    group_id = _GROUP_LATE_FILL_QUERY
    intent_a = group_leg_intent_id(group_id, "leg-a")
    execution_scope_a = _execution_scope("account-a")
    provider_path_a: Path
    client_order_id: str
    initial_counters: dict[str, int]
    partial_counters: dict[str, int]
    final_snapshot: object

    with Arb04GroupCluster(
        tmp_path,
        accounts=_ARB04_CANCEL_ACCOUNTS,
        instruments={account: (_V2_INSTRUMENT,) for account in _ARB04_CANCEL_ACCOUNTS},
        initial_states={"account-a": ("open",), "account-b": ("filled",)},
        expected_orders={
            "account-a": ((_V2_INSTRUMENT, "2", "100", "buy-limit"),),
            "account-b": ((_V2_INSTRUMENT, "4", "100", "sell-limit"),),
        },
        rule_specs={
            "account-a": {_V2_INSTRUMENT: "2"},
            "account-b": {_V2_INSTRUMENT: "1"},
        },
        fault_operation="orders.submit_group_leg",
        fault_drops=1,
        fault_mode="socket_disconnect_after_provider_dispatch",
    ) as cluster:
        request = _group_request(
            group_id,
            cluster.metadata_reference_ns,
            (
                ("leg-a", "account-a", Side.BUY, Decimal("2"), _V2_INSTRUMENT),
                ("leg-b", "account-b", Side.SELL, Decimal("4"), _V2_INSTRUMENT),
            ),
        )

        unknown = cluster.submit_group(request)
        assert unknown.state is GroupOrderState.RECOVERY_REQUIRED
        assert unknown.budget_reservation is not None
        assert unknown.budget_reservation.state is GroupBudgetReservationState.HELD
        leg_a = next(leg for leg in unknown.legs if leg.leg_id == "leg-a")
        leg_b = next(leg for leg in unknown.legs if leg.leg_id == "leg-b")
        assert leg_a.phase is GroupLegPhase.UNKNOWN
        assert leg_a.submitted_intent is not None
        assert leg_a.child_status is None
        assert leg_a.resolution_receipt is None
        assert leg_b.phase is GroupLegPhase.READY
        assert leg_b.submitted_intent is None

        provider_path_a = Path(cluster.account_paths["account-a"]["provider"])
        writes_a, initial_counters = _provider_snapshot(provider_path_a)
        assert len(writes_a) == 1
        client_order_id = writes_a[0]
        assert client_order_id
        assert initial_counters == {"order_queries": 1, "fill_queries": 1}
        assert _risk_legs(cluster.account_paths, "account-a", group_id) == (
            ("leg-a", True, False),
        )
        assert tuple(hold.intent_id for hold in cluster.status("account-a")[3]) == (
            intent_a,
        )

        fault_snapshot = cluster.fault_details()
        assert fault_snapshot["fault_mode"] == "socket_disconnect_after_provider_dispatch"
        assert fault_snapshot["operation"] == "orders.submit_group_leg"
        fault_events = fault_snapshot["events"]
        assert [event["phase"] for event in fault_events] == [
            "socket_closed_before_reply_receive",
            "native_poll_error_after_close",
            "client_uncertain_result",
        ], fault_snapshot
        close_event, native_error, unknown_event = fault_events
        assert close_event["account_ref"] == "account-a"
        assert close_event["operation"] == "orders.submit_group_leg"
        assert close_event["message_id"] == native_error["message_id"]
        assert native_error["message_id"] == unknown_event["message_id"]
        assert close_event["provider_dispatch_count"] == 1
        assert close_event["socket_identity"]
        assert unknown_event["cause_code"] == "receive_transport_error"
        assert unknown_event["socket_retired"] is True

        counts, dropped, remaining = cluster.fault_stats()
        assert counts.get(("account-a", "orders.submit_group_leg")) == 1
        assert counts.get(("account-b", "orders.submit_group_leg")) is None
        assert dropped == 1 and remaining == 0
        initial_wire_query_count = counts.get(("account-a", "orders.query_group_leg"), 0)

        def persist_source(
            *, status: str, revision: int, filled: str, fills: list[dict[str, object]]
        ) -> None:
            with sqlite3.connect(provider_path_a, timeout=1.0) as connection:
                row = connection.execute(
                    "SELECT order_json FROM provider_orders WHERE client_order_id=?",
                    (client_order_id,),
                ).fetchone()
                assert row is not None
                order = json.loads(row[0])
                assert order["client_order_id"] == client_order_id
                assert order["order_revision"] < revision
                order["status"] = status
                order["filled"] = filled
                order["order_revision"] = revision
                order["avg_price"] = "100"
                connection.execute(
                    "UPDATE provider_orders SET order_json=?, fills_json=? "
                    "WHERE client_order_id=?",
                    (
                        json.dumps(order, sort_keys=True, separators=(",", ":")),
                        json.dumps(fills, sort_keys=True, separators=(",", ":")),
                        client_order_id,
                    ),
                )

        order_id = f"provider-{client_order_id}"
        first_fill = _V2SyntheticBtApiFeed._fill(
            account_id="provider-account-a",
            client_order_id=client_order_id,
            order_id=order_id,
            symbol=_V2_INSTRUMENT,
            side="buy",
            trade_id=f"fill-{client_order_id}-1",
            quantity="1",
            price="100",
            revision=2,
            fee="-0.05",
        )
        persist_source(status="partial", revision=2, filled="1", fills=[first_fill])

        # The exact accepted request re-enters the real coordinator, whose
        # query route reconnects the retired DEALER and reads the same order ID.
        partial = cluster.submit_group(request)
        assert partial.state is GroupOrderState.FIRST_PENDING
        assert partial.error_code is None
        assert partial.budget_reservation is not None
        assert partial.budget_reservation.state is GroupBudgetReservationState.HELD
        partial_a = next(leg for leg in partial.legs if leg.leg_id == "leg-a")
        partial_b = next(leg for leg in partial.legs if leg.leg_id == "leg-b")
        assert partial_a.phase is GroupLegPhase.OBSERVED
        assert partial_a.submitted_intent is not None
        assert partial_a.submitted_intent.intent_id == intent_a
        assert partial_a.child_status is not None
        assert partial_a.child_status.state is ExecutionState.PARTIALLY_FILLED
        assert partial_a.child_status.filled_quantity == Decimal("1")
        assert partial_a.child_status.average_price == Decimal("100")
        assert partial_a.child_status.cumulative_commission == Decimal("-0.05")
        assert partial_a.child_status.trade_count == 1
        assert partial_a.resolution_receipt is None
        assert partial_b.phase is GroupLegPhase.READY
        assert partial_b.submitted_intent is None

        writes_a, partial_counters = _provider_snapshot(provider_path_a)
        assert writes_a == (client_order_id,)
        # The coordinator owner loop can safely re-query a recoverable group
        # between strategy replays. Keep read counters monotonic; the durable
        # SOURCE_PROGRESS below binds this observation to the exact client ID
        # and revision rather than assuming a single caller performed it.
        assert partial_counters["order_queries"] >= initial_counters["order_queries"] + 1
        assert partial_counters["fill_queries"] >= initial_counters["fill_queries"] + 1
        writes_b, counters_b = _provider_snapshot(
            Path(cluster.account_paths["account-b"]["provider"])
        )
        assert writes_b == ()
        assert counters_b == {"order_queries": 0, "fill_queries": 0}
        assert _risk_legs(cluster.account_paths, "account-a", group_id) == (
            ("leg-a", True, False),
        )
        assert tuple(hold.intent_id for hold in cluster.status("account-a")[3]) == (
            intent_a,
        )

        counts, dropped, remaining = cluster.fault_stats()
        assert counts.get(("account-a", "orders.submit_group_leg")) == 1
        # Wire query totals include legitimate owner-maintenance reads; the
        # persisted proof event below identifies the exact original order.
        assert counts.get(("account-a", "orders.query_group_leg"), 0) >= (
            initial_wire_query_count + 1
        )
        assert counts.get(("account-b", "orders.submit_group_leg")) is None
        assert counts.get(("account-a", "orders.cancel_group_leg")) is None
        assert counts.get(("account-a", "reservations.release")) is None
        assert dropped == 1 and remaining == 0

        fault_snapshot = cluster.fault_details()
        reconnects = [
            event
            for event in fault_snapshot["events"]
            if event["phase"] == "same_transport_reconnected"
        ]
        assert len(reconnects) == 1, fault_snapshot
        reconnect = reconnects[0]
        assert reconnect["account_ref"] == "account-a"
        assert reconnect["operation"] == "orders.query_group_leg"
        assert reconnect["old_socket_identity"] == close_event["socket_identity"]
        assert reconnect["new_socket_identity"]
        assert reconnect["new_socket_identity"] != reconnect["old_socket_identity"]

        proof_uri = Path(cluster.account_paths["account-a"]["proof"]).resolve().as_uri()
        with sqlite3.connect(f"{proof_uri}?mode=ro", uri=True) as connection:
            connection.execute("PRAGMA query_only=ON")
            partial_proof_rows = connection.execute(
                "SELECT event_type, payload_json FROM group_proof_events "
                "WHERE intent_id=? ORDER BY revision",
                (intent_a,),
            ).fetchall()
        partial_proof = [
            (event_type, json.loads(payload_json))
            for event_type, payload_json in partial_proof_rows
        ]
        partial_progress = [
            payload for event_type, payload in partial_proof if event_type == "SOURCE_PROGRESS"
        ]
        assert len(partial_progress) == 1
        assert partial_progress[0]["client_order_id"] == client_order_id
        assert partial_progress[0]["state"] == "PARTIALLY_FILLED"
        assert partial_progress[0]["order_revision"] == 2
        assert partial_progress[0]["fill_watermark_revision"] == 2
        assert Decimal(str(partial_progress[0]["filled_quantity"])) == Decimal("1")
        assert Decimal(str(partial_progress[0]["fee_total"])) == Decimal("-0.05")
        assert partial_progress[0]["trade_count"] == 1
        assert partial_progress[0]["fill_records"][0]["record"]["trade_id"] == (
            f"fill-{client_order_id}-1"
        )
        assert not any(
            event_type in {"SOURCE_RECONCILED", "PROOF_READY", "RISK_RESOLVED"}
            for event_type, _payload in partial_proof
        )

        second_fill = _V2SyntheticBtApiFeed._fill(
            account_id="provider-account-a",
            client_order_id=client_order_id,
            order_id=order_id,
            symbol=_V2_INSTRUMENT,
            side="buy",
            trade_id=f"fill-{client_order_id}-2",
            quantity="1",
            price="100",
            revision=3,
            fee="-0.05",
        )
        persist_source(
            status="filled",
            revision=3,
            filled="2",
            fills=[first_fill, second_fill],
        )

        final = cluster.submit_group(request)
        final_snapshot = final
        assert final.state is GroupOrderState.PAIRED
        assert final.error_code is None
        assert final.residual_base_units == Decimal("0")
        final_a = next(leg for leg in final.legs if leg.leg_id == "leg-a")
        final_b = next(leg for leg in final.legs if leg.leg_id == "leg-b")
        assert final_a.submitted_intent is not None
        assert final_a.submitted_intent.intent_id == intent_a
        assert final_a.child_status is not None
        assert final_a.child_status.state is ExecutionState.FILLED
        assert final_a.child_status.filled_quantity == Decimal("2")
        assert final_a.child_status.average_price == Decimal("100")
        assert final_a.child_status.cumulative_commission == Decimal("-0.10")
        assert final_a.child_status.cumulative_commission_currency == "USD"
        assert final_a.child_status.trade_count == 2
        assert final_a.child_status.order_revision == 3
        assert final_a.resolution_receipt is not None
        assert final_a.resolution_receipt.resolution_kind == "TERMINAL_EXPOSURE_RESOLVED"
        assert final_a.resolution_receipt.final_state is ExecutionState.FILLED
        assert final_a.resolution_receipt.filled_quantity == Decimal("2")
        assert final_a.resolution_receipt.trade_count == 2
        assert final_a.resolution_receipt.dispatch_latch_active is False
        assert final_b.submitted_intent is not None
        assert final_b.submitted_intent.quantity == Decimal("4.000")
        assert final_b.child_status is not None
        assert final_b.child_status.state is ExecutionState.FILLED
        assert final_b.child_status.filled_quantity == Decimal("4")
        assert final_b.resolution_receipt is not None
        assert final_b.resolution_receipt.resolution_kind == "TERMINAL_EXPOSURE_RESOLVED"

        writes_a, final_counters_a = _provider_snapshot(provider_path_a)
        assert writes_a == (client_order_id,)
        assert final_counters_a["order_queries"] >= partial_counters["order_queries"] + 1
        assert final_counters_a["fill_queries"] >= partial_counters["fill_queries"] + 1
        writes_b, final_counters_b = _provider_snapshot(
            Path(cluster.account_paths["account-b"]["provider"])
        )
        assert len(writes_b) == 1 and len(set(writes_b)) == 1
        assert final_counters_b == {"order_queries": 1, "fill_queries": 1}

        counts, dropped, remaining = cluster.fault_stats()
        assert counts.get(("account-a", "orders.submit_group_leg")) == 1
        assert counts.get(("account-a", "orders.query_group_leg"), 0) >= (
            initial_wire_query_count + 2
        )
        assert counts.get(("account-b", "orders.submit_group_leg")) == 1
        assert counts.get(("account-a", "orders.cancel_group_leg")) is None
        assert counts.get(("account-a", "orders.query_group_cancel")) is None
        assert counts.get(("account-a", "reservations.release")) is None
        assert dropped == 1 and remaining == 0
        assert _risk_legs(cluster.account_paths, "account-a", group_id) == (
            ("leg-a", True, True),
        )
        assert _risk_legs(cluster.account_paths, "account-b", group_id) == (
            ("leg-b", True, True),
        )
        assert cluster.status("account-a")[3] == ()
        assert cluster.status("account-b")[3] == ()
        for account_ref in _ARB04_CANCEL_ACCOUNTS:
            assert cluster.status(account_ref)[4] == ()

    assert final_snapshot == final
    store_a = SqliteExecutionStore(cluster.account_paths["account-a"]["execution"])
    try:
        record_a = store_a.get(intent_a, scope=execution_scope_a)
        assert record_a is not None
        assert record_a.state is ExecutionState.FILLED
        assert record_a.filled_quantity == Decimal("2")
        assert record_a.average_price == Decimal("100")
        assert record_a.cumulative_commission == Decimal("-0.10")
        assert record_a.dispatch_attempts == 1
        assert record_a.review_required is False
        execution_events_a = tuple(
            event
            for event in store_a.read_outbox(
                after_sequence=0, limit=1000, scope=execution_scope_a
            )
            if event.intent_id == intent_a
        )
        assert tuple(event.event_type for event in execution_events_a) == (
            "intent_recorded",
            "intent_admitted",
            "dispatch_claimed",
            "provider_observation",
            "provider_observation",
            "provider_observation",
        )
        assert tuple(event.state for event in execution_events_a[3:]) == (
            ExecutionState.ACKED,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
        )
    finally:
        store_a.close()

    proof_uri = Path(cluster.account_paths["account-a"]["proof"]).resolve().as_uri()
    with sqlite3.connect(f"{proof_uri}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        proof_rows = connection.execute(
            "SELECT event_type, payload_json FROM group_proof_events "
            "WHERE intent_id=? ORDER BY revision",
            (intent_a,),
        ).fetchall()
    proof = [
        (event_type, json.loads(payload_json))
        for event_type, payload_json in proof_rows
    ]
    event_types = [event_type for event_type, _payload in proof]
    assert event_types.count("ATTEMPT_UNKNOWN") == 1
    assert event_types.count("SOURCE_PROGRESS") == 1
    assert event_types.count("SOURCE_RECONCILED") == 1
    assert event_types.count("PROOF_READY") == 1
    assert event_types.count("RISK_RESOLVED") == 1
    partial_event = next(
        payload for event_type, payload in proof if event_type == "SOURCE_PROGRESS"
    )
    terminal_event = next(
        payload for event_type, payload in proof if event_type == "SOURCE_RECONCILED"
    )
    assert partial_event["client_order_id"] == terminal_event["client_order_id"] == (
        client_order_id
    )
    assert terminal_event["state"] == "FILLED"
    assert terminal_event["order_revision"] == 3
    assert terminal_event["fill_watermark_revision"] == 3
    assert Decimal(str(terminal_event["filled_quantity"])) == Decimal("2")
    assert Decimal(str(terminal_event["fee_total"])) == Decimal("-0.10")
    assert terminal_event["trade_count"] == 2
    terminal_fills = terminal_event["fill_records"]
    assert tuple(item["record"]["trade_id"] for item in terminal_fills) == (
        f"fill-{client_order_id}-1",
        f"fill-{client_order_id}-2",
    )
    assert tuple(
        Decimal(str(item["record"]["size"])) for item in terminal_fills
    ) == (Decimal("1"), Decimal("1"))
    assert tuple(
        Decimal(str(item["record"]["fee"])) for item in terminal_fills
    ) == (Decimal("-0.05"), Decimal("-0.05"))
    assert event_types.index("ATTEMPT_UNKNOWN") < event_types.index("SOURCE_PROGRESS")
    assert event_types.index("SOURCE_PROGRESS") < event_types.index("SOURCE_RECONCILED")
    assert event_types.index("SOURCE_RECONCILED") < event_types.index("PROOF_READY")
    assert event_types.index("PROOF_READY") < event_types.index("RISK_RESOLVED")

    provider_uri = provider_path_a.resolve().as_uri()
    with sqlite3.connect(f"{provider_uri}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        order_json, fills_json = connection.execute(
            "SELECT order_json, fills_json FROM provider_orders WHERE client_order_id=?",
            (client_order_id,),
        ).fetchone()
        assert connection.execute("SELECT COUNT(*) FROM provider_writes").fetchone()[0] == 1
        # This non-cancel scenario has no provider_cancel_dispatches table;
        # the coordinator wire ledger above already asserts that no cancel RPC ran.
    provider_order = json.loads(order_json)
    provider_fills = json.loads(fills_json)
    assert provider_order["client_order_id"] == client_order_id
    assert provider_order["status"] == "filled"
    assert provider_order["order_revision"] == 3
    assert Decimal(str(provider_order["filled"])) == Decimal("2")
    assert Decimal(str(provider_order["quantity"])) == Decimal("2")
    assert tuple(item["trade_id"] for item in provider_fills) == (
        f"fill-{client_order_id}-1",
        f"fill-{client_order_id}-2",
    )
    assert tuple(item["quantity"] for item in provider_fills) == ("1", "1")
    assert tuple(item["fee_amount"] for item in provider_fills) == ("-0.05", "-0.05")

    persisted_groups = SqliteOrderGroupStore(cluster.coordinator_paths["groups"])
    try:
        persisted = persisted_groups.get(_PORTFOLIO, group_id)
        assert persisted == final_snapshot
        group_events = persisted_groups.events(_PORTFOLIO, group_id)
    finally:
        persisted_groups.close()
    assert sum(event.event_type == "FIRST_LEG_SUBMIT_STARTED" for event in group_events) == 1
    assert sum(event.event_type == "HEDGE_LEG_SUBMIT_STARTED" for event in group_events) == 1
    assert sum(event.event_type == "GROUP_PAIRED" for event in group_events) == 1
    assert not any(event.event_type == "GROUP_CLOSED" for event in group_events)
    for account_ref in _ARB04_CANCEL_ACCOUNTS:
        _assert_holds_resolved(cluster.account_paths, account_ref, group_id)
