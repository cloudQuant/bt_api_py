"""DIM09 host-level acceptance for the v3 three-leg group service path.

This slice drives the parent-repo wire gates added in DIM09 step 2 over the
real coordinator service, reservations service, and account-owner services
(in-process harness from ``_dim09_three_leg_fixtures``):

* S1/S2: a versioned submit body is the exact five-key shape with
  ``version == 3`` and exactly three legs, each carrying a ``ratio``; every
  other shape fails closed before acceptance.
* S4/S5: replay recognition and exact replay binding cover the v3 body.
* S7: the reservations member vector accepts 1..3 legs.
* S9: the client accepted-binding member vector accepts 1..3 legs.
* S6 (CFG06b gate, leg-count agnostic): a denied group policy keeps the
  three-leg submission out of acceptance and dispatch entirely.

Nodes:

1. all three legs fill from source-backed provider facts -> PAIRED with a
   HELD budget hold and one durable proof chain per leg;
2. the provider rejects the future leg -> the chain interrupts with
   ``chain_interrupted_zero_fill`` and the call leg is never dispatched;
3. the final-leg submit reply is lost, then owner and coordinator restart ->
   the original-ID query reconciles the UNKNOWN leg with no resubmit;
4. a durable group-policy denial blocks the three-leg submit before
   acceptance with zero dispatch;
5. the wire whitelist is fail-closed: leg counts, version discriminator, leg
   key sets, ratios, contract rules, S7 and S9 windows, plus a legacy 2-leg
   positive control.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest
from bt_api_execution import (
    ExecutionState,
    GroupBudgetReservationState,
    GroupOrderSnapshot,
    GroupOrderState,
    Side,
    group_leg_intent_id,
    order_intent_from_payload,
)
from bt_api_execution.group_order_contracts import GroupReservationPhase
from bt_api_risk.service_component import GroupPolicyService, GroupPolicyStore

from bt_api_py.service_execution_group_client import (
    AcceptedGroupBinding,
    AcceptedGroupBindingError,
)
from tests.integration import _dim09_three_leg_fixtures as fixtures


def _policy_digest(document: dict) -> str:
    canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_dim09_three_legs_reach_paired_with_source_backed_facts(
    tmp_path, monkeypatch
) -> None:
    cluster = fixtures.Dim09ThreeLegCluster(tmp_path, monkeypatch)
    try:
        group_id = "dim09-group-paired"
        result = cluster.submit(cluster.v3_body(group_id))
        assert result["ok"] is True, result
        snapshot = GroupOrderSnapshot.from_payload(result["result"]["snapshot"])

        assert snapshot.state is GroupOrderState.PAIRED
        assert snapshot.error_code is None
        assert snapshot.request.version == 3
        assert snapshot.residual_vector == (Decimal("0"), Decimal("0"), Decimal("0"))
        assert snapshot.residual_base_units == Decimal("0")
        assert snapshot.budget_reservation is not None
        assert snapshot.budget_reservation.state is GroupBudgetReservationState.HELD
        assert (
            snapshot.budget_reservation.policy_fingerprint
            == cluster.portfolio_budget_policy.fingerprint
        )
        quote = snapshot.request.budget_quote
        assert quote is not None and len(quote.legs) == 3
        assert len(snapshot.reservations) == 1
        assert snapshot.reservations[0].phase is GroupReservationPhase.ACTIVE
        assert snapshot.reservations[0].account_ref == fixtures._ACCOUNT
        assert [leg.leg_id for leg in snapshot.legs] == list(fixtures.LEG_IDS)

        for leg in snapshot.legs:
            assert leg.phase.value == "OBSERVED"
            assert leg.submitted_intent is not None
            assert leg.submitted_intent.intent_id == cluster.bound_intent_id(
                group_id, leg.leg_id
            )
            assert leg.child_status is not None
            assert leg.child_status.state is ExecutionState.FILLED
            assert leg.child_status.filled_quantity == Decimal("1")
            assert leg.claim_receipt is not None
            assert leg.claim_receipt.permit.group_id == group_id
            assert leg.claim_receipt.permit.leg_id == leg.leg_id
            assert leg.resolution_receipt is not None
            assert leg.resolution_receipt.resolution_kind == "TERMINAL_EXPOSURE_RESOLVED"
            assert leg.resolution_receipt.trade_count == 1
            assert leg.resolution_receipt.dispatch_latch_active is False
            # Durable per-leg proof chain: one dispatch attempt, the exact
            # wire-declared intent, and the group binding on the record.
            record = cluster.execution_store.get(
                leg.submitted_intent.intent_id, scope=cluster.execution_scope
            )
            assert record is not None
            assert record.state is ExecutionState.FILLED
            assert record.dispatch_attempts == 1
            assert record.group_binding is not None
            assert record.group_binding.group_id == group_id
            assert record.group_binding.leg_id == leg.leg_id
            assert record.payload_sha256 == leg.submitted_intent.fingerprint

        # Source-backed provider facts: exactly one committed write per leg.
        writes, counters = cluster.provider.snapshot()
        assert len(writes) == 3 and len(set(writes)) == 3
        assert counters == {"order_queries": 3, "fill_queries": 3}
        expected_ids = [
            cluster.bound_intent_id(group_id, leg_id) for leg_id in fixtures.LEG_IDS
        ]
        assert cluster.loopback.submitted_intent_ids() == expected_ids
        assert all(
            call[2] == fixtures._ACCOUNT
            for call in cluster.loopback.calls
            if call[1] == "orders.submit_group_leg"
        )
        reserve_calls = [
            call for call in cluster.loopback.calls if call[1] == "reservations.reserve"
        ]
        assert len(reserve_calls) == 1
        assert reserve_calls[0][2] == fixtures._ACCOUNT
        assert reserve_calls[0][3] == cluster.reservation_scope_ref
        members = reserve_calls[0][4]["members"]
        assert len(members) == 3
        assert {member["leg_id"] for member in members} == set(fixtures.LEG_IDS)

        # Risk vector: all three legs claimed and resolved; no live holds.
        vector = cluster.risk_admission.read_group_vector_snapshot(
            cluster.risk_scope, group_id
        )
        assert len(vector.legs) == 3
        assert all(leg.claimed and leg.resolved for leg in vector.legs)
        assert cluster.authority.pending_holds() == ()

        # Read-side stability: the persisted snapshot is byte-stable and the
        # query path performs no provider work.
        assert cluster.current_snapshot(group_id) == snapshot
        assert cluster.provider.snapshot() == (writes, counters)
    finally:
        cluster.close()


def test_dim09_second_leg_rejected_stops_third_leg_over_wire(
    tmp_path, monkeypatch
) -> None:
    cluster = fixtures.Dim09ThreeLegCluster(
        tmp_path, monkeypatch, initial_states=("filled", "rejected")
    )
    try:
        group_id = "dim09-group-rejected-mid"
        result = cluster.submit(cluster.v3_body(group_id))
        assert result["ok"] is True, result
        snapshot = GroupOrderSnapshot.from_payload(result["result"]["snapshot"])

        assert snapshot.state is GroupOrderState.RECOVERY_REQUIRED
        assert snapshot.error_code == "chain_interrupted_zero_fill"
        assert snapshot.residual_vector == (Decimal("0"), Decimal("1"), Decimal("1"))
        assert snapshot.residual_base_units == Decimal("2")
        assert snapshot.budget_reservation is not None
        assert snapshot.budget_reservation.state is GroupBudgetReservationState.HELD
        assert len(snapshot.reservations) == 1
        assert snapshot.reservations[0].phase is GroupReservationPhase.ACTIVE

        put_leg, future_leg, call_leg = snapshot.legs
        assert put_leg.child_status.state is ExecutionState.FILLED
        assert put_leg.child_status.filled_quantity == Decimal("1")
        assert future_leg.child_status is not None
        assert future_leg.child_status.state is ExecutionState.REJECTED
        assert future_leg.child_status.filled_quantity == Decimal("0")
        assert call_leg.submitted_intent is None
        assert call_leg.child_status is None

        # Wire evidence: the third leg was never dispatched (or even queried
        # for a child); only the two earlier legs reached the provider.
        submitted = cluster.loopback.submitted_intent_ids()
        assert submitted == [
            cluster.bound_intent_id(group_id, "leg-put"),
            cluster.bound_intent_id(group_id, "leg-future"),
        ]
        assert cluster.bound_intent_id(group_id, "leg-call") not in submitted
        writes, counters = cluster.provider.snapshot()
        assert len(writes) == 2
        assert counters == {"order_queries": 2, "fill_queries": 2}
        assert cluster.current_snapshot(group_id) == snapshot
    finally:
        cluster.close()


def test_dim09_coordinator_restart_reconciles_third_leg_unknown(
    tmp_path, monkeypatch
) -> None:
    group_id = "dim09-group-unknown-call"
    call_intent_id = group_leg_intent_id(group_id, "leg-call")
    cluster = fixtures.Dim09ThreeLegCluster(
        tmp_path,
        monkeypatch,
        drop_submit_replies_for=(call_intent_id,),
    )
    try:
        body = cluster.v3_body(group_id)
        result = cluster.submit(body)
        assert result["ok"] is True, result
        snapshot = GroupOrderSnapshot.from_payload(result["result"]["snapshot"])

        assert snapshot.state is GroupOrderState.RECOVERY_REQUIRED
        assert snapshot.error_code is None
        assert snapshot.budget_reservation is not None
        assert snapshot.budget_reservation.state is GroupBudgetReservationState.HELD
        call_leg = snapshot.legs[2]
        assert call_leg.phase.value == "UNKNOWN"
        assert call_leg.submitted_intent is not None
        assert call_leg.submitted_intent.intent_id == call_intent_id

        # The reply was lost, never the write: all three provider writes are
        # committed before the synthetic ConnectionError.
        writes, counters = cluster.provider.snapshot()
        assert len(writes) == 3
        assert counters == {"order_queries": 3, "fill_queries": 3}
        assert cluster.loopback.submitted_intent_ids() == [
            cluster.bound_intent_id(group_id, leg_id) for leg_id in fixtures.LEG_IDS
        ]

        # Owner "process" restart on the same durable stores: the committed
        # child is served by original-ID query and no latch needs recovery.
        mark = cluster.loopback.mark()
        cluster.restart_owner()
        record = cluster.execution_store.get(
            call_intent_id, scope=cluster.execution_scope
        )
        assert record is not None
        assert record.state is ExecutionState.FILLED
        assert record.dispatch_attempts == 1

        # Coordinator restart: the same durable group stores under a renewed
        # registration epoch still read the held, UNKNOWN snapshot.
        cluster.restart_coordinator()
        pending = cluster.current_snapshot(group_id)
        assert pending.state is GroupOrderState.RECOVERY_REQUIRED
        assert pending.legs[2].phase.value == "UNKNOWN"
        assert pending.budget_reservation.state is GroupBudgetReservationState.HELD
        assert cluster.loopback.operation_names(since=mark) == []

        # Replaying the exact v3 body reconciles via the original intent ID.
        replayed = cluster.submit(body)
        assert replayed["ok"] is True, replayed
        reconciled = GroupOrderSnapshot.from_payload(replayed["result"]["snapshot"])
        assert reconciled.state is GroupOrderState.PAIRED
        assert reconciled.error_code is None
        assert reconciled.residual_vector == (Decimal("0"), Decimal("0"), Decimal("0"))
        assert reconciled.residual_base_units == Decimal("0")
        assert reconciled.budget_reservation.state is GroupBudgetReservationState.HELD
        assert reconciled.legs[2].child_status is not None
        assert reconciled.legs[2].child_status.state is ExecutionState.FILLED
        assert reconciled.legs[2].child_status.filled_quantity == Decimal("1")

        after_names = cluster.loopback.operation_names(since=mark)
        # The recovery tick re-reads the reservation receipt and the original
        # child ID; nothing is re-reserved, released, or resubmitted.
        assert after_names.count("orders.query_group_leg") == 1
        assert set(after_names) <= {"reservations.query", "orders.query_group_leg"}
        assert cluster.loopback.submitted_intent_ids(since=mark) == []
        query_calls = [
            call
            for call in cluster.loopback.calls[mark:]
            if call[1] == "orders.query_group_leg"
        ]
        assert query_calls[0][4]["intent_id"] == call_intent_id
        assert cluster.loopback.submitted_intent_ids() == [
            cluster.bound_intent_id(group_id, leg_id) for leg_id in fixtures.LEG_IDS
        ]
        record_after = cluster.execution_store.get(
            call_intent_id, scope=cluster.execution_scope
        )
        assert record_after is not None
        assert record_after.dispatch_attempts == 1
    finally:
        cluster.close()


def test_dim09_group_policy_denial_blocks_three_leg_submit(
    tmp_path, monkeypatch
) -> None:
    policy_ref = "dim09-group-admission-v1"
    denied_group_id = "dim09-group-denied"
    document = {
        "admitted_portfolios": [fixtures._PORTFOLIO],
        "denied_group_ids": [denied_group_id],
    }
    policy_store = GroupPolicyStore(str(tmp_path / "dim09-group-policy.sqlite3"))
    version = _policy_digest(document)
    gate = None
    cluster = None
    try:
        policy_store.pin(policy_ref, version, document)
        gate = GroupPolicyService(policy_store, policy_ref, version)
        cluster = fixtures.Dim09ThreeLegCluster(
            tmp_path, monkeypatch, group_admission=gate
        )
        result = cluster.submit(cluster.v3_body(denied_group_id))
        assert result == {"ok": False, "code": "GROUP_POLICY_DENIED", "result": {}}

        # Denied before acceptance: no run binding, no group snapshot, no
        # reservation or order RPC, no provider write.
        assert (
            cluster.origin_run_bindings.get(fixtures._PORTFOLIO, denied_group_id) is None
        )
        assert cluster.coordinator_store.get(fixtures._PORTFOLIO, denied_group_id) is None
        assert cluster.loopback.calls == []
        assert cluster.provider.snapshot() == ((), {"order_queries": 0, "fill_queries": 0})
    finally:
        if cluster is not None:
            cluster.close()
        if gate is not None:
            gate.close()
        policy_store.close()


def test_dim09_three_leg_wire_whitelist_is_fail_closed(tmp_path, monkeypatch) -> None:
    cluster = fixtures.Dim09ThreeLegCluster(
        tmp_path, monkeypatch, initial_states=("filled", "filled")
    )
    try:
        group_id = "dim09-group-whitelist"

        def denied(body: dict, code: str) -> None:
            result = cluster.submit(body)
            assert result == {"ok": False, "code": code, "result": {}}, (result, code)
            assert (
                cluster.origin_run_bindings.get(fixtures._PORTFOLIO, body["group_id"])
                is None
            )
            assert (
                cluster.coordinator_store.get(fixtures._PORTFOLIO, body["group_id"])
                is None
            )
            assert cluster.loopback.calls == []

        # S1: the versioned body requires exactly three legs.
        two_legs = cluster.v3_body(group_id)
        two_legs["legs"] = two_legs["legs"][:2]
        denied(two_legs, "GROUP_LEGS_INVALID")
        four_legs = cluster.v3_body(group_id)
        four_legs["legs"] = four_legs["legs"] + [
            {
                "leg_id": "leg-extra",
                "account_ref": fixtures._ACCOUNT,
                "ratio": "1",
                "intent": cluster.leg_intent("leg-extra", Side.BUY, group_id=group_id),
            }
        ]
        denied(four_legs, "GROUP_LEGS_INVALID")

        # S2: only the exact five-key body with `version` int 3 is v3.
        for bad_version in (2, "3", 3.0, True, None):
            denied(cluster.v3_body(group_id, version=bad_version), "GROUP_VERSION_INVALID")

        # v3 legs must carry `ratio`; malformed ratios fail before contract work.
        missing_ratio = cluster.v3_body(group_id)
        del missing_ratio["legs"][2]["ratio"]
        denied(missing_ratio, "GROUP_LEG_INVALID")
        for bad_ratio in ("1e0", "0", "-1", " 1", ""):
            body = cluster.v3_body(group_id)
            body["legs"][2]["ratio"] = bad_ratio
            denied(body, "GROUP_LEG_TERMS_INVALID")

        # Step-1 v3 contract rules still bite over the wire.
        first_ratio = cluster.v3_body(group_id)
        first_ratio["legs"][0]["ratio"] = "2"
        denied(first_ratio, "GROUP_INTENT_INVALID")
        same_side = cluster.v3_body(
            group_id, sides=(Side.BUY, Side.BUY, Side.BUY)
        )
        denied(same_side, "GROUP_INTENT_INVALID")

        # Legacy bodies may not smuggle ratios or a third leg.
        legacy_ratio = cluster.legacy_body(group_id)
        legacy_ratio["legs"][1]["ratio"] = "1"
        denied(legacy_ratio, "GROUP_LEG_INVALID")
        legacy_three = cluster.legacy_body(
            group_id,
            leg_ids=("leg-a", "leg-b", "leg-c"),
            sides=(Side.BUY, Side.SELL, Side.BUY),
        )
        denied(legacy_three, "GROUP_LEGS_INVALID")

        # S7: the reservations member window accepts 1..3 legs.
        def reserve_reply(group: str, members: list[dict]) -> dict:
            return dict(
                cluster.reservation_service.handle(
                    "reservations.reserve",
                    context=cluster.reservation_context,
                    arguments={
                        "origin_run_id": fixtures._RUN,
                        "group_id": group,
                        "deadline_ns": cluster.deadline_ns,
                        "members": members,
                    },
                )
            )

        reserve_group = "dim09-group-reserve-window"
        member_legs = ("leg-m1", "leg-m2", "leg-m3", "leg-m4")

        def member(leg_id: str, group: str) -> dict:
            return {
                "leg_id": leg_id,
                "intent": cluster.leg_intent(leg_id, Side.BUY, group_id=group),
            }

        four_members = reserve_reply(
            reserve_group, [member(leg_id, reserve_group) for leg_id in member_legs]
        )
        assert four_members == {"ok": False, "code": "INVALID_MEMBERS", "result": {}}
        empty_members = reserve_reply(reserve_group, [])
        assert empty_members == {"ok": False, "code": "INVALID_MEMBERS", "result": {}}
        # Positive controls: a one-member and a three-member vector each pass
        # the length gate and reserve for real on the trusted Risk gate (the
        # client begin_reservation persists the exact member vector).
        one_group = "dim09-group-reserve-one"
        one_member = reserve_reply(one_group, [member(member_legs[0], one_group)])
        assert one_member["ok"] is True, one_member
        assert [leg["leg_id"] for leg in one_member["result"]["legs"]] == ["leg-m1"]
        assert all(leg["state"] == "ACTIVE" for leg in one_member["result"]["legs"])
        three_group = "dim09-group-reserve-three"
        three_members = reserve_reply(
            three_group,
            [member(leg_id, three_group) for leg_id in member_legs[:3]],
        )
        assert three_members["ok"] is True, three_members
        assert [leg["leg_id"] for leg in three_members["result"]["legs"]] == [
            "leg-m1",
            "leg-m2",
            "leg-m3",
        ]
        assert all(leg["state"] == "ACTIVE" for leg in three_members["result"]["legs"])
        # No reservation-only control reached the wire or the provider leaf.
        assert cluster.loopback.calls == []
        assert cluster.provider.snapshot() == ((), {"order_queries": 0, "fill_queries": 0})

        # S9: the client accepted-binding member vector accepts 1..3 legs.
        binding_intents = [
            order_intent_from_payload(
                cluster.leg_intent(leg_id, Side.BUY, group_id="dim09-binding-group")
            )
            for leg_id in ("leg-1", "leg-2", "leg-3", "leg-4")
        ]

        def binding_with(members: tuple) -> AcceptedGroupBinding:
            return AcceptedGroupBinding(
                identity={
                    "role": "service",
                    "principal_ref": "dim09-binding-principal",
                    "actor_ref": None,
                    "delegate_ref": None,
                },
                principal_ref="dim09-binding-principal",
                first_registration_id="dim09-binding-registration",
                instance_id="dim09-binding-owner",
                scope_ref="dim09-binding-scope",
                service_epoch="dim09-binding-epoch",
                policy_version="dim09-policy-v1",
                requirements_version="dim09-requirements-v1",
                stable_scope=cluster.owner_scope,
                origin_run_id="dim09-binding-run",
                group_id="dim09-binding-group",
                deadline_ns=cluster.deadline_ns,
                members=members,
                request_fingerprint="a" * 64,
                reservation=None,
            )

        one = binding_with((("leg-1", binding_intents[0]),))
        assert len(one.members) == 1
        three = binding_with(
            tuple((f"leg-{index}", binding_intents[index - 1]) for index in (1, 2, 3))
        )
        assert len(three.members) == 3
        with pytest.raises(AcceptedGroupBindingError):
            binding_with(())
        with pytest.raises(AcceptedGroupBindingError):
            binding_with(
                tuple(
                    (f"leg-{index}", binding_intents[index - 1])
                    for index in (1, 2, 3, 4)
                )
            )

        # Positive control: the legacy four-key two-leg body still pairs.
        legacy_group = "dim09-group-legacy-control"
        legacy_result = cluster.submit(cluster.legacy_body(legacy_group))
        assert legacy_result["ok"] is True, legacy_result
        legacy_snapshot = GroupOrderSnapshot.from_payload(
            legacy_result["result"]["snapshot"]
        )
        assert legacy_snapshot.state is GroupOrderState.PAIRED
        assert legacy_snapshot.error_code is None
        assert legacy_snapshot.residual_vector is None
        assert legacy_snapshot.budget_reservation.state is GroupBudgetReservationState.HELD
        assert len(legacy_snapshot.legs) == 2
        writes, counters = cluster.provider.snapshot()
        assert len(writes) == 2
        assert counters == {"order_queries": 2, "fill_queries": 2}
        assert cluster.loopback.submitted_intent_ids() == [
            cluster.bound_intent_id(legacy_group, leg_id)
            for leg_id in fixtures._LEGACY_IDS
        ]
    finally:
        cluster.close()
