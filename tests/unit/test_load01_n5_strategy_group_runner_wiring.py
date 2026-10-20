from __future__ import annotations

from dataclasses import replace
from collections import Counter
from decimal import Decimal
from pathlib import Path
import time
from types import MappingProxyType, SimpleNamespace

import pytest

from bt_api_execution import (
    ContractValidationError,
    ExecutionScope,
    ExecutionState,
    GroupLegPhase,
    OrderIntent,
    TimeInForce,
    group_leg_intent_id,
)
from bt_api_risk import InstrumentRiskMetadata
from tests.unit import test_load01_hundred_strategy_30min as runner
from tests.unit._load01_group_workload import (
    N5_GROUP_QUANTITY_UNIT,
    N5_BASE_UNIT_SCALES,
    N5_LEG_SPECS,
)
from tests.unit._load01_profiles import load01_profile


def _metadata() -> tuple[InstrumentRiskMetadata, ...]:
    now_ns = time.time_ns()
    return tuple(
        InstrumentRiskMetadata(
            instrument=spec.instrument,
            metadata_version=f"load01-n5-{spec.instrument.lower()}-v1",
            as_of_ns=now_ns - 1_000_000_000,
            expires_at_ns=now_ns + 3_600_000_000_000,
            tick_size=Decimal("0.01"),
            quantity_step=spec.quantity_step,
            contract_multiplier=spec.contract_multiplier,
            max_gross_notional=Decimal("100000"),
            taker_fee_bps=spec.taker_fee_bps,
            fixed_fee=spec.fixed_fee,
            max_slippage_bps=spec.max_slippage_bps,
            quantity_unit=N5_GROUP_QUANTITY_UNIT,
            valuation_unit="USD",
            quote_currency="USD",
        )
        for spec in N5_LEG_SPECS
    )


def _services(account_index: int = 0):
    return (
        {
            "service_id": f"account-{account_index}",
            "instance_id": f"account-instance-{account_index}",
            "scope_ref": f"account-scope-{account_index}",
            "endpoint": "tcp://127.0.0.1:31901",
            "security_ref": f"account-security-{account_index}",
        },
        {
            "service_id": f"execution-{account_index}",
            "instance_id": f"execution-instance-{account_index}",
            "order_scope_ref": f"orders-scope-{account_index}",
            "query_scope_ref": f"query-scope-{account_index}",
            "endpoint": "tcp://127.0.0.1:31902",
            "security_ref": f"execution-security-{account_index}",
        },
    )


def _coordinator(strategy_index: int = 0, account_index: int = 0):
    return {
        "service_id": "n5-group-coordinator",
        "instance_id": "n5-group-coordinator-instance",
        "endpoint": "tcp://127.0.0.1:31903",
        "security_ref": "n5-group-security",
        "server_public": "synthetic-public-key",
        "peer": object(),
        "strategy_bindings": {
            strategy_index: {
                "scope_ref": f"load01-n5-group-scope-a{account_index:02d}-v1",
                "portfolio_ref": "load01-n5-portfolio-v1",
                "origin_strategy_run_id": f"load01-n5-owner-run-a{account_index:02d}-v1",
            }
        },
    }


def _filled_projection_snapshot(
    metadata: tuple[InstrumentRiskMetadata, ...],
    *,
    account_ref: str,
    strategy_ref: str,
    group_id: str,
    deadline_ns: int,
):
    scope = ExecutionScope(
        "synthetic", "paper", account_ref, strategy_ref, "2026-10-18"
    )
    plans = []
    progress = []
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
            tags={runner.INSTRUMENT_METADATA_DIGEST_TAG: item.digest},
        )
        plans.append(
            SimpleNamespace(
                leg_id=spec.leg_id,
                account_ref=account_ref,
                ratio=spec.ratio,
                maximum_intent=intent,
                quantity_step=item.quantity_step,
                base_units_per_order_unit=dict(N5_BASE_UNIT_SCALES)[spec.instrument],
                metadata_valid_until_ns=item.expires_at_ns,
            )
        )
        progress.append(
            SimpleNamespace(
                leg_id=spec.leg_id,
                submitted_intent=intent,
                child_status=SimpleNamespace(
                    intent_id=intent.intent_id,
                    state=ExecutionState.FILLED,
                    order_revision=1,
                    trade_count=1,
                ),
                phase=GroupLegPhase.OBSERVED,
            )
        )

    request = SimpleNamespace(
        group_id=group_id,
        version=3,
        portfolio_ref="load01-n5-portfolio-v1",
        strategy_id=strategy_ref,
        account_refs=(account_ref,),
        first_leg_id=N5_LEG_SPECS[0].leg_id,
        legs=tuple(plans),
    )
    snapshot = SimpleNamespace(
        request=request,
        state=runner.GroupOrderState.PAIRED,
        revision=1,
        legs=tuple(progress),
        created_at_ns=deadline_ns - 1_000_000,
        updated_at_ns=deadline_ns,
    )
    return snapshot, progress


def _freeze_wire_json(value):
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_wire_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_wire_json(item) for item in value)
    return value


def test_n5_full_schedule_separates_groups_from_ordinary_order_rpcs():
    profile = load01_profile("N5")
    strategy_count = len(profile.accounts_by_strategy)
    assert profile.strategy_indices_by_account == (tuple(range(5)), tuple(range(5, 10)))
    assert profile.expected_group_capable_strategies == (0, 5)
    assert profile.order_targets_per_account_per_second == pytest.approx((2.0, 2.0))
    assert profile.group_targets_per_account_per_second == (0.1, 0.1)
    assert profile.child_dispatch_targets_per_account_per_second == pytest.approx((2.2, 2.2))
    for account_indices in profile.strategy_indices_by_account:
        designated, *ordinary = account_indices
        assert profile.order_interval_seconds_by_strategy[designated] == pytest.approx(5.0)
        assert profile.order_interval_seconds_by_strategy[designated] ** -1 == pytest.approx(0.2)
        assert all(
            profile.order_interval_seconds_by_strategy[index] ** -1 == pytest.approx(0.45)
            for index in ordinary
        )

    order_totals = {"orders.submit": 0, "orders.query": 0, "orders.cancel": 0}
    group_totals = {"groups.submit": 0, "groups.query": 0}
    child_total = 0
    for index in range(strategy_count):
        order_totals_for_strategy = runner._load01_expected_order_operation_counts(
            profile, index, 1800
        )
        group_totals_for_strategy = runner._load01_expected_group_operation_counts(
            profile, index, 1800
        )
        for operation in order_totals:
            order_totals[operation] += order_totals_for_strategy[operation]
        for operation in group_totals:
            group_totals[operation] += group_totals_for_strategy[operation]
        child_total += group_totals_for_strategy["group_child_dispatch_count"]

    assert runner._load01_expected_order_operation_counts(profile, 0, 1800) == {
        "orders.submit": 180,
        "orders.query": 0,
        "orders.cancel": 180,
    }
    assert runner._load01_expected_group_operation_counts(profile, 0, 1800) == {
        "groups.submit": 180,
        "groups.query": 180,
        "group_logical_count": 180,
        "group_child_dispatch_count": 540,
        "group_logical_count_by_account": {runner._ACCOUNT_REFS[0]: 180},
        "group_child_dispatch_count_by_account": {runner._ACCOUNT_REFS[0]: 540},
        "group_child_dispatch_count_by_leg": {
            "leg-put": 180,
            "leg-future": 180,
            "leg-call": 180,
        },
    }
    assert runner._load01_expected_order_operation_counts(profile, 1, 1800) == {
        "orders.submit": 810,
        "orders.query": 0,
        "orders.cancel": 810,
    }
    assert runner._load01_expected_group_operation_counts(profile, 1, 1800)[
        "group_logical_count"
    ] == 0
    assert runner._load01_expected_group_operation_counts(profile, 1, 1800)[
        "group_logical_count_by_account"
    ] == {runner._ACCOUNT_REFS[0]: 0}
    assert runner._load01_expected_group_operation_counts(profile, 1, 1800)[
        "group_child_dispatch_count_by_leg"
    ] == {"leg-put": 0, "leg-future": 0, "leg-call": 0}
    assert order_totals == {
        "orders.submit": 6_840,
        "orders.query": 0,
        "orders.cancel": 6_840,
    }
    assert group_totals == {"groups.submit": 360, "groups.query": 360}
    assert child_total == 1_080


def test_n5_strategy_config_mounts_only_its_portfolio_scoped_group_client():
    account_service, execution_service = _services()
    config = runner._strategy_config(
        0,
        account_service,
        execution_service,
        "tcp://127.0.0.1:31911",
        "tcp://127.0.0.1:31912",
        "tcp://127.0.0.1:31913",
        account_index=0,
        profile_id="N5",
        coordinator_service=_coordinator(),
    )
    group_bindings = [
        row
        for row in config["clients"]["execution"]["settings"]["bindings"]
        if row["mount"] == "broker.order_groups"
    ]
    assert len(group_bindings) == 1
    assert group_bindings[0] == {
        "mount": "broker.order_groups",
        "service_id": "n5-group-coordinator",
        "instance_id": "n5-group-coordinator-instance",
        "scope_ref": "load01-n5-group-scope-a00-v1",
        "portfolio_ref": "load01-n5-portfolio-v1",
        "transport_ref": "load01-n5-group-client-000-v1",
    }
    assert "account_ref" not in group_bindings[0]
    assert "broker.order_groups" not in config["clients"]["gateway"]
    runner.parse_client_config(config)


def test_n5_ordinary_strategy_has_no_group_binding():
    account_service, execution_service = _services()
    config = runner._strategy_config(
        1,
        account_service,
        execution_service,
        "tcp://127.0.0.1:31911",
        "tcp://127.0.0.1:31912",
        "tcp://127.0.0.1:31913",
        account_index=0,
        profile_id="N5",
    )
    bindings = config["clients"]["execution"]["settings"]["bindings"]
    assert all(row["mount"] != "broker.order_groups" for row in bindings)
    runner.parse_client_config(config)


def test_n5_metadata_validation_uses_parent_typed_native_rows():
    metadata = _metadata()
    assert runner._load01_validate_n5_group_metadata(metadata) is metadata
    bad_metadata = (replace(metadata[0], quantity_unit="contracts"), *metadata[1:])
    with pytest.raises(ValueError, match="fixed synthetic template"):
        runner._load01_validate_n5_group_metadata(bad_metadata)


def test_group_rpc_percentiles_and_timeouts_remain_separate_from_order_rpcs():
    collector = runner._Load01N5GroupRpcMetrics()
    collector.record("groups.submit", 100_000_000, "success")
    collector.record("groups.query", 3_000_000, "timeout")
    report = runner._load01_n5_group_rpc_metrics_report(collector)

    assert set(report["operations"]) == {"groups.submit", "groups.query"}
    assert report["operations"]["groups.submit"]["observed_count"] == 1
    assert report["operations"]["groups.submit"]["p50_ms"] == 100.0
    assert report["operations"]["groups.query"]["timeout_count"] == 1
    assert report["operations"]["groups.query"]["p95_ms"] == 3.0
    assert report["timeout_count_total"] == 1
    assert report["thresholds_are_engineering_targets_not_user_sla"] is True
    registration, _, _registration_raw = runner._load01_read_threshold_registration(
        Path(runner.__file__).resolve().parents[2],
        {Path(runner._LOAD01_THRESHOLD_REGISTRATION_PATH).as_posix(): runner._LOAD01_THRESHOLD_REGISTRATION_SHA256},
    )
    registered_targets = registration["engineering_targets"]["latency"][
        "n5_group_rpc_ms_each_operation"
    ]
    assert registered_targets["observational_percentiles"] == ["p50", "p95"]
    expected_targets = {
        key: registered_targets[key]
        for key in (
            "observational_percentiles", "p99_max",
            "single_call_timeout_ms", "unexpected_timeout_count_max",
        )
    }
    assert report["engineering_targets_ms"] == {
        operation: expected_targets
        for operation in ("groups.submit", "groups.query")
    }


def test_n5_coordinator_binding_requires_matching_origin_run_id():
    selected = runner._load01_n5_group_service_binding(
        _coordinator(), strategy_index=0, account_index=0
    )
    assert selected == {
        "scope_ref": "load01-n5-group-scope-a00-v1",
        "portfolio_ref": "load01-n5-portfolio-v1",
        "owner_run_id": "load01-n5-owner-run-a00-v1",
    }
    invalid = _coordinator()
    invalid["strategy_bindings"][0]["origin_strategy_run_id"] = "random-run"
    with pytest.raises(ValueError, match="owner run binding"):
        runner._load01_n5_group_service_binding(
            invalid, strategy_index=0, account_index=0
        )


def test_n5_durable_query_requires_observed_filled_children_and_exact_ids(monkeypatch):
    metadata = _metadata()
    account_ref = "load01-account-00"
    strategy_ref = "strategy-000"
    group_id = "load01-n5-group-a00-s000-c0000001"
    deadline_ns = time.time_ns() + 30_000_000_000
    scope = ExecutionScope(
        "synthetic", "paper", account_ref, strategy_ref, "2026-10-18"
    )
    plans = []
    progress = []
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
            tags={runner.INSTRUMENT_METADATA_DIGEST_TAG: item.digest},
        )
        plans.append(
            SimpleNamespace(
                leg_id=spec.leg_id,
                account_ref=account_ref,
                ratio=spec.ratio,
                maximum_intent=intent,
                quantity_step=item.quantity_step,
                base_units_per_order_unit=dict(N5_BASE_UNIT_SCALES)[spec.instrument],
                metadata_valid_until_ns=item.expires_at_ns,
            )
        )
        progress.append(
            SimpleNamespace(
                leg_id=spec.leg_id,
                submitted_intent=intent,
                child_status=SimpleNamespace(
                    intent_id=intent.intent_id,
                    state=ExecutionState.FILLED,
                    order_revision=1,
                    trade_count=1,
                ),
                phase=GroupLegPhase.OBSERVED,
            )
        )

    request = SimpleNamespace(
        group_id=group_id,
        version=3,
        portfolio_ref="load01-n5-portfolio-v1",
        strategy_id=strategy_ref,
        account_refs=(account_ref,),
        first_leg_id=N5_LEG_SPECS[0].leg_id,
        legs=tuple(plans),
    )
    snapshot = SimpleNamespace(
        request=request,
        state=runner.GroupOrderState.PAIRED,
        revision=1,
        legs=tuple(progress),
        created_at_ns=deadline_ns - 1_000_000,
        updated_at_ns=deadline_ns,
    )
    monkeypatch.setattr(
        runner.GroupOrderSnapshot,
        "from_payload",
        staticmethod(lambda _payload: snapshot),
    )
    query_result = {"ok": True, "code": "OK", "result": {"found": True, "snapshot": {}}}

    facts = runner._load01_n5_group_query_facts(
        query_result,
        group_id=group_id,
        portfolio_ref="load01-n5-portfolio-v1",
        strategy_ref=strategy_ref,
        account_ref=account_ref,
        metadata=metadata,
    )
    assert facts["query_found"] is True
    assert facts["state"] == "PAIRED"
    assert facts["leg_intent_ids"] == {
        spec.leg_id: group_leg_intent_id(group_id, spec.leg_id)
        for spec in N5_LEG_SPECS
    }
    assert {fact["phase"] for fact in facts["legs"]} == {"OBSERVED"}
    assert {fact["execution_state"] for fact in facts["legs"]} == {"FILLED"}
    assert {
        fact["distinct_durable_filled_child_intent_count"]
        for fact in facts["legs"]
    } == {1}
    assert {
        fact["distinct_durable_filled_child_intent_count_source"]
        for fact in facts["legs"]
    } == {"group_query_snapshot"}
    assert {fact["provider_write_count"] for fact in facts["legs"]} == {None}
    assert {
        fact["provider_write_count_status"] for fact in facts["legs"]
    } == {"NOT_REGISTERED_FROM_OWNER_LOG"}

    progress[0].child_status = SimpleNamespace(
        intent_id=progress[0].submitted_intent.intent_id,
        state=ExecutionState.UNKNOWN,
        order_revision=2,
        trade_count=0,
    )
    with pytest.raises(AssertionError, match="child dispatch is unresolved"):
        runner._load01_n5_group_query_facts(
            query_result,
            group_id=group_id,
            portfolio_ref="load01-n5-portfolio-v1",
            strategy_ref=strategy_ref,
            account_ref=account_ref,
            metadata=metadata,
        )


def test_n5_group_query_facts_thaws_frozen_wire_collections(monkeypatch):
    metadata = _metadata()
    account_ref = "load01-account-00"
    strategy_ref = "strategy-000"
    group_id = "load01-n5-group-a00-s000-c0000002"
    deadline_ns = time.time_ns() + 30_000_000_000
    snapshot, _progress = _filled_projection_snapshot(
        metadata,
        account_ref=account_ref,
        strategy_ref=strategy_ref,
        group_id=group_id,
        deadline_ns=deadline_ns,
    )
    frozen_snapshot = _freeze_wire_json(
        {
            "request": {
                "legs": [
                    {
                        "intent": {
                            "tags": {
                                runner.INSTRUMENT_METADATA_DIGEST_TAG: "a" * 64
                            }
                        }
                    }
                ]
            },
            "state": "PAIRED",
            "revision": 1,
            "reservations": [],
            "legs": [
                {
                    "submitted_intent": {
                        "tags": {
                            runner.INSTRUMENT_METADATA_DIGEST_TAG: "a" * 64
                        }
                    }
                }
            ],
            "residual_base_units": None,
            "error_code": None,
            "created_at_ns": 1,
            "updated_at_ns": 1,
            "budget_reservation": {},
            "residual_vector": ["0", "0", "0"],
        }
    )
    with pytest.raises(
        ContractValidationError, match="invalid group snapshot collections"
    ):
        runner.GroupOrderSnapshot.from_payload(frozen_snapshot)

    decoded_containers = {}

    def decode_thawed_snapshot(payload):
        assert type(payload) is dict
        assert type(payload["request"]) is dict
        assert type(payload["request"]["legs"]) is list
        assert type(payload["request"]["legs"][0]) is dict
        assert type(payload["request"]["legs"][0]["intent"]["tags"]) is dict
        assert type(payload["reservations"]) is list
        assert type(payload["legs"]) is list
        assert type(payload["legs"][0]["submitted_intent"]["tags"]) is dict
        assert type(payload["residual_vector"]) is list
        decoded_containers["snapshot"] = payload
        return snapshot

    monkeypatch.setattr(
        runner.GroupOrderSnapshot,
        "from_payload",
        staticmethod(decode_thawed_snapshot),
    )
    query_result = _freeze_wire_json(
        {
            "ok": True,
            "code": "OK",
            "result": {"found": True, "snapshot": frozen_snapshot},
        }
    )

    facts = runner._load01_n5_group_query_facts(
        query_result,
        group_id=group_id,
        portfolio_ref="load01-n5-portfolio-v1",
        strategy_ref=strategy_ref,
        account_ref=account_ref,
        metadata=metadata,
    )

    assert decoded_containers["snapshot"]["residual_vector"] == ["0", "0", "0"]
    assert facts["query_found"] is True
    assert facts["state"] == "PAIRED"
    assert len(facts["legs"]) == 3


def test_n5_strategy_report_exposes_durable_rows_and_zero_non_group_routes():
    legs = tuple(spec.leg_id for spec in N5_LEG_SPECS)
    capable_state = {
        "group_logical_count": 180,
        "group_success_count": 180,
        "group_failure_count": 0,
        "group_unresolved_count": 0,
        "group_logical_by_account": Counter({0: 180}),
        "group_child_dispatch_count": 540,
        "group_child_dispatch_by_account": Counter({0: 540}),
        "group_child_dispatch_count_by_leg": Counter({leg_id: 180 for leg_id in legs}),
        "group_child_outcome_by_leg": {
            leg_id: Counter({
                "READY": 0,
                "REQUESTING": 0,
                "UNKNOWN": 0,
                "OBSERVED": 180,
            })
            for leg_id in legs
        },
        "group_child_execution_state_by_leg": {
            leg_id: Counter({"FILLED": 180, "UNKNOWN": 0, "DUPLICATE": 0, "UNRESOLVED": 0})
            for leg_id in legs
        },
        "group_child_intent_ids_by_leg": {
            leg_id: [f"intent-{leg_id}-{index:03d}" for index in range(180)]
            for leg_id in legs
        },
        "group_durable_records": [
            {
                "query_found": True,
                "group_id": f"group-{index:03d}",
                "version": 3,
                "revision": 4,
                "state": "PAIRED",
                "portfolio_ref": "load01-n5-portfolio-v1",
                "strategy_ref": "strategy-000",
                "created_at_ns": index + 1,
                "updated_at_ns": index + 2,
                "leg_intent_ids": {
                    leg_id: f"intent-{leg_id}-{index:03d}" for leg_id in legs
                },
            }
            for index in range(180)
        ],
        "group_child_dispatch_latency_ns_by_account_strategy": None,
        "group_child_dispatch_latency_status": "NOT_REGISTERED_BY_COORDINATOR_SNAPSHOT",
        "group_lifecycle_samples_ms": [1.25, 2.5],
        "rpc_collector": None,
    }

    def stats_for(index, group_state):
        return runner._strategy_stats(
            index=index,
            market_receives={},
            last_sequences={},
            last_epochs={},
            market_delivery_receipts={},
            order_cycles=0,
            account_reads=0,
            e2e_samples=[],
            order_samples=[],
            order_rpc_metrics=None,
            market_e2e_due_count=0,
            market_e2e_valid_count=0,
            account_samples=[],
            duplicate_events=0,
            unrecovered_gap_count=0,
            unrecovered_gap_events=0,
            reconnect_gap_events=0,
            reconnect_records=[],
            reconnect_gaps_by_stream={},
            slow_pause_observed=False,
            market_stream_ids=(),
            market_recovery_observations=[],
            telemetry={},
            telemetry_queue_drops=0,
            telemetry_delivery_drops=0,
            telemetry_delivered=0,
            max_queue_count=0,
            max_queue_bytes=0,
            market_queue_snapshot={},
            max_market_poll_ms=0.0,
            account_identity={},
            account_identities={0: {}},
            order_cycles_by_account=Counter(),
            account_reads_by_account=Counter(),
            runtime=SimpleNamespace(
                state="closed",
                cleanup_failures=0,
                unregister_uncertainties=0,
                bindings={},
            ),
            profile_id="N5",
            n5_group_metrics_state=group_state,
            planned_duration_seconds=1800,
        )

    metrics = stats_for(0, capable_state)["n5_group_metrics"]
    assert metrics["expected"]["groups.submit"] == 180
    assert metrics["expected"]["groups.query"] == 180
    assert metrics["observed"]["group_success_count"] == 180
    assert metrics["group_durable_record_count"] == 180
    assert metrics["group_logical_count_by_account"] == {
        runner._ACCOUNT_REFS[0]: 180
    }
    assert metrics["group_child_dispatch_count_by_account"] == {
        runner._ACCOUNT_REFS[0]: 540
    }
    assert metrics["group_child_dispatch_count_source"] == (
        "distinct_durable_filled_child_intent_observation_not_provider_write_log"
    )
    assert metrics["observed"]["group_child_dispatch_count_source"] == (
        "distinct_durable_filled_child_intent_observation_not_provider_write_log"
    )
    assert metrics["group_child_provider_write_count"] is None
    assert metrics["group_child_provider_write_count_status"] == (
        "NOT_REGISTERED_FROM_OWNER_LOG"
    )
    assert set(metrics["group_durable_records"][0]) == {
        "query_found",
        "group_id",
        "version",
        "revision",
        "state",
        "portfolio_ref",
        "strategy_ref",
        "created_at_ns",
        "updated_at_ns",
        "leg_intent_ids",
    }
    assert metrics["group_lifecycle_ms"] == {
        "count": 2,
        "p50": 1.25,
        "p95": 1.25,
        "p99": 1.25,
        "max": 2.5,
    }
    for leg_id in legs:
        assert metrics["group_child_outcome_by_leg"][leg_id] == {
            "OBSERVED": 180,
            "READY": 0,
            "REQUESTING": 0,
            "UNKNOWN": 0,
        }
        assert metrics["group_child_execution_state_by_leg"][leg_id]["FILLED"] == 180
        assert metrics["group_child_execution_state_by_leg"][leg_id]["UNKNOWN"] == 0
        assert metrics["group_child_execution_state_by_leg"][leg_id]["DUPLICATE"] == 0
        assert metrics["group_child_execution_state_by_leg"][leg_id]["UNRESOLVED"] == 0
        route = metrics["group_child_routes_by_leg"][leg_id]
        assert route["expected_dispatch_count"] == 180
        assert route["observed_dispatch_count"] == 180
        assert route["observed_dispatch_count_source"] == (
            "distinct_durable_filled_child_intent_observation_not_provider_write_log"
        )
        assert len(route["intent_ids"]) == 180

    ordinary = stats_for(1, None)["n5_group_metrics"]
    assert ordinary["enabled"] is False
    assert ordinary["expected_logical_group_count"] == 0
    assert ordinary["observed_child_dispatch_count"] == 0
    for leg_id in legs:
        assert ordinary["group_child_routes_by_leg"][leg_id]["expected_dispatch_count"] == 0
        assert ordinary["group_child_routes_by_leg"][leg_id]["observed_dispatch_count"] == 0
        assert ordinary["group_child_routes_by_leg"][leg_id]["intent_ids"] == []
        assert ordinary["group_child_outcome_by_leg"][leg_id]["OBSERVED"] == 0
        assert ordinary["group_child_execution_state_by_leg"][leg_id]["UNKNOWN"] == 0
