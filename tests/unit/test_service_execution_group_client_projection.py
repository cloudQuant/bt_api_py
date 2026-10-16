from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from bt_api_base.service_protocol import WireScope
from bt_api_execution import (
    ExecutionScope,
    GroupOrderIntent,
    GroupOrderLegPlan,
    OrderIntent,
    Side,
    SqliteOrderGroupStore,
    TimeInForce,
    group_leg_intent_id,
)
from bt_api_execution.group_order_contracts import GroupOrderEvent, GroupOrderSnapshot
from bt_api_py.service_execution_group_client import (
    AcceptedGroupBindingError,
    GroupOriginRunBinding,
    verify_accepted_group_projection,
)


def _request(group_id: str) -> GroupOrderIntent:
    deadline_ns = 10_000
    scope = ExecutionScope("PAPER", "synthetic", "account-a", "strategy-a")
    plans = []
    for leg_id, side in (("leg-a", Side.BUY), ("leg-b", Side.SELL)):
        intent = OrderIntent.limit(
            intent_id=group_leg_intent_id(group_id, leg_id),
            scope=scope,
            signal_id=f"signal-{group_id}-{leg_id}",
            instrument="SYNTH-USD",
            side=side,
            quantity=Decimal("1"),
            price=Decimal("10"),
            time_in_force=TimeInForce.IOC,
            metadata_version="metadata-v1",
            deadline_ns=deadline_ns,
        )
        plans.append(
            GroupOrderLegPlan(
                leg_id=leg_id,
                account_ref="account-a",
                maximum_intent=intent,
                base_units_per_order_unit=Decimal("1"),
                quantity_step=Decimal("1"),
                metadata_valid_until_ns=deadline_ns + 1,
            )
        )
    return GroupOrderIntent(
        group_id=group_id,
        portfolio_ref="portfolio-a",
        strategy_id="strategy-a",
        coordinator_principal_ref="coordinator-service",
        received_at_ns=100,
        deadline_ns=deadline_ns,
        first_leg_id="leg-a",
        legs=tuple(plans),
    )


def _binding(request: GroupOrderIntent) -> GroupOriginRunBinding:
    return GroupOriginRunBinding(
        request=request,
        origin_run_id="run-a",
        strategy_principal_ref="strategy-principal",
        strategy_identity={
            "role": "STRATEGY_CLIENT",
            "principal_ref": "strategy-principal",
            "actor_ref": "strategy-a",
            "delegate_ref": None,
        },
        strategy_scope=WireScope(
            execution_domain_ref="paper:group-test",
            account_refs=("account-a",),
            strategy_ref="strategy-a",
            run_id="run-a",
            portfolio_ref="portfolio-a",
        ),
        coordinator_principal_ref="coordinator-service",
        instance_id="coordinator-instance",
        scope_ref="coordinator-scope",
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        registration_id="registration-a",
        service_epoch="epoch-a",
        request_fingerprint=request.fingerprint,
    )


def test_accepted_projection_accepts_interleaved_global_group_event_sequences(tmp_path):
    store = SqliteOrderGroupStore(tmp_path / "groups.sqlite3")
    try:
        request_a = _request("group-a")
        request_b = _request("group-b")
        initial_a = store.create_or_get(request_a, now_ns=100)
        store.create_or_get(request_b, now_ns=100)
        snapshot_a = store.bind_account_scopes(
            initial_a, {"account-a": "a" * 64}, now_ns=101
        )
        events_a = store.events("portfolio-a", "group-a")

        assert type(snapshot_a) is GroupOrderSnapshot
        assert all(type(event) is GroupOrderEvent for event in events_a)
        assert tuple(event.sequence for event in events_a) == (1, 3)
        assert verify_accepted_group_projection(
            _binding(request_a), snapshot_a, events_a
        ) == request_a.fingerprint

        with pytest.raises(AcceptedGroupBindingError):
            verify_accepted_group_projection(_binding(request_a), object(), events_a)
        with pytest.raises(AcceptedGroupBindingError):
            verify_accepted_group_projection(
                _binding(request_a), snapshot_a, (events_a[0], object())
            )
        with pytest.raises(AcceptedGroupBindingError):
            verify_accepted_group_projection(
                _binding(request_a), snapshot_a, events_a[:-1]
            )
        with pytest.raises(AcceptedGroupBindingError):
            verify_accepted_group_projection(
                _binding(request_a), replace(snapshot_a, revision=2), events_a
            )

        reversed_sequences = (
            replace(events_a[0], sequence=3),
            replace(events_a[1], sequence=1),
        )
        with pytest.raises(AcceptedGroupBindingError):
            verify_accepted_group_projection(
                _binding(request_a), snapshot_a, reversed_sequences
            )
    finally:
        store.close()
