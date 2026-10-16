"""Fixed-source integration test for ARB04 group reply-loss/UNKNOWN semantics.

Plan anchor: iteration 10 plan line 770 (socket reply loss with a durable
journal; recovery by the original business ID; no resubmission, no early
release while UNKNOWN; no compensating/blind reverse order before the facts
are confirmed).

Both tests run the full spawned CURVE service chain (strategy client ->
coordinator host -> account hosts -> real SQLite stores and the synthetic
SQLite provider leaves). The coordinator host's real ZMQ transport factory is
wrapped so the reply of the first ``orders.submit_group_leg`` request is
discarded *after* the account host executed the request and answered; the
coordinator then observes the transport's genuine
``RequestOutcomeUnknownError`` exactly as after a lost socket reply.

Test A: the account-side fact was already durable and terminal before the
reply was lost. The recovery must read it back through the ORIGINAL business
ID (``orders.query_group_leg`` for the original child intent), pair the group
exactly once and never resubmit, never release the budget hold early and never
send a compensating order.

Test B: the account-side fact is genuinely non-terminal at reply-loss time and
a fill arrives at the provider only later (a late fill injected into the
provider ledger after the UNKNOWN). The system must freeze honestly: still
UNKNOWN after a replay, no re-observation, no resubmission, no early release,
no blind reverse. The missing re-observation path is reported as the residual
gap, not papered over.
"""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from bt_api_execution import (
    ExecutionState,
    GroupBudgetReservationState,
    GroupLegPhase,
    GroupOrderState,
    Side,
    SqliteExecutionStore,
    SqliteOrderGroupStore,
    group_leg_intent_id,
)
from bt_api_py.service_execution_group_authority import (
    ExecutionGroupAuthority,
    ExecutionStoreGroupCompletionVerifier,
)

from tests.integration._arb04_group_reply_loss_support import Arb04GroupCluster
from tests.integration.test_arb03_dim05_group_partial_fill_service import (
    _PORTFOLIO,
    _assert_holds_resolved,
    _assert_provider_writes,
    _execution_scope,
    _group_request,
    _leg_outbox,
    _provider_snapshot,
    _risk_legs,
)
from tests.unit._service_group_provider_fixtures import _V2_INSTRUMENT

_ARB04_ACCOUNTS = ("account-a", "account-b")
_GROUP_REPLY_LOSS_TERMINAL = "group-arb04-reply-loss-terminal-01"
_GROUP_REPLY_LOSS_LATE_FILL = "group-arb04-reply-loss-late-fill-02"


def _pending_hold_intents(account_paths: dict[str, dict[str, str]], account_ref: str):
    execution_scope = _execution_scope(account_ref)
    authority_store = SqliteExecutionStore(account_paths[account_ref]["execution"])
    authority = ExecutionGroupAuthority(
        account_paths[account_ref]["group_authority"],
        completion_verifier=ExecutionStoreGroupCompletionVerifier(
            authority_store,
            execution_scopes=(execution_scope,),
        ),
    )
    try:
        return tuple(hold.intent_id for hold in authority.pending_holds())
    finally:
        authority.close()
        authority_store.close()


def _inject_late_fill(ledger_path: Path) -> str:
    """Make the acknowledged zero-fill provider order fill, after the fact."""

    with sqlite3.connect(ledger_path) as connection:
        row = connection.execute(
            "SELECT client_order_id, order_json, fills_json FROM provider_orders"
        ).fetchone()
        assert row is not None, "provider order row is missing"
        client_order_id, order_json, fills_json = row
        order = json.loads(order_json)
        assert order["status"] == "open", order
        assert str(order["filled"]) == "0", order
        assert json.loads(fills_json) == [], fills_json
        order["status"] = "filled"
        order["filled"] = order["quantity"]
        order["order_revision"] = 2
        order["avg_price"] = order["price"]
        late_fill = {
            "account_id": order["account_id"],
            "client_order_id": client_order_id,
            "fee_amount": "-0.10",
            "fee_currency": "USD",
            "fill_revision": 2,
            "order_id": order["order_id"],
            "quantity": order["quantity"],
            "side": order["side"],
            "symbol": order["symbol"],
            "trade_id": "fill-" + client_order_id + "-2",
            "trade_price": order["price"],
        }
        connection.execute(
            "UPDATE provider_orders SET order_json=?, fills_json=? WHERE client_order_id=?",
            (
                json.dumps(order, sort_keys=True, separators=(",", ":")),
                json.dumps([late_fill], sort_keys=True, separators=(",", ":")),
                client_order_id,
            ),
        )
    return client_order_id


def test_arb04_reply_loss_first_leg_recovered_by_original_id_without_resubmit_or_release(
    tmp_path: Path,
) -> None:
    """A lost submit reply must recover via the original business ID, once."""

    with Arb04GroupCluster(
        tmp_path,
        accounts=_ARB04_ACCOUNTS,
        instruments={account: (_V2_INSTRUMENT,) for account in _ARB04_ACCOUNTS},
        initial_states={"account-a": ("cancelled:1.5",), "account-b": ("filled",)},
        expected_orders={
            "account-a": ((_V2_INSTRUMENT, "2", "100", "buy-limit"),),
            "account-b": ((_V2_INSTRUMENT, "3", "100", "sell-limit"),),
        },
        rule_specs={
            "account-a": {_V2_INSTRUMENT: "2"},
            "account-b": {_V2_INSTRUMENT: "1"},
        },
        fault_operation="orders.submit_group_leg",
        fault_drops=1,
    ) as cluster:
        request = _group_request(
            _GROUP_REPLY_LOSS_TERMINAL,
            cluster.metadata_reference_ns,
            (
                ("leg-a", "account-a", Side.BUY, Decimal("2"), _V2_INSTRUMENT),
                ("leg-b", "account-b", Side.SELL, Decimal("4"), _V2_INSTRUMENT),
            ),
        )

        # Call 1: the account host receives and fully executes the first-leg
        # submit, but its reply is lost on the wire.
        unknown = cluster.submit_group(request)
        assert unknown.state is GroupOrderState.RECOVERY_REQUIRED
        assert unknown.error_code is None
        assert unknown.budget_reservation is not None
        assert unknown.budget_reservation.state is GroupBudgetReservationState.HELD

        leg_a = next(leg for leg in unknown.legs if leg.leg_id == "leg-a")
        leg_b = next(leg for leg in unknown.legs if leg.leg_id == "leg-b")
        assert leg_a.phase is GroupLegPhase.UNKNOWN
        assert leg_a.submitted_intent is not None
        assert leg_a.submitted_intent.quantity == Decimal("2")
        assert leg_a.child_status is None
        assert leg_a.claim_receipt is None
        assert leg_a.resolution_receipt is None
        assert leg_b.phase is GroupLegPhase.READY
        assert leg_b.submitted_intent is None
        assert leg_b.child_status is None

        counts, dropped, remaining = cluster.fault_stats()
        assert counts[("account-a", "orders.submit_group_leg")] == 1
        assert counts.get(("account-b", "orders.submit_group_leg")) is None
        assert dropped == 1
        assert remaining == 0

        # The account-side fact is already durable, terminal and fully
        # observed; the coordinator has none of it because the reply was lost.
        status_a = cluster.status("account-a")
        assert len(status_a[1]) == 1
        assert status_a[2] == {"order_queries": 1, "fill_queries": 1}
        assert status_a[4] == ()
        # No provider write and no fill observation happened on account-b.
        status_b = cluster.status("account-b")
        assert status_b[1] == ()
        assert status_b[2] == {"order_queries": 0, "fill_queries": 0}
        assert status_b[3] == ()
        assert status_b[4] == cluster.expected_orders["account-b"]

        # Call 2: exact replay; recovery reads the durable fact back through
        # the original child intent id and pairs the group.
        paired = cluster.submit_group(request)
        assert paired.state is GroupOrderState.PAIRED
        assert paired.error_code is None
        assert paired.residual_base_units == Decimal("0")
        leg_a = next(leg for leg in paired.legs if leg.leg_id == "leg-a")
        leg_b = next(leg for leg in paired.legs if leg.leg_id == "leg-b")
        assert leg_a.child_status is not None
        assert leg_a.child_status.state is ExecutionState.CANCELLED
        assert leg_a.child_status.filled_quantity == Decimal("1.5")
        assert leg_a.claim_receipt is not None
        assert leg_a.resolution_receipt is not None
        assert leg_a.resolution_receipt.resolution_kind == "TERMINAL_EXPOSURE_RESOLVED"
        assert leg_b.submitted_intent is not None
        assert leg_b.submitted_intent.quantity == Decimal("3")
        assert leg_b.child_status is not None
        assert leg_b.child_status.state is ExecutionState.FILLED
        assert leg_b.child_status.filled_quantity == Decimal("3")
        assert leg_b.resolution_receipt is not None
        assert leg_b.resolution_receipt.resolution_kind == "TERMINAL_EXPOSURE_RESOLVED"

        # Call 3: a further duplicate replay changes nothing, on the wire too.
        replayed = cluster.submit_group(request)
        assert replayed == paired
        counts, dropped, remaining = cluster.fault_stats()
        assert counts[("account-a", "orders.submit_group_leg")] == 1
        assert counts[("account-b", "orders.submit_group_leg")] == 1
        assert counts[("account-a", "orders.query_group_leg")] == 2
        assert counts[("account-b", "orders.query_group_leg")] == 1
        assert dropped == 1
        assert remaining == 0
        assert counts.get(("account-a", "reservations.release")) is None
        assert counts.get(("account-b", "reservations.release")) is None

        status_a = cluster.status("account-a")
        assert len(status_a[1]) == 1
        assert status_a[2] == {"order_queries": 1, "fill_queries": 1}
        assert status_a[4] == ()
        status_b = cluster.status("account-b")
        assert len(status_b[1]) == 1
        assert status_b[2] == {"order_queries": 1, "fill_queries": 1}
        assert status_b[4] == ()

    intent_a = group_leg_intent_id(_GROUP_REPLY_LOSS_TERMINAL, "leg-a")
    intent_b = group_leg_intent_id(_GROUP_REPLY_LOSS_TERMINAL, "leg-b")

    persisted_groups = SqliteOrderGroupStore(cluster.coordinator_paths["groups"])
    try:
        persisted = persisted_groups.get(_PORTFOLIO, _GROUP_REPLY_LOSS_TERMINAL)
        assert persisted == replayed
        events = persisted_groups.events(_PORTFOLIO, _GROUP_REPLY_LOSS_TERMINAL)
    finally:
        persisted_groups.close()

    unknown_observations = tuple(
        event
        for event in events
        if event.event_type == "FIRST_LEG_STATUS_OBSERVED"
        and event.payload.get("state") == "UNKNOWN"
    )
    assert len(unknown_observations) == 1
    unknown_payload = unknown_observations[0].payload
    assert unknown_payload["leg_id"] == "leg-a"
    assert unknown_payload["state"] == "UNKNOWN"
    assert unknown_payload["code"] == "ORDER_SUBMIT_OUTCOME_UNKNOWN"
    assert unknown_payload.get("budget_state") == GroupBudgetReservationState.HELD.value
    assert (
        len(tuple(event for event in events if event.event_type == "FIRST_LEG_SUBMIT_STARTED"))
        == 1
    )
    assert (
        len(tuple(event for event in events if event.event_type == "HEDGE_LEG_SUBMIT_STARTED"))
        == 1
    )
    sized = tuple(event for event in events if event.event_type == "HEDGE_LEG_SIZED")
    assert len(sized) == 1
    assert sized[0].payload["quantity"] == "3.000"
    assert len(tuple(event for event in events if event.event_type == "GROUP_PAIRED")) == 1
    assert not tuple(event for event in events if event.event_type == "GROUP_RECOVERY_REQUIRED")

    execution_scope_a = _execution_scope("account-a")
    execution_scope_b = _execution_scope("account-b")
    store_a = SqliteExecutionStore(cluster.account_paths["account-a"]["execution"])
    try:
        record_a = store_a.get(intent_a, scope=execution_scope_a)
        assert record_a is not None
        assert record_a.state is ExecutionState.CANCELLED
        assert record_a.filled_quantity == Decimal("1.5")
        assert record_a.average_price == Decimal("100")
        assert record_a.cumulative_commission == Decimal("-0.05")
        assert record_a.dispatch_attempts == 1
        assert record_a.unknown_reason is None
        assert record_a.review_required is False
        events_a = _leg_outbox(store_a, execution_scope_a, intent_a)
        assert tuple(event.event_type for event in events_a) == (
            "intent_recorded",
            "intent_admitted",
            "dispatch_claimed",
            "provider_observation",
            "provider_observation",
        )
        assert events_a[4].state is ExecutionState.CANCELLED
        assert events_a[4].payload["filled_quantity"] == "1.5"
    finally:
        store_a.close()

    store_b = SqliteExecutionStore(cluster.account_paths["account-b"]["execution"])
    try:
        record_b = store_b.get(intent_b, scope=execution_scope_b)
        assert record_b is not None
        assert record_b.state is ExecutionState.FILLED
        assert record_b.filled_quantity == Decimal("3")
        assert record_b.dispatch_attempts == 1
        assert record_b.unknown_reason is None
        events_b = _leg_outbox(store_b, execution_scope_b, intent_b)
        assert tuple(event.event_type for event in events_b) == (
            "intent_recorded",
            "intent_admitted",
            "dispatch_claimed",
            "provider_observation",
        )
        assert events_b[3].state is ExecutionState.FILLED
    finally:
        store_b.close()

    assert _risk_legs(cluster.account_paths, "account-a", _GROUP_REPLY_LOSS_TERMINAL) == (
        ("leg-a", True, True),
    )
    assert _risk_legs(cluster.account_paths, "account-b", _GROUP_REPLY_LOSS_TERMINAL) == (
        ("leg-b", True, True),
    )
    for account_ref in _ARB04_ACCOUNTS:
        _assert_holds_resolved(cluster.account_paths, account_ref, _GROUP_REPLY_LOSS_TERMINAL)
        _assert_provider_writes(Path(cluster.account_paths[account_ref]["provider"]), 1)
    assert _pending_hold_intents(cluster.account_paths, "account-a") == ()
    assert _pending_hold_intents(cluster.account_paths, "account-b") == ()


def test_arb04_late_fill_after_reply_loss_stays_unknown_without_blind_reverse(
    tmp_path: Path,
) -> None:
    """A genuinely late fill must not be adopted, resent, released or reversed."""

    with Arb04GroupCluster(
        tmp_path,
        accounts=_ARB04_ACCOUNTS,
        instruments={account: (_V2_INSTRUMENT,) for account in _ARB04_ACCOUNTS},
        initial_states={"account-a": ("open",), "account-b": ("filled",)},
        expected_orders={
            "account-a": ((_V2_INSTRUMENT, "2", "100", "buy-limit"),),
            "account-b": ((_V2_INSTRUMENT, "3", "100", "sell-limit"),),
        },
        rule_specs={
            "account-a": {_V2_INSTRUMENT: "2"},
            "account-b": {_V2_INSTRUMENT: "1"},
        },
        fault_operation="orders.submit_group_leg",
        fault_drops=1,
    ) as cluster:
        request = _group_request(
            _GROUP_REPLY_LOSS_LATE_FILL,
            cluster.metadata_reference_ns,
            (
                ("leg-a", "account-a", Side.BUY, Decimal("2"), _V2_INSTRUMENT),
                ("leg-b", "account-b", Side.SELL, Decimal("4"), _V2_INSTRUMENT),
            ),
        )

        # Call 1: the first-leg order is acknowledged but non-terminal at the
        # provider, and the reply is lost on the wire.
        unknown = cluster.submit_group(request)
        assert unknown.state is GroupOrderState.RECOVERY_REQUIRED
        assert unknown.error_code is None
        assert unknown.budget_reservation is not None
        assert unknown.budget_reservation.state is GroupBudgetReservationState.HELD
        leg_a = next(leg for leg in unknown.legs if leg.leg_id == "leg-a")
        leg_b = next(leg for leg in unknown.legs if leg.leg_id == "leg-b")
        assert leg_a.phase is GroupLegPhase.UNKNOWN
        assert leg_a.submitted_intent is not None
        assert leg_a.child_status is None
        assert leg_b.phase is GroupLegPhase.READY
        assert leg_b.submitted_intent is None

        counts, dropped, remaining = cluster.fault_stats()
        assert counts[("account-a", "orders.submit_group_leg")] == 1
        assert counts.get(("account-b", "orders.submit_group_leg")) is None
        assert dropped == 1
        assert remaining == 0

        intent_a = group_leg_intent_id(_GROUP_REPLY_LOSS_LATE_FILL, "leg-a")
        status_a = cluster.status("account-a")
        assert len(status_a[1]) == 1
        assert status_a[2] == {"order_queries": 1, "fill_queries": 1}
        assert tuple(hold.intent_id for hold in status_a[3]) == (intent_a,)
        assert status_a[4] == ()
        # Account-b never saw any order work, only its reservation claim.
        status_b = cluster.status("account-b")
        assert status_b[1] == ()
        assert status_b[2] == {"order_queries": 0, "fill_queries": 0}
        assert status_b[3] == ()
        assert status_b[4] == cluster.expected_orders["account-b"]

        # The fill arrives at the provider only now, after the UNKNOWN.
        late_client_order_id = _inject_late_fill(
            Path(cluster.account_paths["account-a"]["provider"])
        )
        assert late_client_order_id.startswith("grp-")

        # Call 2: the replay is recovered by the ORIGINAL child intent and the
        # journal still proves nothing terminal; the group must stay UNKNOWN.
        recovered = cluster.submit_group(request)
        assert recovered.state is GroupOrderState.RECOVERY_REQUIRED
        assert recovered.error_code == "order_outcome_unknown"
        assert recovered.budget_reservation is not None
        assert recovered.budget_reservation.state is GroupBudgetReservationState.HELD
        leg_a = next(leg for leg in recovered.legs if leg.leg_id == "leg-a")
        leg_b = next(leg for leg in recovered.legs if leg.leg_id == "leg-b")
        assert leg_a.phase is GroupLegPhase.UNKNOWN
        assert leg_a.child_status is not None
        assert leg_a.child_status.state is ExecutionState.UNKNOWN
        assert leg_a.child_status.filled_quantity == Decimal("0")
        assert leg_a.claim_receipt is not None
        assert leg_a.resolution_receipt is None
        assert leg_b.phase is GroupLegPhase.READY
        assert leg_b.submitted_intent is None
        assert leg_b.child_status is None
        assert recovered.state is not GroupOrderState.PAIRED
        assert recovered.residual_base_units != Decimal("0")

        counts, dropped, remaining = cluster.fault_stats()
        # No second submit and no second provider observation ever happened.
        assert counts[("account-a", "orders.submit_group_leg")] == 1
        assert counts.get(("account-b", "orders.submit_group_leg")) is None
        assert counts[("account-a", "orders.query_group_leg")] == 2
        assert counts.get(("account-a", "orders.resolve_group_leg")) is None
        assert counts.get(("account-b", "orders.resolve_group_leg")) is None
        assert counts.get(("account-a", "reservations.release")) is None
        assert counts.get(("account-b", "reservations.release")) is None
        assert dropped == 1
        assert remaining == 0

        status_a = cluster.status("account-a")
        assert len(status_a[1]) == 1
        assert status_a[2] == {"order_queries": 1, "fill_queries": 1}
        assert tuple(hold.intent_id for hold in status_a[3]) == (intent_a,)
        status_b = cluster.status("account-b")
        assert status_b[1] == ()
        assert status_b[2] == {"order_queries": 0, "fill_queries": 0}
        assert status_b[3] == ()
        assert status_b[4] == cluster.expected_orders["account-b"]

    persisted_groups = SqliteOrderGroupStore(cluster.coordinator_paths["groups"])
    try:
        persisted = persisted_groups.get(_PORTFOLIO, _GROUP_REPLY_LOSS_LATE_FILL)
        assert persisted == recovered
        events = persisted_groups.events(_PORTFOLIO, _GROUP_REPLY_LOSS_LATE_FILL)
    finally:
        persisted_groups.close()

    submits = tuple(event for event in events if event.event_type == "FIRST_LEG_SUBMIT_STARTED")
    assert len(submits) == 1
    observations = tuple(
        event for event in events if event.event_type == "FIRST_LEG_STATUS_OBSERVED"
    )
    assert len(observations) == 2
    first_payload = observations[0].payload
    assert first_payload["leg_id"] == "leg-a"
    assert first_payload["state"] == "UNKNOWN"
    assert first_payload["code"] == "ORDER_SUBMIT_OUTCOME_UNKNOWN"
    second_payload = observations[1].payload
    assert second_payload["leg_id"] == "leg-a"
    assert second_payload["state"] == "UNKNOWN"
    assert "code" not in second_payload
    recovery_events = tuple(
        event for event in events if event.event_type == "GROUP_RECOVERY_REQUIRED"
    )
    assert len(recovery_events) == 1
    assert recovery_events[0].payload["code"] == "ORDER_OUTCOME_UNKNOWN"
    for absent in (
        "HEDGE_LEG_SUBMIT_STARTED",
        "HEDGE_LEG_SIZED",
        "GROUP_PAIRED",
        "GROUP_DISPATCH_RESOLUTION_RECORDED",
    ):
        assert not tuple(event for event in events if event.event_type == absent), absent

    # The late fill is durable at the provider, but was never adopted: no
    # re-observation exists, so the record stays honestly UNKNOWN.
    with sqlite3.connect(cluster.account_paths["account-a"]["provider"]) as connection:
        late_order = json.loads(
            connection.execute(
                "SELECT order_json FROM provider_orders WHERE client_order_id=?",
                (late_client_order_id,),
            ).fetchone()[0]
        )
    assert late_order["status"] == "filled"
    assert late_order["order_revision"] == 2
    assert late_order["filled"] == late_order["quantity"]

    execution_scope_a = _execution_scope("account-a")
    store_a = SqliteExecutionStore(cluster.account_paths["account-a"]["execution"])
    try:
        record_a = store_a.get(intent_a, scope=execution_scope_a)
        assert record_a is not None
        assert record_a.state is ExecutionState.UNKNOWN
        assert record_a.filled_quantity == Decimal("0")
        assert record_a.dispatch_attempts == 1
        assert record_a.review_required is True
        assert record_a.unknown_reason is not None
        events_a = _leg_outbox(store_a, execution_scope_a, intent_a)
        assert tuple(event.event_type for event in events_a) == (
            "intent_recorded",
            "intent_admitted",
            "dispatch_claimed",
            "dispatch_unknown",
        )
    finally:
        store_a.close()

    intent_b = group_leg_intent_id(_GROUP_REPLY_LOSS_LATE_FILL, "leg-b")
    store_b = SqliteExecutionStore(cluster.account_paths["account-b"]["execution"])
    try:
        assert store_b.get(intent_b, scope=_execution_scope("account-b")) is None
        assert _leg_outbox(store_b, _execution_scope("account-b"), intent_b) == ()
    finally:
        store_b.close()

    assert _risk_legs(cluster.account_paths, "account-a", _GROUP_REPLY_LOSS_LATE_FILL) == (
        ("leg-a", True, False),
    )
    assert _risk_legs(cluster.account_paths, "account-b", _GROUP_REPLY_LOSS_LATE_FILL) == (
        ("leg-b", False, False),
    )
    # Both dispatch holds stay pending: nothing was resolved or released.
    assert _pending_hold_intents(cluster.account_paths, "account-a") == (intent_a,)
    assert _pending_hold_intents(cluster.account_paths, "account-b") == ()
    _assert_provider_writes(Path(cluster.account_paths["account-a"]["provider"]), 1)
    writes_b, counters_b = _provider_snapshot(Path(cluster.account_paths["account-b"]["provider"]))
    assert writes_b == ()
    assert counters_b == {"order_queries": 0, "fill_queries": 0}
