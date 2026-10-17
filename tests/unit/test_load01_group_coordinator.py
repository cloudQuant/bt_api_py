"""Focused source-fake capacity contract for the shared N5 coordinator."""

from __future__ import annotations

from decimal import Decimal

from bt_api_execution import ExecutionScope, OrderType
from bt_api_py.service_execution_group import GroupInstrumentRule
from tests.unit._load01_group_coordinator import (
    _PORTFOLIO_REF,
    _coordinator_target_map,
    build_n5_group_metadata,
)
from tests.unit.test_service_gateway_hundred_strategy_integration import _ACCOUNT_REFS
from tests.unit._load01_group_workload import N5_LEG_SPECS


def test_n5_targets_share_capacity_for_all_1800_held_group_reservations() -> None:
    metadata = build_n5_group_metadata(now_ns=2_000_000_000_000)
    metadata_by_instrument = {item.instrument: item for item in metadata}
    instrument_rules = {
        spec.instrument: GroupInstrumentRule(
            metadata=metadata_by_instrument[spec.instrument],
            base_units_per_order_unit=spec.contract_multiplier,
            allowed_order_types=frozenset({OrderType.LIMIT}),
        )
        for spec in N5_LEG_SPECS
    }
    routes = {
        strategy_index: {
            "account_ref": _ACCOUNT_REFS[account_index],
            "strategy_ref": f"load01-strategy-{strategy_index:03d}",
            "execution_scope": ExecutionScope(
                provider="fixture",
                environment="simulation",
                account_ref=_ACCOUNT_REFS[account_index],
                strategy_id=f"load01-strategy-{strategy_index:03d}",
            ),
            "scope_ref": f"load01-n5-group-scope-a{account_index:02d}-v1",
            "risk_scope_fingerprint": "0" * 64,
            "order_scope_ref": f"load01-n5-order-a{account_index:02d}-v1",
            "query_scope_ref": f"load01-n5-query-a{account_index:02d}-v1",
            "reservation_scope_ref": f"load01-n5-reservation-a{account_index:02d}-v1",
            "instruments": instrument_rules,
        }
        for account_index, strategy_index in enumerate(range(0, 100, 10))
    }

    targets = _coordinator_target_map(routes, metadata)

    assert len(targets) == 10
    assert len({scope.portfolio_ref for scope in targets}) == 1
    assert {scope.portfolio_ref for scope in targets} == {_PORTFOLIO_REF}
    assert len({scope.account_refs[0] for scope in targets}) == 10
    assert len({scope.strategy_ref for scope in targets}) == 10
    policies = [target.portfolio_budget_policy for target in targets.values()]
    assert all(policy is not None for policy in policies)
    assert len({policy.fingerprint for policy in policies if policy is not None}) == 1

    policy = policies[0]
    assert policy is not None
    one_group_notional = Decimal("262.9133")
    full_window_groups = 1_800
    assert policy.max_group_gross_notional == one_group_notional
    assert policy.max_active_groups == full_window_groups
    assert policy.max_active_gross_notional == one_group_notional * full_window_groups

