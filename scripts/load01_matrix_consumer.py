"""Offline LOAD01 matrix threshold evaluator.

This artifact consumes completed runner JSON and an independently supplied
source binding. Its PASS is a local report evaluation, never formal acceptance.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from math import ceil, isfinite
from typing import Any, Mapping

PROFILE_IDS = ("U0", "N1", "N2", "N3", "N4", "N5", "N6")
P0_PROFILE_IDS = ("U0",)
SOURCE_ALIASES = {
    "runner",
    "profiles",
    "metrics_helper",
    "metrics_focused_test",
    "recovery_timing",
    "group_workload",
    "group_coordinator",
    "group_topology",
    "n5_owner_resources",
    "n5_report",
}
SOURCE_ALIAS_MODULES_AND_PATHS = {
    "runner": ("tests.unit.test_load01_hundred_strategy_30min", "tests/unit/test_load01_hundred_strategy_30min.py"),
    "profiles": ("tests.unit._load01_profiles", "tests/unit/_load01_profiles.py"),
    "metrics_helper": ("tests.unit._load01_metrics", "tests/unit/_load01_metrics.py"),
    "metrics_focused_test": ("tests.unit.test_load01_runner_metrics", "tests/unit/test_load01_runner_metrics.py"),
    "recovery_timing": ("tests.unit._load01_recovery_timing", "tests/unit/_load01_recovery_timing.py"),
    "group_workload": ("tests.unit._load01_group_workload", "tests/unit/_load01_group_workload.py"),
    "group_coordinator": ("tests.unit._load01_group_coordinator", "tests/unit/_load01_group_coordinator.py"),
    "group_topology": ("tests.unit._load01_group_topology", "tests/unit/_load01_group_topology.py"),
    "n5_owner_resources": ("tests.unit._load01_n5_owner_resources", "tests/unit/_load01_n5_owner_resources.py"),
    "n5_report": ("tests.unit._load01_n5_report", "tests/unit/_load01_n5_report.py"),
}
SOURCE_ALIAS_REPORT_KEYS = {
    "runner": "load_runner",
    "profiles": "tests.unit._load01_profiles",
    "metrics_helper": "tests.unit._load01_metrics",
    "metrics_focused_test": "load_metrics_tests",
    "recovery_timing": "tests.unit._load01_recovery_timing",
    "group_workload": "tests.unit._load01_group_workload",
    "group_coordinator": "tests.unit._load01_group_coordinator",
    "group_topology": "tests.unit._load01_group_topology",
    "n5_owner_resources": "tests.unit._load01_n5_owner_resources",
    "n5_report": "tests.unit._load01_n5_report",
}
SOURCE_BINDING_RECEIPT_REQUIREMENTS = {
    "schema": "load01-root-trusted-source-binding-v1",
    "issuer": "Root",
    "verification_basis": "fixed_source_manifest_and_raw_registration_bytes",
    "required_fields": [
        "status",
        "sdk_head",
        "registration_sha256",
        "source_pins",
        "report_source_bindings",
    ],
    "report_may_supply_receipt": False,
}
MIB = 1024 * 1024


def _number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and isfinite(value)


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _decimal_value(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _map(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _row(mapping: Mapping[Any, Any], index: int) -> Any:
    for key in (str(index), f"strategy-{index:03d}", index):
        if key in mapping:
            return mapping[key]
    return None


def _missing(out: list[str], path: str) -> None:
    out.append(f"missing:{path}")


def _profile(registration: Mapping[str, Any], profile_id: str) -> Mapping[str, Any] | None:
    workload = _map(registration.get("workload_contract"))
    profiles = _map(workload.get("profiles")) if workload else None
    return _map(profiles.get(profile_id)) if profiles else None


def validate_registration(registration: Mapping[str, Any]) -> list[str]:
    """Validate selector invariants and the profile-specific process gates."""
    problems: list[str] = []
    workload = _map(registration.get("workload_contract"))
    consumer = _map(registration.get("matrix_consumer_contract"))
    if workload is None:
        return ["missing:workload_contract"]
    if workload.get("formal_matrix_evaluation_profiles") != list(PROFILE_IDS):
        problems.append("matrix_profiles_must_be_U0_N1_N2_N3_N4_N5_N6")
    if workload.get("p0_full_evaluation_eligible_profiles") != list(P0_PROFILE_IDS):
        problems.append("p0_baseline_must_remain_U0_only")
    if not consumer or consumer.get("profile_matrix_selector_is_independent_of_p0_eligibility") is not True:
        problems.append("matrix_selector_must_ignore_p0_eligibility")
    totals = _map(workload.get("total_child_processes_by_profile"))
    gates = _map(workload.get("minimum_available_memory_before_spawn_bytes_by_profile"))
    profiles = _map(workload.get("profiles"))
    reserve = workload.get("minimum_memory_reserve_bytes")
    if totals is None or gates is None or profiles is None or not _count(reserve):
        return problems + ["missing:profile_topology_memory_registration"]
    source_scope = _map(registration.get("source_scope"))
    required_aliases = _map(source_scope.get("required_runtime_source_aliases")) if source_scope else None
    if required_aliases is None or set(required_aliases) != SOURCE_ALIASES:
        problems.append("missing_or_invalid:required_runtime_source_aliases")
    else:
        for alias, (module, path) in SOURCE_ALIAS_MODULES_AND_PATHS.items():
            declared = _map(required_aliases.get(alias))
            if declared is None or declared.get("module") != module or declared.get("path") != path:
                problems.append(f"wrong:required_runtime_source_alias:{alias}")
    binding_requirements = _map(source_scope.get("external_trusted_binding_requirements")) if source_scope else None
    if binding_requirements != SOURCE_BINDING_RECEIPT_REQUIREMENTS:
        problems.append("missing_or_invalid:external_trusted_binding_requirements")
    if source_scope and "final_source_binding" in source_scope:
        problems.append("invalid:registration_embeds_final_source_binding")
    for profile_id in PROFILE_IDS:
        p = _map(profiles.get(profile_id))
        topology = _map(p.get("process_topology")) if p else None
        if topology is None:
            problems.append(f"missing:profile_topology:{profile_id}")
            continue
        parts = (
            topology.get("strategy_processes"),
            topology.get("account_owner_processes"),
            topology.get("market_hub_processes"),
            topology.get("monitor_processes"),
            topology.get("shared_group_coordinator_processes"),
        )
        expected = totals.get(profile_id)
        gate = gates.get(profile_id)
        if any(not _count(value) for value in parts) or not _count(expected) or not _count(gate):
            problems.append(f"invalid:profile_topology_memory:{profile_id}")
            continue
        count = sum(parts)
        if count != expected or topology.get("total_child_processes") != expected:
            problems.append(f"wrong:child_count:{profile_id}")
        if gate != count * 72 * MIB + reserve or topology.get("required_pre_spawn_available_memory_bytes") != gate:
            problems.append(f"wrong:memory_gate:{profile_id}")
    if totals.get("N5") != 113 or gates.get("N5") != 10_678_697_984:
        problems.append("N5_requires_113_children_and_10678697984_bytes")
    for profile_id in PROFILE_IDS:
        if profile_id != "N5":
            if totals.get(profile_id) != 112:
                problems.append(f"wrong:non_N5_child_count:{profile_id}")
            if gates.get(profile_id) != 10_603_200_512:
                problems.append(f"wrong:non_N5_memory_gate:{profile_id}")
    n5_profile = _map(profiles.get("N5"))
    n5_orders = _map(n5_profile.get("orders")) if n5_profile else None
    n5 = _map(n5_orders.get("n5_group_contract")) if n5_orders else None
    engineering = _map(registration.get("engineering_targets"))
    latency = _map(engineering.get("latency")) if engineering else None
    ordinary_rpc_targets = _map(latency.get("order_rpc_ms_each_operation")) if latency else None
    if ordinary_rpc_targets is None:
        problems.append("missing:ordinary_order_RPC_latency_registration")
    elif (
        ordinary_rpc_targets.get("observational_percentiles"),
        ordinary_rpc_targets.get("p99_max"),
        ordinary_rpc_targets.get("single_call_timeout_ms"),
        ordinary_rpc_targets.get("acceptance_scope"),
        latency.get("ordinary_order_rpc_timeout_count_max"),
    ) != (
        ["p50", "p95"],
        5000,
        5000,
        "USER_SELECTED_SYSTEM_STABILITY_NOT_HFT",
        0,
    ):
        problems.append("wrong:ordinary_order_RPC_stability_targets")
    if latency is None or not isinstance(latency.get("rpc_percentile_scope"), str) or not latency.get("rpc_percentile_scope"):
        problems.append("missing:RPC_percentile_acceptance_scope")
    market_latency_targets = _map(latency.get("market_end_to_end_ms")) if latency else None
    if market_latency_targets is None:
        problems.append("missing:market_event_latency_registration")
    elif (
        market_latency_targets.get("observational_percentiles") != ["p50", "p95", "p99"]
        or market_latency_targets.get("acceptance_scope")
        != "OBSERVATION_ONLY_NO_LOW_LATENCY_GATE"
        or any(
            name in market_latency_targets
            for name in ("p50_max", "p95_max", "p99_max")
        )
    ):
        problems.append("wrong:market_event_latency_targets")
    targets = _map(latency.get("n5_group_rpc_ms_each_operation")) if latency else None
    if n5 is None or targets is None:
        problems.append("missing:N5_group_rpc_registration")
    else:
        capable = n5.get("group_capable_strategy_indices")
        if not isinstance(capable, list) or any(type(index) is not int or not 0 <= index < 100 for index in capable) or len(set(capable)) != len(capable):
            problems.append("invalid:N5_group_capable_strategy_indices")
        elif capable != list(range(0, 100, 10)):
            problems.append("wrong:N5_group_capable_strategy_strata")
        if not isinstance(capable, list) or n5.get("group_capable_strategies") != len(capable):
            problems.append("wrong:N5_group_capable_strategy_count")
        expected_source_identity_by_report_account = {
            f"account-{account_index:02d}": {
                "account_ref": f"gw02-account-{account_index:02d}",
                "group_strategy_index": account_index * 10,
                "group_strategy_ref": f"load01-strategy-{account_index * 10:03d}",
            }
            for account_index in range(10)
        }
        if n5.get("source_identity_by_report_account") != expected_source_identity_by_report_account:
            problems.append("wrong:N5_group_source_identity_by_report_account")
        if n5.get("group_legs_per_group") != 3 or n5.get("group_ratio_of_logical_cycles") != 0.05:
            problems.append("wrong:N5_group_shape")
        per_account = _map(n5.get("per_account_1800_second_targets"))
        whole = _map(n5.get("whole_profile_1800_second_targets"))
        if per_account is None or whole is None:
            problems.append("missing:N5_group_volume_registration")
        else:
            exact_per_account = {"group_requests": 180, "single_orders": 3420, "group_child_dispatches": 540, "total_child_dispatches": 3960}
            exact_whole = {"group_requests": 1800, "single_orders": 34200, "group_child_dispatches": 5400, "total_child_dispatches": 39600}
            if any(per_account.get(key) != value for key, value in exact_per_account.items()) or any(whole.get(key) != value for key, value in exact_whole.items()):
                problems.append("wrong:N5_group_volume_targets")
            if whole.get("group_requests") != per_account.get("group_requests") * 10 or whole.get("group_child_dispatches") != per_account.get("group_child_dispatches") * 10:
                problems.append("wrong:N5_group_account_to_whole_totals")
        if targets.get("operations") != ["groups.submit", "groups.query"]:
            problems.append("wrong:N5_group_RPC_operations")
        if (
            targets.get("observational_percentiles"),
            targets.get("p99_max"),
            targets.get("single_call_timeout_ms"),
            targets.get("unexpected_timeout_count_max"),
            targets.get("registration_status"),
        ) != (
            ["p50", "p95"],
            5000,
            5000,
            0,
            "USER_SELECTED_SYSTEM_STABILITY_NOT_HFT",
        ):
            problems.append("wrong:N5_group_RPC_targets")
        provider = _map(n5.get("provider_write_evidence"))
        if provider is None:
            problems.append("missing:N5_group_provider_write_registration")
        else:
            expected_accounts = [f"account-{index:02d}" for index in range(10)]
            expected_strategy_map = {account: index * 10 for index, account in enumerate(expected_accounts)}
            if provider.get("report_path") != "accounts_and_execution.n5_group_metrics.provider_ledger_by_account[account_ref]":
                problems.append("wrong:N5_group_provider_write_report_path")
            if provider.get("source_path") != "close_facts.n5_group_runtime.provider_ledger":
                problems.append("wrong:N5_group_provider_write_source_path")
            if provider.get("attribution") != "n5_group_proof_runtime_only":
                problems.append("wrong:N5_group_provider_write_attribution")
            if provider.get("account_refs") != expected_accounts or provider.get("group_strategy_index_by_account") != expected_strategy_map:
                problems.append("wrong:N5_group_provider_account_strategy_map")
            if (
                provider.get("expected_group_child_make_order_writes_per_account") != 540
                or provider.get("expected_unique_client_order_ids_per_account") != 540
                or provider.get("maximum_duplicate_client_order_ids_per_account") != 0
                or provider.get("expected_group_child_make_order_writes_whole_profile") != 5400
            ):
                problems.append("wrong:N5_group_provider_write_targets")
            required_provider_fields = {
                "attribution",
                "account_ref",
                "strategy_ref",
                "strategy_index",
                "make_order_write_count",
                "provider_order_count",
                "unique_client_order_id_count",
                "duplicate_client_order_id_count",
                "writes_by_client_order_id",
                "write_without_order_record_count",
                "order_record_without_write_count",
                "unjoined_write_count",
                "unjoined_make_order_write_count",
                "unjoined_client_order_ids",
                "duplicate_join_client_order_id_count",
                "duplicate_join_client_order_ids",
                "unjoined_query_count",
                "order_query_count",
                "fill_query_count",
                "order_status_counts",
            }
            if set(provider.get("required_fields", [])) != required_provider_fields:
                problems.append("wrong:N5_group_provider_required_fields")
            if set(provider.get("required_zero_anomaly_fields", [])) != {
                "duplicate_client_order_id_count", "write_without_order_record_count",
                "order_record_without_write_count", "unjoined_write_count",
                "unjoined_make_order_write_count", "duplicate_join_client_order_id_count",
                "unjoined_query_count",
            }:
                problems.append("wrong:N5_group_provider_zero_anomaly_registration")
            if set(provider.get("required_zero_anomaly_list_fields", [])) != {
                "unjoined_client_order_ids", "duplicate_join_client_order_ids",
            }:
                problems.append("wrong:N5_group_provider_zero_anomaly_list_registration")
            if set(provider.get("required_order_row_fields", [])) != {
                "client_order_id", "write_count", "order_status", "quantity", "filled",
            }:
                problems.append("wrong:N5_group_provider_order_row_fields")
            join = _map(provider.get("intent_order_join"))
            if join is None:
                problems.append("missing:N5_group_intent_order_join_registration")
            else:
                if join.get("report_path") != "accounts_and_execution.n5_group_metrics.intent_order_join_by_account[account_ref]":
                    problems.append("wrong:N5_group_intent_order_join_report_path")
                if join.get("source_path") != "close_facts.n5_group_runtime.intent_order_join":
                    problems.append("wrong:N5_group_intent_order_join_source_path")
                if join.get("owner_count_report_path") != "accounts_and_execution.n5_group_metrics.owner_join_counts_by_account[account_ref]":
                    problems.append("wrong:N5_group_intent_order_join_count_path")
                if join.get("rows_per_account") != 540 or join.get("joined_rows_per_account") != 540:
                    problems.append("wrong:N5_group_intent_order_join_targets")
                if join.get("required_join_state") != "joined" or join.get("required_provider_order_status") != "filled":
                    problems.append("wrong:N5_group_intent_order_join_states")
                quantities = _map(join.get("required_quantity_by_leg"))
                if quantities != {"leg-put": "2", "leg-future": "8", "leg-call": "4"}:
                    problems.append("wrong:N5_group_intent_order_join_quantities")
                if join.get("quantity_source") != "tests.unit._load01_group_workload.N5_LEG_SPECS":
                    problems.append("wrong:N5_group_intent_order_join_quantity_source")
                if join.get("minimum_order_query_count_per_join_row") != 1 or join.get("minimum_fill_query_count_per_join_row") != 1:
                    problems.append("wrong:N5_group_intent_order_join_query_minima")
                if set(join.get("required_zero_anomalies", [])) != {
                    "attempt_missing", "duplicate_attempt_started", "invalid_attempt_binding",
                    "missing_provider_write", "duplicate_provider_write", "missing_provider_order_record",
                }:
                    problems.append("wrong:N5_group_intent_order_join_zero_anomalies")
                if set(join.get("required_fields", [])) != {
                    "group_id", "leg_id", "intent_id", "attempt_started_count",
                    "attempt_started_revision", "attempt_started_record_sha256",
                    "attempt_binding_valid", "client_order_id", "provider_write_count",
                    "provider_order_row_present", "order_status", "order_query_count",
                    "fill_query_count", "join_state",
                }:
                    problems.append("wrong:N5_group_intent_order_join_required_fields")
        observational = _map(n5.get("observational_only_fields"))
        if observational is None or observational.get("group_child_dispatch_latency_ns_by_account_strategy", "missing") is not None or observational.get("group_child_dispatch_latency_status") is None:
            problems.append("N5_child_leg_latency_must_remain_observational")
    return problems


def _source_binding_issues(
    registration: Mapping[str, Any],
    report: Mapping[str, Any],
    trusted: Mapping[str, Any] | None,
) -> list[str]:
    """Compare the report with an independently supplied Root receipt.

    The receipt is produced from the fixed source export and raw registration
    bytes. Report fields are comparison targets only and never create trust.
    """
    issues: list[str] = []
    scope = _map(registration.get("source_scope"))
    requirements = _map(scope.get("external_trusted_binding_requirements")) if scope else None
    if requirements != SOURCE_BINDING_RECEIPT_REQUIREMENTS:
        issues.append("incomplete:external_trusted_binding_requirements")
    # Compatibility for old local fixture inputs only: this field is not used
    # as an authority. A pending legacy placeholder remains fail-closed.
    legacy_final = _map(scope.get("final_source_binding")) if scope else None
    if legacy_final is not None:
        if legacy_final.get("status") != "BOUND":
            issues.append("incomplete:registration_source_binding_not_final")
        if "registration_sha256" in legacy_final:
            issues.append("invalid:registration_source_binding_contains_self_hash")
    if not isinstance(trusted, Mapping):
        return ["missing:external_trusted_source_binding"]
    if trusted.get("status") != "BOUND":
        issues.append("incomplete:external_trusted_source_binding_not_bound")
    candidate = trusted.get("registration_sha256")
    head = trusted.get("sdk_head")
    pins = trusted.get("source_pins")
    report_pins = trusted.get("report_source_bindings")
    required_aliases = _map(scope.get("required_runtime_source_aliases")) if scope else None
    if not isinstance(candidate, str) or len(candidate) != 64 or any(ch not in "0123456789abcdef" for ch in candidate):
        issues.append("incomplete:trusted_binding_registration_sha256_invalid")
    if not isinstance(head, str) or len(head) not in (40, 64) or any(ch not in "0123456789abcdef" for ch in head):
        issues.append("incomplete:trusted_binding_sdk_head_invalid")
    if not isinstance(pins, Mapping):
        issues.append("incomplete:trusted_binding_source_pins_missing")
    if not isinstance(report_pins, Mapping):
        issues.append("incomplete:trusted_binding_report_source_bindings_missing")
    if required_aliases is None or set(required_aliases) != SOURCE_ALIASES:
        issues.append("incomplete:registration_required_source_aliases_invalid")
    if isinstance(pins, Mapping) and set(pins) != SOURCE_ALIASES:
        issues.append("incomplete:trusted_source_alias_set_mismatch")
    if isinstance(head, str) and report.get("fixed_parent_commit") != head:
        issues.append("incomplete:report_head_binding_mismatch")
    actual_report_pins = _map(report.get("source_bindings"))
    if actual_report_pins != report_pins:
        issues.append("incomplete:report_source_bindings_mismatch")
    metrics = _map(report.get("registered_performance_metrics"))
    reg_pin = _map(metrics.get("registration")) if metrics else None
    if reg_pin is None or reg_pin.get("sha256") != candidate:
        issues.append("incomplete:report_registration_pin_mismatch")
    if isinstance(pins, Mapping) and isinstance(report_pins, Mapping):
        required_report_keys = {SOURCE_ALIAS_REPORT_KEYS[alias] for alias in SOURCE_ALIASES}
        if not required_report_keys.issubset(report_pins):
            issues.append("incomplete:trusted_report_required_source_keys_missing")
        for alias, (_, expected_path) in SOURCE_ALIAS_MODULES_AND_PATHS.items():
            registered = _map(required_aliases.get(alias)) if required_aliases else None
            pinned = _map(pins.get(alias))
            report_key = SOURCE_ALIAS_REPORT_KEYS[alias]
            expected_report = _map(report_pins.get(report_key))
            actual = _map(actual_report_pins.get(report_key)) if actual_report_pins else None
            if registered is None or registered.get("path") != expected_path:
                issues.append(f"incomplete:registration_source_alias_path_mismatch:{alias}")
            for label, item in (("pin", pinned), ("trusted_report", expected_report), ("report", actual)):
                if item is None or item.get("path") != expected_path:
                    issues.append(f"incomplete:{label}_source_path_mismatch:{alias}")
                    continue
                digest = item.get("sha256")
                if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                    issues.append(f"incomplete:{label}_source_sha256_invalid:{alias}")
            if pinned is not None and expected_report is not None and pinned.get("sha256") != expected_report.get("sha256"):
                issues.append(f"incomplete:trusted_report_source_sha256_mismatch:{alias}")
            if pinned is not None and actual is not None and pinned.get("sha256") != actual.get("sha256"):
                issues.append(f"incomplete:report_source_sha256_mismatch:{alias}")
    return issues


def _profile_streams(
    registration: Mapping[str, Any], profile: Mapping[str, Any]
) -> tuple[list[Mapping[str, Any]], dict[int, set[int]]]:
    workload = registration["workload_contract"]
    market = profile["market"]
    streams = workload["market_key_sets"][market["market_key_set_ref"]]
    if len(streams) != market["topic_count"]:
        raise ValueError("market stream count mismatch")
    subscribed: dict[int, set[int]] = {}
    for stratum in profile["orders"]["per_strategy_target_strata"]:
        for index in stratum["strategy_indices"]:
            if type(index) is not int or not 0 <= index < 100 or index in subscribed:
                raise ValueError("strategy strata overlap or are invalid")
            subscribed[index] = set(stratum["subscribed_topic_indices"])
    if set(subscribed) != set(range(100)):
        raise ValueError("strategy topic strata incomplete")
    if any(not 0 <= topic < len(streams) for topics in subscribed.values() for topic in topics):
        raise ValueError("strategy topic index invalid")
    return list(streams), subscribed


def _expected_rpc_counts(profile: Mapping[str, Any], index: int, duration: float) -> dict[str, int]:
    orders = profile["orders"]
    stratum = next(row for row in orders["per_strategy_target_strata"] if index in row["strategy_indices"])
    interval = stratum["order_cycle_interval_seconds"]
    phase = orders["order_phase_seconds_by_strategy"][index]
    cycles = ceil(max(0.0, duration - phase) / interval) if duration > phase else 0
    route_count = sum(index in account for account in orders["account_strategy_indices_by_account"])
    if profile.get("_matrix_profile_id") == "N5":
        single_rate = stratum.get("account_routed_single_order_target_per_second")
        single_cycles = round(single_rate * duration) if _number(single_rate) else cycles
        result = {
            "orders.submit": single_cycles * route_count,
            "orders.query": 0,
            "orders.cancel": single_cycles * route_count,
        }
    else:
        result = {"orders.submit": cycles * route_count, "orders.query": 0, "orders.cancel": cycles * route_count}
    if profile.get("profile_id") == "N4" or profile.get("registration_status") == "N4":
        result["orders.cancel"] = max(0, cycles - 1) * route_count
        result["orders.query"] = max(0, 2 * cycles - 1) * route_count
    # N4 is keyed by its profile id in this registration schema; callers may also
    # pass the outer profile id in the internal marker below.
    if profile.get("_matrix_profile_id") == "N4":
        result["orders.cancel"] = max(0, cycles - 1) * route_count
        result["orders.query"] = max(0, 2 * cycles - 1) * route_count
    return result


def _check_performance(
    registration: Mapping[str, Any],
    profile_id: str,
    profile: Mapping[str, Any],
    report: Mapping[str, Any],
    incomplete: list[str],
    failures: list[str],
) -> None:
    metrics = _map(report.get("registered_performance_metrics"))
    load = _map(report.get("load"))
    if metrics is None or load is None:
        _missing(incomplete, "registered_performance_metrics_or_load")
        return
    if metrics.get("profile_id") != profile_id or metrics.get("mode") != "full":
        incomplete.append("metric_profile_identity_or_mode_mismatch")
    duration = load.get("actual_duration_seconds")
    if not _number(duration) or duration < registration["workload_contract"]["full_duration_seconds"]:
        incomplete.append("full_run_duration_missing_or_short")
        return
    tolerance = registration["engineering_targets"]["goodput"]["tolerance_fraction"]
    market = profile["market"]
    input_rows = _map(metrics.get("input_messages_by_topic"))
    topic_names = {f"K{i:02d}" for i in range(market["topic_count"])}
    if input_rows is None or set(input_rows) != topic_names:
        incomplete.append("input_topic_coverage_incomplete")
    else:
        for index, rate in enumerate(market["input_messages_per_second_by_topic"]):
            row = _map(input_rows.get(f"K{index:02d}"))
            observed = row.get("observed_source_sequence") if row else None
            expected = rate * duration
            if row is None or row.get("target_per_second") != rate or not _count(observed):
                incomplete.append(f"input_topic_invalid:K{index:02d}")
            elif abs(observed - expected) > expected * tolerance:
                failures.append(f"input_topic_outside_band:K{index:02d}")
    try:
        streams, subscriptions = _profile_streams(registration, profile)
    except (KeyError, TypeError, ValueError):
        streams, subscriptions = [], {}
        incomplete.append("profile_market_stream_or_subscription_registration_invalid")
    deliveries = _map(metrics.get("delivered_messages_by_strategy_and_topic"))
    if deliveries is None:
        _missing(incomplete, "delivered_messages_by_strategy_and_topic")
    else:
        for index in range(100):
            row = _map(_row(deliveries, index))
            if row is None:
                incomplete.append(f"delivery_strategy_missing:{index}")
                continue
            expected_topics = subscriptions.get(index, set())
            if set(row) != {f"K{i:02d}" for i in expected_topics}:
                incomplete.append(f"delivery_topic_coverage_mismatch:{index}")
            for topic in expected_topics:
                name = f"K{topic:02d}"
                topic_row = _map(row.get(name))
                observed = topic_row.get("observed_delivery_count") if topic_row else None
                expected_rate = market["input_messages_per_second_by_topic"][topic]
                expected = expected_rate * duration
                if topic_row is None or topic_row.get("target_per_second") != expected_rate or not _count(observed):
                    incomplete.append(f"delivery_measurement_invalid:{index}:{name}")
                elif abs(observed - expected) > expected * tolerance:
                    failures.append(f"delivery_outside_band:{index}:{name}")

    accounts = _map(metrics.get("order_cycles_by_account"))
    if accounts is None:
        _missing(incomplete, "order_cycles_by_account")
    else:
        for account_index, rate in enumerate(profile["orders"]["per_account_logical_order_cycles_per_second"]):
            row = _map(accounts.get(f"account-{account_index:02d}")) or _map(_row(accounts, account_index))
            observed = row.get("observed_order_cycles") if row else None
            expected = rate * duration
            if row is None or not _count(observed):
                incomplete.append(f"account_order_cycles_missing:{account_index}")
            elif abs(observed - expected) > expected * tolerance:
                failures.append(f"account_order_cycles_outside_band:{account_index}")

    rpc = _map(metrics.get("order_rpc_by_strategy"))
    if rpc is None:
        _missing(incomplete, "order_rpc_by_strategy")
    else:
        latency_targets = registration["engineering_targets"]["latency"]["order_rpc_ms_each_operation"]
        timeout_limit = registration["engineering_targets"]["latency"]["ordinary_order_rpc_timeout_count_max"]
        for index in range(100):
            row = _map(_row(rpc, index))
            if row is None:
                incomplete.append(f"order_rpc_strategy_missing:{index}")
                continue
            try:
                expected_ops = _expected_rpc_counts({**profile, "_matrix_profile_id": profile_id}, index, duration)
            except (KeyError, TypeError, ValueError, StopIteration, ZeroDivisionError):
                incomplete.append(f"order_rpc_schedule_invalid:{index}")
                continue
            if set(row) != set(expected_ops):
                incomplete.append(f"order_rpc_operation_coverage_mismatch:{index}")
            for operation, expected in expected_ops.items():
                op = _map(row.get(operation))
                if op is None:
                    incomplete.append(f"order_rpc_missing:{index}:{operation}")
                    continue
                observed, scheduled = op.get("observed_count"), op.get("scheduled_count")
                errors, timeouts = op.get("error_count"), op.get("timeout_count")
                if not all(_count(value) for value in (observed, scheduled, errors, timeouts)):
                    incomplete.append(f"order_rpc_counts_invalid:{index}:{operation}")
                    continue
                if scheduled != expected:
                    incomplete.append(f"order_rpc_schedule_mismatch:{index}:{operation}")
                if expected == 0:
                    if observed:
                        failures.append(f"unregistered_order_rpc_observed:{index}:{operation}")
                    continue
                if abs(observed - expected) > expected * tolerance:
                    failures.append(f"order_rpc_outside_volume_band:{index}:{operation}")
                if errors:
                    failures.append(f"order_rpc_errors:{index}:{operation}")
                if timeouts > timeout_limit:
                    failures.append(f"ordinary_order_rpc_timeout:{index}:{operation}")
                sample_count, capacity = op.get("sampled_count"), op.get("sample_capacity")
                coverage, full = op.get("sample_coverage_fraction"), op.get("full_population_retained")
                if not _count(sample_count) or sample_count == 0 or not _count(capacity) or capacity == 0 or not _number(coverage) or not 0 <= coverage <= 1 or type(full) is not bool:
                    incomplete.append(f"order_rpc_sample_metadata_missing:{index}:{operation}")
                percentiles = _map(op.get("percentiles_ms_nearest_rank"))
                if percentiles is None:
                    incomplete.append(f"order_rpc_percentiles_missing:{index}:{operation}")
                    continue
                for name in ("p50", "p95", "p99"):
                    value = percentiles.get(name)
                    if not _number(value):
                        incomplete.append(f"order_rpc_percentile_invalid:{index}:{operation}:{name}")
                    elif name == "p99" and value > latency_targets["p99_max"]:
                        failures.append(f"order_rpc_latency_exceeded:{index}:{operation}:{name}")

    latencies = _map(metrics.get("market_end_to_end_by_strategy"))
    if latencies is None:
        _missing(incomplete, "market_end_to_end_by_strategy")
    else:
        targets = registration["engineering_targets"]["latency"]["market_end_to_end_ms"]
        for index in range(100):
            row = _map(_row(latencies, index))
            percentiles = _map(row.get("percentiles_ms")) if row else None
            if row is None or row.get("status") != "OBSERVED" or percentiles is None:
                incomplete.append(f"market_latency_missing:{index}")
                continue
            for name in ("p50", "p95", "p99"):
                value = percentiles.get(name)
                if not _number(value):
                    incomplete.append(f"market_latency_invalid:{index}:{name}")


def _check_cpu_resources(registration: Mapping[str, Any], report: Mapping[str, Any], incomplete: list[str], failures: list[str]) -> None:
    metrics = _map(report.get("registered_performance_metrics")) or {}
    cpu = _map(metrics.get("system_cpu"))
    count = registration["workload_contract"]["expected_resource_samples_for_full_run"]
    if cpu is None or cpu.get("status") != "COMPLETE" or cpu.get("schedule_complete") is not True:
        incomplete.append("system_cpu_schedule_incomplete")
    else:
        if cpu.get("sample_count") != count or cpu.get("expected_sample_count") != count or cpu.get("coverage_fraction") != 1:
            incomplete.append("system_cpu_sample_coverage_incomplete")
        targets = registration["engineering_targets"]["cpu"]
        mean, p95 = cpu.get("mean_percent"), cpu.get("p95_percent")
        if not _number(mean) or not _number(p95):
            incomplete.append("system_cpu_summary_missing")
        else:
            if mean > targets["system_mean_percent_max"]:
                failures.append("system_cpu_mean_target_exceeded")
            if p95 > targets["system_p95_percent_max"]:
                failures.append("system_cpu_p95_target_exceeded")
    samples = report.get("resource_samples")
    if not isinstance(samples, list):
        incomplete.append("resource_samples_missing")
    else:
        steady = [row for row in samples if isinstance(row, Mapping) and row.get("load01_cpu_steady_load_sample") is True]
        ticks = [row.get("load01_cpu_sample_tick") for row in steady]
        if len(steady) != count or ticks != list(range(1, count + 1)):
            incomplete.append("resource_sample_rows_or_ticks_incomplete")
        limit = registration["workload_contract"]["database_tree_limit_bytes"]
        for row in steady:
            size = row.get("database_tree_bytes")
            if not _count(size):
                incomplete.append("database_tree_watermark_missing")
                break
            if size > limit:
                failures.append("database_tree_limit_exceeded")
                break
    for name in ("market_queue_snapshot_final", "durable_monitor", "service_registry_sessions_after_close", "normal_shutdown_complete", "process_lifecycle", "children", "cleanup", "preflight", "topology"):
        if name not in report:
            _missing(incomplete, name)
    durable = _map(report.get("durable_monitor"))
    if durable is not None:
        bytes_after = durable.get("sqlite_bytes_after_close")
        limit = registration["workload_contract"]["database_tree_limit_bytes"]
        if not _count(bytes_after):
            incomplete.append("monitor_sqlite_bytes_after_close_missing")
        elif bytes_after > limit:
            failures.append("monitor_sqlite_limit_exceeded")


def _check_topology_shutdown(registration: Mapping[str, Any], profile_id: str, report: Mapping[str, Any], incomplete: list[str], failures: list[str]) -> None:
    workload = registration["workload_contract"]
    expected = workload["total_child_processes_by_profile"][profile_id]
    required_memory = workload["minimum_available_memory_before_spawn_bytes_by_profile"][profile_id]
    topology, preflight, lifecycle = (_map(report.get(key)) for key in ("topology", "preflight", "process_lifecycle"))
    if topology is None or preflight is None or lifecycle is None:
        _missing(incomplete, "topology_preflight_process_lifecycle")
        return
    if topology.get("total_child_processes") != expected:
        failures.append("profile_child_topology_mismatch")
    projected = preflight.get("projected_all_child_rss_bytes")
    required = preflight.get("required_available_for_start_bytes")
    available = preflight.get("available_memory_bytes")
    reserve = workload["minimum_memory_reserve_bytes"]
    if projected != expected * 72 * MIB or required != required_memory or required != projected + reserve:
        failures.append("profile_pre_spawn_memory_gate_mismatch")
    if not _count(available):
        incomplete.append("pre_spawn_available_memory_missing")
    elif available < required_memory:
        failures.append("profile_pre_spawn_memory_gate_not_met")
    if lifecycle.get("expected_child_count") != expected or lifecycle.get("actual_child_count") != expected or lifecycle.get("normal_exit_count") != expected:
        failures.append("process_lifecycle_child_count_mismatch")
    if lifecycle.get("all_child_exit_codes_zero") is not True or lifecycle.get("forced_termination_count") != 0:
        failures.append("process_lifecycle_not_normal")
    children = report.get("children")
    if not isinstance(children, list) or len(children) != expected:
        incomplete.append("child_records_incomplete")
    elif any(not isinstance(row, Mapping) or row.get("normal_exit") is not True or row.get("closed") is not True or row.get("exit_code") != 0 for row in children):
        failures.append("child_record_abnormal_or_unclosed")
    cleanup = _map(report.get("cleanup"))
    if cleanup is None:
        _missing(incomplete, "cleanup")
    elif cleanup.get("errors") != [] or cleanup.get("termination_requests") != []:
        failures.append("cleanup_errors_or_forced_termination")
    if report.get("normal_shutdown_complete") is not True:
        failures.append("normal_shutdown_not_confirmed")
    sessions = report.get("service_registry_sessions_after_close")
    if not isinstance(sessions, Mapping):
        incomplete.append("post_close_registry_sessions_missing")
    elif any(_count(value) and value != 0 for value in sessions.values()):
        failures.append("post_close_registry_sessions_nonzero")


def _check_terminal(registration: Mapping[str, Any], report: Mapping[str, Any], incomplete: list[str], failures: list[str]) -> None:
    terminal = _map(report.get("market_terminal_catchup"))
    if terminal is None:
        _missing(incomplete, "market_terminal_catchup")
        return
    if terminal.get("state") != "complete":
        failures.append("terminal_market_catchup_not_complete")
    elapsed = terminal.get("catchup_elapsed_ms")
    max_ms = registration["engineering_targets"]["recovery"]["publish_stop_to_consumer_catchup_seconds_max"] * 1000
    if not _number(elapsed):
        incomplete.append("terminal_catchup_elapsed_missing")
    elif elapsed > max_ms:
        failures.append("terminal_catchup_deadline_exceeded")
    rows = _map(terminal.get("strategy_results"))
    if rows is None or len(rows) != 100:
        incomplete.append("terminal_strategy_coverage_incomplete")
    else:
        for index in range(100):
            row = _map(_row(rows, index))
            if row is None:
                incomplete.append(f"terminal_strategy_missing:{index}")
            elif row.get("complete") is not True or row.get("failures") not in ([], ()):
                failures.append(f"terminal_strategy_failed:{index}")
    if terminal.get("failure_codes_by_strategy") not in (None, {}):
        failures.append("terminal_failure_codes_nonempty")


def _check_recovery(registration: Mapping[str, Any], profile_id: str, profile: Mapping[str, Any], report: Mapping[str, Any], streams: list[Mapping[str, Any]], subscriptions: dict[int, set[int]], incomplete: list[str], failures: list[str]) -> None:
    market = _map(report.get("market")) or {}
    load = _map(report.get("load")) or {}
    aggregate = _map(market.get("recovery_timing"))
    if aggregate is None:
        aggregate = _map(load.get("market_recovery_timing")) or _map(report.get("recovery_timing"))
    if aggregate is None:
        _missing(incomplete, "market.recovery_timing/load.market_recovery_timing")
        return

    deadline_ms = 10_000
    gap_count = aggregate.get("unrecovered_gap_count")
    if not _count(gap_count):
        incomplete.append("unrecovered_gap_count_unknown")
    elif gap_count:
        failures.append("critical_unrecovered_gap")
    gap_events = aggregate.get("unrecovered_gap_event_count")
    if not _count(gap_events):
        incomplete.append("unrecovered_gap_event_count_unknown")
    elif gap_events:
        failures.append("critical_unrecovered_gap_event")
    if aggregate.get("target_timing_deadline_ms") != deadline_ms:
        incomplete.append("recovery_deadline_not_registered_10s")
    if aggregate.get("target_timing_status") == "EXCEEDED_10S":
        failures.append("recovery_timing_exceeded_10s")
    elif aggregate.get("target_timing_status") != "WITHIN_10S_FOR_ALL_TARGETS":
        incomplete.append("recovery_timing_incomplete")

    incidents = aggregate.get("incidents")
    if not isinstance(incidents, list):
        _missing(incomplete, "recovery_timing.incidents")
        return
    schedule = registration["fault_injection_schedule"]["staggered_market_reconnect_300s_windows"]
    windows = schedule["full_mode_windows_seconds"]
    expected_reconnects = len(windows) * schedule["expected_reconnects_per_window"]
    expected_by_window: dict[int, set[int]] = {}
    for window in range(1, len(windows) + 1):
        chosen: set[int] = set()
        for account in profile["orders"]["account_strategy_indices_by_account"]:
            if account:
                chosen.add(account[(window - 1) % len(account)])
        expected_by_window[window] = chosen
    expected_reconnect_targets = sum(
        len(subscriptions.get(index, set()))
        for indices in expected_by_window.values()
        for index in indices
    )
    expected_slow_targets = len(subscriptions.get(0, set()))
    expected_target_count = expected_reconnect_targets + expected_slow_targets
    slow = [row for row in incidents if isinstance(row, Mapping) and row.get("kind") == "slow_consumer"]
    reconnect = [row for row in incidents if isinstance(row, Mapping) and row.get("kind") == "reconnect"]
    if len(slow) != 1:
        incomplete.append("slow_consumer_incident_count_mismatch")
    if len(reconnect) != expected_reconnects:
        incomplete.append("reconnect_incident_count_mismatch")

    pause = _map(market.get("slow_consumer"))
    pause_start = pause.get("pause_started_seconds") if pause else None
    pause_end = pause.get("pause_ended_seconds") if pause else None
    if not _number(pause_start) or not _number(pause_end):
        incomplete.append("slow_consumer_schedule_anchors_missing")
    elif pause_start < 30 or pause_end < 45 or pause_end - pause_start < 15:
        failures.append("slow_consumer_pause_outside_registered_schedule")

    seen_by_window = {window: set() for window in expected_by_window}
    seen_incidents: set[tuple[Any, ...]] = set()
    for incident in incidents:
        if not isinstance(incident, Mapping):
            incomplete.append("recovery_incident_invalid")
            continue
        kind, index = incident.get("kind"), incident.get("strategy_index")
        if incident.get("profile_id") != profile_id:
            incomplete.append("recovery_incident_profile_mismatch")
        if type(index) is not int or not 0 <= index < 100:
            incomplete.append("recovery_incident_strategy_invalid")
            continue
        window = incident.get("window_number")
        if kind == "slow_consumer":
            if index != 0:
                failures.append("slow_consumer_strategy_mismatch")
            if window is not None:
                incomplete.append("slow_consumer_window_must_be_null")
            incident_key = (kind, index)
        elif kind == "reconnect":
            if type(window) is not int or window not in expected_by_window:
                incomplete.append("reconnect_window_number_missing")
                incident_key = (kind, index, window)
            elif index not in expected_by_window[window]:
                failures.append("reconnect_strategy_outside_schedule")
                incident_key = (kind, index, window)
            else:
                seen_by_window[window].add(index)
                incident_key = (kind, index, window)
        else:
            incomplete.append("unknown_recovery_kind")
            continue
        if incident_key in seen_incidents:
            failures.append("duplicate_recovery_incident")
        seen_incidents.add(incident_key)

        expected_streams = {streams[i]["stream_id"] for i in subscriptions.get(index, set())}
        stream_rows = _map(incident.get("streams"))
        if stream_rows is None or set(stream_rows) != expected_streams:
            incomplete.append(f"recovery_stream_coverage_mismatch:{kind}:{index}")
            continue
        fault_start = incident.get("fault_started_monotonic_ns")
        fault_end = incident.get("fault_ended_monotonic_ns")
        capture_start = incident.get("head_capture_started_monotonic_ns")
        capture_end = incident.get("head_capture_completed_monotonic_ns")
        capture_deadline = incident.get("head_capture_budget_deadline_monotonic_ns")
        anchors_valid = all(_count(value) and value > 0 for value in (fault_start, fault_end, capture_start, capture_end)) and fault_start <= fault_end <= capture_start <= capture_end
        if not anchors_valid:
            incomplete.append(f"recovery_clock_anchors_invalid:{kind}:{index}")
        if not _count(capture_deadline) or not _count(fault_end) or capture_deadline != fault_end + 10_000_000_000:
            incomplete.append(f"recovery_capture_deadline_anchor_invalid:{kind}:{index}")
        if incident.get("head_capture_budget_status") == "EXCEEDED_10S":
            failures.append(f"recovery_head_capture_budget_exceeded:{kind}:{index}")
        elif incident.get("head_capture_budget_status") != "WITHIN_10S":
            incomplete.append(f"recovery_head_capture_budget_incomplete:{kind}:{index}")
        if incident.get("head_capture_timeout_status") not in ("NO_TIMEOUT",):
            incomplete.append(f"recovery_head_capture_timeout_or_error:{kind}:{index}")
        capture_budget_ms = incident.get("head_capture_budget_elapsed_ms")
        if not _number(capture_budget_ms):
            incomplete.append(f"recovery_head_capture_budget_elapsed_missing:{kind}:{index}")
        elif capture_budget_ms > deadline_ms:
            failures.append(f"recovery_head_capture_budget_exceeded:{kind}:{index}")
        capture_ms = incident.get("head_capture_elapsed_ms")
        if not _number(capture_ms):
            incomplete.append(f"recovery_head_capture_elapsed_missing:{kind}:{index}")
        elif capture_ms < 0:
            incomplete.append(f"recovery_head_capture_elapsed_invalid:{kind}:{index}")
        rpc_count, rpc_errors = incident.get("head_rpc_count"), incident.get("head_rpc_error_count")
        maintenance_count = incident.get("head_maintenance_check_count")
        maintenance_errors = incident.get("head_maintenance_error_count")
        if rpc_count != len(expected_streams) or not _count(rpc_count):
            incomplete.append(f"recovery_head_rpc_coverage_incomplete:{kind}:{index}")
        if not _count(rpc_errors):
            incomplete.append(f"recovery_head_rpc_error_count_missing:{kind}:{index}")
        elif rpc_errors:
            failures.append(f"recovery_head_rpc_errors:{kind}:{index}")
        if not _count(maintenance_count) or maintenance_count < len(expected_streams):
            incomplete.append(f"recovery_head_maintenance_coverage_incomplete:{kind}:{index}")
        if not _count(maintenance_errors):
            incomplete.append(f"recovery_head_maintenance_error_count_missing:{kind}:{index}")
        elif maintenance_errors:
            failures.append(f"recovery_head_maintenance_errors:{kind}:{index}")
        if incident.get("target_stream_count") != len(expected_streams) or incident.get("target_receipt_count") != len(expected_streams) or incident.get("target_reached_count") != len(expected_streams):
            incomplete.append(f"recovery_incident_target_counts_incomplete:{kind}:{index}")
        if incident.get("target_timing_status") == "EXCEEDED_10S":
            failures.append(f"recovery_incident_timing_exceeded:{kind}:{index}")
        elif incident.get("target_timing_status") != "WITHIN_10S_FOR_ALL_TARGETS":
            incomplete.append(f"recovery_incident_timing_incomplete:{kind}:{index}")
        if incident.get("gap_coverage_status") != "COMPLETE_TO_TARGET":
            incomplete.append(f"recovery_incident_gap_coverage_incomplete:{kind}:{index}")
        if incident.get("snapshot_skipped_sequence_count") != 0 or incident.get("snapshot_skipped_sequence_unknown_stream_count") != 0:
            incomplete.append(f"recovery_incident_snapshot_skip:{kind}:{index}")
        if incident.get("snapshot_skipped_sequence_known_stream_count") != len(expected_streams):
            incomplete.append(f"recovery_incident_snapshot_skip_coverage_missing:{kind}:{index}")

        for stream_id, raw in stream_rows.items():
            row = _map(raw)
            if row is None:
                incomplete.append(f"recovery_stream_row_invalid:{stream_id}")
                continue
            status = row.get("target_timing_status")
            if status == "EXCEEDED_10S":
                failures.append(f"recovery_stream_timing_exceeded:{stream_id}")
            elif status != "WITHIN_10S":
                incomplete.append(f"recovery_stream_timing_incomplete:{stream_id}")
            target_seq = row.get("head_target_sequence")
            target_epoch = row.get("head_target_epoch")
            head_captured = row.get("head_captured_monotonic_ns")
            deadline = row.get("target_timing_deadline_monotonic_ns")
            receipt = _map(row.get("first_qualifying_receipt"))
            receipt_seq = receipt.get("sequence") if receipt else None
            receipt_epoch = receipt.get("epoch") if receipt else None
            receipt_ns = receipt.get("received_monotonic_ns") if receipt else None
            elapsed = row.get("target_timing_elapsed_ms")
            if not _count(target_seq) or target_seq <= 0 or target_epoch in (None, ""):
                incomplete.append(f"recovery_head_target_invalid:{stream_id}")
            if not _count(head_captured) or not _count(capture_end) or head_captured > capture_end:
                incomplete.append(f"recovery_head_capture_anchor_invalid:{stream_id}")
            if not _count(deadline) or not _count(fault_end) or deadline != fault_end + 10_000_000_000:
                incomplete.append(f"recovery_target_deadline_invalid:{stream_id}")
            if not _count(receipt_seq) or not _count(target_seq) or receipt_seq < target_seq or receipt_epoch != target_epoch or not _count(receipt_ns) or not _count(head_captured) or receipt_ns < head_captured:
                incomplete.append(f"recovery_receipt_anchor_invalid:{stream_id}")
            if row.get("target_receipt_observed") is not True or row.get("target_reached") is not True:
                incomplete.append(f"recovery_target_receipt_unproven:{stream_id}")
            if not _number(elapsed):
                incomplete.append(f"recovery_elapsed_missing:{stream_id}")
            elif _count(fault_end) and _count(receipt_ns):
                actual = (receipt_ns - fault_end) / 1_000_000
                if abs(elapsed - actual) > 2:
                    incomplete.append(f"recovery_elapsed_anchor_mismatch:{stream_id}")
                if actual > deadline_ms:
                    failures.append(f"recovery_elapsed_exceeded:{stream_id}")
            skipped = row.get("snapshot_skipped_sequence_count")
            if not _count(skipped):
                incomplete.append(f"recovery_snapshot_skip_count_missing:{stream_id}")
            elif skipped:
                incomplete.append(f"recovery_snapshot_skip_incomplete:{stream_id}")
            if row.get("gap_coverage_status") != "COMPLETE_TO_TARGET" or row.get("coverage_gap_event_count") != 0:
                incomplete.append(f"recovery_interval_coverage_incomplete:{stream_id}")

    for window, expected_indices in expected_by_window.items():
        if seen_by_window[window] != expected_indices:
            incomplete.append(f"reconnect_window_coverage_incomplete:{window}")
    if len(slow) == 1 and (slow[0].get("strategy_index") != 0 or slow[0].get("window_number") is not None):
        failures.append("slow_consumer_schedule_identity_mismatch")

    if aggregate.get("incident_count") != len(incidents) or aggregate.get("incident_count_expected_from_observed_faults") != len(incidents):
        incomplete.append("recovery_aggregate_incident_cardinality_mismatch")
    if aggregate.get("profile_identity_missing_incident_count") != 0 or aggregate.get("reconnect_window_invalid_incident_count") != 0:
        incomplete.append("recovery_aggregate_profile_or_window_identity_incomplete")
    if aggregate.get("target_stream_count") != expected_target_count or aggregate.get("target_receipt_count") != expected_target_count or aggregate.get("target_reached_count") != expected_target_count:
        incomplete.append("recovery_aggregate_target_cardinality_mismatch")
    if aggregate.get("head_rpc_count") != expected_target_count or aggregate.get("head_rpc_count_expected_from_observed_faults") != expected_target_count:
        incomplete.append("recovery_aggregate_head_rpc_cardinality_mismatch")
    if aggregate.get("head_rpc_error_count") != 0:
        failures.append("recovery_aggregate_head_rpc_errors")
    if aggregate.get("target_timing_within_10s_count") != expected_target_count or aggregate.get("target_timing_exceeded_10s_count") != 0:
        incomplete.append("recovery_aggregate_timing_counts_incomplete")
    if aggregate.get("snapshot_skipped_sequence_count") != 0 or aggregate.get("snapshot_skipped_sequence_unknown_stream_count") != 0:
        incomplete.append("recovery_aggregate_snapshot_skips_present_or_unknown")
    if aggregate.get("snapshot_skipped_sequence_known_stream_count") != expected_target_count:
        incomplete.append("recovery_aggregate_snapshot_coverage_missing")
    if aggregate.get("gap_coverage_incomplete_incident_count") != 0 or aggregate.get("gap_coverage_status") != "COMPLETE_TO_TARGET":
        incomplete.append("recovery_aggregate_gap_coverage_incomplete")

    profile_counts = _map(aggregate.get("profile_counts"))
    profile_count = _map(profile_counts.get(profile_id)) if profile_counts else None
    by_kind = _map(profile_count.get("by_kind")) if profile_count else None
    if by_kind is None:
        incomplete.append("recovery_profile_counts_missing")
    else:
        expected_rows = {
            "slow_consumer": (1, expected_slow_targets),
            "reconnect": (expected_reconnects, expected_reconnect_targets),
        }
        for kind, (count, target_count) in expected_rows.items():
            row = _map(by_kind.get(kind))
            if row is None:
                incomplete.append(f"recovery_profile_kind_missing:{kind}")
                continue
            if row.get("incident_count") != count or row.get("incident_count_expected_from_observed_faults") != count:
                incomplete.append(f"recovery_profile_incident_count_mismatch:{kind}")
            if row.get("target_stream_count") != target_count or row.get("target_stream_count_expected") != target_count:
                incomplete.append(f"recovery_profile_target_count_mismatch:{kind}")
            if row.get("target_receipt_count") != target_count or row.get("target_timing_within_10s_count") != target_count:
                incomplete.append(f"recovery_profile_receipt_count_mismatch:{kind}")
            if row.get("target_timing_exceeded_10s_count") != 0:
                failures.append(f"recovery_profile_timing_exceeded:{kind}")
            if row.get("target_timing_status") != "WITHIN_10S_FOR_ALL_TARGETS":
                incomplete.append(f"recovery_profile_timing_incomplete:{kind}")
        expected_window_counts = {str(index): schedule["expected_reconnects_per_window"] for index in range(1, len(windows) + 1)}
        if profile_count.get("reconnect_window_counts") != expected_window_counts:
            incomplete.append("recovery_profile_reconnect_window_counts_mismatch")


def _check_payload(registration: Mapping[str, Any], profile_id: str, profile: Mapping[str, Any], report: Mapping[str, Any], incomplete: list[str], failures: list[str]) -> None:
    target = profile["market"].get("payload_bytes_target_per_published_event")
    if target is None:
        return
    load = _map(report.get("load")) or {}
    topology = _map(report.get("topology")) or {}
    reported = load.get("market_payload_bytes_target")
    if reported is None:
        reported = topology.get("topic_payload_target_bytes")
    if reported is None:
        incomplete.append(f"payload_target_hook_missing:{profile_id}")
    elif reported != target:
        failures.append(f"payload_target_mismatch:{profile_id}")
    per_topic = _map(report.get("input_payload_bytes_per_message_by_topic"))
    if per_topic is None:
        incomplete.append(f"payload_per_topic_measurements_missing:{profile_id}")
    else:
        for index in range(profile["market"]["topic_count"]):
            if per_topic.get(f"K{index:02d}") != target:
                incomplete.append(f"payload_topic_target_unproven:{profile_id}:K{index:02d}")


def _check_n6_quota(profile: Mapping[str, Any], report: Mapping[str, Any], incomplete: list[str], failures: list[str]) -> None:
    contract = profile["orders"].get("n6_quota_admission_contract")
    if not isinstance(contract, Mapping):
        incomplete.append("n6_quota_contract_missing")
        return
    load = _map(report.get("load"))
    wave = _map(load.get("quota_admission_wave")) if load else None
    if wave is None:
        _missing(incomplete, "load.quota_admission_wave")
        return
    for field, expected in (
        ("offered", contract["offered_requests_target"]),
        ("granted", contract["expected_grants"]),
        ("deadline_exceeded", contract["expected_deadline_exceeded"]),
    ):
        value = wave.get(field)
        if not _count(value):
            incomplete.append(f"n6_quota_count_missing:{field}")
        elif value != expected:
            failures.append(f"n6_quota_count_mismatch:{field}")
    if wave.get("provider_query_dispatched_by_wave") is not False:
        failures.append("n6_quota_provider_query_separation_failed")
    if wave.get("pending_waiters_after_collection") != contract["pending_waiters_after_collection_max"]:
        failures.append("n6_quota_pending_waiters_nonzero_or_missing")
    for field, maximum in (
        ("grant_p95_ns", contract["grant_latency_ms_max"] * 1_000_000),
        ("denial_p95_ns", contract["denial_p95_cleanup_ms_max"] * 1_000_000),
    ):
        value = wave.get(field)
        if not _count(value):
            incomplete.append(f"n6_quota_latency_missing:{field}")
        elif value > maximum:
            failures.append(f"n6_quota_latency_exceeded:{field}")
    results = _map(report.get("strategy_results"))
    if results is None or len(results) != contract["strategy_count"]:
        incomplete.append("n6_quota_strategy_coverage_incomplete")
        return
    grants = denials = 0
    for index in range(contract["strategy_count"]):
        result = _map(_row(results, index))
        wave_row = _map(result.get("quota_admission_wave")) if result else None
        outcomes = wave_row.get("outcomes") if wave_row else None
        if not isinstance(outcomes, list) or len(outcomes) != contract["requests_per_strategy"]:
            incomplete.append(f"n6_quota_strategy_outcomes_missing:{index}")
            continue
        labels = [row.get("outcome") for row in outcomes if isinstance(row, Mapping) and row.get("strategy_index") == index]
        if len(labels) != contract["requests_per_strategy"]:
            incomplete.append(f"n6_quota_outcome_identity_invalid:{index}")
        grants += labels.count("GRANTED")
        denials += labels.count("DEADLINE_EXCEEDED")
    if grants != contract["expected_grants"] or denials != contract["expected_deadline_exceeded"]:
        failures.append("n6_quota_outcome_distribution_mismatch")
    metrics = _map(report.get("registered_performance_metrics")) or {}
    rpc = _map(metrics.get("order_rpc_by_strategy")) or {}
    for index in range(100):
        row = _map(_row(rpc, index)) or {}
        query = _map(row.get("orders.query"))
        if query and query.get("observed_count") != 0:
            failures.append(f"n6_quota_polluted_ordinary_query:{index}")


def _check_n5_group(registration: Mapping[str, Any], profile: Mapping[str, Any], report: Mapping[str, Any], incomplete: list[str], failures: list[str]) -> None:
    contract = profile["orders"].get("n5_group_contract")
    if not isinstance(contract, Mapping):
        incomplete.append("n5_group_contract_missing")
        return
    section = _map(report.get("accounts_and_execution"))
    aggregate = _map(section.get("n5_group_metrics")) if section else None
    if aggregate is None:
        _missing(incomplete, "accounts_and_execution.n5_group_metrics")
        return
    by_strategy = _map(aggregate.get("by_strategy"))
    if by_strategy is None or len(by_strategy) != 100:
        incomplete.append("n5_group_strategy_coverage_incomplete")
        return
    capable = set(contract["group_capable_strategy_indices"])
    source_identity_by_report_account = _map(
        contract.get("source_identity_by_report_account")
    )
    if source_identity_by_report_account is None:
        incomplete.append("n5_group_source_identity_registration_missing")
    target_requests = contract["per_account_1800_second_targets"]["group_requests"]
    target_children = contract["per_account_1800_second_targets"]["group_child_dispatches"]
    legs_per_group = contract["group_legs_per_group"]
    rpc_targets = registration["engineering_targets"]["latency"]["n5_group_rpc_ms_each_operation"]
    total_requests = total_children = 0
    account_by_strategy = {
        index: account_index
        for account_index, indexes in enumerate(profile["orders"]["account_strategy_indices_by_account"])
        for index in indexes
    }
    child_intents: set[str] = set()
    derived_children_by_account = {f"account-{index:02d}": 0 for index in range(10)}
    derived_children_account_observed: set[str] = set()
    derived_join_keys_by_account: dict[str, set[tuple[str, str, str]]] = {
        f"account-{index:02d}": set() for index in range(10)
    }
    durable_group_ids: set[str] = set()
    unresolved_group_total = 0
    group_failure_total = 0
    unknown_child_outcomes = 0
    for index in range(100):
        row = _map(_row(by_strategy, index))
        if row is None:
            incomplete.append(f"n5_group_strategy_missing:{index}")
            continue
        expected_req = target_requests if index in capable else 0
        expected_child = target_children if index in capable else 0
        account_ref = f"account-{account_by_strategy.get(index, -1):02d}"
        source_identity = (
            _map(source_identity_by_report_account.get(account_ref))
            if source_identity_by_report_account is not None
            else None
        )
        source_account_ref = source_identity.get("account_ref") if source_identity else None
        source_strategy_index = source_identity.get("group_strategy_index") if source_identity else None
        source_strategy_ref = source_identity.get("group_strategy_ref") if source_identity else None
        if not isinstance(source_account_ref, str) or not source_account_ref:
            incomplete.append(f"n5_group_source_account_identity_missing:{index}")
        if index in capable and (
            source_strategy_index != index
            or not isinstance(source_strategy_ref, str)
            or not source_strategy_ref
        ):
            incomplete.append(f"n5_group_source_strategy_identity_missing:{index}")
        expected_metrics = _map(row.get("expected"))
        observed_metrics = _map(row.get("observed"))
        if expected_metrics is None or observed_metrics is None:
            incomplete.append(f"n5_group_expected_or_observed_metrics_missing:{index}")
        expected_logical = row.get("expected_logical_group_count")
        observed_req = row.get("observed_logical_group_count")
        expected_child_count = row.get("expected_child_dispatch_count")
        observed_child = row.get("observed_child_dispatch_count")
        if not all(_count(value) for value in (expected_logical, observed_req, expected_child_count, observed_child)):
            incomplete.append(f"n5_group_counts_invalid:{index}")
            continue
        if expected_logical != expected_req or observed_req != expected_req:
            failures.append(f"n5_group_logical_count_outside_target:{index}")
        if expected_child_count != expected_child or observed_child != expected_child:
            failures.append(f"n5_group_child_count_outside_target:{index}")
        if index in capable and row.get("group_child_dispatch_count_source") != "distinct_durable_filled_child_intent_observation_not_provider_write_log":
            incomplete.append(f"n5_group_child_dispatch_source_unqualified:{index}")
        if index in capable and (
            row.get("group_child_provider_write_count") is not None
            or row.get("group_child_provider_write_count_status") != "NOT_REGISTERED_FROM_OWNER_LOG"
        ):
            incomplete.append(f"n5_group_strategy_provider_source_mixed:{index}")
        total_requests += observed_req
        total_children += observed_child

        for operation, expected in (("groups.submit", expected_req), ("groups.query", expected_req)):
            if expected_metrics is not None and expected_metrics.get(operation) != expected:
                failures.append(f"n5_group_expected_rpc_count_mismatch:{index}:{operation}")
        if expected_metrics is not None:
            if expected_metrics.get("group_logical_count") != expected_req:
                failures.append(f"n5_group_expected_logical_count_mismatch:{index}")
            if expected_metrics.get("group_child_dispatch_count") != expected_child:
                failures.append(f"n5_group_expected_child_count_mismatch:{index}")
            expected_account_logical = _map(expected_metrics.get("group_logical_count_by_account"))
            expected_account_children = _map(expected_metrics.get("group_child_dispatch_count_by_account"))
            expected_leg_children = _map(expected_metrics.get("group_child_dispatch_count_by_leg"))
        else:
            expected_account_logical = expected_account_children = expected_leg_children = None
        expected_maps_valid = True
        for field, mapping in (
            ("expected_group_logical_by_account", expected_account_logical),
            ("expected_group_child_by_account", expected_account_children),
            ("expected_group_child_by_leg", expected_leg_children),
        ):
            if mapping is not None and any(not _count(value) for value in mapping.values()):
                incomplete.append(f"n5_group_{field}_invalid:{index}")
                expected_maps_valid = False
        if expected_account_logical is not None and expected_account_children is not None and expected_leg_children is not None and expected_maps_valid:
            if index in capable:
                if sum(expected_account_logical.values()) != expected_req or expected_account_logical.get(source_account_ref) != expected_req or any(value for key, value in expected_account_logical.items() if key != source_account_ref):
                    failures.append(f"n5_group_expected_account_logical_distribution_mismatch:{index}")
                if sum(expected_account_children.values()) != expected_child or expected_account_children.get(source_account_ref) != expected_child or any(value for key, value in expected_account_children.items() if key != source_account_ref):
                    failures.append(f"n5_group_expected_account_child_distribution_mismatch:{index}")
                if len(expected_leg_children) != legs_per_group or sum(expected_leg_children.values()) != expected_child or any(value != target_requests for value in expected_leg_children.values()):
                    failures.append(f"n5_group_expected_leg_distribution_mismatch:{index}")
            elif expected_account_logical or expected_account_children or len(expected_leg_children) != legs_per_group or any(expected_leg_children.values()):
                failures.append(f"n5_group_expected_counts_on_noncapable_strategy:{index}")
        if observed_metrics is not None:
            for field, expected in (
                ("group_logical_count", expected_req),
                ("group_success_count", expected_req),
                ("group_failure_count", 0),
                ("group_unresolved_count", 0),
                ("group_child_dispatch_count", expected_child),
            ):
                value = observed_metrics.get(field)
                if not _count(value):
                    incomplete.append(f"n5_group_observed_field_missing:{index}:{field}")
                elif value != expected:
                    failures.append(f"n5_group_observed_field_mismatch:{index}:{field}")
            unresolved_group_total += observed_metrics.get("group_unresolved_count", 0) if _count(observed_metrics.get("group_unresolved_count")) else 0
            group_failure_total += observed_metrics.get("group_failure_count", 0) if _count(observed_metrics.get("group_failure_count")) else 0

        logical_by_account = _map(row.get("group_logical_count_by_account"))
        children_by_account = _map(row.get("group_child_dispatch_count_by_account"))
        children_by_leg = _map(row.get("group_child_dispatch_count_by_leg"))
        if any(value is None for value in (expected_account_logical, expected_account_children, expected_leg_children, logical_by_account, children_by_account, children_by_leg)):
            incomplete.append(f"n5_group_account_or_leg_counts_missing:{index}")
        else:
            if any(not _count(value) for mapping in (logical_by_account, children_by_account, children_by_leg) for value in mapping.values()):
                incomplete.append(f"n5_group_observed_account_or_leg_counts_invalid:{index}")
            if index in capable:
                if expected_account_logical.get(source_account_ref) != expected_req or logical_by_account.get(source_account_ref) != expected_req or sum(value for value in logical_by_account.values() if _count(value)) != expected_req or any(value for key, value in logical_by_account.items() if key != source_account_ref):
                    failures.append(f"n5_group_account_logical_count_mismatch:{index}")
                if expected_account_children.get(source_account_ref) != expected_child or children_by_account.get(source_account_ref) != expected_child or sum(value for value in children_by_account.values() if _count(value)) != expected_child or any(value for key, value in children_by_account.items() if key != source_account_ref):
                    failures.append(f"n5_group_account_child_count_mismatch:{index}")
            elif any(value for value in (*logical_by_account.values(), *children_by_account.values())):
                failures.append(f"n5_group_count_on_noncapable_strategy:{index}")
            if sum(value for value in children_by_leg.values() if _count(value)) != expected_child or len(children_by_leg) != legs_per_group:
                failures.append(f"n5_group_leg_child_count_mismatch:{index}")
            if index in capable and (set(children_by_leg) != set(expected_leg_children or {}) or any(value != target_requests for value in children_by_leg.values())):
                failures.append(f"n5_group_child_leg_target_mismatch:{index}")
            if expected_leg_children and expected_leg_children != children_by_leg:
                failures.append(f"n5_group_expected_leg_counts_mismatch:{index}")

        child_outcomes = _map(row.get("group_child_outcome_by_leg"))
        if child_outcomes is None or (index in capable and len(child_outcomes) != legs_per_group):
            incomplete.append(f"n5_group_child_outcomes_missing:{index}")
        elif index in capable:
            expected_legs = set(expected_leg_children or {})
            if set(child_outcomes) != expected_legs:
                incomplete.append(f"n5_group_child_outcome_leg_coverage_mismatch:{index}")
            for leg, state_counts in child_outcomes.items():
                states = _map(state_counts)
                if states is None or any(not _count(value) for value in states.values()):
                    incomplete.append(f"n5_group_child_outcome_counts_invalid:{index}:{leg}")
                    continue
                required_phases = {"READY", "REQUESTING", "UNKNOWN", "OBSERVED"}
                if not required_phases.issubset({str(state).split(".")[-1] for state in states}):
                    incomplete.append(f"n5_group_child_outcome_phase_coverage_missing:{index}:{leg}")
                if sum(states.values()) != target_requests:
                    failures.append(f"n5_group_child_outcome_total_mismatch:{index}:{leg}")
                observed_count = sum(value for state, value in states.items() if str(state).split(".")[-1] == "OBSERVED")
                if observed_count != target_requests or sum(states.values()) != observed_count:
                    failures.append(f"n5_group_child_outcome_not_observed:{index}:{leg}")
                    unknown_child_outcomes += max(0, sum(states.values()) - observed_count)
        elif child_outcomes is not None:
            if len(child_outcomes) != legs_per_group:
                incomplete.append(f"n5_group_child_outcome_leg_coverage_mismatch:{index}")
            for leg, state_counts in child_outcomes.items():
                states = _map(state_counts)
                if states is None or any(not _count(value) for value in states.values()):
                    incomplete.append(f"n5_group_child_outcome_counts_invalid:{index}:{leg}")
                elif any(states.values()):
                    failures.append(f"n5_group_child_outcome_on_noncapable_strategy:{index}:{leg}")

        execution_states = _map(row.get("group_child_execution_state_by_leg"))
        if execution_states is None or len(execution_states) != legs_per_group:
            incomplete.append(f"n5_group_child_execution_states_missing:{index}")
        elif index in capable:
            if set(execution_states) != set(expected_leg_children or {}):
                incomplete.append(f"n5_group_child_execution_state_leg_coverage_mismatch:{index}")
            for leg, state_counts in execution_states.items():
                states = _map(state_counts)
                if states is None or any(not _count(value) for value in states.values()):
                    incomplete.append(f"n5_group_child_execution_state_counts_invalid:{index}:{leg}")
                    continue
                normalized = {str(state).split(".")[-1]: value for state, value in states.items()}
                if not {"FILLED", "UNKNOWN", "DUPLICATE", "UNRESOLVED"}.issubset(normalized):
                    incomplete.append(f"n5_group_child_execution_state_keys_missing:{index}:{leg}")
                if normalized.get("FILLED") != target_requests or sum(states.values()) != target_requests or any(value for name, value in normalized.items() if name != "FILLED"):
                    failures.append(f"n5_group_child_execution_state_not_filled:{index}:{leg}")
        elif execution_states is not None:
            for leg, state_counts in execution_states.items():
                states = _map(state_counts)
                if states is None or any(not _count(value) for value in states.values()):
                    incomplete.append(f"n5_group_child_execution_state_counts_invalid:{index}:{leg}")
                elif any(states.values()):
                    failures.append(f"n5_group_child_execution_state_on_noncapable_strategy:{index}:{leg}")

        route_intents_by_leg: dict[str, set[str]] = {}
        routes = _map(row.get("group_child_routes_by_leg"))
        if routes is None or len(routes) != legs_per_group:
            incomplete.append(f"n5_group_child_routes_missing:{index}")
        else:
            if set(routes) != set(children_by_leg or {}):
                incomplete.append(f"n5_group_child_route_leg_coverage_mismatch:{index}")
            for leg, route_raw in routes.items():
                route = _map(route_raw)
                if route is None:
                    incomplete.append(f"n5_group_child_route_invalid:{index}:{leg}")
                    continue
                intent_ids = route.get("intent_ids")
                if not isinstance(intent_ids, list) or any(not isinstance(value, str) or not value for value in intent_ids):
                    incomplete.append(f"n5_group_child_route_intents_missing:{index}:{leg}")
                    continue
                route_intents = set(intent_ids)
                if len(route_intents) != len(intent_ids):
                    failures.append(f"n5_group_duplicate_child_intent:{index}:{leg}")
                if index in capable:
                    if route.get("account_ref") != source_account_ref or route.get("strategy_ref") != source_strategy_ref:
                        failures.append(f"n5_group_child_route_owner_mismatch:{index}:{leg}")
                    if not route.get("portfolio_ref") or not route.get("scope_ref") or len(intent_ids) != target_requests:
                        incomplete.append(f"n5_group_child_route_identity_incomplete:{index}:{leg}")
                    if route.get("expected_dispatch_count") != target_requests or route.get("observed_dispatch_count") != target_requests:
                        failures.append(f"n5_group_child_route_count_mismatch:{index}:{leg}")
                    if route.get("observed_dispatch_count_source") != "distinct_durable_filled_child_intent_observation_not_provider_write_log":
                        incomplete.append(f"n5_group_child_route_source_unqualified:{index}:{leg}")
                    route_intents_by_leg[leg] = route_intents
                    duplicate_intents = child_intents.intersection(route_intents)
                    if duplicate_intents:
                        failures.append(f"n5_group_child_intent_duplicate_across_routes:{index}:{leg}")
                    child_intents.update(route_intents)
                elif route.get("account_ref") is not None or route.get("strategy_ref") is not None or route.get("portfolio_ref") is not None or route.get("scope_ref") is not None or intent_ids or route.get("expected_dispatch_count") != 0 or route.get("observed_dispatch_count") != 0:
                    failures.append(f"n5_group_route_on_noncapable_strategy:{index}:{leg}")
            if index in capable and len(route_intents_by_leg) == legs_per_group:
                if any(len(values) != target_requests for values in route_intents_by_leg.values()):
                    failures.append(f"n5_group_route_intent_count_mismatch:{index}")
                derived_count = len(set().union(*route_intents_by_leg.values()))
                derived_children_by_account[account_ref] += derived_count
                derived_children_account_observed.add(account_ref)
                if derived_count != expected_child:
                    failures.append(f"n5_group_distinct_durable_child_intent_count_mismatch:{index}")

        durable_records = row.get("group_durable_records")
        durable_count = row.get("group_durable_record_count")
        if not isinstance(durable_records, list) or not _count(durable_count):
            incomplete.append(f"n5_group_durable_records_missing:{index}")
        elif durable_count != expected_req or len(durable_records) != expected_req:
            failures.append(f"n5_group_durable_record_count_mismatch:{index}")
        elif index in capable:
            durable_intents_by_leg: dict[str, set[str]] = {leg: set() for leg in route_intents_by_leg}
            route_portfolio_by_leg = {leg: route.get("portfolio_ref") for leg, route in (routes or {}).items() if isinstance(route, Mapping)}
            for record in durable_records:
                if not isinstance(record, Mapping):
                    incomplete.append(f"n5_group_durable_record_invalid:{index}")
                    continue
                group_id = record.get("group_id")
                version, revision = record.get("version"), record.get("revision")
                created, updated = record.get("created_at_ns"), record.get("updated_at_ns")
                leg_ids = _map(record.get("leg_intent_ids"))
                if record.get("query_found") is not True:
                    failures.append(f"n5_group_snapshot_query_not_found:{index}")
                if not isinstance(group_id, str) or not group_id:
                    incomplete.append(f"n5_group_durable_group_id_missing:{index}")
                elif group_id in durable_group_ids:
                    failures.append("n5_group_durable_group_id_duplicate")
                else:
                    durable_group_ids.add(group_id)
                if version != 3 or not _count(revision) or revision <= 0 or record.get("state") != "PAIRED":
                    failures.append(f"n5_group_durable_snapshot_state_invalid:{index}")
                if record.get("strategy_ref") != source_strategy_ref or not record.get("portfolio_ref"):
                    failures.append(f"n5_group_durable_owner_identity_invalid:{index}")
                if leg_ids is not None and any(record.get("portfolio_ref") != portfolio for portfolio in route_portfolio_by_leg.values()):
                    failures.append(f"n5_group_durable_portfolio_route_mismatch:{index}")
                if not _count(created) or not _count(updated) or created <= 0 or updated < created:
                    incomplete.append(f"n5_group_durable_timestamps_invalid:{index}")
                if leg_ids is None or set(leg_ids) != set(route_intents_by_leg) or len(leg_ids) != legs_per_group or any(not isinstance(value, str) or not value for value in leg_ids.values()):
                    incomplete.append(f"n5_group_durable_leg_intent_ids_invalid:{index}")
                elif routes is not None:
                    for leg, value in leg_ids.items():
                        if value not in route_intents_by_leg.get(leg, set()):
                            failures.append(f"n5_group_durable_intent_route_mismatch:{index}")
                        if value in durable_intents_by_leg.setdefault(leg, set()):
                            failures.append(f"n5_group_durable_intent_duplicate:{index}:{leg}")
                        durable_intents_by_leg[leg].add(value)
                        if isinstance(group_id, str) and group_id and isinstance(leg, str) and isinstance(value, str) and value:
                            derived_join_keys_by_account[account_ref].add((group_id, leg, value))
            if durable_intents_by_leg != route_intents_by_leg:
                failures.append(f"n5_group_durable_intent_coverage_mismatch:{index}")

        lifecycle = _map(row.get("group_lifecycle_ms"))
        if lifecycle is None or not _count(lifecycle.get("count")):
            incomplete.append(f"n5_group_lifecycle_summary_missing:{index}")
        elif index in capable:
            if lifecycle.get("count") != expected_req or any(not _number(lifecycle.get(name)) or lifecycle[name] < 0 for name in ("p50", "p95", "p99", "max")):
                incomplete.append(f"n5_group_lifecycle_summary_invalid:{index}")
        elif lifecycle.get("count") != 0 or any(lifecycle.get(name) is not None for name in ("p50", "p95", "p99", "max")):
            incomplete.append(f"n5_group_lifecycle_noncapable_not_empty:{index}")

        child_latency_status = row.get("group_child_dispatch_latency_status")
        child_latency = row.get("group_child_dispatch_latency_ns_by_account_strategy", "missing")
        if child_latency_status != "NOT_REGISTERED_BY_COORDINATOR_SNAPSHOT" or child_latency is not None:
            incomplete.append(f"n5_group_child_latency_provenance_missing:{index}")

        rpc_root = _map(row.get("group_rpc_metrics"))
        operations = _map(rpc_root.get("operations")) if rpc_root else None
        if operations is None or set(operations) != {"groups.submit", "groups.query"}:
            incomplete.append(f"n5_group_rpc_operations_missing:{index}")
            continue
        for operation in ("groups.submit", "groups.query"):
            op = _map(operations.get(operation))
            expected_rpc = expected_req
            if op is None:
                incomplete.append(f"n5_group_rpc_operation_missing:{index}:{operation}")
                continue
            observed, success, errors, timeout = (op.get(name) for name in ("observed_count", "success_count", "error_count", "timeout_count"))
            expected_count = op.get("expected_count")
            if not all(_count(value) for value in (observed, errors, timeout, expected_count)):
                incomplete.append(f"n5_group_rpc_counts_invalid:{index}:{operation}")
            else:
                if expected_count != expected_rpc or observed != expected_rpc:
                    failures.append(f"n5_group_rpc_volume_or_success_mismatch:{index}:{operation}")
                if success is not None and (not _count(success) or success != expected_rpc):
                    failures.append(f"n5_group_rpc_success_count_mismatch:{index}:{operation}")
                if errors:
                    failures.append(f"n5_group_rpc_error:{index}:{operation}")
                if timeout > rpc_targets["unexpected_timeout_count_max"]:
                    failures.append(f"n5_group_rpc_timeout:{index}:{operation}")
            sampled, capacity = op.get("sampled_count"), op.get("sample_capacity")
            coverage, retained = op.get("sample_coverage_fraction"), op.get("full_population_retained")
            if any(value is not None for value in (sampled, capacity, coverage, retained)):
                if not _count(sampled) or (expected_rpc and sampled <= 0) or not _count(capacity) or (expected_rpc and capacity <= 0) or not _number(coverage) or not 0 <= coverage <= 1 or type(retained) is not bool:
                    incomplete.append(f"n5_group_rpc_sample_metadata_invalid:{index}:{operation}")
                elif expected_rpc and _count(observed) and abs(coverage - min(sampled, observed) / observed) > 1e-6:
                    incomplete.append(f"n5_group_rpc_sample_coverage_mismatch:{index}:{operation}")
            if expected_rpc == 0:
                continue
            percentiles = _map(op.get("percentiles_ms"))
            if percentiles is None:
                raw_ns = _map(op.get("percentiles_ns"))
                if raw_ns is not None:
                    percentiles = {name: value / 1_000_000 if _number(value) else None for name, value in raw_ns.items()}
            for name in ("p50", "p95", "p99"):
                value = op.get(f"{name}_ms")
                if value is None and percentiles is not None:
                    value = percentiles.get(name)
                ns_value = op.get(f"{name}_ns")
                if not _number(value) or not _number(ns_value) or ns_value < 0:
                    incomplete.append(f"n5_group_rpc_percentile_invalid:{index}:{operation}:{name}")
                else:
                    if abs(value - ns_value / 1_000_000) > 0.001:
                        incomplete.append(f"n5_group_rpc_percentile_unit_mismatch:{index}:{operation}:{name}")
                    if name == "p99" and value > rpc_targets["p99_max"]:
                        failures.append(f"n5_group_rpc_latency_exceeded:{index}:{operation}:{name}")
    provider_registration = _map(contract.get("provider_write_evidence"))
    provider_ledgers = _map(aggregate.get("provider_ledger_by_account"))
    join_registration = _map(provider_registration.get("intent_order_join")) if provider_registration else None
    joins_by_account = _map(aggregate.get("intent_order_join_by_account"))
    owner_join_counts = _map(aggregate.get("owner_join_counts_by_account"))
    join_client_ids_by_account: dict[str, set[str]] = {}
    quantity_by_client_order_id: dict[str, Decimal] = {}
    if join_registration is None:
        incomplete.append("n5_group_intent_order_join_registration_missing")
    elif joins_by_account is None:
        incomplete.append("n5_group_intent_order_join_by_account_missing")
    elif owner_join_counts is None:
        incomplete.append("n5_group_owner_join_counts_by_account_missing")
    else:
        expected_accounts = provider_registration.get("account_refs") if provider_registration else None
        strategy_by_account = _map(provider_registration.get("group_strategy_index_by_account")) if provider_registration else None
        if not isinstance(expected_accounts, list) or strategy_by_account is None:
            incomplete.append("n5_group_intent_order_join_account_registration_invalid")
        else:
            for account_ref in expected_accounts:
                expected_join_count = join_registration.get("rows_per_account")
                raw_rows = joins_by_account.get(account_ref)
                counts = _map(owner_join_counts.get(account_ref))
                if not isinstance(raw_rows, list):
                    incomplete.append(f"n5_group_intent_order_join_rows_missing:{account_ref}")
                    continue
                if not _count(expected_join_count):
                    incomplete.append("n5_group_intent_order_join_registered_count_invalid")
                    continue
                if len(raw_rows) != expected_join_count:
                    failures.append(f"n5_group_intent_order_join_row_count_mismatch:{account_ref}")
                if counts is None:
                    incomplete.append(f"n5_group_intent_order_join_owner_counts_missing:{account_ref}")
                else:
                    for field in ("intent_order_join_count", "joined_intent_order_count"):
                        value = counts.get(field)
                        if not _count(value):
                            incomplete.append(f"n5_group_intent_order_join_owner_count_invalid:{account_ref}:{field}")
                        elif value != expected_join_count:
                            failures.append(f"n5_group_intent_order_join_owner_count_mismatch:{account_ref}:{field}")

                expected_keys = derived_join_keys_by_account.get(account_ref, set())
                quantity_registration = _map(join_registration.get("required_quantity_by_leg"))
                observed_keys: set[tuple[str, str, str]] = set()
                client_ids: set[str] = set()
                for row_index, raw_row in enumerate(raw_rows):
                    join_row = _map(raw_row)
                    if join_row is None:
                        incomplete.append(f"n5_group_intent_order_join_row_invalid:{account_ref}:{row_index}")
                        continue
                    key_values = tuple(join_row.get(name) for name in ("group_id", "leg_id", "intent_id"))
                    if any(not isinstance(value, str) or not value for value in key_values):
                        incomplete.append(f"n5_group_intent_order_join_identity_missing:{account_ref}:{row_index}")
                    else:
                        key = (key_values[0], key_values[1], key_values[2])
                        if key in observed_keys:
                            failures.append(f"n5_group_intent_order_join_identity_duplicate:{account_ref}")
                        observed_keys.add(key)
                    attempt_count = join_row.get("attempt_started_count")
                    attempt_revision = join_row.get("attempt_started_revision")
                    attempt_digest = join_row.get("attempt_started_record_sha256")
                    binding_valid = join_row.get("attempt_binding_valid")
                    if not _count(attempt_count) or not _count(attempt_revision) or attempt_revision <= 0:
                        incomplete.append(f"n5_group_intent_order_join_attempt_evidence_invalid:{account_ref}:{row_index}")
                    elif attempt_count != 1:
                        failures.append(f"n5_group_intent_order_join_attempt_count_mismatch:{account_ref}:{row_index}")
                    if not isinstance(attempt_digest, str) or len(attempt_digest) != 64 or any(char not in "0123456789abcdef" for char in attempt_digest.lower()):
                        incomplete.append(f"n5_group_intent_order_join_attempt_digest_invalid:{account_ref}:{row_index}")
                    if type(binding_valid) is not bool:
                        incomplete.append(f"n5_group_intent_order_join_attempt_binding_missing:{account_ref}:{row_index}")
                    elif not binding_valid:
                        failures.append(f"n5_group_intent_order_join_attempt_binding_invalid:{account_ref}:{row_index}")
                    client_order_id = join_row.get("client_order_id")
                    if not isinstance(client_order_id, str) or not client_order_id:
                        incomplete.append(f"n5_group_intent_order_join_client_id_missing:{account_ref}:{row_index}")
                    else:
                        if client_order_id in client_ids:
                            failures.append(f"n5_group_intent_order_join_client_id_duplicate:{account_ref}")
                        client_ids.add(client_order_id)
                        leg_id = key_values[1] if isinstance(key_values[1], str) else None
                        expected_quantity = _decimal_value(quantity_registration.get(leg_id)) if quantity_registration and leg_id else None
                        if expected_quantity is None or expected_quantity <= 0:
                            incomplete.append(f"n5_group_intent_order_join_leg_quantity_registration_invalid:{account_ref}:{row_index}")
                        else:
                            quantity_by_client_order_id[client_order_id] = expected_quantity
                    provider_write_count = join_row.get("provider_write_count")
                    if not _count(provider_write_count):
                        incomplete.append(f"n5_group_intent_order_join_write_count_invalid:{account_ref}:{row_index}")
                    elif provider_write_count != 1:
                        failures.append(f"n5_group_intent_order_join_write_count_mismatch:{account_ref}:{row_index}")
                    order_row_present = join_row.get("provider_order_row_present")
                    if type(order_row_present) is not bool:
                        incomplete.append(f"n5_group_intent_order_join_provider_row_presence_missing:{account_ref}:{row_index}")
                    elif not order_row_present:
                        failures.append(f"n5_group_intent_order_join_provider_row_missing:{account_ref}:{row_index}")
                    order_status = join_row.get("order_status")
                    if not isinstance(order_status, str) or not order_status:
                        incomplete.append(f"n5_group_intent_order_join_order_status_missing:{account_ref}:{row_index}")
                    elif order_status != join_registration.get("required_provider_order_status"):
                        failures.append(f"n5_group_intent_order_join_order_status_mismatch:{account_ref}:{row_index}")
                    for query_field in ("order_query_count", "fill_query_count"):
                        query_count = join_row.get(query_field)
                        if not _count(query_count):
                            incomplete.append(f"n5_group_intent_order_join_query_count_invalid:{account_ref}:{row_index}:{query_field}")
                        else:
                            minimum_query_count = join_registration.get(
                                "minimum_order_query_count_per_join_row" if query_field == "order_query_count" else "minimum_fill_query_count_per_join_row"
                            )
                            if not _count(minimum_query_count):
                                incomplete.append(f"n5_group_intent_order_join_query_minimum_invalid:{account_ref}:{query_field}")
                            elif query_count < minimum_query_count:
                                failures.append(f"n5_group_intent_order_join_query_count_below_minimum:{account_ref}:{row_index}:{query_field}")
                    join_state = join_row.get("join_state")
                    if not isinstance(join_state, str) or not join_state:
                        incomplete.append(f"n5_group_intent_order_join_state_missing:{account_ref}:{row_index}")
                    elif join_state != join_registration.get("required_join_state"):
                        failures.append(f"n5_group_intent_order_join_state_not_joined:{account_ref}:{row_index}")
                if expected_keys and observed_keys != expected_keys:
                    failures.append(f"n5_group_intent_order_join_durable_identity_mismatch:{account_ref}")
                if len(observed_keys) != len(raw_rows):
                    failures.append(f"n5_group_intent_order_join_identity_coverage_mismatch:{account_ref}")
                join_client_ids_by_account[account_ref] = client_ids
    if provider_registration is None:
        incomplete.append("n5_group_provider_write_registration_missing")
    elif provider_ledgers is None:
        incomplete.append("n5_group_provider_ledger_by_account_missing")
    else:
        expected_accounts = provider_registration.get("account_refs")
        strategy_by_account = _map(provider_registration.get("group_strategy_index_by_account"))
        if not isinstance(expected_accounts, list) or len(expected_accounts) != 10 or strategy_by_account is None:
            incomplete.append("n5_group_provider_account_registration_invalid")
        else:
            provider_total = provider_unique_total = provider_duplicate_total = 0
            provider_totals_complete = True
            for account_ref in expected_accounts:
                strategy_index = strategy_by_account.get(account_ref)
                source_identity = (
                    _map(source_identity_by_report_account.get(account_ref))
                    if source_identity_by_report_account is not None
                    else None
                )
                ledger = _map(provider_ledgers.get(account_ref))
                if ledger is None:
                    incomplete.append(f"n5_group_provider_ledger_missing:{account_ref}")
                    provider_totals_complete = False
                    continue
                if not _count(strategy_index) or strategy_index not in capable:
                    incomplete.append(f"n5_group_provider_strategy_binding_invalid:{account_ref}")
                    provider_totals_complete = False
                    continue
                if (
                    source_identity is None
                    or source_identity.get("group_strategy_index") != strategy_index
                    or not isinstance(source_identity.get("account_ref"), str)
                    or not isinstance(source_identity.get("group_strategy_ref"), str)
                ):
                    incomplete.append(f"n5_group_provider_source_identity_missing:{account_ref}")
                    provider_totals_complete = False
                    continue
                if (
                    ledger.get("attribution") != provider_registration.get("attribution")
                    or ledger.get("account_ref") != source_identity.get("account_ref")
                    or ledger.get("strategy_ref") != source_identity.get("group_strategy_ref")
                    or ledger.get("strategy_index") != strategy_index
                ):
                    incomplete.append(f"n5_group_provider_ledger_identity_or_attribution_invalid:{account_ref}")
                    provider_totals_complete = False
                zero_fields = provider_registration.get("required_zero_anomaly_fields", [])
                if not isinstance(zero_fields, list):
                    incomplete.append("n5_group_provider_zero_anomaly_registration_invalid")
                    zero_fields = []
                count_fields = set(zero_fields) | {"provider_order_count", "order_query_count", "fill_query_count"}
                for field in count_fields:
                    value = ledger.get(field)
                    if not _count(value):
                        incomplete.append(f"n5_group_provider_ledger_count_invalid:{account_ref}:{field}")
                        provider_totals_complete = False
                    elif field in zero_fields and value != 0:
                        failures.append(f"n5_group_provider_ledger_anomaly_nonzero:{account_ref}:{field}")
                    elif field == "provider_order_count" and value != provider_registration.get("expected_group_child_make_order_writes_per_account"):
                        failures.append(f"n5_group_provider_order_count_mismatch:{account_ref}")
                for field in ("unjoined_client_order_ids", "duplicate_join_client_order_ids"):
                    values = ledger.get(field)
                    if not isinstance(values, list):
                        incomplete.append(f"n5_group_provider_ledger_anomaly_ids_missing:{account_ref}:{field}")
                    elif values:
                        failures.append(f"n5_group_provider_ledger_anomaly_ids_nonempty:{account_ref}:{field}")
                write_count = ledger.get("make_order_write_count")
                unique_count = ledger.get("unique_client_order_id_count")
                duplicate_count = ledger.get("duplicate_client_order_id_count")
                if not all(_count(value) for value in (write_count, unique_count, duplicate_count)):
                    incomplete.append(f"n5_group_provider_ledger_counts_missing:{account_ref}")
                    provider_totals_complete = False
                else:
                    expected_writes = provider_registration["expected_group_child_make_order_writes_per_account"]
                    if write_count != expected_writes:
                        failures.append(f"n5_group_provider_write_count_mismatch:{account_ref}")
                    if account_ref not in derived_children_account_observed:
                        incomplete.append(f"n5_group_provider_derived_intent_cross_check_missing:{account_ref}")
                    elif write_count != derived_children_by_account.get(account_ref):
                        failures.append(f"n5_group_provider_derived_intent_count_mismatch:{account_ref}")
                    if unique_count != provider_registration["expected_unique_client_order_ids_per_account"] or unique_count != write_count:
                        failures.append(f"n5_group_provider_unique_client_order_id_count_mismatch:{account_ref}")
                    if duplicate_count > provider_registration["maximum_duplicate_client_order_ids_per_account"]:
                        failures.append(f"n5_group_provider_duplicate_client_order_id:{account_ref}")
                    provider_total += write_count
                    provider_unique_total += unique_count
                    provider_duplicate_total += duplicate_count
                writes = ledger.get("writes_by_client_order_id")
                if not isinstance(writes, list) or not writes:
                    incomplete.append(f"n5_group_provider_write_rows_missing:{account_ref}")
                else:
                    seen_client_ids: set[str] = set()
                    if len(writes) != provider_registration.get("expected_group_child_make_order_writes_per_account"):
                        failures.append(f"n5_group_provider_order_row_count_mismatch:{account_ref}")
                    for write in writes:
                        if not isinstance(write, Mapping):
                            incomplete.append(f"n5_group_provider_write_row_invalid:{account_ref}")
                            continue
                        client_order_id = write.get("client_order_id")
                        row_write_count = write.get("write_count")
                        if not isinstance(client_order_id, str) or not client_order_id or not _count(row_write_count) or row_write_count == 0:
                            incomplete.append(f"n5_group_provider_write_row_identity_invalid:{account_ref}")
                            continue
                        if client_order_id in seen_client_ids or row_write_count != 1:
                            failures.append(f"n5_group_provider_write_row_duplicate:{account_ref}")
                        seen_client_ids.add(client_order_id)
                        order_status = write.get("order_status")
                        if not isinstance(order_status, str) or not order_status:
                            incomplete.append(f"n5_group_provider_write_row_status_missing:{account_ref}")
                        elif order_status != "filled":
                            failures.append(f"n5_group_provider_write_row_status_mismatch:{account_ref}")
                        expected_quantity = quantity_by_client_order_id.get(client_order_id)
                        if expected_quantity is None:
                            incomplete.append(f"n5_group_provider_write_row_leg_join_missing:{account_ref}")
                        for field in ("quantity", "filled"):
                            quantity = _decimal_value(write.get(field))
                            if quantity is None:
                                incomplete.append(f"n5_group_provider_write_row_{field}_invalid:{account_ref}")
                            elif quantity <= 0:
                                failures.append(f"n5_group_provider_write_row_{field}_nonpositive:{account_ref}")
                            elif expected_quantity is not None and quantity != expected_quantity:
                                failures.append(f"n5_group_provider_write_row_{field}_mismatch:{account_ref}")
                    if account_ref in join_client_ids_by_account and seen_client_ids != join_client_ids_by_account[account_ref]:
                        failures.append(f"n5_group_provider_write_join_client_id_mismatch:{account_ref}")
                status_counts = _map(ledger.get("order_status_counts"))
                if status_counts is None or any(not _count(value) for value in status_counts.values()):
                    incomplete.append(f"n5_group_provider_order_status_counts_missing:{account_ref}")
                elif status_counts.get("filled") != provider_registration.get("expected_group_child_make_order_writes_per_account") or sum(status_counts.values()) != provider_registration.get("expected_group_child_make_order_writes_per_account"):
                    failures.append(f"n5_group_provider_order_status_distribution_mismatch:{account_ref}")
            if provider_totals_complete:
                if provider_total != provider_registration.get("expected_group_child_make_order_writes_whole_profile"):
                    failures.append("n5_group_provider_write_total_mismatch")
                if provider_unique_total != provider_registration.get("expected_group_child_make_order_writes_whole_profile"):
                    failures.append("n5_group_provider_unique_client_order_id_total_mismatch")
                if provider_duplicate_total != 0:
                    failures.append("n5_group_provider_duplicate_client_order_id_total_nonzero")

    whole = contract["whole_profile_1800_second_targets"]
    if total_requests != whole["group_requests"]:
        incomplete.append("n5_group_request_total_mismatch")
    if total_children != whole["group_child_dispatches"]:
        incomplete.append("n5_group_child_total_mismatch")
    if unresolved_group_total:
        failures.append("n5_group_unresolved")
    if group_failure_total:
        failures.append("n5_group_failure")
    if unknown_child_outcomes:
        failures.append("n5_group_unknown_child_outcome")


def _result(profile_id: str | None, incomplete: list[str], failures: list[str]) -> dict[str, Any]:
    status = "FAIL" if failures else "INCOMPLETE" if incomplete else "PASS"
    return {
        "profile_id": profile_id,
        "status": status,
        "failures": sorted(set(failures)),
        "incomplete_reasons": sorted(set(incomplete)),
        "evidence_level": "LOCAL_REPORT_THRESHOLD_EVALUATION_ONLY",
        "formal_acceptance_status": "NOT_CLAIMED_BY_CONSUMER",
    }


def evaluate_profile(
    registration: Mapping[str, Any],
    report: Mapping[str, Any],
    trusted_binding: Mapping[str, Any] | None,
    profile_id: str | None = None,
) -> dict[str, Any]:
    """Score one full report without reading or trusting the runner P0 flag."""
    incomplete: list[str] = []
    failures: list[str] = []
    profile_id = profile_id or report.get("load_profile")
    contract = _map(registration.get("matrix_consumer_contract")) or {}
    if profile_id not in PROFILE_IDS or profile_id not in contract.get("formal_matrix_evaluation_profiles", []):
        return _result(profile_id, ["profile_not_registered_for_matrix"], [])
    profile = _profile(registration, profile_id)
    if profile is None:
        return _result(profile_id, ["profile_registration_missing"], [])
    incomplete.extend(_source_binding_issues(registration, report, trusted_binding))
    if profile.get("runner_status") not in ("IMPLEMENTED", "SOURCE_VERIFIED", "BOUND"):
        incomplete.append("profile_runner_not_integrated_at_bound_source")
    if report.get("load_profile") != profile_id or report.get("mode") != "full" or report.get("state") != "LOAD_PROFILE_COMPLETE":
        incomplete.append("completed_full_profile_report_missing")
    load = _map(report.get("load")) or {}
    if load.get("requested_duration_seconds") != registration["workload_contract"]["full_duration_seconds"]:
        incomplete.append("requested_full_duration_mismatch")
    _check_performance(registration, profile_id, profile, report, incomplete, failures)
    _check_cpu_resources(registration, report, incomplete, failures)
    _check_topology_shutdown(registration, profile_id, report, incomplete, failures)
    _check_terminal(registration, report, incomplete, failures)
    try:
        streams, subscriptions = _profile_streams(registration, profile)
    except (KeyError, TypeError, ValueError):
        streams, subscriptions = [], {}
        incomplete.append("profile_market_stream_registration_invalid")
    _check_recovery(registration, profile_id, profile, report, streams, subscriptions, incomplete, failures)
    _check_payload(registration, profile_id, profile, report, incomplete, failures)
    if profile_id == "N5":
        _check_n5_group(registration, profile, report, incomplete, failures)
    if profile_id == "N6":
        _check_n6_quota(profile, report, incomplete, failures)
    return _result(profile_id, incomplete, failures)


def evaluate_matrix(
    registration: Mapping[str, Any],
    reports_by_profile: Mapping[str, Mapping[str, Any]],
    trusted_binding: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Evaluate U0 and N1–N6 independently; P0 remains U0-only."""
    registration_errors = validate_registration(registration)
    consumer_contract = _map(registration.get("matrix_consumer_contract"))
    selector = consumer_contract.get("formal_matrix_evaluation_profiles", []) if consumer_contract else []
    rows: dict[str, Any] = {}
    for profile_id in selector if isinstance(selector, list) else []:
        report = reports_by_profile.get(profile_id)
        rows[profile_id] = (
            evaluate_profile(registration, report, trusted_binding, profile_id)
            if isinstance(report, Mapping)
            else _result(profile_id, ["full_profile_report_missing"], [])
        )
    status = (
        "FAIL" if any(row["status"] == "FAIL" for row in rows.values())
        else "INCOMPLETE" if registration_errors or any(row["status"] == "INCOMPLETE" for row in rows.values())
        else "PASS"
    )
    return {
        "status": status,
        "registration_errors": registration_errors,
        "profiles": rows,
        "p0_baseline_profiles": list(P0_PROFILE_IDS),
        "formal_matrix_profiles": list(PROFILE_IDS),
        "evidence_level": "LOCAL_REPORT_THRESHOLD_EVALUATION_ONLY",
        "formal_acceptance_status": "NOT_CLAIMED_BY_CONSUMER",
    }
